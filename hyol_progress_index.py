"""Disposable, disk-backed log index. Original logs and ledgers remain read-only.

Only appended complete JSON records are decoded. SQLite keeps deduplication and
counter history off the Python heap; the live projection retains 100 details per
task. A replaced/truncated log or an out-of-order append rebuilds the projection
by streaming the index, never by materializing the entire history.
"""
from __future__ import annotations

from collections import deque
import hashlib
import json
from pathlib import Path
import sqlite3
import tempfile

DETAIL_LIMIT = 100
MAX_LINE_BYTES = 8 * 1024 * 1024


class Projection:
    def __init__(self, tasks, action_task):
        self.tasks, self.action_task = tasks, action_task
        self.runs, self.bindings, self.health, self.cells = {}, {}, {}, {}
        self.ended_runs, self.last_events, self.active_tasks = set(), {}, {}
        self.count, self.centers, self.started, self.last_key = 0, [], None, ('', -1)

    def consume(self, event, order):
        self.last_key = (str(event.get('ts', '')), order)
        kind, name, identifier = event.get('event', ''), event.get('account'), event.get('run_id')
        self.last_events[identifier] = {'ts': event.get('ts'), 'event': kind}
        if kind in ('multi_solo_run_started', 'daily_task_started',
                    'generic_current_window_bound', 'combat_interleaved_start') and identifier:
            self.runs.setdefault(identifier, str(event.get('ts', '')))
        if kind in ('multi_solo_run_end', 'daily_summary', 'emergency_stop', 'scheduler_stopped'):
            self.ended_runs.add(identifier)
        if kind == 'multi_solo_tabs_counted':
            self.count = int(event.get('count') or self.count)
            self.centers = event.get('title_centers', [])
        if kind == 'multi_solo_run_started':
            self.started = {key: event[key] for key in ('tasks', 'managed_enabled') if key in event}
        index = event.get('tab_index')
        if isinstance(index, int) and 1 <= index <= 6 and isinstance(name, str) and not name.startswith('当前窗口标签'):
            for old in [key for key, value in self.bindings.items() if value == name and key != index]:
                del self.bindings[old]
            self.bindings[index] = name
        if not name:
            return
        if kind in ('account_offline', 'combat_account_offline_skipped',
                    'multi_solo_account_disconnected', 'multi_solo_account_disconnected_skipped',
                    'combat_watchdog_timeout', 'multi_solo_account_stalled'):
            self.health[name] = event.get('detail') or ('已掉线：提示确认' if kind != 'combat_watchdog_timeout' else '掉线：30秒无变化')
        elif kind in ('account_verified', 'identity_bound', 'account_bound', 'combat_account_bound', 'multi_solo_start_return_verified'):
            self.health.pop(name, None)
        task = event.get('task') or event.get('mode')
        if task in self.tasks and kind in ('multi_solo_task_started', 'daily_task_started'):
            self.active_tasks[name] = task
        if kind in ('multi_solo_managed_complete', 'multi_solo_managed_pending'):
            task = 'managed'
        if kind.startswith('multi_solo_survival_'):
            task = 'survival'
        if task not in self.tasks:
            task = self.action_task(str(event.get('target') or event.get('action') or '')) or self.action_task(kind)
        if task not in self.tasks and kind in ('return_leaf_verified', 'navigation_pending'):
            task = self.active_tasks.get(name)
        if task not in self.tasks:
            return
        rec = self.cells.setdefault((name, task), {'events': deque(maxlen=DETAIL_LIMIT),
                                                 'evidence_paths': deque(maxlen=DETAIL_LIMIT)})
        # Large archive action maps belong in the disk index, not UI details.
        detail = {key: value for key, value in event.items() if key != 'actions'}
        rec['events'].append(detail)
        for field in ('completed', 'remaining', 'score', 'result', 'claims', 'home_returned',
                      'followup_check_due', 'task_completed', 'finished_at'):
            if field in event:
                rec[field] = event[field]
        if kind in ('multi_solo_task_complete', 'daily_task_complete', 'multi_solo_managed_complete',
                    'multi_solo_task_skip_done', 'multi_solo_survival_second_run_complete',
                    'multi_solo_survival_no_reset_needed'):
            rec['status'], rec['task_completed'] = 'done', True
        elif kind in ('multi_solo_task_started', 'daily_task_started'):
            rec['status'] = 'running'
        elif 'pending' in kind or 'blocked' in kind or kind == 'combat_slot_pending':
            rec.update(status='pending', last_error=event.get('detail', ''))
        elif kind == 'multi_solo_task_skipped_for_later' and rec.get('status') != 'done':
            rec['status'] = 'skipped'
        if event.get('evidence'):
            rec['evidence_paths'].append(str(event['evidence']))
        if kind in ('return_leaf_verified', 'multi_solo_task_complete', 'daily_task_complete'):
            rec['home_returned'] = True

    def cell_records(self):
        return {key: {**value, 'events': list(value['events']),
                      'evidence_paths': list(value['evidence_paths'])} for key, value in self.cells.items()}


