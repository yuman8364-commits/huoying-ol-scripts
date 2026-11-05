"""Quota-gated continuation of the existing Codex chat; check-only never writes."""
import argparse
from contextlib import closing, contextmanager
from datetime import datetime
import json
import math
import os
from pathlib import Path
import re
import sqlite3
import subprocess
import sys
import tempfile
import time
import uuid

from codex_quota import read_quota, read_thread_status, resolve_codex

ROOT = Path(__file__).resolve().parents[1]
CONFIG = ROOT / 'state' / 'codex_continuation.json'


def load_config():
    value = json.loads(CONFIG.read_text(encoding='utf-8-sig'))
    if not isinstance(value, dict) or not isinstance(value.get('enabled'), bool):
        raise RuntimeError('Continuation config was invalid')
    uuid.UUID(value['thread_id'])
    if (not isinstance(value.get('not_before_at'), (int, float))
            or isinstance(value['not_before_at'], bool)
            or not math.isfinite(value['not_before_at'])):
        raise RuntimeError('Continuation reset time was invalid')
    return value


def write(path, value):
    descriptor, temporary = tempfile.mkstemp(prefix=path.name + '.', suffix='.tmp', dir=path.parent)
    try:
        with os.fdopen(descriptor, 'w', encoding='utf-8') as output:
            json.dump(value, output, ensure_ascii=False, indent=2)
            output.flush()
            os.fsync(output.fileno())
        os.replace(temporary, path)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


def merge_dispatch(updates, expected_thread):
    config = load_config()
    if config['thread_id'] != expected_thread:
        raise RuntimeError('Continuation target changed during dispatch')
    for key, value in updates.items():
        if value is None:
            config.pop(key, None)
        else:
            config[key] = value
    write(CONFIG, config)
    return config


@contextmanager
def dispatch_lock():
    # Kernel lock releases on process death; a stale lock file is harmless.
    path = CONFIG.with_suffix('.lock')
    with path.open('a+b') as handle:
        handle.seek(0, os.SEEK_END)
        if handle.tell() == 0:
            handle.write(b'0')
            handle.flush()
        handle.seek(0)
        locked = False
        try:
            if os.name == 'nt':
                import msvcrt
                msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                import fcntl
                fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
            locked = True
        except (OSError, BlockingIOError):
            pass
        try:
            yield locked
        finally:
            if locked:
                handle.seek(0)
                if os.name == 'nt':
                    msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
                else:
                    fcntl.flock(handle.fileno(), fcntl.LOCK_UN)


def rollout_status(path, thread_id):
    # Old mtime cannot establish that an unfinished desktop turn is inactive.
    active = set()
    identified = False
    terminal = False
    try:
        with Path(path).open(encoding='utf-8') as source:
            for line in source:
                if not line.endswith('\n'):
                    return 'writing'
                value = json.loads(line)
                payload = value.get('payload') or {}
                if value.get('type') == 'session_meta':
                    if payload.get('id') != thread_id:
                        return 'wrong_thread'
                    if payload.get('cwd') and Path(payload['cwd']).resolve() != ROOT:
                        return 'wrong_workspace'
                    identified = True
                if value.get('type') != 'event_msg':
                    continue
                kind = payload.get('type')
                if kind not in ('task_started', 'task_complete', 'turn_aborted'):
                    continue
                turn_id = payload.get('turn_id')
                if not turn_id:
                    return 'unknown_turn'
                if kind == 'task_started':
                    active.add(turn_id)
                    terminal = False
                else:
                    active.discard(turn_id)
                    terminal = True
        if not identified:
            return 'unverified_thread'
        if active:
            return 'active'
        return 'idle' if terminal else 'unknown_turn'
    except (OSError, ValueError, TypeError, AttributeError):
        return 'unreadable'


def manual_pause():
    if str(ROOT) not in sys.path:
        sys.path.insert(0, str(ROOT))
    from hyol_diagnostics import manual_pause_status
    return manual_pause_status(ROOT)


