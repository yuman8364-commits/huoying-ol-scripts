"""Meaningful crash/resume coverage for unknown historical work, with no game input."""
from copy import deepcopy
from datetime import date,timedelta,datetime
import json
import os
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import Mock,patch

import hyol_scheduler as hs
import hyol_history as history
from hyol_checkpoints import load_tasks,read_checkpoint,source_token
from tools.prepare_generic_release import prepare,clean_config


class HistoryTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.yesterday = (date.today()-timedelta(days=1)).isoformat()
        self.stamp = self.yesterday+'T08:00:00+08:00'
        self.started = datetime.now().timestamp()-60
        self.token = f'1:2:{int((self.started+11644473600)*10_000_000):x}'
        self.key = 'arbitrary-role'
        self.source = self.root/self.yesterday/(hs.slug(self.key)+'.json')
        hs.atomic_json(self.source,dict(date=self.yesterday,account=self.key,actions={
            'arena_casual_battle_1':dict(status='pending',updated_at=self.stamp,detail='unknown result')}))
        self.original = self.source.read_bytes()
        self.account = hs.Account(1,self.key,180)
        self.state = hs.StateStore([self.key],directory=self.root)
        self.s = SimpleNamespace(config={'HistoricalPendingPolicy':'isolate_inactive'},
            state=self.state,session_token=self.token,event=Mock())
        self.proof = dict(session_token=self.token,observations=3,account=self.key,evidence='verified.png')

    def test_old_pending_is_archived_unknown_and_not_restored_or_marked_done(self):
        self.assertEqual(history.isolate_inactive_work(self.s,self.account,proof=self.proof),1)
        self.assertEqual(self.source.read_bytes(),self.original)
        self.assertEqual(read_checkpoint(self.source)['actions']['arena_casual_battle_1']['status'],'pending')
        state = hs.StateStore([self.key],directory=self.root)
        self.assertEqual(state.action_status(self.key,'arena_casual_battle_1'),'new')
        self.assertFalse((self.root/'checkpoint_resolutions.json').exists())
        entry = next(iter(state.accounts[self.key]['historical_unresolved'].values()))
        self.assertEqual(entry['outcome'],'historical_unknown')
        self.assertEqual(entry['record']['status'],'pending')
        self.assertTrue(any(Path(row['archive']).read_bytes()==self.original for row in entry['archives']))
        state.set_action(self.key,'arena_casual_battle_1','pending','current independent attempt')
        self.assertTrue(state.action_record(self.key,'arena_casual_battle_1')['operation_id'])
        restarted = hs.StateStore([self.key],directory=self.root)
        self.assertEqual(restarted.action_status(self.key,'arena_casual_battle_1'),'pending')
        self.assertNotIn('checkpoint_conflict',restarted.action_record(self.key,'arena_casual_battle_1'))

    def seed_mail_budget(self):
        old = dict(date=self.yesterday, account=self.key, updated_at=self.stamp,
            actions={'mail_claim_all':dict(status='pending', updated_at=self.stamp),
                     'mail_claim_all_network_3':dict(status='done', updated_at=self.stamp)},
            network_retries={'mail_claim_all':dict(attempts=3, verified=True)})
        hs.atomic_json(self.source, old)
        self.original = self.source.read_bytes()
        self.s.state = hs.StateStore([self.key], directory=self.root)

    def test_old_unknown_and_current_done_are_split_without_resolving_old_result(self):
        action = 'arena_casual_battle_1'
        done = dict(status='done', updated_at=hs.now_iso(), operation_id='current:1', detail='positive current result')
        self.state.accounts[self.key]['actions'][action] = deepcopy(done)
        self.state.save_account(self.key)
        self.s.state = hs.StateStore([self.key], directory=self.root)
        self.assertEqual('pending', self.s.state.action_status(self.key, action))
        self.assertEqual(1, history.isolate_inactive_work(self.s, self.account, proof=self.proof))
        restarted = hs.StateStore([self.key], directory=self.root)
        self.assertEqual(done, restarted.action_record(self.key, action))
        self.assertEqual(self.original, self.source.read_bytes())
        self.assertFalse((self.root/'checkpoint_resolutions.json').exists())
        entry = next(iter(restarted.accounts[self.key]['historical_unresolved'].values()))
        self.assertEqual('pending', entry['record']['status'])

    def test_old_unknown_is_removed_from_current_pending_conflict_only(self):
        action = 'arena_casual_battle_1'
        pending = dict(status='pending', updated_at=hs.now_iso(), operation_id='current:2', detail='unknown current result')
        self.state.accounts[self.key]['actions'][action] = deepcopy(pending)
        self.state.save_account(self.key)
        self.s.state = hs.StateStore([self.key], directory=self.root)
        self.assertEqual(1, history.isolate_inactive_work(self.s, self.account, proof=self.proof))
        restarted = hs.StateStore([self.key], directory=self.root)
        self.assertEqual(pending, restarted.action_record(self.key, action))
        self.assertEqual(self.original, self.source.read_bytes())

    def test_prior_survival_round_cannot_supply_current_completed_steps(self):
        current = self.state.accounts[self.key]['actions']
        current.clear()
        current['survival_battle_step_2'] = dict(status='pending', updated_at=hs.now_iso(), operation_id='current:2')
        current['survival_battle_step_4'] = dict(status='done', updated_at=self.stamp,
                                             continuation_context=self.yesterday)
        self.state.save_account(self.key)
        history.isolate_inactive_work(self.s, self.account, proof=self.proof)
        self.assertEqual('pending', self.state.action_status(self.key, 'survival_battle_step_2'))
        self.assertEqual('new', self.state.action_status(self.key, 'survival_battle_step_4'))
        archived = self.state.accounts[self.key]['historical_contexts']
        self.assertTrue(any(row['record']['status'] == 'done' for row in archived.values()))

    def test_inactive_retry_budget_archives_with_original_unknown_attempt(self):
        self.seed_mail_budget()
        self.assertEqual(history.isolate_inactive_work(self.s,self.account,proof=self.proof),2)
        restarted = hs.StateStore([self.key], directory=self.root)
        self.assertNotIn('mail_claim_all', restarted.accounts[self.key]['network_retries'])
        self.assertEqual('new', restarted.action_status(self.key,'mail_claim_all_network_3'))
        entry = next(iter(restarted.accounts[self.key]['historical_network_retries'].values()))
        self.assertEqual(dict(attempts=3,verified=True), entry['original'])
        self.assertEqual(self.original,self.source.read_bytes())
        self.assertTrue(any(Path(row['archive']).read_bytes()==self.original for row in entry['archives']))
        self.assertEqual('pending', read_checkpoint(self.source)['actions']['mail_claim_all']['status'])

    def test_upgrade_migrates_already_isolated_budget_and_allows_one_new_claim(self):
        self.seed_mail_budget()
        with patch.object(history,'inactive_retry_budgets',return_value={}):
            history.isolate_inactive_work(self.s,self.account,proof=self.proof)
        self.s.state = hs.StateStore([self.key], directory=self.root)
        self.assertFalse(self.s.state.accounts[self.key]['actions'])
        self.assertEqual(history.isolate_inactive_work(self.s,self.account,proof=self.proof),1)
        self.s.state = hs.StateStore([self.key], directory=self.root)
        self.assertEqual(history.isolate_inactive_work(self.s,self.account,proof=self.proof),0)
        import hyol_network_retry as retry
        clock, clicked = [0.], []
        self.s.check_stop=Mock(); self.s.wait_interruptible=lambda seconds:clock.__setitem__(0,clock[0]+seconds)
        self.s.commit_action=lambda account,key,detail:self.s.state.set_action(account.name,key,'done',detail)
        with patch.object(retry.time,'monotonic',side_effect=lambda:clock[0]):
            retry.run(self.s,self.account,'mail_claim_all','测试领取',lambda:bool(clicked),lambda:True,
                      lambda label:clicked.append(label) or True)
        self.assertEqual(['mail_claim_all_network_1'],clicked)
        self.assertEqual(1,self.s.state.accounts[self.key]['network_retries']['mail_claim_all']['attempts'])
        self.assertEqual(self.original,self.source.read_bytes())

    def test_current_claim_or_same_day_budget_prevents_historical_reset(self):
        for current_action in (True,False):
            with self.subTest(current_action=current_action):
                self.seed_mail_budget()
                if current_action:
                    self.s.state.set_action(self.key,'mail_claim_all_network_1','pending','today unknown')
                else:
                    self.s.state.accounts[self.key]['network_retries']['mail_claim_all']['source_day']=date.today().isoformat()
                before = deepcopy(self.s.state.accounts[self.key]['network_retries'])
                history.isolate_inactive_work(self.s,self.account,proof=self.proof)
                self.assertEqual(before,self.s.state.accounts[self.key]['network_retries'])
                if current_action:
                    self.assertEqual('pending',self.s.state.action_status(self.key,'mail_claim_all_network_1'))

    def test_already_isolated_budget_still_requires_idle_proof_and_exact_old_source(self):
        self.seed_mail_budget()
        with patch.object(history,'inactive_retry_budgets',return_value={}):
            history.isolate_inactive_work(self.s,self.account,proof=self.proof)
        today_path = self.s.state.account_paths[self.key]
        original_today = today_path.read_bytes()
        with patch.object(history,'prove_idle_identity',side_effect=hs.PendingError('battle visible')):
            with self.assertRaises(hs.PendingError):history.isolate_inactive_work(self.s,self.account)
        self.assertEqual(original_today,today_path.read_bytes())
        changed=read_checkpoint(self.source)
        changed['actions']['mail_claim_all']['checkpoint_conflict']=[dict(status='pending',updated_at=hs.now_iso())]
        hs.atomic_json(self.source,changed)
        self.assertEqual(history.isolate_inactive_work(self.s,self.account,proof=self.proof),0)
        self.assertEqual(3,self.s.state.accounts[self.key]['network_retries']['mail_claim_all']['attempts'])

    def test_exact_isolated_originals_do_not_resurrect_from_older_aggregate_copies(self):
        self.seed_mail_budget()
        earlier_day=(date.today()-timedelta(days=2)).isoformat()
        earlier=self.root/earlier_day/self.source.name
        original=dict(status='pending',updated_at=earlier_day+'T08:00:00+08:00',detail='first unknown')
        hs.atomic_json(earlier,dict(date=earlier_day,account=self.key,actions={'mail_claim_all':original}))
        earlier_bytes=earlier.read_bytes()
        latest=read_checkpoint(self.source)
        latest['actions']['mail_claim_all']=dict(original,
            origin=dict(path=str(earlier),token=source_token(earlier,'mail_claim_all',original)),
            checkpoint_conflict=[dict(status='pending',updated_at=self.stamp,detail='second unknown')])
        hs.atomic_json(self.source,latest)
        self.s.state=hs.StateStore([self.key],directory=self.root)
        history.isolate_inactive_work(self.s,self.account,proof=self.proof)
        restarted=hs.StateStore([self.key],directory=self.root)
        self.assertEqual('new',restarted.action_status(self.key,'mail_claim_all'))
        self.assertNotIn('mail_claim_all',restarted.accounts[self.key]['network_retries'])
        self.assertEqual(earlier_bytes,earlier.read_bytes())
        # A changed original still blocks; exact-copy exclusion cannot hide it.
        original['detail']='changed unknown'
        hs.atomic_json(earlier,dict(date=earlier_day,account=self.key,actions={'mail_claim_all':original}))
        self.assertEqual('pending',hs.StateStore([self.key],directory=self.root).action_status(self.key,'mail_claim_all'))

    def test_idle_proof_allows_available_task_entry_but_rejects_running_path_and_combat(self):
        self.s.active=self.account
        self.s.capture=Mock(return_value=object())
        self.s.check_stop=Mock();self.s.wait_interruptible=Mock()
        self.s.verify_identity=Mock(return_value=(True,{}))
        self.s.main_ready=Mock(return_value=True)
        self.s._battle_auto_on_visible=Mock(return_value=False)
        self.s._battle_hud_visible=lambda frame: hs.Scheduler._battle_hud_visible(self.s,frame)
        self.s.save_raw_evidence=Mock(return_value='idle.png')
        self.s.chrome_click=Mock()
        self.s.template=Mock(return_value=(False,0.,0,0))
        with patch('hyol_accounts.game_session_token',return_value=self.token):
            proof=history.prove_idle_identity(self.s,self.account)
            self.assertEqual(proof['observations'],3)
            self.assertEqual(self.s.capture.call_count,3)
            self.s.template.side_effect=lambda asset,*args,**kw:(asset=='treasure_auto_path_running.bmp',1.,0,0)
            with self.assertRaises(hs.PendingError):history.prove_idle_identity(self.s,self.account)
            self.s.template.side_effect=None;self.s._battle_auto_on_visible.return_value=True
            with self.assertRaises(hs.PendingError):history.prove_idle_identity(self.s,self.account)
            self.s._battle_auto_on_visible.return_value=False
            self.s.template.side_effect=lambda asset,*args,**kw:(
                asset in ('survival_battle_hud_left_current.bmp','survival_battle_hud_right_current.bmp'),1.,0,0)
            with self.assertRaises(hs.PendingError):history.prove_idle_identity(self.s,self.account)
        self.s.chrome_click.assert_not_called()

    def test_old_completed_battle_context_never_credits_a_fresh_isolated_attempt(self):
        old = read_checkpoint(self.source)
        old['actions']['arena_casual_battle_0'] = dict(status='done',updated_at=self.stamp)
        hs.atomic_json(self.source,old)
        original = self.source.read_bytes()
        self.s.state = hs.StateStore([self.key],directory=self.root)
        self.assertEqual(self.s.state.action_status(self.key,'arena_casual_battle_0'),'done')
        history.isolate_inactive_work(self.s,self.account,proof=self.proof)
        restarted = hs.StateStore([self.key],directory=self.root)
        self.assertEqual(restarted.action_status(self.key,'arena_casual_battle_0'),'new')
        context = next(iter(restarted.accounts[self.key]['historical_contexts'].values()))
        self.assertEqual(context['record']['status'],'done')
        self.assertEqual(self.source.read_bytes(),original)

    def test_current_pending_battle_retains_its_old_completed_context(self):
        old = read_checkpoint(self.source)
        old['actions']['arena_casual_battle_0'] = dict(status='done',updated_at=self.stamp)
        hs.atomic_json(self.source,old)
        self.s.state = hs.StateStore([self.key],directory=self.root)
        self.s.state.set_action(self.key,'arena_casual_battle_2','pending')
        history.isolate_inactive_work(self.s,self.account,proof=self.proof)
        restarted = hs.StateStore([self.key],directory=self.root)
        self.assertEqual(restarted.action_status(self.key,'arena_casual_battle_0'),'done')
        self.assertEqual(restarted.action_status(self.key,'arena_casual_battle_2'),'pending')

    def test_today_pending_and_post_session_historical_change_still_block(self):
        self.state.set_action(self.key,'mail_take','pending')
        self.assertEqual(history.isolate_inactive_work(self.s,self.account,proof=self.proof),1)
        self.assertEqual(self.state.action_status(self.key,'mail_take'),'pending')
        changed = read_checkpoint(self.source)
        changed['actions']['arena_casual_battle_1']['updated_at'] = hs.now_iso()
        hs.atomic_json(self.source,changed)
        state = hs.StateStore([self.key],directory=self.root)
        self.assertEqual(state.action_status(self.key,'arena_casual_battle_1'),'pending')
        self.s.state = state
        self.assertEqual(history.isolate_inactive_work(self.s,self.account,proof=self.proof),0)

    def test_added_conflict_child_invalidates_exact_source_isolation(self):
        history.isolate_inactive_work(self.s,self.account,proof=self.proof)
        changed = read_checkpoint(self.source)
        changed['actions']['arena_casual_battle_1']['checkpoint_conflict'] = [dict(
            status='pending',updated_at=hs.now_iso(),detail='new ambiguous attempt')]
        hs.atomic_json(self.source,changed)
        state = hs.StateStore([self.key],directory=self.root)
        self.assertEqual(state.action_status(self.key,'arena_casual_battle_1'),'pending')
        self.assertTrue(state.action_record(self.key,'arena_casual_battle_1')['checkpoint_conflict'])

    def test_dead_session_anonymous_isolation_keeps_owner_unknown_and_local_bytes(self):
        local = self.root/'local'
        previous_creation = int((self.started-86400+11644473600)*10_000_000)
        path = local/f'window_9_10_{previous_creation:x}'/self.yesterday/'anonymous.json'
        hs.atomic_json(path,dict(account='当前窗口标签2',actions={
            'mail_take':dict(status='pending',updated_at=self.stamp)}))
        original = path.read_bytes()
        self.s.shared_state_directory = self.root
        self.s.active = self.account
        with patch.object(hs,'STATE_DIR',local), \
             patch('hyol_accounts.game_session_token',return_value=self.token), \
             patch('hyol_accounts.session_is_alive',return_value=True):
            self.assertEqual(history.isolate_local_anonymous(self.s),0)
        with patch.object(hs,'STATE_DIR',local), \
             patch('hyol_accounts.game_session_token',return_value=self.token), \
             patch('hyol_accounts.session_is_alive',return_value=False), \
             patch.object(history,'prove_idle_identity',return_value=self.proof):
            self.assertEqual(history.isolate_local_anonymous(self.s),1)
        self.assertEqual(path.read_bytes(),original)
        entry = next(iter(read_checkpoint(self.root/'historical_isolations.json').values()))
        self.assertIsNone(entry['account'])
        self.assertEqual(entry['record']['status'],'pending')

    def test_cross_source_conflict_with_current_intent_is_never_isolated(self):
        row = self.state.accounts[self.key]['actions']['arena_casual_battle_1']
        row['checkpoint_conflict'] = [dict(status='pending',updated_at=hs.now_iso())]
        with patch.object(history,'prove_idle_identity') as prove:
            self.assertEqual(history.isolate_inactive_work(self.s,self.account),0)
        prove.assert_not_called()

    def test_failed_idle_proof_preserves_all_original_bytes(self):
        with patch.object(history,'prove_idle_identity',side_effect=hs.PendingError('battle visible')):
            with self.assertRaises(hs.PendingError): history.isolate_inactive_work(self.s,self.account)
        self.assertEqual(self.source.read_bytes(),self.original)
        self.assertEqual(self.state.action_status(self.key,'arena_casual_battle_1'),'pending')
        self.assertFalse((self.root/'historical_isolations.json').exists())

    def test_old_task_deadline_is_preserved_in_history_and_current_task_is_independent(self):
        oldpath = self.source.with_name(self.source.stem+'_tasks.json')
        old = dict(survival=dict(status='pending',last_progress=self.started-200,deadline_at=123,
                                task_completed=False,home_returned=False))
        hs.atomic_json(oldpath,old)
        os.utime(oldpath,(self.started-200,self.started-200))
        original = oldpath.read_bytes()
        path,records = load_tasks(self.state,self.key)
        self.assertEqual(records['survival']['continuation_day'],self.yesterday)
        history.isolate_inactive_work(self.s,self.account,records=records,task_path=path,proof=self.proof)
        self.assertNotIn('survival',records)
        archived = next(iter(records['_historical_unresolved'].values()))
        self.assertEqual(archived['record']['deadline_at'],123)
        self.assertEqual(oldpath.read_bytes(),original)
        self.assertNotIn('survival',load_tasks(hs.StateStore([self.key],directory=self.root),self.key)[1])

    def test_old_task_conflicts_can_be_isolated_but_current_conflict_children_cannot(self):
        oldpath = self.source.with_name(self.source.stem+'_tasks.json')
        for current_child in (True,False):
            child = dict(status='pending',updated_at=hs.now_iso() if current_child else self.stamp)
            hs.atomic_json(oldpath,dict(survival=dict(status='pending',updated_at=self.stamp,
                                                    checkpoint_conflict=[child])))
            os.utime(oldpath,(self.started-200,self.started-200))
            path,records = load_tasks(self.state,self.key)
            history.isolate_inactive_work(self.s,self.account,records=records,task_path=path,proof=self.proof)
            self.assertEqual('survival' in records,current_child)


