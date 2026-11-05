"""Regression coverage for the October 9 stopped solo run; no game input."""
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

from PIL import Image
import hyol_scheduler as hs
import hyol_history as history


class LiveSoloFixTests(unittest.TestCase):
    def visual_scheduler(self):
        s = object.__new__(hs.Scheduler)
        s.templates, s.detector = hs.TemplateStore(.92), None
        return s

    def transition_scheduler(self, frames):
        account = hs.Account(1, 'arbitrary-role', 180)
        s = SimpleNamespace(active=account, _validate_tab_binding=Mock(),
            _saved_tab_mapping=lambda:{account.name:180}, _generic_active_tab_x=Mock(return_value=180),
            capture=Mock(side_effect=frames), healthy=Mock(side_effect=lambda frame:frame!='white'),
            tab_is_visually_active=Mock(return_value=True), chrome_click=Mock(),
            template=Mock(return_value=(False,0,0,0)),
            _combat_active_visible=Mock(side_effect=lambda mode,frame:frame=='battle'),
            _combat_ready_visible=Mock(return_value=False), verify_identity=Mock(return_value=(False,{})),
            _record_tab_binding=Mock(), event=Mock(), save_raw_evidence=Mock(return_value='unknown.png'))
        clock = [0.]
        s.wait_interruptible=lambda seconds:clock.__setitem__(0,clock[0]+seconds)
        return s,account,clock

    def test_paired_battle_hud_survives_obscured_auto_without_confirming_result(self):
        s=self.visual_scheduler()
        with Image.open(hs.ROOT/'tests/fixtures/survival_battle_hud_occluded_20261009.png') as frame:
            self.assertTrue(s._combat_active_visible('survival',frame))
            for missing in ((410,200,520,255),(1390,200,1520,255)):
                partial=frame.copy();partial.paste((13,15,17),missing)
                self.assertFalse(s._combat_active_visible('survival',partial))
            self.assertFalse(s.template('survival_result_confirm.bmp',(701,519,1151,819),frame)[0])
        self.assertFalse(s._combat_active_visible('survival',Image.new('RGB',(1920,1032),'white')))

    def test_same_selected_tab_waits_through_white_repaint_without_input(self):
        s,account,clock=self.transition_scheduler(['white','battle'])
        with patch.object(hs.time,'monotonic',side_effect=lambda:clock[0]):
            result=hs.Scheduler.switch_account_for_combat(s,account,'survival',True)
        self.assertEqual('battle',result)
        self.assertEqual(.4,clock[0])
        s.chrome_click.assert_not_called()
        s.save_raw_evidence.assert_not_called()

    def test_unknown_running_tab_saves_evidence_and_never_restarts_fight(self):
        s,account,clock=self.transition_scheduler(iter(lambda:'unknown',None))
        with patch.object(hs.time,'monotonic',side_effect=lambda:clock[0]):
            with self.assertRaisesRegex(hs.PendingError,'no positive battle/page anchor'):
                hs.Scheduler.switch_account_for_combat(s,account,'survival',True)
        self.assertGreaterEqual(clock[0],12)
        s.chrome_click.assert_not_called()
        s.save_raw_evidence.assert_called_once_with('unknown',account,'combat_transition_unknown','pending')
        s._record_tab_binding.assert_not_called()

    def test_user_changed_tab_during_transition_is_never_accepted(self):
        s,account,clock=self.transition_scheduler(['unknown'])
        s._generic_active_tab_x.side_effect=[180,320]
        with patch.object(hs.time,'monotonic',side_effect=lambda:clock[0]):
            with self.assertRaisesRegex(hs.PendingError,'tab changed'):
                hs.Scheduler.switch_account_for_combat(s,account,'survival',True)
        s.chrome_click.assert_not_called()
        s._record_tab_binding.assert_not_called()

    def test_battle_hud_blocks_idle_historical_isolation_even_if_auto_is_hidden(self):
        s=self.visual_scheduler()
        account=hs.Account(1,'arbitrary-role',180)
        token='1:2:1dc370adbd58400'
        frame=Image.open(hs.ROOT/'tests/fixtures/survival_battle_hud_occluded_20261009.png')
        self.addCleanup(frame.close)
        s.active=account;s.capture=Mock(return_value=frame);s.check_stop=Mock()
        s.verify_identity=Mock(return_value=(True,{}));s.main_ready=Mock(return_value=True)
        s._battle_auto_on_visible=Mock(return_value=False)
        s.state=SimpleNamespace(aliases={});s.save_raw_evidence=Mock()
        with patch('hyol_accounts.game_session_token',return_value=token):
            with self.assertRaises(hs.PendingError):history.prove_idle_identity(s,account)
        s.save_raw_evidence.assert_not_called()


if __name__=='__main__':unittest.main()
