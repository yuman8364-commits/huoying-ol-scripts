"""Fresh starts, bounded account observation and phase handoff without game input."""
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch
import tempfile
import threading
import unittest

import numpy as np
from PIL import Image

import hyol_accounts as accounts
import hyol_scheduler as hs
import hyol_runtime as runtime
import hyol_team_pipeline as pipeline
from hyol_checkpoints import encode_mask, load_tasks
from hyol_daily_tasks import DEFAULT_SOLO_OPTIONS, DailyScheduler
from hyol_multi_solo import MultiSoloRunner
from hyol_run_context import RunContext


class FreshRunCase(unittest.TestCase):
    def setUp(self):
        folder = tempfile.TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        self.root = Path(folder.name)
        self.config = dict(AccountDiscovery='automatic', SharedStateDirectory=str(self.root/'state'))
        self.context = RunContext.create(self.root,self.config)


class FreshRunTests(FreshRunCase):
    def test_old_pending_done_and_corrupt_ledgers_are_never_loaded(self):
        base = self.context.identity_directory
        base.mkdir()
        for name,text in (('team_pipeline_latest.json','{broken'),('multi_solo_latest.json','{"stage":"done"}'),
                          ('checkpoint_resolutions.json','{broken'),('historical_isolations.json','{broken')):
            (base/name).write_text(text,encoding='utf-8')
        with patch.object(hs,'read_config',return_value=self.config):
            first = MultiSoloRunner(('mail',),run_context=self.context)
            second = MultiSoloRunner(('mail',))
        self.assertNotEqual(first.scheduler.state.directory,second.scheduler.state.directory)
        self.assertFalse(first.scheduler.state.restore_history)
        account = first.scheduler.accounts[0]
        path, records = load_tasks(first.scheduler.state,account.name)
        self.assertEqual(records,{})
        self.assertTrue(path.is_relative_to(self.context.directory))
        self.assertEqual((base/'team_pipeline_latest.json').read_text(),'{broken')

    def test_same_run_actions_survive_phase_handoff_but_next_start_is_empty(self):
        state = hs.StateStore(['a'],directory=self.context.directory,restore_history=False)
        state.set_action('a','claim','pending','submitted once')
        same = hs.StateStore(['a'],directory=self.context.directory,restore_history=False)
        next_context = RunContext.create(self.root,self.config)
        fresh = hs.StateStore(['a'],directory=next_context.directory,restore_history=False)
        self.assertEqual(same.action_status('a','claim'),'pending')
        self.assertEqual(fresh.action_status('a','claim'),'new')

    def test_current_identity_registration_does_not_inspect_old_work(self):
        mask = (np.random.default_rng(1).random((32,145))>.5).astype(np.uint8)
        s = SimpleNamespace(shared_state_directory=self.context.directory,
            identity_directory=self.context.identity_directory,run_context=self.context,no_content_clicks=False)
        with patch.object(accounts,'_has_unresolved_work',side_effect=AssertionError('history read')):
            mapping,_ = accounts.register_observations(s,{1:[mask]*3})
            again,_ = accounts.register_observations(s,{1:[mask]*3})
        self.assertEqual(mapping,again)
        self.assertTrue((self.context.identity_directory/'account_identities.json').is_file())
        self.assertFalse((self.context.directory/'account_identities.json').exists())

    def test_pipeline_initializes_current_options_without_reading_latest(self):
        with patch.object(hs,'read_config',return_value=self.config):
            owner = pipeline.TeamPipelineRunner((1,),solo_tasks=('quiz',),run_context=self.context)
        owner.scheduler._bound_identity_names = {1:'a',2:'b'}
        owner.scheduler.session_token = 'current'
        with patch.object(pipeline,'read_checkpoint',side_effect=AssertionError('old task read')):
            owner.initialize_run()
        self.assertEqual(owner.record['request']['solo_tasks'],['quiz'])
        self.assertEqual(owner.record['teams']['1']['tasks'],{})
        self.assertEqual(owner.record['id'],self.context.identifier)
        self.assertFalse(hasattr(owner,'restore'))

    def test_default_weekend_is_off_even_on_weekend_and_explicit_selection_works(self):
        self.assertNotIn('weekend',DEFAULT_SOLO_OPTIONS)
        self.assertNotIn('weekend',runtime.RunRequest().tasks)
        with patch.object(hs,'read_config',return_value=self.config), patch('hyol_weekend.is_weekend',return_value=True):
            default = MultiSoloRunner()
            selected = MultiSoloRunner(('weekend',))
        self.assertNotIn('weekend',default.tasks)
        self.assertEqual(selected.tasks,('weekend',))

    def test_started_context_never_clears_a_later_stop(self):
        with patch('hyol_diagnostics.clear_manual_pause') as clear:
            self.context.begin()
            self.context.begin()
            clear.assert_called_once()
            self.context.stop.set()
            with self.assertRaises(KeyboardInterrupt):
                self.context.begin()
            clear.assert_called_once()

    def test_stop_during_start_prevents_execution(self):
        with patch('hyol_diagnostics.clear_manual_pause',side_effect=lambda *_:self.context.stop.set()):
            with self.assertRaises(KeyboardInterrupt):
                self.context.begin()
        self.assertFalse(self.context.started)

    def test_window_session_change_ends_run(self):
        s = SimpleNamespace(hwnd=99)
        with patch.object(accounts,'game_session_token',side_effect=['one','two']), patch.object(hs.Win32,'pid',return_value=7):
            self.context.bind_window(s)
            with self.assertRaises(hs.PendingError):
                self.context.bind_window(s)


