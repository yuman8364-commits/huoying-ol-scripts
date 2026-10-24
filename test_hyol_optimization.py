"""Focused offline acceptance for log indexing, cache budgets and cleanup."""
import gc
import importlib.util
import json
import os
import sqlite3
import subprocess
import sys
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest
import weakref
from unittest.mock import patch

import numpy as np
from PIL import Image
import hyol_scheduler as hs
from hyol_runtime import RuntimeController
from hyol_task_progress import ProgressReader


class IncrementalProgressTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.log = self.root / 'logs' / 'HYOL_Scheduler_run.jsonl'
        self.log.parent.mkdir()
        self.reader = ProgressReader(self.root)
        self.addCleanup(self.temp.cleanup)
        self.addCleanup(self.reader.close)
        self.append(event='multi_solo_run_started', tasks=['arena', 'survival'], managed_enabled=False)
        self.append(event='account_bound', account='角色甲', tab_index=1)

    def append(self, **fields):
        event = dict(ts='2026-10-24T12:00:%02d+08:00' % getattr(self, 'sequence', 0), run_id='run', **fields)
        self.sequence = getattr(self, 'sequence', 0) + 1
        with self.log.open('a', encoding='utf-8') as handle:
            handle.write(json.dumps(event, ensure_ascii=False) + '\n')

    def load(self):
        return self.reader.load('2026-10-24', 'run')

    def test_unchanged_refresh_decodes_nothing_and_append_reads_only_new_bytes(self):
        self.load()
        before = (self.reader.index.bytes_read, self.reader.index.records_decoded)
        self.load()
        self.assertEqual(before, (self.reader.index.bytes_read, self.reader.index.records_decoded))
        size = self.log.stat().st_size
        self.append(event='arena_victory_confirmed', account='角色甲', target='arena_attempt_victory_1')
        self.assertEqual(1, self.load()['rows'][0]['tasks']['arena']['verified_wins'])
        self.assertEqual(self.log.stat().st_size-size, self.reader.index.bytes_read-before[0])

    def test_split_utf8_and_incomplete_line_are_retried_without_losing_or_duplicating_event(self):
        self.load()
        record = json.dumps(dict(event='daily_task_pending', run_id='run', ts='2026-10-24T13:00:00+08:00',
                                 account='角色甲', task='arena', detail='中文待核实'), ensure_ascii=False).encode()
        split = record.index('中'.encode()) + 1
        with self.log.open('ab') as handle:
            handle.write(record[:split])
        self.assertTrue(self.load()['warnings'])
        with self.log.open('ab') as handle:
            handle.write(record[split:] + b'\n')
        cell = self.load()['rows'][0]['tasks']['arena']
        self.assertEqual('中文待核实', cell['last_error'])
        self.assertEqual(1, sum(e['event']=='daily_task_pending' for e in cell['events']))

    def test_truncate_and_atomic_replacement_discard_removed_results(self):
        original = self.log.read_bytes()
        self.append(event='arena_victory_confirmed', account='角色甲', target='win1')
        self.assertEqual(1, self.load()['rows'][0]['tasks']['arena']['verified_wins'])
        self.log.write_bytes(original)
        self.assertEqual(0, self.load()['rows'][0]['tasks']['arena']['verified_wins'])
        replacement = self.log.with_suffix('.new')
        replacement.write_bytes(original + b'\n')
        replacement.replace(self.log)
        self.assertEqual(0, self.load()['rows'][0]['tasks']['arena']['verified_wins'])

    def test_duplicate_logs_and_repeated_wins_do_not_inflate_counts(self):
        self.append(event='arena_victory_confirmed', account='角色甲', target='win1')
        self.append(event='arena_victory_confirmed', account='角色甲', target='win1')
        copy = self.root/'other'; copy.mkdir()
        (copy/self.log.name).write_bytes(self.log.read_bytes())
        self.reader.log_roots.append(copy)
        self.assertEqual(1, self.load()['rows'][0]['tasks']['arena']['verified_wins'])

    def test_detail_limit_preserves_old_fields_and_full_counter_history(self):
        self.append(event='daily_task_pending', account='角色甲', task='arena', remaining=7, detail='原结果待核实')
        self.append(event='arena_victory_confirmed', account='角色甲', target='early-win')
        for i in range(250):
            self.append(event='arena_observed', account='角色甲', task='arena', sample=i)
        cell = self.load()['rows'][0]['tasks']['arena']
        self.assertEqual(100, len(cell['events']))
        self.assertEqual(7, cell['remaining'])
        self.assertEqual('pending', cell['status'])
        self.assertEqual(1, cell['verified_wins'])
        self.assertIn('early-win', self.log.read_text('utf-8'))

    def test_out_of_order_append_rebuilds_chronologically(self):
        self.append(event='daily_task_complete', account='角色甲', task='arena', task_completed=True)
        with self.log.open('a', encoding='utf-8') as handle:
            handle.write(json.dumps(dict(event='daily_task_pending', run_id='run', account='角色甲',
                task='arena', ts='2026-10-24T11:59:00+08:00', detail='旧状态'))+'\n')
        self.assertEqual('done', self.load()['rows'][0]['tasks']['arena']['status'])

    def test_state_cache_has_entry_and_byte_bounds_and_scope_switch_drops_it(self):
        self.reader.max_cache_entries = 3
        self.reader.max_cache_bytes = 80
        for i in range(12):
            path = self.root / f'{i}.json'; path.write_text(json.dumps({'value': 'a'*10}))
            self.reader._read(path)
        self.assertLessEqual(len(self.reader.cache), 3)
        self.assertLessEqual(self.reader.cache_bytes, 80)
        self.load()
        self.assertEqual(0, len(self.reader.cache))
        self.assertEqual([], self.reader.load('2026-10-24', 'other')['rows'])

    def test_reader_never_changes_original_files_and_temp_index_is_removed_on_close(self):
        before = {p:p.read_bytes() for p in self.root.rglob('*') if p.is_file()}
        self.load()
        index = Path(self.reader.index.temporary.name)
        self.assertTrue(index.exists())
        self.assertEqual(before, {p:p.read_bytes() for p in self.root.rglob('*') if p.is_file()})
        self.reader.close()
        self.assertFalse(index.exists())


