"""Narrow native UI adapter; game decisions remain with RuntimeController.

Only plain requests and current-run snapshots cross the WebView bridge. The UI
cannot choose a filesystem path, construct a scheduler, or declare completion.
"""
from __future__ import annotations

import base64
from collections import deque
from copy import deepcopy
from dataclasses import asdict, fields
from datetime import datetime
import json
import mimetypes
import os
from pathlib import Path
import threading
import time

from hyol_runtime import RuntimeController, RunRequest, VERSION
from hyol_task_progress import ProgressReader, local_day, shared_state
from hyol_log_messages import present_event, chinese_text, error_message, task_label, LEVEL_LABELS


def plain(value):
    return json.loads(json.dumps(value, ensure_ascii=False, default=str),parse_constant=lambda _value: None)


def user_error(exc, prefix=''):
    message,suggestion=error_message(str(exc))
    return dict(ok=False,error=prefix+message,suggestion=suggestion,diagnostic=str(exc))


def parse_request(payload):
    if not isinstance(payload, dict):
        raise ValueError('任务参数必须为对象')
    names = {f.name for f in fields(RunRequest)}
    if set(payload) - names:
        raise ValueError('存在不支持的任务参数')
    result = asdict(RunRequest())
    result.update(payload)
    for name in ('account_count', 'arena_wins', 'leader', 'bond_count', 'bond_kill_target', 'bond_score_target', 'escort_remaining'):
        if type(result[name]) is not int:
            raise ValueError('账号、标签及次数必须是整数')
    for name in ('friend_shortcut', 'new_bond_round', 'final_managed', 'escort_prior_settled', 'preformed_duos', 'arena_second_skill'):
        if type(result[name]) is not bool:
            raise ValueError('开关必须为布尔值')
    for name in ('mode', 'formation', 'arena_target_mode', 'bond_target_mode', 'bond_stop_mode',
                 'training_arena_stop_policy', 'team_control'):
        if name not in result:
            continue
        if not isinstance(result[name], str):
            raise ValueError('任务类型或选项无效')
    for name in ('tasks', 'team_tasks', 'members', 'leaders', 'preformed_trio', 'grocery_items'):
        values = result[name]
        if not isinstance(values, (list, tuple)) or len(values) > (32 if name in ('tasks','team_tasks') else 20):
            raise ValueError('任务或标签列表无效')
        expected = str if name in ('tasks', 'team_tasks', 'grocery_items') else int
        if any(type(v) is not expected for v in values):
            raise ValueError('任务或标签列表类型无效')
        result[name] = tuple(values)
    if not isinstance(result['bond_team_goals'], (list, tuple)):
        raise ValueError('每队羁绊目标必须为列表')
    if any(not isinstance(row, (list, tuple)) for row in result['bond_team_goals']):
        raise ValueError('每队羁绊目标格式无效')
    result['bond_team_goals'] = tuple(tuple(row) for row in result['bond_team_goals'])
    if not isinstance(result['preformed_pairs'], (list,tuple)) or any(not isinstance(row,(list,tuple)) for row in result['preformed_pairs']):
        raise ValueError('二人队配对格式无效')
    result['preformed_pairs'] = tuple(tuple(row) for row in result['preformed_pairs'])
    if not 1 <= result['leader'] <= 6 or any(v not in range(1, 7) for v in result['members']):
        raise ValueError('标签须为1～6')
    return RunRequest(**result)


def task_codes(request):
    if request.mode in ('solo', 'multi'):
        tasks = list(request.tasks)
    elif request.mode == 'team':
        tasks = list(request.team_tasks)
    elif request.mode == 'team_then_multi':
        tasks = [*request.team_tasks, *request.tasks]
    else:
        tasks = [request.mode]
    if request.final_managed and request.mode != 'bond':
        tasks.append('managed')
    return list(dict.fromkeys(tasks))


def evidence_paths(record):
    found = []
    def add(value):
        if isinstance(value, str) and Path(value).suffix.lower() in ('.png', '.jpg', '.jpeg'):
            found.append(value)
        elif isinstance(value, dict):
            for item in value.values():
                add(item)
        elif isinstance(value, (list, tuple)):
            for item in value:
                add(item)
    for key, value in record.items():
        if 'evidence' in key or key.endswith('_path') or key in ('puzzle', 'before', 'after', 'claims', 'combat_controls', 'goal_observation', 'member_results', 'daily_observation', 'transactions'):
            add(value)
    return list(dict.fromkeys(found))[-24:]


