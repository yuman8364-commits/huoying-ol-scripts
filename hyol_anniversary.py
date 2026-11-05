"""Limited Xinhuo sign-in for the dedicated anniversary activity section.

Only live unlocked cards authorize claims. Campaign receipts survive daily
and run journals, so a new Start cannot replay an unknown reward request.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from functools import lru_cache
from pathlib import Path
import time
import uuid

import numpy as np
from PIL import Image
import hyol_scheduler as hs

CAMPAIGN = 'xinhuo-2026-11'
LAST_DAY = 10
TITLE_ROI = (880,210,1180,310)
SHELL_ROI = (540,270,620,330)
DEADLINE_ROI = (735,325,940,355)
CLOSE_ROI = (1325,280,1370,325)
ROW_ROI = (540,350,640,740)
CLAIM_ROI = (805,557,990,625)
PREVIOUS_ROI = (740,685,805,755)
NEXT_ROI = (1255,685,1320,755)
CENTERS = (848,941,1033,1125,1217)
SUFFIX = '_20261105.bmp'


def china_day():
    return datetime.now(timezone(timedelta(hours=8))).date().isoformat()


def name(key):
    return 'anniversary_'+key+SUFFIX


def matches(s, key, roi, frame):
    return s.template(name(key),roi,frame,threshold=.98)[0]


def page_visible(s, frame):
    return (matches(s,'title',TITLE_ROI,frame)
            and matches(s,'deadline',DEADLINE_ROI,frame)
            and matches(s,'close',CLOSE_ROI,frame))


def shell_visible(s, frame):
    return matches(s,'shell',SHELL_ROI,frame) and matches(s,'close',CLOSE_ROI,frame)


def color_fraction(frame, roi, color):
    pixels=np.asarray(frame.crop(roi).convert('RGB'),dtype=np.int16)
    red,green,blue=(pixels[:,:,i] for i in range(3))
    if color=='gold':
        mask=(red>190)&(green>110)&(blue<130)&(red>blue+70)
    else:
        mask=(blue>100)&(green>80)&(blue>red+45)
    return float(mask.mean())


def claim_state(s, frame):
    if not page_visible(s,frame):return None
    if matches(s,'claimed',CLAIM_ROI,frame):return 'claimed'
    # NCC alone also matches a greyscale version of a gold/blue button.
    # Independent colour evidence is mandatory for every enabled control.
    if (matches(s,'claim',CLAIM_ROI,frame)
            and color_fraction(frame,(820,574,968,607),'gold')>.30):
        return 'available'
    if (matches(s,'locked',CLAIM_ROI,frame)
            and color_fraction(frame,(820,574,968,607),'gold')<.02):
        return 'locked'
    return None


@lru_cache(maxsize=10)
def day_label(day):
    with Image.open(hs.ASSETS/name('day_'+str(day))) as image:
        return np.asarray(image.convert('L'))>210


def selected_day(frame):
    """Read the independently selected ring and its actual day label."""
    ys,xs=np.mgrid[-36:37,-36:37]
    annulus=(xs*xs+ys*ys>=31.5**2)&(xs*xs+ys*ys<=34**2)
    selected=[]
    for center in CENTERS:
        pixels=np.asarray(frame.crop((center-36,685,center+37,758)).convert('RGB'))
        if float((pixels.max(axis=2)[annulus]>210).mean())>.28:
            selected.append(center)
    if len(selected)!=1:return None
    center=selected[0]
    pixels=np.asarray(frame.crop((center-22,709,center+22,729)).convert('RGB'),dtype=np.int16)
    observed=(pixels.min(axis=2)>210)&(pixels.max(axis=2)-pixels.min(axis=2)<30)
    scores=[]
    for day in range(1,LAST_DAY+1):
        reference=day_label(day)
        denominator=int(observed.sum()+reference.sum())
        score=2*int((observed&reference).sum())/denominator if denominator else 0.
        scores.append((score,day))
    scores.sort(reverse=True)
    return scores[0][1] if scores[0][0]>=.98 and scores[0][0]-scores[1][0]>.01 else None


def _snapshot(s, account, expected=None, timeout=5):
    deadline=time.monotonic()+timeout
    previous=None
    while True:
        try:
            frame=s.content_guard(account,[name('title')],anchor_threshold=.98)
        except hs.SafetyError as exc:
            # A discarded geometry sample never authorizes input. Obtain two
            # new guarded samples after this transient surface repaint.
            if (str(exc)=='game geometry changed during capture; discard stale frame'
                    and time.monotonic()<deadline):
                previous=None
                s.wait_interruptible(.2)
                continue
            raise
        day=selected_day(frame) if page_visible(s,frame) else None
        state=claim_state(s,frame)
        value=(day,state)
        if day is not None and state is not None and (expected is None or day==expected):
            if value==previous:return frame,day,state
            previous=value
        else:
            previous=None
        if time.monotonic()>=deadline:
            evidence=s.save_raw_evidence(frame,account,'anniversary_preview_unknown','pending')
            raise hs.PendingError('周年庆签到的日期或领取状态未稳定确认；'+str(evidence))
        s.wait_interruptible(.2)


def _move_clear(s):
    s.check_stop()
    if isinstance(getattr(s,'hwnd',None),int) and s.hwnd:
        s.ensure_window()
        hs.Win32.move(*s.screen_point(1450,850))


def _step(s, account, day, direction):
    key,roi=('next',NEXT_ROI) if direction==1 else ('previous',PREVIOUS_ROI)
    target=day+direction
    def ready(frame):
        return (page_visible(s,frame) and selected_day(frame)==day
                and color_fraction(frame,roi,'blue')>.07)
    if not s.click_template(account,name(key),roi,20,20,
            'welfare_anniversary_preview_'+str(target),[name('title')],
            threshold=.98,anchor_threshold=.98,frame_predicate=ready):
        raise hs.PendingError('周年庆签到翻页前状态发生变化，未重复切换')
    _move_clear(s)
    return _snapshot(s,account,target)


def _close(s, account):
    if not s.click_template(account,name('close'),CLOSE_ROI,11,11,
            'welfare_anniversary_close',[name('shell')],threshold=.98,
            anchor_threshold=.98,frame_predicate=lambda f:shell_visible(s,f)):
        raise hs.PendingError('限时活动关闭按钮未确认')
    if not s.wait_absent(name('shell'),SHELL_ROI,5,threshold=.98):
        raise hs.PendingError('限时活动关闭结果未确认')


def _open_if_present(s, account):
    frame=s.capture()
    if page_visible(s,frame):return True
    if not shell_visible(s,frame):
        toolbar_roi=(470,69,540,135)
        if not matches(s,'toolbar',toolbar_roi,frame):return False
        if not s.click_template(account,name('toolbar'),toolbar_roi,23,20,
                'welfare_limited_rewards_open',['anchor_top_menu.bmp'],
                threshold=.98,anchor_threshold=.98):
            return False
        if not s.wait_template(name('shell'),SHELL_ROI,6,threshold=.98):
            raise hs.PendingError('限时活动入口已打开，页面尚未确认')
        _move_clear(s)
        s.wait_interruptible(.3)
        frame=s.capture()
    # Two known list pages. Search text, never a row number or a paid panel.
    seen=set()
    for _ in range(3):
        if not shell_visible(s,frame):
            raise hs.PendingError('限时活动目录发生变化，保留页面')
        for key in ('row','row_selected'):
            if matches(s,key,ROW_ROI,frame):
                if not s.click_template(account,name(key),ROW_ROI,25,8,
                        'welfare_anniversary_select',[name('shell')],threshold=.98,
                        anchor_threshold=.98,frame_predicate=lambda f:shell_visible(s,f)):
                    raise hs.PendingError('限时签到入口点击前发生变化')
                if not s.wait_template(name('title'),TITLE_ROI,6,threshold=.98):
                    raise hs.PendingError('签到活动标题未确认')
                _move_clear(s)
                if not page_visible(s,s.capture()):
                    raise hs.PendingError('签到活动期次或关闭按钮未确认')
                return True
        signature=hs.image_hash(frame.crop((500,350,660,737)))
        if signature in seen:break
        seen.add(signature)
        for key,roi in (('list_up',(515,753,565,800)),('list_down',(595,753,645,800))):
            if (matches(s,key,roi,frame) and color_fraction(frame,roi,'gold')>.04):
                if not s.click_template(account,name(key),roi,12,12,
                        'welfare_limited_'+key,[name('shell')],threshold=.98,
                        anchor_threshold=.98,frame_predicate=lambda f:shell_visible(s,f)):
                    raise hs.PendingError('限时活动目录翻页发生变化')
                _move_clear(s)
                s.wait_interruptible(.3)
                frame=s.capture()
                break
        else:break
    _close(s,account)
    return False  # Deliberately no event, warning, error, or completion credit.


def _activity_identity(s, account):
    """Resolve the observed role across portable runs with different UUIDs."""
    from hyol_accounts import resolve_observations
    from hyol_checkpoints import read_checkpoint
    directory=getattr(s,'identity_directory',None) or getattr(s,'shared_state_directory',None)
    if directory is None:raise hs.PendingError('限时签到缺少持久化凭据目录')
    folder=Path(directory)/'activities'/'anniversary'/CAMPAIGN
    if not hasattr(s,'identity_directory'):
        return hs.slug(account.name),folder  # Non-portable legacy adapter.
    context=getattr(s,'run_context',None)
    binding=getattr(context,'binding',None)
    observations={}
    if isinstance(binding,dict):
        centers=binding.get('centers',())
        slot=centers.index(account.tab_x)+1 if account.tab_x in centers else account.index
        owner=getattr(s.state,'aliases',{}).get(account.name,account.name)
        if binding.get('mapping',{}).get(slot)!=owner:
            raise hs.PendingError('限时签到账号与当前标签映射不一致')
        observations=dict(binding.get('observations',{}))
    else:
        slot=account.index
        observations={index:(mask,)*3 for index,mask in
                      getattr(s,'_dynamic_identity_masks',{}).items()}
    if slot not in observations:
        raise hs.PendingError('限时签到缺少本轮连续核实的角色身份，保留原领取凭据')
    path=folder/'identities.json'
    original=read_checkpoint(path)
    refs={key:dict(row) for key,row in original.items()}
    for key,row in refs.items():
        receipt=read_checkpoint(folder/(key+'.json'))
        row['has_unresolved_work']=any(value.get('status')=='pending'
            for value in receipt.get('days',{}).values() if isinstance(value,dict))
    mapping,updated=resolve_observations(observations,refs,
        id_factory=lambda:'reward_'+uuid.uuid4().hex)
    for row in updated.values():row.pop('has_unresolved_work',None)
    if read_checkpoint(path)!=original:
        raise hs.PendingError('限时签到的身份凭据被更新，请先核对原记录')
    hs.atomic_json(path,updated)
    return mapping[slot],folder


def _journal(s, account):
    from hyol_checkpoints import read_checkpoint
    key,folder=_activity_identity(s,account)
    path=folder/(key+'.json')
    journal=read_checkpoint(path)
    if journal and (journal.get('campaign')!=CAMPAIGN or journal.get('identity_key')!=key):
        raise hs.PendingError('限时签到的原账号凭据不一致，保留原文件')
    journal.setdefault('campaign',CAMPAIGN)
    journal.setdefault('identity_key',key)
    journal.setdefault('account',account.name)
    journal['last_observed_account']=account.name
    journal.setdefault('days',{})
    if not isinstance(journal['days'],dict):
        raise hs.PendingError('限时签到原领取凭据格式异常')
    return path,journal


def _persist_result(s, account, result):
    account_record=s.state.accounts[account.name]
    account_record.setdefault('limited_rewards',{})[CAMPAIGN]=result
    s.state.save_account(account.name)
    runner=getattr(s,'navigator',None)
    if runner is not None:
        runner.record('anniversary')['anniversary']=result
        runner.save()


def collect_if_present(s, account):
    """Return None silently when absent; a visible unknown claim stays pending."""
    today=china_day()
    if not '2026-11-05'<=today<='2026-11-25':return None
    if not _open_if_present(s,account):return None
    path,journal=_journal(s,account)
    result=dict(campaign=CAMPAIGN,status='pending',checked_day=today,
                claimed_now=0,already_claimed=[],confirmed_days=[],journal_path=str(path))
    _persist_result(s,account,result)
    frame,day,state=_snapshot(s,account)
    while day>1:
        frame,day,state=_step(s,account,day,-1)
    for expected in range(1,LAST_DAY+1):
        if china_day()!=today:
            raise hs.PendingError('签到检查期间日期已切换，原领取凭据保留到下次核对')
        if day!=expected:raise hs.PendingError('签到实际日期与原预览顺序不一致')
        prior=journal['days'].get(str(day),{})
        if not isinstance(prior,dict):raise hs.PendingError('签到原领取凭据无法核验')
        action=f'anniversary_{CAMPAIGN}_day_{day}_claim'
        pending=(prior.get('status')=='pending' or s.state.action_status(account.name,action)=='pending')
        if state=='locked':
            if pending or any(row.get('status')=='pending' for row in journal['days'].values() if isinstance(row,dict)):
                raise hs.PendingError('限时签到仍有原领取结果未确认，未再次点击')
            if not result['confirmed_days']:
                # Visible but no day is unlocked: no false claim/completion.
                result.update(status='not_ready',next_day=day)
                _persist_result(s,account,result)
                _close(s,account)
                return result
            result['next_day']=day
            break
        if state=='available':
            if pending or prior.get('status') in ('claimed','observed_claimed') or s.state.action_status(account.name,action)=='done':
                raise hs.PendingError('该日签到存在原领取凭据，禁止重新发送')
            evidence=s.save_raw_evidence(frame,account,'anniversary_day_'+str(day)+'_intent','before')
            row=dict(status='pending',day=day,requested_at=hs.now_iso(),requested_day=today,
                     before_evidence=str(evidence),run_id=getattr(getattr(s,'run_context',None),'id',None))
            journal['days'][str(day)]=row
            hs.atomic_json(path,journal)  # Write ahead, including across runs/dates.
            if not s.click_template(account,name('claim'),CLAIM_ROI,75,20,action,
                    [name('title')],irreversible=True,threshold=.98,anchor_threshold=.98,
                    frame_predicate=lambda fresh:page_visible(s,fresh)
                        and selected_day(fresh)==day and claim_state(s,fresh)=='available'):
                if s.state.action_status(account.name,action)=='new':
                    row['status']='not_sent'
                    hs.atomic_json(path,journal)
                raise hs.PendingError('签到领取按钮点击前发生变化，已保留凭据')
            _move_clear(s)
            deadline=time.monotonic()+8
            while True:
                frame,observed_day,state=_snapshot(s,account,day)
                if observed_day==day and state=='claimed':break
                if time.monotonic()>=deadline:
                    raise hs.PendingError('签到领取已发送，但已领取印章尚未确认；禁止重发')
                s.wait_interruptible(.3)
            result['claimed_now']+=1
            pending=True
        evidence=s.save_raw_evidence(frame,account,'anniversary_day_'+str(day)+'_claimed','verified')
        if pending:
            s.commit_action(account,action,'同一期同一日的已领取印章已连续确认')
            journal['days'].setdefault(str(day),{}).update(status='claimed',completion_evidence=str(evidence),
                                             confirmed_at=hs.now_iso())
        else:
            result['already_claimed'].append(day)
            journal['days'].setdefault(str(day),{}).update(status='observed_claimed',
                completion_evidence=str(evidence),confirmed_at=hs.now_iso())
        hs.atomic_json(path,journal)
        result['confirmed_days'].append(day)
        result['completion_evidence']=str(evidence)
        _persist_result(s,account,result)
        if day==LAST_DAY:break
        frame,day,state=_step(s,account,day,1)
    # Completion refers to currently unlocked days; future locked days stay due.
    result.update(status='done',available_claims_verified=True,verified_at=hs.now_iso())
    _persist_result(s,account,result)
    _close(s,account)
    s.event('limited_rewards_verified',account,task='anniversary',campaign=CAMPAIGN,
            claimed_now=result['claimed_now'],confirmed_days=result['confirmed_days'],
            evidence=result['completion_evidence'])
    return result


def run(r):
    result=collect_if_present(r.scheduler,r.account)
    if result is None or result.get('status')=='not_ready':
        r.record('anniversary').update(availability=dict(status='not_open',observed=True),
                                      task_completed=False,last_error='')
        r.save()
    else:
        r.record('anniversary').pop('availability',None)
        r.save()
    return result


def request_satisfied(record):
    if record.get('home_returned') is not True:return False
    if record.get('status')=='skipped':
        return (record.get('task_completed') is not True
                and record.get('availability',{}).get('status')=='not_open'
                and record['availability'].get('observed') is True)
    return (record.get('status')=='done' and record.get('task_completed') is True
            and record.get('anniversary',{}).get('available_claims_verified') is True
            and bool(record['anniversary'].get('completion_evidence'))
            and bool(record['anniversary'].get('confirmed_days')))
