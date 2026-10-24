"""The unified desktop must show the actual independent-task blocking reason."""
from pathlib import Path
from types import SimpleNamespace
import tempfile
import threading
import unittest
from unittest.mock import Mock, patch
import hyol_bond as bond
import hyol_runtime as runtime


class IndependentFeedbackTests(unittest.TestCase):
    def bond_runner(self):
        runner = object.__new__(bond.BondRunner)
        runner.scheduler = SimpleNamespace(event=Mock(), stop=threading.Event())
        runner.request = {}
        runner.run = Mock(return_value=2)
        return runner

    def execute(self, runner, request):
        with tempfile.TemporaryDirectory() as temp:
            events = []
            controller = runtime.RuntimeController(events.append, Path(temp))
            with patch.object(runtime, 'preflight_live', return_value={'status':'ready','healthy':True,'content_clicks':0}), \
                 patch.object(runtime, 'manual_pause_status', return_value={'paused':False}), \
                 patch.object(runtime, 'shared_directory', return_value=Path(temp)/'state'), \
                 patch.object(runtime, 'make_runner', return_value=runner), \
                 patch.object(runtime.hs, 'atomic_json'), patch.object(runtime.hs, 'ForegroundRuntimeLease') as lease:
                lease.return_value.acquire.return_value = True
                controller._run(request)
            return next(e for e in events if e['event']=='unified_finished')

    def test_bond_event_retains_the_exact_failure_before_dispatch(self):
        runner = self.bond_runner()
        runner.event('bond_command_summary', result=2, status='pending', detail='原羁绊账号未关联，保留原第4场')
        self.assertEqual(runner.last_summary['detail'], '原羁绊账号未关联，保留原第4场')
        self.assertEqual(runner.last_summary['result'], 2)
        runner.scheduler.event.assert_called_once()

    def test_bond_specific_blocking_reason_reaches_desktop_receipt_without_finish(self):
        runner = self.bond_runner()
        runner.event('bond_command_summary', result=2, status='pending', detail='原羁绊账号未关联，保留原第4场')
        receipt = self.execute(runner, runtime.RunRequest(mode='bond', final_managed=False))
        self.assertEqual(receipt['detail'], '原羁绊账号未关联，保留原第4场')
        self.assertEqual(receipt['status'], 'pending')
        self.assertEqual(receipt['result'], 2)
        self.assertEqual(receipt['runner_summary'], runner.last_summary)

    def test_closed_weekend_has_a_date_and_reason_without_becoming_complete(self):
        runner = SimpleNamespace(run=Mock(return_value=0), scheduler=SimpleNamespace(stop=threading.Event()),
            records={}, calendar_plan=dict(execution_day='2026-10-23', effective_tasks=[],
                                          calendar_skipped={'weekend':'closed'}))
        receipt = self.execute(runner, runtime.RunRequest(mode='weekend', final_managed=False))
        self.assertIn('2026-10-23', receipt['detail'])
        self.assertIn('未开放', receipt['detail'])
        self.assertEqual(receipt['status'], 'pending')
        self.assertEqual(receipt['result'], 2)


if __name__ == '__main__':
    unittest.main()
