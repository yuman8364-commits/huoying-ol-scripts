"""Loading without resource paths permits observation only, with the original deadline."""
from types import SimpleNamespace
from unittest.mock import Mock
import unittest
from PIL import Image
import hyol_scheduler as hs
import hyol_treasure as treasure


class TreasureLoadingTests(unittest.TestCase):
    def runner(self, *, missing=(), since=None):
        frame = Image.new('RGB', (1920,1032), (30,70,40))
        for name, xy in (('map_loading_word_current.bmp',(793,557)),
                         ('map_loading_bar_left_current.bmp',(744,530)),
                         ('map_loading_bar_right_current.bmp',(1160,530))):
            if name not in missing:
                with Image.open(hs.ASSETS/name) as asset:
                    frame.paste(asset.convert('RGB'), xy)
        record = dict(status='pending', started_at=1000, deadline_at=1300)
        if since is not None:
            record['transition_started_at'] = since
        s = SimpleNamespace(capture=Mock(return_value=frame), check_stop=Mock(), main_ready=Mock(return_value=False),
                            _combat_active_visible=Mock(return_value=False), _battle_auto_on_visible=Mock(return_value=False),
                            click_point=Mock(), commit_action=Mock())
        t = hs.TemplateStore(.98)
        r = SimpleNamespace(scheduler=s, record=lambda name:record, save=Mock(), click=Mock(), event=Mock())
        r.visible = lambda name, roi, image=None, threshold=.92: t.match(
            frame if image is None else image, hs.ASSETS/name, roi, threshold=threshold)[0]
        return r, frame, record

    def test_loading_bar_without_resource_path_preserves_expired_deadline_and_sends_no_input(self):
        r, frame, record = self.runner()
        self.assertTrue(treasure.loading_visible(r, frame))
        self.assertFalse(treasure.TreasureRun(r, clock=lambda:1400).step())
        self.assertEqual(record['deadline_at'],1300)
        self.assertTrue(record['stop_due'])
        self.assertEqual(record['phase'],'loading')
        r.click.assert_not_called()
        r.scheduler.click_point.assert_not_called()
        r.scheduler.commit_action.assert_not_called()

    def test_loading_text_and_both_bar_caps_are_required(self):
        for name in ('map_loading_word_current.bmp','map_loading_bar_left_current.bmp',
                     'map_loading_bar_right_current.bmp'):
            with self.subTest(missing=name):
                r, frame, record = self.runner(missing=(name,))
                self.assertFalse(treasure.loading_visible(r, frame))

    def test_bar_without_text_cannot_authorize_ground_or_start(self):
        r, frame, record = self.runner(missing=('map_loading_word_current.bmp',))
        with self.assertRaisesRegex(hs.PendingError,'当前画面未知'):
            treasure.TreasureRun(r, clock=lambda:1400).step()
        r.click.assert_not_called()
        r.scheduler.click_point.assert_not_called()
        self.assertEqual(record['deadline_at'],1300)

    def test_loading_timeout_keeps_original_deadline_and_remains_pending(self):
        r, frame, record = self.runner(since=1100)
        with self.assertRaisesRegex(hs.PendingError,'加载60秒'):
            treasure.TreasureRun(r, clock=lambda:1200).step()
        self.assertEqual(record['deadline_at'],1300)
        r.click.assert_not_called()
        r.scheduler.click_point.assert_not_called()

    def test_treasure_toolbar_observation_uses_icon_before_background_text(self):
        def ensure(account, target, roi):
            self.assertEqual(target, 'treasure_toolbar_icon_core_current.bmp')
        s = SimpleNamespace(ensure_activity_toolbar=Mock(side_effect=ensure), capture=Mock())
        r = SimpleNamespace(scheduler=s, account='same-account', click=Mock(), wait=Mock())
        r.visible = lambda name, *args, **kwargs: name == 'treasure_toolbar_icon_core_current.bmp'
        treasure.open_page(r)
        self.assertEqual(r.click.call_args.args[0], 'treasure_toolbar_icon_core_current.bmp')
        self.assertEqual(r.click.call_args.kwargs['threshold'], .98)


if __name__ == '__main__':
    unittest.main()