class RecognitionLifetimeTests(unittest.TestCase):
    def test_native_thread_default_respects_explicit_deployment_setting(self):
        from hyol_environment import configure_numeric_runtime
        environment = {}
        configure_numeric_runtime(environment)
        self.assertEqual('2', environment['OPENBLAS_NUM_THREADS'])
        environment['OPENBLAS_NUM_THREADS'] = '4'
        configure_numeric_runtime(environment)
        self.assertEqual('4', environment['OPENBLAS_NUM_THREADS'])

    def test_legacy_exports_are_the_extracted_components(self):
        import hyol_platform, hyol_storage, hyol_vision
        self.assertIs(hs.Win32, hyol_platform.Win32)
        self.assertIs(hs.StateStore, hyol_storage.StateStore)
        self.assertIs(hs.TemplateStore, hyol_vision.TemplateStore)

    def test_small_frame_budget_changes_neither_score_nor_coordinate(self):
        rng = np.random.default_rng(2)
        array = rng.integers(0, 255, (100, 160), dtype=np.uint8)
        frame = Image.fromarray(array).convert('RGB')
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder)/'marker.bmp'; Image.fromarray(array[25:35, 40:55]).save(path)
            standard, small = hs.TemplateStore(.9), hs.TemplateStore(.9, max_frame_bytes=1)
            standard.begin_frame(frame); small.begin_frame(frame)
            self.assertEqual(standard.match(frame, path, (0,0,160,100)), small.match(frame, path, (0,0,160,100)))
            self.assertFalse(small._frame_correlation)
            self.assertFalse(small._frame_crops)

    def test_cleanup_drops_cyclic_runner_and_buffers_without_writing_ledgers(self):
        scheduler = object.__new__(hs.Scheduler)
        scheduler.templates = hs.TemplateStore(.9)
        scheduler.templates.begin_frame(Image.new('RGB', (1920,1032)))
        scheduler._last_frame = scheduler.templates._frame
        scheduler.detector = SimpleNamespace(cached_frame=scheduler._last_frame, cached_predictions=[1], session=object())
        class Runner:
            pass
        runner = Runner(); runner.scheduler = scheduler; runner.solo = runner
        controller = RuntimeController(lambda _:None)
        controller.runner, controller.scheduler = runner, scheduler
        runner_ref = weakref.ref(runner)
        with patch.object(hs, 'atomic_json', side_effect=AssertionError('No ledger writes')):
            controller._release_run_resources()
        self.assertIsNone(controller.runner)
        self.assertIsNone(scheduler._last_frame)
        self.assertIsNone(scheduler.templates._frame)
        self.assertIsNone(scheduler.detector.session)
        del runner
        gc.collect()
        self.assertIsNone(runner_ref())


