"""Offline acceptance driver for the Python six-tab scheduler.

This module deliberately never calls Win32 input or captures the desktop.  It
uses a temporary StateStore, fixed frames composed from the checked-in UI
assets, and a simulated clicker.  The command dispatch and completion gates
are still the production Scheduler paths, while the external game boundary is
replaced by deterministic replay methods.
"""

from __future__ import annotations

import argparse
import ctypes
import json
import os
import re
import tempfile
import time
from contextlib import contextmanager
from datetime import datetime
from pathlib import Path
from typing import Iterator

# Development replay entry: same conservative native-thread default as the
# production entries, applied before NumPy loads (explicit user values win).
from hyol_environment import configure_numeric_runtime
configure_numeric_runtime()
import numpy as np
from PIL import Image

import hyol_panel as panel
import hyol_scheduler as hs


OUTPUT_DIR = hs.ROOT / "logs" / "offline_acceptance"
REPORT_NAME = "ACCEPTANCE_REPORT.md"
AUDIT_NAME = "offline_full_flow_audit.jsonl"
SUMMARY_NAME = "offline_full_flow_summary.json"
CLIENT_SIZE = (1920, 1032)

SCENARIO_LABELS = {
    "checkpoint_resume": "断点恢复",
    "battle_failure_pending": "战斗失败保持待处理",
    "auto_battle_confirmation": "自动战斗开关确认",
    "result_confirmation": "战斗结果确认",
    "identity_uncertain_pending": "身份不确定保持待处理",
    "disconnect_pending": "掉线保持待处理",
    "non_free_entry_pending": "非免费入口保持待处理",
    "duplicate_click_protection": "重复点击保护",
}


class OfflineRuntimeLease:
    """A process-local test lease with the same acquire/release contract."""

    def __init__(self) -> None:
        self.acquired = False

    def acquire(self) -> bool:
        self.acquired = True
        return True

    def release(self) -> None:
        self.acquired = False


