"""Battle adapters that observe and control only the user's current account."""
from __future__ import annotations

from copy import deepcopy
from datetime import datetime,timedelta
import hashlib
import json
from pathlib import Path
import time
from types import SimpleNamespace

import numpy as np

import hyol_scheduler as hs
import hyol_arena_3v3 as trio
from hyol_bond import BondRunner, BOND_READY_ROI, BOND_MATCH_ROI
from hyol_team_bond import TeamBondDriver
from hyol_errors import TaskUnavailable


BOND_CAPTAIN='bond_current_captain_badge_20261109.bmp'
BOND_CAPTAIN_ROI=(320,435,385,505)
BOND_MEMBER_NAME_ROI=(485,508,570,532)


def _current_frame(runner):
    s=runner.scheduler
    s.check_stop()
    frame=s.capture()
    if s._generic_active_tab_x(frame)!=runner.account.tab_x:
        raise hs.SafetyError('当前游戏标签已改变，未切回或操作其他账号')
    if s.offline_dialog_visible(frame):
        raise hs.AccountOffline(runner.account,'当前账号掉线，原队伍与战斗凭据保留')
    return frame


def _material(rows,kind):
    from hyol_checkpoints import encode_mask
    mask=np.asarray([[int(c) for c in row] for row in rows],dtype=np.uint8)
    return dict(mask=encode_mask(mask),shape=list(mask.shape),kind=kind)


def bond_party(frame,templates):
    """Two real name strips and the captain badge on the known lobby layout."""
    from hyol_bond_goals import lobby_identity
    anchor=templates.match(frame,hs.ASSETS/'bond_ready_anchor.bmp',BOND_READY_ROI,threshold=.98)
    if not anchor[0] or not templates.match(frame,hs.ASSETS/'bond_match_battle.bmp',BOND_MATCH_ROI,threshold=.98)[0]:
        return None
    dx,dy=anchor[2]-1111,anchor[3]-271
    shift=lambda roi:tuple(value+(dx if i%2==0 else dy) for i,value in enumerate(roi))
    if not templates.match(frame,hs.ASSETS/BOND_CAPTAIN,shift(BOND_CAPTAIN_ROI),threshold=.98)[0]:
        return None
    own=lobby_identity(frame,templates)
    if not own:return None
    rgb=np.asarray(frame.crop(shift(BOND_MEMBER_NAME_ROI)).convert('RGB'),dtype=np.int16)
    mask=((rgb.min(axis=2)>180)&(rgb.max(axis=2)-rgb.min(axis=2)<40)).astype(np.uint8)
    if int(mask.sum())<30:return None
    member=[''.join(map(str,row)) for row in mask]
    if own==member:return None
    signature=hashlib.sha256(json.dumps([own,member],separators=(',',':')).encode()).hexdigest()
    return dict(member_count=2,captain=True,lobby_rows=own,member_rows=member,signature=signature,
                member_token='observed_name_'+hashlib.sha256(mask.tobytes()).hexdigest()[:24],
                material=_material(own,'bond_lobby'))


def observe_bond_party(runner):
    found=[];proof=[]
    for sample in range(2):
        frame=_current_frame(runner)
        observation=bond_party(frame,runner.scheduler.templates)
        if observation is None:
            raise hs.PendingError('当前羁绊准备页未确认本号队长与真实二人队伍；保留队伍，未发送匹配')
        found.append(observation)
        proof.append(str(runner.scheduler.save_raw_evidence(frame,runner.account,
                                                            'current_bond_party_'+str(sample+1),'observed')))
        if sample==0:runner.scheduler.wait_interruptible(.25)
    if found[0]!=found[1]:raise hs.PendingError('当前羁绊队长或队友姓名未连续确认，未匹配')
    return dict(found[-1],samples=proof,evidence=proof[-1],observation_scope='current_account')


