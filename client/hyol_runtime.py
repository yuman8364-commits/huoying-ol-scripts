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
from hyol_team_six import TeamRunner, TeamSelection, DEFAULT_TASKS
from hyol_bond import BondRunner, BOND_ACCOUNT, MAX_BATTLES, target_reached
from hyol_diagnostics import manual_pause_status, mark_manual_pause, run_id
from tools.preflight_live import run as preflight_live

VERSION = '2026.11.09.24-opencv'
RELEASE_DATE = '2026-11-09'
EDITION = 'OpenCV版'
BASELINE_VERSION = '2026.10.25.4-opencv'
# The YOLO fast edition is the same source with a vision-profile package; the
# packaged client INI carries ClientEdition=YOLO极速版 so the UI can display
# the edition without a diverging source tree.  Recognition logic never
# branches on the display edition.
try:
    from hyol_app_paths import read_client_edition as _read_client_edition
    _detected_edition = _read_client_edition()
    if _detected_edition:
        EDITION = _detected_edition
except Exception:
    pass
MODES = ('solo', 'multi', 'team', 'team_then_multi', 'arena', 'bond', 'weekend', 'treasure', 'puzzle', 'training_record', 'anniversary')
INDEPENDENT_MODES = ('arena', 'bond', 'weekend', 'treasure', 'puzzle', 'training_record')


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
    preformed_duos: bool = False
    preformed_pairs: tuple[tuple[int, int], ...] = ()
    preformed_trio: tuple[int, ...] = ()
    bond_count: int = 5
    bond_stop_mode: str = 'battles'
    bond_target_mode: str = 'battles'
    bond_kill_target: int = 160
    bond_score_target: int = 100
    bond_team_goals: tuple[tuple[int, str, int], ...] = ()
    new_bond_round: bool = False
    final_managed: bool = True
    arena_target_mode: str = 'tickets'
    escort_remaining: int = 0
    escort_prior_settled: bool = False
    team_control: str = 'all_members'
    training_arena_stop_policy: str = 'auto'
    arena_second_skill: bool = False
    grocery_items: tuple[str, ...] = ()

    def __post_init__(self):
        from hyol_grocery import validate_items
        if type(self.arena_second_skill) is not bool:
            raise ValueError('竞技场第二技能须为开关')
        object.__setattr__(self, 'grocery_items', validate_items(self.grocery_items))
        if 'grocery' in self.tasks and not self.grocery_items:
            raise ValueError('请在杂货屋二级设置中选择至少一种商品')
        from hyol_current_team import CURRENT_TEAM_TASKS
        if self.team_control not in ('all_members', 'current_account'):
            raise ValueError('组队活动控制范围无效')
        if self.training_arena_stop_policy not in ('auto', 'final_goal', 'tickets'):
            raise ValueError('修行录竞技场结束条件无效')
        current_team = self.mode == 'solo' and any(t in CURRENT_TEAM_TASKS for t in self.tasks)
        if current_team:
            object.__setattr__(self, 'team_control', 'current_account')
            object.__setattr__(self, 'account_count', 1)
            object.__setattr__(self, 'tasks', tuple(t for t in self.tasks if t != 'arena_3v3')
                               + (('arena_3v3',) if 'arena_3v3' in self.tasks else ()))
            if set(self.tasks) & {'bond', 'arena_3v3'}:
                object.__setattr__(self, 'final_managed', False)
        elif self.team_control == 'current_account' and self.mode != 'solo':
            raise ValueError('仅当前账号的组队活动请在单人入口执行')
        from hyol_arena_3v3 import TASK, validate_trio, normalize_tasks
        if TASK in self.team_tasks and self.mode=='team_then_multi':
            trio=validate_trio(self.preformed_trio)
            if self.preformed_duos or self.preformed_pairs or 'bond' in self.team_tasks:
                raise ValueError('三人3v3不能与二人羁绊或二人队设置混用')
            object.__setattr__(self,'preformed_trio',trio)
            object.__setattr__(self,'leaders',(trio[0],))
            object.__setattr__(self,'team_tasks',normalize_tasks(self.team_tasks))
            object.__setattr__(self,'formation','keep')
        elif self.preformed_trio:
            raise ValueError('三人队标签仅用于已有队伍的竞技场3v3')
        from hyol_bond_goals import validate_options, validate_pairs
        validate_options(self.bond_target_mode, self.bond_kill_target, self.bond_score_target, self.bond_team_goals)
        validate_pairs(self.preformed_pairs, self.leaders)
        if self.preformed_pairs and (not self.preformed_duos or self.mode != 'team_then_multi'):
            raise ValueError('指定配对仅用于已有二人队')
        if (self.bond_target_mode != 'battles' or self.bond_team_goals) and self.mode != 'team_then_multi' and not (current_team and 'bond' in self.tasks and not self.bond_team_goals):
            raise ValueError('击杀数和掉分目标请在已有队伍的二人羁绊中设置')
        if self.bond_team_goals and (any(row[0] not in self.leaders for row in self.bond_team_goals) if self.leaders else False):
            raise ValueError('羁绊单队目标须对应本轮选择的队长标签')
        if self.mode not in MODES:
            raise ValueError('请选择有效任务类型')
        if self.mode == 'anniversary':
            object.__setattr__(self,'tasks',('anniversary',))
            object.__setattr__(self,'final_managed',False)
            object.__setattr__(self,'formation','keep')
        if self.bond_stop_mode not in ('endless', 'battles', 'wins') or self.mode != 'bond' and self.bond_stop_mode != 'battles':
            raise ValueError('独立羁绊目标须为一直打、打5次或5场胜利')
        if not 1 <= self.account_count <= 6 or not 1 <= self.arena_wins <= hs.ARENA_MAX_ATTEMPTS:
            raise ValueError('账号数量须为1～6，竞技场辅助胜场数须为1～10')
        if self.mode in INDEPENDENT_MODES:
            object.__setattr__(self, 'account_count', 1)
        if self.arena_target_mode not in ('tickets', 'wins'):
            raise ValueError('竞技场结束条件须为战令归零或指定胜场')
        if self.formation not in ('keep', 'main', 'fast') or not 1 <= self.bond_count <= MAX_BATTLES:
            raise ValueError('阵容或羁绊次数无效')
        if not isinstance(self.final_managed, bool) or not isinstance(self.new_bond_round, bool):
            raise ValueError('最后托管选项必须为开启或关闭')
        if type(self.preformed_duos) is not bool or self.preformed_duos and self.mode != 'team_then_multi':
            raise ValueError('已组好的二人队仅用于已有队伍模式')
        if type(self.escort_remaining) is not int or self.escort_remaining not in (0,1,2):
            raise ValueError('押镖剩余续做次数须为1或2')
        if type(self.escort_prior_settled) is not bool:
            raise ValueError('此前押镖交付确认必须为布尔值')
        if self.escort_remaining and (self.mode!='solo' or self.tasks!=('escort',)
                or self.final_managed or not self.escort_prior_settled):
            raise ValueError('剩余押镖续做须仅选择当前账号押镖，确认此前已交付并关闭最后托管')
        if not self.escort_remaining and self.escort_prior_settled:
            raise ValueError('未选择剩余押镖续做时不能使用交付确认')
        if self.mode in ('solo', 'multi', 'team_then_multi'):
            allowed = (*SOLO_OPTIONS, *CURRENT_TEAM_TASKS) if self.mode == 'solo' else SOLO_OPTIONS
            if ((not self.tasks and self.mode != 'team_then_multi') or len(set(self.tasks)) != len(self.tasks)
                    or any(task not in allowed for task in self.tasks)):
                raise ValueError('请至少选择一项有效的日常任务')
        if self.mode == 'team':
            TeamSelection(self.leader, () if self.friend_shortcut else self.members,
                          self.friend_shortcut, self.team_tasks)
        if self.mode == 'team_then_multi':
            if self.preformed_duos and not self.preformed_pairs and any(slot not in (1,3,5) for slot in self.leaders):
                raise ValueError('已组好二人队按标签1+2、3+4、5+6执行，队长须为1、3、5')
            if (len(self.leaders) > 6 or len(set(self.leaders)) != len(self.leaders)
                    or any(type(slot) is not int or slot not in range(1,7) for slot in self.leaders)):
                raise ValueError('队长标签须互不重复；留空可自动识别')
            if not self.team_tasks or len(set(self.team_tasks)) != len(self.team_tasks) or any(t not in PIPELINE_TEAM_TASKS for t in self.team_tasks):
                raise ValueError('已有队伍衔接请勾选副本、通缉、强者或二人羁绊')


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


