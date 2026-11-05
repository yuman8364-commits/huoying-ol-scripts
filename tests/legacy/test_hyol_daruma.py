"""Daruma count and timing checks using live pixel samples plus motion cases."""
import unittest
from datetime import datetime
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock,patch

import numpy as np
from PIL import Image

import hyol_daruma as d
import hyol_scheduler as hs


FIXTURES = Path(__file__).resolve().parent / 'fixtures'


class PixelRunner:
    def __init__(self):
        self.templates = hs.TemplateStore(.9)

    def visible(self, name, roi, frame=None, threshold=.92):
        return self.templates.match(frame, hs.ASSETS / name, roi, threshold)[0]


class DarumaTests(unittest.TestCase):
    def test_shared_pause_blocks_raw_strike_before_mouse_down_and_keeps_intent(self):
        account = hs.Account(1,'verified-role',180)
        s = Mock()
        s.no_content_clicks = False
        s.active = account
        s.state.action_status.return_value = 'new'
        s.check_input_pause.side_effect = KeyboardInterrupt('shared F12 pause')
        runner = SimpleNamespace(scheduler=s,account=account)
        motion = d.Motion(900,160.,1,10.,.02)
        with patch.object(d,'_trusted_geometry'),patch.object(d.time,'perf_counter',return_value=10.),patch.object(d.time,'sleep'),patch.object(hs.Win32.user32,'mouse_event') as send,self.assertRaises(KeyboardInterrupt):
            d._fast_strike(runner,motion,.01,'daruma_original_strike')
        send.assert_not_called()
        self.assertEqual('pending',s.state.set_action.call_args.args[2])
        s.capture.assert_not_called()

    def test_calendar_uses_local_saturday_sunday_only(self):
        self.assertTrue(d.is_weekend(datetime(2026, 9, 26, 0, 1)))
        self.assertTrue(d.is_weekend(datetime(2026, 9, 27, 23, 59)))
        self.assertFalse(d.is_weekend(datetime(2026, 9, 28, 0, 1)))

    def test_real_reward_one_and_three_are_distinct(self):
        r = PixelRunner()
        for value, fixture, y in ((1, 'daruma_reward_1.png', 642),
                                  (3, 'daruma_reward_3.png', 418)):
            frame = Image.new('RGB', (1920, 1032), (30, 30, 30))
            frame.paste(Image.open(FIXTURES / fixture), (d.REWARD_DIGIT[0], y))
            roi = (d.REWARD_DIGIT[0], y, d.REWARD_DIGIT[2], y + (d.REWARD_DIGIT[3] - d.REWARD_DIGIT[1]))
            self.assertEqual(value, d._single_digit(r, 'daruma_reward_digit_', roi, frame))

    def test_real_chance_one(self):
        frame = Image.new('RGB', (1920, 1032), (30, 30, 30))
        frame.paste(Image.open(FIXTURES / 'daruma_chance_1.png'), d.CHANCE_DIGIT[:2])
        self.assertEqual(1, d._single_digit(PixelRunner(), 'daruma_chance_digit_', d.CHANCE_DIGIT, frame))

    def test_moving_pointer_requires_one_narrow_change(self):
        baseline = np.asarray(Image.open(FIXTURES / 'daruma_bar.png').convert('RGB'), dtype=np.int16)
        right = baseline.copy()
        right[:, 180:186, :] = (245, 215, 25)
        self.assertEqual(d.BAR[0] + 182, d.moving_column(baseline, right))
        right[:, 300:306, :] = (245, 215, 25)
        self.assertIsNone(d.moving_column(baseline, right))

    def test_live_flame_and_lower_cylinder_selection(self):
        root = Path(__file__).resolve().parent
        screenshot = root / 'logs' / 'daruma_bottom_hover.png'
        if not screenshot.exists():
            self.skipTest('live Daruma replay image unavailable')
        frame = Image.open(screenshot)
        baseline = np.asarray(Image.open(FIXTURES / 'daruma_bar.png').convert('RGB'), dtype=np.int16)
        bar = np.asarray(frame.crop(d.BAR).convert('RGB'), dtype=np.int16)
        lower = np.asarray(frame.crop(d.BOTTOM_SELECTION).convert('RGB'), dtype=np.int16)
        self.assertEqual(897, d.moving_column(baseline, bar))
        self.assertTrue(d.bottom_selected(lower))

    def test_bottom_rim_tracks_sideways_motion_after_weak_hit(self):
        path = Path(__file__).resolve().parent / 'logs' / 'daruma_after_calibrated_readonly.png'
        if not path.exists():
            self.skipTest('live Daruma rim replay unavailable')
        frame = Image.open(path)
        roi = np.asarray(frame.crop(d.BOTTOM_TRACK_ROI).convert('RGB'), dtype=np.int16)
        self.assertEqual((960, 1078), d.selected_bottom_bounds(roi))
        self.assertIsNone(d.selected_bottom_bounds(np.zeros_like(roi)))

    def test_live_trace_locates_late_judgment_marker(self):
        path = Path(__file__).resolve().parent / 'logs' / 'daruma_traces' / (
            'daruma_account_00a0b00ccc_2026-09-27_1_strike_5_retry_9.npz')
        if not path.exists():
            self.skipTest('live Daruma strike trace unavailable')
        self.assertEqual(1067, d.judgment_column_from_trace(str(path)))

    def test_finished_game_has_six_icons_at_45_pixel_spacing(self):
        root = Path(__file__).resolve().parent / 'logs' / 'scheduler_evidence' / '2026-09-27' / 'account_00a0b00ccc'
        before = root / '214413_155849_before_daruma_account_00a0b00ccc_2026-09-27_1_before.png'
        ended = root / '231246_945429_after_daruma_account_00a0b00ccc_2026-09-27_1_after.png'
        if not before.exists() or not ended.exists():
            self.skipTest('live final-layer replay unavailable')
        self.assertEqual(6, d.obtained_count(Image.open(before), Image.open(ended)))

    def test_motion_regression_ignores_frame_jitter_and_wrap(self):
        for start, direction in ((900, 1), (1180, -1)):
            samples = [(10.0 + i * .03, round(start + direction * 160 * i * .03 + (i % 3 - 1)), .025)
                       for i in range(12)]
            samples.insert(0, (9.97, 1300 if direction == 1 else 765, .025))
            motion = d.estimate_motion(samples)
            self.assertIsNotNone(motion)
            self.assertEqual(direction, motion.direction)
            self.assertAlmostEqual(abs(motion.velocity), 160, delta=8)
            target = 980 if direction == 1 else 1100
            self.assertIsNotNone(d.predicted_hit_seconds(motion, target, .02, 10.33))
            self.assertIsNone(d.predicted_hit_seconds(motion, start, .02, 10.33))


if __name__ == '__main__':
    unittest.main()
