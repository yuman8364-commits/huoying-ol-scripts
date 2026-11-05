"""Discover account identities from the current game, without shipped account data."""
from __future__ import annotations

from copy import deepcopy
import os
from pathlib import Path
import uuid
import ctypes
import time
import hashlib
import itertools

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


def identity_registry_path(scheduler):
    """Portable runs own their observations; never open a machine's role library."""
    if not getattr(scheduler, 'portable_accounts', False):
        return getattr(scheduler, 'identity_directory', scheduler.shared_state_directory)/'account_identities.json'
    context = getattr(scheduler, 'run_context', None)
    if context is not None:
        return context.directory/'account_identities.json'
    directory = getattr(scheduler, '_portable_identity_directory', None)
    if directory is None:
        identifier = getattr(getattr(scheduler, 'diagnostics', None), 'identifier', None) or uuid.uuid4().hex
        directory = Path(scheduler.shared_state_directory)/'runs'/identifier
        scheduler._portable_identity_directory = directory
    return Path(directory)/'account_identities.json'


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
    """A reused PID or HWND is a different session; unverifiable input fails closed.

    True strictly means "no reuse evidence was found" — never "verified same
    session". Callers must not treat True as an authorization signal; the only
    current caller (history archiving) uses True to keep unknown results
    pending, which is the conservative direction.
    """
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
                or any(hs.mask_f1(first, second) < .92
                       for first, second in itertools.combinations(masks, 2))):
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
    # A newly seen role can resemble a role already observed on another tab.
    # Do not let that occupied candidate suppress enrollment of the new glyphs.
    # Add a candidate only when every above-floor identity has a different,
    # unique strongest observation in all three frames. The final assignment
    # still has to pass the original .92 floor and .08 global ambiguity gap.
    matrices = [{slot: identity_mask_scores(masks[i], refs)
                 for slot, masks in observations.items()} for i in range(3)]
    owners = {}
    for key in refs:
        winners = []
        for matrix in matrices:
            ranked = sorted(((row[key], slot) for slot, row in matrix.items()), reverse=True)
            if (ranked[0][0] < .92 or len(ranked) > 1 and ranked[0][0] == ranked[1][0]):
                break
            winners.append(ranked[0][1])
        if len(winners) == 3 and len(set(winners)) == 1:
            owners[key] = winners[0]
    additions = []
    for slot, masks in observations.items():
        matching = {key for matrix in matrices for key, score in matrix[slot].items() if score >= .92}
        if matching and all(key in owners and owners[key] != slot for key in matching):
            unresolved = {key for matrix in matrices for key, score in matrix[slot].items()
                          if score >= .80 and refs[key].get('has_unresolved_work')}
            if any(key not in owners or owners[key] == slot for key in unresolved):
                continue
            additions.append((slot, masks[0]))
    for slot, mask in additions:
        key = id_factory()
        if key in refs:
            raise hs.PendingError('新账号标识重复，保留原身份资料')
        refs[key] = dict(mask=encode_mask(mask), shape=list(mask.shape), account_id=key,
                         display_label=f'账号{len(refs)+1}', origin='automatic_three_frame_observation')
    assignments = [joint_identity_assignment({slot:masks[i] for slot,masks in observations.items()}, refs)[0]
                   for i in range(3)]
    if not assignments[0] or any(mapping != assignments[0] for mapping in assignments[1:]):
        raise hs.PendingError('账号身份重复或存在歧义，未关联历史断点')
    return assignments[0], refs


