"""Run-scoped diagnostics. No game input, uploads, or broad filesystem collection."""
from __future__ import annotations

import io
import hashlib
import json
import os
import platform
import sys
import tempfile
import threading
import traceback
import uuid
import zipfile
from collections import deque
from datetime import datetime
from pathlib import Path


def run_id():
    return datetime.now().strftime('%Y%m%d_%H%M%S_%f') + '_' + uuid.uuid4().hex[:8]


def write_json(path, value):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name + '.' + uuid.uuid4().hex + '.tmp')
    try:
        temporary.write_text(json.dumps(value, ensure_ascii=False, indent=2, default=str), encoding='utf-8')
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


class Diagnostics:
    def __init__(self, root, identifier=None):
        self.root = Path(root)
        self.identifier = identifier or run_id()
        self.events = deque(maxlen=600)
        self.lock = threading.RLock()
        self.last_path = None
        self.last_exception = ''

    def observe(self, event, fields):
        with self.lock:
            self.events.append(json.loads(json.dumps({'event': event, **fields}, ensure_ascii=False, default=str)))
            if sys.exc_info()[0] is not None:
                self.last_exception = traceback.format_exc()

    def export(self, *, summary=None, frame=None, state=None, log_path=None):
        """Copy only this run's explicit inputs; never recurse through user files."""
        with self.lock:
            events = list(self.events)
        report = {
            'run_id': self.identifier, 'created_at': datetime.now().isoformat(),
            'summary': summary or {}, 'python': sys.version,
            'platform': platform.platform(), 'frozen': bool(getattr(sys, 'frozen', False)),
            'traceback': self.last_exception or (traceback.format_exc() if sys.exc_info()[0] is not None else ''),
            'privacy': '截图可能包含角色信息；只收集传入的游戏画面，不自动上传。',
            'collection_errors': [],
        }
        payloads = {'events.jsonl': '\n'.join(json.dumps(e, ensure_ascii=False, default=str) for e in events)}
        payloads['state_snapshot.json'] = json.dumps(state or {}, ensure_ascii=False, indent=2, default=str)
        if log_path:
            try:
                # Keep a bounded tail from the selected run, never other runs.
                with Path(log_path).open('rb') as handle:
                    handle.seek(0, 2)
                    handle.seek(max(0, handle.tell() - 4 * 1024 * 1024))
                    payloads['run_log_tail.jsonl'] = handle.read()
            except OSError as exc:
                report['collection_errors'].append('log: ' + str(exc))
        if frame is not None:
            try:
                buffer = io.BytesIO()
                frame.save(buffer, format='PNG')
                payloads['last_game_frame.png'] = buffer.getvalue()
                report['frame_size'] = list(frame.size)
            except Exception as exc:
                report['collection_errors'].append('screenshot: ' + str(exc))
        else:
            report['collection_errors'].append('没有已验证的游戏截图，未抓取桌面替代')
        payloads['README.txt'] = '火影OL诊断包\n请将此ZIP回传给脚本维护者。\n截图可能包含角色信息，请先检查。\n' + json.dumps(report['summary'], ensure_ascii=False, indent=2, default=str)
        failures = []
        for folder in (self.root / 'logs' / 'reports', Path(tempfile.gettempdir()) / 'HYOL_diagnostics'):
            path = folder / ('HYOL_' + self.identifier + '_' + uuid.uuid4().hex[:6] + '.zip')
            try:
                folder.mkdir(parents=True, exist_ok=True)
                report['collection_errors'].extend(failures)
                with zipfile.ZipFile(path, 'x', zipfile.ZIP_DEFLATED) as archive:
                    archive.writestr('report.json', json.dumps(report, ensure_ascii=False, indent=2, default=str))
                    for name, content in payloads.items():
                        archive.writestr(name, content)
                self.last_path = path
                return path
            except (OSError, zipfile.BadZipFile) as exc:
                failures.append(str(exc))
        # ZIP failure must not hide the original failure or break lease cleanup.
        fallback = Path(tempfile.gettempdir()) / ('HYOL_' + self.identifier + '.txt')
        try:
            fallback.write_text(json.dumps({**report, 'export_errors': failures}, ensure_ascii=False, default=str, indent=2), encoding='utf-8')
            self.last_path = fallback
            return fallback
        except OSError:
            return None


_pause_lock = threading.RLock()


class _PauseRecordError(ValueError):
    def __init__(self, path, cause):
        self.path = str(path)
        super().__init__(f'{path}: {cause}')


