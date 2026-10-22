"""PR3 behavioral checks with real account/round ledgers and no desktop input."""
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
import unittest
from unittest.mock import Mock
from unittest.mock import patch
from PIL import Image

import hyol_scheduler as hs
import hyol_multi_solo as multi


class ArenaTargetTests(unittest.TestCase):
    def test_legacy_daily_marker_cannot_close_incomplete_account_goal(self):
        for target_mode in ('tickets', 'wins'):
            with self.subTest(target_mode=target_mode):
                account = hs.Account(1, 'account-a', 180)
                s = object.__new__(hs.Scheduler)
                s.state = SimpleNamespace(
                    global_state={'arena_requests': {'account-a': dict(
                        target_mode=target_mode, target=2, status='running', attempt_limit=10)}},
                    accounts={'account-a': {'actions': {'arena_attempt_victory_1': {'status': 'done'}}}},
                    action_status=lambda name, key: 'done' if key == 'arena_daily_victory' else 'new')
                s.arena_victory_target = 1  # Another account's last-prepared target.
                s._arena_daily_rewards_complete = Mock(return_value=True)
                self.assertEqual(1, s._next_combat_index(account, 'arena'))

    def test_persisted_complete_request_is_audited_without_new_battle(self):
        account = hs.Account(1, 'account-a', 180)
        rec = dict(status='done', task_completed=True, home_returned=True)
        runner = SimpleNamespace(record=lambda task: rec, save=Mock())
        s = SimpleNamespace(
            accounts=[account], by_name={account.name: account},
            state=SimpleNamespace(global_state={'arena_requests': {account.name: dict(
                status='done', target_mode='wins', requested_wins=1)}}),
            prepare_additional_arena_request=Mock(), run_combat_interleaved=Mock(),
            recheck_arena_rewards=Mock(), _arena_daily_rewards_complete=Mock(return_value=True),
            capture=Mock(return_value=Image.new('RGB', (1920,1032))),
            main_ready=Mock(return_value=True), template=Mock(return_value=(True,1,0,0)))
        c = object.__new__(multi.MultiSoloRunner)
        c.scheduler = s
        c.startup_returned = {account.name}
        c.manual_deferred = c.excluded = c.disconnected = set()
        c.deferred = {}
        c.arena_target_mode, c.arena_wins = 'wins', 1
        c.check_account = Mock(return_value=True)
        c.runner_for = Mock(return_value=runner)
        c.event = c.note_skipped = Mock()
        c._run_arena_stage()
        s.recheck_arena_rewards.assert_called_once_with(account)
        s.prepare_additional_arena_request.assert_not_called()
        s.run_combat_interleaved.assert_not_called()
        self.assertTrue(rec['task_completed'])


class SurvivalDelayedStartTests(unittest.TestCase):
    def setUp(self):
        self.folder = TemporaryDirectory()
        self.addCleanup(self.folder.cleanup)
        self.account = hs.Account(1, 'account-a', 180)
        self.s = object.__new__(hs.Scheduler)
        self.s.state = hs.StateStore([self.account.name], Path(self.folder.name), restore_history=False)
        self.s.state.accounts[self.account.name]['continuation_scope'] = 'current-run:account-a:round-1'
        self.action = 'survival_battle_step_1'
        self.s.state.set_action(self.account.name, self.action, 'pending')
        self.s.state.accounts[self.account.name]['actions'][self.action]['before_evidence'] = 'start-before.png'
        observations, key = self.s._survival_attempt_evidence(self.account, self.action)
        observations[key] = {'score_before': 80}
        self.frame = Image.new('RGB', (1920,1032))
        self.slot = hs.CombatSlot(self.account, 'survival', running=True)
        self.s.template = Mock(return_value=(False,0,0,0))
        self.s._battle_hud_visible = Mock(return_value=True)
        self.s._content_guard_once = Mock(return_value=self.frame)
        self.s.verify_identity = Mock(return_value=(True, {}))
        self.s.event = Mock()
        self.s.click_template = Mock()

    def test_later_hud_records_start_without_resending_click(self):
        self.assertTrue(self.s._reconcile_survival_start_transition(self.slot, self.frame))
        original = self.s.state.action_record(self.account.name, self.action)
        start = self.s.state.action_record(self.account.name, self.action + '_start')
        self.assertEqual('done', start['status'])
        self.assertEqual(original['operation_id'] + '_start', start['operation_id'])
        self.assertEqual('pending', original['status'])
        self.assertEqual('start-before.png', original['before_evidence'])
        self.s.click_template.assert_not_called()

    def test_unknown_page_or_changed_round_never_records_start(self):
        self.s._battle_hud_visible.return_value = False
        self.assertFalse(self.s._reconcile_survival_start_transition(self.slot, self.frame))
        self.s._content_guard_once.assert_not_called()
        self.s._battle_hud_visible.return_value = True
        self.s.state.accounts[self.account.name]['continuation_scope'] = 'different-run'
        with self.assertRaises(hs.PendingError):
            self.s._reconcile_survival_start_transition(self.slot, self.frame)
        self.assertEqual('new', self.s.state.action_status(self.account.name, self.action + '_start'))

    def test_changed_identity_cannot_commit_a_delayed_start(self):
        self.s.verify_identity.return_value = (False, {})
        with self.assertRaises(hs.PendingError):
            self.s._reconcile_survival_start_transition(self.slot, self.frame)
        self.assertEqual('new', self.s.state.action_status(self.account.name, self.action + '_start'))


