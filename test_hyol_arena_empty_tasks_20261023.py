"""Explicit empty-task rows close rewards without inventing claim clicks."""
from pathlib import Path
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

import hyol_scheduler as hs


class ArenaEmptyTaskTests(unittest.TestCase):
    def setUp(self):
        self.account = hs.Account(6, 'same-account', 855)
        self.s = object.__new__(hs.Scheduler)
        self.s.diagnostics = SimpleNamespace(identifier='same-run')
        self.s.state = SimpleNamespace(accounts={self.account.name: {'actions': {}}}, day='2026-10-23',
                                       save_account=Mock(), set_action=Mock())
        self.s.state.action_status = lambda name, key: self.s.state.accounts[name]['actions'].get(key, {}).get('status', 'new')
        self.s.capture = Mock()
        self.s.event = Mock()
        self.s.wait_interruptible = Mock()
        self.s.save_raw_evidence = Mock(return_value=Path('two_empty_rows.png'))
        self.s.click_template = Mock()
        self.s._combat_active_visible = Mock(return_value=False)
        self.s._arena_victory_visible = Mock(return_value=False)
        self.s._arena_defeat_visible = Mock(return_value=False)
        def template(name, roi, frame, **kwargs):
            if name == 'arena_ready_anchor.bmp':
                ok = frame.ready
            elif name == 'arena_daily_no_tasks_current.bmp':
                ok = frame.rows[0 if roi[1] < 590 else 1]
            else:
                ok = False
            return ok, 1. if ok else 0., 0, 0
        self.s.template = Mock(side_effect=template)

    def frame(self, rows=(True, True), ready=True):
        return SimpleNamespace(rows=rows, ready=ready)

    def observe(self, first, second):
        self.s.capture.side_effect = [first, second]
        with patch('hyol_identity_overlays.clear_hover_preview'), patch.object(hs, 'check_battle_watchdog'):
            self.s._claim_arena_tasks(self.account)

    def proof(self, **overrides):
        return dict(status='verified', account=self.account.name, day='2026-10-23',
                    run_id='same-run', observations=2, evidence='two_empty_rows.png', **overrides)

    def test_two_empty_rows_on_two_ready_frames_complete_without_claim(self):
        self.observe(self.frame(), self.frame())
        self.assertTrue(self.s._arena_daily_rewards_complete(self.account))
        self.s.click_template.assert_not_called()
        self.assertFalse(self.s._arena_reward_claim_done(self.account, 2))
        self.assertEqual(2, self.s.state.accounts[self.account.name]['arena_daily_reward_exhaustion']['observations'])

    def test_one_empty_row_never_completes_both_tasks(self):
        self.observe(self.frame((True, False)), self.frame((True, False)))
        self.assertFalse(self.s._arena_daily_rewards_complete(self.account))
        self.s.click_template.assert_not_called()

    def test_transient_empty_rows_do_not_persist_completion(self):
        self.observe(self.frame(), self.frame((False, False)))
        self.assertFalse(self.s._arena_daily_rewards_complete(self.account))

    def test_unknown_page_cannot_authorize_empty_task_text(self):
        self.assertFalse(self.s._arena_daily_rows_exhausted(self.frame(ready=False)))

    def test_active_battle_or_matching_cannot_authorize_empty_tasks(self):
        self.s._combat_active_visible.return_value = True
        self.assertFalse(self.s._arena_daily_rows_exhausted(self.frame()))

    def test_pending_claim_is_not_reconciled_by_empty_rows(self):
        data = self.s.state.accounts[self.account.name]
        data['actions']['arena_daily_reward_claim_2'] = {'status': 'pending'}
        data['arena_daily_reward_exhaustion'] = self.proof()
        with self.assertRaisesRegex(hs.PendingError, 'outcome unknown'):
            self.s._claim_arena_tasks(self.account)
        self.s.capture.assert_not_called()
        self.assertFalse(self.s._arena_daily_rewards_complete(self.account))

    def test_other_day_run_account_or_single_observation_stays_incomplete(self):
        for key, value in (('day', '2026-10-22'), ('run_id', 'another-run'),
                           ('account', 'another-account'), ('observations', 1),
                           ('observations', '2'), ('observations', None), ('observations', True),
                           ('evidence', '')):
            with self.subTest(key=key):
                proof = self.proof()
                proof[key] = value
                self.s.state.accounts[self.account.name]['arena_daily_reward_exhaustion'] = proof
                self.assertFalse(self.s._arena_daily_rewards_complete(self.account))

    def test_two_actual_claims_remain_valid_without_empty_text(self):
        self.s.state.accounts[self.account.name]['actions'] = {
            'arena_daily_reward_claim_1': {'status': 'done'},
            'arena_daily_reward_claim_2': {'status': 'done'}}
        self.assertTrue(self.s._arena_daily_rewards_complete(self.account))
        self.s.capture.assert_not_called()


if __name__ == '__main__':
    unittest.main()
