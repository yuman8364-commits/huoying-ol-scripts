"""Read-only absent-surface isolation; all desktop APIs are mocked."""
from contextlib import contextmanager
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

import numpy as np
from PIL import Image

import hyol_accounts as accounts
import hyol_scheduler as hs


NO_SURFACE = 'game content surface is ambiguous: 0 visible regions'


class LiveBlankTabTests(unittest.TestCase):
    def scheduler(self):
        s = object.__new__(hs.Scheduler)
        s.hwnd, s.config = 42, {'AdaptiveGeometry': '1'}
        s.no_content_clicks, s._capture_binding_probe = True, True
        s.check_stop = s.check_input_pause = Mock()
        s.validate_geometry = Mock(return_value=False)
        s.ensure_window = Mock()
        s.event = Mock()
        s.templates = SimpleNamespace(match=Mock(), begin_frame=Mock())
        s.run_context = SimpleNamespace(check_window=Mock(), bind_window=Mock())
        s.click_count, s.content_click_count = 0, 0
        s._last_frame, s._capture_geometry = object(), None
        return s

    @contextmanager
    def window(self, *, error=NO_SURFACE, windows=None, foreground=42, rect=hs.EXPECTED_WINDOW, iconic=False):
        with patch.object(hs.Win32, 'game_windows', return_value=[42] if windows is None else windows), \
             patch.object(hs.Win32, 'foreground', return_value=foreground), \
             patch.object(hs.Win32, 'rect', return_value=rect), \
             patch.object(hs.Win32, 'game_content_rect', side_effect=hs.SafetyError(error)), \
             patch.object(hs.Win32.user32, 'IsIconic', return_value=iconic), \
             patch.object(hs.Win32, 'hit_test', return_value=1) as hit, \
             patch.object(hs.Win32, 'click') as click, \
             patch.object(hs.ImageGrab, 'grab', return_value=Image.new('RGB', (1920, 1032), 'white')) as grab:
            yield SimpleNamespace(hit=hit, click=click, grab=grab)

    def test_raw_browser_frame_is_observation_only_and_never_replaces_content_frame(self):
        s = self.scheduler()
        previous = s._last_frame
        with self.window() as desktop:
            frame = s.capture_account_observation()
            self.assertTrue(frame._hyol_chrome_only)
            self.assertEqual(dict(window=hs.EXPECTED_WINDOW, content=None, normalized=False),
                             frame._hyol_chrome_only_geometry)
            self.assertIs(previous, s._last_frame)
            self.assertIsNone(s._capture_geometry)
            self.assertFalse(s.healthy(frame))
            with self.assertRaises(hs.SafetyError):
                s.template('anchor.bmp', hs.VIEW, frame)
            with self.assertRaises(hs.PendingError):
                accounts.collect_current_identity(s, hs.Account(1, 'a', 180), frame)
            with self.assertRaisesRegex(hs.SafetyError, '0 visible regions'):
                s.capture()
            with self.assertRaises(hs.SafetyError):
                s.screen_point(100, 100)
            s.templates.match.assert_not_called()
            s.templates.begin_frame.assert_not_called()
            desktop.click.assert_not_called()

    def test_raw_fallback_requires_readonly_discovery_and_unique_stable_window(self):
        cases = ({'error': 'game content surface is ambiguous: 2 visible regions'},
                 {'windows': [42, 99]}, {'foreground': 99},
                 {'rect': (1, 0, 1921, 1032)}, {'iconic': True})
        for settings in cases:
            with self.subTest(settings=settings), self.window(**settings) as desktop:
                with self.assertRaises(hs.SafetyError):
                    self.scheduler().capture_account_observation()
                desktop.grab.assert_not_called()
                desktop.click.assert_not_called()
        for field in ('no_content_clicks', '_capture_binding_probe'):
            with self.subTest(field=field), self.window() as desktop:
                s = self.scheduler()
                setattr(s, field, False)
                with self.assertRaises(hs.SafetyError):
                    s.capture_account_observation()
                desktop.grab.assert_not_called()

    def test_discovery_native_switch_uses_existing_title_and_keeps_content_input_zero(self):
        s = self.scheduler()
        account = hs.Account(2, 'a', 315)
        with self.window() as desktop, patch('hyol_multi_solo.tab_close_positions', return_value=(214, 349)):
            s.chrome_click(315, 10, 'observe_account:2', account)
            desktop.click.assert_called_once()
            self.assertEqual((315, 10), desktop.click.call_args.args)
            self.assertEqual(42, desktop.click.call_args.kwargs['expected_hwnd'])
            self.assertEqual(0, s.content_click_count)
            self.assertEqual(1, s.click_count)
            self.assertGreaterEqual(s.run_context.check_window.call_count, 2)

    def test_discovery_switch_rejects_changed_layout_unknown_title_and_unsafe_hit(self):
        for markers, target, hit in (((214, 349), 999, 1),
                                     ([(214, 349), (214, 349, 484)], 315, 1),
                                     ((214, 349), 315, 2)):
            with self.subTest(target=target, markers=markers, hit=hit), self.window() as desktop:
                s = self.scheduler()
                desktop.hit.return_value = hit
                kwargs = {'side_effect': markers} if isinstance(markers, list) else {'return_value': markers}
                with patch('hyol_multi_solo.tab_close_positions', **kwargs):
                    with self.assertRaises(hs.SafetyError):
                        s.chrome_click(target, 10, 'observe_account:2', hs.Account(2, 'a', target))
                desktop.click.assert_not_called()

    def test_pause_or_wrong_observation_scope_never_sends_native_input(self):
        with self.window() as desktop:
            s = self.scheduler()
            s.check_input_pause.side_effect = KeyboardInterrupt('paused')
            with self.assertRaises(KeyboardInterrupt):
                s.chrome_click(315, 10, 'observe_account:2', hs.Account(2, 'a', 315))
            desktop.click.assert_not_called()
        with self.window() as desktop:
            s = self.scheduler()
            s.no_content_clicks = False
            with self.assertRaises(hs.SafetyError):
                s.chrome_click(315, 10, 'observe_account:2', hs.Account(2, 'a', 315))
            desktop.click.assert_not_called()

    def test_precise_legacy_native_switch_and_rebind_remain_guarded(self):
        for label, account in (('switch_tab:a', hs.Account(2, 'a', 315)), ('rebind_probe:2', None)):
            with self.subTest(label=label), self.window() as desktop, \
                 patch('hyol_multi_solo.tab_close_positions', return_value=(214, 349)):
                s = self.scheduler()
                s.no_content_clicks = False
                s.chrome_click(315, 10, label, account)
                desktop.click.assert_called_once()
                self.assertEqual(0, s.content_click_count)
        with self.window() as desktop, patch('hyol_multi_solo.tab_close_positions', return_value=(214, 349)):
            s = self.scheduler()
            with self.assertRaises(hs.SafetyError):
                s.chrome_click(315, 10, 'rebind_probe:1')
            desktop.click.assert_not_called()

    def test_initial_blank_binding_requires_explicit_discovery_scope(self):
        with self.window() as desktop:
            s = self.scheduler()
            s.bind_window(wait=False)
            s.run_context.bind_window.assert_called_once_with(s)
            self.assertTrue(any(call.args[0] == 'window_bound_chrome_only' for call in s.event.call_args_list))
            desktop.click.assert_not_called()

    def scan_scheduler(self, *, blank_first=False, readonly=True):
        raw, content = Image.new('RGB', (1920, 1032)), Image.new('RGB', (1920, 1032))
        raw._hyol_chrome_only = True
        current = {'slot': 1}
        s = SimpleNamespace(config={}, no_content_clicks=readonly, content_click_count=0,
                            check_stop=Mock(), event=Mock(), wait_interruptible=Mock(),
                            offline_dialog_visible=Mock(return_value=False),
                            template=Mock(return_value=(False, 0, 0, 0)),
                            save_raw_evidence=Mock(return_value='blank-evidence.png'),
                            templates=SimpleNamespace())
        s.capture = lambda: raw if blank_first and current['slot'] == 1 else content
        s.capture_account_observation = s.capture
        s._generic_active_tab_x = lambda frame: (180, 315)[current['slot']-1]
        s.chrome_click = lambda x, y, label, account: current.update(slot=account.index)
        return s

    def test_readonly_scan_isolates_raw_tab_and_collects_dim_modal_without_dismiss(self):
        s = self.scan_scheduler(blank_first=True)
        with patch('hyol_multi_solo.tab_close_positions', return_value=(214, 349)), \
             patch('hyol_checkpoints.current_identity_mask', return_value=np.ones((20, 20), dtype=np.uint8)), \
             patch.object(accounts, 'is_captain', return_value=False), \
             patch.object(accounts, 'dismiss_current_final_result') as result_close, \
             patch('hyol_identity_overlays.dismiss_identity_overlays') as overlay_close:
            _, observations, _, unavailable = accounts.observe_accounts(s)
        self.assertEqual({2}, set(observations))
        self.assertIn('content_surface_unavailable', unavailable[1])
        self.assertEqual(3, len(observations[2]))
        self.assertEqual(0, s.content_click_count)
        result_close.assert_not_called()
        overlay_close.assert_not_called()
        self.assertTrue(any(call.args[0] == 'account_identity_pending' for call in s.event.call_args_list))

    def test_readonly_unknown_identity_stays_pending_and_normal_scan_keeps_cleanup(self):
        s = self.scan_scheduler()
        with patch('hyol_multi_solo.tab_close_positions', return_value=(214,)), \
             patch('hyol_checkpoints.current_identity_mask', return_value=np.zeros((20, 20), dtype=np.uint8)), \
             patch.object(accounts, 'is_captain', return_value=False):
            _, observations, _, unavailable = accounts.observe_accounts(s)
        self.assertEqual({}, observations)
        self.assertEqual({1}, set(unavailable))
        normal = self.scan_scheduler(readonly=False)
        with patch('hyol_multi_solo.tab_close_positions', return_value=(214,)), \
             patch('hyol_checkpoints.current_identity_mask', return_value=np.ones((20, 20), dtype=np.uint8)), \
             patch.object(accounts, 'is_captain', return_value=False), \
             patch.object(accounts, 'dismiss_current_final_result', side_effect=lambda s, a, frame: frame) as result_close, \
             patch('hyol_identity_overlays.dismiss_identity_overlays', side_effect=lambda s, a, frame, **kw: frame) as overlay_close:
            _, observations, _, unavailable = accounts.observe_accounts(normal)
        self.assertEqual({1}, set(observations))
        self.assertEqual({}, unavailable)
        result_close.assert_called_once()
        self.assertEqual(2, overlay_close.call_count)


if __name__ == '__main__':
    unittest.main()