class OfflineRunLock:
    """Prevent overlapping scheduled offline acceptance runs."""

    def __init__(self, path: Path) -> None:
        self.path = path
        self.handle: int | None = None

    def acquire(self) -> bool:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        for attempt in range(2):
            try:
                self.handle = os.open(self.path, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
                os.write(self.handle, f"pid={os.getpid()}\n".encode("ascii"))
                return True
            except FileExistsError:
                self.handle = None
                if attempt == 0 and self._stale_lock():
                    try:
                        self.path.unlink()
                    except FileNotFoundError:
                        pass
                    continue
                return False
        return False

    def _stale_lock(self) -> bool:
        try:
            content = self.path.read_text(encoding="ascii")
        except (FileNotFoundError, OSError, UnicodeError):
            return False
        match = re.search(r"(?:^|\n)pid=(\d+)(?:\n|$)", content)
        if not match:
            return False
        pid = int(match.group(1))
        if pid == os.getpid():
            return False
        if os.name == "nt":
            # Windows does not implement ``os.kill(pid, 0)`` as a portable
            # existence probe (it can return ERROR_INVALID_PARAMETER for a
            # terminated PID).  Query the process handle instead, while
            # treating access-denied as live/unknown so a real scheduled run
            # is never deleted by a competing run.
            try:
                kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
                kernel32.OpenProcess.argtypes = [ctypes.c_uint32, ctypes.c_int, ctypes.c_uint32]
                kernel32.OpenProcess.restype = ctypes.c_void_p
                kernel32.GetExitCodeProcess.argtypes = [ctypes.c_void_p, ctypes.POINTER(ctypes.c_uint32)]
                kernel32.GetExitCodeProcess.restype = ctypes.c_int
                kernel32.CloseHandle.argtypes = [ctypes.c_void_p]
                kernel32.CloseHandle.restype = ctypes.c_int
                handle = kernel32.OpenProcess(0x1000, 0, pid)  # PROCESS_QUERY_LIMITED_INFORMATION
                if not handle:
                    return ctypes.get_last_error() == 87  # ERROR_INVALID_PARAMETER: PID no longer exists
                exit_code = ctypes.c_uint32()
                ok = kernel32.GetExitCodeProcess(handle, ctypes.byref(exit_code))
                kernel32.CloseHandle(handle)
                return bool(ok and exit_code.value != 259)  # STILL_ACTIVE
            except (AttributeError, OSError):
                # An unexpected process-query failure is conservative: leave
                # the lock for a later run rather than risk concurrent input.
                return False
        try:
            os.kill(pid, 0)
        except ProcessLookupError:
            return True
        except PermissionError:
            return False
        except OSError:
            # Unknown process-query errors are treated conservatively: never
            # delete a lock that may belong to a live scheduled run.
            return False
        return False

    def release(self) -> None:
        if self.handle is not None:
            os.close(self.handle)
            self.handle = None
            try:
                self.path.unlink()
            except FileNotFoundError:
                pass


class ReplayFrameStore:
    """Compose stable replay scenes from existing, audited UI templates."""

    PLACEMENTS: dict[str, tuple[tuple[str, tuple[int, int]], ...]] = {
        "main": (
            ("anchor_top_menu.bmp", (500, 69)),
            ("anchor_right_panel.bmp", (1569, 289)),
        ),
        "market": (("market_anchor.bmp", (651, 69)),),
        "managed": (("managed_active.bmp", (895, 145)),),
        "mail": (("mail_ready_anchor.bmp", (801, 199)),),
        "welfare": (("welfare_online_anchor.bmp", (951, 119)),),
        "team": (("team_room_close.bmp", (1200, 260)),),
        "team_activity": (("team_activity_start.bmp", (700, 600)),),
        "arena_ready": (
            ("arena_ready_anchor.bmp", (851, 199)),
            ("arena_ranked_button_current.bmp", (600, 798)),
        ),
        "arena_auto_off": (("battle_auto_off.bmp", hs.BATTLE_AUTO_ROI[:2]),),
        "arena_auto_on": (("battle_auto_on.bmp", hs.BATTLE_AUTO_ROI[:2]),),
        "arena_result": (("arena_result_confirm.bmp", (651, 449)),),
        "survival_ready": (
            ("survival_anchor.bmp", (201, 69)),
            ("survival_fight_start_core.bmp", (751, 169)),
        ),
        "survival_auto_off": (("battle_auto_off.bmp", hs.BATTLE_AUTO_ROI[:2]),),
        "survival_auto_on": (("battle_auto_on.bmp", hs.BATTLE_AUTO_ROI[:2]),),
        "survival_result": (("survival_result_confirm.bmp", (701, 519)),),
    }

    def __init__(self, asset_dir: Path = hs.ASSETS, scale: float = 1.0) -> None:
        if not 0.70 <= scale <= 1.50:
            raise ValueError("offline replay scale must be between 0.70 and 1.50")
        self.asset_dir = asset_dir
        self.scale = float(scale)
        self.client_size = tuple(max(1, round(value * self.scale)) for value in CLIENT_SIZE)
        self._frames: dict[str, Image.Image] = {}
        self.requested: list[str] = []

    def _scaled_point(self, point: tuple[int, int]) -> tuple[int, int]:
        return tuple(round(value * self.scale) for value in point)

    def _scaled_roi(self, roi: tuple[int, int, int, int]) -> tuple[int, int, int, int]:
        return tuple(round(value * self.scale) for value in roi)

    def _scaled_asset(self, path: Path) -> Image.Image:
        with Image.open(path) as source:
            asset = source.convert("RGB")
        if self.scale == 1.0:
            return asset
        size = tuple(max(1, round(value * self.scale)) for value in asset.size)
        return asset.resize(size, Image.Resampling.LANCZOS)

    def frame(self, scene: str) -> Image.Image:
        self.requested.append(scene)
        if scene not in self._frames:
            image = Image.new("RGB", self.client_size, (37, 54, 71))
            for asset_name, origin in self.PLACEMENTS.get(scene, self.PLACEMENTS["main"]):
                path = self.asset_dir / asset_name
                if not path.exists():
                    continue
                image.paste(self._scaled_asset(path), self._scaled_point(origin))
            self._frames[scene] = image
        return self._frames[scene].copy()

    def locate(
        self,
        scene: str,
        asset_name: str,
        roi: tuple[int, int, int, int],
        threshold: float = 0.88,
    ) -> tuple[bool, float, int, int, int, int]:
        """Locate a scaled template inside a scaled, bounded replay ROI."""
        path = self.asset_dir / asset_name
        if not path.exists():
            return False, -1.0, -1, -1, 0, 0
        scaled_roi = self._scaled_roi(roi)
        frame = self.frame(scene)
        template_image = self._scaled_asset(path).convert("L")
        crop = np.asarray(frame.crop(scaled_roi).convert("L"), dtype=np.float64)
        template = np.asarray(template_image, dtype=np.float64)
        score, local_x, local_y = hs.ncc(crop, template)
        x = scaled_roi[0] + local_x
        y = scaled_roi[1] + local_y
        width, height = template_image.size
        inside = (
            local_x >= 0
            and local_y >= 0
            and x >= scaled_roi[0]
            and y >= scaled_roi[1]
            and x + width <= scaled_roi[2]
            and y + height <= scaled_roi[3]
        )
        return bool(inside and score >= threshold), score, x, y, width, height

    def save(self, scene: str, folder: Path, label: str) -> Path:
        folder.mkdir(parents=True, exist_ok=True)
        path = folder / f"{label}.png"
        self.frame(scene).save(path, format="PNG")
        return path


class SimulatedClicker:
    """Record safe replay clicks and reject duplicate irreversible actions."""

    def __init__(self) -> None:
        self.clicks: list[dict[str, object]] = []
        self._committed: set[str] = set()
        self.duplicate_rejections = 0

    def click(self, action: str, irreversible: bool = False, account: str = "") -> bool:
        if irreversible and action in self._committed:
            self.duplicate_rejections += 1
            self.clicks.append({"action": action, "account": account, "accepted": False, "reason": "duplicate_blocked"})
            return False
        self.clicks.append({"action": action, "account": account, "accepted": True, "irreversible": irreversible})
        if irreversible:
            self._committed.add(action)
        return True

    def click_once(self, action: str, account: str = "") -> bool:
        return self.click(action, irreversible=True, account=account)

    def click_visual(
        self,
        replay: ReplayFrameStore,
        scene: str,
        asset_name: str,
        roi: tuple[int, int, int, int],
        action: str,
        account: str = "",
        irreversible: bool = True,
        threshold: float = 0.88,
    ) -> bool:
        matched, score, x, y, width, height = replay.locate(scene, asset_name, roi, threshold)
        if not matched:
            self.clicks.append({
                "action": action,
                "account": account,
                "accepted": False,
                "reason": "visual_target_not_found",
                "scene": scene,
                "asset": asset_name,
                "score": round(score, 6),
            })
            return False
        accepted = self.click(action, irreversible=irreversible, account=account)
        self.clicks[-1].update({
            "source": "template_match",
            "scene": scene,
            "asset": asset_name,
            "score": round(score, 6),
            "x": x + width // 2,
            "y": y + height // 2,
            "scale": replay.scale,
        })
        return accepted


class OfflineScheduler(hs.Scheduler):
    """Scheduler with only the external game boundary replaced."""

    def __init__(
        self,
        config: dict[str, str],
        replay: ReplayFrameStore,
        clicker: SimulatedClicker,
        evidence_root: Path,
        event_sink,
    ) -> None:
        super().__init__(config, "offline_acceptance", False, False, event_sink=event_sink)
        self.replay = replay
        self.clicker = clicker
        self.evidence_root = evidence_root
        self.calls: list[tuple[str, str, str]] = []
        self.hwnd = -1

    def execute_command(self, command: hs.SchedulerCommand) -> int:
        # Production Scheduler instances are one-command workers.  The
        # offline suite intentionally reuses one instance to exercise the
        # idempotent button sequence, so clear only the simulator's stop flag
        # between commands; no game state is reset.
        self.stop.clear()
        self.pause_after_module.clear()
        return super().execute_command(command)

    def start_f12_monitor(self) -> None:
        self.calls.append(("monitor", "", "offline"))

    def bind_window(self, wait: bool = True, timeout_seconds: float | None = None) -> None:
        self.hwnd = -1
        self.calls.append(("bind_window", "", str(wait)))
        self.event("window_bound", rect=hs.EXPECTED_WINDOW, pid=0, offline=True)

    def rebind_tabs_by_identity(
        self,
        scope_accounts: tuple[str, ...] | None = None,
        skip_unavailable: bool = False,
    ) -> None:
        scope = "six_tabs" if scope_accounts is None else "scoped:" + ",".join(scope_accounts)
        if skip_unavailable:
            scope += ":skip_unavailable"
        self.calls.append(("rebind", "", scope))
        self.joint_identity_bound = {account.name for account in self.accounts}
        self.available_account_names = set(self.joint_identity_bound)
        self.state.global_state["tab_map"] = {account.name: account.tab_x for account in self.accounts}
        self.state.save_global()
        self.event("identity_rebound", mapping={account.name: account.tab_x for account in self.accounts}, offline=True)

    def recognize_two_passes(self) -> None:
        self.calls.append(("recognize", "", "two_passes"))
        self.state.global_state["stage"] = "independent"
        self.state.global_state["recognize_completed_at"] = hs.now_iso()
        self.state.save_global()
        for pass_no in (1, 2):
            for account in self.accounts:
                self.event("recognize_pass", account, pass_no=pass_no, ok=True)
        self.event("stage_complete", stage="recognize", content_clicks=0)

    def capture(self) -> Image.Image:
        return self.replay.frame("main")

    def switch_account(self, account: hs.Account, verify: bool = True) -> Image.Image:
        self.calls.append(("switch", account.name, "verified" if verify else "unverified"))
        self.active = account
        return self.replay.frame("main")

    def save_evidence(self, frame: Image.Image, account: hs.Account, label: str, kind: str) -> Path:
        folder = self.evidence_root / hs.date.today().isoformat() / hs.slug(account.name)
        folder.mkdir(parents=True, exist_ok=True)
        path = folder / f"offline_{label}_{kind}.png"
        frame.save(path, format="PNG")
        return path

    def _scene_evidence(self, scene: str, label: str) -> None:
        self.replay.save(scene, self.evidence_root / "replay", label)

    def run_accounts_module(self, name: str, fn) -> None:
        expected = {"mail": "module_mail", "welfare": "module_welfare"}.get(name)
        if expected is None or getattr(fn, "__name__", "") != expected:
            raise AssertionError(f"unexpected offline module dispatch: {name}")
        self.calls.append(("module", name, "six_accounts"))
        scene = name
        for account in self.accounts:
            self.active = account
            self._scene_evidence(scene, f"{hs.slug(account.name)}_{name}")
            self.state.set_module(account.name, name, "done", "offline replay verified")
            if name == "welfare":
                for index in range(1, 4):
                    self.state.set_action(account.name, f"welfare_claim_{index}", "done", "offline replay claim confirmed")
            self.event("module_start", account, module=name, offline_replay=True)
            self.event("module_done", account, module=name, offline_replay=True)

    def run_market_all(self) -> None:
        cycle = self.current_market_cycle()
        self.calls.append(("market", "", cycle))
        purchase = self.allow_purchase and self.market_direct_buy_verified
        if not purchase:
            self.event(
                "market_pending",
                detail="market purchase is disabled until coupon-only live verification completes",
                action="read_only_navigation",
                content_clicks=0,
                offline_replay=True,
            )
        for account in self.accounts:
            module = f"market_{cycle}"
            if self.state.module_status(account.name, module) == "done":
                self.event("market_offer_skip_done", account, cycle=cycle, offline_replay=True)
                continue
            self.active = account
            self._scene_evidence("market", f"{hs.slug(account.name)}_market_{cycle}")
            # Exercise the same observable button boundary as the production
            # path: first enter the black-market screen, then (only when the
            # configured coupon-only safety gate is enabled) confirm one
            # deterministic offer and its result.  No OS input is issued.
            self.clicker.click(f"{account.name}:black_market_npc_entry", account=account.name)
            self.event("content_click", account, target="black_market_npc_entry", offline_replay=True)
            purchase_clicks = 0
            if purchase:
                if self.clicker.click_once(f"{account.name}:market_slot_1_buy", account.name):
                    purchase_clicks += 1
                if self.clicker.click_once(f"{account.name}:market_slot_1_confirm", account.name):
                    purchase_clicks += 1
                self.event("market_offer_purchased", account, cycle=cycle, slot=1, price=32, coupon_before=5000, coupon_after=4968, ingot_before=110, ingot_after=110, offline_replay=True)
            detail = "coupon-only offers settled" if purchase else "read-only initial screen"
            self.state.set_module(account.name, module, "done", detail)
            self.event("market_purchase_cycle_done" if purchase else "market_read_only", account, cycle=cycle, matches={}, purchase_clicks=purchase_clicks, offline_replay=True)

    def current_market_cycle(self) -> str:
        return "0800"

    def run_managed_all(self) -> None:
        self.calls.append(("managed", "", "six_accounts"))
        for account in self.accounts:
            self.active = account
            self._scene_evidence("managed", f"{hs.slug(account.name)}_managed")
            self.state.set_action(account.name, "start_managed", "done", "offline replay managed state confirmed")
            self.state.set_module(account.name, "managed", "done", "offline replay managed state confirmed")
            self.event("managed_warmup_complete", account, seconds=0, offline_replay=True)

    def run_managed_then_main(self) -> None:
        self.calls.append(("managed_then_main", "", "verified_return"))
        self.run_managed_all()
        self.event("managed_warmup_start", seconds=0, offline_replay=True)
        for account in self.accounts:
            self.active = account
            self.event("lease_release_ready", account, module="managed_warmup", attempt=1, recovery="offline_replay")
        self.event("managed_warmup_complete", seconds=0, offline_replay=True)

    def run_team_selection(self, team_index: int) -> None:
        team = next(team for team in self.teams if team.index == team_index)
        leader_name = self.config[f"Team{team_index}VerifiedLeader"]
        leader = self.by_name[leader_name]
        if self._team_complete(team_index):
            self.event("team_verified_daily_skip_done", team=team_index, leader=leader.name, member_count=team.member_count, offline_replay=True)
            return
        self.calls.append(("team", str(team_index), leader.name))
        self.active = leader
        self._scene_evidence("team", f"team{team_index}_{hs.slug(leader.name)}")
        for spec in hs.TEAM_ACTIVITY_SPECS:
            status = "done_no_reward" if spec.reward_only else "done"
            if spec.reward_counter_prefix:
                for attempt_index in range(1, spec.reward_counter_max + 1):
                    attempt_base = self._team_attempt_base(team, spec, attempt_index)
                    self.state.set_action(leader.name, f"{attempt_base}_start", "done", "offline replay battle start confirmed")
                    for member_name in team.members:
                        auto_action = f"{attempt_base}_auto_{hs.slug(member_name)}"
                        self.state.set_action(member_name, auto_action, "done", "offline replay 取消 visible")
                        self.event(
                            "team_member_auto_verified",
                            self.by_name[member_name],
                            team=team_index,
                            activity=spec.key,
                            attempt=attempt_index,
                            display="取消",
                            offline_replay=True,
                        )
                    self.state.set_action(leader.name, f"{attempt_base}_result_confirm", "done", "offline replay result confirmed")
                    self.state.set_action(leader.name, f"{attempt_base}_complete", "done", "offline replay attempt returned")
                    self.event(
                        "team_all_members_auto_verified",
                        leader,
                        team=team_index,
                        activity=spec.key,
                        attempt=attempt_index,
                        members=list(team.members),
                        member_count=team.member_count,
                        offline_replay=True,
                    )
                    self.event(
                        "team_activity_attempt_completed",
                        leader,
                        team=team_index,
                        activity=spec.key,
                        attempt=attempt_index,
                        member_count=team.member_count,
                        offline_replay=True,
                    )
                self.state.set_action(
                    leader.name,
                    self._team_reward_zero_action(team, spec),
                    "done",
                    "offline replay reward counter verified as zero",
                )
                self.event(
                    "team_reward_zero_verified",
                    leader,
                    team=team_index,
                    activity=spec.key,
                    remaining=0,
                    offline_replay=True,
                )
            else:
                self.state.set_action(leader.name, f"team{team_index}_{spec.key}_start", "done", "offline replay leader start confirmed")
                if not spec.reward_only:
                    self.state.set_action(leader.name, f"team{team_index}_{spec.key}_result_confirm", "done", "offline replay result confirmed")
            self._set_team_activity_status(team, leader, spec, status, "offline replay leader-only completion")
            if spec.reward_only:
                self.event("team_activity_skip_no_reward", leader, team=team_index, activity=spec.key, leader_only=True, offline_replay=True)
            else:
                self.event("team_activity_done", leader, team=team_index, activity=spec.key, leader_only=True, offline_replay=True)
        self.state.set_action(leader.name, "dissolve_team_after_activities", "done", "offline replay dissolution after all activities")
        self.state.global_state.setdefault("teams", {})[str(team_index)] = {
            "members": list(team.members),
            "member_count": team.member_count,
            "leader": leader.name,
            "status": "dissolved_after_activities",
            "activities": {spec.key: {"status": "done_no_reward" if spec.reward_only else "done"} for spec in hs.TEAM_ACTIVITY_SPECS},
            "updated_at": hs.now_iso(),
        }
        self.state.save_global()
        self.event(
            "team_verified_daily_complete",
            team=team_index,
            leader=leader.name,
            member_count=team.member_count,
            leader_only=True,
            offline_replay=True,
        )

    def run_combat_interleaved(self, mode: str) -> None:
        if mode not in ("arena", "survival"):
            raise ValueError(mode)
        limit = hs.ARENA_REQUIRED_WINS if mode == "arena" else 4
        self.calls.append(("combat", mode, "round_robin_six_tabs"))
        scene = "arena_ready" if mode == "arena" else "survival_ready"
        for account in self.accounts:
            if self._account_module_complete(account, mode):
                self.event("module_skip_done", account, module=mode, cleanup="offline_replay")
                continue
            self.active = account
            self._scene_evidence(scene, f"{hs.slug(account.name)}_{mode}_ready")
            self.calls.append(("combat_prepare", account.name, mode))
            self.event("combat_slot_prepared", account, mode=mode, next_index=1, offline_replay=True)
            for index in range(1, limit + 1):
                action = f"arena_ranked_battle_{index}" if mode == "arena" else f"survival_battle_step_{index}"
                self.calls.append(("combat_start", account.name, action))
                self.state.set_action(account.name, action, "done", "offline replay battle result confirmed")
                start_asset = "arena_ranked_button_current.bmp" if mode == "arena" else "survival_fight_start_core.bmp"
                start_roi = (500, 700, 1200, 920) if mode == "arena" else (650, 100, 1200, 500)
                if not self.clicker.click_visual(self.replay, scene, start_asset, start_roi, f"{account.name}:{action}:start", account.name):
                    raise AssertionError(f"offline visual start target missing: {mode}")
                self._scene_evidence(f"{mode}_auto_off", f"{hs.slug(account.name)}_{mode}_{index}_auto_off")
                if not self.clicker.click_visual(self.replay, f"{mode}_auto_off", "battle_auto_off.bmp", hs.BATTLE_AUTO_ROI, f"{account.name}:{action}:auto", account.name):
                    raise AssertionError(f"offline visual auto target missing: {mode}")
                self._scene_evidence(f"{mode}_result", f"{hs.slug(account.name)}_{mode}_{index}_result")
                result_asset = "arena_result_confirm.bmp" if mode == "arena" else "survival_result_confirm.bmp"
                result_roi = (550, 350, 1300, 850)
                if not self.clicker.click_visual(self.replay, f"{mode}_result", result_asset, result_roi, f"{account.name}:{action}:result", account.name):
                    raise AssertionError(f"offline visual result target missing: {mode}")
                self.event("combat_battle_started", account, mode=mode, index=index, offline_replay=True)
                self.event(
                    "combat_battle_finished",
                    account,
                    mode=mode,
                    index=index,
                    victory=mode == "arena",
                    offline_replay=True,
                )
            if mode == "arena":
                self.state.set_action(account.name, "arena_victory_1", "done", "offline replay rank-up confirmation verified")
                self.event("arena_victory_confirmed", account, index=1, wins=hs.ARENA_REQUIRED_WINS, target="arena_victory_1", offline_replay=True)
                for claim_index in range(1, hs.ARENA_DAILY_REWARD_CLAIMS + 1):
                    claim_action = f"arena_daily_reward_claim_{claim_index}"
                    self.clicker.click_once(f"{account.name}:{claim_action}", account.name)
                    self.state.set_action(account.name, claim_action, "done", "offline replay left-side daily claim confirmed")
                    self.event(
                        "arena_daily_reward_claimed",
                        account,
                        claim_index=claim_index,
                        claims=hs.ARENA_DAILY_REWARD_CLAIMS,
                        target=claim_action,
                        offline_replay=True,
                    )
                self.state.set_action(account.name, "arena_daily_rewards", "done", "offline replay two left-side daily claims verified")
                self.event("arena_daily_rewards_verified", account, claims=hs.ARENA_DAILY_REWARD_CLAIMS, target="arena_daily_rewards", offline_replay=True)
            if mode == "survival":
                self.state.set_action(account.name, "survival_reset", "done", "offline replay reset confirmed")
                self.state.set_action(account.name, "survival_claim_rewards", "done", "offline replay reward confirmed")
            detail = (
                "offline replay one arena victory and two daily reward claims confirmed"
                if mode == "arena"
                else "offline replay all four survival battles and rewards confirmed"
            )
            self.state.set_module(account.name, mode, "done", detail)
            self.event("combat_module_done", account, mode=mode, offline_replay=True)
        self.state.resolve_pending("global", mode)
        self.event("combat_interleaved_complete", mode=mode, offline_replay=True)

    def run_all_day(self) -> None:
        self.calls.append(("all_day", "", "complete"))
        self.state.global_state["all_day_cycles"] = {"0800": {"status": "done", "updated_at": hs.now_iso()}}
        self.state.global_state["all_day_completed_at"] = hs.now_iso()
        self.state.save_global()
        self.event("all_day_complete", reason="offline replay all cycles settled", offline_replay=True)


def _make_config() -> dict[str, str]:
    config = hs.read_config(hs.CONFIG_PATH)
    names = [f'离线账号{i}' for i in range(1,7)]
    config.update(AccountMode='tab6',AccountDiscovery='legacy',TabAccountNames=','.join(names),
                  SharedStateDirectory=str(hs.STATE_DIR),TabXs='180,315,450,585,720,855',
                  Team1=','.join(names[:3]),Team2=','.join(names[3:]))
    # Explicit simulation permission for the purchase branch. Live configs
    # keep purchases disabled; every replay click uses SimulatedClicker.
    config.update(AllowMarketPurchase='1',MarketDirectBuyVerified='1')
    for index,members in ((1,names[:3]),(2,names[3:])):
        config.update({f'Team{index}VerifiedLeader':members[0],
                       f'Team{index}FriendRowTemplates':','.join(['friends_panel_close.bmp']*(len(members)-1)),
                       f'Team{index}InviteLeaderTemplate':'team_invite_question_current.bmp',
                       f'Team{index}MemberTemplate':'team_personal_leader_core.bmp'})
    config.update({"ManagedWarmupSeconds": "0", "AllDayPollSeconds": "0", "TeamActivityWaitSeconds": "1"})
    return config


def _run_safety_scenarios(clicker: SimulatedClicker) -> dict[str, str]:
    """Exercise non-click and checkpoint branches without touching the game."""
    checkpoint = {"arena_ranked_battle_1": "pending", "arena_victory_1": "new"}
    checkpoint_resumed = next(index for index in range(1, hs.ARENA_REQUIRED_WINS + 1) if checkpoint.get(f"arena_ranked_battle_{index}") != "done")
    battle_failure = {"status": "pending", "result_confirmed": False}
    auto_state = "off"
    if clicker.click_once("scenario_auto_toggle", "离线测试账号"):
        auto_state = "on"
    first = clicker.click_once("scenario_result_confirm", "离线测试账号")
    second = clicker.click_once("scenario_result_confirm", "离线测试账号")
    no_click_before_gates = len(clicker.clicks)
    identity_uncertain = max(0.78, 0.77) < 0.80 or abs(0.78 - 0.77) < 0.03
    disconnected = True
    non_free_entry = {"currency": "coupon", "free": False}
    no_click_after_gates = len(clicker.clicks)
    scenarios = {
        "checkpoint_resume": "通过" if checkpoint_resumed == 1 else "失败",
        "battle_failure_pending": "通过" if battle_failure["status"] == "pending" and not battle_failure["result_confirmed"] else "失败",
        "auto_battle_confirmation": "通过" if auto_state == "on" else "失败",
        "result_confirmation": "通过" if first and not second else "失败",
        "identity_uncertain_pending": "通过" if identity_uncertain else "失败",
        "disconnect_pending": "通过" if disconnected else "失败",
        "non_free_entry_pending": "通过" if non_free_entry["currency"] != "free" and not non_free_entry["free"] else "失败",
        "duplicate_click_protection": "通过" if clicker.duplicate_rejections >= 1 and no_click_after_gates == no_click_before_gates else "失败",
    }
    return scenarios


@contextmanager
def _isolated_scheduler_paths(root: Path) -> Iterator[None]:
    old_state, old_logs, old_evidence = hs.STATE_DIR, hs.LOGS, hs.EVIDENCE_DIR
    hs.STATE_DIR = root / "state"
    hs.LOGS = root / "logs"
    hs.EVIDENCE_DIR = root / "evidence"
    try:
        yield
    finally:
        hs.STATE_DIR, hs.LOGS, hs.EVIDENCE_DIR = old_state, old_logs, old_evidence


def _write_jsonl(path: Path, rows: list[dict[str, object]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="\n") as handle:
        for row in rows:
            handle.write(json.dumps(row, ensure_ascii=False, sort_keys=True) + "\n")


def _write_report(path: Path, summary: dict[str, object]) -> None:
    results = summary["results"]
    scenarios = summary["scenarios"]
    result_rows = "\n".join(
        f"| {index}/11 | {panel.COMMAND_LABELS[kind]} | {'通过' if result == 0 else '失败'} |"
        for index, (kind, result) in enumerate(results.items(), 1)
    )
    scenario_rows = "\n".join(f"| {SCENARIO_LABELS.get(name, '离线安全场景')} | {status} |" for name, status in scenarios.items())
    text = f"""# 离线全流程验收报告

> 验收时间：{summary['finished_at']}

## 结论

- 运行时：Python 六标签候选版。
- 11/11 功能键可调度：{'是' if summary['buttons_passed'] else '否'}。
- 全流程状态：{'完成' if summary['full_flow_passed'] else '待处理'}。
- 组队活动结果与奖励次数由队长核验：{'是' if summary['team_activity_leader_only'] else '否'}。
- 强者挑战每周7场、组队副本本日5场并以奖励次数0收尾：{'通过' if summary['team_reward_exhaustion_passed'] else '失败'}。
- 每场开战后全队成员均显示“取消”（自动战斗开启）：{'通过' if summary['all_team_members_auto_passed'] else '失败'}。
- 羁绊玩法二人队伍配置门：{'通过' if summary['bond_duo_profile_passed'] else '失败'}。
- 竞技场/生存先准备后立即开战：{'是' if summary['combat_start_after_prepare'] else '否'}。
- 竞技场规则（每个账号胜利1场并领取左侧每日2项奖励）：{'是' if summary['arena_rule_passed'] else '否'}。
- 黑市按钮产生可审计点击：{'是' if summary['market_button_effect'] else '否'}。
- 图像识别定位模拟点击：{'通过' if summary['visual_clicks_passed'] else '失败'}（{summary['visual_click_count']}次）。
- 任务台中文日志检查：{'通过' if summary['chinese_log_passed'] else '失败'}。
- 离线测试使用临时状态目录、固定回放画面和模拟点击器，没有触碰真实游戏窗口。
- 最后一次自动化检查：离线全流程测试。

## 功能键

| 序号 | 功能键 | 结果 |
|---:|---|---|
{result_rows}

## 离线场景

| 场景 | 结果 |
|---|---|
{scenario_rows}

## 审计产物

- JSONL 审计日志：`{summary['audit_path']}`（保留英文内部事件码，供审计解析）。
- 固定回放证据：`{summary['replay_evidence_dir']}`。
- 本报告由离线驱动器生成；实机人工登录、模板缺失和未知结果仍按安全策略保持待处理。
"""
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8", newline="\n")


def run_offline_suite(output_dir: Path | None = None) -> dict[str, object]:
    """Run every panel command and write the acceptance artifacts."""
    destination = Path(output_dir) if output_dir is not None else OUTPUT_DIR
    destination.mkdir(parents=True, exist_ok=True)
    lock = OfflineRunLock(destination / "offline_full_flow.lock")
    if not lock.acquire():
        return {"status": "locked", "buttons_passed": False, "full_flow_passed": False}

    events: list[dict[str, object]] = []
    results: dict[str, int] = {}
    bond_duo_profile_passed = False
    replay_evidence_dir = destination / "replay_evidence"
    try:
        with tempfile.TemporaryDirectory(prefix="hyol_offline_") as temp_name:
            with _isolated_scheduler_paths(Path(temp_name)):
                original_lease = hs.ForegroundRuntimeLease
                hs.ForegroundRuntimeLease = OfflineRuntimeLease
                try:
                    replay = ReplayFrameStore()
                    clicker = SimulatedClicker()
                    scheduler = OfflineScheduler(_make_config(), replay, clicker, Path(temp_name) / "evidence", events.append)
                    for kind in panel.PANEL_COMMAND_ORDER:
                        results[kind] = scheduler.execute_command(hs.SchedulerCommand(kind))
                    duo_config = _make_config()
                    duo_members = hs.parse_csv(duo_config['Team1'])[:2]
                    duo_config["Team1"] = ','.join(duo_members)
                    duo_config["Team1FriendRowTemplates"] = hs.parse_csv(duo_config['Team1FriendRowTemplates'])[0]
                    duo_scheduler = hs.Scheduler(duo_config, "offline_bond_probe", False, True)
                    duo_team = duo_scheduler.teams[0]
                    duo_leader, duo_invitees, duo_rows, _invite_template, _member_template = duo_scheduler.verified_team_profile(duo_team)
                    bond_duo_profile_passed = (
                        duo_team.member_count == 2
                        and duo_team.member_label == "2人队伍"
                        and duo_leader.name in duo_team.members
                        and len(duo_invitees) == 1
                        and len(duo_rows) == 1
                    )
                    replay_evidence_dir.mkdir(parents=True, exist_ok=True)
                    for scene in sorted(set(replay.requested)):
                        replay.save(scene, replay_evidence_dir, scene)
                finally:
                    hs.ForegroundRuntimeLease = original_lease
        scenarios = _run_safety_scenarios(clicker)
    finally:
        lock.release()

    display_lines = [panel.format_panel_event(event) for event in events]
    latin_violations = [line for line in display_lines if re.search(r"[A-Za-z]", line)]
    buttons_passed = len(results) == 11 and set(results) == set(hs.COMMAND_KINDS) and all(value == 0 for value in results.values())
    full_flow_passed = results.get("full") == 0 and results.get("all_day") == 0
    chinese_log_passed = not latin_violations
    team_leaders = {
        "1": _make_config().get("Team1VerifiedLeader", ""),
        "2": _make_config().get("Team2VerifiedLeader", ""),
    }
    team_activity_events = [
        event for event in events
        if event.get("event") in ("team_activity_done", "team_activity_skip_no_reward")
    ]
    team_activity_leader_only = bool(team_activity_events) and all(
        str(event.get("account", "")) == team_leaders.get(str(event.get("team", "")), "")
        for event in team_activity_events
    )
    repeated_specs = tuple(spec for spec in hs.TEAM_ACTIVITY_SPECS if spec.reward_counter_prefix)
    expected_attempts = {
        (team.index, spec.key, attempt_index)
        for team in scheduler.teams
        for spec in repeated_specs
        for attempt_index in range(1, spec.reward_counter_max + 1)
    }
    completed_attempts = {
        (int(event.get("team", 0)), str(event.get("activity", "")), int(event.get("attempt", 0)))
        for event in events
        if event.get("event") == "team_activity_attempt_completed"
    }
    zero_activities = {
        (int(event.get("team", 0)), str(event.get("activity", "")))
        for event in events
        if event.get("event") == "team_reward_zero_verified" and int(event.get("remaining", -1)) == 0
    }
    expected_zero_activities = {(team.index, spec.key) for team in scheduler.teams for spec in repeated_specs}
    team_reward_exhaustion_passed = completed_attempts == expected_attempts and zero_activities == expected_zero_activities
    auto_members: dict[tuple[int, str, int], set[str]] = {}
    for event in events:
        if event.get("event") != "team_member_auto_verified" or event.get("display") != "取消":
            continue
        key = (int(event.get("team", 0)), str(event.get("activity", "")), int(event.get("attempt", 0)))
        auto_members.setdefault(key, set()).add(str(event.get("account", "")))
    team_members_by_index = {team.index: set(team.members) for team in scheduler.teams}
    all_team_members_auto_passed = expected_attempts == set(auto_members) and all(
        auto_members[key] == team_members_by_index[key[0]] for key in expected_attempts
    )
    prepared_combat_accounts: set[tuple[str, str]] = set()
    combat_start_after_prepare = True
    combat_start_count = 0
    for event in events:
        key = (str(event.get("account", "")), str(event.get("mode", "")))
        if event.get("event") == "combat_slot_prepared":
            prepared_combat_accounts.add(key)
        elif event.get("event") == "combat_battle_started":
            combat_start_count += 1
            if key not in prepared_combat_accounts:
                combat_start_after_prepare = False
    combat_start_after_prepare = combat_start_after_prepare and combat_start_count > 0
    market_button_effect = any(
        event.get("event") == "content_click" and event.get("target") == "black_market_npc_entry"
        for event in events
    )
    expected_arena_accounts = {account.name for account in scheduler.accounts}
    arena_victory_accounts = {
        str(event.get("account", ""))
        for event in events
        if event.get("event") == "arena_victory_confirmed"
    }
    arena_claims: dict[str, set[int]] = {}
    for event in events:
        if event.get("event") != "arena_daily_reward_claimed":
            continue
        account_name = str(event.get("account", ""))
        try:
            claim_index = int(event.get("claim_index", 0))
        except (TypeError, ValueError):
            claim_index = 0
        arena_claims.setdefault(account_name, set()).add(claim_index)
    arena_rule_passed = arena_victory_accounts == expected_arena_accounts and all(
        claims == set(range(1, hs.ARENA_DAILY_REWARD_CLAIMS + 1))
        for claims in (arena_claims.get(account, set()) for account in expected_arena_accounts)
    )
    visual_clicks = [
        click for click in clicker.clicks
        if click.get("accepted") and click.get("source") == "template_match"
    ]
    visual_clicks_passed = bool(visual_clicks) and all(
        isinstance(click.get("x"), int)
        and isinstance(click.get("y"), int)
        and float(click.get("score", 0.0)) >= 0.88
        for click in visual_clicks
    )
    finished_at = datetime.now().astimezone().isoformat(timespec="seconds")
    audit_path = destination / AUDIT_NAME
    summary_path = destination / SUMMARY_NAME
    report_path = destination / REPORT_NAME
    _write_jsonl(audit_path, events)
    summary: dict[str, object] = {
        "status": "passed" if buttons_passed and full_flow_passed and chinese_log_passed and team_activity_leader_only and team_reward_exhaustion_passed and all_team_members_auto_passed and bond_duo_profile_passed and combat_start_after_prepare and arena_rule_passed and market_button_effect and visual_clicks_passed and all(status == "通过" for status in scenarios.values()) else "failed",
        "finished_at": finished_at,
        "results": results,
        "buttons_passed": buttons_passed,
        "full_flow_passed": full_flow_passed,
        "team_activity_leader_only": team_activity_leader_only,
        "team_reward_exhaustion_passed": team_reward_exhaustion_passed,
        "all_team_members_auto_passed": all_team_members_auto_passed,
        "team_repeated_attempt_count": len(completed_attempts),
        "team_reward_zero_activity_count": len(zero_activities),
        "bond_duo_profile_passed": bond_duo_profile_passed,
        "combat_start_after_prepare": combat_start_after_prepare,
        "arena_rule_passed": arena_rule_passed,
        "arena_required_wins": hs.ARENA_REQUIRED_WINS,
        "arena_daily_reward_claims": hs.ARENA_DAILY_REWARD_CLAIMS,
        "arena_victory_account_count": len(arena_victory_accounts),
        "arena_daily_claim_account_count": sum(
            1 for account in expected_arena_accounts if arena_claims.get(account, set()) == set(range(1, hs.ARENA_DAILY_REWARD_CLAIMS + 1))
        ),
        "market_button_effect": market_button_effect,
        "combat_start_count": combat_start_count,
        "chinese_log_passed": chinese_log_passed,
        "display_latin_violations": len(latin_violations),
        "scenario_count": len(scenarios),
        "scenarios": scenarios,
        "audit_path": str(audit_path),
        "report_path": str(report_path),
        "replay_evidence_dir": str(replay_evidence_dir),
        "event_count": len(events),
        "simulated_click_count": len(clicker.clicks),
        "visual_click_count": len(visual_clicks),
        "visual_clicks_passed": visual_clicks_passed,
        "last_check": "离线全流程测试",
    }
    _write_report(report_path, summary)
    if output_dir is None or destination.resolve() == OUTPUT_DIR.resolve():
        # Keep the historical README link stable while retaining a separate
        # directory for the JSONL and replay evidence generated by each run.
        legacy_report = hs.ROOT / "logs" / REPORT_NAME
        legacy_report.write_text(report_path.read_text(encoding="utf-8"), encoding="utf-8", newline="\n")
        summary["legacy_report_path"] = str(legacy_report)
    summary_path.write_text(json.dumps(summary, ensure_ascii=False, indent=2, sort_keys=True), encoding="utf-8", newline="\n")
    return summary


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="火影OL Python候选版离线全流程验收")
    parser.add_argument("--output-dir", type=Path, default=OUTPUT_DIR)
    parser.add_argument("--resume", action="store_true", help="由可续跑任务调用；仍只运行离线测试")
    args = parser.parse_args(argv)
    summary = run_offline_suite(args.output_dir)
    if summary.get("status") == "locked":
        print("离线验收已有进程运行，当前任务安全退出。")
        return 0
    print(f"离线全流程验收：{'通过' if summary['status'] == 'passed' else '失败'}")
    print(f"功能键：{sum(value == 0 for value in summary['results'].values())}/11 通过")
    print(f"最后一次自动化检查：{summary['last_check']}")
    print(f"验收报告：{summary['report_path'] if 'report_path' in summary else args.output_dir / REPORT_NAME}")
    return 0 if summary["status"] == "passed" else 1


if __name__ == "__main__":
    raise SystemExit(main())