def preserve_activity_notices(request):
    # Escort targets do not use the friend controls covered by optional cards.
    return request.preformed_duos or (request.mode in ('solo','multi') and request.tasks==('escort',))


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
    directory=getattr(run_context,'directory',None)
    if isinstance(directory,Path):
        options=dict(arena_second_skill=request.arena_second_skill,grocery_items=list(request.grocery_items))
        option_file=directory/'desktop_options.json'
        previous=read_record(option_file)
        if previous and previous!=options:
            raise hs.PendingError('原运行的第二技能或杂货屋设置不同，请沿用原设置接续')
        if not previous:hs.atomic_json(option_file,options)
    if request.mode == 'team_then_multi':
        original = getattr(run_context, 'pipeline_original_request_key', None)
        solo_tasks = tuple(original['tasks']) if isinstance(original, dict) else request.tasks
        runner = TeamPipelineRunner(request.leaders, request.team_tasks, solo_tasks,
                                    arena_wins=request.arena_wins, arena_target_mode=request.arena_target_mode,
                                    formation=request.formation,
                                    managed_enabled=request.final_managed, event_sink=event_sink, root=root,
                                    run_context=run_context, bond_count=request.bond_count,
                                    preformed_duos=request.preformed_duos,
                                    preformed_pairs=request.preformed_pairs,
                                    preformed_trio=request.preformed_trio,
                                    bond_target_mode=request.bond_target_mode, bond_kill_target=request.bond_kill_target,
                                    bond_score_target=request.bond_score_target, bond_team_goals=request.bond_team_goals)
    elif request.mode in ('multi','anniversary'):
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
                            legacy_state_paths=bond_legacy_paths(root), new_round=request.new_bond_round,
                            keep_ready=True, stop_mode=request.bond_stop_mode)
    else:
        tasks = request.tasks if request.mode == 'solo' else (request.mode,)
        from hyol_current_team import CurrentAccountRunner, CURRENT_TEAM_TASKS
        runner_class = (CurrentAccountRunner if request.mode == 'solo'
                        and any(t in CURRENT_TEAM_TASKS for t in tasks) else DailyRunner)
        runner = runner_class('all', selected_tasks=tasks, config_path=root/'DAILY_Client.ini',
                             generic_current_window=True, formation=request.formation,
                             arena_wins=request.arena_wins, arena_target_mode=request.arena_target_mode,
                             managed_enabled=request.final_managed,
                             event_sink=event_sink)
        if isinstance(runner, CurrentAccountRunner):
            runner.desktop_request = request
        if request.mode == 'solo' and request.escort_remaining:
            runner.escort_remaining_only = request.escort_remaining
            runner.escort_prior_settled = request.escort_prior_settled
    prohibit_purchase(runner.scheduler)
    runner.training_arena_stop_policy = request.training_arena_stop_policy
    runner.scheduler.training_arena_stop_policy = request.training_arena_stop_policy
    runner.scheduler.arena_second_skill = request.arena_second_skill
    runner.scheduler.grocery_items = request.grocery_items
    runner.scheduler.daily_observation_enabled = True
    # Nested pipeline runners own a second scheduler; carry explicit options.
    runner.arena_second_skill = request.arena_second_skill
    runner.grocery_items = request.grocery_items
    if request.mode in INDEPENDENT_MODES:
        runner.scheduler.config['CurrentWindowOnly'] = '1'
    if preserve_activity_notices(request):
        runner.scheduler.config['PreserveActivityNotices']='1'
    # The explicit Start preparation has already handed focus to the verified
    # game window. Never repeatedly steal it back from another application
    # during the task, even when a preserved INI enables AutoForeground.
    runner.scheduler.config['AutoForeground'] = '0'
    runner.scheduler.auto_foreground = False
    return runner


