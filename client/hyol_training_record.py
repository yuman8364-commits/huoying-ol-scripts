"""Claim rewards first, then tailor and refresh each account's daily work."""
from __future__ import annotations

import hashlib
import math
import re
import time
import uuid
from copy import deepcopy

import hyol_scheduler as hs
from hyol_checkpoints import complete
from hyol_ocr import recognize


class TrainingPending(hs.WelfareNotReady):
    pass


TASK_ORDER=('mail','escort','ranking','survival','quiz','arena','puzzle','daruma','treasure','welfare')
MAX_REWARD_SWEEPS=8
MAPPINGS=((r'忍者测验','quiz'),(r'生存演习|生存试炼','survival'),(r'排位赛|排位战','ranking'),
          (r'竞技场','arena'),(r'忍者拼图','puzzle'),(r'达摩','daruma'),
          (r'挖宝|藏宝图','treasure'),(r'领取.*邮件|邮件.*附件','mail'),(r'在线福利','welfare'),(r'押镖|押送','escort'))


def normalized(text):
    return (re.sub(r'\s+','',text).replace('／','/').replace('冫欠','次')
            .replace('强者降临副不','强者降临副本').replace('强者降临副朩','强者降临副本').replace('忍界大覘','忍界大战'))


def counter_hint(words):
    """Locate a damaged counter only when OCR separately located its slash."""
    words=[word for word in words if word['x']<350]
    slashes=[i for i,word in enumerate(words) if normalized(word['text'])=='/']
    if len(slashes)!=1:return None
    slash=slashes[0]
    numeric=lambda word: bool(re.fullmatch(r'[0-9佣巧]+',normalized(word['text'])))
    left=slash-1
    while left>=0 and numeric(words[left]):left-=1
    right=slash+1
    while right<len(words) and numeric(words[right]):right+=1
    left+=1
    if left==slash or right==slash+1:return None
    part=words[left:right]
    left_text=''.join(normalized(word['text']) for word in words[left:slash])
    right_text=''.join(normalized(word['text']) for word in words[slash+1:right])
    damaged=(any(glyph in left_text+right_text for glyph in ('佣','巧'))
             or left_text.isdigit() and right_text.isdigit() and int(left_text)>int(right_text))
    if not damaged:return None
    description=normalized(''.join(word['text'] for word in words[:left]))
    if not description:return None
    def word_box(part):
        return (math.floor(min(word['x'] for word in part))-2,
                math.floor(min(word['y'] for word in part))-2,
                math.ceil(max(word['x']+word['width'] for word in part))+1,
                math.ceil(max(word['y']+word['height'] for word in part))+1)
    left_box=list(word_box(words[left:slash]));right_box=list(word_box(words[slash+1:right]))
    left_box[2]=min(left_box[2],math.floor(words[slash]['x']))
    right_box[0]=max(right_box[0],math.ceil(words[slash]['x']+words[slash]['width']))
    return dict(description=description,left=left_text,right=right_text,
                side_boxes=(tuple(left_box),tuple(right_box)),
                box=(math.floor(min(word['x'] for word in part))-2,
                     math.floor(min(word['y'] for word in part))-2,
                     math.ceil(max(word['x']+word['width'] for word in part))+1,
                     math.ceil(max(word['y']+word['height'] for word in part))+1))


def counter_candidate(frame,body,hint):
    """Return a candidate only if two differently thresholded OCR readings agree.

    This is still only a candidate: another matching page observation is
    required before it may authorize a task or reward click.
    """
    import numpy as np
    from PIL import Image,ImageOps
    x1,y1,x2,y2=hint['box']
    if not (0<=x1<x2<=350 and 0<=y1<y2<=body[3]-body[1]):return None
    readings=[]
    if '巧' in hint['left']+hint['right']:
        # The live font joins the digits "15" into a Chinese-looking glyph.
        # Separate the actual glyph columns on both sides of the OCR-located
        # slash; no character-to-number substitution authorizes a task.
        boxes=hint.get('side_boxes',())
        if len(boxes)!=2:return None
        # At 200 the live font loses the thin lower strokes of "5". Both
        # lower thresholds retain separate glyphs; agreement and the later
        # independent frame are still required before authorizing work.
        for floor in (160,180):
            values=spaced_counter_values(frame,body,boxes,floor)
            if values is None:return None
            readings.append(values)
    else:
        rgb=np.asarray(frame.crop((body[0]+x1,body[1]+y1,body[0]+x2,body[1]+y2)).convert('RGB'),dtype=np.int16)
        for floor in (180,200):
            ink=(rgb.min(axis=2)>=floor)&((rgb.max(axis=2)-rgb.min(axis=2))<=25)
            mask=Image.fromarray(np.where(ink,0,255).astype('uint8')).convert('RGB')
            mask=mask.resize((mask.width*2,mask.height*2),Image.Resampling.NEAREST)
            try:text=recognize(ImageOps.expand(mask,border=12,fill='white')).get('text','').replace('／','/').strip()
            except hs.PendingError:return None
            # The original OCR already located the slash; thresholding can drop it.
            if not re.fullmatch(r"[0-9/'‘’\"\s]+",text):return None
            numbers=re.findall(r'\d+',text)
            if len(numbers)!=2 or any(len(number)>6 for number in numbers):return None
            readings.append((int(numbers[0]),int(numbers[1])))
    if readings[0]!=readings[1]:return None
    current,target=readings[0]
    if not 0<=current<=target or not target:return None
    # Preserve the order of every digit fragment from the original OCR.
    for observed,value in ((hint['left'],str(current)),(hint['right'],str(target))):
        position=0
        for fragment in re.findall(r'\d+',observed):
            position=value.find(fragment,position)
            if position<0:return None
            position+=len(fragment)
    return current,target


def spaced_counter_values(frame,body,boxes,floor):
    """Read both sides as separated glyphs, retaining the observed slash split."""
    import numpy as np
    from PIL import Image
    glyphs=[];counts=[]
    for x1,y1,x2,y2 in boxes:
        if not (0<=x1<x2<=350 and 0<=y1<y2<=body[3]-body[1]):return None
        rgb=np.asarray(frame.crop((body[0]+x1,body[1]+y1,body[0]+x2,body[1]+y2)).convert('RGB'),dtype=np.int16)
        ink=(rgb.min(axis=2)>=floor)&((rgb.max(axis=2)-rgb.min(axis=2))<=25)
        columns=np.flatnonzero(ink.any(axis=0));groups=[]
        for column in columns:
            if not groups or column>groups[-1][-1]+1:groups.append([])
            groups[-1].append(int(column))
        if not 1<=len(groups)<=6:return None
        counts.append(len(groups))
        for group in groups:
            glyph=ink[:,group[0]:group[-1]+1];ys=np.flatnonzero(glyph.any(axis=1))
            if not len(ys):return None
            glyph=glyph[ys[0]:ys[-1]+1]
            if not 6<=glyph.shape[0]<=24 or not 2<=glyph.shape[1]<=14:return None
            glyphs.append(Image.fromarray(np.where(glyph,0,255).astype('uint8')).resize(
                (glyph.shape[1]*6,glyph.shape[0]*6),Image.Resampling.NEAREST))
    canvas=Image.new('L',(sum(g.width for g in glyphs)+30*(len(glyphs)+1),max(g.height for g in glyphs)+40),255)
    x=30
    for glyph in glyphs:canvas.paste(glyph,(x,20));x+=glyph.width+30
    try:text=normalized(recognize(canvas).get('text',''))
    except hs.PendingError:return None
    if not re.fullmatch(r'\d{2,12}',text) or len(text)!=len(glyphs):return None
    return int(text[:counts[0]]),int(text[counts[0]:])


