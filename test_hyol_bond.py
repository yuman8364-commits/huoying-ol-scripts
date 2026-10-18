from __future__ import annotations

import unittest
import json
from pathlib import Path
import tempfile
from types import SimpleNamespace
from unittest.mock import Mock, patch

from PIL import Image
import hyol_bond as bond


class BondClientTests(unittest.TestCase):
    def transition_runner(self):
        runner = object.__new__(bond.BondRunner)
        scheduler = Mock()
        scheduler.config = {"TeamActivityWaitSeconds": "600"}
        scheduler.capture.return_value = Image.new("RGB",(1920,1032))
        scheduler.offline_dialog_visible.return_value = False
        scheduler.template.return_value = (False, 0, 0, 0)
        scheduler._team_battle_start_visible.return_value = False
        scheduler._team_result_template.return_value = None
        scheduler._team_reward_result_visible.return_value = False
        scheduler._battle_auto_on_visible.return_value = False
        runner.scheduler = scheduler
        runner.request = {"run_id": "r1", "phase": "running", "completed": 2}
        runner.ready_visible = Mock(return_value=False)
        return runner

    def test_unknown_transition_times_out_without_click_or_count_change(self):
        runner = self.transition_runner()
        now = [0.0]
        runner.scheduler.wait_interruptible.side_effect = lambda seconds: now.__setitem__(0, now[0] + 1)
        with patch.object(bond.time, "monotonic", side_effect=lambda: now[0]):
            with self.assertRaisesRegex(bond.hs.BattleStalled, "30秒无变化"):
                runner.poll_battle(3)
        self.assertEqual(now[0], 30)
        self.assertEqual(runner.request["completed"], 2)
        self.assertEqual(runner.request["phase"], "running")
        runner.scheduler.click_template.assert_not_called()

    def test_loading_anchor_flicker_cannot_restart_transition_timer(self):
        runner = self.transition_runner()
        now = [0.0]
        runner.scheduler.wait_interruptible.side_effect = lambda seconds: now.__setitem__(0, now[0] + 1)
        runner.scheduler.template.side_effect = lambda name, *a, **kw: (
            name == "arena_loading_anchor.bmp" and int(now[0]) % 2 == 0, 1, 0, 0)
        with patch.object(bond.time, "monotonic", side_effect=lambda: now[0]):
            with self.assertRaisesRegex(bond.hs.BattleStalled, "30秒无变化"):
                runner.poll_battle(3)
        self.assertEqual(now[0], 30)
        runner.scheduler.click_template.assert_not_called()

    def test_unchanged_recognized_battle_stops_at_thirty_seconds_without_inventing_result(self):
        runner = self.transition_runner()
        now = [0.0]
        def wait(seconds):
            now[0] += 1
        runner.scheduler.wait_interruptible.side_effect = wait
        runner.scheduler._battle_auto_on_visible.return_value = True
        with patch.object(bond.time, "monotonic", side_effect=lambda: now[0]):
            with self.assertRaises(bond.hs.BattleStalled):
                runner.poll_battle(3)
        self.assertEqual(now[0], 30)
        self.assertEqual(runner.request['completed'], 2)
        self.assertEqual(runner.request['phase'], 'running')
        runner.scheduler.click_template.assert_not_called()

    def test_completed_control_reappearance_never_clicks_again(self):
        runner = self.transition_runner()
        runner.scheduler.state.action_status.return_value = "done"
        runner.scheduler.template.return_value = (True, 1, 0, 0)
        with self.assertRaisesRegex(bond.hs.PendingError, "refusing duplicate"):
            runner.click_once_and_wait_absent("button.bmp", (0, 0, 10, 10), 1, 1, "start", 8)
        runner.scheduler.click_template.assert_not_called()

    def test_new_request_starts_at_zero(self) -> None:
        request = bond.prepare_request({}, 3, "now")
        self.assertEqual("running", request["status"])
        self.assertEqual(3, request["requested"])
        self.assertEqual(0, request["completed"])
        self.assertEqual("ready", request["phase"])

    def test_running_request_resumes_completed_count(self) -> None:
        saved = {"run_id": "r1", "status": "running", "requested": 3, "completed": 1, "phase": "ready"}
        request = bond.prepare_request(saved, 3, "later")
        self.assertEqual("r1", request["run_id"])
        self.assertEqual(1, request["completed"])
        self.assertEqual(3, request["requested"])

    def test_running_request_accepts_changed_count(self) -> None:
        saved = {"run_id": "r1", "status": "running", "requested": 1, "completed": 0, "phase": "ready"}
        request = bond.prepare_request(saved, 4, "later")
        self.assertEqual("r1", request["run_id"])
        self.assertEqual(4, request["requested"])

    def test_done_request_starts_new_round(self) -> None:
        saved = {"run_id": "r1", "status": "done", "requested": 2, "completed": 2, "phase": "ready"}
        request = bond.prepare_request(saved, 2, "later")
        self.assertNotEqual("r1", request["run_id"])
        self.assertEqual(0, request["completed"])

    def test_timed_out_request_preserves_inflight_battle_and_completed_count(self) -> None:
        saved = {"run_id": "r1", "status": "game_timeout", "requested": 3,
                 "completed": 2, "phase": "running", "last_error": "loading timeout"}
        request = bond.prepare_request(saved, 3, "later")
        self.assertEqual((request['run_id'], request['completed'], request['phase']), ('r1',2,'running'))
        self.assertEqual(request['status'], 'running')
        self.assertEqual(saved['status'], 'game_timeout')

    def test_count_range(self) -> None:
        for value in (0, 100):
            with self.assertRaises(ValueError):
                bond.prepare_request({}, value, "now")

    def test_ready_page_requires_anchor_and_match_button(self) -> None:
        scheduler = Mock()
        scheduler.capture.return_value = object()
        scheduler.template.side_effect = [(True, 1.0, 1, 1), (True, 1.0, 1, 1)]
        runner = object.__new__(bond.BondRunner)
        runner.scheduler = scheduler
        self.assertTrue(runner.ready_visible())
        self.assertEqual(2, scheduler.template.call_count)

    def test_game_timeout_has_explicit_user_message(self) -> None:
        message = bond.explain_failure("game timeout: bond battle did not progress")
        self.assertIn("游戏超时", message)
        self.assertIn("脚本已停止", message)

    def test_loading_watchdog_compatibility_boundary_is_thirty_seconds(self) -> None:
        self.assertFalse(bond.loading_timed_out(10.0, 39.9))
        self.assertTrue(bond.loading_timed_out(10.0, 40.0))

    def test_ready_page_must_remain_stable_without_result_overlay(self) -> None:
        scheduler = Mock()
        scheduler.capture.return_value = object()
        scheduler._team_result_template.return_value = None
        scheduler._team_reward_result_visible.side_effect = [False, True]
        runner = object.__new__(bond.BondRunner)
        runner.scheduler = scheduler
        runner.ready_visible = Mock(return_value=True)
        self.assertFalse(runner.ready_visible_stable(seconds=0, samples=3))
        self.assertEqual(2, scheduler.capture.call_count)

    def test_unknown_corrupt_or_inconsistent_phase_never_creates_new_request(self):
        saved = {'run_id': 'original', 'status': 'running', 'requested': 3,
                 'completed': 1, 'phase': 'running'}
        for changes in ({'status': 'unknown'}, {'phase': 'unknown'}, {'completed': 4},
                        {'requested': True}, {'run_id': ''}, {'status': 'done'}):
            with self.subTest(changes=changes), self.assertRaises(bond.hs.PendingError):
                bond.prepare_request({**saved, **changes}, 5, 'later')

    def test_unified_resume_preserves_target_and_done_needs_explicit_new_round(self):
        saved = {'run_id': 'original', 'owner': 'role', 'status': 'game_timeout',
                 'requested': 3, 'completed': 1, 'phase': 'running'}
        original = dict(saved)
        request = bond.prepare_request(saved, 8, 'later', preserve_target=True, new_round=True)
        self.assertEqual((request['run_id'], request['requested'], request['completed'], request['phase']),
                         ('original', 3, 1, 'running'))
        self.assertEqual(saved, original)
        saved.update(status='done', completed=3, phase='ready')
        same = bond.prepare_request(saved, 8, 'later', preserve_target=True, new_round=False)
        self.assertEqual(same, saved)
        fresh = bond.prepare_request(saved, 8, 'later', preserve_target=True, new_round=True)
        self.assertNotEqual(fresh['run_id'], 'original')
        self.assertEqual((fresh['requested'], fresh['completed']), (8, 0))


