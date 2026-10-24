"""Read-only task snapshots. Never construct a scheduler or merge/write ledgers."""
from __future__ import annotations

import json
import hashlib
import os
import re
import sqlite3
from collections import OrderedDict
from datetime import datetime, timedelta, timezone
from hyol_progress_index import ProgressLogIndex
from pathlib import Path

TASK_LABELS = dict(mail='邮件', welfare='福利', ranking='排位', survival='生存',
                   quiz='测验', arena='竞技场', daruma='达摩', weekend='周末', treasure='挖宝', managed='托管')
STATUSES = dict(new='未执行', running='执行中', done='已完成', pending='待核实',
                skipped='跳过', unselected='未选择', offline='掉线')


def mail_cleanup_required(record):
    return (record.get('status') == 'pending'
            and '普通背包已满' in str(record.get('last_error') or ''))


def task_status_text(task, record):
    if task == 'mail' and mail_cleanup_required(record):
        return '背包满需清理'
    return STATUSES.get(record.get('status'), '待核实')


def local_day():
    return datetime.now(timezone(timedelta(hours=8))).date().isoformat()


def shared_state(root):
    root = Path(root)
    for name in ('HYOL_Daily.ini', 'DAILY_Client.ini', 'ARENA_Client.ini'):
        path = root / name
        if path.is_file():
            for line in path.read_text(encoding='utf-8-sig').splitlines():
                key, sep, value = line.partition('=')
                if sep and key.strip() == 'SharedStateDirectory':
                    directory = Path(os.path.expandvars(value.strip()))
                    return directory if directory.is_absolute() else root / directory
    return root / 'state'


def action_task(key):
    for task in TASK_LABELS:
        if key.startswith((task + '_', 'daily_' + task + '_')):
            return task
    if key == 'start_managed' or key.startswith('start_managed_'):
        return 'managed'
    if key.startswith(('daruma_', 'puzzle_')):
        return 'weekend'
    return None


def complete(task, rec):
    if not (rec.get('status') == 'done' and rec.get('task_completed') is True
            and not rec.get('followup_check_due')):
        return False
    if task == 'managed':
        return bool(rec.get('evidence'))
    if task == 'daruma':
        from hyol_daruma_task import verified
        return rec.get('home_returned') is True and verified(rec)
    return rec.get('home_returned') is True and (task != 'ranking' or rec.get('zero_verified') is True)


def survival_battle_counts(events, account, day, actions=None):
    """Count result/start/confirmation records; scores never imply battles."""
    def verified(records):
        result = {}
        for key, value in records.items():
            match = re.fullmatch(r'survival_battle_step_(\d+)', key)
            if not match or not isinstance(value, dict) or value.get('status') != 'done':
                continue
            if value.get('updated_at') and not str(value['updated_at']).startswith(day):
                continue
            if all(isinstance(records.get(other), dict) and records[other].get('status') == 'done' for other in (
                    key+'_start', 'survival_result_confirm_'+match[1])):
                result[key] = value
        return result

    archived, seen, observed = [], set(), set()
    archived_records = []
    for event in events:
        if event.get('account') != account:
            continue
        if event.get('event') == 'irreversible_committed' and re.fullmatch(
                r'survival_battle_step_\d+', str(event.get('target', ''))):
            observed.add(event['target'])
        if event.get('event') != 'survival_first_run_archived':
            continue
        records = verified(event.get('actions', {}))
        signature = json.dumps(records, sort_keys=True, ensure_ascii=False)
        if signature in seen:
            continue
        seen.add(signature)
        archived_records.append(records)
        archived.append(len(records) if actions is not None else len(observed))
        observed.clear()
    if actions is None:
        current = len(observed)
    else:
        records = verified(actions)
        # The archive event precedes clearing the first-round keys. Avoid
        # counting that same snapshot twice during its atomic-file updates.
        if archived_records and actions.get('survival_second_run_started', {}).get('status') != 'done':
            for archive in archived_records:
                records = {key: value for key, value in records.items()
                           if key not in archive or value.get('operation_id') != archive[key].get('operation_id')}
        current = len(records)
    first = sum(archived)
    return dict(verified_battles=first+current, verified_first_round_battles=first,
                verified_current_round_battles=current)