def row_visual_mask(frame,body,row):
    """Capture only the fixed white goal glyphs, excluding reward/status art."""
    import numpy as np
    if row.get('y') is None:return None
    y=body[1]+int(row['y'])
    box=(body[0]+35,y-3,body[0]+350,y+20)
    if box[1]<body[1] or box[3]>body[3]:return None
    rgb=np.asarray(frame.crop(box).convert('RGB'),dtype=np.int16)
    mask=(rgb.min(axis=2)>=180)&((rgb.max(axis=2)-rgb.min(axis=2))<=25)
    return mask if int(mask.sum())>=20 else None


def same_row_mask(first,second):
    """Only merge one-to-one overlaps whose actual goal glyphs are identical."""
    if first is None or second is None or first.shape!=second.shape:return False
    import numpy as np
    height,width=first.shape
    for dy in range(-2,3):
        for dx in range(-2,3):
            a=first[max(0,dy):min(height,height+dy),max(0,dx):min(width,width+dx)]
            b=second[max(0,-dy):min(height,height-dy),max(0,-dx):min(width,width-dx)]
            if int(a.sum())>=20 and np.array_equal(a,b):return True
    return False


def prior_goal_proofs(r):
    """Load only this account's last complete scan, including its actual glyphs."""
    from PIL import Image
    rec=r.record('training_record')
    snapshots=[rec.get(key,{}) for key in ('latest','before')]
    proofs=[];frames={}
    rows=[row for snapshot in snapshots if snapshot.get('scan_complete') is True for row in snapshot.get('rows',[])]
    rows.sort(key=lambda row:(bool(row.get('progress_known')),bool(row.get('task'))),reverse=True)
    for row in rows:
        if row.get('identity_ambiguous'):continue
        box=row.get('proof_box');path=row.get('evidence')
        if not path or not isinstance(box,(list,tuple)) or len(box)!=4:continue
        try:
            if path not in frames:
                with Image.open(path) as source:frames[path]=source.convert('RGB')
            frame=frames[path]
            # proof_box starts 10 px left and 2 px above row_visual_mask.
            body=(box[0]-25,0,box[2],frame.height)
            mask=row_visual_mask(frame,body,dict(y=box[1]+5))
        except (OSError,ValueError,TypeError):continue
        if mask is not None:
            duplicates=[old for old in proofs if same_row_mask(mask,old['mask'])]
            if duplicates and not any(old['row'].get('progress_known') and row.get('progress_known')
                    and (old['row'].get('current'),old['row'].get('target'))!=(row.get('current'),row.get('target'))
                    for old in duplicates):continue
            proofs.append(dict(row=row,mask=mask))
    return proofs


def recover_visual_rows(current,masks,prior,evidence):
    """Recover OCR metadata only when the complete goal and counter glyphs match.

    Claim status is always read from the current page. A changed counter or
    tier cannot borrow an older target, task mapping or remaining budget.
    """
    candidates=[[old for old in prior if same_row_mask(mask,old['mask'])] for mask in masks]
    for row,matches in zip(current,candidates):
        if row.get('identity_ambiguous') or len(matches)!=1:continue
        old=matches[0]['row']
        if sum(old is item['row'] for group in candidates for item in group)!=1:continue
        if not old.get('task') and not old.get('progress_known'):continue
        row.update(key=old['key'],description=old['description'])
        if old.get('task'):row['task']=old['task']
        if old.get('task_label'):row['task_label']=old['task_label']
        # Earlier OCR may be damaged too. Fill missing metadata, but never
        # overwrite a fresh numeric read or bypass a candidate's second frame.
        if old.get('progress_known') and not row.get('progress_known') and not row.get('counter_candidate'):
            row.update(current=old['current'],target=old['target'],progress_known=True)
        row['visual_verified_evidence']=[old.get('evidence',''),evidence]
        if not row.get('completed'):
            row['reason']='目标已达成，奖励待领取' if row.get('claimable') else '同号前后实图字形已核实'
    # A new Arena tier changes its digits, so it must not borrow the old row's
    # budget. Its fixed three-character label can still prove the task type
    # when Chinese OCR splits 场/技 into radicals. Only the type is recovered.
    arena_labels=[old for old in prior if old['row'].get('task')=='arena'
                  and (old['row'].get('description','').startswith('竞技场')
                       or old['row'].get('task_label')=='竞技场')]
    for row,mask in zip(current,masks):
        if (row.get('task') or row.get('identity_ambiguous') or not row.get('progress_known')
                or '获胜' not in row.get('description','') or mask is None):continue
        matches=[old for old in arena_labels if same_row_mask(mask[:,:55],old['mask'][:,:55])]
        if not matches:continue
        row.update(task='arena',task_label='竞技场',
                   task_visual_verified_evidence=[matches[0]['row'].get('evidence',''),evidence])
        if not row.get('completed') and not row.get('claimable'):
            row['reason']='竞技场标签实图已核实，缺口按本页新计数计算'


