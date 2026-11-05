"""A missing idle entry must not strand later tasks; combat custody remains strict."""
from copy import deepcopy
from types import SimpleNamespace
import time
import unittest
from unittest.mock import Mock
from PIL import Image

import hyol_multi_solo as multi
import hyol_scheduler as hs


class SkipContinuationTests(unittest.TestCase):
    def setup_runner(self, *, combat=False):
        accounts=[hs.Account(2,'morning-tab-2',310),hs.Account(3,'following-tab-3',450)]
        records={a.name:{'ranking':dict(status='new',phase='confirmed' if combat else 'ready',completed=0)}
                 for a in accounts}
        coordinator=multi.MultiSoloRunner.__new__(multi.MultiSoloRunner)
        coordinator.tasks=('ranking','welfare')
        coordinator.manual_deferred=set()
        coordinator.startup_returned={a.name for a in accounts}
        coordinator.deferred={}
        coordinator.deferred_at={}
        coordinator.excluded=set()
        coordinator.disconnected=set()
        coordinator.event=Mock()
        coordinator.save_progress=Mock()
        coordinator.return_account_to_origin=Mock()
        coordinator.check_account=Mock(return_value=True)
        coordinator.skipped_tasks={a.name:['welfare'] for a in accounts}
        s=SimpleNamespace(accounts=accounts,by_name={a.name:a for a in accounts},active=None,
            navigator=None,_task_observation=None,check_stop=Mock(),check_pause_boundary=Mock(return_value=False),
            capture=Mock(return_value=Image.new('RGB',(1920,1032))),
            main_ready=Mock(return_value=True),verify_identity=Mock(return_value=(True,{})),
            template=Mock(return_value=(True,1.,0,0)),_combat_active_visible=Mock(return_value=combat),
            ensure_account_main_before_release=Mock(return_value=True),wait_interruptible=Mock())
        s._saved_tab_mapping=lambda:{a.name:a.tab_x for a in accounts}
        s._generic_active_tab_x=lambda frame:s.active.tab_x
        def switch(account,*args,**kwargs):s.active=account
        s.switch_account_for_combat=Mock(side_effect=switch)
        actions={a.name:dict(actions={'daily_ranking_1_start':dict(status='pending')} if combat else {}) for a in accounts}
        s.state=SimpleNamespace(accounts=actions,action_status=lambda name,key:
            actions[name]['actions'].get(key,{}).get('status','new'))
        coordinator.scheduler=s
        runners={}
        for account in accounts:
            runner=SimpleNamespace(account=account,records=records[account.name],save=Mock(),return_to_main=Mock())
            runner.record=lambda task,owner=account.name:records[owner].setdefault(task,dict(status='new'))
            def ranking(account=account):
                if account.index==2 or combat:raise hs.PendingError('活动入口和折叠菜单按钮均未确认')
                records[account.name]['ranking'].update(zero_verified=True,remaining=0)
            runner.ranking=ranking
            runners[account.name]=runner
        coordinator.runner_for=lambda account:runners[account.name]
        return coordinator,accounts,records

    def test_missing_tab_two_entry_keeps_record_and_allows_later_task_and_account(self):
        coordinator,accounts,records=self.setup_runner()
        self.assertTrue(coordinator._run_ranking_stage())
        self.assertEqual('pending',records[accounts[0].name]['ranking']['status'])
        self.assertEqual('done',records[accounts[1].name]['ranking']['status'])
        self.assertNotIn(accounts[0].name,coordinator.deferred)
        self.assertEqual(['ranking'],coordinator.blocked_tasks[accounts[0].name])
        seen=[]
        def finish(runner,task):
            seen.append((runner.account.index,task))
            runner.record(task).update(status='done',home_returned=True)
        coordinator._run_task=finish
        self.assertTrue(coordinator.run_skipped_tasks())
        self.assertEqual([(2,'welfare'),(3,'welfare')],seen)
        self.assertEqual('pending',records[accounts[0].name]['ranking']['status'])

    def test_real_pending_battle_stays_deferred_and_never_released_as_idle(self):
        coordinator,accounts,records=self.setup_runner(combat=True)
        original=deepcopy(coordinator.scheduler.state.accounts)
        self.assertTrue(coordinator._run_ranking_stage())
        self.assertEqual({a.name:'ranking' for a in accounts},coordinator.deferred)
        self.assertEqual(original,coordinator.scheduler.state.accounts)
        coordinator.return_account_to_origin.assert_not_called()
        self.assertFalse(hasattr(coordinator,'blocked_tasks'))

    def test_action_resume_reacquires_account_despite_throttled_background_probe(self):
        coordinator,accounts,_=self.setup_runner()
        account=accounts[0]
        coordinator.deferred[account.name]='ranking'
        coordinator.deferred_at[account.name]=time.monotonic()-300
        coordinator.deferred_probed_at={account.name:time.monotonic()}
        coordinator.stall_checks={}
        coordinator.exclusion_reasons={}
        coordinator.scheduler.active=accounts[1]
        self.assertFalse(coordinator.probe_deferred(account))
        coordinator.scheduler.switch_account_for_combat.assert_not_called()
        self.assertTrue(coordinator.probe_deferred(account,force=True))
        self.assertEqual(account,coordinator.scheduler.active)

    def test_failed_reacquisition_does_not_authorize_task_execution(self):
        coordinator,accounts,_=self.setup_runner()
        account=accounts[0]
        coordinator.deferred[account.name]='ranking'
        coordinator.stall_checks={}
        coordinator.exclusion_reasons={}
        coordinator.scheduler.switch_account_for_combat.side_effect=hs.PendingError('身份未核实')
        self.assertFalse(coordinator.probe_deferred(account,force=True))
        self.assertEqual('ranking',coordinator.deferred[account.name])


if __name__=='__main__':unittest.main()
