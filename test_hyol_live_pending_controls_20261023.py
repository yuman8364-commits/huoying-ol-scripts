"""Keep unknown gameplay pending while bounding waits and retiring expired UI."""
from copy import deepcopy
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

import hyol_daily_tasks as daily
import hyol_ranking as ranking
import hyol_scheduler as hs
from hyol_run_context import RunContext
import test_hyol_ranking as ranking_fixtures


class UnchangedRankingTests(unittest.TestCase):
    def runner(self, phase, *, interleave):
        fixture = ranking_fixtures.RankingTests()
        self.addCleanup(fixture.doCleanups)
        r = fixture.runner(5)
        r.rec.update(before=5, phase=phase, status='pending')
        r.actions.update(daily_ranking_1_start='pending', daily_ranking_1_confirm='pending')
        r.interleave_after_end = interleave
        # An old unbounded loop must fail this test quickly rather than hang.
        r.scheduler.capture.side_effect = [r.scheduler.capture.return_value] * 6 + [AssertionError('unbounded wait')]
        return r

    def test_all_sent_phases_yield_without_repeating_any_input(self):
        for phase in ('start_pending', 'confirmed', 'end_pending', 'end_sent', 'result_sent'):
            with self.subTest(phase=phase):
                r = self.runner(phase, interleave=True)
                before = deepcopy(r.actions)
                with patch.object(ranking, 'stable_remaining', return_value=5):
                    self.assertEqual('yield_waiting_result', ranking.run(r))
                self.assertEqual([], r.clicks)
                self.assertEqual(before, r.actions)
                self.assertEqual(phase, r.rec['phase'])
                self.assertEqual(0, r.rec['completed'])

    def test_single_account_unchanged_page_stops_at_thirty_seconds(self):
        r = self.runner('confirmed', interleave=False)
        before = deepcopy(r.actions)
        with patch.object(ranking, 'stable_remaining', return_value=5), \
                patch.object(ranking.time, 'monotonic', side_effect=[0, 0, 31]), \
                patch.object(hs, 'check_battle_watchdog'):
            with self.assertRaisesRegex(hs.PendingError, '连续30秒'):
                ranking.run(r)
        self.assertEqual([], r.clicks)
        self.assertEqual(before, r.actions)
        self.assertEqual('confirmed', r.rec['phase'])


