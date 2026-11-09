"""Prefer free SS coupons; use coins when SS is unavailable. Never insure."""
from __future__ import annotations

from datetime import datetime, timezone, timedelta
import hashlib
import json
import math
from pathlib import Path
import re
import time
import uuid

import hyol_scheduler as hs
from hyol_ocr import recognize

TARGETS = ('coupon','coupon','coin')
TASK_LIMIT_SECONDS = 30 * 60
DEADLINE_WARNING_SECONDS = 5 * 60
DELIVERY_RESERVE_SECONDS = 2 * 60
TRAVEL_FIRST_CHECK_SECONDS = 30
TRAVEL_POLL_SECONDS = 20


class EscortNotOpen(hs.PendingError):
    """No escort has started; other available tasks may continue."""


class EscortDeadlineExceeded(hs.PendingError):
    """The local delivery deadline passed; the original result stays pending."""


def deadline_at(active):
    """Keep the acceptance deadline across departure, tab changes and restarts."""
    started = active.get('started_at')
    if type(started) not in (int, float) or not math.isfinite(started) or started < 0:
        raise hs.PendingError('原押镖接取时间缺失或无效，不能重新计时或发送押送操作')
    deadline = active.get('deadline_at', started + TASK_LIMIT_SECONDS)
    if type(deadline) not in (int, float) or not math.isfinite(deadline) or deadline < started:
        raise hs.PendingError('原押镖截止时间无效，保留原任务')
    return min(deadline, started + TASK_LIMIT_SECONDS)


def seconds_remaining(active, now=None):
    now = time.time() if now is None else now
    deadline = deadline_at(active)
    if now < max(active['started_at'], active.get('deadline_checked_at', active['started_at'])) - 2:
        raise hs.PendingError('系统时间回退，不能延长原押镖半小时期限')
    return deadline - now


def poll_seconds(record):
    active = record.get('active')
    if not active:
        return .5
    if not active.get('departure_sent') or active.get('delivery_sent'):
        return .5
    try:
        remaining=seconds_remaining(active)
        return .5 if remaining <= DELIVERY_RESERVE_SECONDS else 1 if remaining <= DEADLINE_WARNING_SECONDS else TRAVEL_POLL_SECONDS
    except hs.PendingError:
        return 1


def priority_key(driver):
    active = driver.rec.get('active')
    if not active:
        return (1, math.inf)
    try:
        return (0, deadline_at(active))
    except hs.PendingError:
        return (0, -math.inf)


def current_identity_frame(scheduler, account, frame, active):
    """Observe at most three redraw frames; every later input still rechecks identity."""
    samples=3 if active and isinstance(getattr(getattr(scheduler,'run_context',None),'binding',None),dict) else 1
    for index in range(samples):
        selected=scheduler._generic_active_tab_x(frame)==account.tab_x
        valid,scores=scheduler.verify_identity(account,frame)
        if not selected or valid or index==samples-1:
            return frame,selected,valid,scores
        scheduler.event('escort_identity_resampling',account,sample=index+1,
                        content_authorized=False,detail='原押送切图或交付后身份重绘，先观察新帧，尚未授权输入')
        scheduler.wait_interruptible(.35)
        frame=scheduler.capture()


def legs_verified(record):
    legs=record.get('legs',())
    return (isinstance(legs,list) and len(legs)==3
            and all(isinstance(leg,dict) and leg_kind_verified(leg,TARGETS[index])
                    and leg.get('status')=='done' and leg.get('completion_observed') is True
                    and bool(leg.get('completion_evidence')) and bool(leg.get('before_evidence'))
                    and leg.get('remaining')==2-index for index,leg in enumerate(legs)))


def leg_kind_verified(leg, requested):
    if leg.get('kind')==requested:
        return True
    return (requested=='coupon' and leg.get('kind')=='coin' and leg.get('requested_kind')=='coupon'
            and (leg.get('fallback_reason')=='ss_free_quota_exhausted' and leg.get('free_ss_before')==0
                 or leg.get('fallback_reason')=='ss_free_refresh_exhausted'
                    and type(leg.get('free_ss_before')) is int and leg['free_ss_before']>0
                    and leg.get('free_refresh_before')==0 and leg.get('coupon_offers_before')==0
                    and leg.get('rows_complete_before') is True))


def priority_leg_verified(leg):
    # Historical 'ss_offer_absent' can coexist with unused SS/free refreshes.
    # Preserve that receipt, but never accept it as coupon-priority success.
    return leg_kind_verified(leg,'coupon') and leg.get('fallback_reason')!='ss_offer_absent'


def verified(record):
    if record.get('completion_policy')=='coupon_priority_remaining':
        return priority_remaining_verified(record)
    return (record.get('status')=='done' and record.get('home_returned') is True
            and record.get('task_completed') is True and not record.get('active')
            and legs_verified(record)
            and record.get('coupon_completed')==sum(leg['kind']=='coupon' for leg in record['legs'])
            and record.get('coin_completed')==sum(leg['kind']=='coin' for leg in record['legs']))


def priority_remaining_verified(record):
    start=record.get('policy_leg_start')
    baseline=record.get('policy_baseline_remaining')
    legs=record.get('legs',[])
    samples=record.get('remaining_zero_samples',[])
    return (record.get('status')=='done' and record.get('task_completed') is True
            and record.get('home_returned') is True and not record.get('active')
            and not record.get('refresh_pending') and record.get('remaining_after')==0
            and type(start) is int and 0<=start<=len(legs)
            and type(baseline) is int and 0<=baseline<=3 and len(legs)-start==baseline
            and isinstance(samples,list) and len(samples)==2 and all(samples)
            and (not record.get('daily_counter_required') or record.get('daily_remaining_after')==0
                 and len(record.get('daily_remaining_zero_samples',()))==3
                 and all(record['daily_remaining_zero_samples']))
            and all(priority_leg_verified(leg) and leg.get('status')=='done'
                    and leg.get('completion_observed') is True and leg.get('completion_evidence')
                    and leg.get('before_evidence') and leg.get('before_remaining')==baseline-index
                    and leg.get('remaining')==baseline-index-1
                    for index,leg in enumerate(legs[start:])))


def remaining_legs_verified(record, count):
    """Only newly executed legs count; an earlier consumed leg stays unverified."""
    legs=record.get('legs',[])
    evidence=record.get('baseline_evidence')
    return (type(count) is int and count in (1,2)
            and record.get('scope')=='remaining_only'
            and record.get('prior_settled_confirmed') is True
            and record.get('baseline_remaining')==count
            and isinstance(evidence,list) and len(evidence)==2 and all(evidence)
            and isinstance(legs,list) and len(legs)==count
            and all(isinstance(leg,dict) and leg_kind_verified(leg,'coupon')
                    and leg.get('status')=='done' and leg.get('completion_observed') is True
                    and bool(leg.get('completion_evidence')) and bool(leg.get('before_evidence'))
                    and leg.get('before_remaining')==count-index
                    and leg.get('remaining')==count-index-1 for index,leg in enumerate(legs)))


def remaining_verified(record, count):
    return (type(count) is int and count in (1,2)
            and record.get('status')=='done' and record.get('home_returned') is True
            and record.get('task_completed') is True and not record.get('active')
            and record.get('prior_consumed_unverified')==3-count
            and record.get('remaining_after')==0 and record.get('completed')==count
            and remaining_legs_verified(record,count)
            and record.get('coupon_completed')==sum(leg['kind']=='coupon' for leg in record['legs'])
            and record.get('coin_completed')==sum(leg['kind']=='coin' for leg in record['legs']))


def pixel_proof(frame, box):
    return dict(box=box, sha256=hashlib.sha256(frame.crop(box).convert('RGB').tobytes()).hexdigest())


def dispatch_text_proof(frame, box):
    """Keep opaque white/yellow glyphs; the translucent panel background moves."""
    import numpy as np
    rgb=np.asarray(frame.crop(box).convert('RGB'),dtype=np.int16)
    masks=[(rgb.min(axis=2)>=170)&(rgb.max(axis=2)-rgb.min(axis=2)<=35)]
    masks.extend((rgb[:,:,0]>=floor)&(rgb[:,:,1]>=floor-60)&(rgb[:,:,2]<120)
                 for floor in (180,210))
    return dict(kind='dispatch_text_mask',box=box,
                sha256=hashlib.sha256(b''.join(mask.tobytes() for mask in masks)).hexdigest())


def fresh_proof(frame, proof, scheduler=None):
    if proof and proof.get('kind')=='dispatch_text_mask':
        return dispatch_text_proof(frame,tuple(proof['box']))['sha256']==proof['sha256']
    if proof and proof.get('kind')=='dispatch_currency_template':
        if scheduler is None:return False
        box=tuple(proof['box'])
        found,_,x,y=scheduler.templates.match(frame,hs.ASSETS/proof['asset'],box,threshold=.97)
        return found and (x,y)==box[:2]
    if proof and proof.get('kind')=='delivery_amount_mask':
        return delivery_amount_proof(frame,tuple(proof['box']))['sha256']==proof['sha256']
    return bool(proof) and pixel_proof(frame,proof['box'])['sha256']==proof['sha256']


def offer_reward_amount(s, frame, box, ocr_base=None):
    """Read the complete base digits and any green multiplier twice."""
    import numpy as np
    from PIL import Image, ImageOps
    rgb=np.asarray(frame.crop(box).convert('RGB'),dtype=np.int16)
    green=((rgb[:,:,1]>=170)&(rgb[:,:,1]-rgb[:,:,0]>=70)&(rgb[:,:,1]-rgb[:,:,2]>=70))
    readings=[]
    for floor in (170,180):
        white=(rgb.min(axis=2)>=floor)&(rgb.max(axis=2)-rgb.min(axis=2)<=35)
        if int(white.sum())<10:return None
        ys,xs=np.where(white)
        pattern=Image.fromarray(np.where(white,0,255).astype('uint8')).crop(
            (int(xs.min()),int(ys.min()),int(xs.max())+1,int(ys.max())+1)).convert('RGB')
        matches=set()
        for path in hs.ASSETS.glob('escort_offer_amount_*_white_*.bmp'):
            match=re.fullmatch(r'escort_offer_amount_(\d{1,8})_white_(170|180)\.bmp',path.name)
            if not match:continue
            with Image.open(path) as reference:
                if reference.size!=pattern.size:continue
            if s.templates.match(pattern,path,(0,0,*pattern.size),threshold=.995)[0]:
                matches.add(int(match[1]))
        if len(matches)>1:return None
        if matches:
            base=matches.pop()
        else:
            enlarged=pattern.resize((pattern.width*4,pattern.height*4),Image.Resampling.NEAREST)
            value=compact(recognize(ImageOps.expand(enlarged,border=12,fill='white')).get('text',''))
            if not re.fullmatch(r'\d{1,8}',value):return None
            base=int(value)
        green=((rgb[:,:,1]>=floor)&(rgb[:,:,1]-rgb[:,:,0]>=70)&(rgb[:,:,1]-rgb[:,:,2]>=70))
        multiplier=1
        if green.any():
            ys,xs=np.where(green)
            marker=Image.fromarray(np.where(green,0,255).astype('uint8')).crop(
                (int(xs.min()),int(ys.min()),int(xs.max())+1,int(ys.max())+1)).convert('RGB')
            path=hs.ASSETS/'escort_offer_multiplier_2_green.bmp'
            with Image.open(path) as reference:
                if reference.size!=marker.size:return None
            if not s.templates.match(marker,path,(0,0,*marker.size),threshold=.995)[0]:return None
            multiplier=2
        readings.append((base,multiplier))
    if readings[0]!=readings[1] or readings[0][0]<=0:return None
    base,multiplier=readings[0]
    return dict(amount=base*multiplier,base_amount=base,reward_multiplier=multiplier,
                amount_readings=[list(value) for value in readings],proof=pixel_proof(frame,box))


