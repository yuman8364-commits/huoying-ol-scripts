"""Focused regression tests for independent team scheduling and VIP survival."""
from pathlib import Path
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

from PIL import Image

import hyol_scheduler as hs
import hyol_team_pipeline as pipeline
from hyol_combat_watchdog import BattleWatchdog
from test_hyol_team_pipeline import coordinator


class AdaptivePipelineTests(unittest.TestCase):
    def test_slow_team_does_not_block_second_team_or_next_round(self):
        owner = coordinator()
        owner.tasks = ('group_instance',)
        trace = []
        for index in owner.leaders:
            slot = owner.record['teams'][str(index)]
            rec = slot['tasks'].setdefault('group', {'status': 'running', 'phase': 'running',
                                                     'completed': 0, 'launched_battle': 1})
            driver = SimpleNamespace(account=owner.scheduler.accounts[index - 1],
                                     next_poll_seconds=8 if index == 1 else .6)
            def tick(task, index=index, rec=rec):
                trace.append((index, owner._poll_clock()))
                if index == 4 or len([item for item in trace if item[0] == index]) >= 2:
                    rec.update(status='done', phase='done', completed=1)
            driver.tick = tick
            driver.record = lambda task, rec=rec: rec
            owner.drivers[index] = driver
        owner.dissolve = Mock(side_effect=lambda index, slot: slot.update(status='done'))
        owner.run_teams()
        self.assertEqual([index for index, _ in trace], [1, 4, 1])
        self.assertEqual(owner.record['teams']['1']['status'], 'done')
        self.assertEqual(owner.record['teams']['4']['status'], 'done')

    def test_adaptive_poll_near_expected_result(self):
        rec = dict(status='running', phase='running', launched_battle=2, completed=1,
                   battle_timer_started_at=100.0, battle_durations=[48, 50, 52])
        driver = SimpleNamespace(next_poll_seconds=8, record=lambda task: rec)
        with patch.object(pipeline.time, 'time', return_value=103.0):
            self.assertEqual(pipeline.TeamPipelineRunner._adaptive_team_poll(driver, 'group'), 8.0)
        with patch.object(pipeline.time, 'time', return_value=144.0):
            self.assertEqual(pipeline.TeamPipelineRunner._adaptive_team_poll(driver, 'group'), 2.0)
        with patch.object(pipeline.time, 'time', return_value=170.0):
            self.assertEqual(pipeline.TeamPipelineRunner._adaptive_team_poll(driver, 'group'), 3.0)


class SurvivalVipTests(unittest.TestCase):
    def setUp(self):
        self.frame = Image.new('RGB', (1920, 1032))
        self.account = hs.Account(1, 'vip', 180)

    def test_membership_start_eighty_is_valid_score(self):
        s = object.__new__(hs.Scheduler)
        s._survival_full_score_visible = Mock(return_value=False)
        s._survival_score_twenty_visible = Mock(return_value=False)
        s.template = Mock(side_effect=lambda name, *args, **kwargs:
                          (name == 'survival_score_80.bmp', 1.0, 0, 0))
        self.assertEqual(s._survival_observed_score(self.frame), 80)

    def test_result_already_done_never_sends_second_click(self):
        s = object.__new__(hs.Scheduler)
        s.template = Mock(side_effect=lambda name, *args, **kwargs:
                          (name == 'survival_result_confirm.bmp', 1.0, 0, 0))
        s.state = SimpleNamespace(action_status=lambda name, action:
                                  'done' if action == 'survival_result_confirm_1' else 'pending')
        s.click_template = Mock()
        slot = hs.CombatSlot(self.account, 'survival', running=True)
        with self.assertRaises(hs.PendingError):
            s._poll_survival_slot(slot, self.frame)
        s.click_template.assert_not_called()

    def test_pending_result_reconciles_after_vip_score_advances(self):
        s = object.__new__(hs.Scheduler)
        action = 'survival_battle_step_1'
        actions = {action: {'status': 'pending'},
                   action + '_start': {'status': 'done'},
                   'survival_result_confirm_1': {'status': 'pending'}}
        s.state = SimpleNamespace(
            accounts={'vip': {'actions': actions,
                              'survival_attempt_observations': {
                                  'run_1:' + action: {'score_before': 80}}}},
            action_status=lambda name, label: actions.get(label, {}).get('status', 'new'),
            save_account=Mock())
        s._combat_ready_visible = Mock(return_value=True)
        s._combat_active_visible = Mock(return_value=False)
        s.verify_identity = Mock(return_value=(True, {}))
        s.capture = Mock(return_value=self.frame)
        s.wait_interruptible = Mock()
        s._survival_full_score_visible = Mock(return_value=False)
        s._survival_score_twenty_visible = Mock(return_value=False)
        s.template = Mock(side_effect=lambda name, *args, **kwargs:
                          (name == 'survival_score_140.bmp', 1.0, 0, 0))
        s.save_raw_evidence = Mock(return_value=Path('vip_reconcile.png'))
        s.event = Mock()
        s.commit_action = Mock(side_effect=lambda account, label, detail:
                               actions.setdefault(label, {}).update(status='done'))
        slot = hs.CombatSlot(self.account, 'survival', next_index=1, running=True)
        with patch.object(hs, 'grade_candidates', return_value=[]):
            self.assertTrue(s._observe_survival_pending_page(slot, self.frame))
        self.assertEqual(actions['survival_result_confirm_1']['status'], 'done')
        self.assertEqual(actions[action]['status'], 'done')
        self.assertFalse(slot.running)

    def test_unsampled_background_gap_does_not_trigger_fake_stall(self):
        watchdog = BattleWatchdog(idle_seconds=30)
        self.assertIsNone(watchdog.observe(self.frame, 0))
        self.assertIsNone(watchdog.observe(self.frame, 31))
        self.assertIsNone(watchdog.observe(self.frame, 45))
        stall = watchdog.observe(self.frame, 62)
        self.assertEqual(stall['reason'], 'unchanged_frame')


if __name__ == '__main__':
    unittest.main()
