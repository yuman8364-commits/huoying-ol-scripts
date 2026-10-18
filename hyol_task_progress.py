"""Read-only task snapshots. Never construct a scheduler or merge/write ledgers."""
from __future__ import annotations

import json
import hashlib
import os
import re
from datetime import datetime, timedelta, timezone
from pathlib import Path

TASK_LABELS = dict(mail='邮件', welfare='福利', ranking='排位', survival='生存',
                   quiz='测验', arena='竞技场', weekend='周末', treasure='挖宝', managed='托管')
STATUSES = dict(new='未执行', running='执行中', done='已完成', pending='待核实',
                skipped='跳过', unselected='未选择', offline='掉线')


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


class ProgressReader:
    def __init__(self, root, *, log_roots=None):
        self.root = Path(root)
        self.state = shared_state(self.root)
        self.log_roots = [Path(p) for p in log_roots] if log_roots is not None else self._log_roots()
        self.cache = {}
        self.warnings = []

    def _log_roots(self):
        roots = [self.root / 'logs', self.state.parent / 'logs']
        desktop = Path(os.environ.get('USERPROFILE', str(Path.home()))) / 'Desktop' / '可用脚本'
        if desktop.is_dir():
            for package in desktop.iterdir():
                if package.is_dir() and any((package / name).is_file() for name in
                                           ('HYOL_Daily.ini','DAILY_Client.ini','ARENA_Client.ini')):
                    if shared_state(package).resolve() == self.state.resolve():
                        roots.append(package / 'logs')
        return list(dict.fromkeys(roots))

    def _read(self, path, *, lines=False):
        path = Path(path)
        try:
            stat = path.stat()
            signature = (stat.st_mtime_ns, stat.st_size)
            previous = self.cache.get(path)
            if previous and previous[0] == signature:
                return previous[1]
            raw = path.read_text(encoding='utf-8-sig')
            if lines:
                value = []
                for line in raw.splitlines():
                    try:
                        event = json.loads(line)
                    except ValueError:
                        self.warnings.append('日志末行写入中：' + path.name)
                        continue
                    if isinstance(event, dict):
                        value.append(event)
            else:
                value = json.loads(raw)
                if not isinstance(value, dict):
                    raise ValueError('expected object')
            self.cache[path] = (signature, value)
            return value
        except FileNotFoundError:
            return [] if lines else {}
        except (OSError, ValueError) as exc:
            self.warnings.append('待刷新：' + path.name + ' ' + str(exc))
            previous = self.cache.get(path)
            return previous[1] if previous else ([] if lines else {})

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
        events = []
        for root in self.log_roots:
            pattern = ('HYOL_Scheduler_' + run_id + '.jsonl' if run_id else
                       'HYOL_Scheduler_' + day.replace('-', '') + '_*.jsonl')
            for path in root.glob(pattern):
                events.extend(self._read(path, lines=True))
        # Copies of one log do not count as extra victories/operations.
        unique = {}
        for event in events:
            key = (event.get('run_id'), event.get('ts'), event.get('event'),
                   event.get('account'), event.get('target'), event.get('task'))
            unique[key] = event
        events = sorted(unique.values(), key=lambda e: str(e.get('ts', '')))
        runs = {}
        for event in events:
            if event.get('event') in ('multi_solo_run_started', 'daily_task_started',
                                     'generic_current_window_bound', 'combat_interleaved_start'):
                identifier = event.get('run_id')
                if identifier:
                    runs.setdefault(identifier, str(event.get('ts', '')))
        if run_id:
            events = [e for e in events if e.get('run_id') == run_id]
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
        bindings, health, cells, selected = {}, {}, {}, None
        ended_runs = set()
        last_events = {}
        active_tasks = {}
        centers = []
        count = int(latest.get('tab_count') or (6 if day == local_day() and not run_id else 0))
        if not run_id:
            mapping = global_state.get('verified_solo_tab_map', global_state.get('tab_map', {}))
            # Derive physical slots from logged chrome, not compacted identity order.
            for event in reversed(events):
                if event.get('event') == 'multi_solo_tabs_counted':
                    centers = event.get('title_centers', [])
                    break
            for name, x in mapping.items():
                matches = [i+1 for i, center in enumerate(centers) if abs(center-x) <= 20]
                if len(matches) == 1:
                    bindings[matches[0]] = name
            count = max(count, len(bindings))
        for event in events:
            kind, name = event.get('event', ''), event.get('account')
            identifier = event.get('run_id')
            last_events[identifier] = event
            if kind in ('multi_solo_run_end','daily_summary','emergency_stop','scheduler_stopped'):
                ended_runs.add(identifier)
            index = event.get('tab_index')
            if kind == 'multi_solo_tabs_counted':
                count = int(event.get('count') or count)
            if kind == 'multi_solo_run_started':
                selected = set(event.get('tasks', []))
                if event.get('managed_enabled', latest.get('managed_enabled', True)):
                    selected.add('managed')
            if isinstance(index, int) and 1 <= index <= 6 and isinstance(name, str) and not name.startswith('当前窗口标签'):
                for old in [key for key, value in bindings.items() if value == name and key != index]:
                    del bindings[old]
                bindings[index] = name
            if not name:
                continue
            if kind in ('account_offline', 'combat_account_offline_skipped',
                        'multi_solo_account_disconnected', 'multi_solo_account_disconnected_skipped',
                        'combat_watchdog_timeout', 'multi_solo_account_stalled'):
                health[name] = event.get('detail') or ('已掉线：提示确认' if kind != 'combat_watchdog_timeout' else '掉线：30秒无变化')
            elif kind in ('account_verified', 'identity_bound', 'account_bound', 'combat_account_bound', 'multi_solo_start_return_verified'):
                health.pop(name, None)
            task = event.get('task') or event.get('mode')
            if task in TASK_LABELS and kind in ('multi_solo_task_started','daily_task_started'):
                active_tasks[name] = task
            if kind in ('multi_solo_managed_complete', 'multi_solo_managed_pending'):
                task = 'managed'
            if kind.startswith('multi_solo_survival_'):
                task = 'survival'
            if task not in TASK_LABELS:
                task = action_task(str(event.get('target') or event.get('action') or '')) or action_task(kind)
            if task not in TASK_LABELS and kind in ('return_leaf_verified','navigation_pending'):
                task = active_tasks.get(name)
            if task not in TASK_LABELS:
                continue
            rec = cells.setdefault((name, task), {})
            rec.setdefault('events', []).append(event)
            for field in ('completed','remaining','score','result','claims','home_returned',
                          'followup_check_due','task_completed','finished_at'):
                if field in event:
                    rec[field] = event[field]
            if kind in ('multi_solo_task_complete', 'daily_task_complete', 'multi_solo_managed_complete',
                        'multi_solo_task_skip_done', 'multi_solo_survival_second_run_complete',
                        'multi_solo_survival_no_reset_needed'):
                rec['status'] = 'done'
            elif kind in ('multi_solo_task_started', 'daily_task_started'):
                rec['status'] = 'running'
            elif 'pending' in kind or 'blocked' in kind or kind == 'combat_slot_pending':
                rec['status'] = 'pending'
                rec['last_error'] = event.get('detail', '')
            elif kind == 'multi_solo_task_skipped_for_later' and rec.get('status') != 'done':
                rec['status'] = 'skipped'
            if event.get('evidence'):
                rec.setdefault('evidence_paths', []).append(str(event['evidence']))
            if kind in ('return_leaf_verified','multi_solo_task_complete','daily_task_complete'):
                rec['home_returned'] = True
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
                    rec.update(survival_battle_counts(events, name, day, None if run_id else actions))
                if task == 'arena' and not run_id:
                    request = global_state.get('arena_requests', {}).get(name, {})
                    wins = sum(isinstance(v, dict) and v.get('status') == 'done'
                               for k, v in actions.items() if re.fullmatch(r'arena_attempt_victory_\d+', k))
                    rec['verified_wins'] = max(0, wins - request.get('base_wins', 0))
                    rec['requested_wins'] = request.get('requested_wins')
                if run_id and task == 'arena':
                    victories = {e.get('target') or e.get('index') for e in events
                                 if e.get('account') == name and e.get('event') == 'arena_victory_confirmed'}
                    rec['verified_wins'] = len(victories)
                task_rows[task] = rec
            rows.append(dict(label=f'标签{index}', index=index, account=name,
                             health=health.get(name, '已绑定' if name else '身份待核实'), tasks=task_rows))
        used = [e for row in rows for cell in row['tasks'].values() for e in cell.get('events', [])]
        updated = max([str(global_state.get('updated_at', ''))] + [str(e.get('ts', '')) for e in used])
        return dict(day=day, run_id=run_id, updated_at=updated, rows=rows,
                    runs=sorted(runs.items(), key=lambda row: row[1], reverse=True),
                    warnings=list(dict.fromkeys(self.warnings)))
