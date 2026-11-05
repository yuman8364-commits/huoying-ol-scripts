"""Reconcile the archived refused selection on its own current counter page."""
from pathlib import Path
import sys
import time
from copy import deepcopy
from PIL import Image

sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
import hyol_scheduler as hs
from hyol_checkpoints import read_checkpoint
from hyol_team_pipeline import TeamPipelineRunner,LeaderActivityRunner,PAGE_ANCHORS,PAGE_ROIS


def main():
    saved=read_checkpoint(hs.STATE_DIR/'team_pipeline_latest.json')
    request=saved['request']
    owner=TeamPipelineRunner(tuple(request['leaders']),tuple(request['team_tasks']),tuple(request['solo_tasks']),
                             arena_wins=request['arena_wins'],formation=request['formation'],
                             managed_enabled=request['final_managed'])
    s=owner.scheduler;s.no_content_clicks=True
    lease=hs.ForegroundRuntimeLease()
    if not lease.acquire():raise hs.PendingError('已有前台工作线程')
    report=dict(at=hs.now_iso(),batch=saved['id'],operation='原被拒入场的对应副本次数对账',new_battles=0)
    try:
        s.start_f12_monitor()
        expected={i:saved['teams'][str(i)]['owner'] for i in owner.leaders}
        located=s.reassociate_captains(expected)
        s.bind_session(tuple(located['physical_tabs']),allow_unavailable=True,leaders_only=True,
                       tab_positions=located['positions'])
        owner.restore()
        candidates=[(int(i),slot) for i,slot in owner.record['teams'].items()
                    if slot.get('tasks',{}).get('group',{}).get('intent',{}).get('label')==
                       f'group_{slot.get("tasks",{}).get("group",{}).get("completed",0)+1}_select']
        if len(candidates)!=1:raise hs.PendingError('原被拒入场请求不唯一，不更改')
        index,slot=candidates[0]
        if s._bound_identity_names.get(index)!=slot['owner']:raise hs.PendingError('原队长实名未核实')
        account=s.accounts[index-1];driver=LeaderActivityRunner(owner,account,slot);rec=slot['tasks']['group']
        action=driver.action(rec['intent']['label'])
        if s.state.action_status(account.name,action)!='pending':raise hs.PendingError('原选择不处于未决状态')
        archived=Path(slot['last_error_evidence'])
        with Image.open(archived) as warning:
            if not driver.visible('group_solo_warning_current.bmp',(760,480,1150,540),warning,.95):
                raise hs.PendingError('原拒入场截图未核实')
        completed=rec['completed']
        s.no_content_clicks=False
        frame=s.switch_account(account)
        if s.template('friends_panel_close.bmp',(1870,260,1920,350),frame,threshold=.98)[0]:
            s._close_friends(account)
        prior=rec.get('counter_recovery_navigation')
        if prior and prior.get('navigation',{}).get('unknown_page_evidence') and not prior['navigation'].get('last_progress_at'):
            rec.setdefault('counter_recovery_navigation_history',[]).append(deepcopy(prior))
            rec['counter_recovery_navigation']=dict(completed=completed,phase='ready')
        audit=rec.setdefault('counter_recovery_navigation',dict(completed=completed,phase='ready'))
        owner.save()
        for _ in range(50):
            s.check_stop();frame=s.switch_account(account)
            if driver.visible(PAGE_ANCHORS['group'],PAGE_ROIS['group'],frame,.98):break
            driver.navigate('group',audit,frame);s.wait_interruptible(.6)
        else:raise hs.PendingError('原被拒选择对应副本页未确认')
        rec.setdefault('entry_rejection',dict(selection=dict(rec['intent']),before_evidence=str(archived),
                                             cancellation='用户重新上线并恢复队伍后原提示已消失'))
        rec['phase']='rejected_entry';owner.save()
        for _ in range(3):
            frame=s.switch_account(account);driver.reconcile_rejected_group_entry(rec,frame);s.wait_interruptible(.35)
        if rec['completed']!=completed or rec.get('intent') or rec.get('phase')!='verify_members':
            raise hs.PendingError('原被拒入场结果仍未核实')
        report.update(result='rejected_without_battle',completed=completed,
                      daily=rec['daily_before'],friendship=rec['friend_before'],
                      evidence=rec['entry_rejections'][-1]['evidence'],captain_positions=located,
                      content_clicks=s.content_click_count)
        print(report,flush=True)
    finally:
        hs.atomic_json(hs.LOGS/'pipeline_rejected_entry_reconciled_20261007.json',report)
        s.stop.set();lease.release()


if __name__=='__main__':main()
