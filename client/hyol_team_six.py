"""Anonymous six-tab team client. Selection is by visible tab position only."""
from __future__ import annotations

import ctypes
from dataclasses import dataclass, replace
from datetime import datetime
import itertools
import queue
import threading
import time
import tkinter as tk
from tkinter import ttk

import numpy as np
from PIL import Image

import hyol_scheduler as hs
from hyol_diagnostics import manual_pause_status
from hyol_daily_tasks import VERSION


TAB_XS = (180, 310, 445, 590, 730, 860)
TAB_LABELS = tuple(f"第{i}个游戏标签" for i in range(1, 7))
DEFAULT_TASKS = ("group_instance", "wanted", "strong_approach")
TASKS = (*DEFAULT_TASKS, "sealed", "rescue_jinchuriki", "team_pvp")
TASK_LABELS = {"group_instance": "组队副本", "wanted": "通缉", "strong_approach": "强者",
               "sealed": "封印忍者", "rescue_jinchuriki": "营救人柱力（开放且免费）",
               "team_pvp": "组队竞技（免费奖励）"}
FRIEND_ROW_X = 1754
FRIEND_ROW_WIDTH = 120
FRIEND_ROW_Y = 390
FRIEND_ROW_STEP = 50


@dataclass(frozen=True)
class TeamSelection:
    leader: int
    members: tuple[int, ...]
    friend_shortcut: bool = False
    tasks: tuple[str, ...] = DEFAULT_TASKS

    def __post_init__(self) -> None:
        if self.leader not in range(1, 7):
            raise ValueError("队长须选第1～6个游戏标签")
        if self.friend_shortcut:
            if self.members:
                raise ValueError("好友快捷选项由画面确定两名队员")
        elif len(self.members) not in (1, 2) or len(set(self.members)) != len(self.members):
            raise ValueError("须选择1或2个不同的队员标签")
        if self.leader in self.members or any(slot not in range(1, 7) for slot in self.members):
            raise ValueError("队长与队员标签不能重复")
        if not self.tasks or len(set(self.tasks)) != len(self.tasks) or any(task not in TASKS for task in self.tasks):
            raise ValueError("须勾选有效的组队任务")
        object.__setattr__(self, "tasks", tuple(self.tasks))


def _trim(mask: np.ndarray) -> np.ndarray | None:
    ys, xs = np.nonzero(mask)
    if not len(xs):
        return None
    return mask[ys.min():ys.max()+1, xs.min():xs.max()+1]


def _visible_name_mask(image: Image.Image) -> np.ndarray | None:
    rgb = np.asarray(image.convert("RGB"), dtype=np.int16)
    white = (rgb.min(2) > 175) & (rgb.max(2) - rgb.min(2) < 75)
    yellow = ((rgb[:, :, 0] > 140) & (rgb[:, :, 1] > 125)
              & (rgb[:, :, 2] < 145) & (rgb[:, :, 0] > rgb[:, :, 2] + 40))
    return _trim((white | yellow).astype(np.uint8))


def friend_room_name_score(friend: Image.Image, room: Image.Image) -> float:
    left, right = _visible_name_mask(friend), _visible_name_mask(room)
    if left is None or right is None or left.shape[1] < 25 or right.shape[1] < 25:
        return 0.0
    scaled = Image.fromarray(right * 255).resize((left.shape[1], left.shape[0]),
                                                     Image.Resampling.NEAREST)
    return hs.mask_f1(left, (np.asarray(scaled) > 0).astype(np.uint8))


def name_shape_score(identity: Image.Image, friend_row: Image.Image) -> float:
    """Compare glyph shapes only; neither name text nor a name asset is saved."""
    left = np.asarray(identity.convert("RGB"), dtype=np.int16)[8:28]
    right = np.asarray(friend_row.convert("RGB"), dtype=np.int16)[5:19]
    left_mask = _trim(((left.min(2) > 180) & (left.max(2) - left.min(2) < 75)).astype(np.uint8))
    white = (right.min(2) > 175) & (right.max(2) - right.min(2) < 75)
    yellow = ((right[:, :, 0] > 140) & (right[:, :, 1] > 125)
              & (right[:, :, 2] < 145) & (right[:, :, 0] > right[:, :, 2] + 40))
    right_mask = _trim((white | yellow).astype(np.uint8))
    if left_mask is None or right_mask is None or left_mask.shape[1] < 25 or right_mask.shape[1] < 25:
        return 0.0
    scaled = Image.fromarray(right_mask * 255).resize(
        (left_mask.shape[1], left_mask.shape[0]), Image.Resampling.NEAREST)
    return hs.mask_f1(left_mask, (np.asarray(scaled) > 0).astype(np.uint8))


def yellow_friend_rows(frame: Image.Image) -> tuple[int, ...]:
    """Find the first two yellow name bands in the visible friends list."""
    rgb = np.asarray(frame.convert("RGB"), dtype=np.int16)
    crop = rgb[380:780, 1755:1858]
    yellow = ((crop[:, :, 0] > 130) & (crop[:, :, 1] > 110)
              & (crop[:, :, 2] < 150) & (crop[:, :, 0] > crop[:, :, 2] + 40))
    totals = yellow.sum(axis=1)
    active = np.flatnonzero(totals >= 6)
    groups: list[list[int]] = []
    for y in active:
        if not groups or y - groups[-1][-1] > 3:
            groups.append([])
        groups[-1].append(int(y))
    rows = []
    for group in groups:
        if len(group) >= 5 and sum(int(totals[y]) for y in group) >= 70:
            row_y = 380 + group[0] - 6
            if 380 <= row_y <= 730:
                rows.append(row_y)
    return tuple(rows)


def assign_friend_rows(
    identities: dict[int, Image.Image], rows: dict[int, Image.Image],
    allowed_slots: tuple[int, ...], min_score: float = .62, min_margin: float = .08,
) -> dict[int, int]:
    """Return row-y to tab-slot mapping only when every winner is unique."""
    if not rows or len(rows) > len(allowed_slots):
        raise hs.PendingError("好友列表与已打开标签无法一一对应")
    scores = {(y, slot): name_shape_score(identities[slot], row)
              for y, row in rows.items() for slot in allowed_slots}
    candidates = []
    for slots in itertools.permutations(allowed_slots, len(rows)):
        mapping = dict(zip(rows, slots))
        chosen = [scores[y, slot] for y, slot in mapping.items()]
        if min(chosen) < min_score:
            continue
        if any(scores[y, slot] - max(
            (scores[y, other] for other in allowed_slots if other != slot), default=0.0
        ) < min_margin for y, slot in mapping.items()):
            continue
        candidates.append((sum(chosen), mapping))
    if len(candidates) != 1:
        raise hs.PendingError("好友名称画面匹配不唯一；未发送邀请")
    return candidates[0][1]


class MembershipIncomplete(hs.PendingError):
    """Only positively identified, configured members occupy the visible room."""


def room_member_assignment(frame: Image.Image, references: dict[str, np.ndarray], expected: tuple[str, ...]) -> tuple[str, ...]:
    """Match occupied room rows, weighting the server digits for same nicknames."""
    assigned = []
    start = hs.TEAM_ROOM_MEMBER_ORIGIN[1]
    for height in hs.TEAM_ROOM_MEMBER_ROW_HEIGHTS:
        # Names are beneath the portrait/HP line; an occupied unknown row is
        # never interpreted as a vacant slot or a configured teammate.
        name_band = hs.identity_text_mask(frame.crop((715, start+85, 960, start+height-3)))
        if int(name_band.sum()) < 10:
            start += height
            continue
        scene = hs.identity_text_mask(frame.crop((700, start, 1200, start+height)))
        scores = []
        for name, reference in references.items():
            corr, x, y = hs.ncc(scene.astype(np.float64), reference.astype(np.float64))
            got = scene[y:y+reference.shape[0], x:x+reference.shape[1]]
            if got.shape != reference.shape:
                continue
            score = (.6*hs.mask_f1(reference[:, :hs.IDENTITY_SERVER_WIDTH], got[:, :hs.IDENTITY_SERVER_WIDTH])
                     + .4*hs.mask_f1(reference, got))
            # A high glyph-overlap score at a poor correlation location is
            # not an identity candidate. Ranking it first used to mask the
            # correctly located role and reject otherwise verified rooms.
            if score >= .85 and corr >= .85:
                scores.append((score, corr, name))
        scores.sort(reverse=True)
        if (not scores or scores[0][0] < .85 or scores[0][1] < .85
                or len(scores) > 1 and scores[0][0] - scores[1][0] < .06):
            raise hs.PendingError("组队房间存在未知或歧义角色，不能关联邀请")
        name = scores[0][2]
        if name not in expected or name in assigned:
            raise hs.PendingError("组队房间有额外或重复成员，保留原队伍")
        assigned.append(name)
        start += height
    if not assigned:
        raise hs.PendingError("组队房间没有正向角色证据")
    return tuple(assigned)


