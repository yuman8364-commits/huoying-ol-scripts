import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
import zipfile

from PIL import Image
import hyol_scheduler as hs
from hyol_diagnostics import Diagnostics, clear_manual_pause, mark_manual_pause, manual_pause_status
from hyol_recognition import Detection, OnnxDetector, Viewport, fuse
from tools.yolo_workflow import prepare
from hyol_navigation import return_to_leaf
from unittest.mock import Mock
from hyol_geometry import GameGeometry


class DiagnosticsTests(unittest.TestCase):
    def setUp(self):
        # Pause tests must never read or acknowledge a real TEMP fallback.
        isolated = tempfile.TemporaryDirectory()
        self.addCleanup(isolated.cleanup)
        fallback = patch('hyol_diagnostics.tempfile.gettempdir', return_value=isolated.name)
        fallback.start()
        self.addCleanup(fallback.stop)

    def shared_pause_roots(self, folder):
        parent = Path(folder)
        source, daily, team = (parent/name for name in ('source', 'daily-package', 'team-package'))
        for directory in (source, daily, team):
            directory.mkdir()
        shared = source/'state'
        (daily/'DAILY_Client.ini').write_text('SharedStateDirectory='+str(shared), encoding='utf-8-sig')
        (team/'TEAM_Client.ini').write_text('SharedStateDirectory='+str(shared), encoding='utf-8')
        (team/'DAILY_Client.ini').write_text('SharedStateDirectory='+str(shared), encoding='utf-8')
        return source, daily, team

    def test_preflight_manual_pause_never_creates_scheduler(self):
        from tools import preflight_live as preflight
        with patch.object(preflight, 'manual_pause_status', return_value={'paused': True}), patch.object(hs, 'atomic_json'), patch.object(hs, 'Scheduler') as scheduler:
            self.assertEqual(preflight.run()['status'], 'manual_pause')
            scheduler.assert_not_called()

    def test_external_f12_record_stops_input_without_clearing_audit(self):
        import threading
        scheduler = object.__new__(hs.Scheduler)
        scheduler.stop = threading.Event()
        scheduler.event = Mock()
        with patch('hyol_diagnostics.manual_pause_status', return_value={'paused': True}) as read, \
             patch('hyol_diagnostics.clear_manual_pause') as clear, patch.object(hs.Win32, 'click') as clicked:
            with self.assertRaisesRegex(KeyboardInterrupt, '人工暂停记录'):
                scheduler.chrome_click(180, 10, 'switch_tab:test', hs.Account(1, 'test', 180))
            read.assert_called_once_with(hs.ROOT)
            clear.assert_not_called()
            clicked.assert_not_called()
        self.assertTrue(scheduler.stop.is_set())

    def test_preflight_lease_conflict_never_binds_window(self):
        from tools import preflight_live as preflight
        scheduler = Mock()
        scheduler.run_context = None
        scheduler.diagnostics.identifier = 'test'
        scheduler.diagnostics.last_path = None
        scheduler.hwnd = None
        scheduler.content_click_count = 0
        lease = Mock()
        lease.acquire.return_value = False
        with patch.object(preflight, 'manual_pause_status', return_value={'paused': False}), patch.object(hs, 'atomic_json'), patch.object(hs, 'Scheduler', return_value=scheduler), patch.object(hs, 'ForegroundRuntimeLease', return_value=lease):
            result = preflight.run()
        self.assertEqual(result['status'], 'blocked')
        scheduler.bind_window.assert_not_called()
        scheduler.stop.set.assert_called_once()
        lease.release.assert_called_once()

    def test_event_history_is_not_changed_by_later_state_mutation(self):
        with tempfile.TemporaryDirectory() as folder:
            d = Diagnostics(folder)
            state = {'completed':2}
            d.observe('checkpoint', {'state':state})
            state['completed'] = 3
            self.assertEqual(d.events[-1]['state']['completed'],2)
    def test_bundle_is_run_scoped_and_contains_failure_without_screenshot(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            (root/'unrelated_secret.txt').write_text('must not collect')
            d = Diagnostics(root)
            d.observe('recognition', {'reason': 'not found'})
            path = d.export(summary={'result': 2}, state={'completed': 2})
            with zipfile.ZipFile(path) as archive:
                self.assertNotIn('unrelated_secret.txt', archive.namelist())
                self.assertEqual(json.loads(archive.read('state_snapshot.json'))['completed'], 2)
                self.assertTrue(json.loads(archive.read('report.json'))['collection_errors'])
                self.assertIn('recognition', archive.read('events.jsonl').decode())

    def test_export_falls_back_when_project_folder_not_writable(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)/'not_directory'
            root.write_text('file')
            with patch('hyol_diagnostics.tempfile.gettempdir', return_value=folder):
                path = Diagnostics(root).export(frame=Image.new('RGB', (20, 20)))
            self.assertIn('HYOL_diagnostics', str(path))
            with zipfile.ZipFile(path) as archive:
                self.assertIn('last_game_frame.png', archive.namelist())

    def test_manual_stop_is_persisted(self):
        with tempfile.TemporaryDirectory() as folder:
            mark_manual_pause(folder, 'run1', 'F12')
            self.assertTrue(json.loads((Path(folder)/'state/manual_pause.json').read_text(encoding='utf-8-sig'))['paused'])

    def test_explicit_resume_preserves_pause_audit_record(self):
        with tempfile.TemporaryDirectory() as folder:
            mark_manual_pause(folder, 'run1', 'F12')
            cleared = clear_manual_pause(folder, '用户明确继续')
            value = json.loads((Path(folder)/'state/manual_pause.json').read_text(encoding='utf-8-sig'))
            self.assertEqual(cleared, [str(Path(folder)/'state/manual_pause.json')])
            self.assertFalse(value['paused'])
            self.assertEqual(value['reason'], 'F12')
            self.assertEqual(value['resume_reason'], '用户明确继续')
            self.assertIn('resumed_at', value)
            self.assertFalse(manual_pause_status(folder)['paused'])

    def test_corrupt_pause_record_cannot_be_cleared(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder)/'state/manual_pause.json'
            path.parent.mkdir()
            path.write_text('{broken')
            with self.assertRaises(RuntimeError):
                clear_manual_pause(folder, '用户明确继续')

    def test_fallback_pause_is_honored_and_scoped(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)/'blocked'
            root.write_text('not a directory')
            with patch('hyol_diagnostics.tempfile.gettempdir', return_value=folder):
                mark_manual_pause(root, 'run1', 'F12')
                root.unlink()
                root.mkdir()
                self.assertTrue(manual_pause_status(root)['paused'])
                self.assertFalse(manual_pause_status(Path(folder)/'other')['paused'])

    def test_corrupt_pause_record_blocks_automatic_resume(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder)/'state/manual_pause.json'
            path.parent.mkdir()
            path.write_text('{broken')
            status = manual_pause_status(folder)
            self.assertTrue(status['paused'])
            self.assertEqual(status['path'], str(path))

    def test_pause_in_one_package_blocks_other_package_and_source(self):
        with tempfile.TemporaryDirectory() as folder:
            source, daily, team = self.shared_pause_roots(folder)
            mark_manual_pause(daily, 'daily-run', 'F12')
            for entry in (source, daily, team):
                status = manual_pause_status(entry)
                self.assertTrue(status['paused'])
                self.assertEqual(status['state_directory'], str(source/'state'))
            canonical = source/'state/manual_pause.json'
            legacy = daily/'state/manual_pause.json'
            before = json.loads(canonical.read_text(encoding='utf-8'))
            self.assertCountEqual(clear_manual_pause(source, '用户明确继续'), [str(canonical), str(legacy)])
            for path in (canonical, legacy):
                value = json.loads(path.read_text(encoding='utf-8'))
                self.assertFalse(value['paused'])
                self.assertEqual(value['reason'], 'F12')
                self.assertEqual(value['at'], before['at'])
            for entry in (source, daily, team):
                self.assertFalse(manual_pause_status(entry)['paused'])

    def test_repeated_and_new_pauses_preserve_previous_time_and_resume_audit(self):
        with tempfile.TemporaryDirectory() as folder:
            source, daily, team = self.shared_pause_roots(folder)
            canonical = source/'state/manual_pause.json'
            mark_manual_pause(daily, 'run1', 'F12')
            first = json.loads(canonical.read_text(encoding='utf-8'))
            mark_manual_pause(daily, 'run1', 'F12')
            self.assertEqual(json.loads(canonical.read_text(encoding='utf-8'))['at'], first['at'])
            clear_manual_pause(source, '明确恢复1')
            mark_manual_pause(team, 'run2', 'F12再次停止')
            value = json.loads(canonical.read_text(encoding='utf-8'))
            self.assertEqual(value['pause_history'][0]['at'], first['at'])
            self.assertEqual(value['pause_history'][0]['reason'], 'F12')
            self.assertEqual(value['pause_history'][0]['resume_reason'], '明确恢复1')
            self.assertCountEqual(value['entry_roots'], [str(daily), str(team)])
            clear_manual_pause(source, '明确恢复2')
            self.assertFalse(manual_pause_status(daily)['paused'])
            self.assertFalse(manual_pause_status(team)['paused'])

    def test_relative_shared_directory_and_legacy_local_pause_are_honored(self):
        with tempfile.TemporaryDirectory() as folder:
            source, daily, _ = self.shared_pause_roots(folder)
            (daily/'DAILY_Client.ini').write_text('SharedStateDirectory=../source/state', encoding='utf-8')
            local = daily/'state/manual_pause.json'
            local.parent.mkdir()
            original = {'paused': True, 'project': str(daily), 'reason': '旧F12', 'at': '2026-09-30T18:00:00'}
            local.write_text(json.dumps(original), encoding='utf-8')
            self.assertTrue(manual_pause_status(daily)['paused'])
            clear_manual_pause(daily, '用户明确继续旧入口')
            value = json.loads(local.read_text(encoding='utf-8'))
            self.assertEqual(value['reason'], original['reason'])
            self.assertEqual(value['at'], original['at'])
            mark_manual_pause(daily, 'new-run', 'F12')
            self.assertTrue(manual_pause_status(source)['paused'])

    def test_legacy_temp_pause_is_bound_to_its_packages_shared_project(self):
        with tempfile.TemporaryDirectory() as folder:
            source, daily, team = self.shared_pause_roots(folder)
            with patch('hyol_diagnostics.tempfile.gettempdir', return_value=folder):
                fallback = Path(folder)/'HYOL_manual_pause.json'
                fallback.write_text(json.dumps({'paused': True, 'project': str(daily),
                                               'reason': '旧F12', 'at': 'old-time'}), encoding='utf-8')
                self.assertTrue(manual_pause_status(source)['paused'])
                self.assertTrue(manual_pause_status(team)['paused'])
                self.assertFalse(manual_pause_status(Path(folder)/'unrelated')['paused'])
                clear_manual_pause(team, '用户恢复共同项目')
                value = json.loads(fallback.read_text(encoding='utf-8'))
                self.assertFalse(value['paused'])
                self.assertEqual(value['reason'], '旧F12')
                self.assertEqual(value['at'], 'old-time')

    def test_scoped_fallbacks_do_not_overwrite_or_resume_another_project(self):
        with tempfile.TemporaryDirectory() as folder:
            source, daily, team = self.shared_pause_roots(folder)
            other = Path(folder)/'other-project'
            other.mkdir()
            (source/'state').write_text('blocked directory', encoding='utf-8')
            (other/'state').write_text('blocked directory', encoding='utf-8')
            with patch('hyol_diagnostics.tempfile.gettempdir', return_value=folder):
                mark_manual_pause(daily, 'shared-run', 'F12')
                mark_manual_pause(other, 'other-run', '别的项目F12')
                self.assertEqual(len(list(Path(folder).glob('HYOL_manual_pause_*.json'))), 2)
                for root in (source, other):
                    (root/'state').unlink()
                    (root/'state').mkdir()
                self.assertTrue(manual_pause_status(team)['paused'])
                self.assertTrue(manual_pause_status(other)['paused'])
                clear_manual_pause(source, '只恢复共同项目')
                self.assertFalse(manual_pause_status(team)['paused'])
                self.assertTrue(manual_pause_status(other)['paused'])

    def test_config_conflict_blocks_and_preserves_pause_without_selecting_a_scope(self):
        with tempfile.TemporaryDirectory() as folder:
            source, daily, _ = self.shared_pause_roots(folder)
            other = Path(folder)/'unrelated-state'
            (daily/'TEAM_Client.ini').write_text('SharedStateDirectory='+str(other), encoding='utf-8')
            self.assertTrue(manual_pause_status(daily)['paused'])
            with self.assertRaisesRegex(RuntimeError, '冲突'):
                clear_manual_pause(daily, '用户继续')
            mark_manual_pause(daily, 'conflict-run', 'F12')
            value = json.loads((daily/'state/manual_pause.json').read_text(encoding='utf-8'))
            self.assertTrue(value['paused'])
            self.assertEqual(value['reason'], 'F12')
            self.assertIn('冲突', value['scope_error'])
            self.assertFalse((source/'state/manual_pause.json').exists())
            self.assertFalse((other/'manual_pause.json').exists())

    def test_all_records_are_validated_before_any_pause_is_cleared(self):
        with tempfile.TemporaryDirectory() as folder:
            source, daily, team = self.shared_pause_roots(folder)
            mark_manual_pause(daily, 'run', 'F12')
            canonical = source/'state/manual_pause.json'
            original = canonical.read_bytes()
            legacy = daily/'state/manual_pause.json'
            legacy.write_text('{corrupt', encoding='utf-8')
            self.assertTrue(manual_pause_status(team)['paused'])
            with self.assertRaises(RuntimeError):
                clear_manual_pause(source, '用户继续')
            self.assertEqual(canonical.read_bytes(), original)
            self.assertEqual(legacy.read_text(encoding='utf-8'), '{corrupt')

    def test_new_pause_does_not_destroy_corrupt_existing_record(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            path = root/'state/manual_pause.json'
            path.parent.mkdir()
            path.write_text('{corrupt', encoding='utf-8')
            with patch('hyol_diagnostics.tempfile.gettempdir', return_value=folder):
                mark_manual_pause(root, 'run', 'F12')
                self.assertEqual(path.read_text(encoding='utf-8'), '{corrupt')
                fallback = next(root.glob('HYOL_manual_pause_*.json'))
                self.assertEqual(json.loads(fallback.read_text(encoding='utf-8'))['reason'], 'F12')
                self.assertTrue(manual_pause_status(root)['paused'])
                with self.assertRaises(RuntimeError):
                    clear_manual_pause(root, '用户继续')

    def test_foreign_temp_record_is_ignored_even_if_its_pause_field_is_invalid(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)/'current'
            root.mkdir()
            with patch('hyol_diagnostics.tempfile.gettempdir', return_value=folder):
                fallback = Path(folder)/'HYOL_manual_pause.json'
                original = json.dumps({'project': str(Path(folder)/'other'), 'paused': 'broken'})
                fallback.write_text(original, encoding='utf-8')
                self.assertFalse(manual_pause_status(root)['paused'])
                self.assertEqual(clear_manual_pause(root, '用户继续'), [])
                self.assertEqual(fallback.read_text(encoding='utf-8'), original)

    def test_unowned_legacy_temp_pause_blocks_but_cannot_be_cleared_for_one_project(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)/'project'
            root.mkdir()
            with patch('hyol_diagnostics.tempfile.gettempdir', return_value=folder):
                fallback = Path(folder)/'HYOL_manual_pause.json'
                original = json.dumps({'paused': True, 'reason': 'legacy F12'})
                fallback.write_text(original, encoding='utf-8')
                self.assertTrue(manual_pause_status(root)['paused'])
                with self.assertRaisesRegex(RuntimeError, '项目归属'):
                    clear_manual_pause(root, '用户继续此项目')
                self.assertEqual(fallback.read_text(encoding='utf-8'), original)

    def test_empty_resume_reason_cannot_acknowledge_pause(self):
        with tempfile.TemporaryDirectory() as folder:
            mark_manual_pause(folder, 'run', 'F12')
            path = Path(folder)/'state/manual_pause.json'
            original = path.read_bytes()
            with self.assertRaisesRegex(RuntimeError, '恢复原因'):
                clear_manual_pause(folder, '')
            self.assertEqual(path.read_bytes(), original)

    def test_f12_sets_stop_before_logging(self):
        import threading
        s = hs.Scheduler.__new__(hs.Scheduler)
        s.stop = threading.Event()
        observed = []
        s.event = lambda *a, **kw: observed.append(s.stop.is_set())
        with patch.object(hs.Win32, 'f12_down', return_value=True), patch.object(hs.threading, 'Thread') as worker:
            worker.return_value.start.side_effect = lambda: worker.call_args.kwargs['target']()
            s.start_f12_monitor()
        self.assertEqual(observed, [True])

    def test_logger_preserves_event_in_fallback(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)/'blocked'
            root.write_text('file')
            logger = hs.JsonlLogger(root/'run.jsonl')
            with patch.object(hs.tempfile, 'gettempdir', return_value=folder), patch.object(hs.time, 'sleep'), self.assertWarns(RuntimeWarning):
                logger.write('cleanup', result=2)
            self.assertEqual(json.loads((Path(folder)/'HYOL_diagnostics/run.jsonl').read_text(encoding='utf-8-sig'))['event'], 'cleanup')


class RecognitionTests(unittest.TestCase):
    def test_rounding_cannot_leave_tiny_content_bounds(self):
        geometry = GameGeometry((0,0,3,3),(1,1,2,2))
        self.assertEqual(geometry.point(1918,1030),(1,1))
    def test_scaled_real_replays_keep_original_threshold(self):
        for width,height in ((1600,900),(1280,800)):
            path = hs.ROOT/f'logs/geometry_live_{width}x{height}_20260912_222600_791432_7f820890.png'
            if not path.exists():
                self.skipTest('live replay image unavailable')
            with Image.open(path) as frame:
                store = hs.TemplateStore(.9)
                store.resize_scale = (1918/(width-2),962/(height-70))
                ok,score,_,_ = store.match(frame,hs.ASSETS/'daily_practice_toolbar.bmp',(651,69,851,169))
                self.assertTrue(ok)
                self.assertGreaterEqual(score,.9)
                self.assertEqual(store.last_match_source,'template_scaled')

    def test_scaled_blank_frame_does_not_find_button(self):
        store = hs.TemplateStore(.9)
        store.resize_scale = (1.5,1.3)
        self.assertFalse(store.match(Image.new('RGB',(1920,1032),'white'),hs.ASSETS/'daily_practice_toolbar.bmp',(651,69,851,169))[0])
    def test_content_geometry_preserves_chrome_independently(self):
        geometry = GameGeometry((100,200,1060,750),(101,269,1059,749))
        self.assertEqual(geometry.point(960,10), (580,210))
        self.assertEqual(geometry.point(960,550), (580,509))
        raw = Image.new('RGB',(960,550),'red')
        raw.paste(Image.new('RGB',(958,480),'blue'),(1,69))
        normalized = geometry.normalize(raw)
        self.assertEqual(normalized.getpixel((960,10)), (255,0,0))
        self.assertEqual(normalized.getpixel((960,550)), (0,0,255))
        with self.assertRaises(ValueError):
            geometry.normalize(Image.new('RGB',(500,500)))

    def test_content_outside_window_is_rejected(self):
        with self.assertRaises(ValueError):
            GameGeometry((100,200,1060,750),(0,269,1059,749))
    def test_disagreement_never_authorizes_click(self):
        template = Detection('button', (0,0,20,20), .98, 'template', True)
        model = Detection('button', (80,80,100,100), .99, 'yolo', True)
        self.assertFalse(fuse(template, [model], model_enabled=True).accepted)

    def test_model_only_requires_validation(self):
        missing = Detection('button', None, .4, 'template', False)
        candidate = Detection('button', (0,0,20,20), .95, 'yolo', True)
        self.assertFalse(fuse(missing, [candidate]).accepted)
        self.assertTrue(fuse(missing, [candidate], model_enabled=True).accepted)

    def test_ambiguous_model_never_picks_largest_score(self):
        missing = Detection('button', None, 0, 'template')
        candidates = [Detection('button', (0,0,10,10), .99, 'yolo', True), Detection('button', (20,0,30,10), .9, 'yolo', True)]
        self.assertFalse(fuse(missing, candidates, model_enabled=True).accepted)

    def test_viewport_handles_offset_and_scaling(self):
        self.assertEqual(Viewport((100,200,900,600), (1600,800)).screen_point((800,400)), (500,400))
        with self.assertRaises(ValueError):
            Viewport((100,200,900,600), (1600,800)).screen_point((1600,400))

    def test_missing_model_is_explicit_and_does_not_download(self):
        with tempfile.TemporaryDirectory() as folder:
            manifest = Path(folder)/'detector.json'
            manifest.write_text(json.dumps({'mode':'shadow','file':'missing.onnx','sha256':'invalid'}))
            detector = OnnxDetector(manifest)
            self.assertIsNone(detector.session)
            self.assertTrue(detector.error)
            self.assertEqual(detector.predict(Image.new('RGB',(10,10))), [])


class ReturnTests(unittest.TestCase):
    def test_unknown_page_does_not_attempt_absent_catalog_close(self):
        s = Mock()
        s.run_context = None
        s.main_ready.return_value = False
        s.template.return_value = (False, .2, -1, -1)
        s._target_card_visible.side_effect = lambda frame: hs.Scheduler._target_card_visible(s, frame)
        with self.assertRaisesRegex(hs.PendingError, 'no known popup'):
            hs.Scheduler.prepare_main(s, hs.Account(4,'test',590))
        s.click_template.assert_not_called()

    def test_arena_ready_resume_does_not_detour_to_main(self):
        s = Mock()
        s.run_context = None
        s._next_combat_index.return_value = 7
        s._combat_limit.return_value = 26
        s.state.action_status.return_value = 'new'
        s._combat_ready_visible.return_value = True
        slot = hs.CombatSlot(hs.Account(4,'test',590), 'arena')
        hs.Scheduler._prepare_arena_slot(s, slot)
        s.prepare_main.assert_not_called()
        s.click_template.assert_not_called()
        s._claim_arena_tasks.assert_called_once()

    def arena_recovery_scheduler(self):
        s = Mock()
        s.run_context = None
        s.current_account_only = 'test'
        s.generic_current_tab_x = 590
        s.template.return_value = (False, 0, -1, -1)
        s._combat_active_visible.return_value = False
        s._combat_ready_visible.return_value = False
        s._generic_current_tab_is_active.return_value = True
        s.main_ready.return_value = True
        return s

    def test_arena_pending_main_reaches_reconciliation_without_clicks(self):
        s = self.arena_recovery_scheduler()
        hs.Scheduler.switch_account_for_combat(s, hs.Account(4,'test',590), 'arena', True)
        self.assertEqual(s.main_ready.call_count, 3)
        s.click_template.assert_not_called()
        s.chrome_click.assert_not_called()
        s.commit_action.assert_not_called()

    def test_arena_pending_main_rejects_changed_tab(self):
        s = self.arena_recovery_scheduler()
        s._generic_current_tab_is_active.return_value = False
        with self.assertRaisesRegex(hs.PendingError, 'tab changed'):
            hs.Scheduler.switch_account_for_combat(s, hs.Account(4,'test',590), 'arena', True)
        s.click_template.assert_not_called()

    def test_arena_pending_transient_main_does_not_reconcile(self):
        s = self.arena_recovery_scheduler()
        s.main_ready.side_effect = [True, False, True]
        with patch.object(hs.time, 'monotonic', side_effect=[0, 1, 2, 13]):
            with self.assertRaisesRegex(hs.PendingError, 'no positive'):
                hs.Scheduler.switch_account_for_combat(s, hs.Account(4,'test',590), 'arena', True)
        s.save_raw_evidence.assert_not_called()

    def scheduler(self):
        s = Mock()
        s.run_context = None
        s.state.accounts = {'test': {'actions': {}}}
        s.verify_identity.return_value = (True, {'test': 1.})
        s._combat_active_visible.return_value = False
        s.template.side_effect = lambda name, *a, **k: (name == 'leaf_village_name_current.bmp', 1, 10, 10)
        s.main_ready.return_value = True
        return s

    def test_already_in_village_confirms_three_frames_without_click(self):
        s = self.scheduler()
        self.assertTrue(return_to_leaf(s, hs.Account(1,'test',180)))
        self.assertEqual(s.capture.call_count, 4)  # One spring probe plus three village frames.
        self.assertEqual(s.main_ready.call_count, 3)
        s.click_template.assert_not_called()

    def test_unsettled_action_blocks_navigation_outside_village(self):
        s = self.scheduler()
        s.state.accounts['test']['actions']['battle'] = {'status':'pending'}
        s.main_ready.return_value = False
        with self.assertRaises(hs.PendingError):
            return_to_leaf(s, hs.Account(1,'test',180))
        s.click_template.assert_not_called()

    def test_pending_mail_can_observe_village_without_settling_or_clicking(self):
        s = self.scheduler()
        s.state.accounts['test']['actions']['mail_claim_all'] = {'status': 'pending'}
        self.assertTrue(return_to_leaf(s, hs.Account(1,'test',180)))
        self.assertEqual(s.capture.call_count, 4)
        self.assertEqual(s.main_ready.call_count, 3)
        self.assertEqual(s.state.accounts['test']['actions']['mail_claim_all']['status'], 'pending')
        s.click_template.assert_not_called()
        s.commit_action.assert_not_called()

    def test_pending_mail_still_blocks_navigation_from_unknown_page(self):
        s = self.scheduler()
        s.main_ready.return_value = False
        s.state.accounts['test']['actions']['mail_claim_all'] = {'status': 'pending'}
        with patch('hyol_navigation.time.monotonic', side_effect=[0, 0, 1, 61]):
            with self.assertRaises(hs.PendingError):
                return_to_leaf(s, hs.Account(1,'test',180))
        s.click_template.assert_not_called()

    def test_active_battle_blocks_return(self):
        s = self.scheduler()
        s._combat_active_visible.return_value = True
        with self.assertRaises(hs.PendingError):
            return_to_leaf(s, hs.Account(1,'test',180))
        s.click_template.assert_not_called()


class DatasetTests(unittest.TestCase):
    def test_replay_one_prediction_cannot_cover_two_truth_boxes(self):
        from tools.replay_detector import score_frame
        truth = [{'target':'a','xyxy':[0,0,20,20]}] * 2
        result = score_frame(truth, [Detection('a',(0,0,20,20),.9,'yolo',True)], ['a'])['a']
        self.assertEqual((result['tp'],result['fp'],result['fn']), (1,0,1))

    def test_replay_negative_and_wrong_location_are_false_positives(self):
        from tools.replay_detector import score_frame
        predictions = [Detection('a',(100,100,120,120),.99,'yolo',True)]
        empty = score_frame([], predictions, ['a'])['a']
        wrong = score_frame([{'target':'a','xyxy':[0,0,20,20]}], predictions, ['a'])['a']
        self.assertEqual((empty['tp'],empty['fp'],empty['fn']), (0,1,0))
        self.assertEqual((wrong['tp'],wrong['fp'],wrong['fn']), (0,1,1))

    def test_unreviewed_and_session_leakage_rejected(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            Image.new('RGB', (10,10), 'red').save(root/'a.png')
            Image.new('RGB', (10,10), 'blue').save(root/'b.png')
            sample = dict(image='a.png', session='one', split='train', reviewed=False, boxes=[])
            manifest = root/'review.json'
            data = dict(classes=['button'], samples=[sample])
            manifest.write_text(json.dumps(data))
            with self.assertRaisesRegex(ValueError, 'unreviewed'):
                prepare(manifest, root/'out')
            sample['reviewed'] = True
            data['samples'].append({**sample, 'image':'b.png','split':'test'})
            manifest.write_text(json.dumps(data))
            with self.assertRaisesRegex(ValueError, 'session leakage'):
                prepare(manifest, root/'out')


if __name__ == '__main__':
    unittest.main()
