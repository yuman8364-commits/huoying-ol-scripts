"""Normal member-result acknowledgements must not infer a battle or identity."""
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

from PIL import Image

import hyol_accounts as accounts
import hyol_scheduler as hs


class ResultScheduler:
    def __init__(self, directory):
        self.shared_state_directory = Path(directory)
        self.no_content_clicks = False
        self._identity_observation_only = False
        self.active = hs.Account(6, 'original', 855)
        self.generic_current_tab_x = None
        self.current_account_only = None
        self._verified_current_mask = 'original-mask'
        self.page = 'result'
        self.selected_x = 315
        self.offline = False
        self.banner = 'arena_victory_banner.bmp'
        self.button = 'team_result_confirm.bmp'
        self.title = 'white'
        self.check_stop = Mock()
        self.ensure_window = Mock()
        self.wait_interruptible = Mock()
        self.event = Mock()
        self.clicks = 0
        self.battle_ledger = {'completed':5, 'actions':{'old_battle':{'status':'pending'}}}
        self.capture_hook = None

    def capture(self):
        if self.capture_hook:
            self.capture_hook(self)
        frame = Image.new('RGB', (1920,1032))
        frame.paste(self.title, (267,5,339,30))
        frame.info.update(page=self.page, active_x=self.selected_x, offline=self.offline)
        return frame

    def template(self, asset, roi, frame, **kwargs):
        visible = frame.info['page'] == 'result' and asset in (self.button,self.banner)
        return visible, 1. if visible else 0., 897,626

    def offline_dialog_visible(self, frame):
        return frame.info['offline']

    def _generic_active_tab_x(self, frame):
        return frame.info['active_x']

    def main_ready(self, frame):
        return frame.info['page'] == 'home'

    def content_guard(self, selected, page_anchors=(), anchor_threshold=None):
        self.ensure_window()
        return self.capture()

    def save_raw_evidence(self, frame, selected, label, phase):
        path = self.shared_state_directory/(phase+'.png')
        frame.save(path)
        return path

    def click_template(self, selected, asset, roi, dx, dy, label, anchors, **kwargs):
        self.content_guard(selected,anchors,anchor_threshold=.98)
        receipt = json.loads((self.shared_state_directory/'normal_result_cleanup.json').read_text('utf-8'))
        assert any(row['status']=='pending' for row in receipt.values())
        assert self.active == selected and self.generic_current_tab_x == selected.tab_x
        assert self.current_account_only == selected.name
        assert not kwargs.get('irreversible',False)
        self.clicks += 1
        self.page = 'home'
        return True


class MemberResultTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.s = ResultScheduler(self.directory.name)
        self.account = hs.Account(2,'unknown-current-member',315)
        self.session = patch.object(accounts,'game_session_token',return_value='123:456:creation')
        self.session.start()
        self.addCleanup(self.session.stop)

    def assert_guards_restored(self):
        self.assertEqual(self.s.active.name,'original')
        self.assertIsNone(self.s.generic_current_tab_x)
        self.assertIsNone(self.s.current_account_only)
        self.assertEqual(self.s._verified_current_mask,'original-mask')
        self.assertNotIn('content_guard',self.s.__dict__)

    def test_normal_member_confirm_returns_home_without_crediting_old_battle(self):
        frame = accounts.dismiss_current_final_result(self.s,self.account)
        self.assertTrue(self.s.main_ready(frame))
        self.assertEqual(self.s.clicks,1)
        receipts = json.loads((Path(self.directory.name)/'normal_result_cleanup.json').read_text())
        self.assertEqual(len(receipts),1)
        row = next(iter(receipts.values()))
        self.assertEqual(row['status'],'done')
        self.assertTrue(row['home_returned'])
        self.assertFalse(row['battle_credit']);self.assertFalse(row['task_credit'])
        self.assertEqual(self.s.battle_ledger,{'completed':5,'actions':{'old_battle':{'status':'pending'}}})
        self.assert_guards_restored()
        accounts.dismiss_current_final_result(self.s,self.account)
        self.assertEqual(self.s.clicks,1)

    def test_defeat_scroll_and_arena_button_also_acknowledge_normal_result(self):
        self.s.banner = 'arena_defeat_banner.bmp'
        self.s.button = 'arena_result_confirm.bmp'
        accounts.dismiss_current_final_result(self.s,self.account)
        self.assertEqual(self.s.clicks,1)

    def test_button_without_final_scroll_cannot_start_any_action(self):
        self.s.banner = 'unknown-banner'
        accounts.dismiss_current_final_result(self.s,self.account)
        self.assertEqual(self.s.clicks,0)
        self.assertFalse((Path(self.directory.name)/'normal_result_cleanup.json').exists())

    def test_strong_result_then_loot_acknowledgement_have_separate_zero_credit_receipts(self):
        original=self.s.click_template
        def acknowledge(*args,**kwargs):
            done=original(*args,**kwargs)
            if self.s.clicks==1:
                self.s.page='result'
                self.s.banner='team_reward_result_header_current.bmp'
                self.s.button=hs.TEAM_REWARD_RESULT_TEMPLATE
            return done
        self.s.click_template=acknowledge
        frame=accounts.dismiss_current_final_result(self.s,self.account)
        self.assertTrue(self.s.main_ready(frame))
        self.assertEqual(self.s.clicks,2)
        rows=json.loads((Path(self.directory.name)/'normal_result_cleanup.json').read_text())
        self.assertEqual(len(rows),2)
        self.assertTrue(all(row['status']=='done' and not row['battle_credit'] and not row['task_credit'] for row in rows.values()))
        self.assertEqual(self.s.battle_ledger,{'completed':5,'actions':{'old_battle':{'status':'pending'}}})
        self.assert_guards_restored()

    def test_existing_loot_page_requires_its_header_and_button(self):
        self.s.button=hs.TEAM_REWARD_RESULT_TEMPLATE
        self.s.banner='unknown'
        accounts.dismiss_current_final_result(self.s,self.account)
        self.assertEqual(self.s.clicks,0)
        self.s.banner='team_reward_result_header_current.bmp'
        accounts.dismiss_current_final_result(self.s,self.account)
        self.assertEqual(self.s.clicks,1)
        self.assert_guards_restored()

    def test_read_only_and_joint_identity_probe_never_confirm(self):
        for attribute in ('no_content_clicks','_identity_observation_only'):
            setattr(self.s,attribute,True)
            accounts.dismiss_current_final_result(self.s,self.account)
            self.assertEqual(self.s.clicks,0)
            setattr(self.s,attribute,False)

    def test_changed_tab_offline_title_and_result_each_prevent_click(self):
        for change in (lambda s:setattr(s,'selected_x',450),lambda s:setattr(s,'offline',True),
                       lambda s:setattr(s,'title','red'),lambda s:setattr(s,'page','running')):
            with self.subTest(change=change):
                self.s = ResultScheduler(self.directory.name)
                before = self.s.capture()
                self.s.capture_hook = change
                with self.assertRaises(hs.PendingError):
                    accounts.dismiss_current_final_result(self.s,self.account,before)
                self.assertEqual(self.s.clicks,0)

    def test_geometry_or_foreground_guard_failure_never_clicks(self):
        self.s.ensure_window.side_effect = hs.SafetyError('geometry changed')
        with self.assertRaises(hs.SafetyError):
            accounts.dismiss_current_final_result(self.s,self.account)
        self.assertEqual(self.s.clicks,0)

    def test_tab_hover_fill_change_after_input_keeps_same_title_and_returns_home(self):
        original = self.s.click_template
        def acknowledge(*args,**kwargs):
            result = original(*args,**kwargs)
            self.s.title = '#dddddd'
            return result
        self.s.click_template = acknowledge
        accounts.dismiss_current_final_result(self.s,self.account)
        self.assertEqual(self.s.clicks,1)
        self.assert_guards_restored()

    def test_home_after_crash_reconciles_matching_ui_receipt_with_zero_clicks(self):
        original = self.s.click_template
        def crash_after_input(*args,**kwargs):
            original(*args,**kwargs)
            raise RuntimeError('simulated process exit')
        self.s.click_template = crash_after_input
        with self.assertRaises(RuntimeError):
            accounts.dismiss_current_final_result(self.s,self.account)
        self.assert_guards_restored()
        self.assertEqual(self.s.clicks,1)
        self.s.click_template = original
        accounts.dismiss_current_final_result(self.s,self.account)
        self.assertEqual(self.s.clicks,1)
        rows = json.loads((Path(self.directory.name)/'normal_result_cleanup.json').read_text())
        row = next(iter(rows.values()))
        self.assertEqual(row['status'],'done')
        self.assertTrue(row['reconciled_without_input'])
        self.assertFalse(row['battle_credit'])

    def test_final_input_guard_rechecks_scroll_and_restores_scope_on_failure(self):
        original = self.s.click_template
        def fail_input(*args,**kwargs):
            self.s.page = 'running'
            return original(*args,**kwargs)
        self.s.click_template = fail_input
        with self.assertRaises(hs.PendingError):
            accounts.dismiss_current_final_result(self.s,self.account)
        self.assertEqual(self.s.clicks,0)
        self.assert_guards_restored()
        rows = json.loads((Path(self.directory.name)/'normal_result_cleanup.json').read_text())
        self.assertEqual(next(iter(rows.values()))['status'],'pending')

    def test_crashed_old_receipt_cannot_bypass_fresh_result_observations(self):
        path = Path(self.directory.name)/'normal_result_cleanup.json'
        old = {'old':{'status':'pending','battle_credit':False,'session':'old-session'}}
        path.write_text(json.dumps(old))
        before = self.s.capture()
        self.s.capture_hook = lambda s:setattr(s,'selected_x',450)
        with self.assertRaises(hs.PendingError):
            accounts.dismiss_current_final_result(self.s,self.account,before)
        self.assertEqual(self.s.clicks,0)
        self.assertEqual(json.loads(path.read_text()),old)

    def test_binding_observes_name_after_normal_result_acknowledgement(self):
        self.s._dynamic_identity_materials = {}
        with patch.object(accounts,'collect_current_identity',return_value=([Mock()]*3,None)) as collect, \
             patch.object(accounts,'register_observations',return_value=({2:'account-new'},{})):
            self.assertEqual(accounts.bind_visible_identity(self.s,self.account),'account-new')
        self.assertTrue(self.s.main_ready(collect.call_args.args[2]))
        self.assertEqual(self.s.clicks,1)


if __name__=='__main__':
    unittest.main()