def discover_log_roots(root, state=None):
    """Every logs directory this installation may read; creates no index.

    Pure directory discovery so scheduler back-tracking can locate evidence
    folders without building a temporary SQLite index or a progress reader.
    """
    root = Path(root)
    state = shared_state(root) if state is None else Path(state)
    roots = [root / 'logs', state.parent / 'logs']
    desktop = Path(os.environ.get('USERPROFILE', str(Path.home()))) / 'Desktop' / '可用脚本'
    if desktop.is_dir():
        for package in desktop.iterdir():
            if package.is_dir() and any((package / name).is_file() for name in
                                       ('HYOL_Daily.ini', 'DAILY_Client.ini', 'ARENA_Client.ini')):
                if shared_state(package).resolve() == state.resolve():
                    roots.append(package / 'logs')
    return list(dict.fromkeys(roots))


class ProgressReader:
    def __init__(self, root, *, log_roots=None):
        self.root = Path(root)
        self.state = shared_state(self.root)
        self.log_roots = ([Path(p) for p in log_roots] if log_roots is not None
                          else discover_log_roots(self.root, self.state))
        self.cache = OrderedDict()
        self.cache_bytes = 0
        self.max_cache_entries = 32
        self.max_cache_bytes = 8 * 1024 * 1024
        self._cache_sizes = {}
        # The disk index is disposable: constructing it lazily keeps a locked or
        # unwritable TEMP from ever breaking the client itself.
        self.index = None
        self._index_error = ''
        self._last_good = None
        self._scope = None
        self.warnings = []

    def _ensure_index(self):
        if self.index is None:
            try:
                self.index = ProgressLogIndex(TASK_LABELS, action_task)
                self._index_error = ''
            except (OSError, sqlite3.Error) as exc:
                self._index_error = str(exc)
        return self.index

    def _discard_index(self):
        index, self.index = self.index, None
        if index is not None:
            try:
                index.close()
            except Exception:
                pass

    def close(self):
        self._discard_index()
        self.cache.clear()
        self._cache_sizes.clear()
        self.cache_bytes = 0

    def __del__(self):
        try:
            self.close()
        except Exception:
            pass

    def _read(self, path):
        """Small ledger cache only; JSONL history is indexed separately."""
        path = Path(path)
        try:
            stat = path.stat()
            signature = (stat.st_mtime_ns, stat.st_size, stat.st_ino)
            previous = self.cache.get(path)
            if previous and previous[0] == signature:
                self.cache.move_to_end(path)
                return previous[1]
            value = json.loads(path.read_text(encoding='utf-8-sig'))
            if not isinstance(value, dict):
                raise ValueError('expected object')
            self.cache_bytes -= self._cache_sizes.pop(path, 0)
            self.cache.pop(path, None)
            if stat.st_size <= self.max_cache_bytes:
                self.cache[path] = (signature, value)
                self._cache_sizes[path] = stat.st_size
                self.cache_bytes += stat.st_size
            while self.cache and (len(self.cache) > self.max_cache_entries or self.cache_bytes > self.max_cache_bytes):
                old, _ = self.cache.popitem(last=False)
                self.cache_bytes -= self._cache_sizes.pop(old)
            return value
        except FileNotFoundError:
            self.cache.pop(path, None)
            self.cache_bytes -= self._cache_sizes.pop(path, 0)
            return {}
        except (OSError, ValueError) as exc:
            self.warnings.append('待刷新：' + path.name + ' ' + str(exc))
            previous = self.cache.get(path)
            return previous[1] if previous else {}

    def days(self):
        found = {local_day()}
        if self.state.is_dir():
            found.update(p.name for p in self.state.iterdir()
                         if p.is_dir() and re.fullmatch(r'\d{4}-\d{2}-\d{2}', p.name))
        return sorted(found, reverse=True)

    def load(self, day=None, run_id=None):
        day = day or local_day()
        if not re.fullmatch(r'\d{4}-\d{2}-\d{2}', day):
            raise ValueError('日期须为YYYY-MM-DD')
        self.warnings = []
        scope = (day, run_id)
        if scope != self._scope:
            self.cache.clear()
            self._cache_sizes.clear()
            self.cache_bytes = 0
            self._scope = scope
            self._last_good = None  # The previous snapshot belongs to another view.
        progress_index = self._ensure_index()
        if progress_index is None:
            self.warnings.append('进度索引不可用，待刷新：' + self._index_error)
            return self._degraded(day, run_id)
        try:
            snapshot = self._load_with_index(progress_index, scope, day, run_id)
        except sqlite3.Error as exc:
            # A failed index transaction must never surface partial counts.
            # Drop the broken index (a fresh one is rebuilt on the next refresh)
            # and keep the last verified snapshot for this scope.
            self.warnings.append('进度索引读取失败，保留上次快照：' + str(exc))
            self._discard_index()
            return self._degraded(day, run_id)
        self._last_good = snapshot
        return snapshot

    def _degraded(self, day, run_id):
        # Missing data is unknown state, never completion: without a verified
        # snapshot for this scope the panel simply shows an empty unknown view.
        warnings = list(dict.fromkeys(self.warnings))
        if self._last_good is not None:
            snapshot = dict(self._last_good)
            snapshot['warnings'] = warnings
            return snapshot
        return dict(day=day, run_id=run_id, updated_at='', rows=[], runs=[], warnings=warnings)

    def _load_with_index(self, progress_index, scope, day, run_id):
        pattern = ('HYOL_Scheduler_' + run_id + '.jsonl' if run_id else
                   'HYOL_Scheduler_' + day.replace('-', '') + '_*.jsonl')
        paths = [path for root in self.log_roots for path in root.glob(pattern)]
        observed_summary = progress_index.sync(paths, scope, self.warnings)
        runs = observed_summary.runs
        global_state = self._read(self.state / (day + '.json')) if not run_id else {}
        latest = self._read(self.state / 'multi_solo_latest.json') if not run_id else {}
        if not str(latest.get('updated_at', '')).startswith(day):
            latest = {}
        accounts = {}
        if not run_id:
            for path in (self.state / day).glob('account_*.json'):
                if path.name.endswith('_tasks.json'):
                    continue
                ledger = self._read(path)
                name = ledger.get('account')
                if name:
                    accounts[name] = (ledger, self._read(path.with_name(path.stem + '_tasks.json')))
        bindings = {}
        count = int(latest.get('tab_count') or (6 if day == local_day() and not run_id else 0))
        if not run_id:
            mapping = global_state.get('verified_solo_tab_map', global_state.get('tab_map', {}))
            for name, x in mapping.items():
                matches = [i+1 for i, center in enumerate(observed_summary.centers) if abs(center-x) <= 20]
                if len(matches) == 1:
                    bindings[matches[0]] = name
        for index, name in observed_summary.bindings.items():
            for old in [key for key, value in bindings.items() if value == name and key != index]:
                del bindings[old]
            bindings[index] = name
        count = observed_summary.count or max(count, len(bindings))
        health, cells = observed_summary.health, observed_summary.cell_records()
        ended_runs, last_events = observed_summary.ended_runs, observed_summary.last_events
        selected = None
        if observed_summary.started is not None:
            selected = set(observed_summary.started.get('tasks', []))
            if observed_summary.started.get('managed_enabled', latest.get('managed_enabled', True)):
                selected.add('managed')
        if not run_id:
            # A resumed task can be saved before its companion action ledger
            # is written today. Its verified binding still identifies the exact
            # task file; never drop a pending task just because that ledger is absent.
            for name in set(bindings.values()):
                if name in accounts:
                    continue
                stems = ['account_' + hashlib.sha256(name.encode('utf-8')).hexdigest()[:10]]
                if re.fullmatch(r'account_[0-9a-f]{32}', name):
                    stems.insert(0, name)
                for stem in stems:
                    records = self._read(self.state / day / (stem + '_tasks.json'))
                    if records:
                        accounts[name] = (self._read(self.state / day / (stem + '.json')), records)
                        break
        rows = []
        for index in range(1, max(count, max(bindings, default=0)) + 1):
            name = bindings.get(index)
            ledger, records = accounts.get(name, ({}, {}))
            actions = ledger.get('actions', {})
            task_rows = {}
            for task in TASK_LABELS:
                observed = cells.get((name, task), {})
                rec = dict(records.get(task, {})) if not run_id else dict(observed)
                rec['events'] = observed.get('events', [])
                paths = list(observed.get('evidence_paths', []))
                for key, value in rec.items():
                    if isinstance(value, str) and ('evidence' in key or key.endswith('_path')):
                        paths.append(value)
                error = str(rec.get('last_error') or '')
                paths.extend(re.findall(r'[A-Za-z]:[\\/][^\n；]+?\.(?:png|jpg|jpeg)', error))
                pending_actions = [key for key, value in actions.items()
                                   if isinstance(value, dict) and value.get('status') == 'pending'
                                   and action_task(key) == task]
                if run_id:
                    status = rec.get('status', 'new')
                elif pending_actions or rec.get('followup_check_due'):
                    status = 'pending'
                elif complete(task, rec):
                    status = 'done'
                else:
                    status = rec.get('status', 'new')
                    if status == 'done':
                        status = 'pending'
                if status == 'new' and selected is not None and task not in selected:
                    status = 'unselected'
                if status == 'running':
                    recent = observed.get('events', [])
                    owner = recent[-1].get('run_id') if recent else latest.get('run_id')
                    last = last_events.get(owner, {})
                    try:
                        age = (datetime.now(timezone.utc)-datetime.fromisoformat(last.get('ts',''))).total_seconds()
                    except ValueError:
                        age = float('inf')
                    if owner in ended_runs or age > 30:
                        status = 'pending'
                        error = error or '运行已停止，完成条件尚未核实'
                if name in health and status in ('new', 'running', 'skipped', 'pending'):
                    status = 'offline'
                rec.update(status=status, pending_actions=pending_actions,
                           evidence_paths=list(dict.fromkeys(paths)), last_error=error)
                rec['verified_claims'] = [key for key, value in actions.items()
                                         if isinstance(value, dict) and value.get('status') == 'done'
                                         and action_task(key) == task and ('claim' in key or 'reward' in key)]
                rec['verified_results'] = [key for key, value in actions.items()
                                          if isinstance(value, dict) and value.get('status') == 'done'
                                          and action_task(key) == task and ('victory' in key or 'defeat' in key or 'result_confirm' in key)]
                if task == 'survival':
                    rec.update(survival_battle_counts(progress_index.survival_events(name), name, day, None if run_id else actions))
                if task == 'arena' and not run_id:
                    request = global_state.get('arena_requests', {}).get(name, {})
                    wins = sum(isinstance(v, dict) and v.get('status') == 'done'
                               for k, v in actions.items() if re.fullmatch(r'arena_attempt_victory_\d+', k))
                    rec['verified_wins'] = max(0, wins - request.get('base_wins', 0))
                    rec['requested_wins'] = request.get('requested_wins')
                if run_id and task == 'arena':
                    rec['verified_wins'] = progress_index.arena_wins(name)
                task_rows[task] = rec
            rows.append(dict(label=f'标签{index}', index=index, account=name,
                             health=health.get(name, '已绑定' if name else '身份待核实'), tasks=task_rows))
        used = [e for row in rows for cell in row['tasks'].values() for e in cell.get('events', [])]
        updated = max([str(global_state.get('updated_at', ''))] + [str(e.get('ts', '')) for e in used])
        return dict(day=day, run_id=run_id, updated_at=updated, rows=rows,
                    runs=sorted(runs.items(), key=lambda row: row[1], reverse=True),
                    warnings=list(dict.fromkeys(self.warnings)))
