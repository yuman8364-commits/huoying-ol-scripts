"""Regression cases for the observed October 8 solo failures and guarded timings."""
import unittest
import threading
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch
from PIL import Image

import hyol_scheduler as hs
from hyol_daily_tasks import DailyScheduler
from hyol_multi_solo import MultiSoloRunner
from hyol_performance import PerformanceMeter


class SoloImprovementsTests(unittest.TestCase):
    def make_scheduler(self):
        s = object.__new__(hs.Scheduler)
        s.templates, s.detector = hs.TemplateStore(.92), None
        return s

    def test_archived_token_counts_are_unique_with_strict_threshold(self):
        s = self.make_scheduler()
        for count in range(4):
            with self.subTest(count=count), Image.open(
                    hs.ROOT/f'tests/fixtures/arena_tokens_{count}_20261008.png') as frame:
                self.assertEqual(count, s._arena_token_count(frame))
        self.assertIsNone(s._arena_token_count(Image.new('RGB', (1920,1032))))

    def test_zero_tokens_overrides_visible_ranked_button(self):
        s = self.make_scheduler()
        s.capture = Mock(return_value=Image.new('RGB',(1920,1032)))
        s.template = Mock(return_value=(True,1.,0,0))
        s._arena_token_count = Mock(return_value=0)
        s.check_stop, s.wait_interruptible = Mock(), Mock()
        s.save_raw_evidence, s.event = Mock(), Mock()
        s.click_template = Mock(return_value=True)
        with patch.object(hs.time,'monotonic',side_effect=[0,1,2,3]):
            self.assertEqual('casual',s._click_arena_mode_when_stable(hs.Account(1,'a',180),'battle'))
        self.assertEqual('arena_casual_button_current.bmp',s.click_template.call_args.args[1])
        predicate = s.click_template.call_args.kwargs['frame_predicate']
        s._arena_token_count.return_value = 1
        self.assertFalse(predicate(s.capture()))

    def test_unknown_token_count_never_starts_any_mode(self):
        s = self.make_scheduler()
        s.capture, s.template = Mock(), Mock(return_value=(True,1.,0,0))
        s._arena_token_count = Mock(return_value=None)
        s.check_stop, s.wait_interruptible, s.event = Mock(), Mock(), Mock()
        s.save_raw_evidence, s.click_template = Mock(), Mock()
        with patch.object(hs.time,'monotonic',side_effect=[0,0,31]):
            self.assertIsNone(s._click_arena_mode_when_stable(hs.Account(1,'a',180),'battle'))
        s.click_template.assert_not_called()

    def test_final_guard_rejects_changed_tokens_before_pending_write(self):
        s = self.make_scheduler()
        s.no_content_clicks = False
        s.check_input_pause = Mock()
        s.content_guard = Mock(return_value=Image.new('RGB',(1920,1032)))
        s.state = SimpleNamespace(set_action=Mock(),action_status=Mock(return_value='new'))
        s.save_evidence = Mock()
        with patch.object(hs.Win32,'click') as click:
            with self.assertRaises(hs.TargetPreconditionChanged):
                s.click_point(hs.Account(1,'a',180),700,800,'battle',[],True,
                              frame_predicate=lambda _frame:False)
        click.assert_not_called()
        s.state.set_action.assert_not_called()
        s.save_evidence.assert_not_called()

    def test_claims_after_first_two_get_new_persistent_action(self):
        s = self.make_scheduler()
        account = hs.Account(1,'a',180)
        actions = {f'arena_daily_reward_claim_{i}':{'status':'done'} for i in (1,2)}
        s.state = SimpleNamespace(accounts={'a':{'actions':actions}},
            action_status=lambda name,key:actions.get(key,{}).get('status','new'),
            set_action=lambda name,key,status,detail:actions.update({key:{'status':status}}))
        s.capture = Mock()
        controls = iter([True,False,False,False,False])
        def match(name,*_args,**_kwargs):
            return (next(controls) if name=='arena_reward_claim.bmp' else True,1.,400,535)
        s.template = Mock(side_effect=match)
        def commit(_account,key,_detail):
            actions[key]={'status':'done'}
        s.commit_action = Mock(side_effect=commit)
        s.click_template = Mock(return_value=True)
        s.event, s.wait_interruptible = Mock(),Mock()
        s._claim_arena_tasks(account)
        self.assertEqual('arena_daily_reward_claim_3',s.click_template.call_args.args[5])
        self.assertEqual('done',actions['arena_daily_reward_claim_3']['status'])
        s.commit_action.assert_called_once()
        self.assertEqual({'status':'done'},actions['arena_daily_reward_claim_1'])
        self.assertEqual({'status':'done'},actions['arena_daily_reward_claim_2'])

    def test_pending_extra_claim_blocks_repeated_claim(self):
        s = self.make_scheduler()
        s.state = SimpleNamespace(accounts={'a':{'actions':{
            'arena_daily_reward_claim_3':{'status':'pending'}}}})
        s.capture,s.click_template = Mock(),Mock()
        with self.assertRaises(hs.PendingError):
            s._claim_arena_tasks(hs.Account(1,'a',180))
        s.capture.assert_not_called()
        s.click_template.assert_not_called()

    def test_content_ready_returns_before_fixed_twelve_seconds(self):
        s = self.make_scheduler()
        s.capture,s.check_stop,s.event,s.wait_interruptible=Mock(),Mock(),Mock(),Mock()
        s._combat_ready_visible=Mock(return_value=True)
        s._arena_token_count=Mock(return_value=0)
        with patch.object(hs.time,'monotonic',side_effect=[0,.1,.3,.3]):
            s._wait_arena_ready_content(hs.Account(1,'a',180))
        self.assertEqual(2,s.capture.call_count)
        self.assertEqual(.3,s.event.call_args.kwargs['seconds'])

    def test_deferred_probe_waits_without_switching(self):
        r=object.__new__(MultiSoloRunner)
        r.scheduler=Mock()
        r.event=Mock()
        r.deferred={'a':'arena'}
        r.deferred_at={'a':100}
        r.excluded=set()
        with patch('hyol_multi_solo.time.monotonic',return_value=110):
            r.probe_deferred(hs.Account(1,'a',180))
        r.scheduler.switch_account_for_combat.assert_not_called()

    def test_done_first_run_with_followup_is_not_skipped(self):
        r=object.__new__(MultiSoloRunner)
        r.scheduler=Mock(accounts=[])
        r.event=Mock()
        r._survival_followup=Mock()
        runner=Mock(account=hs.Account(1,'a',180))
        runner.record.return_value={'status':'done','task_completed':True,
                                    'home_returned':True,'followup_check_due':True}
        r._run_task(runner,'survival')
        r._survival_followup.assert_called_once_with(runner)
        runner.survival.assert_not_called()

    def test_exclusive_timings_do_not_double_count_nested_capture(self):
        class S:
            def capture(self):return 'frame'
            def template(self):return self.capture()
        s=S()
        for name in ('switch_account','switch_account_for_combat','wait_interruptible',
                     'click_template','click_point','save_raw_evidence'):
            setattr(s,name,lambda:None)
        s.templates=SimpleNamespace(match=lambda:None)
        meter=PerformanceMeter(s)
        with patch('hyol_performance.time.perf_counter',side_effect=[0,1,3,5]):
            self.assertEqual('frame',s.template())
        rows=meter.snapshot()
        meter.close()
        self.assertEqual(5,rows['template']['seconds'])
        self.assertEqual(3,rows['template']['exclusive_seconds'])
        self.assertEqual(2,rows['capture']['exclusive_seconds'])
        self.assertNotIn('capture',s.__dict__)

    def test_overlapping_threads_keep_independent_exclusive_timings(self):
        capture_started, template_started, capture_finished = (
            threading.Event(), threading.Event(), threading.Event())
        class S:
            def capture(self):
                capture_started.set()
                if not template_started.wait(2):
                    raise RuntimeError('template did not start')
            def template(self):
                template_started.set()
                if not capture_finished.wait(2):
                    raise RuntimeError('capture did not finish')
        s=S()
        for name in ('switch_account','switch_account_for_combat','wait_interruptible',
                     'click_template','click_point','save_raw_evidence'):
            setattr(s,name,lambda:None)
        s.templates=SimpleNamespace(match=lambda:None)
        meter=PerformanceMeter(s)
        clocks={'meter_capture':iter([0,2]),'meter_template':iter([1,4])}
        errors=[]
        def capture_worker():
            try:s.capture()
            except BaseException as exc:errors.append(exc)
            finally:capture_finished.set()
        def template_worker():
            try:
                if not capture_started.wait(2):
                    raise RuntimeError('capture did not start')
                s.template()
            except BaseException as exc:errors.append(exc)
        workers=[threading.Thread(target=capture_worker,name='meter_capture'),
                 threading.Thread(target=template_worker,name='meter_template')]
        try:
            with patch('hyol_performance.time.perf_counter',
                       side_effect=lambda:next(clocks[threading.current_thread().name])):
                for worker in workers:worker.start()
                for worker in workers:worker.join(5)
            self.assertFalse(any(worker.is_alive() for worker in workers))
            self.assertFalse(errors)
            rows=meter.snapshot()
            self.assertEqual(2,rows['capture']['exclusive_seconds'])
            self.assertEqual(3,rows['template']['exclusive_seconds'])
        finally:
            meter.close()

    def test_inline_first_reward_is_persisted_before_reset_continuation(self):
        s=object.__new__(DailyScheduler)
        account=hs.Account(1,'a',180)
        slot=hs.CombatSlot(account,'survival')
        s.survival_inline_two_runs=True
        s.template=Mock(return_value=(True,1.,0,0))
        s.wait_template=Mock(return_value=True)
        s._survival_full_score_visible=Mock(return_value=True)
        s.claim_survival_rewards,s.reconcile_survival_result_confirmations=Mock(),Mock()
        s.state=SimpleNamespace(action_status=lambda *args:'new')
        rec={}
        s.navigator=Mock()
        s.navigator.record.return_value=rec
        def fail_reset(*args):
            self.assertTrue(rec['task_completed'])
            self.assertTrue(rec['followup_check_due'])
            self.assertFalse(rec['home_returned'])
            s.navigator.save.assert_called_once()
            raise hs.PendingError('reset result unknown')
        with patch.object(hs.Scheduler,'_finish_survival_slot',side_effect=fail_reset):
            with self.assertRaises(hs.PendingError):
                s._finish_survival_slot(slot)
        self.assertFalse(slot.complete)

    def test_inline_second_run_completes_without_extra_followup(self):
        s=object.__new__(DailyScheduler)
        account=hs.Account(1,'a',180)
        slot=hs.CombatSlot(account,'survival')
        s.survival_inline_two_runs=True
        s.template=Mock(return_value=(True,1.,0,0))
        s.wait_template=Mock(return_value=True)
        s._survival_full_score_visible=Mock(return_value=True)
        s.claim_survival_rewards,s.reconcile_survival_result_confirmations=Mock(),Mock()
        s.state=SimpleNamespace(action_status=lambda *args:'done')
        rec={'followup_check_due':True,'completed':1}
        s.navigator=Mock()
        s.navigator.record.return_value=rec
        with patch.object(hs.Scheduler,'_finish_survival_slot',side_effect=lambda scheduler,slot:setattr(slot,'complete',True)):
            s._finish_survival_slot(slot)
        self.assertTrue(s.navigator.survival_inline_complete)
        self.assertFalse(rec['followup_check_due'])
        self.assertEqual(2,rec['completed'])

    def test_arena_poll_reuses_one_frame_until_an_action(self):
        s=self.make_scheduler()
        frame=Image.new('RGB',(1920,1032))
        s.capture=Mock(return_value=frame)
        s.template=Mock(return_value=(False,0.,0,0))
        s._combat_progress_watchdog,s.wait_interruptible=Mock(),Mock()
        s._arena_waiting_battle_visible=Mock(return_value=False)
        s._arena_victory_visible,s._arena_defeat_visible=Mock(return_value=False),Mock(return_value=False)
        s._battle_auto_on_visible=Mock(return_value=True)
        s._poll_arena_slot(hs.CombatSlot(hs.Account(1,'a',180),'arena',running=True))
        s.capture.assert_called_once()
        for call in s.template.call_args_list:
            self.assertIs(frame,call.args[2])
        s._battle_auto_on_visible.assert_called_once_with(frame)

    def test_survival_poll_accepts_prechecked_frame_without_recapturing(self):
        s=self.make_scheduler()
        frame=Image.new('RGB',(1920,1032))
        s.capture,s.wait_interruptible=Mock(),Mock()
        s.template=Mock(return_value=(False,0.,0,0))
        s._poll_survival_slot(hs.CombatSlot(hs.Account(1,'a',180),'survival'),frame)
        s.capture.assert_not_called()
        self.assertTrue(all(call.args[2] is frame for call in s.template.call_args_list))

    def test_completed_arena_run_rechecks_rewards_without_another_battle(self):
        r=object.__new__(MultiSoloRunner)
        account=hs.Account(1,'a',180)
        r.scheduler=Mock(accounts=[account])
        r.scheduler.state.global_state={}
        r.scheduler.main_ready.return_value=True
        r.scheduler.template.return_value=(True,1.,0,0)
        r.event=Mock()
        r.arena_wins=5
        runner=Mock(account=account)
        rec={'status':'done','task_completed':True,'home_returned':True}
        runner.record.return_value=rec
        r._run_task(runner,'arena')
        r.scheduler.recheck_arena_rewards.assert_called_once_with(account)
        runner.arena.assert_not_called()
        self.assertIn('a',r.arena_rewards_audited)
        r._run_task(runner,'arena')
        r.scheduler.recheck_arena_rewards.assert_called_once()

    def test_survival_recognition_pending_without_battle_action_is_resumed(self):
        r=object.__new__(MultiSoloRunner)
        account=hs.Account(1,'a',180)
        r.scheduler=SimpleNamespace(state=SimpleNamespace(accounts={'a':{'actions':{}}}))
        r.runners={'a':SimpleNamespace(records={'survival':{
            'status':'pending','followup_check_due':True,'last_error':'SS counter obscured'}})}
        self.assertEqual('survival',r.unresolved_combat_task(account))

    def test_unselected_pending_task_still_blocks_startup(self):
        r=object.__new__(MultiSoloRunner)
        account=hs.Account(1,'a',180)
        r.scheduler=SimpleNamespace(state=SimpleNamespace(accounts={'a':{'actions':{}}}))
        r.runners={'a':SimpleNamespace(records={'mail':{'status':'pending'}})}
        self.assertEqual('mail',r.unresolved_combat_task(account))

if __name__=='__main__':
    unittest.main()