def writer_status(thread_id):
    """Probe the existing thread-store lock without changing its contents.

    A desktop thread can retain its writer even when no turn is running. It is
    then unsafe and ineffective to start a second app-server with exec resume.
    """
    home = Path(os.environ.get('CODEX_HOME') or Path.home() / '.codex')
    path = home / 'thread-writer-locks' / (thread_id + '.lock')
    try:
        with path.open('rb') as handle:
            if os.name == 'nt':
                import msvcrt
                try:
                    msvcrt.locking(handle.fileno(), msvcrt.LK_NBRLCK, 1)
                except OSError:
                    return 'held'
                msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                import fcntl
                try:
                    fcntl.flock(handle.fileno(), fcntl.LOCK_SH | fcntl.LOCK_NB)
                except (OSError, BlockingIOError):
                    return 'held'
                fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
        return 'available'
    except FileNotFoundError:
        return 'available'
    except OSError:
        return 'unknown'


def failed_before_turn(log_path):
    """Recognize a proven writer conflict, never infer it from exit code alone."""
    try:
        with Path(log_path).open(encoding='utf-8') as source:
            text = source.read(65537)
        if len(text) > 65536:
            return False
        for line in text.splitlines():
            try:
                event = json.loads(line)
            except ValueError:
                continue
            if isinstance(event, dict) and event.get('type') in (
                    'thread.started', 'turn.started', 'item.started',
                    'turn.completed', 'turn.failed'):
                return False
        return ('already has an active writer' in text
                and 'Failed to create session' in text
                and 'thread/resume' in text)
    except (OSError, TypeError):
        return False


def queued_dispatch_status(config):
    """Queue acceptance is not a consumed turn; require both durable signals."""
    home = Path(os.environ.get('CODEX_HOME') or Path.home() / '.codex')
    path = home / 'queue_1.sqlite'
    try:
        with closing(sqlite3.connect(path.resolve().as_uri() + '?mode=ro', uri=True)) as database:
            pending = database.execute(
                'SELECT 1 FROM queued_items WHERE thread_id=? AND id=?',
                (config['thread_id'], config['queued_submission_id'])).fetchone()
        if pending:
            return 'waiting_for_queued_turn'
        started = False
        marker_seen = False
        with Path(config['session_path']).open(encoding='utf-8') as source:
            source.seek(config['queued_rollout_offset'])
            for line in source:
                if not line.endswith('\n'):
                    return 'queued_turn_unconfirmed'
                value = json.loads(line)
                payload = value.get('payload') or {}
                if value.get('type') == 'event_msg' and payload.get('type') == 'task_started':
                    started = True
                if (value.get('type') == 'response_item' and payload.get('role') == 'user'
                        and config['queued_prompt_marker'] in json.dumps(payload, ensure_ascii=False)):
                    marker_seen = True
        return 'queued_turn_consumed' if started and marker_seen else 'queued_turn_unconfirmed'
    except (OSError, ValueError, TypeError, KeyError, sqlite3.Error):
        return 'queued_turn_unconfirmed'