class RankingCooperationTests(unittest.TestCase):
    def coordinator(self, *, fail=False, fail_custody=True, waiting=40):
        accounts = [hs.Account(1, 'account-a', 180), hs.Account(2, 'account-b', 315)]
        clock = {'now': 0.0}
        records, runners, calls = {}, {}, {a.name: [] for a in accounts}
        for account in accounts:
            rec = dict(status='pending', phase='end_sent', completed=0, remaining=1)
            records[account.name] = rec
            def step(account=account, rec=rec):
                calls[account.name].append(clock['now'])
                if fail and account.index == 1:
                    raise hs.SafetyError('unverified ranking content')
                if account.index == 1 and len(calls[account.name]) <= waiting:
                    return 'yield_waiting_result'
                rec.update(phase='ready', completed=1, remaining=0, zero_verified=True)
            runners[account.name] = SimpleNamespace(
                account=account, record=lambda task, rec=rec: rec, save=Mock(),
                ranking=step, return_to_main=Mock())
        s = SimpleNamespace(
            accounts=accounts, by_name={a.name:a for a in accounts}, active=None,
            state=SimpleNamespace(action_status=Mock(return_value='new')),
            check_pause_boundary=Mock(return_value=False), check_stop=Mock(),
            capture=Mock(return_value=Image.new('RGB', (1920,1032))),
            main_ready=Mock(return_value=True), template=Mock(return_value=(True,1,0,0)),
            _saved_tab_mapping=lambda: {a.name:a.tab_x for a in accounts},
            _generic_active_tab_x=lambda frame: s.active.tab_x,
            _combat_active_visible=Mock(return_value=fail_custody),
            ensure_account_main_before_release=Mock(return_value=False))
        s.switch_account_for_combat = lambda a, *args, **kwargs: setattr(s, 'active', a)
        s.wait_interruptible = lambda seconds: clock.update(now=clock['now']+seconds)
        c = object.__new__(multi.MultiSoloRunner)
        c.scheduler = s
        c.startup_returned = {a.name for a in accounts}
        c.manual_deferred = c.excluded = c.disconnected = set()
        c.deferred, c.deferred_at = {}, {}
        c.runner_for = lambda a: runners[a.name]
        c.event = c.save_progress = c.note_skipped = c.skip_disconnected = c.skip_stalled = Mock()
        return c, clock, records, calls

    def test_more_than_thirty_paced_polls_can_finish_before_timeout(self):
        c, clock, records, calls = self.coordinator()
        with patch.object(multi.time, 'monotonic', side_effect=lambda: clock['now']):
            self.assertTrue(c._run_ranking_stage())
        self.assertEqual(41, len(calls['account-a']))
        self.assertTrue(all(b-a >= .399 for a,b in zip(calls['account-a'], calls['account-a'][1:])))
        self.assertEqual('done', records['account-a']['status'])
        self.assertEqual('done', records['account-b']['status'])

    def test_waiting_timeout_is_elapsed_time_and_keeps_other_account_done(self):
        c, clock, records, calls = self.coordinator(waiting=1000)
        with patch.object(multi.time, 'monotonic', side_effect=lambda: clock['now']):
            self.assertTrue(c._run_ranking_stage())
        self.assertGreaterEqual(clock['now'], 30)
        self.assertEqual('pending', records['account-a']['status'])
        self.assertEqual('done', records['account-b']['status'])

    def test_safety_failure_is_quarantined_only_with_verified_custody(self):
        c, clock, records, calls = self.coordinator(fail=True)
        with patch.object(multi.time, 'monotonic', side_effect=lambda: clock['now']):
            self.assertTrue(c._run_ranking_stage())
        self.assertEqual('pending', records['account-a']['status'])
        self.assertEqual('done', records['account-b']['status'])
        self.assertIn('account-a', c.deferred)
        c, clock, records, calls = self.coordinator(fail=True, fail_custody=False)
        with patch.object(multi.time, 'monotonic', side_effect=lambda: clock['now']):
            with self.assertRaises(hs.LeaseBlocked):
                c._run_ranking_stage()
        self.assertEqual([], calls['account-b'])


if __name__ == '__main__':
    unittest.main()
