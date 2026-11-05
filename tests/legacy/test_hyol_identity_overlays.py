"""Known X recovery must never credit a personal identity or any task result."""
from copy import deepcopy
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
from unittest.mock import Mock
from types import SimpleNamespace

import hyol_accounts as accounts
import hyol_identity_overlays as overlays
import hyol_scheduler as hs
from test_hyol_account_results import ResultScheduler


class OverlayScheduler(ResultScheduler):
    def __init__(self,directory):
        super().__init__(directory)
        self.page = 'notice'
        self.close_history = []
        self.missing = set()
        self.close_exception = None
        self.extra=set()

    def template(self,asset,roi,frame,**kwargs):
        pages = {
            'notice': {'login_update_notice_title_current.bmp','login_update_notice_body_current.bmp',
                       'login_update_notice_close_core_current.bmp','welfare_title_current.bmp','welfare_close_current.bmp'},
            'welfare': {'welfare_title_current.bmp','welfare_close_current.bmp'},
            'market': {'market_anchor.bmp','market_close.bmp'},
            'arena': {'arena_ready_anchor.bmp','arena_casual_button_current.bmp','market_close.bmp'},
            'unknown': {'welfare_close_current.bmp','market_close.bmp'},
            'home': set()}
        visible = asset in (pages[frame.info['page']]|self.extra) and asset not in self.missing
        return visible,1. if visible else 0.,1368,433

    def click_template(self,selected,asset,roi,dx,dy,label,anchors,**kwargs):
        self.content_guard(selected,anchors,anchor_threshold=.98)
        records = json.loads((self.shared_state_directory/'identity_overlay_cleanup.json').read_text('utf-8'))
        assert any(row['status']=='pending' for row in records.values())
        assert self.active==selected and self.generic_current_tab_x==selected.tab_x
        assert self.current_account_only==selected.name
        assert not kwargs.get('irreversible',False)
        assert kwargs['threshold']==.98
        self.clicks += 1
        self.close_history.append(asset)
        if self.close_exception:
            raise self.close_exception
        self.page = 'welfare' if self.page=='notice' else 'home'
        return True


class IdentityOverlayTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.s=OverlayScheduler(self.temp.name)
        self.account=hs.Account(2,'unconfirmed-tab',315)
        self.session=patch.object(accounts,'game_session_token',return_value='window:process:created')
        self.session.start();self.addCleanup(self.session.stop)

    def assert_restored(self):
        self.assertEqual('original',self.s.active.name)
        self.assertIsNone(self.s.generic_current_tab_x)
        self.assertIsNone(self.s.current_account_only)
        self.assertEqual('original-mask',self.s._verified_current_mask)
        self.assertNotIn('content_guard',self.s.__dict__)

    def test_notice_then_welfare_only_close_their_own_x_and_preserve_task_ledger(self):
        original=deepcopy(self.s.battle_ledger)
        frame=overlays.dismiss_identity_overlays(self.s,self.account)
        self.assertTrue(self.s.main_ready(frame))
        self.assertEqual(['login_update_notice_close_core_current.bmp','welfare_close_current.bmp'],self.s.close_history)
        self.assertEqual(original,self.s.battle_ledger)
        records=json.loads((self.s.shared_state_directory/'identity_overlay_cleanup.json').read_text())
        self.assertEqual(2,len(records))
        for row in records.values():
            self.assertEqual('done',row['status'])
            self.assertEqual('unconfirmed',row['identity'])
            self.assertFalse(row['battle_credit']);self.assertFalse(row['task_credit'])
        self.assert_restored()

    def test_market_x_recovers_hidden_name_without_binding_an_identity(self):
        self.s.page='market'
        original=deepcopy(self.s.battle_ledger)
        self.assertTrue(self.s.main_ready(overlays.dismiss_identity_overlays(self.s,self.account)))
        self.assertEqual(['market_close.bmp'],self.s.close_history)
        self.assertEqual(original,self.s.battle_ledger)
        self.assert_restored()

    def test_verified_arena_lobby_x_preserves_every_original_task_action(self):
        self.s.page='arena';original=deepcopy(self.s.battle_ledger)
        self.assertTrue(self.s.main_ready(overlays.dismiss_identity_overlays(self.s,self.account)))
        self.assertEqual(['market_close.bmp'],self.s.close_history)
        self.assertEqual(original,self.s.battle_ledger)
        self.assert_restored()

    def test_arena_matching_loading_or_result_never_closes_the_underlying_lobby(self):
        for asset in ('arena_matching_anchor.bmp','arena_loading_anchor.bmp',
                      'arena_result_confirm.bmp','arena_defeat_confirm.bmp','arena_rankup_confirm.bmp'):
            with self.subTest(asset=asset):
                self.s.page='arena';self.s.extra={asset}
                with self.assertRaises(hs.PendingError):overlays.dismiss_identity_overlays(self.s,self.account)
                self.assertEqual(0,self.s.clicks)
                self.assert_restored()

    def test_missing_notice_x_never_clicks_the_recognized_underlying_welfare_x(self):
        self.s.missing.add('login_update_notice_close_core_current.bmp')
        with self.assertRaises(hs.PendingError):overlays.dismiss_identity_overlays(self.s,self.account)
        self.assertEqual(0,self.s.clicks)
        self.assert_restored()

    def test_missing_notice_title_or_body_does_not_close_underlying_window(self):
        for asset in ('login_update_notice_title_current.bmp','login_update_notice_body_current.bmp'):
            with self.subTest(asset=asset):
                self.s.missing={asset}
                with self.assertRaises(hs.PendingError):overlays.dismiss_identity_overlays(self.s,self.account)
                self.assertEqual(0,self.s.clicks)

    def test_readonly_or_identity_observation_never_closes_any_page(self):
        for field in ('no_content_clicks','_identity_observation_only'):
            setattr(self.s,field,True)
            frame=overlays.dismiss_identity_overlays(self.s,self.account)
            self.assertEqual('notice',frame.info['page'])
            self.assertEqual(0,self.s.clicks)
            setattr(self.s,field,False)
        self.assertFalse((self.s.shared_state_directory/'identity_overlay_cleanup.json').exists())

    def test_changed_tab_or_browser_title_before_close_has_zero_input(self):
        for kind in ('tab','title'):
            with self.subTest(kind=kind):
                self.s.selected_x=315;self.s.title='white'
                calls=[0]
                def change(s):
                    calls[0]+=1
                    if calls[0]>=2:
                        if kind=='tab':s.selected_x=450
                        else:s.title='black'
                self.s.capture_hook=change
                with self.assertRaises(hs.PendingError):overlays.dismiss_identity_overlays(self.s,self.account)
                self.assertEqual(0,self.s.clicks)
                self.assert_restored()

    def test_failed_post_click_capture_preserves_receipt_and_restores_every_guard(self):
        self.s.close_exception=hs.SafetyError('post-click capture failed')
        with self.assertRaises(hs.SafetyError):overlays.dismiss_identity_overlays(self.s,self.account)
        self.assertEqual(1,self.s.clicks)
        self.assert_restored()
        records=json.loads((self.s.shared_state_directory/'identity_overlay_cleanup.json').read_text())
        self.assertEqual('pending',next(iter(records.values()))['status'])
        with self.assertRaises(hs.PendingError):overlays.dismiss_identity_overlays(self.s,self.account)
        self.assertEqual(1,self.s.clicks)

    def test_unknown_page_with_only_generic_red_x_has_zero_input(self):
        self.s.page='unknown'
        self.assertEqual('unknown',overlays.dismiss_identity_overlays(self.s,self.account).info['page'])
        self.assertEqual(0,self.s.clicks)
        self.assert_restored()

    def test_f12_before_ui_close_has_zero_input_and_no_task_changes(self):
        original=deepcopy(self.s.battle_ledger)
        self.s.check_stop.side_effect=KeyboardInterrupt()
        with self.assertRaises(KeyboardInterrupt):overlays.dismiss_identity_overlays(self.s,self.account)
        self.assertEqual(0,self.s.clicks)
        self.assertEqual(original,self.s.battle_ledger)
        self.assert_restored()


class HoverPreviewTests(unittest.TestCase):
    def scheduler(self):
        return SimpleNamespace(hwnd=123,no_content_clicks=False,_identity_observation_only=False,
            ensure_window=Mock(),check_input_pause=Mock(),screen_point=Mock(return_value=(560,210)),
            wait_interruptible=Mock(),content_click_count=0)

    def test_hover_clear_uses_current_geometry_without_any_click_or_task_change(self):
        s=self.scheduler()
        with patch.object(hs.Win32,'foreground',return_value=123),patch.object(hs.Win32,'move') as move,patch.object(hs.Win32,'click') as click:
            self.assertTrue(overlays.clear_hover_preview(s))
            move.assert_called_once_with(560,210);click.assert_not_called()
        self.assertEqual(0,s.content_click_count)
        for field in ('no_content_clicks','_identity_observation_only'):
            setattr(s,field,True)
            with patch.object(hs.Win32,'move') as move:
                self.assertFalse(overlays.clear_hover_preview(s));move.assert_not_called()
            setattr(s,field,False)

    def test_hover_clear_rejects_foreign_foreground_before_any_pointer_input(self):
        s=self.scheduler()
        with patch.object(hs.Win32,'foreground',return_value=456),patch.object(hs.Win32,'move') as move:
            with self.assertRaises(hs.SafetyError):overlays.clear_hover_preview(s)
            move.assert_not_called()


if __name__=='__main__':unittest.main()