def merge_scan_page(rows,previous,current,masks,evidence):
    """Join uniquely matching adjacent-page observations without guessing tiers."""
    candidates=[]
    for row,mask in zip(current,masks):
        matches=[]
        for index,old in enumerate(previous):
            oldrow=old['row']
            same=same_row_mask(old['mask'],mask)
            if same and row.get('y',0)>oldrow.get('y',0)+2:same=False
            # A blank mock or missing glyph crop can only represent the same
            # page when its entire OCR line and location are unchanged.
            if (not same and old['mask'] is None and mask is None
                    and oldrow['key']==row['key'] and oldrow['text']==row['text']
                    and oldrow.get('y')==row.get('y')):same=True
            if same and oldrow.get('counter_candidate') and row.get('counter_candidate'):
                same=oldrow['counter_candidate']==row['counter_candidate']
            if same:matches.append(index)
        candidates.append(matches)
    uses={index:sum(index in match for match in candidates) for index in range(len(previous))}
    following=[]
    for row,mask,matches in zip(current,masks,candidates):
        old=previous[matches[0]] if len(matches)==1 and uses[matches[0]]==1 else None
        if old:
            oldrow=old['row'];bucket=old['bucket']
            row['key']=oldrow['key']
            observations=list(oldrow.get('observations',[]))
            if (oldrow.get('counter_candidate') and row.get('counter_candidate')==oldrow['counter_candidate']
                    and oldrow.get('evidence')!=evidence):
                row['progress_known']=True
                row['counter_verified_evidence']=[oldrow.get('evidence'),evidence]
                row['reason']='已有自动化任务' if row.get('task') else '当前版本尚未自动化此任务'
            if oldrow.get('identity_ambiguous'):
                row.update(identity_ambiguous=True,completed=False,claimable=False,task=None,
                           reason='相同文字对应不同目标画面，等待人工核实')
        else:
            bucket=row['key'];observations=[]
            if bucket in rows:
                # Identical OCR descriptions do not prove that two different
                # visual rows are the same reward or tier.
                prior=rows[bucket]
                for item in (prior,row):
                    item.update(identity_ambiguous=True,completed=False,claimable=False,task=None,
                                reason='相同文字对应不同目标画面，等待人工核实')
                bucket=hashlib.sha256((bucket+'|'+evidence+'|'+str(row.get('y'))).encode()).hexdigest()[:16]
                row['key']=bucket
        observations.append(dict(evidence=evidence,raw_text=row.get('raw_text',row.get('text',''))))
        row['observations']=observations
        row['evidence']=evidence
        rows[bucket]=row
        following.append(dict(bucket=bucket,row=row,mask=mask))
    return following


def parse_rows(data):
    groups=[]
    for line in data.get('lines',[]):
        words=line.get('words',[])
        if not words:
            continue
        y=min(word['y'] for word in words)
        target=next((group for group in groups if abs(group['y']-y)<14),None)
        if target is None:
            target={'y':y,'words':[]};groups.append(target)
        target['words'].extend(words)
    rows=[]
    for group in sorted(groups,key=lambda row:row['y']):
        words=sorted(group['words'],key=lambda word:word['x'])
        description_text=normalized(''.join(word['text'] for word in words if word['x']<350))
        status_text=normalized(''.join(word['text'] for word in words if word['x']>=350))
        statuses=re.findall('已完成|已领取|可领取|领取奖励|领取|进行中',status_text)
        text=description_text+''.join(statuses)
        if len(text)<5 or (len(text)<7 and not re.search(r'\d+/\d+',text) and not text.startswith('挖')) or '活动剩余' in text:
            continue
        progress=re.search(r'(\d+)\s*/\s*(\d+)',text)
        if not progress and not re.search('完成|参与|获胜|累计|消耗|达到|获得|登录|通过|挑战|领取|使用|问答|进行中|挖',text):
            continue
        current,total=(int(progress[1]),int(progress[2])) if progress else (None,None)
        known=bool(progress and total and 0<=current<=total)
        done=bool(known and current==total) or '已完成' in text or '已领取' in text
        claimable=('领取' in status_text and '已领取' not in status_text) or '可领取' in description_text
        description=re.sub(r'\d+/\d+.*$','',text)
        for suffix in ('进行中','已完成','可领取','领取奖励'):
            description=description.replace(suffix,'')
        task=next((task for pattern,task in MAPPINGS if re.search(pattern,description)),None)
        if '喵问答' in description or '斗祭' in description or '忍界大战' in description:
            task=None
        hint=counter_hint(words)
        if hint:
            known=False
            done='已完成' in text or '已领取' in text
        rows.append(dict(key=hashlib.sha256(description.encode()).hexdigest()[:16],
                         description=description,text=text,raw_text=text,counter_hint=hint,current=current,target=total,
                         progress_known=known,completed=done and not claimable,
                         claimable=claimable,task=task,y=group['y'],
                         reason='目标已达成，修行录奖励待领取' if claimable else '' if done else '目标计数未可靠识别' if not known
                         else '已有自动化任务' if task else '当前版本尚未自动化此任务'))
    return rows


def planned_tasks(rows, order=None):
    requested={row['task'] for row in rows if row.get('task') and not row.get('completed')
               and not row.get('claimable') and row.get('progress_known')
               and row['current']<row['target']}
    sequence=tuple(dict.fromkeys((*(order or ()),*TASK_ORDER)))
    return tuple(task for task in sequence if task in requested and task in TASK_ORDER)


def goal_summary(rows):
    return dict(total=len(rows),completed=sum(row.get('completed') is True for row in rows),
                remaining=sum(not row.get('completed') for row in rows),
                claimable=sum(row.get('claimable') is True for row in rows),
                unreadable=sum(not row.get('progress_known') and not row.get('completed') for row in rows),
                unsupported=sum(not row.get('task') and not row.get('completed') for row in rows))


def observe_page(r, body, frame):
    data=recognize(frame.crop(body))
    rows=parse_rows(data)
    words=[word for line in data.get('lines',()) for word in line.get('words',())]
    for row in rows:
        hint=row.get('counter_hint')
        if hint:
            candidate=counter_candidate(frame,body,hint)
            if candidate:
                row.update(description=hint['description'],current=candidate[0],target=candidate[1],
                           counter_candidate=list(candidate),progress_known=False,
                           reason='计数候选等待另一帧复核')
                row['key']=hashlib.sha256(row['description'].encode()).hexdigest()[:16]
                row['task']=next((task for pattern,task in MAPPINGS if re.search(pattern,row['description'])),None)
                if any(word in row['description'] for word in ('喵问答','斗祭','忍界大战')):row['task']=None
        y=body[1]+row['y']
        if (not row.get('task') and row['description'].startswith('竞')
                and '获胜' in row['description']):
            # Recover only the fixed task label in this exact row. Counters,
            # claim status and quotas remain independently verified.
            label_roi=(body[0]+28,max(body[1],int(y-4)),body[0]+93,min(body[3],int(y+24)))
            label=r.scheduler.template('training_record_arena_label_20261101.bmp',label_roi,frame,threshold=.99)
            if label[0]:
                row.update(task='arena',task_label='竞技场',
                           task_label_proof=dict(asset='training_record_arena_label_20261101.bmp',
                                                 roi=label_roi,score=label[1]))
        roi=(body[0]+430,max(body[1],int(y-30)),body[2],min(body[3],int(y+45)))
        claimed=r.scheduler.template('training_record_claimed.bmp',roi,frame,threshold=.98)[0]
        claim=r.scheduler.template('training_record_claim.bmp',roi,frame,threshold=.98)
        achieved=row['progress_known'] and row['current']==row['target']
        row.update(claimed=claimed,claimable=claim[0] and not claimed,completed=claimed,status_roi=roi)
        if claim[0]:
            row['claim_roi']=(claim[2]-2,claim[3]-2,claim[2]+101,claim[3]+33)
        row['proof_box']=(body[0]+25,max(body[1],int(y-5)),body[0]+350,min(body[3],int(y+27)))
        points={int(normalized(w['text'])) for w in words if 390<=w['x']<=450 and abs(w['y']-row['y'])<35
                and re.fullmatch(r'\d{2,4}',normalized(w['text']))}
        point_roi=(body[0]+390,max(body[1],int(y+15)),body[0]+435,min(body[3],int(y+40)))
        visual_points=[value for value in (125,150,200) if r.scheduler.template(
            'training_record_points_'+str(value)+'.bmp',point_roi,frame,threshold=.98)[0]]
        row['reward_points']=visual_points[0] if len(visual_points)==1 else next(iter(points)) if len(points)==1 else None
        row['xp_roi']=(body[0]-130,body[1]-28,body[0]-22,body[1]+6)
        if claimed:row['reason']=''
        elif claim[0]:row['reason']='目标已达成，准备领取修行录奖励' if achieved else '领奖前目标计数仍待核实'
        elif achieved:row['reason']='目标已达成，领奖状态尚未核实'
        receipt=reward_receipt(r,row) or {}
        if (receipt.get('status')=='done' and receipt.get('method') in (
                'claimed_stamp','immediate_week_xp_delta','recorded_immediate_week_xp_delta')):
            row.update(completed=True,claimable=False,reason='本轮领奖结果已核实')
        if claim[0] and len(receipt.get('attempts',[]))>=2:
            row['reason']='退出重进并尝试两次后，领奖结果仍待核实'
    return data,rows


