"""Final managed mode after a proven arena quota stop, without losing unknowns."""
from copy import deepcopy
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

from PIL import Image

import hyol_daily_tasks as daily
import hyol_scheduler as hs
from hyol_arena_outcome import exhausted_record
from hyol_errors import ArenaQuotaExhausted
from hyol_multi_solo import MultiSoloRunner
from hyol_runtime import RunRequest, completion_verified


class ManagedFinishTests(unittest.TestCase):
    def runner(self):
        temporary = TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        folder = Path(temporary.name)
        for name in ('quota', 'home', 'managed'):
            Image.new('RGB', (4, 4)).save(folder / (name + '.png'))
        account = hs.Account(1, 'fixture', 180)
        data = dict(actions={}, arena_quota_pending_rewards=dict(
            day='2026-11-09', tickets_exhausted=True, unfinished_tasks_observed=True,
            evidence=str(folder / 'quota.png')))
        request = dict(status='quota_exhausted', tickets_exhausted=True,
                       ticket_count_at_completion=0, home_returned=True,
                       home_evidence=str(folder / 'home.png'))
        scheduler = Mock()
        scheduler.state = SimpleNamespace(day='2026-11-09', accounts={account.name: data},
                                         global_state={'arena_requests': {account.name: request}})
        scheduler.daily_observation_enabled = False
        scheduler.content_click_count = 0
        scheduler.main_ready.return_value = True
        scheduler.save_raw_evidence.return_value = folder / 'managed.png'
        runner = object.__new__(daily.DailyRunner)
        runner.scheduler, runner.account = scheduler, account
        runner.task, runner.current = 'all', ''
        runner.selected_tasks = ('arena', 'welfare')
        runner.records = {'arena': exhausted_record(scheduler, account)}
        runner.managed_enabled = True
        runner.calendar_plan = {}
        runner.save, runner.event = Mock(), Mock()
        runner.bind, runner.prepare_request, runner.return_to_main = Mock(), Mock(), Mock()
        runner.arena = Mock(side_effect=ArenaQuotaExhausted('战令已用尽，奖励条件未达成'))
        runner.welfare = Mock()
        return runner

    def run_daily(self, runner):
        with patch.object(hs, 'ForegroundRuntimeLease') as lease, \
                patch.object(hs, 'atomic_json'), patch.object(daily, 'read_json', return_value={}):
            lease.return_value.acquire.return_value = True
            return runner.run()

    def test_daily_runs_later_welfare_and_managed_after_verified_quota_stop(self):
        runner = self.runner()
        original = deepcopy(runner.scheduler.state.accounts)
        self.assertEqual(2, self.run_daily(runner))
        runner.welfare.assert_called_once()
        runner.scheduler.run_managed_gate.assert_called_once_with(runner.account)
        self.assertEqual('done', runner.record('managed')['status'])
        self.assertTrue(runner.record('managed')['evidence'])
        self.assertEqual('pending', runner.record('arena')['status'])
        self.assertFalse(runner.record('arena')['task_completed'])
        self.assertTrue(runner.record('arena')['rewards_pending'])
        self.assertEqual(original, runner.scheduler.state.accounts)
        self.assertFalse(completion_verified(RunRequest(tasks=runner.selected_tasks), runner, 2))

    def test_user_disabled_managed_remains_disabled_after_quota_stop(self):
        runner = self.runner()
        runner.managed_enabled = False
        self.assertEqual(2, self.run_daily(runner))
        runner.scheduler.run_managed_gate.assert_not_called()

    def test_pending_reward_record_is_preserved_when_starting_managed_directly(self):
        runner = self.runner()
        runner.record('welfare').update(status='done', task_completed=True, home_returned=True)
        original = deepcopy(runner.record('arena'))
        runner.finish_solo_managed()
        runner.scheduler.run_managed_gate.assert_called_once_with(runner.account)
        self.assertEqual(original, runner.record('arena'))

    def test_quota_claim_alone_never_authorizes_managed(self):
        for defect in ('missing_quota', 'missing_home', 'changed_receipt', 'wrong_day',
                       'tickets_left', 'not_home', 'followup', 'unknown_battle',
                       'unknown_claim', 'no_authoritative_stop', 'another_pending_task'):
            with self.subTest(defect=defect):
                runner = self.runner()
                runner.record('welfare').update(status='done', task_completed=True, home_returned=True)
                rec = runner.record('arena')
                data = runner.scheduler.state.accounts[runner.account.name]
                request = runner.scheduler.state.global_state['arena_requests'][runner.account.name]
                if defect == 'missing_quota':
                    Path(rec['quota_evidence']).unlink()
                elif defect == 'missing_home':
                    Path(rec['home_evidence']).unlink()
                elif defect == 'changed_receipt':
                    rec['quota_evidence'] = rec['home_evidence']
                elif defect == 'wrong_day':
                    runner.scheduler.state.day = '2026-11-10'
                elif defect == 'tickets_left':
                    rec['tickets_remaining'] = 1
                elif defect == 'not_home':
                    rec['home_returned'] = False
                elif defect == 'followup':
                    rec['followup_check_due'] = True
                elif defect == 'unknown_battle':
                    data['actions']['arena_casual_battle_5'] = dict(status='pending')
                elif defect == 'unknown_claim':
                    data['actions']['arena_daily_reward_claim_1'] = dict(status='pending')
                elif defect == 'no_authoritative_stop':
                    request['status'] = 'running'
                elif defect == 'another_pending_task':
                    runner.record('welfare')['status'] = 'pending'
                before = deepcopy(runner.records)
                with self.assertRaises(hs.PendingError):
                    runner.finish_solo_managed()
                runner.scheduler.run_managed_gate.assert_not_called()
                self.assertEqual(before, runner.records)

    def test_unopened_escort_still_blocks_managed(self):
        runner = self.runner()
        runner.selected_tasks = ('arena', 'escort')
        runner.record('escort').update(status='pending', home_returned=True,
                                      task_completed=False, last_error='押镖未开放')
        with self.assertRaises(hs.PendingError):
            runner.finish_solo_managed()
        runner.scheduler.run_managed_gate.assert_not_called()

    def test_daily_unknown_quota_is_reported_as_managed_blocker(self):
        runner = self.runner()
        runner.scheduler.state.accounts[runner.account.name]['actions']['arena_casual_battle_5'] = dict(status='pending')
        self.assertEqual(2, self.run_daily(runner))
        runner.scheduler.run_managed_gate.assert_not_called()
        events = [call for call in runner.event.call_args_list
                  if call.kwargs.get('task') == 'managed' and '托管' in call.kwargs.get('detail', '')]
        self.assertTrue(events, 'A checked final-managed option must explain why it was skipped')

    def test_multi_finalizes_quota_account_independently_of_unknown_account(self):
        ready = self.runner()
        ready.record('welfare').update(status='done', task_completed=True, home_returned=True)
        unknown = self.runner()
        unknown.account = hs.Account(2, 'unknown', 315)
        unknown.record('arena')['outcome'] = 'unknown'
        unknown.finish_solo_managed = Mock()
        owner = object.__new__(MultiSoloRunner)
        owner.managed_enabled, owner.tasks = True, ready.selected_tasks
        owner.startup_returned = {ready.account.name, unknown.account.name}
        owner.excluded, owner.deferred = set(), {}
        owner.runners = {ready.account.name: ready, unknown.account.name: unknown}
        owner.event, owner.save_progress = Mock(), Mock()
        owner.check_account = Mock(return_value=True)
        owner.scheduler = SimpleNamespace(accounts=[ready.account, unknown.account],
            by_name={a.name: a for a in (ready.account, unknown.account)},
            check_pause_boundary=Mock(return_value=False), begin_task_observation=Mock(),
            end_task_observation=Mock())
        self.assertTrue(owner.finish_managed_accounts())
        ready.scheduler.run_managed_gate.assert_called_once_with(ready.account)
        unknown.finish_solo_managed.assert_not_called()
        self.assertEqual('pending', ready.record('arena')['status'])

    def test_failed_managed_start_does_not_change_arena_outcome(self):
        runner = self.runner()
        runner.scheduler.run_managed_gate.side_effect = hs.PendingError('托管启动未核实')
        original = deepcopy(runner.record('arena'))
        self.assertEqual(2, self.run_daily(runner))
        self.assertEqual('pending', runner.record('managed')['status'])
        self.assertEqual(original['quota_evidence'], runner.record('arena')['quota_evidence'])
        self.assertFalse(runner.record('arena')['task_completed'])


if __name__ == '__main__':
    unittest.main()