class ProgressLogIndex:
    def __init__(self, tasks, action_task):
        self.tasks, self.action_task = tasks, action_task
        self.temporary = tempfile.TemporaryDirectory(prefix='hyol-progress-')
        # WebView dispatches successive API calls on different threads. The
        # owning DesktopBridge lock serializes reads and close; SQLite must
        # permit that serialized connection ownership to follow those calls.
        self.connection = sqlite3.connect(str(Path(self.temporary.name) / 'index.sqlite'),
                                          check_same_thread=False)
        self.connection.executescript('''
            PRAGMA cache_size=-2048;
            PRAGMA temp_store=FILE;
            CREATE TABLE sources(path TEXT PRIMARY KEY, cursor TEXT);
            CREATE TABLE raw(source TEXT, ordinal INTEGER, key TEXT, ts TEXT, data TEXT,
                             PRIMARY KEY(source, ordinal));
            CREATE INDEX raw_keys ON raw(key);
            CREATE TABLE events(key TEXT PRIMARY KEY, source TEXT, ordinal INTEGER,
                                ts TEXT, kind TEXT, account TEXT, data TEXT);
            CREATE INDEX event_order ON events(ts);
            CREATE INDEX event_counters ON events(account, kind);
        ''')
        self.scope = None
        self.summary = Projection(tasks, action_task)
        self.bytes_read = self.records_decoded = self.rebuilds = 0
        self.closed = False

    def close(self):
        if not self.closed:
            self.closed = True
            self.connection.close()
            self.temporary.cleanup()
            self.summary = Projection(self.tasks, self.action_task)

    def __del__(self):
        try:
            self.close()
        except Exception:
            pass

    def sync(self, paths, scope, warnings):
        db, rebuild = self.connection, False
        if scope != self.scope:
            db.executescript('DELETE FROM raw; DELETE FROM events; DELETE FROM sources;')
            self.scope, self.summary = scope, Projection(self.tasks, self.action_task)
        names = {str(Path(path).resolve()) for path in paths}
        old_names = {row[0] for row in db.execute('SELECT path FROM sources')}
        with db:
            for name in old_names - names:
                db.execute('DELETE FROM raw WHERE source=?', (name,))
                db.execute('DELETE FROM sources WHERE path=?', (name,))
                rebuild = True
            for name in sorted(names):
                try:
                    rebuild = self._sync_file(name, warnings) or rebuild
                except (OSError, ValueError) as exc:
                    warnings.append('待刷新：' + Path(name).name + ' ' + str(exc))
            if rebuild:
                db.execute('DELETE FROM events')
                # Later sources win duplicate keys, as in the previous reader.
                for key, source, ordinal, ts, data in db.execute('SELECT key,source,ordinal,ts,data FROM raw ORDER BY source,ordinal'):
                    event = json.loads(data)
                    db.execute('INSERT OR REPLACE INTO events VALUES(?,?,?,?,?,?,?)',
                               (key, source, ordinal, ts, event.get('event', ''), event.get('account'), data))
                # Swap the projection only after the whole rebuild succeeds: a
                # failed transaction rolls the tables back, and the in-memory
                # summary must never show counts that the index did not commit.
                rebuilt = Projection(self.tasks, self.action_task)
                for order, data in db.execute('SELECT rowid,data FROM events ORDER BY ts,rowid'):
                    rebuilt.consume(json.loads(data), order)
                self.summary = rebuilt
                self.rebuilds += 1
        return self.summary

    def _sync_file(self, name, warnings):
        path, db = Path(name), self.connection
        stat = path.stat()
        row = db.execute('SELECT cursor FROM sources WHERE path=?', (name,)).fetchone()
        old = json.loads(row[0]) if row else None
        identity = [stat.st_dev, stat.st_ino, getattr(stat, 'st_birthtime_ns', 0)]
        rebuild, offset, ordinal = False, 0, 0
        with path.open('rb') as handle:
            if old:
                offset, ordinal = old['offset'], old['ordinal']
                handle.seek(0)
                head = hashlib.sha256(handle.read(old['head_length'])).hexdigest()
                handle.seek(max(0, offset-64))
                boundary = hashlib.sha256(handle.read(min(offset, 64))).hexdigest()
                changed = (old['identity'] != identity or stat.st_size < offset
                           or head != old['head'] or boundary != old['boundary']
                           or stat.st_size == old['size'] and stat.st_mtime_ns != old['mtime'])
                if changed:
                    db.execute('DELETE FROM raw WHERE source=?', (name,))
                    rebuild, offset, ordinal = True, 0, 0
            handle.seek(offset)
            while True:
                start = handle.tell()
                line = handle.readline(MAX_LINE_BYTES + 1)
                if not line:
                    break
                self.bytes_read += len(line)
                if len(line) > MAX_LINE_BYTES:
                    raise ValueError('日志单行过大，保留原记录等待核验')
                if not line.strip():
                    offset = handle.tell()
                    continue
                try:
                    event = json.loads(line.decode('utf-8-sig'))
                except (UnicodeDecodeError, ValueError):
                    if not line.endswith(b'\n'):
                        warnings.append('日志末行写入中：' + path.name)
                        handle.seek(start)
                        break
                    warnings.append('日志行无法解析：' + path.name)
                    offset = handle.tell()
                    continue
                offset, ordinal = handle.tell(), ordinal + 1
                self.records_decoded += 1
                if not isinstance(event, dict):
                    continue
                if self.scope[1] and event.get('run_id') != self.scope[1]:
                    continue
                key = json.dumps([event.get(field) for field in
                                  ('run_id', 'ts', 'event', 'account', 'target', 'task')], ensure_ascii=False)
                ts, data = str(event.get('ts', '')), json.dumps(event, ensure_ascii=False)
                db.execute('INSERT OR REPLACE INTO raw VALUES(?,?,?,?,?)', (name, ordinal, key, ts, data))
                previous = db.execute('SELECT rowid,source,data FROM events WHERE key=?', (key,)).fetchone()
                if previous and previous[1] > name:
                    continue
                if previous and previous[2] == data:
                    db.execute('UPDATE events SET source=?,ordinal=? WHERE key=?', (name, ordinal, key))
                    continue
                db.execute('''INSERT INTO events VALUES(?,?,?,?,?,?,?) ON CONFLICT(key) DO UPDATE SET
                              source=excluded.source,ordinal=excluded.ordinal,ts=excluded.ts,
                              kind=excluded.kind,account=excluded.account,data=excluded.data''',
                           (key, name, ordinal, ts, event.get('event', ''), event.get('account'), data))
                order = db.execute('SELECT rowid FROM events WHERE key=?', (key,)).fetchone()[0]
                if previous or (ts, order) < self.summary.last_key:
                    rebuild = True
                elif not rebuild:
                    self.summary.consume(event, order)
            head_length = min(offset, 256)
            handle.seek(0)
            head = hashlib.sha256(handle.read(head_length)).hexdigest()
            handle.seek(max(0, offset-64))
            boundary = hashlib.sha256(handle.read(min(offset, 64))).hexdigest()
        cursor = dict(identity=identity, offset=offset, ordinal=ordinal, head_length=head_length,
                      head=head, boundary=boundary, size=stat.st_size, mtime=stat.st_mtime_ns)
        db.execute('INSERT OR REPLACE INTO sources VALUES(?,?)', (name, json.dumps(cursor)))
        return rebuild

    def survival_events(self, account):
        for data, in self.connection.execute('''SELECT data FROM events WHERE account=?
            AND kind IN ('irreversible_committed','survival_first_run_archived') ORDER BY ts,rowid''', (account,)):
            yield json.loads(data)

    def arena_wins(self, account):
        return self.connection.execute('''SELECT COUNT(DISTINCT json_quote(COALESCE(
            json_extract(data,'$.target'),json_extract(data,'$.index'))))
            FROM events WHERE account=? AND kind='arena_victory_confirmed' ''', (account,)).fetchone()[0]