def completion_verified(request, runner, result):
    """Zero exit alone cannot turn a calendar skip or incomplete record into done."""
    if result != 0:
        return False
    if request.mode == 'anniversary':
        from dataclasses import replace
        return completion_verified(replace(request,mode='multi'),runner,result)
    if request.escort_remaining:
        from hyol_escort import remaining_verified
        return remaining_verified(runner.records.get('escort_remaining',{}),request.escort_remaining)
    if request.mode in ('puzzle', 'training_record'):
        from dataclasses import replace
        return completion_verified(replace(request,mode='solo',tasks=(request.mode,)),runner,result)
    if request.mode == 'team_then_multi':
        return runner.completed()
    if request.mode in ('solo', 'arena', 'weekend', 'treasure'):
        from hyol_puzzle import weekend_verified
        from hyol_daruma_task import verified as daruma_verified
        from hyol_escort import verified as escort_verified
        from hyol_anniversary import request_satisfied
        tasks = request.tasks if request.mode == 'solo' else (request.mode,)
        records = runner.records
        from hyol_current_team import CURRENT_TEAM_TASKS
        return all(runner.current_task_complete(task, records.get(task, {}))
                   if task in CURRENT_TEAM_TASKS and request.team_control == 'current_account' else
                   request_satisfied(records.get(task,{})) if task=='anniversary' else
                   records.get(task, {}).get('status') == 'done'
                   and records[task].get('task_completed') is True
                   and records[task].get('home_returned') is True
                   and (task not in ('weekend','puzzle') or weekend_verified(records[task]))
                   and (task != 'daruma' or daruma_verified(records[task]))
                   and (task != 'escort' or escort_verified(records[task]))
                   and (task != 'training_record' or records[task].get('all_goals_complete') is True)
                   and not records[task].get('followup_check_due') for task in tasks) and (
                       not request.final_managed or records.get('managed', {}).get('status') == 'done'
                       and records['managed'].get('task_completed') is True
                       and bool(records['managed'].get('evidence')))
    if request.mode == 'multi':
        s = runner.scheduler
        if runner.excluded or runner.startup_pending or len(s.accounts) != request.account_count:
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
        # Observer exceptions must never break the worker's finally chain;
        # same swallow policy as Scheduler.event.
        try:
            self.sink({'event': event, 'run_id': self.identifier, **fields})
        except Exception:
            pass

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

    def check_window(self, scope=None):
        from hyol_preparation import validate_scope
        scope = validate_scope(scope)
        return self._launch('check', lambda: self._check(scope))

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

    def _check(self, scope=None):
        try:
            self._not_cancelled()
            from hyol_preparation import validate_scope
            check = preflight_live(prepare_scope=validate_scope(scope),stop_event=self.cancel,
                                   event_sink=self.sink,on_scheduler=lambda s:setattr(self,'scheduler',s))
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
        config['AutoForeground'] = '0'
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
        bond_kept_ready = False
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
            from hyol_training_resume import is_training, read_candidate, save_cursor
            from hyol_training_policy import local_day
            training = is_training(request)
            pipeline = request.mode == 'team_then_multi'
            if pipeline:
                from hyol_team_resume import read_candidate as read_pipeline_candidate
                candidate = read_pipeline_candidate(shared_directory(self.root),request,execution_day=local_day())
                prior_arena=candidate if candidate and candidate.get('observe_new_scope') else None
                if prior_arena:candidate=None
            else:
                candidate = read_candidate(shared_directory(self.root),request) if training else None
            if candidate:self.identifier=candidate['run_id']
            context = RunContext.create(self.root,identifier=self.identifier,
                execution_day=candidate['execution_day'] if candidate else local_day() if training or pipeline else None,
                session_token=candidate['session_token'] if candidate else '')
            self.run_context = context
            if request.mode == 'solo' and request.team_control == 'current_account':
                context.current_team_request = request
            if pipeline:
                context.pipeline_request = request
                context.pipeline_resume = bool(candidate)
                if prior_arena:context.pipeline_previous_arena_scope=prior_arena
                if candidate:
                    context.pipeline_original_request_key = candidate['request_key']
                    context.pipeline_deferred_tasks = tuple(candidate.get('deferred_tasks', ()))
            context.preserve_activity_notices = preserve_activity_notices(request)
            context.begin()
            if training:save_cursor(context,request,status='preparing',resumable=True)
            self.emit('unified_run_started',state_directory=str(context.directory))
            self.emit('unified_status', detail='检查当前羁绊准备页……' if request.mode == 'bond' else
                      '检查所选三人队；已在3v3时保留活动页面……' if request.preformed_trio else
                      '检查所选账号并自动回木叶村……')
            with context.activate():
                from hyol_preparation import scope_for_request
                from hyol_training_resume import restore_binding, verify_resume_window
                restored = bool((training or pipeline) and candidate and restore_binding(context,candidate))
                if pipeline and candidate and not restored:
                    raise hs.PendingError('已有队伍原身份绑定缺失，保留原场，不重新准备或开战')
                if pipeline and restored:
                    # This flag records the original three-frame preparation;
                    # it does not assert that the resumed battle is now home.
                    context.binding['prepared_leaf_verified']=True
                if restored:
                    check=verify_resume_window(self.root,context,self.sink,lambda s:setattr(self,'scheduler',s))
                else:
                    check = preflight_live(prepare_scope=scope_for_request(request),stop_event=context.stop,
                                           event_sink=self.sink,on_scheduler=lambda s:setattr(self,'scheduler',s),
                                           preserve_bond=(request.mode == 'bond' or request.mode == 'solo'
                                                          and request.team_control == 'current_account'
                                                          and bool(set(request.tasks) & {'bond','arena_3v3'})))
                if training and context.binding and check.get('preparation'):
                    context.binding['training_prepared_accounts']=[row['account'] for row in check.get('accounts',())
                                                                  if row.get('leaf_verified') and row.get('account')]
                if training:save_cursor(context,request,status='running',resumable=True)
                if check.get('preparation') is True:
                    self.emit('unified_preparation_result',check=check)
                    self._not_cancelled(check_pause=True)
                    if check.get('status') == 'stopped':
                        raise KeyboardInterrupt('准备检查已停止')
                    if check.get('status') not in ('ready', 'pending'):
                        raise hs.SafetyError('准备检查已中止：'+str(check.get('reason', check.get('status'))))
                    if not any(row.get('leaf_verified') is True or
                               request.preformed_trio and row.get('activity_ready')=='arena_3v3'
                               for row in check.get('accounts',())):
                        raise hs.PendingError('所选账号尚未确认回木叶村：'+str(check.get('reason','请查看逐号原因')))
                    check = preflight_live(stop_event=context.stop)
            if not (check.get('status') == 'ready' and check.get('healthy') is True
                    and check.get('content_clicks') == 0):
                raise hs.PendingError('游戏窗口检查未通过：'+str(check.get('reason', check.get('status'))))
            if request.escort_remaining and check.get('main_ready') is not True:
                raise hs.PendingError('剩余押镖开始前未确认主界面，未发送任务操作')
            self._not_cancelled(check_pause=True)
            self.runner = make_runner(request, self.sink, self.root, run_context=context)
            if pipeline:
                from hyol_team_resume import save_cursor as save_pipeline_cursor
                save_pipeline_cursor(context,request,status='running')
            self.scheduler = self.runner.scheduler
            self._not_cancelled(check_pause=True)
            self.emit('unified_status', detail=('当前窗口：匹配、检查自动、结算后下一场；保留队伍，不领取宝箱。'
                      if request.mode == 'bond' else '执行所选任务；最后逐账号核实木叶回村'
                      +('与托管。' if request.final_managed else '。')))
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
                if request.mode == 'bond' and code == 0 and not getattr(self.runner, 'keep_ready', False):
                    owner = getattr(self.runner, '_unified_owner', '')
                    if not owner or self.runner.request.get('owner') != owner:
                        raise hs.PendingError('羁绊开战身份与原轮次未关联；战斗结果保留，不能收尾')
                    self.runner._unified_owner = owner
                    finish = {'status': 'pending', 'request': dict(self.runner.request), 'owner': owner,
                              'at': hs.now_iso(), 'entry_root': str(self.root)}
                    hs.atomic_json(bond_path, finish)
            self._not_cancelled(check_pause=True)
            if request.mode == 'bond' and code == 0 and getattr(self.runner, 'keep_ready', False) and not original:
                rec = self.runner.request
                evidence = rec.get('ready_evidence', '')
                bond_kept_ready = True
                complete = (rec.get('status') == 'done' and rec.get('phase') == 'ready'
                            and rec.get('owner') == getattr(self.runner, '_unified_owner', None)
                            and target_reached(rec) and bool(evidence)
                            and Path(evidence).is_file())
                detail = '当前窗口羁绊场次与结算已核实；停留准备页，可开始新一轮。' if complete else '羁绊准备页完成凭据仍待核实。'
            elif request.mode == 'bond' and code == 0:
                evidence = self._finish_bond(self.runner, managed_enabled=request.final_managed)
                complete = bool(evidence)
                finish.update(status='done', evidence=evidence, managed_enabled=request.final_managed,
                              home_returned=True, finished_at=hs.now_iso())
                hs.atomic_json(bond_path, finish)
            else:
                complete = completion_verified(request, self.runner, code)
                if complete and request.escort_remaining:
                    detail = f'本次剩余{request.escort_remaining}次押镖和回村已核实；此前次数保留原记录。'
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
            code, complete, detail = 130, False, ('羁绊已停止；再次开始继续原轮次，未决场次不重复匹配。'
                if request.mode == 'bond' else '本轮已停止；再次开始将按当前画面和选择创建新运行。')
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
                       'detail': detail, 'final_managed_evidence': evidence if request.final_managed and not bond_kept_ready else '',
                       'home_evidence': evidence if not request.final_managed and not bond_kept_ready else '',
                       'bond_ready_evidence': evidence if bond_kept_ready else '',
                       'bond_kept_ready': bond_kept_ready, 'at': hs.now_iso(),
                       'live_acceptance': '未实测条目须由逐项实机验收记录确认'}
            if context is not None:
                from hyol_training_resume import is_training, save_cursor
                if is_training(request):
                    try:
                        cursor=save_cursor(context,request,status=receipt['status'])
                        receipt['training_resumable']=cursor['resumable']
                        if code == 130:receipt['detail']='修行录已停止；再次开始会沿原轮次、原动作及数量预算继续。'
                    except OSError as exc:
                        receipt.update(status='pending',result=2,save_error=str(exc),
                                       detail='修行录原轮次入口保存失败；原任务文件保留，请先核实入口。')
                elif request.mode == 'team_then_multi':
                    from hyol_team_resume import save_cursor as save_pipeline_cursor
                    try:
                        cursor=save_pipeline_cursor(context,request,status=receipt['status'])
                        receipt['pipeline_resumable']=cursor['resumable']
                        if code==130:
                            receipt['detail']='已有队伍本轮已停止；再次开始沿原场次、原通缉与押镖凭据继续。'
                    except OSError as exc:
                        receipt.update(status='pending',result=2,save_error=str(exc),
                                       detail='已有队伍原轮次入口保存失败；原任务文件保留，请先核实入口。')
            if pipeline_summary:
                receipt['pipeline'] = pipeline_summary
            if isinstance(runner_summary, dict):
                receipt['runner_summary'] = runner_summary
            try:
                hs.atomic_json(self.root/'logs'/('unified_'+self.identifier+'.json'), receipt)
                hs.atomic_json(self.root/'logs/unified_latest.json', receipt)
            except OSError as exc:
                receipt['save_error'] = str(exc)
            try:
                self.emit('unified_finished', **receipt)
            finally:
                self._release_run_resources()
            self.emit('unified_idle')

    def _release_run_resources(self):
        # The receipt owns only plain data. The GUI no longer needs runners or
        # captures; drop even cyclic nested runners after the worker is finished.
        pending = [self.runner, self.scheduler]
        self.runner = self.scheduler = self.run_context = None
        seen = set()
        while pending:
            current = pending.pop()
            if current is None or id(current) in seen:
                continue
            seen.add(id(current))
            values = getattr(current, '__dict__', {})
            pending.extend(values.get(key) for key in ('scheduler', 'solo'))
            runners = values.get('runners')
            if isinstance(runners, dict):
                pending.extend(runners.values())
            if isinstance(current, hs.Scheduler):
                current.release_resources()