class PackageProfileTests(unittest.TestCase):
    def test_standard_profile_excludes_models_and_uses_portable_config(self):
        from tools.build_unified_package import copy_resources, validate_profile
        with tempfile.TemporaryDirectory() as folder:
            root, package = Path(folder)/'source', Path(folder)/'package'
            (root/'assets').mkdir(parents=True); package.mkdir()
            (root/'assets/test.bmp').write_bytes(b'asset')
            (root/'assets/accounts').mkdir()
            (root/'assets/accounts/private.bmp').write_bytes(b'private')
            for name in ('HYOL_Daily.ini','DAILY_Client.ini','TEAM_Client.ini','ARENA_Client.ini'):
                (root/name).write_text('TabAccountNames=personal\nAllowMarketPurchase=1\n')
            (root/'README_统一客户端.md').write_text('guide', 'utf-8')
            copy_resources(root, package)
            validate_profile(package)
            self.assertFalse((package/'models').exists())
            self.assertFalse((package/'assets/accounts').exists())
            config = (package/'HYOL_Daily.ini').read_text('utf-8')
            self.assertIn('AllowMarketPurchase=0', config)
            self.assertIn('EnableShadowDetection=0', config)
            self.assertNotIn('personal', config)
            (package/'_internal/onnxruntime').mkdir(parents=True)
            with self.assertRaises(ValueError):
                validate_profile(package)

    def test_diagnostic_profile_copies_only_hash_pinned_current_model(self):
        import hashlib
        from tools.build_unified_package import copy_resources
        with tempfile.TemporaryDirectory() as folder:
            root, package = Path(folder)/'source', Path(folder)/'package'
            (root/'assets').mkdir(parents=True); (root/'models/candidates').mkdir(parents=True); package.mkdir()
            model = b'fixed-model'
            (root/'models/game_ui.onnx').write_bytes(model)
            (root/'models/candidates/old.onnx').write_bytes(b'old')
            manifest = dict(file='game_ui.onnx', sha256=hashlib.sha256(model).hexdigest())
            (root/'models/detector.json').write_text(json.dumps(manifest))
            for name in ('HYOL_Daily.ini','DAILY_Client.ini','TEAM_Client.ini','ARENA_Client.ini'):
                (root/name).write_text('AllowMarketPurchase=0\n')
            (root/'README_统一客户端.md').write_text('guide', 'utf-8')
            copy_resources(root, package, True)
            self.assertEqual(model, (package/'models/game_ui.onnx').read_bytes())
            self.assertFalse((package/'models/candidates').exists())
            (root/'models/game_ui.onnx').write_bytes(b'changed')
            with self.assertRaisesRegex(ValueError, 'SHA256'):
                copy_resources(root, package, True)

    def test_missing_optional_vision_is_explicit_only_when_requested(self):
        from hyol_recognition import OnnxDetector
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder)/'absent.json'
            self.assertEqual('', OnnxDetector(path, allow_shadow=False).error)
            self.assertIn('optional', OnnxDetector(path, allow_shadow=True).error)


