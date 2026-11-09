"""Finite continuous series, scoped boundary checks and terminal visual proof."""
from copy import deepcopy
from types import SimpleNamespace
from unittest.mock import Mock, patch
import unittest

from PIL import Image

import hyol_training_record as training
import hyol_training_coordinator as coordinator
import hyol_training_policy as policy
import hyol_training_resume as resume


def goal(task='arena', current=0, target=15):
    return dict(key=task,task=task,description='竞技场获胜15次' if task=='arena' else '完成15次排位战',
                current=current,target=target,progress_known=True,completed=False,
                claimable=False,claimed=False,series_state='in_progress',y=44,text=task,
                proof_box=(10,10,60,25),status_roi=(80,10,130,45),evidence='original.png')


def runner(rows=None):
    records={'training_record':{}}
    frame=Image.new('RGB',(1920,1032),(195,195,195))
    scheduler=SimpleNamespace(state=SimpleNamespace(day='2026-11-09',accounts={}),
        capture=Mock(return_value=frame),content_guard=Mock(return_value=frame),
        verify_identity=Mock(return_value=(True,{})),wait_interruptible=Mock(),
        wait_until_main=Mock(return_value=True),save_raw_evidence=Mock(return_value='fresh.png'),
        template=Mock(return_value=(False,0,0,0)),check_stop=Mock())
    r=SimpleNamespace(account=SimpleNamespace(name='account-a',tab_x=180),scheduler=scheduler,
        records=records,record=lambda task:records.setdefault(task,{}),save=Mock(),event=Mock(),click=Mock())
    if rows is not None:
        records['training_record']['latest']=snapshot(rows)
    return r,frame


def snapshot(rows, **fields):
    return dict(rows=deepcopy(rows),pages=[],scan_complete=True,rewards_complete=True,
                scan_version=2,claims_sent=0,scope='full',at='2026-11-09T01:00:00+08:00',**fields)


class StopPolicyTests(unittest.TestCase):
    def test_explicit_choice_overrides_weekday_and_auto_retains_date_default(self):
        self.assertEqual(policy.arena_policy('2026-11-09')['mode'],'tickets')
        self.assertEqual(policy.arena_policy('2026-11-08')['mode'],'wins')
        self.assertEqual(policy.arena_policy('2026-11-09','final_goal')['mode'],'wins')
        self.assertEqual(policy.arena_policy('2026-11-08','tickets')['mode'],'tickets')
        with self.assertRaises(ValueError):policy.arena_policy('2026-11-09','guess')

    def test_quantity_locks_highest_observed_deficit_without_guessing_final_tier(self):
        rows=[goal(current=2,target=5),goal(current=2,target=15)]
        plan=policy.quantity_plan('arena',rows,'2026-11-09','final_goal')
        self.assertEqual(plan['required'],13)
        self.assertEqual(plan['stop_policy'],'final_goal')
        self.assertIsNone(training.series_summary(rows)['arena:wins']['final_target'])

    def test_resume_key_preserves_auto_compatibility_and_explicit_budget_choice(self):
        request=SimpleNamespace(mode='solo',account_count=1,formation='keep')
        original=resume.request_key(request)
        request.training_arena_stop_policy='auto'
        self.assertEqual(resume.request_key(request),original)
        request.training_arena_stop_policy='final_goal'
        self.assertNotEqual(resume.request_key(request),original)


