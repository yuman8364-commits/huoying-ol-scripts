"""Exercise isolation, durable visual intent and real GUI wiring without game input."""
from test_identity_fixtures import ROLE_A, ROLE_B, ROLE_C, ROLE_D, ROLE_E, ROLE_F, legacy_config
from pathlib import Path
from datetime import date,timedelta,datetime
from types import SimpleNamespace
import json
import tempfile
import threading
import time
import unittest
import numpy as np
from PIL import Image
from unittest.mock import Mock, patch

import hyol_scheduler as hs
import hyol_team_pipeline as pipeline
import hyol_runtime as runtime
from hyol_client import UnifiedClient


def coordinator():
    owner = object.__new__(pipeline.TeamPipelineRunner)
    owner.leaders,owner.tasks,owner.solo_tasks = (1,4),pipeline.TEAM_TASKS,('mail',)
    owner.formation,owner.arena_wins,owner.managed_enabled = 'main',5,False
    owner.arena_target_mode = 'tickets'
    owner.record = dict(id='original',phase='teams',teams={str(i):dict(status='running',tasks={},leader_slot=i)
                                                         for i in owner.leaders})
    owner.save,owner.event = Mock(),Mock()
    owner.scheduler = Mock()
    owner.scheduler.run_context = None
    owner.run_context = SimpleNamespace(layout_matches=lambda frame: True)
    owner.scheduler.template.return_value = (False, 0., 0, 0)
    owner.scheduler.accounts = [hs.Account(i,f'role{i}',100+i*100) for i in range(1,7)]
    owner.scheduler.state = SimpleNamespace(day='2026-10-06',aliases={'role1':'captainA','role4':'captainB'},
                                           accounts={f'role{i}':{'actions':{}} for i in range(1,7)})
    owner.scheduler.by_name = {a.name:a for a in owner.scheduler.accounts}
    owner.scheduler._bound_identity_names = {1:'captainA',4:'captainB'}
    owner.drivers = {}
    owner.solo_exclusions = {}
    elapsed = [0.0]
    owner._poll_clock = lambda: elapsed[0]
    owner.scheduler.wait_interruptible.side_effect = lambda seconds: elapsed.__setitem__(0,elapsed[0]+seconds)
    return owner


