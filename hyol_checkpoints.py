"""Shared task ledgers and durable, evidence-based cross-day continuations."""
from __future__ import annotations

from copy import deepcopy
import hashlib
import json
from pathlib import Path
import re


class CheckpointError(ValueError):
    pass


def read_checkpoint(path):
    path = Path(path)
    try:
        value = json.loads(path.read_text(encoding='utf-8'))
    except FileNotFoundError:
        return {}
    except (OSError, ValueError) as exc:
        raise CheckpointError(f'断点无法读取，保留原文件：{path}') from exc
    if not isinstance(value, dict):
        raise CheckpointError(f'断点格式异常，保留原文件：{path}')
    return value


def complete(record):
    return (isinstance(record, dict) and record.get('status') == 'done'
            and record.get('task_completed') is True and record.get('home_returned') is True)


def merge_records(records):
    """Unverified disagreement survives migration; a legacy 'done' alone is insufficient."""
    merged = {}
    for source, ledger in records:
        for task, incoming in ledger.items():
            if not isinstance(incoming, dict) or task.startswith('_'):
                continue
            value = deepcopy(incoming)
            if value.get('status') == 'done' and not complete(value):
                value.update(status='pending', last_error='旧完成记录缺少结果和回村证据')
            old = merged.get(task)
            if old is None or old.get('status', 'new') == 'new':
                merged[task] = value
            elif value.get('status', 'new') != 'new' and old != value:
                # Never move a persisted treasure deadline forward during migration.
                deadlines = [row for row in (old, value) if row.get('deadline_at') is not None]
                chosen = min(deadlines, key=lambda row: row['deadline_at']) if deadlines else old
                chosen = deepcopy(chosen)
                if not (complete(old) and complete(value)):
                    chosen.update(status='pending', task_completed=False,
                                  last_error='入口断点存在不同记录，需要原流程对账')
                    chosen['checkpoint_conflict'] = [old, value]
                merged[task] = chosen
            merged[task].setdefault('checkpoint_sources', [])
            if str(source) not in merged[task]['checkpoint_sources']:
                merged[task]['checkpoint_sources'].append(str(source))
    return merged


def load_tasks(state, name):
    if not getattr(state, 'restore_history', True):
        account_path = state.account_paths[name]
        path = account_path.with_name(account_path.stem + '_tasks.json')
        return path, read_checkpoint(path)
    from hyol_history import is_isolated
    isolated = read_checkpoint(state.directory / 'historical_isolations.json')
    account_path = state.account_paths[name]
    path = account_path.with_name(account_path.stem + '_tasks.json')
    if path.exists():
        return path, read_checkpoint(path)
    suffixes = ('_multi_solo.json', '_daily_client.json', '_team_client.json')
    current = [(account_path.with_name(account_path.stem + suffix),
                read_checkpoint(account_path.with_name(account_path.stem + suffix))) for suffix in suffixes]
    records = merge_records(current)
    # Read the newest dated ledger per task, including completed records that
    # supersede older pending snapshots. Daily completed counters are not copied.
    seen = {key for key, value in records.items() if value.get('status', 'new') != 'new'}
    for directory in sorted(state.directory.glob('????-??-??'), reverse=True):
        if not directory.is_dir() or directory.name >= state.day:
            continue
        sources = [directory / (account_path.stem + suffix)
                   for suffix in ('_tasks.json', *suffixes)]
        sources = [source for source in sources if source.exists()]
        if not sources:
            continue
        # Parse every historical entry at most once. The old code re-opened
        # all sources for each task when checking isolated action provenance.
        canonical = sources[0] if sources[0].name.endswith('_tasks.json') else None
        # A canonical task journal supersedes alternate entrances; never open
        # those secondary ledgers (which could be stale or partially written).
        selected_sources = [canonical] if canonical else sources
        snapshots = [(source, read_checkpoint(source)) for source in selected_sources]
        previous = snapshots[0][1] if canonical else merge_records(snapshots)
        for task, rec in previous.items():
            if task in seen or not isinstance(rec, dict) or task.startswith('_'):
                continue
            seen.add(task)
            task_sources = snapshots[:1] if canonical else [
                (source, ledger) for source, ledger in snapshots if task in ledger]
            if task_sources and all(is_isolated(isolated, source, 'task:'+task, ledger[task])
                                    for source, ledger in task_sources):
                continue
            if complete(rec) or rec.get('status', 'new') == 'new':
                continue
            records[task] = deepcopy(rec)
            records[task].update(status='pending', continuation_day=directory.name,
                                 task_completed=False)
    return path, records