class CleanReleaseTests(unittest.TestCase):
    def test_generic_config_has_no_local_names_paths_or_member_templates(self):
        config = clean_config('AccountMode=dynamic\nTabAccountNames=sample-user\nTeam1=sample-user\n'
                              'Team1InviteTemplate=personal.bmp\nSharedStateDirectory=X:\\private\\state\n')
        self.assertNotIn('sample-user',config)
        self.assertNotIn('X:\\private',config)
        self.assertNotIn('personal.bmp',config)
        self.assertIn('SharedStateDirectory=%LOCALAPPDATA%',config)

    def test_clean_bundle_excludes_personal_assets_receipts_and_keeps_generic_detector(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder); package = root/'installed'
            for name in ('client.exe','assets/identity_wanted_canyon_map_title_current.bmp',
                         'assets/accounts/private_identity.bmp','assets/friend_private_row.bmp',
                         'assets/team1_three_member_anchor.bmp','assets/team2_two_member_anchor.bmp',
                         'assets/team_invite_from_private.bmp','assets/detector.source.json',
                         'state/account_identities.json','logs/history.json'):
                path = package/name;path.parent.mkdir(parents=True,exist_ok=True);path.write_bytes(b'test')
            for name in ('HYOL_Daily.ini','DAILY_Client.ini','TEAM_Client.ini','ARENA_Client.ini'):
                (root/name).write_text('AccountDiscovery=automatic\n',encoding='utf-8')
            (root/'README_通用客户端.md').write_text('generic guide',encoding='utf-8')
            result = prepare(root,package,root/'clean')
            dest = Path(result['folder'])
            self.assertTrue((dest/'assets/identity_wanted_canyon_map_title_current.bmp').exists())
            self.assertFalse((dest/'state').exists());self.assertFalse((dest/'logs').exists())
            self.assertEqual(len(list((dest/'assets').rglob('*'))),1)
            self.assertTrue(Path(result['archive']).is_file())
            self.assertEqual((package/'state/account_identities.json').read_bytes(),b'test')


if __name__=='__main__': unittest.main()
