"""Discover account identities from the current game, without shipped account data."""
from __future__ import annotations

from copy import deepcopy
import os
from pathlib import Path
import uuid
import ctypes
import time
import hashlib

import numpy as np


def state_directory(config, root, *, environ=None):
    """Explicit installation settings win; clean installations share per-user state."""
    environ = os.environ if environ is None else environ
    value = config.get('SharedStateDirectory', '').strip()
    if value:
        path = Path(os.path.expandvars(value))
        return (path if path.is_absolute() else Path(root)/path).resolve()
    legacy = Path(root)/'state'
    # An upgrade keeps its existing ledgers. Empty folders in a clean package
    # do not take precedence over the common per-user directory.
    if legacy.is_dir() and any(legacy.rglob('*.json')):
        return legacy.resolve()
    local = environ.get('LOCALAPPDATA')
    return (Path(local)/'HYOLClient'/'state' if local else Path(root)/'state').resolve()


def game_session_token(scheduler):
    import hyol_scheduler as hs
    pid = hs.Win32.pid(scheduler.hwnd)
    handle = hs.Win32.kernel32.OpenProcess(hs.Win32.PROCESS_QUERY_LIMITED_INFORMATION, False, pid)
    if not handle:
        raise hs.PendingError('无法核实游戏会话起点')
    try:
        creation, end, kernel, user = (ctypes.c_ulonglong() for _ in range(4))
        if not hs.Win32.kernel32.GetProcessTimes(handle, ctypes.byref(creation), ctypes.byref(end),
                                               ctypes.byref(kernel), ctypes.byref(user)):
            raise hs.PendingError('无法读取游戏会话起点')
    finally:
        hs.Win32.kernel32.CloseHandle(handle)
    return f'{pid}:{scheduler.hwnd}:{creation.value:x}'


def session_is_alive(token):
    """A reused PID or HWND is a different session; unverifiable input fails closed."""
    import hyol_scheduler as hs
    try:
        pid_text, hwnd_text, creation = token.split(':')
        pid, hwnd = int(pid_text), int(hwnd_text)
    except (AttributeError, ValueError):
        return True
    if not hs.Win32.user32.IsWindow(hwnd) or hs.Win32.pid(hwnd) != pid:
        return False
    try:
        from types import SimpleNamespace
        return game_session_token(SimpleNamespace(hwnd=hwnd)) == token
    except hs.PendingError:
        return True


def resolve_observations(observations, identities, *, id_factory=None):
    """Enroll stable new glyphs, then require a unique assignment of every observation.

    Existing keys remain compatible with historical ledgers. New accounts have
    opaque UUID keys; display labels never determine ownership or permissions.
    Nothing is written until the complete assignment has passed all three frames.
    """
    import hyol_scheduler as hs
    from hyol_checkpoints import (encode_mask, identity_mask_scores,
                                 joint_identity_assignment)
    refs = deepcopy(identities)
    id_factory = id_factory or (lambda: 'account_'+uuid.uuid4().hex)
    if not observations:
        return {}, refs
    for slot, masks in observations.items():
        if (len(masks) < 3 or min(int(mask.sum()) for mask in masks) < 50
                or any(hs.mask_f1(masks[0], mask) < .92 for mask in masks[1:])):
            raise hs.PendingError(f'第{slot}个标签的身份特征未连续三帧确认')
        scores = [identity_mask_scores(mask, refs) for mask in masks]
        if all(max(row.values(), default=0) < .92 for row in scores):
            # An unstable old role must not be disguised as a fresh identity.
            close = {key for row in scores for key, score in row.items()
                     if score >= .80 and refs[key].get('has_unresolved_work')}
            if close:
                raise hs.PendingError(f'第{slot}个标签与历史未决身份相似，先核实原身份')
            key = id_factory()
            if key in refs:
                raise hs.PendingError('新账号标识重复，保留原身份资料')
            refs[key] = dict(mask=encode_mask(masks[0]), shape=list(masks[0].shape),
                             account_id=key, display_label=f'账号{len(refs)+1}',
                             origin='automatic_three_frame_observation')
    assignments = [joint_identity_assignment({slot:masks[i] for slot,masks in observations.items()}, refs)[0]
                   for i in range(3)]
    if not assignments[0] or any(mapping != assignments[0] for mapping in assignments[1:]):
        raise hs.PendingError('账号身份重复或存在歧义，未关联历史断点')
    return assignments[0], refs


