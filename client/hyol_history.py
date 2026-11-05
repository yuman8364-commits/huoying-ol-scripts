"""Separate proven inactive historical intent from current work without resolving it."""
from __future__ import annotations

from copy import deepcopy
from datetime import datetime
import hashlib
import json
from pathlib import Path
import re
import uuid

from hyol_checkpoints import read_checkpoint, source_token, action_versions, action_family, combine_action_versions


def fingerprint(record):
    value = {key:value for key,value in record.items()
             if key not in ('origin', 'continuation_context')}
    return hashlib.sha256(json.dumps(value, sort_keys=True, ensure_ascii=False,
                                    separators=(',', ':')).encode('utf-8')).hexdigest()


def is_isolated(manifest, path, key, record):
    entry = manifest.get(source_token(path, key, record), {})
    if (entry.get('outcome') == 'historical_unknown'
            and entry.get('fingerprint') == fingerprint(record)):
        return True
    # A later aggregate can contain the same original intents and share one
    # source token. Verify every flattened original rather than depending on
    # whichever aggregate last supplied that token's fingerprint.
    pending = [original for original in action_versions(record) if original.get('status') == 'pending']
    if not pending:
        return False
    for original in pending:
        isolated = manifest.get(source_token(path, key, original), {})
        saved = isolated.get('record')
        if (isolated.get('outcome') != 'historical_unknown'
                or not isinstance(saved, dict) or fingerprint(saved) != fingerprint(original)):
            return False
    return True


def archive_bytes(path, directory):
    """Archive exact bytes before modifying any current receipt."""
    path = Path(path)
    if not path.exists():
        return None
    raw = path.read_bytes()
    digest = hashlib.sha256(raw).hexdigest()
    target = Path(directory)/'historical_archive'/(digest+'.json')
    target.parent.mkdir(parents=True, exist_ok=True)
    if target.exists():
        if target.read_bytes() != raw:
            raise ValueError('历史原始记录归档冲突')
    else:
        with target.open('xb') as handle:
            handle.write(raw)
    return dict(path=str(path), archive=str(target), sha256=digest)


def session_started_at(token):
    try:
        pid, hwnd, creation = token.split(':')
        if int(pid) <= 0 or int(hwnd) <= 0:
            return None
        started = int(creation,16)/10_000_000 - 11644473600
        return started if started > 0 else None
    except (AttributeError, ValueError):
        return None


def receipt_day(record, path):
    for value in (record.get('continuation_day'), record.get('source_day'),
                  Path(record.get('origin', {}).get('path', path)).parent.name,
                  str(record.get('updated_at',''))[:10]):
        if isinstance(value,str) and re.fullmatch(r'\d{4}-\d{2}-\d{2}', value):
            return value
    return None


def inactive(record, path, day, started_at):
    """No same-day or post-session intent is eligible, including conflict children."""
    source_day = receipt_day(record,path)
    if not source_day or source_day >= day or started_at is None:
        return False
    value = record.get('updated_at') or record.get('finished_at')
    if isinstance(value,str) and value[:10] >= day:
        return False
    try:
        stamp = datetime.fromisoformat(value).timestamp() if value else float(record['last_progress'])
    except (KeyError, TypeError, ValueError, OverflowError):
        if value:
            return False
        try:
            stamp = Path(path).stat().st_mtime
        except OSError:
            return False
    return 0 < stamp < started_at


def has_current_intent(record,day,started_at):
    for key,value in record.items():
        if isinstance(value,dict) and has_current_intent(value,day,started_at):
            return True
        if isinstance(value,list) and any(has_current_intent(v,day,started_at) for v in value if isinstance(v,dict)):
            return True
        if key in ('updated_at','created_at','started_at','finished_at') and isinstance(value,str) and value[:10] >= day:
            return True
        if key in ('last_progress','started_at','last_progress_at') and isinstance(value,(int,float)) and value >= started_at:
            return True
    return False