class BindingTests(FreshRunCase):
    def test_prebinding_result_guard_keeps_observed_tab_coordinates(self):
        with self.context.activate():
            scheduler = pipeline.PipelineScheduler()
        observed = hs.Account(2,'当前窗口标签2',315)
        self.assertNotEqual(scheduler.by_name[observed.name].tab_x,observed.tab_x)
        scheduler.active = observed
        scheduler.generic_current_tab_x = observed.tab_x
        scheduler._verified_current_mask = None
        frame = Image.new('RGB',(1920,1032))
        scheduler.ensure_window = Mock()
        scheduler.capture = Mock(return_value=frame)
        scheduler._generic_current_tab_is_active = Mock(return_value=True)
        scheduler._saved_tab_mapping = Mock(return_value={})
        scheduler.template = Mock(side_effect=lambda name,*_args,**_kwargs:
            (name=='team_result_confirm.bmp',1.,0,0))
        self.assertIs(scheduler._content_guard_once(
            observed,['team_result_confirm.bmp'],anchor_threshold=.98),frame)

    def test_observation_probe_does_not_use_a_previous_tab_map(self):
        self.binding()
        observed = hs.Account(2,'tab2',315)
        scheduler = SimpleNamespace(run_context=self.context,
            by_name={'tab2':hs.Account(2,'tab2',310)},_capture_binding_probe=False)
        self.context.probing = True
        self.assertIs(self.context.resolve(scheduler,observed),observed)
        self.context.probing = False
        scheduler._capture_binding_probe = True
        self.assertIs(self.context.resolve(scheduler,observed),observed)

    def test_bound_actions_still_resolve_to_the_verified_current_map(self):
        self.binding()
        original = hs.Account(2,'b',310)
        current = hs.Account(2,'b',315)
        scheduler = SimpleNamespace(run_context=self.context,
            by_name={'b':current},_capture_binding_probe=False)
        self.assertIs(self.context.resolve(scheduler,original),current)

    def bind_real_team_scheduler(self, unavailable=False):
        frames, masks, refs = self.binding()
        mapping = {1:'a'} if unavailable else {1:'a',2:'b'}
        observations = {i:[masks[i]]*3 for i in mapping}
        with self.context.activate():
            scheduler = pipeline.PipelineScheduler()
        self.assertIsNone(scheduler.available_account_names)
        scheduler.bind_window = Mock()
        scheduler._observed_captain_tabs = {180:True,315:False}
        with patch.object(accounts,'observe_accounts',return_value=(
                (180,315),observations,frames,{2:'offline_prompt'} if unavailable else {})) as observe, \
             patch.object(accounts,'register_observations',return_value=(mapping,refs)), \
             patch.object(accounts,'game_session_token',return_value='test-session'), \
             patch('hyol_checkpoints.current_identity_crop',return_value=Image.new('RGB',(145,32))):
            scheduler._bind_automatic_session((),True,True,None)
        observe.assert_called_once()
        scheduler.capture = Mock(return_value=frames[1])
        scheduler.healthy = Mock(return_value=True)
        scheduler.verify_identity = Mock(return_value=(True,{}))
        scheduler._generic_active_tab_x = Mock(return_value=180)
        scheduler.offline_dialog_visible = Mock(return_value=False)
        scheduler.chrome_click = Mock()
        self.context.layout_matches = Mock(return_value=True)
        return scheduler,frames[1]

    def test_real_team_binding_allows_first_captain_selection(self):
        scheduler,frame = self.bind_real_team_scheduler()
        self.assertIs(self.context.select(scheduler,scheduler.accounts[0]),frame)
        scheduler.chrome_click.assert_not_called()
        self.assertEqual(scheduler.active,scheduler.accounts[0])
        self.assertFalse(scheduler.state.restore_history)

    def test_real_team_binding_excludes_unobserved_accounts(self):
        scheduler,_ = self.bind_real_team_scheduler(unavailable=True)
        with self.assertRaisesRegex(hs.PendingError,'不在可核实标签'):
            self.context.select(scheduler,scheduler.accounts[1])
        scheduler.chrome_click.assert_not_called()

    def loading_scheduler(self, visible=True, assets=None):
        scheduler,frame = self.bind_real_team_scheduler()
        scheduler.healthy.return_value = False
        scheduler.verify_identity.return_value = (False,{})
        scheduler._bound_role_verified = Mock(return_value=(False,{},not visible))
        scheduler.check_stop = Mock()
        scheduler.wait_interruptible = Mock()
        scheduler.save_raw_evidence = Mock(return_value='pending.png')
        scheduler.event = Mock()
        assets = assets if assets is not None else {
            'map_loading_word_current.bmp','map_loading_finished_current.bmp'}
        scheduler.template = Mock(side_effect=lambda name,*_args,**_kwargs:
            (name in assets,1. if name in assets else 0.,0,0))
        return scheduler,frame

    def test_known_map_loading_allows_only_observation_without_rescan(self):
        scheduler,frame = self.loading_scheduler()
        self.context.rebuild = Mock(side_effect=AssertionError('rescan'))
        self.assertIs(self.context.select(scheduler,scheduler.accounts[0],allow_transition=True),frame)
        scheduler.chrome_click.assert_not_called()
        scheduler.event.assert_called_once()
        self.assertFalse(scheduler.event.call_args.kwargs['content_authorized'])
        self.assertEqual(scheduler.event.call_args.kwargs['phase'],'map_loading')

    def test_one_loading_word_cannot_accept_an_unknown_page(self):
        scheduler,_ = self.loading_scheduler(assets={'map_loading_word_current.bmp'})
        with self.assertRaises(hs.PendingError):
            self.context.select(scheduler,scheduler.accounts[0],allow_transition=True)
        scheduler.event.assert_not_called()

    def test_loading_art_cannot_hide_a_visible_conflicting_identity(self):
        scheduler,_ = self.loading_scheduler(visible=False)
        with self.assertRaises(hs.PendingError):
            self.context.select(scheduler,scheduler.accounts[0],allow_transition=True)
        scheduler.event.assert_not_called()

    def test_loading_observation_is_not_enabled_for_other_callers(self):
        scheduler,_ = self.loading_scheduler()
        with self.assertRaises(hs.PendingError):
            self.context.select(scheduler,scheduler.accounts[0])
        scheduler.event.assert_not_called()

    def binding(self):
        frames = {i:Image.new('RGB',(1920,1032)) for i in (1,2)}
        masks = {i:(np.random.default_rng(i).random((32,145))>.5).astype(np.uint8) for i in (1,2)}
        refs = {key:dict(mask=encode_mask(masks[i]),shape=list(masks[i].shape)) for i,key in ((1,'a'),(2,'b'))}
        self.context.remember((180,315),{1:'a',2:'b'},{i:[masks[i]]*3 for i in masks},frames,refs,{})
        return frames,masks,refs

    def scheduler(self):
        self.binding()
        s = Mock()
        s.accounts = [hs.Account(1,'a',180),hs.Account(2,'b',315)]
        s.by_name = {a.name:a for a in s.accounts}
        s.available_account_names = set(s.by_name)
        s.state = hs.StateStore(list(s.by_name),directory=self.context.directory,restore_history=False)
        s.config = {};s.active = None;s._capture_account = None;s._observed_captain_tabs = {}
        s.capture.return_value = Image.new('RGB',(1920,1032))
        s._generic_active_tab_x.return_value = 180
        s.offline_dialog_visible.return_value = False
        s.healthy.return_value = True
        s.verify_identity.return_value = (True,{})
        self.context.layout_matches = Mock(return_value=True)
        return s

    def test_handoff_adopts_map_without_click_or_scan(self):
        self.binding()
        s = SimpleNamespace(config={},event=Mock(),hwnd=0)
        with patch.object(accounts,'observe_accounts',side_effect=AssertionError('rescan')):
            self.context.adopt_solo(s)
        self.assertEqual([(a.name,a.tab_x) for a in s.accounts],[('a',180),('b',315)])
        self.assertFalse(s.state.restore_history)
        self.assertIsNone(s.active)

    def test_current_tab_is_observed_without_clicking_it_again(self):
        s=self.scheduler();a=s.accounts[0]
        self.context.select(s,a)
        s.chrome_click.assert_not_called()
        s.capture.assert_called_once()
        self.assertEqual(s.active,a)

    def test_switch_requires_a_fresh_post_click_frame(self):
        s=self.scheduler();s._generic_active_tab_x.return_value=315
        frames=[Image.new('RGB',(1920,1032),color=color) for color in ('red','blue')]
        s.capture.side_effect=frames
        result=self.context.select(s,s.accounts[0])
        s.chrome_click.assert_called_once()
        self.assertIs(result,frames[1])
        self.assertIs(s.verify_identity.call_args.args[1],frames[1])

    def test_redraw_recovers_with_three_samples_without_rescanning(self):
        s=self.scheduler();s.verify_identity.side_effect=[(False,{}),(False,{}),(True,{})]
        self.context.rebuild=Mock(side_effect=AssertionError('rescan'))
        self.context.select(s,s.accounts[0])
        self.assertEqual(s.capture.call_count,3)
        s.chrome_click.assert_not_called()

    def test_transient_tab_chrome_redraw_does_not_consume_a_rescan(self):
        s=self.scheduler()
        self.context.layout_matches=Mock(side_effect=[False,False,True,True])
        self.context.rebuild=Mock(side_effect=AssertionError('transient rescan'))
        self.context.select(s,s.accounts[0])
        self.assertEqual(s.capture.call_count,2)
        self.context.rebuild.assert_not_called()

    def test_unconfirmed_identity_never_authorizes_content(self):
        s=self.scheduler();s.verify_identity.return_value=(False,{})
        with patch('hyol_checkpoints.current_identity_mask',return_value=np.zeros((32,145),dtype=np.uint8)):
            with self.assertRaises(hs.PendingError):self.context.select(s,s.accounts[0])
        s.chrome_click.assert_not_called()
        self.assertIsNone(s.active)

    def test_reorder_preserves_current_actions_and_rebuilds_only_once(self):
        frames,masks,refs=self.binding()
        s=SimpleNamespace(accounts=[hs.Account(1,'a',180),hs.Account(2,'b',315)],config={},event=Mock())
        s.by_name={a.name:a for a in s.accounts}
        s.state=hs.StateStore(['a','b'],directory=self.context.directory,restore_history=False)
        s.state.set_action('a','claim','pending','sent')
        with patch.object(accounts,'observe_accounts',return_value=((180,315),{1:[masks[2]]*3,2:[masks[1]]*3},frames,{})), \
             patch.object(accounts,'register_observations',return_value=({1:'b',2:'a'},refs)) as register:
            self.context.rebuild(s)
            with self.assertRaises(hs.PendingError):self.context.rebuild(s)
        self.assertEqual([(a.name,a.tab_x) for a in s.accounts],[('a',315),('b',180)])
        self.assertEqual(s.state.action_status('a','claim'),'pending')
        register.assert_called_once()

    def test_initial_identity_redraw_is_bounded_and_keeps_three_stable_frames(self):
        _,masks,_=self.binding()
        s=self.scheduler();s._generic_active_tab_x.return_value=180
        with patch('hyol_checkpoints.current_identity_mask',side_effect=[masks[1],masks[2],masks[1],masks[1],masks[1]]), \
             patch.object(accounts,'is_captain',return_value=True):
            observed,_=accounts.collect_current_identity(s,s.accounts[0])
        self.assertEqual(len(observed),3)
        self.assertEqual(s.capture.call_count,5)

    def test_own_running_battle_can_be_observed_without_claiming_identity(self):
        s=self.scheduler();s.verify_identity.return_value=(False,{})
        s._bound_role_verified.return_value=(False,{},False)
        reference=np.ones((10,10),dtype=np.uint8)
        s._tab_title_masks={1:reference};s._title_mask.return_value=reference
        self.context.select(s,s.accounts[0],allow_hidden=True)
        s.chrome_click.assert_not_called()
        self.assertEqual(s.event.call_args.args[0],'account_observation_bound')
        self.assertFalse(s.event.call_args.kwargs['content_authorized'])

    def test_solo_handoff_has_no_discovery_or_empty_task_stages(self):
        self.binding()
        events=[]
        with patch.object(hs,'read_config',return_value=self.config),patch.object(hs,'LOGS',self.root/'logs'):
            runner=MultiSoloRunner(('mail',),run_context=self.context,event_sink=events.append)
        runner.scheduler.start_f12_monitor=Mock()
        runner.scheduler.bind_window=Mock()
        runner.discover_tabs=Mock(side_effect=AssertionError('second discovery'))
        runner.bind_accounts=Mock(side_effect=AssertionError('second binding'))
        with patch.object(accounts,'observe_accounts',side_effect=AssertionError('second scan')):
            result=runner.run(manage_lease=False,before_prepare=lambda r:setattr(r.scheduler,'accounts',[]))
        self.assertEqual(result,2)
        self.assertTrue(any(e['event']=='multi_solo_no_accounts' for e in events))
        self.assertFalse(any(e['event']=='multi_solo_stage_started' for e in events))