def _has_unresolved_work(directory, key):
    import hyol_scheduler as hs
    from hyol_checkpoints import read_checkpoint
    stem = hs.slug(key)
    for path in Path(directory).glob('????-??-??/'+stem+'*.json'):
        record = read_checkpoint(path)
        if any(isinstance(row, dict) and row.get('status') == 'pending'
               for row in record.get('actions', {}).values()):
            return True
        if any(isinstance(row, dict) and row.get('status') == 'pending'
               for row in record.values()):
            return True
    return False


def collect_current_identity(scheduler, account, frame=None):
    """Only observe: caller owns the foreground lease and tab selection."""
    import hyol_scheduler as hs
    from hyol_checkpoints import current_identity_mask
    masks, badges = [], []
    for sample in range(3):
        scheduler.check_stop()
        frame = frame if sample == 0 and frame is not None else scheduler.capture()
        if scheduler.offline_dialog_visible(frame):
            raise hs.AccountOffline(account, '当前标签显示掉线弹窗')
        if scheduler._generic_active_tab_x(frame) != account.tab_x:
            raise hs.PendingError('采集身份时当前标签变化，未注册账号')
        masks.append(current_identity_mask(frame, scheduler.templates))
        badges.append(is_captain(scheduler, frame))
        if sample < 2:
            scheduler.wait_interruptible(.35)
    if (min(int(mask.sum()) for mask in masks) < 50
            or any(hs.mask_f1(masks[0], mask) < .92 for mask in masks[1:])):
        if any(scheduler.template(asset,roi,frame,threshold=.98)[0] for asset,roi in (
                ('team_result_confirm.bmp',hs.TEAM_BATTLE_RESULT_ROI),
                ('arena_result_confirm.bmp',(651,449,1151,829)),
                ('survival_result_confirm.bmp',(701,519,1151,819)),
                (hs.TEAM_BATTLE_START_TEMPLATE,hs.TEAM_BATTLE_START_ROI))):
            raise hs.PendingError(f'第{account.index}个标签仍在战斗或结算，姓名不可见；先结束原战斗并回到主界面，保留原记录')
        raise hs.PendingError(f'第{account.index}个标签身份被遮挡或变化')
    observed = getattr(scheduler, '_observed_captain_tabs', {})
    observed[account.tab_x] = all(badges)
    scheduler._observed_captain_tabs = observed
    return masks, frame


def is_captain(scheduler, frame):
    return any(scheduler.template(asset,(0,69,45,149),frame,threshold=.95)[0] for asset in (
        'team_personal_leader_core.bmp','team_personal_leader_white_core.bmp',
        'team_personal_leader_text_core_current.bmp'))


def _final_result(scheduler, frame):
    """Require both the finished-battle scroll and its acknowledgement control."""
    import hyol_scheduler as hs
    banner = next(((asset, x, y) for asset, roi in (
        ('arena_victory_banner.bmp', hs.ARENA_VICTORY_ROI),
        ('arena_defeat_banner.bmp', (780, 300, 1150, 650)))
        for ok, _score, x, y in [scheduler.template(asset, roi, frame, threshold=.95)] if ok), None)
    if banner is None:
        return None
    for asset in ('team_result_confirm.bmp', 'arena_result_confirm.bmp'):
        ok, _score, x, y = scheduler.template(asset, hs.TEAM_BATTLE_RESULT_ROI, frame, threshold=.98)
        if ok:
            return asset, x, y, banner
    return None