def reconcile_claimed_rows(r, rows):
    """Refresh snapshots collected before a claim replaced that goal's tier."""
    for row in rows:
        if row.get('identity_ambiguous'):continue
        receipt=reward_receipt(r,row) or {}
        if (receipt.get('status')=='done' and receipt.get('method') in (
                'claimed_stamp','immediate_week_xp_delta','recorded_immediate_week_xp_delta')
                and receipt.get('evidence')):
            row.update(completed=True,claimable=False,claimed=True,reason='本轮领奖结果已核实')
    return rows


def week_xp(frame, roi):
    try:
        crop=frame.crop(roi)
        text=normalized(recognize(crop.resize((crop.width*3,crop.height*3))).get('text',''))
    except hs.PendingError:return None
    match=re.search(r'(\d{1,5})/6500',text)
    return int(match[1]) if match and 0<=int(match[1])<=6500 else None


def finish_claim(r,rec,evidence,**proof):
    if rec.get('status')=='done':return
    for attempt in rec['attempts']:
        action=attempt['action']
        if r.scheduler.state.action_status(r.account.name,action)=='pending':
            r.scheduler.commit_action(r.account,action,'同一修行录目标领奖结果确认；不按点击次数增加奖励数：'+evidence)
        attempt['status']='settled'
    rec.update(status='done',evidence=evidence,claimed_at=hs.now_iso(),**proof)
    r.save();r.event('training_reward_claim_verified',goal=rec['description'],evidence=evidence,**proof)


def reward_receipt(r,row,create=False):
    claims=r.record('training_record').setdefault('claims',{})
    if row['key'] in claims:return claims[row['key']]
    matches=[rec for rec in claims.values() if normalized(rec['description'])==normalized(row['description'])]
    if len(matches)>1:raise TrainingPending('修行录原领奖目标存在多个来源，未重复领取')
    if matches:return matches[0]
    if create:
        claims[row['key']]=dict(description=row['description'],status='pending',attempts=[])
        return claims[row['key']]
    return None


def verify_immediate_gain(r,row,rec,before):
    points=row.get('reward_points')
    if before is None or type(points) is not int or not 0<points<=1000 or before>=6500:return False
    expected=min(6500,before+points);frames=[]
    # This runs before any other claim or content action. A later aggregate
    # increase can never settle an earlier unknown click.
    for sample in range(3):
        frame=r.scheduler.content_guard(r.account,['training_record_task_tab.bmp'],anchor_threshold=.95)
        if not r.scheduler.verify_identity(r.account,frame)[0]:
            raise TrainingPending('领奖后账号身份变化，保留原领取凭据')
        if week_xp(frame,row['xp_roi'])!=expected:return False
        frames.append(frame)
        if sample<2:r.scheduler.wait_interruptible(.2)
    samples=[str(r.scheduler.save_raw_evidence(frame,r.account,'training_reward_gain_'+str(i),'verified'))
             for i,frame in enumerate(frames,1)]
    finish_claim(r,rec,samples[-1],method='immediate_week_xp_delta',week_before=before,week_after=expected,samples=samples)
    return True


def settle_reward(r, row, frame):
    rec=reward_receipt(r,row)
    if not rec or rec.get('status')=='done' or row.get('claimed') is not True:return
    proof=hashlib.sha256(frame.crop(row['proof_box']).tobytes()).digest()
    for sample in range(3):
        r.scheduler.check_stop()
        if (not r.scheduler.verify_identity(r.account,frame)[0]
                or hashlib.sha256(frame.crop(row['proof_box']).tobytes()).digest()!=proof
                or not r.scheduler.template('training_record_claimed.bmp',row['status_roi'],frame,threshold=.98)[0]):
            raise TrainingPending('原修行录目标已领取印章未连续三帧核实，保留领奖凭据')
        if sample<2:r.scheduler.wait_interruptible(.2);frame=r.scheduler.capture()
    evidence=str(r.scheduler.save_raw_evidence(frame,r.account,'training_reward_'+row['key'],'verified'))
    finish_claim(r,rec,evidence,method='claimed_stamp')


