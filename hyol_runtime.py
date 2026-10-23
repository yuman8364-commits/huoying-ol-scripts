"""Adapters for one unified desktop worker; existing runners own game leases."""
from __future__ import annotations

from dataclasses import asdict, dataclass
import json
from pathlib import Path
import threading

import hyol_scheduler as hs
from hyol_daily_tasks import DailyRunner, DEFAULT_SOLO_OPTIONS, SOLO_OPTIONS
from hyol_multi_solo import MultiSoloRunner
from hyol_team_pipeline import TeamPipelineRunner, SUPPORTED_TEAM_TASKS as PIPELINE_TEAM_TASKS, detect_leaders
from hyol_team_six import TeamRunner, TeamSelection, TASKS as TEAM_TASKS, DEFAULT_TASKS
from hyol_bond import BondRunner, BOND_ACCOUNT, MAX_BATTLES
from hyol_diagnostics import clear_manual_pause, manual_pause_status, mark_manual_pause, run_id
from tools.preflight_live import run as preflight_live

VERSION = '2026.10.23.8'
MODES = ('solo', 'multi', 'team', 'team_then_multi', 'arena', 'bond', 'weekend', 'treasure')


@dataclass(frozen=True)
class RunRequest:
    mode: str = 'solo'
    tasks: tuple[str, ...] = DEFAULT_SOLO_OPTIONS
    account_count: int = 6
    arena_wins: int = 5
    formation: str = 'keep'
    leader: int = 1
    leaders: tuple[int, ...] = ()
    members: tuple[int, ...] = (2, 3)
    friend_shortcut: bool = False
    team_tasks: tuple[str, ...] = DEFAULT_TASKS
    bond_count: int = 5
    new_bond_round: bool = False
    final_managed: bool = True
    arena_target_mode: str = 'tickets'

    def __post_init__(self):
        if self.mode not in MODES:
            raise ValueError('请选择有效任务类型')
        if not 1 <= self.account_count <= 6 or not 1 <= self.arena_wins <= hs.ARENA_MAX_ATTEMPTS:
            raise ValueError('账号数量须为1～6，竞技场辅助胜场数须为1～10')
        if self.arena_target_mode not in ('tickets', 'wins'):
            raise ValueError('竞技场结束条件须为战令归零或指定胜场')
        if self.formation not in ('keep', 'main', 'fast') or not 1 <= self.bond_count <= MAX_BATTLES:
            raise ValueError('阵容或羁绊次数无效')
        if not isinstance(self.final_managed, bool) or not isinstance(self.new_bond_round, bool):
            raise ValueError('最后托管选项必须为开启或关闭')
        if self.mode in ('solo', 'multi', 'team_then_multi'):
            if (not self.tasks or len(set(self.tasks)) != len(self.tasks)
                    or any(task not in SOLO_OPTIONS for task in self.tasks)):
                raise ValueError('请至少选择一项有效的日常任务')
        if self.mode == 'team':
            TeamSelection(self.leader, () if self.friend_shortcut else self.members,
                          self.friend_shortcut, self.team_tasks)
        if self.mode == 'team_then_multi':
            if (len(self.leaders) > 6 or len(set(self.leaders)) != len(self.leaders)
                    or any(type(slot) is not int or slot not in range(1,7) for slot in self.leaders)):
                raise ValueError('队长标签须互不重复；留空可自动识别')
            if not self.team_tasks or len(set(self.team_tasks)) != len(self.team_tasks) or any(t not in PIPELINE_TEAM_TASKS for t in self.team_tasks):
                raise ValueError('已有队伍衔接请勾选副本、通缉或强者')


def prohibit_purchase(scheduler):
    scheduler.config['AllowMarketPurchase'] = '0'
    scheduler.config['MarketDirectBuyVerified'] = '0'
    # The user authorized final managed mode. This enables that execution gate;
    # run_managed_gate still verifies the actual settings and active frames.
    scheduler.config['ManagedSafetyVerified'] = '1'
    scheduler.allow_purchase = False
    scheduler.market_direct_buy_verified = False


