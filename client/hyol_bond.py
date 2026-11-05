"""Standalone visual client for repeated Bond battles from the ready page."""
from __future__ import annotations

import json
import hashlib
import os
import queue
import threading
import time
import uuid
from datetime import datetime
from pathlib import Path

import tkinter as tk
from tkinter import ttk

import hyol_scheduler as hs


VERSION = '2026.10.31.3'
MAX_BATTLES = 99
BOND_NO_RESPONSE_SECONDS = 30.0
BOND_ACCOUNT = hs.Account(0, "当前羁绊窗口", 0)
BOND_READY_ROI = (1000, 200, 1500, 450)
BOND_MATCH_ROI = (650, 700, 1050, 900)
BOND_RESULT_ROI = hs.TEAM_BATTLE_RESULT_ROI
BOND_STATE_PATH = hs.ROOT / "bond_client_state.json"
BOND_RESULT_PATH = hs.ROOT / "bond_client_result.json"


def prepare_current_window(root, *, stop_event=None, event_sink=None, on_scheduler=None):
    """Explicit Start handoff without closing Bond or touching another tab."""
    from hyol_preparation import handoff_start_window
    from hyol_diagnostics import clear_manual_pause
    config = hs.read_config(Path(root)/'HYOL_Daily.ini')
    config.update(AutoForeground='0', CurrentWindowOnly='1', AllowMarketPurchase='0')
    s = hs.Scheduler(config, 'independent_bond_prepare', False, False, event_sink=event_sink)
    if stop_event is not None:
        s.stop = stop_event
    if on_scheduler is not None:
        on_scheduler(s)
    result = dict(status='pending', healthy=False, content_clicks=0)
    lease = hs.ForegroundRuntimeLease()
    owned = False
    try:
        owned = lease.acquire()
        if not owned:
            raise hs.PendingError('已有其他脚本占用游戏窗口')
        clear_manual_pause(root, '用户主动开始当前窗口独立羁绊')
        handoff_start_window(s)
        s.bind_window(wait=False)
        frame = s.capture()
        if not s.healthy(frame) or s.offline_dialog_visible(frame):
            raise hs.PendingError('当前窗口画面未就绪或已掉线，未开始羁绊')
        result.update(status='ready', healthy=True, main_ready=s.main_ready(frame))
    except Exception as exc:
        result['reason'] = str(exc)
    finally:
        result['content_clicks'] = s.content_click_count
        if owned:
            lease.release()
        if not s.run_context:
            s.stop.set()
        s.release_resources()
    return result


def load_json(path: Path) -> dict:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
        return value if isinstance(value, dict) else {}
    except (OSError, ValueError):
        return {}


def target_reached(request):
    mode = request.get('stop_mode', 'battles')
    if mode == 'endless':
        return False
    value = request.get('verified_wins', 0) if mode == 'wins' else request.get('completed', 0)
    return value >= request.get('requested', 1)


def prepare_request(saved: dict, requested: int, now: str, *, preserve_target=False, new_round=True, stop_mode='battles') -> dict:
    """Create, resume or resize one repeatable battle request."""
    if not isinstance(requested, int) or isinstance(requested, bool) or not 1 <= requested <= MAX_BATTLES:
        raise ValueError(f"battle count must be 1..{MAX_BATTLES}")
    if stop_mode not in ('endless', 'battles', 'wins'):
        raise ValueError('invalid Bond stop mode')
    if saved:
        target, completed = saved.get('requested'), saved.get('completed')
        mode = saved.get('stop_mode', 'battles')
        wins = saved.get('verified_wins', 0)
        if (saved.get('status') not in {'running', 'game_timeout', 'done'}
                or not isinstance(saved.get('run_id'), str) or not saved['run_id'].strip()
                or not isinstance(target, int) or isinstance(target, bool) or not 1 <= target <= MAX_BATTLES
                or mode not in ('endless', 'battles', 'wins')
                or not isinstance(completed, int) or isinstance(completed, bool) or completed < 0
                or mode == 'battles' and completed > target
                or type(wins) is not int or not 0 <= wins <= completed
                or saved.get('phase') not in {'ready', 'match_pending', 'running', 'result_confirmed'}
                or saved.get('status') == 'done' and (not target_reached(saved) or saved.get('phase') != 'ready')):
            raise hs.PendingError('羁绊原断点格式或阶段无法核验，保留原请求，不重复开战')
        if saved.get('status') == 'done' and not new_round:
            return dict(saved)
    restart_endless = (new_round and saved.get('stop_mode') == 'endless'
                       and saved.get('phase') == 'ready' and saved.get('last_error') == 'F12 emergency stop')
    if saved.get("status") not in {"running", "game_timeout"} or restart_endless:
        return {
            "run_id": datetime.now().strftime("%Y%m%d_%H%M%S_%f")+'_'+uuid.uuid4().hex[:8],
            "status": "running",
            "requested": requested,
            "completed": 0,
            "phase": "ready",
            "stop_mode": stop_mode,
            "verified_wins": 0,
            "created_at": now,
            "updated_at": now,
            "last_error": "",
        }
    request = dict(saved)
    request["status"] = "running"
    request["requested"] = saved['requested'] if preserve_target else requested
    request["updated_at"] = now
    request["last_error"] = ""
    return request


def explain_failure(detail: object) -> str:
    raw = str(detail or "未返回具体原因")
    for token, message in (
        ("bond ready page", "当前不是图一所示的羁绊准备页，或“匹配战斗”按钮不可见。"),
        ("匹配战斗按钮", "羁绊准备页存在，但“匹配战斗”按钮没有稳定显示。"),
        ("returned without", "战斗返回后没有确认到结算结果，本场不计数。"),
        ("result confirmation", "羁绊结算确认没有完成，本场保留待处理。"),
        ("game timeout", "游戏超时：长时间停在匹配、玩家加载或战斗过渡页，脚本已停止。"),
        ("位置或尺寸不符", "游戏窗口位置或尺寸不符合要求，请恢复到左上角1920×1032。"),
        ("expected one healthy game window", "未找到唯一可用的火影OL窗口。"),
        ("another scheduler owns", "已有其他脚本正在运行，请等它结束后重试。"),
        ("F12", "已按F12紧急停止，本轮尚未完成。"),
    ):
        if token in raw:
            return message + "\n原始诊断：" + raw
    return raw


