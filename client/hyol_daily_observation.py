"""Observe daily rows separately from task execution and claim receipts."""
from datetime import datetime, timezone, timedelta
import re
import time
import hyol_scheduler as hs

ALIASES = {'quiz': ('忍者测验',), 'arena': ('竞技场',), 'ranking': ('排位战', '排位赛'),
           'survival': ('生存演习',), 'escort': ('押镖劫掠', '押镖'),
           'group_instance': ('组队副本',), 'strong_approach': ('强者降临',),
           'wanted': ('通缉任务',), 'sealed': ('封印忍者', '被封印'),
           'rescue_jinchuriki': ('拯救人柱力', '营救人柱力'), 'team_pvp': ('组队竞技',),
           'treasure': ('挖宝', '藏宝图')}
BODY = (505, 315, 1065, 715)
ROW_ASSETS={'quiz':'daily_observed_quiz.bmp','arena':'daily_observed_arena.bmp',
            'survival':'daily_survival_current_row.bmp','group_instance':'daily_group_current_row.bmp',
            'strong_approach':'daily_observed_strong.bmp','wanted':'daily_wanted_current_row.bmp',
            'escort':'escort_daily_label.bmp'}
DONE_ASSETS=('daily_completed_20261109.bmp','daily_completed_arena_20261109.bmp',
             'daily_completed_survival_20261109.bmp')
TAB_TASKS={'normal':{'quiz','arena','survival','strong_approach','wanted'},
           'limited':{'escort','group_instance'}}


def row_locations(s, frame, chosen=None):
    rows={}
    for code in ROW_ASSETS if chosen is None else chosen:
        name=ROW_ASSETS.get(code)
        if not name:continue
        ok,_,x,y=s.template(name,(505,315,660,715),frame,threshold=.94)
        if ok:rows[code]=(x,y)
    return rows


def list_position(s, frame):
    """Only named row positions prove scrolling; animated icons do not."""
    return tuple(sorted((code,y) for code,(_,y) in row_locations(s,frame).items()))


def scroll_once(runner, direction, frame, label):
    s=runner.scheduler
    before=list_position(s,frame)
    if not before:return False
    name='daily_scroll_up_current.bmp' if direction=='up' else 'daily_scroll_down_arrow_20261109.bmp'
    roi=(1060,315,1095,370) if direction=='up' else (1060,660,1095,720)
    if not s.template(name,roi,frame)[0]:return False
    # The old down template included a decorative diamond above the arrow.
    # This tightly enrolled arrow head excludes that changing decoration.
    runner.click(name,roi,10,10,label,['daily_practice_anchor.bmp'])
    s.wait_interruptible(.2)
    after=list_position(s,s.capture())
    moved=bool(after and after!=before)
    if not moved:
        runner.event('daily_scroll_boundary',detail='日常列表位置未变化，停止向'+('上' if direction=='up' else '下')+'翻页')
    return moved


def compact(value):
    return re.sub(r'\s+', '', value or '').replace('：', ':').replace('／', '/')


