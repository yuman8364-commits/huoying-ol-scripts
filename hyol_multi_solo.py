"""Run selected solo dailies by task across the already-open browser tabs.

One foreground lease controls one shared game window. Tab-close glyphs are
observation markers only; every tab click targets the title body.
"""
from __future__ import annotations

import argparse
from copy import deepcopy
import json
import time
from datetime import date, timedelta
from pathlib import Path

import numpy as np
from PIL import Image, ImageGrab

import hyol_scheduler as hs
from hyol_daily_tasks import DailyRunner, DailyScheduler, DEFAULT_SOLO_OPTIONS, SOLO_OPTIONS, read_json, calendar_task_plan
from hyol_daruma_task import verified as daruma_verified
from hyol_escort import verified as escort_verified

TAB_MARKER_CORE = "microclient_tab_close_core.bmp"
TAB_CHROME_ROI = (0, 0, 1800, 39)
TAB_MARKER_THRESHOLD = .98
STALL_RECHECK_SECONDS = 3.0
DEFERRED_RECHECK_SECONDS = 60.0


def tab_close_positions(frame: Image.Image) -> tuple[int, ...]:
    """Count tabs from their X glyphs without returning any clickable X point."""
    cache = getattr(frame, '_hyol_observations', None)
    asset = hs.ASSETS / TAB_MARKER_CORE
    stat = asset.stat()
    key = ('tab_close_positions', str(asset), stat.st_mtime_ns, stat.st_size, TAB_MARKER_THRESHOLD)
    if cache is not None and key in cache:
        return cache[key]
    image = np.asarray(frame.crop(TAB_CHROME_ROI).convert("L"), dtype=np.uint8)
    marker = np.asarray(Image.open(hs.ASSETS / TAB_MARKER_CORE).convert("L"), dtype=np.uint8)
    scores = hs.ncc_map(image, marker, backend='opencv', cutoff=TAB_MARKER_THRESHOLD)
    positions = []
    while scores.size:
        y, x = np.unravel_index(np.argmax(scores), scores.shape)
        if scores[y, x] < TAB_MARKER_THRESHOLD:
            break
        # The core starts four pixels to the right of the enrolled full glyph.
        positions.append(int(x) - 4)
        scores[max(0, y-7):y+8, max(0, x-35):x+36] = -1
    result = tuple(sorted(positions))
    if cache is not None:
        cache[key] = result
    return result


def active_tab_centers(close_positions: tuple[int, ...]) -> tuple[int, ...]:
    """Click the tab title, at least 30 px to the left of its close glyph."""
    if not close_positions:
        raise hs.PendingError(f"浏览器标签X数量异常：{len(close_positions)}")
    if any(b-a < 70 or b-a > 200 for a, b in zip(close_positions, close_positions[1:])):
        raise hs.PendingError("标签X间距异常，未切换账号")
    return tuple(x-34 for x in close_positions)


def observe_ranking_wait(s, account, record, watches):
    """Measure observed frame progress, retaining the existing background policy."""
    key = (record.get('completed', 0), record.get('phase', 'ready'))
    previous = watches.get(account.name)
    if previous is None or previous[0] != key:
        watch = hs.BattleWatchdog.from_config(getattr(s, 'config', {}))
        watches[account.name] = (key, watch)
    else:
        watch = previous[1]
    frame = s.capture()
    hs.check_battle_watchdog(s, watch, account, frame, 'ranking_interleave_wait', mode='ranking')


