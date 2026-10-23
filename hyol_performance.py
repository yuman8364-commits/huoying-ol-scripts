"""Per-worker timings; exclusive time avoids adding nested call durations."""
from __future__ import annotations
import functools
import threading
import time

_active_meters = threading.local()


class PerformanceMeter:
    def __init__(self, scheduler):
        import hyol_scheduler as hs
        self.rows, self.originals = {}, []
        self.scheduler, self.scopes = scheduler, {}
        self.context = threading.local()
        self.lock = threading.RLock()
        calls = [(scheduler, name) for name in (
            'capture', 'template', 'switch_account', 'switch_account_for_combat',
            'wait_interruptible', 'click_template', 'click_point', 'save_raw_evidence')]
        calls += [(scheduler.templates, 'match')]
        # Task and pipeline ledgers also call this directly, without going
        # through StateStore. The active-meter gate counts each physical write
        # once when a team worker hands off to its nested solo worker.
        calls += [(hs, 'atomic_json')]
        if hasattr(scheduler,'state'):
            # Discovery and handoff replace StateStore instances. Observe the
            # class methods so later ledgers remain counted by the active worker.
            for name in ('save_account','save_global'):
                owner=type(scheduler.state) if hasattr(type(scheduler.state),name) else scheduler.state
                calls.append((owner,name))
        if hasattr(scheduler,'log'):
            calls += [(scheduler.log,'write')]
        for owner, name in calls:
            original = getattr(owner, name)
            self.originals.append((owner, name, original, name in owner.__dict__))
            label = 'recognition' if name == 'match' else name
            @functools.wraps(original)
            def measured(*args, _call=original, _label=label, **kwargs):
                stack_of_meters=getattr(_active_meters,'items',())
                if not stack_of_meters or stack_of_meters[-1] is not self:
                    return _call(*args,**kwargs)
                stack = getattr(self.context,'stack',None)
                if stack is None:
                    stack = self.context.stack = []
                inherited = stack[-1][1] if stack else None
                switching = _label in ('switch_account', 'switch_account_for_combat')
                active = ((args[0] if args else kwargs.get('account')) if switching
                          else getattr(self.scheduler, 'active', None))
                account = getattr(active, 'name', None)
                navigator = getattr(self.scheduler, 'navigator', None)
                task = self.scheduler.__dict__.get('_performance_task') or getattr(navigator, 'current', None)
                scope = (account if isinstance(account, str) else 'unbound',
                         task if isinstance(task, str) and task else 'unscoped')
                if inherited is not None:
                    # A capture before the tab changes still belongs to the
                    # requested switch, rather than the previous active tab.
                    scope = (scope[0], inherited[1]) if switching else inherited
                switch_scope = scope if switching else inherited
                started, entry = time.perf_counter(), [0.0, switch_scope]
                stack.append(entry)
                try:
                    return _call(*args, **kwargs)
                finally:
                    elapsed = time.perf_counter()-started
                    stack.pop()
                    if stack:
                        stack[-1][0] += elapsed
                    with self.lock:
                        scoped = self.scopes.setdefault(scope, {})
                        for rows in (self.rows, scoped):
                            row = rows.setdefault(_label, {'calls': 0, 'seconds': 0.0, 'exclusive_seconds': 0.0})
                            row['calls'] += 1
                            row['seconds'] += elapsed
                            row['exclusive_seconds'] += max(0.0, elapsed-entry[0])
            setattr(owner, name, measured)
        stack_of_meters=getattr(_active_meters,'items',None)
        if stack_of_meters is None:
            stack_of_meters=_active_meters.items=[]
        stack_of_meters.append(self)

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
        stack_of_meters=getattr(_active_meters,'items',[])
        if self in stack_of_meters:stack_of_meters.remove(self)

    def scope_snapshot(self):
        with self.lock:
            return [dict(account=account, task=task, operations={name:{
                key:round(value,6) if isinstance(value,float) else value for key,value in row.items()}
                for name,row in rows.items()})
                for (account,task),rows in sorted(self.scopes.items())]