def shared_directory(root):
    from hyol_accounts import state_directory
    return state_directory(hs.read_config(Path(root)/'HYOL_Daily.ini'), root)


def read_record(path):
    try:
        value = json.loads(Path(path).read_text(encoding='utf-8-sig'))
    except FileNotFoundError:
        return {}
    except (OSError, ValueError) as exc:
        raise hs.PendingError(f'原断点无法核验，保留原文件：{path}') from exc
    if not isinstance(value, dict):
        raise hs.PendingError(f'原断点格式异常，保留原文件：{path}')
    return value


def validated_bond_finish(record):
    """A saved completed battle request must never fall through to fresh battles."""
    if not record:
        return None
    original = record.get('request')
    if record.get('status') not in ('pending', 'done') or not isinstance(original, dict):
        raise hs.PendingError('羁绊收尾断点无法核验，不重复战斗')
    requested, completed = original.get('requested'), original.get('completed')
    if (original.get('status') != 'done' or not isinstance(original.get('run_id'), str)
            or original.get('phase') != 'ready'
            or not original['run_id'].strip() or not isinstance(requested, int)
            or isinstance(requested, bool) or not 1 <= requested <= MAX_BATTLES
            or not isinstance(completed, int) or isinstance(completed, bool)
            or completed != requested):
        raise hs.PendingError('羁绊原战斗结果或目标次数无法核验，不重复战斗')
    owner = record.get('owner')
    if not isinstance(owner, str) or not owner.strip() or original.get('owner') != owner:
        raise hs.PendingError('羁绊原轮次未绑定同一实名账号，不重复战斗或收尾')
    if record['status'] == 'done' and not record.get('evidence'):
        raise hs.PendingError('羁绊收尾记录缺少已确认凭据，保留原票据，不重复战斗')
    return original


def bond_requested_count(root, requested):
    current = Path(root)/'bond_client_state.json'
    origins = [current, shared_directory(root).parent/'bond_client_state.json']
    if Path(root).name == '火影OL完整客户端':
        origins.append(Path(root).parent/'火影OL羁绊/bond_client_state.json')
    count = requested
    for path in dict.fromkeys(origins):
        saved = read_record(path)
        if saved.get('status') not in ('running', 'game_timeout'):
            continue
        if path.resolve() != current.resolve():
            raise hs.PendingError('另一个入口有未关联的原羁绊断点，先核实原账号与结果，不开始新战斗：'+str(path))
        count = saved.get('requested')
        if not isinstance(count, int) or isinstance(count, bool) or not 1 <= count <= MAX_BATTLES:
            raise hs.PendingError('原羁绊目标次数无法核验，不覆盖原请求')
    return count


def bond_legacy_paths(root):
    root = Path(root)
    paths = [root/'bond_client_state.json', shared_directory(root).parent/'bond_client_state.json']
    if root.name == '火影OL完整客户端':
        paths.append(root.parent/'火影OL羁绊/bond_client_state.json')
    return tuple(dict.fromkeys(paths))


def make_runner(request, event_sink, root=hs.ROOT, *, run_context=None):
    from contextlib import nullcontext
    from hyol_run_context import RunContext
    if request.mode == 'bond':
        run_context = None  # The independent bond client owns its separate round journal.
    if run_context is None and request.mode != 'bond':
        run_context = RunContext.create(root)
    with run_context.activate() if run_context else nullcontext():
        return _make_runner(request,event_sink,root,run_context)


