"""Read Codex's own quota snapshot without reading or displaying credentials."""
import argparse
import json
import os
from pathlib import Path
import queue
import shutil
import subprocess
import threading
import time


def resolve_codex(exe_path=None):
    """Prefer the installed command, surviving removal of an older app build."""
    command = shutil.which('codex')
    for candidate in (command, exe_path):
        if candidate and Path(candidate).is_file():
            return str(candidate)
    # The desktop app installs the CLI under a versioned directory. An old
    # scheduled-task config must not pin a build which an app update removed.
    local = Path(os.environ.get('LOCALAPPDATA', ''))
    candidates = list((local / 'OpenAI' / 'Codex' / 'bin').glob('*/codex.exe'))
    if candidates:
        return str(max(candidates, key=lambda path: path.stat().st_mtime))
    raise RuntimeError('Codex command was not found')


def _read_rpc(method, params, timeout=25, exe_path=None, shared=False):
    if method not in ('account/rateLimits/read', 'thread/read'):
        raise ValueError('Only read-only Codex requests are allowed')
    exe = resolve_codex(exe_path)
    if not exe:
        raise RuntimeError('Codex command was not found')
    process = subprocess.Popen(
        [exe, 'app-server', 'proxy' if shared else '--stdio'], stdin=subprocess.PIPE,
        stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, text=True,
        encoding='utf-8', creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
    messages = queue.Queue()

    def receive():
        for line in process.stdout:
            try:
                value = json.loads(line)
                if isinstance(value, dict) and value.get('id') in (0, 1):
                    messages.put(value)
            except (ValueError, TypeError):
                continue
        messages.put(None)

    threading.Thread(target=receive, daemon=True).start()

    def send(value):
        process.stdin.write(json.dumps(value, ensure_ascii=False) + '\n')
        process.stdin.flush()

    until = time.monotonic() + timeout

    def response(request_id):
        while time.monotonic() < until:
            try:
                value = messages.get(timeout=max(.1, until-time.monotonic()))
            except queue.Empty:
                break
            if value is None:
                raise RuntimeError('Codex quota connection closed')
            if value.get('id') == request_id:
                if 'error' in value:
                    raise RuntimeError('Codex quota RPC rejected the read')
                if not isinstance(value.get('result'), dict):
                    raise RuntimeError('Codex quota response was invalid')
                return value['result']
        raise RuntimeError('Codex quota read timed out')

    try:
        send({'method': 'initialize', 'id': 0, 'params': {
            'clientInfo': {'name': 'hyol_quota_continuation', 'version': '1.0'},
            'capabilities': {'experimentalApi': True}}})
        response(0)
        send({'method': 'initialized'})
        send({'method': method, 'id': 1, 'params': params})
        return response(1)
    finally:
        try:
            process.stdin.close()
        except (BrokenPipeError, OSError):
            pass
        if process.poll() is None:
            process.terminate()
            try:
                process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait(timeout=5)
        process.stdout.close()


def _window(value):
    if not isinstance(value, dict):
        return None
    # Do not pass through additional fields from future server responses.
    return {field: value.get(field) for field in (
        'usedPercent', 'windowDurationMins', 'resetsAt')}


def read_quota(timeout=25, exe_path=None):
    result = _read_rpc('account/rateLimits/read',
                       {'excludeResetCreditDetails': True}, timeout, exe_path)
    buckets = result.get('rateLimitsByLimitId') or {'codex': result.get('rateLimits')}
    return {
        'ordinary_usage_allowed': result.get('ordinaryUsageAllowed'),
        'buckets': {key: {
            'primary': _window(value.get('primary')),
            'secondary': _window(value.get('secondary')),
        } for key, value in buckets.items() if isinstance(value, dict)},
        'observed_at': int(time.time()),
    }


def read_thread_status(thread_id, timeout=10, exe_path=None):
    """A standalone server's notLoaded status says nothing about desktop work."""
    result = _read_rpc('thread/read', {'threadId': thread_id, 'includeTurns': False},
                       timeout, exe_path, shared=True)
    thread = result.get('thread')
    if not isinstance(thread, dict) or thread.get('id') != thread_id:
        raise RuntimeError('Codex thread response was invalid')
    status = thread.get('status')
    if not isinstance(status, dict):
        raise RuntimeError('Codex thread status was unavailable')
    return {'type': status.get('type'),
            'active_flags': [value for value in status.get('activeFlags', [])
                             if value in ('waitingOnApproval', 'waitingOnUserInput')]}


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', type=Path)
    args = parser.parse_args()
    try:
        result = read_quota()
        if args.output:
            args.output.parent.mkdir(parents=True, exist_ok=True)
            args.output.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding='utf-8')
        print(json.dumps(result, ensure_ascii=False))
    except Exception as exc:
        print(json.dumps({'status': 'unavailable', 'reason': str(exc)}, ensure_ascii=False))
        raise SystemExit(2)
