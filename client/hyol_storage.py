"""Checkpoint storage and append-only logging, independent of task dispatch."""
from __future__ import annotations
from datetime import date, datetime, timedelta
import json
from pathlib import Path
import tempfile
import threading
import time
import warnings
from hyol_errors import PendingError


def _scheduler():
    # The compatibility facade remains the source of configurable paths and
    # instrumentation hooks (atomic_json/clock); no Scheduler is constructed.
    import hyol_scheduler
    return hyol_scheduler


class JsonlLogger:
    def __init__(self, path: Path):
        self.path = path
        self.lock = threading.Lock()
        self.last_error = ""
        self._handle = None
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
        except OSError as exc:
            self.last_error = str(exc)

    def close(self) -> None:
        # 20261106 ZCode: a persistent append handle replaces the per-event
        # open/flush/close chain.  Windows sharing flags still allow external
        # readers to tail the file; the retry path below covers external
        # exclusive locks, and any write error drops back to reopening.
        with self.lock:
            if self._handle is not None and not self._handle.closed:
                try:
                    self._handle.close()
                except OSError:
                    pass
            self._handle = None

    def _fallback(self, payload: str, exc: OSError) -> None:
        self.last_error = str(exc)
        fallback = Path(tempfile.gettempdir()) / "HYOL_diagnostics" / self.path.name
        try:
            fallback.parent.mkdir(parents=True, exist_ok=True)
            with fallback.open("a", encoding="utf-8") as handle:
                handle.write(payload)
            self.last_error += f"; fallback={fallback}"
        except OSError as fallback_exc:
            self.last_error += f"; fallback failed={fallback_exc}"
        try:
            warnings.warn(self.last_error, RuntimeWarning)
        except Exception:
            pass

    def write(self, event: str, **fields: object) -> None:
        row = {"ts": _scheduler().now_iso(), "event": event, **fields}
        payload = json.dumps(row, ensure_ascii=False, sort_keys=True) + "\n"
        # Logging is observational and must never abort a scheduler ``finally``
        # block (which would otherwise skip lease release and its final summary).
        with self.lock:
            for attempt in range(3):
                try:
                    if self._handle is None or self._handle.closed:
                        self._handle = self.path.open("a", encoding="utf-8", newline="\n")
                    self._handle.write(payload)
                    self._handle.flush()
                    return
                except OSError as exc:
                    self.last_error = str(exc)
                    try:
                        if self._handle is not None and not self._handle.closed:
                            self._handle.close()
                    except OSError:
                        pass
                    self._handle = None
                    if attempt == 2:
                        self._fallback(payload, exc)
                        return
                    time.sleep(0.05)