def conditions(config, snapshot, now=None):
    now = time.time() if now is None else now
    bucket = snapshot.get('buckets', {}).get('codex', {})
    windows = [bucket.get(key) for key in ('primary', 'secondary')]
    five_hour = [window for window in windows if isinstance(window, dict)
                 and window.get('windowDurationMins') == 300]
    if len(five_hour) != 1:
        return {'status': 'unknown_quota_window'}
    window = five_hour[0]
    result = {'status': 'checking',
              'ordinary_usage_allowed': snapshot.get('ordinary_usage_allowed'),
              'used_percent': window.get('usedPercent'),
              'next_reset_at': window.get('resetsAt'),
              'not_before_local': datetime.fromtimestamp(config['not_before_at']).isoformat()}
    if now < config['not_before_at']:
        return {**result, 'status': 'waiting_for_initial_reset'}
    if snapshot.get('ordinary_usage_allowed') is not True:
        return {**result, 'status': 'waiting_for_quota_permission'}
    for current in windows:
        if current is None:
            continue
        if not isinstance(current, dict):
            return {**result, 'status': 'unknown_quota_window'}
        percent, reset = current.get('usedPercent'), current.get('resetsAt')
        if (not isinstance(percent, (int, float)) or isinstance(percent, bool)
                or not math.isfinite(percent) or not 0 <= percent < 100
                or not isinstance(reset, (int, float)) or isinstance(reset, bool)
                or not math.isfinite(reset) or reset <= now):
            return {**result, 'status': 'waiting_for_quota_window'}
    if config.get('dispatch_status') in ('starting', 'dispatch_unknown'):
        return {**result, 'status': 'previous_dispatch_needs_review'}
    if config.get('dispatch_status') == 'queued':
        return {**result, 'status': queued_dispatch_status(config)}
    if now < config.get('retry_after_at', 0):
        return {**result, 'status': 'waiting_for_retry'}
    state = rollout_status(config['session_path'], config['thread_id'])
    if state != 'idle':
        return {**result, 'status': 'existing_chat_' + state}
    if Path(config['session_path']).stat().st_mtime > now - 180:
        return {**result, 'status': 'existing_chat_recently_active'}
    known_pre_turn_failure = (config.get('dispatch_status') == 'failed'
                              and failed_before_turn(config.get('dispatch_log')))
    # The service may report a moving reset estimate. Equality of resetsAt
    # alone cannot prevent a successful continuation every thirty minutes.
    last_dispatch = config.get('last_dispatch_epoch')
    if (not known_pre_turn_failure and isinstance(last_dispatch, (int, float))
            and config.get('dispatch_status') in ('finished', 'failed', 'consumed')
            and now - last_dispatch < 300 * 60):
        return {**result, 'status': 'already_dispatched_recently'}
    if (config.get('last_window_reset_at') == window.get('resetsAt')
            and not known_pre_turn_failure):
        return {**result, 'status': 'already_dispatched_this_window'}
    try:
        shared = read_thread_status(config['thread_id'], timeout=5,
                                    exe_path=config.get('codex_exe'))
    except (RuntimeError, OSError):
        shared = {'type': 'unavailable'}
    result['shared_chat_status'] = shared.get('type')
    # notLoaded is not evidence of idle. Verified rollout markers establish
    # the desktop turn state if no shared control transport can report it.
    if shared.get('type') not in ('idle', 'notLoaded', 'unavailable'):
        return {**result, 'status': 'existing_chat_' + str(shared.get('type', 'unknown'))}
    pause = manual_pause()
    return {**result, 'status': 'ready', 'offline_only': pause.get('paused') is not False,
            'dispatch_kind': 'queue'}


def continuation_prompt(offline_only):
    prompt = ('额度恢复后的定时续作已触发。继续本聊天中用户已授权的全项目任务。'
              '先完整读取 D:\\火影ol脚本\\CONTINUE_AFTER_QUOTA.md、PROGRESS.md、ACCEPTANCE.md 和当前断点，'
              '按最新用户要求优化速度、体积与逻辑，完成统一桌面客户端及每功能实机验收。'
              '温泉退出流程需融入共用导航：X、按真实提示退出、右上角返回木叶村。'
              '不自动解除F12暂停，不重复未知开战，不购买。'
              '任何游戏操作之前重新读取人工暂停记录；F12仍生效时只能离线工作。'
              '只有实质进展、完成、失败或需要用户操作才通知，无变化时保持安静。')
    if offline_only:
        prompt += ('当前人工暂停仍生效，本轮仅允许离线修改代码、测试和发布，'
                   '禁止游戏截图和点击，禁止调用 clear_manual_pause，等待用户明确恢复实机。')
    return prompt


