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


def plain(value):
    return json.loads(json.dumps(value, ensure_ascii=False, default=str),parse_constant=lambda _value: None)


def parse_request(payload):
    if not isinstance(payload, dict):
        raise ValueError('任务参数必须为对象')
    names = {f.name for f in fields(RunRequest)}
    if set(payload) - names:
        raise ValueError('存在不支持的任务参数')
    result = asdict(RunRequest())
    result.update(payload)
    for name in ('account_count', 'arena_wins', 'leader', 'bond_count'):
        if type(result[name]) is not int:
            raise ValueError('账号、标签及次数必须是整数')
    for name in ('friend_shortcut', 'new_bond_round', 'final_managed'):
        if type(result[name]) is not bool:
            raise ValueError('开关必须为布尔值')
    for name in ('mode', 'formation', 'arena_target_mode'):
        if not isinstance(result[name], str):
            raise ValueError('任务类型或选项无效')
    for name in ('tasks', 'team_tasks', 'members', 'leaders'):
        values = result[name]
        if not isinstance(values, (list, tuple)) or len(values) > 20:
            raise ValueError('任务或标签列表无效')
        expected = str if name in ('tasks', 'team_tasks') else int
        if any(type(v) is not expected for v in values):
            raise ValueError('任务或标签列表类型无效')
        result[name] = tuple(values)
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
    if request.final_managed:
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
        if 'evidence' in key or key.endswith('_path'):
            add(value)
    return list(dict.fromkeys(found))[-8:]


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
        self._extras = {}
        self._logs = deque(maxlen=1000)
        self._sequence = 0
        self._next_read = 0.0
        self._warnings = []
        self._allowed_evidence = set()
        self._close_notice = ''
        self._closed = False

    def _log(self, event, message, account='系统', level='info', stamp=None):
        self._sequence += 1
        self._logs.append(dict(id=self._sequence, time=(stamp or datetime.now().isoformat())[11:19],
                               level=level, account=account, event=event, message=str(message)))

    def _observe(self, event):
        with self._lock:
            name = str(event.get('event', ''))
            owner = event.get('run_id')
            if name.startswith('unified_') and owner and owner != self._controller.identifier:
                return
            if (not name.startswith('unified_') and owner and self._request
                    and self._request.mode != 'bond' and owner != self._run_id):
                return
            account, index = event.get('account'), event.get('tab_index')
            if isinstance(account, str) and type(index) is int and 1 <= index <= 6:
                if not account.startswith('当前窗口标签'):
                    self._bindings[index] = account
            label = f'标签 {index}' if type(index) is int else '系统'
            text = event.get('detail') or event.get('reason') or event.get('message') or name
            level = ('error' if 'error' in name or 'offline' in name or 'disconnected' in name else
                     'warning' if any(k in name for k in ('pending', 'skipped', 'stop', 'not_open')) else
                     'success' if any(k in name for k in ('complete', '_done', '_verified')) else 'info')
            self._log(name, text, label, level, str(event.get('ts') or datetime.now().isoformat()))
            self._updated = datetime.now().isoformat(timespec='seconds')
            if name == 'unified_status' or name == 'unified_error':
                self._detail = str(text)
            elif name == 'unified_stop_requested':
                self._status, self._detail = 'stopping', str(text)
            elif name == 'unified_window_check':
                self._last_check = plain(event.get('check', {}))
                ready = (self._last_check.get('status') == 'ready' and self._last_check.get('healthy') is True
                         and self._last_check.get('content_clicks') == 0)
                self._detail = '窗口只读检查通过。' if ready else str(self._last_check.get('reason') or '窗口检查待处理。')
            elif name == 'unified_leaders_detected':
                self._leaders = plain(event.get('check', {}))
                self._detail = '队长识别已结束，请核对标签。'
            elif name == 'unified_finished':
                self._receipt = plain(event)
                self._status = event.get('status') if event.get('status') in ('complete', 'stopped', 'pending') else 'pending'
                self._detail = str(event.get('detail') or {'complete':'本轮任务与收尾已确认。',
                                                        'stopped':'本轮已停止。', 'pending':'本轮仍有待处理项。'}[self._status])
                if self._status == 'pending' and '所选活动当前未开放' in self._detail:
                    self._status = 'not_open'
                self._next_read = 0
                self._finish_bond_rows(event)
            elif name == 'unified_idle' and self._operation in ('check','leaders'):
                self._status = self._precheck_status
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
        if kind in ('generic_team_task_started', 'generic_team_managed_started', 'pipeline_team_tick'):
            status = 'running'
        elif kind in ('generic_team_task_pending', 'generic_team_managed_pending'):
            status = 'pending'
        elif kind in ('generic_team_task_done', 'generic_team_managed_complete'):
            status = 'done'
        if status is None:
            return
        targets = [(account, event.get('evidence'))]
        if isinstance(event.get('evidence'), dict):
            targets = list(event['evidence'].items())
        for owner, proof in targets:
            rec = self._extras.setdefault((owner, code), empty_record())
            paths = evidence_paths({'evidence':proof})
            verified = status == 'done' and bool(paths) and (code == 'managed' or event.get('home_returned') is True)
            rec.update(status='done' if verified else 'pending' if status == 'done' else status,
                       task_completed=verified,home_returned=bool(verified and code != 'managed'),
                       evidence_paths=paths,last_error=str(event.get('detail') or ''))
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
        self._bindings.setdefault(1, owner)
        verified = receipt.get('status') == 'complete'
        proof = receipt.get('final_managed_evidence') or receipt.get('home_evidence')
        rec = empty_record()
        rec.update(status='done' if verified else 'pending', task_completed=verified,
                   home_returned=verified, completed=original.get('completed', 0),
                   requested=original.get('requested', self._request.bond_count),
                   evidence_paths=evidence_paths({'evidence':proof}),last_error=receipt.get('detail', ''))
        self._extras[(owner, 'bond')] = rec
        if self._request.final_managed:
            self._extras[(owner, 'managed')] = {**rec, 'evidence':proof} if verified else empty_record()

    def start(self, payload):
        try:
            request = parse_request(payload)
            with self._lock:
                if self._closed or self._controller.busy:
                    return dict(ok=False, error='当前操作尚未结束，请先停止并等待结束。')
                if not self._controller.start(request):
                    return dict(ok=False,error='运行层正在忙，请稍后重试。')
                self._request, self._status, self._operation = request, 'running', 'run'
                self._day, self._started = local_day(), datetime.now().isoformat(timespec='seconds')
                self._updated = self._started
                self._rows, self._bindings, self._extras = [], {}, {}
                self._logs.clear()
                self._receipt = self._last_check = self._leaders = None
                self._allowed_evidence.clear()
                self._warnings, self._close_notice = [], ''
                self._detail = '正在创建新运行并核实游戏窗口。'
                self._next_read = 0
                self._run_id = self._controller.identifier
                return dict(ok=True,run_id=self._run_id)
        except (ValueError, TypeError) as exc:
            return dict(ok=False,error=str(exc))

    def stop(self):
        with self._lock:
            try:
                stopped = self._controller.stop()
                if stopped:
                    self._status = 'stopping'
                return dict(ok=bool(stopped),error='' if stopped else '当前没有正在执行的操作。')
            except Exception as exc:
                self._status = 'stopping'
                self._detail = '停止已请求；暂停记录保存异常：'+str(exc)
                return dict(ok=True,warning=self._detail)

    def _launch_check(self, operation):
        with self._lock:
            if self._closed or self._controller.busy:
                return dict(ok=False,error='请等待当前操作结束。')
            self._operation = operation
            self._precheck_status = self._status
            self._last_check = self._leaders = None
            method = self._controller.check_window if operation == 'check' else self._controller.detect_team_leaders
            return dict(ok=bool(method()))

    def check_window(self):
        return self._launch_check('check')

    def detect_leaders(self):
        return self._launch_check('leaders')

    def _read_pipeline(self):
        if not self._request or self._request.mode != 'team_then_multi':
            return
        path = shared_state(self._root)/'runs'/self._run_id/'team_pipeline_latest.json'
        try:
            record = json.loads(path.read_text('utf-8-sig'))
        except FileNotFoundError:
            return
        except (OSError, ValueError) as exc:
            self._warnings.append('组队进度待刷新：'+str(exc))
            return
        if record.get('id') != self._run_id:
            return
        keys = {'group':'group_instance','strong':'strong_approach','wanted':'wanted'}
        for team in record.get('teams', {}).values():
            for key, item in team.get('tasks', {}).items():
                code = keys.get(key)
                if code not in task_codes(self._request) or not isinstance(item, dict):
                    continue
                rec = {**empty_record(),**item}
                paths = evidence_paths(rec)
                verified = (rec.get('status') == 'done' and rec.get('task_completed') is True
                            and rec.get('home_returned') is True and bool(paths))
                if rec.get('status') == 'done' and not verified:
                    rec['status'], rec['task_completed'] = 'pending', False
                if not verified and rec.get('availability',{}).get('status') == 'not_open':
                    rec.update(status='skipped',last_error='该活动当前未开放，未计完成。')
                rec['evidence_paths'] = paths
                for account in team.get('members', []):
                    self._extras[(account,code)] = deepcopy(rec)

    def _snapshot(self):
        if not self._request or not self._run_id:
            return None
        if time.monotonic() >= self._next_read:
            self._next_read = time.monotonic()+2
            try:
                observed = self._reader.load(self._day,self._run_id)
                self._rows = deepcopy(observed['rows'])
                self._warnings = list(observed.get('warnings', []))
                self._read_pipeline()
            except (OSError, ValueError, TypeError, KeyError) as exc:
                self._warnings = ['进度待刷新，保留已有核实结果：'+str(exc)]
        rows = {r['index']: deepcopy(r) for r in self._rows}
        for index, name in self._bindings.items():
            rows.setdefault(index,dict(index=index,account=name,health='已绑定',tasks={}))
        codes = task_codes(self._request)
        accounts = []
        allowed = set()
        for index,row in sorted(rows.items()):
            name = row.get('account')
            tasks = {}
            for code in codes:
                rec = {**empty_record(),**row.get('tasks', {}).get(code, {})}
                extra = self._extras.get((name,code))
                if extra:
                    rec.update(deepcopy(extra))
                rec['events'] = rec.get('events', [])[-4:]
                rec['evidence_paths'] = evidence_paths(rec)
                if self._status in ('stopped','pending','not_open') and rec['status'] == 'running':
                    rec.update(status='pending',task_completed=False,last_error=rec.get('last_error') or self._detail)
                for path in rec['evidence_paths']:
                    allowed.add(str(Path(path).resolve()))
                tasks[code] = rec
            accounts.append(dict(slot=index,label=f'标签 {index}',account=name,
                                 identity='本轮账号已关联' if name else '身份待核实',
                                 health=row.get('health') or '身份待核实',tasks=tasks))
        self._allowed_evidence = allowed
        return dict(runId=self._run_id,day=self._day,startedAt=self._started,updatedAt=self._updated,
                    status=self._status,taskCodes=codes,accounts=accounts,warning='；'.join(self._warnings),
                    logs=list(self._logs),detail=self._detail)

    def get_state(self):
        with self._lock:
            return plain(dict(version=VERSION,mode='native',busy=self._controller.busy,
                              operation=self._operation,status=self._status,detail=self._detail,
                              snapshot=self._snapshot(),last_check=self._last_check,
                              leaders=self._leaders,close_notice=self._close_notice,
                              request=asdict(self._request) if self._request else None))

    def read_evidence(self, value):
        with self._lock:
            try:
                if not isinstance(value,str):
                    raise ValueError('证据标识无效')
                path = Path(value).resolve()
                if str(path) not in self._allowed_evidence or path.suffix.lower() not in ('.png','.jpg','.jpeg'):
                    raise ValueError('只允许查看当前运行已记录的图片证据')
                roots = [self._root/'logs',shared_state(self._root)/'runs'/self._run_id]
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
                return dict(ok=False,error=str(exc))

    def export_logs(self):
        with self._lock:
            try:
                folder = self._root/'logs'/'exports'
                folder.mkdir(parents=True,exist_ok=True)
                path = folder/f'visible-log-{time.time_ns()}.txt'
                lines = ['火影OL 本轮界面日志（最近1000条；完整运行记录见logs）']
                lines.extend('\t'.join(str(row[k]) for k in ('time','level','account','event','message')) for row in self._logs)
                path.write_text('\n'.join(lines)+'\n','utf-8-sig')
                return dict(ok=True,path=str(path))
            except OSError as exc:
                return dict(ok=False,error=str(exc))

    def open_logs(self):
        try:
            path = self._root/'logs'
            path.mkdir(parents=True,exist_ok=True)
            os.startfile(str(path))
            return dict(ok=True)
        except OSError as exc:
            return dict(ok=False,error=str(exc))

    def _can_close(self):
        with self._lock:
            if self._controller.busy:
                self._close_notice = '任务运行中，请先停止本轮，待结束后再关闭窗口。'
                return False
            return True

    def _close(self):
        with self._lock:
            self._closed = True
            self._reader.close()
            self._rows.clear()
            self._extras.clear()
            self._allowed_evidence.clear()