def empty_record():
    return dict(status='new', task_completed=False, home_returned=False,
                events=[], pending_actions=[], evidence_paths=[], verified_results=[],
                verified_claims=[], last_error='')


class DesktopBridge:
    def __init__(self, root, *, controller_factory=RuntimeController, reader_factory=ProgressReader):
        self._root = Path(root).resolve()
        self._lock = threading.RLock()
        self._controller = controller_factory(self._observe, self._root)
        self._reader = reader_factory(self._root)
        self._request = None
        self._run_id = ''
        self._day = local_day()
        self._status = 'idle'
        self._operation = ''
        self._precheck_status = 'idle'
        self._started = self._updated = ''
        self._detail = '就绪。选择任务后点击开始。'
        self._last_check = None
        self._leaders = None
        self._receipt = None
        self._rows = []
        self._bindings = {}
        self._slot_health = {}
        self._extras = {}
        self._logs = deque(maxlen=1000)
        self._debug_logs = deque(maxlen=1000)
        self._repeat_rows = {}
        self._active_log_tasks = {}
        self._log_write_warning = ''
        self._sequence = 0
        self._next_read = 0.0
        self._warnings = []
        self._allowed_evidence = set()
        self._close_notice = ''
        self._closed = False
        self._reader_thread = None
        self._generation = 0
        from hyol_active_timer import ActiveTimer
        from hyol_onboarding import Onboarding
        from hyol_desktop_journal import DesktopJournal
        self._timer = ActiveTimer(self._root/'logs/timing')
        self._onboarding = Onboarding(self._root)
        self._journal = DesktopJournal(self._root, VERSION,enabled=(self._root/'web/index.html').is_file())
        self._journal.recover_recent()

    def _log(self, event, message, account='系统', level='info', stamp=None, *, task='',
             repeatable=False, suggestion='', diagnostic='', elapsed=None, next_check=None, repeat_context=''):
        queue = self._debug_logs if level == 'debug' else self._logs
        stamp = (stamp or datetime.now().isoformat())[11:19]
        key = (event, account, task, level, str(message), repeat_context if repeatable else diagnostic)
        row = self._repeat_rows.get(key) if repeatable else None
        if row is not None and not any(item is row for item in queue):
            row = None
        if row is None and queue and all(queue[-1].get(k)==v for k,v in (
                ('event',event),('message',str(message)),('account',account),('level',level),('task',task),('diagnostic',diagnostic))):
            row = queue[-1]
        self._sequence += 1
        if row is not None:
            row.update(id=self._sequence, time=stamp, repeat=row.get('repeat',1)+1,
                       elapsed=elapsed, next_check=next_check, diagnostic=diagnostic)
            return row
        row = dict(id=self._sequence, time=stamp, first_time=stamp, level=level, account=account,
                   event=event, message=str(message), task=task, task_label=task_label(task),
                   repeat=1, suggestion=suggestion, diagnostic=diagnostic,
                   elapsed=elapsed, next_check=next_check)
        queue.append(row)
        if repeatable:
            self._repeat_rows[key] = row
        # Bound metadata as well as the two display queues.
        if len(self._repeat_rows) > 2000:
            retained = {id(item) for item in (*self._logs, *self._debug_logs)}
            self._repeat_rows = {key:value for key,value in self._repeat_rows.items() if id(value) in retained}
        return row

    def _visible_logs(self):
        return sorted((*self._logs, *self._debug_logs), key=lambda row: row['id'])

    def _observe(self, event):
        with self._lock:
            name = str(event.get('event', ''))
            owner = event.get('run_id')
            if name == 'unified_run_started' and owner:
                # A resumed training run rebinds the identifier inside the
                # worker after start() captured the fresh id; the event itself
                # carries the authoritative run directory id.
                self._run_id = str(owner)
                self._timer.rebind(self._run_id)
            if name.startswith('unified_') and owner and owner != self._controller.identifier:
                return
            if (not name.startswith('unified_') and owner and self._request
                    and self._request.mode != 'bond' and owner != self._run_id):
                return
            account, index = event.get('account'), event.get('tab_index')
            if name == 'account_disconnected_skipped' and type(index) is int:
                self._slot_health[index] = dict(status='offline',reason='已确认掉线提示，账号身份未绑定')
            if name=='arena3v3_member_pending' and type(index) is int:
                self._slot_health[index]=dict(status='pending',reason=str(event.get('detail') or '3v3成员待核实'))
            if name == 'dynamic_accounts_bound':
                for slot, reason in event.get('unavailable', {}).items():
                    if str(slot).isdigit() and 1 <= int(slot) <= 6:
                        self._slot_health[int(slot)] = dict(status='offline' if reason=='offline_prompt' else 'pending',reason=str(reason))
            if isinstance(account, str) and type(index) is int and 1 <= index <= 6:
                if not account.startswith('当前窗口标签'):
                    self._bindings[index] = account
                    if name in ('account_bound','account_verified','identity_bound'):
                        self._slot_health.pop(index,None)
            label = f'标签 {index}' if type(index) is int else '系统'
            display = present_event(event)
            self._journal.observe(event, owner or self._run_id)
            text, debug = display['message'], display['debug']
            task = display['task'] or self._active_log_tasks.get(label, '')
            if name.endswith(('_task_started','_stage_started')) and task:
                self._active_log_tasks[label] = task
                self._repeat_rows = {key:value for key,value in self._repeat_rows.items() if key[1] != label}
            self._log(name, text, label, display['level'], str(event.get('ts') or datetime.now().isoformat()),
                      task=task, repeatable=display['repeatable'], suggestion=display['suggestion'],
                      diagnostic=display['diagnostic'], elapsed=event.get('seconds'),
                      next_check=event.get('seconds_remaining'),
                      repeat_context=(event.get('phase'),event.get('running'),event.get('status')))
            warning = str(event.get('log_warning') or event.get('save_error') or '')
            if warning and warning != self._log_write_warning:
                message, suggestion = error_message('日志写入异常：' + warning)
                self._log('log_write_warning',message,level='warning',suggestion=suggestion,diagnostic=warning)
                self._log_write_warning = warning
            if (name.endswith(('_task_started','_stage_started','_waiting','_task_complete',
                               '_task_pending','_task_unavailable'))
                    or name in ('combat_battle_started','combat_battle_finished','combat_module_done',
                                'daily_ranking_battle_complete','daily_ranking_end_sent_yield',
                                'daily_ranking_waiting_result_yield','survival_reward_observed',
                                'daily_quiz_submitted')
                    or name.startswith('arena3v3_')) and not debug:
                self._detail=label+' · '+str(text)
            self._updated = datetime.now().isoformat(timespec='seconds')
            if name == 'unified_status' or name == 'unified_error':
                self._detail = str(text)
            elif name == 'escort_progress' and self._status in ('starting','running'):
                self._detail = f'{label} · 押镖：{text}'
            elif name in ('escort_started','escort_departed','escort_delivery_sent','escort_waiting','escort_leg_complete') and self._status in ('starting','running'):
                phase = {'escort_started':'已接镖，等待原任务出发',
                         'escort_departed':'已出发，等待原押送到达',
                         'escort_delivery_sent':'已交付，正在核对结果和剩余次数',
                         'escort_waiting':'等待原押送到达，保留原趟',
                         'escort_leg_complete':'本趟交付与剩余次数已核实'}[name]
                self._detail = f'{label} · 押镖：{phase}'
            elif name == 'unified_stop_requested':
                self._status, self._detail = 'stopping', str(text)
            elif name == 'unified_window_check':
                self._last_check = plain(event.get('check', {}))
                ready = (self._last_check.get('status') == 'ready' and self._last_check.get('healthy') is True
                         and (self._last_check.get('preparation') is True
                              and self._last_check.get('main_ready') is True
                              and bool(self._last_check.get('accounts'))
                              and all(r.get('leaf_verified') is True for r in self._last_check['accounts'])
                              or not self._last_check.get('preparation') and self._last_check.get('content_clicks') == 0))
                self._detail = ('所选账号已检查并确认回木叶村。' if self._last_check.get('preparation')
                                else '窗口只读检查通过。') if ready else str(self._last_check.get('reason') or '部分账号待处理，请查看逐号回村结果。')
            elif name == 'unified_leaders_detected':
                self._leaders = plain(event.get('check', {}))
                self._detail = '队长识别已结束，请核对标签。'
            elif name == 'unified_finished':
                self._timer.stop()
                self._receipt = plain(event)
                self._status = event.get('status') if event.get('status') in ('complete', 'stopped', 'pending') else 'pending'
                self._detail = chinese_text(event.get('detail'), text) or {'complete':'本轮任务与收尾已确认。',
                                                        'stopped':'本轮已停止。', 'pending':'本轮仍有待处理项。'}[self._status]
                if self._status == 'pending' and '所选活动当前未开放' in self._detail:
                    self._status = 'not_open'
                self._next_read = 0
                self._finish_bond_rows(event)
            elif name == 'unified_idle' and self._operation in ('check','leaders'):
                self._status = self._precheck_status
            elif name == 'unified_idle':
                self._timer.stop()
            # unified_idle marks the end of an operation, not a new empty run.
            # In particular it must never overwrite pending/stopped/completed.
            self._observe_team(event)

    def _observe_team(self, event):
        if not self._request:
            return
        kind, code, account = event.get('event', ''), event.get('task'), event.get('account')
        if kind.startswith('generic_team_managed_'):
            code = 'managed'
        if not isinstance(account, str) or code not in task_codes(self._request):
            return
        status = None
        if kind in ('generic_team_task_started', 'generic_team_managed_started', 'pipeline_team_tick', 'current_team_task_started'):
            status = 'running'
        elif kind in ('generic_team_task_pending', 'generic_team_managed_pending', 'current_team_task_pending'):
            status = 'pending'
        elif kind in ('generic_team_task_done', 'generic_team_managed_complete', 'current_team_task_done'):
            status = 'done'
        if status is None:
            return
        targets = [(account, event.get('evidence'))]
        if isinstance(event.get('evidence'), dict):
            targets = list(event['evidence'].items())
        for owner, proof in targets:
            rec = self._extras.setdefault((owner, code), empty_record())
            paths = evidence_paths({'evidence':proof})
            kept_ready = (kind == 'current_team_task_done' and code in ('bond','arena_3v3')
                          and event.get('completion_scope') == 'current_account' and event.get('activity_ready') is True)
            verified = status == 'done' and bool(paths) and (kept_ready or code == 'managed' or event.get('home_returned') is True)
            rec.update(status='done' if verified else 'pending' if status == 'done' else status,
                       task_completed=verified,home_returned=bool(verified and code != 'managed' and not kept_ready),
                       evidence_paths=paths,last_error=str(event.get('detail') or ''))
            if kind.startswith('current_team_'):
                rec.update(completion_scope='current_account', activity_ready=kept_ready,
                           ready_returned=kept_ready)
            if code == 'managed' and verified:
                rec['evidence'] = paths[-1]

    def _finish_bond_rows(self, receipt):
        if not self._request or self._request.mode != 'bond':
            return
        runner = self._controller.runner
        original = plain(getattr(runner, 'request', {}) or {})
        owner = original.get('owner') or next(iter(self._bindings.values()), None)
        if not owner:
            return
        index = getattr(getattr(runner, 'account', None), 'index', None)
        index = index if type(index) is int and index > 0 else next((k for k,v in self._bindings.items() if v == owner), 1)
        self._bindings.setdefault(index, owner)
        verified = receipt.get('status') == 'complete'
        proof = receipt.get('bond_ready_evidence') or receipt.get('final_managed_evidence') or receipt.get('home_evidence')
        rec = empty_record()
        rec.update(status='done' if verified else 'pending', task_completed=verified,
                   home_returned=verified and not receipt.get('bond_kept_ready', False), completed=original.get('completed', 0),
                   ready_returned=verified and receipt.get('bond_kept_ready', False),
                   requested=original.get('requested', self._request.bond_count),
                   stop_mode=original.get('stop_mode', 'battles'), verified_wins=original.get('verified_wins', 0),
                   evidence_paths=evidence_paths({'evidence':proof}),last_error=receipt.get('detail', ''))
        self._extras[(owner, 'bond')] = rec
        rec['bond_goal_label'] = {'endless':'一直打，手动停止','battles':'打5次，胜负均计','wins':'5场胜利'}.get(rec['stop_mode'], '打5次，胜负均计')
        if rec['stop_mode'] == 'endless':
            rec.pop('requested', None)
        if self._request.final_managed and not receipt.get('bond_kept_ready', False):
            self._extras[(owner, 'managed')] = {**rec, 'evidence':proof} if verified else empty_record()

    def start(self, payload):
        try:
            request = parse_request(payload)
            with self._lock:
                if self._closed or self._controller.busy:
                    return dict(ok=False, error='当前操作尚未结束，请先停止并等待结束。')
                if (self._root/'web/index.html').is_file() and self._onboarding.load()['required']:
                    return dict(ok=False,error='首次使用请先查看或跳过设置引导。',onboarding_required=True)
                if not self._controller.start(request):
                    return dict(ok=False,error='运行层正在忙，请稍后重试。')
                self._request, self._status, self._operation = request, 'running', 'run'
                self._day, self._started = local_day(), datetime.now().isoformat(timespec='seconds')
                self._updated = self._started
                self._rows, self._bindings, self._extras = [], {}, {}
                self._slot_health.clear()
                self._logs.clear()
                self._debug_logs.clear()
                self._repeat_rows.clear()
                self._active_log_tasks.clear()
                self._log_write_warning = ''
                self._receipt = self._last_check = self._leaders = None
                self._allowed_evidence.clear()
                self._warnings, self._close_notice = [], ''
                self._detail = '正在创建新运行并核实游戏窗口。'
                self._next_read = 0
                self._run_id = self._controller.identifier
                self._timer.begin(self._run_id)
                self._generation += 1
                return dict(ok=True,run_id=self._run_id)
        except (ValueError, TypeError) as exc:
            return user_error(exc)

    def stop(self):
        with self._lock:
            try:
                stopped = self._controller.stop()
                if stopped:
                    self._status = 'stopping'
                return dict(ok=bool(stopped),error='' if stopped else '当前没有正在执行的操作。')
            except Exception as exc:
                self._status = 'stopping'
                message, suggestion = error_message(str(exc), '暂停记录保存异常。')
                self._detail = '停止已请求；'+message
                return dict(ok=True,warning=self._detail,suggestion=suggestion,diagnostic=str(exc))

    def _launch_check(self, operation, scope=None):
        with self._lock:
            if self._closed or self._controller.busy:
                return dict(ok=False,error='请等待当前操作结束。')
            method = self._controller.check_window if operation == 'check' else self._controller.detect_team_leaders
            launched = bool(method(scope) if scope is not None else method())
            if not launched:
                # A lost race against the engine must not leave check-mode
                # operation state pointing at a run that never started.
                return dict(ok=False)
            self._operation = operation
            self._precheck_status = self._status
            self._last_check = self._leaders = None
            return dict(ok=True)

    def check_window(self, scope=None):
        from hyol_preparation import validate_scope
        try:
            return self._launch_check('check',validate_scope(scope) if scope is not None else None)
        except (TypeError,ValueError) as exc:
            return user_error(exc)

    def detect_leaders(self):
        return self._launch_check('leaders')

    def get_task_order(self):
        from hyol_task_order import load
        result = load(self._root)
        # A missing/broken standalone order must not replace a valid order
        # restored from desktop_settings.json with generated defaults.
        result['saved'] = (self._root/'desktop_task_order.json').is_file() and not bool(result.get('warning'))
        return result

    def get_client_settings(self):
        from hyol_desktop_settings import load
        return load(self._root)

    def get_onboarding(self):
        return self._onboarding.load()

    def acknowledge_onboarding(self, step):
        with self._lock:
            try:
                return self._onboarding.acknowledge(step)
            except (OSError,ValueError) as exc:
                return user_error(exc)

    def skip_onboarding(self):
        with self._lock:
            if self._closed or self._controller.busy:
                return dict(ok=False,error='请先等待当前操作结束')
            try:
                return self._onboarding.skip()
            except (OSError,ValueError) as exc:
                return user_error(exc)

    def complete_onboarding(self, config):
        if self._controller.busy:
            return dict(ok=False,error='请先等待当前操作结束')
        if len(self._onboarding.load()['steps']) != 6:
            return dict(ok=False,error='请先看完六组设置')
        saved = self.save_client_settings(config)
        if not saved.get('ok'):
            return saved
        try:
            return self._onboarding.finish()
        except (OSError,ValueError) as exc:
            return user_error(exc)

    def save_client_settings(self,config):
        from hyol_desktop_settings import save
        with self._lock:
            if self._closed or self._controller.busy:
                return dict(ok=False,error='请在本轮停止后保存下一次使用的设置。')
        try:
            return save(self._root,config)
        except (OSError,ValueError,TypeError) as exc:
            return user_error(exc,'设置未保存：')

    def save_task_order(self, order):
        from hyol_task_order import save
        try:
            return save(self._root, order)
        except (OSError, ValueError, TypeError) as exc:
            return user_error(exc)

    def _read_pipeline(self, record=None):
        if not self._request or self._request.mode != 'team_then_multi':
            return
        path = shared_state(self._root)/'runs'/self._run_id/'team_pipeline_latest.json'
        if record is None:return
        if record.get('id') != self._run_id:
            return
        keys = {'group':'group_instance','strong':'strong_approach','wanted':'wanted','bond':'bond','arena_3v3':'arena_3v3'}
        for team in record.get('teams', {}).values():
            if self._request.preformed_trio and len(team.get('members',[]))==3:
                for index,owner in zip(self._request.preformed_trio,team['members']):
                    self._bindings[index]=owner
            for key, item in team.get('tasks', {}).items():
                code = keys.get(key)
                if code not in task_codes(self._request) or not isinstance(item, dict):
                    continue
                rec = {**empty_record(),**item}
                paths = evidence_paths(rec)
                verified = (rec.get('status') == 'done' and rec.get('task_completed') is True
                            and rec.get('home_returned') is True and bool(paths))
                if code == 'bond':
                    from hyol_bond_goals import team_goal
                    from hyol_team_bond import verified as bond_verified
                    goal=team_goal(self._request,team.get('leader_slot',0))
                    if goal['mode'] in ('kills','score'):
                        verified=verified and bond_verified(item,team,self._request.bond_count,goal)
                if rec.get('status') == 'done' and not verified:
                    rec['status'], rec['task_completed'] = 'pending', False
                if not verified and rec.get('availability',{}).get('status') == 'not_open':
                    if code=='arena_3v3' and rec.get('phase')=='waiting_open':
                        rec.update(status='running',phase_label='等待匹配开放',
                            last_error='等待3v3匹配开放：10:00–14:00、18:00–22:00；队伍与原凭据保留。')
                    else:rec.update(status='skipped',last_error='该活动当前未开放，未计完成。')
                rec['evidence_paths'] = paths
                for account in team.get('members', []):
                    current = deepcopy(rec)
                    goal = current.get('goal', {})
                    if code == 'bond' and goal.get('mode') in ('kills','score'):
                        current.pop('requested', None)  # 99 is a run limit, never the user's target.
                        current['bond_goal_label'] = f'击杀宝箱领取至{goal["target"] or 160}人档' if goal['mode']=='kills' else f'积分降至≤{goal["target"]}'
                        observed = current.get('goal_observation', {}).get('members', {}).get(account, {})
                        if observed:
                            current['bond_current_score'] = observed['score']
                            current['bond_current_kills'] = f'已领至{observed.get("claimed_tier",0)}人档；累计{observed["kills"]}；本档{observed.get("tier_kills",observed["kills"])}/{observed["kill_cap"]}'
                    if code=='arena_3v3':
                        member=rec.get('members',{}).get(account,{})
                        current['arena3v3']=deepcopy(member)
                        current['completed']=sum(v.get('state')=='claimed' for v in member.get('rewards',{}).values())
                        current['requested']=6
                    self._extras[(account,code)] = current
                    if code=='arena_3v3' and self._request.preformed_trio:
                        try:slot_index=self._request.preformed_trio[team.get('members',[]).index(account)]
                        except (ValueError,IndexError):continue
                        for row in self._rows:
                            if row['index']==slot_index and row.get('account'):
                                self._extras[(row['account'],code)]=deepcopy(current)

    def _snapshot(self):
        if not self._request or not self._run_id:
            return None
        self._queue_refresh()
        rows = {r['index']: deepcopy(r) for r in self._rows}
        if self._request.preformed_trio:
            rows={i:r for i,r in rows.items() if i in self._request.preformed_trio}
            for index in self._request.preformed_trio:
                rows.setdefault(index,dict(index=index,account=None,health='身份待核实',tasks={}))
        for index, name in self._bindings.items():
            rows.setdefault(index,dict(index=index,account=name,health='已绑定',tasks={}))
        for index in self._slot_health:
            rows.setdefault(index,dict(index=index,account=None,health='身份待核实',tasks={}))
        if self._request.mode in ('multi','anniversary'):
            for index in range(1,self._request.account_count+1):
                rows.setdefault(index,dict(index=index,account=None,health='身份待核实',tasks={}))
        elif self._request.mode not in ('team','team_then_multi') and not self._request.preformed_trio:
            # A current-window run may be on physical tab 4. The history
            # projection pads 1..4, but those preceding slots were never
            # selected. Keep observed identities and explicit offline slots.
            observed=set(self._bindings)|set(self._slot_health)
            rows={index:row for index,row in rows.items() if row.get('account') or index in observed}
        codes = task_codes(self._request)
        accounts = []
        allowed = set()
        for index,row in sorted(rows.items()):
            name = row.get('account')
            unavailable = self._slot_health.get(index) if not name else None
            if unavailable:
                row['health'] = '已掉线：提示确认' if unavailable['status']=='offline' else '账号待核实：'+unavailable['reason']
            tasks = {}
            for code in codes:
                rec = {**empty_record(),**row.get('tasks', {}).get(code, {})}
                extra = self._extras.get((name,code))
                if extra:
                    rec.update(deepcopy(extra))
                if unavailable:
                    rec.update(status=unavailable['status'],task_completed=False,home_returned=False,
                               last_error=unavailable['reason'])
                rec['events'] = rec.get('events', [])[-4:]
                rec['evidence_paths'] = evidence_paths(rec)
                if self._status in ('stopped','pending','not_open') and rec['status'] == 'running':
                    rec.update(status='pending',task_completed=False,last_error=rec.get('last_error') or self._detail)
                for path in rec['evidence_paths']:
                    allowed.add(str(Path(path).resolve()))
                if rec.get('last_error'):
                    rec['error_diagnostic'] = str(rec['last_error'])
                    rec['last_error'], rec['error_suggestion'] = error_message(rec['last_error'])
                if rec.get('phase'):
                    rec['phase_label'] = chinese_text(rec['phase'], task_label(rec['phase']) or '状态待核实')
                tasks[code] = rec
            accounts.append(dict(slot=index,label=f'标签 {index}',account=name,
                                 identity='本轮账号已关联' if name else '身份待核实',
                                 health=chinese_text(row.get('health'),'账号状态待核实') or '身份待核实',tasks=tasks))
        self._allowed_evidence = allowed
        return dict(runId=self._run_id,day=self._day,startedAt=self._started,updatedAt=self._updated,**self._timer.snapshot(),
                    status=self._status,taskCodes=codes,accounts=accounts,warning='；'.join(chinese_text(item,'进度读取暂不可用，保留上次已核实记录。') for item in self._warnings),
                    logs=self._visible_logs(),detail=chinese_text(self._detail))

    def get_state(self):
        with self._lock:
            if not self._controller.busy:
                self._timer.stop()
            return plain(dict(version=VERSION,mode='native',busy=self._controller.busy,
                              operation=self._operation,status=self._status,detail=chinese_text(self._detail),
                              snapshot=self._snapshot(),last_check=self._last_check,
                              leaders=self._leaders,close_notice=self._close_notice,log_directory=str(self._journal.base),
                              log_warning=self._journal.warning,
                              logs=self._visible_logs() if not self._request else [],
                              request=asdict(self._request) if self._request else None))

    def _queue_refresh(self):
        if self._closed or self._reader_thread is not None or time.monotonic()<self._next_read:
            return
        day, run_id, generation = self._day, self._run_id, self._generation
        def refresh():
            try:
                observed=self._reader.load(day,run_id)
                path=shared_state(self._root)/'runs'/run_id/'team_pipeline_latest.json'
                record=json.loads(path.read_text('utf-8-sig')) if path.is_file() else {}
                with self._lock:
                    if not self._closed and generation==self._generation and run_id==self._run_id:
                        self._rows=deepcopy(observed['rows'])
                        self._warnings=list(observed.get('warnings',[]))
                        self._read_pipeline(record)
            except (OSError,ValueError,TypeError,KeyError) as exc:
                with self._lock:
                    if generation==self._generation:
                        self._warnings=['进度待刷新，保留已有核实结果：'+str(exc)]
            finally:
                with self._lock:
                    self._next_read=time.monotonic()+2
                    self._reader_thread=None
                if self._closed:self._reader.close()
        self._reader_thread=threading.Thread(target=refresh,name='hyol-progress-reader',daemon=True)
        self._reader_thread.start()

    def read_evidence(self, value):
        with self._lock:
            try:
                if not isinstance(value,str):
                    raise ValueError('证据标识无效')
                path = Path(value).resolve()
                if str(path) not in self._allowed_evidence or path.suffix.lower() not in ('.png','.jpg','.jpeg'):
                    raise ValueError('只允许查看当前运行已记录的图片证据')
                state = shared_state(self._root)
                roots = [self._root/'logs',state/'runs'/self._run_id,state.parent/'logs']
                if not any(path.is_relative_to(root.resolve()) for root in roots):
                    raise ValueError('证据路径不在本轮日志或状态目录')
                if path.stat().st_size > 24*1024*1024:
                    raise ValueError('图片过大，请从日志目录查看')
                data = path.read_bytes()
                from PIL import Image
                import io
                with Image.open(io.BytesIO(data)) as picture:
                    picture.verify()
                return dict(ok=True,data_url='data:'+mimetypes.guess_type(path.name)[0]+';base64,'+
                            base64.b64encode(data).decode('ascii'),path=str(path))
            except (OSError,ValueError,TypeError) as exc:
                return user_error(exc)

    def export_logs(self, filters=None):
        with self._lock:
            try:
                filters = filters if isinstance(filters, dict) else {}
                level, account, task = (str(filters.get(key) or 'all') for key in ('level','account','task'))
                query = str(filters.get('query') or '').lower().strip()
                after = filters.get('after_id', 0)
                if type(after) is not int or after < 0:
                    raise ValueError('日志筛选位置无效')
                rows = [row for row in self._visible_logs() if row['id'] > after
                        and (row['level'] != 'debug' if level == 'all' else row['level'] == level)
                        and (account == 'all' or row['account'] == account)
                        and (task == 'all' or row.get('task') == task)
                        and (not query or query in ' '.join(str(row.get(key,'')) for key in
                             ('account','event','message','suggestion','task_label')).lower())]
                folder = self._journal.folder(self._run_id)/'筛选导出' if self._run_id else self._journal.base/'筛选导出'
                folder.mkdir(parents=True,exist_ok=True)
                path = folder/f'筛选日志_{datetime.now():%Y年%m月%d日_%H点%M分%S秒}_{time.time_ns()}.txt'
                lines = ['火影OL 当前筛选日志（业务与诊断分别保留最近1000条；完整记录见日志目录）']
                for row in rows:
                    repeat = f"（累计{row.get('repeat',1)}次，首次{row.get('first_time',row['time'])}）" if row.get('repeat',1)>1 else ''
                    lines.append('\t'.join((row['time'], LEVEL_LABELS.get(row['level'],'信息'),row['account'],
                                           row.get('task_label',''),row['message']+repeat,row.get('suggestion',''))))
                    if level == 'debug':
                        lines.append('原始诊断：'+row.get('diagnostic',row['event']))
                path.write_text('\n'.join(lines)+'\n','utf-8-sig')
                return dict(ok=True,path=str(path),count=len(rows))
            except (OSError,ValueError) as exc:
                message, suggestion = error_message(str(exc), '日志导出失败。')
                return dict(ok=False,error=message,suggestion=suggestion,diagnostic=str(exc))

    def open_logs(self):
        try:
            path = self._journal.folder(self._run_id) if self._run_id else self._journal.base
            path.mkdir(parents=True,exist_ok=True)
            os.startfile(str(path))
            return dict(ok=True)
        except OSError as exc:
            return user_error(exc)

    def _can_close(self):
        with self._lock:
            if self._controller.busy:
                self._close_notice = '任务运行中，请先停止本轮，待结束后再关闭窗口。'
                return False
            return True

    def _close(self):
        with self._lock:
            self._timer.stop()
            self._journal.close()
            self._closed = True
            if self._reader_thread is None:self._reader.close()
            self._rows.clear()
            self._extras.clear()
            self._allowed_evidence.clear()
