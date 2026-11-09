"""Limited three-person arena: observed identities and durable reward intents."""
from __future__ import annotations
from pathlib import Path
import hashlib
import time
import json
from datetime import datetime,timedelta,timezone
from functools import lru_cache
import numpy as np
import hyol_scheduler as hs

TASK='arena_3v3'
SUFFIX='_20261105.bmp'
ROWS=(352,462,572)
REWARDS=(('battle_1','完成一场战斗',1),('battle_3','完成三场战斗',3),
         ('party_3','组队完成三场战斗',3),('organization_1','同组织成员组队一场',1),
         ('win_1','首胜',1),('win_2','累计两胜',2))
MILESTONES=(2,4,6)
ROIS=dict(shell=(620,675,730,725),tasks_tab=(1060,465,1115,590),
          match=(750,790,945,865),close=(1605,95,1665,155),
          leader=(525,330,585,390),ninja_title=(1125,270,1300,330),entry=(1450,69,1690,220))
ENTRY_CURRENT='arena3v3_entry_current_20261106.bmp'
ENTRY_WRAP_CURRENT='arena3v3_entry_wrap_current_20261106.bmp'
CHEST_OPEN_CURRENT='arena3v3_chest_open_current_20261106.bmp'
MATCHING_TZ=timezone(timedelta(hours=8))


def matching_open(current=None):
    current=current or datetime.now(MATCHING_TZ)
    minutes=current.hour*60+current.minute
    return 10*60<=minutes<14*60 or 18*60<=minutes<22*60


def next_matching_open(current=None):
    current=current or datetime.now(MATCHING_TZ)
    if current.hour<10:return current.replace(hour=10,minute=0,second=0,microsecond=0)
    if current.hour<18:return current.replace(hour=18,minute=0,second=0,microsecond=0)
    return (current+timedelta(days=1)).replace(hour=10,minute=0,second=0,microsecond=0)


@lru_cache(maxsize=1)
def offset_templates():return hs.TemplateStore(.98)


def page_offset(frame,templates=None):
    """Two matched controls locate a translated activity canvas per tab."""
    if hasattr(frame,'_hyol_arena3v3_offset'):return frame._hyol_arena3v3_offset
    templates=templates or offset_templates();offsets=[]
    for key,origin in (('shell',(636,688)),('close',(1620,108))):
        result=templates.match(frame,hs.ASSETS/asset(key),ROIS[key],threshold=.98)
        if not result[0]:return None
        offsets.append((result[2]-origin[0],result[3]-origin[1]))
    if offsets[0]!=offsets[1] or max(map(abs,offsets[0]))>24:return None
    frame._hyol_arena3v3_offset=offsets[0]
    return offsets[0]


def canonical_frame(frame,templates=None):
    offset=page_offset(frame,templates)
    if offset is None:raise hs.PendingError('3v3页面两个定位控件未一致，未读取姓名或任务坐标')
    if offset==(0,0):return frame
    cached=getattr(frame,'_hyol_arena3v3_canonical',None)
    if cached is None:
        cached=hs.Image.new('RGB',frame.size);cached.paste(frame,(-offset[0],-offset[1]))
        cached._hyol_arena3v3_offset=(0,0)
        frame._hyol_arena3v3_canonical=cached
    return cached


def asset(key):return 'arena3v3_'+key+SUFFIX


def match(templates,frame,key,roi=None):
    if templates is None:return False
    path=hs.ASSETS/asset(key)
    bounds=roi or ROIS[key]
    offset=page_offset(frame,templates) if key!='entry' else None
    if offset:bounds=tuple(v+offset[index%2] for index,v in enumerate(bounds))
    return path.is_file() and templates.match(frame,path,bounds,threshold=.98)[0]


def page_visible(frame,templates):
    return (match(templates,frame,'shell') and match(templates,frame,'close')
            and any(match(templates,frame,key,ROIS['tasks_tab']) for key in
                    ('tasks_tab','tasks_tab_member','tasks_tab_active')))


def rewards_page_visible(frame,templates):
    return (match(templates,frame,'tasks_title',(1100,280,1190,365))
            and match(templates,frame,'task_first',(1195,365,1390,435))
            and match(templates,frame,'task_last',(1195,765,1390,835)))


def own_index(frame):
    frame=canonical_frame(frame)
    chosen=[]
    for index,y in enumerate(ROWS):
        rgb=np.asarray(frame.crop((380,y-1,520,y+17)).convert('RGB'),dtype=np.int16)
        red,green,blue=(rgb[:,:,n] for n in range(3))
        if float(((blue>red+18)&(red>green+12)&(blue>65)).mean())>.18:chosen.append(index)
    if len(chosen)!=1:raise hs.PendingError('3v3本号高亮成员不唯一，未关联身份')
    return chosen[0]


def identity_mask(frame,templates):
    if not page_visible(frame,templates):return None
    y=ROWS[own_index(frame)];frame=canonical_frame(frame,templates)
    rgb=np.asarray(frame.crop((371,y,516,y+20)).convert('RGB'),dtype=np.int16)
    mask=((rgb.min(axis=2)>180)&((rgb.max(axis=2)-rgb.min(axis=2))<40)).astype(np.uint8)
    out=np.zeros((32,145),dtype=np.uint8);out[12:]=mask
    if int(out.sum())<50:raise hs.PendingError('3v3本号姓名不完整，保留当前队伍')
    return out


