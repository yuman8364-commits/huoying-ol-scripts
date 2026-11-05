"""Visible Bond counters and per-team stopping goals."""
import re
import json
from functools import lru_cache
import numpy as np
from PIL import Image, ImageOps

import hyol_scheduler as hs

MODES = ('battles', 'kills', 'score')
SCORE_ROI = (441, 356, 525, 373)
KILLS_ROI = (1138, 467, 1427, 510)
KILL_DIGITS_ROI = (1328, 478, 1405, 497)
LOBBY_NAME_ROI = (485, 394, 570, 418)


def lobby_identity(frame, store):
    anchor=store.match(frame,hs.ASSETS/'bond_ready_anchor.bmp',(1000,200,1500,450),threshold=.98)
    if not anchor[0]:return None
    dx,dy=anchor[2]-1111,anchor[3]-271
    x1,y1,x2,y2=LOBBY_NAME_ROI
    mask=np.asarray(frame.crop((x1+dx,y1+dy,x2+dx,y2+dy)).convert('L'))>200
    if int(mask.sum())<30:return None  # Matching/results dim the underlying lobby.
    return [''.join(map(str,row.astype(int))) for row in mask]


def lobby_identity_matches(rec, frame, store):
    proof=rec.get('captain_lobby_identity',{})
    return (proof.get('owner')==rec.get('owner') and proof.get('rows') is not None
            and lobby_identity(frame,store)==proof['rows'])


@lru_cache(maxsize=2)
def digit_materials(kind='score'):
    path=hs.ASSETS/f'bond_{kind}_digits_20261030.json'
    if not path.is_file():
        return {}
    material=json.loads(path.read_text('utf-8'))
    lookup={}
    for row in material.get('variants',[]):
        lookup.setdefault(tuple(row['rows']),set()).add(row['digit'])
    return {key:next(iter(values)) for key,values in lookup.items() if len(values)==1}


def exact_score_digits(mask, groups, kind='score'):
    lookup=digit_materials(kind)
    digits=[]
    for columns in groups:
        glyph=mask[:,columns[0]:columns[-1]+1]
        rows=np.flatnonzero(glyph.any(axis=1))
        if not len(rows):return None
        glyph=glyph[rows[0]:rows[-1]+1]
        key=tuple(''.join(map(str,row.astype(int))) for row in glyph)
        if key not in lookup:return None
        digits.append(lookup[key])
    return ''.join(digits)


def validate_options(mode, kills, score, teams):
    if mode not in MODES:
        raise ValueError('羁绊目标须为场数、击杀数或掉分')
    if type(kills) is not int or not 0 <= kills <= 999:
        raise ValueError('羁绊击杀宝箱目标档位须为0～999；0表示160人档也领取完成')
    if type(score) is not int or not 0 <= score <= 99999:
        raise ValueError('羁绊掉分目标须为0～99999')
    if not isinstance(teams, (tuple, list)) or len(teams) > 6:
        raise ValueError('每队羁绊目标格式无效')
    leaders = set()
    for row in teams:
        if (not isinstance(row, (tuple, list)) or len(row) != 3
                or type(row[0]) is not int or row[0] not in range(1, 7)
                or row[0] in leaders or row[1] not in MODES or type(row[2]) is not int):
            raise ValueError('每队羁绊目标须为互不重复的队长标签、目标类型、整数目标')
        limit, minimum = (99, 1) if row[1] == 'battles' else ((999, 0) if row[1] == 'kills' else (99999, 0))
        if not minimum <= row[2] <= limit:
            raise ValueError('每队羁绊目标数值超出范围')
        leaders.add(row[0])


def validate_pairs(pairs, leaders=()):
    if not isinstance(pairs, (tuple,list)) or len(pairs) > 3:
        raise ValueError('二人队配对格式无效')
    used = set()
    for row in pairs:
        if (not isinstance(row, (tuple,list)) or len(row) != 2
                or any(type(i) is not int or i not in range(1,7) for i in row)
                or len(set(row)) != 2 or used.intersection(row)):
            raise ValueError('二人队标签不能重复或跨队重叠')
        used.update(row)
    if pairs and leaders and set(row[0] for row in pairs) != set(leaders):
        raise ValueError('二人队配对须覆盖本轮所选队长')


def team_goal(owner, index):
    mode = getattr(owner, 'bond_target_mode', 'battles')
    target = {'battles': owner.bond_count, 'kills': getattr(owner, 'bond_kill_target', 0),
              'score': getattr(owner, 'bond_score_target', 100)}[mode]
    for leader, selected, value in getattr(owner, 'bond_team_goals', ()):
        if leader == index:
            mode, target = selected, value
    return dict(mode=mode, target=target)


def parse_kills(text):
    compact = re.sub(r'\s+', '', text).replace('／', '/')
    match = re.search(r'(\d{1,3})/(\d{1,3})', compact)
    if not match or '击杀' not in compact or '忍者' not in compact:
        raise hs.PendingError('羁绊右侧击杀数文字和分母尚未确认，不继续匹配')
    value, cap = map(int, match.groups())
    if not 1 <= cap <= 999 or not 0 <= value <= 999:
        raise hs.PendingError('羁绊击杀数范围无法核验')
    return value, cap


def parse_score(text):
    compact = re.sub(r'\s+', '', text)
    if not re.fullmatch(r'\d{1,5}', compact):
        raise hs.PendingError('羁绊段位积分数字尚未确认，不继续匹配')
    return int(compact)