class ScanReuseTests(unittest.TestCase):
    def test_verified_claim_reuses_open_panel_but_final_observation_closes_once(self):
        r,frame=runner();r.scheduler.check_input_pause=Mock();row=goal()
        claimed=[False]
        def claim(*args):
            if claimed[0]:return False
            claimed[0]=True
            r.record('training_record')['claims']={'arena':dict(status='done',description=row['description'],attempts=[])}
            return True
        with patch.object(training,'open_record',return_value=((700,320,1340,710),(1350,215,1420,290))), \
             patch.object(training,'layout',return_value=True),patch.object(training,'scroll'), \
             patch.object(training,'observe_page',side_effect=lambda *a:({},[deepcopy(row)])), \
             patch.object(training,'claim_visible',side_effect=claim):
            first=training.scan(r,'collect',collect_rewards=True,keep_open_after_claims=True)
            self.assertTrue(first['panel_open']);r.click.assert_not_called()
            second=training.scan(r,'fresh')
        self.assertFalse(second['panel_open'])
        self.assertEqual(['training_close_record'],[c.args[4] for c in r.click.call_args_list])
        self.assertIsNot(first['pages'],second['pages'])

    def test_unknown_claim_keeps_verified_exit_and_original_two_attempt_limit(self):
        from test_hyol_training_record import TrainingRecordTests
        r,row,frame,record,actions=TrainingRecordTests().reward_runner();row['text']='完成4次忍者测验4/4领取'
        r.scheduler.content_guard=Mock(return_value=frame);r.scheduler.check_input_pause=Mock()
        r.scheduler.wait_until_main=Mock(return_value=True)
        with patch.object(training,'open_record',return_value=((700,320,1340,710),(1350,215,1420,290))), \
             patch.object(training,'layout',return_value=True),patch.object(training,'scroll'), \
             patch.object(training,'observe_page',side_effect=lambda *a:({},[dict(row)])):
            result=training.scan(r,'pending',collect_rewards=True,keep_open_after_claims=True)
        self.assertFalse(result['panel_open'])
        self.assertEqual(['training_record_claim.bmp','training_record_close.bmp']*2,[c.args[0] for c in r.click.call_args_list])
        self.assertEqual('pending',record['claims']['goal']['status'])
        self.assertEqual(2,len(record['claims']['goal']['attempts']))

    def test_zero_claim_complete_scan_does_not_run_another_full_scan(self):
        r,_=runner();view=snapshot([goal()])
        with patch.object(training,'scan',return_value=view) as scan:
            result=training.refresh_goals(r,'before')
        self.assertIs(result,view)
        self.assertEqual(scan.call_count,1)
        self.assertTrue(r.records['training_record']['reward_collection']['reused_complete_scan'])

    def test_actual_claim_always_requires_new_observation(self):
        r,_=runner();collected=snapshot([goal()]);collected['claims_sent']=1
        fresh=snapshot([goal(current=3)])
        with patch.object(training,'scan',side_effect=[collected,fresh]) as scan:
            self.assertIs(training.refresh_goals(r,'after_arena'),fresh)
        self.assertEqual(scan.call_count,2)

    def test_unknown_receipt_is_not_erased_by_zero_claim_reuse(self):
        r,_=runner();receipt=dict(description='竞技场获胜15次',status='pending',attempts=[])
        r.record('training_record')['claims']={'arena':deepcopy(receipt)}
        view=snapshot([goal()])
        with patch.object(training,'scan',return_value=view) as scan:
            with self.assertRaises(training.TrainingPending):training.refresh_goals(r,'before')
        self.assertEqual(scan.call_count,2)
        self.assertEqual(r.record('training_record')['claims']['arena'],receipt)

    def test_legacy_snapshot_without_mutation_metadata_keeps_safe_rescan(self):
        r,_=runner();view=snapshot([goal()]);view.pop('scan_version')
        with patch.object(training,'scan',return_value=view) as scan:training.refresh_goals(r,'before')
        self.assertEqual(scan.call_count,2)


class TerminalTests(unittest.TestCase):
    def test_claimed_tier_receipt_is_not_terminal_completion(self):
        r,_=runner();row=goal();row.update(series_state='unknown',completed=True)
        r.record('training_record')['claims']={'arena':dict(description=row['description'],status='done',
            method='immediate_week_xp_delta',evidence='claim.png',attempts=[])}
        training.reconcile_claimed_rows(r,[row])
        self.assertTrue(row['tier_claimed'])
        self.assertFalse(training.goal_complete(row))
        self.assertEqual(training.series_summary([row])['arena:wins']['state'],'unknown')

    def test_gray_paper_is_independent_of_an_enabled_or_disabled_button(self):
        self.assertTrue(training.terminal_gray(Image.new('RGB',(80,12),(190,190,190)),(0,0,80,12)))
        self.assertFalse(training.terminal_gray(Image.new('RGB',(80,12),(225,200,140)),(0,0,80,12)))
        self.assertFalse(training.terminal_gray(Image.new('RGB',(80,12),(10,10,10)),(0,0,80,12)))

    def test_terminal_requires_gray_and_stamp_in_independent_matching_frames(self):
        r,frame=runner();row=goal();row.update(claimed=True,terminal_gray_candidate=True,
            terminal_gray_roi=(20,30,100,42),series_state='unknown')
        r.scheduler.template.return_value=(True,1,80,10)
        with patch.object(training,'layout',return_value=((0,0,200,100),(200,0,230,30))):
            training.verify_terminal_rows(r,[row],frame,'first.png')
        self.assertTrue(training.row_terminal(row))
        self.assertEqual(row['terminal_samples'],['first.png','fresh.png'])
        self.assertEqual(training.series_summary([row])['arena:wins']['state'],'terminal_claimed')

    def test_changed_task_text_during_gray_check_cannot_complete_series(self):
        r,frame=runner();row=goal();row.update(claimed=True,terminal_gray_candidate=True,
            terminal_gray_roi=(20,30,100,42),series_state='unknown')
        fresh=frame.copy();fresh.putpixel((15,15),(0,0,0))
        r.scheduler.content_guard.return_value=fresh;r.scheduler.template.return_value=(True,1,80,10)
        with patch.object(training,'layout',return_value=((0,0,200,100),(200,0,230,30))):
            training.verify_terminal_rows(r,[row],frame,'first.png')
        self.assertFalse(training.row_terminal(row))


