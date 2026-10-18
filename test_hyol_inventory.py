"""Capacity recovery: real UI scope, durable conversion and original claims."""
from copy import deepcopy
from datetime import date,timedelta
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
import unittest
from unittest.mock import Mock,patch

from PIL import Image

import hyol_inventory as inv
import hyol_scheduler as hs


class FragmentRecoveryTests(unittest.TestCase):
    def setUp(self):
        folder = TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        self.root = Path(folder.name)
        self.account = hs.Account(1,'verified-role',180)
        self.state = hs.StateStore([self.account.name],self.root/'state')
        self.old = {f'mail_claim_all_network_{i}':dict(status='pending',detail='written before click',updated_at=f'original-{i}') for i in range(1,4)}
        self.state.accounts[self.account.name]['actions'] = deepcopy(self.old)
        self.state.accounts[self.account.name]['network_retries'] = {'mail_claim_all':dict(attempts=3,verified=False)}
        self.s = Mock()
        self.s.state = self.state
        self.s.active = self.account
        self.s.diagnostics.identifier = 'original-ticket'
        self.before = Image.new('RGB',(1920,1032),(30,30,30))
        self.after = self.before.copy()
        self.after.paste('white',inv.POINTS_ROI)
        self.converted = False
        self.prompt = False
        self.scope = True
        self.empty = True
        self.now = 0
        self.s.capture.side_effect = lambda: self.after if self.converted else self.before
        self.s.template.side_effect = self.template
        self.s.wait_template.return_value = True
        self.s.save_raw_evidence.side_effect = self.evidence
        self.s.click_template.side_effect = self.input
        self.s.commit_action.side_effect = lambda a,k,detail: self.state.set_action(a.name,k,'done',detail)
        self.s.wait_interruptible.side_effect = lambda seconds: setattr(self,'now',self.now+seconds)
        self.recovery = inv.FragmentRecovery(self.s,self.account)
        self.recovery.request('real-full-notice.png',reason='user_confirmed_fragment_capacity')

    def evidence(self,frame,account,label,phase):
        path = self.root/(label+'.png')
        frame.save(path)
        return path

    def template(self,name,*args,**kw):
        names = {inv.TITLE,inv.SURPLUS,inv.CLOSE}
        if self.prompt:
            names.update({'fragment_exchange_effect_current.bmp','fragment_exchange_points_label_current.bmp',
                          'fragment_exchange_yes_current.bmp','fragment_exchange_no_current.bmp'})
            if self.scope:
                names.add('fragment_exchange_scope_current.bmp')
        if self.converted and self.empty:
            names.update({inv.EMPTY,'fragment_exchange_disabled_current.bmp'})
        return name in names,1.,0,0

    def input(self,a,name,roi,dx,dy,label,anchors,**kw):
        if kw.get('irreversible'):
            self.state.set_action(a.name,label,'pending','written before click')
        if name == 'fragment_exchange_current.bmp':
            self.prompt = True
        elif name == 'fragment_exchange_yes_current.bmp':
            self.converted = True
            self.prompt = False
        return True

    def space(self):
        with patch.object(inv.time,'monotonic',side_effect=lambda: self.now),patch.object(inv,'close_shells'):
            self.recovery.ensure_space()

    def conversion_calls(self):
        return [call for call in self.s.click_template.call_args_list if call.args[1] == 'fragment_exchange_yes_current.bmp']

    def test_scope_and_result_allow_one_conversion_and_preserve_old_claim_budget(self):
        self.space()
        self.assertEqual(1,len(self.conversion_calls()))
        self.assertEqual('space_verified',self.recovery.record['phase'])
        self.assertEqual(3,self.recovery.record['verified_frames'])
        self.assertEqual(self.old,{k:self.state.accounts[self.account.name]['actions'][k] for k in self.old})
        self.assertEqual(3,self.state.accounts[self.account.name]['network_retries']['mail_claim_all']['attempts'])
        self.recovery = inv.FragmentRecovery(self.s,self.account)
        self.space()
        self.assertEqual(1,len(self.conversion_calls()))

    def test_unverified_prompt_never_confirms_or_refines_with_points(self):
        self.scope = False
        with self.assertRaisesRegex(hs.PendingError,'共同确认'):
            self.space()
        self.assertEqual([],self.conversion_calls())
        self.assertFalse(any(call.args[1] in ('fragment_refine_normal.bmp','fragment_refine_advanced.bmp') for call in self.s.click_template.call_args_list))

    def test_missing_empty_result_keeps_conversion_pending_without_second_input(self):
        self.empty = False
        with self.assertRaisesRegex(hs.PendingError,'结果未确认'):
            self.space()
        self.assertEqual('conversion_requested',self.recovery.record['phase'])
        action = self.recovery.record['conversion_action']
        self.assertEqual('pending',self.state.action_status(self.account.name,action))
        self.recovery = inv.FragmentRecovery(self.s,self.account)
        with self.assertRaises(hs.PendingError):
            self.space()
        self.assertEqual(1,len(self.conversion_calls()))
        self.empty = True
        self.space()
        self.assertEqual('done',self.state.action_status(self.account.name,action))
        self.assertEqual(1,len(self.conversion_calls()))

    def test_empty_grid_without_points_change_never_resolves_conversion(self):
        self.after = self.before
        with self.assertRaisesRegex(hs.PendingError,'结果未确认'):
            self.space()
        self.assertEqual('pending',self.state.action_status(self.account.name,self.recovery.record['conversion_action']))

    def test_f12_after_conversion_request_preserves_original_and_sends_no_repeat(self):
        original = self.input
        def interrupted(*args,**kwargs):
            result = original(*args,**kwargs)
            if args[1] == 'fragment_exchange_yes_current.bmp':
                self.s.capture.reset_mock()
                raise KeyboardInterrupt('F12 after input')
            return result
        self.s.click_template.side_effect = interrupted
        with self.assertRaises(KeyboardInterrupt):
            self.space()
        self.s.capture.assert_not_called()  # Pending evidence uses the last captured frame.
        self.assertEqual('conversion_requested',self.recovery.record['phase'])
        self.assertEqual(self.old,self.recovery.record['original_mail_actions'])
        self.s.click_template.side_effect = original
        self.recovery = inv.FragmentRecovery(self.s,self.account)
        self.space()
        self.assertEqual(1,len(self.conversion_calls()))

    def test_verified_capacity_change_allows_only_one_new_claim_then_settles_originals(self):
        self.space()
        label = 'mail_capacity_claim_original-ticket'
        def send(action):
            self.state.set_action(self.account.name,action,'pending','written before click')
            return True
        click = Mock(side_effect=send)
        with patch.object(inv.time,'monotonic',side_effect=lambda:self.now):
            self.recovery.claim_remaining(Mock(side_effect=[False,True]),Mock(return_value=True),click)
        click.assert_called_once_with(label)
        self.assertTrue(all(self.state.action_status(self.account.name,key) == 'done' for key in self.old))
        self.assertEqual('done',self.state.action_status(self.account.name,label))
        budget = self.state.accounts[self.account.name]['network_retries']['mail_claim_all']
        self.assertEqual(dict(attempts=3,verified=True),budget)
        self.recovery.claim_remaining(Mock(return_value=False),Mock(return_value=True),click)
        click.assert_called_once()

    def test_pending_claim_after_restart_observes_only_and_keeps_budget(self):
        self.space()
        label = 'mail_capacity_claim_original-ticket'
        self.state.set_action(self.account.name,label,'pending','written before click')
        self.recovery.save('mail_claim_requested')
        click = Mock()
        with patch.object(inv.time,'monotonic',side_effect=lambda:self.now),self.assertRaisesRegex(hs.PendingError,'结果仍未知'):
            self.recovery.claim_remaining(Mock(return_value=False),Mock(return_value=True),click)
        click.assert_not_called()
        self.assertEqual('pending',self.state.action_status(self.account.name,label))
        self.assertEqual(3,self.state.accounts[self.account.name]['network_retries']['mail_claim_all']['attempts'])

    def test_changed_or_conflicting_original_prevents_fresh_claim(self):
        self.space()
        key = next(iter(self.old))
        for alteration in ({'updated_at':'different-request'}, {'checkpoint_conflict':dict(status='pending',updated_at='another-original')}):
            with self.subTest(alteration=alteration):
                self.state.accounts[self.account.name]['actions'][key] = {**self.old[key],**alteration}
                click = Mock()
                with self.assertRaises(hs.PendingError):
                    self.recovery.claim_remaining(Mock(return_value=False),Mock(return_value=True),click)
                click.assert_not_called()

    def test_partial_settlement_resumes_only_actions_proven_by_this_ticket(self):
        key = next(iter(self.old))
        self.recovery.save('mail_claim_requested',mail_evidence='same-ticket-result.png')
        self.state.set_action(self.account.name,key,'done','恢复票据：original-ticket')
        self.recovery.validate_originals()
        self.assertIn(key,self.recovery.record['resolved_mail_actions'])
        other = list(self.old)[1]
        self.state.set_action(self.account.name,other,'done','result of another recovery')
        with self.assertRaises(hs.PendingError):
            self.recovery.validate_originals()

    def test_cross_day_preserves_ticket_and_accepts_only_the_same_original_tokens(self):
        day = (date.today()-timedelta(days=1)).isoformat()
        restored_root = self.root/'crossday_state'
        source = restored_root/day/self.state.account_paths[self.account.name].name
        source.parent.mkdir(parents=True)
        ticket = deepcopy(self.recovery.record)
        ticket['original_account_path'] = str(source)
        hs.atomic_json(source,dict(date=day,account=self.account.name,actions=deepcopy(self.old),inventory_recovery=ticket))
        before = source.read_bytes()
        self.s.state = hs.StateStore([self.account.name],restored_root)
        restored = inv.FragmentRecovery(self.s,self.account)
        restored.validate_originals()
        self.assertEqual('original-ticket',restored.record['ticket'])
        self.assertEqual(before,source.read_bytes())
        self.s.click_template.assert_not_called()

    def test_anonymous_wrong_owner_or_generic_error_cannot_authorize_recovery(self):
        self.s.state.accounts['当前窗口标签1'] = {}
        anonymous = hs.Account(1,'当前窗口标签1',180)
        with self.assertRaisesRegex(hs.PendingError,'实名'):
            inv.FragmentRecovery(self.s,anonymous).request('screenshot',reason='user_confirmed_fragment_capacity')
        self.state.accounts[self.account.name][inv.RECORD]['account'] = 'other-role'
        with self.assertRaisesRegex(hs.PendingError,'身份'):
            inv.FragmentRecovery(self.s,self.account)
        self.s.click_template.assert_not_called()
        self.assertFalse(inv.capacity_notice_visible(self.s,self.before))