def _tab_title_mask(frame, tab_x):
    # Browser hover changes the pale tab fill when the pointer leaves for the
    # result button. Only title glyphs, never that fill, identify the tab.
    return (np.asarray(frame.crop((tab_x-48,5,tab_x+24,30)).convert('L')) < 105).astype(np.uint8)


def _same_tab_title(left, right):
    import hyol_scheduler as hs
    return np.array_equal(left, right) or hs.mask_f1(left, right) >= .95


def _reconcile_result_cleanup(scheduler, account, frame):
    """A restart on the home page closes only matching UI receipts, with no input."""
    import hyol_scheduler as hs
    from hyol_checkpoints import read_checkpoint
    from PIL import Image
    path = scheduler.shared_state_directory/'normal_result_cleanup.json'
    if not path.is_file() or not scheduler.main_ready(frame):
        return frame
    rows = read_checkpoint(path)
    token = game_session_token(scheduler)
    candidates = []
    title = _tab_title_mask(frame,account.tab_x)
    for identifier,row in rows.items():
        before = Path(row.get('before_evidence',''))
        if (row.get('status') != 'pending' or row.get('operation') != 'dismiss_final_result'
                or row.get('session') != token or row.get('tab_x') != account.tab_x
                or not before.is_file()):
            continue
        with Image.open(before) as original:
            if _same_tab_title(_tab_title_mask(original,account.tab_x),title):
                candidates.append(identifier)
    if not candidates:
        return frame
    for sample in range(3):
        scheduler.check_stop()
        scheduler.ensure_window()
        frame = scheduler.capture()
        if (scheduler._generic_active_tab_x(frame) != account.tab_x
                or not _same_tab_title(title,_tab_title_mask(frame,account.tab_x))
                or scheduler.offline_dialog_visible(frame) or not scheduler.main_ready(frame)
                or _final_result(scheduler,frame) is not None):
            raise hs.PendingError('原结算关闭后主界面变化，未补记关闭完成')
        if sample < 2:
            scheduler.wait_interruptible(.35)
    after = str(scheduler.save_raw_evidence(frame,account,'normal_member_result','verified'))
    latest = read_checkpoint(path)
    for identifier in candidates:
        if latest.get(identifier) != rows[identifier]:
            raise hs.PendingError('结算关闭凭据被更新，请重新核实')
        latest[identifier] = dict(rows[identifier],status='done',home_returned=True,
                                 evidence=after,finished_at=hs.now_iso(),reconciled_without_input=True)
    hs.atomic_json(path,latest)
    scheduler.event('normal_member_result_reconciled',account,receipts=candidates,
                    evidence=after,observations=3,content_clicks=0,battle_credit=False,task_credit=False)
    return frame