class DriverTests(unittest.TestCase):
    def setUp(self):
        self.owner = coordinator()
        self.slot = self.owner.record['teams']['1']
        self.slot['members'] = ['captainA','memberA']
        self.driver = pipeline.LeaderActivityRunner(self.owner,self.owner.scheduler.accounts[0],self.slot)
        self.driver.group_rewards = lambda frame: (self.s._read_team_reward_remaining(
            self.driver.account,hs.TEAM_ACTIVITY_BY_KEY['group_instance'],frame),0)
        self.driver.recover_group_counter = Mock()
        self.frame = set()
        self.driver.visible = lambda asset,roi=hs.VIEW,frame=None,threshold=.92: asset in (self.frame if frame is None else frame)
        self.s = self.owner.scheduler
        self.s.template.side_effect = lambda asset, roi, frame=None, **kw: (
            self.driver.visible(asset, roi, frame, kw.get('threshold', .92)), 1., 0, 0)
        self.s.capture.side_effect = lambda: self.frame
        self.s.switch_account.side_effect = lambda account: self.frame
        self.s._team_result_template.return_value = None
        self.s._team_reward_result_visible.return_value = False
        self.s._team_battle_start_visible.return_value = False
        self.s._battle_auto_on_visible.return_value = False
        self.s.main_ready.return_value = False
        self.s.verify_identity.return_value = (True,{'captainA':1.0})
        self.actions = {}
        self.s.state.action_status = lambda account,label: self.actions.get(label,'new')
        self.s.commit_action = Mock(side_effect=lambda account,label,detail: self.actions.update({label:'done'}))
        self.s.click_template.side_effect = lambda account,asset,roi,dx,dy,label,anchors,**kwargs: self.actions.update(
            {label:'pending' if kwargs.get('irreversible') else 'done'}) or True

    def test_resumed_wanted_attempt_has_distinct_actions_and_original_prefix(self):
        self.slot['tasks']['wanted'] = dict(attempt_id='fresh',status='new')
        self.assertEqual(self.driver.action('wanted_auto'),'pipeline_original_attempt_fresh_wanted_auto')
        self.assertEqual(self.driver.action('group_6_select'),'pipeline_original_group_6_select')

    def test_unknown_wanted_page_stops_before_long_deadline_without_resending_auto(self):
        rec=self.driver.record('wanted')
        rec.update(phase='running',last_progress=time.time()-61,deadline_at=time.time()+1000)
        self.driver.scene_transition=Mock(return_value=False)
        self.driver.wanted_task_present=Mock(return_value=False)
        with self.assertRaisesRegex(hs.PendingError,'60秒'): self.driver.tick('wanted')
        self.s.click_template.assert_not_called()
        self.assertEqual(rec['phase'],'running')

    def test_solo_warning_never_confirms_or_launches(self):
        for asset in ('group_solo_warning_current.bmp','wanted_solo_warning_current.bmp'):
            self.frame = {asset}
            with self.assertRaisesRegex(hs.PendingError,'整体待续'):
                self.driver.tick('group' if asset.startswith('group') else 'wanted')
        self.s.click_template.assert_not_called()

    def test_pending_button_is_observed_without_resend_after_restart(self):
        rec = self.driver.record('group')
        self.frame = {'team_instance_hard_card.bmp'}
        self.driver.send_once(rec,'team_instance_hard_card.bmp',(550,300,950,650),200,130,'group_1_select')
        self.assertEqual(len(self.s.click_template.call_args_list),1)
        restarted = pipeline.LeaderActivityRunner(self.owner,self.driver.account,self.slot)
        restarted.visible = self.driver.visible
        restarted.tick('group')
        self.assertEqual(len(self.s.click_template.call_args_list),1)
        self.assertEqual(rec['completed'],0)

    def refused_entry(self):
        rec = self.driver.record('group')
        rec.update(completed=3,phase='running',daily_before=2,friend_before=0,reward_before=2)
        self.driver.send_once(rec,'team_instance_hard_card.bmp',(550,300,950,650),200,130,'group_4_select')
        self.s.click_template.reset_mock()
        self.frame = {'group_solo_warning_current.bmp','group_solo_cancel_current.bmp'}
        self.s.wait_absent.return_value = True
        return rec

    def test_refused_entry_cancels_only_then_reconciles_unchanged_counters_without_a_battle(self):
        rec = self.refused_entry()
        self.driver.tick('group')
        self.assertEqual(rec['phase'],'rejected_entry')
        self.assertEqual(self.s.click_template.call_args.args[1],'group_solo_cancel_current.bmp')
        self.assertEqual(self.actions['pipeline_original_group_4_select'],'pending')
        self.frame = {'group_title_current.bmp'}
        self.driver.group_rewards = lambda frame: (2,0)
        self.driver.tick('group');self.driver.tick('group')
        self.assertEqual(self.actions['pipeline_original_group_4_select'],'pending')
        self.driver.tick('group')
        self.assertEqual(rec['completed'],3)
        self.assertEqual(rec['phase'],'verify_members')
        self.assertNotIn('intent',rec)
        self.assertEqual(rec['entry_rejections'][0]['outcome'],'rejected_without_battle')
        self.assertEqual(self.actions['pipeline_original_group_4_select'],'done')
        self.assertEqual(self.s.click_template.call_count,1)

    def test_refused_entry_changed_counter_keeps_original_intent_pending(self):
        rec = self.refused_entry();self.driver.tick('group')
        self.frame = {'group_title_current.bmp'}
        self.driver.group_rewards = lambda frame: (1,0)
        with self.assertRaisesRegex(hs.PendingError,'次数变化'):
            self.driver.tick('group')
        self.assertEqual(self.actions['pipeline_original_group_4_select'],'pending')
        self.assertEqual(rec['completed'],3)
        self.assertIn('intent',rec)

    def test_verified_rejected_entry_uses_new_attempt_only_after_original_member_check(self):
        rec = self.refused_entry();self.driver.tick('group')
        self.frame = {'group_title_current.bmp'}
        self.driver.group_rewards = lambda frame: (2,0)
        for _ in range(3):self.driver.tick('group')
        self.driver.close_page = Mock()
        self.driver.tick('group')
        self.driver.close_page.assert_called_once_with('group')
        self.frame = set()
        self.owner.snapshot = Mock(return_value=(('captainA','memberA'),'original-team.png'))
        self.driver.tick('group')
        self.assertEqual(rec['phase'],'ready')
        self.frame = {'group_title_current.bmp'}
        self.driver.tick('group')
        self.assertEqual(rec['intent']['label'],'group_4_select_retry1')
        self.assertEqual(rec['completed'],3)

    def test_original_members_can_reorder_after_relogin_but_replacement_or_duplicate_blocks(self):
        rec=self.driver.record('group')
        rec.update(phase='verify_members')
        self.owner.snapshot=Mock(return_value=(('memberA','captainA'),'rejoined.png'))
        self.driver.tick('group')
        self.assertEqual(rec['phase'],'ready')
        for members in (('captainA','stranger'),('captainA','captainA')):
            rec['phase']='verify_members';self.owner.snapshot.return_value=(members,'changed.png')
            with self.assertRaisesRegex(hs.PendingError,'成员改变'):self.driver.tick('group')

    def test_button_disappearance_commits_intent_without_counting_a_battle(self):
        rec = self.driver.record('group')
        self.driver.send_once(rec,'button',(0,0,1,1),0,0,'group_1_select')
        self.driver.tick('group')
        self.assertEqual(rec['phase'],'running')
        self.assertEqual(rec['completed'],0)
        self.assertNotIn('intent',rec)
        self.assertEqual(self.actions['pipeline_original_group_1_select'],'done')

    def test_saved_intent_without_send_receipt_cannot_invent_completion(self):
        rec=self.driver.record('group')
        rec['intent']=dict(asset='button',roi=[0,0,1,1],label='group_1_select',deadline=time.time()+12)
        with self.assertRaisesRegex(hs.PendingError,'没有发送凭据'):
            self.driver.tick('group')
        self.s.click_template.assert_not_called()
        self.s.commit_action.assert_not_called()
        self.assertIn('intent',rec)

    def test_expired_unknown_button_stays_pending_without_repeat(self):
        rec = self.driver.record('group')
        self.driver.send_once(rec,'button',(0,0,1,1),0,0,'group_1_select')
        self.frame = {'button'}
        rec['intent']['deadline'] = time.time()-1
        with self.assertRaises(hs.PendingError):
            self.driver.tick('group')
        self.assertEqual(self.actions['pipeline_original_group_1_select'],'pending')
        self.assertEqual(self.s.click_template.call_count,1)

    def test_group_counter_needs_three_consistent_frames_and_rechecks_team(self):
        rec = self.driver.record('group')
        rec.update(phase='running',reward_before=3,last_progress=time.time())
        self.frame = {'group_title_current.bmp'}
        self.s._read_team_reward_remaining.return_value = 2
        self.driver.tick('group'); self.driver.tick('group')
        self.assertEqual(rec['completed'],0)
        self.driver.tick('group')
        self.assertEqual(rec['completed'],1)
        self.assertEqual(rec['phase'],'verify_members')
        self.s.click_template.assert_not_called()

    def test_friendship_decrement_counts_a_battle_even_when_daily_count_is_unchanged(self):
        rec=self.driver.record('group')
        rec.update(phase='running',reward_before=10,daily_before=5,friend_before=5,
                   counter_scope='daily_and_friendship',last_progress=time.time())
        self.frame={'group_title_current.bmp'}
        self.driver.group_rewards=lambda frame:(5,4)
        self.driver.tick('group');self.driver.tick('group');self.driver.tick('group')
        self.assertEqual(rec['completed'],1)
        self.assertEqual(rec['phase'],'verify_members')
        self.s.click_template.assert_not_called()

    def test_zero_daily_with_remaining_friendship_does_not_finish_group(self):
        rec=self.driver.record('group')
        self.frame={'group_title_current.bmp'}
        self.driver.group_rewards=lambda frame:(0,2)
        self.driver.tick('group')
        self.assertEqual(rec['phase'],'running')
        self.assertEqual(rec['reward_before'],2)
        self.assertNotIn('zero_evidence',rec)

    def test_both_group_reward_pools_need_three_zero_frames(self):
        rec=self.driver.record('group')
        self.frame={'group_title_current.bmp'}
        self.driver.group_rewards=lambda frame:(0,0)
        self.driver.tick('group');self.driver.tick('group')
        self.assertNotEqual(rec['phase'],'finishing')
        self.driver.tick('group')
        self.assertEqual(rec['phase'],'finishing')
        self.assertEqual((rec['daily_remaining'],rec['friendship_remaining']),(0,0))

    def test_unchanged_or_unexpected_counter_does_not_count(self):
        rec = self.driver.record('group')
        rec.update(phase='running',reward_before=3,last_progress=time.time())
        self.frame = {'group_title_current.bmp'}
        for remaining in (3,1,0):
            self.s._read_team_reward_remaining.return_value = remaining
            for _ in range(3): self.driver.tick('group')
        self.assertEqual(rec['completed'],0)
        self.s.click_template.assert_not_called()

    def test_zero_skips_new_battle_and_requires_finish_proof(self):
        self.frame = {'group_title_current.bmp'}
        self.s._read_team_reward_remaining.return_value = 0
        self.driver.tick('group')
        rec = self.driver.record('group')
        self.assertEqual(rec['phase'],'ready')
        self.driver.tick('group');self.driver.tick('group')
        self.assertEqual(rec['phase'],'finishing')
        self.assertNotEqual(rec['status'],'done')
        self.s.click_template.assert_not_called()

    def test_wanted_requires_five_empty_observations(self):
        rec = self.driver.record('wanted')
        rec.update(phase='running',deadline_at=time.time()+100)
        self.s.main_ready.return_value = True
        with patch.object(pipeline.hs,'wanted_task_list_is_empty',return_value=True):
            for _ in range(4): self.driver.tick('wanted')
            self.assertEqual(rec['phase'],'running')
            self.driver.tick('wanted')
        self.assertEqual(rec['phase'],'finishing')
        self.s.click_template.assert_not_called()

    def test_pending_button_keeps_fast_observation_after_a_long_battle_poll(self):
        rec=self.driver.record('wanted')
        rec.update(phase='running',deadline_at=time.time()+1200)
        self.driver.tick('wanted')
        self.assertEqual(self.driver.next_poll_seconds,10)
        self.frame={'battle_auto_off.bmp'}
        self.driver.tick('wanted')
        self.assertEqual(self.driver.next_poll_seconds,.6)
        deadline=rec['intent']['deadline']
        self.driver.tick('wanted')
        self.assertEqual(self.driver.next_poll_seconds,.6)
        self.assertEqual(rec['intent']['deadline'],deadline)
        self.s.click_template.assert_called_once()

    def test_wanted_original_deadline_is_not_reset_by_polling(self):
        rec = self.driver.record('wanted')
        rec.update(phase='running',deadline_at=time.time()-1)
        original = rec['deadline_at']
        with self.assertRaisesRegex(hs.PendingError,'1200'):
            self.driver.tick('wanted')
        self.assertEqual(rec['deadline_at'],original)
        self.s.click_template.assert_not_called()

    def test_wanted_auto_can_be_enabled_in_later_battle_without_repeating_old_intent(self):
        rec=self.driver.record('wanted')
        rec.update(phase='running',deadline_at=time.time()+1200)
        self.frame={'battle_auto_off.bmp'}
        self.driver.tick('wanted')
        self.driver.tick('wanted')
        self.assertEqual(self.s.click_template.call_count,1)
        self.frame=set()
        self.driver.tick('wanted')
        self.frame={'battle_auto_off.bmp'}
        self.driver.tick('wanted')
        self.assertEqual(self.s.click_template.call_count,2)
        self.assertEqual(self.actions['pipeline_original_wanted_battle_auto_1'],'done')
        self.assertEqual(self.actions['pipeline_original_wanted_battle_auto_2'],'pending')
        self.assertEqual(rec['auto_sequence'],2)

    def test_wanted_expired_deadline_never_enables_another_battle(self):
        rec=self.driver.record('wanted')
        rec.update(phase='running',deadline_at=time.time()-1)
        self.frame={'battle_auto_off.bmp'}
        with self.assertRaisesRegex(hs.PendingError,'截止已到'):
            self.driver.tick('wanted')
        self.s.click_template.assert_not_called()

    def test_old_wanted_navigation_requires_three_positive_page_frames_and_does_not_count_combat(self):
        self.slot['legacy_navigation']=['team2_wanted_participate']
        self.actions['team2_wanted_participate']='pending'
        self.frame={'wanted_team_anchor.bmp'}
        self.driver.tick('wanted');self.driver.tick('wanted')
        self.assertEqual(self.actions['team2_wanted_participate'],'pending')
        self.s.click_template.assert_not_called()
        self.driver.tick('wanted')
        self.assertEqual(self.actions['team2_wanted_participate'],'done')
        self.assertEqual(self.driver.record('wanted')['completed'],0)
        self.s.click_template.assert_not_called()

    def test_loading_between_navigation_observation_and_guard_is_waited_without_input(self):
        rec = self.driver.record('wanted')
        rec['navigation'] = dict(phase='open', started_at=time.time(), scrolls=0)
        original = dict(rec['navigation'])
        self.s.main_ready.return_value = True
        self.s.content_click_count = 0
        self.s._tab_title_masks = {1: np.ones((4, 8), dtype=np.uint8)}
        self.s._title_mask.return_value = np.ones((4, 8), dtype=np.uint8)
        self.s._generic_active_tab_x.return_value = self.driver.account.tab_x
        self.driver.scene_transition = Mock(side_effect=[False, True])
        self.driver.send = Mock(side_effect=hs.SafetyError('identity and page-anchor guard failed: loading'))
        self.driver.navigate('wanted', rec, self.frame)
        self.assertEqual(original, rec['navigation'])
        self.assertEqual(3, self.driver.next_poll_seconds)
        self.s.click_template.assert_not_called()
        self.s.commit_action.assert_not_called()

    def test_navigation_loading_cannot_swallow_focus_failure_or_a_sent_click(self):
        for message, count in (('game lost foreground', 0),
                               ('identity and page-anchor guard failed: loading', 1)):
            with self.subTest(message=message, count=count):
                rec = self.driver.record('wanted')
                self.s.main_ready.return_value = True
                self.s.content_click_count = 0
                self.driver.scene_transition = Mock(return_value=False)
                def fail(*args, **kwargs):
                    self.s.content_click_count = count
                    raise hs.SafetyError(message)
                self.driver.send = Mock(side_effect=fail)
                with self.assertRaises(hs.SafetyError):
                    self.driver.navigate('wanted', rec, self.frame)

    def test_existing_wanted_movement_resumes_observation_without_new_auto_or_clock_reset(self):
        rec=self.driver.record('wanted')
        original=time.time()-250
        rec.update(last_progress=original,navigation=dict(phase='open',started_at=original+20,scrolls=0))
        self.s.main_ready.return_value=True
        self.frame={'treasure_auto_path_running.bmp','wanted_task_prefix_live.bmp'}
        self.driver.tick('wanted')
        self.assertEqual(rec['phase'],'running')
        self.assertEqual(rec['deadline_at'],original+1200)
        self.assertNotIn('navigation',rec)
        self.assertEqual(rec['completed'],0)
        with patch.object(pipeline.hs,'wanted_task_list_is_empty',return_value=False):
            self.driver.tick('wanted')
        self.assertEqual(rec['deadline_at'],original+1200)
        self.s.click_template.assert_not_called()
        self.s.commit_action.assert_not_called()

    def test_wanted_task_with_unverified_identity_is_not_adopted(self):
        self.frame={'treasure_auto_path_running.bmp','wanted_task_prefix_live.bmp'}
        self.s.main_ready.return_value=True
        self.s.verify_identity.return_value=(False,{})
        self.assertFalse(self.driver.observe_existing_wanted(self.driver.record('wanted'),self.frame))
        self.s.click_template.assert_not_called()

    def test_existing_wanted_arrival_needs_three_catalogue_frames_and_preserves_deadline(self):
        rec=self.driver.record('wanted')
        deadline=time.time()+600
        rec.update(phase='running',observed_existing_chain=True,deadline_at=deadline)
        self.frame={'wanted_team_anchor.bmp','wanted_team_close.bmp'}
        self.driver.tick('wanted');self.driver.tick('wanted')
        self.assertEqual(rec['phase'],'running')
        self.driver.tick('wanted')
        self.assertEqual(rec['phase'],'ready')
        self.assertEqual(rec['completed'],0)
        self.s.click_template.assert_not_called()
        self.driver.tick('wanted')
        self.assertEqual(rec['deadline_at'],deadline)
        self.assertEqual(self.s.click_template.call_count,1)

    def test_expired_observed_wanted_at_catalogue_does_not_restart_auto(self):
        rec=self.driver.record('wanted')
        deadline=time.time()-1
        rec.update(phase='ready',observed_existing_chain=False,deadline_at=deadline)
        self.frame={'wanted_team_anchor.bmp','wanted_team_close.bmp'}
        with self.assertRaisesRegex(hs.PendingError,'不重新启动'):
            self.driver.tick('wanted')
        self.assertEqual(rec['deadline_at'],deadline)
        self.s.click_template.assert_not_called()

    def test_loading_and_existing_path_never_open_daily_or_repeat_route(self):
        rec=self.driver.record('wanted')
        rec['navigation']=dict(phase='open',started_at=time.time(),scrolls=0)
        for assets in ({'map_loading_word_current.bmp','map_loading_finished_current.bmp'},
                       {'treasure_auto_path_running.bmp','wanted_available_auto_current.bmp'}):
            self.frame=assets
            self.driver.navigate('wanted',rec,self.frame)
        self.s.click_template.assert_not_called()
        self.frame={'wanted_available_auto_current.bmp'}
        self.driver.navigate('wanted',rec,self.frame)
        self.driver.navigate('wanted',rec,self.frame)
        self.assertEqual(self.s.click_template.call_count,1)
        self.assertEqual(rec['navigation']['phase'],'arriving')

    def test_wanted_prefix_or_movement_prevents_false_empty_completion(self):
        rec=self.driver.record('wanted');rec.update(phase='running',deadline_at=time.time()+1200)
        self.s.main_ready.return_value=True
        with patch.object(pipeline.hs,'wanted_task_list_is_empty',return_value=True):
            for asset in ('wanted_task_prefix_live.bmp','treasure_auto_path_running.bmp'):
                self.frame={asset}
                for _ in range(5): self.driver.tick('wanted')
        self.assertEqual(rec['phase'],'running')
        self.assertNotIn('empty_evidence',rec)
        self.s.click_template.assert_not_called()

    def test_completed_wanted_row_needs_three_frames_and_then_five_empty_main_frames(self):
        rec=self.driver.record('wanted')
        self.actions['team2_wanted_participate']='pending'
        self.frame={'daily_practice_anchor.bmp','daily_practice_close.bmp','daily_wanted_completed_zero_current.bmp'}
        self.driver.tick('wanted');self.driver.tick('wanted')
        self.assertEqual(rec['phase'],'ready')
        self.s.click_template.assert_not_called()
        self.driver.tick('wanted')
        self.assertEqual(rec['phase'],'checking_empty')
        self.assertEqual(rec['completed'],0)
        self.assertEqual(self.actions['team2_wanted_participate'],'pending')
        self.driver.tick('wanted')
        self.assertEqual(self.s.click_template.call_args.args[1],'daily_practice_close.bmp')
        self.frame=set();self.s.main_ready.return_value=True
        with patch.object(pipeline.hs,'wanted_task_list_is_empty',return_value=True):
            for _ in range(4): self.driver.tick('wanted')
            self.assertEqual(rec['phase'],'checking_empty')
            self.driver.tick('wanted')
        self.assertEqual(rec['phase'],'finishing')
        self.assertEqual(rec['completed'],1)
        self.assertEqual(self.s.click_template.call_count,1)
        self.assertEqual(self.actions['team2_wanted_participate'],'pending')
        self.s.commit_action.assert_not_called()

    def test_completed_wanted_row_cannot_bypass_identity(self):
        self.frame={'daily_practice_anchor.bmp','daily_practice_close.bmp','daily_wanted_completed_zero_current.bmp'}
        self.s.verify_identity.return_value=(False,{})
        with self.assertRaisesRegex(hs.PendingError,'实名未确认'):
            self.driver.tick('wanted')
        self.s.click_template.assert_not_called()

    def test_repeated_daily_row_selection_stops_without_starting_any_battle(self):
        nav = {'phase':'open','started_at':time.time(),'scrolls':0}
        self.frame = {'daily_practice_anchor.bmp','daily_wanted_current_row.bmp'}
        for _ in range(3):
            self.driver.select_daily_row(nav,'daily_wanted_current_row.bmp','test_entry')
        with self.assertRaisesRegex(hs.PendingError,'不重复点选'):
            self.driver.select_daily_row(nav,'daily_wanted_current_row.bmp','test_entry')
        self.assertEqual(self.s.click_template.call_count,3)
        self.assertEqual(nav['row_selection_count'],3)
        self.assertTrue(all(not call.kwargs.get('irreversible') for call in self.s.click_template.call_args_list))

    def test_actual_dynamic_daily_header_verifies_zero_without_the_old_full_title_match(self):
        frame=Image.new('RGB',(1920,1032))
        frame.paste(Image.open(hs.ROOT/'tests/fixtures/team_daily_dynamic_title_current.png'),(1050,140))
        frame.paste(Image.open(hs.ROOT/'tests/fixtures/team_wanted_completed_zero_panel.png'),(500,310))
        store=hs.TemplateStore(.98)
        self.assertFalse(store.match(frame,hs.ASSETS/'daily_practice_anchor.bmp',pipeline.DAILY_ROI,.98)[0])
        self.driver.visible=lambda asset,roi=hs.VIEW,frame=None,threshold=.92:store.match(
            self.frame if frame is None else frame,hs.ASSETS/asset,roi,threshold)[0]
        self.frame=frame
        self.driver.tick('wanted');self.driver.tick('wanted');self.driver.tick('wanted')
        rec=self.driver.record('wanted')
        self.assertEqual(rec['phase'],'checking_empty')
        self.assertEqual(rec['daily_remaining'],0)
        self.s.click_template.assert_not_called()

    def test_new_or_unidentified_wanted_entry_is_never_classified_as_old_navigation(self):
        for key,row in [('team2_wanted_auto',{'updated_at':'2026-08-21','origin':{'token':'old'}}),
                        ('team2_wanted_participate',{'updated_at':'2026-10-06','origin':{'token':'old'}}),
                        ('team2_wanted_participate',{'updated_at':'2026-08-21'})]:
            self.assertFalse(pipeline.legacy_wanted_navigation(key,row,'2026-10-06'))

    def test_arrival_poll_yields_and_does_not_repeat_participation(self):
        rec = self.driver.record('group')
        rec['navigation'] = {'phase':'arriving','started_at':time.time(),'scrolls':0}
        for _ in range(3): self.driver.tick('group')
        self.s.click_template.assert_not_called()
        self.frame = {'group_title_current.bmp'}
        self.driver.tick('group')
        self.assertNotIn('navigation',rec)
        self.assertEqual(rec['phase'],'ready')
        self.s.click_template.assert_not_called()

    def test_unknown_entry_transition_waits_without_clicking_or_extending_deadline(self):
        rec=self.driver.record('group')
        original=time.time()
        rec['navigation']={'phase':'open','started_at':original,'scrolls':0}
        self.driver.tick('group');self.driver.tick('group')
        self.s.click_template.assert_not_called()
        self.assertEqual(rec['navigation']['started_at'],original)
        rec['navigation']['started_at']=time.time()-61
        with self.assertRaisesRegex(hs.PendingError,'60秒'):
            self.driver.tick('group')

    def test_navigation_deferral_does_not_renew_the_next_period_without_progress(self):
        rec=self.driver.record('group')
        rec['navigation']=dict(phase='open',started_at=0,last_progress_at=0,scrolls=0,deferred_seconds=70)
        with patch.object(pipeline.time,'time',return_value=70):self.driver.navigate('group',rec,self.frame)
        self.s.click_template.assert_not_called()
        rec['navigation'].update(last_progress_at=70,deferred_at_progress=70)
        with patch.object(pipeline.time,'time',return_value=130):
            with self.assertRaisesRegex(hs.PendingError,'60秒'):self.driver.navigate('group',rec,self.frame)

    def test_changed_members_block_next_attempt(self):
        rec = self.driver.record('group')
        rec['phase'] = 'verify_members'
        self.owner.snapshot = Mock(return_value=(('captainA','replacement'),'room.png'))
        with self.assertRaisesRegex(hs.PendingError,'成员改变'):
            self.driver.tick('group')
        self.s.click_template.assert_not_called()

    def test_missing_scroll_arrow_never_consumes_scroll_budget(self):
        self.frame = {'daily_practice_anchor.bmp', 'daily_practice_close.bmp', 'daily_normal_tab_selected.bmp'}
        rec = self.driver.record('wanted')
        with patch.object(pipeline, 'daily_page_present', return_value=True), \
             patch.object(pipeline, 'daily_list_signature', return_value='same'):
            for _ in range(30):
                self.driver.navigate('wanted', rec, self.frame)
        self.assertEqual(rec['navigation']['scrolls'], 0)
        self.assertEqual(rec['navigation'].get('scroll_attempts', 0), 0)
        self.s.click_template.assert_not_called()

    def test_scroll_counts_only_changed_rows_and_observed_boundaries(self):
        self.frame = {'daily_practice_anchor.bmp', 'daily_practice_close.bmp', 'daily_normal_tab_selected.bmp',
                      'daily_scroll_up_current.bmp', 'daily_scroll_down_current.bmp'}
        rec = self.driver.record('wanted')
        with patch.object(pipeline, 'daily_page_present', return_value=True), \
             patch.object(pipeline, 'daily_list_signature', side_effect=['middle', 'top', 'top', 'top', 'bottom', 'bottom', 'bottom']):
            self.driver.navigate('wanted', rec, self.frame)
            self.assertEqual(rec['navigation']['scrolls'], 0)
            self.driver.navigate('wanted', rec, self.frame)
            self.assertEqual(rec['navigation']['scrolls'], 1)
            self.driver.navigate('wanted', rec, self.frame)
            self.driver.navigate('wanted', rec, self.frame)
            self.assertEqual(rec['navigation']['scroll_direction'], 'down')
            self.driver.navigate('wanted', rec, self.frame)
            self.assertEqual(rec['navigation']['scrolls'], 2)
            self.driver.navigate('wanted', rec, self.frame)
            with self.assertRaisesRegex(hs.PendingError, '扫描至底'):
                self.driver.navigate('wanted', rec, self.frame)
        self.assertEqual([call.args[1] for call in self.s.click_template.call_args_list],
                         ['daily_scroll_up_current.bmp'] * 2 + ['daily_scroll_down_current.bmp'] * 2)