def counters(frame, store=None):
    from hyol_ocr import recognize
    store = store or hs.TemplateStore(.98)
    anchor = store.match(frame, hs.ASSETS/'bond_ready_anchor.bmp', (1000,200,1500,450), threshold=.98)
    if not anchor[0]:
        raise hs.PendingError('羁绊积分和击杀数缺少当前面板定位')
    dx, dy = anchor[2]-1111, anchor[3]-271
    def crop(roi):
        x1,y1,x2,y2 = roi
        return frame.crop((x1+dx,y1+dy,x2+dx,y2+dy))
    # This small white font is unreadable to Windows OCR at its native size.
    mask = np.asarray(crop(SCORE_ROI).convert('L')) > 200
    active = np.flatnonzero(mask.any(axis=0))
    groups = []
    for column in active:
        if not groups or column > groups[-1][-1]+1:
            groups.append([])
        groups[-1].append(int(column))
    if not 1 <= len(groups) <= 5:
        raise hs.PendingError('羁绊积分字形数量无法核验')
    cache = getattr(store,'_bond_counter_cache',None)
    if not isinstance(cache,dict):
        cache = store._bond_counter_cache = {}
    score_key = ('score',mask.tobytes())
    if score_key in cache:
        score = cache[score_key]
    else:
        patch = mask[:,active[0]:active[-1]+1]
        digits = Image.fromarray(np.where(patch,0,255).astype(np.uint8))
        digits = ImageOps.expand(digits.resize((digits.width*12,digits.height*12)),border=24,fill=255)
        text = exact_score_digits(mask,groups)
        if text is None:
            text = re.sub(r'\s+','',recognize(digits)['text'])
        score = parse_score(text)
        if len(text) != len(groups):
            raise hs.PendingError('羁绊积分数字与实际字形数量不一致，不采用截断读数')
        cache[score_key] = score
    kill_image = crop(KILLS_ROI)
    kill_key = ('kills',kill_image.tobytes())
    if kill_key not in cache:
        label_roi=(1160+dx,470+dy,1340+dx,512+dy)
        if not store.match(frame,hs.ASSETS/'bond_kill_counter_label_core_20261030.bmp',label_roi,threshold=.98)[0]:
            raise hs.PendingError('羁绊击杀计数标签未确认，不采用附近其他数字')
        pixels=np.asarray(crop(KILL_DIGITS_ROI).convert('RGB')).astype(int)
        colored=(pixels.max(axis=2)>150)&(pixels.max(axis=2)-pixels.min(axis=2)>70)
        columns=np.flatnonzero(colored.any(axis=0))
        groups=[]
        for column in columns:
            if not groups or column>groups[-1][-1]+1:groups.append([])
            groups[-1].append(int(column))
        if not 3<=len(groups)<=7:
            raise hs.PendingError('羁绊击杀计数字形数量无法核验')
        patch=colored[:,columns[0]:columns[-1]+1]
        digits=Image.fromarray(np.where(patch,0,255).astype(np.uint8))
        digits=ImageOps.expand(digits.resize((digits.width*12,digits.height*12)),border=24,fill=255)
        ratio=exact_score_digits(colored,groups,'kill')
        if ratio is None:
            ratio=re.sub(r'\s+','',recognize(digits)['text']).replace('／','/')
        if not re.fullmatch(r'\d{1,3}/\d{1,3}',ratio) or len(ratio)!=len(groups):
            raise hs.PendingError('羁绊击杀数字与实际字形数量不一致，不采用截断或猜测读数')
        cache[kill_key] = parse_kills('击杀敌方忍者：'+ratio)
    kills, cap = cache[kill_key]
    while len(cache)>64:
        cache.pop(next(iter(cache)))
    return dict(score=score, kills=kills, kill_cap=cap)


def satisfied(goal, observations, members):
    if (goal.get('mode') not in ('kills', 'score') or type(goal.get('target')) is not int
            or set(observations) != set(members) or len(set(members)) not in (1,2)):
        return False
    for name in members:
        row = observations[name]
        if (not isinstance(row, dict) or row.get('owner') != name
                or type(row.get('score')) is not int or row['score'] < 0
                or type(row.get('kills')) is not int or row['kills'] < 0
                or type(row.get('kill_cap')) is not int or row['kill_cap'] <= 0
                or len(row.get('samples', ())) < 2 or not row.get('evidence')):
            return False
        if goal['mode'] == 'score':
            if row['score'] > goal['target']:
                return False
        elif (type(row.get('claimed_tier')) is not int
              or row['claimed_tier'] < (goal['target'] or 160)):
            return False
    return True


def claimed_tiers(rec, name):
    tiers=set()
    for row in rec.get('kill_chest_claims',{}).get(name,{}).values():
        before,after=row.get('before',{}),row.get('after',{})
        if (row.get('status')=='done' and row.get('owner')==name and len(row.get('samples',()))>=2
                and type(before.get('kill_cap')) is int and before['kill_cap']>0
                and type(before.get('kills')) is int and type(after.get('kills')) is int
                and after.get('kill_cap',0)>before['kill_cap']
                and after['kills']==before['kills']-before['kill_cap']):
            tiers.add(before['kill_cap'])
    return sorted(tiers)


def claimed_kills(rec, name):
    return sum(claimed_tiers(rec,name))


def observation_verified(rec, members, goal):
    proof = rec.get('goal_observation', {})
    if goal.get('mode')=='kills':
        for name in members:
            row=proof.get('members',{}).get(name,{})
            if (row.get('banked_kills')!=claimed_kills(rec,name)
                    or row.get('claimed_tier')!=max(claimed_tiers(rec,name),default=0)
                    or type(row.get('tier_kills')) is not int
                    or row.get('kills')!=row['tier_kills']+row['banked_kills']):
                return False
    return (proof.get('run_id') == rec.get('run_id') and proof.get('after_battle') == rec.get('completed')
            and proof.get('goal') == goal and satisfied(goal, proof.get('members', {}), members))