class IndexResilienceTests(unittest.TestCase):
    """F1/F2: a failing progress index degrades the panel, never the client."""

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.log = self.root / 'logs' / 'HYOL_Scheduler_run.jsonl'
        self.log.parent.mkdir()
        self.reader = ProgressReader(self.root)
        self.addCleanup(self.temp.cleanup)
        self.addCleanup(self.reader.close)
        self.append(event='multi_solo_run_started', tasks=['arena'], managed_enabled=False)
        self.append(event='account_bound', account='角色甲', tab_index=1)

    def append(self, **fields):
        event = dict(ts='2026-10-24T12:00:%02d+08:00' % getattr(self, 'sequence', 0),
                     run_id='run', **fields)
        self.sequence = getattr(self, 'sequence', 0) + 1
        with self.log.open('a', encoding='utf-8') as handle:
            handle.write(json.dumps(event, ensure_ascii=False) + '\n')

    def load(self):
        return self.reader.load('2026-10-24', 'run')

    def test_index_is_lazy_and_construction_failure_degrades_to_unknown(self):
        self.assertIsNone(self.reader.index)          # created only on first load
        self.assertEqual(1, len(self.reader.days()))  # discovery needs no index either
        with patch('hyol_progress_index.tempfile.TemporaryDirectory',
                   side_effect=OSError(13, 'Permission denied')):
            snapshot = self.load()
            self.assertEqual([], snapshot['rows'])    # unknown state, never completion
            self.assertEqual([], snapshot['runs'])
            self.assertEqual('', snapshot['updated_at'])
            self.assertTrue(any('进度索引不可用，待刷新' in w for w in snapshot['warnings']))
            self.assertIsNone(self.reader.index)
        snapshot = self.load()                        # a later refresh recovers
        self.assertIsNotNone(self.reader.index)
        self.assertEqual('角色甲', snapshot['rows'][0]['account'])
        self.assertEqual([], snapshot['warnings'])

    def test_sqlite_failure_keeps_last_verified_snapshot_and_recovers_without_duplicates(self):
        first = self.load()
        self.assertEqual('角色甲', first['rows'][0]['account'])
        self.append(event='arena_victory_confirmed', account='角色甲', target='win1')
        index = self.reader.index
        stale_temp = Path(index.temporary.name)
        index.connection.close()                     # real mid-run index failure
        snapshot = self.load()
        self.assertTrue(any('进度索引读取失败，保留上次快照' in w for w in snapshot['warnings']))
        self.assertEqual(first['rows'], snapshot['rows'])  # last verified rows kept
        self.assertIsNone(self.reader.index)               # broken index discarded
        self.assertFalse(stale_temp.exists())              # its temp dir cleaned up
        snapshot = self.load()                             # fresh index rebuilds
        self.assertEqual(1, snapshot['rows'][0]['tasks']['arena']['verified_wins'])
        self.assertEqual([], snapshot['warnings'])

    def test_scope_switch_does_not_serve_the_other_views_snapshot(self):
        self.load()
        self.reader.index.connection.close()         # break the index for the next read
        other = self.reader.load('2026-10-23', 'run')
        self.assertEqual([], other['rows'])          # unknown view, not the old scope's rows
        self.assertTrue(other['warnings'])
        recovered = self.load()                      # and a refresh recovers normally
        self.assertEqual('角色甲', recovered['rows'][0]['account'])
        self.assertEqual([], recovered['warnings'])