def dismiss_current_final_result(scheduler, account, frame=None):
    """Close a normal member result before its hidden name can be identified.

    This is only a UI acknowledgement. Its separate receipt never credits a
    battle, reward, task or account identity, including on a resumed old batch.
    Read-only identity probes retain their zero-content-input contract.
    """
    import hyol_scheduler as hs
    from hyol_checkpoints import read_checkpoint
    if (scheduler.no_content_clicks
            or getattr(scheduler, '_identity_observation_only', False)):
        return frame if frame is not None else scheduler.capture()
    frame = frame if frame is not None else scheduler.capture()
    expected = _final_result(scheduler, frame)
    if expected is None:
        return _reconcile_result_cleanup(scheduler,account,frame)
    title = _tab_title_mask(frame,account.tab_x)

    def require_result(current):
        if (scheduler.offline_dialog_visible(current)
                or scheduler._generic_active_tab_x(current) != account.tab_x
                or not _same_tab_title(_tab_title_mask(current,account.tab_x),title)
                or _final_result(scheduler, current) != expected):
            raise hs.PendingError('队员结算或当前标签变化，未点击确认')

    for sample in range(3):
        scheduler.check_stop()
        scheduler.ensure_window()
        frame = frame if sample == 0 else scheduler.capture()
        require_result(frame)
        if sample < 2:
            scheduler.wait_interruptible(.35)

    path = scheduler.shared_state_directory/'normal_result_cleanup.json'
    receipts = read_checkpoint(path)
    identifier = uuid.uuid4().hex
    token = game_session_token(scheduler)
    before = str(scheduler.save_raw_evidence(frame, account, 'normal_member_result', 'before'))
    row = dict(status='pending', operation='dismiss_final_result', session=token,
               tab_x=account.tab_x, title_hash=hashlib.sha256(title.tobytes()).hexdigest(), template=expected[0],
               banner=expected[3][0], before_evidence=before, started_at=hs.now_iso(),
               battle_credit=False, task_credit=False, identity='unconfirmed')
    receipts[identifier] = row
    hs.atomic_json(path, receipts)
    scheduler.event('normal_member_result_observed', account, receipt=identifier,
                    evidence=before, observations=3, battle_credit=False)
    names = ('active', 'generic_current_tab_x', 'current_account_only', '_verified_current_mask')
    missing = object()
    prior = {name:getattr(scheduler, name, missing) for name in names}
    guard_override = scheduler.__dict__.get('content_guard', missing)
    original_guard = scheduler.content_guard

    def result_guard(selected, page_anchors=(), anchor_threshold=None):
        if selected != account:
            raise hs.SafetyError('结算确认仅允许当前已观察标签')
        current = original_guard(selected, page_anchors, anchor_threshold=anchor_threshold)
        require_result(current)
        return current

    try:
        # Only this selected tab's finished scroll gets a temporary UI lease.
        # Restore normal account and task guards before returning to discovery.
        scheduler.active = account
        scheduler.generic_current_tab_x = account.tab_x
        scheduler.current_account_only = account.name
        scheduler._verified_current_mask = None
        scheduler.content_guard = result_guard
        if not scheduler.click_template(account, expected[0], hs.TEAM_BATTLE_RESULT_ROI,
                55, 18, 'normal_member_result_confirm_'+identifier, [expected[0]],
                threshold=.98, anchor_threshold=.98):
            raise hs.PendingError('队员结算确认按钮已变化')
    finally:
        if guard_override is missing:
            scheduler.__dict__.pop('content_guard', None)
        else:
            scheduler.content_guard = guard_override
        for name, value in prior.items():
            if value is missing:
                scheduler.__dict__.pop(name, None)
            else:
                setattr(scheduler, name, value)

    deadline = time.monotonic()+12
    stable = 0
    while time.monotonic() < deadline:
        scheduler.check_stop()
        frame = scheduler.capture()
        if (scheduler._generic_active_tab_x(frame) != account.tab_x
                or not _same_tab_title(_tab_title_mask(frame,account.tab_x),title)
                or scheduler.offline_dialog_visible(frame)):
            raise hs.PendingError('队员结算确认后标签或在线状态变化，凭据保留')
        if scheduler.main_ready(frame) and _final_result(scheduler, frame) is None:
            stable += 1
            if stable == 3:
                after = str(scheduler.save_raw_evidence(frame, account, 'normal_member_result', 'verified'))
                receipts = read_checkpoint(path)
                receipts[identifier] = dict(row, status='done', home_returned=True,
                                             evidence=after, finished_at=hs.now_iso())
                hs.atomic_json(path, receipts)
                scheduler.event('normal_member_result_closed', account, receipt=identifier,
                                evidence=after, observations=3, battle_credit=False, task_credit=False)
                return frame
        else:
            stable = 0
        scheduler.wait_interruptible(.35)
    raise hs.PendingError('队员结算确认已发送，主界面尚未连续确认；保留关闭凭据')


def observe_accounts(scheduler):
    """Read all visible tabs before registering or binding any content action."""
    missing = object()
    prior = getattr(scheduler, '_capture_binding_probe', missing)
    scheduler._capture_binding_probe = True
    try:
        return _observe_accounts(scheduler)
    finally:
        if prior is missing:
            scheduler.__dict__.pop('_capture_binding_probe', None)
        else:
            scheduler._capture_binding_probe = prior