class SixTabScheduler(hs.Scheduler):
    """Use tab highlights and transient visuals as the account lease."""

    def template(self, name, roi=hs.VIEW, frame=None, threshold=None):
        result = super().template(name,roi,frame,threshold=threshold)
        if name == 'friends_panel_close.bmp' and not result[0]:
            return super().template('friends_panel_close_core_20261030.bmp',(1840,260,1920,350),frame,threshold=.98)
        return result

    def __init__(self, event_sink=None):
        config = hs.read_config(hs.ROOT / "TEAM_Client.ini")
        config.update(AccountMode="tab6", TabAccountNames=",".join(f"当前窗口标签{i}" for i in range(1, 7)),
                      TabXs=",".join(map(str, TAB_XS)), Team1="当前窗口标签1,当前窗口标签2",
                      Team2="当前窗口标签3,当前窗口标签4", AutoForeground="1",
                      RescueJinchurikiEnabled="1", AllowMarketPurchase="0", ManagedSafetyVerified="1")
        # The user explicitly requested final managed mode for team tasks.
        # Its shared gate still proves the toolbar/start control/active badge.
        super().__init__(config, "team", False, False, event_sink=event_sink)
        self.command_kind = "team1"
        self._tab_title_masks: dict[int, np.ndarray] = {}
        self._session_identity: dict[int, Image.Image] = {}
        self._friend_name_rows: dict[int, Image.Image] = {}
        self._bound_identity_masks: dict[int, np.ndarray] = {}
        self._bound_identity_names: dict[int, str] = {}
        self.automatic_accounts = True
        self.portable_accounts = self.config.get('PortableAccounts', '1') == '1'
        self.config['AccountDiscovery'] = 'automatic'

    @staticmethod
    def _title_mask(frame: Image.Image, x: int) -> np.ndarray:
        patch = np.asarray(frame.crop((x - 48, 5, x + 24, 30)).convert("L"))
        return (patch < 105).astype(np.uint8)

    def verify_identity(self, account: hs.Account, frame=None):
        if getattr(self, 'run_context', None):
            account = self.run_context.resolve(self, account)
        frame = frame or self.capture()
        try:
            active = self._generic_active_tab_x(frame) == account.tab_x
        except hs.PendingError:
            active = False
        reference = self._tab_title_masks.get(account.index)
        current_title = self._title_mask(frame, account.tab_x)
        title_score = hs.mask_f1(reference, current_title) if reference is not None else 0.0
        if reference is not None and not reference.any() and not current_title.any():
            # A blank browser title is valid. The selected-tab highlight and
            # the independently bound, unique role still have to match.
            title_score = 1.0
        role_ok, role_scores, _visible = self._bound_role_verified(account, frame)
        return active and title_score >= .70 and role_ok, {**role_scores, "tab_title": title_score}

    def _bound_role_verified(self, account: hs.Account, frame) -> tuple[bool, dict[str, float], bool]:
        from hyol_arena_3v3 import alias_verified
        activity=alias_verified(self,account,frame)
        if activity is not None:return activity
        names = getattr(self, "_bound_identity_names", {})
        if account.index not in names:
            return True, {}, False  # Initial tab observation has no content authorization.
        cache = getattr(frame, '_hyol_observations', None)
        key = ('team_role', account.index, id(self._known_identity_materials),
               id(self._bound_identity_masks), tuple(names.items()))
        if cache is not None and key in cache:
            return cache[key]
        from hyol_checkpoints import current_identity_mask, joint_identity_assignment
        mask = current_identity_mask(frame, getattr(self, "templates", None))
        if int(mask.sum()) < 50:
            return False, {}, False  # A hidden name still needs the shared page-anchor gate.
        masks = dict(self._bound_identity_masks)
        masks[account.index] = mask
        mapping, matrix, _gap = joint_identity_assignment(masks, self._known_identity_materials)
        result = mapping == names, matrix.get(account.index, {}), True
        if cache is not None:
            cache[key] = result
        return result

    def switch_account(self, account: hs.Account, verify: bool = True):
        context = getattr(self, 'run_context', None)
        if context and context.binding and not context.probing:
            return context.select(self, account,allow_hidden=not verify
                or account.name in getattr(self,'expected_invitation_members',set()))
        self.chrome_click(account.tab_x, 10, f"switch_tab:{account.index}", account)
        self.wait_interruptible(.8)
        frame = self.capture()
        return self._observe_selected_account(account, frame)

    def _observe_selected_account(self, account: hs.Account, frame):
        """Validate a fresh selected-tab frame, including its original title and role."""
        if self.template("offline_login_prompt.bmp", (780,450,1140,660), frame, threshold=.98)[0]:
            raise hs.PendingError(f"第{account.index}个游戏标签需要登录")
        if self._generic_active_tab_x(frame) != account.tab_x:
            raise hs.PendingError(f"第{account.index}个游戏标签未确认高亮")
        if account.index not in self._tab_title_masks:
            self._tab_title_masks[account.index] = self._title_mask(frame, account.tab_x)
        else:
            title_score = hs.mask_f1(self._tab_title_masks[account.index], self._title_mask(frame, account.tab_x))
            if title_score < .70:
                evidence = self.save_raw_evidence(frame, account, "generic_tab_title_changed", "pending")
                self.event("generic_tab_title_unverified", account, title_score=title_score, evidence=str(evidence))
                raise hs.PendingError(f"第{account.index}个游戏标签标题或位置发生变化：{evidence}")
            role_ok, scores, visible = self._bound_role_verified(account, frame)
            if visible and not role_ok:
                evidence = self.save_raw_evidence(frame, account, "generic_tab_role_changed", "pending")
                self.event("generic_tab_role_unverified", account, identity_scores=scores, evidence=str(evidence))
                raise hs.PendingError(f"第{account.index}个标签当前角色与原实名绑定不一致：{evidence}")
        self.active = account
        # Binding/resume is observational.  An invitation may be our earlier
        # pending send, so neither accept nor decline it while selecting a tab.
        return frame

    def content_guard(self, account, page_anchors=(), anchor_threshold=None):
        anchors = tuple(page_anchors)
        frame = super().content_guard(account, anchors, anchor_threshold)
        from hyol_team_invitation import inspect as inspect_invitation, QUESTION
        modal = inspect_invitation(frame,self.templates)
        if modal is not None:
            decline = getattr(self,'_approved_invitation_decline',None)
            accept = getattr(self,'_approved_invitation',None)
            accepting = (accept and accept[0] == account.name and 'team_invite_accept_yes.bmp' in anchors
                         and self.template(accept[1],(601,350,1301,750),frame,threshold=.98)[0])
            declining = (decline == (account.name,modal) and QUESTION in anchors
                         and account.name not in self.expected_invitation_members)
            if not (accepting or declining):
                raise hs.PendingError('邀请来源或原动作未核实，弹窗保留；不接受、不拒绝、不点击其他页面')
        if modal is None and self.template("team_invite_question_current.bmp", (500,300,1400,800), frame, threshold=.95)[0]:
            approved = getattr(self, "_approved_invitation", None)
            if (not approved or approved[0] != account.name or "team_invite_accept_yes.bmp" not in anchors
                    or not self.template(approved[1], (601,350,1301,750), frame, threshold=.98)[0]):
                raise hs.PendingError("邀请来源或原动作未核实，弹窗保留；不接受、不拒绝、不点击其他页面")
        return frame

    def require_named_members(self, names: tuple[str, ...]) -> dict[str, str]:
        aliases = getattr(self.state, "aliases", {})
        if (any(name not in aliases or self.by_name[name].index not in self._session_identity for name in names)
                or len({aliases.get(name) for name in names}) != len(names)):
            raise hs.PendingError("组队标签尚无一致且唯一的实名映射，未发送邀请")
        return {name: aliases[name] for name in names}

    def commit_action(self, account, action, detail=""):
        if action == getattr(self, "_creating_membership", None):
            # Vanishing confirmation only proves that a click was processed.
            # Keep its intent until every member proves the actual room.
            self.event("generic_team_membership_pending", account, action=action, detail=detail)
            return
        return super().commit_action(account, action, detail)

    def create_verified_team(self, team: hs.Team, leader: hs.Account) -> None:
        self.require_named_members(team.members)
        action = f"team{team.index}_create"
        if self.state.action_status(leader.name, action) == "pending":
            raise hs.PendingError("原建队结果待核实；不重复确认或新建队伍")
        self._creating_membership = action
        try:
            super().create_verified_team(team, leader)
        finally:
            self._creating_membership = None

    def bind_session(self, slots: tuple[int, ...], allow_unavailable: bool = False,
                     *, leaders_only: bool = False, tab_positions=None) -> None:
        if self.config.get('AccountDiscovery') == 'automatic':
            return self._bind_automatic_session(slots, allow_unavailable, leaders_only, tab_positions)
        self.bind_window(wait=True, timeout_seconds=15)
        from hyol_multi_solo import tab_close_positions, active_tab_centers
        first = tab_close_positions(self.capture())
        self.wait_interruptible(.3)
        second = tab_close_positions(self.capture())
        centers = active_tab_centers(first)
        if first != second or not 2 <= len(centers) <= 6 or any(slot > len(centers) for slot in slots):
            raise hs.PendingError('组队标签数量或位置变化，未点击标签X或游戏内容')
        if tab_positions is not None and (set(tab_positions) != set(range(1,len(centers)+1))
                or set(tab_positions.values()) != set(centers)):
            raise hs.PendingError('实名恢复标签映射不是当前位置的完整排列')
        self.accounts = [hs.Account(index, f'当前窗口标签{index}',
                                   tab_positions[index] if tab_positions is not None else center)
                         for index, center in enumerate(centers, 1)]
        self.by_name = {account.name: account for account in self.accounts}
        self.config['TabXs'] = ','.join(map(str, centers))
        self._tab_title_masks.clear()
        self._session_identity.clear()
        self._bound_identity_masks = {}
        self._bound_identity_names = {}
        self._unavailable_reasons = {}
        self.event('generic_team_tabs_observed', title_centers=list(centers), close_clicks=0)
        pid = hs.Win32.pid(self.hwnd)
        handle = hs.Win32.kernel32.OpenProcess(hs.Win32.PROCESS_QUERY_LIMITED_INFORMATION, False, pid)
        if not handle:
            raise hs.PendingError("无法核实浏览器会话")
        try:
            creation, end, kernel, user = (ctypes.c_ulonglong() for _ in range(4))
            if not hs.Win32.kernel32.GetProcessTimes(handle, ctypes.byref(creation), ctypes.byref(end),
                                                      ctypes.byref(kernel), ctypes.byref(user)):
                raise hs.PendingError("无法读取浏览器会话起点")
        finally:
            hs.Win32.kernel32.CloseHandle(handle)
        directory = self.shared_state_directory / f"window_{pid}_{self.hwnd}_{creation.value:x}"
        self.session_token = f'{pid}:{self.hwnd}:{creation.value:x}'
        self.state = hs.StateStore([a.name for a in self.accounts], directory=directory)
        self.account_logs = {a.name: hs.JsonlLogger(hs.LOGS / "accounts" / f"{hs.slug(a.name)}.jsonl")
                             for a in self.accounts}
        from hyol_checkpoints import (read_checkpoint, match_identity, encode_mask, decode_mask,
                                     current_identity_mask, current_identity_crop, joint_identity_assignment,
                                     identity_mask_scores)
        identities = read_checkpoint(self.shared_state_directory / 'account_identities.json')
        self._known_identity_materials = identities
        aliases = {}
        observations = {}
        observed_frames = {}
        for slot in slots:
            account = self.accounts[slot-1]
            try:
                frame = self.switch_account(account)
            except (hs.PendingError, hs.AccountOffline) as exc:
                if allow_unavailable and (isinstance(exc, hs.AccountOffline) or "需要登录" in str(exc)):
                    self._unavailable_reasons[slot] = 'offline_prompt'
                    self.event("generic_tab_unavailable", account, reason="login_required")
                    continue
                raise
            name, mask = match_identity(frame, identities)
            if int(mask.sum()) < 50:
                evidence = self.save_raw_evidence(frame, account, "generic_identity_not_visible", "pending")
                self.event("generic_identity_unverified", account, glyph_pixels=int(mask.sum()),
                           first_scores=identity_mask_scores(mask, identities), evidence=str(evidence))
                if allow_unavailable:
                    self._unavailable_reasons[slot] = 'identity_not_visible'
                    self.event("generic_tab_unavailable", account, reason="identity_not_visible")
                    continue
                raise hs.PendingError(f"第{slot}个游戏标签未显示可核对的角色文字")
            self.wait_interruptible(3)
            second = self.capture()
            second_name, second_mask = match_identity(second, identities)
            if hs.mask_f1(mask, second_mask) < .92:
                evidence = [str(self.save_raw_evidence(image, account, f"generic_identity_changed_{index}", "pending"))
                            for index, image in enumerate((frame, second), 1)]
                self.event("generic_identity_unverified", account, mask_consistency=hs.mask_f1(mask, second_mask),
                           observed_names=[name, second_name], evidence=evidence,
                           first_scores=identity_mask_scores(mask, identities), second_scores=identity_mask_scores(second_mask, identities))
                raise hs.PendingError(f'第{slot}个游戏标签角色两帧不一致')
            self._session_identity[slot] = current_identity_crop(frame)
            observations[slot] = (mask, second_mask)
            observed_frames[slot] = (frame, second)
            old = self.state.accounts[account.name]
            if old.get('actions') and not old.get('identity_mask'):
                raise hs.PendingError(f'第{slot}个标签匿名旧动作没有身份素材，须先对账')
            if old.get('identity_mask') and hs.mask_f1(decode_mask(old['identity_mask'], mask.shape), mask) < .92:
                prior, prior_scores, _ = joint_identity_assignment(
                    {slot:decode_mask(old['identity_mask'],mask.shape)},identities)
                current, current_scores, _ = joint_identity_assignment({slot:mask},identities)
                confirmed, _, _ = joint_identity_assignment({slot:second_mask},identities)
                if not prior or prior != current or current != confirmed:
                    raise hs.PendingError(f'第{slot}个标签角色已变化，保留原断点')
                self.event('generic_identity_layout_reconciled',account,mapping=current,
                           prior_scores=prior_scores,current_scores=current_scores,
                           detail='旧字形与当前已核实面板的两帧字形均唯一匹配同一实名；保留原动作')
            old['identity_mask'] = encode_mask(mask)
            if not self.no_content_clicks:
                self.state.save_account(account.name)
            if name and name == second_name:
                aliases[account.name] = name
        joint_names = {}
        if len(aliases) != len(self._session_identity):
            if leaders_only and allow_unavailable:
                # A role below the identity floor must not make a separately
                # verifiable captain unavailable. Duplicate/ambiguous eligible
                # roles still have to pass the joint one-to-one assignment.
                for slot in tuple(self._session_identity):
                    if all(max(identity_mask_scores(mask, identities).values(), default=0) < .92
                           for mask in observations[slot]):
                        self._unavailable_reasons[slot] = 'identity_material_missing'
                        self._session_identity.pop(slot)
                        observations.pop(slot)
                        self.event('generic_tab_unavailable', self.accounts[slot-1],
                                   reason='identity_material_missing')
            # Strict per-frame scores can tie similar server/name prefixes.
            # Observe other tabs without content input so the full assignment
            # can distinguish them. Unavailable extra tabs are never filled in.
            for account in self.accounts:
                if account.index in slots or leaders_only:
                    continue
                try:
                    frame = self.switch_account(account)
                    first = current_identity_mask(frame)
                    if int(first.sum()) < 50:
                        continue
                    self.wait_interruptible(3)
                    second_frame = self.capture()
                    second = current_identity_mask(second_frame)
                    if hs.mask_f1(first, second) >= .92:
                        observations[account.index] = (first, second)
                        observed_frames[account.index] = (frame, second_frame)
                except hs.PendingError as exc:
                    if '需要登录' not in str(exc):
                        raise
            first, matrix, gap = joint_identity_assignment(
                {slot: row[0] for slot, row in observations.items()}, identities)
            second, second_matrix, second_gap = joint_identity_assignment(
                {slot: row[1] for slot, row in observations.items()}, identities)
            if first and first == second:
                joint_names = first
                for slot in self._session_identity:
                    account = self.accounts[slot-1]
                    if account.name in aliases and aliases[account.name] != first[slot]:
                        raise hs.PendingError('严格单帧身份与联合身份冲突，未关联断点')
                    aliases[account.name] = first[slot]
                self.event('generic_team_joint_identity_verified', mapping=first,
                           matrix=matrix, second_matrix=second_matrix,
                           assignment_gap=gap, second_assignment_gap=second_gap,
                           observations=2, content_clicks=self.content_click_count)
            else:
                unresolved = [slot for slot in self._session_identity if self.accounts[slot-1].name not in aliases]
                for slot in unresolved:
                    self._unavailable_reasons[slot] = 'identity_material_missing'
                    account = self.accounts[slot-1]
                    evidence = [str(self.save_raw_evidence(image, account, f"generic_identity_ambiguous_{index}", "pending"))
                                for index, image in enumerate(observed_frames[slot], 1)]
                    self.event("generic_identity_unverified", account, evidence=evidence,
                               first_scores=matrix.get(slot, {}), second_scores=second_matrix.get(slot, {}))
                if not allow_unavailable:
                    raise hs.PendingError(f'标签{unresolved}两帧实名联合对账未唯一确认，不能开始组队')
                for slot in unresolved:
                    self._session_identity.pop(slot)
                    self.event('generic_tab_unavailable', self.accounts[slot-1], reason='identity_material_missing')
        if allow_unavailable and not leaders_only and (slots[0] not in self._session_identity or len(self._session_identity) < 3):
            raise hs.PendingError("队长和另外两个可核对标签不足；未发送邀请")
        selected_names = [self.accounts[slot-1].name for slot in self._session_identity]
        if all(name in aliases for name in selected_names):
            if len(set(aliases.values())) != len(aliases):
                raise hs.PendingError('组队标签身份重复，未关联断点')
            previous_state = self.state
            self.state = hs.StateStore(selected_names, directory=self.shared_state_directory, aliases=aliases)
            for name in selected_names:
                for key, action in previous_state.accounts[name].get('actions', {}).items():
                    current_actions = self.state.accounts[name].setdefault('actions', {})
                    if key not in current_actions:
                        current_actions[key] = action
                    elif (action.get('status') == 'pending' or current_actions[key].get('status') == 'pending') and action != current_actions[key]:
                        raise hs.PendingError('匿名与实名同名动作存在冲突，保留两处原记录，先对账：' + key)
            # A no-content probe may restore old pending intent in memory, but
            # identity observation must not publish that restoration to disk.
            if not self.no_content_clicks:
                for name in selected_names:
                    self.state.save_account(name)
            self._bound_identity_names = joint_names or {slot: aliases[self.accounts[slot-1].name] for slot in self._session_identity}
            self._bound_identity_masks = {slot: observations[slot][1] for slot in self._bound_identity_names}
            self.event('generic_team_checkpoints_bound', slots=list(self._session_identity), observations=2,
                       mapping=self._bound_identity_names)
        self.state.global_state["tab_map"] = {self.accounts[slot-1].name: self.accounts[slot-1].tab_x
                                             for slot in self._session_identity}
        if not self.no_content_clicks:
            self.state.save_global()

    def _bind_automatic_session(self, slots, allow_unavailable, leaders_only, tab_positions):
        from hyol_accounts import observe_accounts, register_observations, game_session_token
        from hyol_checkpoints import current_identity_crop, encode_mask
        self.bind_window(wait=True, timeout_seconds=15)
        context=getattr(self,'run_context',None)
        shared=context.binding if context else None
        if (isinstance(shared,dict) and (shared.get('prepared_leaf_verified') is True or shared.get('prepared_activity_accounts'))
                and isinstance(shared.get('observations'),dict)
                and shared['observations'] and context.layout_matches(self.capture())):
            from hyol_accounts import is_captain
            centers=shared['centers'];observations=shared['observations'];frames=shared['frames']
            unavailable=shared['unavailable'];mapping=shared['mapping'];refs=shared['refs']
            self._observed_captain_tabs={centers[i-1]:is_captain(self,frames[i]) for i in mapping}
            self.event('account_scan_reused',count=len(mapping),content_clicks=0,
                       detail='复用本轮检查回村的三帧身份映射；每次操作继续核对新画面')
        else:
            centers, observations, frames, unavailable = observe_accounts(self)
            mapping, refs = register_observations(self, observations)
        if any(slot > len(centers) for slot in slots):
            raise hs.PendingError('所选账号标签已不存在，原断点保留')
        if tab_positions is not None and (set(tab_positions) != set(range(1,len(centers)+1))
                                         or set(tab_positions.values()) != set(centers)):
            raise hs.PendingError('恢复映射不是当前位置的完整排列')
        # The fresh preparation measured the physical browser layout. Keep
        # highlight detection on that same layout instead of the old INI Xs.
        self.config['TabXs'] = ','.join(map(str, centers))
        self.session_token = game_session_token(self)
        self.accounts[:] = [hs.Account(i,f'当前窗口标签{i}',
                            tab_positions[i] if tab_positions is not None else x)
                         for i,x in enumerate(centers,1)]
        self.by_name = {a.name:a for a in self.accounts}
        physical = {a.index:centers.index(a.tab_x)+1 for a in self.accounts}
        self._bound_identity_names = {slot:mapping[p] for slot,p in physical.items() if p in mapping}
        self._bound_identity_masks = {slot:observations[p][-1] for slot,p in physical.items() if p in mapping}
        self._known_identity_materials = refs
        self._unavailable_reasons = {slot:unavailable[p] for slot,p in physical.items() if p in unavailable}
        self._tab_title_masks = {slot:self._title_mask(frames[p],self.accounts[slot-1].tab_x)
                                 for slot,p in physical.items() if p in mapping}
        self._session_identity = {slot:current_identity_crop(frames[p], self.templates)
                                  for slot,p in physical.items() if p in mapping}
        missing = set(slots)-set(self._bound_identity_names)
        if missing and not allow_unavailable:
            raise hs.PendingError(f'所选标签{sorted(missing)}身份未确认，未开始组队')
        aliases = {self.accounts[slot-1].name:key for slot,key in self._bound_identity_names.items()}
        self.available_account_names = set(aliases)
        self.joint_identity_bound = set(aliases)
        self.state = hs.StateStore(list(aliases),directory=self.shared_state_directory,aliases=aliases,
                                   restore_history=getattr(self, 'run_context', None) is None)
        self.account_logs = {name:hs.JsonlLogger(hs.LOGS/'accounts'/(hs.slug(key)+'.jsonl'))
                             for name,key in aliases.items()}
        for slot,key in self._bound_identity_names.items():
            name = self.accounts[slot-1].name
            self.state.accounts[name]['identity_mask'] = encode_mask(self._bound_identity_masks[slot])
            self.state.accounts[name]['account_id'] = hs.Account(slot,key,0).account_id
            if not self.no_content_clicks:
                self.state.save_account(name)
        self.state.global_state['tab_map'] = {a.name:a.tab_x for a in self.accounts if a.index in self._bound_identity_names}
        if not self.no_content_clicks:
            self.state.save_global()
        self.active = None
        self._automatic_captain_slots = tuple(a.index for a in self.accounts
                         if a.index in self._bound_identity_names
                         and self._observed_captain_tabs.get(a.tab_x))
        if getattr(self, 'run_context', None):
            self.run_context.remember(centers,mapping,observations,frames,refs,unavailable)
        self.event('generic_team_checkpoints_bound',slots=list(self._bound_identity_names),
                   mapping=self._bound_identity_names,observations=3,content_clicks=self.content_click_count)

    def _friend_row_images(self, frame: Image.Image) -> dict[int, Image.Image]:
        return {FRIEND_ROW_Y + i * FRIEND_ROW_STEP:
                frame.crop((FRIEND_ROW_X, FRIEND_ROW_Y + i * FRIEND_ROW_STEP,
                            FRIEND_ROW_X + FRIEND_ROW_WIDTH, FRIEND_ROW_Y + i * FRIEND_ROW_STEP + 36))
                for i in range(8)}

    def _open_friends(self, leader: hs.Account) -> Image.Image:
        self.switch_account(leader)
        self.prepare_main(leader)
        if self.template('mail_ready_anchor.bmp',(500,200,1420,850))[0]:
            if not self.click_template(leader,'mail_close.bmp',(1301,249,1401,349),20,20,
                    'generic_friends_close_mail',['mail_ready_anchor.bmp'],threshold=.98):
                raise hs.PendingError('好友入口前邮件面板关闭未确认')
            if not self.wait_absent('mail_ready_anchor.bmp',(500,200,1420,850),5):
                raise hs.PendingError('邮件面板尚未关闭，未点击好友')
        if self.template('friends_panel_close.bmp',(1870,260,1920,350))[0]:
            return self.capture()
        core = self.click_template(leader, 'friends_toolbar_core_20261030.bmp', (1710,70,1740,125),
                                  8,10,'generic_open_friends',['anchor_top_menu.bmp'],threshold=.98)
        if not core and not self.click_template(leader, "friends_toolbar.bmp", (1715,70,1770,120),
                                   12, 11, "generic_open_friends", ["anchor_top_menu.bmp"], threshold=.88):
            raise hs.PendingError("队长好友按钮未确认")
        if not self.wait_template("friends_panel_close.bmp", (1870,260,1920,350), 5):
            raise hs.PendingError("队长好友列表未打开")
        self.clear_transient_notifications(leader, self.capture())
        return self.capture()

    def _close_friends(self, leader: hs.Account) -> None:
        if not self.click_template(leader, "friends_panel_close.bmp", (1870,260,1920,350),
                                   10, 10, "generic_close_friends", ["friends_panel_close.bmp"]):
            raise hs.PendingError("好友列表关闭按钮未确认")
        if not self.wait_absent("friends_panel_close.bmp", (1870,260,1920,350), 5):
            raise hs.PendingError("好友列表未关闭")

    def _scroll_friends(self, leader: hs.Account, delta: int) -> None:
        self.content_guard(leader, ["friends_panel_close.bmp"])
        hs.Win32.wheel(*self.screen_point(1830, 550), delta)
        self.event("generic_friends_scroll", leader, delta=delta)
        self.wait_interruptible(.25)

    def _friends_top(self, leader: hs.Account) -> Image.Image:
        for _ in range(8):
            before = hs.image_hash(self.capture().crop((1745,380,1900,750)))
            self._scroll_friends(leader, 120)
            after = self.capture()
            if hs.image_hash(after.crop((1745,380,1900,750))) == before:
                return after
        raise hs.PendingError("好友列表未确认滚动到顶部")

    def _find_visible_row(self, frame: Image.Image, slot: int) -> tuple[float, int]:
        ranked = sorted(((name_shape_score(self._session_identity[slot], row), y)
                         for y, row in self._friend_row_images(frame).items()), reverse=True)
        if ranked and ranked[0][0] >= .62 and (len(ranked) == 1 or ranked[0][0]-ranked[1][0] >= .08):
            return ranked[0]
        return (0.0, -1)

    def _scan_member_row(self, leader: hs.Account, slot: int) -> int:
        self._friends_top(leader)
        for _ in range(9):
            frame = self.capture()
            score, y = self._find_visible_row(frame, slot)
            if y >= 0:
                self._friend_name_rows[slot] = frame.crop(
                    (FRIEND_ROW_X, y, FRIEND_ROW_X + FRIEND_ROW_WIDTH, y + 36))
                return y
            before = hs.image_hash(frame.crop((1745,380,1900,750)))
            self._scroll_friends(leader, -120)
            if hs.image_hash(self.capture().crop((1745,380,1900,750))) == before:
                break
        raise hs.PendingError(f"第{slot}个标签在好友列表中未唯一匹配；未发送邀请")

    def resolve_friend_members(self, leader_slot: int, requested: tuple[int, ...] | None) -> tuple[int, ...]:
        """Resolve all member rows before creating a team or sending an invite."""
        leader = self.accounts[leader_slot-1]
        self.require_named_members((leader.name, *(self.accounts[slot-1].name for slot in requested or ())))
        frame = self._open_friends(leader)
        try:
            if requested is None:
                frame = self._friends_top(leader)
                yellow = yellow_friend_rows(frame)
                if len(yellow) < 2 or not 35 <= yellow[1] - yellow[0] <= 65:
                    raise hs.PendingError("好友列表顶部两个黄色高亮项未稳定确认；未发送邀请")
                rows = {y: frame.crop((FRIEND_ROW_X, y, FRIEND_ROW_X+FRIEND_ROW_WIDTH, y+36))
                        for y in yellow[:2]}
                mapping = assign_friend_rows(self._session_identity, rows,
                                             tuple(i for i in self._session_identity if i != leader_slot))
                self._friend_name_rows.update({slot: rows[y] for y, slot in mapping.items()})
                members = tuple(mapping[y] for y in yellow[:2])
            else:
                members = requested
                for slot in requested:
                    self._scan_member_row(leader, slot)
                mapping = {slot: slot for slot in requested}
            self.event("generic_team_friend_mapping_verified", leader, slots=list(members),
                       row_order=list(mapping), shortcut=requested is None)
            return members
        finally:
            if self.template("friends_panel_close.bmp", (1870,260,1920,350))[0]:
                self._close_friends(leader)

    def _find_member_friend_row(self, frame: Image.Image, slot: int) -> int:
        leader = self.active
        if leader is None:
            raise hs.PendingError("队长标签未绑定")
        return self._scan_member_row(leader, slot)

    def invite_member(self, team: hs.Team, leader: hs.Account, member: hs.Account) -> None:
        binding = self.require_named_members(team.members)
        action = f"team{team.index}_invite_slot{member.index}"
        accepted = f"team{team.index}_accept_slot{member.index}"
        sent = self.state.action_status(leader.name, action)
        accepted_status = self.state.action_status(member.name, accepted)
        if accepted_status == "done" and sent == "done":
            return
        if accepted_status in ("done", "pending"):
            self.reconcile_team_membership(team, leader)
            return  # Whole-room proof resolves the old action; never reaccept.
        origin = self.invite_origin_asset(binding[leader.name])
        if sent == "new":
            self.switch_account(member)
            if self.template("team_invite_question_current.bmp", (601,350,1301,750))[0]:
                raise hs.PendingError("队员标签仍有来源不明的邀请；未发送本轮邀请")
            frame = self._open_friends(leader)
            try:
                row_y = self._find_member_friend_row(frame, member.index)
                self.click_point(leader, FRIEND_ROW_X+55, row_y+18,
                                 f"team{team.index}_select_slot{member.index}", ["friends_panel_close.bmp"])
                if not self.wait_template("friend_invite_team.bmp", (1760,350,1920,850), 4):
                    raise hs.PendingError("邀请入队菜单未确认")
                if not self.click_template(leader, "friend_invite_team.bmp", (1760,350,1920,850),
                                           30, 11, action, ["friend_invite_team.bmp"], irreversible=True):
                    raise hs.PendingError("邀请入队按钮消失")
            finally:
                if self.template("friends_panel_close.bmp", (1870,260,1920,350))[0]:
                    self._close_friends(leader)
        self.expected_invitation_members.add(member.name)
        try:
            self.switch_account(member)
            if not self.wait_template(origin, (601,350,1301,750), 30, threshold=.98):
                self.reconcile_team_membership(team, leader)
                return
            for sample in range(2):
                frame = self.capture()
                if (not self.template(origin, (601,350,1301,750), frame, threshold=.98)[0]
                        or not self.template("team_invite_accept_yes.bmp", (751,500,1051,700), frame)[0]):
                    evidence = self.save_raw_evidence(frame, member, "generic_invite_origin_unverified", "pending")
                    raise hs.PendingError(f"邀请未与实名队长连续核实，保留原弹窗：{evidence}")
                if sample == 0:
                    self.wait_interruptible(.35)
            self._approved_invitation = (member.name, origin)
            if not self.click_template(member, "team_invite_accept_yes.bmp", (751,500,1051,700),
                                       50,16,accepted,[origin,"team_invite_accept_yes.bmp"],irreversible=True):
                raise hs.PendingError("邀请接受按钮消失")
            if not self.wait_absent(origin, (601,350,1301,750), 5, threshold=.98):
                raise hs.PendingError("邀请确认框仍在，接受结果待核实；不重复接受")
        finally:
            self._approved_invitation = None
            self.expected_invitation_members.discard(member.name)
        # The clicked Yes and vanished modal remain pending until all selected
        # roles positively appear in the same room on each member's tab.

    def invite_origin_asset(self, canonical_name: str) -> str:
        config = hs.read_config(hs.CONFIG_PATH)
        assets = {config.get(key.replace("VerifiedLeader", "InviteLeaderTemplate"), "")
                  for key, name in config.items() if key.endswith("VerifiedLeader") and name == canonical_name}
        assets = {name for name in assets if name and (hs.ASSETS/name).is_file()}
        if len(assets) != 1:
            raise hs.PendingError("实名队长缺少唯一邀请正文素材，未发送或重复邀请")
        return assets.pop()

    def _inspect_team_room(self, team: hs.Team, member: hs.Account, *, owner=False,
                           allow_partial=False) -> tuple[str, tuple[str, ...]]:
        binding = self.require_named_members(team.members)
        from hyol_checkpoints import decode_mask
        references = {name: decode_mask(row["mask"], tuple(row["shape"]))
                      for name, row in self._known_identity_materials.items()}
        self.switch_account(member)
        self.prepare_main(member)
        self.open_team_room(member, f"team{team.index}_inspect_member{member.index}")
        if not self.wait_template("team_room_close.bmp", (1200,260,1280,340), 6):
            raise hs.PendingError("组队房间未打开")
        result, failure = None, None
        try:
            for sample in range(3):
                frame = self.capture()
                if owner and not self.template("team_dissolve_button.bmp", (1060,315,1160,380), frame, threshold=.98)[0]:
                    raise hs.PendingError("当前实名队长未正向显示解散按钮，原队长身份不能关联")
                assigned = room_member_assignment(frame, references, tuple(binding.values()))
                if binding[member.name] not in assigned:
                    raise hs.PendingError("房间缺少当前实名成员，不能关联原组队动作")
                if not allow_partial and set(assigned) != set(binding.values()):
                    raise MembershipIncomplete("本队已识别成员尚不齐全；原邀请/接受继续待处理")
                if result is not None and result != assigned:
                    raise hs.PendingError("组队房间成员三帧不一致，保留原邀请")
                result = assigned
                if sample < 2:
                    self.wait_interruptible(.35)
            evidence = self.save_raw_evidence(frame, member, "generic_team_room_check", "verified")
        except hs.PendingError as exc:
            failure = exc
            evidence = self.save_raw_evidence(self.capture(), member, "generic_team_room_check", "pending")
        finally:
            if not self.click_template(member, "team_room_close.bmp", (1200,260,1280,340), 20,20,
                                       "generic_close_verified_room", ["team_room_close.bmp"]):
                raise hs.PendingError("队伍房间无法安全关闭")
            if not self.wait_until_main(6):
                raise hs.PendingError("队伍房间关闭后未回村")
        if failure:
            raise failure
        event = "generic_team_partial_members_verified" if allow_partial else "generic_team_members_verified"
        self.event(event, member, slots=[self.by_name[name].index for name in team.members if binding[name] in result],
                   evidence=str(evidence), canonical_members=list(result))
        return str(evidence), result

    def inspect_team_room(self, team: hs.Team, member: hs.Account, *, owner=False) -> str:
        return self._inspect_team_room(team, member, owner=owner)[0]

    def resume_unissued_invites(self, team: hs.Team, leader: hs.Account) -> None:
        """Continue an interrupted formation only from a proved partial room."""
        binding = self.require_named_members(team.members)
        _evidence, present = self._inspect_team_room(team, leader, owner=True, allow_partial=True)
        present_members = [name for name in team.members if binding[name] in present]
        missing = [self.by_name[name] for name in team.members if name not in present_members]
        # An absent role with any old send/accept intent stays unresolved.
        # Even another new invite must wait until the old member is observed.
        for member in missing:
            if (self.state.action_status(leader.name, f"team{team.index}_invite_slot{member.index}") != "new"
                    or self.state.action_status(member.name, f"team{team.index}_accept_slot{member.index}") != "new"):
                raise MembershipIncomplete("原已发送或已接受成员未在房间核实，不重复邀请，也不追加其他成员")
        for name in present_members:
            if name == leader.name:
                continue
            _evidence, observed = self._inspect_team_room(team, self.by_name[name], allow_partial=True)
            if observed != present:
                raise hs.PendingError("部分队伍在已入队标签上不一致，未发送余下邀请")
        for member in missing:
            self.invite_member(team, leader, member)

    def verify_team_room(self, team: hs.Team, leader: hs.Account) -> None:
        self.inspect_team_room(team, leader, owner=True)

    def reconcile_team_membership(self, team: hs.Team, leader: hs.Account) -> None:
        self.require_named_members(team.members)
        evidence = {name: self.inspect_team_room(team, self.by_name[name], owner=name == leader.name)
                    for name in team.members}
        for name in team.members:
            member = self.by_name[name]
            actions = ((leader, f"team{team.index}_create"),) if name == leader.name else (
                (leader, f"team{team.index}_invite_slot{member.index}"),
                (member, f"team{team.index}_accept_slot{member.index}"),
            )
            for actor, action in actions:
                if self.state.action_status(actor.name, action) != "done":
                    self.commit_action(actor, action, "实名全队在每个成员房间连续三帧一致；原组队动作已核实")
        self.state.global_state.setdefault("generic_membership_evidence", {})[str(team.index)] = {
            "members": list(team.members), "identity_binding": self.require_named_members(team.members),
            "evidence": evidence, "checked_at": hs.now_iso(),
        }
        self.state.save_global()

    def _strong_all_member_rewards_zero(self, team: hs.Team, leader: hs.Account,
                                        spec: hs.TeamActivitySpec) -> bool:
        counts, evidence = {}, {}
        for name in team.members:
            member = self.by_name[name]
            self.switch_account(member)
            frame = self.capture()
            if not self.template(spec.page_anchor, (250,80,1720,950), frame)[0]:
                self.open_strong_approach(team, member, spec)
                frame = self.capture()
            counts[member.index] = self._read_team_reward_remaining(member, spec, frame)
            evidence[str(member.index)] = str(self.save_raw_evidence(
                frame, member, f"team{team.index}_strong_remaining_{counts[member.index]}", "verified"))
            self._close_team_activity_page(member, spec)
            if not self.wait_until_main(8):
                raise hs.PendingError(f"第{member.index}个标签读取强者次数后未回村")
        self.state.global_state.setdefault("generic_strong_counts", {})[str(team.index)] = {
            "counts": {str(slot): value for slot, value in counts.items()}, "evidence": evidence,
            "checked_at": hs.now_iso(), "members": list(team.members),
        }
        self.state.save_global()
        self.event("generic_strong_all_member_counts", leader, counts=counts, evidence=evidence)
        return all(value == 0 for value in counts.values())

    def _member_attempt_record(self, team: hs.Team, spec: hs.TeamActivitySpec, attempt: int) -> dict:
        binding = self.require_named_members(team.members)
        records = self.state.global_state.setdefault("generic_member_attempt_returns", {})
        record = records.setdefault(f"{team.index}:{spec.key}:{attempt}", {
            "members": list(team.members), "identity_binding": binding, "home_evidence": {},
            "status": "pending", "source_day": self.state.day,
        })
        if record.get("members") != list(team.members) or record.get("identity_binding") != binding:
            raise hs.PendingError("逐场队员收尾角色与原队伍不一致，保留原战斗记录")
        return record

    def _member_attempt_complete(self, team: hs.Team, spec: hs.TeamActivitySpec, attempt: int) -> bool:
        record = self._member_attempt_record(team, spec, attempt)
        return record.get("status") == "done" and TeamRunner._member_evidence(record.get("home_evidence"), team.members)

    def _next_team_attempt_index(self, team, leader, spec):
        attempt = super()._next_team_attempt_index(team, leader, spec)
        for previous in range(1, attempt):
            if not self._member_attempt_complete(team, spec, previous):
                raise hs.PendingError(f"{spec.label}第{previous}场队员结果、奖励或回村尚未齐全；不开始下一场")
        return attempt

    def _wait_member_result_page(self, member: hs.Account, spec: hs.TeamActivitySpec) -> None:
        unknown_since = time.monotonic()
        watchdog = hs.BattleWatchdog.from_config(getattr(self, 'config', {}), started_at=unknown_since)
        while True:
            self.check_stop()
            frame = self.capture()
            if (self._team_result_template(frame) or self._team_reward_result_visible(frame)
                    or spec.key == "sealed" and self.template("sealed_victory.bmp", (790,330,1160,630), frame)[0]):
                return
            hs.check_battle_watchdog(self, watchdog, member, frame,
                                    f'{spec.key}_member_{member.index}', mode=spec.key)
            if self._battle_auto_on_visible(frame):
                unknown_since = time.monotonic()
            elif self.template("battle_auto_off.bmp", hs.BATTLE_AUTO_ROI, frame)[0]:
                raise hs.PendingError(f"第{member.index}个标签自动战斗已关闭，逐场收尾待处理")
            elif self.main_ready(frame) or self.template(spec.page_anchor, (250,80,1720,950), frame)[0]:
                return
            elif time.monotonic() - unknown_since >= 60:
                evidence = self.save_raw_evidence(frame, member, "generic_member_result_unknown", "pending")
                raise hs.PendingError(f"第{member.index}个标签60秒未确认战斗或结算页面：{evidence}")
            self.wait_interruptible(3)

    def settle_member_attempt(self, team: hs.Team, leader: hs.Account, spec: hs.TeamActivitySpec, attempt: int) -> None:
        if self._member_attempt_complete(team, spec, attempt):
            return
        base = self._team_attempt_base(team, spec, attempt) if spec.reward_counter_prefix else f"team{team.index}_{spec.key}"
        result_action = f"{base}_complete" if spec.reward_counter_prefix else f"{base}_result_confirm"
        if spec.key == "wanted":
            result_action = f"{base}_auto"
        elif spec.key == "sealed":
            result_action = f"sealed_ninja_attempt_{attempt}"
        if self.state.action_status(leader.name, result_action) != "done":
            raise hs.PendingError(f"{spec.label}第{attempt}场队长结果仍未知，不补写成员完成")
        if not self._team_members_auto_complete(team, spec, attempt):
            raise hs.PendingError(f"{spec.label}第{attempt}场缺少全队自动战斗证据，不补写队员完成")
        record = self._member_attempt_record(team, spec, attempt)
        self.state.save_global()
        for name in team.members:
            if record["home_evidence"].get(name):
                continue
            member = self.by_name[name]
            self.switch_account(member)
            self._wait_member_result_page(member, spec)
            frame = self.return_member_to_leaf(team, member, spec.key, attempt_index=attempt)
            evidence = self.save_raw_evidence(frame, member, f"team{team.index}_{spec.key}_attempt{attempt}_home", "verified")
            record["home_evidence"][name] = str(evidence)
            record["updated_at"] = hs.now_iso()
            self.state.save_global()
            self.event("generic_team_member_attempt_home", member, team=team.index, task=spec.key,
                       attempt=attempt, evidence=str(evidence))
        record.update(status="done", finished_at=hs.now_iso())
        self.state.save_global()
        self.switch_account(leader)

    def resume_completed_member_attempts(self, team, leader, spec):
        if spec and spec.key == "sealed":
            for attempt in (1, 2):
                if self.state.action_status(leader.name, f"sealed_ninja_attempt_{attempt}") != "done":
                    break
                self.settle_member_attempt(team, leader, spec, attempt)
            return
        if not spec or not spec.reward_counter_prefix:
            return
        for attempt in range(1, spec.reward_counter_max+1):
            if self.state.action_status(leader.name, f"{self._team_attempt_base(team, spec, attempt)}_complete") != "done":
                break
            self.settle_member_attempt(team, leader, spec, attempt)

    def team_task_in_flight(self, team, leader, spec) -> bool:
        if not spec:
            return False
        if spec.reward_counter_prefix:
            for attempt in range(1, spec.reward_counter_max+1):
                base = self._team_attempt_base(team, spec, attempt)
                if self.state.action_status(leader.name, f"{base}_start") in ("pending", "done"):
                    if (self.state.action_status(leader.name, f"{base}_complete") != "done"
                            or not self._member_attempt_complete(team, spec, attempt)):
                        return True
            return False
        action = f"team{team.index}_{spec.key}_{'auto' if spec.key == 'wanted' else 'start'}"
        return self.state.action_status(leader.name, action) in ("pending", "done") and (
            not self._team_activity_completion_verified(team, leader, spec)
            or not self._member_attempt_complete(team, spec, 1))

    def sealed_task_in_flight(self, leader, team=None) -> bool:
        if any(isinstance(row, dict) and row.get("status") == "pending"
               and key.startswith(("sealed_ninja_attempt_", "sealed_confirm_", "sealed_result_"))
               for key, row in self.state.accounts[leader.name].get("actions", {}).items()):
            return True
        if team:
            spec = replace(hs.TEAM_ACTIVITY_BY_KEY["group_instance"], key="sealed", reward_counter_prefix=None)
            return any(self.state.action_status(leader.name, f"sealed_ninja_attempt_{attempt}") == "done"
                       and not self._member_attempt_complete(team, spec, attempt) for attempt in (1, 2))
        return False

    def _resume_team_instance_battle(self, team, leader, frame=None, start_action=None, attempt_index=None):
        spec = hs.TEAM_ACTIVITY_BY_KEY["group_instance"]
        attempt = attempt_index or self._next_team_attempt_index(team, leader, spec)
        super()._resume_team_instance_battle(team, leader, frame, start_action, attempt)
        self.settle_member_attempt(team, leader, spec, attempt)

    def _resume_generic_team_battle(self, team, leader, spec, frame=None, start_action=None, attempt_index=None):
        attempt = attempt_index or (self._next_team_attempt_index(team, leader, spec) if spec.reward_counter_prefix else 1)
        super()._resume_generic_team_battle(team, leader, spec, frame, start_action, attempt)
        self.settle_member_attempt(team, leader, spec, attempt)

    def _poll_generic_team_activity(self, team, leader, spec, attempt_index=None):
        attempt = attempt_index or (self._next_team_attempt_index(team, leader, spec) if spec.reward_counter_prefix else 1)
        super()._poll_generic_team_activity(team, leader, spec, attempt)
        self.settle_member_attempt(team, leader, spec, attempt)

    def run_generic_team_activity(self, team, leader, spec):
        action = f"team{team.index}_{spec.key}_start"
        if self.state.action_status(leader.name, action) in ("pending", "done"):
            frame = self.switch_account(leader)
            if (self._team_battle_start_visible(frame) or self._battle_auto_on_visible(frame)
                    or self.template("battle_auto_off.bmp", hs.BATTLE_AUTO_ROI, frame)[0]
                    or self._team_result_template(frame) or self._team_reward_result_visible(frame)):
                self._poll_generic_team_activity(team, leader, spec)
                return
        super().run_generic_team_activity(team, leader, spec)

    def run_verified_wanted(self, team, leader):
        super().run_verified_wanted(team, leader)
        self.settle_member_attempt(team, leader, hs.TEAM_ACTIVITY_BY_KEY["wanted"], 1)

    def return_member_to_leaf(self, team: hs.Team, member: hs.Account, task: str, *, attempt_index: int | None = None) -> Image.Image:
        from hyol_navigation import return_to_leaf
        self.switch_account(member)
        for _ in range(4):
            frame = self.capture()
            result = self._team_result_template(frame)
            if task == "sealed" and self.template("sealed_victory.bmp", (790,330,1160,630), frame)[0]:
                result = "team_result_confirm.bmp"
            reward = self._team_reward_result_visible(frame)
            if result or reward:
                name = result or hs.TEAM_REWARD_RESULT_TEMPLATE
                task_key = f"{task}_attempt_{attempt_index}" if attempt_index is not None else task
                action = f"team{team.index}_{task_key}_member{member.index}_{'result' if result else 'reward'}"
                if self.state.action_status(member.name, action) == "pending":
                    raise hs.PendingError(f"第{member.index}个标签结算结果未知")
                if self.state.action_status(member.name, action) == "done":
                    raise hs.PendingError(f"第{member.index}个标签结算按钮仍可见，拒绝重复点击")
                if not self.click_template(member, name, hs.TEAM_BATTLE_RESULT_ROI,
                                           55 if result and name.startswith("team_") else 64,
                                           18 if result and name.startswith("team_") else 19,
                                           action, [name], irreversible=True):
                    raise hs.PendingError(f"第{member.index}个标签结算按钮消失")
                if not self.wait_absent(name, hs.TEAM_BATTLE_RESULT_ROI, 6):
                    raise hs.PendingError(f"第{member.index}个标签结算按钮未消失")
                self.commit_action(member, action, "队员结算按钮已确认消失")
                continue
            if self.template("team_instance_close.bmp", (1370,240,1460,330), frame)[0]:
                if not self.click_template(member, "team_instance_close.bmp", (1370,240,1460,330),
                                           20, 20, f"team{team.index}_{task}_member{member.index}_close",
                                           ["team_instance_close.bmp"]):
                    raise hs.PendingError("队员副本选择页关闭按钮消失")
                continue
            if task == "strong_approach" and self.template("strong_approach_anchor.bmp", (250,80,1720,950), frame)[0]:
                self._close_team_activity_page(member, hs.TEAM_ACTIVITY_BY_KEY[task])
                continue
            break
        return_to_leaf(self, member)
        frame = self.capture()
        if not self.main_ready(frame):
            raise hs.PendingError(f"第{member.index}个标签未确认木叶主界面")
        return frame


