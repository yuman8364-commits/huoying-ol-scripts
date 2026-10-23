"""Live-derived finishing guards: strict entries, card intent, and notice queues."""
from pathlib import Path
from types import SimpleNamespace
import json
import tempfile
import unittest
from unittest.mock import Mock, patch

from PIL import Image
import hyol_checkpoints as checkpoints
import hyol_daily_tasks as daily
import hyol_managed as managed
import hyol_scheduler as hs
import hyol_treasure as treasure

HERE=hs.ROOT/'logs/live_continuation_20261023'


class LiveFinishTests(unittest.TestCase):
    def frame(self, name):
        return Image.open(HERE/'observation_resume9'/name).convert('RGB')

    def card_frame(self):
        return self.frame('tab_4_unknown.png')

    def scheduler(self, frame):
        s=object.__new__(hs.Scheduler)
        s.templates=hs.TemplateStore(.98)
        s.detector=None
        s.capture=Mock(return_value=frame)
        s.event=Mock()
        s.click_template=Mock(return_value=True)
        s.wait_interruptible=Mock()
        return s

    def intent(self, s):
        directory=tempfile.TemporaryDirectory(dir=hs.ROOT/'logs')
        self.addCleanup(directory.cleanup)
        proof=Path(directory.name)/'before.png'
        self.frame('tab_1.png').save(proof)
        account=hs.Account(4,'80区秦秦',585)
        actions={'start_managed_network_1':dict(status='pending',updated_at='2026-10-23T14:40:00+08:00',before_evidence=str(proof))}
        s.state=SimpleNamespace(day='2026-10-23',accounts={account.name:dict(actions=actions,
            network_retries={'start_managed':dict(attempts=1,source_day='2026-10-23')})},
            action_status=lambda name,key: actions.get(key,{}).get('status','new'))
        s.commit_action=Mock()
        return account,actions

    def test_real_card_prompt_matches_both_accounts_and_not_the_village(self):
        s=self.scheduler(self.card_frame())
        self.assertTrue(managed.first_card_prompt_visible(s,self.card_frame()))
        other=Image.open(HERE/'runtime/scheduler_evidence/2026-10-23/account_3ed81e0fcf/143923_009696_pending_start_managed_network_failed.png').convert('RGB')
        self.assertTrue(managed.first_card_prompt_visible(s,other))
        self.assertFalse(managed.first_card_prompt_visible(s,self.frame('tab_1.png')))

    def test_missing_prompt_text_never_authorizes_generic_confirmation(self):
        frame=self.card_frame()
        frame.paste((20,25,30),(826,508,1094,560))
        s=self.scheduler(frame)
        account,_actions=self.intent(s)
        self.assertFalse(managed.confirm_first_card(s,account,frame))
        s.click_template.assert_not_called()

    def test_one_card_confirmation_uses_original_start_and_fresh_full_prompt(self):
        frame=self.card_frame()
        s=self.scheduler(frame)
        account,actions=self.intent(s)
        def click(_account,name,roi,*args,**kw):
            self.assertEqual(name,managed.CARD_CONFIRM)
            self.assertTrue(kw['irreversible'])
            self.assertTrue(kw['frame_predicate'](frame))
            changed=frame.copy()
            changed.paste((20,25,30),(826,508,1094,560))
            self.assertFalse(kw['frame_predicate'](changed))
            actions[managed.CARD_ACTION]=dict(status='pending',updated_at='2026-10-23T14:40:00+08:00')
            return True
        s.click_template.side_effect=click
        self.assertTrue(managed.confirm_first_card(s,account,frame))
        self.assertFalse(managed.confirm_first_card(s,account,frame))
        self.assertEqual(s.click_template.call_count,1)
        self.assertEqual(actions['start_managed_network_1']['status'],'pending')
        s.commit_action.assert_not_called()

    def test_invalid_start_receipts_send_no_card_input(self):
        for defect in ('missing','origin','conflict','old_day','no_proof','bad_budget'):
            with self.subTest(defect=defect):
                frame=self.card_frame(); s=self.scheduler(frame); account,actions=self.intent(s)
                row=actions['start_managed_network_1']
                if defect=='missing': actions.clear()
                if defect=='origin': row['origin']={'old':'receipt'}
                if defect=='conflict': row['checkpoint_conflict']=[{},{}]
                if defect=='old_day': row['updated_at']='2026-10-22T14:40:00+08:00'
                if defect=='no_proof': row['before_evidence']='does-not-exist.png'
                if defect=='bad_budget': s.state.accounts[account.name]['network_retries']['start_managed']['attempts']=4
                with self.assertRaises(hs.PendingError): managed.confirm_first_card(s,account,frame)
                s.click_template.assert_not_called()

    def test_already_confirmed_card_never_repeats(self):
        frame=self.card_frame(); s=self.scheduler(frame); account,actions=self.intent(s)
        actions[managed.CARD_ACTION]=dict(status='done',updated_at='2026-10-23T14:40:00+08:00')
        self.assertFalse(managed.confirm_first_card(s,account,frame))
        s.click_template.assert_not_called()

    def test_card_receipt_is_reconciled_only_by_explicit_active_gate_caller(self):
        s=self.scheduler(self.card_frame()); account,actions=self.intent(s)
        actions[managed.CARD_ACTION]=dict(status='pending',updated_at='2026-10-23T14:40:00+08:00')
        managed.reconcile_card_confirmation(s,account)
        self.assertEqual(s.commit_action.call_args.args[1],managed.CARD_ACTION)
        actions[managed.CARD_ACTION]['checkpoint_conflict']=[{},{}]
        s.commit_action.reset_mock()
        with self.assertRaises(hs.PendingError): managed.reconcile_card_confirmation(s,account)
        s.commit_action.assert_not_called()

    def test_generic_recovery_does_not_spend_the_managed_card(self):
        frame=self.card_frame(); s=self.scheduler(frame)
        self.assertFalse(hs.Scheduler.recover_known_page(s,hs.Account(4,'80区秦秦',585),frame))
        s.click_template.assert_not_called()

    def test_double_dim_identity_keeps_original_account_and_competitor_thresholds(self):
        frame=self.card_frame(); s=self.scheduler(frame)
        refs=json.loads((hs.ROOT/'state/account_identities.json').read_text('utf-8-sig'))
        refs=refs.get('identities',refs)
        mask=checkpoints.current_identity_mask(frame,s.templates)
        scores=checkpoints.identity_mask_scores(mask,refs)
        best=sorted(scores,key=scores.get,reverse=True)
        self.assertEqual(best[0],'80区秦秦')
        self.assertGreaterEqual(scores[best[0]],.82)
        self.assertGreaterEqual(scores[best[0]]-scores[best[1]],.10)
        altered=frame.copy(); altered.paste((20,25,30),(826,508,1094,560))
        self.assertEqual(checkpoints._identity_layout(altered,s.templates)[1],50)
        self.assertEqual(int(checkpoints.current_identity_mask(altered,s.templates).sum()),0)

    def test_merchant_notice_has_its_own_strict_positive_title(self):
        frame=self.frame('tab_5.png'); s=self.scheduler(frame)
        self.assertEqual(s._activity_notice_anchor(frame),'activity_notice_merchant_title_current.bmp')
        changed=frame.copy(); changed.paste((20,25,30),(1680,700,1919,930))
        self.assertIsNone(s._activity_notice_anchor(changed))

    def managed_gate_scheduler(self, active=False):
        frame=self.card_frame(); s=self.scheduler(frame); account,actions=self.intent(s)
        flags={'active':active,'time':0.,'active_samples':0}
        active_frame=self.frame('tab_2.png')
        s.capture.side_effect=lambda: active_frame if flags['active'] else frame
        def active_score(_frame):
            flags['active_samples']+=int(flags['active'])
            return float(flags['active'])
        s.managed_active_score=active_score
        s.config={'ManagedSafetyVerified':'1'}
        s.switch_account=Mock(); s.check_stop=Mock(); s.ensure_assets=Mock()
        s.reconcile_survival_finished_speed_controls=Mock()
        s.prepare_main=Mock(); s.open_managed_panel_with_retry=Mock()
        s.state.save_account=Mock(); s.state.set_module=Mock()
        def commit(_account,key,detail):
            actions.setdefault(key,{})['status']='done'
        s.commit_action.side_effect=commit
        s.wait_interruptible.side_effect=lambda seconds: flags.__setitem__('time',flags['time']+seconds)
        def click(_account,name,roi,*args,**kw):
            self.assertEqual(name,managed.CARD_CONFIRM,'Never resend the original start')
            self.assertTrue(kw['frame_predicate'](frame))
            actions[managed.CARD_ACTION]=dict(status='pending',updated_at='2026-10-23T14:40:00+08:00')
            flags['active']=True
            return True
        s.click_template.side_effect=click
        return s,account,actions,flags

    def test_primary_gate_resumes_original_prompt_then_requires_ten_active_samples(self):
        s,account,actions,flags=self.managed_gate_scheduler()
        with patch.object(hs.time,'monotonic',side_effect=lambda:flags['time']):
            hs.Scheduler.run_managed_gate(s,account)
        s.prepare_main.assert_not_called(); s.open_managed_panel_with_retry.assert_not_called()
        self.assertEqual(s.click_template.call_count,1)
        self.assertGreaterEqual(flags['active_samples'],10)
        self.assertEqual(s.state.accounts[account.name]['network_retries']['start_managed']['attempts'],1)
        self.assertEqual(actions['start_managed_network_1']['status'],'done')
        self.assertEqual(actions[managed.CARD_ACTION]['status'],'done')
        self.assertEqual(actions['start_managed']['status'],'done')

    def test_primary_gate_reconciles_existing_active_start_with_zero_inputs(self):
        s,account,actions,flags=self.managed_gate_scheduler(active=True)
        with patch.object(hs.time,'monotonic',side_effect=lambda:flags['time']):
            hs.Scheduler.run_managed_gate(s,account)
        self.assertEqual(flags['active_samples'],10)
        s.click_template.assert_not_called(); s.prepare_main.assert_not_called()
        self.assertEqual(actions['start_managed_network_1']['status'],'done')
        self.assertEqual(actions['start_managed']['status'],'done')

    def test_nine_active_samples_do_not_prove_managed_or_send_a_card(self):
        s,account,actions,flags=self.managed_gate_scheduler(active=True)
        s.managed_active_score=Mock(side_effect=[0.]+[1.]*9)
        with self.assertRaisesRegex(hs.PendingError,'ten consecutive'):
            hs.Scheduler.run_managed_gate(s,account)
        s.click_template.assert_not_called(); s.commit_action.assert_not_called()
        self.assertEqual(actions['start_managed_network_1']['status'],'pending')

    def test_actual_merchant_card_is_closed_once_and_disappearance_is_verified(self):
        frame=self.frame('tab_5.png'); s=self.scheduler(frame)
        cleared=frame.copy(); cleared.paste((20,25,30),(1680,700,1919,930))
        s.capture.return_value=cleared
        self.assertEqual(s.clear_transient_notifications(hs.Account(5,'51区鸣路',720),frame),cleared)
        self.assertEqual(s.click_template.call_count,1)
        predicate=s.click_template.call_args.kwargs['frame_predicate']
        self.assertTrue(predicate(frame)); self.assertFalse(predicate(cleared))

    def test_treasure_uses_strict_icon_and_same_fresh_anchor(self):
        frame=self.frame('tab_1.png'); s=self.scheduler(frame)
        r=object.__new__(daily.DailyRunner)
        r.scheduler=s; r.account=hs.Account(1,'1区女室友',180)
        r.wait=Mock(); s.ensure_activity_toolbar=Mock()
        treasure.open_page(r)
        call=s.click_template.call_args
        self.assertEqual(call.args[1],'treasure_toolbar_icon_core_current.bmp')
        self.assertEqual(call.args[6],['anchor_top_menu.bmp','treasure_toolbar_icon_core_current.bmp'])
        self.assertEqual(call.kwargs['threshold'],.98)
        self.assertEqual(call.kwargs['anchor_threshold'],.98)

    def test_treasure_missing_icon_uses_enrolled_text_or_stays_pending(self):
        r=Mock(); r.visible.side_effect=lambda name,*args,**kw: name=='treasure_toolbar_core_current.bmp'
        treasure.open_page(r)
        self.assertEqual(r.click.call_args.args[0],'treasure_toolbar_core_current.bmp')
        self.assertIn('treasure_toolbar_core_current.bmp',r.click.call_args.args[5])


if __name__=='__main__': unittest.main()
