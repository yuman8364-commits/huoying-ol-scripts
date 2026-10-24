"""The shared round X needs an enrolled notice body, including a queued group card."""
from pathlib import Path
from unittest.mock import Mock
import unittest
from PIL import Image
import hyol_scheduler as hs

PROBE=hs.ROOT/'logs/portable_20261023/live_probe'
DUEL=hs.ROOT/'logs/live_continuation_20261023/runtime/scheduler_evidence/2026-10-23/account_a802d1815a/160555_921192_pending_original_managed_resume9.png'


class NoticeQueueTests(unittest.TestCase):
    def scheduler(self,frame):
        s=object.__new__(hs.Scheduler);s.templates=hs.TemplateStore(.98);s.detector=None
        s.capture=Mock(return_value=frame);s.wait_interruptible=Mock();s.event=Mock()
        s.click_template=Mock(return_value=True)
        return s

    def frame(self,slot):return Image.open(PROBE/f'tab_{slot}.png').convert('RGB')

    def cleared(self,frame):
        fresh=frame.copy();fresh.paste((20,25,30),(1680,700,1919,930))
        return fresh

    def test_real_group_notice_uses_same_strict_round_close(self):
        frame=self.frame(1);s=self.scheduler(frame)
        self.assertEqual(s._activity_notice_anchor(frame),'activity_notice_anchor.bmp')
        match=s._activity_notice_close_match(frame)
        self.assertIsNotNone(match,'A known group notice must admit its positively enrolled shared X')
        self.assertEqual(match[0],'activity_notice_merchant_close_current.bmp')
        self.assertGreaterEqual(match[1],.98)
        self.assertEqual(match[2:4],(1877,717))

    def test_real_group_card_closes_once_and_requires_disappearance(self):
        frame=self.frame(1);fresh=self.cleared(frame);s=self.scheduler(frame)
        s.capture.return_value=fresh
        self.assertEqual(s.clear_transient_notifications(hs.Account(1,'generic-tab',180),frame),fresh)
        s.click_template.assert_called_once()
        self.assertTrue(s.click_template.call_args.kwargs['frame_predicate'](frame))
        self.assertFalse(s.click_template.call_args.kwargs['frame_predicate'](fresh))

    def test_shared_X_without_a_known_notice_body_sends_no_input(self):
        frame=self.frame(1);unknown=self.cleared(frame)
        unknown.paste(frame.crop((1877,717,1912,752)),(1877,717))
        s=self.scheduler(unknown)
        self.assertIsNone(s._activity_notice_anchor(unknown))
        self.assertEqual(s.clear_transient_notifications(hs.Account(1,'generic-tab',180),unknown),unknown)
        s.click_template.assert_not_called()

    def test_notice_without_its_close_stays_pending(self):
        frame=self.frame(1);frame.paste((20,25,30),(1865,700,1919,780));s=self.scheduler(frame)
        with self.assertRaisesRegex(hs.PendingError,'verified X is missing'):
            s.clear_transient_notifications(hs.Account(1,'generic-tab',180),frame)
        s.click_template.assert_not_called()

    def test_merchant_notice_keeps_the_exact_header_and_close_pair(self):
        frame=self.frame(5);s=self.scheduler(frame)
        self.assertEqual(s._activity_notice_anchor(frame),'activity_notice_merchant_title_current.bmp')
        self.assertGreaterEqual(s._activity_notice_close_match(frame)[1],.98)

    def test_real_queued_duel_notice_uses_its_strict_native_size_close(self):
        frame=Image.open(DUEL).convert('RGB');s=self.scheduler(frame)
        self.assertEqual(s._activity_notice_anchor(frame),'activity_notice_anchor.bmp')
        match=s._activity_notice_close_match(frame)
        self.assertIsNotNone(match,'The real larger close button must be positively identified')
        self.assertEqual(match[0],'activity_notice_round_large_close_current.bmp')
        self.assertGreaterEqual(match[1],.98)
        self.assertEqual(match[5:7],(42,42))
        fresh=self.cleared(frame);s.capture.return_value=fresh
        self.assertEqual(s.clear_transient_notifications(hs.Account(5,'generic-tab',720),frame),fresh)
        s.click_template.assert_called_once()
        predicate=s.click_template.call_args.kwargs['frame_predicate']
        self.assertTrue(predicate(frame))
        self.assertFalse(predicate(fresh))

    def test_known_notice_frame_without_close_stays_pending(self):
        frame=Image.open(DUEL).convert('RGB');frame.paste((20,25,30),(1865,700,1919,780))
        s=self.scheduler(frame)
        with self.assertRaisesRegex(hs.PendingError,'verified X is missing'):
            s.clear_transient_notifications(hs.Account(5,'generic-tab',720),frame)
        s.click_template.assert_not_called()

    def test_large_round_close_without_known_body_sends_no_input(self):
        source=Image.open(DUEL).convert('RGB')
        frame=self.cleared(source)
        frame.paste(source.crop((1873,714,1915,756)),(1873,714))
        s=self.scheduler(frame)
        self.assertIsNone(s._activity_notice_anchor(frame))
        self.assertEqual(s.clear_transient_notifications(hs.Account(5,'generic-tab',720),frame),frame)
        s.click_template.assert_not_called()


if __name__=='__main__':unittest.main()
