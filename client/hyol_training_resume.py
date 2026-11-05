"""Persist the training run selected by Start, independently of client installs."""
from pathlib import Path
import json
import re

from hyol_training_policy import local_day


def is_training(request):
    return request.mode == 'training_record' or (request.mode in ('solo','multi')
                                                   and tuple(request.tasks) == ('training_record',))


def request_key(request):
    return dict(mode='multi' if request.mode == 'multi' else 'solo',
                account_count=request.account_count if request.mode == 'multi' else 1,
                formation=request.formation)


def read_candidate(directory, request):
    import hyol_scheduler as hs
    path=Path(directory)/'training_active_run.json'
    try:data=json.loads(path.read_text(encoding='utf-8-sig'))
    except FileNotFoundError:return None
    except (OSError,ValueError) as exc:raise hs.PendingError('修行录原轮次入口无法读取，保留原文件') from exc
    if not isinstance(data,dict) or type(data.get('resumable')) is not bool:
        raise hs.PendingError('修行录原轮次入口格式未知，未创建新预算')
    if not data['resumable']:return None
    identifier=data.get('run_id','')
    if (not isinstance(identifier,str) or not re.fullmatch(r'\d{8}_\d{6}_\d{6}_[0-9a-f]{8}',identifier)
            or not isinstance(data.get('execution_day'),str) or not isinstance(data.get('session_token'),str)):
        raise hs.PendingError('修行录原运行或会话凭据无效，未创建新预算')
    from datetime import date
    try:date.fromisoformat(data['execution_day'])
    except ValueError as exc:raise hs.PendingError('修行录原执行日期无效') from exc
    if data.get('request_key') != request_key(request):
        raise hs.PendingError('原修行录轮次仍有未决步骤；保持原账号范围和阵容设置后继续，未覆盖原预算')
    run=Path(directory)/'runs'/identifier
    if not run.is_dir():raise hs.PendingError('修行录原运行目录缺失，未重建任务')
    return data


def has_unfinished(context):
    day=context.execution_day or local_day()
    folder=context.directory/day
    for path in folder.glob('*.json'):
        try:data=json.loads(path.read_text(encoding='utf-8-sig'))
        except (OSError,ValueError):return True
        if any(isinstance(row,dict) and row.get('status') == 'pending' for row in data.get('actions',{}).values()):
            return True
        parent=data.get('training_record',{})
        if isinstance(parent,dict):
            if parent.get('active_job'):return True
            if any(row.get('status') != 'done' for row in parent.get('claims',{}).values()):return True
            if parent and parent.get('rewards_complete') is not True and not parent.get('all_goals_complete'):
                return True
    for name in ('identity_overlay_cleanup.json','identity_overlay_discovery.json'):
        path=context.directory/name
        if path.exists():
            try:data=json.loads(path.read_text(encoding='utf-8-sig'))
            except (OSError,ValueError):return True
            if any(isinstance(row,dict) and row.get('status') == 'pending' for row in data.values()):return True
    return False


def save_cursor(context, request, *, status, resumable=None):
    import hyol_scheduler as hs
    if resumable is None:resumable=has_unfinished(context)
    data=dict(version=1,run_id=context.identifier,execution_day=context.execution_day or local_day(),
              session_token=context.session_token,request_key=request_key(request),status=status,
              resumable=bool(resumable),updated_at=hs.now_iso())
    if isinstance(getattr(context,'binding',None),dict):persist_binding(context)
    hs.atomic_json(context.identity_directory/'training_active_run.json',data)
    return data


def persist_binding(context):
    """Retain the actual original role observations for hidden combat pages."""
    import hyol_scheduler as hs
    from hyol_checkpoints import encode_mask
    row=context.binding
    path=context.directory/'training_binding.json'
    if path.exists() or not row.get('training_prepared_accounts'):return
    directory=context.directory/'training_binding';directory.mkdir(exist_ok=True)
    frames={}
    for slot,frame in row['frames'].items():
        target=directory/f'slot_{int(slot)}.png'
        frame.save(target)
        frames[str(slot)]=str(target.relative_to(context.directory))
    value=dict(version=1,run_id=context.identifier,execution_day=context.execution_day,session_token=context.session_token,
               centers=list(row['centers']),mapping=row['mapping'],refs=row['refs'],frames=frames,
               observations={str(slot):[encode_mask(mask) for mask in masks] for slot,masks in row['observations'].items()},
               unavailable=row.get('unavailable',{}),prepared_accounts=row['training_prepared_accounts'])
    hs.atomic_json(path,value)