class CoordinationTests(unittest.TestCase):
    def test_other_team_foreground_work_is_excluded_only_from_unsent_navigation(self):
        owner=coordinator();slot=owner.record['teams']['4']
        slot['tasks']={
            'group':dict(phase='ready',completed=10,navigation=dict(phase='open',started_at=123,last_progress_at=125)),
            'wanted':dict(phase='running',deadline_at=456,navigation=dict(phase='arriving',started_at=127))}
        owner.scheduler.state.action_status=lambda account,label:'new'
        owner.defer_navigation(1,80)
        self.assertEqual(slot['tasks']['group']['navigation'],dict(
            phase='open',started_at=123,last_progress_at=125,deferred_seconds=80))
        self.assertEqual(slot['tasks']['wanted']['deadline_at'],456)
        self.assertNotIn('deferred_seconds',slot['tasks']['wanted']['navigation'])

    def test_sent_navigation_intent_receives_no_deferral(self):
        owner=coordinator();slot=owner.record['teams']['4']
        slot['tasks']={'wanted':dict(phase='ready',completed=0,navigation=dict(phase='open',started_at=123))}
        owner.scheduler.state.action_status=lambda account,label:'pending'
        owner.defer_navigation(1,80)
        self.assertNotIn('deferred_seconds',slot['tasks']['wanted']['navigation'])
        owner.save.assert_not_called()

    def test_slow_dissolution_does_not_time_out_the_other_teams_unsent_entry(self):
        owner=coordinator();owner.tasks=('wanted',)
        owner.record['teams']['1']['tasks']={'wanted':{'status':'done'}}
        slot=owner.record['teams']['4']
        slot['tasks']={'wanted':dict(status='new',phase='ready',completed=0,deadline_at=999,
            navigation=dict(phase='open',started_at=1,last_progress_at=1,scrolls=0))}
        owner.scheduler.state.action_status=lambda account,label:'new'
        owner.scheduler.main_ready.return_value=False
        driver=pipeline.LeaderActivityRunner(owner,owner.scheduler.accounts[3],slot)
        driver.visible=lambda *args,**kw:False
        def tick(task):
            driver.navigate(task,slot['tasks'][task],'waiting frame')
            slot['tasks'][task]['status']='done'
        owner.drivers[4]=SimpleNamespace(account=driver.account,tick=tick,next_poll_seconds=.6)
        def dissolve(index,slot):
            if index==1:owner.scheduler.wait_interruptible(80)
            slot['status']='done'
        owner.dissolve=Mock(side_effect=dissolve)
        with patch.object(pipeline.time,'time',side_effect=lambda:1+owner._poll_clock()):owner.run_teams()
        self.assertEqual(slot['status'],'done')
        self.assertEqual(slot['tasks']['wanted']['deadline_at'],999)
        self.assertEqual(slot['tasks']['wanted']['navigation']['started_at'],1)
        self.assertEqual(slot['tasks']['wanted']['navigation']['deferred_seconds'],80)

    def test_teams_interleave_instead_of_finishing_one_first(self):
        owner = coordinator(); trace=[]
        owner.dissolve=Mock(side_effect=lambda index,slot:slot.update(status='done'))
        for index in owner.leaders:
            slot = owner.record['teams'][str(index)]
            def tick(task,index=index,slot=slot):
                trace.append((index,task)); slot['tasks'][task]={'status':'done'}
            owner.drivers[index] = SimpleNamespace(account=owner.scheduler.accounts[index-1],tick=tick,
                record=lambda task,slot=slot: slot['tasks'].setdefault(task,{'status':'new'}))
        owner.run_teams()
        self.assertEqual(trace,[(1,'group'),(4,'group'),(1,'wanted'),(4,'wanted')])
        self.assertEqual([call.args[0] for call in owner.dissolve.call_args_list],[1,4])

    def test_offline_team_does_not_block_other_team(self):
        owner=coordinator(); trace=[]
        owner.dissolve=Mock(side_effect=lambda index,slot:slot.update(status='done'))
        owner.drivers[1]=SimpleNamespace(account=owner.scheduler.accounts[0],
            record=lambda t:{'status':'new'},tick=Mock(side_effect=hs.AccountOffline(owner.scheduler.accounts[0])))
        slot=owner.record['teams']['4']
        def tick(task):
            trace.append(task);slot['tasks'][task]={'status':'done'}
        owner.drivers[4]=SimpleNamespace(account=owner.scheduler.accounts[3],tick=tick,
            record=lambda t:slot['tasks'].setdefault(t,{'status':'new'}))
        owner.run_teams()
        self.assertEqual(trace,['group','wanted'])
        self.assertEqual(owner.record['teams']['1']['status'],'pending')
        self.assertEqual(slot['status'],'done')

    def test_finished_team_dissolves_before_slow_team_and_is_never_polled_again(self):
        owner=coordinator();trace=[]
        owner.tasks=('wanted',)
        for index in owner.leaders:
            slot=owner.record['teams'][str(index)]
            slot['tasks']['wanted']={'status':'new','deadline_at':123456789}
            driver=SimpleNamespace(account=owner.scheduler.accounts[index-1],next_poll_seconds=10,ticks=0)
            def tick(task,index=index,slot=slot,driver=driver):
                trace.append((index,owner._poll_clock()))
                driver.ticks+=1
                if index==1 or driver.ticks==3:
                    slot['tasks'][task]['status']='done'
            driver.tick=tick;owner.drivers[index]=driver
        def dissolve(index,slot):
            trace.append(('dissolve',index));slot['status']='done'
        owner.dissolve=Mock(side_effect=dissolve)
        owner.run_teams()
        self.assertEqual(trace,[(1,0),('dissolve',1),(4,0),(4,10),(4,20),('dissolve',4)])
        self.assertEqual(owner.record['teams']['4']['tasks']['wanted']['deadline_at'],123456789)

    def test_ready_team_progresses_while_other_team_waits_for_its_poll(self):
        owner=coordinator();trace=[];owner.tasks=('wanted',)
        for index in owner.leaders:
            slot=owner.record['teams'][str(index)];slot['tasks']['wanted']={'status':'new'}
            driver=SimpleNamespace(account=owner.scheduler.accounts[index-1],next_poll_seconds=10 if index==1 else .6,ticks=0)
            def tick(task,index=index,slot=slot,driver=driver):
                trace.append((index,owner._poll_clock()));driver.ticks+=1
                if driver.ticks==2:slot['tasks'][task]['status']='done'
            driver.tick=tick;owner.drivers[index]=driver
        owner.dissolve=Mock(side_effect=lambda index,slot:slot.update(status='done'))
        owner.run_teams()
        self.assertEqual(trace,[(1,0),(4,0),(4,.6),(1,10)])

    def test_restored_completed_team_dissolves_without_a_driver_or_task_replay(self):
        owner=coordinator()
        owner.record['teams']['1'].update(status='complete',tasks={'group':{'status':'done'},'wanted':{'status':'done'}})
        owner.record['teams']['4']['status']='done'
        owner.dissolve=Mock(side_effect=lambda index,slot:slot.update(status='done'))
        owner.run_teams()
        owner.dissolve.assert_called_once_with(1,owner.record['teams']['1'])
        owner.scheduler.wait_interruptible.assert_not_called()
        owner.scheduler.switch_account.assert_not_called()

    def test_failed_dissolution_is_local_pending_and_never_retried_in_same_run(self):
        owner=coordinator()
        for slot in owner.record['teams'].values():
            slot.update(status='complete',tasks={'group':{'status':'done'},'wanted':{'status':'done'}})
        def dissolve(index,slot):
            if index==1:raise hs.PendingError('原解散结果未知')
            slot['status']='done'
        owner.dissolve=Mock(side_effect=dissolve)
        owner.run_teams()
        self.assertEqual([call.args[0] for call in owner.dissolve.call_args_list],[1,4])
        self.assertEqual(owner.record['teams']['1']['status'],'pending')
        self.assertEqual(owner.record['teams']['4']['status'],'done')

    def test_global_safety_and_stop_are_not_swallowed_as_local_pending(self):
        for error in (hs.SafetyError('window changed'),KeyboardInterrupt()):
            owner=coordinator()
            owner.drivers[1]=SimpleNamespace(account=owner.scheduler.accounts[0],record=lambda t:{'status':'new'},
                                             tick=Mock(side_effect=error))
            with self.assertRaises(type(error)): owner.run_teams()

    def test_pending_team_members_excluded_before_solo_actions(self):
        owner=coordinator()
        owner.record['teams']['1'].update(status='pending',members=['role1','role2'])
        owner.record['teams']['4'].update(status='done',members=['role4','role5'])
        s=Mock();s.accounts=owner.scheduler.accounts.copy()
        solo=SimpleNamespace(scheduler=s,startup_returned=set())
        with patch.object(pipeline,'verify_unteamed',return_value='create.png') as verify, \
             patch('hyol_navigation.return_to_leaf',return_value=True):
            owner.handoff(solo)
        self.assertEqual([a.index for a in s.accounts],[3,4,5,6])
        self.assertEqual({c.args[1].index for c in verify.call_args_list},{3,4,5,6})
        self.assertEqual(set(owner.solo_exclusions),{'role1','role2'})
        self.assertEqual(solo.startup_returned,{'role3','role4','role5','role6'})

    def test_solo_handoff_requires_village_proof_in_addition_to_empty_team(self):
        owner=coordinator();owner.record['teams']={}
        s=Mock();s.accounts=owner.scheduler.accounts[:2]
        solo=SimpleNamespace(scheduler=s,startup_returned=set())
        with patch.object(pipeline,'verify_unteamed',return_value='create.png'), \
             patch('hyol_navigation.return_to_leaf',side_effect=[False,True]):
            owner.handoff(solo)
        self.assertEqual([a.index for a in s.accounts],[2])
        self.assertEqual(solo.startup_returned,{'role2'})
        self.assertIn('role1',owner.solo_exclusions)

    def test_unconfirmed_solo_account_isolated_without_stopping_others(self):
        owner=coordinator()
        owner.record['teams']={}
        s=Mock();s.accounts=owner.scheduler.accounts[:2]
        with patch.object(pipeline,'verify_unteamed',side_effect=[hs.PendingError('still teamed'),'create.png']), \
             patch('hyol_navigation.return_to_leaf',return_value=True):
            owner.handoff(SimpleNamespace(scheduler=s,startup_returned=set()))
        self.assertEqual([a.index for a in s.accounts],[2])
        self.assertIn('role1',owner.solo_exclusions)

    def test_dissolve_pending_never_sends_again(self):
        owner=coordinator();s=owner.scheduler
        s.state.action_status=Mock(return_value='pending');s.state.commit_action=Mock()
        s.commit_action=Mock()
        slot=owner.record['teams']['1']
        slot['tasks']['group'] = dict(daily_remaining=0,friendship_remaining=0,
                                     reward_exhausted=True,zero_evidence='zero.png')
        with patch.object(pipeline,'verify_unteamed',return_value='create.png'), \
             patch('hyol_navigation.return_to_leaf',return_value=True):
            owner.dissolve(1,slot)
        s.click_template.assert_not_called()
        self.assertEqual(slot['status'],'done')
        self.assertEqual(slot['dissolve_evidence'],'create.png')





    def test_summary_exposes_captain_failure_in_user_result(self):
        owner=coordinator()
        owner.record['teams']['1'].update(status='pending',last_error='活动未开放')
        self.assertIn('标签1：活动未开放',owner.summary()['detail'])



    def test_completion_requires_both_task_and_dissolve_evidence(self):
        owner=coordinator()
        members = ['captainA','member1','captainB','member4']
        owner.record.update(phase='done',solo_eligible=members,solo_result=0,solo_exclusions={},
                            required_account_count=4,expected_accounts=members,
                            solo_records={name:dict(mail=dict(status='done',task_completed=True,home_returned=True))
                                          for name in members})
        for index, slot in owner.record['teams'].items():
            slot['members'] = ['captainA' if index == '1' else 'captainB', 'member'+index]
            slot.update(status='done',dissolve_evidence='create.png',home_returned=True,
                        tasks={key:dict(status='done',task_completed=True,home_returned=True,evidence='room.png',
                                        zero_evidence='zero.png',empty_evidence='empty.png',
                                        daily_remaining=0,friendship_remaining=0,reward_exhausted=True)
                               for key in pipeline.TASK_KEYS.values()})
        self.assertTrue(owner.completed())
        owner.record['teams']['1']['tasks']['group'].pop('evidence')
        self.assertFalse(owner.completed())
        owner.record['teams']['1']['tasks']['group']['evidence']='room.png'
        owner.record['teams']['4'].pop('dissolve_evidence')
        self.assertFalse(owner.completed())

    def test_summary_exposes_selected_solo_pending_without_changing_original_records(self):
        owner = coordinator()
        owner.record.update(phase='solo', solo_eligible=[ROLE_D], solo_exclusions={})
        owner.solo_tasks = ('mail','quiz')
        records = dict(mail=dict(status='pending',last_error='原领取结果尚未核实',origin={'token':'keep'}),
                       quiz=dict(status='done',task_completed=True,home_returned=True),
                       survival=dict(status='pending',last_error='未选择的旧任务'))
        owner.solo = SimpleNamespace(runners={ROLE_D:SimpleNamespace(records=records)})
        before = json.dumps(records,sort_keys=True)
        result = owner.summary()
        self.assertEqual(len(result['solo_pending']),1)
        self.assertEqual(result['solo_pending'][0]['account'],ROLE_D)
        self.assertIn('原领取结果尚未核实',result['detail'])
        self.assertNotIn('未选择的旧任务',result['detail'])
        self.assertEqual(json.dumps(records,sort_keys=True),before)

    def test_summary_keeps_unverified_home_and_followup_pending(self):
        owner = coordinator()
        owner.solo_tasks = ('quiz','survival')
        owner.solo = SimpleNamespace(runners={'role1':SimpleNamespace(records={
            'quiz':dict(status='done',task_completed=True,home_returned=False),
            'survival':dict(status='done',task_completed=True,home_returned=True,followup_check_due=True)})})
        result = owner.summary()
        self.assertEqual([row['task'] for row in result['solo_pending']],['quiz','survival'])
        self.assertIn('回村尚未确认',result['detail'])
        self.assertIn('生存补跑未结束',result['detail'])

    def test_empty_solo_handoff_does_not_seal_an_unfinished_request_as_done(self):
        from hyol_run_context import RunContext
        with tempfile.TemporaryDirectory() as folder:
            owner=coordinator()
            owner.root=Path(folder)
            owner.run_context=RunContext(owner.root,owner.root/'state')
            owner.run_context.begin=Mock()
            owner.scheduler.stop=owner.run_context.stop
            owner.record.update(source_day=owner.scheduler.state.day,execution_day=owner.scheduler.state.day)
            owner.initialize_run=Mock();owner.prepare_teams=Mock();owner.run_teams=Mock()
            owner.sink=None
            solo=Mock();solo.scheduler.state.day=owner.scheduler.state.day
            solo.run.return_value=0;solo.runners={};solo.initial_unavailable=set();solo.disconnected=set()
            with patch.object(pipeline.hs,'ForegroundRuntimeLease'),patch.object(pipeline,'MultiSoloRunner',return_value=solo):
                self.assertEqual(owner.run(),2)
            owner.scheduler=solo.scheduler
            self.assertNotEqual(owner.record['phase'],'done')








    def test_six_account_acceptance_rejects_missing_members_skips_or_unfinished_managed(self):
        owner = coordinator()
        owner.managed_enabled = True
        owner.record.update(phase='done', solo_result=0, solo_exclusions={}, required_team_size=3,
                            required_account_count=6, solo_eligible=[f'role{i}' for i in range(1,7)])
        for index, slot in owner.record['teams'].items():
            start = 1 if index == '1' else 4
            slot.update(status='done', members=[f'role{i}' for i in range(start,start+3)],
                        dissolve_evidence='create.png', home_returned=True,
                        tasks={key: dict(status='done', task_completed=True, home_returned=True,
                                         evidence='room.png', zero_evidence='zero.png', empty_evidence='empty.png',
                                         daily_remaining=0,friendship_remaining=0,reward_exhausted=True)
                               for key in pipeline.TASK_KEYS.values()})
        owner.record['solo_records'] = {f'role{i}': {
            'mail': dict(status='done', task_completed=True, home_returned=True),
            'managed': dict(status='done', task_completed=True, evidence='active.png')}
            for i in range(1,7)}
        self.assertTrue(owner.completed())
        owner.record['solo_initial_unavailable'] = ['role6']
        self.assertFalse(owner.completed())
        owner.record['solo_initial_unavailable'] = []
        owner.record['solo_eligible'].pop()
        self.assertFalse(owner.completed())
        owner.record['solo_eligible'].append('role6')
        owner.record['solo_records']['role6']['managed']['status'] = 'pending'
        self.assertFalse(owner.completed())