def _make_runner(request, event_sink, root, run_context):
    """Construct an existing runner, not its GUI and not another worker."""
    root = Path(root)
    if request.mode == 'team_then_multi':
        runner = TeamPipelineRunner(request.leaders, request.team_tasks, request.tasks,
                                    arena_wins=request.arena_wins, arena_target_mode=request.arena_target_mode,
                                    formation=request.formation,
                                    managed_enabled=request.final_managed, event_sink=event_sink, root=root,
                                    run_context=run_context)
    elif request.mode == 'multi':
        runner = MultiSoloRunner(request.tasks, config_path=root/'HYOL_Daily.ini',
                                 account_count=request.account_count, arena_wins=request.arena_wins,
                                 arena_target_mode=request.arena_target_mode, formation=request.formation,
                                 managed_enabled=request.final_managed,
                                 event_sink=event_sink, run_context=run_context)
    elif request.mode == 'team':
        selection = TeamSelection(request.leader, () if request.friend_shortcut else request.members,
                                  request.friend_shortcut, request.team_tasks)
        runner = TeamRunner(selection, managed_enabled=request.final_managed,
                            formation=request.formation, event_sink=event_sink)
    elif request.mode == 'bond':
        runner = BondRunner(request.bond_count, event_sink=event_sink,
                            config_path=root/'HYOL_Daily.ini', require_identity=True,
                            legacy_state_paths=bond_legacy_paths(root), new_round=request.new_bond_round)
    else:
        tasks = request.tasks if request.mode == 'solo' else (request.mode,)
        runner = DailyRunner('all', selected_tasks=tasks, config_path=root/'DAILY_Client.ini',
                             generic_current_window=True, formation=request.formation,
                             arena_wins=request.arena_wins, arena_target_mode=request.arena_target_mode,
                             managed_enabled=request.final_managed,
                             event_sink=event_sink)
    prohibit_purchase(runner.scheduler)
    return runner


def completion_verified(request, runner, result):
    """Zero exit alone cannot turn a calendar skip or incomplete record into done."""
    if result != 0:
        return False
    if request.mode == 'team_then_multi':
        return runner.completed()
    if request.mode in ('solo', 'arena', 'weekend', 'treasure'):
        from hyol_puzzle import weekend_verified
        from hyol_daruma_task import verified as daruma_verified
        tasks = request.tasks if request.mode == 'solo' else (request.mode,)
        records = runner.records
        return all(records.get(task, {}).get('status') == 'done'
                   and records[task].get('task_completed') is True
                   and records[task].get('home_returned') is True
                   and (task != 'weekend' or weekend_verified(records[task]))
                   and (task != 'daruma' or daruma_verified(records[task]))
                   and not records[task].get('followup_check_due') for task in tasks) and (
                       not request.final_managed or records.get('managed', {}).get('status') == 'done'
                       and records['managed'].get('task_completed') is True
                       and bool(records['managed'].get('evidence')))
    if request.mode == 'multi':
        s = runner.scheduler
        if runner.excluded or runner.startup_pending or not s.accounts:
            return False
        for account in s.accounts:
            local = runner.runners.get(account.name)
            if local is None or not completion_verified(
                    RunRequest(mode='solo', tasks=request.tasks, final_managed=request.final_managed), local, 0):
                return False
        return True
    if request.mode == 'team':
        if runner.team is None or not runner._selected_tasks_complete(runner.team):
            return False
        record = runner._run_record(runner.team)
        return (record.get('phase') == 'done'
                and record.get('source_day') == runner.scheduler.state.day
                and record.get('managed_enabled', True) == request.final_managed
                and runner._dissolve_complete(record.get('dissolve', {}), runner.team.members)
                and (not request.final_managed or all(record.get('managed', {}).get(name, {}).get('status') == 'done'
                        and record['managed'][name].get('task_completed') is True
                        and record['managed'][name].get('evidence') for name in runner.team.members)))
    return False  # Bond needs the separately verified navigation/managed gate.