class NamedBondContinuationTests(unittest.TestCase):
    def setUp(self):
        folder = tempfile.TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        self.root = Path(folder.name)
        self.shared = self.root/'state'
        self.shared.mkdir()
        self.account = bond.hs.Account(2, 'verified-role', 180)
        self.runner = object.__new__(bond.BondRunner)
        self.runner.scheduler = SimpleNamespace(shared_state_directory=self.shared,
            bind_generic_current_window=Mock(return_value=self.account))
        self.runner.requested = 8
        self.runner.require_identity = True
        self.runner.new_round = False
        self.runner.request = {}
        self.runner.legacy_state_paths = (self.root/'bond_client_state.json',)
        self.path = self.shared/'bond'/(bond.hs.slug(self.account.name)+'.json')
        state_patch = patch.object(bond.hs, 'STATE_DIR', self.shared)
        state_patch.start()
        self.addCleanup(state_patch.stop)

    def write(self, path, value):
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(value), encoding='utf-8')

    def original(self, **changes):
        return {'run_id': 'original', 'owner': self.account.name, 'status': 'game_timeout',
                'requested': 3, 'completed': 1, 'phase': 'running', **changes}

    def test_canonical_cross_entry_resume_keeps_original_target_and_run(self):
        old = self.original()
        self.write(self.path, old)
        before = self.path.read_bytes()
        self.runner.bind_identity_request()
        self.assertEqual(self.runner.request['run_id'], 'original')
        self.assertEqual(self.runner.request['requested'], 3)
        self.assertEqual(self.runner.request['phase'], 'running')
        self.assertEqual(self.runner._unified_owner, self.account.name)
        self.assertEqual(self.runner.account, self.account)
        self.assertEqual(self.runner.request_path, self.path)
        self.assertEqual(self.path.read_bytes(), before)  # Binding does not rewrite intent.
        self.runner.scheduler.bind_generic_current_window.assert_called_once_with(require_game_context=False)

    def test_completed_named_request_is_reused_and_new_round_is_explicit(self):
        self.write(self.path, self.original(status='done', phase='ready', completed=3))
        self.runner.bind_identity_request()
        self.assertEqual((self.runner.request['run_id'], self.runner.request['status']), ('original', 'done'))
        self.runner.new_round = True
        self.runner.bind_identity_request()
        self.assertNotEqual(self.runner.request['run_id'], 'original')
        self.assertEqual((self.runner.request['requested'], self.runner.request['completed']), (8, 0))

    def test_legacy_same_owner_is_imported_without_replacing_source(self):
        legacy = self.runner.legacy_state_paths[0]
        self.write(legacy, self.original())
        before = legacy.read_bytes()
        self.runner.bind_identity_request()
        self.assertEqual(self.runner.request['run_id'], 'original')
        self.assertEqual(self.runner.request['requested'], 3)
        self.assertEqual(legacy.read_bytes(), before)
        self.runner.save_request()
        self.assertEqual(json.loads(self.path.read_text())['owner'], self.account.name)
        self.assertEqual(legacy.read_bytes(), before)

    def test_anonymous_or_wrong_owner_pending_never_creates_canonical_request(self):
        for owner in (None, 'other-role'):
            with self.subTest(owner=owner):
                legacy = self.runner.legacy_state_paths[0]
                self.write(legacy, self.original(owner=owner))
                before = legacy.read_bytes()
                with self.assertRaisesRegex(bond.hs.PendingError, '未关联'):
                    self.runner.bind_identity_request()
                self.assertFalse(self.path.exists())
                self.assertEqual(legacy.read_bytes(), before)
                self.assertEqual(self.runner.request, {})

    def test_unconfirmed_visible_account_never_links_old_checkpoint(self):
        self.runner.scheduler.bind_generic_current_window.return_value = bond.hs.Account(2, '当前窗口标签2', 180)
        self.runner.wait_for_named_identity = Mock()
        self.write(self.path, self.original())
        with self.assertRaisesRegex(bond.hs.PendingError, '未唯一确认'):
            self.runner.bind_identity_request()
        self.assertEqual(self.runner.request, {})

    def test_any_cross_day_or_legacy_entry_anonymous_pending_blocks_new_round(self):
        entry = self.root/'old-client'
        self.runner.legacy_state_paths = (entry/'bond_client_state.json',)
        self.runner.new_round = True
        for directory in (self.shared, entry/'state'):
            with self.subTest(directory=directory):
                path = directory/'2026-10-01'/(bond.hs.slug(bond.BOND_ACCOUNT.name)+'.json')
                self.write(path, {'actions': {'bond_original_battle_2_match': {'status': 'pending'}}})
                before = path.read_bytes()
                with self.assertRaisesRegex(bond.hs.PendingError, '匿名动作仍未决'):
                    self.runner.bind_identity_request()
                self.runner.scheduler.bind_generic_current_window.assert_not_called()
                self.assertEqual(path.read_bytes(), before)
                path.unlink()

    def test_distinct_pending_runs_and_advanced_legacy_progress_stay_pending(self):
        self.write(self.path, self.original())
        legacy = self.runner.legacy_state_paths[0]
        for changes in ({'run_id': 'another-run'}, {'completed': 2}, {'requested': 4}):
            with self.subTest(changes=changes):
                self.write(legacy, self.original(**changes))
                with self.assertRaises(bond.hs.PendingError):
                    self.runner.bind_identity_request()
                self.assertEqual(self.runner.request, {})

    def test_corrupt_source_never_creates_replacement(self):
        from hyol_checkpoints import CheckpointError
        legacy = self.runner.legacy_state_paths[0]
        legacy.write_text('{broken', encoding='utf-8')
        with self.assertRaises(CheckpointError):
            self.runner.bind_identity_request()
        self.assertFalse(self.path.exists())
        self.assertEqual(legacy.read_text(), '{broken')

    def test_named_identity_wait_observes_long_battle_without_any_click(self):
        s = Mock()
        s.shared_state_directory = self.shared
        s.offline_dialog_visible.return_value = False
        s._battle_auto_on_visible.return_value = True
        self.runner.scheduler = s
        now = [0.]
        s.capture.side_effect = lambda: Image.new('RGB',(1920,1032),(60 if int(now[0])%6==0 else 140,)*3)
        s.wait_interruptible.side_effect = lambda seconds: now.__setitem__(0, now[0]+seconds)
        with patch.object(bond.time, 'monotonic', side_effect=lambda: now[0]), \
             patch('hyol_checkpoints.match_identity', side_effect=lambda *args: (
                 self.account.name if now[0] >= 660 else None, None)):
            self.runner.wait_for_named_identity(bond.BOND_ACCOUNT)
        self.assertEqual(now[0], 660)
        s.click_template.assert_not_called()
        s.save_raw_evidence.assert_not_called()
        self.assertTrue(all(call.args == (3,) for call in s.wait_interruptible.call_args_list))

    def test_unchanged_identity_wait_uses_thirty_seconds_in_prep_and_loading(self):
        for loading, limit in ((False, 30), (True, 30)):
            with self.subTest(loading=loading):
                s = Mock()
                s.shared_state_directory = self.shared
                s.capture.return_value = Image.new("RGB",(1920,1032))
                s.offline_dialog_visible.return_value = False
                s._battle_auto_on_visible.return_value = False
                s._combat_active_visible.return_value = False
                s.template.side_effect = lambda name, *args, **kw: (
                    loading and name == 'arena_loading_anchor.bmp', 1., 0, 0)
                self.runner.scheduler = s
                now = [0.]
                s.wait_interruptible.side_effect = lambda seconds: now.__setitem__(0, now[0]+seconds)
                with patch.object(bond.time, 'monotonic', side_effect=lambda: now[0]), \
                     patch('hyol_checkpoints.match_identity', return_value=(None, None)), \
                     self.assertRaisesRegex(bond.hs.BattleStalled, '30秒无变化'):
                    self.runner.wait_for_named_identity(bond.BOND_ACCOUNT)
                self.assertEqual(now[0], limit)
                s.click_template.assert_not_called()
                s.save_raw_evidence.assert_called_once()

    def test_f12_interrupts_identity_wait_and_keeps_request_unchanged(self):
        s = Mock()
        s.shared_state_directory = self.shared
        s._battle_auto_on_visible.return_value = True
        s.wait_interruptible.side_effect = KeyboardInterrupt('F12')
        self.runner.scheduler = s
        self.runner.request = self.original()
        before = dict(self.runner.request)
        with patch('hyol_checkpoints.match_identity', return_value=(None, None)), self.assertRaises(KeyboardInterrupt):
            self.runner.wait_for_named_identity(bond.BOND_ACCOUNT)
        self.assertEqual(self.runner.request, before)
        s.click_template.assert_not_called()
        s.save_raw_evidence.assert_not_called()

    def test_binding_failure_and_f12_never_write_new_battle_intent(self):
        for failure, expected in ((bond.hs.PendingError('old identity unknown'), 2),
                                  (KeyboardInterrupt('F12'), 130)):
            with self.subTest(failure=type(failure).__name__):
                runner = object.__new__(bond.BondRunner)
                runner.scheduler = Mock()
                runner.requested = 3
                runner.request = {}
                runner.require_identity = True
                runner.bind_identity_request = Mock(side_effect=failure)
                runner.save_request = Mock()
                runner.start_battle = Mock()
                with patch.object(bond.hs, 'ForegroundRuntimeLease') as lease, \
                     patch('hyol_diagnostics.manual_pause_status', return_value={'paused': False}):
                    lease.return_value.acquire.return_value = True
                    self.assertEqual(runner.run(), expected)
                runner.save_request.assert_not_called()
                runner.start_battle.assert_not_called()
                self.assertEqual(runner.request, {})
                lease.return_value.release.assert_called_once()

    def recovery_runner(self):
        runner = object.__new__(bond.BondRunner)
        runner._account = self.account
        runner.scheduler = Mock()
        runner.scheduler.template.return_value = (False, 0., 0, 0)
        runner.scheduler._team_battle_start_visible.return_value = False
        runner.scheduler._team_reward_result_visible.return_value = False
        runner.scheduler._battle_auto_on_visible.return_value = False
        runner.scheduler._team_result_template.return_value = None
        runner.request = self.original(run_id='r1', requested=3, completed=2, phase='running')
        runner.save_request = Mock()
        runner.ready_visible = Mock(return_value=True)
        runner.ready_visible_stable = Mock(return_value=True)
        runner.click_once_and_wait_absent = Mock()
        return runner

    def test_f12_after_result_input_recovers_observation_and_only_same_battle_pending(self):
        runner = self.recovery_runner()
        evidence = self.root/'observed-result.png'
        from PIL import Image
        Image.new('RGB', (4,4), 'white').save(evidence)
        runner.scheduler.save_raw_evidence.return_value = evidence
        runner.scheduler._team_result_template.return_value = 'team_result_confirm.bmp'
        runner.click_once_and_wait_absent.side_effect = KeyboardInterrupt('F12 after input')
        with self.assertRaises(KeyboardInterrupt):
            runner.poll_battle(3)
        self.assertEqual(runner.request['completed'], 2)
        self.assertEqual(runner.request['phase'], 'running')
        self.assertEqual(runner.request['result_observation']['owner'], self.account.name)
        self.assertTrue(runner.result_was_observed(3))
        runner.save_request.assert_called_once()  # Actual observation persisted before input.

        runner.scheduler._team_result_template.return_value = None
        pending = {'bond_r1_battle_3_match', 'bond_r1_battle_3_result', 'bond_r1_battle_2_match'}
        runner.scheduler.state.action_status.side_effect = lambda name, action: 'pending' if action in pending else 'new'
        runner.click_once_and_wait_absent.reset_mock(side_effect=True)
        runner.poll_battle(3)
        runner.click_once_and_wait_absent.assert_not_called()
        self.assertEqual(runner.request['completed'], 3)
        self.assertEqual(runner.request['phase'], 'ready')
        self.assertEqual([call.args[1] for call in runner.scheduler.commit_action.call_args_list],
                         ['bond_r1_battle_3_match', 'bond_r1_battle_3_result'])

    def test_observation_missing_wrong_owner_run_or_battle_never_counts(self):
        evidence = self.root/'result.png'
        evidence.write_bytes(b'image')
        valid = {'run_id': 'r1', 'battle': 3, 'owner': self.account.name,
                 'template': 'team_result_confirm.bmp', 'evidence': str(evidence)}
        for changes in ({'owner': 'other-role'}, {'run_id': 'other-run'}, {'battle': 2},
                        {'evidence': str(self.root/'missing.png')}, {'template': 'unknown.bmp'}):
            with self.subTest(changes=changes):
                runner = self.recovery_runner()
                runner.request['result_observation'] = {**valid, **changes}
                with self.assertRaisesRegex(bond.hs.PendingError, 'without a positively confirmed result'):
                    runner.poll_battle(3)
                self.assertEqual(runner.request['completed'], 2)
                runner.scheduler.commit_action.assert_not_called()
                runner.click_once_and_wait_absent.assert_not_called()

    def test_multiple_pending_origins_cannot_be_cleared_by_one_result(self):
        runner = self.recovery_runner()
        runner.request['phase'] = 'result_confirmed'
        runner.scheduler.state.action_status.return_value = 'pending'
        runner.scheduler.commit_action.side_effect = bond.hs.PendingError('多个未决来源')
        with self.assertRaisesRegex(bond.hs.PendingError, '多个未决来源'):
            runner.poll_battle(3)
        self.assertEqual(runner.request['completed'], 2)
        runner.click_once_and_wait_absent.assert_not_called()


class StandaloneBondGuiTests(unittest.TestCase):
    def test_real_new_round_checkbox_is_explicit_and_consumed_once(self):
        folder = tempfile.TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        with patch.object(bond, 'BOND_RESULT_PATH', Path(folder.name)/'result.json'), \
             patch('hyol_client_tools.add_diagnostic_controls'):
            app = bond.BondClient()
        app.withdraw()
        self.addCleanup(app.destroy)
        app.update_idletasks()
        self.assertFalse(app.new_round.get())
        app.new_round.set(True)
        fake = SimpleNamespace(scheduler=Mock(), run=Mock(return_value=2))
        with patch.object(bond.threading, 'Thread') as thread, \
             patch.object(bond, 'BondRunner', return_value=fake) as create:
            thread.return_value.is_alive.return_value = False
            app.start()
            thread.call_args.kwargs['target']()
        self.assertTrue(create.call_args.kwargs['new_round'])
        self.assertFalse(app.new_round.get())
        fake.run.assert_called_once()


if __name__ == "__main__":
    unittest.main()