class ArchiveRecoveryTests(unittest.TestCase):
    def test_unreadable_old_jpeg_requires_its_exact_positive_identity_and_click_journal(self):
        owner=coordinator();s=owner.scheduler;s.hwnd=123
        slot=owner.record['teams']['1'];slot['owner']='captainA'
        driver=pipeline.LeaderActivityRunner(owner,s.accounts[0],slot)
        rec=driver.record('group');rec.update(phase='running',reward_before=5)
        action=driver.action('group_1_select')
        s.state.action_status=Mock(return_value='done')
        s.verify_identity=Mock(return_value=(False,{}))
        driver.group_rewards=Mock(return_value=(5,5))
        with tempfile.TemporaryDirectory() as folder,patch.object(hs,'LOGS',Path(folder)):
            directory=Path(folder)/'scheduler_evidence/2026-10-06'/hs.slug(driver.account.name)
            directory.mkdir(parents=True)
            image=directory/('080000_before_'+action+'.jpg');Image.new('RGB',(80,80)).save(image)
            with self.assertRaisesRegex(hs.PendingError,'身份未确认'):driver.recover_group_counter(rec)
            self.assertEqual(rec['reward_before'],5)
            journal=Path(folder)/'HYOL_Scheduler_20261006_original.jsonl'
            rows=[dict(event='generic_team_checkpoints_bound',mapping={'1':'captainA'}),
                  dict(event='content_click',target=action,account=driver.account.name,
                       tab_index=1,hwnd=123,evidence=str(image))]
            journal.write_text('\n'.join(json.dumps(row) for row in rows),encoding='utf-8')
            driver.recover_group_counter(rec)
            self.assertEqual((rec['reward_before'],rec['daily_before'],rec['friend_before']),(10,5,5))
            self.assertEqual(rec['completed'],0)
            s.click_template.assert_not_called();s.commit_action.assert_not_called()

    def test_other_captain_journal_cannot_associate_an_unknown_archive(self):
        owner=coordinator();s=owner.scheduler;s.hwnd=123
        slot=owner.record['teams']['1'];slot['owner']='captainA'
        driver=pipeline.LeaderActivityRunner(owner,s.accounts[0],slot)
        with tempfile.TemporaryDirectory() as folder,patch.object(hs,'LOGS',Path(folder)):
            image=Path(folder)/'before.jpg';Image.new('RGB',(80,80)).save(image)
            rows=[dict(event='generic_team_joint_identity_verified',mapping={'1':'captainB'}),
                  dict(event='content_click',target='start',account=driver.account.name,
                       tab_index=1,hwnd=123,evidence=str(image))]
            (Path(folder)/'HYOL_Scheduler_20261006_other.jsonl').write_text(
                '\n'.join(json.dumps(row) for row in rows),encoding='utf-8')
            self.assertFalse(driver.archived_click_owner(image,'start'))