def delivery_amount_proof(frame, box):
    import numpy as np
    rgb=np.asarray(frame.crop(box).convert('RGB'),dtype=np.int16)
    masks=[((rgb.min(axis=2)>=floor)&(rgb.max(axis=2)-rgb.min(axis=2)<=35)).tobytes()
           for floor in (170,180)]
    return dict(kind='delivery_amount_mask',box=box,
                sha256=hashlib.sha256(b''.join(masks)).hexdigest())


def distinguish_observed_owners(observations,refs):
    """A uniquely observed original owner cannot also be a new sibling role."""
    from hyol_checkpoints import identity_mask_scores
    scores={slot:[identity_mask_scores(mask,refs) for mask in masks]
            for slot,masks in observations.items()}
    for key,row in refs.items():
        if not row.get('has_unresolved_work'):continue
        owners=[slot for slot,reads in scores.items() if all(
            read.get(key,0)>=.92 and read.get(key,0)-max(
                (v for other,v in read.items() if other!=key),default=0)>=.03 for read in reads)]
        if len(owners)==1:row['has_unresolved_work']=False


def activity_identity(s, account):
    """Escort receipts survive portable run IDs without importing role libraries."""
    from hyol_accounts import resolve_observations
    from hyol_checkpoints import read_checkpoint
    directory = getattr(s,'identity_directory',None)
    day = getattr(getattr(s,'state',None),'day',None)
    if not isinstance(directory,Path) or not isinstance(day,str):
        return None,None
    folder = directory/'escort'/day
    path = folder/'identities.json'
    refs = read_checkpoint(path)
    context = getattr(s,'run_context',None)
    binding = getattr(context,'binding',None)
    masks=None
    if isinstance(binding,dict):
        slot=account.index
        if binding.get('centers'):
            try:slot=binding['centers'].index(account.tab_x)+1
            except ValueError:raise hs.PendingError('押镖账号不在本次已核实标签布局中')
            owner=getattr(s.state,'aliases',{}).get(account.name,account.name)
            if binding.get('mapping',{}).get(slot)!=owner:
                raise hs.PendingError('押镖标签与账号实名映射不一致，未关联次数凭据')
        masks=binding.get('observations',{}).get(slot)
    if not masks:
        mask = getattr(s,'_dynamic_identity_masks',{}).get(account.index)
        if mask is not None:masks=(mask,)*3
    if not masks:
        raise hs.PendingError('押镖缺少本轮已核实身份，不能关联当日押送凭据')
    for key,row in refs.items():
        saved=read_checkpoint(folder/(key+'.json'))
        sample=read_checkpoint(folder/(key+'_remaining_once.json'))
        remaining=read_checkpoint(folder/(key+'_remaining.json'))
        row['has_unresolved_work']=bool(saved.get('active') or sample.get('active') or remaining.get('active'))
    observations={slot:(mask,)*3 for slot,mask in getattr(s,'_dynamic_identity_masks',{}).items()}
    observations[account.index]=masks
    distinguish_observed_owners(observations,refs)
    mapping,updated=resolve_observations(observations,refs,
                        id_factory=lambda:'escort_'+uuid.uuid4().hex)
    key=mapping[account.index]
    for row in updated.values():row.pop('has_unresolved_work',None)
    # A different foreground worker cannot race this write under the lease.
    if {k:{a:b for a,b in v.items() if a!='has_unresolved_work'} for k,v in refs.items()} != read_checkpoint(path):
        raise hs.PendingError('押镖身份凭据被更新，保留原押送')
    hs.atomic_json(path,updated)
    return key,folder/(key+'.json')


def pending_activity(s, account):
    """Read only: navigation must not abandon an earlier portable-run escort."""
    return bool(pending_activity_records(s,account))


def pending_activity_records(s,account):
    """Return only the observed role's original active receipts, without writing."""
    from hyol_checkpoints import read_checkpoint
    from hyol_accounts import resolve_observations
    directory=getattr(s,'identity_directory',None)
    day=getattr(getattr(s,'state',None),'day',None)
    masks=getattr(s,'_dynamic_identity_masks',None)
    if not isinstance(directory,Path) or not isinstance(day,str) or not isinstance(masks,dict):
        return []
    folder=directory/'escort'/day
    refs=read_checkpoint(folder/'identities.json')
    if not refs:return []
    mask=masks.get(account.index)
    if mask is None:
        raise hs.PendingError('检查缺少押镖当日身份核对资料，保留原路线')
    if int(mask.sum())<50:
        raise hs.PendingError('押镖当前身份资料不足，保留原路线')
    active=set()
    for key in refs:
        row=read_checkpoint(folder/(key+'.json'))
        sample=read_checkpoint(folder/(key+'_remaining_once.json'))
        remaining=read_checkpoint(folder/(key+'_remaining.json'))
        if row.get('active') or sample.get('active') or remaining.get('active'):active.add(key)
    if not active:return []
    for key,row in refs.items():row['has_unresolved_work']=key in active
    observations={slot:(value,)*3 for slot,value in masks.items()}
    distinguish_observed_owners(observations,refs)
    try:
        # Simultaneous observations assign an old cart to its actual owner.
        # A newly observed same-name role must not inherit that cart merely
        # because only the other server's role was previously in this library.
        mapping,_updated=resolve_observations(observations,refs,
            id_factory=lambda:'readonly_probe_'+uuid.uuid4().hex)
    except hs.PendingError as exc:
        raise hs.PendingError('当前身份与原押镖存在歧义，保留原路线') from exc
    owner=mapping.get(account.index)
    if owner not in active:return []
    receipts=[]
    for suffix in ('','_remaining_once','_remaining'):
        path=folder/(owner+suffix+'.json');record=read_checkpoint(path)
        if record.get('active'):receipts.append(dict(path=path,record=record))
    return receipts


def opened(now=None):
    now = now or datetime.now(timezone(timedelta(hours=8)))
    minutes = now.hour*60+now.minute
    return 11*60 <= minutes < 14*60 or 18*60 <= minutes < 21*60


def compact(text):
    return re.sub(r'\s+','',text or '')


def remaining_count(text):
    text = compact(text)
    match = re.search(r'([0-3])[/|I1]3',text)
    return int(match[1]) if match else None


def free_refresh_count(text):
    match = re.search(r'刷新[（(](\d{1,2})',compact(text))
    return int(match[1]) if match and 0 <= int(match[1]) <= 12 else None


def free_ss_count(text):
    match=re.search(r'([0-3])[/|I1]([23])',compact(text))
    return int(match[1]) if match and int(match[1])<=int(match[2]) else None


def ss_fraction_mask(image):
    """Normalize the small gold quota glyphs, including anchored dimmed pages."""
    import numpy as np
    from PIL import Image
    rgb=np.asarray(image.convert('RGB'),dtype=np.int16)
    mask=(rgb[:,:,0]>=8)&(rgb[:,:,0]*100>rgb[:,:,1]*108)&(rgb[:,:,1]*100>rgb[:,:,2]*125)
    ys,xs=np.nonzero(mask)
    if len(xs)<10:return None
    box=(int(xs.min()),int(ys.min()),int(xs.max())+1,int(ys.max())+1)
    if not (10<=box[2]-box[0]<=30 and 6<=box[3]-box[1]<=18):return None
    patch=(255-mask[box[1]:box[3],box[0]:box[2]].astype(np.uint8)*255)
    return Image.fromarray(patch).resize((36,24),Image.Resampling.NEAREST).convert('RGB')


def read_ss_fraction(s, image):
    pattern=ss_fraction_mask(image)
    if pattern is None:return None
    scores=[]
    for count in range(3):
        path=hs.ASSETS/f'escort_free_ss_{count}of2.bmp'
        if not path.is_file():return None
        variants=[path]
        current=hs.ASSETS/f'escort_free_ss_{count}of2_current.bmp'
        if current.is_file():variants.append(current)
        score=max(s.templates.match(pattern,p,(0,0,36,24),threshold=.98)[1] for p in variants)
        scores.append((score,count))
    scores.sort(reverse=True)
    return scores[0][1] if scores[0][0]>=.98 and scores[0][0]-scores[1][0]>=.03 else None


def reward_kind(snapshot, requested):
    if requested!='coupon':return requested,''
    quota=snapshot.get('free_ss')
    if type(quota) is not int or not 0<=quota<=2:
        raise hs.PendingError('SS免费接取次数未核实，未接取或降级任务')
    if quota==0:return 'coin','ss_free_quota_exhausted'
    if any(offer['kind']=='coupon' for offer in snapshot['offers']):
        return 'coupon',''
    if snapshot.get('rows_complete') is True:
        free=snapshot.get('free_refresh')
        if type(free) is not int or free<0:
            raise hs.PendingError('当前没有可接SS，但免费刷新次数未知，禁止降级铜币')
        if free>0:return 'coupon',''
        return 'coin','ss_free_refresh_exhausted'
    raise hs.PendingError('押镖任务行未全部读清，不能判定没有SS任务')


def choose_offer(offers, kind, free_refresh):
    candidates = [o for o in offers if o['kind']==kind and o['amount']>0]
    best = max(candidates,key=lambda o:o['amount']) if candidates else None
    if kind == 'coupon':
        return ('dispatch',best) if best else ('refresh',None) if free_refresh else ('pending',None)
    # 19w is preferred; 17w is acceptable. Exhaust only verified free refreshes
    # before accepting a lower current maximum. Coupon legs always come first.
    if best and (best['amount']>=170000 or not free_refresh):
        return 'dispatch',best
    return ('refresh',None) if free_refresh else ('pending',None)


def completion_text(data):
    return any(re.fullmatch(r'(?:本次)?(?:押镖|押送|护送)(?:任务)?(?:已)?(?:成功|完成)[!！。]?',compact(line['text']))
               for line in data.get('lines',()))


def delivery_layout(s, frame):
    """The user's terminal escort dialog requires three independent labels."""
    ok,_,tx,ty=s.template('escort_delivery_title.bmp',hs.VIEW,frame,threshold=.97)
    if not ok:return None
    body=(max(1,tx-155),max(69,ty+42),min(1919,tx+220),min(1031,ty+270))
    if not (s.template('escort_delivery_task.bmp',body,frame,threshold=.98)[0]
            and s.template('escort_delivery_reward.bmp',body,frame,threshold=.98)[0]):
        return None
    ok,_,x,y=s.template('escort_complete_task.bmp',body,frame,threshold=.97)
    if not ok:return None
    proof=pixel_proof(frame,(body[0],body[1],min(1919,tx+170),max(body[1]+1,y-8)))
    return dict(x=x,y=y,body=body,proof=proof)


