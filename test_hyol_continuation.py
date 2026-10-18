"""Regression coverage for composite execution and observed release failures."""
import queue
import threading
import json
import os
import tempfile
from pathlib import Path
import unittest
from types import SimpleNamespace
from unittest.mock import Mock, patch

import hyol_panel as panel
import hyol_scheduler as hs


class ContinuationTests(unittest.TestCase):
    def test_daily_survival_220_claims_and_closes_without_reset(self):
        from hyol_daily_tasks import DailyScheduler
        s = Mock()
        s.template.return_value = (True, 1, 0, 0)
        s.wait_template.return_value = True
        slot = SimpleNamespace(complete=False, account=hs.Account(1, 'test', 180))
        DailyScheduler._finish_survival_slot(s, slot)
        s.claim_survival_rewards.assert_called_once_with(slot.account)
        s.close_survival.assert_called_once_with(slot.account)
        s._ensure_survival_reset.assert_not_called()
        self.assertTrue(slot.complete)

    def managed_runner(self):
        import hyol_daily_tasks as daily
        r = object.__new__(daily.DailyRunner)
        r.task = 'all'
        r.selected_tasks = ('mail', 'ranking')
        r.records = {task: dict(status='done', task_completed=True, home_returned=True)
                     for task in r.selected_tasks}
        r.account = hs.Account(1, 'test', 180)
        r.scheduler = Mock()
        r.save = Mock()
        r.event = Mock()
        return r

    def test_solo_finish_enters_managed_after_all_tasks_and_keeps_it_active(self):
        r = self.managed_runner()
        r.finish_solo_managed()
        r.scheduler.run_managed_gate.assert_called_once_with(r.account)
        self.assertEqual(r.record('managed')['status'], 'done')
        r.scheduler.exit_managed_for_market.assert_not_called()

    def test_solo_unfinished_task_or_missing_home_blocks_managed(self):
        for updates in ({'status': 'pending'}, {'task_completed': False}, {'home_returned': False}):
            r = self.managed_runner()
            r.record('mail').update(updates)
            with self.assertRaises(hs.PendingError):
                r.finish_solo_managed()
            r.scheduler.run_managed_gate.assert_not_called()

    def test_solo_stop_before_managed_has_zero_managed_clicks(self):
        r = self.managed_runner()
        r.scheduler.check_stop.side_effect = KeyboardInterrupt()
        with self.assertRaises(KeyboardInterrupt):
            r.finish_solo_managed()
        r.scheduler.run_managed_gate.assert_not_called()

    def test_solo_unknown_managed_result_is_not_success(self):
        r = self.managed_runner()
        r.scheduler.run_managed_gate.side_effect = hs.PendingError('unknown outcome')
        with self.assertRaises(hs.PendingError):
            r.finish_solo_managed()
        self.assertNotEqual(r.record('managed')['status'], 'done')
        self.assertFalse(r.record('managed')['task_completed'])

    def test_solo_managed_resume_does_not_reopen_finished_ranking(self):
        r = self.managed_runner()
        r.record('managed')['status'] = 'pending'
        r.prepare_request()
        self.assertEqual(r.record('ranking')['status'], 'done')

    def test_latest_failure_is_not_hidden_by_older_success(self):
        import hyol_daily_tasks as daily
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            success = root / 'daily_all_result.json'
            failure = root / 'daily_all_failure.json'
            success.write_text(json.dumps({'result': 0}))
            failure.write_text(json.dumps({'result': 2, 'detail': 'pending'}))
            os.utime(success, (100, 100))
            os.utime(failure, (200, 200))
            self.assertEqual(daily.latest_client_result(root, 'all')['result'], 2)
            os.utime(success, (300, 300))
            self.assertEqual(daily.latest_client_result(root, 'all')['result'], 0)

    def test_selection_filters_orders_and_transmits_counts(self):
        commands = panel.composite_commands(['arena', 'team2', 'mail'], 3, 2, 4)
        self.assertEqual([c.kind for c in commands], ['mail', 'team2', 'arena'])
        self.assertEqual(commands[1].group_instance_count, 2)
        self.assertEqual(commands[1].strong_approach_count, 4)
        self.assertEqual(commands[2].arena_victory_target, 3)
        for invalid in ([], ['all_day'], ['mail', 'mail']):
            with self.assertRaises(ValueError):
                panel.composite_commands(invalid)

    def run_queue(self, outcomes, pause=False):
        events = queue.Queue()
        controller = panel.PanelTaskController(events)
        executed = []
        def execute(command):
            executed.append(command.kind)
            if pause:
                controller.request_pause()
            return outcomes[len(executed)-1]
        scheduler = Mock()
        scheduler.execute_command.side_effect = execute
        with patch.object(panel.hs, 'Scheduler', return_value=scheduler):
            controller.start(hs.SchedulerCommand('full'), sequence=panel.composite_commands(['mail', 'welfare']))
            controller._thread.join(3)
        self.assertFalse(controller.busy)
        messages = []
        while not events.empty():
            messages.append(events.get_nowait())
        return executed, messages[-1]['result']

    def test_queue_runs_only_selected_tasks(self):
        self.assertEqual(self.run_queue([0, 0]), (['mail', 'welfare'], 0))

    def test_pending_and_error_and_f12_stop_remaining_queue(self):
        for outcome in (2, 3, 130):
            self.assertEqual(self.run_queue([outcome]), (['mail'], outcome))

    def test_pause_at_successful_module_boundary_stops_queue(self):
        self.assertEqual(self.run_queue([0], pause=True), (['mail'], 130))

    def test_legacy_survival_reconciliation_is_scoped(self):
        s = object.__new__(hs.Scheduler)
        statuses = {
            'survival_result_confirm_1': 'pending', 'survival_battle_step_1': 'done',
            'survival_battle_step_1_start': 'done',
            'survival_result_confirm_2': 'pending', 'survival_battle_step_2': 'pending',
            'survival_battle_step_2_start': 'done',
            'survival_result_confirm_3': 'pending', 'survival_battle_step_3': 'done',
            'unrelated_purchase': 'pending',
        }
        s.state = Mock()
        s.state.action_status.side_effect = lambda name, key: statuses.get(key, 'new')
        s.commit_action = Mock()
        account = hs.Account(1, 'test', 180)
        s.reconcile_survival_result_confirmations(account)
        self.assertEqual(s.commit_action.call_count, 1)
        self.assertEqual(s.commit_action.call_args.args[1], 'survival_result_confirm_1')

    def test_survival_result_commits_confirmation_and_battle(self):
        s = object.__new__(hs.Scheduler)
        s.capture = Mock(return_value=object())
        s.template = lambda name, *a, **kw: (name == 'survival_result_confirm.bmp', 1, 0, 0)
        s.state = Mock()
        s.state.action_status.return_value = 'pending'
        s.state.action_status.side_effect = lambda name, key: 'new' if key.startswith('survival_result') else 'pending'
        s.click_template = Mock(return_value=True)
        s.wait_absent = Mock(return_value=True)
        s.wait_template = Mock(return_value=True)
        s.commit_action = Mock()
        s.event = Mock()
        slot = SimpleNamespace(account=hs.Account(1, 'test', 180), next_index=2, running=True)
        s._poll_survival_slot(slot)
        self.assertEqual([c.args[1] for c in s.commit_action.call_args_list],
                         ['survival_result_confirm_2', 'survival_battle_step_2'])
        self.assertFalse(slot.running)

    def test_missing_result_disappearance_does_not_commit(self):
        s = object.__new__(hs.Scheduler)
        s.capture = Mock(return_value=object())
        s.template = lambda name, *a, **kw: (name == 'survival_result_confirm.bmp', 1, 0, 0)
        s.state = Mock()
        s.state.action_status.return_value = 'new'
        s.click_template = Mock(return_value=True)
        s.wait_absent = Mock(return_value=False)
        s.commit_action = Mock()
        slot = SimpleNamespace(account=hs.Account(1, 'test', 180), next_index=2, running=True)
        with self.assertRaises(hs.PendingError):
            s._poll_survival_slot(slot)
        s.commit_action.assert_not_called()
        self.assertTrue(slot.running)

    def test_window_recovery_times_out_without_clicking(self):
        s = object.__new__(hs.Scheduler)
        s.stop = threading.Event()
        s.validate_geometry = Mock(return_value=False)
        s.event = Mock()
        s.wait_interruptible = Mock()
        with patch.object(hs.time, 'monotonic', side_effect=[0, 60]):
            with self.assertRaisesRegex(hs.PendingError, '60秒'):
                s.ensure_window()


if __name__ == '__main__':
    unittest.main()
