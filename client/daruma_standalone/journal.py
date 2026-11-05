"""Durable action intents kept entirely inside the standalone runtime folder."""
from __future__ import annotations

from datetime import datetime
import json
import os
from pathlib import Path
import time
import uuid

from .vision import Pending, binding_matches


def stamp():
    return datetime.now().astimezone().isoformat(timespec='milliseconds')


def replace_file(source: Path, destination: Path):
    """Windows readers/AV can briefly deny replace despite writable files."""
    delays = (0, .01, .02, .04, .08, .12, .16, .20)
    for attempt, delay in enumerate(delays):
        if delay:
            time.sleep(delay)
        try:
            os.replace(source, destination)
            return
        except OSError as exc:
            transient = isinstance(exc, PermissionError) or getattr(exc, 'winerror', None) in (5, 32, 33)
            if not transient or attempt == len(delays)-1:
                raise


def atomic_json(path: Path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name + '.' + uuid.uuid4().hex + '.tmp')
    with temporary.open('w', encoding='utf-8', newline='\n') as handle:
        json.dump(value, handle, ensure_ascii=False, indent=2)
        handle.flush()
        os.fsync(handle.fileno())
    # On failure retain the last good checkpoint and the candidate.
    replace_file(temporary, path)


class Journal:
    def __init__(self, root: Path, binding: dict, sink=None):
        self.root, self.sink = Path(root), sink
        self.directory = None
        for path in sorted((self.root/'runs').glob('*/checkpoint.json'), reverse=True):
            try:
                value = json.loads(path.read_text(encoding='utf-8'))
            except (OSError, ValueError) as exc:
                raise Pending(f'独立断点无法读取，原文件保留：{path}') from exc
            if value.get('binding', {}).get('key') == binding['key'] and value.get('status') != 'done':
                if not binding_matches(value['binding'], binding):
                    raise Pending('独立断点与当前角色或游戏进程不一致；原断点保持不变')
                self.directory, self.data = path.parent, value
                break
        if self.directory is None:
            identifier = datetime.now().strftime('%Y%m%d_%H%M%S_%f')+'_'+uuid.uuid4().hex[:6]
            self.directory = self.root/'runs'/identifier
            self.data = dict(version=1, run_id=identifier, binding=binding, status='new',
                             phase='observe', layers=0, chance=None, remaining=None,
                             round=0, retries=0, latency=.15, actions=[], intent=None)
        self.directory.mkdir(parents=True, exist_ok=True)
        self.save()

    @property
    def path(self):
        return self.directory/'checkpoint.json'

    def save(self, **fields):
        candidate = {**self.data, **fields, 'updated_at': stamp()}
        atomic_json(self.path, candidate)
        self.data.update(candidate)

    def event(self, name, **fields):
        row = {**fields, 'at': stamp(), 'event': name, 'run_id': self.data['run_id']}
        with (self.directory/'events.jsonl').open('a', encoding='utf-8', newline='\n') as handle:
            handle.write(json.dumps(row, ensure_ascii=False)+'\n')
        if self.sink:
            self.sink(row)

    def evidence(self, frame, label):
        name = datetime.now().strftime('%H%M%S_%f')+'_'+label+'.png'
        path = self.directory/name
        frame.save(path, compress_level=1)
        return str(path)

    def begin(self, kind, **fields):
        if self.data.get('intent'):
            raise Pending('上一动作仍未核实，禁止重复输入')
        intent = dict(id=uuid.uuid4().hex, kind=kind, at=stamp(), sent=False, **fields)
        self.save(intent=intent)
        return intent

    def sent(self):
        self.data['intent']['sent'] = True
        self.data['intent']['sent_at'] = stamp()
        self.save()

    def cancel_unsent(self, reason):
        if self.data.get('intent') and not self.data['intent'].get('sent'):
            self.resolve('not_sent', reason=reason)

    def resolve(self, outcome, *, state=None, **fields):
        intent = self.data.get('intent')
        if not intent:
            raise Pending('缺少待核实动作')
        action = dict(intent, outcome=outcome, resolved_at=stamp(), **fields)
        self.save(actions=[*self.data['actions'], action], intent=None, **(state or {}))
        self.event('action_verified', **action)