def delivery_reward_amount(s, frame, delivery, currency=None):
    """Recover small delivery digits only when two thresholded reads agree."""
    import numpy as np
    from PIL import Image, ImageOps
    ok,_,x,y=s.template('escort_delivery_reward.bmp',delivery['body'],frame,threshold=.98)
    if not ok:
        return None
    box=(max(delivery['body'][0],x-5),max(delivery['body'][1],y+20),
         min(delivery['body'][2],x+100),min(delivery['body'][3],y+60))
    currency=currency or delivery_reward_currency(s,frame,delivery)
    if currency:
        # The gold coin highlight can be OCR'd as a leading zero. Read only
        # the digits to the right of the positively identified money icon.
        box=(max(box[0],currency['proof']['box'][2]+1),box[1],box[2],box[3])
    if box[0]>=box[2] or box[1]>=box[3]:
        return None
    rgb=np.asarray(frame.crop(box).convert('RGB'),dtype=np.int16)
    readings=[]
    for floor in (170,180):
        ink=(rgb.min(axis=2)>=floor)&(rgb.max(axis=2)-rgb.min(axis=2)<=35)
        if int(ink.sum())<10:
            return None
        ys,xs=np.where(ink)
        pattern=Image.fromarray(np.where(ink,0,255).astype('uint8')).crop(
            (int(xs.min()),int(ys.min()),int(xs.max())+1,int(ys.max())+1)).convert('RGB')
        enrolled=set()
        amount_assets=(*hs.ASSETS.glob('escort_delivery_amount_*_white*.bmp'),
                      *hs.ASSETS.glob('escort_offer_amount_*_white_*.bmp'))
        for path in amount_assets:
            match=re.fullmatch(r'escort_(?:delivery|offer)_amount_(\d{1,8})_white(?:_(?:170|180))?\.bmp',path.name)
            if not match:continue
            with Image.open(path) as reference:
                if reference.size!=pattern.size:continue
            if s.templates.match(pattern,path,(0,0,pattern.width,pattern.height),threshold=.995)[0]:
                enrolled.add(int(match[1]))
        if len(enrolled)==1:
            readings.append(next(iter(enrolled)));continue
        if len(enrolled)>1:return None
        mask=Image.fromarray(np.where(ink,0,255).astype('uint8')).convert('RGB')
        values=[]
        for scale in (3,4,5):
            enlarged=mask.resize((mask.width*scale,mask.height*scale),Image.Resampling.NEAREST)
            value=compact(recognize(ImageOps.expand(enlarged,border=12,fill='white')).get('text',''))
            if not re.fullmatch(r'\d{1,8}',value):
                if values:return None
                continue
            if values and int(value)!=values[0]:return None
            values.append(int(value))
            if len(values)==2:break
        if len(values)<2 or len(set(values))!=1:
            return None
        readings.append(values[0])
    if readings[0]!=readings[1]:
        return None
    number_box=box if currency else (max(delivery['body'][0],x+16),box[1],box[2],box[3])
    return dict(amount=readings[0],readings=readings,
                proof=delivery_amount_proof(frame,number_box),box=box)


def delivery_reward_currency(s,frame,delivery):
    found,_,x,y=s.template('escort_delivery_reward.bmp',delivery['body'],frame,threshold=.98)
    if not found:return None
    roi=(max(delivery['body'][0],x-25),y+15,min(delivery['body'][2],x+45),y+65)
    matches=[]
    for kind in ('coupon','coin'):
        name='escort_delivery_'+kind+'_currency_20261028.bmp'
        found,score,ix,iy=s.template(name,roi,frame,threshold=.98)
        if found:
            with hs.Image.open(hs.ASSETS/name) as image:width,height=image.size
            matches.append(dict(kind=kind,score=score,
                proof=pixel_proof(frame,(ix,iy,ix+width,iy+height))))
    return matches[0] if len(matches)==1 else None


def delivery_button_roi(delivery):
    with hs.Image.open(hs.ASSETS/'escort_complete_task.bmp') as image:
        width,height=image.size
    x,y=delivery['x'],delivery['y']
    return (max(1,x-2),max(69,y-2),min(1919,x+width+2),min(1031,y+height+2))


def unsent_delivery(s, account, active, label):
    return (active.get('delivery_action')==label
            and active.get('phase')=='delivery_pending'
            and active.get('delivery_account')==account.name
            and active.get('delivery_state_directory')==str(s.state.directory)
            and Path(active.get('delivery_before_evidence','')).is_file()
            and s.state.action_status(account.name,label)=='new')


def unsent_departure(s, account, active, label):
    """An existing original journal must prove that mouse-down was never sent."""
    from hyol_checkpoints import read_checkpoint
    directory=getattr(s.state,'directory',None)
    root=getattr(s,'identity_directory',None)
    day=getattr(s.state,'day',None)
    if (not isinstance(directory,Path) or not isinstance(root,Path) or not isinstance(day,str)
            or active.get('departure_action')!=label or active.get('phase')!='depart_pending'
            or active.get('departure_sent') or active.get('departure_account')!=account.name
            or active.get('departure_state_directory')!=str(directory)
            or active.get('source_account')!=account.name
            or active.get('state_directory')!=str(directory)
            or not directory.resolve().is_relative_to(root.resolve())
            or not Path(active.get('before_evidence','')).is_file()):return False
    path=directory/day/(hs.slug(account.name)+'.json')
    if not path.is_file():return False
    original=read_checkpoint(path)
    return (original.get('account')==account.name and original.get('date')==day
            and label not in original.get('actions',{})
            and original.get('actions',{}).get(active.get('action'),{}).get('status')=='pending'
            and s.state.action_status(account.name,label)=='new')


def layout(s,frame):
    ok,score,x,y = s.template('escort_page_title.bmp',hs.VIEW,frame,threshold=.97)
    if not ok:
        return None
    ox,oy = x-423,y-29
    close_roi=(max(1,ox+827),max(69,oy+52),min(1919,ox+884),min(1031,oy+108))
    if not s.template('escort_close.bmp',close_roi,frame,threshold=.97)[0]:
        return None
    return ox,oy


def panel(s,frame, *, counters_only=False):
    origin=layout(s,frame)
    if origin is None:
        raise hs.PendingError('押镖标题和关闭按钮未同时确认')
    ox,oy=origin
    box=(max(1,ox+60),max(69,oy+75),min(1919,ox+831),min(1031,oy+548))
    data=recognize(frame.crop(box))
    words=[{**w,'x':w['x']+box[0],'y':w['y']+box[1]} for l in data.get('lines',()) for w in l.get('words',())]
    count_words=[w['text'] for w in words if ox+765<=w['x']<=ox+820 and oy+77<=w['y']<=oy+108]
    remaining=remaining_count(''.join(count_words))
    if remaining is None:
        remaining=remaining_count(recognize(frame.crop((ox+770,oy+79,ox+813,oy+105)).resize((129,78))).get('text',''))
    refresh_ok=s.template('escort_free_refresh.bmp',(ox+650,oy+488,ox+800,oy+545),frame,threshold=.97)[0]
    free=free_refresh_count(data.get('text','')) if refresh_ok else None
    if not refresh_ok or free is None:
        refresh_box=(ox+670,oy+490,ox+790,oy+531)
        label=compact(recognize(frame.crop(refresh_box).resize((360,123))).get('text',''))
        if '免费刷新' in label:
            bounded=compact(''.join(w['text'] for w in words
                if ox+650<=w['x']<=ox+850 and oy+480<=w['y']<=oy+548))
            count=re.search(r'[（(](\d{1,2})[)）]',bounded)
            if count and 0<=int(count[1])<=12:free=int(count[1])
    ss_roi=(ox+803,oy+107,ox+837,oy+134)
    ss_label=s.template('escort_free_ss_label.bmp',(ox+650,oy+100,ox+837,oy+137),frame,threshold=.97)[0]
    free_ss=read_ss_fraction(s,frame.crop((ox+810,oy+113,ox+833,oy+129))) if ss_label else None
    if counters_only or remaining==0:
        return dict(offers=[],remaining=remaining,free_refresh=free,free_ss=free_ss,origin=origin,
                    rows_complete=remaining==0,text=data.get('text',''))
    roi=(ox+125,oy+135,ox+810,oy+480)
    buttons=s.templates.find_all(frame,hs.ASSETS/'escort_dispatch.bmp',roi,.97)
    insured=s.templates.find_all(frame,hs.ASSETS/'escort_insured_dispatch.bmp',roi,.97)
    icons={kind:s.templates.find_all(frame,hs.ASSETS/asset,roi,.97) for kind,asset in
           (('coupon','escort_coupon_currency.bmp'),('coin','escort_coin_currency.bmp'))}
    offers=[]
    for score,x,y in buttons:
        if any(ix-2<=x<=ix+88 and iy-2<=y<=iy+30 for _,ix,iy in insured):
            continue
        for kind,items in icons.items():
            for _,ix,iy in items:
                if not 60<=x-ix<=180 or abs(iy-y)>28:
                    continue
                amounts=[w for w in words if ix+20<=w['x']<=x-15 and abs(w['y']-iy)<25
                         and re.fullmatch(r'\d{1,8}',compact(w['text']))]
                base=int(compact(min(amounts,key=lambda w:abs(w['y']-iy))['text'])) if amounts else None
                number=offer_reward_amount(s,frame,(ix+27,iy-5,x-8,iy+22),ocr_base=base)
                if number:
                    with hs.Image.open(hs.ASSETS/('escort_'+kind+'_currency.bmp')) as icon:
                        width,height=icon.size
                    offer=dict(kind=kind,**number,x=x,y=y,
                               currency_proof=dict(**pixel_proof(frame,(ix,iy,ix+width,iy+height)),
                                   kind='dispatch_currency_template',asset='escort_'+kind+'_currency.bmp'))
                    offer['proof']=delivery_amount_proof(frame,tuple(offer['proof']['box']))
                    if offer not in offers:
                        offers.append(offer)
    signature=hashlib.sha256(repr([(o['kind'],o['amount'],o['y']) for o in offers]).encode()).hexdigest()
    normal_buttons={(x,y) for _,x,y in buttons if not any(ix-2<=x<=ix+88 and iy-2<=y<=iy+30 for _,ix,iy in insured)}
    return dict(offers=offers,remaining=remaining,free_refresh=free,free_ss=free_ss,origin=origin,signature=signature,
                rows_complete=len({(o['x'],o['y']) for o in offers})==len(normal_buttons),
                counter_proof=dispatch_text_proof(frame,(ox+770,oy+79,ox+813,oy+105)),
                ss_proof=dispatch_text_proof(frame,ss_roi),
                refresh_proof=dispatch_text_proof(frame,(ox+670,oy+497,ox+790,oy+529)),
                text=data.get('text',''))


def daily_visible(s,frame):
    return (s.template('daily_practice_anchor.bmp',(951,119,1501,369),frame,threshold=.98)[0]
        or s.template('daily_practice_title_core_20261029.bmp',(1100,150,1260,260),frame,threshold=.98)[0]
        and (s.template('daily_practice_close.bmp',(1370,200,1460,280),frame,threshold=.98)[0]
             or s.template('login_update_notice_close_core_current.bmp',(1370,200,1460,280),frame,threshold=.98)[0]))


def daily_row_snapshot(s,frame):
    if not daily_visible(s,frame):return None
    matches=[]
    for name in ('escort_daily_label.bmp','escort_activity_label.bmp'):
        ok,_,x,y=s.template(name,(480,280,1080,700),frame,threshold=.95)
        if ok:matches.append((x,y))
    if len(set(matches))!=1:return None
    x,y=matches[0];roi=(x+300,y+8,min(1080,x+355),min(710,y+40))
    readings=[]
    for count in range(4):
        for stamp in ('20261029','20261109'):
            name=f'escort_daily_remaining_{count}of3_{stamp}.bmp'
            if not (hs.ASSETS/name).is_file():continue
            ok,score,nx,ny=s.template(name,roi,frame,threshold=.995)
            if ok:
                with hs.Image.open(hs.ASSETS/name) as image:width,height=image.size
                readings.append(dict(remaining=count,score=score,
                    proof=pixel_proof(frame,(nx,ny,nx+width,ny+height))))
                break
    if len(readings)>1:raise hs.PendingError('押镖日常剩余次数模板冲突，未按零放行')
    if not readings:
        data=recognize(frame.crop(roi))
        value=compact(data.get('text') or ''.join(line.get('text','') for line in data.get('lines',[])))
        match=re.fullmatch(r'([0-3])/3',value)
        if match:
            readings.append(dict(remaining=int(match[1]),proof=pixel_proof(frame,roi)))
    return dict(origin=(x,y),remaining=readings[0]['remaining'] if readings else None,
                counter_proof=readings[0]['proof'] if readings else None)