def action_family(action):
    network = re.fullmatch(r'(.+)_network_\d+', action)
    if network:
        action = network.group(1)
    for prefixes in (('arena_', 'daily_arena_'), ('ranking_', 'daily_ranking_'),
                     ('survival_', 'daily_survival_'), ('treasure_', 'daily_treasure_'),
                     ('sealed_',), ('daruma_', 'daily_weekend_', 'puzzle_'),
                     ('inventory_', 'mail_capacity_')):
        if action.startswith(prefixes):
            return prefixes
    return (action,)


def guard_legacy_local_unresolved(scheduler):
    """Changing the shared ledger must never abandon unidentifiable local intent."""
    if getattr(scheduler, 'run_context', None):
        return
    import hyol_scheduler as hs
    shared = Path(getattr(scheduler, 'shared_state_directory', hs.STATE_DIR)).resolve()
    local = hs.STATE_DIR.resolve()
    if shared == local or not local.exists():
        return
    resolved = read_checkpoint(shared / 'checkpoint_resolutions.json')
    from hyol_history import is_isolated,isolate_local_anonymous
    isolate_local_anonymous(scheduler)
    isolated = read_checkpoint(shared / 'historical_isolations.json')
    rows = []
    for path in local.rglob('*.json'):
        data = read_checkpoint(path)
        if not str(data.get('account', '')).startswith('当前窗口标签'):
            continue
        pending = [key for key, value in data.get('actions', {}).items()
                   if isinstance(value, dict) and value.get('status') == 'pending'
                   and source_token(path, key, value) not in resolved
                   and not is_isolated(isolated,path,key,value)]
        if pending:
            rows.append({'source':str(path), 'account':data['account'], 'actions':pending})
    if rows:
        report = hs.LOGS / 'legacy_checkpoint_unlinked.json'
        hs.atomic_json(report, {'status':'pending', 'sources':rows,
                               'detail':'原本地匿名未决动作保留，须核实角色和原结果后才能关联共享账本'})
        raise hs.PendingError('旧入口有未关联角色的匿名断点，未重启任务；先对账原结果：'+str(report))


def source_token(path, action, record):
    if record.get('origin', {}).get('token'):
        return record['origin']['token']
    value = str(Path(path).resolve()) + '|' + action + '|' + str(record.get('updated_at', ''))
    return hashlib.sha256(value.encode('utf-8')).hexdigest()


def action_versions(record):
    """Flatten old conflict trees without dropping distinct original intents."""
    pending = [record]
    versions, seen = [], set()
    while pending:
        value = pending.pop()
        if not isinstance(value, dict):
            raise CheckpointError('动作冲突记录格式异常，保留原文件')
        version = {key: deepcopy(item) for key, item in value.items()
                   if key != 'checkpoint_conflict'}
        token = version.get('origin', {}).get('token')
        identity = ('origin', token) if token else ('legacy', json.dumps(
            version, sort_keys=True, ensure_ascii=False, separators=(',', ':')))
        if identity not in seen:
            seen.add(identity)
            versions.append(version)
        conflict = value.get('checkpoint_conflict')
        if conflict is not None:
            children = conflict if isinstance(conflict, list) else [conflict]
            pending.extend(reversed(children))
    return dedupe_action_context(versions)


