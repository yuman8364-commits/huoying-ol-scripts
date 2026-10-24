"""Completed quizzes stay complete when their once-daily link disappears."""
from copy import deepcopy
from pathlib import Path
import threading
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

import hyol_daily_tasks as daily
import hyol_scheduler as hs


class CompletedQuizTests(unittest.TestCase):
    def runner(self, record):
        r = object.__new__(daily.DailyRunner)
        r.task, r.current = 'all', ''
        r.selected_tasks = ('quiz', 'arena')
        r.calendar_plan = {}
        r.managed_enabled = False
        r.account = hs.Account(6, 'verified-account', 855)
        r.path = None
        r.records = {'quiz': deepcopy(record), 'arena': {'status': 'pending'}}
        r.scheduler = SimpleNamespace(start_f12_monitor=Mock(), capture=Mock(return_value=object()),
            main_ready=Mock(return_value=True), begin_task_observation=Mock(return_value=None),
            end_task_observation=Mock(), stop=threading.Event(), content_click_count=0,
            event=Mock(), save_raw_evidence=Mock(return_value=Path('verified.png')))
        r.bind = Mock()
        r.return_to_main = Mock()
        r.quiz = Mock()
        r.arena = Mock()
        return r

    def run_case(self, record):
        r = self.runner(record)
        with patch.object(hs, 'ForegroundRuntimeLease') as lease, \
             patch.object(hs, 'atomic_json'), patch.object(daily, 'read_json', return_value={}), \
             patch('hyol_quiz.solo_verified', return_value=False) as solo:
            lease.return_value.acquire.return_value = True
            result = r.run()
        return r, result, solo

    def test_complete_quiz_is_skipped_before_missing_link_check(self):
        completed = dict(status='done', completed=1, task_completed=True,
                         home_returned=True, finished_at='2026-10-23T21:23:49+08:00', last_error='')
        r, result, solo = self.run_case(completed)
        self.assertEqual(0, result)
        self.assertEqual(completed, r.records['quiz'])
        solo.assert_not_called()
        r.quiz.assert_not_called()
        r.arena.assert_called_once_with()

    def test_unstarted_quiz_still_requires_solo_link_proof(self):
        r, result, solo = self.run_case({'status': 'new'})
        self.assertEqual(2, result)
        self.assertEqual('pending', r.records['quiz']['status'])
        solo.assert_called_once()
        r.quiz.assert_not_called()
        r.arena.assert_called_once_with()

    def test_quiz_without_home_proof_cannot_use_completed_skip(self):
        r, result, solo = self.run_case(dict(status='done', task_completed=True, home_returned=False))
        self.assertEqual(2, result)
        self.assertEqual('pending', r.records['quiz']['status'])
        solo.assert_called_once()
        r.quiz.assert_not_called()

    def test_legacy_done_without_task_result_cannot_use_completed_skip(self):
        r, result, solo = self.run_case(dict(status='done', task_completed=False, home_returned=True))
        self.assertEqual(2, result)
        self.assertEqual('pending', r.records['quiz']['status'])
        solo.assert_called_once()
        r.quiz.assert_not_called()


if __name__ == '__main__':
    unittest.main()