def inspect_daily_remaining(r, *, leave_open=False):
    """Read this role's escort row three times; no Go or acceptance input."""
    s=r.scheduler;frame=s.capture()
    if not daily_visible(s,frame):
        if not s.main_ready(frame):raise hs.PendingError('日常次数读取前主界面未确认')
        r.click('daily_practice_toolbar.bmp',(651,69,851,169),20,20,'escort_count_open_daily',['anchor_top_menu.bmp'])
        r.wait('daily_practice_anchor.bmp',(951,119,1501,369))
    if s.template('daily_limited_tab_current.bmp',(510,250,800,310))[0]:
        r.click('daily_limited_tab_current.bmp',(510,250,800,310),40,15,'escort_count_limited',['daily_practice_anchor.bmp'])
    for _ in range(15):
        frame=s.capture();row=None
        for name in ('escort_daily_label.bmp','escort_activity_label.bmp'):
            found,_,x,y=s.template(name,(480,280,1080,700),frame,threshold=.95)
            if found:row=(name,x,y);break
        if row:break
        from hyol_daily_observation import scroll_once
        if not scroll_once(r,'down',frame,'escort_count_scroll'):
            raise hs.PendingError('日常活动押镖行未确认，未读取其他活动次数')
    else:raise hs.PendingError('日常活动押镖行未确认')
    name,x,y=row
    r.click(name,(x-2,y-2,x+100,y+35),20,10,'escort_count_select_row',['daily_practice_anchor.bmp'],threshold=.95)
    samples=[];readings=[];deadline=time.monotonic()+12
    while len(readings)<3:
        index=len(readings)
        frame=s.capture()
        if not s.verify_identity(r.account,frame)[0] or s._generic_active_tab_x(frame)!=r.account.tab_x:
            raise hs.PendingError('日常押镖次数复核时账号或标签变化')
        snapshot=daily_row_snapshot(s,frame)
        if snapshot is None:
            samples.clear();readings.clear()
            if time.monotonic()>=deadline:
                evidence=s.save_raw_evidence(frame,r.account,'escort_daily_row_pending','pending')
                raise hs.PendingError('日常押镖行或独立次数未确认；证据='+str(evidence))
            s.wait_interruptible(.3);continue
        if readings and snapshot['remaining']!=readings[0]:samples.clear();readings.clear();index=0
        readings.append(snapshot['remaining'])
        samples.append(str(s.save_raw_evidence(frame,r.account,'escort_daily_remaining_'+str(index+1),'observed')))
        if index<2:s.wait_interruptible(.3)
        if len(readings)<3 and time.monotonic()>=deadline:
            raise hs.PendingError('日常押镖次数在12秒内未稳定三帧')
    if readings[0]!=readings[1] or readings[1]!=readings[2]:
        raise hs.PendingError('日常押镖剩余次数三帧变化，未按零处理')
    if not leave_open:
        r.click('daily_practice_close.bmp',(1370,200,1460,280),17,17,'escort_count_close_daily',['daily_practice_anchor.bmp'])
    result=dict(remaining=readings[0],samples=samples,source='daily_escort_row',observed_at=hs.now_iso(),
                selected_row=list(snapshot['origin']),counter_proof=snapshot.get('counter_proof'))
    s.event('escort_daily_remaining_observed',r.account,**result)
    return result


def open_page(r, *, selected_daily=None):
    s=r.scheduler;frame=s.capture()
    if s.template('escort_bonus_title_20261028.bmp',(800,460,1130,540),frame,threshold=.98)[0]:
        raise hs.PendingError('原押镖额外奖励弹窗尚未处理，未打开其他入口')
    if layout(s,frame):return frame
    from hyol_navigation import organization_world_visible
    if organization_world_visible(s,frame) and not s.template('daily_practice_toolbar.bmp',(651,69,851,169),frame)[0]:
        name='escort_dispatcher_label_20261029.bmp'
        found,_,x,y=s.template(name,(400,220,1500,700),frame,threshold=.98)
        if not found:raise hs.PendingError('组织地图押镖委托人尚未核实，未点击其他NPC')
        r.click(name,(x-2,y-2,x+76,y+23),34,60,'escort_open_current_dispatcher',
            [name],threshold=.98,anchor_threshold=.98,
            frame_predicate=lambda fresh:organization_world_visible(s,fresh))
        for _ in range(30):
            current=s.capture()
            if layout(s,current):return current
            s.wait_interruptible(.3)
        raise hs.PendingError('原组织押镖NPC已点击，任务页尚未确认，未重复点击或接取')
    if not (s.main_ready(frame) or organization_world_visible(s,frame)
            or s.template('daily_practice_anchor.bmp',(951,119,1501,369),frame)[0]):
        raise hs.PendingError('押镖入口前页面未确认；原战斗和结算保留')
    # The daily limited-time list avoids changing organization membership.
    if not s.template('daily_practice_anchor.bmp',(951,119,1501,369),frame)[0]:
        if not s.template('daily_practice_toolbar.bmp',(651,69,851,169),frame)[0]:
            return open_organization(r)
        r.click('daily_practice_toolbar.bmp',(651,69,851,169),20,20,'escort_open_daily',['anchor_top_menu.bmp'])
        r.wait('daily_practice_anchor.bmp',(951,119,1501,369))
    reuse_daily=False
    if selected_daily is not None:
        current=daily_row_snapshot(s,s.capture())
        reuse_daily=(current is not None and list(current['origin'])==selected_daily.get('selected_row')
                     and current['remaining']==selected_daily.get('remaining')
                     and (not selected_daily.get('counter_proof')
                          or fresh_proof(s.capture(),selected_daily['counter_proof'])))
        if not reuse_daily:
            raise hs.PendingError('已核实的押镖日常行发生变化，未重复导航或前往')
    if not reuse_daily and s.template('daily_limited_tab_current.bmp',(510,250,800,310))[0]:
        r.click('daily_limited_tab_current.bmp',(510,250,800,310),40,15,'escort_limited_tab',['daily_practice_anchor.bmp'])
    for _ in range(15):
        frame=s.capture()
        for asset in ('escort_daily_label.bmp','escort_activity_label.bmp'):
            found,score,x,y=s.template(asset,(480,280,1500,850),frame,threshold=.95)
            if not found:continue
            # Select the positively matched row; the existing detail's Go or
            # Participate control must then be observed independently.
            if not reuse_daily:
                r.click(asset,(x-2,y-2,x+100,y+35),20,10,'escort_select_row',['daily_practice_anchor.bmp'],threshold=.95)
            fresh=s.capture()
            for button in ('escort_go_button.bmp','daily_participate.bmp'):
                ok,_,bx,by=s.template(button,(600,300,1500,900),fresh,threshold=.95)
                if ok:
                    r.click(button,(bx-2,by-2,bx+150,by+55),20,12,'escort_go',['daily_practice_anchor.bmp'],threshold=.95)
                    for _wait in range(40):
                        fresh=s.capture()
                        if layout(s,fresh):return fresh
                        s.wait_interruptible(.5)
                    raise hs.PendingError('押镖前往已发送，等待任务页；不重复前往')
            raise hs.PendingError('押镖入口已选中，前往按钮未核实')
        from hyol_daily_observation import scroll_once
        if not scroll_once(r,'down',frame,'escort_scroll'):break
    r.click('daily_practice_close.bmp',(1370,200,1460,280),17,17,'escort_daily_close',['daily_practice_anchor.bmp'])
    if not s.wait_until_main(8):raise hs.PendingError('日常窗口关闭后尚未核实主界面，未尝试另一入口')
    return open_organization(r)


def open_organization(r):
    s=r.scheduler;frame=s.capture()
    if not s.main_ready(frame):raise hs.PendingError('组织押镖入口前未确认主界面')
    found,_,x,y=s.template('escort_org_entry.bmp',hs.VIEW,frame,threshold=.95)
    if not found:raise hs.PendingError('组织入口未确认；未点击其他功能')
    r.click('escort_org_entry.bmp',(x-2,y-2,x+73,y+74),35,35,'escort_open_organization',
            ['escort_org_entry.bmp'],threshold=.95,anchor_threshold=.95)
    for _ in range(30):
        frame=s.capture()
        found,_,x,y=s.template('escort_activity_label.bmp',hs.VIEW,frame,threshold=.95)
        if found:
            roi=(x+240,y-8,min(1919,x+330),min(1031,y+45))
            if s.template('escort_go_button.bmp',roi,frame,threshold=.95)[0]:
                r.click('escort_go_button.bmp',roi,22,13,'escort_organization_go',
                        ['escort_activity_label.bmp','escort_go_button.bmp'],threshold=.95,anchor_threshold=.95)
                for _wait in range(40):
                    frame=s.capture()
                    if layout(s,frame):return frame
                    s.wait_interruptible(.5)
                raise hs.PendingError('组织押镖前往已发送，任务页尚未确认，不重复发送')
        s.wait_interruptible(.25)
    raise hs.PendingError('组织窗口押镖劫掠及前往按钮未同时确认')