def dedupe_action_context(versions):
    """A historical context copy is the same intent as its proven source."""
    clean = lambda row: {key:value for key,value in row.items()
                         if key not in ('origin','continuation_context')}
    return [row for row in versions if not (
        not row.get('origin') and row.get('continuation_context')
        and any(other.get('origin',{}).get('path')
                and Path(other['origin']['path']).parent.name == row['continuation_context']
                and clean(other) == clean(row) for other in versions))]


def combine_action_versions(versions):
    """Keep remaining intents distinct after exact historical versions are archived."""
    if not versions:
        return None
    versions = dedupe_action_context(deepcopy(versions))
    pending = [i for i, row in enumerate(versions) if row.get('status') == 'pending']
    primary = pending[0] if pending else max(
        range(len(versions)), key=lambda i: str(versions[i].get('updated_at', '')))
    result = versions.pop(primary)
    if versions:
        result['checkpoint_conflict'] = versions
    return result


def merge_pending_action(present, carried):
    """Repeated recovery is idempotent; a conflict never becomes a new budget."""
    primary = present if present.get('status') == 'pending' else carried
    secondary = carried if primary is present else present
    versions = action_versions(primary)
    if secondary:
        versions.extend(action_versions(secondary))
    merged, seen = [], set()
    for value in versions:
        token = value.get('origin', {}).get('token')
        identity = ('origin', token) if token else ('legacy', json.dumps(
            value, sort_keys=True, ensure_ascii=False, separators=(',', ':')))
        if identity not in seen:
            merged.append(value)
            seen.add(identity)
    merged = dedupe_action_context(merged)
    result = merged[0]
    if len(merged) > 1:
        result['checkpoint_conflict'] = merged[1:]
    return result


def restore_navigation(current, history, canonical_name, unique=True):
    """Carry the newest unfinished navigation without replaying old requests.

    A current-day record is authoritative. The first historical record per
    navigation key decides whether older pending work is still unresolved;
    a newer done record is a tombstone, not permission to copy old pending.
    """
    if re.fullmatch(r'(当前窗口标签\d+|第\d+标签|标签\d+|当前窗口)', canonical_name):
        return
    navigation = current.get('navigation', {})
    if not isinstance(navigation, dict):
        raise CheckpointError('导航记录格式异常，保留原断点')
    seen = {key for key, value in navigation.items()
            if isinstance(value, dict) and value.get('status') in ('pending', 'done')}
    for path, previous in reversed(history):
        old_navigation = previous.get('navigation', {})
        if not isinstance(old_navigation, dict):
            raise CheckpointError(f'导航记录格式异常：{path}')
        for key in ('hot_spring_exit', 'return_leaf'):
            if key in seen:
                continue
            record = old_navigation.get(key)
            if record is None:
                continue
            if not isinstance(record, dict):
                raise CheckpointError(f'导航阶段格式异常：{path}')
            status = record.get('status')
            if status not in ('pending', 'done'):
                continue
            seen.add(key)
            if status == 'done':
                continue
            if not unique:
                raise CheckpointError('导航断点的实名账号映射不唯一，禁止恢复')
            carried = deepcopy(record)
            carried.setdefault('source_day', record.get('continuation_day', path.parent.name))
            carried.setdefault('continuation_day', path.parent.name)
            carried.setdefault('source_path', str(path))
            current.setdefault('navigation', {})[key] = carried