class MultiSoloRunner:
    def __init__(self, tasks=DEFAULT_SOLO_OPTIONS, *, config_path=None, account_count=None,
                 arena_wins=5, arena_target_mode='tickets', formation="keep", event_sink=None, managed_enabled=True, run_context=None,
                 execution_day=None):
        chosen = tuple(tasks)
        if not chosen or len(set(chosen)) != len(chosen) or any(task not in SOLO_OPTIONS for task in chosen):
            raise ValueError("请选择不重复的单人日常任务")
        self.requested_tasks = chosen
        self.calendar_plan = calendar_task_plan(self.requested_tasks, execution_day)
        self.tasks = tuple(self.calendar_plan['effective_tasks'])
        if not self.tasks:
            raise ValueError(f"{self.calendar_plan['execution_day']}非周末，达摩和周末活动已按日历跳过；"
                             '没有其他可执行任务，未启动游戏')
        self.arena_wins = arena_wins
        self.arena_target_mode = arena_target_mode
        if not 1 <= arena_wins <= hs.ARENA_MAX_ATTEMPTS:
            raise ValueError(f"竞技场辅助胜场数须在1到{hs.ARENA_MAX_ATTEMPTS}之间")
        if arena_target_mode not in ('tickets', 'wins'):
            raise ValueError('竞技场结束条件须为战令归零或指定胜场')
        if formation not in ('keep', 'main', 'fast'):
            raise ValueError('请选择保持不变、主力或速刷阵容')
        self.formation = formation
        if not isinstance(managed_enabled, bool):
            raise ValueError('最后进入托管须为布尔选项')
        self.managed_enabled = managed_enabled
        self.config_path = Path(config_path or hs.ROOT / "HYOL_Daily.ini")
        config = hs.read_config(self.config_path)
        config['AllowMarketPurchase'] = '0'
        config['EscortCompletionPolicy'] = 'coupon_priority_remaining'
        config['HideOtherPlayers'] = '1'
        self.automatic_accounts = config.get('AccountDiscovery', 'automatic') == 'automatic'
        names = hs.parse_csv(config.get("TabAccountNames", ""))
        if self.automatic_accounts:
            names = [f'当前窗口标签{i}' for i in range(1,7)]
            config['AccountMode'] = 'dynamic'
            config['AccountDiscovery'] = 'automatic'
        if account_count is None:
            account_count = len(names)
        if account_count < 1 or account_count > len(names):
            raise ValueError(f"账号数量须在1到{len(names)}之间；通用模式从当前游戏标签自动建立本轮记录")
        selected_names = names
        missing = [] if self.automatic_accounts else [name for name in selected_names if not (hs.ASSETS / 'accounts' / f'{hs.slug(name)}_identity.bmp').exists()]
        if missing:
            raise ValueError(f"缺少账号身份模板，不能安全自动识别：{', '.join(missing)}")
        config['TabAccountNames'] = ','.join(selected_names)
        # The live X glyphs determine the coordinates before any identity bind.
        # These placeholders satisfy Scheduler construction only.
        config['TabXs'] = ','.join(str(100 + 100*i) for i in range(len(selected_names)))
        from hyol_run_context import RunContext
        self.run_context = run_context or RunContext.create(self.config_path.parent,config)
        with self.run_context.activate():
            self.scheduler = DailyScheduler(config, "multi_solo", False, False, event_sink=event_sink)
        self.scheduler.command_kind = "multi_solo"
        self.scheduler.survival_inline_two_runs = True
        if self.scheduler.account_mode == "current":
            raise ValueError("多账号单人任务需要共享标签账号配置")
        self.requested_account_count = account_count
        self.configured_names = tuple(account.name for account in self.scheduler.accounts)
        self.runners = {}
        self.stall_checks = {}
        self.last_failed_at = {}
        self.excluded = set()
        self.disconnected = set()
        self.exclusion_reasons = {}
        self.deferred = {}
        self.deferred_at = {}
        self.deferred_probed_at = {}
        self.arena_rewards_audited = set()
        self.manual_deferred = set()
        self.initial_unavailable = set()
        self.skipped_tasks = {}
        self.tab_count = 0
        self.startup_returned = set()
        self.startup_waiting_tasks = {}
        self.startup_pending = {}
        self.startup_rechecked = set()

    def save_progress(self, stage):
        hs.atomic_json(self.scheduler.shared_state_directory / "multi_solo_latest.json", {
            "updated_at": hs.now_iso(), "stage": stage, "tab_count": self.tab_count,
            "requested_account_count": self.requested_account_count,
            "stall_checks": self.stall_checks, "excluded": sorted(self.excluded),
            "disconnected": sorted(self.disconnected), "exclusion_reasons": self.exclusion_reasons,
            "deferred": self.deferred, "manual_deferred": sorted(self.manual_deferred),
            "initial_unavailable": sorted(self.initial_unavailable),
            "verified_accounts": sorted(account.name for account in self.scheduler.accounts),
            "skipped_tasks": self.skipped_tasks,
            "startup_returned": sorted(self.startup_returned),
            "startup_waiting_tasks": getattr(self, 'startup_waiting_tasks', {}),
            "startup_pending": self.startup_pending,
            "startup_rechecked": sorted(getattr(self, 'startup_rechecked', set())),
            "arena_requested_wins": self.arena_wins,
            "arena_target_mode": self.arena_target_mode,
            "managed_enabled": getattr(self, 'managed_enabled', True),
            "tasks": list(self.tasks),
            "requested_tasks": list(getattr(self, 'requested_tasks', self.tasks)),
            "effective_tasks": list(self.tasks),
            "calendar_skipped": getattr(self, 'calendar_plan', {}).get('calendar_skipped', {}),
            "execution_day": getattr(self, 'calendar_plan', {}).get('execution_day'),
            "run_id": self.scheduler.diagnostics.identifier,
            "tab_bindings": {str(a.index): a.name for a in self.scheduler.accounts},
            "blocked_tasks": getattr(self, 'blocked_tasks', {}),
            "accounts": {name: {task: runner.record(task).get("status", "new") for task in self.tasks}
                         for name, runner in self.runners.items()},
        })

    def event(self, name, account=None, **fields):
        self.scheduler.event(name, account, **fields)

    def discover_tabs(self):
        s = self.scheduler
        first = tab_close_positions(s.capture())
        s.wait_interruptible(.3)
        second = tab_close_positions(s.capture())
        if first != second:
            raise hs.PendingError("浏览器标签数量变化，未点击任何标签")
        centers = active_tab_centers(first)
        if len(centers) > len(s.accounts):
            raise hs.PendingError(f"当前有{len(centers)}个标签，配置仅有{len(s.accounts)}个候选身份")
        s.accounts = [hs.Account(account.index, account.name, centers[i] if i < len(centers)
                                 else 1801 + 100*i)
                      for i, account in enumerate(s.accounts)]
        s.by_name = {account.name: account for account in s.accounts}
        self.missing_tab_positions = {account.tab_x for account in s.accounts[len(centers):]}
        self.tab_count = len(centers)
        self.event("multi_solo_tabs_counted", count=self.tab_count,
                   close_markers=list(first), title_centers=list(centers), close_clicks=0)
        return centers

    def bind_accounts(self):
        s = self.scheduler
        if getattr(self, 'automatic_accounts', False):
            from hyol_accounts import discover_accounts
            centers, mapping, _frames, unavailable = discover_accounts(s)
            self.tab_count = len(centers)
            s.accounts = sorted(s.accounts, key=lambda a:a.tab_x)[:self.requested_account_count]
            s.by_name = {a.name:a for a in s.accounts}
            s.available_account_names = set(s.by_name)
            self.configured_names = tuple(s.by_name)
            self.initial_unavailable = {f'标签{slot}' for slot in unavailable}
            self.stall_checks = {a.name:0 for a in s.accounts}
            self.event('multi_solo_accounts_bound',online=sorted(s.by_name),pending=sorted(self.initial_unavailable))
            return
        prior_no_content_clicks = s.no_content_clicks
        s.no_content_clicks = True
        try:
            s.rebind_tabs_by_identity(skip_unavailable=True, verified_only=True,
                                      absent_positions=getattr(self, 'missing_tab_positions', set()))
        finally:
            s.no_content_clicks = prior_no_content_clicks
        requested_count = getattr(self, 'requested_account_count', len(s.accounts))
        if len(s.accounts) > requested_count:
            s.accounts = sorted(s.accounts, key=lambda account: account.tab_x)[:requested_count]
            s.by_name = {account.name: account for account in s.accounts}
            s.available_account_names = {account.name for account in s.accounts}
            s.joint_identity_bound = set(s.available_account_names)
        available = {account.name for account in s.available_accounts()}
        configured_names = getattr(self, 'configured_names', tuple(
            account.name for account in s.accounts))
        self.initial_unavailable = set(configured_names) - available
        self.stall_checks = {account.name: 0 for account in s.accounts}
        self.event("multi_solo_accounts_bound", online=sorted(available),
                   pending=sorted(self.initial_unavailable))

    def runner_for(self, account):
        if account.name in self.runners:
            runner = self.runners[account.name]
            runner.account = account
            return runner
        runner = object.__new__(DailyRunner)
        runner.scheduler = self.scheduler
        runner.account = account
        runner.task = "all"
        runner.current = ""
        runner.group_count = 0
        runner.arena_wins = self.arena_wins
        runner.arena_target_mode = self.arena_target_mode
        runner.formation = self.formation
        runner.formation_checked = False
        runner.generic_current_window = False
        runner.track_friendship = False
        runner.group_selection_cancelled = False
        runner.wanted_selection_cancelled = False
        runner.selected_tasks = self.tasks
        runner.requested_tasks = getattr(self, 'requested_tasks', self.tasks)
        runner.calendar_plan = getattr(self, 'calendar_plan', {})
        runner.managed_enabled = getattr(self, 'managed_enabled', True)
        from hyol_checkpoints import load_tasks
        runner.path, runner.records = load_tasks(self.scheduler.state, account.name)
        self.runners[account.name] = runner
        return runner

    def audit_previous_day(self):
        """Keep yesterday's unfinished actions visible without importing stale daily counters."""
        previous = (date.fromisoformat(self.scheduler.state.day) - timedelta(days=1)).isoformat()
        for account in self.scheduler.accounts:
            path = hs.STATE_DIR / previous / (hs.slug(account.name) + '_multi_solo.json')
            records = read_json(path)
            pending = {task: rec.get('last_error', '') for task, rec in records.items()
                       if isinstance(rec, dict) and (rec.get('status') == 'pending'
                       or rec.get('followup_check_due'))}
            action_path = hs.STATE_DIR / previous / (hs.slug(account.name) + '.json')
            old_actions = read_json(action_path).get('actions', {})
            pending_actions = sorted(key for key, value in old_actions.items()
                                     if isinstance(value, dict) and value.get('status') == 'pending')
            if pending or pending_actions:
                self.event('multi_solo_previous_day_pending', account,
                           day=previous, tasks=pending, actions=pending_actions,
                           detail='历史断点保留；今日次数以实画面重新核实')

    def return_account_to_origin(self, account):
        """Audit the account, then reuse the shared resumable village navigation."""
        import hyol_navigation as navigation
        s = self.scheduler
        frame = s.capture()
        before = s.save_raw_evidence(frame, account, 'multi_solo_start_before_return', 'pending')
        self.event('multi_solo_start_return_before', account, evidence=str(before))
        identity_ok, identity_scores = s.verify_identity(account, frame)
        if not identity_ok:
            raise hs.PendingError(
                f'开场回村前账号身份未确认；证据={before}；scores={identity_scores}')
        if set(getattr(self, 'tasks', ())) & {'puzzle', 'training_record'}:
            s.exit_managed_for_market(account)
            frame=s.capture()
        account_state = s.state.accounts.get(account.name, {})
        actions = account_state.get('actions', {})
        pending = [key for key, value in actions.items()
                   if isinstance(value, dict) and value.get('status') == 'pending']
        main = s.main_ready(frame)
        home = (main and s.template('leaf_village_name_current.bmp',
                    navigation.LEAF_NAME_ROI, frame, threshold=.92)[0])
        if pending and not home:
            raise hs.PendingError('存在未决操作，先由原任务对账：' + ', '.join(pending))
        # runner_for reads the shared ledger, including carried cross-day work;
        # a new entrance must not move an unfinished task away from its page.
        records = self.runner_for(account).records
        unfinished = [task for task, record in records.items()
                      if isinstance(record, dict) and (record.get('status') == 'pending'
                      or record.get('followup_check_due'))
                      and not (s.main_ready(frame) and self._task_waiting_on_main(account, task, record))]
        if unfinished and not home:
            raise hs.PendingError('存在未决任务，先由原任务对账：' + ', '.join(unfinished))
        unsafe = (s._combat_active_visible('arena', frame)
                  and not s._combat_ready_visible('arena', frame))
        unsafe = unsafe or (s._combat_active_visible('survival', frame)
                            and not s._combat_ready_visible('survival', frame))
        unsafe = unsafe or any(s.template(name, roi, frame)[0] for name, roi in (
            ('arena_loading_anchor.bmp', hs.ARENA_LOADING_ROI),
            ('arena_result_confirm.bmp', (651, 449, 1151, 829)),
            ('ranking_result_confirm.bmp', (860, 640, 1060, 710)),
            ('ranking_battle_anchor.bmp', (1400, 205, 1510, 251)),
            ('survival_result_confirm.bmp', (701, 519, 1151, 819)),
            ('survival_reward_result_confirm.bmp', (701, 569, 1151, 819)),
        ))
        if unsafe:
            raise hs.PendingError('当前仍显示战斗、加载或结算，先由原任务核实结果')
        if not main:
            spring = navigation.hot_spring_visible(s, frame)
            prompt = (s.template('hot_spring_leave_prompt_text.bmp',
                                 navigation.SPRING_PROMPT_ROI, frame, threshold=.98)[0]
                      and s.template('hot_spring_leave_confirm.bmp',
                                     navigation.SPRING_PROMPT_ROI, frame, threshold=.98)[0])
            in_home=navigation.home_visible(s,frame)
            if not spring and not prompt and not in_home:
                raise hs.PendingError(f'开场回村前主界面与温泉页面未确认；证据={before}')
        already_home = (main and s.template('leaf_village_name_current.bmp',
                                           navigation.LEAF_NAME_ROI, frame, threshold=.92)[0])
        # Shared navigation owns X -> verified dialog text -> village return,
        # hover removal, three-frame proof and the persisted action stages.
        if navigation.return_to_leaf(s, account) is not True:
            raise hs.PendingError('共用导航尚未确认木叶村，开场回村保持待处理')
        frame = s.capture()
        identity_ok, identity_scores = s.verify_identity(account, frame)
        if not identity_ok:
            after = s.save_raw_evidence(frame, account, 'multi_solo_start_return_unverified', 'pending')
            self.event('multi_solo_start_return_identity_pending', account,
                       before=str(before), after=str(after), scores=identity_scores)
            raise hs.PendingError(
                f'回村后账号身份复核失败；证据={after}；scores={identity_scores}')
        if not (s.main_ready(frame) and s.template('leaf_village_name_current.bmp',
                                                  navigation.LEAF_NAME_ROI, frame, threshold=.92)[0]):
            after = s.save_raw_evidence(frame, account, 'multi_solo_start_return_unverified', 'pending')
            self.event('multi_solo_start_return_page_pending', account,
                       before=str(before), after=str(after))
            raise hs.PendingError(f'回村后木叶村主界面变化，原点仍待核实；证据={after}')
        after = s.save_raw_evidence(frame, account, 'multi_solo_start_return_origin', 'verified')
        if already_home:
            self.event('multi_solo_start_already_in_leaf', account,
                       before=str(before), after=str(after))
            self.event('multi_solo_start_return_verified', account,
                       before=str(before), after=str(after), already_home=True, clicked=False)
        else:
            self.event('multi_solo_start_return_verified', account,
                       before=str(before), after=str(after), delegated=True)

    def _task_waiting_on_main(self, account, task, record):
        waiting = getattr(self, 'startup_waiting_tasks', {}).get(account.name, {}).get(task)
        return (isinstance(waiting, dict) and task not in self.tasks
                and waiting.get('record') == record)

    def _preserve_unselected_idle_task(self, account, task):
        """An unselected noncombat task may wait only on an audited idle page."""
        if task in self.tasks or task not in ('mail', 'welfare', 'managed'):
            return False
        s = self.scheduler
        actions = s.state.accounts.get(account.name, {}).get('actions', {})
        if any(isinstance(row, dict) and row.get('status') == 'pending' for row in actions.values()):
            return False
        record = self.runner_for(account).records.get(task)
        if not isinstance(record, dict):
            return False
        original = deepcopy(record)
        for sample in range(3):
            frame = s.capture()
            if (s.main_ready(frame) is not True or not s.verify_identity(account, frame)[0]
                    or s._combat_active_visible('arena', frame)
                    or s._combat_active_visible('survival', frame)):
                return False
            if sample < 2:
                s.wait_interruptible(.35)
        if self.runner_for(account).records.get(task) != original:
            return False
        evidence = s.save_raw_evidence(frame, account, 'unselected_task_waiting_on_main', 'observed')
        waiting = getattr(self, 'startup_waiting_tasks', {})
        self.startup_waiting_tasks = waiting
        waiting.setdefault(account.name, {})[task] = dict(record=original, evidence=str(evidence),
            observed_at=hs.now_iso(), observations=3, outcome='pending_preserved')
        self.event('multi_solo_unselected_task_preserved', account, task=task, evidence=str(evidence),
                   detail='未选任务保留待续；身份与空闲主界面连续确认，无未决操作')
        return True

    def prepare_account_origins(self):
        for account in tuple(self.scheduler.accounts):
            if account.name in self.startup_returned:
                continue
            treasure = self.runner_for(account).record('treasure')
            if treasure.get('deadline_at') is not None and not treasure.get('home_returned'):
                self.startup_pending[account.name] = '挖宝尚未暂停回村，保留原截止时间'
                continue
            if self.scheduler.check_pause_boundary():
                return False
            pending_task = self.unresolved_combat_task(account)
            if (pending_task == 'training_record' and tuple(self.tasks) == ('training_record',)
                    and getattr(self.scheduler,'training_cooperative_enabled',False)):
                # Its owned child/scan must resume in the shared loop, never
                # recursively run an entire single-account training session.
                self.startup_returned.add(account.name)
                self.event('multi_solo_training_resume_queued',account,task='training_record',
                           detail='原修行录已登记，交给多账号轮转恢复；未执行内容输入')
                continue
            if pending_task and self._preserve_unselected_idle_task(account, pending_task):
                pending_task = self.unresolved_combat_task(account)
            if pending_task:
                self.event('multi_solo_start_reconcile', account, task=pending_task,
                           detail='先恢复原任务，禁止开场强制回村')
                if pending_task not in self.tasks:
                    self.startup_pending[account.name] = f'{pending_task}未决，当前未选择其恢复流程'
                    self._allow_other_selected_tasks(account, self.startup_pending[account.name])
                    continue
                try:
                    if pending_task in ('arena', 'survival'):
                        self.scheduler.switch_account_for_combat(account, pending_task, running=True)
                    else:
                        self.scheduler.switch_account(account)
                    runner = self.runner_for(account)
                    if pending_task == 'survival':
                        # An already-started battle must not reopen formation.
                        runner.formation_checked = True
                    if pending_task == 'survival' and runner.record('survival').get('followup_check_due'):
                        self._survival_followup(runner)
                    else:
                        self._run_task(runner, pending_task)
                except hs.AccountOffline as exc:
                    self.skip_disconnected(exc.account, pending_task)
                    continue
                except (hs.PendingError, hs.SafetyError, hs.LeaseBlocked) as exc:
                    self.startup_pending[account.name] = str(exc)
                    self.event('multi_solo_start_reconcile_pending', account, task=pending_task, detail=str(exc))
                    self._allow_other_selected_tasks(account, str(exc))
                    continue
                if self.unresolved_combat_task(account):
                    self.startup_pending[account.name] = f'{pending_task}结果仍未确认'
                    self._allow_other_selected_tasks(account, self.startup_pending[account.name])
                    continue
            if not self.check_account(account):
                self.startup_pending[account.name] = '账号身份或当前页面未核实'
                continue
            try:
                self.return_account_to_origin(account)
                self.startup_returned.add(account.name)
                self.startup_pending.pop(account.name, None)
            except hs.AccountOffline as exc:
                self.skip_disconnected(exc.account)
                continue
            except (hs.PendingError, hs.SafetyError) as exc:
                self.startup_pending[account.name] = str(exc)
                self.event('multi_solo_start_return_pending', account, detail=str(exc))
                if (self.scheduler.active == account and not
                        self.scheduler.ensure_account_main_before_release(account, 'startup_return')):
                    raise hs.LeaseBlocked(f'{account.name}开场页面未安全释放') from exc
            self.save_progress('startup_return')
        return True

    def _allow_other_selected_tasks(self, account, reason):
        """Unknown results stay pending; only a verified idle village releases other tasks."""
        s = self.scheduler
        if account.name in self.excluded or s.active != account:
            return False
        frame = s.capture()
        if not (s.main_ready(frame) and s.verify_identity(account, frame)[0]
                and s.template('leaf_village_name_current.bmp', (1690,110,1920,148), frame, threshold=.92)[0]):
            return False
        from hyol_task_progress import action_task
        actions = s.state.accounts.get(account.name, {}).get('actions', {})
        blocked = {action_task(key) for key, row in actions.items()
                   if isinstance(row, dict) and row.get('status') == 'pending'}
        if None in blocked:
            return False  # An unclassified action cannot be bypassed.
        blocked.update(task for task, rec in self.runner_for(account).records.items()
                       if isinstance(rec, dict) and (rec.get('status') in ('pending','running')
                                                    or rec.get('followup_check_due')))
        try:
            self.return_account_to_origin(account)  # Shared navigation requires three idle/identity frames.
        except (hs.PendingError, hs.SafetyError):
            return False
        if not hasattr(self, 'blocked_tasks'):
            self.blocked_tasks = {}
        self.blocked_tasks[account.name] = sorted(blocked)
        self.startup_returned.add(account.name)
        self.deferred.pop(account.name, None)
        self.deferred_at.pop(account.name, None)
        self.event('multi_solo_other_tasks_released', account, blocked_tasks=sorted(blocked),
                   detail=reason+'；原未决保留，身份与空闲木叶连续确认后继续其他已选任务')
        self.save_progress('other_tasks_released')
        return True

    def _tab_exists(self, account):
        # The selected tab uses a white background and a hover tooltip covers
        # the lower X pixels. The original whole-marker NCC can miss that one
        # tab, so recheck its high-contrast X core in a narrow read-only ROI.
        try:
            frame = self.scheduler.capture()
        except hs.AccountOffline:
            # The last trusted frame still contains valid tab chrome. Use it
            # to locate the next tab without touching the disconnected content.
            frame = self.scheduler._last_frame
        except hs.SafetyError as exc:
            if 'content surface is ambiguous: 0 visible regions' not in str(exc):
                raise
            hwnd = self.scheduler.hwnd
            if (hwnd not in hs.Win32.game_windows()
                    or hs.Win32.rect(hwnd) != hs.EXPECTED_WINDOW
                    or hs.Win32.foreground() != hwnd):
                raise
            # Only inspect browser chrome while the current tab is white.
            frame = ImageGrab.grab(bbox=hs.EXPECTED_WINDOW, all_screens=True).convert('RGB')
        markers = tab_close_positions(frame)
        if any(abs((marker - 34) - account.tab_x) <= 20 for marker in markers):
            return True
        expected = account.tab_x + 38
        left, right = max(0, expected - 18), min(frame.width, expected + 32)
        search = np.asarray(frame.crop((left, 14, right, 34)).convert("L"), dtype=np.float64)
        core = np.asarray(Image.open(hs.ASSETS / TAB_MARKER_CORE).convert("L"), dtype=np.float64)
        scores = hs.ncc_map(search, core)
        return bool(scores.size and np.max(scores) >= .98)

    def note_skipped(self, account, task):
        tasks = self.skipped_tasks.setdefault(account.name, [])
        if task not in tasks:
            tasks.append(task)
        self.event("multi_solo_task_skipped_for_later", account, task=task)

    def skip_disconnected(self, account, task=None):
        name = account.name
        self.excluded.add(name)
        self.disconnected.add(name)
        self.exclusion_reasons[name] = 'offline_prompt'
        self.deferred.pop(name, None)
        self.deferred_at.pop(name, None)
        if task:
            rec = self.runner_for(account).record(task)
            rec.update(status='pending', last_error='中央你已掉线弹窗已确认，本轮跳过账号')
            self.runner_for(account).save()
        self.event('multi_solo_account_disconnected_skipped', account, task=task,
                   detail='保留全部未决动作和原截止时间，不点击掉线弹窗')
        self.save_progress('account_disconnected')

    def skip_stalled(self, account, task, exc):
        mapping = self.scheduler._saved_tab_mapping()
        if not isinstance(mapping, dict) or mapping.get(account.name) != account.tab_x:
            raise hs.LeaseBlocked('30秒无变化，但标签身份绑定未核实；停止交接') from exc
        self.excluded.add(account.name)
        self.exclusion_reasons[account.name] = 'unchanged_30s'
        self.deferred.pop(account.name, None)
        self.deferred_at.pop(account.name, None)
        self.event('multi_solo_account_stalled', account, task=task, detail=str(exc),
                   evidence=getattr(exc, 'evidence', ''), reason='unchanged_30s')
        self.save_progress('account_stalled')

    def check_account(self, account):
        name = account.name
        if name in self.excluded:
            return False
        now = time.monotonic()
        last = self.last_failed_at.get(name)
        if last is not None and now - last < STALL_RECHECK_SECONDS:
            self.event("multi_solo_stall_recheck_deferred", account,
                       seconds_remaining=round(STALL_RECHECK_SECONDS - (now - last), 2))
            return False
        try:
            if not getattr(self, 'run_context', None) and not self._tab_exists(account):
                raise hs.PendingError("浏览器标签缺失")
            try:
                self.scheduler.switch_account(account)
            except hs.PendingError:
                # A background robbery defense may finish after preparation.
                # Escort-only startup can acknowledge its known win/loss UI;
                # owned unfinished tasks still go through their original flow.
                if tuple(getattr(self,'tasks',()))!=('escort',) or self.unresolved_combat_task(account):
                    raise
                from hyol_accounts import _final_result, dismiss_current_final_result
                frame=self.scheduler.capture()
                if (self.scheduler._generic_active_tab_x(frame)!=account.tab_x
                        or _final_result(self.scheduler,frame) is None):
                    raise
                dismiss_current_final_result(self.scheduler,account,frame,allow_reward_confirmation=False)
                self.scheduler.switch_account(account)
            self.stall_checks[name] = 0
            self.last_failed_at.pop(name, None)
            if hasattr(self.scheduler, 'available_account_names'):
                self.scheduler.available_account_names.add(name)
            return True
        except hs.AccountOffline as exc:
            self.skip_disconnected(exc.account)
            return False
        except (hs.PendingError, hs.SafetyError) as exc:
            checks = self.stall_checks.get(name, 0) + 1
            self.stall_checks[name] = checks
            self.last_failed_at[name] = now
            try:
                frame = self.scheduler.capture()
            except hs.SafetyError as capture_exc:
                if 'content surface is ambiguous: 0 visible regions' not in str(capture_exc):
                    raise
                frame = None
            if frame is None:
                reason = 'blank_or_unhealthy'
            elif self.scheduler.template("offline_login_prompt.bmp", (780, 450, 1140, 660), frame, threshold=.98)[0]:
                reason = "offline_prompt"
            elif not self.scheduler.healthy(frame):
                reason = "blank_or_unhealthy"
            elif (self.scheduler._combat_active_visible("arena", frame)
                  or self.scheduler._combat_active_visible("survival", frame)
                  or self.scheduler.template("survival_reset_confirm_yes.bmp", (701, 469, 1151, 719), frame)[0]):
                reason = "activity_occluded_identity"
            else:
                reason = "identity_unverified"
            self.event("multi_solo_account_stalled", account, checks=checks,
                       reason=reason, detail=str(exc))
            if checks >= 2:
                self.excluded.add(name)
                self.exclusion_reasons[name] = reason
                if reason == "offline_prompt":
                    self.disconnected.add(name)
                self.event("multi_solo_account_excluded", account, checks=checks,
                           reason=reason, detail="两次检查未取得可操作身份，本轮不再检查")
            self.save_progress("account_check")
            return False

    def _run_task(self, runner, task):
        s, account = self.scheduler, runner.account
        if task in getattr(self, 'blocked_tasks', {}).get(account.name, ()):
            self.event('multi_solo_task_blocked_original_pending', account, task=task,
                       detail='原任务已优先对账且仍未决，本轮不重复；继续其他已选任务')
            return
        started_at = time.monotonic()
        rec = runner.record(task)
        if (rec.get("status") == "done" and rec.get("task_completed")
                and rec.get("home_returned") and not rec.get('followup_check_due')
                and (task != 'daruma' or daruma_verified(rec))
                and (task != 'arena' or account.name in getattr(self,'arena_rewards_audited',set()))):
            self.event("multi_solo_task_skip_done", account, task=task)
            return
        runner.current = task
        rec.update(status="running", last_error="")
        runner.save()
        self.event("multi_solo_task_started", account, task=task)
        s.navigator = runner
        original_accounts = s.accounts
        observation = s.begin_task_observation(account, task)
        try:
            if task == 'survival' and rec.get('followup_check_due'):
                rec['status'] = 'pending'
                self._survival_followup(runner)
                return
            if task == 'arena' and rec.get('task_completed'):
                s.recheck_arena_rewards(account)
            if task == 'daruma' and rec.get('task_completed') and not daruma_verified(rec):
                raise hs.PendingError('达摩完成标记缺少奖励归零证据，保留待处理')
            if not rec.get('task_completed'):
                if (task == 'survival' and runner.visible(
                        'survival_anchor.bmp', (201,69,601,419))
                        and s.state.action_status(account.name, 'survival_battle_step_1') == 'done'):
                    runner.formation_checked = True
                    self.event('multi_solo_survival_formation_reused', account,
                               detail='同日首场已核实，沿用已检查阵容继续断点')
                getattr(runner, task)()
            frame = s.capture()
            if not (s.main_ready(frame) and s.template(
                    'leaf_village_name_current.bmp', (1690,110,1920,148), frame,
                    threshold=.92)[0]):
                runner.return_to_main()
                frame = s.capture()
            if not (s.main_ready(frame) and s.template(
                    'leaf_village_name_current.bmp', (1690,110,1920,148), frame,
                    threshold=.92)[0]):
                raise hs.PendingError(f"{task}结束后未确认木叶村主界面；保留断点")
            if task == 'anniversary' and rec.get('availability',{}).get('status')=='not_open':
                rec.update(status='skipped',task_completed=False,home_returned=True,last_error='')
                runner.save()
                return
            rec.update(status="done", task_completed=True, home_returned=True,
                       finished_at=hs.now_iso(), last_error="")
            if task == "survival":
                rec["followup_check_due"] = not (getattr(runner, 'survival_inline_complete', False) is True)
            if task == "arena":
                if not hasattr(self,'arena_rewards_audited'):
                    self.arena_rewards_audited = set()
                self.arena_rewards_audited.add(account.name)
                rec["arena_requested_wins"] = self.arena_wins
                request = s.state.global_state.get('arena_requests', {}).get(account.name)
                if isinstance(request, dict):
                    request.update(status='done', completed_at=hs.now_iso())
                    s.state.save_global()
            runner.save()
            self.event("multi_solo_task_complete", account, task=task,
                       completed=rec.get('completed'), home_returned=True,
                       task_completed=True, followup_check_due=rec.get('followup_check_due', False))
        except hs.AccountOffline:
            rec.update(status='pending', last_error='中央你已掉线弹窗已确认，本轮跳过账号')
            runner.save()
            self.skip_disconnected(account, task)
            return
        except hs.BattleStalled as exc:
            rec.update(status='pending', last_error=str(exc))
            runner.save()
            self.skip_stalled(account, task, exc)
            return
        except (hs.PendingError, hs.SafetyError) as exc:
            rec.update(status="pending", last_error=str(exc))
            runner.save()
            self.event("multi_solo_task_pending", account, task=task, detail=str(exc))
            mapping = s._saved_tab_mapping()
            if (task in ("arena", "survival") and isinstance(mapping, dict)
                    and mapping.get(account.name) == account.tab_x
                    and s._combat_active_visible(task, s.capture())):
                self.deferred[account.name] = task
                self.deferred_at[account.name] = time.monotonic()
                self.event("multi_solo_combat_deferred", account, task=task,
                           detail="已核实仍在战斗，保留断点并处理其他账号")
                return
            if task == 'ranking' and rec.get('phase', 'ready') == 'ready':
                frame = s.capture()
                if (s.template('ranking_page.bmp', (790, 95, 1080, 195), frame, threshold=.95)[0]
                        and s.template('ranking_counter_label.bmp', (1470, 153, 1586, 184), frame, threshold=.95)[0]
                        and not s.template('ranking_power_warning.bmp', (790, 505, 1130, 560), frame, threshold=.95)[0]
                        and not s.template('ranking_result_confirm.bmp', (860, 640, 1060, 710), frame, threshold=.95)[0]):
                    if s.click_template(account, 'ranking_close.bmp', (1600, 95, 1670, 160),
                                        17, 17, 'ranking_close_pending_counter',
                                        ['ranking_page.bmp', 'ranking_counter_label.bmp']):
                        s.wait_until_main(8)
            if s.active == account and not s.ensure_account_main_before_release(account, task):
                raise hs.LeaseBlocked(f"{account.name}未安全回村，不能切换下一账号") from exc
            if task == 'survival' and hasattr(self, 'skipped_tasks'):
                # Backfill only through the original state machine; a pending
                # battle is never cleared or replaced by this scheduling hint.
                self.note_skipped(account, task)
        finally:
            s.end_task_observation(observation, complete=rec.get('status') == 'done' and not rec.get('followup_check_due'))
            original_accounts[:] = [s.by_name.get(a.name,a) for a in original_accounts]
            s.accounts = original_accounts
            s.navigator = None
            self.event('multi_solo_task_elapsed', account, task=task,
                       seconds=round(time.monotonic() - started_at, 3),
                       status=rec.get('status', 'unknown'))

    def probe_deferred(self, account, *, force=False):
        task = self.deferred.get(account.name)
        if not task or account.name in self.excluded:
            return False
        now = time.monotonic()
        probed = getattr(self, 'deferred_probed_at', {})
        last = probed.get(account.name, getattr(self, 'deferred_at', {}).get(account.name))
        if not force and last is not None and now - last < DEFERRED_RECHECK_SECONDS:
            self.event('multi_solo_deferred_probe_throttled', account, task=task,
                       seconds_remaining=round(DEFERRED_RECHECK_SECONDS-(now-last),2))
            return False
        self.deferred_probed_at = probed
        probed[account.name] = now
        try:
            self.scheduler.switch_account_for_combat(account, task, running=True)
            self.stall_checks[account.name] = 0
            self.event("multi_solo_deferred_healthy", account, task=task)
            return True
        except hs.AccountOffline as exc:
            self.skip_disconnected(exc.account, task)
        except (hs.PendingError, hs.SafetyError) as exc:
            checks = self.stall_checks.get(account.name, 0) + 1
            self.stall_checks[account.name] = checks
            self.event("multi_solo_account_stalled", account, checks=checks, detail=str(exc))
            if checks >= 2:
                self.excluded.add(account.name)
                self.exclusion_reasons[account.name] = "combat_page_unverified"
                self.event("multi_solo_account_excluded", account, checks=checks,
                           reason="combat_page_unverified", detail="战斗标签两次无法确认健康状态")
        return False


    def _run_survival_stage(self):
        """Start each eligible survival battle first, then poll each account independently."""
        s = self.scheduler
        original_accounts = s.accounts
        original_navigator = getattr(s, 'navigator', None)
        original_navigators = getattr(s, '_combat_navigators', None)
        original_observation = getattr(s, '_task_observation', None)
        original_inline = getattr(s, 'survival_inline_two_runs', None)
        candidates = []
        runners = {}
        pending_detail = ''
        started_at = time.monotonic()
        try:
            for configured in tuple(original_accounts):
                account = s.by_name.get(configured.name, configured)
                runner = self.runner_for(account)
                runner.current = 'survival'
                record = runner.record('survival')
                if (record.get('status') == 'done' and record.get('task_completed')
                        and record.get('home_returned') and not record.get('followup_check_due')):
                    self.event('multi_solo_task_skip_done', account, task='survival')
                    continue
                if (account.name not in self.startup_returned
                        or account.name in self.manual_deferred
                        or account.name in self.deferred
                        or account.name in self.excluded
                        or account.name in self.disconnected
                        or 'survival' in getattr(self, 'blocked_tasks', {}).get(account.name, ())):
                    self.note_skipped(account, 'survival')
                    continue
                if not self.check_account(account):
                    self.note_skipped(account, 'survival')
                    continue
                s.navigator = runner
                frame = s.capture()
                page_visible = runner.visible('survival_anchor.bmp', (201, 69, 601, 419), frame)
                actions = s.state.accounts.get(account.name, {}).get('actions', {})
                has_pending_battle = any(
                    key.startswith(('survival_battle_step_', 'survival_result_confirm_'))
                    and isinstance(value, dict) and value.get('status') == 'pending'
                    for key, value in actions.items())
                has_survival_attempt = any(
                    key.startswith('survival_battle_step_') and isinstance(value, dict)
                    and value.get('status') in ('pending', 'done')
                    for key, value in actions.items())
                in_combat = (s._battle_hud_visible(frame)
                             or s.template('survival_result_confirm.bmp', (701, 519, 1151, 819), frame)[0]
                             or s.template('survival_reward_result_confirm.bmp', (701, 569, 1151, 819), frame)[0])
                if (in_combat or has_pending_battle
                        or (page_visible and (has_survival_attempt or record.get('followup_check_due')))):
                    # Resume the exact page/attempt. Never change formation while
                    # a battle or an unresolved result may still be active.
                    runner.formation_checked = True
                elif not runner.formation_checked:
                    try:
                        runner.ensure_formation_once()
                    except hs.AccountOffline as exc:
                        self.skip_disconnected(account, 'survival')
                        continue
                    except hs.PendingError as exc:
                        record.update(status='pending', task_completed=False, home_returned=False,
                                      last_error=str(exc))
                        runner.save()
                        self.deferred[account.name] = 'survival'
                        self.deferred_at[account.name] = time.monotonic()
                        self.event('multi_solo_survival_prepare_pending', account, detail=str(exc))
                        continue
                record.update(status='running', last_error='')
                runner.save()
                runners[account.name] = runner
                candidates.append(account)
                self.event('multi_solo_task_started', account, task='survival')
            if not candidates:
                self.event('multi_solo_survival_no_eligible_accounts',
                           detail='没有需要继续执行的生存演习账号')
                return True

            s.accounts = candidates
            s._combat_navigators = runners
            s.survival_inline_two_runs = True
            # Each CombatSlot owns its watchdog. Do not let one global task
            # observation accidentally monitor a different account after a tab switch.
            s._task_observation = None
            self.event('multi_solo_survival_interleaved_start',
                       accounts=[account.name for account in candidates],
                       detail='每号确认开战后立即切号；战斗中确认x2；结果与积分按账号轮询')
            try:
                s.run_combat_interleaved('survival')
            except hs.PendingError as exc:
                pending_detail = str(exc)
                self.event('multi_solo_survival_stage_pending', detail=pending_detail)

            for account in candidates:
                runner = runners[account.name]
                record = runner.record('survival')
                if s._account_module_complete(account, 'survival'):
                    runner.survival_inline_complete = True
                    record.update(status='done', task_completed=True, home_returned=True,
                                  followup_check_due=False, finished_at=hs.now_iso(), last_error='',
                                  completed=2 if s.state.action_status(
                                      account.name, 'survival_second_run_started') == 'done' else 1)
                    runner.save()
                    self.deferred.pop(account.name, None)
                    self.deferred_at.pop(account.name, None)
                    self.event('multi_solo_task_complete', account, task='survival',
                               task_completed=True, home_returned=True, followup_check_due=False)
                else:
                    detail = pending_detail or '生存演习尚有战斗、积分、领奖或重置状态待核实'
                    record.update(status='pending', task_completed=False, home_returned=False,
                                  last_error=detail)
                    runner.save()
                    # Keep this account away from unrelated content clicks until
                    # the original survival action can be reconciled.
                    self.deferred[account.name] = 'survival'
                    self.deferred_at[account.name] = time.monotonic()
                    self.event('multi_solo_task_pending', account, task='survival', detail=detail)
            self.event('multi_solo_survival_stage_elapsed',
                       seconds=round(time.monotonic() - started_at, 3),
                       accounts=[account.name for account in candidates])
            return True
        finally:
            s.accounts = original_accounts
            s.navigator = original_navigator
            s._task_observation = original_observation
            if original_navigators is None:
                if hasattr(s, '_combat_navigators'):
                    delattr(s, '_combat_navigators')
            else:
                s._combat_navigators = original_navigators
            if original_inline is None:
                if hasattr(s, 'survival_inline_two_runs'):
                    delattr(s, 'survival_inline_two_runs')
            else:
                s.survival_inline_two_runs = original_inline

    def _run_ranking_stage(self):
        """Yield to the next account immediately after each verified end-battle click."""
        s = self.scheduler
        original_accounts = s.accounts
        original_navigator = getattr(s, 'navigator', None)
        original_observation = getattr(s, '_task_observation', None)
        candidates = []
        pending = {}
        next_polls = {}
        wait_watches = {}
        started_at = time.monotonic()
        try:
            for configured in tuple(original_accounts):
                account = s.by_name.get(configured.name, configured)
                runner = self.runner_for(account)
                record = runner.record('ranking')
                if (record.get('status') == 'done' and record.get('task_completed')
                        and record.get('home_returned') and record.get('zero_verified')
                        and record.get('remaining') == 0):
                    self.event('multi_solo_task_skip_done', account, task='ranking')
                    continue
                if (account.name not in self.startup_returned
                        or account.name in self.manual_deferred
                        or account.name in self.deferred
                        or account.name in self.excluded
                        or account.name in self.disconnected
                        or 'ranking' in getattr(self, 'blocked_tasks', {}).get(account.name, ())):
                    self.note_skipped(account, 'ranking')
                    continue
                candidates.append(account)
                pending[account.name] = account
                self.event('multi_solo_task_started', account, task='ranking')
            if not candidates:
                self.event('multi_solo_ranking_no_eligible_accounts',
                           detail='没有需要继续执行的排位赛账号')
                return True

            s.accounts = candidates
            s._task_observation = None
            while pending:
                if s.check_pause_boundary():
                    self.save_progress('paused')
                    return False
                progressed = False
                for account in tuple(pending.values()):
                    s.check_stop()
                    if s.check_pause_boundary():
                        self.save_progress('paused')
                        return False
                    now = time.monotonic()
                    if now < next_polls.get(account.name, 0):
                        continue
                    runner = self.runner_for(account)
                    runner.current = 'ranking'
                    record = runner.record('ranking')
                    phase = record.get('phase', 'ready')
                    attempt = record.get('completed', 0) + 1
                    from hyol_ranking import action_base as ranking_action_base
                    action_base = ranking_action_base(record,attempt)
                    has_pending_action = any(
                        s.state.action_status(account.name, action_base + '_' + suffix) == 'pending'
                        for suffix in ('start', 'confirm', 'end', 'result', 'result_recover'))
                    running = phase != 'ready' or has_pending_action
                    s.navigator = runner
                    runner.interleave_after_end = True
                    persist_progress = True
                    try:
                        s.switch_account_for_combat(account, 'ranking', running=running)
                        record.update(status='running', last_error='')
                        runner.save()
                        result = runner.ranking()
                        if result in ('yield_after_end', 'yield_waiting_result'):
                            if result == 'yield_after_end':
                                record['ranking_interleave_wait_checks'] = 0
                                wait_watches.pop(account.name, None)
                                detail = '结束战斗动作已发送，立即切换账号；结果和剩余次数延后核实'
                                event_name = 'multi_solo_ranking_end_sent_yield'
                                progressed = True
                            else:
                                checks = int(record.get('ranking_interleave_wait_checks', 0)) + 1
                                record['ranking_interleave_wait_checks'] = checks
                                observe_ranking_wait(s, account, record, wait_watches)
                                detail = '排位结果尚未稳定，已让出本轮继续处理其他账号'
                                event_name = 'multi_solo_ranking_wait_yield'
                                persist_progress = checks == 1 or checks % 5 == 0
                            record.update(status='pending', task_completed=False, home_returned=False,
                                          last_error=detail)
                            next_polls[account.name] = time.monotonic() + .4
                            if persist_progress or result == 'yield_after_end':
                                runner.save()
                                self.event(event_name, account, detail=detail)
                        else:
                            frame = s.capture()
                            if not (s.main_ready(frame) and s.template(
                                    'leaf_village_name_current.bmp', (1690, 110, 1920, 148),
                                    frame, threshold=.92)[0]):
                                runner.return_to_main()
                                frame = s.capture()
                            if not (s.main_ready(frame) and s.template(
                                    'leaf_village_name_current.bmp', (1690, 110, 1920, 148),
                                    frame, threshold=.92)[0]):
                                raise hs.PendingError('排位次数归零后未确认木叶村主界面')
                            if record.get('zero_verified') is not True or record.get('remaining') != 0:
                                raise hs.PendingError('排位未正向确认剩余次数归零，不能标记完成')
                            record.pop('ranking_interleave_wait_checks', None)
                            record.update(status='done', task_completed=True, home_returned=True,
                                          finished_at=hs.now_iso(), last_error='')
                            runner.save()
                            pending.pop(account.name, None)
                            self.deferred.pop(account.name, None)
                            self.deferred_at.pop(account.name, None)
                            self.event('multi_solo_task_complete', account, task='ranking',
                                       task_completed=True, home_returned=True, remaining=0)
                            progressed = True
                    except hs.AccountOffline as exc:
                        self.skip_disconnected(account, 'ranking')
                        pending.pop(account.name, None)
                        progressed = True
                    except hs.BattleStalled as exc:
                        record.update(status='pending',task_completed=False,home_returned=False,
                                      last_error=str(exc))
                        runner.save()
                        self.skip_stalled(account, 'ranking', exc)
                        pending.pop(account.name, None)
                        progressed = True
                    except (hs.PendingError, hs.SafetyError) as exc:
                        record.update(status='pending', task_completed=False, home_returned=False,
                                      last_error=str(exc))
                        runner.save()
                        frame = s.capture()
                        mapping = s._saved_tab_mapping()
                        custody = (getattr(s, 'active', None) == account
                                   and isinstance(mapping, dict) and mapping.get(account.name) == account.tab_x
                                   and s._generic_active_tab_x(frame) == account.tab_x
                                   and s._combat_active_visible('ranking', frame))
                        if not custody and not s.ensure_account_main_before_release(account, 'ranking'):
                            raise hs.LeaseBlocked(f'{account.name}排位异常后无法确认安全交接') from exc
                        # A missing entry on a verified idle village is a
                        # task-local skip, not a battle occupying this tab.
                        # Retain its original record/actions and release the
                        # other selected tasks through the existing audit.
                        released = not custody and self._allow_other_selected_tasks(account,str(exc))
                        if not released:
                            self.deferred[account.name] = 'ranking'
                            self.deferred_at[account.name] = time.monotonic()
                        self.event('multi_solo_ranking_stage_pending', account, detail=str(exc))
                        pending.pop(account.name, None)
                        progressed = True
                    finally:
                        runner.interleave_after_end = False
                    if persist_progress:
                        self.save_progress('ranking')
                if not progressed:
                    s.wait_interruptible(.4)
            self.event('multi_solo_ranking_stage_elapsed',
                       seconds=round(time.monotonic() - started_at, 3),
                       accounts=[account.name for account in candidates])
            return True
        finally:
            s.accounts = original_accounts
            s.navigator = original_navigator
            s._task_observation = original_observation

    def _run_arena_stage(self):
        """Start arena battles across eligible accounts first, then poll them cooperatively."""
        s = self.scheduler
        original_accounts = s.accounts
        original_eligible = getattr(s, 'arena_eligible_accounts', None)
        original_mode = getattr(s, 'arena_target_mode', None)
        original_target = getattr(s, 'arena_victory_target', None)
        original_limit = getattr(s, 'arena_attempt_limit', None)
        candidates = []
        runners = {}
        started_at = time.monotonic()
        try:
            for configured in tuple(original_accounts):
                account = s.by_name.get(configured.name, configured)
                if account.name in getattr(self, 'arena_rewards_audited', set()):
                    self.event('multi_solo_task_skip_done', account, task='arena',
                               detail='本轮已核实战令归零和奖励领取，不重复开启')
                    continue
                if (account.name not in self.startup_returned
                        or account.name in self.manual_deferred
                        or account.name in self.deferred
                        or account.name in self.excluded
                        or account.name in self.disconnected):
                    self.note_skipped(account, 'arena')
                    runner = self.runner_for(account)
                    record = runner.record('arena')
                    if record.get('status') == 'running':
                        record.update(status='pending', task_completed=False, home_returned=False,
                                      last_error='账号本轮暂缓，未执行竞技场')
                        runner.save()
                    continue
                if self.check_account(account):
                    runner = self.runner_for(account)
                    runners[account.name] = runner
                    record = runner.record('arena')
                    request = s.state.global_state.get('arena_requests', {}).get(account.name, {})
                    same_target = (isinstance(request, dict) and request.get('status') == 'done'
                                   and request.get('target_mode', 'wins') == self.arena_target_mode
                                   and (self.arena_target_mode == 'tickets'
                                        or request.get('requested_wins') == self.arena_wins))
                    if record.get('task_completed') and same_target:
                        runner.current = 'arena'
                        s.recheck_arena_rewards(account)
                        frame = s.capture()
                        if not (s._arena_daily_rewards_complete(account) and s.main_ready(frame)
                                and s.template('leaf_village_name_current.bmp', (1690,110,1920,148), frame,
                                               threshold=.92)[0]):
                            raise hs.PendingError('已完成竞技场请求的奖励或回村复查尚未核实；禁止重建目标')
                        if not hasattr(self, 'arena_rewards_audited'):
                            self.arena_rewards_audited = set()
                        self.arena_rewards_audited.add(account.name)
                        record.update(status='done', home_returned=True, followup_check_due=False, last_error='')
                        runner.save()
                        self.event('multi_solo_task_skip_done', account, task='arena',
                                   detail='持久完成请求已复查奖励及回村，不追加目标')
                        continue
                    candidates.append(account)
                else:
                    self.note_skipped(account, 'arena')
                    runner = self.runner_for(account)
                    record = runner.record('arena')
                    if record.get('status') == 'running':
                        record.update(status='pending', task_completed=False, home_returned=False,
                                      last_error='账号身份或在线状态未确认，本轮暂缓竞技场')
                        runner.save()
            if not candidates:
                self.event('multi_solo_arena_no_eligible_accounts',
                           detail='没有通过身份与在线检查的竞技场账号')
                return

            s.accounts = candidates
            s.arena_target_mode = self.arena_target_mode
            s.arena_victory_target = self.arena_wins
            requests = []
            for account in candidates:
                runner = runners[account.name]
                runner.current = 'arena'
                record = runner.record('arena')
                record.update(status='running', task_completed=False, home_returned=False,
                              followup_check_due=False, last_error='')
                runner.save()
                s.prepare_additional_arena_request(
                    account, self.arena_wins, target_mode=self.arena_target_mode)
                request = s.state.global_state.get('arena_requests', {}).get(account.name, {})
                if isinstance(request, dict):
                    requests.append(request)
                self.event('multi_solo_task_started', account, task='arena',
                           target_mode=self.arena_target_mode,
                           requested_wins=self.arena_wins,
                           detail=(('先逐号发起战斗，按实际战令归零结束'
                                    if self.arena_target_mode == 'tickets'
                                    else f'先逐号发起战斗，每号目标新增{self.arena_wins}胜')
                                   if getattr(s, 'arena_calendar_policy_enabled', False) else
                                   '先逐号发起战斗，每账号每轮最多3场，战令目标未完成下次继续'
                                   if self.arena_target_mode == 'tickets'
                                   else f'每号目标新增{self.arena_wins}胜，本轮最多3场，未完成下次继续'))
            if requests:
                s.arena_attempt_limit = max(
                    int(item.get('attempt_limit', hs.ARENA_MAX_ATTEMPTS))
                    for item in requests if isinstance(item, dict))
            pending_detail = ''
            try:
                s.run_combat_interleaved('arena')
            except hs.PendingError as exc:
                # A single unresolved slot must not stop polling/starting the
                # other accounts. The scheduler has already quarantined unsafe
                # slots and preserved their action ledgers.
                pending_detail = str(exc)
                self.event('multi_solo_arena_stage_pending', detail=pending_detail)

            if not hasattr(self, 'arena_rewards_audited'):
                self.arena_rewards_audited = set()
            for account in candidates:
                runner = runners[account.name]
                record = runner.record('arena')
                complete = s._account_module_complete(account, 'arena')
                if complete:
                    record.update(status='done', task_completed=True, home_returned=True,
                                  followup_check_due=False, finished_at=hs.now_iso(), last_error='',
                                  arena_requested_wins=self.arena_wins,
                                  arena_target_mode=self.arena_target_mode)
                    self.arena_rewards_audited.add(account.name)
                    runner.save()
                    self.event('multi_solo_task_complete', account, task='arena',
                               task_completed=True, home_returned=True,
                               target_mode=self.arena_target_mode,
                               completed=s._arena_victory_count(account))
                else:
                    detail = pending_detail or '竞技场目标、战令归零或奖励领取尚未全部核实'
                    request = s.state.global_state.get('arena_requests', {}).get(account.name, {})
                    limited = isinstance(request, dict) and request.get('status') == 'limited'
                    record.update(status='pending', task_completed=False,
                                  home_returned=limited and request.get('home_returned') is True,
                                  followup_check_due=False, last_error=detail,
                                  arena_requested_wins=self.arena_wins,
                                  arena_target_mode=self.arena_target_mode)
                    runner.save()
                    self.event('multi_solo_task_pending', account, task='arena', detail=detail,
                               target_mode=self.arena_target_mode)
        finally:
            s.accounts = original_accounts
            if original_eligible is None:
                if hasattr(s, 'arena_eligible_accounts'):
                    delattr(s, 'arena_eligible_accounts')
            else:
                s.arena_eligible_accounts = original_eligible
            if original_mode is None:
                if hasattr(s, 'arena_target_mode'):
                    delattr(s, 'arena_target_mode')
            else:
                s.arena_target_mode = original_mode
            if original_target is None:
                if hasattr(s, 'arena_victory_target'):
                    delattr(s, 'arena_victory_target')
            else:
                s.arena_victory_target = original_target
            if original_limit is None:
                if hasattr(s, 'arena_attempt_limit'):
                    delattr(s, 'arena_attempt_limit')
            else:
                s.arena_attempt_limit = original_limit
            self.event('multi_solo_arena_stage_elapsed',
                       seconds=round(time.monotonic() - started_at, 3),
                       target_mode=self.arena_target_mode,
                       accounts=[account.name for account in candidates])

    def _survival_followup(self, runner):
        """Revisit survival after the other solo tasks, using the real page state."""
        s, account = self.scheduler, runner.account
        rec = runner.record("survival")
        if not rec.get("followup_check_due"):
            return
        if 'survival' in getattr(self, 'blocked_tasks', {}).get(account.name, ()):
            return
        pending_task = self.unresolved_combat_task(account)
        if (pending_task and pending_task != 'survival') or account.name in getattr(self, 'deferred', {}):
            self.event('multi_solo_survival_followup_blocked', account,
                       task=pending_task or self.deferred[account.name],
                       detail='原战斗未决，保留生存补跑断点，不进入生存页面')
            return
        s.navigator = runner
        original_accounts = s.accounts
        observation = s.begin_task_observation(account, 'survival')
        try:
            frame = s.capture()
            original_battle_visible = (s._battle_hud_visible(frame) is True or any(
                runner.visible(name, roi, frame) is True for name, roi in (
                    ('survival_result_confirm.bmp', (701,519,1151,819)),
                    ('survival_reward_result_confirm.bmp', (701,569,1151,819)))))
            if original_battle_visible:
                actions = s.state.accounts.get(account.name, {}).get('actions', {})
                if not any(key.startswith(('survival_battle_step_', 'survival_result_confirm_'))
                           and isinstance(row, dict) and row.get('status') == 'pending'
                           for key, row in actions.items()):
                    raise hs.PendingError('当前生存战斗缺少原未决凭据，不接管或重新进入活动')
                # A follow-up can be interrupted inside its second run. Resume
                # that exact battle/result before trying to open a village menu.
                runner.formation_checked = True
                s.accounts = [account]
                self.event('multi_solo_survival_original_battle_resumed', account,
                           detail='先核对原战斗和结果，不重新打开活动或切换阵容')
                s.run_combat_interleaved('survival')
                if not s._account_module_complete(account, 'survival'):
                    raise hs.PendingError('原生存战斗、积分或领奖仍未核实，保留补跑断点')
                if not s.main_ready(s.capture()):
                    runner.return_to_main()
                if not s.main_ready(s.capture()):
                    raise hs.PendingError('原生存战斗对账后未确认回村')
                runner.survival_inline_complete = True
                completed = (2 if s.state.action_status(account.name, 'survival_second_run_started') == 'done'
                             else max(1, rec.get('completed', 0)))
                rec.update(status='done', home_returned=True, followup_check_due=False,
                           task_completed=True, completed=completed, finished_at=hs.now_iso(), last_error='')
                runner.save()
                self.event('multi_solo_survival_second_run_complete', account)
                return
            if not runner.formation_checked:
                if runner.visible("survival_anchor.bmp", (201, 69, 601, 419)):
                    runner.close_page("survival")
                runner.ensure_formation_once()
            runner.enter("survival")
            page = (201, 69, 601, 419)
            score = (751, 769, 1151, 1031)
            reset = (851, 769, 1251, 1031)
            score_deadline = time.monotonic() + 12
            while True:
                frame = s.capture()
                if (runner.visible("survival_score_0.bmp", score, frame)
                        or s._survival_score_twenty_visible(frame)
                        or s._survival_partial_score(frame) in (20,80,140,160,200)
                        or s._survival_full_score_visible(frame)):
                    break
                if time.monotonic() >= score_deadline:
                    break
                s.wait_interruptible(.4)
            if not runner.visible("survival_anchor.bmp", page, frame):
                raise hs.PendingError("生存补跑页面未确认")
            if s.reconcile_survival_partial_reset(account, frame) is True:
                self._queue_survival_second_run(account, '原重置剩余次数1→0及部分积分已核实；不推断战斗次数')
            if runner.visible("survival_score_0.bmp", score, frame):
                if s.state.action_status(account.name, "survival_second_run_started") != "done":
                    if not (rec.get('task_completed') and rec.get('completed', 0) >= 1):
                        raise hs.PendingError('0/220可见，但生存首轮完成记录不足')
                    self._queue_survival_second_run(account, '首轮完成后页面已显示0/220')
                    self.event('multi_solo_survival_reset_observed', account)
            elif s._survival_score_twenty_visible(frame):
                if not (rec.get('task_completed') and rec.get('completed', 0) >= 1):
                    raise hs.PendingError('20/220可见，但生存首轮完成记录不足')
                if s.state.action_status(account.name, 'survival_second_run_started') != 'done':
                    self._queue_survival_second_run(account, '首轮后页面已显示20/220，重置后的首场待续')
                self.event('multi_solo_survival_reset_observed', account, score=20)
            elif s._survival_partial_score(frame) in (20,80,140,160,200):
                if s.state.action_status(account.name, 'survival_second_run_started') != 'done':
                    if not (rec.get('task_completed') and rec.get('completed', 0) >= 1
                            and s.state.action_status(account.name, 'survival_reset') == 'done'):
                        raise hs.PendingError('部分积分已识别，但首轮完成或原重置证据不足；不补记战斗')
                    self._queue_survival_second_run(account, '原重置已核实，部分积分从原下一场续作')
                self.event('multi_solo_survival_partial_second_run', account, score=s._survival_partial_score(frame),
                           detail='积分只描述页面，沿用已核实动作寻找下一场，不补计次数')
            elif s._survival_full_score_visible(frame):
                reset_exhausted = (runner.visible("survival_reset_zero_text.bmp", reset, frame)
                                   or runner.visible("survival_reset_zero_digit.bmp", reset, frame))
                if reset_exhausted and runner.visible("survival_final_claimed.bmp", (1151,769,1451,1031), frame):
                    runner.close_page("survival")
                    rec.update(status='done', task_completed=True, home_returned=True,
                               followup_check_due=False, last_error='')
                    runner.save()
                    self.event("multi_solo_survival_no_reset_needed", account)
                    return
                if not reset_exhausted and runner.visible("survival_reset.bmp", reset, frame):
                    s._ensure_survival_reset(account)
                    self._queue_survival_second_run(account, '生存首轮已核实重置，后置补跑')
                elif runner.visible("survival_final_claimed.bmp", (1151, 769, 1451, 1031), frame):
                    runner.close_page("survival")
                    if not s.main_ready(s.capture()):
                        raise hs.PendingError("生存无重置后未确认回村")
                    rec.update(status='done', task_completed=True, home_returned=True,
                               followup_check_due=False, last_error='')
                    runner.save()
                    self.event("multi_solo_survival_no_reset_needed", account)
                    return
                else:
                    raise hs.PendingError("生存已满分，但重置/最终领取状态无法唯一识别")
            else:
                evidence = s.save_raw_evidence(frame, account,
                                               'survival_followup_score_unknown', 'pending')
                if s.state.action_status(account.name, 'survival_second_run_started') != 'done':
                    raise hs.PendingError(f"生存积分未知且第二轮重置未核实；画面={evidence}")
                # Resume the existing second run from counters and its audited
                # attempts. Never synthesize a completed battle from this score.
                self.event('multi_solo_survival_partial_second_run', account, evidence=str(evidence))
            runner.survival()
            if not s.main_ready(s.capture()):
                runner.return_to_main()
            rec.update(status="done", home_returned=True, followup_check_due=False,
                       task_completed=True, completed=2, finished_at=hs.now_iso(), last_error="")
            runner.save()
            self.event("multi_solo_survival_second_run_complete", account)
        except hs.AccountOffline as exc:
            rec.update(status='pending', last_error=str(exc))
            runner.save()
            self.skip_disconnected(account, 'survival')
        except hs.BattleStalled as exc:
            rec.update(status='pending', last_error=str(exc))
            runner.save()
            self.skip_stalled(account, 'survival', exc)
        except (hs.PendingError, hs.SafetyError) as exc:
            rec.update(status="pending", last_error=str(exc))
            runner.save()
            self.event("multi_solo_survival_followup_pending", account, detail=str(exc))
            if s.active == account and not s.ensure_account_main_before_release(account, "survival"):
                raise hs.LeaseBlocked(f"{account.name}生存补跑未安全回村") from exc
        finally:
            s.end_task_observation(observation, complete=not rec.get('followup_check_due'))
            original_accounts[:] = [s.by_name.get(a.name,a) for a in original_accounts]
            s.accounts = original_accounts
            s.navigator = None

    def _queue_survival_second_run(self, account, reason):
        s = self.scheduler
        actions = s.state.accounts.get(account.name, {}).get('actions', {})
        if any(key.startswith(('survival_battle_step_', 'survival_result_confirm_')) and isinstance(row, dict)
               and row.get('status') == 'pending' for key, row in actions.items()):
            raise hs.PendingError('生存首轮仍有未决战斗，禁止覆盖为第二轮')
        archives = s.state.accounts[account.name].setdefault('survival_round_archives', {})
        reset = s.state.action_record(account.name, 'survival_reset')
        key = reset.get('operation_id', 'second_round') if isinstance(reset, dict) else 'second_round'
        if key not in archives:
            archives[key] = {'reason': reason, 'archived_at': hs.now_iso(),
                             'actions': json.loads(json.dumps({k:v for k,v in actions.items() if k.startswith('survival_')}))}
            s.state.save_account(account.name)
        self.event('multi_solo_survival_first_run_archived', account,
                   actions={key: value for key, value in s.state.accounts.get(account.name, {}).get('actions', {}).items()
                            if key.startswith('survival_')})
        for step in range(1, s._combat_limit('survival') + 1):
            action = s._combat_action_name('survival', step)
            for key in (action, f'{action}_start', f'survival_select_step_{step}',
                        f'survival_result_confirm_{step}'):
                s.state.set_action(account.name, key, 'new', reason)
        for key in ('survival_claim_rewards', 'survival_claim_yes', 'survival_reward_result_confirm'):
            s.state.set_action(account.name, key, 'new', reason)
        s.state.set_action(account.name, 'survival_second_run_started', 'done', reason)
        s.state.set_module(account.name, 'survival', 'new', '第二轮后置补跑')
        self.event('multi_solo_survival_second_run_queued', account, reason=reason)

    def unresolved_combat_task(self, account):
        reconcile = getattr(self.scheduler, 'reconcile_survival_finished_speed_controls', None)
        if callable(reconcile):
            reconcile(account)
        actions = self.scheduler.state.accounts.get(account.name, {}).get('actions', {})
        if not isinstance(actions, dict):
            actions = {}
        runner = getattr(self,'runners',{}).get(account.name)
        records = getattr(runner,'records',None)
        def owner(task):
            parent=records.get('training_record',{}) if isinstance(records,dict) else {}
            if ('training_record' in getattr(self,'tasks',())
                    and task in parent.get('planned_tasks',())):
                return 'training_record'
            return task
        for prefixes, task in ((('arena_', 'daily_arena_'), 'arena'),
                               (('ranking_', 'daily_ranking_'), 'ranking'),
                               (('survival_', 'daily_survival_'), 'survival'),
                               (('daruma_',), 'daruma' if 'daruma' in getattr(self, 'tasks', ()) else 'weekend'),
                               (('puzzle_', 'daily_weekend_'), 'puzzle' if 'puzzle' in getattr(self,'tasks',()) else 'weekend')):
            from hyol_manual_continuation import quarantined_external_action
            if any(key.startswith(prefixes) and isinstance(record, dict)
                   and record.get('status') == 'pending'
                   and not quarantined_external_action(record,account.name)
                   for key, record in actions.items()):
                return owner(task)
        if isinstance(records,dict):
            for task, record in records.items():
                if (isinstance(record,dict) and (record.get('status') in ('pending','running')
                                                or record.get('followup_check_due'))
                        and not self._task_waiting_on_main(account, task, record)):
                    # A pre-click recognition failure has no pending battle
                    # action, but still needs its original task resumed before
                    # the generic origin gate; otherwise every stage skips it.
                    return owner(task)
        return None

    def run_skipped_tasks(self):
        """Backfill only stages skipped for an unavailable tab, in task order."""
        s = self.scheduler
        for configured in tuple(s.accounts):
            account = s.by_name[configured.name]
            if account.name not in getattr(self, 'startup_returned', {account.name}):
                missing = self.skipped_tasks.get(account.name, [])
                if (not missing or account.name in self.excluded or account.name in self.deferred
                        or account.name in getattr(self, 'manual_deferred', set())
                        or account.name in getattr(self, 'startup_rechecked', set())):
                    continue
                if s.check_pause_boundary():
                    return False
                self.startup_rechecked.add(account.name)
                self.save_progress('startup_recheck')
                if not self.check_account(account):
                    continue
                try:
                    self.return_account_to_origin(account)
                except hs.AccountOffline as exc:
                    self.skip_disconnected(exc.account)
                    continue
                except (hs.PendingError, hs.SafetyError) as exc:
                    self.startup_pending[account.name] = str(exc)
                    self.event('multi_solo_start_recheck_pending', account, detail=str(exc))
                    self.save_progress('startup_recheck_pending')
                    if (s.active == account and not
                            s.ensure_account_main_before_release(account, 'startup_return_recheck')):
                        raise hs.LeaseBlocked(f'{account.name}开场复查未安全释放') from exc
                    continue
                self.startup_returned.add(account.name)
                self.startup_pending.pop(account.name, None)
                self.event('multi_solo_start_recovered_after_stages', account)
            missing = self.skipped_tasks.get(account.name, [])
            if not missing or account.name in self.excluded or account.name in self.deferred:
                continue
            if s.check_pause_boundary():
                return False
            if not self.check_account(account):
                continue
            runner = self.runner_for(account)
            for task in tuple(self.tasks):
                if task not in missing:
                    continue
                s.check_stop()
                if s.check_pause_boundary():
                    return False
                self._run_task(runner, task)
                if runner.record(task).get("status") == "done" and runner.record(task).get("home_returned"):
                    missing.remove(task)
                if account.name in self.deferred:
                    break
            self.save_progress("skipped_backfill")
        return True

    def finish_managed_accounts(self):
        self.scheduler._performance_task = 'managed'
        """Start managed mode last, only for accounts with a complete solo run."""
        if not getattr(self, 'managed_enabled', True):
            self.event('multi_solo_managed_disabled', detail='本轮最后进入托管未勾选')
            return True
        s = self.scheduler
        if 'escort' in self.tasks:
            expected=getattr(self,'requested_account_count',None) or len(s.accounts)
            if (len(s.accounts)!=expected or getattr(self,'initial_unavailable',set())
                    or any(not escort_verified(self.runner_for(a).record('escort')) for a in s.accounts)):
                self.event('multi_solo_managed_waiting_for_all_escort',
                    detail='先完成全部所选账号的押镖及日常0/3核实，再统一逐号进入托管')
                return True
        for configured in tuple(s.accounts):
            if s.check_pause_boundary():
                return False
            account = s.by_name[configured.name]
            if account.name not in getattr(self, 'startup_returned', {account.name}):
                continue
            if account.name in self.excluded or account.name in self.deferred:
                continue
            runner = self.runners.get(account.name)
            if runner is None or any(
                runner.record(task).get('status') != 'done'
                or not runner.record(task).get('task_completed')
                or not runner.record(task).get('home_returned')
                or runner.record(task).get('followup_check_due')
                or task == 'daruma' and not daruma_verified(runner.record(task))
                or task == 'escort' and not escort_verified(runner.record(task))
                for task in self.tasks
            ):
                self.event('multi_solo_managed_pending', account,
                           detail='所选任务未全部完成、回村或生存补跑未结束')
                continue
            if not self.check_account(account):
                continue
            try:
                s.navigator = runner
                observation = s.begin_task_observation(account, 'managed')
                runner.finish_solo_managed()
                self.event('multi_solo_managed_complete', account)
            except hs.AccountOffline as exc:
                self.skip_disconnected(exc.account, 'managed')
            except hs.BattleStalled as exc:
                rec = runner.record('managed')
                rec.update(status='pending', last_error=str(exc))
                runner.save()
                self.skip_stalled(account, 'managed', exc)
            except (hs.PendingError, hs.SafetyError) as exc:
                rec = runner.record('managed')
                rec.update(status='pending', last_error=str(exc))
                runner.save()
                self.event('multi_solo_managed_pending', account, detail=str(exc))
                if s.active == account and not s.ensure_account_main_before_release(account, 'managed'):
                    raise hs.LeaseBlocked(f'{account.name}托管状态未核实，不能切换下一账号') from exc
            finally:
                s.end_task_observation(observation, complete=runner.record('managed').get('status') == 'done')
                s.navigator = None
            self.save_progress('managed')
        return True

    def run(self, *, manage_lease=True, before_prepare=None):
        if not self.tasks:
            self.event('weekend_calendar_skip', detail='无可执行任务；未识别游戏窗口')
            return 2
        s = self.scheduler
        lease = hs.ForegroundRuntimeLease()
        if manage_lease and not lease.acquire():
            raise hs.PendingError("已有脚本占用游戏窗口")
        outcome = 'interrupted'
        from hyol_performance import PerformanceMeter
        meter = None
        try:
            meter = PerformanceMeter(s)
            self.event('multi_solo_run_started', tasks=list(self.tasks), managed_enabled=getattr(self, 'managed_enabled', True),
                       requested_account_count=getattr(self, 'requested_account_count', None))
            if getattr(self, 'calendar_plan', {}).get('calendar_skipped'):
                self.event('solo_calendar_filtered', **self.calendar_plan,
                           detail='执行日非周末，跳过达摩和周末活动；原请求及旧断点保留')
            context = getattr(self, 'run_context', None)
            if context and manage_lease:
                context.begin()
            s.start_f12_monitor()
            s.bind_window(wait=True, timeout_seconds=60)
            if context and context.binding is not None:
                context.adopt_solo(s)
                self.tab_count = len(context.binding['centers'])
                self.initial_unavailable = {f'标签{i}' for i in context.binding['unavailable']}
                s.accounts = s.accounts[:self.requested_account_count]
                s.by_name = {a.name:a for a in s.accounts}
                self.configured_names = tuple(s.by_name)
                self.stall_checks = {a.name:0 for a in s.accounts}
                if context.binding.get('resumed_original_binding'):
                    self.startup_returned=set(context.binding.get('training_prepared_accounts',())) & set(s.by_name)
                self.event('multi_solo_accounts_bound',online=list(s.by_name),reused=True)
            else:
                if not getattr(self, 'automatic_accounts', False):
                    self.discover_tabs()
                self.bind_accounts()
            if before_prepare is not None:
                before_prepare(self)
                s.check_stop()
            if not s.accounts:
                self.event('multi_solo_no_accounts',detail='本轮没有可执行的账号；请查看账号或队伍原因')
                self.save_progress('no_accounts')
                outcome = 'pending'
                return 2
            self.save_progress("bound")
            if not self.prepare_account_origins():
                self.save_progress('paused')
                return 2
            for task in self.tasks:
                if s.check_pause_boundary():
                    self.save_progress("paused")
                    return 2
                stage_started = time.monotonic()
                s._performance_task = task
                self.event("multi_solo_stage_started", task=task)
                if task == 'training_record' and getattr(s, 'training_cooperative_enabled', False):
                    from hyol_training_coordinator import run_multi
                    run_multi(self)
                    self.save_progress('training_record')
                    self.event('multi_solo_stage_finished', task=task,
                               seconds=round(time.monotonic()-stage_started,3))
                    continue
                if task == 'escort':
                    from hyol_escort import run_multi
                    run_multi(self)
                    self.save_progress('escort')
                    self.event('multi_solo_stage_finished',task=task,
                               seconds=round(time.monotonic()-stage_started,3))
                    continue
                if task == 'treasure':
                    from hyol_treasure import run_multi
                    run_multi(self)
                    self.save_progress('treasure')
                    self.event('multi_solo_stage_finished', task=task,
                               seconds=round(time.monotonic()-stage_started, 3))
                    continue
                if task == 'arena':
                    self._run_arena_stage()
                    self.event('multi_solo_stage_finished', task=task,
                               excluded=sorted(self.excluded), disconnected=sorted(self.disconnected),
                               seconds=round(time.monotonic() - stage_started, 3))
                    self.save_progress(task)
                    continue
                if task == 'survival':
                    if not self._run_survival_stage():
                        self.save_progress('paused')
                        return 2
                    self.event('multi_solo_stage_finished', task=task,
                               excluded=sorted(self.excluded), disconnected=sorted(self.disconnected),
                               seconds=round(time.monotonic() - stage_started, 3))
                    self.save_progress(task)
                    continue
                if task == 'ranking':
                    if not self._run_ranking_stage():
                        self.save_progress('paused')
                        return 2
                    self.event('multi_solo_stage_finished', task=task,
                               excluded=sorted(self.excluded), disconnected=sorted(self.disconnected),
                               seconds=round(time.monotonic() - stage_started, 3))
                    self.save_progress(task)
                    continue
                for configured in tuple(s.accounts):
                    s.check_stop()
                    account = s.by_name[configured.name]
                    if account.name not in self.startup_returned:
                        self.note_skipped(account, task)
                        continue
                    if account.name in getattr(self, 'manual_deferred', set()):
                        self.note_skipped(account, task)
                        continue
                    if account.name in self.deferred:
                        self.probe_deferred(account)
                        self.note_skipped(account, task)
                        continue
                    previous = self.runner_for(account).record(task)
                    if (task == 'arena' and previous.get('status') == 'done'
                            and (previous.get('arena_requested_wins', 5) != self.arena_wins
                                 or previous.get('arena_target_mode', 'wins') != self.arena_target_mode)):
                        prior_request = s.state.global_state.get('arena_requests', {}).get(account.name)
                        if isinstance(prior_request, dict) and prior_request.get('status') != 'done':
                            prior_request.update(status='done', completed_at=previous.get('finished_at'))
                            s.state.save_global()
                        previous.update(status='new', task_completed=False, home_returned=False)
                        self.runner_for(account).save()
                        self.event('multi_solo_arena_new_request', account,
                                   requested_wins=self.arena_wins)
                    if (previous.get('status') == 'done' and previous.get('task_completed')
                            and previous.get('home_returned') and not previous.get('followup_check_due')
                            and (task != 'arena' or account.name in getattr(self,'arena_rewards_audited',set()))):
                        self.event('multi_solo_task_skip_done', account, task=task)
                        continue
                    if self.check_account(account):
                        self._run_task(self.runner_for(account), task)
                    else:
                        self.note_skipped(account, task)
                self.event("multi_solo_stage_finished", task=task,
                           excluded=sorted(self.excluded), disconnected=sorted(self.disconnected),
                           seconds=round(time.monotonic() - stage_started, 3))
                self.save_progress(task)
            for name, task in tuple(self.deferred.items()):
                if name in getattr(self, 'manual_deferred', set()):
                    self.event('multi_solo_deferred_until_next_run', s.by_name[name], task=task)
                    continue
                if s.check_pause_boundary():
                    self.save_progress("paused")
                    return 2
                account = s.by_name[name]
                if name not in self.startup_returned:
                    continue
                if name not in self.excluded:
                    recent = time.monotonic() - getattr(self, 'deferred_at', {}).get(name, 0) < 60
                    if recent:
                        self.event('multi_solo_deferred_waiting', account, task=task,
                                   detail='刚观察到匹配、加载或战斗；本轮不立即重复进入')
                    # An informational probe may be throttled and leave a
                    # different tab active. A real resume must acquire this
                    # exact account again immediately before task execution.
                    acquired = not recent and self.probe_deferred(account,force=True)
                    if name not in self.excluded and acquired:
                        self._run_task(self.runner_for(account), task)
                        if self.runner_for(account).record(task).get("status") == "done":
                            self.deferred.pop(name, None)
                            self.deferred_at.pop(name, None)
                self.save_progress("deferred_recheck")
            if not self.run_skipped_tasks():
                self.save_progress("paused")
                return 2
            if "survival" in self.tasks:
                for configured in tuple(s.accounts):
                    if s.check_pause_boundary():
                        self.save_progress("paused")
                        return 2
                    account = s.by_name[configured.name]
                    if account.name not in self.startup_returned:
                        continue
                    runner = self.runners.get(account.name)
                    if runner and runner.record("survival").get("followup_check_due") and self.check_account(account):
                        self._survival_followup(runner)
                self.save_progress("survival_followup")
            if not self.finish_managed_accounts():
                self.save_progress('paused')
                return 2
            from hyol_anniversary import request_satisfied as anniversary_satisfied
            result = 0 if not self.excluded and not self.startup_pending and all(
                anniversary_satisfied(self.runner_for(account).record(task)) if task=='anniversary' else
                self.runner_for(account).record(task).get("status") == "done"
                and self.runner_for(account).record(task).get('task_completed') is True
                and self.runner_for(account).record(task).get('home_returned') is True
                and not self.runner_for(account).record(task).get("followup_check_due")
                and (task != 'daruma' or daruma_verified(self.runner_for(account).record(task)))
                and (task != 'escort' or escort_verified(self.runner_for(account).record(task)))
                for account in s.accounts for task in self.tasks
            ) and (not getattr(self, 'managed_enabled', True) or all(
                self.runner_for(account).record('managed').get('status') == 'done'
                for account in s.accounts
            )) else 2
            outcome = 'complete' if result == 0 else 'pending'
            return result
        except BaseException as exc:
            self.event('multi_solo_run_error', detail=str(exc), error_type=type(exc).__name__)
            raise
        finally:
            try:
                self.event('multi_solo_run_end', outcome=outcome)
            finally:
                try:
                    if meter is not None:
                        timings, scopes = meter.snapshot(), meter.scope_snapshot()
                        meter.close()
                        self.event('multi_solo_performance', timings=timings, scopes=scopes,
                                   detail='seconds为嵌套总时长；仅exclusive_seconds可相加')
                finally:
                    s.stop.set()
                    if manage_lease:
                        lease.release()