class Driver:
    def __init__(self,r,*,single_kind=None,remaining_only=None,prior_settled=False):
        if single_kind not in (None,'coupon','coin'):
            raise ValueError('单次验收须指定点券或铜币')
        if remaining_only is not None and (single_kind is not None or type(remaining_only) is not int
                                           or remaining_only not in (1,2)):
            raise ValueError('续做剩余次数必须为1或2，且不能混用单次验收')
        if remaining_only is not None and prior_settled is not True:
            raise hs.PendingError('此前押送是否已经交付未确认，不能开始剩余次数')
        self.single_kind=single_kind
        self.remaining_only=remaining_only
        self.targets=(single_kind,) if single_kind else ('coupon',)*remaining_only if remaining_only else TARGETS
        self.prefix='escort_remaining_once' if single_kind else 'escort_remaining' if remaining_only else 'escort'
        self.r=r;self.s=r.scheduler;self.rec=r.record(self.prefix if single_kind or remaining_only else 'escort')
        self.identity_key,self.activity_path = activity_identity(self.s,r.account)
        directory=getattr(self.s,'identity_directory',None)
        day=getattr(getattr(self.s,'state',None),'day',None)
        if self.activity_path is not None:
            if not single_kind and not remaining_only:
                sample=self.activity_path.with_name(self.activity_path.stem+'_remaining_once.json')
                if sample.is_file() and json.loads(sample.read_text('utf-8-sig')).get('active'):
                    raise hs.PendingError('单次验收仍有未决押送，不能另开三次押镖')
                partial=self.activity_path.with_name(self.activity_path.stem+'_remaining.json')
                if partial.is_file():
                    existing=json.loads(partial.read_text('utf-8-sig'))
                    if existing.get('active') or existing.get('legs') or existing.get('baseline_remaining') is not None:
                        raise hs.PendingError('本日已有剩余次数续做凭据，不能另开三次押镖')
            if single_kind:
                if self.activity_path.is_file() and json.loads(self.activity_path.read_text('utf-8-sig')).get('active'):
                    raise hs.PendingError('原三次押镖仍有未决动作，不能另开单次验收')
                self.activity_path=self.activity_path.with_name(self.activity_path.stem+'_remaining_once.json')
            if remaining_only:
                from hyol_checkpoints import read_checkpoint
                original=read_checkpoint(self.activity_path)
                sample=read_checkpoint(self.activity_path.with_name(self.activity_path.stem+'_remaining_once.json'))
                if any(row.get('active') or row.get('legs') or row.get('refresh_pending') for row in (original,sample)):
                    raise hs.PendingError('原押镖已有活动或次数凭据，不能另建剩余次数续做')
                self.activity_path=self.activity_path.with_name(self.activity_path.stem+'_remaining.json')
            if self.activity_path.is_file():
                try:saved=json.loads(self.activity_path.read_text(encoding='utf-8-sig'))
                except (OSError,ValueError) as exc:raise hs.PendingError('押镖当日凭据无法读取，禁止重开') from exc
                if (saved.get('identity_key')!=self.identity_key or saved.get('execution_day')!=day
                        or saved.get('targets')!=list(self.targets)):
                    raise hs.PendingError('押镖当日凭据身份或目标冲突，禁止重开')
                if remaining_only and saved.get('baseline_remaining') not in (None,remaining_only):
                    raise hs.PendingError('剩余押镖原始次数不一致，不能重新设定')
                if saved.get('active') or saved.get('legs') or saved.get('baseline_remaining') is not None:
                    if self.rec.get('active') and self.rec['active']!=saved.get('active'):
                        raise hs.PendingError('押镖本轮与当日凭据冲突，禁止覆盖')
                    self.rec.update(saved)
                    if not (remaining_only and remaining_verified(saved,remaining_only)):
                        self.rec.update(status='pending',home_returned=False,task_completed=False)
            self.rec.update(account=r.account.name,identity_key=self.identity_key,
                            execution_day=day,targets=list(self.targets))
        self.rec.setdefault('legs',[])
        self.rec.setdefault('refreshes',0)

    def complete(self):
        if not self.single_kind and not self.remaining_only:
            return verified(self.rec)
        if self.remaining_only:
            return remaining_verified(self.rec,self.remaining_only)
        return (self.rec.get('status')=='done' and self.rec.get('home_returned') is True
                and self.rec.get('task_completed') is True and not self.rec.get('active')
                and self.valid_legs())

    def valid_legs(self):
        if not self.single_kind and not self.remaining_only:
            return legs_verified(self.rec)
        legs=self.rec.get('legs',[])
        if self.remaining_only:
            return remaining_legs_verified(self.rec,self.remaining_only)
        return (len(legs)==1 and leg_kind_verified(legs[0],self.single_kind)
                and legs[0].get('before_remaining')==1 and legs[0].get('remaining')==0
                and legs[0].get('status')=='done' and legs[0].get('completion_observed') is True
                and bool(legs[0].get('completion_evidence')) and bool(legs[0].get('before_evidence')))

    def save(self):
        if self.activity_path is not None:hs.atomic_json(self.activity_path,self.rec)
        self.r.save()

    def read_panel(self, frame):
        self.s.check_stop()
        self.s.event('escort_progress', self.r.account, task='escort', phase='panel_read',
                     detail='正在核对剩余次数、免费SS次数和押镖奖励')
        started = time.monotonic()
        snapshot = panel(self.s, frame)
        self.s.check_stop()
        missing=[label for key,label in (('remaining','剩余次数'),('free_ss','免费SS次数'),('free_refresh','免费刷新次数'))
                 if snapshot.get(key) is None]
        self.s.event('escort_progress', self.r.account, task='escort', phase='panel_ready',
                     seconds=round(time.monotonic()-started, 3),
                     detail=('押镖面板已读取，'+ '、'.join(missing)+'尚未核实，未接取'
                             if missing else '押镖面板已读清，准备按当前奖励核验下一步'))
        return snapshot

    def require_live_deadline(self, active):
        remaining = seconds_remaining(active)
        active.update(deadline_at=deadline_at(active), deadline_checked_at=time.time(),
                      seconds_remaining=max(0, round(remaining, 1)))
        if remaining <= 0:
            active['deadline_status'] = 'exceeded_pending'
            self.save()
            raise EscortDeadlineExceeded('原押镖已到30分钟本地截止时间；不再发送出发或交付，不重接，实际结果保留待核实')
        if remaining <= DEADLINE_WARNING_SECONDS and not active.get('deadline_warning_sent'):
            active['deadline_warning_sent'] = True
            self.s.event('escort_deadline_warning', self.r.account, task='escort',
                         seconds_remaining=round(remaining), deadline_at=active['deadline_at'],
                         detail='原镖不足5分钟，优先检查终点交付，暂停接取新镖')
        self.save()
        return remaining

    def commit_original(self,active,*,detail=None):
        s=self.s;original=s.state
        source_account=active.get('source_account',self.r.account.name)
        source=active.get('state_directory')
        if source and Path(source)!=original.directory:
            directory=Path(source).resolve()
            if not directory.is_relative_to(s.identity_directory.resolve()):
                raise hs.PendingError('押镖原凭据目录不属于本机身份目录')
            s.state=hs.StateStore([source_account],directory=directory)
        try:
            if detail is None:
                detail='押镖完成提示及两帧完成次数已核实：'+active['completion_evidence']
            status=s.state.action_status(source_account,active['action'])
            if status=='done' and s.state.action_record(source_account,active['action']).get('detail')==detail:
                return
            if status!='pending':
                raise hs.PendingError('押镖原开始凭据未确认，不能补记完成')
            source=hs.Account(self.r.account.index,source_account,self.r.account.tab_x)
            s.commit_action(source,active['action'],detail)
        finally:s.state=original

    def reconcile_unsent(self, active, frame):
        """A prepared intent can be cancelled before the write-ahead click.

        Require the actual original ledger plus two unchanged task counters;
        a pending/done/missing original ledger never authorizes another click.
        """
        from hyol_checkpoints import read_checkpoint
        directory = getattr(self.s, 'identity_directory', None)
        source = active.get('state_directory')
        owner = active.get('source_account')
        day = self.rec.get('execution_day')
        if not isinstance(directory, Path) or not source or not owner or not day:
            return False
        source = Path(source).resolve()
        if not source.is_relative_to(directory.resolve()):
            raise hs.PendingError('押镖原凭据目录不属于本机身份目录')
        path = source/day/(hs.slug(owner)+'.json')
        if not path.is_file():
            return False
        ledger = read_checkpoint(path)
        if (ledger.get('account') != owner or ledger.get('date') != day
                or active['action'] in ledger.get('actions', {})):
            return False
        if layout(self.s, frame) is None:
            return False
        for sample in range(2):
            if sample:
                self.s.wait_interruptible(.3)
                frame = self.s.capture()
            snapshot = panel(self.s, frame)
            if snapshot['remaining'] != active['before_remaining']:
                return False
        # click_point persists the action BEFORE any mouse-down. Its absence
        # in an existing original ledger proves this prepared intent was unsent.
        proof = str(self.s.save_raw_evidence(frame, self.r.account,
                                             'escort_cancelled_before_input', 'verified'))
        self.rec.setdefault('unsent_attempts', []).append(
            {**self.rec.pop('active'), 'status': 'not_sent', 'evidence': proof})
        self.save()
        self.s.event('escort_unsent_intent_verified', self.r.account,
                     evidence=proof, detail='原动作账本无发送凭据且两帧次数未变；保留取消记录，未计押镖完成')
        return True

    def tick(self):
        s,r,rec=self.s,self.r,self.rec
        s.check_stop()
        if self.complete():return True
        active=rec.get('active')
        if getattr(s, 'current_account_only', None) == r.account.name:
            frame = s.capture()
            frame,selected,identity_ok,scores=current_identity_frame(s,r.account,frame,active)
            if not selected or not identity_ok:
                context = getattr(s, 'run_context', None)
                if (selected and active and active.get('departure_sent')
                        and isinstance(getattr(context, 'binding', None), dict)
                        and context.hidden_identity_observation(s, r.account, frame)
                        and context.loading_scene_visible(s, frame)):
                    evidence = str(s.save_raw_evidence(frame, r.account, 'escort_map_loading', 'observed'))
                    s.event('escort_transition_wait', r.account, evidence=evidence,
                            content_authorized=False, detail='原押送切图加载中，仅观察；身份恢复后再核验交付')
                    return False
                evidence = str(s.save_raw_evidence(frame, r.account, 'escort_identity_changed', 'pending'))
                s.event('escort_identity_pending', r.account, evidence=evidence, scores=scores,
                        selected_tab=selected, content_authorized=False)
                raise hs.PendingError('当前押镖账号身份或标签变化，保留原押送')
        else:
            context=getattr(s,'run_context',None)
            if active and isinstance(getattr(context,'binding',None),dict):
                # An owned running escort changes maps and temporarily hides
                # the role strip. Observe its bound tab without authorizing
                # content; every click still uses the fresh identity guard.
                frame=context.select(s,r.account,allow_hidden=True,allow_transition=True)
            else:
                frame=s.switch_account(r.account)
        if active and self.reconcile_unsent(active, frame):
            # Re-enter the remaining-count policy on the next tick. Do not
            # dispatch from this cancelled intent or the legacy fixed scope.
            return False
        if active:
            bonus=s.template('escort_bonus_title_20261028.bmp',(800,460,1130,540),frame,threshold=.98)[0]
            if bonus:
                if (not active.get('delivery_sent') or not active.get('delivery_action')
                        or not Path(active.get('delivery_before_evidence','')).is_file()):
                    raise hs.PendingError('额外奖励弹窗缺少原交付凭据，未确认或推断完成')
                if active.get('bonus_confirm_action'):
                    raise hs.PendingError('原额外奖励确认已请求，不重复确认')
                label=active['delivery_action']+'_bonus_confirm'
                active.update(bonus_confirm_action=label,bonus_account=r.account.name,
                    bonus_state_directory=str(s.state.directory));self.save()
                r.click('escort_bonus_confirm_20261028.bmp',(880,565,1040,650),50,16,label,
                    ['escort_bonus_title_20261028.bmp','escort_bonus_confirm_20261028.bmp'],
                    irreversible=True,threshold=.98,anchor_threshold=.98)
                return False
            if active.get('bonus_confirm_action') and not active.get('bonus_confirmed'):
                for sample in range(3):
                    current=s.capture()
                    if (not s.verify_identity(r.account,current)[0]
                            or s._generic_active_tab_x(current)!=r.account.tab_x
                            or s.template('escort_bonus_title_20261028.bmp',(800,460,1130,540),current,threshold=.98)[0]):
                        raise hs.PendingError('原额外奖励确认后未连续核实账号和弹窗消失')
                    if sample<2:s.wait_interruptible(.2)
                proof=str(s.save_raw_evidence(current,r.account,'escort_bonus_closed','verified'))
                self.commit_original({**active,'action':active['bonus_confirm_action'],
                    'source_account':active['bonus_account'],'state_directory':active['bonus_state_directory'],
                    'completion_evidence':proof},
                    detail='原交付后的额外奖励弹窗已连续三帧关闭；未据此核实押镖次数或记任务完成：'+proof)
                active.update(bonus_confirmed=True,bonus_evidence=proof);self.save()
            if s.template('daily_practice_anchor.bmp',(951,119,1501,369),frame)[0]:
                from hyol_navigation import preparation_blocked
                if preparation_blocked(s,frame) or active.get('daily_panel_close_requested'):
                    raise hs.PendingError('原押镖被日常面板遮挡，关闭状态待确认，不重复关闭或接取')
                active['daily_panel_close_requested']=True;self.save()
                r.click('daily_practice_close.bmp',(1370,200,1460,280),17,17,
                    active['action']+'_close_obstructing_daily',['daily_practice_anchor.bmp','daily_practice_close.bmp'],
                    frame_predicate=lambda fresh:not preparation_blocked(s,fresh))
                return False
            if s.template('escort_support_title.bmp',hs.VIEW,frame,threshold=.97)[0]:
                label=active['action']+'_depart'
                prior_intent=bool(active.get('departure_action'))
                if prior_intent and not unsent_departure(s,r.account,active,label):
                    raise hs.PendingError('原出发已发送但申请支援页仍在，保留原押送，不重复出发')
                found,_,x,y=s.template('escort_depart.bmp',hs.VIEW,frame,threshold=.97)
                if not found:
                    raise hs.PendingError('押镖申请支援页已确认，出发按钮未核实')
                self.require_live_deadline(active)
                if prior_intent:
                    proof=str(s.save_raw_evidence(frame,r.account,'escort_departure_not_sent','verified'))
                    active.update(departure_unsent_verified=True,departure_unsent_evidence=proof)
                    s.event('escort_departure_unsent_verified',r.account,evidence=proof,
                        detail='原接镖凭据存在、原出发动作账本无发送记录；仅首次发送原出发，不重新接镖')
                active.update(departure_action=label,departure_account=r.account.name,
                              departure_state_directory=str(s.state.directory),phase='depart_pending')
                self.save()
                r.click('escort_depart.bmp',(x-2,y-2,x+95,y+35),45,15,label,
                        ['escort_support_title.bmp','escort_depart.bmp'],irreversible=True,
                        threshold=.97,anchor_threshold=.97,
                        frame_predicate=lambda fresh: seconds_remaining(active)>0)
                active.update(phase='departed',departure_sent=True,departure_sent_at=time.time())
                self.save()
                s.event('escort_departed',r.account,kind=active['kind'],leg=active['leg'],operation=label)
                return False
            delivery=delivery_layout(s,frame)
            if delivery:
                if not active.get('departure_sent'):
                    raise hs.PendingError('押镖交付页缺少原出发凭据，保留原任务')
                label=active['action']+'_delivery'
                if active.get('delivery_action') and not unsent_delivery(s,r.account,active,label):
                    raise hs.PendingError('原交付已发送且任务页仍在，不重复领取')
                currency=delivery_reward_currency(s,frame,delivery)
                if currency is None or currency['kind']!=active.get('kind'):
                    raise hs.PendingError('终点奖励实际币种未确认或与原选定币种不一致，未点击交付')
                active['delivery_currency_proof']=currency
                number=delivery_reward_amount(s,frame,delivery,currency=currency)
                if number is None or number['amount']<=0:
                    active['delivery_unreadable_evidence']=str(s.save_raw_evidence(
                        frame,r.account,'escort_delivery_amount_unreadable','pending'))
                    self.save()
                    raise hs.PendingError('终点押镖奖励数字未确认，未点击完成任务')
                if number['amount']!=active['amount']:
                    # Robbery can reduce the delivered reward, and the offer
                    # OCR can be wrong. Keep both reads; the original role,
                    # departure and actual currency authorize this settlement.
                    active['reward_difference']=dict(selected=active['amount'],
                        delivered=number['amount'],reason='actual_terminal_reward_differs')
                active['delivery_amount_proof']=number
                click_proof=number['proof']
                remaining=seconds_remaining(active)
                active.update(deadline_at=deadline_at(active),
                    delivery_seconds_remaining=remaining,
                    delivery_deadline_status='within_deadline' if remaining>0 else 'late_server_completion_visible')
                before=str(s.save_raw_evidence(frame,r.account,'escort_delivery_before','before'))
                active.setdefault('delivery_before_evidence',before)
                active['delivery_latest_before_evidence']=before
                active.update(delivery_action=label,delivery_account=r.account.name,
                              delivery_state_directory=str(s.state.directory),phase='delivery_pending')
                self.save()
                x,y=delivery['x'],delivery['y']
                r.click('escort_complete_task.bmp',delivery_button_roi(delivery),35,8,label,
                        ['escort_delivery_title.bmp','escort_delivery_task.bmp','escort_delivery_reward.bmp'],
                        irreversible=True,threshold=.97,anchor_threshold=.97,
                        frame_predicate=lambda fresh: (current:=delivery_layout(s,fresh)) is not None
                            and (current['x'],current['y'])==(delivery['x'],delivery['y'])
                            and fresh_proof(fresh,click_proof)
                            and fresh_proof(fresh,currency['proof']))
                active.update(delivery_sent=True,phase='delivery_sent',delivery_sent_at=time.time());self.save()
                s.event('escort_delivery_sent',r.account,kind=active['kind'],leg=active['leg'])
                return False
            data=recognize(frame)
            text=compact(data.get('text',''))
            if re.search(r'(?:押镖|押送|护送)(?:任务)?(?:进行)?中',text):
                active['running_observed']=True;self.save()
            if completion_text(data):
                active['completion_evidence']=str(s.save_raw_evidence(frame,r.account,'escort_completed','verified'))
                active['completion_observed']=True;self.save()
            if not active.get('completion_observed') and not active.get('delivery_sent'):
                remaining=self.require_live_deadline(active)
                s.event('escort_waiting',r.account,kind=active['kind'],leg=active['leg'],
                        seconds_remaining=round(remaining),deadline_at=active['deadline_at'],
                        detail='等待原押镖完成；接取后30分钟内须交付，经过时间不作为完成证据')
                return False
            snapshot=panel(s,open_page(r),counters_only=True)
            expected=active['before_remaining']-1
            if snapshot['remaining']!=expected:
                raise hs.PendingError('押镖完成提示与剩余次数未同时核实，保留原场')
            # The explicit completion and fresh completion counter must agree
            # twice; no waiting-duration or disappearing button credit.
            second=panel(s,s.capture(),counters_only=True)
            if second['remaining']!=expected:
                raise hs.PendingError('押镖完成次数复查变化，保留原场')
            if active.get('delivery_sent') and not active.get('completion_observed'):
                active.update(completion_observed=True,
                    completion_evidence=str(s.save_raw_evidence(s.capture(),r.account,'escort_delivered_counter','verified')))
                self.save()
            active.update(free_ss_after=second.get('free_ss'),free_refresh_after=second.get('free_refresh'))
            self.commit_original(active)
            if active.get('departure_action'):
                self.commit_original({**active,'action':active['departure_action'],
                                      'source_account':active['departure_account'],
                                      'state_directory':active['departure_state_directory']})
            if active.get('delivery_action'):
                self.commit_original({**active,'action':active['delivery_action'],
                                      'source_account':active['delivery_account'],
                                      'state_directory':active['delivery_state_directory']})
            active.update(status='done',finished_at=hs.now_iso(),remaining=expected)
            rec['legs'].append(rec.pop('active'));self.save()
            s.event('escort_leg_complete',r.account,kind=active['kind'],leg=active['leg'],evidence=active['completion_evidence'])
            return False
        if len(rec['legs'])==len(self.targets):
            if not self.valid_legs():
                raise hs.PendingError('三次押镖及SS改铜币的凭据不完整')
            from hyol_navigation import return_to_leaf
            f=s.capture();origin=layout(s,f)
            if origin:
                ox,oy=origin
                r.click('escort_close.bmp',(ox+827,oy+52,ox+884,oy+108),17,17,'escort_close',['escort_page_title.bmp'],threshold=.97)
            return_to_leaf(s,r.account,allow_pending_navigation=True)
            rec.update(status='done',task_completed=True,home_returned=True,completed=len(self.targets),
                       coupon_completed=sum(leg['kind']=='coupon' for leg in rec['legs']),
                       coin_completed=sum(leg['kind']=='coin' for leg in rec['legs']),
                       last_error='',finished_at=hs.now_iso())
            if self.remaining_only:
                rec.update(scope='remaining_only',prior_consumed_unverified=3-self.remaining_only,
                           remaining_after=0)
            self.save();return True
        if not opened():
            raise EscortNotOpen('押镖未开放（11:00–14:00、18:00–21:00），本轮保留待处理')
        frame=open_page(r);snapshot=self.read_panel(frame)
        return self.tick_panel(frame,snapshot)

    def tick_panel(self, frame, snapshot):
        """Use only this tick's panel; the input guard rechecks fresh pixels."""
        s,r,rec=self.s,self.r,self.rec
        if self.remaining_only and rec.get('baseline_remaining') is None:
            if snapshot['remaining']!=self.remaining_only:
                raise hs.PendingError('本轮剩余押镖次数与指定范围不一致')
            first=str(s.save_raw_evidence(frame,r.account,'escort_remaining_baseline_1','verified'))
            s.wait_interruptible(.3)
            frame=s.capture()
            if (s._generic_active_tab_x(frame)!=r.account.tab_x or not s.verify_identity(r.account,frame)[0]):
                raise hs.PendingError('复核剩余押镖次数时账号或标签变化')
            second=panel(s,frame)
            if second['remaining']!=self.remaining_only:
                raise hs.PendingError('剩余押镖次数两帧不一致，未开始押送')
            second_evidence=str(s.save_raw_evidence(frame,r.account,'escort_remaining_baseline_2','verified'))
            rec.update(baseline_remaining=self.remaining_only,baseline_evidence=[first,second_evidence],
                       prior_consumed_unverified=3-self.remaining_only,prior_settled_confirmed=True,
                       scope='remaining_only')
            self.save();snapshot=second
        rec['last_panel'] = snapshot
        rec['last_panel_evidence'] = str(s.save_raw_evidence(frame,r.account,'escort_panel','observed'))
        self.save()
        if (snapshot['remaining'] is None or snapshot['remaining']<len(self.targets)-len(rec['legs'])
                or self.remaining_only and snapshot['remaining']!=self.remaining_only-len(rec['legs'])
                or self.single_kind and snapshot['remaining']!=1):
            raise hs.PendingError('押镖剩余次数不足或无法确认，保留已完成次数，不补发')
        pending=rec.get('refresh_pending')
        if pending:
            if snapshot['free_refresh'] != pending['before']-1:
                raise hs.PendingError('免费刷新已发送，剩余免费次数未确认变化，不重复刷新')
            s.commit_action(r.account,pending['action'],'免费刷新次数已确认减少一次')
            rec.pop('refresh_pending');self.save()
        requested=self.targets[len(rec['legs'])]
        kind,fallback=reward_kind(snapshot,requested)
        if kind=='coin' and snapshot.get('rows_complete') is not True:
            raise hs.PendingError('铜币任务金额未全部读清，不能确定当前最高奖励')
        candidates=[offer for offer in snapshot['offers'] if offer['kind']==kind]
        if getattr(self,'prefer_existing_offers',False) and candidates:
            choice,offer='dispatch',max(candidates,key=lambda offer:offer['amount'])
        else:
            choice,offer=choose_offer(snapshot['offers'],kind,snapshot['free_refresh'])
        if choice=='pending':
            raise hs.PendingError('当前押镖奖励或免费刷新未核实')
        if choice=='refresh':
            if rec['refreshes']>=12 or snapshot['free_refresh'] is None:
                raise hs.PendingError('免费刷新上限或次数未核实，不点击付费刷新')
            action=f'{self.prefix}_free_refresh_{rec["refreshes"]+1}'
            rec['refresh_pending']=dict(before=snapshot['free_refresh'],action=action)
            rec['refreshes']+=1;self.save()
            ox,oy=snapshot['origin']
            r.click('escort_free_refresh.bmp',(ox+650,oy+488,ox+800,oy+545),35,12,action,
                    ['escort_page_title.bmp','escort_free_refresh.bmp'],irreversible=True,threshold=.97,anchor_threshold=.97,
                    frame_predicate=lambda fresh: opened() and fresh_proof(fresh,snapshot.get('refresh_proof')))
            return False
        index=len(rec['legs'])+1;action=f'{self.prefix}_{kind}_{index}_start'
        if s.state.action_status(r.account.name,action)!='new':
            raise hs.PendingError('原押镖开始凭据未决，禁止重新押送')
        rec['active']=dict(kind=kind,leg=index,amount=offer['amount'],action=action,
                           base_amount=offer.get('base_amount',offer['amount']),
                           reward_multiplier=offer.get('reward_multiplier',1),
                           amount_readings=offer.get('amount_readings'),
                           requested_kind=requested,fallback_reason=fallback,
                           free_ss_before=snapshot.get('free_ss'),
                           free_refresh_before=snapshot.get('free_refresh'),
                           currency_proof=offer.get('currency_proof'),
                           coupon_offers_before=sum(o['kind']=='coupon' for o in snapshot['offers']),
                           rows_complete_before=snapshot.get('rows_complete'),
                           source_account=r.account.name,
                           before_remaining=snapshot['remaining'],started_at=time.time(),status='pending',
                           before_evidence=str(s.save_raw_evidence(frame,r.account,'escort_before_dispatch','before')))
        rec['active']['deadline_at']=deadline_at(rec['active'])
        rec['active']['deadline_policy']='acceptance_30_minutes'
        directory=getattr(s.state,'directory',None)
        if isinstance(directory,Path):rec['active']['state_directory']=str(directory)
        self.save()
        def same_offer(fresh):
            return (opened() and layout(s,fresh)==snapshot['origin']
                    and seconds_remaining(rec['active'])>0
                    and fresh_proof(fresh,snapshot.get('counter_proof'))
                    and (requested!='coupon' or fresh_proof(fresh,snapshot.get('ss_proof')))
                    and fresh_proof(fresh,offer.get('currency_proof'),s)
                    and fresh_proof(fresh,offer.get('proof')))
        x,y=offer['x'],offer['y']
        s.event('escort_progress',r.account,task='escort',phase='dispatch',
                detail=f'奖励及免费次数已核实，接取第{index}趟'+('点券' if kind=='coupon' else '铜币'))
        r.click('escort_dispatch.bmp',(x-2,y-2,x+40,y+25),14,8,action,
                ['escort_page_title.bmp','escort_dispatch.bmp'],irreversible=True,threshold=.97,anchor_threshold=.97,
                frame_predicate=same_offer)
        s.event('escort_started',r.account,kind=kind,leg=index,reward=offer['amount'],operation=action)
        return False