def restore_actions(state):
    """Carry pending intent and its original flow context, never yesterday's module completion."""
    resolved = read_checkpoint(state.directory / 'checkpoint_resolutions.json')
    from hyol_history import is_isolated
    isolated = read_checkpoint(state.directory / 'historical_isolations.json')
    for name, current in state.accounts.items():
        stem = state.account_paths[name].stem
        actions = current.setdefault('actions', {})
        canonical_name = getattr(state, 'aliases', {}).get(name, name)
        history = []
        for path in sorted(state.directory.glob(f'????-??-??/{stem}.json')):
            if path.parent.name >= state.day:
                continue
            previous = read_checkpoint(path)
            if previous.get('account') != canonical_name:
                raise CheckpointError(f'断点账号与文件不一致：{path}')
            history.append((path, previous))
        canonical_names = [getattr(state, 'aliases', {}).get(value, value) for value in state.accounts]
        restore_navigation(current, history, canonical_name, canonical_names.count(canonical_name) == 1)
        if 'inventory_recovery' not in current:
            for path, previous in reversed(history):
                recovery = previous.get('inventory_recovery')
                if recovery is None:
                    continue
                if not isinstance(recovery, dict) or recovery.get('account') != canonical_name:
                    raise CheckpointError(f'满仓恢复断点账号或格式异常：{path}')
                if recovery.get('status') == 'pending':
                    if canonical_names.count(canonical_name) != 1:
                        raise CheckpointError('满仓恢复账号身份不唯一')
                    current['inventory_recovery'] = deepcopy(recovery)
                    current['inventory_recovery'].setdefault('source_day', path.parent.name)
                break
        for path, previous in reversed(history):
            old_actions = previous.get('actions', {})
            if not isinstance(old_actions, dict):
                raise CheckpointError(f'动作记录格式异常：{path}')
            pending = {key: rec for key, rec in old_actions.items()
                       if isinstance(rec, dict) and rec.get('status') == 'pending'
                       and source_token(path, key, rec) not in resolved
                       and not is_isolated(isolated,path,key,rec)}
            for key, rec in pending.items():
                token = source_token(path, key, rec)
                present = actions.get(key, {})
                for context_key, context in old_actions.items():
                    if (context_key.startswith(action_family(key)) and context_key not in actions
                            and isinstance(context,dict) and context.get('status') != 'pending'):
                        actions[context_key] = deepcopy(context)
                        actions[context_key]['continuation_context'] = path.parent.name
                carried = deepcopy(rec)
                carried['origin'] = {'path': str(path), 'action': key, 'token': token}
                actions[key] = merge_pending_action(present, carried)
            for label, record in previous.get('network_retries', {}).items():
                if not any(key == label or key.startswith(label+'_network_') for key in pending):
                    continue
                if not isinstance(record, dict) or not isinstance(record.get('attempts'), int):
                    raise CheckpointError(f'网络重试次数格式异常：{path}')
                retries = current.setdefault('network_retries', {})
                existing = retries.get(label)
                if existing is None:
                    retries[label] = deepcopy(record)
                    retries[label].setdefault('source_day', path.parent.name)
                elif record['attempts'] > existing.get('attempts', 0):
                    existing['attempts'] = record['attempts']
                    existing['verified'] = False
            if any(key.startswith(('arena_', 'daily_arena_')) for key in pending):
                old_global = read_checkpoint(state.directory / (path.parent.name + '.json'))
                request = old_global.get('arena_requests', {}).get(name)
                if isinstance(request, dict):
                    state.global_state.setdefault('arena_requests', {}).setdefault(name, deepcopy(request))


def resolve_action(state, name, action, detail):
    origin = state.accounts[name].get('actions', {}).get(action, {}).get('origin')
    if not isinstance(origin, dict) or not origin.get('token'):
        return
    from hyol_scheduler import atomic_json, now_iso
    path = state.directory / 'checkpoint_resolutions.json'
    records = read_checkpoint(path)
    records[origin['token']] = dict(origin, account=name, resolved_at=now_iso(), detail=detail)
    atomic_json(path, records)


def encode_mask(mask):
    import base64
    import numpy as np
    return base64.b64encode(np.packbits(mask.astype(bool)).tobytes()).decode('ascii')


