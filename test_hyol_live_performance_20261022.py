"""Real ledger writes and cooperative-worker timing without desktop input."""
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
import json
import threading
import unittest
from unittest.mock import Mock, patch

import hyol_performance as performance
import hyol_scheduler as hs


def scheduler(name='source', state=None):
    s = SimpleNamespace(active=SimpleNamespace(name=name), state=state,
                        templates=SimpleNamespace(match=Mock()),
                        _performance_task='ranking')
    for method in ('capture', 'template', 'switch_account', 'switch_account_for_combat',
                   'wait_interruptible', 'click_template', 'click_point', 'save_raw_evidence'):
        setattr(s, method, Mock())
    if state is None:
        del s.state
    return s


class LivePerformanceTests(unittest.TestCase):
    def test_real_task_pipeline_and_multi_progress_direct_saves_are_counted(self):
        from hyol_daily_tasks import DailyRunner
        from hyol_multi_solo import MultiSoloRunner
        from hyol_team_pipeline import TeamPipelineRunner

        with TemporaryDirectory() as directory:
            base = Path(directory)
            s = scheduler()
            s.shared_state_directory = base
            s.accounts = []
            s.diagnostics = SimpleNamespace(identifier='same-run')
            daily = object.__new__(DailyRunner)
            daily.path, daily.records = base/'account_tasks.json', {'mail': {'status': 'done'}}
            pipeline = object.__new__(TeamPipelineRunner)
            pipeline.path, pipeline.record = base/'team_pipeline_latest.json', {'id': 'same-run'}
            multi = object.__new__(MultiSoloRunner)
            multi.scheduler, multi.tasks, multi.runners = s, ('mail',), {}
            multi.tab_count, multi.requested_account_count = 0, 6
            multi.arena_wins, multi.arena_target_mode = 5, 'tickets'
            for name in ('stall_checks', 'exclusion_reasons', 'deferred', 'skipped_tasks', 'startup_pending'):
                setattr(multi, name, {})
            for name in ('excluded', 'disconnected', 'manual_deferred', 'initial_unavailable', 'startup_returned'):
                setattr(multi, name, set())
            meter = performance.PerformanceMeter(s)
            try:
                daily.save()
                pipeline.save()
                multi.save_progress('mail')
                self.assertEqual(3, meter.snapshot()['atomic_json']['calls'])
                self.assertEqual('done', json.loads(daily.path.read_text('utf-8'))['mail']['status'])
                self.assertEqual('same-run', json.loads(pipeline.path.read_text('utf-8'))['id'])
                saved = json.loads((base/'multi_solo_latest.json').read_text('utf-8'))
                self.assertEqual(('mail', 'same-run'), (saved['stage'], saved['run_id']))
            finally:
                meter.close()

    def test_direct_json_and_replaced_real_state_store_are_counted_once(self):
        clock = {'now': 0.0}
        original = hs.atomic_json

        def write(path, value):
            clock['now'] += .2
            return original(path, value)

        with TemporaryDirectory() as directory:
            base = Path(directory)
            s = scheduler(state=hs.StateStore(['source'], base/'before', restore_history=False))
            with patch.object(hs, 'atomic_json', side_effect=write), \
                 patch.object(performance.time, 'perf_counter', side_effect=lambda: clock['now']):
                meter = performance.PerformanceMeter(s)
                try:
                    hs.atomic_json(base/'direct.json', {'direct': True})
                    s.state = hs.StateStore(['source'], base/'after', restore_history=False)
                    s.state.save_account('source')
                    s.state.save_global()
                    rows = meter.snapshot()
                    self.assertEqual(3, rows['atomic_json']['calls'])
                    self.assertEqual(1, rows['save_account']['calls'])
                    self.assertEqual(1, rows['save_global']['calls'])
                    self.assertAlmostEqual(.6, rows['atomic_json']['exclusive_seconds'])
                    self.assertAlmostEqual(0, rows['save_account']['exclusive_seconds'])
                    self.assertAlmostEqual(0, rows['save_global']['exclusive_seconds'])
                    self.assertAlmostEqual(.6, sum(row['exclusive_seconds'] for row in rows.values()))
                    self.assertTrue(json.loads((base/'direct.json').read_text('utf-8'))['direct'])
                    self.assertEqual('source', json.loads(s.state.account_paths['source'].read_text('utf-8'))['account'])
                    self.assertEqual(s.state.day, json.loads(s.state.path.read_text('utf-8'))['date'])
                finally:
                    meter.close()
        self.assertIs(original, hs.atomic_json)

    def test_nested_meters_measure_only_current_worker_and_restore_functions(self):
        original_atomic = hs.atomic_json
        original_account, original_global = hs.StateStore.save_account, hs.StateStore.save_global
        with TemporaryDirectory() as directory:
            base = Path(directory)
            a = scheduler('team', hs.StateStore(['team'], base/'team', restore_history=False))
            b = scheduler('solo', hs.StateStore(['solo'], base/'solo', restore_history=False))
            outer = performance.PerformanceMeter(a)
            outer_atomic = hs.atomic_json
            try:
                hs.atomic_json(base/'team-direct.json', {'worker': 'team'})
                inner = performance.PerformanceMeter(b)
                try:
                    hs.atomic_json(base/'solo-direct.json', {'worker': 'solo'})
                    b.state = hs.StateStore(['solo'], base/'solo-replaced', restore_history=False)
                    b.state.save_account('solo')
                    self.assertEqual(1, outer.snapshot()['atomic_json']['calls'])
                    self.assertNotIn('save_account', outer.snapshot())
                    self.assertEqual(2, inner.snapshot()['atomic_json']['calls'])
                    self.assertEqual(1, inner.snapshot()['save_account']['calls'])
                finally:
                    inner.close()
                self.assertIs(outer_atomic, hs.atomic_json)
                a.state.save_global()
                self.assertEqual(2, outer.snapshot()['atomic_json']['calls'])
                self.assertEqual(1, outer.snapshot()['save_global']['calls'])
            finally:
                outer.close()
            outer.close()  # Repeated cleanup must not overwrite the restored function.
        self.assertIs(original_atomic, hs.atomic_json)
        self.assertIs(original_account, hs.StateStore.save_account)
        self.assertIs(original_global, hs.StateStore.save_global)

    def test_switch_children_inherit_target_and_task_before_active_changes(self):
        clock = {'now': 0.0}

        def spend(seconds):
            clock['now'] += seconds

        s = scheduler()
        s.templates.match = lambda: spend(.3)

        def template():
            spend(.2)
            return s.templates.match()

        def capture():
            spend(.1)
            return s.template()

        def switch(account):
            spend(.05)
            s.capture()
            s.active = account
            s._performance_task = 'changed_inside_switch'
            s.capture()
            spend(.05)

        def combat_switch(account):
            spend(.1)
            s.switch_account(account)
            spend(.1)

        s.capture, s.template = capture, template
        s.switch_account, s.switch_account_for_combat = switch, combat_switch
        target = SimpleNamespace(name='target')
        with patch.object(performance.time, 'perf_counter', side_effect=lambda: clock['now']):
            meter = performance.PerformanceMeter(s)
            try:
                s.switch_account_for_combat(account=target)
                rows = meter.snapshot()
                self.assertAlmostEqual(1.5, sum(row['exclusive_seconds'] for row in rows.values()))
                self.assertGreater(sum(row['seconds'] for row in rows.values()), 1.5)
                scopes = meter.scope_snapshot()
                self.assertEqual(1, len(scopes))
                self.assertEqual(('target', 'ranking'), (scopes[0]['account'], scopes[0]['task']))
                self.assertEqual(2, scopes[0]['operations']['capture']['calls'])
                self.assertEqual(2, scopes[0]['operations']['recognition']['calls'])
                s.capture()
                self.assertEqual({('target', 'ranking'), ('target', 'changed_inside_switch')},
                                 {(row['account'], row['task']) for row in meter.scope_snapshot()})
            finally:
                meter.close()
        self.assertIs(capture, s.capture)
        self.assertIs(template, s.template)
        self.assertIs(switch, s.switch_account)

    def test_exception_leaves_no_inherited_switch_scope(self):
        s = scheduler()
        original_capture = Mock(side_effect=[RuntimeError('capture failed'), None])
        s.capture = original_capture
        s.switch_account = lambda account: s.capture()
        meter = performance.PerformanceMeter(s)
        try:
            with self.assertRaisesRegex(RuntimeError, 'capture failed'):
                s.switch_account(SimpleNamespace(name='target'))
            s._performance_task = 'after_failure'
            s.capture()
            self.assertEqual({('target', 'ranking'), ('source', 'after_failure')},
                             {(row['account'], row['task']) for row in meter.scope_snapshot()})
        finally:
            meter.close()
        self.assertIs(original_capture, s.capture)

    def test_other_thread_json_is_not_attributed_to_foreground_worker(self):
        original = hs.atomic_json
        with TemporaryDirectory() as directory:
            base = Path(directory)
            errors = []

            def background():
                try:
                    hs.atomic_json(base/'background.json', {'thread': 'background'})
                except BaseException as exc:
                    errors.append(exc)

            meter = performance.PerformanceMeter(scheduler())
            try:
                worker = threading.Thread(target=background)
                worker.start()
                worker.join(timeout=5)
                self.assertFalse(worker.is_alive())
                self.assertEqual([], errors)
                self.assertNotIn('atomic_json', meter.snapshot())
                hs.atomic_json(base/'foreground.json', {'thread': 'foreground'})
                self.assertEqual(1, meter.snapshot()['atomic_json']['calls'])
                self.assertEqual('background', json.loads((base/'background.json').read_text('utf-8'))['thread'])
            finally:
                meter.close()
        self.assertIs(original, hs.atomic_json)


if __name__ == '__main__':
    unittest.main()