def restore_binding(context, candidate):
    """Load only original same-session proofs; fresh chrome is checked separately."""
    import hyol_scheduler as hs
    from hyol_checkpoints import decode_mask, current_identity_mask, joint_identity_assignment
    from PIL import Image
    path=context.directory/'training_binding.json'
    if not path.exists():return False
    try:data=json.loads(path.read_text(encoding='utf-8-sig'))
    except (OSError,ValueError) as exc:raise hs.PendingError('修行录原身份绑定凭据无法读取') from exc
    if (data.get('run_id') != context.identifier or data.get('session_token') != candidate['session_token']
            or data.get('execution_day') != candidate['execution_day']):
        raise hs.PendingError('修行录原身份绑定与运行、日期或会话不一致')
    centers=data['centers'];mapping={int(k):v for k,v in data['mapping'].items()};refs=data['refs']
    if (not 1<=len(centers)<=6 or len(set(centers)) != len(centers)
            or any(type(x) is not int or not 50<=x<=1800 for x in centers)
            or not mapping or any(slot<1 or slot>len(centers) or owner not in refs for slot,owner in mapping.items())):
        raise hs.PendingError('修行录原标签或账号映射无效')
    observations={};frames={};store=hs.TemplateStore(.98)
    for slot,owner in mapping.items():
        values=data['observations'].get(str(slot),[])
        if len(values)!=3:raise hs.PendingError('修行录原身份缺少三帧观察')
        shape=tuple(refs[owner]['shape'])
        observations[slot]=[decode_mask(value,shape) for value in values]
        frame_path=(context.directory/data['frames'][str(slot)]).resolve()
        if not frame_path.is_relative_to(context.directory.resolve()):
            raise hs.PendingError('修行录原截图路径越出运行目录')
        with Image.open(frame_path) as image:frames[slot]=image.convert('RGB')
        if hs.mask_f1(current_identity_mask(frames[slot],store),observations[slot][-1])<.92:
            raise hs.PendingError('修行录原身份截图与三帧凭据不一致')
    for sample in range(3):
        if joint_identity_assignment({slot:values[sample] for slot,values in observations.items()},refs)[0] != mapping:
            raise hs.PendingError('修行录原三帧无法重现唯一账号映射')
    prepared=set(data.get('prepared_accounts',[]))
    if not prepared or not prepared<=set(mapping.values()):raise hs.PendingError('修行录原开场准备范围无效')
    context.remember(centers,mapping,observations,frames,refs,{int(k):v for k,v in data.get('unavailable',{}).items()})
    context.binding.update(resumed_original_binding=True,training_prepared_accounts=list(prepared))
    return True


def verify_resume_window(root, context, event_sink, on_scheduler):
    import hyol_scheduler as hs
    from hyol_preparation import handoff_start_window
    config=hs.read_config(Path(root)/'DAILY_Client.ini')
    config.update(AccountMode='dynamic',AccountDiscovery='automatic',AllowMarketPurchase='0',AutoForeground='0')
    scheduler=hs.Scheduler(config,'readonly_preflight',False,True,event_sink=event_sink)
    on_scheduler(scheduler)
    # This is the same explicit Start handoff, followed by observation only.
    handoff_start_window(scheduler)
    scheduler.bind_window(wait=True,timeout_seconds=10)
    for sample in range(3):
        frame=scheduler.capture_account_observation()
        context.check_window(scheduler)
        if not context.layout_matches(frame):raise hs.PendingError('修行录原浏览器标签布局变化，未恢复原场')
        if sample<2:scheduler.wait_interruptible(.2)
    scheduler.event('training_original_binding_revalidated',observations=3,content_clicks=0,
                    detail='同一游戏会话、原三帧账号与当前标签布局核实，沿原任务恢复')
    return dict(status='ready',healthy=True,content_clicks=0,training_resumed=True,main_ready=scheduler.main_ready(frame))


def pending_prior_sources(runner):
    """Same-day input intents from a different run cannot become fresh clicks."""
    import hyol_scheduler as hs
    from hyol_checkpoints import decode_mask,joint_identity_assignment
    scheduler=runner.scheduler;context=getattr(scheduler,'run_context',None)
    if context is None:return []
    materials=getattr(scheduler,'_dynamic_identity_materials',{})
    current=materials.get(runner.account.name)
    if not isinstance(current,dict) or not current.get('mask'):return []
    shape=tuple(current['shape']);mask=decode_mask(current['mask'],shape)
    day=scheduler.state.day;found=[]
    for registry in context.identity_directory.glob('runs/*/account_identities.json'):
        if registry.parent.resolve() == context.directory.resolve() or not (registry.parent/day).is_dir():continue
        try:refs=json.loads(registry.read_text(encoding='utf-8-sig'))
        except (OSError,ValueError):continue
        scores=[]
        for owner,row in refs.items():
            if tuple(row.get('shape',())) == shape:
                scores.append((hs.mask_f1(mask,decode_mask(row['mask'],shape)),owner))
        scores.sort(reverse=True)
        binding=getattr(context,'binding',None)
        assignment=joint_identity_assignment(binding['masks'],refs)[0] if isinstance(binding,dict) else None
        owner=assignment.get(runner.account.index) if assignment else None
        if owner is None:
            if not scores or scores[0][0]<.92 or (len(scores)>1 and scores[0][0]-scores[1][0]<.08):continue
            owner=scores[0][1]
        identity_score=hs.mask_f1(mask,decode_mask(refs[owner]['mask'],shape))
        path=registry.parent/day/(hs.slug(owner)+'.json')
        if not path.is_file():continue
        try:record=json.loads(path.read_text(encoding='utf-8-sig'))
        except (OSError,ValueError):continue
        for action,receipt in record.get('actions',{}).items():
            if not isinstance(receipt,dict) or receipt.get('status') != 'pending':continue
            task=next((task for task in ('arena','ranking','survival','quiz','treasure','escort','mail','welfare','puzzle','daruma')
                       if action.startswith((task+'_','daily_'+task+'_'))),None)
            if task:found.append(dict(task=task,action=action,source=str(path),run_id=registry.parent.name,
                                      identity_score=identity_score,replayed=False))
    return found