def run(check_only=False):
    if check_only:
        config = load_config()
        if not config['enabled']:
            return {'status': 'disabled'}
        return conditions(config, read_quota(exe_path=config.get('codex_exe')))
    with dispatch_lock() as locked:
        if not locked:
            return {'status': 'continuation_already_running'}
        config = load_config()
        if not config['enabled']:
            return {'status': 'disabled'}
        result = conditions(config, read_quota(exe_path=config.get('codex_exe')))
        if result['status'] == 'queued_turn_consumed':
            merge_dispatch({'dispatch_status': 'consumed',
                            'consumed_at': datetime.now().isoformat()}, config['thread_id'])
            return result
        if result['status'] != 'ready':
            return result
        exe = resolve_codex(config.get('codex_exe'))
        current = load_config()
        if not current['enabled'] or current['thread_id'] != config['thread_id']:
            return {'status': 'dispatch_cancelled'}
        if rollout_status(config['session_path'], config['thread_id']) != 'idle':
            return {'status': 'existing_chat_active'}
        run_id = datetime.now().strftime('%Y%m%d_%H%M%S_%f')
        folder = ROOT / 'logs' / 'codex_continuation'
        folder.mkdir(parents=True, exist_ok=True)
        log_path = folder / (run_id + '.jsonl')
        return queue_continuation(exe, config, result, log_path)


def queue_continuation(exe, config, result, log_path):
    """Only the existing desktop writer consumes this message; no new chat."""
    marker = 'HYOL_QUOTA_CONTINUATION_' + str(uuid.uuid4())
    prompt = marker + '\n' + continuation_prompt(result['offline_only'])
    offset = Path(config['session_path']).stat().st_size
    merge_dispatch({'dispatch_status': 'starting', 'dispatch_log': str(log_path),
                    'last_dispatch_at': datetime.now().isoformat(),
                    'last_dispatch_epoch': time.time(),
                    'last_window_reset_at': result['next_reset_at'],
                    'retry_after_at': None}, config['thread_id'])
    try:
        process = subprocess.run(
            [exe, 'queue', '--thread', config['thread_id'], '--message', prompt],
            cwd=ROOT, capture_output=True, text=True, encoding='utf-8', timeout=30,
            creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
    except OSError:
        merge_dispatch({'dispatch_status': 'failed_launch', 'last_window_reset_at': None,
                        'last_dispatch_epoch': None, 'retry_after_at': time.time() + 1800},
                       config['thread_id'])
        return {**result, 'status': 'failed_launch'}
    except subprocess.TimeoutExpired:
        merge_dispatch({'dispatch_status': 'dispatch_unknown'}, config['thread_id'])
        return {**result, 'status': 'previous_dispatch_needs_review'}
    match = re.search(r'Queued message ([0-9a-f-]{36}) for thread ([0-9a-f-]{36})\.',
                      process.stdout)
    if process.returncode or not match or match.group(2) != config['thread_id']:
        # The queue command may have persisted before an output failure. Do not
        # enqueue twice or log stderr that could contain unrelated account data.
        merge_dispatch({'dispatch_status': 'dispatch_unknown',
                        'last_exit_code': process.returncode}, config['thread_id'])
        return {**result, 'status': 'previous_dispatch_needs_review'}
    record = {'dispatch_status': 'queued', 'queued_submission_id': match.group(1),
              'queued_prompt_marker': marker, 'queued_rollout_offset': offset,
              'last_exit_code': 0}
    merge_dispatch(record, config['thread_id'])
    log_path.write_text(json.dumps({'status': 'queued_unconfirmed',
                                   'thread_id': config['thread_id'],
                                   'queued_submission_id': match.group(1)},
                                  ensure_ascii=False) + '\n', encoding='utf-8')
    return {**result, 'status': 'queued_unconfirmed', 'queued_submission_id': match.group(1)}


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--check-only', action='store_true',
                        help='Read quota/conditions without state writes or model turns')
    args = parser.parse_args()
    try:
        print(json.dumps(run(args.check_only), ensure_ascii=False))
    except Exception as exc:
        print(json.dumps({'status': 'unavailable', 'reason': str(exc)}, ensure_ascii=False))
        raise SystemExit(2)