def _observe_accounts(scheduler):
    import hyol_scheduler as hs
    from hyol_multi_solo import tab_close_positions, active_tab_centers
    first = tab_close_positions(scheduler.capture())
    scheduler.wait_interruptible(.3)
    if first != tab_close_positions(scheduler.capture()):
        raise hs.PendingError('游戏标签位置变化，未注册账号')
    centers = active_tab_centers(first)
    if not 1 <= len(centers) <= 6:
        raise hs.PendingError('当前支持1～6个稳定游戏标签')
    scheduler.config['TabXs'] = ','.join(map(str, centers))
    scheduler._observed_captain_tabs = {}
    observations, frames, unavailable = {}, {}, {}
    for slot, x in enumerate(centers, 1):
        scheduler.check_stop()
        account = hs.Account(slot, f'当前窗口标签{slot}', x)
        scheduler.chrome_click(x, 10, f'observe_account:{slot}', account)
        scheduler.wait_interruptible(.8)
        frame = scheduler.capture()
        if (scheduler.offline_dialog_visible(frame)
                or scheduler.template('offline_login_prompt.bmp',(780,450,1140,660),frame,threshold=.98)[0]):
            unavailable[slot] = 'offline_prompt'
            scheduler.event('account_disconnected_skipped', account, identity='unconfirmed')
            continue
        try:
            frame = dismiss_current_final_result(scheduler, account, frame)
            observations[slot], frames[slot] = collect_current_identity(scheduler, account, frame)
        except hs.AccountOffline:
            unavailable[slot] = 'offline_prompt'
            scheduler.event('account_disconnected_skipped', account, identity='unconfirmed')
        except hs.PendingError as exc:
            unavailable[slot] = str(exc)
            save = getattr(scheduler,'save_raw_evidence',None)
            evidence = str(save(frame,account,'account_identity_unavailable','pending')) if callable(save) else ''
            scheduler.event('account_identity_pending', account, detail=str(exc),evidence=evidence)
    return centers, observations, frames, unavailable


def register_observations(scheduler, observations):
    from hyol_checkpoints import read_checkpoint
    import hyol_scheduler as hs
    path = scheduler.shared_state_directory/'account_identities.json'
    original = read_checkpoint(path)
    refs = deepcopy(original)
    for key, row in refs.items():
        row['has_unresolved_work'] = _has_unresolved_work(scheduler.shared_state_directory, key)
    mapping, refs = resolve_observations(observations, refs)
    for row in refs.values():
        row.pop('has_unresolved_work', None)
    # Recheck before merging so an unrelated updater cannot be overwritten.
    if read_checkpoint(path) != original:
        raise hs.PendingError('账号注册资料被另一入口更新，请重新观察')
    if refs != original and not scheduler.no_content_clicks:
        hs.atomic_json(path, refs)
    scheduler._dynamic_identity_materials = refs
    return mapping, refs


def discover_accounts(scheduler):
    """Bind current accounts directly to durable identities, including a clean install."""
    import hyol_scheduler as hs
    centers, observations, frames, unavailable = observe_accounts(scheduler)
    mapping, refs = register_observations(scheduler, observations)
    accounts = [hs.Account(slot, key, centers[slot-1]) for slot,key in sorted(mapping.items())]
    scheduler.accounts = accounts
    scheduler.by_name = {account.name:account for account in accounts}
    scheduler.identity_candidates = tuple(hs.Account(i+1,key,0) for i,key in enumerate(refs))
    scheduler.state = hs.StateStore([a.name for a in accounts], directory=scheduler.shared_state_directory)
    scheduler.available_account_names = {a.name for a in accounts}
    scheduler.joint_identity_bound = set(scheduler.available_account_names)
    scheduler._dynamic_identity_materials = refs
    scheduler._dynamic_identity_masks = {slot:masks[-1] for slot,masks in observations.items()}
    scheduler._dynamic_identity_names = mapping
    scheduler._unavailable_reasons = unavailable
    scheduler.config['TabAccountNames'] = ','.join(a.name for a in accounts)
    scheduler.account_logs = {a.name:hs.JsonlLogger(hs.LOGS/'accounts'/(hs.slug(a.name)+'.jsonl')) for a in accounts}
    scheduler.active = None
    scheduler.state.global_state['verified_solo_tab_map'] = {a.name:a.tab_x for a in accounts}
    scheduler.state.global_state['tab_map'] = {a.name:a.tab_x for a in accounts}
    if not scheduler.no_content_clicks:
        scheduler.state.save_global()
    scheduler.event('dynamic_accounts_bound', mapping=mapping, unavailable=unavailable,
                    observations=3, content_clicks=getattr(scheduler,'content_click_count',0))
    return centers, mapping, frames, unavailable


