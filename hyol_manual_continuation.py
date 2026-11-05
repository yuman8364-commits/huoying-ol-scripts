"""Keep externally interrupted inputs pending while a new remaining scope is audited."""
from copy import deepcopy
import hashlib
import json
from pathlib import Path
import uuid

import hyol_scheduler as hs


def quarantined_external_action(row, account=None):
    if not isinstance(row,dict) or row.get('status')!='pending':return False
    proof=row.get('external_interruption',{})
    if not isinstance(proof,dict) or proof.get('kind')!='user_manual_activity':return False
    if account is not None and proof.get('account')!=account:return False
    try:
        path=Path(proof['acknowledgement']);raw=path.read_bytes()
        ack=json.loads(raw.decode('utf-8-sig'))
        return (hashlib.sha256(raw).hexdigest()==proof['ack_sha256']
                and ack.get('source')=='user_manual_activity'
                and ack.get('run_id')==proof.get('run_id')
                and ack.get('execution_day')==proof.get('execution_day')
                and proof.get('execution_day')==hs.date.today().isoformat()
                and proof.get('account') in ack.get('accounts',{}).values()
                and type(proof.get('current_remaining')) is int
                and 0<=proof['current_remaining']<=10
                and hashlib.sha256(Path(proof['current_counter_evidence']).read_bytes()).hexdigest()
                    ==proof.get('current_counter_sha256')
                and Path(row.get('before_evidence','')).is_file())
    except (OSError,ValueError,KeyError,TypeError):return False


def prepare_remaining_ranking(r, acknowledgement):
    """Never settle the old outcome; open and read the ready page before a new namespace."""
    import hyol_ranking as ranking
    from hyol_navigation import preparation_blocked
    path=Path(acknowledgement);raw=path.read_bytes();ack=json.loads(raw.decode('utf-8-sig'))
    digest=hashlib.sha256(raw).hexdigest();s=r.scheduler;rec=r.record('ranking')
    context=getattr(s,'run_context',None)
    if (ack.get('source')!='user_manual_activity'
            or ack.get('run_id')!=getattr(context,'identifier',None)
            or ack.get('execution_day')!=s.state.day
            or r.account.name not in ack.get('accounts',{}).values()):
        raise hs.PendingError('手动活动确认与原账号、运行或日期不一致')
    if rec.get('manual_activity_ack_sha256')==digest:return
    if rec.get('status')=='done' and rec.get('zero_verified') is True:return
    if rec.get('phase') not in ('start_pending','confirmed'):
        raise hs.PendingError('当前原场仍需自己的结果处理，未切换剩余次数范围')
    before=rec.get('before')
    if type(before) is not int or not 1<=before<=10:
        raise hs.PendingError('原挑战次数未核实')
    if not ranking.original_start_witness_valid(r,rec):
        raise hs.PendingError('原开战和次数写前凭据未通过校验，未重建剩余范围')
    base=ranking.action_base(rec,rec.get('completed',0)+1)
    actions=s.state.accounts[r.account.name].get('actions',{})
    pending={name:row for name,row in actions.items() if isinstance(row,dict)
             and row.get('status')=='pending' and not quarantined_external_action(row,r.account.name)}
    allowed={base+'_start',base+'_confirm'}
    if not pending or not set(pending)<=allowed or any(
            not Path(row.get('before_evidence','')).is_file() for row in pending.values()):
        raise hs.PendingError('原账号有其他未决输入或凭据缺失，未重建剩余范围')
    frame=s.switch_account(r.account)
    if not r.visible('ranking_page.bmp',ranking.PAGE,frame,.95):
        for sample in range(3):
            if (not s.main_ready(frame) or preparation_blocked(s,frame)
                    or ranking.transition_loading_visible(frame)
                    or s._generic_active_tab_x(frame)!=r.account.tab_x
                    or not s.verify_identity(r.account,frame)[0]):
                raise hs.PendingError('手动操作后的空闲场景、标签和账号尚未连续确认')
            if sample<2:s.wait_interruptible(.2);frame=s.capture()
        s.ensure_activity_toolbar(r.account,'ranking_toolbar.bmp',ranking.TOOLBAR)
        r.click('ranking_toolbar.bmp',ranking.TOOLBAR,23,25,'ranking_manual_observe_open',['anchor_top_menu.bmp'])
        r.wait('ranking_page.bmp',ranking.PAGE)
    frame=s.capture()
    if preparation_blocked(s,frame) or r.visible('ranking_power_warning.bmp',(790,505,1130,560),frame,.95):
        raise hs.PendingError('原场仍有战斗、结果或确认框，未切换剩余范围')
    count=ranking.stable_remaining(r)
    frame=s.capture()
    if (preparation_blocked(s,frame) or ranking.remaining(r,frame,save_unknown=False)!=count
            or count>before or s._generic_active_tab_x(frame)!=r.account.tab_x
            or not s.verify_identity(r.account,frame)[0]):
        raise hs.PendingError('当前实际剩余次数或页面与原范围冲突')
    evidence=str(s.save_raw_evidence(frame,r.account,'ranking_after_manual_counter','observed'))
    counter_digest=hashlib.sha256(Path(evidence).read_bytes()).hexdigest()
    history=rec.setdefault('external_interruption_history',[])
    history.append({k:deepcopy(v) for k,v in rec.items() if k!='external_interruption_history'})
    for row in pending.values():
        row['external_interruption']=dict(kind='user_manual_activity',account=r.account.name,
            run_id=context.identifier,execution_day=s.state.day,
            acknowledgement=str(path.resolve()),ack_sha256=digest,
            current_counter_evidence=evidence,current_counter_sha256=counter_digest,
            current_remaining=count,outcome='unknown_preserved')
    s.state.save_account(r.account.name)
    rec.update(action_prefix='daily_ranking_manual_'+uuid.uuid4().hex[:12],phase='ready',status='pending',
        remaining=count,before=count,zero_verified=False,task_completed=False,home_returned=False,
        manual_activity_ack_sha256=digest,current_remaining_evidence=evidence,last_error='')
    rec.pop('manual_defeat_result_evidence',None);rec.pop('manual_defeat_result_sha256',None)
    r.save()
    r.event('ranking_remaining_scope_after_manual',remaining=count,evidence=evidence,
        old_pending=list(pending),detail='旧开战输入仍为pending且不计战果；当前三帧空闲页和实际次数另立动作编号')
