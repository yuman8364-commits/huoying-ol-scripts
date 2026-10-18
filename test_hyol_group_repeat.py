"""Real header replay and complete multi-battle loops to verified zero rewards."""
from copy import deepcopy
from pathlib import Path
import time
import unittest
from unittest.mock import Mock, patch

from PIL import Image
import hyol_scheduler as hs
import hyol_team_pipeline as pipeline
import test_hyol_team_pipeline as fixtures
import test_hyol_pipeline_resume as resume_fixtures


class GroupRepeatTests(unittest.TestCase):
    def setUp(self):
        fixtures.DriverTests.setUp(self)
        self.driver.close_page = Mock()
        self.owner.snapshot = Mock()
        self.owner.group_party_verified = Mock(return_value=True)
        self.frame.add('group_title_current.bmp')
        self.counts = [5,5]
        self.driver.group_rewards = lambda _frame: tuple(self.counts)

    def run_to_zero(self, daily, friendship, completed=0):
        self.counts[:] = [daily,friendship]
        rec = self.driver.record('group')
        rec.update(phase='ready',completed=completed,counter_scope='daily_and_friendship')
        selections = []
        while sum(self.counts):
            self.driver.tick('group')
            selections.append(rec['intent']['label'])
            self.assertNotEqual(rec['status'], 'done')
            self.assertTrue(self.driver.absent_intent(rec, self.frame))
            self.assertEqual(rec['completed'], completed+len(selections)-1)
            self.counts[1 if self.counts[1] else 0] -= 1
            for _ in range(3):
                self.driver.tick('group')
            self.assertEqual(rec['completed'], completed+len(selections))
            self.assertEqual(rec['phase'], 'ready')
            self.assertNotIn('zero_evidence', rec)
        for _ in range(3):
            self.driver.tick('group')
        self.assertEqual(rec['phase'], 'finishing')
        self.assertTrue(pipeline.group_reward_zero_confirmed(rec))
        self.driver.close_page.assert_not_called()
        self.owner.snapshot.assert_not_called()
        return rec, selections

    def test_two_reward_pools_loop_through_ten_battles_without_leaving_the_page(self):
        rec, labels = self.run_to_zero(5,5)
        self.assertEqual(labels, [f'group_{index}_select' for index in range(1,11)])
        self.assertEqual(len(rec['battle_history']), 10)
        self.assertEqual(rec['battle_history'][0]['daily_remaining'], 5)
        self.assertEqual(rec['battle_history'][0]['friendship_remaining'], 4)
        self.assertEqual(rec['battle_history'][-1]['daily_remaining'], 0)

    def test_five_daily_rewards_require_five_real_counter_decrements(self):
        rec, labels = self.run_to_zero(5,0)
        self.assertEqual(len(labels), 5)
        self.assertEqual(rec['completed'], 5)

    def test_observed_one_battle_checkpoint_resumes_at_second_and_keeps_first_receipt(self):
        self.actions['pipeline_original_group_1_select'] = 'done'
        rec, labels = self.run_to_zero(5,4,completed=1)
        self.assertEqual(labels, [f'group_{index}_select' for index in range(2,11)])
        self.assertEqual(self.actions['pipeline_original_group_1_select'], 'done')
        self.assertEqual(rec['completed'], 10)

    def test_unverified_party_keeps_room_recheck_before_another_battle(self):
        self.owner.group_party_verified.return_value = False
        rec = self.driver.record('group')
        rec.update(phase='running',reward_before=10,completed=0,last_progress=time.time())
        self.counts[:] = [5,4]
        for _ in range(3):
            self.driver.tick('group')
        self.assertEqual(rec['completed'], 1)
        self.assertEqual(rec['phase'], 'verify_members')
        self.driver.tick('group')
        self.driver.close_page.assert_called_once_with('group')
        self.assertNotIn('pipeline_original_group_2_select', self.actions)

    def test_nonzero_or_unproven_zero_cannot_finish_the_task(self):
        valid = dict(daily_remaining=0,friendship_remaining=0,reward_exhausted=True,zero_evidence='zero.png')
        for field,value in [('daily_remaining',5),('friendship_remaining',4),
                            ('reward_exhausted',False),('zero_evidence',''),('daily_remaining',False)]:
            with self.subTest(field=field):
                rec = dict(valid,phase='finishing',completed=1)
                rec[field] = value
                with self.assertRaisesRegex(hs.PendingError, '奖励.*归零'):
                    self.driver.finish_task('group', rec)
                self.assertNotIn('status', rec)
        self.s.click_template.assert_not_called()
        self.owner.snapshot.assert_not_called()

    def test_nonzero_legacy_done_never_dissolves_or_completes_the_pipeline(self):
        saved = resume_fixtures.PipelineCompletionTests().finished_teams(self.owner)
        saved.update(phase='done',solo_result=0,solo_eligible=saved['expected_accounts'])
        saved['solo_records'] = {name:dict(mail=dict(status='done',task_completed=True,home_returned=True))
                                 for name in saved['expected_accounts']}
        self.owner.record = saved
        slot = saved['teams']['1']
        slot['tasks']['group'].update(completed=1,daily_remaining=5,friendship_remaining=4)
        self.assertFalse(self.owner.completed())
        with self.assertRaisesRegex(hs.PendingError, '奖励.*归零'):
            self.owner.dissolve(1,slot)
        self.s.click_template.assert_not_called()