def bind_visible_identity(scheduler, anonymous):
    """Enroll the already-selected current account, preserving old anonymous guards."""
    import hyol_scheduler as hs
    from hyol_checkpoints import joint_identity_assignment
    frame = dismiss_current_final_result(scheduler, anonymous)
    masks, _frame = collect_current_identity(scheduler, anonymous, frame)
    try:
        mapping, _refs = register_observations(scheduler, {anonymous.index:masks})
    except hs.PendingError:
        # Similar server glyphs may need the complete simultaneous assignment.
        # This scope permits only observed title tabs; game content remains
        # disabled until the original selected role has been observed again.
        read_only = scheduler.no_content_clicks
        observation_only = getattr(scheduler, '_identity_observation_only', False)
        scheduler.no_content_clicks = True
        scheduler._identity_observation_only = True
        try:
            _centers, observations, _frames, _unavailable = observe_accounts(scheduler)
            if anonymous.index not in observations:
                raise hs.PendingError('原当前账号未连续确认，未启动任务')
            scheduler.chrome_click(anonymous.tab_x,10,'restore_current_account_observation',anonymous)
            scheduler.wait_interruptible(.8)
            fresh, _frame = collect_current_identity(scheduler,anonymous)
        finally:
            scheduler.no_content_clicks = read_only
            scheduler._identity_observation_only = observation_only
        mapping, refs = register_observations(scheduler,observations)
        if anonymous.index not in mapping:
            raise hs.PendingError('原当前账号未连续确认，未启动任务')
        for mask in fresh:
            current = {slot:rows[-1] for slot,rows in observations.items()}
            current[anonymous.index] = mask
            if joint_identity_assignment(current,refs)[0] != mapping:
                raise hs.PendingError('返回原标签后身份变化，未启动任务')
        scheduler._dynamic_identity_masks = {slot:rows[-1] for slot,rows in observations.items()}
        scheduler._dynamic_identity_masks[anonymous.index] = fresh[-1]
        scheduler._dynamic_identity_names = mapping
    else:
        # A new single-role binding must not inherit an earlier tab assignment.
        # Keep exactly the context that passed this three-frame observation.
        scheduler._dynamic_identity_masks = {anonymous.index:masks[-1]}
        scheduler._dynamic_identity_names = mapping
    return mapping[anonymous.index]


def verify_dynamic_identity(scheduler, account, frame):
    from hyol_checkpoints import current_identity_mask, joint_identity_assignment
    refs = scheduler._dynamic_identity_materials
    expected = scheduler.state.aliases.get(account.name, account.name)
    mask = current_identity_mask(frame, scheduler.templates)
    masks = dict(getattr(scheduler, '_dynamic_identity_masks', {}))
    masks[account.index] = mask
    mapping, matrix, _gap = joint_identity_assignment(masks, refs)
    bound = getattr(scheduler, '_dynamic_identity_names', {})
    return (mapping.get(account.index) == expected
            and all(mapping.get(slot) == key for slot,key in bound.items())
            and scheduler._generic_active_tab_x(frame) == account.tab_x), matrix.get(account.index, {})
