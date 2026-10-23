"""Reproduce current-account focus races without sending desktop input."""
import threading
import unittest
from types import SimpleNamespace
from unittest.mock import Mock, patch

from PIL import Image
import hyol_scheduler as hs


class SoloFocusTests(unittest.TestCase):
    def setUp(self):
        self.s = object.__new__(hs.Scheduler)
        self.s.hwnd = 123
        self.s.auto_foreground = True
        self.s.current_account_only = 'current-account'
        self.s.config = {'AdaptiveGeometry': '0'}
        self.s.stop = threading.Event()
        self.s.event = Mock()
        self.s.check_stop = Mock()
        self.s.wait_interruptible = Mock()
        self.user = Mock()
        self.user.IsIconic.return_value = False
        self.patches = [patch.object(hs.Win32, 'user32', self.user),
                        patch.object(hs.Win32, 'game_windows', return_value=[123]),
                        patch.object(hs.Win32, 'rect', return_value=hs.EXPECTED_WINDOW),
                        patch.object(hs.Win32, 'activate'),
                        patch.object(hs.Win32, 'click'),
                        patch.object(hs.time, 'sleep')]
        self.mocks = [p.start() for p in self.patches]
        for p in self.patches:
            self.addCleanup(p.stop)

    def tearDown(self):
        self.mocks[4].assert_not_called()

    def test_transient_focus_loss_recovers_before_single_account_guard(self):
        with patch.object(hs.Win32, 'foreground', side_effect=[456, 123]):
            self.s.ensure_window()
        self.assertEqual(2, self.mocks[3].call_count)
        self.assertTrue(any(c.args[0] == 'window_focus_retry' for c in self.s.event.call_args_list))

    def test_persistent_focus_loss_is_bounded_and_blocks_input(self):
        with patch.object(hs.Win32, 'foreground', return_value=456):
            with self.assertRaises(hs.SafetyError):
                self.s.ensure_window()
        self.assertEqual(3, self.mocks[3].call_count)

    def test_auto_foreground_disabled_never_activates_or_retries(self):
        self.s.auto_foreground = False
        with patch.object(hs.Win32, 'foreground', return_value=456):
            self.assertFalse(self.s.validate_geometry(activate=True))
        self.mocks[3].assert_not_called()
        self.s.wait_interruptible.assert_not_called()

    def test_readonly_geometry_validation_never_requests_focus(self):
        with patch.object(hs.Win32, 'foreground', return_value=456):
            self.assertTrue(self.s.validate_geometry())
        self.mocks[3].assert_not_called()

    def test_geometry_change_stops_without_focus_retry(self):
        with patch.object(hs.Win32, 'rect', return_value=(0, 0, 1280, 720)), \
             patch.object(hs.Win32, 'foreground', return_value=456):
            self.assertFalse(self.s.validate_geometry(activate=True))
        self.assertLessEqual(self.mocks[3].call_count, 1)

    def test_missing_window_stops_without_activation(self):
        with patch.object(hs.Win32, 'game_windows', return_value=[]):
            self.assertFalse(self.s.validate_geometry(activate=True))
        self.mocks[3].assert_not_called()

    def test_pause_during_focus_settle_propagates(self):
        self.s.wait_interruptible.side_effect = KeyboardInterrupt('F12')
        with patch.object(hs.Win32, 'foreground', return_value=456):
            with self.assertRaises(KeyboardInterrupt):
                self.s.ensure_window()
        self.assertEqual(1, self.mocks[3].call_count)

    def test_focus_loss_during_capture_discards_frame(self):
        stale = object()
        self.s._last_frame = stale
        self.s.templates = SimpleNamespace(begin_frame=Mock())
        self.s.check_offline_frame = Mock()
        with patch.object(hs.Win32, 'foreground', side_effect=[123, 456]), \
             patch.object(hs.ImageGrab, 'grab', return_value=Image.new('RGB', (1920, 1032))):
            with self.assertRaisesRegex(hs.SafetyError, 'foreground'):
                self.s.capture()
        self.assertIs(stale, self.s._last_frame)
        self.s.templates.begin_frame.assert_not_called()
        self.s.check_offline_frame.assert_not_called()


if __name__ == '__main__':
    unittest.main()
