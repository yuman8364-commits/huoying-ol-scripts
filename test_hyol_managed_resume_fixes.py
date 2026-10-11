"""Visual safety and parameter contracts for managed and unchanged formations."""
import inspect
import tempfile
from pathlib import Path
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

from PIL import Image
import hyol_scheduler as hs
import hyol_managed as managed
import hyol_daily_tasks as daily
import hyol_multi_solo as multi
import hyol_multi_solo_gui as gui
import hyol_runtime as runtime
import hyol_team_six as team
import hyol_accounts as accounts
from hyol_client import UnifiedClient


class ManagedOffsetTests(unittest.TestCase):
    def setUp(self):
        self.s = object.__new__(hs.Scheduler)
        self.s.templates, self.s.detector = hs.TemplateStore(.98), None
        self.frame = Image.open(hs.ROOT/'tests/fixtures/managed_panel_offset_20261009.png').convert('RGB')

    def test_shifted_panel_reads_all_six_options_without_lowering_threshold(self):
        expected = ('unchecked',)*3 + ('checked',)*3
        for (key,box,label), state in zip(managed.OPTIONS, expected):
            self.assertEqual(state, managed.option_state(self.s,key,box,label,self.frame))

    def test_panel_translation_tracks_both_axes(self):
        for dx,dy in ((-9,0),(4,8),(-15,-10)):
            frame = Image.new('RGB',self.frame.size)
            frame.paste(self.frame,(dx,dy))
            for (key,box,label), expected in zip(managed.OPTIONS, ('unchecked',)*3+('checked',)*3):
                self.assertEqual(expected,managed.option_state(self.s,key,box,label,frame))

    def test_unknown_title_or_label_never_authorizes_a_checkbox(self):
        for roi in (managed.TITLE_ROI,(1035,350,1150,385)):
            frame = self.frame.copy()
            frame.paste((0,0,0),roi)
            with self.assertRaises(hs.PendingError):
                managed.option_state(self.s,*managed.OPTIONS[0],frame)

    def test_positive_stopped_panel_is_closed_before_releasing_lease(self):
        s=self.s
        s.click_template=Mock(return_value=True)
        s.wait_absent=Mock(return_value=True)
        s.wait_until_main=Mock(return_value=True)
        self.assertTrue(s.recover_known_page(hs.Account(1,'anonymous',180),self.frame))
        call=s.click_template.call_args
        self.assertEqual('managed_settings_close_current.bmp',call.args[1])
        self.assertEqual(.98,call.kwargs['threshold'])
        self.assertEqual(.98,call.kwargs['anchor_threshold'])


class UnchangedFormationTests(unittest.TestCase):
    def test_request_and_all_runner_defaults_preserve_formation(self):
        self.assertEqual('keep',runtime.RunRequest().formation)
        for constructor in (daily.DailyRunner.__init__,daily.DailyRunner.for_bound_account,
                            multi.MultiSoloRunner.__init__,team.TeamRunner.__init__):
            self.assertEqual('keep',inspect.signature(constructor).parameters['formation'].default)
        for value in ('keep','main','fast'):
            self.assertEqual(value,runtime.RunRequest(formation=value).formation)
        with self.assertRaises(ValueError):runtime.RunRequest(formation='unknown')

    def test_keep_never_opens_or_inspects_ninja_page(self):
        runner=object.__new__(daily.DailyRunner)
        runner.formation,runner.formation_checked='keep',False
        runner.scheduler,runner.visible,runner.click,runner.event=Mock(),Mock(),Mock(),Mock()
        runner.ensure_formation_once()
        runner.ensure_formation_once()
        self.assertTrue(runner.formation_checked)
        runner.visible.assert_not_called()
        runner.click.assert_not_called()
        self.assertFalse(runner.scheduler.mock_calls)
        runner.event.assert_called_once()

    def test_team_keep_does_not_switch_members_to_inspect_formation(self):
        runner=object.__new__(team.TeamRunner)
        runner.scheduler=Mock()
        runner._formation(hs.Team(1,('anonymous-a','anonymous-b')),'keep')
        runner.scheduler.switch_account.assert_not_called()

    def test_unified_adapters_forward_unchanged_formation(self):
        for mode,klass in (('solo','DailyRunner'),('multi','MultiSoloRunner'),('team','TeamRunner')):
            fake=SimpleNamespace(scheduler=SimpleNamespace(config={},allow_purchase=True,market_direct_buy_verified=True))
            with patch.object(runtime,klass,return_value=fake) as create:
                runtime.make_runner(runtime.RunRequest(mode=mode),Mock(),hs.ROOT)
                self.assertEqual('keep',create.call_args.kwargs['formation'])

    def test_real_multi_gui_default_and_worker_arguments(self):
        app=gui.MultiSoloApp();app.withdraw();self.addCleanup(app.destroy)
        self.assertEqual('保持不变',app.formation.get())
        with patch.object(gui.threading,'Thread') as thread:app.start()
        self.assertEqual('keep',thread.call_args.kwargs['args'][4])

    def test_unified_default_does_not_load_old_team_formation_or_start_teams(self):
        with tempfile.TemporaryDirectory() as folder:
            root=Path(folder)
            path=root/'state/team_pipeline_latest.json'
            hs.atomic_json(path,dict(phase='solo',request=dict(leaders=[1,6],team_tasks=['wanted'],
                solo_tasks=['mail'],arena_wins=5,formation='main',final_managed=False)))
            before=path.read_bytes()
            app=UnifiedClient(root=root);app.withdraw()
            try:
                request=app.request()
                self.assertEqual('solo',request.mode)
                self.assertEqual('keep',request.formation)
                self.assertTrue(request.final_managed)
                self.assertFalse(app.controller.busy)
                self.assertEqual(before,path.read_bytes())
            finally:app.destroy()