class StateStore:
    def __init__(self, accounts: list[str], directory: Path | None = None, aliases: dict[str, str] | None = None,
                 *, restore_history: bool = True, execution_day: str | None = None):
        self.directory = directory if directory is not None else _scheduler().STATE_DIR
        self.restore_history = restore_history
        self.aliases = aliases or {}
        if execution_day is None:
            from hyol_run_context import current_run
            context=current_run()
            if (context is not None and Path(self.directory).resolve().is_relative_to(context.directory.resolve())):
                execution_day=getattr(context,'execution_day',None)
        self.day = execution_day if execution_day is not None else date.today().isoformat()
        try:
            if type(self.day) is not str or date.fromisoformat(self.day).isoformat()!=self.day:
                raise ValueError('invalid date')
        except (TypeError,ValueError) as exc:
            raise PendingError('原运行执行日期无效，保留断点') from exc
        self.path = self.directory / f"{self.day}.json"
        self.account_paths = {name: self.directory / self.day / f"{_scheduler().slug(self.aliases.get(name, name))}.json" for name in accounts}
        self.global_state = self._load(self.path, {"date": self.day, "stage": "recognize", "pending": [], "teams": {}, "tab_map": {}})
        self.accounts = {
            name: self._load(path, {"date": self.day, "account": self.aliases.get(name, name), "modules": {}, "pending": []})
            for name, path in self.account_paths.items()
        }
        from hyol_checkpoints import CheckpointError, restore_actions
        try:
            if restore_history:
                restore_actions(self)
        except CheckpointError as exc:
            raise PendingError(str(exc)) from exc

    @staticmethod
    def _load(path: Path, default: dict) -> dict:
        from hyol_checkpoints import CheckpointError, read_checkpoint
        try:
            value = read_checkpoint(path)
        except CheckpointError as exc:
            raise PendingError(str(exc)) from exc
        if value and value.get('date') != default['date']:
            raise PendingError(f'断点日期与文件不一致：{path}')
        return value or default

    def save_global(self) -> None:
        self.global_state["updated_at"] = _scheduler().now_iso()
        _scheduler().atomic_json(self.path, self.global_state)

    def save_account(self, name: str) -> None:
        self.accounts[name]["updated_at"] = _scheduler().now_iso()
        _scheduler().atomic_json(self.account_paths[name], self.accounts[name])

    def module_status(self, name: str, module: str) -> str:
        return str(self.accounts[name].get("modules", {}).get(module, {}).get("status", "new"))

    def set_module(self, name: str, module: str, status: str, detail: str = "") -> None:
        modules = self.accounts[name].setdefault("modules", {})
        modules[module] = {"status": status, "detail": detail, "updated_at": _scheduler().now_iso()}
        self.save_account(name)
        # Keep the append-only pending audit trail, but do not leave a
        # previously blocked module looking unresolved after a verified
        # completion.  Market read-only probes are intentionally excluded:
        # only a coupon-only settlement is strong enough to resolve market
        # purchase pending records.
        if status == "done" and (not module.startswith("market_") or detail == "coupon-only offers settled"):
            self.resolve_pending(name, module)

    def add_pending(self, scope: str, module: str, detail: str, retry_at: datetime | None = None) -> None:
        item = {
            "scope": scope,
            "module": module,
            "detail": detail,
            "status": "pending",
            "created_at": _scheduler().now_iso(),
            "retry_at": (retry_at or datetime.now().astimezone() + timedelta(minutes=10)).isoformat(timespec="seconds"),
        }
        target = self.global_state.setdefault("pending", [])
        if not any(
            row.get("scope") == scope
            and row.get("module") == module
            and row.get("detail") == detail
            and str(row.get("status", "pending")) not in ("resolved", "cleared")
            for row in target
        ):
            target.append(item)
            self.save_global()

    def resolve_pending(self, scope: str, module: str, detail: str | None = None) -> int:
        """Mark matching pending rows resolved without deleting audit evidence."""
        target = self.global_state.setdefault("pending", [])
        changed = 0
        resolved_at = _scheduler().now_iso()
        for row in target:
            if not isinstance(row, dict):
                continue
            if row.get("scope") != scope or row.get("module") != module:
                continue
            if str(row.get("status", "pending")) in ("resolved", "cleared"):
                continue
            if detail is not None and row.get("detail") != detail:
                continue
            row["status"] = "resolved"
            row["resolved_at"] = resolved_at
            changed += 1
        if changed:
            self.save_global()
        return changed

    def unresolved_pending_count(self, since: str | None = None) -> int:
        return sum(
            1
            for row in self.global_state.get("pending", [])
            if isinstance(row, dict)
            and str(row.get("status", "pending")) not in ("resolved", "cleared")
            and (since is None or str(row.get("created_at", "")) >= since)
        )

    def action_status(self, name: str, action: str) -> str:
        return str(self.accounts[name].get("actions", {}).get(action, {}).get("status", "new"))

    def action_record(self, name: str, action: str) -> dict[str, object]:
        value = self.accounts[name].get("actions", {}).get(action, {})
        return dict(value) if isinstance(value, dict) else {}

    def set_action(self, name: str, action: str, status: str, detail: str = "") -> None:
        actions = self.accounts[name].setdefault("actions", {})
        previous = actions.get(action, {})
        if status == 'done' and previous.get('checkpoint_conflict'):
            from hyol_checkpoints import action_versions, read_checkpoint
            resolutions = read_checkpoint(self.directory / 'checkpoint_resolutions.json')
            remaining = [row for row in action_versions(previous)[1:]
                         if row.get('status') == 'pending'
                         and row.get('origin', {}).get('token') not in resolutions]
            if remaining:
                raise PendingError('动作存在多个未决来源，单次结果不能结清全部原动作：' + action)
        if previous.get('origin'):
            if status == 'new':
                raise PendingError('跨日未决动作不能重置：' + action)
            if status == 'done':
                from hyol_checkpoints import resolve_action
                resolve_action(self, name, action, detail)
        actions[action] = {"status": status, "detail": detail, "updated_at": _scheduler().now_iso()}
        if status != 'new' and previous.get('before_evidence'):
            actions[action]['before_evidence'] = previous['before_evidence']
        scope = self.accounts[name].get('continuation_scope')
        survival_scope = self.accounts[name].get('survival_operation_scope') if action.startswith('survival_') else None
        if survival_scope:
            if status != 'new':
                run = 2 if actions.get('survival_second_run_started', {}).get('status') == 'done' else 1
                actions[action]['operation_id'] = previous.get('operation_id', f'{survival_scope}:run_{run}:{action}')
        elif scope:
            actions[action]['operation_id'] = previous.get('operation_id',scope+':'+action)
        if previous.get('origin'):
            actions[action]['origin'] = previous['origin']
        self.save_account(name)

    def set_market_offer(self, name: str, key: str, record: dict[str, object]) -> None:
        """Persist one date/batch/slot market quote without erasing old actions."""
        offers = self.accounts[name].setdefault("market_offers", {})
        value = dict(record)
        value.setdefault("key", key)
        value["updated_at"] = _scheduler().now_iso()
        offers[key] = value
        self.save_account(name)