class ViewLifecycleTests(unittest.TestCase):
    """F1: the panel cleans itself up even when construction half-failed."""

    def test_destroy_without_reader_attribute_and_timer_cancelled(self):
        import tkinter as tk
        from hyol_progress_view import ProgressView
        window = tk.Tk()
        window.withdraw()
        destroyed = []
        self.addCleanup(lambda: destroyed or window.destroy())
        view = ProgressView(window, tempfile.mkdtemp(prefix='hyol-view-'), current_only=True)
        self.assertIsNone(view.reader.index)          # lazy: no index without a load
        self.assertIn(view._timer, set(window.tk.call('after', 'info')))
        del view.reader                                # simulate failed construction
        view.destroy()                                 # must not raise
        destroyed.append(True)
        self.assertNotIn(view._timer, set(window.tk.call('after', 'info')))
        window.destroy()


class LogRootDiscoveryTests(unittest.TestCase):
    """F5: log-root discovery is pure; the scheduler no longer builds an index."""

    def test_discovery_matches_reader_without_creating_an_index(self):
        import hyol_task_progress
        with tempfile.TemporaryDirectory() as folder:
            home = Path(folder)
            package = home / 'Desktop' / '可用脚本' / '火影OL完整客户端'
            (package / 'logs').mkdir(parents=True)
            (package / 'HYOL_Daily.ini').write_text(
                'SharedStateDirectory=%s\\state\n' % folder.replace('\\', '\\\\'), encoding='utf-8')
            with patch.dict('os.environ', {'USERPROFILE': str(home)}):
                reader = ProgressReader(package)
                self.addCleanup(reader.close)
                expected = reader.log_roots
                with patch('hyol_progress_index.tempfile.TemporaryDirectory',
                           side_effect=AssertionError('index created')):
                    self.assertEqual(expected, hyol_task_progress.discover_log_roots(package))
        source = Path(__file__).resolve().parent / 'hyol_scheduler.py'
        self.assertNotIn('ProgressReader', source.read_text(encoding='utf-8'))


class EntryThreadConfigTests(unittest.TestCase):
    """F3: the daruma entry caps the OpenBLAS default before NumPy loads."""

    ROOT = Path(__file__).resolve().parent

    def _child_value(self, preset):
        probe = ("import sys, os; sys.path.insert(0, r'%s'); "
                 "assert 'numpy' not in sys.modules; "
                 "import daruma_standalone; value = os.environ.get('OPENBLAS_NUM_THREADS'); "
                 "import numpy; print(value)" % self.ROOT)
        environment = {k: v for k, v in os.environ.items() if k != 'OPENBLAS_NUM_THREADS'}
        if preset:
            environment['OPENBLAS_NUM_THREADS'] = preset
        result = subprocess.run([sys.executable, '-c', probe], capture_output=True,
                                encoding='utf-8', env=environment, timeout=90)
        self.assertEqual(0, result.returncode, result.stderr)
        return result.stdout.strip()

    @unittest.skipUnless(importlib.util.find_spec('numpy'), 'entry subprocess needs NumPy')
    def test_daruma_package_defaults_to_two_and_respects_user_value(self):
        self.assertEqual('2', self._child_value(None))
        self.assertEqual('8', self._child_value('8'))


class LocaleIndependentAuditTests(unittest.TestCase):
    """F4: the pause-audit case passes under both UTF-8 modes."""

    ROOT = Path(__file__).resolve().parent

    def _run_case(self, utf8_mode):
        environment = {k: v for k, v in os.environ.items() if k != 'PYTHONUTF8'}
        environment['PYTHONUTF8'] = utf8_mode
        return subprocess.run(
            [sys.executable, '-B', '-m', 'unittest',
             'test_hyol_infrastructure.DiagnosticsTests.test_explicit_resume_preserves_pause_audit_record'],
            cwd=self.ROOT, capture_output=True, encoding='utf-8', env=environment, timeout=120)

    @unittest.skipUnless(importlib.util.find_spec('PIL'), 'infrastructure case needs PIL')
    def test_pause_audit_case_passes_in_both_utf8_modes(self):
        for mode in ('0', '1'):
            result = self._run_case(mode)
            self.assertEqual(0, result.returncode, 'PYTHONUTF8=%s\n%s' % (mode, result.stderr))


if __name__ == '__main__':
    unittest.main()
