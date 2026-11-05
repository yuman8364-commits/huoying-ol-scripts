"""Strong-team and Daruma integration gates; no live input."""
from pathlib import Path
from dataclasses import replace
from types import SimpleNamespace
import unittest
import threading
import uuid
import tempfile
from unittest.mock import Mock, patch

from PIL import Image

import hyol_scheduler as hs
import hyol_team_pipeline as pipeline
import hyol_daruma_task as daruma
import hyol_runtime as runtime
from hyol_client import UnifiedClient
from test_hyol_team_pipeline import coordinator


class ExpansionTests(unittest.TestCase):
    def test_weekly_strong_page_requires_fixed_reward_label_and_close_on_same_frame(self):
        for close in (True,False):
            s = object.__new__(pipeline.PipelineScheduler)
            frame = Image.new('RGB',(1920,1032))
            s.capture = Mock(return_value=frame)
            def match(name, roi, observed, **kwargs):
                self.assertIs(observed, frame)
                ok = name == 'strong_reward_label_core_current.bmp' or name == 'team_activity_close.bmp' and close
                return ok, 1. if ok else 0., 744,818
            with patch.object(hs.Scheduler,'template',side_effect=match):
                self.assertEqual(s.template('strong_approach_anchor.bmp')[0], close)
            s.capture.assert_called_once()

    def test_new_counter_asset_requires_fresh_page_and_identity_before_acceptance(self):
        with tempfile.TemporaryDirectory() as directory:
            s = object.__new__(pipeline.PipelineScheduler)
            s.event = Mock()
            s.wait_interruptible = Mock(side_effect=lambda _: (Path(directory)/'strong_approach_reward_digit_5.bmp').write_bytes(b'new'))
            frame = object()
            s.capture = Mock(return_value=frame)
            s.verify_identity = Mock(return_value=(True,{}))
            s.template = Mock(return_value=(True,1.,0,0))
            account = hs.Account(1,'test',180)
            spec = hs.TEAM_ACTIVITY_BY_KEY['strong_approach']
            with patch.object(hs,'ASSETS',Path(directory)), patch.object(hs.Scheduler,'_read_team_reward_remaining',
                    side_effect=[hs.PendingError('unknown digit'),5]) as read:
                self.assertEqual(s._read_team_reward_remaining(account,spec,object()),5)
            self.assertIs(read.call_args.args[2],frame)
            s.verify_identity.assert_called_once_with(account,frame)

    def test_request_accepts_strong_and_daruma_without_puzzle(self):
        request = runtime.RunRequest(mode='team_then_multi', team_tasks=('strong_approach',), tasks=('daruma',))
        self.assertEqual(request.team_tasks, ('strong_approach',))
        self.assertEqual(request.tasks, ('daruma',))
        self.assertNotIn('weekend', request.tasks)

    def test_native_gui_exposes_independent_new_selections(self):
        app = UnifiedClient()
        try:
            app.withdraw()
            app.tabs.select(1)
            app.team_mode.set('已有队伍 → 多人单人')
            app._team_mode_changed()
            self.assertFalse(app.pipeline_task_vars['strong_approach'].get())
            self.assertFalse(app.task_vars['daruma'].get())
            app.pipeline_task_vars['strong_approach'].set(True)
            app.task_vars['daruma'].set(True)
            request = app.request()
            self.assertIn('strong_approach', request.team_tasks)
            self.assertIn('daruma', request.tasks)
            self.assertNotIn('weekend', request.tasks)
            self.assertIsNone(app.controller.worker)
        finally:
            app.destroy()

    def test_strong_zero_requires_captain_three_frame_zero_and_evidence(self):
        proof = dict(captain='a', captain_remaining=0, captain_observations=3,
                     reward_exhausted=True, zero_evidence='captain.png')
        self.assertTrue(pipeline.strong_reward_zero_confirmed(proof, 'a'))
        self.assertFalse(pipeline.strong_reward_zero_confirmed(proof, 'b'))
        for value in (1, False, None):
            self.assertFalse(pipeline.strong_reward_zero_confirmed({**proof,'captain_remaining':value}, 'a'))
        for missing in ('captain_observations','zero_evidence'):
            bad = dict(proof); bad.pop(missing)
            self.assertFalse(pipeline.strong_reward_zero_confirmed(bad, 'a'))

    def test_strong_missing_zero_blocks_dissolve_before_input(self):
        owner = coordinator()
        owner.tasks = ('strong_approach',)
        slot = owner.record['teams']['1']
        slot.update(members=['captainA', 'memberA'], tasks={'strong':{'status':'done'}})
        with self.assertRaises(hs.PendingError):
            owner.dissolve(1, slot)
        owner.scheduler.switch_account.assert_not_called()

    def test_stamina_stop_requires_positive_three_frame_proof_and_resolved_start(self):
        proof = dict(captain='a', stop_reason='stamina_exhausted', stamina_exhausted=True, stamina_observations=3,
                     stamina_evidence='stamina.png', refused_start_resolved=True, reward_exhausted=False)
        self.assertTrue(pipeline.strong_stop_confirmed(proof, 'a'))
        self.assertFalse(pipeline.strong_stop_confirmed(proof, 'b'))
        self.assertFalse(pipeline.strong_reward_zero_confirmed(proof, 'a'))
        for missing in ('stamina_evidence','refused_start_resolved','stamina_observations'):
            bad = dict(proof)
            bad.pop(missing)
            self.assertFalse(pipeline.strong_stop_confirmed(bad, 'a'))

    def test_one_strong_battle_does_not_finish_or_dissolve_nonzero_team(self):
        owner = coordinator()
        slot = owner.record['teams']['1']
        slot['members'] = ['captainA','memberA']
        s = owner.scheduler
        s.state.aliases['role2'] = 'memberA'
        s._next_team_attempt_index.return_value = 2
        s._team_activity_completion_verified.return_value = False
        owner.snapshot = Mock(return_value=(('captainA','memberA'),'room.png'))
        driver = pipeline.LeaderActivityRunner(owner, s.accounts[0], slot)
        driver.finish_task = Mock()
        driver.tick('strong')
        self.assertEqual(slot['tasks']['strong']['phase'], 'ready')
        self.assertNotEqual(slot['tasks']['strong']['status'], 'done')
        driver.finish_task.assert_not_called()

    def test_stamina_refusal_resolves_only_original_start_without_battle_credit(self):
        owner = coordinator()
        slot = owner.record['teams']['1']
        slot['members'] = ['captainA', 'memberA']
        slot['owner'] = 'captainA'
        driver = pipeline.LeaderActivityRunner(owner, owner.scheduler.accounts[0], slot)
        driver.finish_task = Mock()
        s = owner.scheduler
        s.strong_stamina_warning.return_value = True
        s._next_team_attempt_index.return_value = 2
        s._team_attempt_base.return_value = 'strong_attempt_2'
        s.state.action_status = Mock(return_value='pending')
        rec = driver.record('strong')
        driver.finish_strong_stamina(rec, hs.Team(1, ('role1','role2')),
                                     hs.TEAM_ACTIVITY_BY_KEY['strong_approach'], object())
        self.assertTrue(pipeline.strong_stop_confirmed(rec, 'captainA'))
        self.assertFalse(rec['reward_exhausted'])
        self.assertIn('battle_credit=false', s.commit_action.call_args.args[2])
        self.assertEqual(s.commit_action.call_args.args[1], 'strong_attempt_2_start')
        s.click_template.assert_not_called()

    def test_nested_same_thread_lease_keeps_parent_mutex_owned(self):
        name = 'Local\\HYOL_Daruma_Integration_Test_' + uuid.uuid4().hex
        outer, inner = hs.ForegroundRuntimeLease(), hs.ForegroundRuntimeLease()
        outer.NAME = inner.NAME = name
        observed = []
        def compete():
            contender = hs.ForegroundRuntimeLease()
            contender.NAME = name
            try: observed.append(contender.acquire())
            finally: contender.release()
        try:
            self.assertTrue(outer.acquire())
            self.assertTrue(inner.acquire())
            inner.release()
            thread = threading.Thread(target=compete)
            thread.start(); thread.join(2)
            self.assertEqual(observed, [False])
        finally:
            inner.release(); outer.release()
        thread = threading.Thread(target=compete)
        thread.start(); thread.join(2)
        self.assertEqual(observed, [False, True])

    def test_strong_uses_existing_team_and_continues_on_captain_until_stop(self):
        owner = coordinator()
        slot = owner.record['teams']['1']
        slot['members'] = ['captainA','memberA']
        slot['owner'] = 'captainA'
        s = owner.scheduler
        s.state.aliases['role2'] = 'memberA'
        s.state.global_state = {'generic_strong_counts':{'1':dict(
            captain='captainA', captain_remaining=0, captain_observations=3, zero_evidence='a.png')}}
        s._next_team_attempt_index.return_value = 2
        s._team_activity_completion_verified.return_value = True
        owner.snapshot = Mock(return_value=(('captainA','memberA'),'room.png'))
        driver = pipeline.LeaderActivityRunner(owner, s.accounts[0], slot)
        driver.finish_task = Mock()
        driver.tick('strong')
        args = s.run_verified_strong_approach.call_args
        self.assertEqual(args.args[0].members, ('role1','role2'))
        self.assertIsNone(args.kwargs['target_count'])
        self.assertFalse(args.args[2].require_all_member_auto)
        self.assertTrue(pipeline.strong_reward_zero_confirmed(slot['tasks']['strong'], 'captainA'))
        s.create_verified_team.assert_not_called()
        s.invite_member.assert_not_called()

    def test_strong_poll_and_resume_never_settle_member_tabs(self):
        s = object.__new__(pipeline.PipelineScheduler)
        s.settle_member_attempt = Mock()
        leader = hs.Account(1,'a',180)
        team = hs.Team(1,('a','b'))
        spec = hs.TEAM_ACTIVITY_BY_KEY['strong_approach']
        with patch.object(hs.Scheduler,'_poll_generic_team_activity') as poll, \
                patch.object(hs.Scheduler,'_resume_generic_team_battle') as resume:
            s._poll_generic_team_activity(team,leader,spec,2)
            s._resume_generic_team_battle(team,leader,spec,attempt_index=2)
        poll.assert_called_once_with(s,team,leader,spec,2)
        resume.assert_called_once_with(s,team,leader,spec,None,None,2)
        s.settle_member_attempt.assert_not_called()

    def test_strong_captain_zero_does_not_switch_or_open_member_pages(self):
        s = object.__new__(pipeline.PipelineScheduler)
        s.capture = Mock(return_value=object())
        s.template = Mock(return_value=(True,1.,0,0))
        s.verify_identity = Mock(return_value=(True,{}))
        s._read_team_reward_remaining = Mock(return_value=0)
        s.wait_interruptible = Mock()
        s.save_raw_evidence = Mock(return_value='zero.png')
        s.event = Mock()
        s.state = SimpleNamespace(aliases={'a':'captainA'},global_state={},save_global=Mock())
        s.switch_account, s.open_strong_approach = Mock(), Mock()
        leader = hs.Account(1,'a',180)
        self.assertTrue(s._strong_all_member_rewards_zero(hs.Team(1,('a','b')),leader,
                         hs.TEAM_ACTIVITY_BY_KEY['strong_approach']))
        self.assertEqual(s._read_team_reward_remaining.call_count,3)
        s.switch_account.assert_not_called()
        s.open_strong_approach.assert_not_called()

    def test_real_captain_result_return_does_not_require_member_auto_receipts(self):
        for method in ('_poll_generic_team_activity','_resume_generic_team_battle'):
            s = object.__new__(pipeline.PipelineScheduler)
            s.check_stop = Mock()
            s.capture = Mock(return_value=object())
            s._team_attempt_base = Mock(return_value='strong_attempt_1')
            s._team_result_template = Mock(return_value=None)
            s._team_reward_result_visible = Mock(return_value=False)
            s._team_battle_start_visible = Mock(return_value=False)
            s._battle_auto_on_visible = Mock(return_value=False)
            s.template = Mock(return_value=(False,0.,0,0))
            s.main_ready = Mock(return_value=True)
            s.event, s._set_team_activity_status = Mock(), Mock()
            s._team_members_auto_complete = Mock(side_effect=AssertionError('member auto checked'))
            s.settle_member_attempt = Mock(side_effect=AssertionError('member result visited'))
            s.state = SimpleNamespace(action_status=Mock(return_value='done'),set_action=Mock())
            spec=replace(hs.TEAM_ACTIVITY_BY_KEY['strong_approach'],require_all_member_auto=False)
            with patch.object(hs,'check_battle_watchdog'):
                getattr(s,method)(hs.Team(1,('a','b')),hs.Account(1,'a',180),spec,attempt_index=1)
            self.assertEqual(s.state.set_action.call_args.args[1:3],('strong_attempt_1_complete','done'))
            s._team_members_auto_complete.assert_not_called()
            s.settle_member_attempt.assert_not_called()

    def runner(self):
        s = Mock()
        s.state.day = daruma.datetime.now().date().isoformat()
        s.hwnd = 42
        s.shared_state_directory = Path('isolated-test/state/runs/current')
        s.content_click_count = s.click_count = 0
        s.content_guard.return_value = Image.new('RGB', (1920,1032))
        r = Mock(scheduler=s, account=hs.Account(1,'accountA',180))
        rec = {}
        r.record.return_value = rec
        return r, rec

    def test_daruma_engine_uses_current_run_and_proven_zero_then_return(self):
        r, rec = self.runner()
        engine = Mock()
        engine.desktop.content_clicks = 2
        engine.journal.path = Path('isolated-test/checkpoint.json')
        engine.journal.data = dict(status='done', intent=None, actions=[], remaining=0,
                                   zero_verified=True, zero_evidence='zero.png', round=3, chance=0, layers=6)
        engine.run.return_value = dict(status='done', checkpoint=str(engine.journal.path))
        with patch('hyol_daruma.is_weekend', return_value=True), patch('hyol_daruma._open_weekend'), \
             patch.object(daruma, 'game_session_token', return_value='session'), \
             patch.object(daruma, 'DarumaRunner', return_value=engine) as make:
            daruma.run(r)
        self.assertTrue(daruma.verified(rec))
        self.assertTrue(str(make.call_args.kwargs['root']).startswith(str(r.scheduler.shared_state_directory)))
        self.assertIs(make.call_args.kwargs['stop'], r.scheduler.stop)
        self.assertEqual(r.scheduler.content_click_count, 2)
        r.return_to_main.assert_called_once()

    def test_daruma_nonzero_or_pending_never_closes_or_completes(self):
        for status in ('done','pending'):
            r, rec = self.runner()
            engine = Mock()
            engine.desktop.content_clicks = 0
            engine.journal.path = Path('checkpoint.json')
            engine.journal.data = dict(status='done', intent=None, actions=[], remaining=1,
                                       zero_verified=False, zero_evidence=None)
            engine.run.return_value = dict(status=status, checkpoint='checkpoint.json')
            with patch('hyol_daruma.is_weekend', return_value=True), patch('hyol_daruma._open_weekend'), \
                 patch.object(daruma, 'game_session_token', return_value='session'), \
                 patch.object(daruma, 'DarumaRunner', return_value=engine):
                with self.assertRaises(hs.PendingError): daruma.run(r)
            r.click.assert_not_called()
            r.return_to_main.assert_not_called()
            self.assertFalse(daruma.verified(rec))

    def test_completion_rejects_bare_done_daruma(self):
        request = runtime.RunRequest(tasks=('daruma',), final_managed=False)
        runner = SimpleNamespace(records={'daruma':dict(status='done', task_completed=True, home_returned=True)})
        self.assertFalse(runtime.completion_verified(request, runner, 0))


if __name__ == '__main__':
    unittest.main()