def loading_timed_out(loading_since: float | None, now: float) -> bool:
    return loading_since is not None and now - loading_since >= BOND_NO_RESPONSE_SECONDS


class BondRunner:
    def __init__(self, requested: int, event_sink=None, *, config_path=None, require_identity=True,
                 legacy_state_paths=(), new_round=False, keep_ready=False, stop_mode='battles') -> None:
        self.requested = requested
        self.require_identity = require_identity
        self.new_round = new_round
        self.keep_ready = keep_ready
        self.stop_mode = stop_mode
        self.legacy_state_paths = tuple(dict.fromkeys((BOND_STATE_PATH, *map(Path, legacy_state_paths))))
        self.request_path = BOND_STATE_PATH
        self._account = BOND_ACCOUNT
        self.scheduler = hs.Scheduler(
            hs.read_config(config_path or hs.CONFIG_PATH),
            "bond_client",
            False,
            False,
            event_sink=event_sink,
        )
        if keep_ready:
            self.scheduler.config.update(CurrentWindowOnly='1', PreserveActivityNotices='1')
            # Repeated starts must recognize the same role, even in a portable
            # installation whose other tasks deliberately use fresh run IDs.
            self.scheduler._portable_identity_directory = self.scheduler.shared_state_directory/'bond'
        # The standalone client is page-bound instead of account-bound. Give
        # its synthetic audit identity a normal per-day action file and log
        # without changing the six configured game accounts.
        state = self.scheduler.state
        state.account_paths[BOND_ACCOUNT.name] = (
            hs.STATE_DIR / state.day / f"{hs.slug(BOND_ACCOUNT.name)}.json"
        )
        state.accounts[BOND_ACCOUNT.name] = hs.StateStore._load(
            state.account_paths[BOND_ACCOUNT.name],
            {"date": state.day, "account": BOND_ACCOUNT.name, "modules": {}, "pending": []},
        )
        self.scheduler.account_logs[BOND_ACCOUNT.name] = hs.JsonlLogger(
            hs.LOGS / "accounts" / f"{hs.slug(BOND_ACCOUNT.name)}.jsonl"
        )
        self.request: dict = {}

    @property
    def account(self):
        return getattr(self, '_account', BOND_ACCOUNT)

    def bind_identity_request(self):
        """Bind before any game action and keep one named request across entries."""
        if getattr(self, 'keep_ready', False):
            return self.bind_repeatable_request()
        from hyol_checkpoints import read_checkpoint
        s = self.scheduler
        shared = s.shared_state_directory
        # Anonymous historical actions cannot be attributed from a new tab
        # number, even if the current player's name is now visible.
        directories = {Path(directory).resolve() for directory in
                       (shared, hs.STATE_DIR, *(path.parent/'state' for path in self.legacy_state_paths))}
        for directory in directories:
            for path in directory.glob('????-??-??/'+hs.slug(BOND_ACCOUNT.name)+'.json'):
                old = read_checkpoint(path)
                if any(isinstance(row, dict) and row.get('status') == 'pending'
                       for row in old.get('actions', {}).values()):
                    raise hs.PendingError(f'旧羁绊匿名动作仍未决，须核实原身份和结果：{path}')
        account = s.bind_generic_current_window(require_game_context=False)
        if account.name.startswith('当前窗口标签'):
            self.wait_for_named_identity(account)
            account = s.bind_generic_current_window(require_game_context=False)
            if account.name.startswith('当前窗口标签'):
                raise hs.PendingError('羁绊开战前账号未唯一确认；保留原请求，不点击匹配')
        self._account = account
        self._unified_owner = account.name
        self.request_path = shared/'bond'/(hs.slug(account.name)+'.json')
        canonical = read_checkpoint(self.request_path)
        if canonical and canonical.get('owner') != account.name:
            raise hs.PendingError('羁绊共享断点身份冲突，保留原文件')
        saved = canonical
        for path in self.legacy_state_paths:
            if path.resolve() == self.request_path.resolve():
                continue
            legacy = read_checkpoint(path)
            if not legacy:
                continue
            prepare_request(legacy, self.requested, hs.now_iso(), preserve_target=True, new_round=False)
            if legacy.get('owner') != account.name:
                if legacy.get('status') != 'done' or not self.new_round:
                    raise hs.PendingError(f'另一个入口的羁绊断点未关联当前账号，保留原请求：{path}')
                continue
            if saved:
                if saved.get('run_id') != legacy.get('run_id'):
                    if legacy.get('status') != 'done':
                        raise hs.PendingError('跨入口羁绊有不同未决轮次，不开始新战斗')
                    continue
                if (legacy.get('completed', -1) > saved.get('completed', -1)
                        or legacy.get('requested') != saved.get('requested')):
                    raise hs.PendingError('跨入口羁绊进度冲突，保留所有原请求')
            else:
                saved = legacy
        self.request = prepare_request(saved, self.requested, hs.now_iso(),
                                       preserve_target=True, new_round=self.new_round)
        self.request['owner'] = account.name
        if saved:
            self.request.setdefault('checkpoint_sources', [str(self.request_path),
                                                         *map(str, self.legacy_state_paths)])
        return saved

    def bind_repeatable_request(self):
        """Bind one current tab; preserve inactive anonymous history separately."""
        from hyol_accounts import game_session_token
        from hyol_checkpoints import read_checkpoint, decode_mask
        from hyol_history import archive_bytes, inactive, prove_idle_identity, session_started_at
        from hyol_bond_goals import lobby_identity
        from hyol_multi_solo import active_tab_centers, tab_close_positions
        from hyol_team_pipeline import daily_page_present
        s = self.scheduler
        frame = s.capture()
        markers = tab_close_positions(frame)
        s.config['TabXs'] = ','.join(map(str, active_tab_centers(markers)))
        tab_x = s._generic_active_tab_x(frame)
        session = game_session_token(s)
        scope_key = hashlib.sha256((session+'|'+str(tab_x)).encode()).hexdigest()
        self.scope_path = s.shared_state_directory/'bond/scopes'/(scope_key+'.json')
        scope = read_checkpoint(self.scope_path)
        def bind_page_scope(current):
            owner = current['owner']
            account = hs.Account(active_tab_centers(markers).index(tab_x)+1, owner, tab_x)
            s.state = hs.StateStore([owner], directory=s.shared_state_directory)
            s.accounts = [account]
            s.by_name = {owner: account}
            s.active = s._capture_account = account
            s.current_account_only = owner
            s.generic_current_tab_x = tab_x
            s._capture_binding_probe = False
            if current.get('world_mask'):
                s._verified_current_mask = decode_mask(current['world_mask'], tuple(current['world_shape']))
            s.account_logs[owner] = hs.JsonlLogger(hs.LOGS/'accounts'/(hs.slug(owner)+'.jsonl'))
            s.event('current_identity_checkpoint_bound', account, session=session, observations=3,
                    detail='仅关联当前羁绊窗口的队长姓名凭据，未切换账号')
            return account
        if scope and not s.main_ready(frame):
            if (scope.get('session') != session or scope.get('tab_x') != tab_x
                    or scope.get('markers') != list(markers) or not scope.get('owner')):
                raise hs.PendingError('羁绊原当前窗口或标签布局改变，未匹配')
            owner = scope['owner']
            original = read_checkpoint(s.shared_state_directory/'bond'/(hs.slug(owner)+'.json'))
            if original.get('run_id') != scope.get('run_id') or original.get('owner') != owner:
                raise hs.PendingError('羁绊当前窗口未关联原轮次，不重复匹配')
            if self.ready_visible(frame):
                for _ in range(3):
                    frame = s.capture()
                    if (not self.ready_visible(frame) or not scope.get('lobby_rows')
                            or lobby_identity(frame, s.templates) != scope['lobby_rows']):
                        raise hs.PendingError('当前羁绊队长与原窗口凭据不一致，不新开战斗')
                    s.wait_interruptible(.2)
            elif (original.get('phase') not in ('match_pending', 'running', 'result_confirmed')
                    and not (original.get('phase') == 'ready' and original.get('navigation_started_at')
                             and daily_page_present(s, frame))):
                raise hs.PendingError('羁绊当前页面不是原轮次准备页或已发送的战斗')
            account = bind_page_scope(scope)
        else:
            rows = lobby_identity(frame, s.templates) if self.ready_visible(frame) else None
            if not rows:
                raise hs.PendingError('请先手动进入当前队长的羁绊准备页，再开始独立羁绊')
            samples = []
            for sample in range(3):
                current = s.capture()
                if (not self.ready_visible(current) or lobby_identity(current, s.templates) != rows
                        or s._generic_active_tab_x(current) != tab_x or game_session_token(s) != session):
                    raise hs.PendingError('当前羁绊队长姓名、准备页或窗口未连续确认')
                samples.append(str(s.save_raw_evidence(current, BOND_ACCOUNT, 'manual_bond_current_page', 'verified')))
                s.wait_interruptible(.2)
            owner = 'bond_scope_'+hashlib.sha256((scope_key+json.dumps(rows)).encode()).hexdigest()[:32]
            scope = dict(owner=owner, session=session, tab_x=tab_x, markers=list(markers),
                         lobby_rows=rows, page_bound=True, samples=samples,
                         idle_proof=dict(session_token=session, session_started_at=session_started_at(session),
                             observations=3, evidence=samples[-1], content_clicks=0, identity='current_bond_captain_page'))
            account = bind_page_scope(scope)
        self._account = account
        self._unified_owner = account.name
        self.scope = scope
        self.request_path = s.shared_state_directory/'bond'/(hs.slug(account.name)+'.json')
        saved = read_checkpoint(self.request_path)
        if saved and saved.get('owner') != account.name:
            raise hs.PendingError('独立羁绊原轮次身份不一致')
        sources = list(saved.get('isolated_legacy', ()))
        for row in sources:
            path = Path(row['path'])
            if path.is_file() and hashlib.sha256(path.read_bytes()).hexdigest() != row['sha256']:
                raise hs.PendingError('隔离的历史羁绊记录发生变化，保留所有原结果')
        known = {str(Path(row['path']).resolve()) for row in sources}
        paths = list(self.legacy_state_paths)
        for directory in {s.shared_state_directory, hs.STATE_DIR, *(p.parent/'state' for p in self.legacy_state_paths)}:
            paths.extend(directory.glob('????-??-??/'+hs.slug(BOND_ACCOUNT.name)+'.json'))
        idle_proof = None
        for path in dict.fromkeys(paths):
            if str(path.resolve()) in known or path.resolve() == self.request_path.resolve():
                continue
            old = read_checkpoint(path)
            pending = (old.get('status') in ('running', 'game_timeout') or any(
                isinstance(row, dict) and row.get('status') == 'pending' for row in old.get('actions', {}).values()))
            if not pending:
                continue
            if old.get('owner') == account.name:
                if saved and saved.get('run_id') != old.get('run_id'):
                    raise hs.PendingError('当前账号存在另一个未决羁绊轮次')
                saved = old
                continue
            if (s.config.get('HistoricalPendingPolicy') != 'isolate_inactive'
                    or not inactive(old, path, s.state.day, session_started_at(session))):
                raise hs.PendingError('存在当前或无法核实日期的旧羁绊未决记录，不开始新匹配：'+str(path))
            idle_proof = idle_proof or scope.get('idle_proof') or prove_idle_identity(s, account)
            archived = archive_bytes(path, s.shared_state_directory/'bond')
            sources.append(dict(archived, outcome='historical_unknown', proof=idle_proof))
        self.request = prepare_request(saved, self.requested, hs.now_iso(), preserve_target=True,
                                       new_round=self.new_round, stop_mode=getattr(self, 'stop_mode', 'battles'))
        if saved and self.request['run_id'] != saved.get('run_id') and self.request_path.is_file():
            self.request['previous_round'] = dict(archive_bytes(self.request_path, s.shared_state_directory/'bond'),
                run_id=saved['run_id'], status='complete' if saved.get('status') == 'done' else 'stopped',
                completed=saved['completed'], verified_wins=saved.get('verified_wins', 0))
        self.request.update(owner=account.name, isolated_legacy=sources)
        self.save_request()
        self.save_scope()
        return saved

    def save_scope(self):
        self.scope.update(run_id=self.request['run_id'], updated_at=hs.now_iso())
        hs.atomic_json(self.scope_path, self.scope)

    def enter_current_ready(self):
        """The user enters Bond; this task only operates the current ready page."""
        from hyol_bond_goals import lobby_identity
        s = self.scheduler
        frame = s.capture()
        if not self.ready_visible(frame):
            raise hs.PendingError('请先手动进入羁绊准备页；独立脚本不自动进入活动')
        if not self.ready_visible_stable():
            raise hs.PendingError('当前窗口羁绊准备页未稳定确认')
        rows = lobby_identity(s.capture(), s.templates)
        if not rows:
            raise hs.PendingError('当前羁绊队长姓名未确认，未匹配')
        if self.scope.get('lobby_rows') and rows != self.scope['lobby_rows']:
            raise hs.PendingError('当前羁绊队长姓名与原轮次不同')
        self.scope['lobby_rows'] = rows
        self.save_scope()

    def wait_for_named_identity(self, anonymous):
        """Observe an occluded identity without attributing or clicking a battle."""
        from hyol_checkpoints import match_identity, read_checkpoint
        s = self.scheduler
        identities = read_checkpoint(s.shared_state_directory/'account_identities.json')
        watchdog = hs.BattleWatchdog(mode='bond')
        s.event('bond_identity_wait', anonymous, detail='身份暂时遮挡；观察原画面，不重复开战')
        while True:
            s.check_stop()
            frame = s.capture()
            if match_identity(frame, identities)[0]:
                return
            hs.check_battle_watchdog(s, watchdog, anonymous, frame, 'bond_identity', mode='bond')
            s.wait_interruptible(3)

    def save_request(self) -> None:
        self.request["updated_at"] = hs.now_iso()
        hs.atomic_json(getattr(self, 'request_path', BOND_STATE_PATH), self.request)

    def event(self, name: str, **fields: object) -> None:
        if name == 'bond_command_summary':
            self.last_summary = dict(fields)
        self.scheduler.event(name, self.account, **fields)

    def ready_visible(self, frame=None) -> bool:
        frame = frame or self.scheduler.capture()
        if getattr(self, 'keep_ready', False):
            from hyol_identity_overlays import inspect_bond_shell
            shell = inspect_bond_shell(self.scheduler, frame)
            if shell and shell[0] in ('bond_points_result', 'blocked_bond_points'):
                return False
        return (
            self.scheduler.template("bond_ready_anchor.bmp", BOND_READY_ROI, frame, threshold=0.92)[0]
            and self.scheduler.template("bond_match_battle.bmp", BOND_MATCH_ROI, frame, threshold=0.92)[0]
        )

    def ready_visible_stable(self, seconds: float = 1.5, samples: int = 3) -> bool:
        """Reject the one-frame ready page exposed between result overlays."""
        interval = seconds / max(1, samples - 1)
        for index in range(samples):
            frame = self.scheduler.capture()
            if self.scheduler._team_result_template(frame) or self.scheduler._team_reward_result_visible(frame):
                return False
            if not self.ready_visible(frame):
                return False
            if index + 1 < samples:
                self.scheduler.wait_interruptible(interval)
        return True

    def click_once_and_wait_absent(
        self,
        template: str,
        roi: tuple[int, int, int, int],
        dx: int,
        dy: int,
        action: str,
        seconds: float,
    ) -> None:
        status = self.scheduler.state.action_status(self.account.name, action)
        if status == "pending":
            raise hs.PendingError(f"{action} prior outcome is unknown; refusing duplicate")
        if status == "done":
            if self.scheduler.template(template, roi, threshold=0.90)[0]:
                raise hs.PendingError(f"{action} completed control reappeared; refusing duplicate")
            return
        if not self.scheduler.click_template(
            self.account,
            template,
            roi,
            dx,
            dy,
            action,
            [template],
            irreversible=True,
            threshold=0.90,
            anchor_threshold=0.90,
        ):
            raise hs.PendingError(f"{template} disappeared before click")
        if not self.scheduler.wait_absent(template, roi, seconds, threshold=0.90):
            raise hs.PendingError(f"{template} remained after click")
        self.scheduler.commit_action(self.account, action, f"{template} disappeared after guarded click")

    def start_battle(self, battle_no: int) -> None:
        frame = self.scheduler.capture()
        if not self.ready_visible(frame):
            raise hs.PendingError("bond ready page or 匹配战斗按钮 is not positively visible")
        if getattr(self, 'keep_ready', False):
            from hyol_bond_goals import lobby_identity
            if not self.scope.get('lobby_rows') or lobby_identity(frame, self.scheduler.templates) != self.scope['lobby_rows']:
                raise hs.PendingError('匹配前当前羁绊队长姓名发生变化，未发送新匹配')
        action = f"bond_{self.request['run_id']}_battle_{battle_no}_match"
        self.request.update(phase="match_pending", battle=battle_no)
        self.request.pop('match_wait_started_at', None)
        self.request.pop('match_found_wait_started_at', None)
        self.save_request()
        self.click_once_and_wait_absent(
            "bond_match_battle.bmp", BOND_MATCH_ROI, 90, 30, action, 12
        )
        self.request["phase"] = "running"
        self.save_request()
        self.event("bond_battle_started", battle=battle_no, requested=self.request["requested"])

    def observe_result(self, frame, battle_no, template):
        # Persist the actual result before clicking its confirmation. F12 may
        # interrupt between the input and the after-frame/commit.
        evidence = self.scheduler.save_raw_evidence(frame, self.account,
                                                   f"bond_battle_{battle_no}_result_observed", "verified")
        observation = {'run_id': self.request['run_id'], 'battle': battle_no,
            'owner': self.account.name, 'template': template, 'evidence': str(evidence)}
        if getattr(self, 'keep_ready', False):
            win = self.scheduler.template('arena_victory_banner.bmp',(780,300,1150,650),frame,threshold=.98)[0]
            loss = (self.scheduler.template('arena_defeat_banner.bmp',(780,300,1150,650),frame,threshold=.98)[0]
                    or self.scheduler.template('bond_defeat_header_20261031.bmp',(750,260,1170,430),frame,threshold=.98)[0])
            outcome = 'win' if win and not loss else 'loss' if loss and not win else 'unknown'
            previous = self.request.get('result_observation', {})
            if previous.get('battle') == battle_no and previous.get('run_id') == self.request['run_id']:
                if outcome == 'unknown':
                    outcome = previous.get('outcome', 'unknown')
                elif previous.get('outcome') in ('win', 'loss') and previous['outcome'] != outcome:
                    raise hs.PendingError('同一场羁绊胜负凭据冲突，不累计胜利')
            observation['outcome'] = outcome
            observation['outcome_evidence'] = (previous.get('outcome_evidence', previous.get('evidence'))
                if not win and not loss and previous.get('battle') == battle_no else str(evidence))
        self.request['result_observation'] = observation
        self.save_request()

    def result_was_observed(self, battle_no):
        observation = self.request.get('result_observation', {})
        return (isinstance(observation, dict) and observation.get('run_id') == self.request['run_id']
                and observation.get('battle') == battle_no and observation.get('owner') == self.account.name
                and observation.get('template') in {'team_result_confirm.bmp', 'arena_result_confirm.bmp',
                                                   hs.TEAM_REWARD_RESULT_TEMPLATE, 'bond_points_result'}
                and isinstance(observation.get('evidence'), str)
                and Path(observation['evidence']).is_file())

    def reconcile_battle_actions(self, battle_no):
        prefix = f"bond_{self.request['run_id']}_battle_{battle_no}_"
        for suffix in ('match', 'leader_start', 'final_start', 'result', 'reward_result'):
            action = prefix+suffix
            if self.scheduler.state.action_status(self.account.name, action) == 'pending':
                self.scheduler.commit_action(self.account, action,
                    '同一实名原轮次的该场结果及三帧准备页已确认，核实原点击结果；未重复点击')

    def poll_battle(self, battle_no: int) -> None:
        run_id = self.request["run_id"]
        result_seen = self.request.get("phase") == "result_confirmed" or self.result_was_observed(battle_no)
        loading_since: float | None = None
        transition_since = time.monotonic()
        watchdog = hs.BattleWatchdog.from_config(self.scheduler.config, started_at=transition_since)
        while True:
            self.scheduler.check_stop()
            frame = self.scheduler.capture()
            if getattr(self, 'keep_ready', False):
                from hyol_identity_overlays import inspect_bond_shell, dismiss_identity_overlays
                shell = inspect_bond_shell(self.scheduler, frame)
                if shell and shell[0] == 'bond_points_result':
                    action = f'bond_{run_id}_battle_{battle_no}_match'
                    if self.scheduler.state.action_status(self.account.name, action) not in ('pending', 'done'):
                        raise hs.PendingError('羁绊积分结算没有本轮原匹配凭据，不补记场次')
                    self.observe_result(frame, battle_no, 'bond_points_result')
                    self.request['phase'] = 'result_confirmed'
                    self.save_request()
                    dismiss_identity_overlays(self.scheduler, self.account, frame, only_kind='bond_points_result')
                    result_seen = True
                    continue
                waiting = self.scheduler.template('bond_wait_battle_header_20261030.bmp',(750,420,1150,540),frame,threshold=.98)[0] and self.scheduler.template('bond_wait_battle_hint_20261030.bmp',(780,580,1120,680),frame,threshold=.98)[0]
                searching = self.scheduler.template('bond_search_opponent_header_20261030.bmp',(750,420,1150,540),frame,threshold=.98)[0] and self.scheduler.template('bond_search_opponent_hint_20261030.bmp',(780,580,1120,680),frame,threshold=.98)[0]
                if waiting or searching:
                    action = f'bond_{run_id}_battle_{battle_no}_match'
                    if self.scheduler.state.action_status(self.account.name, action) not in ('pending', 'done'):
                        raise hs.PendingError('当前等待页没有原匹配凭据，不重复匹配')
                    started = self.request.setdefault('match_wait_started_at', time.time())
                    found = self.request.setdefault('match_found_wait_started_at', time.time()) if waiting else None
                    self.save_request()
                    if time.time()-started > 600 or found is not None and time.time()-found > 120:
                        raise hs.PendingError('羁绊原匹配仍在等待；保留原请求，不取消或重发')
                    self.scheduler.wait_interruptible(3 if waiting else 10)
                    watchdog = hs.BattleWatchdog.from_config(self.scheduler.config)
                    continue
            if not (self.scheduler._team_result_template(frame)
                    or self.scheduler._team_reward_result_visible(frame) or self.ready_visible(frame)):
                hs.check_battle_watchdog(self.scheduler, watchdog, self.account, frame,
                                        f'bond_{run_id}_battle_{battle_no}', mode='bond')

            loading_visible = self.scheduler.template(
                "arena_loading_anchor.bmp", hs.ARENA_LOADING_ROI, frame, threshold=0.90
            )[0]
            if loading_visible:
                self.scheduler.wait_interruptible(.35)
                continue
            loading_since = None

            if self.scheduler._team_battle_start_visible(frame):
                self.click_once_and_wait_absent(
                    hs.TEAM_BATTLE_START_TEMPLATE,
                    hs.TEAM_BATTLE_START_ROI,
                    65,
                    23,
                    f"bond_{run_id}_battle_{battle_no}_leader_start",
                    12,
                )
                transition_since = time.monotonic()
                continue

            if self.scheduler.template("team_leader_final_start.bmp", (650, 400, 1250, 850), frame, threshold=0.90)[0]:
                self.click_once_and_wait_absent(
                    "team_leader_final_start.bmp",
                    (650, 400, 1250, 850),
                    55,
                    18,
                    f"bond_{run_id}_battle_{battle_no}_final_start",
                    8,
                )
                transition_since = time.monotonic()
                continue

            if getattr(self, 'keep_ready', False) and self.scheduler._battle_auto_on_visible(frame):
                control = self.request.setdefault('auto_controls', {}).setdefault(str(battle_no), {})
                if control.get('auto_on') is not True:
                    control.update(auto_on=True, scope='current_window', evidence=str(self.scheduler.save_raw_evidence(
                        frame, self.account, f'bond_{battle_no}_current_auto_on', 'verified')))
                    self.save_request()
                    self.event('bond_current_auto_verified', battle=battle_no, evidence=control['evidence'],
                               detail='当前账号自动已开启；仅等待本场结束，不切换其他窗口')

            if self.scheduler.template("battle_auto_off.bmp", hs.BATTLE_AUTO_ROI, frame)[0]:
                auto_action = f"bond_{run_id}_battle_{battle_no}_auto"
                if getattr(self, 'keep_ready', False) and self.scheduler.state.action_status(self.account.name, auto_action) != 'new':
                    raise hs.PendingError('本场自动开启已有原动作，但当前仍显示关闭；不重复切换')
                if self.scheduler.state.action_status(self.account.name, auto_action) != "done":
                    if not self.scheduler.click_template(
                        self.account,
                        "battle_auto_off.bmp",
                        hs.BATTLE_AUTO_ROI,
                        29,
                        29,
                        auto_action,
                        ["battle_auto_off.bmp"],
                        irreversible=getattr(self, 'keep_ready', False),
                    ):
                        raise hs.PendingError("bond auto-battle control disappeared")
                    if not self.scheduler._wait_battle_auto_on(5):
                        if getattr(self, 'keep_ready', False):
                            from hyol_team_controls import finished_visible
                            if finished_visible(self.scheduler, self.scheduler.capture(), 'bond'):
                                self.request.setdefault('auto_controls', {})[str(battle_no)] = dict(
                                    auto_on=None, enable_requested=True, battle_ended=True)
                                self.save_request()
                                continue
                        raise hs.PendingError("bond auto-battle was not positively enabled")
                    self.scheduler.state.set_action(self.account.name, auto_action, "done", "auto battle visibly enabled")
                    self.event("bond_auto_enabled", battle=battle_no)
                    transition_since = time.monotonic()
                self.scheduler.wait_interruptible(.35)
                continue

            result_template = self.scheduler._team_result_template(frame)
            if result_template:
                self.observe_result(frame, battle_no, result_template)
                self.click_once_and_wait_absent(
                    result_template,
                    BOND_RESULT_ROI,
                    55 if result_template.startswith("team_") else 64,
                    18 if result_template.startswith("team_") else 19,
                    f"bond_{run_id}_battle_{battle_no}_result",
                    8,
                )
                result_seen = True
                self.request["phase"] = "result_confirmed"
                self.save_request()
                self.event("bond_result_confirmed", battle=battle_no, template=result_template)
                transition_since = time.monotonic()
                continue

            if self.scheduler._team_reward_result_visible(frame):
                if getattr(self, 'keep_ready', False):
                    raise hs.PendingError('当前出现奖励领取弹窗；独立羁绊保留原结算，未领取奖励')
                self.observe_result(frame, battle_no, hs.TEAM_REWARD_RESULT_TEMPLATE)
                self.click_once_and_wait_absent(
                    hs.TEAM_REWARD_RESULT_TEMPLATE,
                    hs.TEAM_REWARD_RESULT_ROI,
                    64,
                    19,
                    f"bond_{run_id}_battle_{battle_no}_reward_result",
                    8,
                )
                result_seen = True
                self.request["phase"] = "result_confirmed"
                self.save_request()
                self.event("bond_reward_result_confirmed", battle=battle_no)
                transition_since = time.monotonic()
                continue

            if self.ready_visible(frame):
                if not result_seen:
                    raise hs.PendingError("bond returned without a positively confirmed result")
                if not self.ready_visible_stable():
                    hs.check_battle_watchdog(self.scheduler, watchdog, self.account, frame,
                                            f'bond_{run_id}_battle_{battle_no}', mode='bond')
                    self.scheduler.wait_interruptible(0.35)
                    continue
                self.reconcile_battle_actions(battle_no)
                self.request["phase"] = "ready"
                self.request["completed"] = battle_no
                if getattr(self, 'keep_ready', False):
                    result = dict(self.request.get('result_observation', {}))
                    result['ready_evidence'] = str(self.scheduler.save_raw_evidence(
                        self.scheduler.capture(), self.account, f'bond_{battle_no}_ready', 'verified'))
                    self.request.setdefault('battle_results', {})[str(battle_no)] = result
                    self.request['verified_wins'] = sum(row.get('outcome') == 'win'
                        for row in self.request['battle_results'].values())
                self.save_request()
                self.event(
                    "bond_battle_completed",
                    battle=battle_no,
                    completed=self.request["completed"],
                    requested=self.request["requested"],
                    verified_wins=self.request.get('verified_wins', 0),
                )
                if self.request.get('stop_mode') == 'wins' and result.get('outcome') not in ('win', 'loss'):
                    raise hs.PendingError('原羁绊本场结算已确认但胜负未知，保留结果，不补记胜利或新匹配')
                return

            # Active, matching and unknown frames share the image-change rule.
            self.scheduler.wait_interruptible(0.35)
        evidence = self.scheduler.save_raw_evidence(
            self.scheduler.capture(), self.account, f"bond_battle_{battle_no}_timeout", "pending"
        )
        raise hs.PendingError(f"game timeout: bond battle did not progress; evidence={evidence}")

    def run(self) -> int:
        lease = hs.ForegroundRuntimeLease()
        try:
            if not lease.acquire():
                raise hs.PendingError("another scheduler owns the shared-window runtime lease")
            from hyol_diagnostics import manual_pause_status
            if manual_pause_status(hs.ROOT)['paused']:
                raise KeyboardInterrupt('F12暂停仍在，保留羁绊原请求')
            self.scheduler.start_f12_monitor()
            self.scheduler.bind_window(wait=True, timeout_seconds=15)
            if getattr(self, 'require_identity', False):
                saved = self.bind_identity_request()
            else:
                from hyol_checkpoints import read_checkpoint
                saved = read_checkpoint(BOND_STATE_PATH)
                self.request = prepare_request(saved, self.requested, hs.now_iso())
                self.scheduler.current_account_only = self.account.name
                self.scheduler.active = self.account
            frame = self.scheduler.capture()
            self.scheduler._connection_tab_x = self.scheduler._generic_active_tab_x(frame)
            self.scheduler.generic_current_tab_x = self.scheduler._connection_tab_x
            self.scheduler._capture_account = self.account
            self.scheduler.check_offline_frame(frame)
            self.scheduler.ensure_assets([
                "bond_ready_anchor.bmp",
                "bond_match_battle.bmp",
                "arena_loading_anchor.bmp",
                hs.TEAM_BATTLE_START_TEMPLATE,
                "team_leader_final_start.bmp",
                "battle_auto_off.bmp",
                "battle_auto_on.bmp",
                "battle_auto_on_current.bmp",
                "battle_auto_on_current_core.bmp",
                "team_result_confirm.bmp",
                "arena_result_confirm.bmp",
                hs.TEAM_REWARD_RESULT_TEMPLATE,
            ])
            frame = self.scheduler.capture()
            resumable = saved.get("status") in {"running", "game_timeout"} and self.request.get("phase") in {
                "match_pending", "running", "result_confirmed"
            }
            if getattr(self, 'keep_ready', False) and self.request.get('status') != 'done' and not resumable:
                self.enter_current_ready()
                frame = self.scheduler.capture()
            if self.request.get('status') != 'done' and not self.ready_visible(frame) and not resumable:
                evidence = self.scheduler.save_raw_evidence(frame, self.account, "bond_ready_missing", "pending")
                raise hs.PendingError(f"bond ready page or 匹配战斗按钮 is not positively visible; evidence={evidence}")
            self.save_request()
            self.event(
                "bond_request_ready",
                requested=self.request["requested"],
                completed=self.request["completed"],
                remaining=None if self.request.get('stop_mode') == 'endless' else max(0, self.request["requested"] - (
                    self.request.get('verified_wins', 0) if self.request.get('stop_mode') == 'wins' else self.request["completed"])),
                stop_mode=self.request.get('stop_mode', 'battles'),
                verified_wins=self.request.get('verified_wins', 0),
            )
            if self.request.get('stop_mode') == 'wins' and any(
                    row.get('outcome') not in ('win', 'loss') for row in self.request.get('battle_results', {}).values()):
                raise hs.PendingError('羁绊原场次胜负仍未知，不补记胜利或新匹配')
            while not target_reached(self.request):
                battle_no = int(self.request["completed"]) + 1
                phase = str(self.request.get("phase", "ready"))
                if phase == "ready":
                    self.start_battle(battle_no)
                self.poll_battle(battle_no)
            self.request.update(status="done", phase="ready", completed_at=hs.now_iso(), last_error="")
            if getattr(self, 'keep_ready', False):
                if not self.ready_visible_stable():
                    raise hs.PendingError('全部场次已核实，当前羁绊准备页仍待复核；不重开战斗')
                self.request['ready_evidence'] = str(self.scheduler.save_raw_evidence(
                    self.scheduler.capture(), self.account, 'independent_bond_round_complete', 'verified'))
            self.save_request()
            self.event(
                "bond_command_summary",
                status="complete",
                result=0,
                requested=self.request["requested"],
                completed=self.request["completed"],
                verified_wins=self.request.get('verified_wins', 0),
                stop_mode=self.request.get('stop_mode', 'battles'),
                clicks=self.scheduler.click_count,
                content_clicks=self.scheduler.content_click_count,
                detail="",
            )
            return 0
        except (hs.PendingError, hs.SafetyError, hs.LeaseBlocked) as exc:
            timed_out = "game timeout" in str(exc)
            # A binding/format failure never writes a replacement for the
            # unreadable or anonymous old request.
            if self.request:
                self.request.update(last_error=str(exc))
                if self.request.get('status') != 'done':
                    self.request['status'] = "game_timeout" if timed_out else "running"
                self.save_request()
            self.event(
                "bond_command_summary",
                status="game_timeout" if timed_out else "pending",
                result=2,
                requested=self.request.get("requested", self.requested),
                completed=self.request.get("completed", 0),
                clicks=self.scheduler.click_count,
                content_clicks=self.scheduler.content_click_count,
                detail=str(exc),
            )
            return 2
        except KeyboardInterrupt:
            if self.request:
                self.request.update(status="running", last_error="F12 emergency stop")
                self.save_request()
            self.event("bond_command_summary", status="stopped", result=130, detail="F12 emergency stop")
            return 130
        except Exception as exc:
            self.event("bond_command_summary", status="error", result=3,
                       detail=f"{type(exc).__name__}: {exc}", completed=self.request.get("completed", 0))
            return 3
        finally:
            self.scheduler.stop.set()
            lease.release()