def decode_mask(value, shape):
    import base64
    import numpy as np
    try:
        bits = np.unpackbits(np.frombuffer(base64.b64decode(value, validate=True), dtype=np.uint8))
        return bits[:shape[0]*shape[1]].reshape(shape).astype(bool)
    except (ValueError, TypeError) as exc:
        raise CheckpointError('身份断点格式异常，不能关联账号') from exc


def register_identity_materials(scheduler):
    """Store only enrolled reference glyphs so packaged entrances share the same identities."""
    from PIL import Image
    import hyol_scheduler as hs
    if getattr(scheduler, 'automatic_accounts', False):
        return
    path = getattr(scheduler, 'identity_directory', scheduler.state.directory) / 'account_identities.json'
    identities = read_checkpoint(path)
    for account in scheduler.accounts:
        if account.identity_path.exists():
            with Image.open(account.identity_path) as reference:
                mask = hs.identity_text_mask(reference)
            identities[account.name] = {'mask': encode_mask(mask), 'shape': list(mask.shape)}
    if identities:
        hs.atomic_json(path, identities)


_identity_templates = None


def _identity_layout(frame, templates=None):
    """Known modal title and X permit dimmed glyphs; identity gates stay strict."""
    import hyol_scheduler as hs
    global _identity_templates
    if _identity_templates is None:
        _identity_templates = hs.TemplateStore(.98)
    templates = templates or _identity_templates
    def matches(name, roi):
        path = hs.ASSETS / name
        return path.exists() and templates.match(frame, path, roi, threshold=.98)[0]
    spring = (matches('hot_spring_title_current.bmp', (780, 230, 1100, 430))
              and matches('hot_spring_panel_close.bmp', (550, 70, 1650, 450)))
    managed = (matches('managed_settings_title_current.bmp', (750, 190, 1170, 290))
               and matches('managed_settings_close_current.bmp', (1320, 230, 1450, 330)))
    welfare = (matches('welfare_title_current.bmp', (1120,230,1320,380))
               and matches('welfare_close_current.bmp', (1320,250,1450,350)))
    weekend = (matches('daruma_weekend_page.bmp', (850,230,1180,360))
               and matches('daruma_weekend_close.bmp', (1290,260,1420,370)))
    puzzle = ((matches('puzzle_select_title.bmp', hs.VIEW)
               and matches('puzzle_select_close.bmp', hs.VIEW))
              or (matches('puzzle_game_anchor.bmp', hs.VIEW)
                  and matches('puzzle_game_close.bmp', hs.VIEW)))
    transformation = (matches('treasure_transformation_seal_current.bmp', (760,470,1170,640))
                      and matches('treasure_transformation_duration_current.bmp', (760,470,1170,640)))
    group = (matches('group_title_current.bmp', (850,210,1100,290))
             and matches('team_instance_close.bmp', (1370,240,1460,330)))
    wanted = (matches('wanted_team_anchor.bmp', (500,200,1450,850))
              and matches('wanted_close_current.bmp', (1310,290,1400,390)))
    daily = ((matches('daily_practice_anchor.bmp', (951,119,1501,369))
              or matches('daily_practice_title_core_current.bmp',(1190,140,1420,275)))
             and matches('daily_practice_close.bmp', (1370,200,1460,280)))
    public_room = (matches('team_public_room_title_current.bmp',(900,230,1100,315))
                   and matches('team_room_close.bmp',(1360,250,1450,340)))
    canyon = matches('identity_wanted_canyon_map_title_current.bmp',(1700,110,1850,145))
    roi = hs.IDENTITY_ROI
    if any(matches(name, (106,75,139,105)) for name in (
            'identity_team_badge_core_current.bmp', 'identity_team_badge_core_forest_current.bmp')):
        roi = (roi[0]+15, roi[1], roi[2]+15, roi[3])
    return roi, 56 if welfare or weekend or puzzle or group or wanted or daily or public_room else 50 if spring or managed or transformation else 145 if canyon else hs.IDENTITY_TEXT_THRESHOLD


