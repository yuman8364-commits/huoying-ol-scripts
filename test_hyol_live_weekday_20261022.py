"""Weekday filtering preserves the request and pending receipts without UI input."""
from contextlib import nullcontext
from copy import deepcopy
from datetime import datetime
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

import hyol_daily_tasks as daily
import hyol_multi_solo as multi
import hyol_scheduler as hs
import hyol_team_pipeline as pipeline


class Thursday(datetime):
    @classmethod
    def now(cls, tz=None):
        return cls(2026, 10, 22, 20, 0)


class WeekdayCalendarTests(unittest.TestCase):
    def owner(self, folder, *, day='2026-10-22', tasks=('mail', 'daruma', 'weekend')):
        owner = object.__new__(pipeline.TeamPipelineRunner)
        owner.leaders, owner.tasks, owner.solo_tasks = (1,), ('wanted',), tasks
        owner.managed_enabled = False
        owner.path = Path(folder) / 'pipeline.json'
        owner.record = dict(execution_day=day, phase='done', required_account_count=2,
            expected_accounts=['a', 'b'], solo_eligible=['a', 'b'], solo_result=0,
            solo_exclusions={}, request=dict(solo_tasks=list(tasks)),
            teams={'1': dict(status='done', members=['a', 'b'], home_returned=True,
                dissolve_evidence='create.png', tasks={'wanted': dict(status='done',
                    task_completed=True, home_returned=True, evidence='wanted.png', empty_evidence='empty.png')})},
            solo_records={name: dict(mail=dict(status='done', task_completed=True, home_returned=True),
                daruma=dict(status='pending', last_error='original menu failure', operation_id='keep-original'))
                for name in ('a', 'b')})
        return owner

    def test_weekday_plan_keeps_requested_tasks_and_only_filters_calendar_activities(self):
        requested = ['mail', 'daruma', 'weekend', 'quiz']
        plan = daily.calendar_task_plan(requested, '2026-10-22')
        self.assertEqual(requested, plan['requested_tasks'])
        self.assertEqual(['mail', 'quiz'], plan['effective_tasks'])
        self.assertEqual({'daruma', 'weekend'}, set(plan['calendar_skipped']))
        for reason in plan['calendar_skipped'].values():
            self.assertEqual(dict(reason='non_weekend', execution_day='2026-10-22', weekday=3), reason)
        self.assertEqual(['mail', 'daruma', 'weekend', 'quiz'], requested)
        self.assertEqual(requested, daily.calendar_task_plan(requested, '2026-10-24')['effective_tasks'])

    def test_multi_constructor_uses_the_same_dated_effective_selection(self):
        scheduler = SimpleNamespace(accounts=[hs.Account(1, 'a', 180)], account_mode='dynamic')
        context = SimpleNamespace(activate=lambda: nullcontext())
        with patch.object(hs, 'read_config', return_value={'AccountDiscovery': 'automatic'}), \
             patch.object(multi, 'DailyScheduler', return_value=scheduler):
            runner = multi.MultiSoloRunner(('mail', 'daruma', 'weekend'),
                execution_day='2026-10-22', run_context=context)
        self.assertEqual(('mail', 'daruma', 'weekend'), runner.requested_tasks)
        self.assertEqual(('mail',), runner.tasks)
        self.assertEqual({'daruma', 'weekend'}, set(runner.calendar_plan['calendar_skipped']))

    def test_only_calendar_multi_request_stops_before_constructing_scheduler(self):
        with patch.object(multi, 'DailyScheduler') as scheduler:
            with self.assertRaisesRegex(ValueError, '2026-10-22非周末.*已按日历跳过'):
                multi.MultiSoloRunner(('daruma', 'weekend'), execution_day='2026-10-22')
        scheduler.assert_not_called()

    def test_daily_client_calendar_only_request_has_no_window_or_lease_and_preserves_pending(self):
        scheduler = SimpleNamespace(config={}, event=Mock())
        context = SimpleNamespace(activate=lambda: nullcontext())
        with patch.object(daily, 'datetime', Thursday), \
             patch.object(hs, 'read_config', return_value={}), \
             patch('hyol_run_context.current_run', return_value=context), \
             patch.object(daily, 'DailyScheduler', return_value=scheduler):
            runner = daily.DailyRunner('all', selected_tasks=('daruma', 'weekend'))
        runner.records = {'daruma': dict(status='pending', operation_id='original')}
        before = deepcopy(runner.records)
        with patch.object(hs, 'ForegroundRuntimeLease') as lease:
            self.assertEqual(0, runner.run())
        lease.assert_not_called()
        self.assertEqual((), runner.selected_tasks)
        self.assertEqual(('daruma', 'weekend'), runner.requested_tasks)
        self.assertEqual(before, runner.records)
        self.assertEqual('weekend_calendar_skip', scheduler.event.call_args.args[0])
        self.assertEqual('2026-10-22', scheduler.event.call_args.kwargs['execution_day'])

    def test_pipeline_weekday_completion_and_summary_use_validated_calendar_not_fake_done(self):
        with tempfile.TemporaryDirectory() as folder:
            owner = self.owner(folder)
            before = deepcopy(owner.record['solo_records'])
            original = deepcopy(owner.record['request'])
            owner.save()
            self.assertTrue(owner.completed())
            summary = owner.summary()
            self.assertEqual([], summary['solo_pending'])
            self.assertEqual(['mail'], summary['effective_solo_tasks'])
            self.assertEqual({'daruma', 'weekend'}, set(summary['calendar_skipped_tasks']))
            self.assertEqual(before, owner.record['solo_records'])
            self.assertEqual(original, owner.record['request'])
            self.assertEqual(('mail', 'daruma', 'weekend'), owner.solo_tasks)

    def test_calendar_skip_cannot_remove_mail_or_use_an_unbound_execution_day(self):
        with tempfile.TemporaryDirectory() as folder:
            owner = self.owner(folder)
            owner.save()
            owner.record['effective_solo_tasks'] = []
            owner.record['calendar_skipped_tasks']['mail'] = dict(reason='non_weekend', execution_day='2026-10-22', weekday=3)
            self.assertFalse(owner.completed())
            self.assertFalse(owner.summary()['calendar_valid'])
            owner.record.pop('execution_day')
            self.assertFalse(owner.completed())

    def test_saturday_still_requires_daruma_zero_checkpoint_and_home_evidence(self):
        with tempfile.TemporaryDirectory() as folder:
            owner = self.owner(folder, day='2026-10-24', tasks=('mail', 'daruma'))
            owner.save()
            self.assertEqual({}, owner.record['calendar_skipped_tasks'])
            with patch.object(daily, 'datetime', Thursday):
                self.assertFalse(owner.completed())  # Do not reinterpret Saturday as Thursday.
            for records in owner.record['solo_records'].values():
                records['daruma'].update(status='done', task_completed=True, home_returned=True,
                    remaining=0, zero_verified=True, zero_evidence='zero.png', checkpoint='daruma.json')
            self.assertTrue(owner.completed())
            owner.record['solo_records']['a']['daruma'].pop('checkpoint')
            self.assertFalse(owner.completed())

    def test_invalid_or_changed_request_cannot_authorize_calendar_skips(self):
        with self.assertRaises(ValueError):
            daily.calendar_task_plan(('daruma',), 'not-a-date')
        with tempfile.TemporaryDirectory() as folder:
            owner = self.owner(folder)
            owner.save()
            owner.record['request']['solo_tasks'] = ['mail', 'quiz', 'daruma', 'weekend']
            self.assertFalse(owner.completed())


if __name__ == '__main__':
    unittest.main()