class InterfaceTests(unittest.TestCase):

    def test_controller_readonly_scan_can_observe_portrait_with_other_panels_open(self):
        events=[]
        controller=runtime.RuntimeController(events.append,Path('unused'))
        with patch.object(runtime,'manual_pause_status',return_value={'paused':False}), \
             patch.object(runtime,'preflight_live',return_value=dict(status='ready',healthy=True,main_ready=False,content_clicks=0)), \
             patch.object(runtime,'detect_leaders',return_value=dict(leaders=[1,6],content_clicks=0)) as scan, \
             patch.object(runtime,'make_runner') as create:
            controller._detect_leaders()
        scan.assert_called_once()
        create.assert_not_called()
        self.assertEqual(next(e for e in events if e['event']=='unified_leaders_detected')['check']['leaders'],[1,6])
        self.assertEqual(events[-1]['event'],'unified_idle')

    def test_controller_unhealthy_preflight_does_not_scan(self):
        controller=runtime.RuntimeController(Mock(),Path('unused'))
        with patch.object(runtime,'manual_pause_status',return_value={'paused':False}), \
             patch.object(runtime,'preflight_live',return_value=dict(status='unhealthy',healthy=False,content_clicks=0)), \
             patch.object(runtime,'detect_leaders') as scan:
            controller._detect_leaders()
        scan.assert_not_called()

    def test_invalid_leaders_tasks_and_empty_solo_are_rejected(self):
        for extra in ({'leaders':(1,1)},{'leaders':(True,4)},
                      {'leaders':(1,7)},{'team_tasks':('rescue_jinchuriki',)},{'tasks':()}):
            options=dict(mode='team_then_multi',leaders=(1,4),team_tasks=pipeline.TEAM_TASKS,tasks=('mail',))
            options.update(extra)
            with self.assertRaises(ValueError): runtime.RunRequest(**options)

    def test_request_reaches_pipeline_and_preserves_final_managed(self):
        fake=SimpleNamespace(scheduler=SimpleNamespace(config={},allow_purchase=True,market_direct_buy_verified=True))
        request=runtime.RunRequest(mode='team_then_multi',leaders=(2,5),team_tasks=('wanted',),tasks=('mail',),
                                   final_managed=False,arena_wins=7)
        with patch.object(runtime,'TeamPipelineRunner',return_value=fake) as make:
            runtime.make_runner(request,Mock(),Path('unused'))
        self.assertEqual(make.call_args.args,((2,5),('wanted',),('mail',)))
        self.assertEqual(make.call_args.kwargs['arena_wins'],7)
        self.assertFalse(make.call_args.kwargs['managed_enabled'])
        self.assertIsNotNone(make.call_args.kwargs['run_context'])
        self.assertFalse(fake.scheduler.allow_purchase)

    def test_real_ui_requires_leaders_shares_solo_options_and_uses_all_accounts(self):
        app=UnifiedClient(root=Path(tempfile.gettempdir())/'pipeline-ui-unused')
        try:
            app.withdraw();app.tabs.select(1);app.team_mode.set('已有队伍 → 多人单人');app._team_mode_changed()
            app.pipeline_leaders[0].set('');app.pipeline_leaders[1].set('')
            self.assertEqual(app.request().leaders,())
            app.pipeline_auto.set(False)
            with self.assertRaises(ValueError): app.request()
            app.pipeline_leaders[0].set('2');app.pipeline_leaders[1].set('5')
            app.account_count.set('1');app.task_vars['treasure'].set(False)
            app.task_vars['mail'].set(True);app.final_managed.set(False)
            request=app.request()
            self.assertEqual(request.mode,'team_then_multi')
            self.assertEqual(request.leaders,(2,5));self.assertEqual(request.account_count,6)
            self.assertEqual(request.team_tasks,pipeline.TEAM_TASKS)
            self.assertFalse(request.final_managed);self.assertNotIn('treasure',request.tasks)
            self.assertIsNone(app.controller.worker)
            with patch.object(app.controller,'start',return_value=True) as start:
                app._start()
            app.team_mode.set('自动建队');app._team_mode_changed()
            self.assertEqual(app.request().mode,'team')
        finally: app.destroy()

    def test_team_desktop_entry_uses_same_ui_and_opens_team_tab(self):
        import hyol_team_tasks
        app=Mock()
        with patch.object(hyol_team_tasks,'UnifiedClient',return_value=app):
            self.assertEqual(hyol_team_tasks.main(),0)
        app.tabs.select.assert_called_once_with(1)
        app.mainloop.assert_called_once()