def current_identity_crop(frame, templates=None):
    roi, _threshold = _identity_layout(frame, templates)
    return frame.crop(roi)


def current_identity_mask(frame, templates=None):
    import hyol_scheduler as hs
    roi, threshold = _identity_layout(frame, templates)
    mask = hs.identity_text_mask(frame.crop(roi), threshold=threshold)
    # Rows 8-11 are the animated map edge above the fixed name glyphs. A
    # bright repaint there disproportionately changes the narrow server score.
    # Keep the glyph threshold and assignment floor unchanged.
    mask[:12, :] = 0
    return mask


def identity_mask_scores(mask, identities):
    import hyol_scheduler as hs
    scores = {}
    for name, value in identities.items():
        reference = decode_mask(value['mask'], tuple(value['shape']))
        if reference.shape != mask.shape:
            raise CheckpointError('身份素材尺寸与当前归一化窗口不一致')
        scores[name] = (.6*hs.mask_f1(reference[:, :hs.IDENTITY_SERVER_WIDTH], mask[:, :hs.IDENTITY_SERVER_WIDTH])
                        + .4*hs.mask_f1(reference, mask))
    return scores


def joint_identity_assignment(masks, identities):
    """Require a unique one-to-one glyph assignment; never fill an unknown slot."""
    from functools import lru_cache
    if not masks or len(masks) > len(identities) or any(int(mask.sum()) < 50 for mask in masks.values()):
        return {}, {}, 0.
    slots, names = tuple(masks), tuple(identities)
    matrix = {slot: identity_mask_scores(masks[slot], identities) for slot in slots}

    @lru_cache(None)
    def best_two(row, used):
        if row == len(slots):
            return ((0., ()),)
        candidates = []
        for index, name in enumerate(names):
            if used & (1 << index):
                continue
            # A below-floor name cannot identify this role and must not be a
            # credible runner-up merely because it shares the server prefix.
            if matrix[slots[row]][name] < .92:
                continue
            for score, tail in best_two(row+1, used | (1 << index)):
                candidates.append((matrix[slots[row]][name]+score, (index,)+tail))
        return tuple(sorted(candidates, reverse=True)[:2])

    ranked = best_two(0, 0)
    if not ranked:
        return {}, matrix, 0.
    gap = ranked[0][0]-ranked[1][0] if len(ranked) > 1 else 1.
    mapping = {slot: names[index] for slot, index in zip(slots, ranked[0][1])}
    if gap < .08 or any(matrix[slot][name] < .92 for slot, name in mapping.items()):
        return {}, matrix, gap
    return mapping, matrix, gap


def match_identity(frame, identities):
    mask = current_identity_mask(frame)
    if int(mask.sum()) < 30:
        return None, mask
    scores = identity_mask_scores(mask, identities)
    ordered = sorted(scores, key=scores.get, reverse=True)
    if ordered and scores[ordered[0]] >= .92:
        other = scores[ordered[1]] if len(ordered) > 1 else 0
        if scores[ordered[0]] - other >= .08:
            return ordered[0], mask
    return None, mask