class FragmentLiveReplayTests(unittest.TestCase):
    def setUp(self):
        self.folder = hs.LOGS/'fragment_refinery'
        self.store = hs.TemplateStore(.98)
        self.s = SimpleNamespace(template=lambda name,roi,frame,threshold=None:self.store.match(frame,hs.ASSETS/name,roi,threshold))

    def frame(self,name):
        with Image.open(self.folder/(name+'_20261004.png')) as frame:
            return frame.convert('RGB')

    def test_real_cultivation_selected_and_inactive_are_distinct(self):
        roi = (1390,95,1475,175)
        self.assertTrue(inv.visible(self.s,'ninja_cultivation_selected_current.bmp',roi,self.frame('cultivation')))
        self.assertFalse(inv.visible(self.s,'ninja_cultivation_selected_current.bmp',roi,self.frame('ninja')))
        self.assertTrue(inv.visible(self.s,'ninja_cultivation_current.bmp',roi,self.frame('ninja')))

    def test_real_scope_requires_all_phrases_and_both_buttons(self):
        frame = self.frame('exchange_prompt')
        self.assertTrue(inv.exchange_prompt_visible(self.s,frame))
        frame.paste('black',(805,518,1118,534))
        self.assertFalse(inv.exchange_prompt_visible(self.s,frame))
        self.assertFalse(inv.exchange_prompt_visible(self.s,self.frame('refinery')))

    def test_real_before_after_empty_grid_points_and_disabled_control_agree(self):
        before,after = self.frame('refinery'),self.frame('converted')
        self.assertTrue(inv.refinery_visible(self.s,before))
        self.assertTrue(inv.refinery_visible(self.s,after))
        self.assertFalse(self.store.match(before,hs.ASSETS/inv.EMPTY,inv.EMPTY_ROI,.995)[0])
        self.assertTrue(self.store.match(after,hs.ASSETS/inv.EMPTY,inv.EMPTY_ROI,.995)[0])
        self.assertNotEqual(inv.points_fingerprint(before),inv.points_fingerprint(after))
        scheduler = object.__new__(hs.Scheduler)
        scheduler.templates = self.store
        scheduler.template = self.s.template
        scheduler.last_detection = None
        roi = (1210,735,1370,820)
        self.assertTrue(scheduler.active_template('fragment_exchange_current.bmp',roi,before,threshold=.98)[0])
        self.assertFalse(scheduler.active_template('fragment_exchange_current.bmp',roi,after,threshold=.98)[0])


if __name__ == '__main__':
    unittest.main()