class InterferenceTests(unittest.TestCase):
    def scheduler(self):
        s=object.__new__(pipeline.PipelineScheduler)
        s.no_content_clicks=False
        s.hwnd=None
        s.frame=Image.new('RGB',(1920,1032))
        s.frame.paste(Image.open(hs.ROOT/'tests/fixtures/pipeline_spirit_reward_shell_current.png'),(940,408))
        store=hs.TemplateStore(.98)
        s.template=lambda asset,roi,frame=None,threshold=.98:store.match(
            s.frame if frame is None else frame,hs.ASSETS/asset,roi,threshold)
        s.capture=Mock(side_effect=lambda:s.frame)
        s.check_stop=Mock();s.wait_interruptible=Mock()
        s.save_raw_evidence=Mock(return_value='interference.png')
        s.wait_absent=Mock(return_value=True)
        s.event=Mock()
        def close(*args,**kwargs):
            s.frame=Image.new('RGB',(1920,1032))
            return True
        s.click_template=Mock(side_effect=close)
        return s

    def test_known_interference_closes_only_x_and_records_separate_evidence(self):
        s=self.scheduler()
        result=s.clear_pipeline_interference(hs.Account(1,'captain',180),s.frame)
        self.assertIs(result,s.frame)
        self.assertEqual(s.capture.call_count,4)
        self.assertEqual(s.click_template.call_args.args[1],'pipeline_spirit_reward_close_current.bmp')
        self.assertNotIn('irreversible',s.click_template.call_args.kwargs)
        self.assertEqual(s.event.call_args.args[0],'pipeline_interference_closed')

    def test_readonly_scan_never_dismisses_an_interference(self):
        s=self.scheduler();s.no_content_clicks=True
        self.assertIs(s.clear_pipeline_interference(hs.Account(1,'captain',180),s.frame),s.frame)
        s.capture.assert_not_called();s.click_template.assert_not_called()

    def test_missing_own_close_or_interrupted_observation_has_no_input(self):
        for interrupted in (True,False):
            with self.subTest(interrupted=interrupted):
                s=self.scheduler()
                if interrupted:
                    s.wait_interruptible.side_effect=KeyboardInterrupt('F12')
                    expected=KeyboardInterrupt
                else:
                    s.frame.paste(Image.new('RGB',(55,57)),(1195,408))
                    expected=hs.PendingError
                with self.assertRaises(expected):
                    s.clear_pipeline_interference(hs.Account(1,'captain',180),s.frame)
                s.click_template.assert_not_called()


