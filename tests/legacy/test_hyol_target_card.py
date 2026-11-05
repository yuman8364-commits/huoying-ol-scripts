"""Real-frame simulated clicks for the group-entry target-card obstruction."""
import unittest
from unittest.mock import Mock
from PIL import Image
import hyol_scheduler as hs


class TargetCardTests(unittest.TestCase):
    def scheduler(self, frame):
        s = object.__new__(hs.Scheduler)
        s.config = {'RecognitionRetrySeconds': '0'}
        s.templates = hs.TemplateStore(.90)
        s.frame = frame
        s.capture = lambda: s.frame
        s.template = lambda name, roi=hs.VIEW, frame=None, threshold=None: s.templates.match(
            frame if frame is not None else s.frame, hs.ASSETS / name, roi, threshold)
        s.content_guard = lambda *args, **kwargs: s.frame
        s.event = Mock()
        s.wait_interruptible = Mock()
        s.wait_absent = lambda name, roi, *args, **kwargs: not s.template(name, roi, threshold=kwargs.get('threshold'))[0]
        s.click_point = Mock()
        return s

    def test_real_failure_closes_only_matched_target_card(self):
        with Image.open(hs.LOGS/'group_target_card_failure_20260915.png') as image:
            frame = image.convert('RGB')
        with Image.open(hs.LOGS/'group_target_card_main_fixture_20260915.png') as image:
            main = image.convert('RGB')
        s = self.scheduler(frame)
        self.assertFalse(s.main_ready(frame))
        self.assertTrue(s._target_card_visible(frame))
        self.assertTrue(s.main_ready(main))
        def simulated_click(account, x, y, label, anchors, *args, **kwargs):
            self.assertTrue(s._target_card_visible(s.frame))
            self.assertEqual(label, 'target_card_close_recover')
            self.assertTrue(713 <= x < 744 and 96 <= y < 129)
            s.frame = main
        s.click_point.side_effect = simulated_click
        s.prepare_main(hs.Account(1, 'test', 180), clear_notifications=False)
        s.click_point.assert_called_once()
        self.assertTrue(s.main_ready())

    def test_missing_card_control_never_authorizes_click(self):
        with Image.open(hs.LOGS/'group_target_card_failure_20260915.png') as image:
            original = image.convert('RGB')
        for roi in (hs.TARGET_CARD_CLOSE_ROI, hs.TARGET_CARD_ACTIONS_ROI, (1569, 289, 1919, 489)):
            frame = original.copy()
            frame.paste('black', roi)
            s = self.scheduler(frame)
            self.assertFalse(s._target_card_visible(frame))
            s.click_point.assert_not_called()

    def test_unchanged_overlay_stops_after_one_click(self):
        with Image.open(hs.LOGS/'group_target_card_failure_20260915.png') as image:
            s = self.scheduler(image.convert('RGB'))
        with self.assertRaisesRegex(hs.PendingError, '未重复点击'):
            s.prepare_main(hs.Account(1, 'test', 180), clear_notifications=False)
        s.click_point.assert_called_once()

    def test_real_weekend_shell_is_not_a_ready_main_page(self):
        with Image.open(hs.LOGS/'identity_layout_20261004_0744/tab_3_x450_unknown.png') as image:
            s = self.scheduler(image.convert('RGB'))
        self.assertTrue(s.template('daruma_weekend_page.bmp', (850,230,1180,360), threshold=.98)[0])
        self.assertTrue(s.template('daruma_weekend_close.bmp', (1290,260,1420,370), threshold=.98)[0])
        self.assertFalse(s.main_ready())
        s.click_point.assert_not_called()


if __name__ == '__main__':
    unittest.main()