def bind_current_identity(scheduler, anonymous):
    """Promote a session using its discovery rule and a fresh identity recheck."""
    import hyol_scheduler as hs
    directory = scheduler.state.directory
    shared = directory.parent
    identities = read_checkpoint(getattr(scheduler,'identity_directory',shared) / 'account_identities.json')
    automatic = getattr(scheduler, 'automatic_accounts', False)
    first_frame = scheduler.capture()
    if automatic:
        first_name = None
        first_mask = current_identity_mask(first_frame, scheduler.templates)
    else:
        first_name, first_mask = match_identity(first_frame, identities)
    old = scheduler.state.accounts[anonymous.name]
    old_mask = old.get('identity_mask')
    if old_mask and int(first_mask.sum()) >= 30:
        if hs.mask_f1(decode_mask(old_mask, first_mask.shape), first_mask) < .92:
            raise hs.PendingError('匿名标签角色已变化，保留旧断点，禁止关联')
    if automatic or not first_name:
        if old.get('actions') and not old_mask:
            raise hs.PendingError('匿名旧断点没有身份素材，不能凭标签序号恢复')
        if automatic:
            from hyol_accounts import bind_visible_identity
            first_name = bind_visible_identity(scheduler, anonymous)
            identities = scheduler._dynamic_identity_materials
            first_mask = scheduler._dynamic_identity_masks[anonymous.index]
            if old_mask and hs.mask_f1(decode_mask(old_mask, first_mask.shape), first_mask) < .92:
                raise hs.PendingError('匿名标签角色已变化，保留旧断点，禁止关联')
        else:
            if int(first_mask.sum()) >= 30:
                old['identity_mask'] = encode_mask(first_mask)
                scheduler.state.save_account(anonymous.name)
            return anonymous
    scheduler.wait_interruptible(3)
    second_frame = scheduler.capture()
    if automatic:
        if scheduler.offline_dialog_visible(second_frame):
            raise hs.AccountOffline(anonymous, '当前标签显示掉线弹窗')
        if scheduler._generic_active_tab_x(second_frame) != anonymous.tab_x:
            raise hs.PendingError('复核身份时当前标签变化，未关联断点')
        mask = current_identity_mask(second_frame, scheduler.templates)
        masks = {**scheduler._dynamic_identity_masks, anonymous.index: mask}
        mapping, _scores, _gap = joint_identity_assignment(masks, identities)
        if mapping != scheduler._dynamic_identity_names:
            raise hs.PendingError('账号身份联合复核不一致，未关联断点')
        second_name = mapping.get(anonymous.index)
    else:
        second_name, mask = match_identity(second_frame, identities)
    if second_name != first_name or hs.mask_f1(first_mask, mask) < .92:
        raise hs.PendingError('账号身份两帧不一致，未关联断点')
    if old.get('actions') and not old_mask:
        raise hs.PendingError('唯一账号已确认，但匿名旧动作缺身份证据，须先对账')
    account = hs.Account(anonymous.index, first_name, anonymous.tab_x)
    state = hs.StateStore([first_name], directory=shared,
                          restore_history=getattr(scheduler,'run_context',None) is None)
    # The session glyph and current unique account must both agree before copying.
    if old_mask and old.get('actions'):
        actions = state.accounts[first_name].setdefault('actions', {})
        for key, value in old['actions'].items():
            if key not in actions:
                actions[key] = deepcopy(value)
            elif value.get('status') == 'pending' and actions[key] != value:
                actions[key] = merge_pending_action(actions[key], value)
        _, anonymous_tasks = load_tasks(scheduler.state, anonymous.name)
        task_path, tasks = load_tasks(state, first_name)
        hs.atomic_json(task_path, merge_records([(task_path, tasks), (directory, anonymous_tasks)]))
    state.accounts[first_name]['identity_mask'] = encode_mask(mask)
    state.save_account(first_name)
    scheduler.state = state
    scheduler.accounts = [account]
    scheduler.by_name = {first_name: account}
    scheduler.current_account_only = first_name
    scheduler.active = account
    scheduler._capture_account = account
    scheduler._verified_current_mask = mask
    if getattr(scheduler, 'automatic_accounts', False):
        scheduler._dynamic_identity_materials = identities
        scheduler._dynamic_identity_masks = {**getattr(scheduler,'_dynamic_identity_masks',{}),account.index:mask}
        scheduler._dynamic_identity_names = {**getattr(scheduler,'_dynamic_identity_names',{}),account.index:first_name}
    scheduler.account_logs = {first_name: hs.JsonlLogger(hs.LOGS / 'accounts' / (hs.slug(first_name)+'.jsonl'))}
    scheduler.event('current_identity_checkpoint_bound', account, session=str(directory),
                    observations=4 if automatic else 2)
    return account