def legacy_identity_mask(frame,templates):
    """Reproduce pre-offset masks solely to validate archived binding images."""
    if not page_visible(frame,templates):return None
    chosen=[]
    for index,y in enumerate(ROWS):
        rgb=np.asarray(frame.crop((380,y-1,520,y+17)).convert('RGB'),dtype=np.int16)
        red,green,blue=(rgb[:,:,n] for n in range(3))
        if float(((blue>red+18)&(red>green+12)&(blue>65)).mean())>.18:chosen.append(index)
    if len(chosen)!=1:return None
    y=ROWS[chosen[0]];rgb=np.asarray(frame.crop((371,y,516,y+20)).convert('RGB'),dtype=np.int16)
    out=np.zeros((32,145),dtype=np.uint8)
    out[12:]=((rgb.min(axis=2)>180)&((rgb.max(axis=2)-rgb.min(axis=2))<40)).astype(np.uint8)
    return out if int(out.sum())>=50 else None


def captain(frame,templates):
    return page_visible(frame,templates) and own_index(frame)==0 and match(templates,frame,'leader')


def normalize_tasks(tasks):
    tasks=tuple(tasks)
    return tuple(t for t in tasks if t!=TASK)+((TASK,) if TASK in tasks else ())


def validate_trio(trio):
    if (not isinstance(trio,(list,tuple)) or len(trio)!=3 or
            any(type(v) is not int or not 1<=v<=6 for v in trio) or len(set(trio))!=3):
        raise ValueError('竞技场3v3须指定队长和两名不同队员标签（1～6）')
    return tuple(trio)


for _key,_roi in ROIS.items():hs.GUARD_ANCHOR_ROIS[asset(_key)]=_roi
for _key in ('tasks_tab_member','tasks_tab_active'):hs.GUARD_ANCHOR_ROIS[asset(_key)]=ROIS['tasks_tab']
hs.GUARD_ANCHOR_ROIS[asset('tasks_title')]=(1100,280,1190,365)
hs.GUARD_ANCHOR_ROIS[ENTRY_CURRENT]=ROIS['entry']
hs.GUARD_ANCHOR_ROIS[ENTRY_WRAP_CURRENT]=ROIS['entry']


def enter_from_world(s,account):
    frame=s.switch_account(account)
    for name in (ENTRY_WRAP_CURRENT,ENTRY_CURRENT,asset('entry')):
        if (hs.ASSETS/name).is_file() and s.template(name,ROIS['entry'],frame,threshold=.98)[0]:
            with hs.Image.open(hs.ASSETS/name) as image:width,height=image.size
            if s.click_template(account,name,ROIS['entry'],width//2,height//2,
                    'arena3v3_enter',[name,'anchor_top_menu.bmp'],threshold=.98,anchor_threshold=.98):
                return
            raise hs.PendingError('3v3入口在复核时变化，未重复进入')
    raise hs.PendingError('3v3活动入口文字未核实，保留现有队伍')


def white_mask(image):
    rgb=np.asarray(image.convert('RGB'),dtype=np.int16)
    return (rgb.min(axis=2)>200)&(rgb.max(axis=2)-rgb.min(axis=2)<35)


def trim(mask):
    ys,xs=np.nonzero(mask)
    return mask[min(ys):max(ys)+1,min(xs):max(xs)+1] if len(xs) else mask[:0,:0]


@lru_cache(maxsize=1)
def counter_glyphs():
    value=json.loads((hs.ASSETS/'arena3v3_counter_glyphs.json').read_text('utf-8'))
    return [(int(item['digit']),trim(np.asarray([[c=='1' for c in row] for row in item['rows']])))
            for item in value['variants']]


def progress_count(frame,index):
    frame=canonical_frame(frame)
    shift=(0,0,28,98,0,0)[index]
    x,y=1301+shift,411+80*index
    observed=trim(white_mask(frame.crop((x,y,x+6,y+10))))
    scores=[]
    for digit,reference in counter_glyphs():
        if observed.shape==reference.shape:
            score=2*int((observed&reference).sum())/max(1,int(observed.sum()+reference.sum()))
            scores.append((score,digit))
    scores.sort(reverse=True)
    if not scores or scores[0][0]<.98:return None
    return scores[0][1]


def observe_rewards(s,frame):
    """All counters are independent visual observations; unknown never means 0."""
    if not match(s.templates,frame,'tasks_title',(1100,280,1190,365)):
        raise hs.PendingError('3v3任务奖励页未确认')
    offset=page_offset(frame,s.templates);frame=canonical_frame(frame,s.templates)
    from hyol_ocr import recognize
    # Only the reward status column needs OCR. Small white counters use exact
    # game glyphs, avoiding OCR's loss of the slash and the denominator.
    words=[word for line in recognize(frame.crop((1525,375,1610,835))).get('lines',[])
           for word in line.get('words',[])]
    rows={}
    for index,(key,label,target) in enumerate(REWARDS):
        text=''.join(w['text'] for w in words if abs(w['y']-(20+80*index))<30).replace(' ','')
        count=progress_count(frame,index)
        if ('已领取' in text or '已领' in text
                or match(s.templates,frame,'claimed',(1520,380+80*index,1620,430+80*index))):state='claimed'
        elif count is not None and count<target:state='locked'
        elif count==target and '领取' in text:state='available'
        else:state='unknown'
        rows[key]=dict(label=label,current=count,target=target,state=state)
    chests={}
    achieved=sum(r['current']==r['target'] or r['state']=='claimed' for r in rows.values())
    for tier,x in zip(MILESTONES,(1238,1328,1418)):
        roi=(x-10,285,x+65,345)
        opened=any((hs.ASSETS/name).is_file() and s.templates.match(frame,hs.ASSETS/name,roi,threshold=.98)[0]
                   for name in (asset('chest_open'),CHEST_OPEN_CURRENT))
        closed=match(s.templates,frame,'chest_closed',roi)
        chests[str(tier)]=dict(state='claimed' if opened else
                             'available' if closed and achieved>=tier else 'locked' if closed else 'unknown')
    return dict(rows=rows,chests=chests,offset=offset)