def _pause_state_directory(root):
    """Use the same explicit shared directory across all configs in one entry.

    No Scheduler import: this also runs before a scheduler can be constructed.
    Config disagreements cannot choose an arbitrary project to resume.
    """
    root = Path(root).resolve()
    choices = {}
    configs = sorted(path for path in root.glob('*.ini')
                     if path.name.lower().endswith('_client.ini')
                     or path.name.lower() == 'hyol_daily.ini') if root.is_dir() else ()
    for path in configs:
        for number, raw in enumerate(path.read_text(encoding='utf-8-sig').splitlines(), 1):
            line = raw.strip()
            if not line or line.startswith(('#', ';')) or '=' not in line:
                continue
            key, value = line.split('=', 1)
            if key.strip() != 'SharedStateDirectory':
                continue
            value = value.strip()
            if not value:
                raise ValueError(f'SharedStateDirectory为空：{path}:{number}')
            directory = Path(value)
            if not directory.is_absolute():
                directory = root / directory
            choices.setdefault(directory.resolve(), []).append(f'{path.name}:{number}')
    if len(choices) > 1:
        raise ValueError('SharedStateDirectory冲突：' + '; '.join(
            f'{directory} ({", ".join(sources)})' for directory, sources in choices.items()))
    return next(iter(choices), root/'state')


def _pause_fallback_path(state_directory):
    identity = os.path.normcase(str(state_directory.resolve()))
    digest = hashlib.sha256(identity.encode('utf-8')).hexdigest()[:20]
    return Path(tempfile.gettempdir()) / f'HYOL_manual_pause_{digest}.json'


def _pause_belongs(value, root, state_directory, path):
    if not isinstance(value, dict):
        raise ValueError('invalid pause record')
    local = root/'state/manual_pause.json'
    project = value.get('project')
    directory = value.get('state_directory')
    if project is not None and (not isinstance(project, str) or not project):
        raise ValueError('invalid pause project')
    if directory is not None and (not isinstance(directory, str) or not directory):
        raise ValueError('invalid pause state_directory')
    # A local legacy pause remains binding if its entry acquired a shared config.
    if path == local and (not project or Path(project).resolve() == root):
        return True
    if directory:
        belongs = Path(directory).resolve() == state_directory
    elif project:
        owner = Path(project).resolve()
        belongs = owner in (root, state_directory.parent)
        if not belongs:
            belongs = _pause_state_directory(owner) == state_directory
    else:
        # Old global emergency records cannot prove which project was stopped.
        # Honor their pause, but an explicit project resume cannot clear them.
        belongs = True
    if not belongs and path in (state_directory/'manual_pause.json', _pause_fallback_path(state_directory)):
        raise ValueError('暂停记录与所在项目目录不一致')
    return belongs


def _pause_records(root, state_directory):
    paths = list(dict.fromkeys((state_directory/'manual_pause.json', root/'state/manual_pause.json',
                              _pause_fallback_path(state_directory),
                              Path(tempfile.gettempdir())/'HYOL_manual_pause.json')))
    records = []
    for path in paths:
        try:
            raw = path.read_bytes()
        except FileNotFoundError:
            continue
        except OSError as exc:
            raise _PauseRecordError(path, exc) from exc
        try:
            value = json.loads(raw.decode('utf-8-sig'))
            if not _pause_belongs(value, root, state_directory, path):
                continue
        except (OSError, ValueError, TypeError, RuntimeError) as exc:
            raise _PauseRecordError(path, exc) from exc
        if not isinstance(value.get('paused'), bool):
            raise _PauseRecordError(path, 'invalid pause record')
        history = value.get('pause_history', [])
        if not isinstance(history, list) or not all(isinstance(item, dict) for item in history):
            raise _PauseRecordError(path, 'invalid pause history')
        records.append((path, value, raw))
        entries = value.get('entry_roots', [])
        if not isinstance(entries, list) or not all(isinstance(entry, str) and entry for entry in entries):
            raise _PauseRecordError(path, 'invalid pause entry_roots')
        # Only recorded entries still configured for this shared directory are
        # considered; never search other desktop clients or unrelated projects.
        for entry in entries:
            entry_root = Path(entry).resolve()
            if _pause_state_directory(entry_root) == state_directory:
                legacy = entry_root/'state/manual_pause.json'
                if legacy not in paths:
                    paths.append(legacy)
    return records