def bind_preserved_bond(runner,frame=None):
    """Bind the actual preserved lobby without manufacturing village identity.

    Uses the standalone Bond session/tab scope convention but leaves its old
    request untouched. This helper sends no game input and acquires no lease.
    """
    from hyol_accounts import game_session_token
    from hyol_bond_goals import lobby_identity
    from hyol_checkpoints import read_checkpoint,load_tasks
    from hyol_multi_solo import active_tab_centers,tab_close_positions
    s=runner.scheduler;frame=frame if frame is not None else s.capture()
    rows=lobby_identity(frame,s.templates)
    if not rows or not BondRunner.ready_visible(SimpleNamespace(scheduler=s),frame):
        raise hs.PendingError('当前羁绊准备页及队长姓名未确认，未关闭页面或匹配')
    markers=tab_close_positions(frame);centers=active_tab_centers(markers)
    tab_x=s._generic_active_tab_x(frame)
    if tab_x not in centers:raise hs.PendingError('当前羁绊浏览器标签未唯一确认')
    session=game_session_token(s)
    scope_key=hashlib.sha256((session+'|'+str(tab_x)).encode()).hexdigest()
    legacy_path=s.shared_state_directory/'bond/scopes'/(scope_key+'.json')
    legacy=read_checkpoint(legacy_path)
    if legacy:
        if (legacy.get('session')!=session or legacy.get('tab_x')!=tab_x
                or legacy.get('markers')!=list(markers) or legacy.get('lobby_rows')!=rows):
            raise hs.PendingError('当前羁绊页面与原会话姓名凭据不同，保留原轮次')
        original=read_checkpoint(s.shared_state_directory/'bond'/(hs.slug(legacy['owner'])+'.json'))
        if original and original.get('status')!='done':
            raise hs.PendingError('本号已有独立羁绊原轮次待核实，请沿原轮次继续；未创建新匹配')
    owner=legacy.get('owner') or 'bond_scope_'+hashlib.sha256((scope_key+json.dumps(rows)).encode()).hexdigest()[:32]
    account=hs.Account(centers.index(tab_x)+1,owner,tab_x)
    samples=[]
    for sample in range(3):
        current=s.capture()
        if (s._generic_active_tab_x(current)!=tab_x or game_session_token(s)!=session
                or not BondRunner.ready_visible(SimpleNamespace(scheduler=s),current)
                or lobby_identity(current,s.templates)!=rows):
            raise hs.PendingError('当前羁绊姓名、标签或会话未连续稳定，未关联新任务')
        samples.append(str(s.save_raw_evidence(current,account,'current_bond_binding_'+str(sample+1),'observed')))
        if sample<2:s.wait_interruptible(.2)
    s.config['TabXs']=','.join(map(str,centers))
    s.state=hs.StateStore([owner],directory=s.shared_state_directory)
    s.accounts=[account];s.by_name={owner:account};s.active=s._capture_account=account
    s.current_account_only=owner;s.generic_current_tab_x=tab_x;s._capture_binding_probe=False
    s.account_logs[owner]=hs.JsonlLogger(hs.LOGS/'accounts'/(hs.slug(owner)+'.jsonl'))
    runner.account=account;runner.path,runner.records=load_tasks(s.state,owner)
    proof=dict(owner=owner,session=session,tab_x=tab_x,markers=list(markers),lobby_rows=rows,
               samples=samples,evidence=samples[-1],material=_material(rows,'bond_lobby'),
               observation_scope='current_account',content_clicks=0,legacy_scope=legacy)
    path=s.shared_state_directory/'activities/current_team/bond_scopes'/(scope_key+'.json')
    proof['scope_path']=str(path);hs.atomic_json(path,proof)
    runner._current_team_binding=proof
    s.event('current_bond_page_bound',account,detail='当前羁绊队长姓名与原标签已三帧核实，页面和队伍保持不变',
            evidence=samples[-1],content_clicks=0,completion_scope='current_account')
    return proof