def observed_account(s,account):
    """Observe a bound browser title without authorizing any game input."""
    s.check_stop()
    frame=s.capture()
    if s.run_context and not s.run_context.layout_matches(frame):raise hs.PendingError('3v3标签布局变化，保留原队')
    if s._generic_active_tab_x(frame)!=account.tab_x:
        s.chrome_click(account.tab_x,10,'arena3v3_observe_tab',account);s.wait_interruptible(.35)
        frame=s.capture()
    if s.offline_dialog_visible(frame):raise hs.AccountOffline(account,'3v3成员掉线，保留原队')
    def black(f):
        pixels=np.asarray(f.crop((270,110,1640,990)).resize((32,20)).convert('RGB'))
        return float((pixels.max(axis=2)<8).mean())>.995
    if black(frame):
        for _ in range(6):
            s.wait_interruptible(.35);frame=s.capture()
            if not black(frame):break
        if black(frame):
            detail=f'标签{account.index}持续黑屏，保留原3v3场次；恢复原账号页面后继续'
            s.event('arena3v3_member_pending',account,task=TASK,detail=detail)
            raise hs.PendingError(detail)
    title=getattr(s,'_tab_title_masks',{}).get(account.index)
    if title is not None and hs.mask_f1(title,s._title_mask(frame,account.tab_x))<.70 and title.any():
        raise hs.PendingError('3v3成员浏览器标题改变，未关联原场')
    return frame


def row_mask(frame,index):
    frame=canonical_frame(frame)
    y=ROWS[index]
    rgb=np.asarray(frame.crop((371,y,516,y+20)).convert('RGB'),dtype=np.int16)
    return ((rgb.min(axis=2)>180)&(rgb.max(axis=2)-rgb.min(axis=2)<40)).astype(np.uint8)


def verify_party(s,trio):
    signatures=[];owners=[];proofs=[]
    aliases=getattr(s,'_arena3v3_masks',{})
    for n,slot in enumerate(validate_trio(trio)):
        account=s.accounts[slot-1];samples=[]
        ready_deadline=time.monotonic()+15
        for sample in range(3):
            frame=observed_account(s,account)
            while not page_visible(frame,s.templates):
                if time.monotonic()>=ready_deadline:raise hs.PendingError('3v3三名成员尚未全部进入准备页')
                s.wait_interruptible(.3);frame=observed_account(s,account)
            own=own_index(frame)
            if (n==0) != captain(frame,s.templates):raise hs.PendingError('3v3队长与指定标签不符')
            samples.append(identity_mask(frame,s.templates))
            if sample<2:s.wait_interruptible(.15)
        if any(hs.mask_f1(samples[0],m)<.98 for m in samples[1:]):raise hs.PendingError('3v3成员姓名尚未稳定')
        signatures.append([row_mask(frame,i) for i in range(3)]);owners.append(own)
        if slot in aliases and hs.mask_f1(aliases[slot],samples[-1])<.98:raise hs.PendingError('3v3本号身份改变，保留原场')
        aliases[slot]=samples[-1]
        proofs.append(str(s.save_raw_evidence(frame,account,'arena3v3_party','verified')))
    if set(owners)!={0,1,2} or any(hs.mask_f1(signatures[0][r],sig[r])<.98 for sig in signatures[1:] for r in range(3)):
        raise hs.PendingError('3v3成员重复或三人队伍名单不一致')
    s._arena3v3_masks=aliases
    return proofs


def alias_verified(s,account,frame):
    reference=getattr(s,'_arena3v3_masks',{}).get(account.index)
    if reference is not None and page_visible(frame,s.templates):
        score=hs.mask_f1(reference,identity_mask(frame,s.templates))
        return score>=.98,{account.name:score},True
    reference=getattr(s,'_arena3v3_home_masks',{}).get(account.index)
    if reference is not None and s.main_ready(frame):
        from hyol_checkpoints import current_identity_mask
        score=hs.mask_f1(reference,current_identity_mask(frame,s.templates))
        return score>=.98,{account.name:score},True
    return None


def verified(rec,members):
    if (rec.get('status')!='done' or not rec.get('task_completed') or not rec.get('home_returned')
            or not rec.get('evidence') or set(rec.get('members',{}))!=set(members)):return False
    return all(r.get('exited') is True and r.get('unteamed_evidence') and r.get('home_evidence')
               and all(r.get('rewards',{}).get(key,{}).get('state')=='claimed' for key,_,_ in REWARDS)
               and all(r.get('chests',{}).get(str(t),{}).get('state')=='claimed' for t in MILESTONES)
               for r in rec['members'].values())