def _write_pause_with_history(path, value, root, state_directory):
    try:
        previous = json.loads(path.read_text(encoding='utf-8-sig'))
    except FileNotFoundError:
        previous = None
    result = dict(value)
    if previous is not None:
        if not _pause_belongs(previous, root, state_directory, path) or not isinstance(previous.get('paused'), bool):
            raise ValueError(f'既有暂停记录无法核验，保留原文件：{path}')
        history = previous.get('pause_history', [])
        entries = previous.get('entry_roots', [])
        if (not isinstance(history, list) or not all(isinstance(item, dict) for item in history)
                or not isinstance(entries, list)):
            raise ValueError(f'既有暂停历史无法核验，保留原文件：{path}')
        result['entry_roots'] = list(dict.fromkeys([*entries, *value['entry_roots']]))
        if previous.get('paused') and previous.get('run_id') == value['run_id'] and previous.get('reason') == value['reason']:
            result = {**previous, 'entry_roots': result['entry_roots']}
        else:
            result['pause_history'] = [*history, {key: item for key, item in previous.items()
                                                if key != 'pause_history'}]
    write_json(path, result)


def mark_manual_pause(root, identifier, reason):
    """Persist a common pause and the caller's legacy mirror; never clear state."""
    root = Path(root).resolve()
    with _pause_lock:
        scope_error = ''
        try:
            state_directory = _pause_state_directory(root)
        except (OSError, ValueError, RuntimeError) as exc:
            state_directory, scope_error = root/'state', str(exc)
        value = {'paused': True, 'run_id': identifier, 'reason': reason,
                 'project': str(state_directory.parent), 'state_directory': str(state_directory),
                 'entry_roots': [str(root)], 'at': datetime.now().isoformat(),
                 'resume_requires': 'explicit user action'}
        if scope_error:
            value['scope_error'] = scope_error
        common_saved = False
        for path in dict.fromkeys((state_directory/'manual_pause.json', root/'state/manual_pause.json')):
            try:
                _write_pause_with_history(path, value, root, state_directory)
                if path == state_directory/'manual_pause.json':
                    common_saved = True
            except (OSError, ValueError, TypeError):
                continue  # Preserve corrupt originals; use the scoped fallback.
        if not common_saved:
            _write_pause_with_history(_pause_fallback_path(state_directory), value, root, state_directory)


def manual_pause_status(root):
    """Honor shared, legacy and emergency pauses; ambiguity remains paused."""
    root = Path(root).resolve()
    with _pause_lock:
        try:
            records = _pause_records(root, _pause_state_directory(root))
            paused = [(path, value) for path, value, _ in records if value['paused']]
            if paused:
                path, value = paused[0]
                return {**value, 'path': str(path), 'paths': [str(path) for path, _ in paused]}
        except (OSError, ValueError, TypeError, RuntimeError) as exc:
            return {'paused': True, 'path': getattr(exc, 'path', str(root)),
                    'reason': f'暂停记录无法核验：{exc}'}
    return {'paused': False}


def clear_manual_pause(root, reason='user explicitly resumed'):
    """Called only for an explicit user resume; validate every record first.

    Preserve original reasons/times/history and refuse to overwrite corrupt or
    unowned records. Other callers must not infer authorization from a new day.
    """
    root = Path(root).resolve()
    with _pause_lock:
        if not isinstance(reason, str) or not reason.strip():
            raise RuntimeError('明确恢复必须记录非空的用户恢复原因')
        try:
            records = _pause_records(root, _pause_state_directory(root))
            for path, value, _ in records:
                if (value['paused'] and path.name == 'HYOL_manual_pause.json'
                        and not value.get('project') and not value.get('state_directory')):
                    raise ValueError(f'旧临时暂停缺少项目归属，拒绝代其他项目恢复：{path}')
        except (OSError, ValueError, TypeError, RuntimeError) as exc:
            raise RuntimeError(f'暂停记录无法核验，拒绝恢复：{exc}') from exc
        cleared = []
        for path, value, original in records:
            if value['paused']:
                if path.read_bytes() != original:
                    raise RuntimeError(f'暂停记录已变化，拒绝覆盖新暂停：{path}')
                value.update(paused=False, resumed_at=datetime.now().isoformat(), resume_reason=str(reason))
                write_json(path, value)
                cleared.append(str(path))
        return cleared


def export_from_scheduler(scheduler, summary=None):
    state = getattr(scheduler, 'state', None)
    snapshot = {}
    # StateStore writes atomically; read those files instead of iterating live
    # dictionaries while a GUI export thread and game runner mutate them.
    paths = {'global': getattr(state, 'path', None)}
    paths.update({str(key): value for key, value in getattr(state, 'account_paths', {}).copy().items()})
    for name, path in paths.items():
        if path is None:
            continue
        try:
            snapshot[name] = json.loads(Path(path).read_text(encoding='utf-8'))
        except (OSError, ValueError) as exc:
            snapshot[name] = {'snapshot_error': str(exc)}
    return scheduler.diagnostics.export(
        summary=summary, frame=getattr(scheduler, '_last_frame', None),
        state=snapshot,
        log_path=getattr(getattr(scheduler, 'log', None), 'path', None))