def _bond_goal(runner):
    req=runner.desktop_request
    mode=getattr(req,'bond_target_mode','battles')
    target=(getattr(req,'bond_count',5) if mode=='battles' else
            getattr(req,'bond_kill_target',160) if mode=='kills' else getattr(req,'bond_score_target',100))
    from hyol_bond_goals import validate_options
    validate_options(mode,getattr(req,'bond_kill_target',160),getattr(req,'bond_score_target',100),())
    if mode=='battles' and (type(target) is not int or not 1<=target<=99):
        raise ValueError('当前账号羁绊场数须为1至99')
    return dict(mode=mode,target=target)


def arena_activity_day(current=None):
    current=current or datetime.now(trio.MATCHING_TZ)
    if current.tzinfo is not None:current=current.astimezone(trio.MATCHING_TZ)
    return (current-timedelta(hours=5)).date().isoformat()


class CurrentBondDriver(TeamBondDriver):
    def __init__(self,runner,ledger,party):
        self.runner,self.ledger=runner,ledger
        self.scheduler=runner.scheduler;self._account=runner.account
        self.goal=_bond_goal(runner);self.request=ledger.record
        limit=self.goal['target'] if self.goal['mode']=='battles' else 99
        self.slot=dict(owner=ledger.owner,members=[ledger.owner,party['member_token']],
                       leader_slot=runner.account.index,team_evidence=party['evidence'])
        self.owner=SimpleNamespace(scheduler=self.scheduler,tasks=('bond',),preformed_duos=True,
                                   verify_all_combatants=False,save=ledger.save)
        rec=self.request
        if rec.get('goal') not in (None,self.goal):raise hs.PendingError('羁绊原目标未结清，未覆盖原场数或击杀目标')
        if rec.get('party_signature') not in (None,party['signature']):
            raise hs.PendingError('羁绊当前队伍与原轮次不同，保留原匹配')
        defaults=dict(status='running',phase='ready',completed=0,requested=limit,goal=self.goal,
                      run_id='current_bond_'+ledger.owner+'_'+self.scheduler.state.day,
                      owner=ledger.owner,members=self.slot['members'],verified_results=[])
        for key,value in defaults.items():rec.setdefault(key,value)
        if type(rec['completed']) is not int or not 0<=rec['completed']<=limit:
            raise hs.PendingError('羁绊原场数记录无效，未新建轮次')
        rec.update(party_signature=party['signature'],party_observation=party,
                   team_evidence=party['evidence'],observation_scope='current_account',
                   completion_scope='current_account',claim_policy='after_kill_target')
        sources=rec.setdefault('source_accounts',[])
        previous_result=rec.get('result_observation',{})
        if (previous_result.get('run_id')==rec['run_id'] and previous_result.get('owner')
                and previous_result['owner'] not in sources):
            sources.append(previous_result['owner'])
        if runner.account.name not in sources:sources.append(runner.account.name)
        rec['captain_lobby_identity']=dict(owner=ledger.owner,rows=party['lobby_rows'],
                                            samples=party['samples'],evidence=party['evidence'])
        self.watchdog=None;self.next_poll_seconds=.6
        self.scheduler._bond_observation_requests={runner.account.name:rec}
        ledger.attach_material(party['material']);ledger.check_history();ledger.save()

    def ensure_predecessors(self):
        return

    def member_accounts(self):
        return [(self.slot['owner'],self.account)]

    def start_battle(self,battle_no):
        frame=_current_frame(self.runner)
        party=bond_party(frame,self.scheduler.templates)
        if not party or party['signature']!=self.request['party_signature']:
            raise hs.PendingError('羁绊匹配前当前队长或队友姓名改变，未发送匹配')
        self.ledger.check_history()
        super().start_battle(battle_no)

    def close_ready(self):
        raise hs.SafetyError('当前账号羁绊结束后保留准备页，禁止退出队伍')

    def finish_members(self):
        raise hs.SafetyError('当前账号模式不退出或操作队友')

    def result_was_observed(self,battle_no):
        if super().result_was_observed(battle_no):return True
        row=self.request.get('result_observation',{})
        return (row.get('run_id')==self.request['run_id'] and row.get('battle')==battle_no
                and row.get('owner') in self.request.get('source_accounts',())
                and row.get('template') in ('bond_points_result','team_result_confirm.bmp','arena_result_confirm.bmp',hs.TEAM_REWARD_RESULT_TEMPLATE)
                and isinstance(row.get('evidence'),str) and Path(row['evidence']).is_file())

    def verify_completed(self):
        """Past battle receipts do not prove today's current activity page."""
        self.request['activity_ready']=False
        # run_bond has just collected two independent party frames. Keep that
        # proof and check one fresh frame, instead of repeating the whole pass.
        party=bond_party(_current_frame(self.runner),self.scheduler.templates)
        if not party or party['signature']!=self.request.get('party_signature'):
            raise hs.PendingError('羁绊旧完成记录与当前队伍不一致，未重复匹配')
        if self.goal['mode']=='battles':
            results=self.request.get('verified_results',[])
            complete=(self.request.get('completed')==self.request.get('requested')
                      and len(results)==self.request['completed']
                      and all(row.get('result_evidence') and row.get('ready_evidence') for row in results))
        else:
            from hyol_bond_goals import counters,claimed_tiers
            values=[]
            for sample in range(2):
                frame=_current_frame(self.runner)
                if not self.ready_visible(frame):raise hs.PendingError('羁绊旧目标复核时准备页变化')
                values.append(counters(frame,self.scheduler.templates))
                if sample==0:self.scheduler.wait_interruptible(.2)
            if values[0]!=values[1]:raise hs.PendingError('羁绊当前积分或击杀档位不稳定，旧完成结果未复用')
            current=values[-1]
            if self.goal['mode']=='score':complete=current['score']<=self.goal['target']
            else:
                target=self.goal['target'] or 160
                complete=(max(claimed_tiers(self.request,self.ledger.owner),default=0)>=target
                          and current['kill_cap']>target)
        if not complete:raise hs.PendingError('羁绊当前目标或原结算凭据未确认，保留旧成果，未匹配或领奖')
        evidence=str(self.scheduler.save_raw_evidence(_current_frame(self.runner),self.account,
                                                       'current_bond_completed_rechecked','verified'))
        self.request.setdefault('original_completion_evidence',self.request.get('evidence'))
        self.request.update(status='done',task_completed=True,activity_ready=True,home_returned=False,
                            evidence=evidence,completion_rechecked_at=hs.now_iso(),last_error='')
        self.ledger.save()

    def tick(self,task='bond'):
        frame=_current_frame(self.runner)
        if self.request.get('phase')=='done':return
        if self.request.get('phase')=='finishing':
            if not self.ready_visible_stable():raise hs.PendingError('本号羁绊目标结束后准备页尚未稳定，保留原场')
            party=bond_party(self.scheduler.capture(),self.scheduler.templates)
            if not party or party['signature']!=self.request['party_signature']:
                raise hs.PendingError('本号羁绊收尾队伍尚未核实，未退出或重开')
            proof=str(self.scheduler.save_raw_evidence(self.scheduler.capture(),self.account,'current_bond_kept_ready','verified'))
            self.request.update(status='done',phase='done',task_completed=True,activity_ready=True,
                                home_returned=False,completion_scope='current_account',evidence=proof,
                                finished_at=hs.now_iso(),last_error='')
            self.ledger.save()
            self.event('current_team_battle_complete',task='bond',completion_scope='current_account',
                       completed=self.request['completed'],evidence=proof,
                       detail='本号羁绊目标已核实，保留当前队伍与准备页')
            return
        return super().tick(task)