def prove_idle_identity(scheduler, account):
    """Read-only three-frame proof; a village-like battle background is insufficient."""
    import hyol_scheduler as hs
    from hyol_accounts import game_session_token
    token = game_session_token(scheduler)
    for sample in range(3):
        scheduler.check_stop()
        frame = scheduler.capture()
        unsafe = any(scheduler.template(asset,roi,frame,threshold=.95)[0] for asset,roi in (
            ('arena_loading_anchor.bmp',hs.ARENA_LOADING_ROI),
            ('arena_result_confirm.bmp',(651,449,1151,829)),
            ('ranking_result_confirm.bmp',(860,640,1060,710)),
            ('ranking_battle_anchor.bmp',(1400,205,1510,251)),
            ('survival_result_confirm.bmp',(701,519,1151,819)),
            ('survival_reward_result_confirm.bmp',(701,569,1151,819)),
            ('treasure_auto_path_running.bmp',(800,140,1100,200)),
            ('map_loading_word_current.bmp',(600,525,1360,635)),
        ))
        if (scheduler.active != account or not scheduler.verify_identity(account,frame)[0]
                or not scheduler.main_ready(frame) or unsafe
                or scheduler._battle_auto_on_visible(frame)
                or hs.Scheduler._combat_active_visible(scheduler, 'survival', frame)):
            raise hs.PendingError('历史隔离前身份、无战斗或无结算页面未连续确认；原记录保留')
        if sample < 2:
            scheduler.wait_interruptible(.35)
    if game_session_token(scheduler) != token:
        raise hs.PendingError('历史隔离期间游戏会话变化，原记录保留')
    evidence = str(scheduler.save_raw_evidence(frame,account,'historical_inactive_proof','verified'))
    return dict(session_token=token, session_started_at=session_started_at(token),
                account=scheduler.state.aliases.get(account.name,account.name),
                observations=3, evidence=evidence, content_clicks=0)


def inactive_retry_budgets(state, account, actions, candidates, manifest, started):
    """Identify budgets with an exact old source and no current family intent."""
    current = state.accounts[account.name]
    eligible = {}
    for label, record in current.get('network_retries', {}).items():
        if (not isinstance(label, str) or not isinstance(record, dict)
                or not isinstance(record.get('source_day'), str)
                or not re.fullmatch(r'\d{4}-\d{2}-\d{2}', record['source_day'])
                or record['source_day'] >= state.day or started is None
                or has_current_intent(record, state.day, started)):
            continue
        attempts = record.get('attempts')
        if isinstance(attempts, bool) or not isinstance(attempts, int) or not 0 <= attempts <= 3:
            continue
        family = re.compile(re.escape(label) + r'(?:_network_\d+)?')
        # A carried completed step may be archived with its old attempt.
        # Any current receipt, including a current success, blocks a reset.
        if any(family.fullmatch(key) and (not isinstance(row, dict)
               or key not in candidates and not (
                   row.get('continuation_context') and inactive(
                       dict(row, source_day=row['continuation_context']),
                       state.account_paths[account.name], state.day, started)))
               for key, row in actions.items()):
            continue
        sources = []
        original_budget = {k:v for k,v in record.items() if k != 'source_day'}
        for path in sorted(state.directory.glob('????-??-??/' + state.account_paths[account.name].name)):
            if path.parent.name >= state.day:
                continue
            source = read_checkpoint(path)
            budget = source.get('network_retries', {}).get(label)
            if (not isinstance(budget, dict)
                    or {k:v for k,v in budget.items() if k != 'source_day'} != original_budget
                    or not inactive(dict(budget, source_day=path.parent.name,
                                         updated_at=source.get('updated_at')), path, state.day, started)):
                continue
            rows = {key:row for key,row in source.get('actions', {}).items() if family.fullmatch(key)}
            if not rows:
                continue
            safe = True
            for key, row in rows.items():
                if not isinstance(row, dict) or not all(
                        inactive(v, path, state.day, started) for v in action_versions(row)):
                    safe = False
                    break
                if row.get('status') == 'pending' and not (
                        is_isolated(manifest, path, key, row)
                        or all(any(fingerprint(v) == fingerprint(original)
                                   for original, _ in candidates.get(key, ()))
                               for v in action_versions(row))):
                    safe = False
                    break
            if safe:
                sources.append((path, budget))
        if sources:
            eligible[label] = (record, sources)
    return eligible