class PipelineStartTests(FreshRunCase):
    def test_automatic_captains_need_only_one_binding_pass(self):
        from test_hyol_team_pipeline import coordinator
        owner=coordinator();owner.leaders=();owner.root=self.root;owner.run_context=self.context
        self.context.begin=Mock();owner.sink=None
        s=owner.scheduler;s.stop=self.context.stop;s._automatic_captain_slots=(1,4)
        owner.record.update(source_day=s.state.day,execution_day=s.state.day)
        owner.initialize_run=Mock();owner.prepare_teams=Mock();owner.run_teams=Mock()
        solo=Mock();solo.scheduler.state.day=s.state.day;solo.run.return_value=2
        solo.runners={};solo.initial_unavailable=set();solo.disconnected=set()
        with patch.object(hs,'ForegroundRuntimeLease'),patch.object(pipeline,'MultiSoloRunner',return_value=solo) as make:
            self.assertEqual(owner.run(),2)
        s.bind_session.assert_called_once_with((),allow_unavailable=True,leaders_only=True)
        self.assertIs(make.call_args.kwargs['run_context'],self.context)

    def test_dissolve_reuses_the_open_verified_room(self):
        from test_hyol_team_pipeline import coordinator
        owner=coordinator();s=owner.scheduler
        slot=owner.record['teams']['1']
        slot.update(members=['captainA','member'],tasks={'group':dict(daily_remaining=0,
            friendship_remaining=0,reward_exhausted=True,zero_evidence='zero.png')})
        owner.snapshot=Mock(return_value=(('captainA','member'),'room.png'))
        s.state.action_status=Mock(return_value='new')
        with patch.object(pipeline,'verify_unteamed',return_value='no-team.png'), \
             patch('hyol_navigation.return_to_leaf',return_value=True):
            owner.dissolve(1,slot)
        owner.snapshot.assert_called_once_with(s.accounts[0],leave_open=True)
        s.open_team_room.assert_not_called()
        self.assertEqual(slot['status'],'done')

    def test_progress_opens_empty_and_reads_only_the_selected_run(self):
        import tkinter as tk
        from hyol_progress_view import ProgressView
        window=tk.Tk();window.withdraw();self.addCleanup(window.destroy)
        reader=Mock();reader.load.return_value=dict(rows=[],runs=[],updated_at='',warnings=[])
        view=ProgressView(window,self.root,reader=reader,current_only=True)
        reader.days.assert_not_called();reader.load.assert_not_called()
        view.set_run(self.context.identifier)
        self.assertEqual(reader.load.call_args.args[1],self.context.identifier)
        reader.days.assert_not_called()
        self.assertEqual(view.runs.winfo_manager(),'')


