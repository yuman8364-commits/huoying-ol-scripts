"""User-selected desktop preferences; loading never starts a game task."""
from copy import deepcopy
import json
from pathlib import Path

FILENAME='desktop_settings.json'
ENUMS=dict(section={'daily','team','independent','anniversary'},dailyMode={'manual','training_record'},
    scope={'current','multi'},teamMode={'auto','pipeline'},formation={'keep','main','fast'},
    arenaTargetMode={'tickets','wins'},bondStopMode={'endless','battles','wins'},
    bondTargetMode={'battles','kills','score'},trainingArenaStopPolicy={'auto','final_goal','tickets'})
CURRENT_TEAM_TASKS = ('group_instance','wanted','strong_approach','sealed','rescue_jinchuriki',
                      'team_pvp','bond','arena_3v3')
BOOLEANS={'friendShortcut','pipelineAuto','preformedDuos','newBondRound','finalManaged','escortPriorSettled','arenaSecondSkill'}
BOUNDS=dict(accountCount=(1,6),leader=(1,6),arenaWins=(1,200),bondCount=(1,99),
    bondKillTarget=(0,999),bondScoreTarget=(0,99999),escortRemaining=(0,2))
ONE_SHOT={'newBondRound','escortPriorSettled'}


def _integer(value,low,high,name):
    if type(value) is not int or not low<=value<=high:raise ValueError('设置数值无效：'+name)
    return value


def _slots(value,limit,name,*,nullable=False):
    if not isinstance(value,list) or len(value)>limit:raise ValueError('账号标签列表无效：'+name)
    result=[]
    for slot in value:
        result.append(None if nullable and slot in (None,'') else _integer(slot,1,6,name))
    chosen=[v for v in result if v is not None]
    if len(chosen)!=len(set(chosen)):raise ValueError('账号标签重复：'+name)
    return result