def result_template(s,frame):
    roi=(750,340,1170,685)
    if match(s.templates,frame,'victory',roi) and match(s.templates,frame,'result_confirm',roi):
        return asset('result_confirm'),asset('victory'),'win'
    if (match(s.templates,frame,'result_scroll',(320,320,375,715))
            and match(s.templates,frame,'result_confirm',roi)):
        return asset('result_confirm'),asset('result_scroll'),'unknown'
    result=s._team_result_template(frame)
    return (result,result,'unknown') if result else None


hs.GUARD_ANCHOR_ROIS[asset('victory')]=(750,340,1170,685)
hs.GUARD_ANCHOR_ROIS[asset('result_confirm')]=(750,340,1170,685)
hs.GUARD_ANCHOR_ROIS[asset('result_scroll')]=(320,320,375,715)


class Arena3v3Driver:
    def __init__(self,owner,account,slot):
        self.owner,self.s,self.account,self.slot=owner,owner.scheduler,account,slot
        self.trio=tuple(owner.preformed_trio)
        self.rec=slot.setdefault('tasks',{}).setdefault(TASK,dict(status='running',phase='entering',members={},completed=0))
        members=self.rec.setdefault('members',{})
        for name in tuple(members):
            canonical=self.s.state.aliases.get(name,name)
            if canonical!=name:
                if canonical in members and members[canonical]!=members[name]:
                    raise hs.PendingError('3v3成员原凭据存在身份冲突，保留原记录')
                members[canonical]=members.pop(name)
        self.intent_path=self.s.identity_directory/'arena3v3'/self.s.state.day/'rewards.json'
        from hyol_checkpoints import read_checkpoint
        self.intents=read_checkpoint(self.intent_path)
        self.last_heartbeat=0
        self.watchdogs={}
        from hyol_checkpoints import decode_mask
        aliases=getattr(self.s,'_arena3v3_masks',{})
        for key,value in self.rec.get('activity_identities',{}).items():
            aliases[int(key)]=decode_mask(value['mask'],tuple(value['shape']))
        if aliases:self.s._arena3v3_masks=aliases

    def record(self,task=TASK):return self.rec

    def member_key(self,account):return self.s.state.aliases.get(account.name,account.name)

    def account_for_slot(self,slot):
        return self.s.accounts[slot-1]

    def observe_account(self,account):
        return observed_account(self.s,account)

    def reward_owner(self,account):
        return str(self.s.state.accounts[account.name].get('account_id',account.name))

    def claim_action(self,kind,key):
        return 'arena3v3_claim_'+kind+'_'+key

    def verify_party(self):
        return verify_party(self.s,self.trio)

    def rewards_complete(self):
        return (len(self.rec['members'])==3
                and all(all(r['rewards'][k]['state']=='claimed' for k,_,_ in REWARDS)
                        and all(r['chests'][str(t)]['state']=='claimed' for t in MILESTONES)
                        for r in self.rec['members'].values()))

    def settlement_detail(self):
        return '原场已观察到战斗，三人均回到3v3准备页'

    def pending_rewards(self,slots=None):
        owners={self.reward_owner(self.account_for_slot(i))+':'
                for i in self.trio if slots is None or i in slots}
        return any(any(str(token).startswith(owner) for owner in owners) and row.get('status')=='pending'
                   for token,row in self.intents.items() if isinstance(row,dict))

    def save(self):
        hs.atomic_json(self.intent_path,self.intents);self.owner.save()

    def event(self,name,*,account=None,**fields):
        self.s.event('arena3v3_'+name,account or self.account,task=TASK,**fields)

    @property
    def next_poll_seconds(self):return 30 if self.rec.get('phase')=='waiting_open' else .6

    def match_action(self):
        return self.rec.get('match_action','arena3v3_match_'+str(self.rec.get('completed',0)+1))

    def battle_key(self):
        return str(self.rec.get('match_sequence',self.rec.get('completed',0)+1))

    def wait_for_open(self):
        next_at=next_matching_open().isoformat()
        self.rec.update(phase='waiting_open',availability=dict(status='not_open',next_open_at=next_at,
            windows=['10:00–14:00','18:00–22:00']))
        self.save();self.event('waiting_open',next_open_at=next_at,
            detail='3v3匹配未开放（10:00–14:00、18:00–22:00）；保留三人队伍，等待下一时段，不重复匹配')

    def reconcile_closed_unstarted(self):
        """A closed-window request with three untouched zero-goal lobbies never battled."""
        sent=self.rec.get('match_sent_at')
        if (self.rec.get('phase')!='matching' or self.rec.get('battle_seen')
                or self.rec.get('completed',0)!=0 or type(sent) not in (int,float)
                or time.time()-sent<30):return False
        started=datetime.fromtimestamp(sent,MATCHING_TZ)
        if started.date().isoformat()!=self.s.state.day or matching_open(started):return False
        action=self.match_action()
        if self.s.state.action_status(self.account.name,action)!='pending':return False
        def zero(observed):
            return (set(observed.get('rows',{}))=={key for key,_,_ in REWARDS}
                and set(observed.get('chests',{}))==set(map(str,MILESTONES))
                and all(row.get('current')==0 and row.get('state')=='locked' for row in observed['rows'].values())
                and all(row.get('state')=='locked' for row in observed['chests'].values()))
        for slot in self.trio:
            a=self.account_for_slot(slot);member=self.rec.get('members',{}).get(self.member_key(a),{})
            if not zero(dict(rows=member.get('rewards',{}),chests=member.get('chests',{}))):return False
        self.rec['party_evidence']=self.verify_party()
        evidence=[]
        for slot in self.trio:
            a=self.account_for_slot(slot)
            for sample in range(3):
                frame=self.open_tasks(a)
                if not zero(observe_rewards(self.s,frame)):return False
                evidence.append(str(self.s.save_raw_evidence(frame,a,'arena3v3_closed_idle_zero','verified')))
                if sample<2:self.s.wait_interruptible(.2)
        frame=self.observe_account(self.account)
        if not match(self.s.templates,frame,'match'):return False
        self.rec.setdefault('rejected_matches',[]).append(dict(action=action,status='not_started_closed_window_verified',
            matched_at=sent,evidence=evidence,counted_as_battle=False,observed_at=hs.now_iso()))
        self.s.commit_action(self.account,action,'原请求位于游戏关闭时段；三人原零进度及可匹配准备页连续核实，未进战斗、未计场数')
        self.rec['match_sequence']=max(self.rec.get('match_sequence',0),int(action.rsplit('_',1)[1]))
        if matching_open():self.rec['phase']='rewards';self.save()
        else:self.wait_for_open()
        return True

    def open_tasks(self,a):
        s=self.s;frame=self.observe_account(a)
        if not (page_visible(frame,s.templates) or rewards_page_visible(frame,s.templates)):
            raise hs.PendingError('3v3任务页打开前准备页未确认')
        s.active=a
        if not match(s.templates,frame,'tasks_title',(1100,280,1190,365)):
            key='tasks_tab' if match(s.templates,frame,'tasks_tab') else 'tasks_tab_member'
            if not s.click_template(a,asset(key),ROIS['tasks_tab'],15,45,'arena3v3_open_tasks',[asset('shell')],threshold=.98,anchor_threshold=.98):
                raise hs.PendingError('3v3任务标签未确认')
            s.wait_interruptible(.25)
        return s.content_guard(a,[asset('tasks_title')],anchor_threshold=.98)

    def verify_previous_scope(self):
        """Observe old unresolved play without replaying it or erasing its outcome."""
        prior=self.owner.record.get('previous_arena_scope')
        if not prior or self.rec.get('previous_scope_verification',{}).get('verified') is True:return
        from hyol_checkpoints import read_checkpoint,decode_mask
        source=self.s.identity_directory/'runs'/prior['run_id']/'team_pipeline_latest.json'
        original=read_checkpoint(source);fingerprints=[]
        old_trio=original.get('request',{}).get('preformed_trio',())
        for team in original.get('teams',{}).values():
            task=team.get('tasks',{}).get(TASK,{})
            for slot,material in task.get('activity_identities',{}).items():
                if int(slot) not in old_trio:raise hs.PendingError('历史3v3身份与原队伍不一致，未开始当前任务')
                pos=list(old_trio).index(int(slot));names=team.get('members',())
                if pos>=len(names):raise hs.PendingError('历史3v3成员凭据不完整，保留原结果')
                member=task.get('members',{}).get(names[pos],{})
                fingerprints.append((decode_mask(material['mask'],tuple(material['shape'])),member))
        if not fingerprints:raise hs.PendingError('历史3v3身份凭据缺失，未以新任务绕过原场')
        scores={};overlap=[]
        for slot in self.trio:
            reference=self.s._arena3v3_masks[slot]
            ranked=sorted(((hs.mask_f1(reference,mask),index) for index,(mask,_member) in enumerate(fingerprints)),reverse=True)
            scores[str(slot)]=[round(score,6) for score,_index in ranked]
            best,index=ranked[0]
            if .85<=best<.98:raise hs.PendingError('当前3v3成员与历史未决身份存在歧义，未匹配或领奖')
            if best>=.98:overlap.append(fingerprints[index][1])
        observations={}
        if overlap:
            had_progress=any(any(row.get('state')=='claimed' or type(row.get('current')) is int and row['current']>0
                for row in member.get('rewards',{}).values()) for member in overlap)
            if not had_progress:raise hs.PendingError('历史3v3原场尚未核实，且没有可对照的重置凭据；未重开原场')
            for slot in self.trio:
                account=self.account_for_slot(slot);frame=self.open_tasks(account)
                observed=observe_rewards(self.s,frame)
                if (any(row.get('current')!=0 or row.get('state')!='locked' for row in observed['rows'].values())
                        or any(row.get('state')!='locked' for row in observed['chests'].values())):
                    raise hs.PendingError('历史3v3进度尚未正向确认已重置，保留原匹配与奖励结果')
                observations[str(slot)]=dict(observed=observed,evidence=str(self.s.save_raw_evidence(
                    frame,account,'arena3v3_current_goals_reset','verified')))
        self.rec['previous_scope_verification']=dict(verified=True,
            kind='server_goals_reset_observed' if overlap else 'distinct_live_roles',
            original_run_id=prior['run_id'],original_outcome='unknown',counted_as_completed=False,
            source=str(source),source_sha256=hashlib.sha256(source.read_bytes()).hexdigest(),
            identity_scores=scores,observations=observations,observed_at=hs.now_iso())
        self.save();self.event('historical_scope_preserved',detail='历史3v3原结果仍未知；当前身份或任务重置已独立核实，不重发旧匹配')

    def reward_cycle(self,slots=None):
        for i in self.trio if slots is None else slots:
            a=self.account_for_slot(i);frame=self.open_tasks(a)
            observed=observe_rewards(self.s,frame)
            r=self.rec['members'].setdefault(self.member_key(a),dict(rewards={},chests={},script_claims=0))
            r.update(rewards=observed['rows'],chests=observed['chests'])
            for kind,items in (('row',observed['rows']),('chest',observed['chests'])):
                for key,item in items.items():
                    token=f'{self.reward_owner(a)}:{kind}:{key}'
                    intent=self.intents.get(token,{})
                    if item['state']=='claimed':
                        if intent.get('status')=='pending':
                            intent.update(status='done',evidence=str(self.s.save_raw_evidence(frame,a,'arena3v3_claim_'+kind+'_'+key,'verified')))
                            action=intent.get('action') or self.claim_action(kind,key)
                            if self.s.state.action_status(a.name,action)=='pending':
                                self.s.commit_action(a,action,'3v3奖励页正向确认已领取')
                            r['script_claims']+=1;self.save()
                        continue
                    if intent.get('status')=='pending':
                        raise hs.PendingError('3v3原奖励领取结果仍待核实，未重复点击：'+kind+' '+key)
                    if intent.get('status')=='done' and item['state']!='claimed':
                        raise hs.PendingError('3v3奖励页与已领取凭据不一致，未重发领取：'+key)
                    if item['state']=='unknown':raise hs.PendingError('3v3奖励状态未识别：'+kind+' '+key)
                    if item['state']!='available':continue
                    before=str(self.s.save_raw_evidence(frame,a,'arena3v3_claim_before','pending'))
                    action=self.claim_action(kind,key)
                    self.intents[token]=dict(status='pending',before=before,kind=kind,key=key,action=action)
                    self.save()
                    if kind=='row':
                        row=next(n for n,v in enumerate(REWARDS) if v[0]==key)
                        point=(1569,404+80*row)
                    else:point=({2:1260,4:1350,6:1440}[int(key)],316)
                    offset=observed.get('offset',(0,0))
                    point=(point[0]+offset[0],point[1]+offset[1])
                    def still_available(fresh):
                        if not match(self.s.templates,fresh,'tasks_title',(1100,280,1190,365)):return False
                        latest=observe_rewards(self.s,fresh)
                        return (latest.get('offset',(0,0))==offset
                                and latest['rows' if kind=='row' else 'chests'][key]['state']=='available')
                    self.s.click_point(a,*point,action,[asset('tasks_title')],
                                       irreversible=True,anchor_threshold=.98,frame_predicate=still_available)
                    from hyol_navigation import move_cursor_clear
                    move_cursor_clear(self.s)
                    self.event('claim_sent',account=a,tab_index=i,detail='已点击3v3奖励，正在核实领取结果',reward=token)
                    self.s.wait_interruptible(.4)
                    # Re-enter observation from the durable pending intent.
                    return False
            r['reward_evidence']=str(self.s.save_raw_evidence(frame,a,'arena3v3_rewards','observed'))
            self.save()
            if (r['rewards']['organization_1']['state']=='locked'
                    and all(value['state']=='claimed' for key,value in r['rewards'].items() if key!='organization_1')):
                raise hs.PendingError('其余3v3任务已达成，同组织组队任务仍未达成；保留奖励缺项，不继续无效刷场')
        return self.rewards_complete()

    def enter(self):
        s=self.s
        frame=self.observe_account(self.account)
        if not page_visible(frame,s.templates):
            s.switch_account(self.account)
            enter_from_world(s,self.account)
            s.wait_interruptible(.5)
        self.rec['party_evidence']=self.verify_party()
        from hyol_checkpoints import encode_mask
        self.rec['activity_identities']={str(i):dict(mask=encode_mask(s._arena3v3_masks[i]),shape=[32,145]) for i in self.trio}
        self.verify_previous_scope()
        self.rec['phase']='rewards';self.save()

    def tick(self,task=TASK):
        s=self.s;s.check_stop()
        phase=self.rec.get('phase','entering')
        if time.monotonic()-self.last_heartbeat>=5:
            self.last_heartbeat=time.monotonic()
            labels=dict(entering='核对三人队伍',rewards='逐号核实任务和奖励',matching='等待原匹配进入战斗',waiting_open='等待3v3匹配开放',
                        battle='等待原场战斗结束',exiting='核实退出、无队与回村')
            self.event('waiting',detail='3v3：'+labels.get(phase,phase))
        if phase=='done':return
        if phase=='waiting_open':
            if not matching_open():return
            self.rec.update(phase='rewards',availability=dict(status='open',observed_at=hs.now_iso()))
            self.save();phase='rewards'
        if phase=='entering':self.enter();return
        if phase=='exiting':self.finish_exit();return
        if phase in ('matching','battle'):
            if phase=='matching' and self.reconcile_closed_unstarted():return
            self.observe_battle();return
        self.verify_previous_scope()
        if self.reward_cycle():
            self.rec['phase']='exiting';self.save();return
        # A sent reward is always observed before a new match.
        if self.pending_rewards():return
        if not matching_open():self.wait_for_open();return
        self.rec['party_evidence']=self.verify_party()
        leader=self.account;frame=self.observe_account(leader);s.active=leader
        if not match(s.templates,frame,'match'):
            raise hs.PendingError('3v3匹配按钮未确认，保留当前任务')
        actions=s.state.accounts[leader.name].get('actions',{})
        sequence=max([self.rec.get('match_sequence',0),self.rec.get('completed',0),
            *[int(key.rsplit('_',1)[1]) for key in actions if key.startswith('arena3v3_match_') and key.rsplit('_',1)[1].isdigit()]])+1
        action='arena3v3_match_'+str(sequence)
        self.rec.update(phase='matching',match_sent_at=time.time(),battle_seen=False,
                        match_sequence=sequence,match_action=action)
        self.save()
        if not s.click_template(leader,asset('match'),ROIS['match'],70,23,action,[asset('shell')],irreversible=True,threshold=.98,anchor_threshold=.98):
            raise hs.PendingError('3v3匹配前画面变化，原匹配意图保留待核实')
        self.event('matching',detail='队长已匹配，等待本队进入战斗')

    def verify_battle_auto(self,a,slot,frame):
        """Each participant is positively checked once in each original battle."""
        s=self.s
        checks=self.rec.setdefault('battle_auto_checks',{}).setdefault(self.battle_key(),{})
        key=self.member_key(a)
        if checks.get(key,{}).get('verified') is True:return
        enabled=False;s.active=a
        if s.template('battle_auto_off.bmp',hs.BATTLE_AUTO_ROI,frame,threshold=.98)[0]:
            if not s.click_template(a,'battle_auto_off.bmp',hs.BATTLE_AUTO_ROI,29,29,
                                    'arena3v3_auto',['battle_auto_off.bmp'],threshold=.98,anchor_threshold=.98):
                raise hs.PendingError('3v3自动战斗按钮变化')
            if not s._wait_battle_auto_on(5):raise hs.PendingError('3v3本号自动战斗尚未核实')
            frame=s.capture();enabled=True
        if not s._battle_auto_on_visible(frame):
            if result_template(s,frame) or page_visible(frame,s.templates):return
            raise hs.PendingError('3v3本号自动战斗状态待核实')
        proof=str(s.save_raw_evidence(frame,a,'arena3v3_auto_first_check','verified'))
        checks[key]=dict(verified=True,tab_index=slot,enabled_by_script=enabled,
                         evidence=proof,observed_at=hs.now_iso())
        self.save();self.event('auto_verified',account=a,tab_index=slot,evidence=proof,
            detail='本场首次巡查：自动战斗已开启' if enabled else '本场首次巡查：自动战斗原本已开启')

    def observe_battle(self):
        s=self.s
        checks=self.rec.get('battle_auto_checks',{}).get(self.battle_key(),{})
        frames={}
        if all(checks.get(self.member_key(self.account_for_slot(slot)),{}).get('verified') is True for slot in self.trio):
            frame=self.observe_account(self.account);frames[self.account.index]=frame
            if (not page_visible(frame,s.templates) and not rewards_page_visible(frame,s.templates)
                    and not result_template(s,frame) and s._battle_hud_visible(frame)):
                # All three were checked once. Watch the captain until the
                # original battle ends, then collect each participant's result.
                watchdog=self.watchdogs.setdefault(self.account.index,hs.BattleWatchdog.from_config(s.config))
                hs.check_battle_watchdog(s,watchdog,self.account,frame,'arena3v3_battle',mode='arena')
                self.rec.setdefault('battle_last_visible',{}).setdefault(self.battle_key(),{})[self.member_key(self.account)]=time.time()
                self.save();return
        all_lobby=True
        for slot in self.trio:
            a=self.account_for_slot(slot);frame=frames.get(slot)
            if frame is None:frame=self.observe_account(a)
            if page_visible(frame,s.templates) or rewards_page_visible(frame,s.templates):
                # A healthy member may collect positively settled rewards
                # while another member remains unresolved. No new match is
                # authorized until the whole original party is back.
                settled=self.rec.get('battle_results',{}).get(self.battle_key(),{}).get(a.name)
                if settled:
                    self.reward_cycle((slot,))
                    if self.pending_rewards((slot,)):
                        self.save();return
                continue
            all_lobby=False
            result=result_template(s,frame)
            if result:
                button,anchor,outcome=result;s.active=a
                action='arena3v3_result_'+self.battle_key()
                if s.state.action_status(a.name,action)=='pending':raise hs.PendingError('原3v3结算确认待核实，未重发')
                proof=str(s.save_raw_evidence(frame,a,'arena3v3_result_'+outcome,'verified'))
                self.rec.setdefault('battle_results',{}).setdefault(self.battle_key(),{})[a.name]=dict(outcome=outcome,evidence=proof)
                self.save()
                if not s.click_template(a,button,(650,340,1250,850),60,16,action,[anchor],irreversible=True,threshold=.98,anchor_threshold=.98):
                    raise hs.PendingError('3v3结算确认按钮变化')
                if not s.wait_absent(button,(650,340,1250,850),8,threshold=.98):raise hs.PendingError('3v3结算确认后仍待处理')
                s.commit_action(a,action,'3v3原结算控件已确认消失')
                self.rec['battle_seen']=True
            elif s._battle_hud_visible(frame):
                self.rec['battle_seen']=True;self.rec['phase']='battle';s.active=a
                self.rec.setdefault('battle_last_visible',{}).setdefault(self.battle_key(),{})[self.member_key(a)]=time.time()
                self.verify_battle_auto(a,slot,frame)
                watchdog=self.watchdogs.setdefault(slot,hs.BattleWatchdog.from_config(s.config))
                hs.check_battle_watchdog(s,watchdog,a,frame,'arena3v3_battle',mode='arena')
            elif s.template('arena_loading_anchor.bmp',hs.ARENA_LOADING_ROI,frame,threshold=.98)[0]:
                visible=self.rec.setdefault('battle_last_visible',{}).setdefault(self.battle_key(),{})
                first=self.member_key(a) not in visible
                visible[self.member_key(a)]=time.time()
                if first:
                    proof=str(s.save_raw_evidence(frame,a,'arena3v3_member_loading','observed'))
                    self.event('loading',account=a,tab_index=slot,evidence=proof,
                        detail='本号仍在原场等待玩家加载；自动控件出现后再检查，不重开匹配')
                watchdog=self.watchdogs.setdefault(slot,hs.BattleWatchdog.from_config(s.config))
                hs.check_battle_watchdog(s,watchdog,a,frame,'arena3v3_loading',mode='arena')
            elif time.time()-self.rec.get('battle_last_visible',{}).get(self.battle_key(),{}).get(
                    self.member_key(a),self.rec.get('match_sent_at',time.time()))>120:
                raise hs.PendingError('3v3匹配或结算页面待识别，保留原场次')
        if all_lobby and self.rec.get('battle_seen'):
            action=self.match_action()
            s.commit_action(self.account,action,self.settlement_detail())
            self.rec.update(phase='rewards',completed=self.rec.get('completed',0)+1)
            self.watchdogs.clear()
            self.event('settled',detail='本场已结束，逐号复核任务和奖励')
        elif all_lobby and time.time()-self.rec.get('match_sent_at',time.time())>120:
            raise hs.PendingError('原3v3匹配等待超过120秒，保留原匹配，不重复发起')
        self.save()

    def finish_exit(self):
        from hyol_checkpoints import current_identity_mask
        from hyol_navigation import return_to_leaf
        from hyol_team_pipeline import verify_unteamed
        s=self.s
        leader_frame=self.observe_account(self.account)
        if page_visible(leader_frame,s.templates):
            action='arena3v3_exit'
            if s.state.action_status(self.account.name,action)=='new':
                s.active=self.account
                if not s.click_template(self.account,asset('close'),ROIS['close'],15,15,action,[asset('shell')],irreversible=True,threshold=.98,anchor_threshold=.98):
                    raise hs.PendingError('3v3退出按钮未核实')
                self.save();s.wait_interruptible(.5)
            else:raise hs.PendingError('原3v3退出结果待核实，未重复点击')
        for slot in self.trio:
            a=self.account_for_slot(slot);samples=[]
            for n in range(3):
                frame=self.observe_account(a)
                if not s.main_ready(frame):raise hs.PendingError('3v3退出后尚未确认三人回到主界面')
                samples.append(current_identity_mask(frame,s.templates))
                if n<2:s.wait_interruptible(.2)
            if min(int(v.sum()) for v in samples)<50 or any(hs.mask_f1(samples[0],v)<.98 for v in samples[1:]):
                raise hs.PendingError('3v3退出后的实名画面尚未稳定')
            aliases=getattr(s,'_arena3v3_home_masks',{});aliases[slot]=samples[-1];s._arena3v3_home_masks=aliases;s.active=a
            proof=verify_unteamed(s,a)
            if return_to_leaf(s,a,allow_pending_navigation=True) is not True:raise hs.PendingError('3v3退出后回村未核实')
            home=str(s.save_raw_evidence(s.capture(),a,'arena3v3_exit_home','verified'))
            self.rec['members'][self.member_key(a)].update(exited=True,unteamed_evidence=proof,home_evidence=home)
            # Carry the positively observed same-tab exit transition into the
            # following solo stage. The old activity identity and exit proof
            # remain in the task record; only this run's live binding changes.
            if s.run_context and s.run_context.binding:
                from hyol_checkpoints import encode_mask
                binding=s.run_context.binding
                owner=binding['mapping'][slot]
                binding['observations'][slot]=tuple(samples)
                binding['masks'][slot]=samples[-1]
                binding['frames'][slot]=s.capture()
                binding['refs'][owner]={**binding['refs'][owner],'mask':encode_mask(samples[-1]),'shape':list(samples[-1].shape)}
                binding.setdefault('prepared_accounts',{})[owner]=dict(evidence=home,samples=[])
                s._bound_identity_masks[slot]=samples[-1]
                s._known_identity_materials=binding['refs']
            self.save()
        s.commit_action(self.account,'arena3v3_exit','三人退出、无队与木叶回村均核实；没有额外解散操作')
        self.rec.update(status='done',phase='done',task_completed=True,home_returned=True,
                        evidence=self.rec['members'][self.member_key(self.account)]['home_evidence'])
        self.save();self.event('complete',detail='三人六项任务奖励和三个宝箱全部核实领取，退出解散并回村')