class BondClient(tk.Tk):
    def __init__(self) -> None:
        super().__init__()
        self.title(f"火影OL 羁绊独立客户端 {VERSION}")
        self.geometry("850x660+40+90")
        self.minsize(750, 560)
        self.protocol("WM_DELETE_WINDOW", self.close)
        self.events: queue.Queue[dict] = queue.Queue()
        self.worker: threading.Thread | None = None
        self.count_value = tk.IntVar(value=5)
        self.stop_mode_value = tk.StringVar(value='打5次')
        self.new_round = tk.BooleanVar(value=False)
        self.status = tk.StringVar(value="就绪")
        self.result = tk.StringVar(value="请先手动进入羁绊准备页；脚本仅操作当前窗口，匹配后检查自动并连续作战。")
        self.summary: dict = {}
        self.build_ui()
        previous = load_json(BOND_RESULT_PATH)
        if previous:
            self.result.set("上次结果：" + str(previous.get("message", "无记录")))
        self.after(150, self.drain)

    def build_ui(self) -> None:
        style = ttk.Style(self)
        if "vista" in style.theme_names():
            style.theme_use("vista")
        outer = ttk.Frame(self, padding=18)
        outer.pack(fill="both", expand=True)
        ttk.Label(outer, text="羁绊 · 连续作战", font=("Microsoft YaHei UI", 19, "bold")).pack(anchor="w")
        ttk.Label(
            outer,
            text="仅支持当前一个游戏窗口。请手动进入羁绊准备页；脚本匹配、检查自动、结算后继续下一场，不领取宝箱。",
            wraplength=790,
        ).pack(anchor="w", pady=(8, 3))
        ttk.Label(outer, text="每场结算并返回羁绊准备页后自动开始下一场，直到达到所选次数。", wraplength=790).pack(anchor="w")
        controls = ttk.Frame(outer)
        controls.pack(fill="x", pady=15)
        ttk.Label(controls, text="目标：").pack(side="left")
        self.count = ttk.Combobox(controls, values=('一直打','打5次','5场胜利'), width=12,
                                 state='readonly', textvariable=self.stop_mode_value)
        self.count.pack(side="left", padx=(0, 16))
        self.start_button = ttk.Button(controls, text="开始 / 继续羁绊", command=self.start)
        self.start_button.pack(side="left", ipadx=20, ipady=7)
        ttk.Checkbutton(controls, text="开始新一轮", variable=self.new_round).pack(side="left", padx=10)
        ttk.Label(controls, text="F12 立即停止").pack(side="right")
        ttk.Label(outer, textvariable=self.status, font=("Microsoft YaHei UI", 12, "bold")).pack(anchor="w")
        box = ttk.LabelFrame(outer, text="本轮结果 / 失败原因", padding=10)
        box.pack(fill="x", pady=10)
        ttk.Label(box, textvariable=self.result, wraplength=760, justify="left").pack(fill="x")
        log_box = ttk.LabelFrame(outer, text="任务过程", padding=5)
        log_box.pack(fill="both", expand=True)
        self.log = tk.Text(log_box, wrap="word", height=12, state="disabled", font=("Microsoft YaHei UI", 10))
        bar = ttk.Scrollbar(log_box, command=self.log.yview)
        self.log.configure(yscrollcommand=bar.set)
        bar.pack(side="right", fill="y")
        self.log.pack(fill="both", expand=True)
        footer = ttk.Frame(outer)
        footer.pack(fill="x", pady=(10, 0))
        from hyol_client_tools import add_diagnostic_controls
        add_diagnostic_controls(footer, hs.ROOT, lambda: getattr(self, "last_scheduler", None))
        ttk.Button(footer, text="打开本地日志", command=self.open_logs).pack(side="left")
        ttk.Label(footer, text=f"独立运行，无需Codex或Python安装  |  {VERSION}").pack(side="right")

    def append(self, message: str) -> None:
        self.log.configure(state="normal")
        self.log.insert("end", f"{datetime.now():%H:%M:%S}  {message}\n")
        self.log.see("end")
        self.log.configure(state="disabled")

    def start(self) -> None:
        if self.worker and self.worker.is_alive():
            return
        try:
            requested = 5
            if self.stop_mode_value.get() not in ('一直打','打5次','5场胜利'):
                raise ValueError
        except (tk.TclError, ValueError):
            self.status.set("未开始")
            self.result.set('请选择一直打、打5次或5场胜利。')
            return
        self.start_button.configure(state="disabled")
        self.count.configure(state="disabled")
        self.status.set("正在执行，请勿切换游戏标签")
        self.result.set("正在核对羁绊准备页；只有每场结算并返回后才计数。")
        self.summary = {}
        self.append(f"你点击了开始；本轮目标{requested}场。")
        new_round = self.new_round.get()
        self.new_round.set(False)

        def run() -> None:
            result = 3
            try:
                runner = BondRunner(requested, self.events.put, new_round=new_round, keep_ready=True,
                    stop_mode={'一直打':'endless','打5次':'battles','5场胜利':'wins'}[self.stop_mode_value.get()])
                self.last_scheduler = runner.scheduler
                prepared = prepare_current_window(hs.ROOT)
                if prepared.get('status') != 'ready':
                    raise hs.PendingError(prepared.get('reason', '当前窗口尚未就绪'))
                result = runner.run()
            except BaseException as exc:
                self.events.put({"event": "bond_client_error", "detail": f"{type(exc).__name__}: {exc}"})
            finally:
                self.events.put({"event": "bond_client_finished", "result": result})

        self.worker = threading.Thread(target=run, name="GenericBond", daemon=False)
        self.worker.start()

    def drain(self) -> None:
        labels = {
            "bond_request_ready": lambda e: f"任务已建立：目标{e.get('requested')}场，剩余{e.get('remaining')}场",
            "bond_battle_started": lambda e: f"第{e.get('battle')}场已点击匹配并进入流程",
            "bond_auto_enabled": lambda e: f"第{e.get('battle')}场自动战斗已开启",
            "bond_result_confirmed": lambda e: f"第{e.get('battle')}场结算已确认",
            "bond_reward_result_confirmed": lambda e: f"第{e.get('battle')}场奖励结算已确认",
            "bond_battle_completed": lambda e: f"第{e.get('battle')}场完成，已返回羁绊准备页",
        }
        try:
            while True:
                event = self.events.get_nowait()
                name = event.get("event")
                if name in labels:
                    self.append(labels[name](event))
                elif name == "bond_command_summary":
                    self.summary = event
                elif name == "bond_client_error":
                    self.summary = {"detail": event.get("detail", "")}
                elif name == "bond_client_finished":
                    ok = event.get("result") == 0
                    message = (
                        f"本轮完成：{self.summary.get('completed', self.count_value.get())}场均已确认结算并返回羁绊准备页。\n开始/继续沿用原轮次；另开一轮须勾选开始新一轮。"
                        if ok
                        else "本轮未完成：" + explain_failure(self.summary.get("detail"))
                    )
                    self.status.set("完成，可以再次开始" if ok else "未完成，请按原因处理后重试")
                    self.result.set(message)
                    self.append(message)
                    hs.atomic_json(BOND_RESULT_PATH, {
                        "result": event.get("result"),
                        "message": message,
                        "summary": self.summary,
                        "ts": hs.now_iso(),
                        "version": VERSION,
                    })
                    self.start_button.configure(state="normal")
                    self.count.configure(state="readonly")
        except queue.Empty:
            pass
        self.after(150, self.drain)

    def open_logs(self) -> None:
        hs.LOGS.mkdir(parents=True, exist_ok=True)
        os.startfile(hs.LOGS)

    def close(self) -> None:
        if self.worker and self.worker.is_alive():
            self.iconify()
            return
        self.destroy()


if __name__ == "__main__":
    BondClient().mainloop()
