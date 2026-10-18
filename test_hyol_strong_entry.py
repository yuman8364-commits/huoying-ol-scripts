"""Strong-entry and weekly counter regressions from inspected game frames."""
import unittest
from unittest.mock import Mock
from PIL import Image
import hyol_scheduler as hs


class StrongEntryTests(unittest.TestCase):
    def scheduler(self, frame):
        s = object.__new__(hs.Scheduler)
        s.config = {'RecognitionRetrySeconds': '0'}
        s.frame = frame
        s.capture = lambda: s.frame
        s.templates = hs.TemplateStore(.90)
        def template(name, roi=hs.VIEW, frame=None, threshold=None):
            if name == 'strong_approach_anchor.bmp':
                name = 'strong_mirror_anchor.bmp'
            return s.templates.match(frame if frame is not None else s.frame, hs.ASSETS/name, roi, threshold)
        s.template = template
        s.content_guard = lambda *args, **kwargs: s.frame
        s.prepare_main = Mock()
        s.event = Mock()
        s.wait_interruptible = Mock()
        s.wait_template = lambda name, roi, *args: s.template(name, roi)[0]
        s.save_raw_evidence = Mock(return_value='unreadable.png')
        s.click_point = Mock()
        return s

    def test_current_entry_matches_core_and_clicks_inside_icon(self):
        with Image.open(hs.LOGS/'strong_entry_failure_20260915.png') as im:
            frame = im.convert('RGB')
        s = self.scheduler(frame)
        self.assertFalse(s.template('strong_approach_entry.bmp', (1000,69,1450,165))[0])
        self.assertTrue(s.template('strong_approach_entry_core.bmp', (1000,69,1450,150))[0])
        def click(account,x,y,*args,**kwargs):
            self.assertTrue(1205 <= x < 1240 and 72 <= y < 110)
            with Image.open(hs.LOGS/'strong_live_page_20260915.png') as im:
                s.frame = im.convert('RGB')
        s.click_point.side_effect = click
        s.open_strong_approach(hs.Team(0,('test',)), hs.Account(1,'test',180), hs.TEAM_ACTIVITY_BY_KEY['strong_approach'])
        s.click_point.assert_called_once()

    def test_missing_icon_has_zero_clicks(self):
        with Image.open(hs.LOGS/'strong_entry_failure_20260915.png') as im:
            frame = im.convert('RGB')
        frame.paste('black',(1000,69,1450,165))
        s=self.scheduler(frame)
        with self.assertRaises(hs.PendingError):
            s.open_strong_approach(hs.Team(0,('test',)), hs.Account(1,'test',180), hs.TEAM_ACTIVITY_BY_KEY['strong_approach'])
        s.click_point.assert_not_called()

    def test_close_button_is_located_on_current_strong_page(self):
        with Image.open(hs.LOGS/'strong_live_page_20260915.png') as im:
            s = self.scheduler(im.convert('RGB'))
        s.main_ready = lambda frame: False
        s.wait_until_main = Mock(return_value=True)
        s._close_team_activity_page(hs.Account(1,'test',180), hs.TEAM_ACTIVITY_BY_KEY['strong_approach'])
        s.click_point.assert_called_once()
        args = s.click_point.call_args.args
        self.assertTrue(1335 <= args[1] < 1380 and 315 <= args[2] < 360)

    def test_four_normal_rewards_are_not_confused_with_zero_organization_rewards(self):
        with Image.open(hs.LOGS/'strong_live_page_20260915.png') as im:
            s=self.scheduler(im.convert('RGB'))
        self.assertEqual(s._read_team_reward_remaining(hs.Account(1,'test',180),hs.TEAM_ACTIVITY_BY_KEY['strong_approach']),4)
        s.frame.paste('black',hs.STRONG_APPROACH_WEEKLY_REWARD_ROI)
        with self.assertRaises(hs.PendingError):
            s._read_team_reward_remaining(hs.Account(1,'test',180),hs.TEAM_ACTIVITY_BY_KEY['strong_approach'])


if __name__=='__main__': unittest.main()