class RuntimeController:
    def __init__(self, event_sink, root=hs.ROOT):
        self.root = Path(root)
        self.sink = event_sink
        self.worker = None
        self.scheduler = None
        self.runner = None
        self.cancel = threading.Event()
        self.lock = threading.Lock()
        self.identifier = ''
        self.run_context = None

    @property
    def busy(self):
        return self.worker is not None and self.worker.is_alive()

    def emit(self, event, **fields):
        self.sink({'event': event, 'run_id': self.identifier, **fields})

    def _launch(self, operation, target):
        with self.lock:
            if self.busy:
                return False
            self.cancel.clear()
            self.identifier = run_id()
            self.worker = threading.Thread(target=target, name='HYOL-Unified-'+operation, daemon=False)
            self.worker.start()
            return True

    def start(self, request):
        return self._launch('run', lambda: self._run(request))

    def check_window(self):
        return self._launch('check', self._check)

    def detect_team_leaders(self):
        return self._launch('leaders',self._detect_leaders)

    def _detect_leaders(self):
        try:
            self._not_cancelled(check_pause=True)
            check = preflight_live()
            if not (check.get('status') == 'ready' and check.get('healthy') is True
                    and check.get('content_clicks') == 0):
                raise hs.PendingError('队长识别前只读窗口预检未通过')
            self._not_cancelled(check_pause=True)
            result = detect_leaders(event_sink=self.sink,stop_event=self.cancel)
            self._not_cancelled(check_pause=True)
            self.emit('unified_leaders_detected',check=result)
        except KeyboardInterrupt:
            mark_manual_pause(self.root,self.identifier,'队长识别被用户停止')
            self.emit('unified_status',detail='识别已停止，原选择和断点保留。')
        except Exception as exc:
            self.emit('unified_status',detail='队长识别待处理：'+str(exc))
        finally:
            self.emit('unified_idle')


    def stop(self):
        if not self.busy:
            return False
        self.cancel.set()
        if self.run_context is not None:
            self.run_context.interrupted = True
            self.run_context.stop.set()
        if self.scheduler is not None:
            self.scheduler.stop.set()
        mark_manual_pause(self.root, self.identifier, '用户点击统一客户端停止')
        self.emit('unified_stop_requested', detail='正在结束本轮运行。')
        return True

    def _not_cancelled(self, *, check_pause=False):
        if self.cancel.is_set() or (self.run_context is not None and self.run_context.interrupted):
            raise KeyboardInterrupt('用户停止')
        if check_pause and manual_pause_status(self.root)['paused']:
            raise KeyboardInterrupt('人工暂停仍在，停止交接并保留断点')

    def _check(self):
        try:
            self._not_cancelled()
            check = preflight_live()
            self._not_cancelled()
            self.emit('unified_window_check', check=check)
        except BaseException as exc:
            self.emit('unified_error', detail=str(exc))
        finally:
            self.emit('unified_idle')


    def _finish_bond(self, runner, *, managed_enabled=True):
        """A new lease after the battle runner releases; no nested lease or blind exit."""
        request = runner.request
        actions = runner.scheduler.state.accounts[getattr(runner, 'account', BOND_ACCOUNT).name].get('actions', {})
        if (request.get('status') != 'done' or request.get('completed', 0) < request.get('requested', 1)
                or any(row.get('status') == 'pending' for row in actions.values())):
            raise hs.PendingError('羁绊战斗结果或原动作未全部确认，不能收尾托管')
        self._not_cancelled(check_pause=True)
        owner = getattr(runner, '_unified_owner', '')
        if not owner:
            raise hs.PendingError('原羁绊账号未唯一确认；战斗已完成，回村和托管保留待处理，不重开战斗')
        config = hs.read_config(self.root/'DAILY_Client.ini')
        scheduler = hs.Scheduler(config, 'unified_bond_finish', False, False, event_sink=self.sink)
        self.scheduler = scheduler
        prohibit_purchase(scheduler)
        lease = hs.ForegroundRuntimeLease()
        owned = False
        try:
            if manual_pause_status(self.root)['paused']:
                raise hs.PendingError('F12暂停仍在，羁绊收尾保留待处理')
            owned = lease.acquire()
            if not owned:
                raise hs.PendingError('窗口仍被占用，羁绊收尾保留待处理')
            self._not_cancelled(check_pause=True)
            scheduler.start_f12_monitor()
            scheduler.bind_window(wait=True, timeout_seconds=15)
            account = scheduler.bind_generic_current_window(require_game_context=False)
            if account.name != owner:
                raise hs.PendingError('当前账号与原羁绊结果身份不同，保留原收尾；不点击回村或托管')
            source = read_record(shared_directory(self.root)/'bond'/(hs.slug(owner)+'.json'))
            if (source.get('owner') != owner or source.get('run_id') != request.get('run_id')
                    or source.get('status') != 'done' or source.get('phase') != 'ready'
                    or source.get('requested') != request.get('requested')
                    or source.get('completed') != request.get('completed')):
                raise hs.PendingError('羁绊收尾与共享原轮次不一致，保留断点，不重复回村或托管')
            from hyol_navigation import return_to_leaf
            # An already completed managed finish is observed in place. Leaving
            # it only to start it again would change a completed request.
            if not managed_enabled or scheduler.managed_active_score() < .70:
                return_to_leaf(scheduler, account)
            self._not_cancelled(check_pause=True)
            if managed_enabled:
                scheduler.run_managed_gate(account)
            evidence = scheduler.save_raw_evidence(scheduler.capture(), account,
                                                  'unified_bond_managed' if managed_enabled else 'unified_bond_home',
                                                  'verified')
            self.emit('unified_final_managed' if managed_enabled else 'unified_home_verified',
                      account=account.name, evidence=str(evidence),
                      detail='羁绊回木叶及最后托管已确认' if managed_enabled else '羁绊回木叶已确认')
            return str(evidence)
        finally:
            scheduler.stop.set()
            if owned:
                lease.release()

    def _run(self, request):
        if not self.identifier:
            self.identifier = run_id()
        code, complete, detail = 2, False, ''
        exception_type, exception_traceback = '', ''
        self.runner = None
        evidence = ''
        lease = hs.ForegroundRuntimeLease()
        context = None
        try:
            self._not_cancelled()
            if not lease.acquire():
                raise hs.PendingError('已有脚本占用游戏窗口')
            from hyol_run_context import RunContext
            context = RunContext.create(self.root,identifier=self.identifier)
            self.run_context = context
            context.begin()
            self.emit('unified_run_started',state_directory=str(context.directory))
            self.emit('unified_status', detail='只读检查游戏窗口与当前页面……')
            with context.activate():
                check = preflight_live()
            if not (check.get('status') == 'ready' and check.get('healthy') is True
                    and check.get('content_clicks') == 0):
                raise hs.PendingError('游戏窗口检查未通过：'+str(check.get('reason', check.get('status'))))
            self._not_cancelled(check_pause=True)
            self.runner = make_runner(request, self.sink, self.root, run_context=context)
            self.scheduler = self.runner.scheduler
            self._not_cancelled(check_pause=True)
            self.emit('unified_status', detail='执行所选任务；最后逐账号核实木叶回村'
                      +('与托管。' if request.final_managed else '。'))
            bond_path = shared_directory(self.root)/'unified_bond_finish.json' if request.mode == 'bond' else None
            finish = read_record(bond_path) if bond_path else {}
            original = validated_bond_finish(finish) if bond_path else None
            if original and (finish.get('status') == 'pending' or not request.new_bond_round):
                if original.get('owner') and original['owner'] != finish.get('owner'):
                    raise hs.PendingError('羁绊原轮次与收尾身份冲突，保留原断点')
                self.runner.request = dict(original)
                self.runner._unified_owner = finish.get('owner', '')
                code = 0
                self.emit('unified_status', detail='原羁绊战斗已完成；仅核实回村和最后托管。'
                          '新一轮须明确选择“开始新一轮羁绊”。')
            else:
                self._not_cancelled(check_pause=True)
                code = self.runner.run()
                if request.mode == 'bond' and code == 0:
                    owner = getattr(self.runner, '_unified_owner', '')
                    if not owner or self.runner.request.get('owner') != owner:
                        raise hs.PendingError('羁绊开战身份与原轮次未关联；战斗结果保留，不能收尾')
                    self.runner._unified_owner = owner
                    finish = {'status': 'pending', 'request': dict(self.runner.request), 'owner': owner,
                              'at': hs.now_iso(), 'entry_root': str(self.root)}
                    hs.atomic_json(bond_path, finish)
            self._not_cancelled(check_pause=True)
            if request.mode == 'bond' and code == 0:
                evidence = self._finish_bond(self.runner, managed_enabled=request.final_managed)
                complete = bool(evidence)
                finish.update(status='done', evidence=evidence, managed_enabled=request.final_managed,
                              home_returned=True, finished_at=hs.now_iso())
                hs.atomic_json(bond_path, finish)
            else:
                complete = completion_verified(request, self.runner, code)
            if code == 0 and not complete:
                code = 2
                calendar = getattr(self.runner, 'calendar_plan', None)
                if (isinstance(calendar, dict) and calendar.get('calendar_skipped')
                        and not calendar.get('effective_tasks')):
                    detail = f"执行日{calendar.get('execution_day', '')}：所选活动当前未开放，未执行；不计完成。"
                else:
                    detail = '当前任务、领奖、木叶回村'+('或最后托管' if request.final_managed else '')+'证据未齐全；保留待处理。'
                if request.mode == 'team' and self.runner.team:
                    source_day = self.runner._run_record(self.runner.team).get('source_day')
                    if source_day != self.runner.scheduler.state.day:
                        detail = f'已核实{source_day}原队伍收尾；本日任务尚未实机确认，保留待验。'
        except KeyboardInterrupt:
            code, complete, detail = 130, False, '本轮已停止；再次开始将按当前画面和选择创建新运行。'
            mark_manual_pause(self.root, self.identifier, 'F12或用户停止统一客户端')
        except Exception as exc:
            code, complete, detail = 2, False, str(exc)
            import traceback
            exception_type, exception_traceback = type(exc).__name__, traceback.format_exc()
        finally:
            if context:
                context.stop.set()
            lease.release()
            pipeline_summary = self.runner.summary() if request.mode == 'team_then_multi' and self.runner is not None else {}
            runner_summary = getattr(self.runner, 'last_summary', None) if self.runner is not None else None
            if isinstance(runner_summary, dict) and code != 0 and not detail:
                detail = str(runner_summary.get('detail') or '')
            if code != 0 and not detail and not pipeline_summary:
                detail = '本轮任务未完成，请查看对应账号的原因与运行日志。'
            if pipeline_summary:
                detail = (detail+' ' if detail else '') + pipeline_summary['detail']
            receipt = {'version': VERSION, 'run_id': self.identifier, 'request': asdict(request),
                       'state_directory': str(context.directory) if context else '',
                       'error_type': exception_type, 'error_traceback': exception_traceback,
                       'result': code, 'status': 'complete' if complete else 'stopped' if code == 130 else 'pending',
                       'detail': detail, 'final_managed_evidence': evidence if request.final_managed else '',
                       'home_evidence': evidence if not request.final_managed else '', 'at': hs.now_iso(),
                       'live_acceptance': '未实测条目须由逐项实机验收记录确认'}
            if pipeline_summary:
                receipt['pipeline'] = pipeline_summary
            if isinstance(runner_summary, dict):
                receipt['runner_summary'] = runner_summary
            try:
                hs.atomic_json(self.root/'logs'/('unified_'+self.identifier+'.json'), receipt)
                hs.atomic_json(self.root/'logs/unified_latest.json', receipt)
            except OSError as exc:
                receipt['save_error'] = str(exc)
            self.emit('unified_finished', **receipt)
            self.scheduler = None
            self.run_context = None
            self.emit('unified_idle')