class CurrentArena3v3Driver(trio.Arena3v3Driver):
    def __init__(self,runner,ledger):
        self.runner,self.ledger=runner,ledger
        owner=SimpleNamespace(scheduler=runner.scheduler,preformed_trio=(runner.account.index,),
                              record={},save=ledger.save)
        slot=dict(tasks={trio.TASK:ledger.record})
        super().__init__(owner,runner.account,slot)
        from hyol_checkpoints import read_checkpoint
        activity_day=getattr(ledger,'day',self.s.state.day)
        intent_path=self.s.identity_directory/'arena3v3'/activity_day/'rewards.json'
        if intent_path!=self.intent_path:
            self.intent_path=intent_path
            self.intents=read_checkpoint(intent_path)
        legacy=any(str(token).startswith(ledger.owner+':') and isinstance(row,dict) and not row.get('action')
                   for token,row in self.intents.items())
        self.rec.setdefault('claim_action_scope','legacy' if legacy else 'activity_day')
        self.rec.setdefault('phase','entering');self.rec.setdefault('completed',0)
        if self.rec.get('phase')=='ready' and not self.rec.get('activity_identities'):
            self.rec['phase']='entering'
        if self.rec.get('status')=='new':self.rec['status']='running'
        self.rec.update(completion_scope='current_account',controlled_slots=[self.account.index])

    def account_for_slot(self,slot):
        if slot!=self.account.index:raise hs.SafetyError('当前账号3v3禁止访问队友标签')
        return self.account

    def observe_account(self,account):
        self.member_key(account)
        return _current_frame(self.runner)

    def member_key(self,account):
        if account.name!=self.account.name:raise hs.SafetyError('当前账号3v3禁止归属队友奖励')
        return self.ledger.owner

    def reward_owner(self,account):
        self.member_key(account)
        return self.ledger.owner

    def claim_action(self,kind,key):
        token=f'{self.ledger.owner}:{kind}:{key}'
        original=self.intents.get(token,{})
        if original.get('action'):return original['action']
        base=super().claim_action(kind,key)
        if self.rec.get('claim_action_scope')=='legacy':return base
        return base+'_day_'+getattr(self.ledger,'day',self.s.state.day).replace('-','')

    def rewards_complete(self):
        if set(self.rec.get('members',{}))!={self.ledger.owner}:return False
        row=self.rec['members'][self.ledger.owner]
        return (all(row.get('rewards',{}).get(key,{}).get('state')=='claimed' for key,_,_ in trio.REWARDS)
                and all(row.get('chests',{}).get(str(t),{}).get('state')=='claimed' for t in trio.MILESTONES))

    def settlement_detail(self):
        return '原场已观察到战斗且本号回到3v3准备页；未核实或操作队友页面'

    def event(self,name,*,account=None,**fields):
        fields['completion_scope']='current_account'
        if name=='settled':fields['detail']='本号原场已结束，仅复核本号任务和奖励'
        if name=='waiting':
            fields['detail']=str(fields.get('detail','')).replace('逐号','本号').replace('核实退出、无队与回村','核实本号完成并保留队伍')
        self.s.event('arena3v3_'+name,account or self.account,task=trio.TASK,**fields)

    def verify_party(self):
        observations=[];proof=[]
        for sample in range(2):
            frame=_current_frame(self.runner)
            if not trio.page_visible(frame,self.s.templates) or not trio.captain(frame,self.s.templates):
                raise hs.PendingError('当前3v3页面未确认本号为队长，保留页面，未匹配或操作队友')
            masks=[trio.row_mask(frame,i) for i in range(3)]
            if (any(int(mask.sum())<50 for mask in masks)
                    or any(hs.mask_f1(masks[a],masks[b])>=.98 for a in range(3) for b in range(a+1,3))):
                raise hs.PendingError('当前3v3准备页未确认三名不同成员，保留原队伍')
            observations.append(masks)
            proof.append(str(self.s.save_raw_evidence(frame,self.account,'current_3v3_party_'+str(sample+1),'observed')))
            if sample==0:self.s.wait_interruptible(.2)
        if any(hs.mask_f1(a,b)<.98 for a,b in zip(*observations)):
            raise hs.PendingError('当前3v3队伍姓名未连续稳定，未发送匹配')
        signature=hashlib.sha256(b''.join(mask.tobytes() for mask in observations[-1])).hexdigest()
        old=self.rec.get('party_signature')
        if old is not None and old!=signature:
            raise hs.PendingError('当前3v3队伍与原匹配不同，原场和奖励凭据保留')
        self.rec.update(party_signature=signature,party_observation=dict(member_count=3,
            captain=True,signature=signature,samples=proof,evidence=proof[-1],observation_scope='current_account'))
        mask=trio.identity_mask(frame,self.s.templates)
        aliases=getattr(self.s,'_arena3v3_masks',{})
        if self.account.index in aliases and hs.mask_f1(aliases[self.account.index],mask)<.98:
            raise hs.PendingError('当前3v3本号姓名发生变化')
        aliases[self.account.index]=mask;self.s._arena3v3_masks=aliases
        from hyol_checkpoints import encode_mask
        self.ledger.attach_material(dict(mask=encode_mask(mask),shape=list(mask.shape),kind='arena3v3_lobby'))
        self.ledger.check_history()
        return proof

    def reconcile_closed_unstarted(self):
        # One account cannot prove that the other two never entered a battle.
        return False

    def wait_for_open(self):
        self.rec.update(status='pending',phase='waiting_open',activity_ready=True,home_returned=False,
                        availability=dict(status='not_open',next_open_at=trio.next_matching_open().isoformat()))
        self.save()
        raise hs.PendingError('3v3匹配当前未开放（10:00—14:00、18:00—22:00），保留本号队伍和待续进度')

    def finish_exit(self):
        if not self.rewards_complete():raise hs.PendingError('本号3v3任务及宝箱尚未全部领取')
        frame=_current_frame(self.runner)
        if not trio.page_visible(frame,self.s.templates):raise hs.PendingError('本号3v3领奖后准备页未核实')
        self.rec['party_evidence']=self.verify_party()
        proof=str(self.s.save_raw_evidence(frame,self.account,'current_3v3_kept_ready','verified'))
        self.rec.update(status='done',phase='done',task_completed=True,activity_ready=True,
                        home_returned=False,completion_scope='current_account',evidence=proof,
                        finished_at=hs.now_iso(),last_error='')
        self.save();self.event('current_complete',evidence=proof,
                              detail='本号3v3任务及奖励已核实，保留队伍和准备页；队友状态未计入完成')

    def verify_completed(self):
        self.rec['activity_ready']=False
        if self.rec.get('activity_day')!=arena_activity_day():
            raise hs.PendingError('3v3活动日已变化，旧完成结果保留，当前奖励需要重新核实')
        frame=_current_frame(self.runner)
        if not trio.page_visible(frame,self.s.templates):
            if not self.s.main_ready(frame):raise hs.PendingError('3v3旧完成复核前当前页面未知，未发送导航')
            trio.enter_from_world(self.s,self.account)
            deadline=time.monotonic()+15
            while not trio.page_visible(_current_frame(self.runner),self.s.templates):
                if time.monotonic()>=deadline:raise hs.PendingError('3v3完成态复核尚未进入准备页，保留旧成果')
                self.s.wait_interruptible(.3)
        self.rec['party_evidence']=self.verify_party()
        samples=[];observations=[]
        for sample in range(2):
            frame=self.open_tasks(self.account)
            observed=trio.observe_rewards(self.s,frame)
            samples.append(str(self.s.save_raw_evidence(frame,self.account,'current_3v3_complete_recheck_'+str(sample+1),'observed')))
            observations.append(observed)
            if (not all(observed.get('rows',{}).get(key,{}).get('state')=='claimed' for key,_,_ in trio.REWARDS)
                    or not all(observed.get('chests',{}).get(str(t),{}).get('state')=='claimed' for t in trio.MILESTONES)):
                self.rec['completion_recheck']=dict(observed=observed,evidence=samples[-1],at=hs.now_iso())
                raise hs.PendingError('3v3本号当前奖励与旧完成状态不一致，保留旧成果，未重新匹配或领奖')
            if sample==0:self.s.wait_interruptible(.2)
        if observations[0]!=observations[1]:raise hs.PendingError('3v3当前奖励状态两帧不一致，未复用旧完成状态')
        member=self.rec['members'].setdefault(self.ledger.owner,{})
        member.update(rewards=observations[-1]['rows'],chests=observations[-1]['chests'],reward_evidence=samples[-1])
        self.rec.setdefault('original_completion_evidence',self.rec.get('evidence'))
        self.rec.update(status='done',task_completed=True,activity_ready=True,home_returned=False,
                        evidence=samples[-1],completion_recheck=dict(samples=samples,at=hs.now_iso()),last_error='')
        self.save();self.event('completed_rechecked',evidence=samples[-1],
                              detail='已重新进入并核实本号全部奖励为已领取；旧成果保留，本次未匹配或领奖')

    def tick(self,task=trio.TASK):
        _current_frame(self.runner)
        if self.rec.get('phase')=='waiting_open' and not trio.matching_open():
            return self.wait_for_open()
        return super().tick(task)