class UnteamedTests(unittest.TestCase):
    def setUp(self):
        self.account=hs.Account(1,'captainA',200)
        self.s=Mock();self.s.active=self.account
        self.s.capture.return_value='current frame';self.s._generic_active_tab_x.return_value=200
        self.assets={'team_toolbar.bmp'}
        self.s.template.side_effect=lambda asset,*args,**kw:(asset in self.assets,1,0,0)
        self.s.main_ready.return_value=True;self.s.verify_identity.return_value=(True,{})
        self.s.click_template.return_value=True;self.s.wait_until_main.return_value=True
        self.s.save_raw_evidence.return_value='create.png'

    def test_room_arriving_after_wait_timeout_is_observed_before_any_retry(self):
        def late_room(*_args, **_kwargs):
            self.assets.discard('team_toolbar.bmp')
            self.assets.update({'team_room_close.bmp','team_create_button.bmp'})
            return False
        self.s.wait_template.side_effect=late_room
        self.assertEqual('create.png',pipeline.verify_unteamed(self.s,self.account))
        self.s.open_team_room.assert_called_once()
        self.assertEqual(2,self.s.wait_interruptible.call_count)
        self.s.commit_action.assert_not_called()

    def test_visible_room_without_create_proof_cannot_be_reopened(self):
        self.assets.add('team_room_close.bmp')
        with self.assertRaisesRegex(hs.PendingError,'房间内未正向确认无队'):
            pipeline.verify_unteamed(self.s,self.account)
        self.s.open_team_room.assert_not_called()
        self.s.click_template.assert_not_called()

    def test_market_is_closed_before_opening_unteamed_room(self):
        self.assets.add('market_anchor.bmp')
        self.s.main_ready.return_value = False
        order = []
        def closed(*_args, **_kwargs):
            order.append('market_closed')
            self.assets.remove('market_anchor.bmp')
            self.s.main_ready.return_value = True
            return True
        def opened(*_args, **_kwargs):
            order.append('room_opened')
            self.assets.update({'team_room_close.bmp', 'team_create_button.bmp'})
        self.s.open_team_room.side_effect = opened
        with patch('hyol_navigation.close_black_market', side_effect=closed) as close:
            self.assertEqual('create.png', pipeline.verify_unteamed(self.s, self.account))
            close.assert_called_once()
        self.assertEqual(['market_closed', 'room_opened'], order)
        self.s.commit_action.assert_not_called()

    def test_market_exit_failure_cannot_open_unteamed_room(self):
        self.assets.add('market_anchor.bmp')
        self.s.main_ready.return_value = False
        with patch('hyol_navigation.close_black_market', side_effect=hs.PendingError('主界面未确认')):
            with self.assertRaises(hs.PendingError):
                pipeline.verify_unteamed(self.s, self.account)
        self.s.open_team_room.assert_not_called()
        self.s.click_template.assert_not_called()

    def test_ignored_room_open_retries_only_navigation_then_requires_three_create_frames(self):
        attempts=[]
        def opened(*args):
            attempts.append(args)
            if len(attempts)==2:self.assets.update({'team_room_close.bmp','team_create_button.bmp'})
        self.s.open_team_room.side_effect=opened
        self.s.wait_template.side_effect=[False,True]
        self.assertEqual(pipeline.verify_unteamed(self.s,self.account),'create.png')
        self.assertEqual(len(attempts),2)
        self.assertEqual(self.s.wait_interruptible.call_count,2)
        self.s.commit_action.assert_not_called()
        self.assertEqual(self.s.click_template.call_args.args[1],'team_room_close.bmp')

    def test_already_open_empty_room_is_observed_without_reopening(self):
        self.assets.update({'team_room_close.bmp','team_create_button.bmp'})
        self.s.main_ready.return_value=False
        self.assertEqual(pipeline.verify_unteamed(self.s,self.account),'create.png')
        self.s.open_team_room.assert_not_called()

    def test_public_empty_room_uses_its_wider_close_region_without_reopening(self):
        self.assets.update({'team_public_room_title_current.bmp','team_create_button.bmp'})
        self.s.template.side_effect=lambda asset,roi,*args,**kw:(
            roi==pipeline.PUBLIC_ROOM if asset=='team_room_close.bmp' else asset in self.assets,1,0,0)
        self.s.main_ready.return_value=False
        self.assertEqual(pipeline.verify_unteamed(self.s,self.account),'create.png')
        self.s.open_team_room.assert_not_called()
        self.assertEqual(self.s.click_template.call_args.args[2],pipeline.PUBLIC_ROOM)

    def test_unknown_overlay_cannot_be_replaced_by_another_room_open(self):
        self.s.main_ready.return_value=False
        with self.assertRaises(hs.PendingError):pipeline.verify_unteamed(self.s,self.account)
        self.s.open_team_room.assert_not_called()
        self.s.click_template.assert_not_called()

    def test_failed_room_open_is_bounded_without_resending_dissolution(self):
        self.s.wait_template.return_value=False
        with self.assertRaisesRegex(hs.PendingError,'房间未确认'):pipeline.verify_unteamed(self.s,self.account)
        self.assertEqual(self.s.open_team_room.call_count,2)
        self.s.click_template.assert_not_called()
        self.s.commit_action.assert_not_called()

    def test_changed_tab_stops_before_any_room_input(self):
        self.s._generic_active_tab_x.return_value=600
        with self.assertRaises(hs.SafetyError):pipeline.verify_unteamed(self.s,self.account)
        self.s.open_team_room.assert_not_called()
        self.s.click_template.assert_not_called()


class SelectedTabTests(unittest.TestCase):
    def setUp(self):
        self.account=hs.Account(1,'captainA',200)
        self.s=object.__new__(pipeline.PipelineScheduler)
        self.s.active=self.account
        self.s._tab_title_masks={1:np.ones((25,72),dtype=np.uint8)}
        self.s._title_mask=Mock(return_value=self.s._tab_title_masks[1])
        self.s.capture=Mock(return_value='fresh frame')
        self.s._generic_active_tab_x=Mock(return_value=200)
        self.s._bound_role_verified=Mock(return_value=(True,{'captainA':1},True))
        self.s.template=Mock(return_value=(False,0,0,0))
        self.s.offline_dialog_visible=Mock(return_value=False)
        self.s.chrome_click=Mock();self.s.wait_interruptible=Mock()
        self.s.save_raw_evidence=Mock(return_value='changed.png');self.s.event=Mock()

    def test_current_tab_observations_keep_title_and_role_guards_without_reclicking(self):
        for _ in range(3):self.assertEqual(self.s.switch_account(self.account),'fresh frame')
        self.assertEqual(self.s.capture.call_count,3)
        self.assertEqual(self.s._bound_role_verified.call_count,3)
        self.s.chrome_click.assert_not_called()
        self.s.wait_interruptible.assert_not_called()

    def test_changed_title_or_role_cannot_bypass_checks_on_the_current_tab(self):
        self.s._title_mask.return_value=np.zeros((25,72),dtype=np.uint8)
        with self.assertRaisesRegex(hs.PendingError,'标题或位置'):self.s.switch_account(self.account)
        self.s._title_mask.return_value=self.s._tab_title_masks[1]
        self.s._bound_role_verified.return_value=(False,{},True)
        with self.assertRaisesRegex(hs.PendingError,'原实名绑定'):self.s.switch_account(self.account)
        self.s.chrome_click.assert_not_called()

    def test_manual_tab_change_requires_an_actual_switch_and_fresh_validation(self):
        self.s._generic_active_tab_x.side_effect=[600,200]
        self.assertEqual(self.s.switch_account(self.account),'fresh frame')
        self.s.chrome_click.assert_called_once_with(200,10,'switch_tab:1',self.account)
        self.assertEqual(self.s.capture.call_count,2)

    def test_current_tab_capture_safety_failure_propagates_without_input(self):
        self.s.capture.side_effect=hs.SafetyError('window changed')
        with self.assertRaises(hs.SafetyError):self.s.switch_account(self.account)
        self.s.chrome_click.assert_not_called()

    def test_current_tab_offline_prompt_is_skipped_without_clicking_it(self):
        self.s.offline_dialog_visible.return_value=True
        with self.assertRaises(hs.AccountOffline):self.s.switch_account(self.account)
        self.s.chrome_click.assert_not_called()


