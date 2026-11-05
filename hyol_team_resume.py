"""Resume the exact native preformed-team request, including its original actions."""
from dataclasses import asdict, is_dataclass
import json
from pathlib import Path
import re


def request_key(request):
    return json.loads(json.dumps(asdict(request)))


def read_candidate(directory, request):
    import hyol_scheduler as hs
    path = Path(directory)/'pipeline_active_run.json'
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
    selected = request_key(request)
    original = value.get('request_key')
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
    hs.atomic_json(context.identity_directory/'pipeline_active_run.json',value)
    return value


def checkpoint_started(context):
    request = getattr(context,'pipeline_request',None)
    if is_dataclass(request) and getattr(request,'mode',None)=='team_then_multi':
        save_cursor(context,request,status='running')
