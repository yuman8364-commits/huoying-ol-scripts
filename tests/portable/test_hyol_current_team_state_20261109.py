"""Current-account activity resume preserves original ownership and unknown input."""
from copy import deepcopy
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest

import numpy as np
from PIL import Image
import hyol_scheduler as hs
from hyol_checkpoints import encode_mask,source_token,read_checkpoint,action_versions
from hyol_current_team_state import CurrentTeamLedger


class CurrentTeamStateTests(unittest.TestCase):
    def setUp(self):
        self.folder=tempfile.TemporaryDirectory();self.addCleanup(self.folder.cleanup)
        self.root=Path(self.folder.name)/'shared'
        self.mask=(np.random.default_rng(4).random((32,145))>.7).astype(np.uint8)
        self.material=dict(mask=encode_mask(self.mask),shape=list(self.mask.shape),kind='world')

    def runner(self,run='run1',day='2026-11-09',material=None):
        name='account_'+(run.encode().hex()+'0'*32)[:32]
        account=hs.Account(1,name,180)
        state=hs.StateStore([name],self.root/'runs'/run,restore_history=False,execution_day=day)
        material=deepcopy(material or self.material)
        hs.atomic_json(state.directory/'account_identities.json',{name:material})
        state.save_account(name)
        records={}
        scheduler=SimpleNamespace(identity_directory=self.root,state=state,
                                  _dynamic_identity_materials={name:material})
        r=SimpleNamespace(scheduler=scheduler,account=account,records=records)
        r.record=lambda task:records.setdefault(task,dict(status='new',phase='ready',completed=0))
        def save():
            path=state.account_paths[name]
            hs.atomic_json(path.with_name(path.stem+'_tasks.json'),records)
        r.save=save
        return r

    def ledger(self,r,task='bond',request=None,material=None):
        return CurrentTeamLedger(r,task,request or {'mode':'battles','target':5},material)

    def pending(self,r,key='bond_match_1'):
        r.scheduler.state.set_action(r.account.name,key,'pending','原匹配已发送')

    def test_same_role_new_run_restores_same_object_and_pending_origin_without_old_writes(self):
        first=self.runner();old=self.ledger(first)
        old.record.update(status='running',phase='matching',completed=2)
        self.pending(first);old.save()
        source=first.scheduler.state.account_paths[first.account.name];original=source.read_bytes()
        second=self.runner('run2');new=self.ledger(second)
        self.assertEqual(old.owner,new.owner)
        self.assertIs(second.record('bond'),new.record)
        self.assertEqual(2,new.record['completed']);self.assertEqual('matching',new.record['phase'])
        action=second.scheduler.state.action_record(second.account.name,'bond_match_1')
        self.assertEqual('pending',action['status']);self.assertEqual(str(source),action['origin']['path'])
        self.assertEqual(original,source.read_bytes());self.assertTrue(new.check_history())

    def test_dispatch_after_last_shared_save_is_recovered_from_source_journal(self):
        first=self.runner();old=self.ledger(first)
        self.pending(first)
        second=self.runner('run2');new=self.ledger(second)
        self.assertEqual(old.owner,new.owner)
        self.assertEqual('pending',second.scheduler.state.action_status(second.account.name,'bond_match_1'))

    def test_same_run_restart_keeps_one_dispatch_after_origin_is_added_to_snapshot(self):
        r=self.runner();ledger=self.ledger(r,'grocery',{'currency':'moon'})
        key='grocery_buy_practice_potion_original';self.pending(r,key)
        row=r.scheduler.state.accounts[r.account.name]['actions'][key]
        row['before_evidence']='original-before.png'
        r.scheduler.state.save_account(r.account.name);ledger.save()
        for _ in range(3):
            ledger=self.ledger(r,'grocery',{'currency':'moon'})
            versions=action_versions(r.scheduler.state.action_record(r.account.name,key))
            self.assertEqual(1,len(versions));self.assertEqual('pending',versions[0]['status'])
            self.assertEqual(str(ledger.source),versions[0]['origin']['path'])

    def test_same_run_recovers_preexisting_raw_and_origin_duplicate_without_claiming_paid(self):
        r=self.runner();ledger=self.ledger(r,'grocery',{'currency':'moon'})
        key='grocery_buy_practice_potion_original';self.pending(r,key)
        raw=r.scheduler.state.accounts[r.account.name]['actions'][key]
        raw['before_evidence']='original-before.png'
        r.scheduler.state.save_account(r.account.name);ledger.save()
        raw['checkpoint_conflict']=[deepcopy(read_checkpoint(ledger.path)['actions'][key])]
        r.scheduler.state.save_account(r.account.name)
        ledger=self.ledger(r,'grocery',{'currency':'moon'})
        self.assertEqual(1,len(action_versions(r.scheduler.state.action_record(r.account.name,key))))
        self.assertEqual('pending',r.scheduler.state.action_status(r.account.name,key))

    def test_different_before_receipt_remains_distinct_and_cannot_be_committed_once(self):
        r=self.runner();ledger=self.ledger(r,'grocery',{'currency':'moon'})
        key='grocery_buy_practice_potion_original';self.pending(r,key)
        raw=r.scheduler.state.accounts[r.account.name]['actions'][key]
        raw['before_evidence']='original-before.png'
        r.scheduler.state.save_account(r.account.name);ledger.save()
        raw['before_evidence']='different-before.png';r.scheduler.state.save_account(r.account.name)
        self.ledger(r,'grocery',{'currency':'moon'})
        self.assertEqual(2,len(action_versions(r.scheduler.state.action_record(r.account.name,key))))
        with self.assertRaisesRegex(hs.PendingError,'多个未决来源'):
            r.scheduler.state.set_action(r.account.name,key,'done','单笔结果不能解决两次输入')

    def test_commit_before_shared_save_does_not_resurrect_same_proven_pending(self):
        first=self.runner();old=self.ledger(first);self.pending(first)
        actions=first.scheduler.state.accounts[first.account.name]['actions']
        actions['bond_match_1']['before_evidence']='original-dispatch.png'
        first.scheduler.state.save_account(first.account.name);old.save()
        first.scheduler.state.set_action(first.account.name,'bond_match_1','done','正向结果已确认')
        source=first.scheduler.state.account_paths[first.account.name];original=source.read_bytes()
        second=self.runner('run2');new=self.ledger(second)
        self.assertEqual('done',second.scheduler.state.action_status(second.account.name,'bond_match_1'))
        self.assertEqual(original,source.read_bytes());self.assertTrue(new.check_history())

    def test_resolution_is_written_only_to_new_run_and_allows_next_resume(self):
        first=self.runner();old=self.ledger(first);self.pending(first);old.save()
        source=first.scheduler.state.account_paths[first.account.name];original=source.read_bytes()
        second=self.runner('run2');new=self.ledger(second)
        second.scheduler.state.set_action(second.account.name,'bond_match_1','done','原结果正向核实')
        new.record.update(status='done',phase='done',task_completed=True);new.save()
        third=self.runner('run3');last=self.ledger(third)
        self.assertEqual('done',third.scheduler.state.action_status(third.account.name,'bond_match_1'))
        self.assertEqual('done',last.record['status']);self.assertEqual(original,source.read_bytes())
        self.assertTrue((second.scheduler.state.directory/'checkpoint_resolutions.json').is_file())

    def test_request_change_never_replaces_original_goal(self):
        first=self.runner();old=self.ledger(first);old.record['status']='running';old.save()
        original=old.path.read_bytes()
        with self.assertRaisesRegex(hs.PendingError,'当前选择不同'):
            self.ledger(self.runner('run2'),request={'mode':'battles','target':10})
        self.assertEqual(original,old.path.read_bytes())

    def test_old_unmanaged_same_role_pending_blocks_new_cursor(self):
        first=self.runner('unmanaged');self.pending(first,'arena3v3_match_1')
        current=self.runner('run2')
        with self.assertRaisesRegex(hs.PendingError,'旧活动存在未决动作'):
            self.ledger(current)
        self.assertFalse((self.root/'activities/current_team/identities.json').exists())

    def test_another_role_old_pending_does_not_block_current_role(self):
        other=(np.random.default_rng(91).random((32,145))>.7).astype(np.uint8)
        first=self.runner('other',material=dict(mask=encode_mask(other),shape=[32,145]))
        self.pending(first)
        self.assertTrue(self.ledger(self.runner('run2')).check_history())

    def test_near_old_unfinished_identity_cannot_be_registered_as_a_new_role(self):
        first=self.runner();old=self.ledger(first);self.pending(first);old.save()
        changed=self.mask.copy();ys,xs=np.nonzero(changed);changed[ys[::4],xs[::4]]=0
        near=dict(mask=encode_mask(changed),shape=[32,145])
        with self.assertRaisesRegex(hs.PendingError,'历史未决活动身份相似'):
            self.ledger(self.runner('run2',material=near))

    def test_attached_activity_identity_can_resume_without_world_strip(self):
        first=self.runner();old=self.ledger(first)
        mask=(np.random.default_rng(8).random((24,85))>.7).astype(np.uint8)
        material=dict(mask=encode_mask(mask),shape=[24,85],kind='bond_lobby')
        old.attach_material(material);old.save()
        second=self.runner('run2',material=material);new=self.ledger(second,material=material)
        self.assertEqual(old.owner,new.owner)

    def test_cross_day_pending_is_not_cleared_by_new_date(self):
        first=self.runner(day='2026-11-08');old=self.ledger(first);self.pending(first);old.save()
        original=old.path.read_bytes()
        with self.assertRaisesRegex(hs.PendingError,'旧活动存在未决动作'):
            self.ledger(self.runner('run2',day='2026-11-09'))
        self.assertEqual(original,old.path.read_bytes())

    def test_source_path_outside_shared_root_is_rejected(self):
        first=self.runner();old=self.ledger(first)
        value=read_checkpoint(old.path);value['source_state_file']=str(Path(self.folder.name)/'outside.json')
        hs.atomic_json(old.path,value)
        with self.assertRaisesRegex(hs.PendingError,'越出共享状态'):
            self.ledger(self.runner('run2'))

    def test_simultaneous_writer_cannot_overwrite_changed_cursor(self):
        first=self.runner();old=self.ledger(first)
        value=read_checkpoint(old.path);value['record']['completed']=9;hs.atomic_json(old.path,value)
        with self.assertRaisesRegex(hs.PendingError,'另一入口修改'):old.save()
        self.assertEqual(9,read_checkpoint(old.path)['record']['completed'])

    def test_resolution_for_a_foreign_token_does_not_hide_original_pending(self):
        first=self.runner('unmanaged');self.pending(first)
        source=first.scheduler.state.account_paths[first.account.name]
        hs.atomic_json(first.scheduler.state.directory/'checkpoint_resolutions.json',{'foreign':{
            'token':'foreign','path':str(source),'resolved_at':'2026-11-09T01:00:00'}})
        with self.assertRaisesRegex(hs.PendingError,'旧活动存在未决动作'):
            self.ledger(self.runner('run2'))

    def test_pending_action_cannot_be_saved_as_a_completed_activity(self):
        r=self.runner();ledger=self.ledger(r);self.pending(r)
        original=ledger.path.read_bytes()
        ledger.record.update(phase='done',task_completed=True)
        with self.assertRaisesRegex(hs.PendingError,'不能保存完成状态'):ledger.save()
        self.assertEqual(original,ledger.path.read_bytes())

    def test_current_other_activity_pending_blocks_cross_activity_start(self):
        r=self.runner();self.pending(r,'arena3v3_match_1')
        with self.assertRaisesRegex(hs.PendingError,'其他组队活动仍有未决动作'):self.ledger(r)

    def test_duplicate_stable_material_is_ambiguous(self):
        r=self.runner();ledger=self.ledger(r)
        registry=read_checkpoint(ledger.registry_path)
        registry['owners']['current_'+'f'*32]=deepcopy(registry['owners'][ledger.owner])
        hs.atomic_json(ledger.registry_path,registry)
        with self.assertRaisesRegex(hs.PendingError,'多个活动身份接近'):self.ledger(self.runner('run2'))

    def arena(self,r,day):
        return CurrentTeamLedger(r,'arena_3v3',{'goal':'all_current_account_rewards'},activity_day=day)

    def deferred(self,r,ledger):
        proofs=[]
        for n in range(3):
            path=Path(self.folder.name)/('proof'+str(n)+'.png')
            Image.new('RGB',(10,10),(n,10,20)).save(path);proofs.append(str(path))
        ledger.record.update(status='pending',phase='waiting_open',completed=0,activity_day=ledger.day,
            availability={'status':'not_open'},party_evidence=proofs[:2],
            members={ledger.owner:{'script_claims':0,'reward_evidence':proofs[2]}})
        ledger.save()

    def test_activity_day_changes_bucket_without_changing_source_state_day(self):
        r=self.runner(day='2026-11-09');old=self.arena(r,'2026-11-08')
        old.record.update(activity_day='2026-11-08',status='done',phase='done',task_completed=True,
                          completed=3,claim_action_scope='legacy')
        old.save();old_bytes=old.path.read_bytes()
        new=self.arena(r,'2026-11-09')
        self.assertNotEqual(old.path,new.path)
        self.assertEqual('2026-11-09',r.scheduler.state.day)
        self.assertEqual('2026-11-09',new.source.parent.name)
        self.assertEqual('new',new.record['status']);self.assertEqual(0,new.record['completed'])
        self.assertNotIn('claim_action_scope',new.record)
        self.assertEqual(old_bytes,old.path.read_bytes())
        archive=read_checkpoint(new.record['previous_activity_record'])
        self.assertEqual(3,archive['original_record']['completed'])

    def test_first_running_shell_is_not_misclassified_as_an_old_activity(self):
        r=self.runner();r.record('arena_3v3').update(status='running',phase='ready')
        self.assertEqual('ready',self.arena(r,'2026-11-08').record['phase'])

    def test_unstarted_waiting_open_can_cross_boundary_with_old_files_unchanged(self):
        r=self.runner();old=self.arena(r,'2026-11-08');self.deferred(r,old)
        before=old.path.read_bytes();source=old.source.read_bytes()
        new=self.arena(self.runner('run2'),'2026-11-09')
        self.assertEqual('new',new.record['status']);self.assertTrue(new.check_history())
        proof=read_checkpoint(new.path)['unstarted_previous_scopes'][str(old.path)]
        self.assertEqual('not_started',proof['outcome']);self.assertFalse(proof['counted_as_completed'])
        self.assertEqual(before,old.path.read_bytes());self.assertEqual(source,old.source.read_bytes())

    def test_waiting_open_with_dispatched_source_action_still_blocks_boundary(self):
        r=self.runner();old=self.arena(r,'2026-11-08');self.deferred(r,old)
        self.pending(r,'arena3v3_match_1')
        with self.assertRaises(hs.PendingError):self.arena(self.runner('run2'),'2026-11-09')

    def test_finished_other_activity_does_not_make_untouched_arena_started(self):
        r=self.runner();old=self.arena(r,'2026-11-08');self.deferred(r,old)
        r.scheduler.state.set_action(r.account.name,'bond_match_1','done','此前其他活动已核实')
        self.assertTrue(self.arena(self.runner('run2'),'2026-11-09').check_history())

    def test_waiting_open_with_reward_intent_still_blocks_boundary(self):
        r=self.runner();old=self.arena(r,'2026-11-08');self.deferred(r,old)
        hs.atomic_json(self.root/'arena3v3/2026-11-08/rewards.json',{
            old.owner+':row:win_1':{'status':'pending'}})
        with self.assertRaisesRegex(hs.PendingError,'旧日期活动仍未完成'):
            self.arena(self.runner('run2'),'2026-11-09')

    def test_waiting_open_without_original_source_is_unknown(self):
        r=self.runner();old=self.arena(r,'2026-11-08');self.deferred(r,old)
        old.source.unlink()
        with self.assertRaisesRegex(hs.PendingError,'旧日期活动仍未完成'):
            self.arena(self.runner('run2'),'2026-11-09')


if __name__=='__main__':unittest.main()