def validate(value,*,restoring=False):
    from hyol_daily_tasks import SOLO_OPTIONS
    from hyol_team_six import TASKS
    from hyol_team_pipeline import SUPPORTED_TEAM_TASKS
    from hyol_runtime import INDEPENDENT_MODES
    from hyol_task_order import defaults,validate as validate_order
    catalogs=dict(tasks=set(SOLO_OPTIONS),teamTasks=set(TASKS),pipelineTasks=set(SUPPORTED_TEAM_TASKS),
                  currentTeamTasks=set(CURRENT_TEAM_TASKS))
    allowed=set(ENUMS)|BOOLEANS|set(BOUNDS)|set(catalogs)|{
        'independent','members','pipelineLeaders','preformedTrio','preformedPairs','bondTeamGoals','taskOrder','currentTaskOrder','groceryItems'}
    if not isinstance(value,dict) or set(value)-allowed:raise ValueError('保存设置包含不支持的字段')
    result=deepcopy(value)
    if 'groceryItems' in result:
        from hyol_grocery import validate_items
        result['groceryItems']=list(validate_items(result['groceryItems']))
    for key,options in ENUMS.items():
        if key in result and (type(result[key]) is not str or result[key] not in options):raise ValueError('设置选项无效：'+key)
    if 'independent' in result and result['independent'] not in INDEPENDENT_MODES:raise ValueError('独立任务设置无效')
    for key in BOOLEANS:
        if key in result and type(result[key]) is not bool:raise ValueError('设置开关须为布尔值：'+key)
    for key,bounds in BOUNDS.items():
        if key in result:_integer(result[key],*bounds,key)
    for key,options in catalogs.items():
        if key in result:
            tasks=result[key]
            if (not isinstance(tasks,list) or any(type(t) is not str or t not in options for t in tasks)
                    or len(tasks)!=len(set(tasks))):raise ValueError('任务选择无效：'+key)
    if 'currentTaskOrder' in result:
        order=result['currentTaskOrder'];allowed_current=set(SOLO_OPTIONS)|set(CURRENT_TEAM_TASKS)
        if (not isinstance(order,list) or any(type(code) is not str or code not in allowed_current for code in order)
                or len(order)!=len(set(order))):raise ValueError('当前账号任务顺序无效')
        result['currentTaskOrder']=[code for code in order if code!='arena_3v3']+(['arena_3v3'] if 'arena_3v3' in order else [])
    for key,limit,nullable in (('members',2,False),('pipelineLeaders',3,True),('preformedTrio',3,False)):
        if key in result:
            result[key]=_slots(result[key],limit,key,nullable=nullable)
            if key=='preformedTrio' and len(result[key])!=3:raise ValueError('3v3须保存三个不同标签')
    if 'preformedPairs' in result:
        pairs=result['preformedPairs']
        if not isinstance(pairs,list) or len(pairs)>3:raise ValueError('二人队设置无效')
        slots=[]
        for pair in pairs:
            got=_slots(pair,2,'preformedPairs')
            if len(got)!=2:raise ValueError('二人队须保存两个不同标签')
            slots.extend(got)
        if len(slots)!=len(set(slots)):raise ValueError('二人队标签不能重叠')
    if 'bondTeamGoals' in result:
        rows=result['bondTeamGoals'];leaders=[]
        if not isinstance(rows,list) or len(rows)>3:raise ValueError('分队羁绊目标无效')
        for row in rows:
            if not isinstance(row,list) or len(row)!=3:raise ValueError('分队羁绊目标格式无效')
            slot,mode,target=row;leaders.append(_integer(slot,1,6,'bondTeamGoals'))
            if mode not in ('battles','kills','score'):raise ValueError('分队羁绊目标类型无效')
            _integer(target,1 if mode=='battles' else 0,{'battles':99,'kills':999,'score':99999}[mode],'bondTeamGoals')
        if len(leaders)!=len(set(leaders)):raise ValueError('分队羁绊目标重复')
    if 'taskOrder' in result:
        if restoring:
            expected=defaults();order=result['taskOrder']
            if not isinstance(order,dict) or set(order)!=set(expected):raise ValueError('保存的任务顺序无效')
            for key,tasks in order.items():
                if not isinstance(tasks,list) or any(type(t) is not str for t in tasks) or len(tasks)!=len(set(tasks)) or set(tasks)-set(expected[key]):
                    raise ValueError('保存的任务顺序含未知任务')
                order[key]=tasks+[t for t in expected[key] if t not in tasks]
                if key=='solo' and 'grocery' not in tasks:
                    order[key].remove('grocery')
                    order[key].insert(order[key].index('welfare'),'grocery')
        result['taskOrder']=validate_order(result['taskOrder'])
    # These are confirmations for a single run, never reusable preferences.
    for key in ONE_SHOT:
        if key in result:result[key]=False
    return result


def load(root):
    path=Path(root)/FILENAME
    if not path.exists():return dict(ok=True,saved=False,config={},warning='')
    try:
        value=json.loads(path.read_text('utf-8-sig'))
        if not isinstance(value,dict) or value.get('version')!=1:raise ValueError('保存设置的版本无效')
        config=validate(value['config'],restoring=True)
        return dict(ok=True,saved=True,config=config,updated_at=value.get('updated_at',''),warning='')
    except (OSError,ValueError,TypeError,KeyError) as exc:
        from hyol_log_messages import error_message
        message,suggestion=error_message(str(exc),'设置文件内容无法读取。')
        return dict(ok=True,saved=False,config={},warning='保存设置读取失败，原文件保留：'+message,
                    suggestion=suggestion,diagnostic=str(exc))


def save(root,value):
    from hyol_scheduler import atomic_json,now_iso
    config=validate(value);stamp=now_iso();path=Path(root)/FILENAME
    if 'taskOrder' in config:
        from hyol_task_order import save as save_order
        save_order(root,config['taskOrder'])
    atomic_json(path,dict(version=1,config=config,updated_at=stamp))
    return dict(ok=True,saved=True,config=config,updated_at=stamp,path=str(path))
