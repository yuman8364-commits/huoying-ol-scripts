"""Use constructed schedulers so cleanup tests cannot invent runtime fields."""
import tempfile
import threading
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

from PIL import Image
import hyol_scheduler as hs
from hyol_daily_tasks import DailyRunner, DailyScheduler
from hyol_multi_solo import MultiSoloRunner


class ObservationLifecycleTests(unittest.TestCase):
    def scheduler(self, cls=DailyScheduler):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        root = Path(directory.name)
        config = {'AccountMode': 'current', 'TabXs': '180',
                  'SharedStateDirectory': str(root/'state')}
        # Keep the real constructor, stop Event, state store and scope methods.
        with patch.object(hs, 'ROOT', root), patch.object(hs, 'LOGS', root/'logs'):
            scheduler = cls(config, 'multi_solo', False, True)
        self.assertIsInstance(scheduler.stop, threading.Event)
        self.assertNotIn('stop_event', vars(scheduler))
        scheduler.active = scheduler.accounts[0]
        return scheduler

    def test_incomplete_step_cleanup_preserves_its_watchdog_without_attribute_error(self):
        for cls in (hs.Scheduler, DailyScheduler):
            with self.subTest(scheduler=cls.__name__):
                s = self.scheduler(cls)
                account = s.accounts[0]
                previous = s.begin_task_observation(account, 'survival')
                watch = s._task_observation[2]
                s.end_task_observation(previous, complete=False)
                self.assertIsNone(s._task_observation)
                self.assertIs(watch, s._task_watchdogs[(account.name, 'survival')])
                self.assertFalse(s.stop.is_set())

    def test_cleanup_does_not_replace_the_original_pending_exception(self):
        s = self.scheduler()
        previous = s.begin_task_observation(s.accounts[0], 'survival')
        with self.assertRaisesRegex(hs.PendingError, 'original page pending'):
            try:
                raise hs.PendingError('original page pending')
            finally:
                s.end_task_observation(previous, complete=False)
        self.assertIsNone(s._task_observation)

    def test_f12_discards_the_observation_using_the_real_stop_event(self):
        s = self.scheduler()
        account = s.accounts[0]
        previous = s.begin_task_observation(account, 'survival')
        watch = s._task_observation[2]
        watch.observe(Image.new('RGB', (1920, 1032)), 0)
        s.stop.set()
        s.end_task_observation(previous)
        self.assertNotIn((account.name, 'survival'), s._task_watchdogs)
        s.stop.clear()
        s.begin_task_observation(account, 'survival')
        self.assertIsNot(watch, s._task_observation[2])
        self.assertIsNone(s._task_observation[2].observe(Image.new('RGB', (1920, 1032)), 600))

    def test_completed_step_does_not_clear_other_accounts_or_steps(self):
        s = self.scheduler()
        account = s.accounts[0]
        other = hs.Account(2, 'other', 315)
        s.begin_task_observation(other, 'quiz')
        other_watch = s._task_observation[2]
        previous = s.begin_task_observation(account, 'survival')
        s.end_task_observation(previous, complete=True)
        self.assertNotIn((account.name, 'survival'), s._task_watchdogs)
        self.assertIs(other_watch, s._task_watchdogs[(other.name, 'quiz')])
        self.assertEqual(other, s._task_observation[0])

    def test_pending_survival_followup_releases_the_scope_and_keeps_original_record(self):
        s = self.scheduler()
        s.ensure_account_main_before_release = Mock(return_value=True)
        rec = dict(status='pending', completed=1, followup_check_due=True,
                   task_completed=True, home_returned=False)
        runner = SimpleNamespace(account=s.accounts[0], formation_checked=True,
            record=lambda _task: rec, enter=Mock(side_effect=hs.PendingError('original page pending')),
            save=Mock())
        coordinator = object.__new__(MultiSoloRunner)
        coordinator.scheduler, coordinator.deferred = s, {}
        original_accounts = s.accounts
        with patch.object(hs.Win32, 'click', side_effect=AssertionError('No game input')):
            coordinator._survival_followup(runner)
        self.assertEqual('pending', rec['status'])
        self.assertEqual('original page pending', rec['last_error'])
        self.assertTrue(rec['followup_check_due'])
        self.assertIs(original_accounts, s.accounts)
        self.assertIsNone(s.navigator)
        self.assertIsNone(s._task_observation)
        self.assertEqual(0, s.content_click_count)

    def test_pending_task_finishes_cleanup_and_the_next_task_can_run(self):
        s = self.scheduler()
        s.ensure_account_main_before_release = Mock(return_value=True)
        s.main_ready = Mock(return_value=True)
        s.template = Mock(return_value=(True, 1., 0, 0))
        s.capture = Mock(return_value=Image.new('RGB', (1920, 1032)))
        pending, mail = {'status': 'new'}, {'status': 'new'}
        records = {'ranking': pending, 'mail': mail}
        runner = SimpleNamespace(account=s.accounts[0], record=lambda task: records[task], save=Mock(),
            ranking=Mock(side_effect=hs.PendingError('original ranking pending')), mail=Mock())
        coordinator = object.__new__(MultiSoloRunner)
        coordinator.scheduler, coordinator.skipped_tasks = s, {}
        with patch.object(hs.Win32, 'click', side_effect=AssertionError('No game input')):
            coordinator._run_task(runner, 'ranking')
            coordinator._run_task(runner, 'mail')
        self.assertEqual('pending', pending['status'])
        self.assertEqual('original ranking pending', pending['last_error'])
        self.assertEqual('done', mail['status'])
        self.assertIsNone(s.navigator)
        self.assertIsNone(s._task_observation)
        self.assertEqual(0, s.content_click_count)

    def test_page_wait_failure_keeps_the_actual_last_frame_for_the_browser(self):
        s = self.scheduler()
        runner = object.__new__(DailyRunner)
        runner.scheduler, runner.account = s, s.accounts[0]
        s._last_frame = Image.new('RGB', (40, 30), 'navy')
        s.wait_template = Mock(return_value=False)
        s.capture = Mock(side_effect=AssertionError('Do not replace the failed frame'))
        with patch.object(hs, 'EVIDENCE_DIR', s.state.directory.parent/'logs/evidence'), patch.object(
                hs.Win32, 'click', side_effect=AssertionError('No game input')):
            with self.assertRaises(hs.PendingError) as caught:
                runner.wait('ranking_page.bmp', (790, 95, 1080, 195))
        path = Path(str(caught.exception).split('；截图=', 1)[1])
        self.assertTrue(path.is_file())
        with Image.open(path) as image:
            self.assertEqual((40, 30), image.size)
            self.assertEqual((0, 0, 128), image.getpixel((0, 0)))
        s.capture.assert_not_called()
        self.assertEqual(0, s.content_click_count)

    def test_successful_page_wait_does_not_save_failure_evidence(self):
        s = self.scheduler()
        runner = object.__new__(DailyRunner)
        runner.scheduler, runner.account = s, s.accounts[0]
        s.wait_template = Mock(return_value=True)
        s.save_raw_evidence = Mock(side_effect=AssertionError('No failure'))
        runner.wait('ranking_page.bmp', (790, 95, 1080, 195))
        s.save_raw_evidence.assert_not_called()

    def test_page_wait_f12_is_not_replaced_with_a_page_error(self):
        s = self.scheduler()
        runner = object.__new__(DailyRunner)
        runner.scheduler, runner.account = s, s.accounts[0]
        s.wait_template = Mock(side_effect=KeyboardInterrupt())
        s.save_raw_evidence = Mock(side_effect=AssertionError('Preserve pause'))
        with self.assertRaises(KeyboardInterrupt):
            runner.wait('ranking_page.bmp', (790, 95, 1080, 195))
        s.save_raw_evidence.assert_not_called()

    def test_screenshot_io_error_preserves_the_original_page_failure(self):
        s = self.scheduler()
        runner = object.__new__(DailyRunner)
        runner.scheduler, runner.account = s, s.accounts[0]
        s._last_frame = Image.new('RGB', (40, 30))
        s.wait_template = Mock(return_value=False)
        s.save_raw_evidence = Mock(side_effect=OSError('disk unavailable'))
        with self.assertRaisesRegex(hs.PendingError, '页面未确认：ranking_page.bmp'):
            runner.wait('ranking_page.bmp', (790, 95, 1080, 195))

    def guard_scheduler(self):
        s = self.scheduler()
        s._last_frame = Image.new('RGB', (1920, 1032))
        s._saved_tab_mapping = Mock(return_value={s.active.name: s.active.tab_x})
        s._generic_active_tab_x = Mock(return_value=s.active.tab_x)
        s.wait_interruptible = Mock()
        s.save_raw_evidence = Mock(return_value=Path('guard_pending.png'))
        return s

    def test_occluded_role_and_page_are_observed_only_in_the_same_bound_tab(self):
        s = self.guard_scheduler()
        frame = s._last_frame
        error = hs.SafetyError("identity and page-anchor guard failed: scores={} anchors=['survival_anchor.bmp']")
        s._content_guard_once = Mock(side_effect=[error, frame])
        with patch.object(hs.Win32, 'click', side_effect=AssertionError('Observe without input')):
            self.assertIs(frame, s.content_guard(s.active, ['survival_anchor.bmp']))
        s.wait_interruptible.assert_called_once_with(.2)
        s.save_raw_evidence.assert_not_called()

    def test_occluded_frame_in_another_tab_is_not_retried(self):
        s = self.guard_scheduler()
        s._generic_active_tab_x.return_value = s.active.tab_x+135
        error = hs.SafetyError("identity and page-anchor guard failed: scores={} anchors=['survival_anchor.bmp']")
        s._content_guard_once = Mock(side_effect=error)
        with self.assertRaises(hs.SafetyError):
            s.content_guard(s.active, ['survival_anchor.bmp'])
        s.wait_interruptible.assert_not_called()
        s.save_raw_evidence.assert_called_once()

    def test_positive_wrong_role_and_foreground_failures_are_never_retried(self):
        for message in ("identity and page-anchor guard failed: scores={'other': 0.99} anchors=['survival_anchor.bmp']",
                        'game is not foreground'):
            s = self.guard_scheduler()
            s._content_guard_once = Mock(side_effect=hs.SafetyError(message))
            with self.assertRaises(hs.SafetyError):
                s.content_guard(s.active, ['survival_anchor.bmp'])
            s.wait_interruptible.assert_not_called()

    def test_f12_during_page_observation_is_preserved(self):
        s = self.guard_scheduler()
        s._content_guard_once = Mock(side_effect=hs.SafetyError(
            "identity and page-anchor guard failed: scores={} anchors=['survival_anchor.bmp']"))
        s.wait_interruptible.side_effect = KeyboardInterrupt()
        with self.assertRaises(KeyboardInterrupt):
            s.content_guard(s.active, ['survival_anchor.bmp'])
        s.save_raw_evidence.assert_not_called()

    def test_moving_target_is_not_clicked_or_written_pending(self):
        s = self.scheduler()
        s.no_content_clicks = False
        s.content_guard = Mock(return_value=Image.new('RGB', (1920, 1032)))
        s.active_template = Mock(side_effect=[(True,1.,100,200), (True,1.,300,200)])
        s.check_input_pause = Mock()
        with patch.object(hs.Win32, 'click') as click:
            self.assertFalse(s.click_template(s.active, 'button.bmp', hs.VIEW, 1,1,
                                              'moving_target', ['page.bmp'], irreversible=True))
        click.assert_not_called()
        self.assertEqual('new', s.state.action_status(s.active.name, 'moving_target'))

    def test_stable_target_sends_one_input_and_preserves_write_ahead(self):
        s = self.scheduler()
        s.no_content_clicks = False
        frame = Image.new('RGB', (1920, 1032))
        s.content_guard = Mock(return_value=frame)
        s.active_template = Mock(return_value=(True,1.,100,200))
        s.check_input_pause = Mock()
        s.screen_point = Mock(side_effect=lambda x,y:(x,y))
        s.capture = Mock(return_value=frame)
        s.save_evidence = Mock(return_value=Path('before.png'))
        with patch.object(hs.Win32, 'click') as click:
            self.assertTrue(s.click_template(s.active, 'button.bmp', hs.VIEW, 1,1,
                                             'one_target', ['page.bmp'], irreversible=True))
            with self.assertRaises(hs.PendingError):
                s.click_template(s.active, 'button.bmp', hs.VIEW, 1,1,
                                 'one_target', ['page.bmp'], irreversible=True)
        click.assert_called_once_with(101,201, expected_hwnd=s.hwnd,
                                      before_down=s.check_input_pause)
        self.assertEqual('pending', s.state.action_status(s.active.name, 'one_target'))


if __name__ == '__main__':
    unittest.main()