def _drive(runner,ledger,driver,task):
    before=ledger.record.get('completed',0)
    already_done=ledger.record.get('phase')=='done'
    def claims():
        if task=='arena_3v3':
            return sum(row.get('script_claims',0) for row in ledger.record.get('members',{}).values())
        return sum(row.get('status')=='done' for entries in ledger.record.get('kill_chest_claims',{}).values()
                   for row in entries.values())
    before_claims=claims()
    try:
        if already_done:
            driver.verify_completed()
        while ledger.record.get('phase')!='done':
            runner.scheduler.check_stop()
            driver.tick(task)
            ledger.save()
            if ledger.record.get('phase')!='done':
                runner.scheduler.wait_interruptible(min(30.,max(.1,float(driver.next_poll_seconds))))
    except (Exception,KeyboardInterrupt) as exc:
        ledger.record.update(status='pending',task_completed=False,last_error=str(exc))
        raise
    finally:
        ledger.record['completed_in_current_invocation']=max(0,ledger.record.get('completed',0)-before)
        ledger.record['claims_in_current_invocation']=max(0,claims()-before_claims)
        ledger.record['observed_previous_completion']=already_done
        ledger.record['observed_existing_completion']=(ledger.record.get('phase')=='done'
            and not ledger.record['completed_in_current_invocation'] and not ledger.record['claims_in_current_invocation'])
        ledger.save()