def parse_rows(data, now=None):
    now = now or datetime.now(timezone(timedelta(hours=8)))
    lines = []
    for line in data.get('lines', []):
        words = line.get('words', [])
        if words:
            lines.append((min(w['y'] for w in words), compact(line.get('text', ''))))
    names = []
    for y, content in lines:
        for code, aliases in ALIASES.items():
            if any(alias in content for alias in aliases) and not (code == 'arena' and '组队竞技' in content):
                names.append((y, code))
    rows = {}
    for y, code in sorted(set(names)):
        upper = min([other for other, _ in names if other > y+10] or [y+55])
        content = ''.join(value for ly, value in lines if y-12 <= ly < min(upper-5,y+50))
        activity = re.search(r'活跃度:(\d{1,3})/(\d{1,3})', content)
        counter = re.search(r'剩余(?:奖励)?次数:(\d{1,3})/(\d{1,3})', content)
        # The game's 活跃值 column is the reward still available, not progress
        # earned. Full activity numbers do not mean completed.
        completed = '已完成' in content
        remaining = int(counter[1]) if counter else None
        slots = re.findall(r'(\d{1,2}):(\d{2})[-—~～](\d{1,2}):(\d{2})', content)
        minute = now.hour*60+now.minute
        opened = None if not slots else any(int(h)*60+int(m) <= minute < int(eh)*60+int(em) for h,m,eh,em in slots)
        label = '日常已完成' if completed else '日常尚未完成' if activity else '日常完成情况未读出'
        if remaining is not None:
            label += ' · 次数用尽' if remaining == 0 else f' · 剩余{remaining}次'
        if opened is False and remaining != 0:
            label += ' · 当前未开放'
        rows[code] = dict(daily_complete=completed if activity else None, remaining=remaining,
                          total=int(counter[2]) if counter else None, opened=opened, label=label,
                          row_text=content, source='daily_panel', row_y=y)
    return rows


def observe_frame(s, frame, chosen):
    from hyol_ocr import recognize
    rows={}
    for code,(x,y) in row_locations(s,frame,chosen).items():
        done=any(s.template(asset,(990,max(315,y-12),1060,min(715,y+50)),frame,threshold=.96)[0]
                 for asset in DONE_ASSETS)
        crop=frame.crop((760,y+16,890,min(715,y+43)))
        data=recognize(crop.resize((crop.width*2,crop.height*2)))
        content=compact(data.get('text',''))
        counts=re.findall(r'(?<!\d)(\d{1,3})/(\d{1,3})(?!\d)',content)
        remaining,total=map(int,counts[0]) if len(counts)==1 else (None,None)
        opened=None
        if code=='escort':
            from hyol_escort import opened as escort_opened, daily_row_snapshot
            opened=escort_opened()
            counter=daily_row_snapshot(s,frame)
            if counter and counter.get('remaining') is not None:
                remaining,total=counter['remaining'],3
        label='日常已完成' if done else '日常尚未完成'
        if remaining is not None:label+=' · 次数用尽' if remaining==0 else f' · 剩余{remaining}次'
        if opened is False and not done:label+=' · 当前未开放'
        rows[code]=dict(daily_complete=done,remaining=remaining,total=total,opened=opened,
                        label=label,source='daily_panel',row_y=y,row_text=content)
    return rows