class RemainingPriorityDriver(Driver):
    """Read actual remaining attempts; prefer SS coupons, then complete coins."""
    def __init__(self,r):
        super().__init__(r)
        self.prefer_existing_offers=True
        self.rec.setdefault('policy_leg_start',len(self.rec.get('legs',())))
        self.rec.setdefault('original_targets',list(self.rec.get('targets',TARGETS)))
        self.rec['completion_policy']='coupon_priority_remaining'
        self.rec['daily_counter_required']=True
        if self.rec.get('policy_baseline_remaining') is None and self.rec.get('active'):
            self.rec['policy_baseline_remaining']=self.rec['active'].get('before_remaining')
        self.save()

    def complete(self):
        return priority_remaining_verified(self.rec)

    def finish_zero(self,samples,free_ss=None):
        self.rec.update(status='done',task_completed=True,home_returned=True,actual_remaining_complete=True,
            free_ss_remaining_after=free_ss,remaining_after=0,remaining_zero_samples=samples,
            completed=len(self.rec['legs']),last_error='',finished_at=hs.now_iso())
        self.save()
        if not priority_remaining_verified(self.rec):
            self.rec.update(status='pending',task_completed=False,policy_status='failed',
                last_error='实际次数为0，但原优先级、交付或日常零次数凭据不足；保留原结果')
            self.save();raise hs.PendingError(self.rec['last_error'])
        self.s.event('escort_remaining_priority_complete',self.r.account,free_ss=free_ss,daily_remaining=0,
            detail='按真实次数归零；日常押镖行及原交付凭据已核对')
        return True

    def tick(self):
        if self.complete():return True
        if self.rec.get('active'):
            return super().tick()
        r,s=self.r,self.s
        if getattr(s,'current_account_only',None)==r.account.name:
            frame=s.capture()
            if (s._generic_active_tab_x(frame)!=r.account.tab_x
                    or not s.verify_identity(r.account,frame)[0]):
                raise hs.PendingError('读取剩余押镖次数前账号或标签未确认')
        else:
            s.switch_account(r.account)
        daily=None
        if self.rec.get('daily_counter_required') and layout(s,s.capture()) is None:
            daily=inspect_daily_remaining(r,leave_open=True)
            self.rec['last_daily_remaining']=daily;self.save()
            if daily['remaining']==0:
                if self.rec.get('refresh_pending'):raise hs.PendingError('日常为0但原免费刷新未决，保留原凭据')
                if self.rec.get('policy_baseline_remaining') is None:self.rec['policy_baseline_remaining']=0
                start=self.rec.get('policy_leg_start');baseline=self.rec.get('policy_baseline_remaining')
                done=len(self.rec.get('legs',()))-start if type(start) is int else -1
                if type(baseline) is int and 0<=done<baseline:
                    # The user/server consumed attempts between runs. This
                    # finishes only the current empty scope, never old legs.
                    self.rec.setdefault('previous_unverified_scopes',[]).append(dict(
                        policy_leg_start=start,policy_baseline_remaining=baseline,
                        scripted_legs=done,externally_consumed_unverified=baseline-done,
                        counted_as_script_completion=False,samples=daily['samples'],observed_at=hs.now_iso()))
                    self.rec.update(policy_leg_start=len(self.rec.get('legs',())),policy_baseline_remaining=0,
                        previous_outcome_pending=True,observed_remaining_scope_only=True)
                self.rec['daily_remaining_after']=0;self.rec['daily_remaining_zero_samples']=daily['samples']
                r.click('daily_practice_close.bmp',(1370,200,1460,280),17,17,
                    'escort_count_close_daily',['daily_practice_anchor.bmp'])
                from hyol_navigation import return_to_leaf
                return_to_leaf(s,r.account,allow_pending_navigation=True)
                return self.finish_zero([daily['samples'][0],daily['samples'][-1]])
        if not opened() and layout(s,s.capture()) is None:
            raise EscortNotOpen('押镖未开放，剩余任务保留待处理')
        frame=open_page(r,selected_daily=daily) if daily is not None else open_page(r)
        snapshot=self.read_panel(frame)
        if not opened() and snapshot.get('remaining')!=0:
            raise EscortNotOpen('押镖未开放，未接新任务；原次数保留')
        if snapshot.get('remaining') is None:
            raise hs.PendingError('当前真实剩余次数未确认，未派发任务')
        if self.rec.get('policy_baseline_remaining') is None:
            self.rec['policy_baseline_remaining']=snapshot['remaining'];self.save()
        if snapshot['remaining']==0:
            if self.rec.get('refresh_pending'):
                raise hs.PendingError('次数已用尽，但原免费刷新结果仍未核实；保留原凭据')
            samples=[str(s.save_raw_evidence(frame,r.account,'escort_remaining_zero_1','verified'))]
            s.wait_interruptible(.3);fresh=s.capture()
            if (s._generic_active_tab_x(fresh)!=r.account.tab_x
                    or not s.verify_identity(r.account,fresh)[0]):
                raise hs.PendingError('剩余次数复核时账号或标签变化')
            second=panel(s,fresh)
            if second.get('remaining')!=0:
                raise hs.PendingError('剩余次数两帧不一致，未记完成')
            samples.append(str(s.save_raw_evidence(fresh,r.account,'escort_remaining_zero_2','verified')))
            ox,oy=second['origin']
            r.click('escort_close.bmp',(ox+827,oy+52,ox+884,oy+108),17,17,'escort_remaining_close',
                    ['escort_page_title.bmp','escort_close.bmp'],threshold=.97,anchor_threshold=.97)
            from hyol_navigation import return_to_leaf
            return_to_leaf(s,r.account,allow_pending_navigation=True)
            if self.rec.get('daily_counter_required'):
                daily=inspect_daily_remaining(r);self.rec['last_daily_remaining']=daily
                if daily['remaining']!=0:
                    self.rec['daily_zero_pending']=True;self.save()
                    raise hs.PendingError('押镖面板为0，但日常活动押镖行0/3尚未核实')
                self.rec['daily_remaining_after']=0;self.rec['daily_remaining_zero_samples']=daily['samples']
            return self.finish_zero(samples,second.get('free_ss'))
        self.targets=('coupon',)*(len(self.rec['legs'])+1)
        return self.tick_panel(frame,snapshot)


