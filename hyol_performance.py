"""Per-worker timings; exclusive time avoids adding nested call durations."""
from __future__ import annotations
import functools
import threading
import time


class PerformanceMeter:
    def __init__(self, scheduler):
        self.rows, self.originals = {}, []
        self.context = threading.local()
        self.lock = threading.RLock()
        calls = [(scheduler, name) for name in (
            'capture', 'template', 'switch_account', 'switch_account_for_combat',
            'wait_interruptible', 'click_template', 'click_point', 'save_raw_evidence')]
        calls += [(scheduler.templates, 'match')]
        if hasattr(scheduler,'state'):
            calls += [(scheduler.state, 'save_account'),(scheduler.state,'save_global')]
        if hasattr(scheduler,'log'):
            calls += [(scheduler.log,'write')]
        for owner, name in calls:
            original = getattr(owner, name)
            self.originals.append((owner, name, original, name in owner.__dict__))
            label = 'recognition' if name == 'match' else name
            @functools.wraps(original)
            def measured(*args, _call=original, _label=label, **kwargs):
                started, entry = time.perf_counter(), [0.0]
                stack = getattr(self.context,'stack',None)
                if stack is None:
                    stack = self.context.stack = []
                stack.append(entry)
                try:
                    return _call(*args, **kwargs)
                finally:
                    elapsed = time.perf_counter()-started
                    stack.pop()
                    if stack:
                        stack[-1][0] += elapsed
                    with self.lock:
                        row = self.rows.setdefault(_label, {'calls': 0, 'seconds': 0.0, 'exclusive_seconds': 0.0})
                        row['calls'] += 1
                        row['seconds'] += elapsed
                        row['exclusive_seconds'] += max(0.0, elapsed-entry[0])
            setattr(owner, name, measured)

    def snapshot(self):
        with self.lock:
            return {name: {key: round(value,6) if isinstance(value,float) else value
                           for key,value in row.items()} for name,row in self.rows.items()}

    def close(self):
        for owner, name, original, own in reversed(self.originals):
            if own:
                setattr(owner,name,original)
            else:
                delattr(owner,name)
        self.originals.clear()