class ControllerStartTests(unittest.TestCase):
    def test_explicit_start_clears_stop_only_after_acquiring_lease(self):
        with tempfile.TemporaryDirectory() as folder:
            events=[];order=[]
            controller=runtime.RuntimeController(events.append,Path(folder))
            lease=Mock();lease.acquire.side_effect=lambda:(order.append('lease') or True)
            with patch.object(hs,'ForegroundRuntimeLease',return_value=lease), \
                 patch('hyol_diagnostics.clear_manual_pause',side_effect=lambda *_:order.append('clear')), \
                 patch.object(runtime,'preflight_live',side_effect=lambda:(order.append('check') or {'status':'offline'})), \
                 patch.object(runtime,'make_runner') as make:
                controller._run(runtime.RunRequest())
            self.assertEqual(order,['lease','clear','check'])
            lease.release.assert_called_once();make.assert_not_called()

    def test_busy_lease_never_clears_stop_or_constructs_runner(self):
        with tempfile.TemporaryDirectory() as folder:
            controller=runtime.RuntimeController(lambda *_:None,Path(folder))
            lease=Mock();lease.acquire.return_value=False
            with patch.object(hs,'ForegroundRuntimeLease',return_value=lease), \
                 patch('hyol_diagnostics.clear_manual_pause') as clear,patch.object(runtime,'make_runner') as make:
                controller._run(runtime.RunRequest())
            clear.assert_not_called();make.assert_not_called();lease.release.assert_called_once()


if __name__ == '__main__':
    unittest.main()