def reconcile_previous_for_navigation(s,account):
    """Release a stale daily guard only from original receipts and fresh offers.

    A previous result is archived as unknown, never as a successful leg. An
    unexpired accepted cart, a support/delivery dialog or an unknown original
    ledger must still block preparation.
    """
    from hyol_checkpoints import read_checkpoint
    from hyol_daily_tasks import DailyRunner
    from hyol_navigation import preparation_blocked,organization_world_visible
    key,path=activity_identity(s,account)
    if path is None:return False
    saved=read_checkpoint(path);active=saved.get('active')
    if not isinstance(active,dict):return False
    if not active.get('action') or active.get('delivery_sent') or active.get('completion_observed'):
        return False  # An owned settlement must be reconciled by its own driver.
    source=active.get('state_directory');owner=active.get('source_account')
    day=saved.get('execution_day')
    if not source or not owner or day!=s.state.day:return False
    source=Path(source).resolve()
    if not source.is_relative_to(s.identity_directory.resolve()):return False
    ledger_path=source/day/(hs.slug(owner)+'.json')
    if not ledger_path.is_file():return False
    ledger=read_checkpoint(ledger_path)
    if ledger.get('account')!=owner or ledger.get('date')!=day:return False
    unsent=active['action'] not in ledger.get('actions',{})
    if not unsent:
        try:
            if seconds_remaining(active)>0:return False
        except hs.PendingError:return False
    frame=s.capture()
    # Closing a known friends shell does not abandon a route or a result.
    # It is needed before the same role's idle-world/daily evidence can be read.
    friends='friends_panel_close.bmp';friends_roi=(1870,260,1920,350)
    if (not unsent and not opened() and not preparation_blocked(s,frame)
            and s.verify_identity(account,frame)[0]
            and s.template(friends,friends_roi,frame,threshold=.98)[0]):
        if not s.click_template(account,friends,friends_roi,15,15,
                'escort_previous_close_friends',[friends],threshold=.98,anchor_threshold=.98):return False
        s.wait_interruptible(.3);frame=s.capture()
    if (preparation_blocked(s,frame) or not s.verify_identity(account,frame)[0]
            or s._generic_active_tab_x(frame)!=account.tab_x
            or not (s.main_ready(frame) or organization_world_visible(s,frame) or layout(s,frame))):return False
    r=DailyRunner.for_bound_account(s,account,('escort',),managed_enabled=False)
    r.current='escort'
    driver=Driver(r)
    previous=getattr(s,'navigator',None);s.navigator=r
    try:
        if not unsent and not opened() and s.main_ready(frame) and layout(s,frame) is None:
            # The activity is closed now. A fresh idle village and its own
            # zero counter release navigation, but prove no script delivery.
            from hyol_navigation import LEAF_NAME_ROI
            for index in range(3):
                if index:s.wait_interruptible(.2);frame=s.capture()
                if (not s.main_ready(frame) or preparation_blocked(s,frame)
                        or not s.verify_identity(account,frame)[0]
                        or s._generic_active_tab_x(frame)!=account.tab_x
                        or not s.template('leaf_village_name_current.bmp',LEAF_NAME_ROI,frame,threshold=.92)[0]):return False
            daily=inspect_daily_remaining(r)
            if daily.get('remaining')!=0:return False
            original=driver.rec.pop('active')
            driver.rec.setdefault('previous_unverified_attempts',[]).append({**original,
                'status':'deadline_passed_idle_daily_zero_verified','original_outcome':'unknown',
                'counted_as_completed':False,'observed_at':hs.now_iso(),'current_remaining':0,
                'availability_samples':daily['samples'],'original_ledger':str(ledger_path),
                'original_ledger_sha256':hashlib.sha256(ledger_path.read_bytes()).hexdigest()})
            driver.rec.update(status='pending',task_completed=False,home_returned=False,
                policy_leg_start=len(driver.rec.get('legs',())),policy_baseline_remaining=0,
                previous_outcome_pending=True,last_daily_remaining=daily,
                last_error='原押镖超过半小时；空闲木叶及日常0/3已核实，原交付结果未知且未计脚本完成')
            driver.save()
            s.event('escort_previous_deadline_reconciled',account,task='escort',current_remaining=0,
                evidence=daily['samples'],detail='空闲木叶和日常0/3释放导航；原交付仍未知，原账本未修改')
            return True
        frame=open_page(r)
        if unsent:
            return driver.reconcile_unsent(driver.rec['active'],frame)
        samples=[];baseline=None;deadline=time.monotonic()+12
        while len(samples)<3:
            if samples:s.wait_interruptible(.3)
            frame=s.capture()
            if (preparation_blocked(s,frame) or not s.verify_identity(account,frame)[0]
                    or s._generic_active_tab_x(frame)!=account.tab_x or layout(s,frame) is None):
                return False
            current=panel(s,frame)
            if (type(current.get('remaining')) is not int or not 0<=current['remaining']<=3
                    or current['remaining'] and (not current.get('offers')
                        or current.get('rows_complete') is not True
                        or type(current.get('free_ss')) is not int)):
                samples.clear();baseline=None
                if time.monotonic()>=deadline:return False
                s.wait_interruptible(.3);continue
            signature=(current['remaining'],current.get('free_ss'),current.get('free_refresh'),
                       tuple((o['kind'],o['amount'],o['x'],o['y']) for o in current.get('offers',())))
            if baseline is not None and signature!=baseline:
                samples.clear();baseline=None
                if time.monotonic()>=deadline:return False
                s.wait_interruptible(.3);continue
            baseline=signature
            samples.append(str(s.save_raw_evidence(frame,account,'escort_previous_available_'+str(len(samples)+1),'verified')))
        original=driver.rec['active']
        archived={**original,'status':'deadline_passed_current_offers_verified','original_outcome':'unknown',
            'counted_as_completed':False,'observed_at':hs.now_iso(),'current_remaining':current['remaining'],
            'availability_samples':samples,'original_ledger':str(ledger_path),
            'original_ledger_sha256':hashlib.sha256(ledger_path.read_bytes()).hexdigest()}
        driver.rec.setdefault('previous_unverified_attempts',[]).append(archived)
        driver.rec.pop('active')
        driver.rec.update(status='pending',task_completed=False,home_returned=False,
            policy_leg_start=len(driver.rec.get('legs',())),policy_baseline_remaining=current['remaining'],
            previous_outcome_pending=True,last_error='原接取已超过半小时；当前可接页面三帧核实，原结果未知且未计完成')
        driver.save()
        s.event('escort_previous_deadline_reconciled',account,task='escort',
                current_remaining=current['remaining'],evidence=samples,
                detail='仅释放已过期原场的导航守卫；原账本未改、原结果未知，后续按当前实际剩余次数执行')
        return True
    finally:s.navigator=previous


