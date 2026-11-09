"""Resume the exact native preformed-team request, including its original actions."""
from dataclasses import asdict, is_dataclass
import json
from pathlib import Path
import re


def request_key(request):
    return json.loads(json.dumps(asdict(request)))


def cursor_path(directory,request):
    # A standalone limited-event request must not replace an unfinished daily
    # team pipeline. Its own cursor still blocks conflicting 3v3 requests.
    trio=getattr(request,'preformed_trio',())
    name=('pipeline_arena3v3_'+'_'.join(map(str,sorted(trio)))+'_active_run.json' if trio
          and tuple(request.team_tasks)==('arena_3v3',) else 'pipeline_active_run.json')
    return Path(directory)/name


def read_candidate(directory, request, *, execution_day=None):
    import hyol_scheduler as hs
    path = cursor_path(directory,request)
    if not path.exists() and getattr(request,'preformed_trio',()) and tuple(request.team_tasks)==('arena_3v3',):
        legacy=Path(directory)/'pipeline_arena3v3_active_run.json'
        if legacy.exists():
            try:old=json.loads(legacy.read_text('utf-8-sig'))
            except (OSError,ValueError) as exc:raise hs.PendingError('原3v3入口无法读取，未开始另一队') from exc
            original_trio=old.get('request_key',{}).get('preformed_trio',())
            if (not isinstance(original_trio,list) or len(original_trio)!=3
                    or any(type(v) is not int or not 1<=v<=6 for v in original_trio)):
                raise hs.PendingError('原3v3队伍标签未确认，未开始另一队')
            if set(original_trio)&set(request.preformed_trio):path=legacy
    try:
        value = json.loads(path.read_text('utf-8-sig'))
    except FileNotFoundError:
        return None
    except (OSError, ValueError) as exc:
        raise hs.PendingError('已有队伍原轮次入口无法读取，未重新开始') from exc
    if not isinstance(value,dict) or type(value.get('resumable')) is not bool:
        raise hs.PendingError('已有队伍原轮次入口格式未知，保留原文件')
    if not value['resumable']:
        return None
    if (not re.fullmatch(r'\d{8}_\d{6}_\d{6}_[0-9a-f]{8}',str(value.get('run_id','')))
            or not isinstance(value.get('session_token'),str) or not value['session_token']):
        raise hs.PendingError('已有队伍原运行或会话凭据无效')
    if (getattr(request,'preformed_trio',()) and tuple(request.team_tasks)==('arena_3v3',)
            and isinstance(execution_day,str) and isinstance(value.get('execution_day'),str)
            and value['execution_day']<execution_day):
        folder=Path(directory)/'runs'/value['run_id']
        if not (folder/'team_pipeline_latest.json').is_file() or not (folder/'training_binding.json').is_file():
            raise hs.PendingError('历史3v3任务或身份凭据缺失，未开始当前任务')
        # A date alone never authorizes another battle. The driver must prove
        # disjoint live identities or a visible server-side goal reset before
        # ANY claim/match. The old cursor and pending actions remain untouched.
        return dict(value,observe_new_scope=True,cursor_path=str(path),
                    historical_outcome='unknown',counted_as_completed=False)
    selected = request_key(request)
    original = value.get('request_key')
    if isinstance(original,dict):
        original={**original,'arena_second_skill':original.get('arena_second_skill',False),
                  'grocery_items':original.get('grocery_items',[])}
    if isinstance(original,dict) and 'preformed_trio' not in original:
        original={**original,'preformed_trio':[]}
    if original != selected:
        # Deselecting later solo work must not force another team round. Only
        # an ordered subset is allowed; identities, combat and budgets stay put.
        if (not isinstance(original, dict)
                or {k:v for k,v in original.items() if k != 'tasks'}
                != {k:v for k,v in selected.items() if k != 'tasks'}
                or not isinstance(original.get('tasks'), list)
                or selected['tasks'] != [t for t in original['tasks'] if t in selected['tasks']]):
            raise hs.PendingError('已有队伍原轮次未结束，请保持原任务、账号与阵容选择后继续')
        value = dict(value, deferred_tasks=[t for t in original['tasks'] if t not in selected['tasks']])
    folder = Path(directory)/'runs'/value['run_id']
    if not (folder/'team_pipeline_latest.json').is_file() or not (folder/'training_binding.json').is_file():
        raise hs.PendingError('已有队伍原任务或身份凭据缺失，未创建新轮次')
    return value


def save_cursor(context, request, *, status):
    import hyol_scheduler as hs
    from hyol_training_resume import persist_binding
    record = context.directory/'team_pipeline_latest.json'
    if isinstance(context.binding,dict):
        context.binding['training_prepared_accounts'] = list(context.binding['mapping'].values())
        persist_binding(context)
    original = getattr(context, 'pipeline_original_request_key', None)
    value = dict(version=1,run_id=context.identifier,execution_day=context.execution_day,
                 session_token=context.session_token,request_key=original if isinstance(original,dict) else request_key(request),status=status,
                 resumable=record.is_file() and status!='complete',updated_at=hs.now_iso())
    value['selected_tasks'] = list(request.tasks)
    hs.atomic_json(cursor_path(context.identity_directory,request),value)
    return value


def checkpoint_started(context):
    request = getattr(context,'pipeline_request',None)
    if is_dataclass(request) and getattr(request,'mode',None)=='team_then_multi':
        save_cursor(context,request,status='running')
