"""Strong x2 uses a real x1 battle and durable per-attempt toggle receipts."""
from types import SimpleNamespace
from unittest.mock import Mock
import unittest
from PIL import Image
import hyol_scheduler as hs


class StrongSpeedTests(unittest.TestCase):
    def setUp(self):
        self.s = object.__new__(hs.Scheduler)
        self.s.templates = hs.TemplateStore(.92)
        with Image.open(hs.ROOT/'fixtures/strong_speed_20261109/real-x1.png') as image:
            self.x1 = image.convert('RGB')
        self.x2 = self.x1.copy()
        self.x2.paste((0,0,0),(1260,890,1335,975))
        with Image.open(hs.ASSETS/'battle_speed_x2.png') as badge:
            self.x2.paste(badge.convert('RGB'),(1273,910))
        self.team, self.owner = hs.Team(0,('solo',)),hs.Account(1,'solo',180)
        self.spec = hs.TEAM_ACTIVITY_BY_KEY['strong_approach']
        self.base = 'team0_strong_approach_attempt_1'
        self.action = self.base+'_speed_x2'
        self.status = {self.base+'_start':'done'}
        self.s.state = SimpleNamespace(
            action_status=lambda owner,key:self.status.get(key,'new'),
            set_action=lambda owner,key,status,detail:self.status.update({key:status}))
        self.s.event = Mock()
        self.s.save_raw_evidence = Mock(return_value='speed-proof.png')
        self.s.wait_template = Mock(return_value=True)
        self.s.capture = Mock(return_value=self.x2)
        def click(*args,**kwargs):
            self.assertTrue(kwargs['irreversible'])
            self.assertTrue(kwargs['frame_predicate'](self.x1))
            self.status[args[5]] = 'pending'
            self.s._last_frame = self.x2
            return True
        self.s.click_template = Mock(side_effect=click)

    def apply(self, frame):
        return self.s._ensure_strong_battle_x2(self.team,self.owner,self.spec,1,frame)

    def test_real_battle_has_auto_on_and_x1_before_request(self):
        self.assertTrue(self.s._battle_hud_visible(self.x1))
        self.assertTrue(self.s._battle_auto_on_visible(self.x1))
        self.assertTrue(self.s.template('battle_speed_x1.png',(1260,890,1335,975),self.x1,threshold=.84)[0])

    def test_one_toggle_is_verified_and_not_repeated_in_later_polls(self):
        self.assertTrue(self.apply(self.x1))
        self.assertEqual('done',self.status[self.action])
        self.assertFalse(self.apply(self.x2))
        self.s.click_template.assert_called_once()
        self.assertEqual(self.action,self.s.click_template.call_args.args[5])

    def test_already_x2_is_recorded_without_a_toggle(self):
        self.assertFalse(self.apply(self.x2))
        self.assertEqual('done',self.status[self.action])
        self.s.click_template.assert_not_called()

    def test_unknown_toggle_is_not_retried_and_stays_pending(self):
        self.s.wait_template.return_value = False
        with self.assertRaises(hs.PendingError):self.apply(self.x1)
        self.assertEqual('pending',self.status[self.action])
        with self.assertRaises(hs.PendingError):self.apply(self.x1)
        self.s.click_template.assert_called_once()

    def test_original_pending_toggle_can_be_reconciled_from_visible_x2(self):
        self.status[self.action] = 'pending'
        self.assertFalse(self.apply(self.x2))
        self.assertEqual('done',self.status[self.action])
        self.s.click_template.assert_not_called()

    def test_no_owned_start_or_disabled_auto_has_zero_input(self):
        self.status.clear()
        with self.assertRaises(hs.PendingError):self.apply(self.x1)
        self.s.click_template.assert_not_called()
        self.status[self.base+'_start'] = 'done'
        self.s._battle_auto_on_visible = Mock(return_value=False)
        self.assertFalse(self.apply(self.x1))
        self.s.click_template.assert_not_called()

    def test_done_toggle_cannot_silently_flip_a_later_x1_back(self):
        self.status[self.action] = 'done'
        with self.assertRaises(hs.PendingError):self.apply(self.x1)
        self.s.click_template.assert_not_called()


if __name__ == '__main__':
    unittest.main()