class ScopedPlanningTests(unittest.TestCase):
    def test_boundary_scan_updates_only_selected_series_without_full_scan(self):
        r,_=runner();old=snapshot([goal(current=0),goal('ranking',10,15)])
        changed=goal(current=5,target=15)
        with patch.object(training,'open_record',return_value=((0,0,200,100),(200,0,230,30))), \
             patch.object(training,'layout',return_value=((0,0,200,100),(200,0,230,30))), \
             patch.object(training,'_seek_record_top',return_value=iter(())), \
             patch.object(training,'prior_goal_proofs',return_value=[]), \
             patch.object(training,'observe_page',return_value=({},[changed])), \
             patch.object(training,'row_visual_mask',return_value=None), \
             patch.object(training,'scan_steps') as full_scan:
            result=training.drain_steps(training.scan_task_steps(r,'arena','after_arena',old))
        full_scan.assert_not_called()
        self.assertEqual(result['scope'],'task')
        self.assertEqual(next(row for row in result['rows'] if row['task']=='ranking')['current'],10)
        self.assertEqual(next(row for row in result['rows'] if row['task']=='arena')['current'],5)
        self.assertEqual(len(result['task_scans']),1)

    def test_coordinator_uses_scoped_check_only_after_complete_baseline(self):
        r,_=runner([goal()]);plan=coordinator.AccountPlan.__new__(coordinator.AccountPlan)
        plan.r=r;plan.rec=r.record('training_record')
        with patch.object(training,'scan_task_steps',return_value=iter(())) as focused, \
             patch.object(training,'refresh_goal_steps') as full:
            plan._scan_task('arena')
        focused.assert_called_once();full.assert_not_called()

    def test_scoped_snapshot_cannot_directly_complete_all_goals(self):
        r,_=runner();plan=coordinator.AccountPlan.__new__(coordinator.AccountPlan)
        plan.r=r;plan.rec=r.record('training_record');plan.rec.update(latest={'scope':'task'},remaining=[])
        plan.errors={};plan._scan=Mock()
        plan._finish()
        plan._scan.assert_called_once_with('final')
        self.assertNotIn('all_goals_complete',plan.rec)

    def test_exhausted_quota_retains_nonterminal_goal_without_new_driver(self):
        r,_=runner();plan=coordinator.AccountPlan.__new__(coordinator.AccountPlan)
        plan.r=r;plan.s=r.scheduler;plan.rec=r.record('training_record');plan.rec['jobs']=[]
        plan.day='2026-11-09';plan.errors={};plan.exhausted={'ranking':'次数用完，待下次继续'}
        plan.driver_factory=Mock();plan._finish=Mock()
        with patch('hyol_training_coordinator.local_day',return_value=plan.day), \
             patch('hyol_task_order.load',return_value={'order':{'solo':['ranking']}}), \
             patch('hyol_training_resume.pending_prior_sources',return_value=[]):
            plan._plan(snapshot([goal('ranking',10,15)]))
        plan.driver_factory.assert_not_called()
        self.assertEqual(plan.rec['daily_plan']['tasks'][0]['status'],'deferred')
        self.assertEqual(len(plan.rec['remaining']),1)

    def test_original_active_budget_survives_new_selected_policy(self):
        r,_=runner();r.training_arena_stop_policy='tickets'
        old=dict(id='original',task='arena',status='waiting',target_mode='wins',required=13)
        r.record('training_record')['active_job']=old
        factory=Mock()
        with patch.object(training,'unresolved_supported_tasks',return_value=('arena',)):
            plan=coordinator.AccountPlan(r,driver_factory=factory)
        self.assertIs(factory.call_args.args[1],old)
        self.assertEqual(old['required'],13)
        self.assertEqual(old['target_mode'],'wins')

    def test_treasure_reward_popup_does_not_define_one_map(self):
        r,_=runner();request=dict(task='treasure',id='bounded',required=3)
        with patch('hyol_treasure.TreasureRun'):
            coordinator.TreasureDriver(r,request)
        self.assertFalse(r.record('treasure')['training_single_cycle'])
        self.assertNotIn('settled',request)
        self.assertEqual(request['required'],3)


if __name__=='__main__':unittest.main()