def run_bond(runner):
    from hyol_current_team_state import CurrentTeamLedger
    from hyol_current_team import observe_current_party
    s=runner.scheduler;frame=_current_frame(runner)
    world=s.main_ready(frame)
    if world:observe_current_party(runner,required=2)
    ledger=CurrentTeamLedger(runner,'bond',_bond_goal(runner))
    ledger.check_history()
    if world:
        # Reuse only the named daily-row navigation, before any matching.
        navigator=TeamBondDriver.__new__(TeamBondDriver)
        navigator.scheduler=s;navigator._account=runner.account
        if ledger.record.get('phase')=='done':
            ledger.record['activity_ready']=False
            navigator.request=ledger.record['completion_navigation']={}
        else:navigator.request=ledger.record
        navigator.owner=SimpleNamespace(save=ledger.save)
        deadline=time.monotonic()+100
        while bond_party(s.capture(),s.templates) is None:
            s.check_stop()
            if time.monotonic()>=deadline:raise hs.PendingError('羁绊入口未到达已核实二人准备页，保留原导航')
            navigator.enter_ready(_current_frame(runner));ledger.save();s.wait_interruptible(.5)
    party=observe_bond_party(runner)
    driver=CurrentBondDriver(runner,ledger,party)
    return _drive(runner,ledger,driver,'bond')


def run_arena3v3(runner):
    from hyol_current_team_state import CurrentTeamLedger
    from hyol_current_team import observe_current_party
    frame=_current_frame(runner)
    if runner.scheduler.main_ready(frame):observe_current_party(runner,required=3)
    day=arena_activity_day()
    ledger=CurrentTeamLedger(runner,'arena_3v3',dict(goal='all_current_account_rewards'),activity_day=day)
    ledger.record.setdefault('activity_day',day)
    ledger.check_history()
    driver=CurrentArena3v3Driver(runner,ledger)
    return _drive(runner,ledger,driver,'arena_3v3')