class DiscoveryOfflineTests(unittest.TestCase):
    def scheduler(self):
        s=object.__new__(hs.Scheduler)
        s._capture_binding_probe=False
        s.no_content_clicks=False
        s.config={}
        s.active=hs.Account(1,'anonymous',180)
        s.hwnd=99
        s.current_account_only=None
        s.click_count=0
        s.check_input_pause,s.ensure_window=Mock(),Mock()
        s.screen_point=Mock(side_effect=lambda x,y:(x,y))
        s.state=Mock()
        s.current_x=315
        s._generic_active_tab_x=Mock(side_effect=lambda frame:frame.info['x'])
        s.offline_dialog_visible=Mock(side_effect=lambda frame:frame.info['offline'])
        def capture():
            frame=Image.new('RGB',(1920,1032))
            frame.info.update(x=s.current_x,offline=s.current_x==180)
            s.check_offline_frame(frame)
            return frame
        s.capture=Mock(side_effect=capture)
        def select(x,y,label,account):
            with patch.object(hs.Win32,'foreground',return_value=99), \
                 patch.object(hs.Win32,'hit_test',return_value=1), \
                 patch.object(hs.Win32,'click',side_effect=lambda x,y:setattr(s,'current_x',x)):
                hs.Scheduler.chrome_click(s,x,y,label,account)
        s.chrome_click=Mock(side_effect=select)
        s.wait_interruptible,s.check_stop,s.event=Mock(),Mock(),Mock()
        s.template=Mock(return_value=(False,0.,0,0))
        return s

    def test_first_offline_tab_does_not_abort_later_online_identity_observation(self):
        s=self.scheduler()
        with patch.object(multi,'tab_close_positions',return_value=[214,349]), \
             patch.object(accounts,'dismiss_current_final_result',side_effect=lambda s,a,f:f), \
             patch.object(accounts,'collect_current_identity',return_value=(['mask']*3,Image.new('RGB',(1920,1032)))):
            centers,observations,frames,unavailable=accounts.observe_accounts(s)
        self.assertEqual({1:'offline_prompt'},unavailable)
        self.assertEqual({2},set(observations))
        self.assertFalse(s._capture_binding_probe)
        s.state.save_account.assert_not_called()

    def test_tab_going_offline_during_three_identity_frames_is_skipped(self):
        s=self.scheduler()
        with patch.object(multi,'tab_close_positions',return_value=[214,349]), \
             patch.object(accounts,'dismiss_current_final_result',side_effect=lambda s,a,f:f), \
             patch.object(accounts,'collect_current_identity',side_effect=hs.AccountOffline(s.active,'offline')):
            centers,observations,frames,unavailable=accounts.observe_accounts(s)
        self.assertEqual({1:'offline_prompt',2:'offline_prompt'},unavailable)
        self.assertEqual({},observations)
        self.assertFalse(s._capture_binding_probe)

    def test_observation_restores_capture_guards_even_after_a_geometry_error(self):
        s=self.scheduler()
        with patch.object(accounts,'_observe_accounts',side_effect=hs.SafetyError('geometry')):
            with self.assertRaises(hs.SafetyError):accounts.observe_accounts(s)
        self.assertFalse(s._capture_binding_probe)


