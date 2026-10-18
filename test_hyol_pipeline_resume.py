"""Current-run completion and activity availability regression."""
from copy import deepcopy
from datetime import date, datetime, timedelta
import json
from pathlib import Path
import tempfile
import threading
import unittest
from unittest.mock import Mock, patch

import hyol_team_pipeline as pipeline
import hyol_scheduler as hs
import hyol_runtime as runtime
import hyol_team_tasks as team_entry
import test_hyol_team_pipeline as fixtures
from hyol_client import UnifiedClient
from test_hyol_team_pipeline import coordinator


class PipelineCompletionTests(unittest.TestCase):
    def saved(self, owner):
        saved = deepcopy(owner.record)
        saved.update(request=owner.request_key(), source_day=date.today().isoformat(),
                     execution_day=date.today().isoformat(), session_token='session1', phase='solo')
        for index, slot in saved['teams'].items():
            slot.update(owner=owner.scheduler._bound_identity_names[int(index)],
                        members=[owner.scheduler._bound_identity_names[int(index)], 'member'+index],
                        tasks=dict(group=dict(status='done', completed=5, task_completed=True,
                                             home_returned=True),
                                   wanted=dict(status='new', phase='finishing', completed=1,
                                               daily_remaining=0, deadline_at=123,
                                               empty_evidence='original-empty.png')))
        saved['teams']['1']['status'] = 'pending'
        saved['teams']['4'].update(status='done', phase='done', dissolve_evidence='dissolved.png')
        owner.scheduler.state.day = date.today().isoformat()
        owner.scheduler.session_token = 'session1'
        return saved









    def finished_teams(self, owner):
        saved = self.saved(owner)
        members = []
        for slot in saved['teams'].values():
            slot.update(status='done',phase='done',dissolve_evidence='dissolved.png',home_returned=True)
            members.extend(slot['members'])
            for rec in slot['tasks'].values():
                rec.update(status='done',phase='done',task_completed=True,home_returned=True,
                           evidence='result.png',zero_evidence='zero.png',empty_evidence='empty.png',
                           daily_remaining=0,friendship_remaining=0,reward_exhausted=True)
        saved.update(required_account_count=len(members),expected_accounts=members)
        return saved





    def test_zero_exit_and_done_phase_need_every_original_account_and_task(self):
        owner = coordinator()
        owner.record = self.finished_teams(owner)
        owner.record.update(phase='done',solo_result=0,solo_eligible=owner.record['expected_accounts'])
        self.assertFalse(owner.completed())
        owner.record['solo_records'] = {name:dict(mail=dict(status='done',task_completed=True,home_returned=True))
                                        for name in owner.record['expected_accounts']}
        self.assertTrue(owner.completed())
        owner.record['solo_records']['captainA']['mail']['status'] = 'pending'
        self.assertFalse(owner.completed())
        owner.record['solo_records']['captainA']['mail']['status'] = 'done'
        del owner.record['required_account_count']
        self.assertFalse(owner.completed())


    def test_weekend_without_child_completion_proofs_keeps_batch_pending(self):
        owner = coordinator()
        owner.record = self.finished_teams(owner)
        owner.solo_tasks = ('weekend',)
        owner.record.update(phase='done',solo_result=0,solo_eligible=owner.record['expected_accounts'])
        owner.record['solo_records'] = {name:dict(weekend=dict(status='done',task_completed=True,home_returned=True))
                                        for name in owner.record['expected_accounts']}
        self.assertFalse(owner.completed())



    def test_start_failure_reason_precedes_completed_team_counts(self):
        runner = Mock()
        runner.run.side_effect = hs.PendingError('原组队选择不同，请恢复原组队选择')
        runner.summary.return_value = dict(detail='原批次未完成。组队已完成并解散2队。')
        events = []
        with tempfile.TemporaryDirectory() as folder:
            controller = runtime.RuntimeController(events.append, Path(folder))
            with patch.object(runtime, 'manual_pause_status', return_value={'paused':False}), \
                 patch.object(hs, 'ForegroundRuntimeLease'), \
                 patch.object(runtime, 'preflight_live', return_value=dict(status='ready',healthy=True,content_clicks=0)), \
                 patch.object(runtime, 'make_runner', return_value=runner):
                controller._run(runtime.RunRequest(mode='team_then_multi',tasks=('mail',),team_tasks=pipeline.TEAM_TASKS))
        result = next(row for row in events if row['event'] == 'unified_finished')
        self.assertEqual(result['status'], 'pending')
        self.assertTrue(result['detail'].startswith('原组队选择不同'))