def isolate_inactive_work(scheduler, account, *, records=None, task_path=None, proof=None):
    """Keep unknown records pending in an archive; only exact old sources are excluded."""
    import hyol_scheduler as hs
    if scheduler.config.get('HistoricalPendingPolicy') != 'isolate_inactive':
        return 0
    state = scheduler.state
    current = state.accounts[account.name]
    directory = state.directory
    manifest_path = directory/'historical_isolations.json'
    manifest = read_checkpoint(manifest_path)
    token = getattr(scheduler,'session_token',None)
    if not isinstance(token,str):
        from hyol_accounts import game_session_token
        token = game_session_token(scheduler)
    started = session_started_at(token)
    actions = current.setdefault('actions',{})
    candidates = {}
    retained = {}
    for key,row in actions.items():
        if not isinstance(row,dict) or row.get('status') != 'pending':
            continue
        versions = action_versions(row)
        fallback = row.get('origin',{}).get('path',state.account_paths[account.name])
        versions = [(v,Path(v.get('origin',{}).get('path',fallback))) for v in versions]
        eligible = [(v,path) for v,path in versions if v.get('status') == 'pending'
                    and inactive(v,path,state.day,started)]
        if eligible:
            remaining = [v for v,path in versions if not (
                v.get('status') == 'pending' and inactive(v,path,state.day,started))]
            # An unidentifiable conflict child may be the same original intent.
            # Only a durable operation id proves a distinct current attempt.
            if any(v.get('status') == 'pending' and not v.get('operation_id') for v in remaining):
                continue
            candidates[key] = eligible
            retained[key] = remaining
    remaining_pending = {key: versions for key,row in actions.items()
        if isinstance(row,dict) and row.get('status') == 'pending'
        for versions in ([retained[key]] if key in candidates else [action_versions(row)])
        if any(v.get('status') == 'pending' for v in versions)}
    old_contexts = {key: row for key,row in actions.items()
        if started is not None and isinstance(row,dict) and row.get('status') != 'pending'
        and row.get('continuation_context') and not has_current_intent(row,state.day,started)
        and inactive(dict(row,source_day=row['continuation_context']),
                     state.account_paths[account.name],state.day,started)
        and not any(pending_key.startswith(action_family(key)) and any(
            v.get('status') == 'pending' and (not v.get('operation_id') or v.get('origin'))
            for v in versions) for pending_key,versions in remaining_pending.items())}
    old_tasks = {}
    for key,row in (records or {}).items():
        if (key.startswith('_') or not isinstance(row,dict) or started is None
                or row.get('status') not in ('pending','running') or has_current_intent(row,state.day,started)):
            continue
        prefixes = {'mail':('mail_','inventory_'),'arena':('arena_','daily_arena_'),
                    'ranking':('ranking_','daily_ranking_'),'survival':('survival_','daily_survival_'),
                    'weekend':('puzzle_','daruma_','daily_weekend_')}.get(key,(key+'_','daily_'+key+'_'))
        if any(action.startswith(prefixes) and (action not in candidates or any(
                   version.get('status') == 'pending' for version in retained.get(action, ())))
               and isinstance(value,dict) and value.get('status') == 'pending'
               for action,value in actions.items()):
            continue
        source_day = row.get('continuation_day') or row.get('source_day')
        sources = []
        if isinstance(source_day,str) and re.fullmatch(r'\d{4}-\d{2}-\d{2}',source_day):
            for suffix in ('_tasks.json','_multi_solo.json','_daily_client.json','_team_client.json'):
                path = directory/source_day/(state.account_paths[account.name].stem+suffix)
                source = read_checkpoint(path).get(key)
                if isinstance(source,dict):
                    sources.append((path,source))
        if sources and all(inactive(version,path,state.day,started)
                           for path,source in sources for version in action_versions(source)):
            old_tasks[key] = (row,sources)
    budget_candidates = {key: versions for key,versions in candidates.items() if not retained[key]}
    retry_budgets = inactive_retry_budgets(state, account, actions, budget_candidates, manifest, started)
    if not candidates and not old_tasks and not retry_budgets and not old_contexts:
        return 0
    proof = proof or prove_idle_identity(scheduler,account)
    if (proof.get('session_token') != token or proof.get('observations') != 3
            or proof.get('account') != state.aliases.get(account.name,account.name)):
        raise hs.PendingError('历史隔离凭据与当前账号或会话不一致')
    archives = []
    for path in (state.account_paths[account.name],task_path):
        if path is not None:
            receipt = archive_bytes(path,directory)
            if receipt: archives.append(receipt)
    history = current.setdefault('historical_unresolved',{})
    count = 0
    for key,versions in candidates.items():
        for version,path in versions:
            source = read_checkpoint(path).get('actions',{}).get(key,version)
            receipt = archive_bytes(path,directory)
            if receipt: archives.append(receipt)
            identifier = source_token(path,key,version)
            entry = dict(outcome='historical_unknown',fingerprint=fingerprint(version),
                         account=proof['account'],action=key,record=deepcopy(version),
                         proof=proof,archives=deepcopy(archives),isolated_at=hs.now_iso())
            manifest[identifier] = entry
            history[identifier] = entry
        remaining = combine_action_versions(retained[key])
        if remaining is None:
            del actions[key]
        else:
            actions[key] = remaining
        count += 1
    for key,(row,sources) in old_tasks.items():
        for path,source in sources:
            receipt = archive_bytes(path,directory)
            if receipt: archives.append(receipt)
            identifier = source_token(path,'task:'+key,source)
            entry = dict(outcome='historical_unknown',fingerprint=fingerprint(source),
                         account=proof['account'],task=key,record=deepcopy(row),
                         proof=proof,archives=deepcopy(archives),isolated_at=hs.now_iso())
            manifest[identifier] = entry
            records.setdefault('_historical_unresolved',{})[identifier] = entry
        del records[key]
        count += 1
    # A carried completed step belongs to the old attempt, too. Reusing it
    # after isolating that attempt would give a fresh battle false credit.
    for key,row in old_contexts.items():
        identifier = source_token(state.account_paths[account.name],key,row)
        current.setdefault('historical_contexts',{})[identifier] = dict(
            record=deepcopy(row),proof=proof,archives=deepcopy(archives))
        del actions[key]
    count = max(count, int(bool(old_contexts)))
    for label, (record, sources) in retry_budgets.items():
        for path, original in sources:
            receipt = archive_bytes(path, directory)
            if receipt:
                archives.append(receipt)
            identifier = source_token(path, 'retry:' + label, original)
            entry = dict(outcome='historical_retry_budget', fingerprint=fingerprint(original),
                         account=proof['account'], target=label, record=deepcopy(record),
                         original=deepcopy(original), proof=proof,
                         archives=deepcopy(archives), isolated_at=hs.now_iso())
            manifest[identifier] = entry
            current.setdefault('historical_network_retries', {})[identifier] = entry
        del current['network_retries'][label]
        count += 1
    # Persist the manifest first. A crash leaves current pending work intact;
    # it never turns an unknown result into a successful resolution.
    hs.atomic_json(manifest_path,manifest)
    current.setdefault('continuation_scope',state.day+'_'+uuid.uuid4().hex)
    request = state.global_state.get('arena_requests',{}).get(account.name)
    if (isinstance(request,dict) and not any(key.startswith(('arena_','daily_arena_'))
            and row.get('status') == 'pending' for key,row in actions.items())
            and inactive(dict(request,updated_at=request.get('created_at')),
                         directory/(str(request.get('created_at',''))[:10])/'global.json',state.day,started)):
        current.setdefault('historical_global_requests',[]).append(dict(
            task='arena',record=deepcopy(request),outcome='historical_unknown',proof=proof))
        del state.global_state['arena_requests'][account.name]
        state.save_global()
    state.save_account(account.name)
    if old_tasks and task_path:
        hs.atomic_json(task_path,records)
    scheduler.event('historical_work_isolated',account,count=count,contexts=len(old_contexts),proof=proof,
                    detail='历史未知结果逐字节保留；当前任务使用独立操作凭据')
    return count