class FinishedSpeedControlTests(unittest.TestCase):
    def setUp(self):
        self.folder = TemporaryDirectory()
        self.addCleanup(self.folder.cleanup)
        root = Path(self.folder.name)
        self.context = RunContext(root, root/'state', identifier='20261023_controls', started=True)
        self.account = hs.Account(1, 'role', 180)
        self.s = object.__new__(daily.DailyScheduler)
        self.s.run_context = self.context
        self.s.state = hs.StateStore([self.account.name], directory=self.context.directory, restore_history=False)
        self.s.event = Mock()
        self.s.click_template = Mock(side_effect=AssertionError('no game input allowed'))
        self.data = self.s.state.accounts[self.account.name]
        self.scope = f'survival:{self.context.identifier}:{self.account.account_id}'
        self.data['survival_operation_scope'] = self.scope
        self.speed = 'survival_run_2_battle_speed_x2_1'
        self.parents = ('survival_battle_step_1', 'survival_battle_step_1_start', 'survival_result_confirm_1')
        self.proof = root/'before.png'
        self.proof.write_bytes(b'preserved evidence')
        day = self.s.state.day
        self.data['actions'] = {
            key: dict(status='pending' if key == self.speed else 'done',
                      operation_id=f'{self.scope}:run_2:{key}', before_evidence=str(self.proof),
                      updated_at=day+('T10:00:00+08:00' if key == self.speed else 'T10:01:00+08:00'),
                      detail='survival score confirmed: 0->80' if key == self.parents[0] else 'original')
            for key in (self.speed, *self.parents)}
        self.data['survival_attempt_observations'] = {'run_2:survival_battle_step_1': {'score_before': 0}}

    def test_expired_toggle_preserves_original_unknown_and_is_idempotent(self):
        original = deepcopy(self.data['actions'][self.speed])
        parents = {key: deepcopy(self.data['actions'][key]) for key in self.parents}
        self.assertEqual(1, self.s.reconcile_survival_finished_speed_controls(self.account))
        row = self.data['actions'][self.speed]
        self.assertEqual('expired', row['status'])
        self.assertFalse(row['speed_verified'])
        self.assertEqual(original, row['original_pending'])
        self.assertEqual(parents, {key: self.data['actions'][key] for key in self.parents})
        self.assertEqual(0, self.s.reconcile_survival_finished_speed_controls(self.account))
        self.s.click_template.assert_not_called()

    def test_missing_or_unsettled_parent_leaves_pending_untouched(self):
        for key in self.parents:
            for status in ('new', 'pending', None):
                with self.subTest(key=key, status=status):
                    previous = deepcopy(self.data['actions'][key])
                    if status is None:
                        self.data['actions'].pop(key)
                    else:
                        self.data['actions'][key]['status'] = status
                    before = deepcopy(self.data)
                    self.assertEqual(0, self.s.reconcile_survival_finished_speed_controls(self.account))
                    self.assertEqual(before, self.data)
                    self.data['actions'][key] = previous

    def test_wrong_round_account_scope_or_conflict_cannot_expire(self):
        for key in (self.speed, *self.parents):
            for change in ({'operation_id': 'another-account:run_2:'+key},
                           {'operation_id': f'{self.scope}:run_1:{key}'},
                           {'checkpoint_conflict': True}, {'origin': {'day': '2000-01-01'}}):
                with self.subTest(key=key, change=change):
                    previous = deepcopy(self.data['actions'][key])
                    self.data['actions'][key].update(change)
                    before = deepcopy(self.data)
                    self.assertEqual(0, self.s.reconcile_survival_finished_speed_controls(self.account))
                    self.assertEqual(before, self.data)
                    self.data['actions'][key] = previous

    def test_unproven_points_or_missing_evidence_cannot_expire(self):
        for detail in ('survival score confirmed: 0->0', 'survival score confirmed: 80->140',
                       'survival score confirmed: 0->221', 'written before click'):
            self.data['actions'][self.parents[0]]['detail'] = detail
            before = deepcopy(self.data)
            self.assertEqual(0, self.s.reconcile_survival_finished_speed_controls(self.account))
            self.assertEqual(before, self.data)
        self.data['actions'][self.parents[0]]['detail'] = 'survival score confirmed: 0->80'
        self.proof.unlink()
        before = deepcopy(self.data)
        self.assertEqual(0, self.s.reconcile_survival_finished_speed_controls(self.account))
        self.assertEqual(before, self.data)

    def test_other_pending_actions_survive_and_expired_toggle_never_reclicks(self):
        self.data['actions']['mail_claim_all'] = {'status': 'pending', 'detail': 'unknown'}
        self.data['actions']['survival_reset'] = {'status': 'pending', 'detail': 'unknown'}
        others = {key: deepcopy(self.data['actions'][key]) for key in ('mail_claim_all', 'survival_reset')}
        self.assertEqual(1, self.s.reconcile_survival_finished_speed_controls(self.account))
        self.assertEqual(others, {key: self.data['actions'][key] for key in others})
        self.data['actions']['survival_second_run_started'] = {'status': 'done'}
        self.s.template = Mock(side_effect=AssertionError('expired toggle cannot inspect or retry a new control'))
        self.assertFalse(self.s._ensure_survival_battle_x2(SimpleNamespace(account=self.account, next_index=1), object()))
        self.s.click_template.assert_not_called()

    def test_legacy_or_other_state_directory_cannot_expire(self):
        before = deepcopy(self.data)
        self.s.run_context = None
        self.assertEqual(0, self.s.reconcile_survival_finished_speed_controls(self.account))
        self.s.run_context = self.context
        self.s.state.directory = Path(self.folder.name)/'another-run'
        self.assertEqual(0, self.s.reconcile_survival_finished_speed_controls(self.account))
        self.assertEqual(before, self.data)


if __name__ == '__main__':
    unittest.main()