def claim_visible(r, row, frame, visit):
    s=r.scheduler
    if row.get('identity_ambiguous'):return False
    if row.get('claimed'):
        settle_reward(r,row,frame);return False
    if not (row.get('claimable') and row.get('progress_known') and row['current']==row['target']):return False
    rec=reward_receipt(r,row,create=True)
    attempts=rec['attempts']
    if rec.get('status')=='done' or len(attempts)>=2 or attempts and attempts[-1]['visit']==visit:return False
    if attempts:
        if not attempts[-1].get('closed_verified'):
            raise TrainingPending('原领奖后尚未核实退出并重新进入，未重复点击')
        delay=10-(time.time()-attempts[-1]['sent_at'])
        if delay>0:s.wait_interruptible(delay)
    roi=row['claim_roi'];box=row['proof_box']
    proof=hashlib.sha256(frame.crop(box).tobytes()).digest()
    xp_proof=hashlib.sha256(frame.crop(row['xp_roi']).tobytes()).digest() if row.get('xp_roi') else None
    def same_goal(fresh):
        return (hashlib.sha256(fresh.crop(box).tobytes()).digest()==proof
                and (xp_proof is None or hashlib.sha256(fresh.crop(row['xp_roi']).tobytes()).digest()==xp_proof)
                and s.template('training_record_task_tab.bmp',hs.VIEW,fresh,threshold=.95)[0]
                and s.template('training_record_claim.bmp',roi,fresh,threshold=.98)[0]
                and not s.template('training_record_claimed.bmp',roi,fresh,threshold=.98)[0])
    for sample in range(3):
        s.check_stop()
        if not s.verify_identity(r.account,frame)[0] or not same_goal(frame):
            raise TrainingPending('修行录领奖前身份、目标或领取按钮未连续核实，未点击')
        if sample<2:s.wait_interruptible(.2);frame=s.capture()
    number=len(attempts)+1;action=f'training_claim_{row["key"]}_attempt_{number}'
    before=week_xp(frame,row['xp_roi']) if row.get('xp_roi') else None
    attempt=dict(action=action,visit=visit,sent_at=time.time(),status='pending',
                 before_evidence=str(s.save_raw_evidence(frame,r.account,action,'before')))
    attempts.append(attempt);r.save()
    try:
        r.click('training_record_claim.bmp',roi,49,15,action,['training_record_task_tab.bmp'],
                irreversible=True,threshold=.98,anchor_threshold=.95,frame_predicate=same_goal)
    except (hs.AccountOffline, hs.BattleStalled):
        raise
    except hs.PendingError as exc:
        attempt['last_error']=str(exc);r.save()
    r.event('training_reward_claim_requested',goal=row['description'],attempt=number,reentered=number>1,
            detail='最多两次；第二次须先核实退出和重新进入，不在原页连点')
    verify_immediate_gain(r,row,rec,before)
    return True


def arena_goal_request(rows, record):
    saved=record.get('training_goal_request')
    if isinstance(saved,dict):
        if (saved.get('mode')!='wins' or type(saved.get('wins')) is not int
                or not 1<=saved['wins']<=hs.ARENA_MAX_ATTEMPTS):
            raise TrainingPending('修行录竞技场原胜场请求无法核实，未重建请求')
        return saved
    # Existing work keeps its original budget, including an older daily request.
    if record.get('status','new')!='new' or record.get('task_completed'):
        return None
    goals=[row for row in rows if row.get('task')=='arena' and row.get('progress_known')
           and not row.get('completed') and not row.get('claimable')
           and '获胜' in row.get('description','') and row['current']<row['target']]
    if not goals:return None
    wins=max(row['target']-row['current'] for row in goals)
    if not 1<=wins<=hs.ARENA_MAX_ATTEMPTS:
        raise TrainingPending('修行录竞技场所需胜场超出可验证范围')
    saved=dict(mode='wins',wins=wins,goals=[dict(key=row['key'],current=row['current'],
        target=row['target'],evidence=row.get('evidence','')) for row in goals])
    record['training_goal_request']=saved
    return saved


def additional_arena_goal(snapshot,record):
    """A settled request may advance only to a proven higher, newly unlocked tier."""
    if not complete(record):return None
    proposal=arena_goal_request(snapshot.get('rows',[]),{'status':'new'})
    if not proposal:return None
    previous=record.get('training_goal_request')
    if not previous:return proposal  # An existing daily request may fill its first training goal.
    if snapshot.get('scan_complete') is not True or snapshot.get('rewards_complete') is not True:return None
    goals=previous.get('goals',[]) if isinstance(previous,dict) else []
    if not goals or any(type(goal.get('target')) is not int or goal['target']<=0 for goal in goals):return None
    target=max(goal['target'] for goal in goals)
    # The fresh counter must prove the old tier was reached. A changed OCR key
    # or the same incomplete goal cannot reopen a completed battle budget.
    if any(goal['target']<=target or goal['current']<target for goal in proposal['goals']):return None
    return proposal


def layout(r,frame):
    found,_,x,y=r.scheduler.template('training_record_task_tab.bmp',hs.VIEW,frame,threshold=.95)
    if not found:
        return None
    close=(x+100,y-20,min(1919,x+260),y+60)
    if not r.scheduler.template('training_record_close.bmp',close,frame,threshold=.95)[0]:
        return None
    return (max(1,x-485),y+72,min(1919,x+148),min(1031,y+463)),close


def entry_text_match(s, frame):
    """White glyphs stay fixed when the map behind the sidebar changes."""
    import numpy as np
    from PIL import Image
    if not s.template('training_record_sidebar_title.bmp',(1700,335,1900,385),frame,threshold=.98)[0]:
        return False,0.,0,0
    rgb=np.asarray(frame.convert('RGB'),dtype=np.int16)
    ink=(rgb.min(axis=2)>=200)&(rgb.max(axis=2)-rgb.min(axis=2)<=35)
    mask=Image.fromarray((ink.astype(np.uint8)*255)).convert('RGB')
    return s.templates.match(mask,hs.ASSETS/'training_record_entry_textmask.bmp',(1690,340,1919,450),threshold=.98)


def open_record(r):
    s=r.scheduler
    frame=s.capture()
    found=layout(r,frame)
    if found:
        return found
    shell=s.template('training_record_title.bmp',(390,120,770,280),frame,threshold=.97)[0]
    if not shell:
        if not s.main_ready(frame):
            r.return_to_main();frame=s.capture()
        if s.template('training_record_entry.bmp',(1690,340,1919,450),frame,threshold=.94)[0]:
            r.click('training_record_entry.bmp',(1690,340,1919,450),45,7,'training_open_record',
                    ['anchor_top_menu.bmp'],threshold=.94)
        else:
            ok,_score,x,y=entry_text_match(s,frame)
            if not ok:raise TrainingPending('修行录侧栏标题和入口文字未同时确认')
            s.click_point(r.account,x+45,y+7,'training_open_record',
                          ['training_record_sidebar_title.bmp'],anchor_threshold=.98,
                          frame_predicate=lambda fresh: s.main_ready(fresh)
                              and entry_text_match(s,fresh)[0] and entry_text_match(s,fresh)[2:]==(x,y))
    deadline=time.monotonic()+8
    while time.monotonic()<deadline:
        frame=s.capture();found=layout(r,frame)
        if found:return found
        title=s.template('training_record_title.bmp',(390,120,770,280),frame,threshold=.97)[0]
        closed=s.template('training_record_close.bmp',(1350,215,1420,290),frame,threshold=.95)[0]
        if title and closed and s.template('training_record_task_inactive.bmp',(1170,220,1320,285),frame,threshold=.95)[0]:
            r.click('training_record_task_inactive.bmp',(1170,220,1320,285),36,15,
                    'training_open_tasks',['training_record_title.bmp'],threshold=.95,anchor_threshold=.97)
        s.wait_interruptible(.2)
    evidence=str(s.save_raw_evidence(s.capture(),r.account,'training_open_tasks_timeout','pending'))
    raise TrainingPending('修行录任务页和关闭按钮尚未同时确认：'+evidence)