def isolate_local_anonymous(scheduler):
    """Archive dead-session anonymous intent without assigning it to a current role."""
    import hyol_scheduler as hs
    from hyol_accounts import game_session_token,session_is_alive
    local = hs.STATE_DIR.resolve()
    shared = scheduler.shared_state_directory.resolve()
    if getattr(scheduler,'config',{}).get('HistoricalPendingPolicy') != 'isolate_inactive' or local == shared:
        return 0
    manifest_path = shared/'historical_isolations.json'
    manifest = read_checkpoint(manifest_path)
    token = game_session_token(scheduler)
    started = session_started_at(token)
    candidates = []
    for path in local.rglob('*.json'):
        record = read_checkpoint(path)
        if not str(record.get('account','')).startswith('当前窗口标签'):
            continue
        previous = next((re.fullmatch(r'window_(\d+)_(\d+)_([0-9a-f]+)',part)
                         for part in path.parts if part.startswith('window_')),None)
        if previous is None:
            continue
        previous_token = ':'.join(previous.groups())
        if (session_started_at(previous_token) is None or session_is_alive(previous_token)
                or started is None or session_started_at(previous_token) >= started):
            continue
        for key,row in record.get('actions',{}).items():
            if (isinstance(row,dict) and row.get('status') == 'pending'
                    and not is_isolated(manifest,path,key,row)
                    and all(inactive(version,path,scheduler.state.day,started)
                            for version in action_versions(row))):
                candidates.append((path,key,row,previous_token))
    if not candidates:
        return 0
    proof = prove_idle_identity(scheduler,scheduler.active)
    for path,key,row,previous in candidates:
        identifier = source_token(path,key,row)
        manifest[identifier] = dict(outcome='historical_unknown',fingerprint=fingerprint(row),
            account=None,action=key,record=deepcopy(row),source_session=previous,
            proof=proof,archives=[archive_bytes(path,shared)],isolated_at=hs.now_iso())
    hs.atomic_json(manifest_path,manifest)
    scheduler.event('historical_anonymous_work_isolated',count=len(candidates),proof=proof,
                    detail='旧会话匿名未知记录原样归档，未指定为任何当前账号的完成结果')
    return len(candidates)