class PartyHeaderReplayTests(unittest.TestCase):
    evidence = hs.ROOT/'logs/release_preserved/group_repeat_20261014/evidence'
    members = ('captain','member-a','member-b')

    def frames(self, team):
        return (Image.open(self.evidence/f'team_{team}_room.png').convert('RGB'),
                Image.open(self.evidence/f'team_{team}_group.jpg').convert('RGB'))

    def proof(self, frame):
        return pipeline.enroll_party_header([frame]*3,self.members,'captain','verified-room.png')

    def test_both_actual_teams_match_their_verified_room_and_group_page(self):
        for team in ('a','b'):
            with self.subTest(team=team):
                room, group = self.frames(team)
                proof = self.proof(room)
                self.assertIsNotNone(proof)
                self.assertTrue(pipeline.party_header_matches(proof,group,self.members))

    def test_other_team_is_rejected_even_with_same_declared_member_names(self):
        room, _ = self.frames('a')
        _, other = self.frames('b')
        self.assertFalse(pipeline.party_header_matches(self.proof(room),other,self.members))

    def test_reordered_same_members_can_match_but_duplicate_references_cannot(self):
        room, group = self.frames('a')
        proof = self.proof(room)
        proof['names'].reverse()
        self.assertTrue(pipeline.party_header_matches(proof,group,self.members))
        proof['names'][1] = deepcopy(proof['names'][0])
        self.assertFalse(pipeline.party_header_matches(proof,group,self.members))

    def test_changed_server_glyphs_cannot_reuse_a_matching_nickname(self):
        room, group = self.frames('a')
        proof = self.proof(room)
        row = proof['names'][0]
        mask = pipeline.decode_mask(row['mask'],tuple(row['shape']))
        mask[:, :pipeline.PARTY_SERVER_WIDTH] = False
        row['mask'] = pipeline.encode_mask(mask)
        self.assertFalse(pipeline.party_header_matches(proof,group,self.members))

    def test_missing_or_corrupt_enrollment_requires_room_fallback(self):
        room, group = self.frames('a')
        proof = self.proof(room)
        self.assertFalse(pipeline.party_header_matches({},group,self.members))
        proof['observations'] = 1
        self.assertFalse(pipeline.party_header_matches(proof,group,self.members))
        proof['observations'] = 3
        proof['names'][0]['mask'] = 'not-base64'
        self.assertFalse(pipeline.party_header_matches(proof,group,self.members))

    def test_post_first_battle_room_keeps_the_same_actual_members(self):
        room, _ = self.frames('b')
        after = Image.open(self.evidence/'team_b_room_after.png').convert('RGB')
        self.assertTrue(pipeline.party_header_matches(self.proof(room),after,self.members))

    def test_actual_group_page_still_requires_current_captain_and_account_identity(self):
        room, group = self.frames('a')
        owner = fixtures.coordinator()
        store = hs.TemplateStore(.98)
        owner.scheduler.template = lambda asset,roi,frame,threshold: store.match(
            frame,hs.ASSETS/asset,roi,threshold=threshold)
        owner.scheduler.verify_identity = Mock(return_value=(True,{}))
        slot = dict(members=self.members,party_header=self.proof(room))
        account = owner.scheduler.accounts[0]
        self.assertTrue(owner.group_party_verified(account,slot,group))
        owner.scheduler.verify_identity.return_value = (False,{})
        self.assertFalse(owner.group_party_verified(account,slot,group))
        owner.scheduler.verify_identity.return_value = (True,{})
        with patch.object(pipeline,'portrait_leader_present',return_value=False):
            self.assertFalse(owner.group_party_verified(account,slot,group))


if __name__ == '__main__':
    unittest.main()