def main():
    parser = argparse.ArgumentParser(description="共享窗口多账号单人日常")
    parser.add_argument("--tasks", nargs="+", choices=SOLO_OPTIONS,
                        default=list(DEFAULT_SOLO_OPTIONS))
    parser.add_argument("--arena-wins", type=int, default=5,
                        help="仅在 --arena-target-mode wins 时生效")
    parser.add_argument("--arena-target-mode", choices=("tickets", "wins"), default="tickets",
                        help="默认耗尽战令；wins 表示按指定新增胜场数停止")
    parser.add_argument("--account-count", type=int, default=None,
                        help="本轮要识别的账号数量；候选账号取配置TabAccountNames前N个")
    parser.add_argument("--formation", choices=("keep", "main", "fast"), default="keep")
    parser.add_argument("--config", type=Path, default=hs.ROOT / "HYOL_Daily.ini")
    args = parser.parse_args()
    runner = MultiSoloRunner(args.tasks, config_path=args.config,
                             account_count=args.account_count,
                             arena_wins=args.arena_wins, arena_target_mode=args.arena_target_mode,
                             formation=args.formation,
                             event_sink=lambda event: print(json.dumps(event, ensure_ascii=False), flush=True)
                             if str(event.get("event", "")).startswith("multi_solo_") else None)
    try:
        result = runner.run()
        print(json.dumps({"result": result, "progress": str(hs.STATE_DIR / "multi_solo_latest.json")},
                         ensure_ascii=False), flush=True)
        return result
    except (hs.PendingError, hs.SafetyError, hs.LeaseBlocked) as exc:
        print(json.dumps({"result": 2, "detail": str(exc)}, ensure_ascii=False), flush=True)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