def resolve_available_observations(observations, identities):
    """Quarantine ambiguous tabs; never pick one side of a duplicate identity."""
    import hyol_scheduler as hs
    successful = []
    slots = tuple(observations)
    # At most six tabs: inspect all largest valid subsets, retaining only
    # identities whose ownership is invariant across every possible subset.
    for size in range(len(slots) - 1, 0, -1):
        for chosen in itertools.combinations(slots, size):
            try:
                mapping, _refs = resolve_observations({s: observations[s] for s in chosen}, identities)
            except hs.PendingError:
                continue
            successful.append(mapping)
        if successful:
            break
    common = set.intersection(*(set(row) for row in successful)) if successful else set()
    for slot in tuple(common):
        owners = {row[slot] if row[slot] in identities else None for row in successful}
        if len(owners) != 1:
            common.remove(slot)
    if not common:
        raise hs.PendingError('账号身份重复或存在歧义，没有可独立确认的账号')
    mapping, refs = resolve_observations({s: observations[s] for s in slots if s in common}, identities)
    return mapping, refs, {s: '账号身份重复或存在歧义，保留本号原断点' for s in slots if s not in mapping}


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
    for sample in range(5):
        scheduler.check_stop()
        frame = frame if sample == 0 and frame is not None else scheduler.capture()
        if getattr(frame, '_hyol_chrome_only', False):
            raise hs.PendingError('当前标签没有可用游戏内容区域；原始浏览器帧不能登记身份')
        if scheduler.offline_dialog_visible(frame):
            raise hs.AccountOffline(account, '当前标签显示掉线弹窗')
        if scheduler._generic_active_tab_x(frame) != account.tab_x:
            raise hs.PendingError('采集身份时当前标签变化，未注册账号')
        masks.append(current_identity_mask(frame, scheduler.templates))
        badges.append(is_captain(scheduler, frame))
        if (len(masks) >= 3 and min(int(mask.sum()) for mask in masks[-3:]) >= 50
                and all(hs.mask_f1(first, second) >= .92
                        for first, second in itertools.combinations(masks[-3:], 2))):
            masks, badges = masks[-3:], badges[-3:]
            break
        if sample < 4:
            scheduler.wait_interruptible(.35)
    if (min(int(mask.sum()) for mask in masks) < 50
            or any(hs.mask_f1(first, second) < .92
                   for first, second in itertools.combinations(masks, 2))):
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
    header = 'team_reward_result_header_current.bmp'
    reward = scheduler.template(header, (700,300,1200,500), frame, threshold=.98)
    if reward[0]:
        button = scheduler.template(hs.TEAM_REWARD_RESULT_TEMPLATE, hs.TEAM_REWARD_RESULT_ROI, frame, threshold=.98)
        if button[0]:
            return hs.TEAM_REWARD_RESULT_TEMPLATE, button[2], button[3], (header,reward[2],reward[3])
    rankdown = scheduler.template('arena_rankdown_banner.bmp', (700,220,1250,430), frame, threshold=.98)
    if rankdown[0]:
        for asset in ('arena_rankdown_confirm.bmp','arena_rankdown_confirm_idle_current.bmp'):
            button = scheduler.template(asset, (800,600,1150,760), frame, threshold=.98)
            if button[0]:
                return asset, button[2], button[3], ('arena_rankdown_banner.bmp',rankdown[2],rankdown[3])
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
    # Native tab tooltips start at y=28 and add a dark border to this
    # 5..30 crop. Keep every title glyph through y=25 and ignore that edge.
    # Preserve the mask shape and receipt hashes; only comparison is trimmed.
    return np.array_equal(left, right) or hs.mask_f1(left[:21], right[:21]) >= .95


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