class PortraitTests(unittest.TestCase):
    def test_actual_public_room_has_create_proof_at_the_correct_close_position(self):
        store=hs.TemplateStore(.98)
        frame=Image.new('RGB',(1920,1032))
        frame.paste(Image.open(hs.ROOT/'tests/fixtures/team_public_empty_room_current.png'),(480,235))
        self.assertTrue(store.match(frame,hs.ASSETS/'team_create_button.bmp',pipeline.CREATE,.98)[0])
        self.assertTrue(store.match(frame,hs.ASSETS/'team_room_close.bmp',pipeline.PUBLIC_ROOM,.98)[0])
        self.assertFalse(store.match(frame,hs.ASSETS/'team_room_close.bmp',pipeline.ROOM,.98)[0])

    def test_new_modal_identity_layouts_require_both_strict_title_and_close(self):
        from hyol_checkpoints import current_identity_mask
        cases=(('team_public_identity_current.png','team_public_empty_room_current.png',(480,235),pipeline.PUBLIC_ROOM),
               ('team_daily_identity_current.png','team_daily_dynamic_title_current.png',(1050,140),(1396,220,1440,265)))
        for header,body,position,close in cases:
            frame=Image.new('RGB',(1920,1032))
            frame.paste(Image.open(hs.ROOT/'tests/fixtures'/header),(0,69))
            frame.paste(Image.open(hs.ROOT/'tests/fixtures'/body),position)
            store=hs.TemplateStore(.98)
            self.assertGreater(int(current_identity_mask(frame,store).sum()),50)
            frame.paste(Image.new('RGB',(close[2]-close[0],close[3]-close[1])),close[:2])
            self.assertEqual(int(current_identity_mask(frame,store).sum()),0)

    def setUp(self):
        self.template = np.asarray(Image.open(hs.ASSETS/'team_personal_leader_core.bmp').convert('L'),dtype=float)

    def score(self,frame,roi,asset='team_personal_leader_core.bmp'):
        template=np.asarray(Image.open(hs.ASSETS/asset).convert('L'),dtype=float)
        return hs.ncc(np.asarray(frame.crop(roi).convert('L'),dtype=float),template)[0]

    def test_user_captain_and_member_headers_have_opposite_results(self):
        for file,expected in (('team_captain_header.png',True),('team_captain_white_header.png',True),('team_member_header.png',False)):
            header=Image.open(hs.ROOT/'tests/fixtures'/file).convert('RGB')
            frame=Image.new('RGB',(1920,1032));frame.paste(header,(0,69))
            scheduler=SimpleNamespace(template=lambda name,roi,image,threshold:
                                      (self.score(image,roi,name)>=threshold,self.score(image,roi,name),0,0))
            self.assertIs(pipeline.portrait_leader_present(scheduler,frame),expected)

    def test_identical_party_leader_icon_is_excluded_from_personal_roi(self):
        frame=Image.new('RGB',(1920,1032))
        frame.paste(Image.open(hs.ASSETS/'team_personal_leader_core.bmp'),(8,200))
        self.assertGreater(self.score(frame,(0,69,300,300)),.99)
        self.assertLess(self.score(frame,pipeline.PERSONAL_AVATAR_ROI),.95)

    def test_current_captain_badge_text_accepts_changed_background_at_original_threshold(self):
        frame=Image.new('RGB',(1920,1032))
        frame.paste(Image.open(hs.ROOT/'tests/fixtures/team_captain1_autumn_header.png'),(0,69))
        store=hs.TemplateStore(.98)
        scheduler=SimpleNamespace(template=lambda name,roi,image,threshold:store.match(image,hs.ASSETS/name,roi,threshold))
        self.assertLess(self.score(frame,pipeline.PERSONAL_AVATAR_ROI,'team_personal_leader_white_core.bmp'),.95)
        self.assertTrue(pipeline.portrait_leader_present(scheduler,frame))
        frame.paste(Image.new('RGB',(45,80)),(0,69))
        frame.paste(Image.open(hs.ASSETS/'team_personal_leader_text_core_current.bmp'),(10,200))
        self.assertFalse(pipeline.portrait_leader_present(scheduler,frame))

    def test_actual_failed_captain_name_with_animated_background_is_uniquely_bound(self):
        import hyol_checkpoints as cp
        identities=cp.read_checkpoint(hs.STATE_DIR/'account_identities.json')
        masks={}
        for index,file in ((1,'team_identity_animated_header.png'),(6,'team_identity_captain6_header.png')):
            with Image.open(hs.ROOT/'tests/fixtures'/file) as header:
                frame=Image.new('RGB',(1920,1032));frame.paste(header,(0,0))
            masks[index]=cp.current_identity_mask(frame)
        mapping,scores,gap=cp.joint_identity_assignment(masks,identities)
        self.assertEqual(mapping,{1:ROLE_A,6:ROLE_B})
        self.assertGreaterEqual(min(scores[slot][name] for slot,name in mapping.items()),.92)
        self.assertGreaterEqual(gap,.08)
        self.assertEqual(cp.joint_identity_assignment({1:masks[1],6:masks[1]},identities)[0],{})

    def test_actual_two_reward_pools_replay_first_battle_without_fake_daily_decrement(self):
        template=hs.TemplateStore(.98)
        scheduler=object.__new__(hs.Scheduler)
        scheduler.template=lambda name,roi,frame,threshold=.98:template.match(frame,hs.ASSETS/name,roi,threshold=threshold)
        scheduler.event=Mock();scheduler.save_raw_evidence=Mock(return_value='unreadable.png')
        owner=coordinator();owner.scheduler=scheduler
        driver=pipeline.LeaderActivityRunner(owner,hs.Account(1,'captain',180),owner.record['teams']['1'])
        for file,expected in (('team_group_reward_before_first.png',(5,5)),('team_group_reward_after_first.png',(5,4))):
            with Image.open(hs.ROOT/'tests/fixtures'/file) as footer:
                frame=Image.new('RGB',(1920,1032));frame.paste(footer,(500,765))
            self.assertEqual(driver.group_rewards(frame),expected)

    def test_actual_wanted_path_and_npc_loading_are_positive_readonly_transitions(self):
        store=hs.TemplateStore(.98)
        owner=coordinator()
        driver=pipeline.LeaderActivityRunner(owner,owner.scheduler.accounts[0],owner.record['teams']['1'])
        driver.visible=lambda name,roi=hs.VIEW,frame=None,threshold=.98:store.match(
            frame,hs.ASSETS/name,roi,threshold)[0]
        fixture=Image.open(hs.ROOT/'tests/fixtures/team_wanted_running_regions.png')
        frame=Image.new('RGB',(1920,1032))
        frame.paste(fixture.crop((0,0,300,60)),(800,140))
        frame.paste(fixture.crop((400,0,595,110)),(1715,480))
        self.assertTrue(driver.wanted_task_present(frame))
        self.assertTrue(driver.scene_transition(frame))
        loading=Image.new('RGB',(1920,1032))
        loading.paste(Image.open(hs.ROOT/'tests/fixtures/team_npc_loading_region.png'),(600,525))
        self.assertTrue(driver.scene_transition(loading))
        self.assertFalse(driver.scene_transition(Image.new('RGB',(1920,1032))))

    def test_actual_gray_wanted_zero_row_is_detected_without_matching_other_daily_rows(self):
        store=hs.TemplateStore(.98)
        region=Image.open(hs.ROOT/'tests/fixtures/team_wanted_completed_zero_panel.png')
        frame=Image.new('RGB',(1920,1032));frame.paste(region,(500,310))
        asset=hs.ASSETS/'daily_wanted_completed_zero_current.bmp'
        self.assertTrue(store.match(frame,asset,(500,310,1090,720),.98)[0])
        frame.paste(Image.new('RGB',(590,65)),(500,650))
        self.assertFalse(store.match(frame,asset,(500,310,1090,720),.98)[0])

    def test_current_wanted_zero_cores_handle_gray_style_and_must_belong_to_one_row(self):
        store = hs.TemplateStore(.98)
        s = SimpleNamespace(template=lambda asset,roi,frame,threshold=.98:
                            store.match(frame,hs.ASSETS/asset,roi,threshold))
        frame = Image.new('RGB',(1920,1032))
        region = Image.open(hs.ROOT/'tests/fixtures/team_wanted_gray_zero_panel_20261008.png')
        frame.paste(region,(500,310))
        self.assertFalse(store.match(frame,hs.ASSETS/'daily_wanted_completed_zero_current.bmp',
                                     (500,310,1090,720),.98)[0])
        self.assertTrue(pipeline.wanted_daily_zero(s,frame))
        for box in ((535,673,626,704),(845,688,869,706),(1004,668,1053,705)):
            changed = frame.copy();changed.paste('gray',box)
            self.assertFalse(pipeline.wanted_daily_zero(s,changed))
        changed = frame.copy()
        count = frame.crop((845,688,869,706))
        changed.paste('gray',(845,688,869,706));changed.paste(count,(845,630))
        self.assertFalse(pipeline.wanted_daily_zero(s,changed))

    def test_current_wanted_nonzero_digit_cannot_match_zero_completion(self):
        from PIL import ImageDraw
        store = hs.TemplateStore(.98)
        s = SimpleNamespace(template=lambda asset,roi,frame,threshold=.98:
                            store.match(frame,hs.ASSETS/asset,roi,threshold))
        frame = Image.new('RGB',(1920,1032))
        frame.paste(Image.open(hs.ROOT/'tests/fixtures/team_wanted_gray_zero_panel_20261008.png'),(500,310))
        # Keep slash/five, the wanted label and the completion badge; alter
        # only the zero digit. A full-row background score cannot accept this.
        draw = ImageDraw.Draw(frame)
        draw.rectangle((845,688,852,705),fill=(177,177,177))
        draw.line((849,692,849,700),fill=(60,60,60),width=2)
        draw.line((847,693,849,691),fill=(60,60,60),width=1)
        self.assertFalse(pipeline.wanted_daily_zero(s,frame))

    def test_readonly_offline_dialog_skips_the_account_even_if_its_badge_is_visible(self):
        s=object.__new__(pipeline.PipelineScheduler)
        s.offline_dialog_visible=Mock(return_value=True)
        s.save_raw_evidence=Mock(return_value='offline.png')
        account=hs.Account(6,'captainB',600)
        with patch.object(pipeline.SixTabScheduler,'switch_account',return_value='dimmed captain frame') as select:
            with self.assertRaises(hs.AccountOffline) as got:
                s.switch_account(account)
        self.assertIs(got.exception.account,account)
        self.assertIsNone(s._capture_account)
        select.assert_called_once_with(account,True)

    def test_readonly_detection_fills_two_captains_and_does_not_cancel_success(self):
        s=Mock();s.stop=threading.Event();s.content_click_count=0;s.config={}
        s.template.return_value=(True,1.0,0,0)
        s._generic_active_tab_x.side_effect=lambda frame:frame*100
        s.switch_account.side_effect=lambda a:a.index
        s.capture.side_effect=[0,0,1,1,2,2,3,3,4,4,5,5,6,6]
        stop=threading.Event()
        lease=Mock();lease.acquire.return_value=True
        with patch.object(pipeline,'PipelineScheduler',return_value=s), \
             patch.object(pipeline.hs,'ForegroundRuntimeLease',return_value=lease), \
             patch.object(pipeline,'tab_close_positions',return_value=(1,2,3,4,5,6)), \
             patch.object(pipeline,'active_tab_centers',return_value=(100,200,300,400,500,600)), \
             patch.object(pipeline,'portrait_leader_present',side_effect=lambda scheduler,frame:frame in (1,6)), \
             patch.object(pipeline.hs,'atomic_json'):
            result=pipeline.detect_leaders(stop_event=stop)
        self.assertEqual(result['leaders'],[1,6])
        self.assertFalse(stop.is_set())
        self.assertEqual([c.args[0].index for c in s.switch_account.call_args_list],[1,2,3,4,5,6])
        s.click_template.assert_not_called()
        lease.release.assert_called_once()

    def test_detection_stop_prevents_any_tab_selection(self):
        s=Mock();s.stop=threading.Event();s.config={}
        stop=threading.Event();stop.set()
        lease=Mock();lease.acquire.return_value=True
        with patch.object(pipeline,'PipelineScheduler',return_value=s), \
             patch.object(pipeline.hs,'ForegroundRuntimeLease',return_value=lease), \
             patch.object(pipeline,'tab_close_positions',return_value=(1,2)), \
             patch.object(pipeline,'active_tab_centers',return_value=(100,200)):
            with self.assertRaises(KeyboardInterrupt):pipeline.detect_leaders(stop_event=stop)
        s.switch_account.assert_not_called()
        lease.release.assert_called_once()


if __name__ == '__main__':
    unittest.main()