def driver_for(r):
    config=getattr(r.scheduler,'config',{})
    return RemainingPriorityDriver(r) if config.get('EscortCompletionPolicy')=='coupon_priority_remaining' else Driver(r)


def run(r):
    count=getattr(r,'escort_remaining_only',None)
    driver=(Driver(r,remaining_only=count,prior_settled=getattr(r,'escort_prior_settled',False))
            if count else driver_for(r))
    while not driver.tick():r.scheduler.wait_interruptible(poll_seconds(driver.rec))


def tick_through_departure(driver, scheduler):
    """Settle this cart and send the next departure before releasing its tab."""
    active=driver.rec.get('active')
    limit=time.monotonic()+45 if active and not active.get('departure_sent') else None
    last_pending=None
    for _ in range(90):
        scheduler.check_stop()
        previous_legs=len(driver.rec.get('legs',()))
        previous_refresh=driver.rec.get('refreshes',0)
        previous_delivery=bool((driver.rec.get('active') or {}).get('delivery_sent'))
        try:
            done=driver.tick()
            last_pending=None
        except hs.AccountOffline:
            raise
        except hs.PendingError as exc:
            active=driver.rec.get('active')
            if not active or active.get('departure_sent'):
                raise
            last_pending=exc
            done=False
        active=driver.rec.get('active')
        if done:
            return done
        # Once this visit has positively settled a leg, reuse the open vendor
        # immediately for the next leg (or its zero-count finish). A free
        # refresh is likewise completed on the same tab, not a later sweep.
        if not active:
            if (len(driver.rec.get('legs',()))>previous_legs
                    or driver.rec.get('refreshes',0)>previous_refresh):
                scheduler.wait_interruptible(.3)
                continue
            return False
        if active.get('departure_sent'):
            if active.get('delivery_sent') and not previous_delivery:
                scheduler.wait_interruptible(.5)
                continue
            return False
        if limit is None:limit=time.monotonic()+45
        if time.monotonic()>=limit:
            break
        scheduler.wait_interruptible(.5)
    detail='接镖后出发尚未核实，保持当前账号并停止轮转；原接取与截止凭据保留'
    if last_pending is not None:detail+='：'+str(last_pending)
    driver.rec.update(status='pending',task_completed=False,home_returned=False,last_error=detail)
    driver.save()
    raise hs.SafetyError(detail)


def run_multi(coordinator):
    s=coordinator.scheduler
    drivers=[]
    for account in tuple(s.accounts):
        if account.name not in coordinator.startup_returned or account.name in coordinator.excluded:
            coordinator.note_skipped(account,'escort');continue
        r=coordinator.runner_for(account);r.current='escort'
        if verified(r.record('escort')):continue
        r.record('escort').update(status='running',last_error='');r.save()
        try:drivers.append(driver_for(r))
        except hs.PendingError as exc:
            r.record('escort').update(status='pending',task_completed=False,home_returned=False,last_error=str(exc));r.save()
            coordinator.event('multi_solo_task_pending',account,task='escort',detail=str(exc))
    next_checks={}
    restored_active={id(d) for d in drivers if d.rec.get('active')}
    while drivers:
        # Normal visits retain physical tab order. Only an unstarted accepted
        # cart or a genuinely near deadline can pre-empt that sweep.
        def visit_order(driver):
            active=driver.rec.get('active')
            if active:
                try:urgent=(id(driver) in restored_active or getattr(driver,'pending_checks',0)>0
                            or not active.get('departure_sent') or seconds_remaining(active)<=DEADLINE_WARNING_SECONDS)
                except hs.PendingError:urgent=True
                if urgent:return (*priority_key(driver),driver.r.account.index)
            return (1,driver.r.account.index,driver.r.account.index)
        for driver in sorted(tuple(drivers),key=visit_order):
            s.check_stop()
            active=driver.rec.get('active')
            if active and active.get('departure_sent') and not active.get('delivery_sent'):
                try:urgent=seconds_remaining(active)<=DEADLINE_WARNING_SECONDS
                except hs.PendingError:urgent=True
                departure=active.get('departure_sent_at')
                if not urgent and type(departure) in (int,float):
                    initial=max(0,departure+TRAVEL_FIRST_CHECK_SECONDS-time.time())
                    due=max(next_checks.get(id(driver),0),time.monotonic()+initial)
                    if due>time.monotonic():
                        next_checks[id(driver)]=due
                        continue
            if not driver.rec.get('active'):
                active_drivers=[d for d in drivers if d.rec.get('active')]
                blocked=False
                for current in active_drivers:
                    try:
                        blocked=(getattr(current,'pending_checks',0)>0 or
                                 seconds_remaining(current.rec['active'])<=DEADLINE_WARNING_SECONDS)
                    except hs.PendingError:
                        blocked=True
                    if blocked:break
                if blocked:continue
            s.navigator=driver.r
            try:
                done=tick_through_departure(driver,s)
                if done:drivers.remove(driver)
                driver.pending_checks=0
                restored_active.discard(id(driver))
            except hs.AccountOffline as exc:
                coordinator.skip_disconnected(exc.account,'escort');drivers.remove(driver)
            except hs.SafetyError:
                raise
            except hs.PendingError as exc:
                driver.rec.update(status='pending',task_completed=False,home_returned=False,last_error=str(exc));driver.save()
                driver.pending_checks=getattr(driver,'pending_checks',0)+1
                if (driver.pending_checks<=3 or str(exc)!=getattr(driver,'last_pending_reason',None)
                        or time.monotonic()-getattr(driver,'last_pending_event',0)>=60):
                    coordinator.event('multi_solo_task_pending',driver.r.account,task='escort',detail=str(exc))
                    driver.last_pending_event=time.monotonic()
                driver.last_pending_reason=str(exc)
                active=driver.rec.get('active')
                if isinstance(exc,EscortNotOpen) and not active:
                    driver.rec['availability']=dict(status='not_open',reason=str(exc))
                    driver.save()
                    # The daily counter is intentionally left open until the
                    # escort decision. A closed time window must close that
                    # proven panel before the next task uses the toolbar.
                    if driver.r.return_to_main() is not True:
                        raise hs.LeaseBlocked('押镖未开放，活动面板关闭及回村仍待核实') from exc
                    driver.rec['home_returned']=True
                    driver.save()
                    coordinator.event('multi_solo_task_unavailable',driver.r.account,task='escort',
                        detail=str(exc),task_completed=False,home_returned=True)
                    drivers.remove(driver)
                    continue
                if (active and active.get('departure_sent') and not active.get('delivery_sent')
                        and str(exc).startswith('终点押镖奖励数字未确认') and driver.pending_checks>=3):
                    driver.rec['observation_blocked']=dict(reason=str(exc),checks=driver.pending_checks,
                        evidence=active.get('delivery_unreadable_evidence'),original_outcome='unknown')
                    driver.save();drivers.remove(driver)
                    coordinator.deferred[driver.r.account.name]='escort'
                    coordinator.deferred_at[driver.r.account.name]=time.monotonic()
                    coordinator.event('multi_solo_task_pending',driver.r.account,task='escort',
                        detail='终点金额连续三次未核实，本号保留原交付待处理；继续其他账号，不长时间反复检查')
                    continue
                try:remaining=seconds_remaining(active) if active else 0
                except hs.PendingError:remaining=0
                if active and not isinstance(exc,EscortDeadlineExceeded) and (remaining>0 or driver.pending_checks<3):
                    # A transient unreadable frame must not drop an in-flight
                    # escort from monitoring after only three observations.
                    # Original action receipts continue to prohibit resends.
                    continue
                if driver.rec.get('daily_zero_pending') and driver.pending_checks<3:continue
                if not active and driver.pending_checks<3:continue
                drivers.remove(driver)
                if driver.rec.get('active'):
                    coordinator.deferred[driver.r.account.name]='escort'
                    coordinator.deferred_at[driver.r.account.name]=time.monotonic()
            finally:s.navigator=None
            if driver in drivers:
                active=driver.rec.get('active')
                if active and type(active.get('departure_sent_at')) in (int,float):
                    next_checks[id(driver)]=time.monotonic()+poll_seconds(driver.rec)
        coordinator.save_progress('escort')
        if drivers:
            now=time.monotonic()
            delays=[max(.5,next_checks.get(id(d),now)-now) for d in drivers]
            s.wait_interruptible(min(delays))
