"""Activity rotation must not strand a failed Daruma entry over the role strip."""
import unittest
from unittest.mock import Mock, patch

from PIL import Image

import hyol_daruma as daruma
import hyol_scheduler as hs


class OfflineEntry:
    def __init__(self, menu_y=None):
        self.templates = hs.TemplateStore(.9)
        self.main = Image.new('RGB', (1920, 1032), (30, 30, 30))
        self.activity = self.main.copy()
        self.weekend = self.main.copy()
        self.main.info['page'] = 'main'
        self.activity.info['page'] = 'activity'
        self.weekend.info['page'] = 'weekend'
        with Image.open(hs.ASSETS / 'daruma_hotspot.bmp') as image:
            self.main.paste(image, (490, 85))
        if menu_y is not None:
            with Image.open(hs.ASSETS / 'daruma_weekend_menu.bmp') as image:
                self.activity.paste(image, (500, menu_y))
        with Image.open(hs.ASSETS / 'daruma_weekend_page.bmp') as image:
            self.weekend.paste(image, (900, 260))
        self.frame = self.main
        self.account = hs.Account(1, 'role', 180)
        self.scheduler = Mock()
        self.scheduler.capture.side_effect = self.capture
        self.scheduler.main_ready.side_effect = lambda frame: frame.info.get('page') == 'main'
        self.scheduler.wait_until_main.side_effect = lambda seconds: self.frame is self.main
        self.scheduler.save_raw_evidence.side_effect = lambda frame, account, label, phase: label+'.png'
        self.clicks = []
        self.events = []

    def capture(self):
        self.scheduler._last_frame = self.frame
        return self.frame

    def visible(self, name, roi, frame=None, threshold=.92):
        return self.templates.match(frame if frame is not None else self.frame,
                                    hs.ASSETS / name, roi, threshold)[0]

    def click(self, name, roi, dx, dy, label, anchors, **kwargs):
        if not self.visible(name, roi):
            raise hs.PendingError('unrecognized input target: '+name)
        self.clicks.append(label)
        self.frame = self.activity if label == 'daruma_open_hotspot' else self.weekend
        self.capture()

    def wait(self, name, roi, seconds):
        if not self.visible(name, roi):
            raise hs.PendingError('page not confirmed: '+name)

    def event(self, name, **fields):
        self.events.append((name, fields))


class DarumaActivityEntryTests(unittest.TestCase):
    def test_same_menu_text_can_move_to_first_middle_or_last_visible_row(self):
        for row in (350, 485, 710):
            with self.subTest(row=row):
                runner = OfflineEntry(row)
                daruma._open_weekend(runner)
                self.assertIs(runner.frame, runner.weekend)
                self.assertEqual(['daruma_open_hotspot', 'daruma_open_weekend'], runner.clicks)

    def test_missing_menu_closes_only_proven_recharge_shell_and_keeps_pending(self):
        runner = OfflineEntry()

        def close(scheduler, account, frame, *, only_kind):
            self.assertIs(account, runner.account)
            self.assertIs(frame, runner.activity)
            self.assertEqual('recharge_activity', only_kind)
            runner.frame = runner.main
            return runner.main

        with patch('hyol_identity_overlays.inspect', return_value=('recharge_activity',)), \
                patch('hyol_identity_overlays.dismiss_identity_overlays', side_effect=close) as cleanup:
            with self.assertRaisesRegex(hs.PendingError, '当前可见活动菜单.*原任务保留pending'):
                daruma._open_weekend(runner)
        cleanup.assert_called_once()
        self.assertEqual(['daruma_open_hotspot'], runner.clicks)
        self.assertIs(runner.frame, runner.main)
        self.assertEqual(['daruma_entry_unconfirmed', 'daruma_entry_unconfirmed_returned'],
                         [row[0] for row in runner.events])
        for _, fields in runner.events:
            self.assertFalse(fields['task_credit'])
            self.assertFalse(fields['reward_credit'])
        runner.scheduler.commit_action.assert_not_called()

    def test_unknown_or_partial_shell_keeps_original_page_and_never_closes(self):
        for observed in (None, ('blocked_recharge_activity',), ('welfare',)):
            with self.subTest(observed=observed):
                runner = OfflineEntry()
                with patch('hyol_identity_overlays.inspect', return_value=observed), \
                        patch('hyol_identity_overlays.dismiss_identity_overlays') as cleanup:
                    with self.assertRaisesRegex(hs.PendingError, 'page not confirmed'):
                        daruma._open_weekend(runner)
                cleanup.assert_not_called()
                self.assertIs(runner.frame, runner.activity)
                self.assertEqual([], runner.events)

    def test_failed_cleanup_or_missing_return_never_emits_returned_credit(self):
        runner = OfflineEntry()
        with patch('hyol_identity_overlays.inspect', return_value=('recharge_activity',)), \
                patch('hyol_identity_overlays.dismiss_identity_overlays') as cleanup:
            with self.assertRaisesRegex(hs.PendingError, '仍未确认木叶'):
                daruma._open_weekend(runner)
        cleanup.assert_called_once()
        self.assertEqual(['daruma_entry_unconfirmed'], [row[0] for row in runner.events])
        self.assertIs(runner.frame, runner.activity)

    def test_existing_verified_weekend_or_game_page_is_never_reopened(self):
        for name, point in (('daruma_weekend_page.bmp', (900, 260)),
                            ('daruma_game_page.bmp', (930, 230))):
            with self.subTest(name=name):
                runner = OfflineEntry()
                runner.frame = Image.new('RGB', (1920, 1032), (30, 30, 30))
                with Image.open(hs.ASSETS / name) as image:
                    runner.frame.paste(image, point)
                daruma._open_weekend(runner)
                self.assertEqual([], runner.clicks)
                runner.scheduler.wait_until_main.assert_not_called()


if __name__ == '__main__':
    unittest.main()