class UnselectedIdleTaskTests(unittest.TestCase):
    def coordinator(self, task='mail'):
        owner=object.__new__(multi.MultiSoloRunner)
        account=hs.Account(1,'anonymous',180)
        owner.tasks=('ranking','survival','quiz')
        owner.runners={account.name:SimpleNamespace(records={task:dict(status='pending',last_error='old error')})}
        owner.runner_for=Mock(return_value=owner.runners[account.name])
        owner.event=Mock()
        s=owner.scheduler=Mock()
        s.state.accounts={account.name:dict(actions={})}
        s.main_ready.return_value=True
        s.verify_identity.return_value=(True,{})
        s._combat_active_visible.return_value=False
        s.save_raw_evidence.return_value=Path('observed.png')
        return owner, account

    def test_unselected_mail_record_is_preserved_on_verified_idle_main(self):
        owner,account=self.coordinator()
        original=dict(owner.runners[account.name].records['mail'])
        self.assertTrue(owner._preserve_unselected_idle_task(account,'mail'))
        self.assertEqual(original,owner.runners[account.name].records['mail'])
        self.assertIsNone(owner.unresolved_combat_task(account))
        self.assertEqual(3,owner.scheduler.verify_identity.call_count)
        owner.scheduler.click_template.assert_not_called()
        owner.scheduler.commit_action.assert_not_called()

    def test_failed_managed_settings_can_wait_for_final_managed_stage(self):
        owner,account=self.coordinator('managed')
        self.assertTrue(owner._preserve_unselected_idle_task(account,'managed'))
        self.assertEqual('pending',owner.runners[account.name].records['managed']['status'])
        self.assertIsNone(owner.unresolved_combat_task(account))

    def test_unknown_action_or_non_main_page_never_bypasses_origin_gate(self):
        for changed in ('action','page','identity','battle'):
            owner,account=self.coordinator()
            if changed=='action':owner.scheduler.state.accounts[account.name]['actions']['mail_claim']=dict(status='pending')
            elif changed=='page':owner.scheduler.main_ready.return_value=False
            elif changed=='identity':owner.scheduler.verify_identity.return_value=(False,{})
            else:owner.scheduler._combat_active_visible.return_value=True
            self.assertFalse(owner._preserve_unselected_idle_task(account,'mail'))
            self.assertEqual('mail',owner.unresolved_combat_task(account))
            owner.scheduler.click_template.assert_not_called()

    def test_selected_task_or_combat_record_is_never_deferred(self):
        for task in ('survival','arena','ranking','quiz'):
            owner,account=self.coordinator(task)
            self.assertFalse(owner._preserve_unselected_idle_task(account,task))

    def test_updated_pending_record_invalidates_idle_proof(self):
        owner,account=self.coordinator()
        self.assertTrue(owner._preserve_unselected_idle_task(account,'mail'))
        owner.runners[account.name].records['mail']['last_error']='new attempt'
        self.assertEqual('mail',owner.unresolved_combat_task(account))


class SurvivalPendingProofTests(unittest.TestCase):
    def scheduler(self, confirmation):
        s=object.__new__(hs.Scheduler)
        account=hs.Account(1,'anonymous',180)
        s.state=Mock()
        action='survival_battle_step_2'
        original=dict(status='pending', operation_id='current:'+action)
        s.state.accounts={account.name:{'actions':{action:original,
            'survival_result_confirm_2':confirmation}}}
        s._combat_ready_visible=Mock(return_value=True)
        s._combat_active_visible=Mock(return_value=False)
        s.verify_identity=Mock(return_value=(True,{}))
        s.wait_interruptible=Mock();s.capture=Mock(return_value=Image.new('RGB',(1920,1032)))
        s.save_raw_evidence=Mock(return_value=Path('observed.png'))
        s._survival_full_score_visible=Mock(return_value=True)
        s.templates=Mock();s.event=Mock();s.commit_action=Mock()
        return s,hs.CombatSlot(account,'survival',next_index=2),original

    def test_full_score_alone_preserves_the_unknown_battle(self):
        s,slot,original=self.scheduler({})
        with patch.object(hs,'grade_candidates',return_value=[]):
            self.assertFalse(s._observe_survival_pending_page(slot,s.capture()))
        self.assertEqual('pending',original['status'])
        s.commit_action.assert_not_called()
        self.assertEqual(220,s.state.accounts[slot.account.name]['survival_resume_observations']['survival_battle_step_2']['score'])

    def test_same_operation_confirmed_result_can_resume_without_a_new_start(self):
        s,slot,_=self.scheduler(dict(status='done',operation_id='current:survival_result_confirm_2'))
        with patch.object(hs,'grade_candidates',return_value=[]):
            self.assertTrue(s._observe_survival_pending_page(slot,s.capture()))
        s.commit_action.assert_called_once()
        self.assertEqual('survival_battle_step_2',s.commit_action.call_args.args[1])

    def test_prior_round_confirmation_cannot_complete_a_current_battle(self):
        s,slot,_=self.scheduler(dict(status='done',operation_id='prior:survival_result_confirm_2'))
        with patch.object(hs,'grade_candidates',return_value=[]):
            self.assertFalse(s._observe_survival_pending_page(slot,s.capture()))
        s.commit_action.assert_not_called()

    def test_identity_or_page_change_preserves_pending_without_a_result_commit(self):
        s,slot,_=self.scheduler(dict(status='done',operation_id='current:survival_result_confirm_2'))
        s.verify_identity.side_effect=[(True,{}),(False,{})]
        self.assertFalse(s._observe_survival_pending_page(slot,s.capture()))
        s.commit_action.assert_not_called();s.save_raw_evidence.assert_not_called()


if __name__=='__main__':unittest.main()