class UnopenedGroupTests(unittest.TestCase):
    def setUp(self):
        fixtures.DriverTests.setUp(self)
        self.frame = {'daily_practice_anchor.bmp', 'daily_practice_close.bmp',
                      'daily_limited_tab_selected.bmp', 'daily_group_detail_current.bmp',
                      'daily_participate_disabled_current.bmp'}
        self.s.wait_until_main.return_value = True

    def skip_group(self):
        with patch.object(pipeline, 'group_open_now', return_value=False):
            for _ in range(3):
                self.driver.tick('group')

    def test_three_closed_frames_skip_only_this_run_without_crediting_a_battle(self):
        rec = self.driver.record('group')
        rec['completed'] = 3
        self.skip_group()
        self.assertEqual(rec['completed'], 3)
        self.assertEqual(rec['status'], 'new')
        self.assertFalse(rec['availability']['task_credit'])
        self.assertEqual(rec['availability']['reason'], '当前不在开放时间')
        self.assertEqual(self.owner._not_open_tasks, {(1, 'group')})
        self.assertEqual(self.s.click_template.call_count, 1)
        self.assertEqual(self.s.click_template.call_args.args[1], 'daily_practice_close.bmp')
        self.s.commit_action.assert_not_called()

    def test_next_team_tick_runs_wanted_and_only_then_dissolves(self):
        self.skip_group()
        self.owner.record['teams']['4']['status'] = 'done'
        self.owner.drivers = {1: self.driver}
        self.driver.tick = Mock(side_effect=lambda task: self.slot['tasks'].setdefault(task, {}).update(status='done'))
        self.owner.dissolve = Mock(side_effect=lambda index, slot: slot.update(status='done'))
        self.owner.run_teams()
        self.driver.tick.assert_called_once_with('wanted')
        self.owner.dissolve.assert_called_once()
        self.assertNotEqual(self.slot['tasks']['group']['status'], 'done')
        self.assertIn('不在开放时间', self.owner.summary()['detail'])
        self.assertFalse(self.owner.completed())

    def test_disabled_during_open_hours_keeps_pending_instead_of_guessing_closed(self):
        with patch.object(pipeline, 'group_open_now', return_value=True):
            with self.assertRaisesRegex(hs.PendingError, '不可参与'):
                self.driver.tick('group')
        self.assertFalse(getattr(self.owner, '_not_open_tasks', set()))
        self.s.click_template.assert_not_called()

    def test_original_unresolved_entry_cannot_be_skipped_as_not_open(self):
        self.actions[self.driver.action('group_0_entry_participate')] = 'pending'
        with patch.object(pipeline, 'group_open_now', return_value=False):
            with self.assertRaisesRegex(hs.PendingError, '原入场或战斗未决'):
                self.driver.tick('group')
        self.s.click_template.assert_not_called()
        self.assertEqual(self.actions[self.driver.action('group_0_entry_participate')], 'pending')

    def test_unknown_identity_prevents_skip_and_close(self):
        self.s.verify_identity.return_value = (False, {})
        with patch.object(pipeline, 'group_open_now', return_value=False):
            with self.assertRaisesRegex(hs.PendingError, '实名未确认'):
                self.driver.tick('group')
        self.s.click_template.assert_not_called()

    def test_closed_panel_without_verified_main_does_not_advance_to_wanted(self):
        self.s.wait_until_main.return_value = False
        with self.assertRaisesRegex(hs.PendingError, '关页回村未确认'):
            self.skip_group()
        self.assertFalse(getattr(self.owner, '_not_open_tasks', set()))
        self.assertEqual(self.driver.record('group')['completed'], 0)

    def test_changed_page_breaks_consecutive_closed_observations(self):
        with patch.object(pipeline, 'group_open_now', return_value=False):
            self.driver.tick('group')
            closed = self.frame
            self.frame = set()
            self.driver.tick('group')
            self.frame = closed
            self.driver.tick('group')
            self.driver.tick('group')
            self.assertFalse(getattr(self.owner, '_not_open_tasks', set()))
            self.driver.tick('group')
        self.assertEqual(self.owner._not_open_tasks, {(1, 'group')})

    def test_opening_boundary_uses_eight_am(self):
        self.assertFalse(pipeline.group_open_now(datetime(2026, 10, 14, 7, 59)))
        self.assertTrue(pipeline.group_open_now(datetime(2026, 10, 14, 8, 0)))
        self.assertTrue(pipeline.group_open_now(datetime(2026, 10, 14, 23, 59)))


if __name__ == '__main__':
    unittest.main()