def dismiss_current_final_result(scheduler, account, frame=None, *, _layers=0,
                                 allow_reward_confirmation=True):
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
    if not allow_reward_confirmation and expected[3][0] not in (
            'arena_victory_banner.bmp', 'arena_defeat_banner.bmp'):
        raise hs.PendingError('检查仅可关闭明确的胜负提示，领奖或其他结算保留给原任务核实')
    if _layers >= 3:
        raise hs.PendingError('已确认的结算层数超过安全上限，未继续确认')
    title = _tab_title_mask(frame,account.tab_x)

    def require_result(current):
        result = _final_result(scheduler,current)
        if (scheduler.offline_dialog_visible(current)
                or scheduler._generic_active_tab_x(current) != account.tab_x
                or not _same_tab_title(_tab_title_mask(current,account.tab_x),title)
                or result is None or result[0] != expected[0] or result[3][0] != expected[3][0]):
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
        dx, dy = (62,15) if expected[3][0] == 'arena_rankdown_banner.bmp' else (55,18)
        if not scheduler.click_template(account, expected[0], hs.TEAM_BATTLE_RESULT_ROI,
                dx, dy, 'normal_member_result_confirm_'+identifier, [expected[0]],
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
        from hyol_identity_overlays import dismiss_identity_overlays
        frame = dismiss_identity_overlays(scheduler,account,frame,only_kind='arena_trial_notice')
        following = _final_result(scheduler,frame)
        if following and following[3][0] != expected[3][0]:
            # Arena defeat and strong loot can each paint a second known
            # acknowledgement. Every layer has its own UI receipt, no credit.
            frame = dismiss_current_final_result(scheduler,account,frame,_layers=_layers+1,
                        allow_reward_confirmation=allow_reward_confirmation)
            # The nested reward acknowledgement has its own bounded wait.
            # Give this outer receipt fresh time to observe the returned page.
            deadline = time.monotonic()+12
            stable = 0
        if (expected[3][0] == 'arena_rankdown_banner.bmp' and following is None
                and not scheduler.template('arena_rankdown_banner.bmp',(700,220,1250,430),frame,threshold=.95)[0]):
            from hyol_identity_overlays import inspect, dismiss_identity_overlays
            parent = inspect(scheduler,frame)
            if parent is not None and parent[0] == 'arena_lobby':
                frame = dismiss_identity_overlays(scheduler,account,frame)
                deadline = time.monotonic()+12
                stable = 0
        if scheduler.main_ready(frame) and _final_result(scheduler, frame) is None:
            stable += 1
            if stable == 3:
                after = str(scheduler.save_raw_evidence(frame, account, 'normal_member_result', 'verified'))
                receipts = read_checkpoint(path)
                if receipts.get(identifier) != row:
                    raise hs.PendingError('结算关闭凭据被并发更新，请重新核实')
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


def _capture_observed_account(scheduler):
    capture = getattr(scheduler, 'capture_account_observation', None)
    previous = scheduler.no_content_clicks
    scheduler.no_content_clicks = True
    try:
        return capture() if callable(capture) else scheduler.capture()
    finally:
        scheduler.no_content_clicks = previous


def _select_observed_account(scheduler, account):
    previous = scheduler.no_content_clicks
    scheduler.no_content_clicks = True
    try:
        scheduler.chrome_click(account.tab_x, 10, f'observe_account:{account.index}', account)
    finally:
        scheduler.no_content_clicks = previous


def wait_observed_tab(scheduler, account, frame, expected_markers):
    """Wait for a stable selected title before collecting any role pixels."""
    import hyol_scheduler as hs
    from hyol_multi_solo import tab_close_positions
    deadline = time.monotonic() + 3
    stable = 0
    while True:
        scheduler.check_stop()
        if tab_close_positions(frame) != expected_markers:
            raise hs.PendingError('身份采集前标签布局改变；未注册账号或点击内容')
        try:
            selected = scheduler._generic_active_tab_x(frame) == account.tab_x
        except hs.PendingError:
            selected = False
        stable = stable + 1 if selected else 0
        if stable >= 2:
            return frame
        if time.monotonic() >= deadline:
            raise hs.PendingError('切号后标签高亮未稳定，未采集或注册账号')
        scheduler.wait_interruptible(.2)
        frame = _capture_observed_account(scheduler)


def _observe_accounts(scheduler):
    import hyol_scheduler as hs
    from hyol_multi_solo import tab_close_positions, active_tab_centers
    first = tab_close_positions(_capture_observed_account(scheduler))
    scheduler.wait_interruptible(.3)
    if first != tab_close_positions(_capture_observed_account(scheduler)):
        raise hs.PendingError('游戏标签位置变化，未注册账号')
    centers = active_tab_centers(first)
    if not 1 <= len(centers) <= 6:
        raise hs.PendingError('当前支持1～6个稳定游戏标签')
    scheduler.config['TabXs'] = ','.join(map(str, centers))
    scheduler._observed_captain_tabs = {}
    scheduler.event('account_scan_started', count=len(centers))
    observations, frames, unavailable = {}, {}, {}
    for slot, x in enumerate(centers, 1):
        scheduler.check_stop()
        account = hs.Account(slot, f'当前窗口标签{slot}', x)
        if slot in getattr(scheduler, '_explicitly_deferred_slots', ()):
            unavailable[slot] = 'user_deferred: 用户要求保留待完成，未切换或操作内容'
            scheduler.event('account_user_deferred', account, content_clicks=0)
            continue
        frame = _capture_observed_account(scheduler)
        try:
            selected = scheduler._generic_active_tab_x(frame) == x
        except hs.PendingError:
            selected = False
        if not selected:
            _select_observed_account(scheduler, account)
            scheduler.wait_interruptible(.35)
            frame = _capture_observed_account(scheduler)
        try:
            frame = wait_observed_tab(scheduler, account, frame, first)
            if getattr(frame, '_hyol_chrome_only', False):
                raise hs.PendingError('content_surface_unavailable: 当前标签没有游戏内容区域，保留原断点')
            if (scheduler.offline_dialog_visible(frame)
                    or scheduler.template('offline_login_prompt.bmp',(780,450,1140,660),frame,threshold=.98)[0]):
                raise hs.AccountOffline(account, '当前标签显示掉线弹窗')
            from hyol_identity_overlays import dismiss_identity_overlays
            for repaint in range(4):
                clicks_before = scheduler.content_click_count
                try:
                    if not (scheduler.no_content_clicks
                            or getattr(scheduler, '_identity_observation_only', False)):
                        frame = dismiss_identity_overlays(scheduler,account,frame,only_kind='arena_trial_notice')
                        frame = dismiss_current_final_result(scheduler, account, frame)
                        frame = dismiss_identity_overlays(scheduler, account, frame)
                    observations[slot], frames[slot] = collect_current_identity(scheduler, account, frame)
                    break
                except hs.PendingError:
                    fresh = scheduler.capture()
                    # Server broadcasts briefly cover the victory banner. Wait
                    # on this same tab for its positive banner/button pair;
                    # never repeat a receipt after any content input was sent.
                    if (repaint == 3 or scheduler.content_click_count != clicks_before
                            or scheduler._generic_active_tab_x(fresh) != x
                            or scheduler.offline_dialog_visible(fresh)
                            or not any(scheduler.template(asset,hs.TEAM_BATTLE_RESULT_ROI,fresh,threshold=.98)[0]
                                       for asset in ('team_result_confirm.bmp','arena_result_confirm.bmp'))):
                        raise
                    scheduler.event('account_result_repaint_wait',account,attempt=repaint+1,content_clicks=0)
                    scheduler.wait_interruptible(.75)
                    frame = scheduler.capture()
        except hs.AccountOffline:
            unavailable[slot] = 'offline_prompt'
            scheduler.event('account_disconnected_skipped', account, identity='unconfirmed')
        except hs.PendingError as exc:
            unavailable[slot] = str(exc)
            save = getattr(scheduler,'save_raw_evidence',None)
            evidence = str(save(frame,account,'account_identity_unavailable','pending')) if callable(save) else ''
            scheduler.event('account_identity_pending', account, detail=str(exc),evidence=evidence)
    scheduler._account_observation_frames = frames
    return centers, observations, frames, unavailable


def register_observations(scheduler, observations):
    from hyol_checkpoints import read_checkpoint
    import hyol_scheduler as hs
    path = identity_registry_path(scheduler)
    original = read_checkpoint(path)
    refs = deepcopy(original)
    portable = getattr(scheduler, 'portable_accounts', False)
    context = getattr(scheduler, 'run_context', None)
    # An explicitly continued old run may already carry its own binding.
    # New Start has no binding and always enrolls from this run's live frames.
    if portable and not refs and context is not None and context.binding is not None:
        refs = deepcopy(context.binding['refs'])
    for key, row in refs.items():
        row['has_unresolved_work'] = (False if context is not None or portable
                                      else _has_unresolved_work(scheduler.shared_state_directory, key))
    rejected = {}
    try:
        mapping, refs = resolve_observations(observations, refs)
    except hs.PendingError as exc:
        try:
            mapping, accepted_refs, rejected = resolve_available_observations(observations, refs)
        except hs.PendingError:
            mapping = None
        from hyol_checkpoints import encode_mask, identity_mask_scores
        save = getattr(scheduler, 'save_raw_evidence', None)
        evidence = {str(slot): str(save(frame, hs.Account(slot, f'当前窗口标签{slot}', 0),
                    'identity_registration', 'pending'))
                    for slot, frame in getattr(scheduler, '_account_observation_frames', {}).items()}
        report = hs.LOGS/('identity_registration_'+scheduler.diagnostics.identifier+'.json')
        hs.atomic_json(report, dict(detail=str(exc), evidence=evidence,
            observations={str(slot): [dict(mask=encode_mask(mask), shape=list(mask.shape),
                scores=identity_mask_scores(mask, refs)) for mask in masks]
                for slot, masks in observations.items()}, content_clicks=scheduler.content_click_count))
        scheduler.event('identity_registration_partial' if mapping else 'identity_registration_pending',
                        detail=str(exc), report=str(report), evidence=evidence, unavailable=rejected)
        if mapping is None:
            raise
        refs = accepted_refs
    scheduler._identity_unavailable_reasons = rejected
    for row in refs.values():
        row.pop('has_unresolved_work', None)
    if portable:
        for slot,key in mapping.items():
            refs[key]['display_label'] = f'标签{slot}'
            refs[key]['scope'] = 'current_run'
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
    unavailable.update(getattr(scheduler, '_identity_unavailable_reasons', {}))
    accounts = [hs.Account(slot, key, centers[slot-1]) for slot,key in sorted(mapping.items())]
    scheduler.accounts = accounts
    scheduler.by_name = {account.name:account for account in accounts}
    scheduler.identity_candidates = tuple(hs.Account(i+1,key,0) for i,key in enumerate(refs))
    scheduler.state = hs.StateStore([a.name for a in accounts], directory=scheduler.shared_state_directory,
                                    restore_history=getattr(scheduler, 'run_context', None) is None)
    scheduler.available_account_names = {a.name for a in accounts}
    scheduler.joint_identity_bound = set(scheduler.available_account_names)
    scheduler._dynamic_identity_materials = refs
    scheduler._dynamic_identity_masks = {slot:masks[-1] for slot,masks in observations.items() if slot in mapping}
    scheduler._dynamic_identity_names = mapping
    if getattr(scheduler, 'run_context', None):
        scheduler.run_context.remember(centers,mapping,observations,frames,refs,unavailable)
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
    from hyol_identity_overlays import dismiss_identity_overlays
    # The quiz result may have an unclaimed reward. Its name glyph is visible
    # under a recognized dim layer, so observe it directly rather than closing
    # the result merely to establish identity.
    from PIL import Image
    from hyol_quiz import result_visible
    capture = getattr(scheduler, 'capture', None)
    frame = capture() if callable(capture) else None
    quiz_result = isinstance(frame, Image.Image) and result_visible(scheduler,frame)
    if not quiz_result:
        frame = (dismiss_current_final_result(scheduler, anonymous, frame)
                 if frame is not None else dismiss_current_final_result(scheduler, anonymous))
        frame = dismiss_identity_overlays(scheduler, anonymous, frame)
    masks, _frame = collect_current_identity(scheduler, anonymous, frame)
    try:
        mapping, _refs = register_observations(scheduler, {anonymous.index:masks})
    except hs.PendingError:
        if scheduler.config.get('CurrentWindowOnly') == '1':
            raise hs.PendingError('独立任务仅运行当前窗口；本窗口身份不能唯一确认，未切换其他标签')
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
    if anonymous.index not in mapping:
        raise hs.PendingError('当前窗口身份仍有歧义，保留原断点；未启动其他账号任务')
    return mapping[anonymous.index]


def verify_dynamic_identity(scheduler, account, frame):
    from hyol_checkpoints import current_identity_mask, joint_identity_assignment
    refs = scheduler._dynamic_identity_materials
    expected = scheduler.state.aliases.get(account.name, account.name)
    bound = getattr(scheduler, '_dynamic_identity_names', {})
    cache = getattr(frame, '_hyol_observations', None)
    key = ('dynamic_identity', expected, account.index, account.tab_x, id(refs),
           id(getattr(scheduler,'_dynamic_identity_masks',None)), tuple(bound.items()))
    if cache is not None and key in cache:
        return cache[key]
    mask = current_identity_mask(frame, scheduler.templates)
    masks = dict(getattr(scheduler, '_dynamic_identity_masks', {}))
    masks[account.index] = mask
    mapping, matrix, _gap = joint_identity_assignment(masks, refs)
    result = (mapping.get(account.index) == expected
            and all(mapping.get(slot) == key for slot,key in bound.items())
            and scheduler._generic_active_tab_x(frame) == account.tab_x), matrix.get(account.index, {})
    if cache is not None:
        cache[key] = result
    return result