def scan(runner, tasks=None, *, force=False):
    s = runner.scheduler
    if getattr(s, 'daily_observation_enabled', False) is not True:
        return {}
    chosen = set(tasks or getattr(runner, 'selected_tasks', ()))
    if 'training_record' in chosen:
        chosen.update(('quiz', 'arena', 'ranking', 'survival', 'escort'))
    chosen &= set(ROW_ASSETS)
    if not chosen:
        return {}
    cache = getattr(runner, '_daily_observations', {})
    if not force and chosen <= cache.keys():
        return {key:cache[key] for key in chosen}
    frame = s.capture()
    from hyol_escort import daily_visible, delivery_layout
    if (delivery_layout(s,frame) is not None
            or s.template('escort_support_title.bmp',hs.VIEW,frame,threshold=.98)[0]
            or any(isinstance(rec,dict) and rec.get('active') for key,rec in getattr(runner,'records',{}).items()
                   if key.startswith('escort'))):
        return {}  # Reconcile the original cart before navigating daily panels.
    if not s.main_ready(frame) and not daily_visible(s, frame):
        return {}  # A battle/unknown modal is never navigated away for inspection.
    if not s.verify_identity(runner.account, frame)[0]:
        raise hs.PendingError('日常检查时账号身份未确认')
    result = {}
    deadline=time.monotonic()+45
    if not daily_visible(s,frame):
        runner.click('daily_practice_toolbar.bmp',(651,69,851,169),20,20,'daily_inspect_open',['anchor_top_menu.bmp'])
        runner.wait('daily_practice_anchor.bmp',(951,119,1501,369))
    try:
        for tab in ('normal','limited'):
            target=chosen & TAB_TASKS[tab]
            if not target:continue
            name='daily_'+tab+'_tab_current.bmp'
            if s.template(name,(510,250,800,310))[0]:
                runner.click(name,(510,250,800,310),35,15,'daily_inspect_'+tab,['daily_practice_anchor.bmp'])
            elif not s.template('daily_'+tab+'_tab_selected.bmp',(510,250,800,310))[0]:
                continue
            # Inspect the page already on screen. Search down first, then up
            # only for still-missing rows; no reset-to-top click sequence.
            for direction in ('down','up'):
                seen=set()
                for index in range(16):
                    s.check_stop()
                    if time.monotonic()>=deadline:break
                    from hyol_identity_overlays import clear_hover_preview
                    clear_hover_preview(s)
                    frame=s.capture()
                    if not s.verify_identity(runner.account,frame)[0]:raise hs.PendingError('日常检查时账号发生变化')
                    position=list_position(s,frame)
                    if not position or position in seen:break
                    seen.add(position)
                    rows=observe_frame(s,frame,target-result.keys())
                    if rows:
                        s.wait_interruptible(.15)
                        fresh=s.capture()
                        second=observe_frame(s,fresh,rows.keys())
                        stable={code:row for code,row in rows.items() if code in second
                                and all(row[k]==second[code][k] for k in ('daily_complete','remaining','opened','row_y'))}
                        if stable:
                            evidence=str(s.save_raw_evidence(fresh,runner.account,'daily_inspect_'+tab+'_'+direction+'_'+str(index),'observed'))
                            for code,row in stable.items():
                                row.update(evidence=evidence,observed_at=hs.now_iso(),execution_day=s.state.day,
                                           account=runner.account.name,run_id=s.diagnostics.identifier)
                                result[code]=row
                    if target <= result.keys() or time.monotonic()>=deadline:break
                    if not scroll_once(runner,direction,frame,'daily_inspect_'+direction):break
                if target <= result.keys() or time.monotonic()>=deadline:break
            if time.monotonic()>=deadline:
                runner.event('daily_inspection_unavailable',detail='日常检查达到45秒上限，已保存读出的项目；其余保留具体未确认状态')
                break
    finally:
        frame=s.capture()
        if daily_visible(s,frame):
            runner.click('daily_practice_close.bmp',(1370,200,1460,280),17,17,'daily_inspect_close',['daily_practice_anchor.bmp'])
            if not s.wait_until_main(8):raise hs.PendingError('日常检查后未确认返回主界面')
    for code in chosen:
        row=result.get(code,dict(daily_complete=None,remaining=None,opened=None,source='daily_panel',
                                 label='日常列表未确认该项目', observed_at=hs.now_iso(),execution_day=s.state.day))
        runner.record(code)['daily_observation']=row
        cache[code]=row
        runner.event('daily_status_observed',task=code,detail=row['label'],observation=row,evidence=row.get('evidence'))
    runner._daily_observations=cache
    runner.save()
    return {key:cache[key] for key in chosen}


def before_tasks(runner):
    try:
        # Escort owns a three-frame counter check immediately before entry.
        # Avoid opening and closing the same daily panel once beforehand.
        selected=tuple(task for task in getattr(runner,'selected_tasks',()) if task!='escort')
        if not selected:return {}
        return scan(runner,selected)
    except hs.PendingError as exc:
        # Preserve the failed observation, but let the original task's own
        # navigation/identity gates decide whether it can execute.
        runner.event('daily_inspection_unavailable',detail='日常检查未完成：'+str(exc))
        return {}


def after_task(runner, task):
    try:
        return scan(runner,(task,),force=True)
    except hs.PendingError as exc:
        runner.event('daily_inspection_unavailable',task=task,detail='任务结果已保存，日常标记复核未完成：'+str(exc))
        if not runner.scheduler.main_ready(runner.scheduler.capture()):
            raise
        return {}