class _FormationProbe:
    """Reuse the checked ninja-page flow with the selected team account."""

    def __init__(self, scheduler: SixTabScheduler, account: hs.Account, formation: str, fast_preset_point=None):
        self.scheduler = scheduler
        self.account = account
        self.formation = formation
        self.fast_preset_point = fast_preset_point

    def visible(self, name, roi=hs.VIEW, frame=None, threshold=.92):
        return self.scheduler.template(name, roi, frame, threshold=threshold)[0]

    def click(self, name, roi, dx, dy, label, anchors, irreversible=False, threshold=.90, anchor_threshold=.90):
        if not self.scheduler.click_template(self.account, name, roi, dx, dy, label, anchors,
                                             irreversible=irreversible, threshold=threshold,
                                             anchor_threshold=anchor_threshold):
            raise hs.PendingError(f"阵容按钮未确认：{name}")

    def wait(self, name, roi, seconds=12):
        if not self.scheduler.wait_template(name, roi, seconds, threshold=.90):
            raise hs.PendingError(f"阵容页面未确认：{name}")

    def event(self, name, **fields):
        self.scheduler.event(name, self.account, **fields)


class TeamRunner:
    def __init__(self, selection: TeamSelection, event_sink=None, managed_enabled: bool = True,
                 formation: str = 'keep'):
        self.selection = selection
        if formation not in ('keep', 'main', 'fast'):
            raise ValueError('请选择保持不变、主力或速刷阵容')
        self.formation = formation
        if not isinstance(managed_enabled,bool):
            raise ValueError('最后托管选项必须为开启或关闭')
        self.managed_enabled = managed_enabled
        self.scheduler = SixTabScheduler(event_sink)
        self.team: hs.Team | None = None
        self.fast_points: dict[int, tuple[int, int]] = {}
        self.legacy_wanted_unverified = False

    def _run_record(self, team: hs.Team) -> dict:
        observed = getattr(self.scheduler, "_session_identity", None)
        if observed is not None and any(self.scheduler.by_name[name].index not in observed for name in team.members):
            raise hs.PendingError("原队成员标签本轮未全部核实身份，不能继续收尾")
        runs = self.scheduler.state.global_state.setdefault("generic_team_runs", {})
        aliases = getattr(self.scheduler.state, "aliases", {})
        binding = {name: aliases[name] for name in team.members} if all(name in aliases for name in team.members) else None
        record = runs.setdefault(str(team.index), {
            "members": list(team.members), "leader_slot": self.selection.leader,
            "member_slots": [self.scheduler.by_name[name].index for name in team.members[1:]],
            "selected_tasks": list(self.selection.tasks), "tasks": {}, "managed": {}, "phase": "tasks",
            "source_day": getattr(self.scheduler.state, "day", datetime.now().date().isoformat()),
            "base_index": team.index % 10000, "identity_binding": binding,
            "managed_enabled": getattr(self, "managed_enabled", True),
        })
        if record.get("members") != list(team.members):
            raise hs.PendingError("队伍收尾断点成员与当前身份不一致，保留原记录")
        if record.get("identity_binding") and record["identity_binding"] != binding:
            raise hs.PendingError("队伍收尾断点角色身份与当前绑定不一致")
        if record.get("selected_tasks") != list(self.selection.tasks):
            raise hs.PendingError("本队已有其他任务选择的断点，须先核实原队伍收尾，不能覆盖")
        return record

    def _save_run(self, team: hs.Team) -> None:
        self._run_record(team)["updated_at"] = hs.now_iso()
        self.scheduler.state.save_global()

    def _resume_finishing_team(self) -> hs.Team | None:
        """Find the same bound team before opening Friends or rebuilding it."""
        candidates = []
        for key, record in self.scheduler.state.global_state.get("generic_team_runs", {}).items():
            if record.get("leader_slot") != self.selection.leader:
                continue
            if not self.selection.friend_shortcut and sorted(record.get("member_slots", [])) != sorted(self.selection.members):
                continue
            if record.get("selected_tasks") != list(self.selection.tasks):
                if record.get("phase") != "done":
                    raise hs.PendingError("原队伍尚未完成；须先恢复原任务选择，不能覆盖未决周期")
                continue
            if record.get("phase") not in ("dissolve", "managed", "done"):
                continue
            if record.get("phase") == "done" and record.get("source_day") != getattr(self.scheduler.state, "day", datetime.now().date().isoformat()):
                continue  # Finishing yesterday's run never completes today's request.
            members = tuple(record.get("members", ()))
            if len(members) not in (2, 3) or any(name not in self.scheduler.by_name for name in members):
                raise hs.PendingError("组队收尾断点成员无效")
            candidates.append(hs.Team(int(key), members))
        if len(candidates) > 1:
            raise hs.PendingError("存在多个同队长收尾断点，不能猜测队员")
        return candidates[0] if candidates else None

    def _restore_finishing_runs(self) -> None:
        """Carry the original unfinished closing phase, with verified identities."""
        s = self.scheduler
        directory = getattr(s.state, "directory", None)
        if directory is None:
            return
        from hyol_checkpoints import read_checkpoint
        current = s.state.global_state.setdefault("generic_team_runs", {})
        seen = set(current)
        today = getattr(s.state, "day", datetime.now().date().isoformat())
        restored = False
        transfers = {}
        for path in sorted(directory.glob("????-??-??.json"), reverse=True):
            if path.stem >= today:
                continue
            history = read_checkpoint(path)
            if history and history.get("date") != path.stem:
                raise hs.PendingError(f"历史组队断点日期与文件不一致，保留原阶段：{path}")
            for key, old in history.get("generic_team_runs", {}).items():
                if key in seen:
                    continue
                seen.add(key)  # A later done record supersedes its old phase.
                if old.get("phase") not in ("tasks", "dissolve", "managed"):
                    continue
                old_slots = {old.get("leader_slot"), *old.get("member_slots", [])}
                selected_slots = {self.selection.leader, *self.selection.members}
                if not old_slots.intersection(selected_slots):
                    continue
                if (old.get("leader_slot") != self.selection.leader
                        or not self.selection.friend_shortcut and sorted(old.get("member_slots", [])) != sorted(self.selection.members)):
                    raise hs.PendingError("当前标签仍属于另一未完组队收尾，须先恢复原队伍")
                names = old.get("members", [])
                binding = old.get("identity_binding")
                aliases = getattr(s.state, "aliases", {})
                if (not binding or len(set(binding.values())) != len(names)
                        or any(name not in s.by_name or aliases.get(name) != binding.get(name) for name in names)):
                    raise hs.PendingError("跨日组队收尾缺少一致且唯一的角色绑定，旧阶段保持未决")
                if old.get("phase") == "tasks":
                    # Exact-key account restoration cannot recover completed
                    # earlier attempts and member auto/result context.  Never
                    # rebuild this room or start attempt 1 to hide old intent.
                    raise hs.PendingError(f"{old.get('source_day', path.stem)}原组队仍在任务阶段，"
                                          "缺少完整逐场上下文对账；保留原动作，不重建、不重开战")
                transfers[key] = dict(old, source_day=old.get("source_day", path.stem),
                                      resumed_from=str(path), resumed_at=hs.now_iso())
                restored = True
        if restored:
            current.update(transfers)
            s.state.save_global()

    def _new_team_cycle(self, leader: hs.Account, members: tuple[int, ...]) -> hs.Team:
        """Keep completed ledgers; a new selection uses a distinct team cycle."""
        s = self.scheduler
        ordered = sorted(members)
        base = 100 + leader.index * 100 + ordered[0] * 10 + (ordered[1] if len(ordered) == 2 else 0)
        names = (leader.name, *(s.accounts[slot-1].name for slot in ordered))
        runs = s.state.global_state.get("generic_team_runs", {})
        prior = [(int(key), value) for key, value in runs.items()
                 if value.get("base_index", int(key) % 10000) == base]
        if not prior:
            return hs.Team(base, names)
        latest_index, previous = max(prior)
        if previous.get("phase") != "done":
            return hs.Team(latest_index, names)
        self._assert_no_unresolved(hs.Team(latest_index, names))
        if (previous.get("members") != list(names)
                or not self._dissolve_complete(previous.get("dissolve", {}), names)
                or not all(self._task_complete(previous.get("tasks", {}).get(task, {}), names)
                           for task in previous.get("selected_tasks", ()))
                or previous.get("managed_enabled", True) and (
                    set(previous.get("managed", {})) != set(names)
                    or not all(row.get("status") == "done" and row.get("task_completed") is True and row.get("evidence")
                               for row in previous["managed"].values()))):
            raise hs.PendingError("旧周期尚无全队任务、解散及所选收尾证据，不能开启新队周期")
        aliases = getattr(s.state, "aliases", {})
        if not previous.get("identity_binding") or any(aliases.get(name) != previous["identity_binding"].get(name) for name in names):
            raise hs.PendingError("旧周期缺少一致且唯一的角色身份，不能关联新队周期")
        new_team = hs.Team(base + (latest_index // 10000 + 1) * 10000, names)
        record = self._run_record(new_team)
        if (previous.get("source_day") == record["source_day"]
                and previous.get("identity_binding") and previous["identity_binding"] == record["identity_binding"]
                and previous.get("members") == list(names) and not previous.get("legacy_wanted_unverified")):
            record["tasks"] = {task: dict(row, carried_from_team=latest_index)
                               for task, row in previous.get("tasks", {}).items()
                               if task in self.selection.tasks and task != "strong_approach"
                               and self._task_complete(row, names)}
        self._save_run(new_team)
        return new_team

    def _assert_no_unresolved(self, team: hs.Team, allowed: dict[str, set[str]] | None = None) -> None:
        for name in team.members:
            exempt = (allowed or {}).get(name, set())
            actions = self.scheduler.state.accounts[name].get("actions", {})
            pending = [key for key, row in actions.items()
                       if isinstance(row, dict) and row.get("status") == "pending" and key not in exempt]
            if pending:
                raise hs.PendingError(f"第{self.scheduler.by_name[name].index}个标签有未决动作，不能收尾托管：" + ", ".join(pending))

    def _selected_tasks_complete(self, team: hs.Team) -> bool:
        record = self._run_record(team)
        tasks = record.get("tasks", {})
        return not record.get("legacy_wanted_unverified") and all(
            self._task_complete(tasks.get(task, {}), team.members)
            for task in self.selection.tasks
        )

    @staticmethod
    def _member_evidence(evidence, members) -> bool:
        return isinstance(evidence, dict) and set(evidence) == set(members) and all(evidence.values())

    @classmethod
    def _task_complete(cls, task, members) -> bool:
        return (task.get("status") == "done" and task.get("task_completed") is True
                and task.get("home_returned") is True and cls._member_evidence(task.get("home_evidence"), members))

    @classmethod
    def _dissolve_complete(cls, dissolved, members) -> bool:
        return (dissolved.get("status") == "done" and dissolved.get("home_returned") is True
                and cls._member_evidence(dissolved.get("member_evidence"), members)
                and cls._member_evidence(dissolved.get("home_evidence"), members))

    def _formation(self, team: hs.Team, formation: str) -> None:
        if formation == 'keep':
            self.scheduler.event('generic_team_formation_preserved', detail='保持当前阵容，不进入忍者页面')
            return
        from hyol_daily_tasks import DailyRunner
        from hyol_navigation import return_to_leaf
        for name in team.members:
            member = self.scheduler.by_name[name]
            self.scheduler.switch_account(member)
            if not self.scheduler.main_ready():
                return_to_leaf(self.scheduler, member)
            self.scheduler.prepare_main(member)
            probe = _FormationProbe(self.scheduler, member, formation, self.fast_points.get(member.index))
            DailyRunner.verify_main_formation(probe)
            if probe.fast_preset_point is not None:
                self.fast_points[member.index] = probe.fast_preset_point

    def _check_selection_record(self, team: hs.Team) -> None:
        s = self.scheduler
        active = s.state.global_state.get("generic_active_team")
        if active is not None and active != team.index:
            raise hs.PendingError("另一组标签仍有未解散的组队断点，先对账")
        s.state.global_state["generic_active_team"] = team.index
        s.state.global_state.setdefault("generic_team_selections", {})[str(team.index)] = {
            "leader": self.selection.leader,
            "members": [s.by_name[name].index for name in team.members if name != team.members[0]],
        }
        s.state.save_global()

    def _migrate_legacy_strong_attempt(self, team: hs.Team, leader: hs.Account) -> None:
        if leader.index != 1:
            return
        old = "team0_strong_approach_attempt_1_complete"
        new = f"team{team.index}_strong_approach_attempt_1_complete"
        s = self.scheduler
        if s.state.action_status(leader.name, old) == "done" and s.state.action_status(leader.name, new) == "new":
            s.state.set_action(leader.name, new, "done", "沿用同一浏览器会话已核实的旧组队客户端第1场结果")
            s.event("generic_legacy_strong_attempt_reconciled", leader, source=old, target=new)

    def _reconcile_old_wanted(self, team: hs.Team, leader: hs.Account) -> None:
        """The former one-tab client may have completed wanted without home."""
        s = self.scheduler
        legacy = s.state.account_paths[leader.name].with_name(hs.slug(leader.name)+"_team_client.json")
        try:
            import json
            old = json.loads(legacy.read_text(encoding="utf-8"))
        except (FileNotFoundError, OSError, ValueError):
            return
        rec = old.get("wanted", {}) if isinstance(old, dict) else {}
        if not isinstance(rec, dict) or not rec.get("task_completed"):
            return
        s.switch_account(leader)
        frame = s.capture()
        if not s.main_ready(frame) or not hs.wanted_task_list_is_empty(frame):
            raise hs.PendingError("旧通缉记录已结算但未核实回村；先对账，不重新点击自动完成")
        if not rec.get("home_returned"):
            rec["home_returned"] = True
            rec["status"] = "done"
            hs.atomic_json(legacy, old)
        s._set_team_activity_status(team, leader, hs.TEAM_ACTIVITY_BY_KEY["wanted"], "done",
                                    "旧单窗口通缉已结束；队员自动战斗无本轮证据")
        self.legacy_wanted_unverified = True
        s.event("generic_legacy_wanted_home_reconciled", leader, team=team.index,
                member_auto_verified=False)

    def _prepare_team(self, team: hs.Team, leader: hs.Account) -> None:
        s = self.scheduler
        s.require_named_members(team.members)
        self._check_selection_record(team)
        create = s.state.action_status(leader.name, f"team{team.index}_create")
        membership = s.state.global_state.get("generic_membership_evidence", {}).get(str(team.index), {})
        if (create == "done" and membership.get("members") == list(team.members)
                and membership.get("identity_binding") == s.require_named_members(team.members)
                and self._member_evidence(membership.get("evidence"), team.members)
                and all(s.state.action_status(leader.name, f"team{team.index}_invite_slot{s.by_name[name].index}") == "done"
                        and s.state.action_status(name, f"team{team.index}_accept_slot{s.by_name[name].index}") == "done"
                        for name in team.members[1:])
                and any(s.sealed_task_in_flight(leader, team) if task == "sealed" else
                        s.team_task_in_flight(team, leader, hs.TEAM_ACTIVITY_BY_KEY.get(task)) for task in self.selection.tasks)):
            s.event("generic_team_battle_resumed", leader, team=team.index,
                    detail="原实名组队证据齐全，先恢复原战斗或逐员结算，不重建队伍、不打开房间遮挡战斗")
            return
        uncertain = create == "pending"
        for name in team.members[1:]:
            member = s.by_name[name]
            sent = s.state.action_status(leader.name, f"team{team.index}_invite_slot{member.index}")
            accepted = s.state.action_status(member.name, f"team{team.index}_accept_slot{member.index}")
            uncertain |= accepted == "pending" or sent in ("pending", "done") and accepted != "done"
        if uncertain:
            for name in team.members[1:]:
                member = s.by_name[name]
                sent = s.state.action_status(leader.name, f"team{team.index}_invite_slot{member.index}")
                accepted = s.state.action_status(member.name, f"team{team.index}_accept_slot{member.index}")
                if sent in ("pending", "done") and accepted == "new":
                    # Resume only the original invite. invite_member verifies
                    # its named leader twice and never sends it again.
                    s.invite_member(team, leader, member)
            # All tabs must agree before old intent is committed.
            # An incomplete room never triggers a duplicate send or create.
            try:
                s.reconcile_team_membership(team, leader)
            except MembershipIncomplete:
                s.resume_unissued_invites(team, leader)
                s.reconcile_team_membership(team, leader)
            s.ensure_team_room_type(team, leader)
            return
        s.create_verified_team(team, leader)
        for name in team.members:
            if name != leader.name:
                s.invite_member(team, leader, s.by_name[name])
        s.reconcile_team_membership(team, leader)
        s.ensure_team_room_type(team, leader)

    def _run_task(self, task: str, team: hs.Team, leader: hs.Account) -> None:
        s = self.scheduler
        spec = hs.TEAM_ACTIVITY_BY_KEY.get(task)
        if task == "sealed":
            spec = replace(hs.TEAM_ACTIVITY_BY_KEY["group_instance"], key="sealed", label="封印忍者",
                           reward_counter_prefix=None, reward_counter_max=2)
        if task == 'rescue_jinchuriki' and not hs.rescue_activity_open_now():
            s.event('rescue_calendar_pending', leader, detail='当前不在配置开放时段；不点击活动')
            raise hs.PendingError('营救人柱力未到开放时段，保留待验')
        if task == "wanted":
            self._reconcile_old_wanted(team, leader)
        resuming_battle = s.sealed_task_in_flight(leader, team) if task == "sealed" else s.team_task_in_flight(team, leader, spec)
        s.resume_completed_member_attempts(team, leader, spec)
        if not resuming_battle:
            self._formation(team, getattr(self, 'formation', 'keep'))
        if task == "group_instance":
            s.run_verified_group_instance(team, leader, target_count=None)
        elif task == "wanted":
            s.run_verified_wanted(team, leader)
        elif task == "strong_approach":
            self._migrate_legacy_strong_attempt(team, leader)
            if s._team_activity_completion_verified(team, leader, spec):
                # A saved done flag is insufficient for this new request.
                if not s._strong_all_member_rewards_zero(team, leader, spec):
                    raise hs.PendingError("强者旧完成记录与全队真实剩余次数不一致，保留待处理")
            else:
                s.run_verified_strong_approach(team, leader, spec, target_count=None)
        elif task == "sealed":
            from hyol_daily_tasks import DailyRunner
            probe = DailyRunner.for_bound_account(s, leader, (task,))
            probe.team = team
            original_event = probe.event
            def member_settlement_event(name, **fields):
                if name == "daily_sealed_completed":
                    s.settle_member_attempt(team, leader, spec, int(probe.record(task)["completed"]))
                original_event(name, **fields)
            probe.event = member_settlement_event
            probe.sealed()
            if not probe.record(task).get("zero_verified") or s.state.action_status(leader.name, "sealed_real_remaining_zero") != "done":
                raise hs.PendingError("封印游戏剩余次数尚未正向核实为0")
        else:
            spec = replace(spec, require_all_member_auto=True)
            s.run_generic_team_activity(team, leader, spec)
            if (s._team_activity_status(team, leader, spec) != "done_no_reward"
                    and not s._team_members_auto_complete(team, spec, 1)):
                raise hs.PendingError(f"{TASK_LABELS[task]}缺少本次逐成员自动战斗证据，保留原结果，不重复开战")
        if task not in ("wanted", "sealed") and not s._team_activity_completion_verified(team, leader, spec):
            raise hs.PendingError(f"{TASK_LABELS[task]}剩余次数尚未确认为0")
        home_evidence = {}
        for name in team.members:
            member = s.by_name[name]
            frame = s.return_member_to_leaf(team, member, task)
            home_evidence[name] = str(s.save_raw_evidence(frame, member, f"generic_{task}_home", "verified"))
        record = self._run_record(team)
        record["legacy_wanted_unverified"] = self.legacy_wanted_unverified
        record["tasks"][task] = {
            "status": "done", "task_completed": True, "home_returned": True,
            "home_evidence": home_evidence, "finished_at": hs.now_iso(),
        }
        self._save_run(team)
        s.event("generic_team_task_done", leader, task=task, slots=[s.by_name[name].index for name in team.members],
                home_returned=True, evidence=home_evidence)

    def run(self) -> int:
        s = self.scheduler
        lease = hs.ForegroundRuntimeLease()
        owned = False
        current = ""
        result, detail = 2, ""
        try:
            pause = manual_pause_status(hs.ROOT)
            if pause.get("paused"):
                raise hs.PendingError("F12人工暂停仍在；须先经审计恢复入口恢复")
            owned = lease.acquire()
            if not owned:
                raise hs.PendingError("已有脚本占用游戏窗口")
            s.start_f12_monitor()
            slots = (self.selection.leader, *self.selection.members) if not self.selection.friend_shortcut else tuple(range(1,7))
            s.bind_session(tuple(dict.fromkeys(slots)), allow_unavailable=self.selection.friend_shortcut)
            self._restore_finishing_runs()
            leader = s.accounts[self.selection.leader-1]
            self.team = self._resume_finishing_team()
            if self.team is None:
                # Older/cross-day actions may have no matching finish record.
                # They still prohibit opening Friends or creating another team.
                for slot in slots:
                    account = s.accounts[slot-1]
                    actions = s.state.accounts.get(account.name, {}).get("actions", {})
                    if any(isinstance(row, dict) and row.get("status") == "pending"
                           and (key.endswith("_dissolve_after_tasks") or key == "start_managed"
                                or key.startswith("start_managed_network_"))
                           for key, row in actions.items()):
                        raise hs.PendingError("旧解散或托管动作缺少本队收尾证据，先对账，不重新组队")
                requested = None if self.selection.friend_shortcut else self.selection.members
                members = s.resolve_friend_members(self.selection.leader, requested)
                self.team = self._new_team_cycle(leader, members)
                self._run_record(self.team)
                self._prepare_team(self.team, leader)
            else:
                s.event("generic_team_finish_resumed", leader, team=self.team.index,
                        phase=self._run_record(self.team)["phase"], detail="恢复组队收尾，不重复建队或邀请")
            record = self._run_record(self.team)
            record["managed_enabled"] = getattr(self, "managed_enabled", True)
            self._save_run(self.team)
            pending_tasks = []
            for task in self.selection.tasks if record["phase"] == "tasks" else ():
                current = task
                s.event("generic_team_task_started", leader, task=task)
                if self._task_complete(record.get("tasks", {}).get(task, {}), self.team.members):
                    s.event("generic_team_task_done", leader, task=task, home_returned=True,
                            evidence=record["tasks"][task]["home_evidence"],
                            detail=f"{TASK_LABELS[task]}沿用本周期已核实的全队完成证据，不重复执行")
                    continue
                try:
                    self._run_task(task, self.team, leader)
                except hs.AccountOffline:
                    raise
                except hs.PendingError as exc:
                    pending_tasks.append(task)
                    s.event('generic_team_task_pending', leader, task=task, detail=str(exc))
                    # Continue only after every member is positively back on the
                    # world page with no pending battle/start/claim intent.
                    for name in self.team.members:
                        member = s.by_name[name]
                        frame = s.switch_account(member)
                        actions = s.state.accounts[name].get('actions', {})
                        if not s.main_ready(frame) or any(row.get('status') == 'pending' for row in actions.values()):
                            raise hs.LeaseBlocked('组队未决动作或页面尚未安全释放') from exc
            if pending_tasks or not self._selected_tasks_complete(self.team):
                raise hs.PendingError('仍待处理：' + '、'.join(TASK_LABELS[t] for t in pending_tasks)
                                      if pending_tasks else "所选任务缺少全队当前完成及回村证据，保留队伍，不开始托管")
            if record["phase"] in ("tasks", "dissolve"):
                self._assert_no_unresolved(self.team, {leader.name: {f"team{self.team.index}_dissolve_after_tasks"}}
                                           if record["phase"] == "dissolve" else None)
            if record["phase"] == "tasks":
                record["phase"] = "dissolve"
                self._save_run(self.team)
            if record["phase"] == "dissolve":
                current = "dissolve"
                self._dissolve_after_tasks(self.team, leader)
                record["phase"] = "managed"
                self._save_run(self.team)
            if record["managed_enabled"]:
                current = "managed"
                self._finish_managed(self.team, leader)
                detail = "所选组队任务均已核实完成、全队回木叶并解散，逐成员托管已确认"
            else:
                current = "home"
                self._finish_without_managed(self.team)
                detail = "所选组队任务均已核实完成、全队回木叶并解散；本次未勾选最后托管"
            result = 0
            if record.get("source_day") != getattr(s.state, "day", datetime.now().date().isoformat()):
                ending = "全队回木叶、解散及托管已确认" if record["managed_enabled"] else "全队回木叶和解散已确认，本次未勾选托管"
                detail = f"已完成{record['source_day']}原组队收尾：{ending}"
        except KeyboardInterrupt:
            result, detail = 130, "F12紧急停止，断点已保留"
        except hs.AccountOffline as exc:
            detail = str(exc)
            s.event('generic_team_account_offline_skipped', exc.account, task=current,
                    detail='该成员掉线，原队伍任务保持待处理；不补人、不重新开战')
        except Exception as exc:
            detail = f"{type(exc).__name__}: {exc}"
            s.event("generic_team_pending", detail=detail, task=current)
        finally:
            s.stop.set()
            if owned:
                lease.release()
            s.event("daily_summary", result=result, task="team", current=current, detail=detail,
                    selected_slots=[s.by_name[name].index for name in self.team.members] if self.team else [],
                    finish_phase=s.state.global_state.get("generic_team_runs", {}).get(str(self.team.index), {}).get("phase", "") if self.team else "",
                    source_day=s.state.global_state.get("generic_team_runs", {}).get(str(self.team.index), {}).get("source_day", "") if self.team else "",
                    clicks=s.content_click_count)
        return result

    def _dissolve_after_tasks(self, team: hs.Team, leader: hs.Account) -> None:
        s = self.scheduler
        if not self._selected_tasks_complete(team):
            raise hs.PendingError("所选任务及全队回村未核实，不能解散后托管")
        action = f"team{team.index}_dissolve_after_tasks"
        self._assert_no_unresolved(team, {leader.name: {action}})
        if s.state.action_status(leader.name, action) == "new":
            for name in team.members:
                s.return_member_to_leaf(team, s.by_name[name], "before_dissolve")
            s.switch_account(leader)
            if not s.main_ready():
                raise hs.PendingError("解散队伍前未确认队长回村")
            s.open_team_room(leader, f"team{team.index}_open_dissolve_room")
            if not s.wait_template("team_room_close.bmp", (1200,260,1280,340), 6):
                raise hs.PendingError("解散前组队房间未打开")
            if not s.click_template(leader, "team_dissolve_button.bmp", (1060,315,1160,380),
                                    25, 10, action, ["team_room_close.bmp"], irreversible=True):
                raise hs.PendingError("队长解散按钮未确认")
            s.wait_absent("team_room_close.bmp", (1200,260,1280,340), 5)
        # Room closure alone can be an X/overlay transition.  Opening each
        # member's room and observing Create proves that nobody remains teamed.
        unteamed = {name: self._verify_unteamed(team, s.by_name[name]) for name in team.members}
        if s.state.action_status(leader.name, action) != "done":
            s.commit_action(leader, action, "全队逐成员连续三帧显示创建队伍，解散已核实")
        record = self._run_record(team)
        record["dissolve"] = {"status": "done", "member_evidence": unteamed,
                              "verified_at": hs.now_iso(), "home_returned": False}
        self._save_run(team)
        home = {}
        for name in team.members:
            member = s.by_name[name]
            frame = s.return_member_to_leaf(team, member, "dissolve")
            home[name] = str(s.save_raw_evidence(frame, member, f"team{team.index}_dissolved_home", "verified"))
        record["dissolve"].update(home_returned=True, home_evidence=home)
        s.state.global_state.pop("generic_active_team", None)
        self._save_run(team)
        s.event("generic_team_dissolved", leader, team=team.index, member_evidence=unteamed,
                detail="全队无队状态及返回木叶已确认，开始最后托管")

    def _verify_unteamed(self, team: hs.Team, member: hs.Account) -> str:
        s = self.scheduler
        frame = s.switch_account(member)
        if s.main_ready(frame):
            s.open_team_room(member, f"team{team.index}_verify_unteamed_{member.index}")
            if not s.wait_template("team_create_button.bmp", (951,650,1301,850), 6):
                raise hs.PendingError(f"第{member.index}个标签未确认创建队伍状态；不重复解散")
        for sample in range(3):
            s.check_stop()
            frame = s.capture()
            if (not s.template("team_create_button.bmp", (951,650,1301,850), frame, threshold=.98)[0]
                    or s.template("team_dissolve_button.bmp", (1060,315,1160,380), frame)[0]):
                evidence = s.save_raw_evidence(frame, member, f"team{team.index}_dissolve_unverified", "pending")
                raise hs.PendingError(f"第{member.index}个标签无队状态不明确，解散待处理：{evidence}")
            if sample < 2:
                s.wait_interruptible(.35)
        evidence = s.save_raw_evidence(frame, member, f"team{team.index}_unteamed", "verified")
        # Close the positively opened room before releasing this member's tab.
        if not s.click_template(member, "team_room_close.bmp", (1200,260,1280,340), 20,20,
                                f"team{team.index}_close_unteamed_{member.index}", ["team_create_button.bmp"]):
            raise hs.PendingError(f"第{member.index}个标签无队房间关闭按钮未确认")
        if not s.wait_until_main(8):
            raise hs.PendingError(f"第{member.index}个标签无队房间关闭后未确认主界面")
        return str(evidence)

    def _finish_managed(self, team: hs.Team, leader: hs.Account) -> None:
        s = self.scheduler
        record = self._run_record(team)
        dissolved = record.get("dissolve", {})
        if not self._selected_tasks_complete(team) or not self._dissolve_complete(dissolved, team.members):
            raise hs.PendingError("任务、全队解散或木叶回村证据未齐全，不开始托管")
        # A pending managed start can only be reconciled by observing active
        # mode, never by another start click.  Other unresolved actions block all.
        allowed = {}
        for name in team.members:
            pending_starts = {
                key for key, row in s.state.accounts[name].get("actions", {}).items()
                if isinstance(row, dict) and row.get("status") == "pending"
                and (key == "start_managed" or key.startswith("start_managed_network_"))
            }
            if pending_starts:
                member = s.by_name[name]
                frame = s.switch_account(member)
                if s.managed_active_score(frame) < .70:
                    raise hs.PendingError(f"第{member.index}个标签上次托管结果未知，不重复启动")
                allowed[name] = pending_starts
        self._assert_no_unresolved(team, allowed)
        for name in team.members:
            s.check_stop()
            member = s.by_name[name]
            s.event("generic_team_managed_started", member, team=team.index,
                    detail=f"最后托管：核实第{member.index}个游戏标签")
            rec = record.setdefault("managed", {}).setdefault(name, {})
            rec.update(status="running", last_error="")
            self._save_run(team)
            try:
                frame = s.switch_account(member)
                if s.managed_active_score(frame) < .70:
                    s.return_member_to_leaf(team, member, "managed")
                s.run_managed_gate(member)
                for action in allowed.get(name, ()):
                    if s.state.action_status(name, action) == "pending":
                        s.commit_action(member, action, "最后托管连续十帧确认，原启动结果已核实")
                evidence = s.save_raw_evidence(s.capture(), member, f"team{team.index}_managed", "verified")
                rec.update(status="done", task_completed=True, evidence=str(evidence),
                           finished_at=hs.now_iso(), last_error="")
                s.event("generic_team_managed_complete", member, team=team.index, evidence=str(evidence),
                        detail=f"第{member.index}个游戏标签已确认托管中")
            except BaseException as exc:
                rec.update(status="pending", task_completed=False, last_error=str(exc))
                s.event("generic_team_managed_pending", member, team=team.index,
                        detail=f"第{member.index}个游戏标签托管待处理：{exc}")
                raise
            finally:
                self._save_run(team)
        self._assert_no_unresolved(team)
        record["phase"] = "done"
        record["finished_at"] = hs.now_iso()
        self._save_run(team)

    def _finish_without_managed(self, team: hs.Team) -> None:
        record = self._run_record(team)
        if not self._selected_tasks_complete(team) or not self._dissolve_complete(record.get("dissolve", {}), team.members):
            raise hs.PendingError("任务、全队解散或木叶回村证据未齐全，不能完成本次任务")
        # Unchecking the option must never hide an earlier uncertain start.
        self._assert_no_unresolved(team)
        record.update(phase="done", managed_enabled=False, finished_at=hs.now_iso())
        self._save_run(team)
        self.scheduler.event("generic_team_managed_disabled", team=team.index,
                             detail="本次未勾选最后托管，全队任务、解散及木叶回村已核实")


class TeamClient(tk.Tk):
    def __init__(self):
        super().__init__()
        self.title("火影OL 六标签通用组队任务 " + VERSION)
        self.geometry("800x800+50+80")
        self.events: queue.Queue[dict] = queue.Queue()
        self.worker: threading.Thread | None = None
        self.runner: TeamRunner | None = None
        self.leader = tk.StringVar(value=TAB_LABELS[0])
        self.mode = tk.StringVar(value="手动选择队员标签")
        self.members = {i: tk.BooleanVar(value=i in (2,3)) for i in range(1,7)}
        self.managed_enabled = tk.BooleanVar(value=True)
        self.tasks = {task: tk.BooleanVar(value=task in DEFAULT_TASKS) for task in TASKS}
        self.status = tk.StringVar(value="就绪：选择队长、队员与任务")
        body = ttk.Frame(self, padding=16)
        body.pack(fill="both", expand=True)
        ttk.Label(body, text="组队任务自选", font=("Microsoft YaHei UI", 20, "bold")).pack(anchor="w")
        ttk.Label(body, text="按六个游戏标签选择；先核实好友和队伍，再执行勾选的组队活动。",
                  wraplength=740).pack(anchor="w", pady=8)
        ttk.Label(body, text="队长标签").pack(anchor="w")
        ttk.Combobox(body, textvariable=self.leader, values=TAB_LABELS, state="readonly", width=24).pack(anchor="w")
        ttk.Label(body, text="队员方式").pack(anchor="w", pady=(8,0))
        self.mode_box = ttk.Combobox(body, textvariable=self.mode,
                                     values=("手动选择队员标签", "好友列表顶部两个黄色高亮好友"),
                                     state="readonly", width=35)
        self.mode_box.pack(anchor="w")
        members_box = ttk.LabelFrame(body, text="手动选择1～2个队员标签", padding=5)
        members_box.pack(fill="x", pady=8)
        self.member_checks = []
        for i, label in enumerate(TAB_LABELS, 1):
            check = ttk.Checkbutton(members_box, text=label, variable=self.members[i])
            check.grid(row=(i-1)//3, column=(i-1)%3, sticky="w", padx=12, pady=3)
            self.member_checks.append(check)
        self.mode.trace_add("write", lambda *_: self._mode_changed())
        tasks_box = ttk.LabelFrame(body, text="按顺序执行", padding=5)
        tasks_box.pack(fill="x")
        for task in TASKS:
            ttk.Checkbutton(tasks_box, text=TASK_LABELS[task], variable=self.tasks[task]).pack(anchor="w")
        ttk.Label(body, text="强者使用主力；其他任务使用速刷／鲨鱼。全队自动战斗和剩余次数均须核实。",
                  wraplength=740).pack(anchor="w", pady=8)
        self.managed_control = ttk.Checkbutton(body,text='最后进入托管',variable=self.managed_enabled)
        self.managed_control.pack(anchor='w',pady=(0,8))
        ttk.Label(body, text="所选任务完成 → 全队回木叶 → 逐成员核实解散 → 按选项进入托管。\n"
                            "任何任务未决或成员掉线均保留断点，不进入成功收尾。",
                  wraplength=740).pack(anchor="w", pady=(0,8))
        controls = ttk.Frame(body)
        controls.pack(fill="x")
        self.start_button = ttk.Button(controls, text="开始 / 继续", command=self.start)
        self.start_button.pack(side="left")
        ttk.Button(controls, text="停止", command=self.stop).pack(side="left", padx=12)
        ttk.Label(body, textvariable=self.status, wraplength=740).pack(anchor="w", pady=8)
        from hyol_client_tools import add_diagnostic_controls
        add_diagnostic_controls(body, hs.ROOT, lambda: self.runner.scheduler if self.runner else None)
        self.log = tk.Text(body, state="disabled", wrap="word")
        self.log.pack(fill="both", expand=True)
        self.protocol("WM_DELETE_WINDOW", self.close)
        self.after(150, self.drain)

    def _mode_changed(self):
        state = "disabled" if self.mode.get().startswith("好友列表") else "normal"
        for check in self.member_checks:
            check.configure(state=state)

    def start(self):
        if self.worker and self.worker.is_alive():
            return
        try:
            leader = TAB_LABELS.index(self.leader.get())+1
            shortcut = self.mode.get().startswith("好友列表")
            members = () if shortcut else tuple(i for i, value in self.members.items() if value.get())
            tasks = tuple(task for task in TASKS if self.tasks[task].get())
            selection = TeamSelection(leader, members, shortcut, tasks)
            self.runner = TeamRunner(selection, self.events.put,managed_enabled=self.managed_enabled.get())
        except (ValueError, tk.TclError) as exc:
            self.status.set(str(exc))
            return
        self.start_button.configure(state="disabled")
        self.status.set("运行中：请保持游戏窗口可见")
        self.iconify()
        self.worker = threading.Thread(target=self.runner.run, daemon=False)
        self.worker.start()

    def stop(self):
        if self.runner:
            self.runner.scheduler.stop.set()

    def close(self):
        if self.worker and self.worker.is_alive():
            self.stop()
            self.after(200, self.close)
        else:
            self.destroy()

    def drain(self):
        try:
            while True:
                event = self.events.get_nowait()
                name = event.get("event", "")
                if name in ("generic_team_pending", "generic_team_task_pending", "generic_team_task_done",
                            "generic_team_task_started", "generic_team_finish_resumed", "generic_team_dissolved",
                            "generic_team_managed_started", "generic_team_managed_complete", "generic_team_managed_pending",
                            "generic_team_account_offline_skipped", "daily_summary"):
                    self.log.configure(state="normal")
                    self.log.insert("end", f"{datetime.now():%H:%M:%S} {event.get('detail') or name}\n")
                    self.log.see("end")
                    self.log.configure(state="disabled")
                if name in ("generic_team_dissolved", "generic_team_managed_started", "generic_team_managed_pending"):
                    self.status.set(str(event.get("detail", name)))
                if name == "daily_summary":
                    self.status.set("完成："+str(event.get('detail') or '所选任务、全队回木叶及解散均已核实') if event.get("result") == 0 else
                                    "未完成：" + str(event.get("detail", "")))
                    self.start_button.configure(state="normal")
                    self.deiconify()
        except queue.Empty:
            pass
        self.after(150, self.drain)


def main() -> int:
    TeamClient().mainloop()
    return 0