def scroll(r,body,delta,*,steps=1):
    s=r.scheduler
    if s.no_content_clicks:
        raise hs.PendingError('本次调用禁止修行录内容滚动')
    frame=s.content_guard(r.account,['training_record_task_tab.bmp'],anchor_threshold=.95)
    found=layout(r,frame)
    if not found or found[0]!=body:
        raise hs.PendingError('修行录页面已变化，未滚动')
    if s._generic_active_tab_x(frame)!=r.account.tab_x:
        raise hs.SafetyError('修行录选中的账号标签已变化')
    x,y=s.screen_point(body[2]-30,(body[1]+body[3])//2)
    for index in range(steps):
        s.check_input_pause()
        if hs.Win32.foreground()!=s.hwnd:
            raise hs.SafetyError('修行录滚动期间游戏失去前台')
        if index and index%8==0:
            s.content_guard(r.account,['training_record_task_tab.bmp'],anchor_threshold=.95)
        hs.Win32.wheel(x,y,120 if delta>0 else -120, expected_hwnd=s.hwnd)
        s.wait_interruptible(.025)
    s.wait_interruptible(.35)


def scan_steps(r,label,*,collect_rewards=False,_reward_pass=0):
    s=r.scheduler
    visit=uuid.uuid4().hex
    prior=prior_goal_proofs(r)
    body,close=open_record(r)
    previous=None
    # Reward icons and hover decoration can animate while the list is already
    # at the top. Only the goal-text strip decides whether seeking progressed.
    text_body=(body[0]+25,body[1],min(body[2],body[0]+350),body[3])
    for _ in range(10):
        fingerprint=hs.image_hash(s.capture().crop(text_body))
        if fingerprint==previous:break
        previous=fingerprint
        scroll(r,body,120,steps=8)
        yield
    pages=[];rows={};previous_rows=[];last=None;repeated=0;at_end=False;counter_recheck=False
    for index in range(72):
        yield
        s.check_input_pause()
        frame=s.content_guard(r.account,['training_record_task_tab.bmp'],anchor_threshold=.95)
        if not layout(r,frame):raise TrainingPending('修行录翻页期间页面未确认')
        evidence=str(s.save_raw_evidence(frame,r.account,f'training_{label}_{index}','observed'))
        data,current=observe_page(r,body,frame)
        if not current:raise TrainingPending('修行录当前页未识别到完整任务，不能视为空列表')
        masks=[row_visual_mask(frame,body,row) for row in current]
        recover_visual_rows(current,masks,prior,evidence)
        signature=tuple((int(row.get('y',0)),hashlib.sha256(mask.tobytes()).hexdigest() if mask is not None else row['text'])
                        for row,mask in zip(current,masks))
        repeated=repeated+1 if signature==last else 0
        last=signature
        previous_rows=merge_scan_page(rows,previous_rows,current,masks,evidence)
        pages.append(dict(index=index,evidence=evidence,ocr=data,rows=current))
        if (not counter_recheck and any(row.get('counter_candidate') and not row.get('progress_known')
                                       and not row.get('completed') for row in current)):
            counter_recheck=True
            continue  # A repaired counter needs another live frame before input or scrolling.
        if collect_rewards:
            for row in current:
                if claim_visible(r,row,frame,visit):
                    repeated=0;last=None;counter_recheck=False
                    break
            else:row=None
            if row is not None:
                continue  # Re-read the changed page before touching another row.
        if repeated>=2:
            at_end=True;break
        counter_recheck=False
        scroll(r,body,-120,steps=8)
    if not at_end:raise TrainingPending('修行录72次观察内未确认列表末尾或领取结果，保留未扫描目标')
    frame=s.capture()
    if not layout(r,frame):raise TrainingPending('修行录退出前页面未确认')
    r.click('training_record_close.bmp',close,14,14,'training_close_record',
            ['training_record_task_tab.bmp'],threshold=.95)
    if not s.wait_until_main(8):raise TrainingPending('修行录退出后尚未核实木叶主界面')
    claims=r.record('training_record').get('claims',{}) if collect_rewards else {}
    retry=False
    for rec in claims.values():
        attempts=rec.get('attempts',[])
        if rec.get('status')!='done' and attempts and attempts[-1]['visit']==visit:
            attempts[-1]['closed_verified']=True
            retry=retry or len(attempts)<2
    if collect_rewards:r.save()
    if retry and _reward_pass==0:
        r.event('training_reward_reenter',detail='领奖未确认；已退出并确认主界面，重新进入后核实同一目标再重试')
        following=yield from scan_steps(r,label+'_reenter',collect_rewards=True,_reward_pass=1)
        following['pages']=pages+following['pages']
        return following
    return dict(rows=reconcile_claimed_rows(r,list(rows.values())),pages=pages,scan_complete=True,at=hs.now_iso())


def drain_steps(iterator):
    while True:
        try:next(iterator)
        except StopIteration as result:return result.value


def scan(r,label,*,collect_rewards=False,_reward_pass=0):
    return drain_steps(scan_steps(r,label,collect_rewards=collect_rewards,_reward_pass=_reward_pass))


def _legacy_scan_steps(*args,**kwargs):
    result=scan(*args,**kwargs)
    if False:yield
    return result


def refresh_goals(r,label):
    return drain_steps(refresh_goal_steps(r,label,scanner=_legacy_scan_steps))


def refresh_goal_steps(r,label,*,scanner=scan_steps):
    """Only a separate, complete scan after all claims may authorize new work.

    A claim can replace a tier or unlock a row on a page already visited. The
    collector's mixed snapshots are evidence of claims, never a task plan.
    Unknown receipts retain their original two-attempt budget in scan().
    """
    rec=r.record('training_record')
    rec.update(rewards_complete=False,phase='collecting_rewards')
    r.save()
    for sweep in range(1,MAX_REWARD_SWEEPS+1):
        collection=dict(label=label,sweep=sweep,status='collecting')
        rec['reward_collection']=collection
        r.save()
        collected=yield from scanner(r,f'{label}_claim_{sweep}',collect_rewards=True)
        if collected.get('scan_complete') is not True:
            raise TrainingPending('修行录领奖列表尚未完整遍历，未生成日常计划')
        collection.update(status='rescanning',claim_scan_at=collected['at'])
        rec['phase']='scanning_after_rewards';r.save()
        fresh=yield from scanner(r,f'{label}_fresh_{sweep}')
        if fresh.get('scan_complete') is not True:
            raise TrainingPending('修行录领奖后的独立复扫未完成，未生成日常计划')
        pending=[key for key,claim in rec.get('claims',{}).items() if claim.get('status')!='done']
        claimable=[row for row in fresh['rows'] if row.get('claimable') and not row.get('completed')]
        collection.update(scan_at=fresh['at'],claimable=len(claimable),pending_claims=pending)
        rec.update(latest=fresh,remaining=[row for row in fresh['rows'] if not row.get('completed')])
        if pending:
            collection['status']='pending'
            for key in pending:
                if not any(row.get('key')==key for row in rec['remaining']):
                    rec['remaining'].append(dict(key=key,description=rec['claims'][key]['description'],
                        task=None,completed=False,reason='原领奖结果仍未核实，暂停重新排日常'))
            rec.update(status='pending',phase='claim_pending',task_completed=False)
            r.save()
            raise TrainingPending('修行录原领奖结果仍未核实，保留凭据并暂停重新排日常')
        if claimable:
            if any(not row.get('progress_known') or row.get('current')!=row.get('target') for row in claimable):
                collection['status']='pending'
                rec.update(status='pending',phase='claim_pending',task_completed=False)
                r.save()
                raise TrainingPending('修行录仍有可领取目标，但领奖前计数未核实，暂停重新排日常')
            collection['status']='more_rewards';r.save()
            # Newly unlocked tiers are collected before any new task starts.
            continue
        collection['status']='verified'
        fresh['rewards_complete']=True
        rec['rewards_complete']=True;r.save()
        r.event('training_rewards_cleared',sweeps=sweep,summary=goal_summary(fresh['rows']),
                detail='全页领奖后已独立复扫，当前无可领取奖励及未决领奖凭据')
        return fresh
    rec.update(status='pending',phase='claim_pending',task_completed=False)
    collection['status']='pending';r.save()
    raise TrainingPending('修行录连续出现可领取目标，尚未确认领完，未生成日常计划')


def daily_plan(r,snapshot,order,day,attempted,errors):
    """Describe only this account's post-claim deficits; never sum overlapping goals."""
    from hyol_daily_tasks import calendar_task_plan
    if (snapshot.get('scan_complete') is not True or snapshot.get('rewards_complete') is not True
            or any(row.get('claimable') and not row.get('completed') for row in snapshot.get('rows',[]))):
        raise TrainingPending('修行录尚未确认全页领完并独立复扫，未生成本号日常计划')
    rows=snapshot['rows']
    calendar=calendar_task_plan(planned_tasks(rows,order),day)
    tasks=[]
    for task in calendar['requested_tasks']:
        goals=[dict(key=row['key'],description=row['description'],current=row['current'],
                    target=row['target'],remaining=row['target']-row['current'],evidence=row.get('evidence',''))
               for row in rows if row.get('task')==task and not row.get('completed')
               and not row.get('claimable') and row.get('progress_known') and row['current']<row['target']]
        record=getattr(r,'records',{}).get(task,{})
        extend=task=='arena' and task in attempted and additional_arena_goal(snapshot,record) is not None
        status=('not_open' if task in calendar['calendar_skipped'] else 'pending' if task in errors
                else 'checked' if task in attempted and not extend else 'ready')
        from hyol_training_policy import quantity_plan, local_day
        item=dict(task=task,status=status,goals=goals,**quantity_plan(task,goals,day or local_day()))
        wins=[goal['remaining'] for goal in goals if '获胜' in goal['description']]
        if task=='arena' and wins:item['required_wins']=max(wins)
        if task=='arena':
            item['max_battles_per_run']=(None if getattr(r.scheduler,'arena_calendar_policy_enabled',False)
                                         else hs.ARENA_MAX_BATTLES_PER_RUN)
            remaining=getattr(r.scheduler,'arena_attempts_remaining',None)
            if callable(remaining):
                available=remaining(r.account)
                if type(available) is int:
                    item['attempts_remaining']=available
                    if available==0 and task not in errors:
                        item.update(status='deferred',reason='本轮竞技场已达3场，目标保留下次继续')
        tasks.append(item)
    account=getattr(r,'account',None)
    plan=dict(account=getattr(account,'name',''),execution_day=calendar['execution_day'],
              basis='after_rewards',snapshot_at=snapshot.get('at',''),
              rewards_complete=snapshot.get('rewards_complete') is True,tasks=tasks)
    return plan,calendar


def current_goal_rows(r):
    return current_goal_snapshot(r).get('rows',[])


def current_goal_snapshot(r):
    rec=r.record('training_record')
    return rec.get('latest',rec.get('before',{}))


def execute_supported(r,task):
    rec=r.record(task)
    if task=='arena' and complete(rec):
        extra=additional_arena_goal(current_goal_snapshot(r),rec)
        if extra:
            if rec.get('training_goal_request'):
                rec.setdefault('training_goal_request_history',[]).append(dict(
                    request=deepcopy(rec['training_goal_request']),evidence=rec.get('evidence',''),
                    finished_at=rec.get('finished_at','')))
            else:
                rec.setdefault('daily_completion_before_training',{key:rec[key] for key in (
                    'status','task_completed','home_returned','finished_at','completed','evidence') if key in rec})
            rec.update(status='pending',task_completed=False,home_returned=False)
            rec['training_goal_request']=extra
            r.save()
            r.event('training_arena_additional_goal',wins=extra['wins'],
                    detail='前一请求已完成；仅按领奖后新解锁档位的可靠缺口追加胜场，原请求与战斗凭据保留')
    if complete(rec):
        r.event('training_task_preserved',task=task,detail='本轮已有完成与回村记录')
        return
    original_tasks,original_current=r.selected_tasks,r.current
    original_accounts=getattr(r.scheduler,'accounts',None)
    original_arena=None
    if task=='arena':
        request=arena_goal_request(current_goal_rows(r),rec)
        if request:
            original_arena=r.arena_wins,r.arena_target_mode
            r.arena_wins,r.arena_target_mode=request['wins'],request['mode']
    r.selected_tasks=(task,);r.current=task
    rec.update(status='running')
    r.save()
    observation=r.scheduler.begin_task_observation(r.account,task)
    try:
        finished=rec.get('task_completed') is True
        if task=='ranking':finished=finished and rec.get('zero_verified') is True
        if task=='puzzle':
            from hyol_puzzle import progress_complete
            finished=finished and progress_complete(rec.get('puzzle'))
        if task=='daruma':
            from hyol_daruma_task import verified
            finished=finished and verified(rec)
        if finished and task=='survival':
            r.scheduler.reconcile_survival_result_confirmations(r.account)
        if not finished:getattr(r,task)()
        rec['task_completed']=True;r.save()
        r.return_to_main()
        evidence=str(r.scheduler.save_raw_evidence(r.scheduler.capture(),r.account,'training_'+task+'_complete','verified'))
        rec.update(status='done',home_returned=True,evidence=evidence,finished_at=hs.now_iso(),last_error='')
    except Exception as exc:
        rec.update(status='pending',last_error=str(exc))
        raise
    finally:
        r.scheduler.end_task_observation(observation,complete=rec.get('task_completed',False))
        if original_accounts is not None:
            r.scheduler.accounts=original_accounts
        if original_arena is not None:r.arena_wins,r.arena_target_mode=original_arena
        r.selected_tasks=original_tasks;r.current=original_current;r.save()


def unresolved_supported_tasks(r):
    """Find this parent's original subtask receipts before navigating elsewhere."""
    records=getattr(r,'records',{})
    if not isinstance(records,dict) or not records:
        return ()
    accounts=getattr(getattr(r.scheduler,'state',None),'accounts',{})
    owner=accounts.get(r.account.name,{}) if isinstance(accounts,dict) else {}
    actions=owner.get('actions',{})
    result=[]
    from hyol_manual_continuation import quarantined_external_action
    for task in TASK_ORDER:
        record=records.get(task,{})
        active=record.get('active')
        pending_active=isinstance(active,dict) and active.get('status')!='done'
        pending_active=pending_active or (task=='escort' and bool(record.get('legs'))
                                         and not record.get('task_completed'))
        pending_phase=(record.get('status')=='pending'
                       and record.get('phase','ready') not in ('ready','new','done','verified'))
        prefixes=(task+'_','daily_'+task+'_')
        pending_action=isinstance(actions,dict) and any(
            name.startswith(prefixes) and isinstance(row,dict) and row.get('status')=='pending'
            and not quarantined_external_action(row,r.account.name)
            for name,row in actions.items())
        if pending_active or pending_phase or pending_action:
            result.append(task)
    return tuple(result)


def run(r):
    if getattr(getattr(r,'scheduler',None),'training_cooperative_enabled',False):
        from hyol_training_coordinator import run_single
        return run_single(r)
    return run_legacy(r)


def run_legacy(r):
    from hyol_task_order import load as load_order
    rec=r.record('training_record')
    original=set(rec.get('planned_tasks',()))
    pending=unresolved_supported_tasks(r)
    attempted=set()
    if len(pending)>1:
        raise TrainingPending('多个原子任务仍有未决动作，未打开修行录或推断当前场景')
    for task in pending:
        if task not in original:
            raise TrainingPending(f'原{task}仍有未决任务，未打开修行录或执行其他目标')
        rec.update(status='pending',phase='resuming_supported',current_task=task)
        r.save()
        try:
            execute_supported(r,task)
            attempted.add(task)
        except hs.PendingError as exc:
            rec.update(status='pending',task_completed=False,last_error=str(exc))
            rec.setdefault('errors',{})[task]=str(exc)
            r.save()
            raise TrainingPending(f'先恢复修行录原{task}：{exc}') from exc
    rec.update(status='running',phase='collecting_rewards',task_completed=False,all_goals_complete=False,
               rewards_complete=False,daily_plan=dict(rewards_complete=False,tasks=[]))
    r.save()
    before=refresh_goals(r,'before')
    order=load_order(hs.ROOT)['order']['solo']
    day=getattr(getattr(getattr(r,'scheduler',None),'state',None),'day',None)
    day=day if isinstance(day,str) else None
    rec.update(before=before,before_summary=goal_summary(before['rows']),remaining=[row for row in before['rows'] if not row['completed']],
               planned_tasks=[],errors={},current_task='')
    errors={};snapshot=before
    # Daily handlers keep their original journal. Arena alone can advance to
    # a higher proven tier, preserving every settled request's original budget.
    for _ in range(len(TASK_ORDER)+hs.ARENA_MAX_ATTEMPTS):
        plan,calendar=daily_plan(r,snapshot,order,day,attempted,errors)
        next_tasks=[item['task'] for item in plan['tasks'] if item['status']=='ready']
        rec.update(daily_plan=plan,latest=snapshot,next_tasks=next_tasks,
                   not_open_tasks=list(calendar['calendar_skipped']),
                   planned_tasks=list(dict.fromkeys((*rec['planned_tasks'],*calendar['effective_tasks']))),
                   phase='executing' if next_tasks else 'rechecked',errors=errors)
        r.save()
        r.event('training_plan_detected',tasks=next_tasks,summary=goal_summary(snapshot['rows']),plan=plan,
                detail='已领完奖励；按该账号最新目标缺口安排日常，每完成一项重新领取并计算')
        if not next_tasks:break
        task=next_tasks[0]
        attempted.add(task)
        rec.update(current_task=task);r.save()
        r.event('training_supported_task_started',task=task)
        try:
            execute_supported(r,task)
        except (hs.AccountOffline,hs.BattleStalled):
            raise
        except hs.PendingError as exc:
            errors[task]=str(exc)
            if (unresolved_supported_tasks(r)
                    or not r.scheduler.main_ready(r.scheduler.capture())):
                rec.update(status='pending',errors=errors);r.save()
                raise TrainingPending(f'修行录支持任务{task}待核实：{exc}') from exc
        rec.update(phase='collecting_rewards',errors=errors,current_task='');r.save()
        snapshot=refresh_goals(r,'after_'+task)
        rec.update(after=snapshot,after_summary=goal_summary(snapshot['rows']));r.save()
    else:
        errors['planning']='本轮连续解锁目标达到观察上限，保留当前新档位，下次按原凭据继续'
    after=snapshot
    for row in after['rows']:
        if row.get('task') in calendar['calendar_skipped'] and not row.get('completed'):
            row['reason']='执行日活动未开放，保留待处理'
        elif (row.get('task') in attempted and not row.get('completed')
                and not row.get('claimable') and row.get('task') not in errors):
            row['reason']='本轮对应任务已执行，修行录目标仍未达成'
    remaining=[row for row in after['rows'] if not row['completed']]
    for key,claim in rec.get('claims',{}).items():
        if claim.get('status')!='done' and not any(row['key']==key for row in remaining):
            remaining.append(dict(key=key,description=claim['description'],task=None,completed=False,
                                  reason='原领奖结果仍未核实；最多两次且重试前重新进入'))
    rec.update(after=after,after_summary=goal_summary(after['rows']),remaining=remaining,errors=errors,
               phase='pending' if remaining or errors else 'verified',
               supported_tasks_complete=not errors,all_goals_complete=not remaining and not errors)
    r.save()
    r.event('training_goals_rechecked',completed=sum(row.get('completed') is True for row in after['rows']),remaining=len(remaining),
            detail='；'.join(row['description'] for row in remaining[:6]))
    if remaining or errors:
        rec.update(status='pending',task_completed=False)
        r.save()
        raise TrainingPending(f'修行录已复查：{len(remaining)}项目标待完成或领奖；支持任务已优先处理，详见逐号记录')
