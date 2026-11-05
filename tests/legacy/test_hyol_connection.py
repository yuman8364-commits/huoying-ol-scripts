"""Real disconnect-dialog replay and account-isolation contracts."""
from copy import deepcopy
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import Mock, patch
from PIL import Image

import hyol_scheduler as hs
from hyol_multi_solo import MultiSoloRunner
from hyol_daily_tasks import DailyRunner


class ConnectionTests(unittest.TestCase):
    def scheduler(self, root):
        s = object.__new__(hs.Scheduler)
        s.active = hs.Account(6, 'test', 855)
        s.accounts = [s.active]
        s._capture_account = s.active
        s.no_content_clicks = False
        s.templates = hs.TemplateStore(.98)
        s._generic_active_tab_x = Mock(return_value=855)
        s.state = hs.StateStore(['test'], Path(root))
        s.state.set_action('test', 'arena_casual_battle_1', 'pending', 'original battle')
        s.save_raw_evidence = Mock(return_value=Path(root)/'offline.png')
        s.event = Mock()
        return s

    def test_real_central_dialog_stops_slot_and_preserves_pending_action(self):
        path = hs.LOGS/'scheduler_evidence/2026-10-03/identity_rebind/tab_6_x855_offline.png'
        with TemporaryDirectory() as root, Image.open(path) as frame:
            s = self.scheduler(root)
            before = deepcopy(s.state.accounts['test']['actions'])
            for _ in range(2):
                with self.assertRaises(hs.AccountOffline) as error:
                    s.check_offline_frame(frame)
                self.assertEqual('test', error.exception.account.name)
            self.assertEqual(before, s.state.accounts['test']['actions'])
            self.assertEqual('disconnected', s.state.accounts['test']['connection']['status'])
            self.assertEqual(1, s.save_raw_evidence.call_count)

    def test_unknown_role_or_normal_world_is_never_called_disconnected(self):
        path = hs.LOGS/'scheduler_evidence/2026-10-03/account_b76494cf2c/213036_360235_verified_account_observation.png'
        with TemporaryDirectory() as root, Image.open(path) as frame:
            s = self.scheduler(root)
            self.assertFalse(s.check_offline_frame(frame))
            self.assertNotIn('connection', s.state.accounts['test'])

    def test_observation_or_unbound_tab_never_assigns_dialog_to_old_account(self):
        path = hs.LOGS/'scheduler_evidence/2026-10-03/identity_rebind/tab_6_x855_offline.png'
        with TemporaryDirectory() as root, Image.open(path) as frame:
            s = self.scheduler(root)
            s._capture_binding_probe = True
            self.assertTrue(s.check_offline_frame(frame))
            self.assertNotIn('connection', s.state.accounts['test'])
            s._capture_binding_probe = False
            s._generic_active_tab_x.return_value = 315
            self.assertTrue(s.check_offline_frame(frame))
            self.assertNotIn('connection', s.state.accounts['test'])

    def test_mid_task_disconnect_skips_account_without_home_click_or_completion(self):
        c = object.__new__(MultiSoloRunner)
        c.scheduler = Mock()
        account = hs.Account(2, 'test', 315)
        c.scheduler.accounts = [account]
        c.event = Mock()
        c.skip_disconnected = Mock()
        r = object.__new__(DailyRunner)
        r.account, r.scheduler, r.path, r.records = account, c.scheduler, None, {}
        r.mail = Mock(side_effect=hs.AccountOffline(account))
        c._run_task(r, 'mail')
        self.assertEqual('pending', r.record('mail')['status'])
        self.assertFalse(r.record('mail').get('task_completed', False))
        c.skip_disconnected.assert_called_once_with(account, 'mail')
        c.scheduler.ensure_account_main_before_release.assert_not_called()
        c.scheduler.click_template.assert_not_called()

    def test_wait_checks_foreground_dialog_within_three_seconds(self):
        with TemporaryDirectory() as root:
            s = self.scheduler(root)
            s.hwnd, s._health_checked_at = 42, 0
            s.check_stop = Mock()
            s.capture = Mock(side_effect=hs.AccountOffline(s.active))
            now = [0.0]
            with patch.object(hs.Win32, 'foreground', return_value=42), \
                 patch.object(hs.time, 'monotonic', side_effect=lambda: now[0]), \
                 patch.object(hs.time, 'sleep', side_effect=lambda seconds: now.__setitem__(0, now[0]+seconds)):
                with self.assertRaises(hs.AccountOffline):
                    s.wait_interruptible(20)
            self.assertGreaterEqual(now[0], 3)
            self.assertLess(now[0], 3.3)
            s.capture.assert_called_once()

    def test_page_bound_client_uses_verified_selected_tab(self):
        path = hs.LOGS/'scheduler_evidence/2026-10-03/identity_rebind/tab_6_x855_offline.png'
        with TemporaryDirectory() as root, Image.open(path) as frame:
            s = self.scheduler(root)
            s.active = s._capture_account = hs.Account(0, 'test', 0)
            s.current_account_only, s._connection_tab_x = 'test', 855
            with self.assertRaises(hs.AccountOffline):
                s.check_offline_frame(frame)

    def test_current_tab_client_detects_popup_before_identity_binding(self):
        path = hs.LOGS/'scheduler_evidence/2026-10-03/identity_rebind/tab_6_x855_offline.png'
        with TemporaryDirectory() as root, Image.open(path) as frame:
            s = self.scheduler(root)
            s.config = {'TabXs':'180,315,450,585,720,855'}
            s.capture = Mock(return_value=frame)
            with self.assertRaises(hs.AccountOffline) as error:
                s.bind_generic_current_window()
            self.assertEqual('当前窗口标签6', error.exception.account.name)
            s.event.assert_called_once()
            self.assertEqual('unconfirmed', s.event.call_args.kwargs['identity'])


if __name__ == '__main__':
    unittest.main()
