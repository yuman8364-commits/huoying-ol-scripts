from __future__ import annotations

import argparse
import ctypes
import functools
import hashlib
import itertools
import json
import os
import re
import sys
import threading
import time
import tempfile
import warnings
from collections import OrderedDict
from dataclasses import dataclass
from datetime import date, datetime, time as clock_time, timedelta
from pathlib import Path
from typing import Callable, Iterable

import numpy as np
from PIL import Image, ImageGrab
from hyol_diagnostics import Diagnostics, export_from_scheduler, mark_manual_pause, run_id
from hyol_recognition import Detection, OnnxDetector, fuse
from hyol_geometry import GameGeometry
from hyol_survival_counts import GRADES as SURVIVAL_GRADES, grade_candidates, choose_grade
from hyol_combat_watchdog import BattleWatchdog


# A frozen GUI keeps editable configuration, visual assets, logs and state
# beside the executable.  Normal source runs continue to use this file's
# directory, preserving the development/test layout.
ROOT = Path(sys.executable).resolve().parent if getattr(sys, "frozen", False) else Path(__file__).resolve().parent
ASSETS = ROOT / "assets"
LOGS = ROOT / "logs"
STATE_DIR = ROOT / "state"
EVIDENCE_DIR = LOGS / "scheduler_evidence"
CONFIG_PATH = ROOT / "HYOL_Daily.ini"

GAME_TITLE = "火影忍者online"
GAME_EXE = "qqmicrogamebox.exe"
EXPECTED_WINDOW = (0, 0, 1920, 1032)
VIEW = (1, 69, 1919, 1031)
TARGET_CARD_CLOSE_ROI = (695, 80, 760, 145)
TARGET_CARD_ACTIONS_ROI = (520, 145, 590, 200)
MARKET_NOTICE_ROI = (1550, 600, 1919, 950)
# The black-market NPC can render beside the minimap at the right edge of the
# game viewport.  Keep the lower bound above the notification card, but do not
# cut off the edge label: doing so makes the market command appear to do
# nothing when the map is already in South Konoha.
MARKET_NPC_ROI = (200, 100, 1919, 600)
BATTLE_AUTO_ROI = (1120, 820, 1320, 980)
# A live team battle can remain on the shared leader tab after the activity
# catalogue has already closed.  This is the fixed-client blue ``开战(2)``
# control captured from the Team2 battle evidence.  Keep its ROI narrow and
# require the enrolled button image; a generic center click is unsafe here.
TEAM_BATTLE_START_TEMPLATE = "team_battle_start_current.bmp"
TEAM_BATTLE_START_ROI = (800, 540, 1100, 700)
TEAM_BATTLE_RESULT_ROI = (650, 400, 1250, 850)
TEAM_REWARD_RESULT_TEMPLATE = "team_reward_result_confirm.bmp"
TEAM_REWARD_RESULT_ROI = TEAM_BATTLE_RESULT_ROI
# Reward counters are the authoritative completion gates for the two repeated
# cooperative activities.  A successful return from one battle is only an
# attempt completion; the activity is complete exclusively when the matching
# counter is positively read as zero.
GROUP_INSTANCE_DAILY_REWARD_ROI = (735, 775, 790, 810)
GROUP_INSTANCE_DAILY_REWARD_MAX = 5
STRONG_APPROACH_WEEKLY_REWARD_ROI = (900, 805, 950, 850)
STRONG_APPROACH_WEEKLY_REWARD_MAX = 7
TEAM_MEMBER_AUTO_WAIT_SECONDS = 12.0
ARENA_VICTORY_ROI = (800, 300, 1120, 600)
ARENA_LOADING_ROI = (750, 380, 1200, 500)
# The daily arena target is deliberately small: one positively confirmed
# victory is enough.  The two left-side daily claim controls are separate from
# that target and must each be positively observed before the module is done.
ARENA_REQUIRED_WINS = 1
ARENA_DAILY_REWARD_CLAIMS = 2
ARENA_MAX_ATTEMPTS = 10
ARENA_MAX_REQUESTED_WINS = 99
ARENA_POLL_INTERVAL_SECONDS = 2.0
ARENA_SLOT_PENDING_LIMIT = 3
SURVIVAL_SLOT_PENDING_LIMIT = 6
ARENA_STALE_PENDING_SECONDS = 900
IDENTITY_ROI = (105, 72, 250, 104)
IDENTITY_SERVER_WIDTH = 18
IDENTITY_TEXT_THRESHOLD = 120
IDENTITY_TEXT_SATURATION = 35
POWER_ROI = (92, 112, 315, 153)
VK_F12 = 0x7B

# The online-gift page renders three independent claim cards.  The old
# implementation searched one large ROI for every card; after the first click
# the grey "已领取" button matched that same ROI for card 2 and card 3, so a
# one-claim run was incorrectly recorded as complete.  Keep each card in its
# own bounded ROI so a positive match cannot be borrowed from a neighbouring
# card.  These coordinates are relative to the fixed 1920x1032 game client.
WELFARE_CLAIM_ROIS = (
    (730, 680, 880, 760),
    (925, 680, 1080, 760),
    (1120, 680, 1280, 760),
)

# The member list in the fixed 1920×1032 team room is a 500×370 panel.  Its
# origin is stable in the enrolled screenshots, but Flash can repaint a few
# pixels around the panel; the enrollment matcher searches a small bounded
# neighbourhood before saving a new exact-member asset.
TEAM_ROOM_MEMBER_SIZE = (500, 370)
TEAM_ROOM_MEMBER_ORIGIN = (700, 380)
TEAM_ROOM_MEMBER_ROW_HEIGHTS = (123, 123, 124)
RESCUE_OPEN_FROM = clock_time(18, 0)
RESCUE_OPEN_UNTIL = clock_time(22, 30)
RESCUE_OPEN_WEEKDAYS = frozenset((3, 4, 6))  # 周四、周五、周日


def rescue_activity_open_now(current: datetime | None = None) -> bool:
    """Return whether the live rescue activity is scheduled to be open."""
    current = current or datetime.now()
    return current.weekday() in RESCUE_OPEN_WEEKDAYS and RESCUE_OPEN_FROM <= current.time() <= RESCUE_OPEN_UNTIL


class SafetyError(RuntimeError):
    pass


class TargetPreconditionChanged(SafetyError):
    """A freshly captured target no longer satisfies its observed precondition."""


class PendingError(RuntimeError):
    pass


class BattleStalled(PendingError):
    """Treat an unchanged step as offline for this run, preserving its result."""
    def __init__(self, detail, account=None, evidence=''):
        self.account, self.evidence = account, str(evidence)
        super().__init__(detail)


def check_battle_watchdog(scheduler, watchdog, account, frame, label, *, mode, now=None):
    if (isinstance(frame, Image.Image) and hasattr(scheduler, 'offline_dialog_visible')
            and scheduler.offline_dialog_visible(frame) is True):
        evidence = scheduler.save_raw_evidence(frame, account, 'offline_prompt', 'pending')
        scheduler.event('account_offline', account, reason='offline_prompt', evidence=str(evidence),
                        detail='已掉线：提示确认')
        raise AccountOffline(account, str(evidence))
    watchdog.mode = mode
    focus = getattr(scheduler, '_tab_focus_generation', 0)
    backgrounded = getattr(watchdog, '_tab_focus_generation', focus) != focus
    watchdog._tab_focus_generation = focus
    observation = watchdog.observe(frame, time.monotonic() if now is None else now,
                                   backgrounded=backgrounded)
    if observation is None:
        return
    evidence = scheduler.save_raw_evidence(frame, account, label + '_stalled', 'pending')
    detail = (f"页面连续30秒无变化；本环节已等待{observation['elapsed_seconds']:.0f}秒，"
              f"画面无变化{observation['unchanged_seconds']:.0f}秒。已中止等待并保留原场未决，"
              f"不会自动重开或计胜；画面={evidence}")
    scheduler.event('combat_watchdog_timeout', account, mode=mode, battle=label,
                    evidence=str(evidence), detail=detail, **observation)
    unavailable = getattr(scheduler, '_stalled_accounts', None)
    if not isinstance(unavailable, dict):
        scheduler._stalled_accounts = unavailable = {}
    unavailable[account.name] = detail
    raise BattleStalled(detail, account, evidence)


class AccountOffline(PendingError):
    """A positive disconnect dialog ends this account's slot, preserving its actions."""
    def __init__(self, account, evidence=''):
        self.account = account
        self.evidence = evidence
        super().__init__('检测到中央你已掉线窗口，本轮跳过该账号，断点已保留')


class WelfareNotReady(PendingError):
    """Online reward time has not elapsed; this is an expected pending state."""
    pass


class LeaseBlocked(RuntimeError):
    pass


class ForegroundRuntimeLease:
    """Serialize every scheduler process that can click the shared window."""

    NAME = "Local\\HYOL_ForegroundScheduler"
    WAIT_OBJECT_0 = 0x00000000
    WAIT_ABANDONED_0 = 0x00000080
    WAIT_TIMEOUT = 0x00000102

    def __init__(self) -> None:
        self.handle = None
        self.acquired = False

    def acquire(self) -> bool:
        kernel32 = ctypes.windll.kernel32
        kernel32.CreateMutexW.restype = ctypes.c_void_p
        kernel32.WaitForSingleObject.restype = ctypes.c_uint32
        kernel32.ReleaseMutex.restype = ctypes.c_bool
        kernel32.CloseHandle.restype = ctypes.c_bool
        self.handle = kernel32.CreateMutexW(None, False, self.NAME)
        if not self.handle:
            raise OSError("CreateMutexW failed")
        result = int(kernel32.WaitForSingleObject(self.handle, 0))
        if result in (self.WAIT_OBJECT_0, self.WAIT_ABANDONED_0):
            self.acquired = True
            return True
        if result == self.WAIT_TIMEOUT:
            return False
        raise OSError(f"WaitForSingleObject failed: {result}")

    def release(self) -> None:
        if not self.handle:
            return
        kernel32 = ctypes.windll.kernel32
        if self.acquired:
            kernel32.ReleaseMutex(self.handle)
        kernel32.CloseHandle(self.handle)
        self.handle = None
        self.acquired = False


COMMAND_KINDS = (
    "recognize",
    "mail",
    "welfare",
    "arena",
    "survival",
    "team1",
    "team2",
    "market",
    "managed",
    "all_day",
    "full",
)


@dataclass(frozen=True)
class SchedulerCommand:
    kind: str
    readonly_preflight: bool = False
    arena_victory_target: int = ARENA_REQUIRED_WINS
    group_instance_count: int | None = None
    strong_approach_count: int | None = None
    current_account: str | None = None
    current_tab_x: int | None = None
    arena_additional_victories: bool = False
    generic_current_window: bool = False

    def __post_init__(self) -> None:
        if self.kind not in COMMAND_KINDS:
            raise ValueError(f"unknown scheduler command: {self.kind}")
        if (self.current_account is None) != (self.current_tab_x is None):
            raise ValueError("current_account and current_tab_x must be supplied together")
        if self.current_account is not None and (self.kind != "arena" or not 50 <= self.current_tab_x <= 1800):
            raise ValueError("current-account testing supports arena and a valid visible tab position only")
        if self.arena_additional_victories and self.kind != "arena":
            raise ValueError("additional victories require arena")
        if self.generic_current_window and (self.kind != "arena" or self.current_account is not None):
            raise ValueError("generic current-window mode supports arena without a configured account only")
        if not 1 <= self.arena_victory_target <= ARENA_MAX_REQUESTED_WINS:
            raise ValueError(f"arena_victory_target must be 1..{ARENA_MAX_REQUESTED_WINS}")
        for name, value, maximum in (
            ("group_instance_count", self.group_instance_count, GROUP_INSTANCE_DAILY_REWARD_MAX),
            ("strong_approach_count", self.strong_approach_count, STRONG_APPROACH_WEEKLY_REWARD_MAX),
        ):
            if value is not None and not 0 <= value <= maximum:
                raise ValueError(f"{name} must be 0..{maximum} or None")

    @property
    def team_index(self) -> int | None:
        if self.kind == "team1":
            return 1
        if self.kind == "team2":
            return 2
        return None


def dedupe_digit_matches(matches: Iterable[tuple[float, int, int]], min_x_gap: int = 3) -> list[tuple[float, int, int]]:
    """Keep one strongest template hit per horizontal digit position."""
    ordered = sorted(matches, key=lambda row: (-row[0], row[1], row[2]))
    accepted: list[tuple[float, int, int]] = []
    for match in ordered:
        if any(abs(match[1] - prior[1]) <= min_x_gap for prior in accepted):
            continue
        accepted.append(match)
    return sorted(accepted, key=lambda row: (row[1], row[2]))


def read_template_digits(
    frame: Image.Image,
    templates: "TemplateStore",
    asset_dir: Path,
    prefix: str,
    roi: tuple[int, int, int, int],
    threshold: float = 0.94,
    min_digits: int = 1,
    max_digits: int = 8,
) -> int:
    """Read a bounded integer using per-digit image templates.

    This intentionally rejects missing/ambiguous digits instead of guessing. A
    caller can then keep a purchase or balance check in ``pending`` safely.
    """
    found: list[tuple[float, int, int]] = []
    for digit in range(10):
        path = asset_dir / f"{prefix}{digit}.bmp"
        if not path.exists():
            raise PendingError(f"missing digit template: {path.name}")
        if prefix.startswith("market_"):
            # Market glyphs are adjacent and sit on a textured, dimmed map.
            # Use the edge matcher with a stronger edge cutoff; its bounded
            # local non-max suppression keeps repeated digits such as ``00``.
            edge_threshold = 30 if prefix == "market_balance_digit_" else 70
            variant_paths = [path, *sorted(asset_dir.glob(f"{prefix}{digit}_*.bmp"))]
            matches: list[tuple[float, int, int]] = []
            for variant in variant_paths:
                matches.extend(templates.find_all_edges(frame, variant, roi, threshold, edge_threshold=edge_threshold))
        else:
            matches = templates.find_all(frame, path, roi, threshold)
        found.extend((score, x, digit) for score, x, _y in matches)
    selected = dedupe_digit_matches(found)
    if not (min_digits <= len(selected) <= max_digits):
        raise PendingError(f"digit count outside bounds for {prefix}: {len(selected)}")
    return int("".join(str(digit) for _score, _x, digit in selected))


def edge_binary(image: Image.Image, threshold: int = 70) -> np.ndarray:
    """Return a small, translation-stable edge mask for UI text/icons."""
    gray = np.asarray(image.convert("L"), dtype=np.int16)
    dx = np.abs(np.diff(gray, axis=1, prepend=gray[:, :1]))
    dy = np.abs(np.diff(gray, axis=0, prepend=gray[:1, :]))
    return (dx + dy) > threshold


def edge_f1(reference: Image.Image, candidate: Image.Image, threshold: int = 70) -> float:
    """Compare UI glyph edges while ignoring map/background colour changes."""
    ref = edge_binary(reference, threshold)
    got = edge_binary(candidate, threshold)
    if ref.shape != got.shape:
        return 0.0
    intersection = int((ref & got).sum())
    denominator = int(ref.sum()) + int(got.sum())
    return (2.0 * intersection / denominator) if denominator else 0.0


def identity_text_mask(image: Image.Image, threshold: int = IDENTITY_TEXT_THRESHOLD) -> np.ndarray:
    """Extract the bright, low-saturation identity glyphs from a name strip.

    Grayscale NCC over the complete strip is dominated by the changing map
    texture.  The name itself is white/gray and low-saturation, so this mask
    keeps the server prefix and character name while discarding most of the
    background.
    """
    rgb = np.asarray(image.convert("RGB"), dtype=np.int16)
    mask = (
        (rgb.min(axis=2) >= threshold)
        & ((rgb.max(axis=2) - rgb.min(axis=2)) <= IDENTITY_TEXT_SATURATION)
    ).astype(np.uint8)
    # The enrolled strip is 32 px high and the glyphs start below the
    # animated map edge.  The first eight rows contain only background on the
    # current client; retaining them makes a repaint alter the identity score
    # even when the server/name text is unchanged.  Suppress that fixed
    # background band while keeping the complete glyph region for all tabs.
    if mask.shape[0] > 8:
        mask[:8, :] = 0
    return mask


def mask_f1(reference: np.ndarray, candidate: np.ndarray) -> float:
    """Return F1 overlap for two equal-sized binary masks."""
    if reference.shape != candidate.shape:
        return 0.0
    intersection = int((reference & candidate).sum())
    denominator = int(reference.sum()) + int(candidate.sum())
    return (2.0 * intersection / denominator) if denominator else 0.0


def now_iso() -> str:
    return datetime.now().astimezone().isoformat(timespec="milliseconds")


def slug(text: str) -> str:
    digest = hashlib.sha256(text.encode("utf-8")).hexdigest()[:10]
    return f"account_{digest}"


def read_config(path: Path) -> dict[str, str]:
    result: dict[str, str] = {}
    if not path.exists():
        return result
    for raw in path.read_text(encoding="utf-8-sig").splitlines():
        line = raw.strip()
        if not line or line.startswith(("#", ";")) or "=" not in line:
            continue
        key, value = line.split("=", 1)
        result[key.strip()] = value.strip()
    return result


def parse_csv(value: str) -> list[str]:
    return [item.strip() for item in value.split(",") if item.strip()]


def parse_int_csv(value: str) -> list[int]:
    return [int(item.strip()) for item in value.split(",") if item.strip()]


def choose_leader(members: Iterable[str], powers: dict[str, int]) -> str:
    ordered = list(members)
    if not ordered or any(name not in powers for name in ordered):
        raise ValueError("leader selection requires a power for every team member")
    return max(ordered, key=lambda name: (powers[name], -ordered.index(name)))


def wanted_task_list_is_empty(image: Image.Image) -> bool:
    """Recognize the tested post-wanted state without OCR or a click.

    The ROI is the area below the fixed ``任务列表`` heading. While a wanted
    task is active it contains bright green task text and many character edges;
    after the automatic chain finishes the translucent panel is nearly blank.
    """
    # The translucent left border at x=1715..1724 exposes map vegetation.
    # Actual task text starts to its right; including that border can keep an
    # already empty list pending solely because the map behind it is green.
    roi = np.asarray(image.convert("RGB"), dtype=np.int16)[480:590, 1725:1910]
    green = (roi[:, :, 1] > 110) & (roi[:, :, 1] * 100 > roi[:, :, 0] * 135) & (roi[:, :, 1] * 100 > roi[:, :, 2] * 115)
    # The panel is translucent.  Once its task row disappears, grass, rocks,
    # NPCs, or animation can still produce arbitrarily many image edges.  Edge
    # density therefore cannot be a completion requirement: it caused the
    # wanted loop to wait forever on textured maps.  The active wanted row in
    # every recorded replay contains the distinctive bright-green task text;
    # completion is accepted only after that text is absent for consecutive
    # frames while the caller independently verifies the main page.
    return int(green.sum()) <= 10


def atomic_json(path: Path, value: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary = tempfile.mkstemp(prefix=path.name+'.', suffix='.tmp', dir=path.parent)
    os.close(fd)
    temp = Path(temporary)
    with temp.open("w", encoding="utf-8", newline="\n") as handle:
        json.dump(value, handle, ensure_ascii=False, indent=2, sort_keys=True)
        handle.flush()
        os.fsync(handle.fileno())
    # Windows scanners can briefly hold a just-written file without delete sharing.
    # Retry only the atomic rename; never replay the game action that preceded it.
    for attempt in range(11):
        try:
            os.replace(temp, path)
            break
        except PermissionError:
            if attempt == 10:
                raise
            time.sleep(.1)


def image_hash(image: Image.Image) -> str:
    return hashlib.sha256(image.convert("RGB").tobytes()).hexdigest()


@functools.lru_cache(maxsize=256)
def _fast_fft_length(minimum: int) -> int:
    """Smallest 2/3/5-smooth length; padding keeps valid correlation intact."""
    best = 1 << (minimum - 1).bit_length()
    power5 = 1
    while power5 < best:
        power3 = power5
        while power3 < best:
            needed = (minimum + power3 - 1) // power3
            candidate = power3 * (1 << (needed - 1).bit_length())
            best = min(best, candidate)
            power3 *= 3
        power5 *= 5
    return best


class TemplateSpectra:
    """Bounded transforms of immutable templates, reusable across captures."""
    def __init__(self, max_bytes=64 * 1024 * 1024, max_entries=64):
        self.max_bytes = max_bytes
        self.max_entries = max_entries
        self.bytes = 0
        self.entries = OrderedDict()

    def get(self, template, shape):
        # Mutable arrays are deliberately uncached; replay callers can edit them.
        if template.flags.writeable:
            centered = template - template.mean()
            return float(np.sum(centered * centered)), np.fft.rfft2(centered[::-1, ::-1], shape)
        key = (id(template), shape)
        cached = self.entries.get(key)
        if cached is not None and cached[0] is template:
            self.entries.move_to_end(key)
            return cached[1], cached[2]
        centered = template - template.mean()
        variance = float(np.sum(centered * centered))
        transform = np.fft.rfft2(centered[::-1, ::-1], shape)
        size = template.nbytes + transform.nbytes
        if size <= self.max_bytes and self.max_entries > 0:
            while self.entries and (self.bytes + size > self.max_bytes
                                    or len(self.entries) >= self.max_entries):
                _, old = self.entries.popitem(last=False)
                self.bytes -= old[3]
            # Retain the original array so its identity cannot be recycled.
            self.entries[key] = template, variance, transform, size
            self.bytes += size
        return variance, transform


def ncc_map(image: np.ndarray, template: np.ndarray, *, prepared: dict | None = None,
            template_cache: TemplateSpectra | None = None) -> np.ndarray:
    """Return the normalized-correlation score at every valid position."""
    image = image.astype(np.float64, copy=False)
    template = template.astype(np.float64, copy=False)
    height, width = image.shape
    th, tw = template.shape
    if th > height or tw > width:
        return np.empty((0, 0), dtype=np.float64)
    shape = (_fast_fft_length(height + th - 1), _fast_fft_length(width + tw - 1))
    if template_cache is None:
        centered = template - template.mean()
        template_var = float(np.sum(centered * centered))
        template_transform = None
    else:
        template_var, template_transform = template_cache.get(template, shape)
    if template_var <= 1e-12:
        return np.full((height - th + 1, width - tw + 1), -1.0, dtype=np.float64)
    if prepared is not None:
        if prepared.get('image') is not image:
            prepared.clear()
            prepared['image'] = image
        transforms = prepared.setdefault('transforms', {})
        if shape not in transforms:
            # Bound memory even when many template sizes share a large ROI.
            if len(transforms) >= 2:
                transforms.pop(next(iter(transforms)))
            transforms[shape] = np.fft.rfft2(image, shape)
        image_transform = transforms[shape]
    else:
        image_transform = np.fft.rfft2(image, shape)
    if template_transform is None:
        template_transform = np.fft.rfft2(centered[::-1, ::-1], shape)
    correlation = np.fft.irfft2(
        image_transform * template_transform,
        shape,
    )
    numerator = correlation[th - 1 : height, tw - 1 : width]
    if prepared is not None and 'integrals' in prepared:
        integral, integral2 = prepared['integrals']
    else:
        integral = np.pad(image, ((1, 0), (1, 0))).cumsum(0).cumsum(1)
        integral2 = np.pad(image * image, ((1, 0), (1, 0))).cumsum(0).cumsum(1)
        if prepared is not None:
            prepared['integrals'] = integral, integral2
    sums = integral[th:, tw:] - integral[:-th, tw:] - integral[th:, :-tw] + integral[:-th, :-tw]
    sums2 = integral2[th:, tw:] - integral2[:-th, tw:] - integral2[th:, :-tw] + integral2[:-th, :-tw]
    variance = np.maximum(sums2 - sums * sums / (th * tw), 0)
    denominator = np.sqrt(variance * template_var)
    scores = np.full_like(numerator, -1.0)
    np.divide(numerator, denominator, out=scores, where=denominator > 1e-12)
    return scores


def ncc(image: np.ndarray, template: np.ndarray, *, prepared: dict | None = None,
        template_cache: TemplateSpectra | None = None) -> tuple[float, int, int]:
    scores = ncc_map(image, template, prepared=prepared, template_cache=template_cache)
    if not scores.size:
        return -1.0, -1, -1
    y, x = np.unravel_index(np.argmax(scores), scores.shape)
    return float(scores[y, x]), int(x), int(y)


class Win32:
    user32 = ctypes.windll.user32
    kernel32 = ctypes.windll.kernel32
    PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
    SW_RESTORE = 9
    SW_MAXIMIZE = 3
    MOUSEEVENTF_LEFTDOWN = 0x0002
    MOUSEEVENTF_LEFTUP = 0x0004
    MOUSEEVENTF_WHEEL = 0x0800
    VK_MENU = 0x12
    KEYEVENTF_KEYUP = 0x0002

    class RECT(ctypes.Structure):
        _fields_ = [("left", ctypes.c_long), ("top", ctypes.c_long), ("right", ctypes.c_long), ("bottom", ctypes.c_long)]

    @classmethod
    def title(cls, hwnd: int) -> str:
        length = cls.user32.GetWindowTextLengthW(hwnd)
        buffer = ctypes.create_unicode_buffer(length + 1)
        cls.user32.GetWindowTextW(hwnd, buffer, len(buffer))
        return buffer.value

    @classmethod
    def rect(cls, hwnd: int) -> tuple[int, int, int, int]:
        if hasattr(cls.user32, "SetThreadDpiAwarenessContext"):
            cls.user32.SetThreadDpiAwarenessContext(ctypes.c_void_p(-4))
        value = cls.RECT()
        if not cls.user32.GetWindowRect(hwnd, ctypes.byref(value)):
            raise SafetyError("GetWindowRect failed")
        return value.left, value.top, value.right, value.bottom

    @classmethod
    def pid(cls, hwnd: int) -> int:
        value = ctypes.c_ulong()
        cls.user32.GetWindowThreadProcessId(hwnd, ctypes.byref(value))
        return int(value.value)

    @classmethod
    def process_path(cls, pid: int) -> str:
        handle = cls.kernel32.OpenProcess(cls.PROCESS_QUERY_LIMITED_INFORMATION, False, pid)
        if not handle:
            return ""
        try:
            size = ctypes.c_ulong(32768)
            buffer = ctypes.create_unicode_buffer(size.value)
            if not cls.kernel32.QueryFullProcessImageNameW(handle, 0, buffer, ctypes.byref(size)):
                return ""
            return buffer.value
        finally:
            cls.kernel32.CloseHandle(handle)

    @classmethod
    def game_windows(cls) -> list[int]:
        found: list[int] = []
        callback_type = ctypes.WINFUNCTYPE(ctypes.c_bool, ctypes.c_void_p, ctypes.c_void_p)

        def callback(hwnd: int, _lparam: int) -> bool:
            if cls.user32.IsWindowVisible(hwnd) and cls.title(hwnd) == GAME_TITLE:
                path = cls.process_path(cls.pid(hwnd))
                if Path(path).name.lower() == GAME_EXE:
                    found.append(int(hwnd))
            return True

        callback_ref = callback_type(callback)
        cls.user32.EnumWindows(callback_ref, 0)
        return found

    @classmethod
    def foreground(cls) -> int:
        return int(cls.user32.GetForegroundWindow())

    @classmethod
    def game_content_rect(cls, hwnd: int) -> tuple[int, int, int, int]:
        candidates = set()
        callback_type = ctypes.WINFUNCTYPE(ctypes.c_bool, ctypes.c_void_p, ctypes.c_void_p)
        def callback(child, _):
            if cls.user32.IsWindowVisible(child):
                name = ctypes.create_unicode_buffer(256)
                cls.user32.GetClassNameW(child, name, len(name))
                if name.value == "CefBrowserWindow":
                    candidates.add(cls.rect(child))
            return True
        callback_ref = callback_type(callback)
        cls.user32.EnumChildWindows(hwnd, callback_ref, 0)
        if len(candidates) != 1:
            raise SafetyError(f"game content surface is ambiguous: {len(candidates)} visible regions")
        return candidates.pop()

    @classmethod
    def hit_test(cls, hwnd: int, x: int, y: int) -> int:
        """Bounded, read-only non-client hit test in screen coordinates."""
        result = ctypes.c_size_t()
        send = cls.user32.SendMessageTimeoutW
        send.argtypes = [ctypes.c_void_p, ctypes.c_uint, ctypes.c_size_t,
                         ctypes.c_ssize_t, ctypes.c_uint, ctypes.c_uint,
                         ctypes.POINTER(ctypes.c_size_t)]
        send.restype = ctypes.c_ssize_t
        point = (x & 0xFFFF) | ((y & 0xFFFF) << 16)
        if not send(hwnd, 0x0084, 0, point, 0x0002, 500, ctypes.byref(result)):
            raise SafetyError("window hit test timed out or failed")
        return ctypes.c_ssize_t(result.value).value

    @classmethod
    def activate(cls, hwnd: int) -> None:
        # Never restore, maximize, resize, or move the shared microclient here.
        # Repositioning the top-level window while clicking browser tabs can be
        # interpreted as a title/tab drag and has caused account disconnects.
        if cls.foreground() == hwnd:
            return
        cls.user32.SetForegroundWindow(hwnd)
        if cls.foreground() == hwnd:
            return
        # Windows can reject a foreground request when the panel (or another
        # desktop app) owns the foreground input queue.  Temporarily attach
        # only the input queues, then retry the focus request.  This changes
        # focus ordering but never restores, moves, resizes, or clicks either
        # window; the attachment is always released in the finally block.
        foreground = cls.foreground()
        foreground_pid = ctypes.c_ulong()
        foreground_thread = cls.user32.GetWindowThreadProcessId(foreground, ctypes.byref(foreground_pid))
        current_thread = cls.kernel32.GetCurrentThreadId()
        attached = bool(foreground_thread and foreground_thread != current_thread and cls.user32.AttachThreadInput(current_thread, foreground_thread, True))
        try:
            cls.user32.BringWindowToTop(hwnd)
            cls.user32.SetForegroundWindow(hwnd)
        finally:
            if attached:
                cls.user32.AttachThreadInput(current_thread, foreground_thread, False)
        if cls.foreground() == hwnd:
            return
        # Windows' foreground-lock policy can still reject both calls above
        # even on an unlocked desktop.  A press/release of Alt grants the
        # calling input queue the standard foreground transition privilege;
        # it does not move, resize, restore, or click the game window.
        cls.user32.keybd_event(cls.VK_MENU, 0, 0, 0)
        cls.user32.keybd_event(cls.VK_MENU, 0, cls.KEYEVENTF_KEYUP, 0)
        cls.user32.SetForegroundWindow(hwnd)

    @classmethod
    def click(cls, x: int, y: int, *, expected_hwnd: int | None = None,
              before_down: Callable[[], None] | None = None) -> None:
        if not cls.user32.SetCursorPos(x, y):
            raise SafetyError("SetCursorPos failed")
        # Let Flash observe the stationary cursor, then validate immediately
        # before DOWN: focus and F12 can change during the hover interval.
        time.sleep(0.15)
        if before_down is not None:
            before_down()
        if expected_hwnd is not None:
            if cls.foreground() != expected_hwnd:
                raise SafetyError("game window lost foreground during pre-click hover")
            if cls.f12_down():
                raise KeyboardInterrupt("F12 before mouse down")
        cls.user32.mouse_event(cls.MOUSEEVENTF_LEFTDOWN, 0, 0, 0, 0)
        try:
            time.sleep(0.06)
        finally:
            cls.user32.mouse_event(cls.MOUSEEVENTF_LEFTUP, 0, 0, 0, 0)

    @classmethod
    def move(cls, x: int, y: int) -> None:
        if not cls.user32.SetCursorPos(x, y):
            raise SafetyError("SetCursorPos failed before hover")

    @classmethod
    def wheel(cls, x: int, y: int, delta: int) -> None:
        if not cls.user32.SetCursorPos(x, y):
            raise SafetyError("SetCursorPos failed before wheel")
        time.sleep(0.15)
        cls.user32.mouse_event(cls.MOUSEEVENTF_WHEEL, 0, 0, ctypes.c_uint32(delta).value, 0)

    @classmethod
    def f12_down(cls) -> bool:
        return bool(cls.user32.GetAsyncKeyState(VK_F12) & 0x8000)


class JsonlLogger:
    def __init__(self, path: Path):
        self.path = path
        self.lock = threading.Lock()
        self.last_error = ""
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
        except OSError as exc:
            self.last_error = str(exc)

    def write(self, event: str, **fields: object) -> None:
        row = {"ts": now_iso(), "event": event, **fields}
        payload = json.dumps(row, ensure_ascii=False, sort_keys=True) + "\n"
        # Windows readers such as PowerShell ``Get-Content`` can briefly hold
        # an exclusive sharing lock while a file is being tailed.  Logging is
        # observational and must never abort a scheduler ``finally`` block
        # (which would otherwise skip lease release and its final summary).
        with self.lock:
            for attempt in range(3):
                try:
                    with self.path.open("a", encoding="utf-8", newline="\n") as handle:
                        handle.write(payload)
                        handle.flush()
                    return
                except OSError as exc:
                    self.last_error = str(exc)
                    if attempt == 2:
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
                        return
                    time.sleep(0.05)


class StateStore:
    def __init__(self, accounts: list[str], directory: Path | None = None, aliases: dict[str, str] | None = None,
                 *, restore_history: bool = True):
        self.directory = directory if directory is not None else STATE_DIR
        self.restore_history = restore_history
        self.aliases = aliases or {}
        self.day = date.today().isoformat()
        self.path = self.directory / f"{self.day}.json"
        self.account_paths = {name: self.directory / self.day / f"{slug(self.aliases.get(name, name))}.json" for name in accounts}
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
        self.global_state["updated_at"] = now_iso()
        atomic_json(self.path, self.global_state)

    def save_account(self, name: str) -> None:
        self.accounts[name]["updated_at"] = now_iso()
        atomic_json(self.account_paths[name], self.accounts[name])

    def module_status(self, name: str, module: str) -> str:
        return str(self.accounts[name].get("modules", {}).get(module, {}).get("status", "new"))

    def set_module(self, name: str, module: str, status: str, detail: str = "") -> None:
        modules = self.accounts[name].setdefault("modules", {})
        modules[module] = {"status": status, "detail": detail, "updated_at": now_iso()}
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
            "created_at": now_iso(),
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
        resolved_at = now_iso()
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
        actions[action] = {"status": status, "detail": detail, "updated_at": now_iso()}
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
        value["updated_at"] = now_iso()
        offers[key] = value
        self.save_account(name)


@dataclass(frozen=True)
class Account:
    index: int
    name: str
    tab_x: int

    @property
    def account_id(self) -> str:
        return self.name if re.fullmatch(r'account_[0-9a-f]{32}', self.name) else slug(self.name)

    @property
    def identity_path(self) -> Path:
        return ASSETS / "accounts" / f"{slug(self.name)}_identity.bmp"


def preserve_unknown_tab_assignments(
    rebound: list[Account], unknown_positions: set[int],
    previous_by_x: dict[int, Account], expected_names: set[str],
) -> list[Account]:
    """Keep obscured accounts at their last verified positions or fail closed."""
    result = list(rebound)
    result.extend(previous_by_x[tab_x] for tab_x in sorted(unknown_positions))
    names = [account.name for account in result]
    if len(names) != len(set(names)) or set(names) != expected_names:
        raise PendingError("unknown tabs conflict with last verified identity map")
    return result


@dataclass(frozen=True)
class Team:
    index: int
    # Existing configured teams contain three accounts.  Bond gameplay uses a
    # two-account team, so the runtime must not encode a three-member invariant
    # into the shared invitation/reconciliation code.
    members: tuple[str, ...]

    @property
    def member_count(self) -> int:
        return len(self.members)

    @property
    def member_label(self) -> str:
        return f"{self.member_count}人队伍"


@dataclass(frozen=True)
class TeamActivitySpec:
    """Describe one leader-driven team activity.

    Existing team-instance and wanted flows have activity-specific pages and
    are kept behind their tested runners.  The remaining activities use the
    same generic entry/free/difficulty/start/result/return contract as the
    disabled Q candidate.  Keeping the contract in data makes the team gate
    and the offline simulator cover every activity without duplicating the
    safety rules in five separate implementations.
    """

    key: str
    label: str
    runner: str
    prefix: str = ""
    entry_templates: tuple[str, ...] = ()
    page_anchor: str = ""
    highest_unlocked: str | None = None
    free_template: str | None = None
    start_template: str | None = None
    reward_pending: str | None = None
    result_confirm: str | None = None
    return_anchor: str | None = None
    close_template: str | None = None
    leader_start_template: str | None = None
    closed_template: str | None = None
    no_reward_template: str | None = None
    entry_navigation: str | None = None
    participate_template: str | None = None
    required_member_count: int | None = None
    reward_only: bool = False
    direct_start: bool = False
    reward_counter_prefix: str | None = None
    reward_counter_roi: tuple[int, int, int, int] | None = None
    reward_counter_max: int = 0
    require_all_member_auto: bool = False
    require_reward_confirmation: bool = False


# The leader is the only account whose catalogue, reward count, and result are
# used for activity completion. Repeated cooperative battles additionally
# switch every member tab solely to verify the visible ``取消`` auto state.
# New generic assets are intentionally named
# here without fabricating BMPs; until they are captured from the live client,
# the preflight gate keeps the activity in pending and makes no content click.
TEAM_ACTIVITY_SPECS = (
    TeamActivitySpec(
        "wanted",
        "组队通缉",
        "wanted",
        entry_templates=("daily_practice_toolbar.bmp", "daily_wanted_row.bmp", "daily_participate.bmp"),
        page_anchor="wanted_team_anchor.bmp",
    ),
    TeamActivitySpec(
        "group_instance",
        "组队副本",
        "group_instance",
        entry_templates=("team_instance_left_entry.bmp", "team_instance_participate.bmp", "team_instance_close.bmp", "team_instance_hard_card.bmp"),
        page_anchor="team_instance_close.bmp",
        reward_counter_prefix="group_instance_reward_digit_",
        reward_counter_roi=GROUP_INSTANCE_DAILY_REWARD_ROI,
        reward_counter_max=GROUP_INSTANCE_DAILY_REWARD_MAX,
        require_all_member_auto=True,
    ),
    TeamActivitySpec(
        "strong_approach",
        "强者挑战",
        "generic",
        prefix="strong_approach",
        entry_templates=("strong_approach_entry.bmp",),
        page_anchor="strong_approach_anchor.bmp",
        highest_unlocked="strong_approach_highest_unlocked.bmp",
        free_template="team_activity_free.bmp",
        start_template="team_activity_start.bmp",
        result_confirm="team_result_confirm.bmp",
        return_anchor="team_return_anchor.bmp",
        close_template="team_activity_close.bmp",
        leader_start_template="team_leader_final_start.bmp",
        closed_template="strong_approach_closed.bmp",
        direct_start=True,
        reward_counter_prefix="strong_approach_reward_digit_",
        reward_counter_roi=STRONG_APPROACH_WEEKLY_REWARD_ROI,
        reward_counter_max=STRONG_APPROACH_WEEKLY_REWARD_MAX,
        require_all_member_auto=True,
    ),
    TeamActivitySpec(
        "rescue_jinchuriki",
        "营救人柱力",
        "generic",
        prefix="rescue_jinchuriki",
        entry_templates=("rescue_jinchuriki_entry.bmp",),
        page_anchor="rescue_jinchuriki_anchor.bmp",
        highest_unlocked="rescue_jinchuriki_highest_unlocked.bmp",
        free_template="team_activity_free.bmp",
        start_template="team_activity_start.bmp",
        result_confirm="team_result_confirm.bmp",
        return_anchor="team_return_anchor.bmp",
        close_template="limited_activity_close.bmp",
        leader_start_template="team_leader_final_start.bmp",
        closed_template="rescue_jinchuriki_closed.bmp",
        entry_navigation="daily_limited",
        participate_template="daily_participate.bmp",
    ),
    TeamActivitySpec(
        "team_pvp",
        "组队竞技",
        "generic",
        prefix="team_pvp",
        entry_templates=("team_pvp_entry.bmp",),
        page_anchor="team_pvp_anchor.bmp",
        highest_unlocked="team_pvp_highest_unlocked.bmp",
        free_template="team_activity_free.bmp",
        start_template="team_activity_start.bmp",
        reward_pending="team_pvp_reward_pending.bmp",
        result_confirm="team_result_confirm.bmp",
        return_anchor="team_return_anchor.bmp",
        close_template="limited_activity_close.bmp",
        leader_start_template="team_leader_final_start.bmp",
        closed_template="team_pvp_closed.bmp",
        no_reward_template="daily_participate.bmp",
        entry_navigation="daily_limited",
        required_member_count=2,
        reward_only=True,
    ),
)
TEAM_ACTIVITY_BY_KEY = {spec.key: spec for spec in TEAM_ACTIVITY_SPECS}


@dataclass
class CombatSlot:
    """Cooperative state for one account's in-flight combat page.

    The desktop has one shared foreground window, so this is intentionally a
    round-robin state machine rather than a thread per account.  A slot may be
    in a battle while another slot is being started; every tab switch still
    passes through a positive page/identity gate.
    """

    account: Account
    mode: str
    next_index: int = 1
    running: bool = False
    complete: bool = False
    unavailable: bool = False
    result_index: int = 0
    victory_confirmed: bool = False
    defeat_confirmed: bool = False
    progress_hash: str = ""
    progress_phase: str = ""
    progress_changed_at: float = 0.0
    queue_started_at: float | None = None
    battle_watchdog: BattleWatchdog | None = None
    battle_watchdog_index: int = 0
    next_poll_at: float = 0.0
    pending_checks: int = 0
    start_confirmed: bool = False
    start_requested_at: float | None = None
    needs_prepare: bool = False


def check_combat_slot_watchdog(scheduler, slot, frame, *, now=None):
    watchdog = getattr(slot, 'battle_watchdog', None)
    if watchdog is None or getattr(slot, 'battle_watchdog_index', 0) != slot.next_index:
        watchdog = BattleWatchdog.from_config(getattr(scheduler, 'config', {}), started_at=now)
        slot.battle_watchdog = watchdog
        slot.battle_watchdog_index = slot.next_index
    check_battle_watchdog(scheduler, watchdog, slot.account, frame,
                         f'{slot.mode}_battle_{slot.next_index}', mode=slot.mode, now=now)


class TemplateStore:
    def __init__(self, threshold: float):
        self.threshold = threshold
        self.cache: dict[Path, np.ndarray] = {}
        self.variants = {"leaf_return_current.bmp":
                         ("leaf_return_hover.bmp", "leaf_return_village_current.bmp", "leaf_return_rooftop_current.bmp", "leaf_return_rooftop_second.bmp", "leaf_return_rooftop_live.bmp", "leaf_return_rotation_1.bmp", "leaf_return_rotation_2.bmp", "leaf_return_rotation_3.bmp", "leaf_return_rotation_4.bmp", "leaf_return_rotation_5.bmp")}
        self.resize_scale = (1.0, 1.0)
        self.last_match_size = None
        self.last_match_source = "template"
        self._asset_versions = {}
        self._frame = None
        self._frame_matches = {}
        self._frame_crops = {}
        self._frame_correlation = {}
        self._frame_edges = {}
        self._edge_templates = OrderedDict()
        self._template_spectra = TemplateSpectra()

    def begin_frame(self, frame: Image.Image) -> None:
        # Only capture() opts immutable live snapshots into caching. Replay
        # callers can still edit an image between matches without stale results.
        self._frame = frame
        self._frame_matches.clear()
        self._frame_crops.clear()
        self._frame_correlation.clear()
        self._frame_edges.clear()

    def _crop(self, frame, roi):
        if frame is not self._frame:
            return np.asarray(frame.crop(roi).convert("L"), dtype=np.float64)
        if roi not in self._frame_crops:
            self._frame_crops[roi] = np.asarray(frame.crop(roi).convert("L"), dtype=np.float64)
        return self._frame_crops[roi]

    @staticmethod
    def _asset_version(path):
        try:
            stat = path.stat()
            return stat.st_mtime_ns, stat.st_size
        except FileNotFoundError:
            return None

    def load(self, path: Path) -> np.ndarray:
        version = self._asset_version(path)
        if path not in self.cache or self._asset_versions.get(path) != version:
            self.cache[path] = np.asarray(Image.open(path).convert("L"), dtype=np.float64)
            self.cache[path].setflags(write=False)
            self._asset_versions[path] = version
        return self.cache[path]

    def match(self, frame: Image.Image, path: Path, roi: tuple[int, int, int, int], threshold: float | None = None) -> tuple[bool, float, int, int]:
        self.last_match_size = None
        self.last_match_source = "template"
        version = self._asset_version(path)
        if version is None:
            return False, -1.0, -1, -1
        variants = tuple((name, self._asset_version(path.with_name(name)))
                         for name in self.variants.get(path.name, ()))
        cutoff = self.threshold if threshold is None else threshold
        key = (path, version, variants, roi, cutoff, self.resize_scale)
        cacheable = frame is self._frame
        if cacheable and key in self._frame_matches:
            result, self.last_match_size, self.last_match_source = self._frame_matches[key]
            return result
        result = self._match(frame, path, roi, cutoff)
        if cacheable:
            self._frame_matches[key] = result, self.last_match_size, self.last_match_source
        return result

    def _match(self, frame, path, roi, cutoff):
        x1, y1, x2, y2 = roi
        crop = self._crop(frame, roi)
        prepared = self._frame_correlation.setdefault(roi, {}) if frame is self._frame else None
        score, x, y = ncc(crop, self.load(path), prepared=prepared,
                          template_cache=self._template_spectra)
        if score < cutoff:
            for name in self.variants.get(path.name, ()):
                variant = path.with_name(name)
                if variant.exists():
                    candidate = ncc(crop, self.load(variant), prepared=prepared,
                                    template_cache=self._template_spectra)
                    if candidate[0] > score:
                        score, x, y = candidate
        sx, sy = self.resize_scale
        # Canonical game captures can enlarge/shrink native UI independently
        # of browser chrome. Keep the original cutoff, and reject duplicates.
        if score < cutoff and (abs(sx-1) > .01 or abs(sy-1) > .01):
            original = self.load(path)
            width, height = max(2,round(original.shape[1]*sx)), max(2,round(original.shape[0]*sy))
            pad_x, pad_y = round(abs(sx-1)*frame.width), round(abs(sy-1)*frame.height)
            expanded = (max(1,x1-pad_x), max(69,y1-pad_y), min(frame.width-1,x2+pad_x), min(frame.height-1,y2+pad_y))
            if width < expanded[2]-expanded[0] and height < expanded[3]-expanded[1]:
                resized = np.asarray(Image.fromarray(original.astype(np.uint8)).resize((width,height),Image.Resampling.BILINEAR),dtype=np.float64)
                canvas = self._crop(frame, expanded)
                scores = ncc_map(canvas,resized)
                by,bx = np.unravel_index(np.argmax(scores),scores.shape)
                best = float(scores[by,bx])
                scores[max(0,by-height):by+height+1,max(0,bx-width):bx+width+1] = -1
                if best >= cutoff and float(scores.max()) < cutoff:
                    self.last_match_size = (height,width)
                    self.last_match_source = "template_scaled"
                    return True,best,expanded[0]+int(bx),expanded[1]+int(by)
        return score >= cutoff, score, x1 + x, y1 + y

    def find_all(self, frame: Image.Image, path: Path, roi: tuple[int, int, int, int], threshold: float | None = None) -> list[tuple[float, int, int]]:
        if not path.exists():
            return []
        x1, y1, x2, y2 = roi
        image = np.asarray(frame.crop(roi).convert("L"), dtype=np.float64)
        template = self.load(path)
        th, tw = template.shape
        cutoff = self.threshold if threshold is None else threshold
        results: list[tuple[float, int, int]] = []
        working = image.copy()
        while True:
            score, x, y = ncc(working, template, template_cache=self._template_spectra)
            if score < cutoff or x < 0 or y < 0:
                break
            results.append((score, x1 + x, y1 + y))
            # UI glyphs can be adjacent (for example the ``00`` in a market
            # price).  Suppress only a small neighbourhood around the peak;
            # masking the whole template width would erase the next digit.
            radius_x = max(2, min(4, tw // 3))
            radius_y = max(2, min(4, th // 3))
            left = max(0, x - radius_x)
            top = max(0, y - radius_y)
            right = min(working.shape[1], x + radius_x + 1)
            bottom = min(working.shape[0], y + radius_y + 1)
            working[top:bottom, left:right] = working.mean()
            if len(results) >= 16:
                break
        return results

    def find_all_edges(
        self,
        frame: Image.Image,
        path: Path,
        roi: tuple[int, int, int, int],
        threshold: float | None = None,
        edge_threshold: int = 18,
    ) -> list[tuple[float, int, int]]:
        """Find repeated UI glyphs using edges rather than map-dependent color."""
        if not path.exists():
            return []
        x1, y1, x2, y2 = roi
        # Market digit templates share the same ROI; avoid recomputing the
        # edge mask, image FFT and immutable template FFT for each digit.
        edge_key = (roi, edge_threshold)
        if frame is self._frame:
            image = self._frame_edges.get(edge_key)
            if image is None:
                image = edge_binary(frame.crop(roi), threshold=edge_threshold).astype(np.float64)
                self._frame_edges[edge_key] = image
        else:
            image = edge_binary(frame.crop(roi), threshold=edge_threshold).astype(np.float64)
        template_key = (path, self._asset_version(path), edge_threshold)
        template = self._edge_templates.get(template_key)
        if template is None:
            with Image.open(path) as asset:
                template = edge_binary(asset.convert("L"), threshold=edge_threshold).astype(np.float64)
            template.setflags(write=False)
            self._edge_templates[template_key] = template
            if len(self._edge_templates) > 64:
                self._edge_templates.popitem(last=False)
        else:
            self._edge_templates.move_to_end(template_key)
        th, tw = template.shape
        cutoff = self.threshold if threshold is None else threshold
        prepared = (self._frame_correlation.setdefault(('edge', *edge_key), {})
                    if frame is self._frame else None)
        scores = ncc_map(image, template, prepared=prepared,
                         template_cache=self._template_spectra)
        candidates: list[tuple[float, int, int]] = []
        for y, x in zip(*np.where(scores >= cutoff)):
            candidates.append((float(scores[y, x]), x1 + int(x), y1 + int(y)))
        results: list[tuple[float, int, int]] = []
        radius_x = max(2, min(4, tw // 3))
        radius_y = max(2, min(4, th // 3))
        for score, x, y in sorted(candidates, key=lambda row: (-row[0], row[1], row[2])):
            if any(abs(x - prior_x) <= radius_x and abs(y - prior_y) <= radius_y for _prior_score, prior_x, prior_y in results):
                continue
            results.append((score, x, y))
            if len(results) >= 16:
                break
        return sorted(results, key=lambda row: (row[1], row[2]))


class Scheduler:
    def __init__(
        self,
        config: dict[str, str],
        stage: str,
        enroll: bool,
        no_content_clicks: bool,
        event_sink: Callable[[dict[str, object]], None] | None = None,
    ):
        self.config = config
        self.account_mode = config.get("AccountMode", "tab6").strip().lower()
        self.automatic_accounts = self.account_mode == 'dynamic' or config.get('AccountDiscovery') == 'automatic'
        names = parse_csv(config.get("TabAccountNames", ""))
        tab_xs = parse_int_csv(config.get("TabXs", "180,310,445,590,730,860"))
        if self.account_mode == 'dynamic':
            names = [f'当前窗口标签{i}' for i in range(1,7)]
            tab_xs = [100+100*i for i in range(6)]
        if self.account_mode == "current":
            if not tab_xs or any(not 50 <= x <= 1800 for x in tab_xs):
                raise ValueError("current-window mode requires one or more valid TabXs")
            names = ["当前窗口"]
            self.accounts = [Account(1, names[0], sorted(set(tab_xs))[0])]
        else:
            if stage == "multi_solo":
                if not names or len(names) != len(tab_xs) or len(set(names)) != len(names) or len(set(tab_xs)) != len(tab_xs):
                    raise ValueError("multi_solo requires unique TabAccountNames and TabXs with equal counts")
            elif len(names) != 6 or len(tab_xs) != 6:
                raise ValueError("TabAccountNames and TabXs must each contain exactly six values")
            self.accounts = [Account(i + 1, name, tab_xs[i]) for i, name in enumerate(names)]
        self.by_name = {account.name: account for account in self.accounts}
        # Availability filters may remove an offline slot. Its enrolled role
        # remains a competing identity; removing that competitor can turn a
        # similar server prefix into a false positive on an online account.
        self.identity_candidates = tuple(
            Account(i + 1, name, 0)
            for i, name in enumerate(parse_csv(config.get("TabAccountNames", "")))
        ) or tuple(self.accounts)
        self.teams = [] if self.account_mode in ("current", "dynamic") else [
            Team(1, tuple(parse_csv(config.get("Team1", "")))),
            Team(2, tuple(parse_csv(config.get("Team2", "")))),
        ]
        if self.account_mode not in ("current", "dynamic") and stage != "multi_solo":
            if any(
                team.member_count not in (2, 3)
                or len(set(team.members)) != len(team.members)
                or any(name not in self.by_name for name in team.members)
                for team in self.teams
            ):
                raise ValueError("Team1 and Team2 must each contain two or three unique configured account names")
        self.stage = stage
        self.enroll = enroll
        self.no_content_clicks = no_content_clicks
        self.auto_foreground = config.get("AutoForeground", "1") == "1"
        self.rescue_enabled = config.get("RescueJinchurikiEnabled", "1") == "1"
        self.allow_purchase = config.get("AllowMarketPurchase", "0") == "1"
        self.market_currency = config.get("MarketCurrency", "coupon_only")
        self.market_max_price = config.get("MarketMaxPrice", "none")
        self.market_direct_buy_verified = config.get("MarketDirectBuyVerified", "0") == "1"
        if self.allow_purchase:
            if not self.market_direct_buy_verified:
                raise SafetyError("market purchase requires MarketDirectBuyVerified=1")
            if self.market_currency != "coupon_only":
                raise SafetyError("Python market purchase supports coupon_only only")
            if self.market_max_price != "none":
                try:
                    if int(self.market_max_price) < 0:
                        raise ValueError
                except ValueError as exc:
                    raise SafetyError("MarketMaxPrice must be none or a non-negative integer") from exc
        self.threshold = float(config.get("TemplateThreshold", "0.90"))
        self.identity_threshold = float(config.get("IdentityThreshold", "0.82"))
        self.identity_margin = float(config.get("IdentityMargin", "0.08"))
        self.identity_assignment_threshold = float(config.get("IdentityAssignmentThreshold", "0.80"))
        self.identity_assignment_margin = float(config.get("IdentityAssignmentMargin", "0.03"))
        self.templates = TemplateStore(self.threshold)
        from hyol_accounts import state_directory
        from hyol_run_context import current_run
        self.run_context = current_run()
        self.identity_directory = (self.run_context.identity_directory if self.run_context
                                   else state_directory(config, ROOT))
        self.shared_state_directory = self.run_context.directory if self.run_context else self.identity_directory
        self.state = StateStore(names, directory=self.shared_state_directory, restore_history=self.run_context is None)
        from hyol_checkpoints import register_identity_materials
        register_identity_materials(self)
        run_name = self.run_context.identifier if self.run_context else run_id()
        self.diagnostics = Diagnostics(ROOT, run_name)
        # In shadow mode, model inference is diagnostic only and cannot authorize
        # a click. Keep it opt-in for regular production runs.
        self.detector = OnnxDetector(
            ROOT / "models" / "detector.json",
            allow_shadow=config.get("EnableShadowDetection", "0") == "1",
        )
        self.last_detection = None
        self.log = JsonlLogger(LOGS / f"HYOL_Scheduler_{run_name}.jsonl")
        self.account_logs = {name: JsonlLogger(LOGS / "accounts" / f"{slug(name)}.jsonl") for name in names}
        self.stop = self.run_context.stop if self.run_context else threading.Event()
        self.hwnd = 0
        self.active: Account | None = None
        self.click_count = 0
        self.content_click_count = 0
        self._last_frame: Image.Image | None = None
        self.event_sink = event_sink
        self.pause_after_module = threading.Event()
        self.joint_identity_bound: set[str] = set()
        # None means no availability probe has run yet.  After a full rebind
        # this contains only online, healthy, positively identified accounts.
        self.available_account_names: set[str] | None = None
        self.current_account_only: str | None = None
        self.generic_current_tab_x: int | None = None
        self.command_kind: str | None = None
        self.command_started_at: str | None = None
        self.command_new_pending: list[dict[str, object]] = []
        self._full_market_cycle: str | None = None
        self.arena_victory_target = ARENA_REQUIRED_WINS
        self.arena_eligible_accounts: set[str] | None = None
        self.team_battle_targets: dict[str, int] | None = None
        self.selected_team_commands_done: set[int] = set()
        # Only invitations sent by this scheduler may be accepted by its team flow.
        self.expected_invitation_members: set[str] = set()

    def event(self, event: str, account: Account | None = None, **fields: object) -> None:
        payload = {"hwnd": self.hwnd, **fields}
        diagnostics = getattr(self, "diagnostics", None)
        if diagnostics is not None:
            payload["run_id"] = diagnostics.identifier
            diagnostics.observe(event, payload)
        if account is not None:
            payload["account"] = account.name
            payload["tab_index"] = account.index
            if re.fullmatch(r'account_[0-9a-f]{32}',account.name):
                refs = getattr(self,'_dynamic_identity_materials',{})
                payload['account_label'] = refs.get(account.name,{}).get('display_label',f'账号{account.index}')
            account_log = self.account_logs.get(account.name)
            if account_log is None:
                # Discovery observes anonymous tabs before they have a durable
                # identity. Logging those observations must not abort binding.
                account_log = JsonlLogger(LOGS / 'accounts' / (slug(account.name)+'.jsonl'))
                self.account_logs[account.name] = account_log
            account_log.write(event, **payload)
        self.log.write(event, **payload)
        if diagnostics is not None and (event in ("scheduler_stopped", "emergency_stop", "scheduler_paused") or fields.get("result") == 130):
            try:
                mark_manual_pause(ROOT, diagnostics.identifier, str(fields.get("detail") or fields.get("reason") or "manual stop"))
            except OSError as exc:
                payload["pause_record_error"] = str(exc)
        if diagnostics is not None and event in ("command_summary", "daily_summary", "bond_command_summary", "stage_pending"):
            if fields.get("result", 2) != 0:
                try:
                    report = export_from_scheduler(self, {"event": event, **payload})
                    payload["diagnostic_path"] = str(report) if report else "诊断文件写入失败"
                except Exception as exc:
                    payload["diagnostic_error"] = str(exc)
        if getattr(self.log, "last_error", ""):
            payload["log_warning"] = self.log.last_error
        if self.event_sink is not None:
            try:
                self.event_sink({"ts": now_iso(), "event": event, **payload})
            except Exception:
                # A display callback must never interrupt or alter game input.
                pass

    def request_pause_after_module(self) -> None:
        self.pause_after_module.set()
        self.event("pause_requested", mode="after_current_module")

    def check_pause_boundary(self) -> bool:
        if self.pause_after_module.is_set():
            self.event("scheduler_paused", mode="after_current_module")
            return True
        return False

    def command_summary(self, command: SchedulerCommand, status: str, result: int, detail: str = "") -> None:
        """Emit one authoritative completion record consumed by the panel."""
        pending = self.state.unresolved_pending_count(self.command_started_at or "")
        self.event(
            "command_summary",
            command=command.kind,
            status=status,
            result=result,
            pending=pending,
            clicks=self.click_count,
            content_clicks=self.content_click_count,
            detail=detail,
        )

    def start_f12_monitor(self) -> None:
        context = getattr(self, 'run_context', None)
        if context:
            if context.monitor_started:
                return
            context.monitor_started = True
        def monitor() -> None:
            was_down = False
            while not self.stop.is_set():
                down = Win32.f12_down()
                if down and not was_down:
                    if context:
                        context.interrupted = True
                    self.stop.set()
                    self.event("emergency_stop", reason="F12")
                    return
                was_down = down
                time.sleep(0.05)

        threading.Thread(target=monitor, name="F12Monitor", daemon=True).start()

    def check_stop(self) -> None:
        if self.stop.is_set():
            raise KeyboardInterrupt("F12 emergency stop")

    def check_input_pause(self) -> None:
        """Read the shared F12 record immediately before every input handoff."""
        from hyol_diagnostics import manual_pause_status
        self.check_stop()
        pause = manual_pause_status(ROOT)
        if pause['paused']:
            self.stop.set()
            self.event('manual_pause_input_blocked', pause=pause)
            raise KeyboardInterrupt('人工暂停记录仍生效，未发送游戏输入')

    def bind_window(self, wait: bool = True, timeout_seconds: float | None = None) -> None:
        if wait and timeout_seconds is None:
            timeout_seconds = 60.0
        deadline = time.monotonic() + timeout_seconds if wait and timeout_seconds is not None else None
        while True:
            self.check_stop()
            windows = Win32.game_windows()
            if len(windows) == 1:
                self.hwnd = windows[0]
                if self.validate_geometry(activate=True):
                    if getattr(self, 'run_context', None):
                        self.run_context.bind_window(self)
                    self.event("window_bound", rect=Win32.rect(self.hwnd), pid=Win32.pid(self.hwnd))
                    return
            if deadline is not None and time.monotonic() >= deadline:
                self.event("window_wait_timeout", count=len(windows), reason="expected_one_healthy_game_window")
                if len(windows) == 1:
                    hwnd = windows[0]
                    if Win32.user32.IsIconic(hwnd):
                        raise SafetyError("游戏窗口已最小化，请恢复游戏主界面后重试")
                    rect = Win32.rect(hwnd)
                    if rect != EXPECTED_WINDOW:
                        raise SafetyError(f"游戏窗口位置或尺寸不符：当前{rect}，要求{EXPECTED_WINDOW}；请恢复窗口后重试")
                    raise SafetyError("游戏窗口未取得前台焦点，请恢复游戏窗口后重试")
                raise SafetyError(f"expected one healthy game window; found {len(windows)}")
            self.event("window_wait", count=len(windows), reason="expected_one_healthy_game_window")
            if not wait:
                if len(windows) == 1:
                    if Win32.user32.IsIconic(windows[0]):
                        raise SafetyError("游戏窗口已最小化，请恢复游戏主界面后重试")
                    raise SafetyError(f"游戏窗口位置或尺寸不符：当前{Win32.rect(windows[0])}，要求{EXPECTED_WINDOW}；请恢复窗口后重试")
                raise SafetyError(f"expected one healthy game window; found {len(windows)}")
            time.sleep(3)

    def validate_geometry(self, activate: bool = False) -> bool:
        try:
            if self.hwnd not in Win32.game_windows():
                return False
            if activate and self.auto_foreground:
                Win32.activate(self.hwnd)
                time.sleep(0.4)
            if Win32.user32.IsIconic(self.hwnd):
                return False
            if getattr(self, "config", {}).get("AdaptiveGeometry", "1") == "1":
                GameGeometry(Win32.rect(self.hwnd), Win32.game_content_rect(self.hwnd))
            elif Win32.rect(self.hwnd) != EXPECTED_WINDOW:
                return False
            return not activate or Win32.foreground() == self.hwnd
        except (SafetyError, ValueError):
            return False

    def ensure_window(self) -> None:
        if getattr(self, "current_account_only", None) is not None:
            self.check_stop()
            if not self.validate_geometry(activate=True):
                raise SafetyError("当前游戏窗口不可用或尺寸发生变化，请恢复游戏窗口后再次点击任务")
            return
        deadline = time.monotonic() + 60.0
        while not self.validate_geometry(activate=True):
            self.check_stop()
            if time.monotonic() >= deadline:
                self.event("window_wait_timeout", reason="no_healthy_window_for_60_seconds")
                raise PendingError("游戏超时：60秒未恢复可用窗口，进度已保留")
            self.event("window_unavailable", reason="foreground_or_geometry")
            self.wait_interruptible(min(3.0, max(0.0, deadline - time.monotonic())))
            windows = Win32.game_windows()
            if len(windows) == 1:
                self.hwnd = windows[0]
            self.check_stop()

    def capture(self) -> Image.Image:
        self.check_stop()
        if getattr(self, 'run_context', None):
            self.run_context.check_window(self)
        # ImageGrab captures desktop coordinates, not an HWND.  A user can
        # bring Codex or another app forward during any repaint wait after the
        # last guarded click.  Never interpret that foreign foreground as a
        # game page or use it to authorize a subsequent content action.
        if not self.hwnd:
            raise SafetyError("game window is not bound before capture")
        if Win32.foreground() != self.hwnd:
            previous = Win32.foreground()
            self.event("capture_focus_reacquire", previous_foreground_hwnd=previous)
            if not self.validate_geometry(activate=True):
                foreground = Win32.foreground()
                self.event("capture_rejected", reason="game_not_foreground", foreground_hwnd=foreground)
                raise SafetyError(f"game lost foreground before capture: foreground={foreground}")
        adaptive = getattr(self, "config", {}).get("AdaptiveGeometry", "1") == "1"
        if not adaptive and Win32.rect(self.hwnd) != EXPECTED_WINDOW:
            self.event("capture_rejected", reason="geometry_changed", rect=Win32.rect(self.hwnd))
            raise SafetyError("game geometry changed before capture")
        geometry = GameGeometry(Win32.rect(self.hwnd), Win32.game_content_rect(self.hwnd)) if adaptive else GameGeometry(EXPECTED_WINDOW, VIEW)
        raw = ImageGrab.grab(bbox=geometry.window, all_screens=True).convert("RGB")
        if Win32.rect(self.hwnd) != geometry.window or (adaptive and Win32.game_content_rect(self.hwnd) != geometry.content):
            raise SafetyError("game geometry changed during capture; discard stale frame")
        frame = geometry.normalize(raw)
        frame._hyol_observations = {}
        self._capture_geometry = geometry
        self.templates.resize_scale = (1918/(geometry.content[2]-geometry.content[0]),962/(geometry.content[3]-geometry.content[1]))
        self.templates.begin_frame(frame)
        self._last_frame = frame
        self._health_checked_at = time.monotonic()
        self.check_offline_frame(frame)
        scope = getattr(self, '_task_observation', None)
        if (isinstance(scope, tuple) and self.active == scope[0]
                and not getattr(self, '_capture_binding_probe', False)
                and getattr(self, '_capture_account', None) in (None, scope[0])):
            check_battle_watchdog(self, scope[2], scope[0], frame, scope[1], mode=scope[1])
        return frame

    def begin_task_observation(self, account, task):
        """Observe only the executing account; chrome switches never reset its timer."""
        previous = getattr(self, '_task_observation', None)
        if task == 'treasure':
            # Excavation's intentional 300s goal wait is owned by its timer;
            # battle waits still have their own watchdog and offline prompts
            # remain checked by every capture.
            self._task_observation = None
            return previous
        watches = getattr(self, '_task_watchdogs', {})
        key = (account.name, task)
        watch = watches.setdefault(key, BattleWatchdog(mode=task))
        self._task_watchdogs = watches
        self._task_observation = (account, task, watch)
        return previous

    def end_task_observation(self, previous=None, *, complete=False):
        scope = getattr(self, '_task_observation', None)
        if scope and (complete or self.stop.is_set()):
            getattr(self, '_task_watchdogs', {}).pop((scope[0].name, scope[1]), None)
        self._task_observation = previous

    def offline_dialog_visible(self, frame: Image.Image) -> bool:
        # Templates identify the dialog or its exact text, never a dimmed
        # background, a missing role name or an unhealthy screen.
        return any(self.templates.match(frame, ASSETS/name, (650,350,1300,780), .98)[0]
                   for name in ('offline_login_prompt.bmp', 'offline_login_prompt_current.bmp',
                                'offline_login_text_current.bmp', 'offline_health_login_prompt_current.bmp') if (ASSETS/name).exists())

    def check_offline_frame(self, frame: Image.Image) -> bool:
        if not self.offline_dialog_visible(frame):
            return False
        account = getattr(self, '_capture_account', None) or getattr(self, 'active', None)
        if (account is None or getattr(self, '_capture_binding_probe', False)
                or getattr(self, 'no_content_clicks', False)):
            return True
        try:
            selected = self._generic_active_tab_x(frame)
        except PendingError:
            return True  # The dialog is real; its owner is not yet uniquely bound.
        bound_tab = account.tab_x
        if bound_tab == 0 and getattr(self, 'current_account_only', None) == account.name:
            bound_tab = getattr(self, '_connection_tab_x', None)
        if selected != bound_tab:
            return True
        offline = getattr(self, 'disconnected_accounts', None)
        if offline is None:
            self.disconnected_accounts = offline = set()
        if account.name not in offline:
            evidence = self.save_raw_evidence(frame, account, 'offline_dialog', 'pending')
            offline.add(account.name)
            if getattr(self, 'available_account_names', None) is not None:
                self.available_account_names.discard(account.name)
            self.state.accounts[account.name]['connection'] = {
                'status': 'disconnected', 'observed_at': now_iso(), 'evidence': str(evidence)}
            self.state.save_account(account.name)
            self.event('account_disconnected_skipped', account, evidence=str(evidence),
                       detail='中央掉线弹窗正向识别；未点击弹窗，保留未决动作')
        else:
            evidence = self.state.accounts[account.name].get('connection', {}).get('evidence', '')
        raise AccountOffline(account, str(evidence))

    def screen_point(self, x: int, y: int) -> tuple[int, int]:
        geometry = getattr(self, "_capture_geometry", None)
        if geometry is None:
            geometry = GameGeometry(Win32.rect(self.hwnd), Win32.game_content_rect(self.hwnd))
        if Win32.rect(self.hwnd) != geometry.window or Win32.game_content_rect(self.hwnd) != geometry.content:
            raise SafetyError("game geometry changed before input; discard stale target")
        return geometry.point(x, y)

    @staticmethod
    def healthy(frame: Image.Image) -> bool:
        crop = np.asarray(frame.crop(VIEW).resize((96, 48)).convert("L"), dtype=np.float64)
        return float(crop.std()) >= 8.0 and float(crop.mean()) not in (0.0, 255.0)

    def template(self, name: str, roi: tuple[int, int, int, int], frame: Image.Image | None = None, threshold: float | None = None) -> tuple[bool, float, int, int]:
        frame = frame or self.capture()
        result = self.templates.match(frame, ASSETS / name, roi, threshold)
        # The reconnect dialog has multiple live skins with identical safety
        # meaning.  Either positive template classifies the tab as offline;
        # neither path authorizes clicking the dialog.
        if name == "offline_login_prompt.bmp" and not result[0]:
            for fallback in ("offline_login_prompt_current.bmp", "offline_login_text_current.bmp"):
                current = self.templates.match(frame, ASSETS / fallback, roi, threshold)
                if current[0]:
                    result = current
                    break
        detector = getattr(self, "detector", None)
        if detector is None or detector.session is None:
            return result
        ok, score, x, y = result
        shape = self.templates.last_match_size or (self.templates.load(ASSETS / name).shape if (ASSETS / name).exists() else (0, 0))
        template = Detection(name, (x, y, x+shape[1], y+shape[0]) if ok else None, score,
                             self.templates.last_match_source, ok,
                             'missing_template' if not (ASSETS / name).exists() else '')
        predictions = []
        if detector.session is not None and name in detector.manifest.get("classes", []):
            try:
                predictions = [d for d in detector.predict(frame) if d.target == name
                               and roi[0] <= (d.box[0]+d.box[2])/2 <= roi[2]
                               and roi[1] <= (d.box[1]+d.box[3])/2 <= roi[3]]
            except Exception as exc:
                detector.error = f"{type(exc).__name__}: {exc}"
        # Shadow detections are observational and cannot override either outcome.
        fused = fuse(template, predictions, model_enabled=True) if detector.enabled_for(name) else template
        self.last_detection = fused
        if getattr(self, "diagnostics", None) is not None:
            self.diagnostics.observe("recognition", {"target": name, "roi": roi,
                "template": template.as_dict(), "model": [d.as_dict() for d in predictions],
                "decision": fused.as_dict(), "model_error": detector.error})
        if not fused.accepted:
            return False, fused.score, -1, -1
        return True, fused.score, round(fused.box[0]), round(fused.box[1])

    def active_template(
        self,
        name: str,
        roi: tuple[int, int, int, int],
        frame: Image.Image | None = None,
        threshold: float | None = None,
    ) -> tuple[bool, float, int, int]:
        """Match a control and reject the grey disabled version of it.

        The limited-activity catalogue reuses the same button outline for a
        disabled preview. Grayscale NCC therefore reports a match for both
        the orange ``参与`` control and the grey preview. Keep the normal
        match coordinates for evidence, but require a positive orange pixel
        ratio before this irreversible entry can be used.
        """
        matched, score, x, y = self.template(name, roi, frame, threshold)
        disabled_variants = {'mail_attachment_warehouse.bmp': 'mail_warehouse_empty_disabled.bmp',
                             'mail_claim_all.bmp': 'mail_claim_all_disabled.bmp',
                             'fragment_exchange_current.bmp': 'fragment_exchange_disabled_current.bmp'}
        if matched and name in disabled_variants:
            original_detection = getattr(self, 'last_detection', None)
            disabled = self.template(disabled_variants[name], roi, frame, threshold=.995)
            if disabled[0]:
                self.last_detection = Detection(name, None, score, 'classification', False, 'disabled_control')
                return False, score, x, y
            # The negative grey classification must not erase the actionable
            # model detection and thereby bypass its independent second frame.
            self.last_detection = original_detection
        if not matched or name != "daily_participate.bmp":
            return matched, score, x, y
        path = ASSETS / name
        with Image.open(path) as reference:
            width, height = reference.size
        current = np.asarray(
            (frame or self.capture()).crop((x, y, x + width, y + height)).convert("RGB"),
            dtype=np.int16,
        )
        red, green, blue = current[:, :, 0], current[:, :, 1], current[:, :, 2]
        orange = (red >= 105) & (red >= green + 18) & (red >= blue + 30)
        if float(orange.mean()) < 0.12:
            return False, score, x, y
        return True, score, x, y

    def identity_scores(self, frame: Image.Image) -> dict[str, float]:
        if isinstance(getattr(self, '_dynamic_identity_materials', None), dict):
            from hyol_checkpoints import identity_mask_scores, current_identity_mask
            return identity_mask_scores(current_identity_mask(frame, self.templates), self._dynamic_identity_materials)
        scores: dict[str, float] = {}
        from hyol_checkpoints import current_identity_mask
        current = current_identity_mask(frame, self.templates)
        candidates = {account.name: account for account in getattr(self, "identity_candidates", ())}
        candidates.update((account.name, account) for account in self.accounts)
        for account in candidates.values():
            if not account.identity_path.exists():
                scores[account.name] = -1.0
                continue
            with Image.open(account.identity_path) as reference_image:
                reference = identity_text_mask(reference_image.crop((0, 0, IDENTITY_ROI[2] - IDENTITY_ROI[0], IDENTITY_ROI[3] - IDENTITY_ROI[1])))
            # Weight the server prefix so accounts with similar suffixes are
            # not confused by a changing map background.
            full_score = mask_f1(reference, current)
            server_score = mask_f1(reference[:, :IDENTITY_SERVER_WIDTH], current[:, :IDENTITY_SERVER_WIDTH])
            scores[account.name] = 0.60 * server_score + 0.40 * full_score
        return scores

    def verify_identity(self, account: Account, frame: Image.Image | None = None) -> tuple[bool, dict[str, float]]:
        if getattr(self, 'run_context', None):
            account = self.run_context.resolve(self, account)
        frame = frame or self.capture()
        if isinstance(getattr(self, '_dynamic_identity_materials', None), dict):
            from hyol_accounts import verify_dynamic_identity
            return verify_dynamic_identity(self, account, frame)
        scores = self.identity_scores(frame)
        own = scores.get(account.name, -1.0)
        other = max((score for name, score in scores.items() if name != account.name), default=-1.0)
        strict = own >= self.identity_threshold and own - other >= self.identity_margin
        jointly_bound = (
            account.name in self.joint_identity_bound
            and (
                (own >= self.identity_assignment_threshold and own >= other)
                or (own >= 0.60 and own - other >= 0.08)
            )
            and self.tab_is_visually_active(account, frame)
        )
        return strict or jointly_bound, scores

    def tab_is_visually_active(self, account: Account, frame: Image.Image) -> bool:
        target = np.asarray(frame.crop((account.tab_x - 48, 5, account.tab_x + 48, 42)).convert("RGB"), dtype=np.float64)
        neighbors = []
        for other in self.accounts:
            if other != account:
                neighbors.append(float(np.asarray(frame.crop((other.tab_x - 48, 5, other.tab_x + 48, 42)).convert("L")).mean()))
        # Single-account/current-tab runs deliberately narrow ``accounts`` to
        # the already identity-bound account.  There is no competing tab paint
        # to compare in that mode, so the visual-active condition is vacuous.
        if not neighbors:
            return True
        return float(target.mean()) >= (sum(neighbors) / len(neighbors)) - 12.0

    def identity_visible_for_rebind(
        self,
        expected: Account | None,
        tab_x: int,
        frame: Image.Image,
    ) -> bool:
        scores = self.identity_scores(frame)
        if expected is None and max(scores.values(), default=-1.0) >= self.identity_assignment_threshold:
            return True
        if not scores:
            return False
        if expected is None:
            best_name = max(scores, key=scores.get)
            expected = self.by_name.get(best_name)
            if expected is None:
                return False
        own = scores.get(expected.name, -1.0)
        other = max((score for name, score in scores.items() if name != expected.name), default=-1.0)
        positioned = Account(expected.index, expected.name, tab_x)
        # Animated map pixels behind the identity strip can lower the full-mask
        # score while the server/name glyphs remain uniquely strongest.  The
        # account must be the current unique winner; this also safely handles
        # an externally restarted browser whose six tabs were reordered.
        return ((own >= self.identity_assignment_threshold and own >= other)
                or (own >= 0.60 and own - other >= 0.08)) and self.tab_is_visually_active(positioned, frame)

    def chrome_click(self, x: int, y: int, label: str, account: Account | None = None) -> None:
        self.check_input_pause()
        blank_tab_chrome = False
        if (label.startswith(('rebind_probe:', 'switch_tab:'))
                and self.hwnd in Win32.game_windows()
                and Win32.rect(self.hwnd) == EXPECTED_WINDOW
                and Win32.foreground() == self.hwnd):
            try:
                Win32.game_content_rect(self.hwnd)
            except SafetyError as exc:
                if 'content surface is ambiguous: 0 visible regions' in str(exc):
                    # A white tab has no CEF child, but its verified browser
                    # chrome remains usable for switching to another tab.
                    blank_tab_chrome = True
                    self.event('blank_tab_chrome_switch', account, target=label)
                else:
                    raise
        if not blank_tab_chrome:
            self.ensure_window()
        if getattr(self, "current_account_only", None) is not None:
            identity_observation = (
                getattr(self, '_identity_observation_only', False)
                and getattr(self, 'automatic_accounts', False)
                and self.no_content_clicks
                and account is not None
                and (x,y) == (account.tab_x,10)
                and label in (f'observe_account:{account.index}', 'restore_current_account_observation'))
            if identity_observation:
                pass
            elif account is None or account.name != self.current_account_only:
                raise SafetyError("single-account test forbids other accounts and unscoped chrome actions")
            elif label.startswith(("switch_tab:", "combat_switch:")) and (x, y) == (account.tab_x, 10):
                self.event("single_account_tab_switch_suppressed", account, target=label)
                return
            else:
                raise SafetyError("single-account test forbids chrome actions")
        if not (0 <= x < 1920 and 0 <= y < VIEW[1]):
            raise SafetyError(f"chrome click outside trusted toolbar: {x},{y}")
        physical_x, physical_y = ((x, y) if blank_tab_chrome else
                                  (self.screen_point(x,y) if hasattr(self,"screen_point") else (x,y)))
        for attempt in range(1, 4):
            try:
                hit = Win32.hit_test(self.hwnd, physical_x, physical_y)
                break
            except SafetyError as exc:
                if str(exc) != "window hit test timed out or failed" or attempt == 3:
                    raise
                # A busy Flash/browser thread can miss one read-only WM_NCHITTEST.
                # No input has been sent. Revalidate the window and target before
                # observing again; an unsafe hit result is never retried.
                self.event("chrome_hit_test_retry", account, target=label, attempt=attempt)
                self.wait_interruptible(.2)
                self.ensure_window()
                self.check_input_pause()
                if Win32.foreground() != self.hwnd:
                    raise SafetyError("window changed during chrome target validation") from exc
                if blank_tab_chrome:
                    if Win32.rect(self.hwnd) != EXPECTED_WINDOW:
                        raise SafetyError("window changed during blank-tab chrome validation") from exc
                elif hasattr(self, "screen_point") and self.screen_point(x, y) != (physical_x, physical_y):
                    raise SafetyError("window changed during chrome target validation") from exc
        if hit != 1:  # HTCLIENT; caption, resize edges and system buttons are unsafe.
            self.event("chrome_click_rejected", account, target=label, x=x, y=y, hit_test=hit)
            raise SafetyError(f"chrome target is not client content: hit_test={hit}")
        # The hit-test response is not a lease: recheck focus and geometry
        # immediately before sending input, without silently waiting/retrying.
        if Win32.foreground() != self.hwnd:
            raise SafetyError("window changed during chrome target validation")
        if blank_tab_chrome and Win32.rect(self.hwnd) != EXPECTED_WINDOW:
            raise SafetyError("window changed during blank-tab chrome validation")
        if not blank_tab_chrome and hasattr(self,"screen_point") and self.screen_point(x,y) != (physical_x,physical_y):
            raise SafetyError("window changed during chrome target validation")
        self.check_input_pause()
        Win32.click(physical_x, physical_y, expected_hwnd=self.hwnd,
                    before_down=self.check_input_pause)
        if account is not None and (x, y) == (account.tab_x, 10):
            focus_key = (self.hwnd, account.tab_x)
            if getattr(self, '_tab_focus_key', None) != focus_key:
                self._tab_focus_generation = getattr(self, '_tab_focus_generation', 0) + 1
                self._tab_focus_key = focus_key
        self._capture_account = account
        self._capture_binding_probe = label.startswith(('rebind_probe:', 'observe_account:'))
        self.click_count += 1
        self.event("chrome_click", account, target=label, x=x, y=y)

    def _tab_mapping_key(self) -> str:
        return "verified_solo_tab_map" if self.command_kind == "multi_solo" else "tab_map"

    def _saved_tab_mapping(self) -> dict:
        mapping = self.state.global_state.get(self._tab_mapping_key(), {})
        return mapping if isinstance(mapping, dict) else {}

    def _validate_tab_binding(self, account: Account) -> None:
        duplicate_accounts = sorted(
            other.name for other in self.accounts
            if other.name != account.name and other.tab_x == account.tab_x
        )
        mapping = self._saved_tab_mapping()
        duplicate_mappings = sorted(
            name for name, x in mapping.items()
            if name != account.name and x == account.tab_x
        )
        mapped_x = mapping.get(account.name)
        if mapped_x is not None and mapped_x != account.tab_x:
            duplicate_mappings.append(f"{account.name}:{mapped_x}")
        if not duplicate_accounts and not duplicate_mappings:
            return
        frame = getattr(self, "_last_frame", None)
        evidence = None
        if frame is not None:
            evidence = self.save_raw_evidence(frame, account, "tab_mapping_conflict", "pending")
        self.event("tab_mapping_conflict", account, tab_x=account.tab_x,
                   duplicate_accounts=duplicate_accounts,
                   duplicate_mappings=sorted(set(duplicate_mappings)),
                   mapping_source=self._tab_mapping_key(), evidence=str(evidence) if evidence else None)
        raise PendingError(f"账号标签坐标冲突，已停止切换；证据={evidence}")

    def _record_tab_binding(self, account: Account) -> None:
        self._validate_tab_binding(account)
        self.state.global_state.setdefault(self._tab_mapping_key(), {})[account.name] = account.tab_x
        self.state.save_global()

    def switch_account(self, account: Account, verify: bool = True) -> Image.Image:
        context = getattr(self, 'run_context', None)
        if context and context.binding and not context.probing:
            return context.select(self, account)
        # Click near the upper tab body rather than the close glyph.  The
        # browser recalculates tab widths as titles repaint; using the title
        # band keeps a fixed enrolled x-coordinate from landing on an adjacent
        # tab's close button during that reflow.
        self._validate_tab_binding(account)
        self.chrome_click(account.tab_x, 10, f"switch_tab:{account.name}", account)
        time.sleep(1.0)
        frame = self.capture()
        if not self.healthy(frame):
            frame = self.recover_white_tab(account)
        if self.template("offline_login_prompt.bmp", (780, 450, 1140, 660), frame, threshold=0.98)[0]:
            raise PendingError("account is offline and requires manual login; content clicks disabled")
        team_room_occluded = self.template("team_room_close.bmp", (1200, 260, 1280, 340), frame)[0]
        saved_map = self._saved_tab_mapping()
        if team_room_occluded and isinstance(saved_map, dict) and saved_map.get(account.name) == account.tab_x:
            # The verified team-room shell hides the normal identity strip.
            # The tab coordinate plus the last joint identity map is sufficient
            # to release this account to prepare_main, which closes the room
            # through its own matched X before any activity click.
            self.active = account
            self.event("account_bound_by_verified_team_room", account, mapping_source="last_verified_tab_map")
            return frame
        if (self.command_kind == "multi_solo" and isinstance(saved_map, dict)
                and saved_map.get(account.name) == account.tab_x
                and self.tab_is_visually_active(account, frame)):
            from hyol_quiz import result_visible, start_dialog_visible
            survival_page = self.template(
                'survival_anchor.bmp', (201,69,601,419), frame, threshold=.92)[0]
            if result_visible(self, frame) or start_dialog_visible(self, frame) or survival_page:
                self.active = account
                self.event("account_bound_by_verified_solo_page", account,
                           mapping_source="last_verified_tab_map")
                return frame
        if (
            self.command_kind in ("team1", "team2")
            and self.template(TEAM_BATTLE_START_TEMPLATE, TEAM_BATTLE_START_ROI, frame, threshold=0.90)[0]
            and isinstance(saved_map, dict)
            and saved_map.get(account.name) == account.tab_x
        ):
            # A team battle hides the identity strip.  Only a team command,
            # the last complete tab map, and the positively matched enrolled
            # start control may release the leader tab.  The activity runner
            # still decides whether the one-time start can be clicked.
            self.active = account
            self.event("account_bound_by_verified_team_battle", account, mapping_source="last_verified_tab_map")
            return frame
        if self.command_kind in ("team1", "team2") and isinstance(saved_map, dict) and saved_map.get(account.name) == account.tab_x:
            generic_activity = next(
                (
                    spec.key
                    for spec in TEAM_ACTIVITY_SPECS
                    if spec.runner == "generic"
                    and self._team_activity_visible(spec, frame)
                ),
                None,
            )
            if generic_activity:
                # Generic activity pages also hide the identity strip. The
                # last complete team map plus the exact page anchor is the
                # only allowed identity substitute; the activity state
                # machine still controls every content click.
                self.active = account
                self.event(
                    "account_bound_by_verified_team_activity",
                    account,
                    activity=generic_activity,
                    mapping_source="last_verified_tab_map",
                )
                return frame
        if verify:
            ok, scores = self.verify_identity(account, frame)
            # A shared Flash tab can still be repainting for one frame after a
            # browser-tab switch.  Re-sample only this non-mutating identity
            # check; never repeat a content click or an irreversible action.
            # Give a shared Flash tab a bounded repaint window before declaring
            # the identity ambiguous. This is a read-only check and cannot
            # duplicate a content action.
            for _ in range(10):
                # A low but non-zero score is often a stale repaint and can
                # even favour another same-server account.  Wait for the
                # requested account's strict margin, rather than accepting any
                # non-empty text as proof of identity.
                if ok:
                    break
                self.wait_interruptible(0.8)
                frame = self.capture()
                if self.template("offline_login_prompt.bmp", (780, 450, 1140, 660), frame, threshold=0.98)[0]:
                    raise PendingError("account is offline and requires manual login; content clicks disabled")
                ok, scores = self.verify_identity(account, frame)
            if not ok:
                if self.template("start_game_button.bmp", (701, 569, 1251, 869), frame)[0]:
                    raise PendingError("account is at the saved-session Start Game page; automatic start is disabled")
                if not self.no_content_clicks and self.tab_is_visually_active(account, frame):
                    self.active = account
                    if self.recover_known_page(account, frame):
                        frame = self.capture()
                        ok, scores = self.verify_identity(account, frame)
                if not ok:
                    raise PendingError(f"identity mismatch scores={scores}")
            if not self.tab_is_visually_active(account, frame):
                raise PendingError("active-tab visual gate failed")
        self.active = account
        self._record_tab_binding(account)
        self.event("account_bound", account, frame_hash=image_hash(frame))
        if not self.no_content_clicks:
            self.dismiss_unsolicited_team_invite(account, frame)
        return frame

    def dismiss_unsolicited_team_invite(self, account: Account, frame: Image.Image | None = None) -> bool:
        """Decline a positively identified invitation outside our own invite flow."""
        expected = getattr(self, "expected_invitation_members", None)
        if not isinstance(expected, set):
            return False
        if self.no_content_clicks or account.name in expected:
            return False
        frame = frame or self.capture()
        question_roi = (500, 300, 1400, 800)
        question = "team_invite_question_current.bmp"
        button = "team_invite_decline_no_current.bmp"
        found, _, qx, qy = self.template(question, question_roi, frame, threshold=.95)
        if not found:
            return False
        # Both controls must belong to the same modal, at the spacing shown
        # by the enrolled invitation. A generic blue "No" is insufficient.
        no_roi = (qx + 143, qy + 28, qx + 259, qy + 76)
        no_found, _, nx, ny = self.template(button, no_roi, frame, threshold=.95)
        if not no_found or abs(nx - (qx + 149)) > 6 or abs(ny - (qy + 34)) > 6:
            raise PendingError("邀请弹窗已识别，但否按钮位置不符；未点击")
        identity_ok = (self.generic_current_tab_x is not None
                       or self.verify_identity(account, frame)[0])
        if (self.active != account or not self.tab_is_visually_active(account, frame)
                or not identity_ok):
            raise PendingError("邀请弹窗账号身份未确认；未点击")
        if not self.click_template(account, button, no_roi, 50, 17,
                                   "unsolicited_team_invite_decline", [question],
                                   threshold=.95, anchor_threshold=.95):
            raise PendingError("邀请弹窗否按钮消失；未点击")
        if not self.wait_absent(question, question_roi, 3, threshold=.95):
            raise PendingError("邀请弹窗点击否后仍在；未重复点击")
        self.event("unsolicited_team_invite_declined", account)
        return True

    def recover_white_tab(self, account: Account) -> Image.Image:
        unhealthy = 1
        frame = self.capture()
        for _ in range(2):
            time.sleep(1)
            frame = self.capture()
            if self.healthy(frame):
                return frame
            unhealthy += 1
        evidence = self.save_raw_evidence(frame, account, "white_tab_confirmed", "pending")
        self.event("white_tab_confirmed", account, consecutive=unhealthy, evidence=str(evidence))
        if self.config.get("AutoRefreshWhiteTab", "0") != "1":
            raise PendingError("white tab confirmed; automatic refresh disabled to preserve the saved login session")
        ok, score, x, y = self.templates.match(frame, ASSETS / "refresh_button.bmp", (1250, 0, 1410, 69))
        if not ok:
            raise PendingError("white tab confirmed but trusted refresh button is missing")
        self.event("template_match", account, template="refresh_button.bmp", score=score, x=x, y=y)
        self.chrome_click(x + 33, y + 14, f"refresh_white_tab:{account.name}", account)
        for _ in range(20):
            self.wait_interruptible(1)
            frame = self.capture()
            if self.healthy(frame):
                self.event("white_tab_recovered", account, method="single_refresh")
                return frame
        raise PendingError("white tab remained unhealthy after one trusted refresh; saved-account recovery assets unavailable")

    def recover_known_page(self, account: Account, frame: Image.Image) -> bool:
        # Close the activity catalogue before releasing the shared-tab lease.
        # Its translucent overlay leaves both generic main-page anchors visible,
        # so the X itself must be positively identified; fixed coordinates alone
        # are not sufficient and can drag the client when the page is absent.
        if self.template("offline_login_prompt.bmp", (780, 450, 1140, 660), frame, threshold=0.98)[0]:
            raise PendingError("offline login prompt requires manual login")
        from hyol_managed import stopped_panel_visible
        if stopped_panel_visible(self, frame):
            if not self.click_template(account, 'managed_settings_close_current.bmp',
                                       (1320,230,1450,330), 18,18,
                                       'managed_settings_close_recover',
                                       ['managed_settings_title_current.bmp'],
                                       threshold=.98, anchor_threshold=.98):
                return False
            if not self.wait_absent('managed_settings_title_current.bmp',
                                    (750,190,1170,290), 5, threshold=.98):
                return False
            return self.wait_until_main(8)
        if self.template("home_leave.bmp", (1800, 930, 1920, 1032), frame)[0]:
            if self.click_template(
                account,
                "home_leave.bmp",
                (1800, 930, 1920, 1032),
                15,
                17,
                "recover_close_home",
                ["home_leave.bmp"],
            ):
                return self.wait_until_main(8)
            return False
        if self.template("market_dialog_close_current.bmp", (1100, 350, 1250, 500), frame, threshold=0.88)[0]:
            if self.click_template(
                account,
                "market_dialog_close_current.bmp",
                (1100, 350, 1250, 500),
                25,
                25,
                "recover_close_market_dialog",
                ["market_dialog_close_current.bmp"],
                threshold=0.88,
            ):
                return self.wait_until_main(6)
            return False
        if (self.template("arena_matching_anchor.bmp", (601, 349, 1201, 751), frame)[0]
                or self._arena_waiting_battle_visible(frame)):
            # A matching modal can leave the arena page visible underneath;
            # never click the underlying arena X while the modal is present.
            return False
        if (self.template("battle_auto_off.bmp", BATTLE_AUTO_ROI, frame)[0]
                or self._battle_auto_on_visible(frame) or self._battle_hud_visible(frame)):
            # Battle controls are evidence that the battle is still active;
            # never click an arena-page close button underneath them.
            return False
        if self.template("arena_rankup_confirm.bmp", (801, 600, 1101, 760), frame)[0]:
            # Rank-up confirmation likewise sits above the arena page.  It is
            # handled by the arena result loop, never by a background close.
            return False
        if self.template("arena_defeat_confirm.bmp", (801, 580, 1101, 760), frame)[0]:
            return False
        for spec in TEAM_ACTIVITY_SPECS:
            if spec.runner != "generic":
                continue
            if self._team_activity_visible(spec, frame):
                # A generic team result/return page must be resumed by its
                # leader-only state machine.  Closing it here could release
                # the lease without proving the result or duplicate a start.
                return False
        if self.template("survival_reset_confirm_yes.bmp", (701, 469, 1151, 719), frame, threshold=0.92)[0]:
            # The modal blocks the background X.  Only the survival reset
            # state machine may decide whether the audited Yes is retryable.
            return False
        if self.template("survival_reward_result_confirm.bmp", (701, 569, 1151, 819), frame)[0]:
            # The reward result blocks every control on the survival page.
            # Its audited close and any interrupted reset reconciliation belong
            # exclusively to the survival state machine.
            return False
        if self.template("team_member_reduced_confirm.bmp", (750, 450, 1180, 700), frame, threshold=0.96)[0]:
            # The wanted auto-chain can finish after a member disconnects and
            # leave this blocking notice above the leader's main page. Close
            # only the positively matched notice; membership reconciliation
            # remains the team state machine's responsibility.
            if self.click_template(
                account,
                "team_member_reduced_confirm.bmp",
                (750, 450, 1180, 700),
                51,
                20,
                "team_member_reduced_confirm",
                ["team_member_reduced_confirm.bmp"],
                threshold=0.96,
            ):
                if not self.wait_absent("team_member_reduced_confirm.bmp", (750, 450, 1180, 700), 5, threshold=0.96):
                    return False
                self.event("team_member_reduced_notice_closed", account)
                return self.wait_until_main(5)
            return False
        if self.template("wanted_claim_task.bmp", (600, 650, 1350, 780), frame)[0]:
            # The wanted auto-chain can leave its task catalogue open even
            # after the task list becomes empty.  This variant hides the
            # normal daily-page anchor, so use the catalogue's own task-card
            # proof and the narrowly bounded close-X ROI.  The live render is
            # 0.878 against the enrolled X, hence the local 0.86 cutoff; no
            # fixed-coordinate close is allowed without both proofs.
            if self.click_template(
                account,
                "daily_practice_close.bmp",
                (1300, 280, 1400, 390),
                20,
                20,
                "recover_close_wanted_task_catalogue",
                ["wanted_claim_task.bmp", "daily_practice_close.bmp"],
                threshold=0.86,
            ):
                return self.wait_until_main(8)
            return False
        if self.template("daily_practice_anchor.bmp", (951, 119, 1501, 369), frame)[0]:
            if self.click_template(
                account,
                "daily_practice_close.bmp",
                (1360, 190, 1470, 290),
                20,
                20,
                "recover_close_daily_practice",
                ["daily_practice_anchor.bmp", "daily_practice_close.bmp"],
            ):
                return self.wait_until_main(6)
            return False
        if self.template("arena_ready_anchor.bmp", (851, 199, 1451, 449), frame)[0]:
            if not self.click_template(
                account, "survival_close.bmp", (1550, 60, 1700, 200), 20, 20,
                "recover_close_arena", ["arena_ready_anchor.bmp", "survival_close.bmp"], threshold=0.84, anchor_threshold=0.84,
            ):
                return False
            return self.wait_until_main(6)
        for template, roi, close_template, close_roi, label in (
            ("welfare_close.bmp", (1301, 219, 1451, 369), "welfare_close.bmp", (1301, 219, 1451, 369), "recover_close_welfare"),
            ("welfare_online_anchor.bmp", (451, 319, 751, 469), "welfare_close.bmp", (1301, 219, 1451, 369), "recover_close_welfare"),
        ):
            if self.template(template, roi, frame)[0]:
                if not self.click_template(account, close_template, close_roi, 20, 20, label, [template, close_template]):
                    return False
                return self.wait_until_main(6)
        if self.template("mail_warehouse_anchor.bmp", (801, 199, 1101, 389), frame)[0]:
            if not self.click_template(account, "mail_warehouse_close.bmp", (1174, 259, 1275, 359), 20, 20, "recover_close_mail_warehouse", ["mail_warehouse_anchor.bmp", "mail_warehouse_close.bmp"]):
                return False
            time.sleep(1)
            next_frame = self.capture()
            if not self.template("mail_ready_anchor.bmp", (801, 199, 1101, 369), next_frame)[0]:
                return False
            if self.click_template(account, "mail_close.bmp", (1301, 249, 1401, 349), 20, 20, "recover_close_mail", ["mail_ready_anchor.bmp", "mail_close.bmp"]):
                return self.wait_until_main(6)
            return False
        if self.template("mail_ready_anchor.bmp", (801, 199, 1101, 369), frame)[0]:
            if self.click_template(account, "mail_close.bmp", (1301, 249, 1401, 349), 20, 20, "recover_close_mail", ["mail_ready_anchor.bmp", "mail_close.bmp"]):
                return self.wait_until_main(6)
            return False
        if self.template("market_anchor.bmp", (651, 69, 1251, 319), frame)[0]:
            from hyol_navigation import close_black_market
            return close_black_market(self, account, frame=frame)
        # Market navigation can fail after the world-map or South Konoha
        # screen has already been opened.  Leaving that map visible and then
        # switching tabs is unsafe: the next identity probe is no longer on a
        # verified main page and may quarantine an otherwise healthy account.
        # The world-map toolbar button is the only enrolled toggle for both
        # map layers; click it only when the matching map anchor is present,
        # then require the normal main-page gate before releasing the lease.
        for map_anchor, map_roi, label in (
            ("leaf_map_south.bmp", (851, 519, 1101, 819), "recover_close_leaf_map"),
            ("world_map_training_field.bmp", (751, 719, 1051, 1031), "recover_close_world_map"),
        ):
            if self.template(map_anchor, map_roi, frame)[0]:
                if self.click_template(
                    account,
                    "world_map_button.bmp",
                    (1769, 69, 1839, 169),
                    7,
                    7,
                    label,
                    [map_anchor],
                ):
                    return self.wait_until_main(8)
                return False
        if self.template("survival_anchor.bmp", (201, 69, 601, 419), frame)[0]:
            if self.click_template(account, "survival_close.bmp", (1585, 77, 1685, 177), 20, 20, "recover_close_survival", ["survival_anchor.bmp", "survival_close.bmp"]):
                return self.wait_until_main(6)
            return False
        if self.template("friends_panel_close.bmp", (1870, 260, 1920, 350), frame)[0]:
            if not self.click_template(
                account,
                "friends_panel_close.bmp",
                (1870, 260, 1920, 350),
                20,
                20,
                "recover_close_friends_panel",
                ["friends_panel_close.bmp"],
            ):
                return False
            if not self.wait_absent("friends_panel_close.bmp", (1870, 260, 1920, 350), 5):
                return False
            return self.wait_until_main(6)
        return False

    def enroll_identities(self) -> list[Image.Image]:
        ASSETS.joinpath("accounts").mkdir(parents=True, exist_ok=True)
        frames: list[Image.Image] = []
        for account in self.accounts:
            self.check_stop()
            frame = self.switch_account(account, verify=False)
            # A tab can be visually healthy while its Flash scene is still a
            # blank/transition frame.  Never overwrite a known identity asset
            # from that frame: wait for a non-empty identity strip and the
            # selected-tab chrome gate before persisting the crop.
            identity_ready = False
            for _ in range(12):
                mask = identity_text_mask(frame.crop(IDENTITY_ROI))
                if int(mask.sum()) >= 20 and self.tab_is_visually_active(account, frame):
                    identity_ready = True
                    break
                self.wait_interruptible(0.5)
                frame = self.capture()
            if not identity_ready:
                raise PendingError(f"identity strip did not stabilize while enrolling {account.name}")
            frames.append(frame)
            evidence = EVIDENCE_DIR / date.today().isoformat() / "enroll"
            evidence.mkdir(parents=True, exist_ok=True)
            frame.save(evidence / f"tab_{account.index}_{slug(account.name)}.png")
            frame.crop(IDENTITY_ROI).save(account.identity_path)
            self.event("identity_enrolled", account, roi=IDENTITY_ROI, path=str(account.identity_path))
        self.templates.cache.clear()
        matrix: dict[str, dict[str, float]] = {}
        for source, frame in zip(self.accounts, frames):
            matrix[source.name] = self.identity_scores(frame)
        atomic_json(EVIDENCE_DIR / date.today().isoformat() / "identity_matrix.json", matrix)
        return frames

    def rebind_tabs_by_identity(
        self,
        scope_accounts: Iterable[str] | None = None,
        skip_unavailable: bool = False,
        verified_only: bool = False,
        absent_positions: Iterable[int] = (),
    ) -> None:
        all_positions = sorted(account.tab_x for account in self.accounts)
        previous_by_x = {account.tab_x: account for account in self.accounts}
        saved_map = self._saved_tab_mapping()
        saved_map_valid = False
        if isinstance(saved_map, dict) and set(saved_map) == set(self.by_name):
            saved_by_x: dict[int, Account] = {}
            try:
                for name, raw_tab_x in saved_map.items():
                    tab_x = int(raw_tab_x)
                    configured = self.by_name[name]
                    # The saved map describes the account's current physical
                    # tab position, which may differ from the configured
                    # order after the user drags tabs.  Do not reuse the
                    # configured Account object with its stale tab_x; doing so
                    # can duplicate a coordinate when preserving an
                    # out-of-scope account during a scoped team rebind.
                    saved_by_x[tab_x] = Account(configured.index, configured.name, tab_x)
            except (KeyError, TypeError, ValueError):
                saved_by_x = {}
            saved_map_valid = (
                len(saved_by_x) == len(self.accounts)
                and set(saved_by_x) == set(all_positions)
                and len({account.name for account in saved_by_x.values()}) == len(self.accounts)
            )
            if saved_map_valid:
                # The last successful joint bind is stronger than the
                # configured order when a user has dragged tabs around.
                previous_by_x = saved_by_x
        scope_names = set(scope_accounts) if scope_accounts is not None else None
        if scope_names is not None:
            if not scope_names or not scope_names.issubset({account.name for account in previous_by_x.values()}):
                raise PendingError("scoped identity rebind requires known configured accounts")
            if not saved_map_valid:
                raise PendingError("scoped identity rebind requires a complete previously verified tab map")
            positions = sorted(tab_x for tab_x, account in previous_by_x.items() if account.name in scope_names)
        else:
            positions = all_positions
        tab_index_by_x = {tab_x: index for index, tab_x in enumerate(all_positions, 1)}
        frames: list[tuple[int, Image.Image]] = []
        offline_positions: set[int] = set()
        unavailable_positions: set[int] = set()
        missing_slot_positions: set[int] = set()
        occluded_positions: dict[int, str] = {}
        rebind_evidence = EVIDENCE_DIR / date.today().isoformat() / "identity_rebind" / run_id()
        rebind_evidence.mkdir(parents=True, exist_ok=True)
        absent_positions = set(absent_positions)
        last_chrome_frame = None
        for index, tab_x in enumerate(positions, 1):
            actual_index = tab_index_by_x[tab_x]
            if tab_x in absent_positions:
                unavailable_positions.add(tab_x)
                missing_slot_positions.add(tab_x)
                self.event("identity_rebind_missing_tab_skipped", tab_index=actual_index,
                           tab_x=tab_x, reason="requested_account_tab_absent")
                continue
            probe = Account(actual_index, f"probe_{actual_index}", tab_x)
            try:
                chrome_frame = self.capture()
                last_chrome_frame = chrome_frame
            except SafetyError as exc:
                if ('content surface is ambiguous: 0 visible regions' not in str(exc)
                        or not skip_unavailable):
                    raise
                # The previous tab may be white with no CEF child. Its
                # browser chrome and enrolled tab positions still exist.
                chrome_frame = last_chrome_frame
            multi_ok, _multi_score, multi_x, _multi_y = (
                self.template("microclient_account_multi.bmp", (0, 0, 1100, 35),
                              chrome_frame, threshold=0.92)
                if chrome_frame is not None else (False, 0., 0, 0)
            )
            if multi_ok:
                with Image.open(ASSETS / "microclient_account_multi.bmp") as multi_asset:
                    multi_width = multi_asset.width
                if multi_x <= tab_x <= multi_x + multi_width:
                    if not skip_unavailable:
                        raise PendingError(f"configured tab position {actual_index} is missing; account-multi control occupies x={tab_x}")
                    unavailable_positions.add(tab_x)
                    missing_slot_positions.add(tab_x)
                    if chrome_frame is not None:
                        chrome_frame.save(rebind_evidence / f"tab_{actual_index}_x{tab_x}_missing.png")
                    self.event(
                        "identity_rebind_missing_tab_skipped",
                        tab_index=actual_index,
                        tab_x=tab_x,
                        reason="account_multi_control_occupies_configured_slot",
                    )
                    continue
            self.chrome_click(tab_x, 10, f"rebind_probe:{actual_index}")
            time.sleep(1.0)
            # A tab swap briefly removes the CEF content child on this client.
            # Keep the probe read-only until the unique surface reappears.
            for surface_retry in range(12):
                try:
                    frame = self.capture()
                    break
                except SafetyError as exc:
                    if 'content surface is ambiguous: 0 visible regions' not in str(exc):
                        raise
                    self.event('identity_rebind_surface_wait', tab_index=actual_index,
                               tab_x=tab_x, attempt=surface_retry + 1)
                    self.wait_interruptible(.5)
            else:
                self.event('identity_rebind_surface_pending', tab_index=actual_index,
                           tab_x=tab_x)
                unavailable_positions.add(tab_x)
                continue
            # Wait through transient Flash/map repaints. This is a read-only
            # identity probe and cannot duplicate a game action; the bounded
            # ten-sample window is roughly eight seconds at 0.8s intervals.
            for _ in range(10):
                if self.template("offline_login_prompt.bmp", (780, 450, 1140, 660), frame, threshold=0.98)[0]:
                    offline_positions.add(tab_x)
                    self.event("identity_rebind_offline", tab_index=actual_index, tab_x=tab_x)
                    break
                if self.identity_visible_for_rebind(None, tab_x, frame):
                    break
                self.wait_interruptible(0.8)
                frame = self.capture()
            if tab_x in offline_positions:
                frame.save(rebind_evidence / f"tab_{actual_index}_x{tab_x}_offline.png")
                continue
            # Some activity pages (notably Arena) hide the normal top-left
            # identity strip.  If the tab is otherwise healthy and the
            # position was previously assigned, close only a positively
            # identified, reversible page X before scoring identity again.
            # This keeps rebind fail-closed while allowing a later command to
            # recover a verified Arena/market/survival page instead of
            # quarantining a healthy tab solely because its identity is
            # occluded.  ``--no-content-clicks`` explicitly disables this
            # recovery path.
            if (
                saved_map_valid
                and self.template("survival_reset_confirm_yes.bmp", (701, 469, 1151, 719), frame, threshold=0.92)[0]
            ):
                expected = previous_by_x[tab_x]
                occluded_positions[tab_x] = "survival_reset_confirm"
                frames.append((tab_x, frame))
                self.event(
                    "identity_rebind_occluded_page",
                    expected,
                    tab_index=actual_index,
                    tab_x=tab_x,
                    mode="survival_reset_confirm",
                    mapping_source="last_verified_tab_map",
                )
                continue
            if saved_map_valid and self.template("team_room_close.bmp", (1200, 260, 1280, 340), frame)[0]:
                expected = previous_by_x[tab_x]
                occluded_positions[tab_x] = "team_room"
                frames.append((tab_x, frame))
                self.event(
                    "identity_rebind_occluded_page",
                    expected,
                    tab_index=actual_index,
                    tab_x=tab_x,
                    mode="team_room",
                    mapping_source="last_verified_tab_map",
                )
                continue
            prior_active = self.active
            if not self.no_content_clicks and max(self.identity_scores(frame).values(), default=-1.0) < 0.20:
                expected = previous_by_x[tab_x]
                self.active = expected
                try:
                    recovered = self.recover_known_page(expected, frame)
                except (PendingError, SafetyError) as exc:
                    recovered = False
                    self.event("identity_rebind_recovery_rejected", tab_index=actual_index, tab_x=tab_x, detail=str(exc))
                if recovered:
                    self.event("identity_rebind_recovered_page", expected, tab_index=actual_index, tab_x=tab_x)
                    frame = self.capture()
                    # Closing a known page can leave one or two repaint frames
                    # with an empty identity strip.  Give the main page a
                    # second bounded observation window before classifying it
                    # as unknown.
                    for _ in range(10):
                        if self.identity_visible_for_rebind(None, tab_x, frame):
                            break
                        self.wait_interruptible(0.8)
                        frame = self.capture()
                else:
                    self.active = prior_active
            identity_visible = self.identity_visible_for_rebind(None, tab_x, frame)
            if (
                not identity_visible
                and not self.healthy(frame)
                and saved_map_valid
                and self.config.get("AutoRefreshWhiteTab", "0") == "1"
            ):
                # Rebind used to reject a white CEF/Flash tab before it could
                # reach ``recover_white_tab``.  A one-time refresh is safe
                # only for a tab position covered by the last complete map;
                # the refreshed page is still required to pass the normal
                # identity/offline gates below before any content click.
                expected = previous_by_x[tab_x]
                self.active = expected
                self.event("identity_rebind_white_tab_refresh", expected, tab_index=actual_index, tab_x=tab_x)
                frame = self.recover_white_tab(expected)
                if self.template("offline_login_prompt.bmp", (780, 450, 1140, 660), frame, threshold=0.98)[0]:
                    offline_positions.add(tab_x)
                    frame.save(rebind_evidence / f"tab_{actual_index}_x{tab_x}_offline_after_refresh.png")
                    continue
                identity_visible = self.identity_visible_for_rebind(None, tab_x, frame)
            if not identity_visible:
                if not self.healthy(frame):
                    if skip_unavailable:
                        unavailable_positions.add(tab_x)
                        frame.save(rebind_evidence / f"tab_{actual_index}_x{tab_x}_unhealthy.png")
                        self.event("identity_rebind_unavailable_skipped", tab_index=actual_index, tab_x=tab_x, reason="unhealthy")
                        continue
                    raise PendingError(f"tab position {actual_index} is unhealthy during identity rebind")
                # A running arena/survival scene intentionally hides the
                # identity strip.  Preserve only a previously verified
                # position-to-account mapping; an arbitrary active page is
                # still pending and is never assigned by guesswork.
                active_mode = next(
                    (candidate for candidate in ("arena", "survival") if self._combat_active_visible(candidate, frame)),
                    None,
                )
                if active_mode is None and self.command_kind == "multi_solo" and saved_map_valid:
                    from hyol_quiz import result_visible, start_dialog_visible
                    if result_visible(self, frame):
                        active_mode = "quiz_result"
                    elif start_dialog_visible(self, frame):
                        active_mode = "quiz_start_dialog"
                    elif self.template('survival_anchor.bmp', (201,69,601,419), frame, threshold=.92)[0]:
                        active_mode = "survival_page"
                if active_mode is None and self.command_kind in ("team1", "team2") and saved_map_valid:
                    # Team activity and its battle lobby can hide the normal
                    # identity strip. A scoped team command may preserve the
                    # last verified position only when an enrolled activity
                    # page or the enrolled leader battle control is visible.
                    if self._team_battle_start_visible(frame):
                        active_mode = "team_battle"
                    else:
                        activity = next(
                            (
                                spec.key
                                for spec in TEAM_ACTIVITY_SPECS
                                if spec.runner == "generic" and self._team_activity_visible(spec, frame)
                            ),
                            None,
                        )
                        if activity:
                            active_mode = f"team_activity:{activity}"
                if active_mode and saved_map_valid:
                    expected = previous_by_x[tab_x]
                    self.active = prior_active
                    occluded_positions[tab_x] = active_mode
                    frames.append((tab_x, frame))
                    self.event(
                        "identity_rebind_occluded_page",
                        expected,
                        tab_index=actual_index,
                        tab_x=tab_x,
                        mode=active_mode,
                        mapping_source="last_verified_tab_map",
                    )
                    continue
                self.event("identity_rebind_pending", tab_index=actual_index, tab_x=tab_x, reason="identity_strip_missing")
                frame.save(rebind_evidence / f"tab_{actual_index}_x{tab_x}_unknown.png")
                if skip_unavailable:
                    unavailable_positions.add(tab_x)
                    self.event("identity_rebind_unavailable_skipped", tab_index=actual_index, tab_x=tab_x, reason="identity_unknown")
                    continue
                raise PendingError(f"identity rebind could not verify tab x={tab_x}; page state is unknown")
            if not self.healthy(frame):
                if skip_unavailable:
                    unavailable_positions.add(tab_x)
                    frame.save(rebind_evidence / f"tab_{actual_index}_x{tab_x}_unhealthy.png")
                    self.event("identity_rebind_unavailable_skipped", tab_index=actual_index, tab_x=tab_x, reason="unhealthy_after_identity")
                    continue
                raise PendingError(f"tab position {actual_index} is unhealthy during identity rebind")
            frame.save(rebind_evidence / f"tab_{actual_index}_x{tab_x}.png")
            frames.append((tab_x, frame))
        visible_frames = [(tab_x, frame) for tab_x, frame in frames if tab_x not in occluded_positions]
        # A disconnected position proves no role. In particular a prior
        # partial map/configured order cannot exclude its candidate name from
        # the visible tabs' joint assignment after tabs were reordered.
        available_accounts = list(self.accounts)
        online_only = verified_only or skip_unavailable
        if not visible_frames and not occluded_positions:
            raise PendingError("all tab positions are offline during identity rebind")
        score_rows = [self.identity_scores(frame) for _tab_x, frame in visible_frames]
        @functools.lru_cache(None)
        def best_two(row: int, used: int) -> tuple[tuple[float, tuple[int, ...]], ...]:
            if row == len(visible_frames):
                return ((0.0, ()),)
            candidates = []
            for index, candidate in enumerate(available_accounts):
                if used & (1 << index):
                    continue
                for tail_score, tail_assignment in best_two(row + 1, used | (1 << index)):
                    candidates.append((score_rows[row][candidate.name] + tail_score,
                                       (index,) + tail_assignment))
            return tuple(sorted(candidates, reverse=True)[:2])

        ranked_assignments = best_two(0, 0)
        best_total = ranked_assignments[0][0] if ranked_assignments else -1e9
        second_total = ranked_assignments[1][0] if len(ranked_assignments) > 1 else -1e9
        best_assignment = (tuple(available_accounts[index] for index in ranked_assignments[0][1])
                           if ranked_assignments else None)
        if best_assignment is None and visible_frames:
            raise PendingError("identity rebind produced no assignment")
        if best_assignment is None:
            best_assignment = ()
        unaffected_positions = set(all_positions) - set(positions)
        rebound: list[Account] = [previous_by_x[tab_x] for tab_x in unaffected_positions]
        unavailable_positions.update(offline_positions)
        # A visible but blank/unidentified browser tab has no proven account
        # identity.  Do not pin the account formerly at that x before the
        # healthy tabs have been jointly assigned; tab reordering could put
        # that account in a different, healthy slot.
        if not online_only:
            rebound.extend(previous_by_x[tab_x] for tab_x in offline_positions)
        matrix: dict[str, dict[str, float]] = {}
        frame_by_x = {tab_x: frame for tab_x, frame in frames}
        for tab_x in sorted(occluded_positions):
            account = previous_by_x[tab_x]
            scores = self.identity_scores(frame_by_x[tab_x])
            matrix[f"position_{tab_x}"] = scores
            rebound.append(Account(account.index, account.name, tab_x))
        for pos, (tab_x, _frame) in enumerate(visible_frames):
            matrix[f"position_{tab_x}"] = score_rows[pos]
        # Persist the complete score matrix before any threshold exception so
        # an ambiguous live bind has authoritative evidence for diagnosis.
        matrix_path = rebind_evidence / "matrix.json"
        atomic_json(matrix_path, matrix)
        for pos, (tab_x, _frame) in enumerate(visible_frames):
            account = best_assignment[pos]
            scores = score_rows[pos]
            own = scores[account.name]
            unique_identity_ok = self.identity_visible_for_rebind(account, tab_x, frame_by_x[tab_x])
            if own < self.identity_assignment_threshold and not unique_identity_ok:
                raise PendingError(f"identity rebind ambiguous at tab x={tab_x}: assigned={account.name} scores={scores}")
            rebound.append(Account(account.index, account.name, tab_x))
        if len({account.name for account in rebound}) != len(rebound):
            raise PendingError("offline/occluded slot conflicts with an observed identity; keep original checkpoints")
        assigned_names = {account.name for account in rebound}
        unknown_slot_positions = unavailable_positions - offline_positions
        if online_only:
            # A solo run can work with the positively identified tabs alone.
            # Never assign an unknown or missing slot to a leftover name.
            rebound = [account for account in rebound
                       if account.tab_x not in unavailable_positions]
            names = [account.name for account in rebound]
            if len(names) != len(set(names)) or len({account.tab_x for account in rebound}) != len(rebound):
                raise PendingError("verified solo tabs conflict; no account content clicks")
        elif unknown_slot_positions and saved_map_valid:
            # Unknown tabs retain their last verified positions. Pairing
            # sorted slots with configured account order can silently swap two
            # obscured accounts and misattribute content clicks.
            rebound = preserve_unknown_tab_assignments(
                rebound, unknown_slot_positions, previous_by_x, set(self.by_name))
        elif unknown_slot_positions:
            raise PendingError("unknown tabs have no prior verified identity map")
        else:
            missing_accounts = [account for account in self.accounts if account.name not in assigned_names]
            if len(missing_accounts) != len(unknown_slot_positions):
                raise PendingError("visible identity assignment could not uniquely reconcile missing browser tabs")
            for tab_x, account in zip(sorted(unknown_slot_positions), missing_accounts):
                rebound.append(Account(account.index, account.name, tab_x))
        assignment_gap = 1.0 if len(visible_frames) < 2 else best_total - second_total
        if assignment_gap < self.identity_assignment_margin:
            raise PendingError(f"identity rebind global assignment gap too small: gap={assignment_gap:.6f}")
        if online_only and not rebound:
            raise PendingError("no positively identified online account")
        self.accounts = sorted(rebound, key=lambda account: account.index)
        self.by_name = {account.name: account for account in self.accounts}
        self.joint_identity_bound = {account.name for account in self.accounts if account.tab_x not in unavailable_positions}
        self.available_account_names = set(self.joint_identity_bound)
        if not verified_only:
            self.state.global_state["tab_map"] = {account.name: account.tab_x for account in self.accounts}
        else:
            self.state.global_state["verified_solo_tab_map"] = {
                account.name: account.tab_x for account in self.accounts}
        self.state.save_global()
        self.event(
            "identity_rebound",
            mapping={account.name: account.tab_x for account in self.accounts},
            scope=sorted(scope_names) if scope_names is not None else "all",
            offline_positions=sorted(offline_positions),
            unavailable_positions=sorted(unavailable_positions),
            missing_slot_positions=sorted(missing_slot_positions),
            occluded_positions={str(tab_x): mode for tab_x, mode in sorted(occluded_positions.items())},
            total_score=best_total,
            assignment_gap=assignment_gap,
            evidence_directory=str(rebind_evidence),
            matrix_evidence=str(matrix_path),
        )

    def recognize_two_passes(self) -> None:
        if self.account_mode == 'dynamic':
            from hyol_accounts import discover_accounts
            discover_accounts(self)
            if not self.accounts:
                raise PendingError('未发现身份可核实的在线账号')
            self.state.global_state['stage'] = 'independent'
            self.state.save_global()
            return
        if self.enroll or not all(account.identity_path.exists() for account in self.accounts):
            self.enroll_identities()
        else:
            self.rebind_tabs_by_identity(skip_unavailable=True)
        eligible_accounts = self.available_accounts()
        skipped = [account.name for account in self.accounts if account.name not in {item.name for item in eligible_accounts}]
        if skipped:
            self.event("recognize_unavailable_accounts_skipped", accounts=skipped, eligible=[item.name for item in eligible_accounts])
        if not eligible_accounts:
            raise PendingError("identity recognition found no online, positively identified account")
        failures: list[str] = []
        for pass_no in (1, 2):
            for account in eligible_accounts:
                try:
                    frame = self.switch_account(account, verify=True)
                    ok, scores = self.verify_identity(account, frame)
                    self.event("recognize_pass", account, pass_no=pass_no, ok=ok, scores=scores)
                    if not ok:
                        failures.append(f"{account.name}:pass{pass_no}")
                except PendingError as exc:
                    failures.append(f"{account.name}:pass{pass_no}:{exc}")
                    self.event("recognize_pending", account, pass_no=pass_no, detail=str(exc))
        if failures:
            raise PendingError("identity recognition incomplete: " + "; ".join(failures))
        self.state.global_state["stage"] = "independent"
        self.state.global_state["recognize_completed_at"] = now_iso()
        self.state.save_global()
        self.event("stage_complete", stage="recognize", content_clicks=self.content_click_count)

    def available_accounts(self) -> list[Account]:
        names = getattr(self, "available_account_names", None)
        offline = getattr(self, 'disconnected_accounts', set())
        return [account for account in self.accounts if account.name not in offline
                and (names is None or account.name in names)]

    def combat_eligible_accounts(self) -> list[Account]:
        # A generic current-window client has already verified its selected
        # tab highlight and deliberately has no configured identity assets.
        if self.generic_current_tab_x is not None:
            return list(self.accounts)
        return self.available_accounts()

    def content_guard(
        self,
        account: Account,
        page_anchors: Iterable[str] = (),
        anchor_threshold: float | None = None,
    ) -> Image.Image:
        anchors = tuple(page_anchors)
        deadline = time.monotonic() + 30.
        while True:
            try:
                return self._content_guard_once(account, anchors, anchor_threshold)
            except SafetyError as exc:
                message = str(exc)
                retry = message.startswith('page anchor guard failed:')
                if message.startswith('identity and page-anchor guard failed: scores={} '):
                    # Modal pages hide the role strip. Observe a repaint only
                    # while the previously named account's exact tab remains
                    # selected; never move a tab or relax a recognition score.
                    frame = getattr(self, '_last_frame', None)
                    mapping = self._saved_tab_mapping()
                    try:
                        retry = (self.active == account and frame is not None
                                 and isinstance(mapping, dict)
                                 and mapping.get(account.name) == account.tab_x
                                 and self._generic_active_tab_x(frame) == account.tab_x)
                    except (PendingError, SafetyError):
                        retry = False
                if not retry or time.monotonic() >= deadline:
                    frame = getattr(self, '_last_frame', None)
                    if frame is not None and self.active == account:
                        scope = getattr(self, '_task_observation', None)
                        try:
                            evidence = self.save_raw_evidence(frame, account, 'content_guard_failure', 'pending')
                            self.event('content_guard_pending', account, detail=message,
                                       task=scope[1] if scope and scope[0] == account else None,
                                       evidence=str(evidence))
                        except OSError:
                            pass  # Preserve the original guard failure.
                    raise
                self.event("page_anchor_retry", account, detail=str(exc))
                self.wait_interruptible(.2)

    def _content_guard_once(
        self,
        account: Account,
        page_anchors: Iterable[str] = (),
        anchor_threshold: float | None = None,
    ) -> Image.Image:
        context = getattr(self, 'run_context', None)
        if context:
            account = context.resolve(self, account)
        blocked = getattr(self, '_stalled_accounts', {}).get(account.name)
        if blocked:
            raise BattleStalled(blocked, account)
        self.ensure_window()
        if self.active != account:
            raise SafetyError("content click attempted without active account lease")
        frame = self.capture()
        if context and context.binding and not context.probing and not context.layout_matches(frame):
            raise PendingError('标签布局在动作前改变，未发送内容点击')
        if self.generic_current_tab_x is not None and not self._generic_current_tab_is_active(frame):
            raise SafetyError("current browser tab changed during generic current-window run")
        if self.template("offline_login_prompt.bmp", (780, 450, 1140, 660), frame, threshold=0.98)[0]:
            raise PendingError("account is offline and requires manual login; content clicks disabled")
        anchors = list(page_anchors)
        if any(name.startswith('puzzle_') for name in anchors):
            from hyol_puzzle_backend import require_account_tab
            require_account_tab(self, account, frame)
        anchor_ok = False
        for name in anchors:
            if name == "arena_battle_frame_anchor.bmp" and self._battle_hud_visible(frame):
                # The shared pair stays visible when a tooltip/animation hides
                # the old arena frame patch. Require both on this fresh guarded
                # frame before allowing the already scoped battle control.
                anchor_ok = True
                break
            if name == "target_card_actions.bmp":
                # Recheck the full overlay at every input guard, not just its
                # generic red X; the magnifier contains no character identity.
                if self._target_card_visible(frame):
                    anchor_ok = True
                continue
            # The home-map leave control is a small bottom-right glyph.  A
            # full-client NCC search dilutes its score with dynamic map
            # pixels, so keep the safety check in the same bounded ROI used by
            # prepare/recovery rather than widening the click area.
            roi = (1800, 930, 1920, 1032) if name == "home_leave.bmp" else VIEW
            if self.template(name, roi, frame, threshold=anchor_threshold)[0]:
                anchor_ok = True
                break
        saved_map = self._saved_tab_mapping()
        if (
            not anchors
            and self.template("team_room_close.bmp", (1200, 260, 1280, 340), frame)[0]
            and isinstance(saved_map, dict)
            and saved_map.get(account.name) == account.tab_x
        ):
            # A verified team-room shell hides the identity strip. Allow only
            # the no-anchor cleanup path (prepare_main) to proceed; the caller
            # must still close the matched room X before content actions.
            return frame
        if self.generic_current_tab_x is not None:
            ok, scores = True, {account.name: 1.0}
            reference = getattr(self, '_verified_current_mask', None)
            if reference is not None and self.main_ready(frame):
                current = identity_text_mask(frame.crop(IDENTITY_ROI))
                score = mask_f1(reference, current)
                if score < .92:
                    raise SafetyError('当前入口已关联账号的身份发生变化，禁止点击')
        else:
            ok, scores = self.verify_identity(account, frame)
        if not ok and not anchor_ok:
            raise SafetyError(f"identity and page-anchor guard failed: scores={scores} anchors={anchors}")
        if anchors and not anchor_ok:
            raise SafetyError(f"page anchor guard failed: {anchors}")
        return frame

    def save_evidence(self, frame: Image.Image, account: Account, label: str, phase: str) -> Path:
        folder = EVIDENCE_DIR / date.today().isoformat() / slug(account.name)
        folder.mkdir(parents=True, exist_ok=True)
        path = folder / f"{datetime.now():%H%M%S_%f}_{phase}_{label}.jpg"
        frame.save(path, quality=82)
        return path

    def save_raw_evidence(self, frame: Image.Image, account: Account, label: str, phase: str) -> Path:
        """Persist a lossless frame for OCR/template enrollment evidence."""
        folder = EVIDENCE_DIR / date.today().isoformat() / slug(account.name)
        folder.mkdir(parents=True, exist_ok=True)
        path = folder / f"{datetime.now():%H%M%S_%f}_{phase}_{label}.png"
        frame.save(path)
        return path

    def click_point(
        self,
        account: Account,
        x: int,
        y: int,
        label: str,
        anchors: Iterable[str],
        irreversible: bool = False,
        anchor_threshold: float | None = None,
        frame_predicate: Callable[[Image.Image], bool] | None = None,
    ) -> None:
        if getattr(self, 'run_context', None):
            account = self.run_context.resolve(self, account)
        if self.no_content_clicks:
            raise PendingError("content clicks disabled by invocation")
        if not (0 <= x < EXPECTED_WINDOW[2] and VIEW[1] <= y < EXPECTED_WINDOW[3]):
            raise SafetyError(f"content click outside trusted client: {x},{y}")
        self.check_input_pause()
        target_geometry = getattr(self, "_capture_geometry", None)
        before = self.content_guard(account, anchors, anchor_threshold=anchor_threshold)
        if frame_predicate is not None and not frame_predicate(before):
            raise TargetPreconditionChanged("target precondition changed before click")
        if target_geometry is not None and target_geometry != getattr(self, "_capture_geometry", None):
            raise SafetyError("game geometry changed since target detection; discard stale target")
        if irreversible and self.state.action_status(account.name, label) == "pending":
            raise PendingError(f"irreversible action {label} has unknown prior outcome; refusing duplicate click")
        evidence = self.save_evidence(before, account, label, "before") if irreversible else None
        if irreversible:
            self.state.set_action(account.name, label, "pending", "written before click")
            record = self.state.action_record(account.name, label)
            if isinstance(record, dict):
                # action_record() returns a defensive copy. Persist on the
                # actual write-ahead receipt before sending any input.
                self.state.accounts[account.name]['actions'][label]['before_evidence'] = str(evidence)
                self.state.save_account(account.name)
        physical_x, physical_y = self.screen_point(x,y) if hasattr(self,"screen_point") else (x,y)
        self.check_input_pause()
        Win32.click(physical_x, physical_y, expected_hwnd=self.hwnd,
                    before_down=self.check_input_pause)
        self.click_count += 1
        self.content_click_count += 1
        time.sleep(0.35)
        after = self.capture()
        self.event(
            "content_click",
            account,
            target=label,
            x=x,
            y=y,
            before_hash=image_hash(before),
            after_hash=image_hash(after),
            irreversible=irreversible,
            evidence=str(evidence) if evidence else "",
        )

    def commit_action(self, account: Account, label: str, detail: str) -> None:
        self.state.set_action(account.name, label, "done", detail)
        self.event("irreversible_committed", account, target=label, detail=detail)

    def click_template(
        self,
        account: Account,
        name: str,
        roi: tuple[int, int, int, int],
        dx: int,
        dy: int,
        label: str,
        anchors: Iterable[str],
        irreversible: bool = False,
        threshold: float | None = None,
        anchor_threshold: float | None = None,
        frame_predicate: Callable[[Image.Image], bool] | None = None,
    ) -> bool:
        anchors = tuple(anchors)
        deadline = time.monotonic() + max(0., min(5., float(self.config.get("RecognitionRetrySeconds", "1.2"))))
        while True:
            frame = self.content_guard(account, anchors, anchor_threshold=anchor_threshold)
            if frame_predicate is not None and not frame_predicate(frame):
                return False
            ok, score, x, y = self.active_template(name, roi, frame, threshold=threshold)
            if ok or time.monotonic() >= deadline:
                break
            self.event("recognition_retry", account, target=name, score=score, detail="重新采样，未发送点击")
            self.wait_interruptible(.2)
        if not ok:
            return False
        detection = getattr(self, "last_detection", None)
        if detection is not None and detection.source != "template" and detection.box is not None:
            # Model-only observations need an independent frame before input.
            original_box = detection.box
            self.wait_interruptible(.2)
            frame = self.content_guard(account, anchors, anchor_threshold=anchor_threshold)
            if not self.active_template(name, roi, frame, threshold=threshold)[0]:
                return False
            from hyol_recognition import iou
            detection = self.last_detection
            if detection.box is None or iou(original_box, detection.box) < .5:
                return False
            x, y, right, bottom = detection.box
            shape = self.templates.load(ASSETS / name).shape
            dx, dy = round(dx*(right-x)/shape[1]), round(dy*(bottom-y)/shape[0])
            x, y = round(x), round(y)
        self.event("template_match", account, template=name, score=score, x=x, y=y)
        def target_still_current(fresh):
            if frame_predicate is not None and not frame_predicate(fresh):
                return False
            ok, _score, fresh_x, fresh_y = self.active_template(name, roi, fresh, threshold=threshold)
            return ok and (fresh_x, fresh_y) == (x, y)
        try:
            self.click_point(account, x + dx, y + dy, label, anchors, irreversible,
                             anchor_threshold=anchor_threshold, frame_predicate=target_still_current)
        except TargetPreconditionChanged:
            return False
        return True

    def wait_template(self, name: str, roi: tuple[int, int, int, int], seconds: float, threshold: float | None = None) -> bool:
        deadline = time.monotonic() + seconds
        while time.monotonic() < deadline:
            self.check_stop()
            if self.template(name, roi, threshold=threshold)[0]:
                return True
            time.sleep(0.2)
        return False

    def _battle_auto_on_visible(self, frame: Image.Image | None = None) -> bool:
        """Accept both known live skins for an already-enabled auto battle."""
        frame = frame or self.capture()
        return (
            self.template("battle_auto_on.bmp", BATTLE_AUTO_ROI, frame)[0]
            or self.template("battle_auto_on_current.bmp", BATTLE_AUTO_ROI, frame, threshold=0.90)[0]
            or self.template("battle_auto_on_current_core.bmp", BATTLE_AUTO_ROI, frame, threshold=0.95)[0]
        )

    def _wait_battle_auto_on(self, seconds: float = 5) -> bool:
        deadline = time.monotonic() + seconds
        while time.monotonic() < deadline:
            self.check_stop()
            if self._battle_auto_on_visible():
                return True
            time.sleep(0.2)
        return False

    def wait_absent(self, name: str, roi: tuple[int, int, int, int], seconds: float, threshold: float | None = None) -> bool:
        deadline = time.monotonic() + seconds
        while time.monotonic() < deadline:
            self.check_stop()
            if not self.template(name, roi, threshold=threshold)[0]:
                return True
            time.sleep(0.2)
        return False

    def main_ready(self, frame: Image.Image | None = None) -> bool:
        frame = frame or self.capture()
        if self.template("offline_login_prompt.bmp", (780, 450, 1140, 660), frame, threshold=0.98)[0]:
            return False
        # anchor_bottom_menu.bmp contains one account's character portrait and is
        # deliberately excluded from the shared-tab gate. These two anchors are
        # account-neutral and live in independent parts of the main page.
        top = self.template("anchor_top_menu.bmp", (500, 69, 1500, 189), frame)[0]
        right = self.template("anchor_right_panel.bmp", (1569, 289, 1919, 489), frame)[0]
        if not top or not right:
            return False
        overlays = [
            ("arena_matching_anchor.bmp", (601, 349, 1201, 751)),
            ("arena_rankup_confirm.bmp", (801, 600, 1101, 760)),
            ("arena_defeat_confirm.bmp", (801, 580, 1101, 760)),
            ("battle_auto_off.bmp", BATTLE_AUTO_ROI),
            ("battle_auto_on.bmp", BATTLE_AUTO_ROI),
            ("battle_auto_on_current.bmp", BATTLE_AUTO_ROI),
            ("battle_auto_on_current_core.bmp", BATTLE_AUTO_ROI),
            ("arena_ready_anchor.bmp", (851, 199, 1451, 449)),
            ("mail_ready_anchor.bmp", (801, 199, 1101, 369)),
            ("mail_warehouse_anchor.bmp", (801, 199, 1101, 389)),
            ("welfare_close.bmp", (1301, 219, 1451, 369)),
            ("welfare_title_current.bmp", (1120,230,1320,380)),
            ("daruma_weekend_page.bmp", (850,230,1180,360)),
            ("daily_practice_anchor.bmp", (951, 119, 1501, 369)),
            ("survival_anchor.bmp", (201, 69, 601, 419)),
            ("wanted_team_anchor.bmp", (500, 200, 1450, 850)),
            ("strong_approach_anchor.bmp", (250, 80, 1720, 950)),
            ("rescue_jinchuriki_anchor.bmp", (250, 80, 1720, 950)),
            ("team_pvp_anchor.bmp", (250, 80, 1720, 950)),
            ("team_activity_close.bmp", (1350, 180, 1750, 350)),
            ("limited_activity_close.bmp", (1300, 180, 1500, 330)),
            ("team_result_confirm.bmp", (650, 400, 1250, 850)),
            (TEAM_REWARD_RESULT_TEMPLATE, TEAM_REWARD_RESULT_ROI),
            ("team_return_anchor.bmp", (250, 80, 1720, 950)),
            (TEAM_BATTLE_START_TEMPLATE, TEAM_BATTLE_START_ROI),
            ("team_room_close.bmp", (1200, 260, 1280, 340)),
            ("team_instance_close.bmp", (1370, 240, 1460, 330)),
            ("market_anchor.bmp", (651, 69, 1251, 319)),
            ("market_dialog_close_current.bmp", (1100, 350, 1250, 500)),
            ("start_game_button.bmp", (701, 569, 1251, 869)),
            ("home_leave.bmp", (1800, 930, 1920, 1032)),
            ("hot_spring_title_current.bmp", (780, 230, 1100, 430)),
            ("managed_settings_title_current.bmp", (750,190,1170,290)),
        ]
        overlay_visible = any(self.template(name, roi, frame)[0] for name, roi in overlays)
        return top and right and not overlay_visible

    def _activity_notice_close_match(
        self, frame: Image.Image | None = None
    ) -> tuple[str, float, int, int, float, int, int] | None:
        """Return a verified close-X match, including bounded scale drift."""
        frame = frame or self.capture()
        roi = (1865, 700, 1919, 780)
        # Current clients render a dark one-pixel border around the X.  Keep
        # both exact assets as the first choice.  Some activity cards are
        # painted at 1.2x in the fixed client, so retry the same two assets at
        # a small, bounded scale set rather than lowering an unbounded NCC
        # threshold or clicking on color alone.
        for name in ("activity_notice_close_current.bmp", "activity_notice_close.bmp"):
            matched, score, x, y = self.template(name, roi, frame)
            if matched:
                asset = self.templates.load(ASSETS / name)
                return name, score, x, y, 1.0, int(asset.shape[1]), int(asset.shape[0])
        crop = np.asarray(frame.crop(roi).convert("L"), dtype=np.float64)
        for name in ("activity_notice_close_current.bmp", "activity_notice_close.bmp"):
            asset = self.templates.load(ASSETS / name)
            for scale in (1.15, 1.20, 1.25):
                width = max(2, round(asset.shape[1] * scale))
                height = max(2, round(asset.shape[0] * scale))
                resized = np.asarray(
                    Image.fromarray(asset.astype(np.uint8)).resize(
                        (width, height), Image.Resampling.BICUBIC
                    ),
                    dtype=np.float64,
                )
                score, x, y = ncc(crop, resized)
                if score >= 0.90 and x >= 0 and y >= 0:
                    return name, score, roi[0] + x, roi[1] + y, scale, width, height
        return None

    def _activity_notice_close_template(self, frame: Image.Image | None = None) -> str | None:
        """Return the verified close-X asset for the current notice rendering."""
        match = self._activity_notice_close_match(frame)
        return match[0] if match else None

    def clear_transient_notifications(self, account: Account, frame: Image.Image | None = None) -> Image.Image:
        """Close only positively identified lower-right activity notices via their own X."""
        frame = frame or self.capture()
        notice_roi = (1680, 730, 1919, 930)
        close_roi = (1865, 700, 1919, 780)
        # The current Flash client can repaint the notice card with a lower
        # NCC score (the observed live rendering is about 0.624).  Keep this
        # relaxed cutoff confined to the small notification ROI and require
        # its dedicated close-X match before any click is allowed.
        notice_threshold = 0.50
        # A paused client can accumulate the full notification badge queue
        # (seven cards were observed live). Keep a finite ceiling, but allow
        # the verified card, X, and changed-frame loop to drain that queue.
        for _ in range(12):
            notice = self.template("activity_notice_anchor.bmp", notice_roi, frame, threshold=notice_threshold)[0]
            close_match = self._activity_notice_close_match(frame)
            close_name = close_match[0] if close_match else None
            if notice and not close_name:
                raise PendingError("lower-right activity notice is visible but its verified X is missing")
            if not notice:
                return frame
            notice_hash_before = image_hash(frame.crop(notice_roi))
            if close_match[4] == 1.0:
                if not self.click_template(
                    account,
                    close_name,
                    close_roi,
                    19,
                    19,
                    "close_lower_right_activity_notice",
                    ["activity_notice_anchor.bmp", close_name],
                ):
                    raise PendingError("lower-right activity notice X disappeared before click")
            else:
                _name, score, x, y, _scale, width, height = close_match
                self.event(
                    "template_match",
                    account,
                    template=close_name,
                    score=score,
                    x=x,
                    y=y,
                    scaled=True,
                )
                self.click_point(
                    account,
                    x + width // 2,
                    y + height // 2,
                    "close_lower_right_activity_notice",
                    ["activity_notice_anchor.bmp"],
                    anchor_threshold=notice_threshold,
                )
            self.event("activity_notice_closed", account, location="lower_right")
            replacement = False
            for settle_index in range(12):
                self.wait_interruptible(0.5)
                next_frame = self.capture()
                notice_after = self.template(
                    "activity_notice_anchor.bmp",
                    notice_roi,
                    next_frame,
                    threshold=notice_threshold,
                )[0]
                close_after = self._activity_notice_close_match(next_frame)
                if not notice_after and not close_after:
                    return next_frame
                if (
                    notice_after
                    and close_after
                    and image_hash(next_frame.crop(notice_roi)) != notice_hash_before
                ):
                    # A different activity card may be queued in the same
                    # slot.  Process it in the next bounded iteration, but do
                    # not click again while the same card is still settling.
                    frame = next_frame
                    replacement = True
                    break
                if settle_index == 11:
                    if notice_after and close_after:
                        # Some queued cards reuse nearly identical artwork,
                        # and Flash can also ignore the first reversible X
                        # click while repainting.  Re-enter the bounded outer
                        # loop only while both the notice and its dedicated X
                        # are freshly matched.  The finite outer-card limit
                        # prevents an infinite or coordinate-only retry.
                        frame = next_frame
                        replacement = True
                        self.event(
                            "activity_notice_retry",
                            account,
                            reason="verified_notice_and_close_remained_after_settle",
                        )
                        break
                    raise PendingError("lower-right activity notice did not settle after verified close")
            if replacement:
                continue
        raise PendingError("lower-right activity notices remained after 12 verified closes")

    def _target_card_visible(self, frame: Image.Image) -> bool:
        return (
            self.template("target_card_actions.bmp", TARGET_CARD_ACTIONS_ROI, frame, threshold=.96)[0]
            and self.template("target_card_close.bmp", TARGET_CARD_CLOSE_ROI, frame, threshold=.96)[0]
            and self.template("anchor_right_panel.bmp", (1569, 289, 1919, 489), frame)[0]
            and not self._combat_active_visible("arena", frame)
        )

    def prepare_main(self, account: Account, clear_notifications: bool = True) -> None:
        from hyol_navigation import leave_hot_spring
        leave_hot_spring(self, account)
        for _ in range(5):
            frame = self.content_guard(account)
            if (self.template('daruma_weekend_page.bmp', (850,230,1180,360), frame, threshold=.98)[0]
                    and self.template('daruma_weekend_close.bmp', (1290,260,1420,370), frame, threshold=.98)[0]):
                if not self.click_template(account, 'daruma_weekend_close.bmp', (1290,260,1420,370),
                                           16,16, 'weekend_shell_close_recover', ['daruma_weekend_page.bmp'],
                                           threshold=.98, anchor_threshold=.98):
                    raise PendingError('周末活动页面关闭前变化，保留原任务')
                if not self.wait_absent('daruma_weekend_page.bmp', (850,230,1180,360), 5, threshold=.98):
                    raise PendingError('周末活动页面关闭尚未确认')
                continue
            if (self.template('welfare_title_current.bmp', (1120,230,1320,380), frame, threshold=.98)[0]
                    and self.template('welfare_close_current.bmp', (1320,250,1450,350), frame, threshold=.98)[0]):
                if not self.click_template(account, 'welfare_close_current.bmp', (1320,250,1450,350),
                                           18,18, 'welfare_shell_close_recover', ['welfare_title_current.bmp'],
                                           threshold=.98, anchor_threshold=.98):
                    raise PendingError('福利页面关闭前变化，未点击购买')
                if not self.wait_absent('welfare_title_current.bmp', (1120,230,1320,380), 5, threshold=.98):
                    raise PendingError('福利页面关闭尚未确认')
                continue
            if self.template('managed_settings_title_current.bmp', (750,190,1170,290), frame, threshold=.98)[0]:
                if not self.click_template(account, 'managed_settings_close_current.bmp',
                                           (1320,230,1450,330), 18,18,
                                           'managed_settings_close_recover',
                                           ['managed_settings_title_current.bmp'], threshold=.98, anchor_threshold=.98):
                    raise PendingError('托管设置页关闭前变化，保留原任务')
                if not self.wait_absent('managed_settings_title_current.bmp', (750,190,1170,290), 5, threshold=.98):
                    raise PendingError('托管设置页关闭尚未确认')
                continue
            if Scheduler.dismiss_unsolicited_team_invite(self, account, frame):
                continue
            if self._target_card_visible(frame):
                if not self.click_template(
                    account, "target_card_close.bmp", TARGET_CARD_CLOSE_ROI, 15, 16,
                    "target_card_close_recover", ["target_card_actions.bmp"], threshold=.96,
                ):
                    raise PendingError("角色信息浮层关闭按钮消失，未点击其他位置")
                if not self.wait_absent("target_card_close.bmp", TARGET_CARD_CLOSE_ROI, 5, threshold=.96):
                    raise PendingError("角色信息浮层关闭未确认，未重复点击")
                self.event("target_card_closed", account, detail="已关闭遮挡日常秘炼的角色信息浮层")
                continue
            if self.main_ready(frame):
                if clear_notifications:
                    frame = self.clear_transient_notifications(account, frame)
                    if not self.main_ready(frame):
                        continue
                return
            if self.template("home_leave.bmp", (1800, 930, 1920, 1032), frame)[0]:
                if not self.click_template(
                    account,
                    "home_leave.bmp",
                    (1800, 930, 1920, 1032),
                    15,
                    17,
                    "home_leave_recover",
                    ["home_leave.bmp"],
                ):
                    raise PendingError("home-map leave control disappeared")
                if not self.wait_absent("home_leave.bmp", (1800, 930, 1920, 1032), 6):
                    raise PendingError("home-map leave control remained after click")
                continue
            if self.template("market_dialog_close_current.bmp", (1100, 350, 1250, 500), frame, threshold=0.88)[0]:
                if not self.click_template(
                    account,
                    "market_dialog_close_current.bmp",
                    (1100, 350, 1250, 500),
                    25,
                    25,
                    "market_dialog_close_recover",
                    ["market_dialog_close_current.bmp"],
                    threshold=0.88,
                ):
                    raise PendingError("market NPC dialog close disappeared")
                if not self.wait_absent("market_dialog_close_current.bmp", (1100, 350, 1250, 500), 5, threshold=0.88):
                    raise PendingError("market NPC dialog remained after recovery X")
                continue
            if self.template("friends_panel_close.bmp", (1870, 260, 1920, 350), frame)[0]:
                # A failed friend-row probe must leave the account on the
                # verified main page before the next retry or tab switch.
                if not self.click_template(
                    account,
                    "friends_panel_close.bmp",
                    (1870, 260, 1920, 350),
                    20,
                    20,
                    "friends_panel_close_recover",
                    ["friends_panel_close.bmp"],
                ):
                    raise PendingError("friends panel recovery X disappeared")
                if not self.wait_absent("friends_panel_close.bmp", (1870, 260, 1920, 350), 5):
                    raise PendingError("friends panel remained after recovery X")
                continue
            if self.template("team_room_close.bmp", (1200, 260, 1280, 340), frame)[0]:
                # A previously verified Team1/Team2 room may still be open
                # when a later per-account module starts.  Closing the room
                # through its matched X is a reversible view cleanup; it does
                # not dissolve the team or repeat any invitation.
                if not self.click_template(
                    account,
                    "team_room_close.bmp",
                    (1200, 260, 1280, 340),
                    20,
                    20,
                    "team_room_close_recover",
                    ["team_room_close.bmp"],
                ):
                    raise PendingError("team-room recovery X disappeared")
                if not self.wait_absent("team_room_close.bmp", (1200, 260, 1280, 340), 5):
                    raise PendingError("team room remained after recovery X")
                continue
            if self.template("limited_activity_close.bmp", (1300, 180, 1500, 330), frame)[0]:
                # The daily/limited activity catalogue is a reversible
                # navigation shell. Close it only through its matched X so a
                # later module can safely return to the verified main page.
                if not self.click_template(
                    account,
                    "limited_activity_close.bmp",
                    (1300, 180, 1500, 330),
                    20,
                    20,
                    "limited_activity_close_recover",
                    [
                        "daily_practice_anchor.bmp",
                        "rescue_jinchuriki_anchor.bmp",
                        "team_pvp_anchor.bmp",
                        "limited_activity_close.bmp",
                    ],
                ):
                    raise PendingError("限时活动目录关闭按钮消失")
                if not self.wait_absent("limited_activity_close.bmp", (1300, 180, 1500, 330), 5):
                    raise PendingError("限时活动目录关闭按钮仍存在")
                continue
            if self.template("startup_popup_close.bmp", (1101, 219, 1501, 489), frame)[0]:
                self.click_template(account, "startup_popup_close.bmp", (1101, 219, 1501, 489), 8, 8, "startup_popup_close", ["startup_popup_close.bmp"])
                continue
            if self.template("startup_weekly_ok.bmp", (701, 719, 1201, 1031), frame)[0]:
                self.click_template(account, "startup_weekly_ok.bmp", (701, 719, 1201, 1031), 62, 21, "startup_weekly_ok", ["startup_weekly_ok.bmp"])
                continue
            raise PendingError("main anchors missing and no known popup is present")
        if not self.main_ready():
            raise PendingError("main page did not stabilize")

    def run_module(self, account: Account, name: str, fn: Callable[[Account], None]) -> None:
        module_done = self.state.module_status(account.name, name) == "done"
        # Welfare completion is stronger than the historical module flag: an
        # older run may have recorded only claim 1 while falsely marking the
        # module done.  Re-enter the page to repair missing claim 2/3 actions.
        if name == "welfare" and module_done and not self.welfare_claims_complete(account):
            module_done = False
            self.event("module_repair_required", account, module=name, reason="daily_online_requires_three_claims")
        if module_done:
            try:
                if name != "managed" and self.state.module_status(account.name, "managed") == "done":
                    self.exit_managed_for_market(account)
                self.switch_account(account)
            except PendingError as exc:
                if isinstance(exc, AccountOffline):
                    raise
                blocked = f"completed module {name} has an uncleared or unidentified page: {exc}"
                self.event("module_done_cleanup_pending", account, module=name, detail=blocked)
                raise LeaseBlocked(blocked)
            if not self.ensure_account_main_before_release(account, name):
                blocked = f"completed module {name} did not return through a verified X"
                self.event("module_done_cleanup_pending", account, module=name, detail=blocked)
                raise LeaseBlocked(blocked)
            self.event("module_skip_done", account, module=name, cleanup="main_verified")
            return
        self.state.set_module(account.name, name, "running")
        self.event("module_start", account, module=name)
        delays = (5, 30, 300)
        completed = False
        detail = "module did not pass its page/template safety gates"
        for attempt, delay in enumerate(delays, 1):
            clicks_before_attempt = self.content_click_count
            try:
                if name != "managed" and self.state.module_status(account.name, "managed") == "done":
                    # Managed mode can keep an account in an auto-battle screen
                    # where identity is not visible.  Leave it only through the
                    # verified exit control before attempting another module.
                    self.exit_managed_for_market(account)
                self.switch_account(account)
                fn(account)
                completed = True
                detail = ""
                break
            except WelfareNotReady as exc:
                detail = str(exc)
                self.event("welfare_not_ready", account, detail=detail)
                break
            except AccountOffline:
                raise
            except PendingError as exc:
                detail = str(exc)
                self.event("module_retry", account, module=name, attempt=attempt, detail=str(exc))
                if self.content_click_count > clicks_before_attempt:
                    self.event("module_no_repeat_after_click", account, module=name, attempt=attempt, detail=str(exc))
                    break
                if attempt < len(delays):
                    self.wait_interruptible(delay)
            except SafetyError as exc:
                detail = str(exc)
                self.event("module_safety_pending", account, module=name, attempt=attempt, detail=str(exc))
                break
            except Exception as exc:
                detail = f"{type(exc).__name__}: {exc}"
                self.event("module_exception_pending", account, module=name, attempt=attempt, detail=detail)
                break
        if not self.ensure_account_main_before_release(account, name):
            blocked = f"{name} did not return through a verified X; account lease retained"
            self.state.set_module(account.name, name, "quarantined", blocked)
            self.state.add_pending(account.name, name, blocked)
            raise LeaseBlocked(blocked)
        if completed:
            self.state.set_module(account.name, name, "done")
            self.event("module_done", account, module=name)
        else:
            self.state.set_module(account.name, name, "pending", detail)
            self.state.add_pending(account.name, name, detail)
            self.event("module_pending", account, module=name, detail=detail)

    def ensure_account_main_before_release(self, account: Account, module: str) -> bool:
        # This is the central no-drag/no-cross-account invariant: a lease is not
        # released and no other tab is selected until the current activity has
        # closed through a positively identified X and the main page is stable.
        if self.active != account:
            self.event("lease_release_blocked", account, module=module, reason="active_account_not_proven")
            return False
        for attempt in range(1, 4):
            self.check_stop()
            frame = self.capture()
            if self.main_ready(frame):
                self.event("lease_release_ready", account, module=module, attempt=attempt)
                return True
            try:
                recovered = self.recover_known_page(account, frame)
            except (PendingError, SafetyError) as exc:
                self.event("lease_recovery_rejected", account, module=module, attempt=attempt, detail=str(exc))
                recovered = False
            if recovered:
                frame = self.capture()
                if self.main_ready(frame):
                    self.event("lease_release_ready", account, module=module, attempt=attempt, recovery="verified_x")
                    return True
            self.event("lease_release_wait", account, module=module, attempt=attempt, reason="verified_main_not_visible")
            self.wait_interruptible(3)
        return False

    def wait_interruptible(self, seconds: float) -> None:
        deadline = time.monotonic() + seconds
        while time.monotonic() < deadline:
            self.check_stop()
            checked = getattr(self, '_health_checked_at', None)
            if (isinstance(checked, (float, int)) and time.monotonic()-checked >= 3
                    and getattr(self, 'active', None) is not None and getattr(self, 'hwnd', None)
                    and not getattr(self, 'no_content_clicks', False)
                    and Win32.foreground() == self.hwnd):
                self.capture()
            # The deadline can pass between the loop condition and the
            # sleep calculation.  Never hand ``time.sleep`` a negative
            # duration; doing so used to crash a panel worker during a tab
            # rebind and bypass its command summary/cleanup path.
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                break
            time.sleep(min(0.2, remaining))

    def ensure_assets(self, names: Iterable[str]) -> None:
        missing = [name for name in names if not (ASSETS / name).exists()]
        if missing:
            raise PendingError("missing assets: " + ",".join(missing))

    def welfare_claims_complete(self, account: Account) -> bool:
        """Return true only when all three daily-online claims are recorded.

        ``module`` status is deliberately not trusted here.  Older runs could
        mark welfare done after matching the first button three times because
        all cards were searched in one shared ROI.  The action ledger is the
        durable completion gate and lets a later run repair those accounts.
        """
        return all(
            self.state.action_status(account.name, f"welfare_claim_{index}") == "done"
            for index in range(1, 4)
        )

    def welfare_claim_enabled(self, index: int, frame: Image.Image | None = None) -> bool:
        """Require the orange button fill; grayscale NCC also matches disabled grey."""
        frame = self.capture() if frame is None else frame
        ok, _score, x, y = self.template(
            f"welfare_claim_{index}.bmp", WELFARE_CLAIM_ROIS[index-1], frame)
        if not ok:
            return False
        center = np.asarray(frame.convert("RGB"))[y+7:y+25, x+18:x+82]
        if center.shape[:2] != (18, 64):
            return False
        rgb = center.astype(np.float64).mean(axis=(0, 1))
        return bool(rgb[0] - rgb[1] >= 25 and rgb[1] - rgb[2] >= 10)

    def _mail_empty_verified(self, account: Account) -> bool:
        # mail_empty_list.bmp is a historical offline-dialog crop; it cannot
        # prove an empty mailbox. Require the genuine grey warehouse control.
        from hyol_navigation import move_cursor_clear
        move_cursor_clear(self)
        self.wait_interruptible(.3)
        observations = []
        for _ in range(3):
            frame = self.capture()
            ready = self.template('mail_ready_anchor.bmp', (801,199,1101,369), frame)[0]
            disabled, score, x, y = self.template('mail_warehouse_empty_disabled.bmp',
                (1001,719,1251,919), frame, threshold=.995)
            color_error = None
            if disabled:
                with Image.open(ASSETS/'mail_warehouse_empty_disabled.bmp') as reference:
                    a = np.asarray(reference.convert('RGB'), dtype=np.int16)
                b = np.asarray(frame.crop((x,y,x+a.shape[1],y+a.shape[0])).convert('RGB'), dtype=np.int16)
                color_error = float(np.abs(a-b).mean())
                disabled = color_error <= 8
            observations.append(dict(ready=ready, disabled=disabled, score=score, color_error=color_error))
            if not ready or not disabled:
                evidence = self.save_raw_evidence(frame, account, 'mail_attachment_state_unverified', 'pending')
                self.event('mail_attachment_observations', account, observations=observations, evidence=str(evidence))
                return False
            self.wait_interruptible(.3)
        evidence = self.save_raw_evidence(frame, account, 'mail_no_attachments', 'verified')
        self.event('mail_empty_attachments', account, observations=observations, evidence=str(evidence))
        return True

    def _close_empty_mail(self, account: Account) -> None:
        if not self.click_template(account, 'mail_close.bmp', (1301,249,1401,349),20,20,
                                   'mail_close_empty', ['mail_ready_anchor.bmp', 'mail_close.bmp']):
            raise PendingError('已确认无附件，但邮件关闭按钮未核实')
        if not self.wait_main_after_close(account, 'mail_ready_anchor.bmp', (801,199,1101,369)):
            raise PendingError('邮件关闭后主界面未连续三帧确认')

    def _mail_backpack_full_guard(self, account: Account, frame: Image.Image, evidence=None) -> None:
        if (self.template('mail_warehouse_anchor.bmp', (801,199,1101,389), frame, threshold=.98)[0]
                and self.template('mail_backpack_full_current.bmp', (740,450,1180,580), frame, threshold=.985)[0]):
            evidence = evidence or self.save_raw_evidence(frame,account,'mail_backpack_full','pending')
            self.event('mail_backpack_full',account,evidence=str(evidence),capacity='ordinary_backpack',
                       detail='普通背包已满，邮件附件保留待领取；未清理或购买')
            raise PendingError('普通背包已满，邮件附件保留待领取；证据='+str(evidence))

    def module_mail(self, account: Account) -> None:
        self.ensure_assets(["mail_toolbar_icon.bmp", "mail_ready_anchor.bmp", "mail_close.bmp", "mail_attachment_warehouse.bmp", "mail_empty_list.bmp", "mail_warehouse_anchor.bmp", "mail_warehouse_close.bmp", "mail_claim_all.bmp", "mail_claim_all_disabled.bmp", "mail_claimed_stamp.bmp", "mail_backpack_full_current.bmp"])
        from hyol_inventory import FragmentRecovery, CapacityFull, capacity_notice_visible
        recovery = FragmentRecovery(self, account)
        if recovery.active:
            recovery.ensure_space()
        self.prepare_main(account, clear_notifications=False)
        if recovery.active and recovery.record.get('phase') == 'mail_claim_verified':
            recovery.finish()
            return
        if not self.click_template(account, "mail_toolbar_icon.bmp", (1729, 69, 1839, 149), 11, 9, "mail", ["anchor_top_menu.bmp"]):
            raise SafetyError("mail toolbar template does not match this account layout")
        if not self.wait_template("mail_ready_anchor.bmp", (801, 199, 1101, 369), 6):
            raise PendingError("mail page timeout")
        if self._mail_empty_verified(account):
            if recovery.active:
                recovery.settle_claim('mail_capacity_claim_'+recovery.record['ticket'], sent=False)
            self._close_empty_mail(account)
            recovery.finish()
            return
        if not self.click_template(account, "mail_attachment_warehouse.bmp", (1001, 719, 1251, 919), 49, 15, "attachment_warehouse", ["mail_ready_anchor.bmp"]):
            if not self._mail_empty_verified(account):
                raise PendingError("attachment warehouse missing and empty-inbox state was not verified")
            self.event("mail_empty_inbox", account)
            self._close_empty_mail(account)
            return
        if not self.wait_template("mail_warehouse_anchor.bmp", (801, 199, 1101, 389), 6):
            if self.template('mail_no_attachments_tooltip.bmp',(1150,810,1360,880),threshold=.97)[0] and self.template('mail_ready_anchor.bmp',(801,199,1101,369))[0]:
                self.save_raw_evidence(self.capture(),account,'mail_no_attachments_tooltip','verified')
                if not self.click_template(account,'mail_close.bmp',(1301,249,1401,349),20,20,'mail_close_empty_warehouse',['mail_ready_anchor.bmp']):
                    raise PendingError('邮件空仓库已确认但未找到关闭按钮')
                if not self.wait_main_after_close(account, 'mail_ready_anchor.bmp', (801,199,1101,369)):
                    raise PendingError('邮件空仓库关闭未核实')
                self.event('mail_empty_attachments',account)
                return
            raise PendingError("mail warehouse timeout")
        from hyol_network_retry import run as retry_network
        def mail_claimed():
            frame = self.capture()
            self._mail_backpack_full_guard(account,frame)
            if (capacity_notice_visible(self, frame)
                    and self.template('mail_warehouse_anchor.bmp', (801,199,1101,389), frame)[0]):
                evidence = self.save_raw_evidence(frame,account,'mail_capacity_full','verified')
                recovery.request(evidence)
                raise CapacityFull('忍者碎片仓库已满')
            return (self.template('mail_claim_all_disabled.bmp', (1001,689,1251,919), frame, threshold=.995)[0]
                    and self.template('mail_claimed_stamp.bmp', (701,329,1251,719), frame)[0])
        def mail_retry_ready():
            if not self.template('mail_warehouse_anchor.bmp', (801,199,1101,389))[0]:
                return False
            self.content_guard(account, ['mail_warehouse_anchor.bmp'])
            # Remove button hover before strict enabled/disabled classification.
            from hyol_navigation import move_cursor_clear
            move_cursor_clear(self)
            self.wait_interruptible(.3)
            return self.template('mail_claim_all.bmp', (1001,689,1251,919), threshold=.995)[0]
        def claim(label):
            clicked = self.click_template(account, 'mail_claim_all.bmp', (1001,689,1251,919),
                                          47,16,label,['mail_warehouse_anchor.bmp'],
                                          irreversible=True,threshold=.995)
            if clicked:
                frame = self.capture()
                evidence = self.save_raw_evidence(frame,account,label+'_response','observed')
                self.event('mail_claim_response',account,target=label,evidence=str(evidence))
                self._mail_backpack_full_guard(account,frame,evidence)
                if capacity_notice_visible(self,frame):
                    recovery.request(evidence)
                    raise CapacityFull('忍者碎片仓库已满')
            return clicked
        if recovery.active:
            recovery.claim_remaining(mail_claimed, mail_retry_ready, claim)
        else:
            try:
                retry_network(self,account,'mail_claim_all','邮件全部提取',mail_claimed,mail_retry_ready,claim)
            except CapacityFull:
                # The ticket persists before leaving mail. Re-entry shares the
                # original budget and permits one claim after proven capacity change.
                recovery.ensure_space()
                return self.module_mail(account)
        if not self.click_template(account, "mail_warehouse_close.bmp", (1174, 259, 1275, 359), 20, 20, "mail_warehouse_close", ["mail_warehouse_anchor.bmp", "mail_warehouse_close.bmp"]):
            raise PendingError("mail warehouse X missing")
        if not self.wait_template("mail_ready_anchor.bmp", (801, 199, 1101, 369), 5):
            raise PendingError("mail warehouse did not close")
        if not self.click_template(account, "mail_close.bmp", (1301, 249, 1401, 349), 20, 20, "mail_close", ["mail_ready_anchor.bmp", "mail_close.bmp"]):
            raise PendingError("mail X missing")
        if not self.wait_main_after_close(account, 'mail_ready_anchor.bmp', (801,199,1101,369)):
            raise PendingError("mail page did not close")
        recovery.finish()

    def module_welfare(self, account: Account) -> None:
        required = [
            "welfare_toolbar.bmp",
            "welfare_close.bmp",
            "welfare_online_tab.bmp",
            "welfare_online_tab_inactive.bmp",
            "welfare_online_anchor.bmp",
        ]
        required.extend(f"welfare_{kind}_{index}.bmp" for kind in ("claim", "claimed") for index in range(1, 4))
        self.ensure_assets(required)
        self.prepare_main(account, clear_notifications=False)
        if not self.click_template(account, "welfare_toolbar.bmp", (1551, 69, 1711, 169), 26, 24, "welfare_toolbar", ["anchor_top_menu.bmp"]):
            raise PendingError("welfare toolbar missing")
        if not self.wait_template("welfare_close.bmp", (1301, 219, 1451, 369), 8):
            raise PendingError("welfare page did not open")
        if not self.template("welfare_online_anchor.bmp", (451, 319, 751, 469))[0]:
            # The current client adds a yellow exclamation mark to the
            # inactive Online Gift tab.  Keep a dedicated exact template for
            # that state and retain the older text-only bitmap as a backwards
            # compatible fallback; do not weaken the global match threshold.
            clicked_online = self.click_template(
                account,
                "welfare_online_tab_inactive.bmp",
                (501, 319, 721, 449),
                58,
                27,
                "welfare_online_tab",
                ["welfare_close.bmp"],
            )
            if not clicked_online:
                clicked_online = self.click_template(
                    account,
                    "welfare_online_tab.bmp",
                    (501, 319, 721, 449),
                    58,
                    27,
                    "welfare_online_tab_legacy",
                    ["welfare_close.bmp"],
                )
            if not clicked_online:
                raise PendingError("welfare online-gift tab missing; paid page left untouched")
            if not self.wait_template("welfare_online_anchor.bmp", (451, 319, 751, 469), 8):
                raise PendingError("welfare online-gift page timeout")
        not_ready = []
        for index, claim_roi in enumerate(WELFARE_CLAIM_ROIS, 1):
            if not self._claim_online_welfare_card(account, index, claim_roi):
                not_ready.append(index)
        if not_ready:
            self.event("welfare_not_ready", account, cards=not_ready)
            if not self.click_template(account, "welfare_close.bmp", (1301, 219, 1451, 369), 20, 20,
                                       "welfare_close_not_ready", ["welfare_online_anchor.bmp"]):
                raise PendingError("在线福利页面关闭按钮未识别")
            if not self.wait_until_main(5):
                raise PendingError("在线福利页面关闭后未确认主界面")
            raise WelfareNotReady(f"在线福利未到领取时间：奖励卡{not_ready}，本轮稍后再查")
        if not self.welfare_claims_complete(account):
            raise PendingError("daily online welfare did not positively verify all three claims")
        self.event("welfare_all_claims_verified", account, claims=3)
        if not self.click_template(account, "welfare_close.bmp", (1301, 219, 1451, 369), 20, 20, "welfare_close", ["welfare_online_anchor.bmp"]):
            raise PendingError("welfare X missing after rewards")
        if not self.wait_until_main(5):
            raise PendingError("welfare page did not close")

    def _claim_online_welfare_card(self, account: Account, index: int, claim_roi) -> bool:
        claim_action = f"welfare_claim_{index}"
        claimed_name = f"welfare_claimed_{index}.bmp"
        # Every state check uses the same card-local ROI as its click.  A
        # neighbouring card can therefore never satisfy this card's
        # claimed gate, even though all three button bitmaps are similar.
        claimed = self.template(claimed_name, claim_roi)[0]
        status = self.state.action_status(account.name, claim_action)
        if status == "pending":
            if not claimed:
                raise PendingError(f"welfare claim {index} outcome unknown; will not repeat")
            self.commit_action(account, claim_action, "claimed template appeared after pending click")
            return True
        if status == "done":
            if not claimed:
                raise PendingError(f"welfare claim {index} ledger is done but card is not positively claimed")
            return True
        if claimed:
            # Reconcile a claim completed by an earlier version before
            # writing module completion; this is read-only evidence.
            self.commit_action(account, claim_action, "claimed template reconciled")
            return True
        if not self.welfare_claim_enabled(index):
            return False
        if not self.click_template(account, f"welfare_claim_{index}.bmp", claim_roi, 50, 16, claim_action, ["welfare_online_anchor.bmp"], irreversible=True):
            raise PendingError(f"welfare claim {index} state changed during click")
        if not self.wait_template(claimed_name, claim_roi, 5):
            raise PendingError(f"welfare claim {index} outcome unknown; will not repeat")
        self.commit_action(account, claim_action, "claimed template appeared")
        return True

    def _combat_action_name(self, mode: str, index: int) -> str:
        if mode == "arena":
            return f"arena_casual_battle_{index}"
        return f"survival_battle_step_{index}"

    def _combat_limit(self, mode: str, account: Account | None = None) -> int:
        # A safety bound, never proof of score completion. Arena limits belong
        # to each account's request; the no-account form remains a compatibility
        # fallback for old callers and tests.
        if mode != "arena":
            return 20
        if account is not None:
            requests = getattr(getattr(self, 'state', None), 'global_state', {}).get('arena_requests', {})
            record = requests.get(account.name, {}) if isinstance(requests, dict) else {}
            limit = record.get('attempt_limit') if isinstance(record, dict) else None
            if type(limit) is int and limit >= 1:
                return limit
        return getattr(self, "arena_attempt_limit", ARENA_MAX_ATTEMPTS)

    def _arena_victory_count(self, account: Account) -> int:
        account_state = self.state.accounts.get(account.name, {})
        actions = account_state.get("actions", {}) if isinstance(account_state, dict) else {}
        return sum(
            1
            for action, record in actions.items()
            if str(action).startswith("arena_attempt_victory_")
            and isinstance(record, dict)
            and str(record.get("status", "")) == "done"
        )

    def _arena_target_mode_for(self, account: Account) -> str:
        requests = getattr(getattr(self, 'state', None), 'global_state', {}).get('arena_requests', {})
        record = requests.get(account.name, {}) if isinstance(requests, dict) else {}
        mode = record.get('target_mode') if isinstance(record, dict) else None
        if mode in ('tickets', 'wins'):
            return mode
        return getattr(self, 'arena_target_mode', 'wins')

    def _arena_target_for(self, account: Account) -> int:
        requests = getattr(getattr(self, 'state', None), 'global_state', {}).get('arena_requests', {})
        record = requests.get(account.name, {}) if isinstance(requests, dict) else {}
        target = record.get('target') if isinstance(record, dict) else None
        if type(target) is int and target >= 1:
            return target
        return int(getattr(self, 'arena_victory_target', ARENA_REQUIRED_WINS) or ARENA_REQUIRED_WINS)

    def _arena_goal_complete(self, account: Account) -> bool:
        if self._arena_target_mode_for(account) == 'tickets':
            requests = getattr(getattr(self, 'state', None), 'global_state', {}).get('arena_requests', {})
            record = requests.get(account.name, {}) if isinstance(requests, dict) else {}
            return (isinstance(record, dict) and record.get('status') == 'done'
                    and record.get('tickets_exhausted') is True)
        return self._arena_victory_confirmed(account)

    def _arena_victory_confirmed(self, account: Account) -> bool:
        """Return whether the configured *win-count* goal has positive evidence.

        Ticket-exhaustion mode deliberately does not use wins as its stop gate;
        each battle result is still recorded independently by attempt index.
        A generic result-confirm button is not a victory signal: the same
        result path can be followed by a defeat dialog. The legacy rank-up
        action records remain a one-time migration path.
        """
        if self._arena_target_mode_for(account) == 'tickets':
            return False
        target = self._arena_target_for(account)
        if self._arena_victory_count(account) >= target:
            return True
        if target > 1:
            return False
        if self.state.action_status(account.name, "arena_victory_1") == "done":
            return True
        account_state = self.state.accounts.get(account.name, {})
        actions = account_state.get("actions", {}) if isinstance(account_state, dict) else {}
        return any(
            str(action).startswith("arena_rankup_confirm_")
            and isinstance(record, dict)
            and str(record.get("status", "")) == "done"
            for action, record in actions.items()
        )

    def _arena_reward_claim_done(self, account: Account, index: int) -> bool:
        """Accept current and pre-change claim action names for resumption."""
        current = f"arena_daily_reward_claim_{index}"
        legacy = f"arena_reward_claim_{index}"
        return self.state.action_status(account.name, current) == "done" or self.state.action_status(account.name, legacy) == "done"

    def _arena_reward_claim_pending(self, account: Account, index: int) -> bool:
        current = f"arena_daily_reward_claim_{index}"
        legacy = f"arena_reward_claim_{index}"
        return self.state.action_status(account.name, current) == "pending" or self.state.action_status(account.name, legacy) == "pending"

    def _arena_daily_rewards_complete(self, account: Account) -> bool:
        return all(self._arena_reward_claim_done(account, index) for index in range(1, ARENA_DAILY_REWARD_CLAIMS + 1))

    def _arena_difficulties_lowered_complete(self, account: Account) -> bool:
        return all(
            self._arena_reward_claim_done(account, index)
            or self.state.action_status(account.name, f"arena_lower_difficulty_{index}") == "done"
            for index in range(1, ARENA_DAILY_REWARD_CLAIMS + 1)
        )

    def _lower_arena_daily_tasks(self, account: Account) -> None:
        """Use only the two positively verified free difficulty reductions."""
        exhausted_action = "arena_free_difficulty_exhausted"
        if self._arena_daily_rewards_complete(account) or self.state.action_status(account.name, exhausted_action) == "done":
            return
        for index in range(1, ARENA_DAILY_REWARD_CLAIMS + 1):
            action = f"arena_lower_difficulty_{index}"
            if self._arena_reward_claim_done(account, index):
                continue
            status = self.state.action_status(account.name, action)
            if status == "done":
                continue
            if status == "pending":
                raise PendingError("arena difficulty reduction outcome unknown; refusing duplicate")
            hover_y = 520 if index == 1 else 650
            Win32.move(*self.screen_point(100, 500))
            self.event("arena_hover_overlay_cleared", account, index=index, x=100, y=500)
            self.wait_interruptible(1.0)
            self.content_guard(account, ["arena_ready_anchor.bmp"])
            Win32.move(*self.screen_point(440, hover_y))
            self.event("arena_task_hovered", account, index=index, x=440, y=hover_y)
            self.wait_interruptible(0.8)
            if not self.click_template(
                account,
                "arena_lower_difficulty.bmp",
                (301, 469, 521, 879),
                32,
                12,
                f"arena_lower_open_{index}",
                ["arena_ready_anchor.bmp", "arena_lower_difficulty.bmp"],
            ):
                self.state.set_action(account.name, exhausted_action, "done", f"第{index}项每日任务未显示降低难度控件；不购买并继续竞技场")
                self.event("arena_free_difficulty_unavailable", account, index=index, action="skip_free_unavailable")
                return
            if not self.wait_template("arena_lower_confirm.bmp", (750, 520, 1000, 650), 5):
                raise PendingError("arena difficulty reduction confirmation missing")
            used_free = sum(
                self.state.action_status(account.name, f"arena_lower_difficulty_{prior}") == "done"
                for prior in range(1, ARENA_DAILY_REWARD_CLAIMS + 1)
            )
            expected_free = ARENA_DAILY_REWARD_CLAIMS - used_free
            frame = self.capture()
            actual_free = next(
                (
                    value for value in range(ARENA_DAILY_REWARD_CLAIMS, 0, -1)
                    if self.template(f"arena_free_remaining_{value}.bmp", (850, 510, 950, 575), frame, threshold=0.90)[0]
                ),
                0,
            )
            cancel_ok = self.template("arena_lower_cancel.bmp", (950, 540, 1200, 650), frame, threshold=0.90)[0]
            if actual_free > expected_free:
                if cancel_ok:
                    self.click_template(
                        account,
                        "arena_lower_cancel.bmp",
                        (950, 540, 1200, 650),
                        52,
                        17,
                        f"arena_lower_cancel_{index}",
                        ["arena_lower_confirm.bmp", "arena_lower_cancel.bmp"],
                        threshold=0.90,
                    )
                raise PendingError(f"竞技场弹窗免费次数{actual_free}高于本地安全预期{expected_free}，已取消")
            if actual_free == 0:
                if not cancel_ok or not self.click_template(
                    account, "arena_lower_cancel.bmp", (950, 540, 1200, 650), 52, 17,
                    f"arena_lower_cancel_exhausted_{index}", ["arena_lower_confirm.bmp", "arena_lower_cancel.bmp"], threshold=0.90,
                ):
                    raise PendingError("竞技场免费次数无法识别且取消控件缺失")
                self.state.set_action(account.name, exhausted_action, "done", "确认框未显示任何已登记免费次数，已取消且禁止付费")
                self.event("arena_free_difficulty_exhausted", account, index=index, expected=expected_free)
                return
            free_asset = f"arena_free_remaining_{actual_free}.bmp"
            if actual_free < expected_free:
                self.event("arena_free_count_reconciled", account, expected=expected_free, actual=actual_free, source="live_dialog")
            if not self.click_template(
                account,
                "arena_lower_confirm.bmp",
                (750, 520, 1000, 650),
                52,
                17,
                action,
                ["arena_lower_confirm.bmp", free_asset],
                irreversible=True,
                threshold=0.90,
            ):
                raise PendingError("arena free difficulty confirmation disappeared")
            if not self.wait_absent("arena_lower_confirm.bmp", (750, 520, 1000, 650), 5, threshold=0.90):
                raise PendingError("arena difficulty confirmation remained; refusing duplicate")
            self.commit_action(account, action, f"第{index}项每日任务已确认使用免费降低难度；弹窗实测剩余{actual_free}")
            self.event(
                "arena_difficulty_lowered",
                account,
                index=index,
                remaining=actual_free - 1,
                target=action,
            )
            if actual_free == 1:
                self.state.set_action(account.name, exhausted_action, "done", "本次免费降低后弹窗实测免费次数归零，禁止继续或付费")
                self.event("arena_free_difficulty_exhausted", account, index=index, expected=expected_free, after_confirm=True)
                return
            self.wait_interruptible(0.8)
        if not self._arena_difficulties_lowered_complete(account):
            raise PendingError("竞技场每日两项任务未全部降低难度")
        if self.state.action_status(account.name, "arena_daily_difficulties_lowered") != "done":
            self.state.set_action(account.name, "arena_daily_difficulties_lowered", "done", "two free difficulty reductions verified")
        self.event("arena_daily_difficulties_lowered", account, count=ARENA_DAILY_REWARD_CLAIMS)

    def _next_arena_result_index(self, account: Account, battle_index: int, current: int) -> int:
        """Continue result-dialog numbering after a process restart."""
        account_state = self.state.accounts.get(account.name, {})
        actions = account_state.get("actions", {}) if isinstance(account_state, dict) else {}
        prefix = f"arena_result_confirm_{battle_index}_"
        largest = current
        for action, record in actions.items():
            if not str(action).startswith(prefix) or not isinstance(record, dict):
                continue
            if str(record.get("status", "")) == "pending":
                raise PendingError("arena result confirmation outcome unknown; refusing duplicate")
            try:
                largest = max(largest, int(str(action).rsplit("_", 1)[-1]))
            except ValueError:
                continue
        return largest + 1

    def _record_arena_victory(self, account: Account, index: int, detail: str) -> None:
        if self.state.action_status(account.name, "arena_victory_1") == "pending":
            raise PendingError("arena victory marker outcome unknown; refusing to overwrite it")
        attempt_action = f"arena_attempt_victory_{index}"
        if self.state.action_status(account.name, attempt_action) != "done":
            self.commit_action(account, attempt_action, detail)
        if self.state.action_status(account.name, "arena_victory_1") != "done":
            self.commit_action(account, "arena_victory_1", detail)
        if self._arena_difficulties_lowered_complete(account) and self.state.action_status(account.name, "arena_daily_victory") != "done":
            self.commit_action(account, "arena_daily_victory", f"两项任务降低难度后确认胜利：{detail}")
        self.event("arena_victory_confirmed", account, index=index, wins=self._arena_victory_count(account), target=self.arena_victory_target, detail=detail)

    def _arena_attempt_victory_confirmed(self, account: Account, index: int) -> bool:
        return self.state.action_status(account.name, f"arena_attempt_victory_{index}") == "done"

    def _arena_attempt_defeat_confirmed(self, account: Account, index: int) -> bool:
        return any(
            self.state.action_status(account.name, action) == "done"
            for action in (
                f"arena_attempt_defeat_{index}",
                f"arena_defeat_confirm_{index}",
                f"arena_rankdown_confirm_{index}",
            )
        )

    def _arena_victory_visible(self, frame: Image.Image | None = None) -> bool:
        """Recognize the large fixed victory glyph on the arena result scroll."""
        return self.template(
            "arena_victory_banner.bmp",
            ARENA_VICTORY_ROI,
            frame,
            threshold=0.90,
        )[0]

    def _arena_defeat_visible(self, frame: Image.Image | None = None) -> bool:
        """Recognize the large fixed defeat glyph on the arena result scroll."""
        return self.template(
            "arena_defeat_banner.bmp",
            (780, 300, 1150, 650),
            frame,
            threshold=0.90,
        )[0]

    def _recover_arena_victory_from_evidence(self, account: Account, battle_index: int) -> bool:
        """Recover a victory that was positively visible before a prior result click.

        ``click_template`` always saves its guarded pre-click frame.  Older
        runs could acknowledge the generic result button before recording the
        large victory glyph.  Replaying that immutable frame is safer than
        starting another battle or treating a generic result as a win.
        """
        if self._arena_attempt_victory_confirmed(account, battle_index):
            return True
        folder = EVIDENCE_DIR / date.today().isoformat() / slug(account.name)
        pattern = f"*_before_arena_result_confirm_{battle_index}_*.jpg"
        for path in sorted(folder.glob(pattern), key=lambda item: item.stat().st_mtime, reverse=True):
            try:
                with Image.open(path) as evidence:
                    if not self._arena_victory_visible(evidence):
                        continue
            except (OSError, ValueError):
                continue
            detail = f"已保存的结果页明确显示胜利：{path.name}"
            self._record_arena_victory(account, battle_index, detail)
            self.event(
                "arena_victory_recovered",
                account,
                index=battle_index,
                target="arena_victory_1",
                evidence=str(path),
            )
            return True
        return False

    def _recover_arena_defeat_from_evidence(self, account: Account, battle_index: int) -> bool:
        if self._arena_attempt_defeat_confirmed(account, battle_index):
            return True
        folder = EVIDENCE_DIR / date.today().isoformat() / slug(account.name)
        pattern = f"*_before_arena_result_confirm_{battle_index}_*.jpg"
        for path in sorted(folder.glob(pattern), key=lambda item: item.stat().st_mtime, reverse=True):
            try:
                with Image.open(path) as evidence:
                    if not self._arena_defeat_visible(evidence):
                        continue
            except (OSError, ValueError):
                continue
            action = f"arena_attempt_defeat_{battle_index}"
            self.state.set_action(account.name, action, "done", f"已保存的结果页明确显示失败：{path.name}")
            self.event("arena_defeat_recovered", account, index=battle_index, target=action, evidence=str(path))
            return True
        return False

    def _next_combat_index(self, account: Account, mode: str) -> int:
        limit = self._combat_limit(mode, account)
        if mode == "survival":
            actions = self.state.accounts.get(account.name, {}).get("actions", {})
            attempts = {int(key.removeprefix("survival_battle_step_")): record
                        for key, record in actions.items()
                        if key.startswith("survival_battle_step_")
                        and key.removeprefix("survival_battle_step_").isdigit()}
            pending = sorted(index for index, record in attempts.items()
                             if record.get("status") == "pending")
            if pending:
                return pending[0]
            return max((index for index, record in attempts.items()
                        if record.get("status") == "done"), default=0) + 1
        if mode == "arena":
            # A result/rank dialog from an already-started attempt must be
            # drained before completion gates can skip the account.  This is
            # especially important when both rewards were claimed early: the
            # module may be logically satisfied while its pending battle page
            # still owns the shared foreground lease.
            for index in range(1, limit + 1):
                if self.state.action_status(account.name, self._combat_action_name(mode, index)) == "pending":
                    return index
            # Completion is account-scoped: each account owns its target mode
            # and ticket/win request record.
            goal_complete = self._arena_goal_complete(account)
            if goal_complete and self._arena_daily_rewards_complete(account):
                return limit + 1
            # ``arena_daily_victory`` is an old daily-task marker.  It cannot
            # satisfy a newly requested additional-victory target by itself.
            # Only the current target and positive attempt records may close
            # the module.
            if (
                self.state.action_status(account.name, "arena_daily_victory") == "done"
                and self._arena_victory_count(account) >= (self.arena_victory_target or ARENA_REQUIRED_WINS)
                and self._arena_daily_rewards_complete(account)
            ):
                return limit + 1
        for index in range(1, limit + 1):
            if self.state.action_status(account.name, self._combat_action_name(mode, index)) != "done":
                return index
        return limit + 1

    def _battle_hud_visible(self, frame: Image.Image) -> bool:
        """Two independent battle controls prove observation may continue."""
        return (self.template("survival_battle_hud_left_current.bmp",
                              (410, 200, 520, 255), frame, threshold=.98)[0]
                and self.template("survival_battle_hud_right_current.bmp",
                                  (1390, 200, 1520, 255), frame, threshold=.98)[0])

    def _combat_active_visible(self, mode: str, frame: Image.Image | None = None) -> bool:
        frame = frame or self.capture()
        # These controls belong to the shared battle shell, including arena.
        # A skill tooltip can cover Auto for many frames. This only recognizes
        # an active battle; it cannot prove Auto, a result, or a completed fight.
        if self._battle_hud_visible(frame):
            return True
        if mode == "arena" and self._arena_waiting_battle_visible(frame):
            return True
        if mode == "arena":
            probes = (
                ("arena_matching_anchor.bmp", (601, 349, 1201, 751)),
                ("arena_loading_anchor.bmp", ARENA_LOADING_ROI),
                ("arena_battle_frame_anchor.bmp", (250, 80, 500, 300)),
                ("battle_auto_off.bmp", BATTLE_AUTO_ROI),
                ("battle_auto_on.bmp", BATTLE_AUTO_ROI),
                ("battle_auto_on_current.bmp", BATTLE_AUTO_ROI),
                ("battle_auto_on_current_core.bmp", BATTLE_AUTO_ROI),
                ("arena_victory_banner.bmp", ARENA_VICTORY_ROI),
                ("arena_defeat_banner.bmp", (780, 300, 1150, 650)),
                ("arena_result_confirm.bmp", (651, 449, 1151, 829)),
                ("arena_rankup_confirm.bmp", (801, 600, 1101, 760)),
                ("arena_defeat_confirm.bmp", (801, 580, 1101, 760)),
                ("arena_rankdown_banner.bmp", (700, 220, 1250, 430)),
                ("arena_rankdown_confirm.bmp", (800, 600, 1150, 760)),
            )
        elif mode == "ranking":
            # Ranking can be safely revisited after its end-battle click while
            # the result dialog is still open. These anchors validate that
            # pending state instead of waiting for identity text hidden by it.
            probes = (
                ("ranking_page.bmp", (790, 95, 1080, 195)),
                ("ranking_battle_anchor.bmp", (1400, 205, 1510, 251)),
                ("ranking_end_battle.bmp", (1410, 850, 1550, 980)),
                ("ranking_result_confirm.bmp", (860, 640, 1060, 710)),
                ("ranking_result_stats.bmp", (890, 605, 1035, 653)),
                ("ranking_power_warning.bmp", (790, 505, 1130, 560)),
            )
        else:
            probes = (
                ("survival_fight_start.bmp", (751, 169, 1151, 419)),
                ("survival_fight_start_core.bmp", (751, 169, 1151, 419)),
                ("battle_auto_off.bmp", BATTLE_AUTO_ROI),
                ("battle_auto_on.bmp", BATTLE_AUTO_ROI),
                ("battle_auto_on_current.bmp", BATTLE_AUTO_ROI),
                ("battle_auto_on_current_core.bmp", BATTLE_AUTO_ROI),
                ("survival_result_confirm.bmp", (701, 519, 1151, 819)),
                ("survival_reward_result_confirm.bmp", (701, 569, 1151, 819)),
            )
        if any(self.template(name, roi, frame)[0] for name, roi in probes):
            return True
        if mode == "arena":
            ranked = self.template("arena_ranked_button_current.bmp", (521, 719, 851, 919), frame)[0]
            casual = self.template("arena_casual_button_current.bmp", (760, 719, 1050, 919), frame)[0]
            task_state = (
                self.template("arena_condition_unmet.bmp", (301, 469, 541, 829), frame)[0]
                or self.template("arena_reward_claim.bmp", (301, 469, 521, 879), frame)[0]
            )
            return (ranked or casual) and task_state
        return False

    def _arena_waiting_battle_visible(self, frame: Image.Image) -> bool:
        """Recognize the dark '等待战斗中' queue overlay without clicking it."""
        if frame.width != 1920 or frame.height < 1032:
            return False
        rgb = np.asarray(frame.convert('RGB'), dtype=np.float32)
        title = rgb[460:530, 800:1100]
        green = ((title[:, :, 1] > title[:, :, 0] * 1.15)
                 & (title[:, :, 1] > title[:, :, 2] * 1.10)
                 & (title[:, :, 1] > 85)).sum()
        return bool(green >= 900 and rgb[150:900, 300:1600].mean() < 80)

    def _combat_ready_visible(self, mode: str, frame: Image.Image | None = None) -> bool:
        frame = frame or self.capture()
        if mode == "arena":
            title = self.template("arena_ready_anchor.bmp", (851, 199, 1451, 449), frame)[0]
            ranked = self.template("arena_ranked_button_current.bmp", (521, 719, 851, 919), frame)[0]
            casual = self.template("arena_casual_button_current.bmp", (760, 719, 1050, 919), frame)[0]
            current_fight = self.template(
                "arena_battle_button_current.bmp", (650, 770, 920, 875), frame, threshold=0.98
            )[0]
            task_state = (
                self.template("arena_condition_unmet.bmp", (301, 469, 541, 829), frame)[0]
                or self.template("arena_reward_claim.bmp", (301, 469, 521, 879), frame)[0]
            )
            # The title can remain during battle transitions; require a live
            # mode control or the strictly matched current-page fight button.
            return ((ranked or casual) and (title or task_state)) or (title and current_fight)
        if mode == "ranking":
            return (self.template("ranking_page.bmp", (790, 95, 1080, 195), frame, threshold=.95)[0]
                    and self.template("ranking_counter_label.bmp", (1470, 153, 1586, 184),
                                      frame, threshold=.95)[0])
        return self.template("survival_anchor.bmp", (201, 69, 601, 419), frame)[0]

    def switch_account_for_combat(self, account: Account, mode: str, running: bool) -> Image.Image:
        if getattr(self, 'run_context', None):
            account = self.run_context.resolve(self, account)
        """Switch to a combat slot with a page-specific positive gate.

        A battle scene hides the normal top-left identity strip.  Once the
        six-tab mapping has been bound, a running slot may therefore switch by
        its fixed chrome position only when a battle/result anchor is visible;
        an arbitrary tab is never accepted.  Ready pages still prefer the
        normal strict identity check and fall back to a ready-page anchor only
        after that check has failed.
        """
        self._validate_tab_binding(account)
        current_only = getattr(self, "current_account_only", None) == account.name
        if current_only:
            # The standalone client deliberately never changes a tab.  The
            # account was identified on the currently visible page before the
            # combat loop, so a fixed tab coordinate and chrome click would
            # only create a false block when the user arranged tabs differently.
            frame = self.capture()
            if self.template("offline_login_prompt.bmp", (780, 450, 1140, 660), frame, threshold=0.98)[0]:
                raise PendingError("account is offline and requires manual login; content clicks disabled")
            if running:
                deadline = time.monotonic() + 12
                main_samples = 0
                while not (self._combat_active_visible(mode, frame) or self._combat_ready_visible(mode, frame)):
                    # A standalone retry can start after the battle has already
                    # returned to the village. Let the pending-attempt reconciler
                    # inspect that page; this gate never awards a win or starts a fight.
                    if mode == "arena" and self.generic_current_tab_x is not None:
                        if not self._generic_current_tab_is_active(frame):
                            raise PendingError("current tab changed during arena recovery; content clicks disabled")
                        main_samples = main_samples + 1 if self.main_ready(frame) else 0
                        if main_samples >= 3:
                            self.active = account
                            evidence = self.save_raw_evidence(frame, account, "arena_resume_main", "verified")
                            self.event("arena_resume_main_verified", account, evidence=str(evidence), samples=main_samples)
                            return frame
                    if time.monotonic() >= deadline:
                        raise PendingError(f"running {mode} slot has no positive battle/page anchor")
                    self.event("combat_transition_wait", account, mode=mode)
                    self.wait_interruptible(0.4)
                    frame = self.capture()
            elif not (self._combat_ready_visible(mode, frame) or self._combat_active_visible(mode, frame)):
                if self.generic_current_tab_x is not None and self.main_ready(frame):
                    self.active = account
                    self.event("generic_current_main_verified", account, mode=mode)
                    return frame
                ok, scores = self.verify_identity(account, frame)
                if not ok:
                    raise PendingError(f"combat page/identity gate missing for {account.name}: scores={scores}")
            self.active = account
            self._record_tab_binding(account)
            self.event("combat_account_bound", account, mode=mode, running=running, tab_click_suppressed=True)
            return frame
        context = getattr(self, 'run_context', None)
        if context and context.binding and not context.probing and not current_only:
            # Full-client battle/result modals hide the in-game identity strip.
            # The bound tab-title mask authorizes observation/switching only;
            # subsequent content clicks still require fresh page and identity gates.
            return context.select(self, account,
                                  allow_hidden=running, allow_transition=running)
        if not running:
            if (getattr(self, 'active', None) == account
                    and self._saved_tab_mapping().get(account.name) == account.tab_x):
                frame = self.capture()
                if (self.healthy(frame) and self.tab_is_visually_active(account, frame)
                        and self._combat_ready_visible(mode, frame)):
                    self.event('combat_account_bound_by_page', account, mode=mode,
                               repeated_tab_click=False)
                    return frame
            self.chrome_click(account.tab_x, 10, f"combat_switch:{mode}:{account.name}", account)
            self.wait_interruptible(1)
            frame = self.capture()
            for _ in range(5):
                if self.healthy(frame) and self.tab_is_visually_active(account, frame):
                    if self._combat_ready_visible(mode, frame) or self._combat_active_visible(mode, frame):
                        self.active = account
                        self._record_tab_binding(account)
                        self.event("combat_account_bound_by_page", account, mode=mode, repeated_tab_click=False)
                        return frame
                    ok, scores = self.verify_identity(account, frame)
                    if ok:
                        self.active = account
                        self._record_tab_binding(account)
                        self.event("combat_account_bound", account, mode=mode, running=False)
                        return frame
                    self.event("combat_identity_wait", account, mode=mode, detail=f"scores={scores}")
                self.wait_interruptible(0.8)
                frame = self.capture()
            raise PendingError(f"combat page/identity gate missing for {account.name}")
        same_selected_tab = False
        if (getattr(self, "active", None) == account
                and self._saved_tab_mapping().get(account.name) == account.tab_x):
            frame = self.capture()
            try:
                selected_x = self._generic_active_tab_x(frame)
            except PendingError:
                selected_x = -1
            if (abs(selected_x - account.tab_x) <= 20
                    and self.tab_is_visually_active(account, frame)):
                if self.template("offline_login_prompt.bmp", (780, 450, 1140, 660), frame, threshold=0.98)[0]:
                    raise PendingError("account is offline and requires manual login; content clicks disabled")
                if self.healthy(frame) and (self._combat_active_visible(mode, frame)
                                           or self._combat_ready_visible(mode, frame)):
                    self.event("combat_account_bound", account, mode=mode, running=True,
                               tab_click_suppressed=True)
                    return frame
                same_selected_tab = True
        if not same_selected_tab:
            self.chrome_click(account.tab_x, 10, f"combat_switch:{mode}:{account.name}", account)
            self.wait_interruptible(1.0)
            frame = self.capture()
        # Flash and the browser tab highlight do not always repaint in the same
        # frame.  A single sample produced false visual-gate failures even
        # though the requested tab was visibly active.  Wait only for chrome
        # paint stability; no game-content click is made here.
        for _ in range(4):
            if self.tab_is_visually_active(account, frame) and (same_selected_tab or self.healthy(frame)):
                break
            self.wait_interruptible(0.4)
            frame = self.capture()
        else:
            raise PendingError(f"combat tab visual gate failed for {account.name}")
        if self.template("offline_login_prompt.bmp", (780, 450, 1140, 660), frame, threshold=0.98)[0]:
            raise PendingError("account is offline and requires manual login; content clicks disabled")
        deadline = time.monotonic() + 12
        while not (self.healthy(frame) and (self._combat_active_visible(mode, frame)
                                           or self._combat_ready_visible(mode, frame))):
            if same_selected_tab and (not self.tab_is_visually_active(account, frame)
                    or abs(self._generic_active_tab_x(frame) - account.tab_x) > 20):
                raise PendingError("current combat tab changed during transition; content clicks disabled")
            ok, scores = self.verify_identity(account, frame)
            if ok and self.healthy(frame):
                break
            if time.monotonic() >= deadline:
                evidence = self.save_raw_evidence(frame, account, "combat_transition_unknown", "pending")
                self.event("combat_transition_unknown", account, mode=mode,
                           evidence=str(evidence), tab_click_suppressed=same_selected_tab)
                raise PendingError(f"running {mode} slot has no positive battle/page anchor: scores={scores}")
            if self.template("offline_login_prompt.bmp", (780, 450, 1140, 660), frame, threshold=0.98)[0]:
                raise PendingError("account went offline during battle transition")
            self.event("combat_transition_wait", account, mode=mode)
            self.wait_interruptible(0.4)
            frame = self.capture()
        self.active = account
        self._record_tab_binding(account)
        self.event("combat_account_bound", account, mode=mode, running=True,
                   tab_click_suppressed=same_selected_tab)
        return frame

    def _arena_pending_attempt_is_stale(self, account: Account, index: int) -> bool:
        record = self.state.action_record(account.name, self._combat_action_name("arena", index))
        updated_at = str(record.get("updated_at", ""))
        try:
            started = datetime.fromisoformat(updated_at)
        except ValueError:
            return False
        now = datetime.now(started.tzinfo) if started.tzinfo else datetime.now()
        return now - started >= timedelta(seconds=ARENA_STALE_PENDING_SECONDS)

    def _claim_arena_tasks(self, account: Account) -> None:
        # The two visible rows refresh after a claim. They are not the daily
        # reward limit: observed October 8 pages still have claims after #2.
        actions = self.state.accounts.get(account.name, {}).get('actions', {})
        prefixes = ('arena_daily_reward_claim_', 'arena_reward_claim_')
        if any(key.startswith(prefixes) and isinstance(row, dict)
               and row.get('status') == 'pending' for key, row in actions.items()):
            raise PendingError("arena daily reward claim outcome unknown; refusing duplicate")
        # Free-difficulty confirmation can leave the cursor over a ninja card.
        # Clear its preview before observing reward rows and the arena title;
        # otherwise the unchanged tooltip can consume the whole watchdog wait.
        from hyol_identity_overlays import clear_hover_preview
        clear_hover_preview(self)
        claim_index = 1
        while self._arena_reward_claim_done(account, claim_index):
            claim_index += 1
        claims_sent = 0
        watchdog = BattleWatchdog(mode='arena')
        while claims_sent < 12:
            frame = self.capture()
            check_battle_watchdog(self, watchdog, account, frame, 'arena_rewards', mode='arena')
            match = self.template("arena_reward_claim.bmp", (301, 469, 521, 879), frame)
            if not match[0]:
                ready_before = self.template('arena_ready_anchor.bmp', (851,199,1451,449), frame)[0]
                self.wait_interruptible(.2)
                confirmed = self.capture()
                if (ready_before and self.template('arena_ready_anchor.bmp', (851,199,1451,449), confirmed)[0]
                        and not self.template('arena_reward_claim.bmp', (301,469,521,879), confirmed)[0]):
                    break
                continue
            action = f"arena_daily_reward_claim_{claim_index}"
            x, y = match[2], match[3]
            local = (x - 3, y - 3, x + 78, y + 34)
            if not self.click_template(account, "arena_reward_claim.bmp", local, 32, 12,
                                       action, ["arena_ready_anchor.bmp", "arena_reward_claim.bmp"],
                                       irreversible=True):
                self.wait_interruptible(.4)
                continue
            claims_sent += 1
            deadline = time.monotonic() + 5
            absent_frames = 0
            while time.monotonic() < deadline:
                after = self.capture()
                current = self.template("arena_reward_claim.bmp", local, after)
                ready = self.template("arena_ready_anchor.bmp", (851, 199, 1451, 449), after)[0]
                absent_frames = absent_frames + 1 if ready and not current[0] else 0
                if absent_frames >= 2:
                    self.commit_action(account, action, "task-local claim control absent on two ready frames")
                    self.event(
                        "arena_daily_reward_claimed",
                        account,
                        claim_index=claim_index,
                        claims=ARENA_DAILY_REWARD_CLAIMS,
                        target=action,
                    )
                    break
                self.wait_interruptible(0.2)
            else:
                raise PendingError("arena reward claim did not change; refusing duplicate")
            claim_index += 1
            while self._arena_reward_claim_done(account, claim_index):
                claim_index += 1
        else:
            raise PendingError("arena reward claim sequence exceeded safe bound")
        if self._arena_daily_rewards_complete(account):
            if self.state.action_status(account.name, "arena_daily_rewards") != "done":
                self.state.set_action(account.name, "arena_daily_rewards", "done", "two left-side daily reward claims verified")
            self.event("arena_daily_rewards_verified", account, claims=ARENA_DAILY_REWARD_CLAIMS, target="arena_daily_rewards")

    def recheck_arena_rewards(self, account: Account) -> None:
        """Sweep refreshed rows after an older completed run, without another battle."""
        actions = self.state.accounts.get(account.name,{}).get('actions',{})
        if any(key.startswith('arena_') and isinstance(row,dict) and row.get('status')=='pending'
               for key,row in actions.items()):
            raise PendingError('原竞技场动作未决，领奖复查不能绕过原结果对账')
        self.prepare_main(account, clear_notifications=False)
        self.ensure_activity_toolbar(account,'arena_toolbar_core.bmp',(1201,69,1501,169))
        if not self.click_template(account,'arena_toolbar_core.bmp',(1201,69,1501,169),22,22,
                                   'arena_reward_recheck_entry',['anchor_top_menu.bmp']):
            raise PendingError('竞技场领奖复查入口未确认')
        if not self.wait_template('arena_ready_anchor.bmp',(851,199,1451,449),8):
            raise PendingError('竞技场领奖复查页面未确认')
        self._wait_arena_ready_content(account)
        self._claim_arena_tasks(account)
        if not self.click_template(account,'survival_close.bmp',(1550,60,1700,200),20,20,
                                   'arena_reward_recheck_close',['arena_ready_anchor.bmp','survival_close.bmp'],
                                   threshold=.84,anchor_threshold=.84):
            raise PendingError('竞技场领奖复查关闭控件未确认')
        if not self.wait_until_main(12):
            raise PendingError('竞技场领奖复查后未确认主界面')
        self.event('arena_completed_run_rewards_rechecked',account)

    def _wait_arena_ready_content(self, account: Account, seconds: float = 12) -> None:
        from hyol_identity_overlays import clear_hover_preview
        clear_hover_preview(self)
        """Continue on two stable content frames instead of an unconditional sleep."""
        started = time.monotonic()
        stable = 0
        while time.monotonic() - started < seconds:
            self.check_stop()
            frame = self.capture()
            ready = self._combat_ready_visible('arena', frame)
            tokens = self._arena_token_count(frame)
            stable = stable + 1 if ready and tokens is not None else 0
            if stable >= 2:
                self.event('arena_content_ready', account, seconds=round(time.monotonic()-started,3))
                return
            self.wait_interruptible(.2)
        raise PendingError('arena content or token count did not stabilize')

    def _arena_tickets_exhausted_on_page(self, account: Account, *, samples: int = 3) -> bool:
        """Require repeated positive ready-page observations of a zero ticket count."""
        for sample in range(samples):
            frame = self.capture()
            if not self._combat_ready_visible('arena', frame):
                raise PendingError('竞技场页面未确认，不能判断战令是否归零')
            tokens = self._arena_token_count(frame)
            if tokens is None:
                raise PendingError('战令剩余次数无法唯一识别，禁止误入休闲战斗')
            if tokens > 0:
                return False
            if sample + 1 < samples:
                self.wait_interruptible(0.25)
        self.event('arena_tickets_exhausted_verified', account, remaining=0, samples=samples)
        return True

    def _arena_token_count(self, frame: Image.Image) -> int | None:
        matches = [count for count in range(4) if self.template(
            f'arena_tokens_remaining_{count}.bmp', (975,295,1070,340), frame, threshold=.98)[0]]
        return matches[0] if len(matches) == 1 else None

    def _prepare_arena_slot(self, slot: CombatSlot) -> None:
        account = slot.account
        slot.next_index = self._next_combat_index(account, "arena")
        if slot.next_index > self._combat_limit("arena", account):
            # The module may have completed in a previous process.  Re-open
            # only to verify and close a leftover arena page; no battle click
            # is made in this branch.
            frame = self.switch_account_for_combat(account, "arena", running=False)
            if not self._combat_ready_visible("arena", frame):
                self.prepare_main(account, clear_notifications=False)
                self.ensure_activity_toolbar(account, "arena_toolbar_core.bmp", (1201,69,1501,169))
                if not self.click_template(account, "arena_toolbar_core.bmp", (1201, 69, 1501, 169), 22, 22, "arena_toolbar_resume", ["anchor_top_menu.bmp"]):
                    raise PendingError("arena toolbar missing while resuming rewards")
                if not self.wait_template("arena_ready_anchor.bmp", (851, 199, 1451, 449), 8):
                    raise PendingError("arena reward-resume page timeout")
                self.wait_interruptible(3)
            self._finish_arena_slot(slot)
            return
        action = self._combat_action_name("arena", slot.next_index)
        if self.state.action_status(account.name, action) == "pending":
            slot.running = True
            slot.victory_confirmed = self._recover_arena_victory_from_evidence(account, slot.next_index)
            slot.defeat_confirmed = self._recover_arena_defeat_from_evidence(account, slot.next_index)
            frame = self.switch_account_for_combat(account, "arena", running=True)
            if self._combat_active_visible("arena", frame):
                return
            if self._combat_ready_visible("arena", frame):
                if self._arena_attempt_victory_confirmed(account, slot.next_index):
                    self.commit_action(account, action, "arena page visible after positively recorded victory")
                    slot.running = False
                    slot.next_index = self._next_combat_index(account, "arena")
                    return
                if self._arena_attempt_defeat_confirmed(account, slot.next_index):
                    self.commit_action(account, action, "arena page visible after positively confirmed defeat")
                    slot.running = False
                    slot.next_index = self._next_combat_index(account, "arena")
                    return
                slot.running = False
                raise PendingError("arena pending battle returned without a positive victory marker")
            # A settled battle can return to the main map with a reversible
            # side panel (most often the friends panel) covering the right
            # main-page anchor.  Identity has already been positively verified
            # by ``switch_account_for_combat``.  Close only a known,
            # template-matched overlay, then re-evaluate the normal main-page
            # and stale-attempt gates; never treat the identity match alone as
            # proof that the battle ended.
            if self.recover_known_page(account, frame):
                frame = self.capture()
            if self.main_ready(frame) and (slot.victory_confirmed or slot.defeat_confirmed):
                outcome = "main page visible after positively recorded victory" if slot.victory_confirmed else "main page visible after positively confirmed defeat"
                self.commit_action(account, action, outcome)
                slot.running = False
                slot.next_index = self._next_combat_index(account, "arena")
                self.event(
                    "combat_battle_reconciled_from_main",
                    account,
                    mode="arena",
                    index=int(action.rsplit("_", 1)[-1]),
                    victory=slot.victory_confirmed,
                )
                # Identity rebinding may close a settled arena page through
                # its verified X.  Re-enter through the normal guarded path
                # so the next attempt/reward phase still has page anchors.
                return self._prepare_arena_slot(slot)
            if self.main_ready(frame):
                # The village proves where the account is now, not the result
                # of an earlier battle. Age and a date change never authorize
                # committing that action or opening its replacement.
                evidence = self.save_raw_evidence(
                    frame, account, "arena_unresolved_attempt_on_main", "pending")
                self.event(
                    "arena_unresolved_attempt_on_main", account, mode="arena",
                    index=slot.next_index, victory=False,
                    stale=self._arena_pending_attempt_is_stale(account, slot.next_index),
                    evidence=str(evidence),
                )
                slot.running = False
                raise PendingError(f"原竞技场战斗结果未确认；当前主界面不作为结算依据，保留断点：{evidence}")
            raise PendingError("arena battle outcome unknown; refusing duplicate start")
        frame = self.switch_account_for_combat(account, "arena", running=False)
        if self._combat_ready_visible("arena", frame):
            self._claim_arena_tasks(account)
            self._lower_arena_daily_tasks(account)
            self.event("combat_slot_prepared", account, mode="arena", next_index=slot.next_index)
            return
        # Arena entry must not detour into the lower-right notification X.
        # The toolbar itself is the guarded, visually matched destination.
        self.prepare_main(account, clear_notifications=False)
        self.ensure_activity_toolbar(account, "arena_toolbar_core.bmp", (1201,69,1501,169))
        if not self.click_template(account, "arena_toolbar_core.bmp", (1201, 69, 1501, 169), 22, 22, "arena_toolbar", ["anchor_top_menu.bmp"]):
            raise PendingError("arena toolbar missing")
        if not self.wait_template("arena_ready_anchor.bmp", (851, 199, 1451, 449), 8):
            raise PendingError("arena page timeout")
        self._wait_arena_ready_content(account)
        self._claim_arena_tasks(account)
        self._lower_arena_daily_tasks(account)
        self.event("combat_slot_prepared", account, mode="arena", next_index=slot.next_index)

    def _click_arena_mode_when_stable(self, account: Account, action: str, seconds: float = 30) -> str | None:
        from hyol_identity_overlays import clear_hover_preview
        clear_hover_preview(self)
        """Use ranked only with positive tokens; zero tokens selects casual."""
        deadline = time.monotonic() + seconds
        casual_only_frames = 0
        current_fight_frames = 0
        frame = None
        last_observation = {}
        while time.monotonic() < deadline:
            self.check_stop()
            frame = self.capture()
            ranked_match = self.template("arena_ranked_button_current.bmp", (521, 719, 851, 919), frame)
            casual_match = self.template("arena_casual_button_current.bmp", (760, 719, 1050, 919), frame)
            ready_match = self.template("arena_ready_anchor.bmp", (851, 199, 1451, 449), frame)
            current_fight_match = self.template(
                "arena_battle_button_current.bmp", (650, 770, 920, 875), frame, threshold=0.98
            )
            ranked, casual, ready = ranked_match[0], casual_match[0], ready_match[0]
            tokens = self._arena_token_count(frame)
            last_observation = {
                "tokens": tokens,
                "ranked": {"matched": ranked, "score": round(ranked_match[1], 4)},
                "casual": {"matched": casual, "score": round(casual_match[1], 4)},
                "ready": {"matched": ready, "score": round(ready_match[1], 4)},
                "current_fight": {
                    "matched": current_fight_match[0],
                    "score": round(current_fight_match[1], 4),
                },
            }
            if ready and ranked and tokens is not None and tokens > 0:
                casual_only_frames = 0
                current_fight_frames = 0
                if self.click_template(account, "arena_ranked_button_current.bmp",
                                       (521, 719, 851, 919), 88, 29, action,
                                       ["arena_ready_anchor.bmp", "arena_ranked_button_current.bmp"],
                                       irreversible=True,
                                       frame_predicate=lambda fresh: self._arena_token_count(fresh) == tokens):
                    self.event("arena_battle_mode_selected", account, battle_mode="ranked", target=action)
                    return "ranked"
                self.event("arena_mode_revalidation_wait", account, battle_mode="ranked",
                           detail="排位按钮点击前刷新，重新核对战令可用状态")
            elif ready and casual and tokens == 0:
                casual_only_frames += 1
                if self._arena_target_mode_for(account) == 'tickets':
                    if casual_only_frames >= 3:
                        self.event('arena_tickets_exhausted_verified', account,
                                   remaining=0, samples=casual_only_frames)
                        return 'tickets_exhausted'
                elif casual_only_frames >= 3:
                    evidence = self.save_raw_evidence(frame, account,
                                                      "arena_ranked_unavailable", "verified")
                    self.event("arena_ranked_unavailable", account,
                               evidence=str(evidence), observations=casual_only_frames)
                    if self.click_template(account, "arena_casual_button_current.bmp",
                                           (760, 719, 1050, 919), 88, 29, action,
                                           ["arena_ready_anchor.bmp", "arena_casual_button_current.bmp"],
                                           irreversible=True,
                                           frame_predicate=lambda fresh: self._arena_token_count(fresh) == 0):
                        self.event("arena_battle_mode_selected", account,
                                   battle_mode="casual", target=action)
                        return "casual"
                    casual_only_frames = 0
                current_fight_frames = 0
            elif ready and current_fight_match[0] and tokens == 0 and not ranked:
                current_fight_frames += 1
                casual_only_frames = 0
                if current_fight_frames >= 3:
                    if self._arena_target_mode_for(account) == 'tickets':
                        self.event('arena_tickets_exhausted_verified', account,
                                   remaining=0, samples=current_fight_frames)
                        return 'tickets_exhausted'
                    if self.click_template(
                        account,
                        "arena_battle_button_current.bmp",
                        (650, 770, 920, 875),
                        95,
                        36,
                        action,
                        ["arena_ready_anchor.bmp", "arena_battle_button_current.bmp"],
                        irreversible=True,
                        threshold=0.98,
                        frame_predicate=lambda fresh: self._arena_token_count(fresh) == 0,
                    ):
                        self.event(
                            "arena_battle_mode_selected",
                            account,
                            battle_mode="current_arena_page",
                            target=action,
                        )
                        return "current_arena_page"
                    current_fight_frames = 0
            else:
                casual_only_frames = 0
                current_fight_frames = 0
            self.wait_interruptible(0.4)
        if frame is not None:
            evidence = self.save_raw_evidence(frame, account, "arena_mode_unstable", "pending")
            self.event("arena_mode_unstable", account, action=action,
                       observations=last_observation, evidence=str(evidence))
        return None

    def _combat_progress_watchdog(self, slot: CombatSlot, frame: Image.Image) -> None:
        """One 30s image-change rule for matching, loading and active battles."""
        if slot.mode == 'arena' and (
                self._arena_victory_visible(frame) or self._arena_defeat_visible(frame)
                or self.template('arena_result_confirm.bmp', (651,449,1151,829), frame)[0]):
            return  # Settle positive results before observing a completed static page.
        check_combat_slot_watchdog(self, slot, frame)

    def _start_arena_slot(self, slot: CombatSlot) -> None:
        account = slot.account
        if slot.running or slot.complete:
            return
        if slot.next_index > self._combat_limit("arena", account):
            self._finish_arena_slot(slot)
            return
        self._claim_arena_tasks(account)
        if self._arena_victory_confirmed(account):
            self._finish_arena_slot(slot)
            return
        self._lower_arena_daily_tasks(account)
        if (self._arena_target_mode_for(account) == 'tickets'
                and self._arena_tickets_exhausted_on_page(account)):
            slot.next_index = self._combat_limit('arena', account) + 1
            self._finish_arena_slot(slot)
            return
        # Move away from the opponent cards so their hover tooltip cannot
        # cover the casual button, then wait for that exact visual target.
        Win32.move(*self.screen_point(100, 500))
        self.wait_interruptible(0.25)
        action = self._combat_action_name("arena", slot.next_index)
        status = self.state.action_status(account.name, action)
        if status == "pending":
            slot.victory_confirmed = self._recover_arena_victory_from_evidence(account, slot.next_index)
            frame = self.capture()
            if self._combat_active_visible("arena", frame):
                slot.running = True
                return
            if self._combat_ready_visible("arena", frame):
                if self._arena_victory_confirmed(account):
                    self.commit_action(account, action, "arena page visible after positively recorded victory")
                    slot.next_index += 1
                    return
                raise PendingError("arena pending battle returned without a positive victory marker")
            raise PendingError("arena battle outcome unknown; refusing duplicate start")
        if status == "done":
            if not self._arena_victory_confirmed(account):
                raise PendingError("arena battle is recorded but no positive victory was confirmed")
            slot.next_index += 1
            return
        # The opponent list and casual button repaint after result dialogs and
        # reward claims.  Require a successful guarded revalidation, while
        # allowing a transient frame replacement to retry without any click.
        battle_mode = self._click_arena_mode_when_stable(account, action)
        if battle_mode == 'tickets_exhausted':
            slot.next_index = self._combat_limit('arena', account) + 1
            self._finish_arena_slot(slot)
            return
        if battle_mode is None:
            if self._arena_victory_confirmed(account):
                self._finish_arena_slot(slot)
                return
            raise PendingError("arena ranked/casual battle button did not stabilize before the required victory")
        slot.victory_confirmed = False
        slot.defeat_confirmed = False
        slot.start_confirmed = False
        slot.start_requested_at = time.monotonic()
        slot.progress_phase = ""
        slot.progress_changed_at = 0.0
        slot.battle_watchdog = BattleWatchdog.from_config(getattr(self, 'config', {}), started_at=time.monotonic())
        slot.battle_watchdog_index = slot.next_index
        # The guarded mode click is already write-ahead logged. Do not wait up
        # to 30 seconds on this account before issuing the other accounts'
        # starts; confirm matching/loading/battle on the periodic poll pass.
        slot.running = True
        slot.next_poll_at = time.monotonic() + ARENA_POLL_INTERVAL_SECONDS
        self.event('combat_battle_start_requested', account, mode='arena', index=slot.next_index,
                   battle_mode=battle_mode, next_check_seconds=ARENA_POLL_INTERVAL_SECONDS)
        return

    def _arena_opening_once(self, account, index, frame):
        """Write ahead before each opening input; a pending input is never resent."""
        from hyol_identity_overlays import clear_hover_preview
        known=(self.template('arena_battle_frame_anchor.bmp',VIEW,frame)[0]
               or Scheduler._battle_hud_visible(self,frame))
        if known and not (self.template('battle_auto_off.bmp',BATTLE_AUTO_ROI,frame)[0]
                          or self._battle_auto_on_visible(frame)) and clear_hover_preview(self):
            frame=self.capture()
        if not ((self.template('arena_battle_frame_anchor.bmp', VIEW, frame)[0]
                 or Scheduler._battle_hud_visible(self,frame))
                and (self.template('battle_auto_off.bmp', BATTLE_AUTO_ROI, frame)[0]
                     or self._battle_auto_on_visible(frame))):
            return False
        if any(self.template(name, roi, frame)[0] for name, roi in (
                ('arena_result_confirm.bmp', (651,449,1151,829)),
                ('arena_defeat_confirm.bmp', (801,580,1101,760)),
                ('arena_rankup_confirm.bmp', (801,600,1101,760)))):
            return False
        parent = self.state.action_record(account.name, self._combat_action_name('arena', index))
        operation = parent.get('operation_id') if isinstance(parent, dict) else None
        token = '_' + hashlib.sha256(str(operation).encode('utf-8')).hexdigest()[:10] if operation else ''
        skill = f'arena_opening_{index}{token}_skill2'
        auto = f'arena_opening_{index}{token}_auto'
        changed = False
        if self.state.action_status(account.name, skill) == 'new':
            if not self.template('arena_skill2_key.bmp', (890,920,920,950), frame, threshold=.96)[0]:
                return False  # Wait for the skill HUD to paint; no fallback clicks.
            self.click_point(account, 904, 909, skill, ['arena_battle_frame_anchor.bmp'],
                             irreversible=True, frame_predicate=lambda f:
                             self.template('arena_skill2_key.bmp', (890,920,920,950), f, threshold=.96)[0]
                             and not self._arena_victory_visible(f) and not self._arena_defeat_visible(f))
            self.commit_action(account, skill, '本场第二技能已发送一次；不再次点击')
            changed = True
            clear_hover_preview(self)
            frame = self.capture()
        if self.state.action_status(account.name, auto) == 'new':
            changed = True
            if (self._battle_auto_on_visible(frame)
                    or self.state.action_status(account.name, f'battle_auto_{index}') in ('pending', 'done')):
                self.state.set_action(account.name, auto, 'done', '自动已开启或已有发送记录，不切换')
            elif self.template('battle_auto_off.bmp', BATTLE_AUTO_ROI, frame)[0]:
                if not self.click_template(account, 'battle_auto_off.bmp', BATTLE_AUTO_ROI, 29,29,
                                           auto, ['arena_battle_frame_anchor.bmp', 'battle_auto_off.bmp'],
                                           irreversible=True):
                    return False
                self.commit_action(account, auto, '本场自动已发送一次；轮询/恢复不再次点击')
        if changed:
            self.event('arena_opening_observed', account, index=index, skill_action=skill,
                       auto_action=auto, battle_operation_id=operation)
        return self._combat_active_visible('arena', frame)

    def _poll_arena_slot(self, slot: CombatSlot) -> None:
        account = slot.account
        frame = self.capture()
        if self.template('arena_trial_notice_title_current.bmp',(780,270,1190,390),frame,threshold=.98)[0]:
            from hyol_identity_overlays import dismiss_identity_overlays
            dismiss_identity_overlays(self,account,frame,only_kind='arena_trial_notice')
            return
        self._combat_progress_watchdog(slot, frame)
        matching = (self.template("arena_matching_anchor.bmp", (601, 349, 1201, 751), frame)[0]
                    or self._arena_waiting_battle_visible(frame))
        loading = self.template("arena_loading_anchor.bmp", ARENA_LOADING_ROI, frame)[0]
        if (matching or loading or self._battle_hud_visible(frame)) and not slot.start_confirmed:
            slot.start_confirmed = True
            self.event('combat_battle_started', account, mode='arena', index=slot.next_index,
                       phase='matching' if matching else 'loading' if loading else 'battle_hud')
        if matching:
            self.wait_interruptible(0.4)
            return
        if loading:
            self.wait_interruptible(0.4)
            return
        # Do not return merely because the battle frame is visible: the auto
        # control appears inside that frame and must be handled immediately.
        # Result dialogs still take precedence over the auto badge, because the
        # badge can remain painted behind a victory/defeat modal.
        if self._arena_victory_visible(frame):
            slot.victory_confirmed = True
            self._record_arena_victory(account, slot.next_index, "竞技场结果页明确显示胜利")
        if self._arena_defeat_visible(frame):
            slot.defeat_confirmed = True
            action = f"arena_attempt_defeat_{slot.next_index}"
            if self.state.action_status(account.name, action) != "done":
                self.state.set_action(account.name, action, "done", "竞技场结果页明确显示失败")
            self.event("arena_defeat_confirmed", account, index=slot.next_index, target=action, variant="result_banner")
        if self.template("arena_result_confirm.bmp", (651, 449, 1151, 829), frame)[0]:
            slot.result_index = self._next_arena_result_index(account, slot.next_index, slot.result_index)
            label = f"arena_result_confirm_{slot.next_index}_{slot.result_index}"
            if not self.click_template(account, "arena_result_confirm.bmp", (651, 449, 1151, 829), 64, 19, label, ["arena_result_confirm.bmp"], irreversible=True):
                raise PendingError("arena result confirmation disappeared")
            if not self.wait_absent("arena_result_confirm.bmp", (651, 449, 1151, 829), 3):
                raise PendingError("arena result confirmation remained; refusing duplicate")
            self.commit_action(account, label, "arena result confirmation disappeared")
            return
        if self.template("arena_defeat_confirm.bmp", (801, 580, 1101, 760), frame)[0]:
            defeat_action = f"arena_defeat_confirm_{slot.next_index}"
            if self.state.action_status(account.name, defeat_action) == "pending":
                raise PendingError("arena defeat confirmation outcome unknown; refusing duplicate")
            if not self.click_template(account, "arena_defeat_confirm.bmp", (801, 580, 1101, 760), 70, 28, defeat_action, ["arena_defeat_confirm.bmp"]):
                raise PendingError("arena defeat confirmation disappeared")
            if not self.wait_absent("arena_defeat_confirm.bmp", (801, 580, 1101, 760), 5):
                raise PendingError("arena defeat confirmation remained after click")
            self.state.set_action(account.name, defeat_action, "done", "defeat dialog disappeared; no victory recorded")
            slot.defeat_confirmed = True
            self.event("arena_defeat_confirmed", account, index=slot.next_index, target=defeat_action)
            return
        rankdown_button = None
        if self.template("arena_rankdown_banner.bmp", (700,220,1250,430), frame, threshold=.98)[0]:
            rankdown_button = next((asset for asset in
                ('arena_rankdown_confirm.bmp','arena_rankdown_confirm_idle_current.bmp')
                if self.template(asset,(800,600,1150,760),frame,threshold=.98)[0]),None)
        if rankdown_button:
            defeat_action = f"arena_rankdown_confirm_{slot.next_index}"
            if self.state.action_status(account.name, defeat_action) == "pending":
                raise PendingError("arena rank-down confirmation outcome unknown; refusing duplicate")
            if not self.click_template(
                account,
                rankdown_button,
                (800, 600, 1150, 760),
                62,
                15,
                defeat_action,
                ["arena_rankdown_banner.bmp", rankdown_button],
                irreversible=True,
                threshold=.98,
                anchor_threshold=.98,
                frame_predicate=lambda fresh: self.template('arena_rankdown_banner.bmp',(700,220,1250,430),fresh,threshold=.98)[0],
            ):
                raise PendingError("arena rank-down confirmation disappeared")
            if not self.wait_absent("arena_rankdown_banner.bmp", (700, 220, 1250, 430), 5, threshold=0.90):
                raise PendingError("arena rank-down result remained after confirmation")
            self.commit_action(account, defeat_action, "战斗失败页已明确确认")
            from hyol_identity_overlays import clear_hover_preview
            clear_hover_preview(self)
            attempt_action = f"arena_attempt_victory_{slot.next_index}"
            if self.state.action_status(account.name, attempt_action) == "done":
                self.state.set_action(account.name, attempt_action, "invalidated", "subsequent rank-down result proved this attempt was a defeat")
            slot.victory_confirmed = False
            slot.defeat_confirmed = True
            self.event("arena_defeat_confirmed", account, index=slot.next_index, target=defeat_action, variant="rank_down")
            return
        rankup_processed = False
        for rankup_index in range(1, 9):
            if rankup_index > 1:
                frame = self.capture()
            rankup = self.template("arena_rankup_confirm.bmp", (801, 600, 1101, 760), frame)
            if not rankup[0]:
                break
            rankup_processed = True
            label = f"arena_rankup_confirm_{slot.next_index}_{rankup_index}"
            if self.state.action_status(account.name, label) == "pending":
                raise PendingError("arena rank-up confirmation outcome unknown; refusing duplicate")
            if self.state.action_status(account.name, label) == "done":
                self.wait_interruptible(0.35)
                continue
            if not self.click_template(account, "arena_rankup_confirm.bmp", (801, 600, 1101, 760), 70, 28, label, ["arena_rankup_confirm.bmp"], irreversible=True):
                raise PendingError("arena rank-up confirmation disappeared")
            if not self.wait_absent("arena_rankup_confirm.bmp", (801,600,1101,760), 3):
                raise PendingError("竞技场确认发送后按钮仍在，保留本次未决确认，不重复点击")
            self.commit_action(account, label, "post-battle confirmation disappeared; not used as victory proof")
            self.wait_interruptible(0.35)
        else:
            raise PendingError("arena rank-up confirmation sequence did not settle")
        if rankup_processed:
            frame = self.capture()
        try:
            if self._arena_opening_once(account, slot.next_index, frame):
                return
        except SafetyError:
            frame = self.capture()
            if self._combat_active_visible('arena', frame):
                self.event('arena_auto_control_temporarily_occluded', account, index=slot.next_index)
                self.wait_interruptible(.4)
                return
            raise
        if self._battle_auto_on_visible(frame):
            # A result banner can remain behind an unrecognized confirmation.
            # Its repeated presence is not proof that this attempt finished.
            check_combat_slot_watchdog(self, slot, frame)
            self.wait_interruptible(0.4)
            return
        if self._combat_ready_visible("arena", frame):
            action = self._combat_action_name("arena", slot.next_index)
            status = self.state.action_status(account.name, action)
            slot.victory_confirmed = slot.victory_confirmed or self._arena_attempt_victory_confirmed(account, slot.next_index)
            if not slot.victory_confirmed and not slot.defeat_confirmed:
                if not slot.start_confirmed:
                    # A just-clicked battle can briefly leave the ready-page
                    # controls visible while the matching overlay is painting.
                    # Keep polling this slot without treating that transition
                    # frame as an unknown result or quarantining it after 6s.
                    if slot.start_requested_at is None:
                        slot.start_requested_at = time.monotonic()
                    elapsed = time.monotonic() - slot.start_requested_at
                    if elapsed < 30:
                        self.event('combat_battle_start_transition_pending', account,
                                   mode='arena', index=slot.next_index,
                                   elapsed_seconds=round(elapsed, 2), timeout_seconds=30)
                        self.wait_interruptible(0.4)
                        return
                raise PendingError("arena start was requested but no matching/loading/battle page or result was confirmed; refusing duplicate start")
            if status == "pending":
                outcome = "returned to arena page after positively confirmed victory" if slot.victory_confirmed else "returned to arena page after confirmed defeat"
                self.commit_action(account, action, outcome)
            elif status != "done":
                raise PendingError("arena returned without a recorded battle start")
            slot.running = False
            slot.result_index = 0
            completed_index = slot.next_index
            slot.next_index = self._next_combat_index(account, "arena")
            slot.victory_confirmed = False
            slot.defeat_confirmed = False
            slot.start_confirmed = False
            slot.start_requested_at = None
            slot.pending_checks = 0
            slot.next_poll_at = 0.0
            self.event(
                "combat_battle_finished",
                account,
                mode="arena",
                index=completed_index,
                victory=self._arena_attempt_victory_confirmed(account, completed_index),
            )
            return
        check_combat_slot_watchdog(self, slot, frame)
        self.wait_interruptible(0.4)

    def _finish_arena_slot(self, slot: CombatSlot) -> None:
        if slot.complete:
            return
        account = slot.account
        target_mode = self._arena_target_mode_for(account)
        if not self.template("arena_ready_anchor.bmp", (851, 199, 1451, 449))[0]:
            raise PendingError("arena completion page is not positively visible")
        if target_mode == 'tickets':
            if not self._arena_tickets_exhausted_on_page(account):
                raise PendingError('竞技场战令次数尚未归零，不能判定完成')
        else:
            slot.victory_confirmed = slot.victory_confirmed or self._arena_victory_confirmed(account)
            if not slot.victory_confirmed:
                raise PendingError("竞技场尚未确认胜利，保持待处理")
        # Always inspect the current rows, even when older claims #1/#2 are done.
        self._claim_arena_tasks(account)
        reward_deadline = time.monotonic() + 10
        while time.monotonic() < reward_deadline and not self._arena_daily_rewards_complete(account):
            self._claim_arena_tasks(account)
            if self._arena_daily_rewards_complete(account):
                break
            self.wait_interruptible(0.5)
        if not self._arena_daily_rewards_complete(account):
            raise PendingError("竞技场左侧每日两项奖励尚未全部确认领取")
        if not self.click_template(
            account, "survival_close.bmp", (1550, 60, 1700, 200), 20, 20,
            "arena_close", ["arena_ready_anchor.bmp", "survival_close.bmp"], threshold=0.84, anchor_threshold=0.84,
        ):
            raise PendingError("竞技场关闭控件视觉匹配失败")
        if not self.wait_until_main(12):
            raise PendingError("arena page did not close through a verified X")
        slot.complete = True
        requests = self.state.global_state.setdefault('arena_requests', {})
        request = requests.get(account.name)
        if isinstance(request, dict):
            request.update(status='done', completed_at=now_iso(), target_mode=target_mode)
            request['tickets_exhausted'] = target_mode == 'tickets'
            if target_mode == 'tickets':
                request['ticket_count_at_completion'] = 0
            self.state.save_global()
        detail = ('ranked tickets exhausted and two daily reward claims verified'
                  if target_mode == 'tickets'
                  else f'{self._arena_target_for(account)} arena victories and two daily reward claims verified')
        self.state.set_module(account.name, "arena", "done", detail)
        self.event("combat_module_done", account, mode="arena", target_mode=target_mode,
                   wins=self._arena_victory_count(account), target=self._arena_target_for(account),
                   tickets_exhausted=target_mode == 'tickets', claims=ARENA_DAILY_REWARD_CLAIMS)

    def _observe_survival_pending_page(self, slot: CombatSlot, frame: Image.Image) -> bool:
        """A ready page is progress evidence, never an invented battle outcome."""
        from copy import deepcopy
        account, index = slot.account, slot.next_index
        if not self._combat_ready_visible('survival', frame):
            return False
        action = self._combat_action_name('survival', index)
        original = self.state.accounts[account.name].get('actions', {}).get(action, {})
        observed_scores = []
        for sample in range(3):
            if (not self._combat_ready_visible('survival', frame)
                    or self._combat_active_visible('survival', frame)
                    or not self._survival_page_identity_verified(account, frame)):
                return False
            observed_scores.append(self._survival_observed_score(frame))
            if sample < 2:
                self.wait_interruptible(.35)
                frame = self.capture()
        evidence = self.save_raw_evidence(frame, account, 'survival_pending_progress', 'observed')
        candidates = grade_candidates(frame, self.templates, ASSETS)
        score = observed_scores[-1] if len(set(observed_scores)) == 1 else None
        observations = self.state.accounts[account.name].setdefault('survival_resume_observations', {})
        observations[action] = dict(original_pending=deepcopy(original), score=score,
                                    grades=candidates, evidence=str(evidence), observed_at=now_iso())
        self.state.save_account(account.name)
        self.event('survival_pending_progress_observed', account, index=index,
                   score=score, grades=candidates, evidence=str(evidence), outcome='unknown')
        # A confirmed result close is not proof of point progress. Membership
        # starts at 80/220 for some accounts, so compare this exact run's score
        # baseline against the stable current page instead of counting fights.
        label = f'survival_result_confirm_{index}'
        confirmation = self.state.accounts[account.name].get('actions', {}).get(label, {})
        prior, evidence_key = self._survival_attempt_evidence(account, action)
        baseline = prior.get(evidence_key, {}).get('score_before')
        progressed = (type(baseline) is int and type(score) is int
                      and 0 <= baseline < score <= 220
                      and original.get('status') == 'pending'
                      and not original.get('checkpoint_conflict')
                      and not confirmation.get('checkpoint_conflict'))
        operation = original.get('operation_id')
        expected = operation[:-len(action)] + label if isinstance(operation, str) and operation.endswith(':' + action) else None
        if (progressed and expected and confirmation.get('status') == 'done'
                and confirmation.get('operation_id') == expected):
            self.commit_action(account, action, f'原结算完成且生存积分增加：{baseline}->{score}')
            slot.running = False
            return True
        start = self.state.accounts[account.name].get('actions', {}).get(f'{action}_start', {})
        expected_start = operation + '_start' if expected else None
        if (progressed and expected and confirmation.get('status') == 'pending'
                and confirmation.get('operation_id') == expected
                and start.get('status') == 'done'
                and start.get('operation_id') == expected_start
                and not start.get('checkpoint_conflict')):
            self.commit_action(account, label, "原结算已关闭；三帧实名页面及积分增加已核实")
            self.commit_action(account, action, f"中断后从生存积分对账：{baseline}->{score}")
            slot.running = False
            return True
        slot.running = False
        return False

    def _prepare_survival_slot(self, slot: CombatSlot) -> None:
        account = slot.account
        slot.next_index = self._next_combat_index(account, "survival")
        action = self._combat_action_name("survival", slot.next_index) if slot.next_index <= self._combat_limit("survival") else None
        if action is not None and self.state.action_status(account.name, action) == "pending":
            slot.running = True
            frame = self.switch_account_for_combat(account, "survival", running=True)
            if self._combat_active_visible("survival", frame):
                return
            if self._observe_survival_pending_page(slot, frame) is True:
                return self._prepare_survival_slot(slot)
            start_label = f"{action}_start"
            action_record = self.state.action_record(account.name, action)
            legacy_selection_only = not (isinstance(action_record, dict) and action_record.get('before_evidence'))
            if (self.main_ready(frame)
                    and self.state.action_status(account.name, start_label) == "new"
                    and legacy_selection_only):
                # Only legacy reversible opponent-selection records without a
                # write-ahead click receipt may be cleared on the main page.
                # A real start click with before-evidence is never replayed.
                self.state.set_action(account.name, action, "new", "legacy opponent selection expired before audited fight start")
                self.event("survival_selection_reconciled_without_start", account, index=slot.next_index)
                slot.running = False
                return self._prepare_survival_slot(slot)
            raise PendingError("survival battle outcome unknown; refusing duplicate start")
        frame = self.switch_account_for_combat(account, "survival", running=False)
        reward_result_was_visible = False
        if self.template("survival_reset_confirm_yes.bmp", (701, 469, 1151, 719), frame, threshold=0.92)[0]:
            if self.template("survival_complete_220.bmp", (751, 769, 1151, 1031), frame, threshold=0.98)[0]:
                self._ensure_survival_reset(account)
            elif self.template("survival_score_0.bmp", (751, 769, 1151, 1031), frame)[0]:
                self._cancel_premature_survival_reset(account)
            else:
                raise PendingError("生存演习当前进度不明确，禁止确认或取消重置")
        elif self.template("survival_reward_result_confirm.bmp", (701, 569, 1151, 819), frame)[0]:
            reward_result_was_visible = True
            self.claim_survival_rewards(account)
        else:
            # Keep an already-open survival page and its visible checkpoint.
            if not self.template("survival_anchor.bmp", (201, 69, 601, 419), frame)[0]:
                self.prepare_main(account)
                self._open_verified_survival(account)
            self._migrate_survival_direct_flow_state(account)
        if (
            reward_result_was_visible
            and self.state.action_status(account.name, "survival_reset") == "pending"
            and self.template("survival_complete_220.bmp", (751, 769, 1151, 1031), threshold=0.98)[0]
        ):
            # A reward result modal proven visible before preparation means the
            # previous background reset click could not reach the reset button.
            # The unchanged strict 220/220 page after closing that modal proves
            # no reset transition happened, so retrying the reset is safe.
            self.state.set_action(account.name, "survival_reset", "new", "奖励结果弹窗遮挡了重置，页面仍为220/220")
            self.state.set_action(account.name, "survival_reset_confirm_yes", "new", "重置未进入确认阶段")
            self.event("survival_reset_reconciled_blocked_by_reward_modal", account)
        slot.next_index = self._next_combat_index(account, "survival")
        score_deadline = time.monotonic() + 20
        score_zero = False
        score_twenty = False
        score_eighty = False
        score_one_forty = False
        score_one_sixty = False
        score_two_hundred = False
        score_full = False
        while time.monotonic() < score_deadline:
            score_frame = self.capture()
            score_zero = self.template("survival_score_0.bmp", (751, 769, 1151, 1031), score_frame)[0]
            score_twenty = self._survival_score_twenty_visible(score_frame)
            score_eighty = self.template("survival_score_80.bmp", (751, 769, 1151, 1031), score_frame)[0]
            score_one_forty = self.template("survival_score_140.bmp", (751, 769, 1151, 1031), score_frame, threshold=0.98)[0]
            score_one_sixty = self.template("survival_score_160.bmp", (751, 769, 1151, 1031), score_frame, threshold=0.98)[0]
            score_two_hundred = self._survival_score_two_hundred_visible(score_frame)
            if score_two_hundred:
                score_zero = score_twenty = score_eighty = score_one_forty = score_one_sixty = False
            score_full = self._survival_full_score_visible(score_frame)
            if score_zero or score_twenty or score_eighty or score_one_forty or score_one_sixty or score_two_hundred or score_full:
                break
            self.wait_interruptible(1)
        if score_zero and self.state.action_status(account.name, "survival_reset") == "pending":
            self.commit_action(account, "survival_reset", "重置后页面稳定为0/220")
            if self.state.action_status(account.name, "survival_reset_confirm_yes") == "pending":
                self.commit_action(account, "survival_reset_confirm_yes", "确认弹窗已消失且页面稳定为0/220")
            self.state.set_action(account.name, "survival_second_run_started", "done", "首轮完成后的重置已由0/220确认")
            self.event("survival_reset_reconciled_from_zero", account)
        if (
            self.state.action_status(account.name, "survival_reset") == "done"
            and self.state.action_status(account.name, "survival_second_run_started") == "new"
        ):
            self.state.set_action(account.name, "survival_second_run_started", "done", "兼容本日旧版已先重置的实机状态")
        # Score checkpoints describe the page, not historical battle counts.
        # Preserve every old action and resume only the next audited attempt.
        if score_full:
            self._finish_survival_slot(slot)
            return
        self.event("survival_score_observed", account,
                   score=0 if score_zero else 20 if score_twenty else 80 if score_eighty
                   else 140 if score_one_forty else 160 if score_one_sixty else 200 if score_two_hundred else None)
        if not self.template("survival_anchor.bmp", (201, 69, 601, 419))[0]:
            raise PendingError("生存页面未确认，禁止选择副本")
        self.event("combat_slot_prepared", account, mode="survival", next_index=slot.next_index)

    def _survival_score_twenty_visible(self, frame: Image.Image | None = None) -> bool:
        """Recognize 20/220 from the current scoreboard font, not the old C badge."""
        frame = frame or self.capture()
        roi = (751, 769, 1151, 1031)
        _matched, score, x, y = self.template("survival_score_80.bmp", roi, frame, threshold=0)
        if score < .90 or x < 0 or y < 0:
            return False
        with Image.open(ASSETS / "survival_score_80.bmp") as image:
            eighty = np.asarray(image.convert("L"), dtype=np.uint8)
        with Image.open(ASSETS / "survival_complete_220.bmp") as image:
            two_twenty = np.asarray(image.convert("L"), dtype=np.uint8)
        actual = np.asarray(frame.crop((x, y, x + 196, y + 37)).convert("L"), dtype=np.uint8)
        if actual.shape != eighty.shape:
            return False
        # The two-digit score is right aligned before /220. Only its first
        # glyph differs between 80 and 20; the suffix and surrounding text
        # independently anchor this comparison.
        static = np.ones(eighty.shape, dtype=bool)
        static[:, 103:117] = False
        if np.mean((actual[static] > 155) == (eighty[static] > 155)) < .985:
            return False
        current_digit = actual[6:31, 105:117] > 155
        two = two_twenty[6:31, 92:104] > 155
        eight = eighty[6:31, 105:117] > 155
        two_similarity = np.mean(current_digit == two)
        eight_similarity = np.mean(current_digit == eight)
        return bool(two_similarity >= .92 and two_similarity > eight_similarity + .08)

    def _survival_full_score_visible(self, frame: Image.Image | None = None) -> bool:
        frame = frame or self.capture()
        ok, _, x, y = self.template('survival_complete_220.bmp', (751,769,1151,1031), frame, threshold=.98)
        if not ok:
            return False
        with Image.open(ASSETS / 'survival_complete_220.bmp') as source:
            reference = np.asarray(source.convert('L'))[6:31, 90:133] > 155
        actual = np.asarray(frame.crop((x + 90, y + 6, x + 133, y + 31)).convert('L')) > 155
        return bool(actual.shape == reference.shape and np.mean(actual == reference) >= .98)

    def _cancel_premature_survival_reset(self, account: Account) -> None:
        if not self.click_template(
            account,
            "survival_reset_confirm_no.bmp",
            (701, 469, 1151, 719),
            39,
            13,
            "survival_cancel_premature_reset",
            ["survival_reset_confirm_no.bmp"],
            threshold=0.92,
        ):
            raise PendingError("首轮未完成，无法取消生存演习重置")
        if not self.wait_absent("survival_reset_confirm_no.bmp", (701, 469, 1151, 719), 8, threshold=0.92):
            raise PendingError("生存演习重置取消结果未知")
        self.state.set_action(account.name, "survival_reset", "new", "首轮未完成，已取消提前重置")
        self.state.set_action(account.name, "survival_reset_confirm_yes", "new", "首轮未完成，未确认重置")
        self.event("survival_premature_reset_cancelled", account)

    def _survival_partial_score(self, frame):
        if self._survival_score_twenty_visible(frame):
            return 20
        if self._survival_score_two_hundred_visible(frame):
            return 200
        for value in (80, 140, 160):
            if self.template(f'survival_score_{value}.bmp', (751,769,1151,1031), frame, threshold=.98)[0]:
                return value
        return None

    def _survival_score_two_hundred_visible(self, frame):
        """Recognize 200/220 using the enrolled zero glyph, never as full score."""
        if self.template('survival_score_200.bmp', (751,769,1151,1031), frame, threshold=.98)[0]:
            return True
        _ok, score, x, y = self.template('survival_complete_220.bmp',
            (751, 769, 1151, 1031), frame, threshold=0)
        if score < .90 or x < 0 or y < 0:
            return False
        with Image.open(ASSETS/'survival_complete_220.bmp') as source:
            reference = np.asarray(source.convert('L')) > 155
        actual = np.asarray(frame.crop((x, y, x+reference.shape[1], y+reference.shape[0])).convert('L')) > 155
        if actual.shape != reference.shape:
            return False
        static = np.ones(reference.shape, dtype=bool)
        static[:, 105:118] = False
        return bool(np.mean(actual[static] == reference[static]) >= .985
                    and np.mean(actual[6:31, 105:118] == reference[6:31, 118:131]) >= .98)

    def _survival_observed_score(self, frame: Image.Image) -> int | None:
        """Read actual points, including membership-start 80/220.

        The displayed score is a checkpoint, not a count of completed fights.
        A separate reset action determines the round, never the starting score.
        """
        if self._survival_full_score_visible(frame):
            return 220
        if self._survival_score_two_hundred_visible(frame):
            return 200
        if self._survival_score_twenty_visible(frame):
            return 20
        roi = (751, 769, 1151, 1031)
        for score in (140, 160, 80, 0):
            if self.template(f"survival_score_{score}.bmp", roi, frame, threshold=.98)[0]:
                return score
        return None

    def _survival_page_identity_verified(self, account: Account, frame: Image.Image) -> bool:
        """A full-screen page retains only this run's verified tab custody."""
        if self.verify_identity(account, frame)[0]:
            return True
        from hyol_run_context import RunContext
        from hyol_checkpoints import current_identity_mask
        context = getattr(self, 'run_context', None)
        if not isinstance(context, RunContext) or not context.binding:
            return False
        binding = context.binding
        if (binding['mapping'].get(account.index) != self.state.aliases.get(account.name, account.name)
                or self.active != account
                or self._saved_tab_mapping().get(account.name) != account.tab_x
                or not context.layout_matches(frame)
                or self._generic_active_tab_x(frame) != account.tab_x
                or not self.healthy(frame)
                or not self._combat_ready_visible('survival', frame)
                or self._combat_active_visible('survival', frame)
                or int(current_identity_mask(frame, self.templates).sum()) >= 50):
            return False  # A visible but different role must never use hidden-page custody.
        try:
            context.check_window(self)
        except PendingError:
            return False
        reference = binding['frames'].get(account.index)
        if reference is None or binding['centers'][account.index-1] != account.tab_x:
            return False
        def title_mask(image, x):
            return np.asarray(image.crop((x-48,5,x+24,30)).convert('L')) < 105
        previous, current = title_mask(reference, account.tab_x), title_mask(frame, account.tab_x)
        if previous.any() or current.any():
            return mask_f1(previous, current) >= .70
        # One observed tab can have no title text. Treat that only as a unique
        # chrome feature within the same six-role binding, never as identity alone.
        return sum(not title_mask(image, binding['centers'][slot-1]).any()
                   for slot, image in binding['frames'].items()) == 1

    def _survival_attempt_evidence(self, account: Account, action: str):
        from hyol_run_context import RunContext
        context = getattr(self, 'run_context', None)
        if isinstance(context, RunContext):
            if Path(self.state.directory).resolve() != context.directory.resolve():
                raise PendingError('生存账本不属于当前运行，禁止绑定操作凭据')
            record = self.state.accounts[account.name]
            scope = f'survival:{context.identifier}:{account.account_id}'
            if record.get('survival_operation_scope', scope) != scope:
                raise PendingError('生存操作编号属于其他运行或账号，保留原记录')
            record['survival_operation_scope'] = scope
        run = 2 if self.state.action_status(account.name, "survival_second_run_started") == "done" else 1
        observations = self.state.accounts[account.name].setdefault("survival_attempt_observations", {})
        return observations, f"run_{run}:{action}"

    def reconcile_survival_partial_reset(self, account, frame):
        """Prove a reset from its own before image and remaining 1 -> 0, never score alone."""
        if self.state.action_status(account.name, 'survival_reset') != 'pending':
            return False
        if (not self.template('survival_anchor.bmp', (201,69,601,419), frame, threshold=.92)[0]
                or not self._survival_page_identity_verified(account, frame)
                or self._survival_partial_score(frame) is None
                or not any(self.template(name, (851,769,1251,1031), frame, threshold=.98)[0]
                           for name in ('survival_reset_zero_text.bmp', 'survival_reset_zero_current.bmp'))):
            return False
        actions = self.state.accounts.get(account.name, {}).get('actions', {})
        if any(key.startswith(('survival_battle_step_', 'survival_result_confirm_')) and row.get('status') == 'pending'
               for key, row in actions.items() if isinstance(row, dict)):
            return False
        record = self.state.action_record(account.name, 'survival_reset')
        if record.get('checkpoint_conflict'):
            return False
        paths = [Path(record['before_evidence'])] if record.get('before_evidence') else []
        if not paths:
            # Older versions logged before-image paths without putting them in
            # the ledger. Only the original operation's timestamp may select one.
            from hyol_task_progress import ProgressReader
            try:
                stamp = datetime.fromisoformat(record['updated_at'])
            except (ValueError, KeyError):
                return False
            for logs in dict.fromkeys((LOGS, *ProgressReader(ROOT).log_roots)):
                folder = logs/'scheduler_evidence'/self.state.day/slug(account.name)
                for path in folder.glob('*_before_survival_reset.jpg'):
                    try:
                        at = datetime.strptime(path.name[:13], '%H%M%S_%f').time()
                        observed = stamp.replace(hour=at.hour, minute=at.minute, second=at.second, microsecond=at.microsecond)
                        if 0 <= (stamp-observed).total_seconds() <= 5:
                            paths.append(path)
                    except ValueError:
                        continue
        paths = list(dict.fromkeys(paths))
        if len(paths) != 1 or not paths[0].is_file():
            return False
        with Image.open(paths[0]) as image:
            before = image.convert('RGB')
        if not (self._survival_page_identity_verified(account, before)
                and self._survival_full_score_visible(before)
                and self.template('survival_reset.bmp', (851,769,1251,1031), before, threshold=.98)[0]
                and any(self.template(name, (1151,769,1451,1031), before, threshold=.98)[0]
                        for name in ('survival_final_claimed.bmp','survival_final_claimed_current.bmp'))):
            return False
        after = self.save_raw_evidence(frame, account, 'survival_partial_reset', 'verified')
        self.state.accounts[account.name]['actions']['survival_reset'].setdefault('before_evidence', str(paths[0]))
        self.state.save_account(account.name)
        self.commit_action(account, 'survival_reset', '原重置前220/220已领奖、剩余1次；当前部分积分且剩余0次')
        if self.state.action_status(account.name, 'survival_reset_confirm_yes') == 'pending':
            self.commit_action(account, 'survival_reset_confirm_yes', '原重置剩余次数1→0已确认')
        self.event('survival_partial_reset_reconciled', account, before_evidence=str(paths[0]),
                   evidence=str(after), score=self._survival_partial_score(frame),
                   detail='只对账原重置，不补记任何战斗次数或胜负')
        return True

    def _ensure_survival_reset(self, account: Account) -> None:
        reset_action = "survival_reset"
        confirm_action = "survival_reset_confirm_yes"
        if self.reconcile_survival_partial_reset(account, self.capture()) is True:
            return
        confirm_visible = self.template("survival_reset_confirm_yes.bmp", (701, 469, 1151, 719), threshold=0.92)[0]
        if confirm_visible:
            # Builds before direct-flow v2 incorrectly committed the reset by
            # seeing the unchanged 0/220 text behind this blocking modal.
            if self.state.action_status(account.name, reset_action) != 'pending':
                self.state.set_action(account.name, reset_action, "pending", "等待确认重置生存演习")
            if self.state.action_status(account.name, confirm_action) == "pending":
                # A still-visible confirmation button proves the previous
                # click did not transition, so this exact click is retryable.
                self.state.set_action(account.name, confirm_action, "new", "确认按钮仍可见，允许安全重试")
            if not self.click_template(
                account,
                "survival_reset_confirm_yes.bmp",
                (701, 469, 1151, 719),
                38,
                13,
                confirm_action,
                ["survival_reset_confirm_yes.bmp"],
                irreversible=True,
                threshold=0.92,
            ):
                raise PendingError("生存演习重置确认按钮消失")
            if not self.wait_absent("survival_reset_confirm_yes.bmp", (701, 469, 1151, 719), 8, threshold=0.92):
                raise PendingError("生存演习重置确认结果未知")
            # A membership account may restart at 80/220, not 0/220.
            # The recorded full-score/claimed before image and reset-counter
            # drop from 1 to 0 are required before accepting a partial score.
            if self.reconcile_survival_partial_reset(account, self.capture()) is True:
                return
        elif self.state.action_status(account.name, confirm_action) == "pending":
            if not self.template("survival_score_0.bmp", (751, 769, 1151, 1031))[0]:
                raise PendingError("生存演习重置确认结果未知")

        if self.state.action_status(account.name, reset_action) == "done":
            return
        if self.state.action_status(account.name, reset_action) == "new":
            if not self.click_template(
                account,
                "survival_reset.bmp",
                (851, 769, 1251, 1031),
                66,
                18,
                reset_action,
                ["survival_anchor.bmp"],
                irreversible=True,
            ):
                raise PendingError("生存演习重置状态不可用")
            if self.wait_template("survival_reset_confirm_yes.bmp", (701, 469, 1151, 719), 8, threshold=0.92):
                return self._ensure_survival_reset(account)
            # A fully completed run can reset immediately without showing the
            # loss-warning modal.  Accept only the stable 0/220 destination;
            # otherwise the irreversible reset remains pending.
            if self.wait_template("survival_score_0.bmp", (751, 769, 1151, 1031), 8):
                self.commit_action(account, reset_action, "完成态重置后页面直接稳定为0/220")
                self.event("survival_reset_committed_without_modal", account)
                return
            if self.reconcile_survival_partial_reset(account, self.capture()) is True:
                return
            raise PendingError("生存演习重置后既无确认弹窗也未稳定为0/220")
        if not self.wait_template("survival_score_0.bmp", (751, 769, 1151, 1031), 8):
            if self.reconcile_survival_partial_reset(account, self.capture()) is True:
                return
            raise PendingError("生存演习重置后未确认0/220或有来源的会员初始积分，禁止重复重置")
        if self.state.action_status(account.name, confirm_action) == "pending":
            self.commit_action(account, confirm_action, "确认按钮消失且0/220已显示")
        self.commit_action(account, reset_action, "重置确认完成且0/220已显示")

    def _open_verified_survival(self, account: Account) -> None:
        """Open the current direct toolbar entry and require the real page title.

        The former daily-practice route now opens 忍者考试 in the live client,
        whose first-row artwork is similar enough to the old survival replay to
        produce a false match.  The direct toolbar icon plus the 生存演习 title
        anchor is the current two-factor entry proof.
        """
        self.ensure_activity_toolbar(account, "survival_toolbar_current_core.bmp", (1120,69,1320,169))
        if not self.click_template(
            account,
            "survival_toolbar_current_core.bmp",
            (1120, 69, 1320, 169),
            13,
            22,
            "survival_direct_entry",
            ["anchor_top_menu.bmp"],
            threshold=0.92,
        ):
            raise PendingError("生存演习顶部入口未识别")
        if not self.wait_template("survival_anchor.bmp", (201, 69, 601, 419), 12):
            raise PendingError("生存演习页面标题未确认")

    def ensure_activity_toolbar(self, account: Account, target: str, roi: tuple[int, int, int, int]) -> None:
        frame = self.capture()
        if self.template(target, roi, frame, threshold=.92)[0]:
            return
        if not self.main_ready(frame):
            raise PendingError('活动入口未见且主界面未确认，禁止展开菜单')
        expand_roi = (1665,69,1710,135)
        if not self.template('toolbar_expand_current.bmp', expand_roi, frame, threshold=.95)[0]:
            evidence = self.save_raw_evidence(frame, account, 'activity_toolbar_unknown', 'pending')
            raise PendingError(f'活动入口和折叠菜单按钮均未确认；画面={evidence}')
        if not self.click_template(account, 'toolbar_expand_current.bmp', expand_roi, 11, 25,
                                   'activity_toolbar_expand', ['anchor_top_menu.bmp', 'toolbar_expand_current.bmp'],
                                   threshold=.95):
            raise PendingError('活动菜单展开按钮点击前已变化')
        if not self.wait_absent('toolbar_expand_current.bmp', expand_roi, 8, threshold=.95):
            raise PendingError('活动菜单展开后未确认变化，禁止重复点击')
        if not self.wait_template(target, roi, 8, threshold=.92):
            raise PendingError(f'活动菜单展开后未确认入口：{target}')
        self.event('activity_toolbar_expanded', account, target=target)

    def _migrate_survival_direct_flow_state(self, account: Account) -> None:
        """Verify the direct entry without rewriting historical battle evidence."""
        marker = "survival_direct_entry_v2"
        if self.state.action_status(account.name, marker) == "done":
            return
        # Entry verification does not invalidate settled or pending battles.
        # Old logs remain available for reconciliation; route markers are ignored.
        self.state.set_action(account.name, marker, "done", "生存演习顶部入口及页面标题均已确认")
        self.event("survival_direct_flow_verified", account)

    def _select_highest_survival_grade(self, account: Account, index: int) -> dict | None:
        """Wait briefly for overlays to clear, then require a stable grade/counter."""
        deadline = time.monotonic() + getattr(self, "survival_selection_timeout", 30.0)
        selected = None
        signature = None
        stable = 0
        last_frame = None
        last_error = "生存副本等级或次数未完整识别"
        while True:
            frame = self.content_guard(account, ["survival_anchor.bmp"])
            last_frame = frame
            if self._survival_full_score_visible(frame):
                return None
            candidates = grade_candidates(frame, self.templates, ASSETS)
            try:
                if {item["grade"] for item in candidates} != {item[0] for item in SURVIVAL_GRADES}:
                    raise ValueError("生存副本等级未完整识别，不能确认最高可挑战等级")
                current = choose_grade(candidates)
                if current is None:
                    raise ValueError("生存未满分且无已核实可挑战副本")
            except ValueError as exc:
                if str(exc) != last_error:
                    self.event('survival_grade_read_pending', account, index=index,
                               detail=str(exc), grades=candidates)
                last_error = str(exc)
                selected = None
                signature = None
                stable = 0
            else:
                current_signature = tuple(current[key] for key in ("grade", "x", "y", "remaining"))
                if current_signature == signature:
                    stable += 1
                else:
                    signature = current_signature
                    selected = current
                    stable = 1
                if stable >= 2:
                    break
            if time.monotonic() >= deadline:
                evidence = self.save_raw_evidence(frame, account, "survival_selection_unknown", "pending")
                raise PendingError(f"{last_error}；画面={evidence}")
            self.wait_interruptible(.4)
        # A narrow ROI prevents matching another card of the same grade.
        x, y = selected["x"], selected["y"]
        with Image.open(ASSETS / selected["template"]) as badge:
            width, height = badge.size
        if not self.click_template(account, selected["template"],
                                   (x - 2, y - 2, x + width + 2, y + height + 2),
                                   width // 2, height // 2, f"survival_select_step_{index}",
                                   ["survival_anchor.bmp"], threshold=.95):
            raise PendingError("生存最高级副本点击前已变化")
        self.event("survival_highest_grade_selected", account, index=index, **selected)
        return selected

    def _start_survival_slot(self, slot: CombatSlot) -> None:
        account = slot.account
        if slot.running or slot.complete:
            return
        if slot.next_index > self._combat_limit("survival"):
            self._finish_survival_slot(slot)
            return
        action = self._combat_action_name("survival", slot.next_index)
        status = self.state.action_status(account.name, action)
        if status == "pending":
            if self._combat_active_visible("survival"):
                slot.running = True
                return
            raise PendingError(f"{action} has an unknown prior outcome; refusing duplicate battle")
        if status == "done":
            slot.next_index += 1
            return
        if any(self.state.action_status(account.name, label) in ('pending', 'done')
               for label in (action + '_start', f'survival_result_confirm_{slot.next_index}')):
            raise PendingError('生存原开战或结算凭据仍存在，禁止覆盖积分基线或再次开战')
        if any(key.startswith(('survival_reset','survival_claim_','survival_reward_result_confirm'))
               and isinstance(row, dict) and row.get('status') == 'pending'
               for key, row in self.state.accounts[account.name].get('actions', {}).items()):
            raise PendingError('生存重置或奖励动作仍未决，禁止选择下一场或覆盖本轮积分基线')
        # VIP accounts may begin at 80/220; an initial nonzero score is normal.
        # Record the observed point baseline before selecting an opponent.
        before_frame = self.content_guard(account, ['survival_anchor.bmp'])
        before_score = self._survival_observed_score(before_frame)
        if before_score is None:
            evidence = self.save_raw_evidence(before_frame, account, "survival_before_score_unknown", "pending")
            raise PendingError(f"生存开战前积分不明确，保留本场：{evidence}")
        selected = self._select_highest_survival_grade(account, slot.next_index)
        if selected is None:
            self._finish_survival_slot(slot)
            return
        if not self.wait_template("survival_fight_start_core.bmp", (751, 169, 1151, 419), 30, threshold=0.92):
            raise PendingError("survival fight-start state unknown; battle remains pending")
        observations, key = self._survival_attempt_evidence(account, action)
        observations[key] = dict(score_before=before_score, grade=selected['grade'],
                                 remaining_before=selected['remaining'], observed_at=now_iso())
        self.state.save_account(account.name)
        slot.start_requested_at = time.monotonic()
        slot.battle_watchdog = BattleWatchdog.from_config(getattr(self, 'config', {}), started_at=slot.start_requested_at)
        slot.battle_watchdog_index = slot.next_index
        self._click_survival_fight_start(slot)
        slot.start_confirmed = True
        slot.running = True
        ensure_speed = getattr(self, "_ensure_survival_battle_x2", None)
        if callable(ensure_speed):
            ensure_speed(slot, self.capture())
        self.event("combat_battle_started", account, mode="survival", index=slot.next_index)

    def _click_survival_fight_start(self, slot: CombatSlot) -> None:
        account = slot.account
        action = self._combat_action_name("survival", slot.next_index)
        label = f"{action}_start"
        status = self.state.action_status(account.name, action)
        if status == "pending" and self.state.action_status(account.name, label) == "new":
            action_record = self.state.action_record(account.name, action)
            legacy_selection_only = not (isinstance(action_record, dict) and action_record.get('before_evidence'))
            if legacy_selection_only:
                # Older versions used this action key for reversible opponent
                # selection. Only that record, without a write-ahead click receipt,
                # may be cleared from the positively visible start page.
                self.state.set_action(account.name, action, "new", "fight-start visible; legacy selection pending cleared")
                self.event("survival_selection_reconciled_without_start", account, index=slot.next_index)
                status = "new"
        if status == "pending":
            raise PendingError("survival fight-start outcome unknown; refusing duplicate start")
        if status == "done":
            raise PendingError("survival returned to fight-start after a recorded start; result is unknown")
        if not self.click_template(
            account,
            "survival_fight_start_core.bmp",
            (751, 169, 1151, 419),
            60,
            27,
            action,
            ["survival_fight_start_core.bmp"],
            irreversible=True,
            threshold=0.92,
        ):
            raise PendingError("survival fight-start button missing")
        if not self.wait_absent("survival_fight_start_core.bmp", (751, 169, 1151, 419), 1.5, threshold=0.92):
            raise PendingError("survival fight-start click sent; transition still pending, refusing duplicate")
        self.state.set_action(account.name, label, "done", "fight-start control disappeared and battle transition began")

    def _poll_survival_slot(self, slot: CombatSlot, frame: Image.Image | None = None) -> None:
        account = slot.account
        frame = frame or self.capture()
        if self.template("survival_fight_start_core.bmp", (751, 169, 1151, 419), frame, threshold=0.92)[0]:
            action = self._combat_action_name("survival", slot.next_index)
            start_label = f"{action}_start"
            action_record = self.state.action_record(account.name, action)
            click_may_have_been_sent = (
                isinstance(action_record, dict) and bool(action_record.get('before_evidence'))
            ) or self.state.action_status(account.name, start_label) in ('pending', 'done') or slot.start_confirmed
            if click_may_have_been_sent:
                if slot.start_requested_at is None:
                    slot.start_requested_at = time.monotonic()
                elapsed = time.monotonic() - slot.start_requested_at
                if elapsed < 30:
                    self.event('survival_battle_start_transition_pending', account,
                               index=slot.next_index, elapsed_seconds=round(elapsed, 2),
                               timeout_seconds=30, duplicate_click=False)
                    self.wait_interruptible(.4)
                    return
                raise PendingError('生存开战点击已发送，但30秒内未确认离开开战页；保留待处理，不重复点击')
            self._click_survival_fight_start(slot)
            slot.start_confirmed = True
            self.event("combat_battle_started", account, mode="survival", index=slot.next_index, resumed=True)
            return
        # Check the result modal before the background battle badge.  This is
        # important on the shared client where the auto badge can outlive the
        # fight-result repaint by one or more frames.
        if self.template("survival_result_confirm.bmp", (701, 519, 1151, 819), frame)[0]:
            label = f"survival_result_confirm_{slot.next_index}"
            result_status = self.state.action_status(account.name, label)
            if result_status == "pending":
                raise PendingError("survival result confirmation outcome unknown; refusing duplicate")
            if result_status == "done":
                raise PendingError("生存原场结算已核实但按钮再次出现；保留原完成记录，不重复点击")
            action = self._combat_action_name("survival", slot.next_index)
            if self.state.action_status(account.name, action) != "pending":
                raise PendingError(f"{action} has no pending battle start to commit")
            if not self.click_template(account, "survival_result_confirm.bmp", (701, 519, 1151, 819), 63, 18, label, ["survival_result_confirm.bmp"], irreversible=True):
                raise PendingError("survival result confirmation disappeared")
            if not self.wait_absent("survival_result_confirm.bmp", (701, 519, 1151, 819), 10):
                raise PendingError("survival result confirmation remained; outcome is pending")
            if not self.wait_template("survival_anchor.bmp", (201, 69, 601, 419), 10):
                raise PendingError("survival page did not return after result")
            action = self._combat_action_name("survival", slot.next_index)
            if self.state.action_status(account.name, action) != "pending":
                raise PendingError(f"{action} has no pending battle start to commit")
            observations, key = self._survival_attempt_evidence(account, action)
            baseline = observations.get(key, {}).get('score_before')
            # The score can repaint after the dialog disappears. Observe, do not
            # repeat the result click or the fight if the score is still stale.
            score_after = None
            deadline = time.monotonic() + 4
            stable = 0
            while time.monotonic() < deadline:
                fresh = self.capture()
                observed = self._survival_observed_score(fresh)
                if (type(baseline) is int and type(observed) is int
                        and 0 <= baseline < observed <= 220
                        and self._combat_ready_visible('survival', fresh)
                        and self._survival_page_identity_verified(account, fresh)):
                    stable = stable + 1 if observed == score_after else 1
                    score_after = observed
                    if stable >= 2:
                        break
                else:
                    stable = 0
                self.wait_interruptible(.35)
            if stable < 2:
                evidence = self.save_raw_evidence(self.capture(), account,
                                                  f"survival_step_{slot.next_index}_score_unverified", "pending")
                raise PendingError(f"生存结算未证实积分增加：原积分={baseline}，当前={score_after}；"
                                   f"本场保持待续，不重复开战；{evidence}")
            self.commit_action(account, label, "result closed; point increase independently confirmed")
            self.commit_action(account, action, f"survival score confirmed: {baseline}->{score_after}")
            slot.running = False
            slot.next_index += 1
            slot.start_confirmed = False
            slot.start_requested_at = None
            self.event("combat_battle_finished", account, mode="survival", index=slot.next_index - 1)
            return
        ensure_speed = getattr(self, "_ensure_survival_battle_x2", None)
        if callable(ensure_speed):
            ensure_speed(slot, frame)
        self._combat_progress_watchdog(slot, frame)
        if self.template("battle_auto_off.bmp", BATTLE_AUTO_ROI, frame)[0]:
            if not self.click_template(account, "battle_auto_off.bmp", BATTLE_AUTO_ROI, 29, 29, f"survival_battle_auto_{slot.next_index}", ["battle_auto_off.bmp"]):
                raise PendingError("survival auto-battle control disappeared")
            if not self._wait_battle_auto_on(5):
                # The control can be hidden by a skill animation immediately
                # after the click.  Auto is reversible: keep the battle
                # running and re-observe on the next round-robin pass instead
                # of turning a healthy fight into an unknown result.
                self.event("survival_auto_enable_recheck", account, index=slot.next_index)
            return
        self.wait_interruptible(0.4)

    def reconcile_survival_result_confirmations(self, account: Account) -> None:
        """Repair legacy orphan confirmations only from committed battle records."""
        for step in range(1, self._combat_limit("survival") + 1):
            label = f"survival_result_confirm_{step}"
            action = self._combat_action_name("survival", step)
            if (self.state.action_status(account.name, label) == "pending"
                    and self.state.action_status(account.name, action) == "done"
                    and self.state.action_status(account.name, action + "_start") == "done"):
                self.commit_action(account, label, "旧版结算确认补记：同关开战及结算返回均已有完成记录")

    def _finish_survival_slot(self, slot: CombatSlot) -> None:
        if slot.complete:
            return
        account = slot.account
        if not self.template("survival_anchor.bmp", (201, 69, 601, 419))[0]:
            raise PendingError("survival completion page is not positively visible")
        if not self.wait_template("survival_complete_220.bmp", (751, 769, 1151, 1031), 8, threshold=0.98):
            frame = self.capture()
            evidence = self.save_raw_evidence(frame, account, "survival_score_before_completion", "pending")
            _, score, _, _ = self.template("survival_complete_220.bmp", (751, 769, 1151, 1031), frame, threshold=0)
            self.event("survival_score_unverified", account, score=round(score, 4), evidence=str(evidence))
            raise PendingError(f"survival score is not positively 220; evidence={evidence}")
        if not self._survival_full_score_visible():
            evidence = self.save_raw_evidence(self.capture(), account, 'survival_full_digits_unverified', 'pending')
            raise PendingError(f'生存220分数字未严格确认；画面={evidence}')
        actions = self.state.accounts[account.name].get('actions', {})
        if any(key.startswith(('survival_battle_step_', 'survival_result_confirm_'))
               and isinstance(row, dict) and row.get('status') == 'pending'
               for key, row in actions.items()):
            raise PendingError('生存仍有未决开战或结算，禁止领奖、重置或覆盖原动作')
        self.claim_survival_rewards(account)
        self.reconcile_survival_result_confirmations(account)
        if self.state.action_status(account.name, "survival_second_run_started") != "done":
            frame = self.capture()
            reset_exhausted = any(self.template(name, (851,769,1251,1031), frame)[0]
                                  for name in ('survival_reset_zero_text.bmp', 'survival_reset_zero_digit.bmp'))
            if reset_exhausted:
                self.close_survival(account)
                slot.complete = True
                self.state.set_module(account.name, "survival", "done", "满分领奖、重置次数0且已回村")
                self.event('combat_module_done', account, mode='survival', reset_exhausted=True)
                return
            self._ensure_survival_reset(account)
            from copy import deepcopy
            archives = self.state.accounts[account.name].setdefault('survival_round_archives', {})
            archives.setdefault('inline_run_1', dict(
                archived_at=now_iso(),
                actions=deepcopy({key: value for key, value in actions.items()
                                  if key.startswith('survival_')})))
            self.state.save_account(account.name)
            self.event('survival_first_run_archived', account,
                       actions={key: value for key, value in self.state.accounts.get(account.name, {}).get('actions', {}).items()
                                if key.startswith('survival_')})
            for step in range(1, self._combat_limit("survival") + 1):
                action = self._combat_action_name("survival", step)
                for key in (action, f"{action}_start", f"survival_select_step_{step}", f"survival_result_confirm_{step}"):
                    self.state.set_action(account.name, key, "new", "首轮完成并重置，开始第二轮")
            for key in ("survival_claim_rewards", "survival_claim_yes", "survival_reward_result_confirm"):
                self.state.set_action(account.name, key, "new", "首轮奖励已领取，第二轮可再次领取")
            self.state.set_action(account.name, "survival_second_run_started", "done", "首轮满分领奖后已确认重置")
            self.state.set_action(account.name, "survival_route_c_first", "new", "重置后第二轮从0/220开始")
            slot.running = False
            slot.next_index = 1
            slot.start_confirmed = False
            slot.start_requested_at = None
            self.event("survival_second_run_started", account)
            return
        self.close_survival(account)
        slot.complete = True
        self.state.set_module(account.name, "survival", "done")
        self.event("combat_module_done", account, mode="survival")

    def run_combat_interleaved(self, mode: str) -> None:
        """Run arena or survival using a cooperative six-tab round robin.

        One OS thread and one foreground lease are retained.  The scheduler
        starts at most one battle per account, rotates to the next tab while
        those battles run, then polls result dialogs and starts the next round.
        This is the safe equivalent of concurrent starts for a shared window.
        """
        if mode not in ("arena", "survival"):
            raise ValueError(f"unsupported combat mode: {mode}")
        required = (
            [
                "arena_toolbar_core.bmp", "arena_ready_anchor.bmp", "arena_ranked_button_current.bmp", "arena_casual_button_current.bmp",
                "arena_battle_button_current.bmp",
                *[f'arena_tokens_remaining_{count}.bmp' for count in range(4)],
                "arena_condition_unmet.bmp", "arena_reward_claim.bmp", "arena_matching_anchor.bmp", "arena_result_confirm.bmp",
                "arena_rankup_confirm.bmp", "arena_defeat_confirm.bmp", "arena_battle_frame_anchor.bmp", "battle_auto_off.bmp", "battle_auto_on.bmp", "battle_auto_on_current.bmp", "battle_auto_on_current_core.bmp",
            ]
            if mode == "arena"
            else [
                "survival_toolbar_current_core.bmp",
                "survival_anchor.bmp", "survival_close.bmp", "survival_reset.bmp", "survival_reset_confirm_yes.bmp", "survival_reset_confirm_no.bmp", "survival_score_0.bmp", "survival_score_80.bmp", "survival_score_140.bmp", "survival_score_160.bmp", "survival_score_200.bmp", "survival_complete_220.bmp",
                "survival_grade_ss.bmp", "survival_grade_s.bmp", "survival_grade_a_current.bmp", "survival_grade_b_current.bmp", "survival_grade_c.bmp", "survival_fight_start.bmp", "survival_fight_start_core.bmp", "survival_result_confirm.bmp",
                "battle_auto_off.bmp", "battle_auto_on.bmp", "battle_auto_on_current.bmp", "battle_auto_on_current_core.bmp", "battle_speed_x1.png", "battle_speed_x2.png", "survival_claim_all.bmp", "survival_claim_confirm_yes.bmp",
                "survival_reward_result_confirm.bmp", "survival_final_claimed.bmp",
            ]
        )
        self.ensure_assets(required)
        eligible_accounts = self.combat_eligible_accounts()
        if not eligible_accounts:
            raise PendingError(f"no online, positively identified account is available for {mode}")
        if mode == "arena":
            self.arena_eligible_accounts = {account.name for account in eligible_accounts}
            skipped = [account.name for account in self.accounts if account.name not in self.arena_eligible_accounts]
            self.event("arena_unavailable_accounts_skipped", accounts=skipped, eligible=sorted(self.arena_eligible_accounts))
        slots = [CombatSlot(account, mode, next_index=self._next_combat_index(account, mode)) for account in eligible_accounts]
        for slot in slots:
            if self._account_module_complete(slot.account, mode):
                # A repeat of a fully settled command is read-only.  Do not
                # reopen a page or manufacture a new battle just to prove the
                # same completion a second time.
                slot.complete = True
        self.event("combat_interleaved_start", mode=mode, accounts=[slot.account.name for slot in slots])
        for slot in slots:
            try:
                if slot.complete:
                    continue
                if mode == "arena":
                    self._prepare_arena_slot(slot)
                else:
                    self._prepare_survival_slot(slot)
                # For arena, the guarded start click is journaled and the slot
                # becomes pollable immediately. This lets the next account start
                # without waiting for the first account's full transition.
                if not slot.complete and not slot.running:
                    if mode == "arena":
                        self._start_arena_slot(slot)
                    else:
                        self._start_survival_slot(slot)
                if slot.running:
                    slot.next_poll_at = time.monotonic() + (
                        ARENA_POLL_INTERVAL_SECONDS if mode == 'arena' else 0.4)
            except AccountOffline as exc:
                slot.unavailable = True
                self.state.set_module(slot.account.name, mode, 'pending', str(exc))
                self.event('combat_account_offline_skipped', slot.account, mode=mode)
                continue
            except (PendingError, SafetyError) as exc:
                self.state.set_module(slot.account.name, mode, "pending", str(exc))
                self.state.add_pending(slot.account.name, mode, str(exc))
                self.event("combat_slot_pending", slot.account, mode=mode, detail=str(exc))
                if mode in ('arena', 'survival'):
                    action = self._combat_action_name(mode, slot.next_index)
                    if self.state.action_status(slot.account.name, action) == 'pending':
                        slot.running = True
                        if mode == 'arena' and slot.start_requested_at is None:
                            slot.start_requested_at = time.monotonic()
                    if not slot.running:
                        if (self.active == slot.account
                                and not self.ensure_account_main_before_release(slot.account, mode)):
                            raise LeaseBlocked(f"{mode} preparation did not return through a verified main page") from exc
                        slot.needs_prepare = True
                    slot.pending_checks += 1
                    slot.next_poll_at = time.monotonic() + (
                        ARENA_POLL_INTERVAL_SECONDS if mode == 'arena' else 0.4)
                    limit = ARENA_SLOT_PENDING_LIMIT if mode == 'arena' else SURVIVAL_SLOT_PENDING_LIMIT
                    if slot.pending_checks >= limit:
                        slot.unavailable = True
                        self.event('combat_slot_quarantined_after_pending', slot.account, mode=mode,
                                   checks=slot.pending_checks, detail=str(exc))
                    continue
                if self.active == slot.account and not slot.running and not self.ensure_account_main_before_release(slot.account, mode):
                    raise LeaseBlocked(f"{mode} preparation did not return through a verified main page") from exc
                raise
        pause_requested = False
        while any(not slot.complete and not slot.unavailable for slot in slots):
            self.check_stop()
            if self.pause_after_module.is_set():
                pause_requested = True
            progress = False
            for slot in slots:
                if slot.complete or slot.unavailable:
                    continue
                if mode in ('arena', 'survival') and time.monotonic() < slot.next_poll_at:
                    continue
                if pause_requested and not slot.running:
                    continue
                try:
                    frame = self.switch_account_for_combat(slot.account, mode, running=slot.running)
                    if slot.running:
                        if mode == "arena":
                            self._poll_arena_slot(slot)
                        else:
                            self._poll_survival_slot(slot)
                    else:
                        if mode == "arena":
                            if slot.needs_prepare:
                                self._prepare_arena_slot(slot)
                                slot.needs_prepare = False
                            if not slot.complete and not slot.running:
                                self._start_arena_slot(slot)
                        else:
                            if slot.needs_prepare:
                                self._prepare_survival_slot(slot)
                                slot.needs_prepare = False
                            if not slot.complete and not slot.running:
                                self._start_survival_slot(slot)
                    if not slot.running and slot.next_index > self._combat_limit(mode, slot.account):
                        if mode == "arena":
                            self._finish_arena_slot(slot)
                        else:
                            self._finish_survival_slot(slot)
                    slot.pending_checks = 0
                    if slot.running:
                        slot.next_poll_at = time.monotonic() + (
                            ARENA_POLL_INTERVAL_SECONDS if mode == 'arena' else 0.4)
                    progress = True
                except AccountOffline as exc:
                    slot.unavailable = True
                    self.state.set_module(slot.account.name, mode, 'pending', str(exc))
                    self.event('combat_account_offline_skipped', slot.account, mode=mode)
                    continue
                except BattleStalled as exc:
                    slot.unavailable = True
                    self.state.set_module(slot.account.name, mode, 'pending', str(exc))
                    self.state.add_pending(slot.account.name, mode, str(exc))
                    self.event('combat_account_stalled_skipped', slot.account, mode=mode, detail=str(exc))
                    if getattr(self, '_task_observation', None):
                        raise  # The multi-solo owner excludes this label and releases its observation.
                    continue
                except (PendingError, SafetyError) as exc:
                    self.state.set_module(slot.account.name, mode, "pending", str(exc))
                    self.state.add_pending(slot.account.name, mode, str(exc))
                    self.event("combat_slot_pending", slot.account, mode=mode, detail=str(exc), running=slot.running)
                    if mode in ('arena', 'survival'):
                        action = self._combat_action_name(mode, slot.next_index)
                        if self.state.action_status(slot.account.name, action) == 'pending':
                            slot.running = True
                            if mode == 'arena' and slot.start_requested_at is None:
                                slot.start_requested_at = time.monotonic()
                        slot.pending_checks += 1
                        interval = ARENA_POLL_INTERVAL_SECONDS if mode == 'arena' else 0.4
                        if slot.running:
                            slot.next_poll_at = time.monotonic() + interval
                        else:
                            if self.active == slot.account and not self.ensure_account_main_before_release(slot.account, mode):
                                raise LeaseBlocked(f"{mode} slot did not return through a verified main page") from exc
                            slot.needs_prepare = True
                            slot.next_poll_at = time.monotonic() + interval
                        limit = ARENA_SLOT_PENDING_LIMIT if mode == 'arena' else SURVIVAL_SLOT_PENDING_LIMIT
                        if slot.pending_checks >= limit:
                            slot.unavailable = True
                            self.event('combat_slot_quarantined_after_pending', slot.account, mode=mode,
                                       checks=slot.pending_checks, detail=str(exc))
                        continue
                    if self.active == slot.account and not slot.running and not self.ensure_account_main_before_release(slot.account, mode):
                        raise LeaseBlocked(f"{mode} slot did not return through a verified main page") from exc
                    raise
            if pause_requested and not any(slot.running and not slot.unavailable for slot in slots):
                raise PendingError(f"{mode} interleaved stage paused before completion")
            if not progress:
                self.wait_interruptible(0.4)
        if not all(self._account_module_complete(slot.account, mode) for slot in slots):
            raise PendingError(f"{mode} interleaved stage unresolved")
        self.state.resolve_pending("global", mode)
        self.event("combat_interleaved_complete", mode=mode)

    def module_arena(self, account: Account) -> None:
        required = [
            "arena_toolbar_core.bmp", "arena_ready_anchor.bmp", "arena_ranked_button_current.bmp", "arena_casual_button_current.bmp",
            "arena_battle_button_current.bmp",
            *[f'arena_tokens_remaining_{count}.bmp' for count in range(4)],
            "arena_condition_unmet.bmp", "arena_reward_claim.bmp", "arena_matching_anchor.bmp", "arena_result_confirm.bmp",
            "arena_loading_anchor.bmp", "arena_battle_frame_anchor.bmp", "arena_victory_banner.bmp", "arena_rankup_confirm.bmp", "arena_defeat_confirm.bmp",
            "arena_defeat_banner.bmp",
            "arena_rankdown_banner.bmp", "arena_rankdown_confirm.bmp",
            "arena_lower_difficulty.bmp", "arena_lower_confirm.bmp", "arena_lower_cancel.bmp",
            "arena_free_remaining_1.bmp", "arena_free_remaining_2.bmp",
            "battle_auto_off.bmp", "battle_auto_on.bmp", "battle_auto_on_current.bmp", "battle_auto_on_current_core.bmp",
        ]
        self.ensure_assets(required)
        self.prepare_main(account, clear_notifications=False)
        self.ensure_activity_toolbar(account, "arena_toolbar_core.bmp", (1201,69,1501,169))
        if not self.click_template(account, "arena_toolbar_core.bmp", (1201, 69, 1501, 169), 22, 22, "arena_toolbar", ["anchor_top_menu.bmp"]):
            raise PendingError("arena toolbar missing")
        if not self.wait_template("arena_ready_anchor.bmp", (851, 199, 1451, 449), 8):
            raise PendingError("arena page timeout")
        # The title anchor appears well before the opponent list and battle
        # controls.  Wait for actual task/button content; a single early frame
        # must never be interpreted as "no ranked action".
        self._wait_arena_ready_content(account)
        for battle_index in range(1, self._combat_limit("arena", account) + 1):
            # A prior run may already contain the positive victory marker while
            # this compatibility path was interrupted before its final close.
            # Reconcile that state without opening a second battle.
            if self._arena_victory_confirmed(account):
                break
            # Only the positively visible left-side daily claim controls are
            # actionable.  Their task conditions are never fabricated or
            # bypassed; the module closes only after both claim records exist.
            self._claim_arena_tasks(account)

            Win32.move(*self.screen_point(100, 500))
            self.wait_interruptible(0.25)
            if not self.template("arena_casual_button_current.bmp", (760, 719, 1050, 919))[0]:
                if not self.template("arena_condition_unmet.bmp", (301, 469, 541, 829))[0]:
                    # No uncompleted task and no battle button means rewards are
                    # settled.  This is checked only after the 12-second load gate.
                    break
                raise PendingError("arena tasks remain but casual battle control is unavailable")

            action = f"arena_casual_battle_{battle_index}"
            status = self.state.action_status(account.name, action)
            matching_now = self.template("arena_matching_anchor.bmp", (601, 349, 1201, 751))[0]
            if status == "pending":
                # We are positively back on the arena page, so a prior battle
                # start has completed even if the previous process was interrupted.
                if matching_now:
                    # A matching dialog is positive evidence that the prior
                    # start click was delivered; continue waiting for its
                    # battle/result transition without clicking again.
                    status = "done"
                elif self.template("arena_ready_anchor.bmp", (851, 199, 1451, 449))[0]:
                    if not self._arena_victory_confirmed(account):
                        raise PendingError("arena pending battle returned without a positive victory marker")
                    self.commit_action(account, action, "arena page visible after positively recorded victory")
                    continue
                else:
                    raise PendingError("arena battle outcome unknown; refusing duplicate start")
            transitioned = False
            if status == "done":
                if matching_now or not self.template("arena_ready_anchor.bmp", (851, 199, 1451, 449))[0]:
                    transitioned = True
                else:
                    continue
            else:
                if not self.click_template(account, "arena_casual_button_current.bmp", (760, 719, 1050, 919), 88, 29, action, ["arena_ready_anchor.bmp", "arena_casual_button_current.bmp"], irreversible=True):
                    raise PendingError("arena casual action unavailable")
                transition_deadline = time.monotonic() + 30
                while time.monotonic() < transition_deadline:
                    self.check_stop()
                    if self.template("arena_matching_anchor.bmp", (601, 349, 1201, 751))[0]:
                        transitioned = True
                        break
                    if not self.template("arena_ready_anchor.bmp", (851, 199, 1451, 449))[0]:
                        transitioned = True
                        break
                    self.wait_interruptible(0.2)
            if not transitioned:
                raise PendingError("arena did not enter matching or battle; start remains pending")
            result_index = 0
            watchdog = BattleWatchdog.from_config(getattr(self, 'config', {}), started_at=time.monotonic())
            battle_deadline = watchdog.started_at + watchdog.max_seconds
            while time.monotonic() < battle_deadline:
                self.check_stop()
                frame = self.capture()
                if not any(self.template(name, roi, frame)[0] for name, roi in (
                        ('arena_result_confirm.bmp', (651, 449, 1151, 829)),
                        ('arena_defeat_confirm.bmp', (801, 580, 1101, 760)),
                        ('arena_rankup_confirm.bmp', (801, 600, 1101, 760)))):
                    check_battle_watchdog(self, watchdog, account, frame,
                                         f'arena_battle_{battle_index}', mode='arena')
                # The arena-ready anchor remains visible behind the matching
                # dialog.  Never treat that background anchor as a completed
                # battle; wait for the modal to disappear first.
                if self.template("arena_matching_anchor.bmp", (601, 349, 1201, 751))[0]:
                    self.wait_interruptible(0.5)
                    continue
                if self._arena_opening_once(account, battle_index, frame):
                    self.wait_interruptible(.4)
                    continue
                if self.template("arena_result_confirm.bmp", (651, 449, 1151, 829))[0]:
                    result_index = self._next_arena_result_index(account, battle_index, result_index)
                    result_action = f"arena_result_confirm_{battle_index}_{result_index}"
                    if self.state.action_status(account.name, result_action) == "pending":
                        raise PendingError("arena result confirmation outcome unknown; refusing duplicate")
                    if not self.click_template(
                        account,
                        "arena_result_confirm.bmp",
                        (651, 449, 1151, 829),
                        64,
                        19,
                        result_action,
                        ["arena_result_confirm.bmp"],
                        irreversible=True,
                    ):
                        raise PendingError("arena result confirmation disappeared")
                    if not self.wait_absent("arena_result_confirm.bmp", (651, 449, 1151, 829), 3):
                        raise PendingError("arena result confirmation remained; refusing duplicate")
                    self.commit_action(account, result_action, "arena result confirmation disappeared")
                    continue
                if self.template("arena_defeat_confirm.bmp", (801, 580, 1101, 760))[0]:
                    if not self.click_template(
                        account,
                        "arena_defeat_confirm.bmp",
                        (801, 580, 1101, 760),
                        70,
                        28,
                        f"arena_defeat_confirm_{battle_index}",
                        ["arena_defeat_confirm.bmp"],
                    ):
                        raise PendingError("arena defeat confirmation disappeared")
                    if not self.wait_absent("arena_defeat_confirm.bmp", (801, 580, 1101, 760), 5):
                        raise PendingError("arena defeat confirmation remained after click")
                # A rank-up can enqueue more than one confirmation (new
                # ninja reveal(s) followed by the rank promotion screen).  A
                # single click followed by ``wait_absent`` used to quarantine
                # the account when the next prompt replaced the first one.
                # Drain a bounded sequence instead; every click still requires
                # the positively matched button and the sequence fails closed
                # if the UI never settles.
                for rankup_index in range(1, 9):
                    rankup = self.template("arena_rankup_confirm.bmp", (801, 600, 1101, 760))
                    if not rankup[0]:
                        break
                    rankup_action = f"arena_rankup_confirm_{battle_index}_{rankup_index}"
                    if self.state.action_status(account.name, rankup_action) == "pending":
                        raise PendingError("arena rank-up confirmation outcome unknown; refusing duplicate")
                    if self.state.action_status(account.name, rankup_action) == "done":
                        self._record_arena_victory(account, battle_index, "历史段位升级确认记录证明竞技场胜利")
                        self.wait_interruptible(0.35)
                        continue
                    if not self.click_template(
                        account,
                        "arena_rankup_confirm.bmp",
                        (801, 600, 1101, 760),
                        70,
                        28,
                        rankup_action,
                        ["arena_rankup_confirm.bmp"],
                    ):
                        raise PendingError("arena rank-up confirmation disappeared")
                    self.commit_action(account, rankup_action, "rank-up confirmation matched and acknowledged; victory verified")
                    self._record_arena_victory(account, battle_index, "段位升级确认框已出现并完成确认")
                    self.wait_interruptible(0.35)
                else:
                    raise PendingError("arena rank-up confirmation sequence did not settle")
                # ``battle_auto_on`` remains visible behind result/defeat/
                # rank-up overlays.  Evaluate those dialogs first; treating
                # the background auto badge as an unconditional continue can
                # otherwise stall forever on a real victory confirmation.
                if self._battle_auto_on_visible():
                    # Auto mode is already enabled; its presence is still
                    # positive evidence that the battle has not returned to
                    # the arena page.
                    self.wait_interruptible(0.5)
                    continue
                if self.template("arena_ready_anchor.bmp", (851, 199, 1451, 449))[0]:
                    if not self._arena_victory_confirmed(account):
                        raise PendingError("arena returned without a positive victory marker")
                    self.commit_action(account, action, "returned to arena page")
                    self._wait_arena_ready_content(account)
                    break
                self.wait_interruptible(0.5)
            else:
                raise PendingError("arena battle timeout")
        else:
            raise PendingError("arena configured one-win target was not positively completed")

        self._claim_arena_tasks(account)
        if not self._arena_victory_confirmed(account):
            raise PendingError("arena has no positively confirmed victory")
        if not self._arena_daily_rewards_complete(account):
            raise PendingError("arena left-side daily rewards are not both positively claimed")
        if not self.click_template(
            account, "survival_close.bmp", (1550, 60, 1700, 200), 20, 20,
            "arena_close", ["arena_ready_anchor.bmp", "survival_close.bmp"], threshold=0.84, anchor_threshold=0.84,
        ):
            raise PendingError("竞技场关闭控件视觉匹配失败")
        if not self.wait_until_main(12):
            raise PendingError("arena page did not close")

    def module_survival(self, account: Account) -> None:
        original = self.accounts
        try:
            self.accounts = [account]
            self.run_combat_interleaved("survival")
        finally:
            self.accounts = original

    def run_survival_battle(self, account: Account, step: int) -> None:
        """Compatibility entry; use the shared audited selection/result flow."""
        slot = CombatSlot(account, "survival", next_index=step)
        self._start_survival_slot(slot)
        deadline = time.monotonic() + 600
        while slot.running and time.monotonic() < deadline:
            self.check_stop()
            self._poll_survival_slot(slot)
            self.wait_interruptible(.4)
        if slot.running:
            raise PendingError("survival battle timeout; battle action remains pending")

    def claim_survival_rewards(self, account: Account) -> None:
        run_index = 2 if self.state.action_status(account.name, "survival_second_run_started") == "done" else 1
        action = f"survival_claim_rewards_run_{run_index}"
        # A later slot poll can revisit this function after the reward dialog
        # has closed. The enabled and claimed buttons share almost all pixels;
        # the completed receipt must prevent another irreversible claim.
        if self.state.action_status(account.name, action) == "done":
            self.event("survival_rewards_already_verified", account, target=action,
                       detail="本轮奖励已正向确认领取，重复调度只复用完成记录")
            return
        reward_result_visible = self.template("survival_reward_result_confirm.bmp", (701, 569, 1151, 819))[0]
        if not reward_result_visible and self.template("survival_final_claimed.bmp", (1151, 769, 1451, 1031), threshold=0.92)[0]:
            if self.state.action_status(account.name, action) != "done":
                self.commit_action(account, action, "220分奖励箱已确认领取；当前220分页已显示最终领取态")
            return
        if self.state.action_status(account.name, action) == "pending":
            if not reward_result_visible:
                raise PendingError("survival reward outcome unknown; refusing duplicate claim")
        else:
            if not self.click_template(account, "survival_claim_all.bmp", (1251, 819, 1501, 1031), 43, 14, action, ["survival_complete_220.bmp"], irreversible=True, threshold=0.90, anchor_threshold=0.98):
                raise PendingError("survival claim-all missing")
            if self.wait_template("survival_claim_confirm_yes.bmp", (701, 469, 1151, 719), 5):
                if not self.click_template(account, "survival_claim_confirm_yes.bmp", (701, 469, 1151, 719), 52, 17, "survival_claim_yes", ["survival_claim_confirm_yes.bmp"]):
                    raise PendingError("survival claim confirmation disappeared")
            elif not self.template("survival_reward_result_confirm.bmp", (701, 569, 1151, 819))[0]:
                raise PendingError("survival claim produced neither confirmation nor reward result")
        if not self.wait_template("survival_reward_result_confirm.bmp", (701, 569, 1151, 819), 8):
            raise PendingError("survival reward result missing")
        if not self.click_template(account, "survival_reward_result_confirm.bmp", (701, 569, 1151, 819), 51, 17, f"survival_reward_result_confirm_run_{run_index}", ["survival_reward_result_confirm.bmp"]):
            raise PendingError("survival reward result confirmation disappeared")
        if not self.wait_template("survival_final_claimed.bmp", (1151, 769, 1451, 1031), 8, threshold=0.92):
            raise PendingError("survival claimed state missing")
        self.commit_action(account, action, "final claimed state verified")

    def close_survival(self, account: Account) -> None:
        if not self.click_template(account, "survival_close.bmp", (1585, 77, 1685, 177), 20, 20, "survival_close", ["survival_anchor.bmp", "survival_close.bmp"]):
            raise PendingError("survival X missing")
        if not self.wait_until_main(6):
            raise PendingError("survival page did not close through its verified X")

    def wait_main_after_close(self, account: Account, page: str, roi, seconds: float = 12) -> bool:
        """Observe a closed panel and three stable main frames without another click."""
        Win32.move(*self.screen_point(1100, 50))
        deadline = time.monotonic() + seconds
        stable = 0
        observations = {}
        frame = None
        while time.monotonic() < deadline:
            self.check_stop()
            frame = self.capture()
            observations = {
                "closed": not self.template(page, roi, frame)[0],
                "top": self.template("anchor_top_menu.bmp", (500,69,1500,189), frame)[1],
                "right": self.template("anchor_right_panel.bmp", (1569,289,1919,489), frame)[1],
                "main": self.main_ready(frame),
            }
            stable = stable + 1 if observations["closed"] and observations["main"] else 0
            if stable >= 3:
                self.event("panel_closed_main_verified", account, panel=page, frames=stable)
                return True
            self.wait_interruptible(.3)
        evidence = self.save_raw_evidence(frame, account, "panel_close_unverified", "pending") if frame is not None else None
        self.event("panel_close_unverified", account, panel=page,
                   observations=observations, evidence=str(evidence or ""))
        return False

    def wait_until_main(self, seconds: float) -> bool:
        deadline = time.monotonic() + seconds
        while time.monotonic() < deadline:
            self.check_stop()
            if self.main_ready():
                return True
            time.sleep(0.2)
        return False

    def run_independent(self) -> None:
        modules: list[tuple[str, Callable[[Account], None]]] = [
            ("mail", self.module_mail),
            ("welfare", self.module_welfare),
        ]
        for account in self.available_accounts():
            for name, fn in modules:
                if self.check_pause_boundary():
                    raise PendingError("independent stage paused before completion")
                try:
                    self.run_module(account, name, fn)
                except LeaseBlocked as exc:
                    self.event("account_quarantined", account, module=name, detail=str(exc), action="no_further_tab_switch")
                    raise PendingError(f"{account.name} quarantined during {name}; automatic stage stopped") from exc
        unfinished = [
            f"{account.name}:{name}"
            for account in self.available_accounts()
            for name, _fn in modules
            if not self._account_module_complete(account, name)
        ]
        if unfinished:
            detail = "independent stage unresolved: " + ", ".join(unfinished)
            self.state.add_pending("global", "independent", detail)
            raise PendingError(detail)
        self.state.resolve_pending("global", "independent")
        self.state.global_state["stage"] = "team"
        self.state.save_global()
        self.event("stage_complete", stage="independent")

    def run_accounts_module(self, name: str, fn: Callable[[Account], None]) -> None:
        eligible = self.available_accounts()
        skipped = [account.name for account in self.accounts if account.name not in {item.name for item in eligible}]
        if skipped:
            self.event("module_unavailable_accounts_skipped", module=name, accounts=skipped, eligible=[item.name for item in eligible])
        for account in eligible:
            if self.check_pause_boundary():
                return
            try:
                self.run_module(account, name, fn)
            except AccountOffline as exc:
                self.state.set_module(account.name, name, 'pending', str(exc))
                self.event('module_account_offline_skipped', account, module=name)
                continue
            except LeaseBlocked as exc:
                self.event("account_quarantined", account, module=name, detail=str(exc), action="no_further_tab_switch")
                raise

    def run_post_team_combat(self) -> None:
        """Run solo combat only after all verified team leases are released.

        Each stage is a cooperative six-tab round robin: one battle is started
        per account before result polling resumes.  This keeps the shared
        foreground lease invariant while overlapping server wait time.
        """
        if self.check_pause_boundary():
            raise PendingError("post-team combat paused before completion")
        # Keep the small dependency-injected unit doubles used by the legacy
        # safety tests working: production StateStore always exposes
        # ``action_status`` and therefore takes the interleaved path below.
        if not callable(getattr(self.state, "action_status", None)):
            for account in self.accounts:
                try:
                    self.run_module(account, "arena", self.module_arena)
                    self.run_module(account, "survival", self.module_survival)
                except LeaseBlocked as exc:
                    self.event("account_quarantined", account, module="combat", detail=str(exc), action="no_further_tab_switch")
                    raise PendingError(f"{account.name} quarantined during post-team combat; post-team stage stopped") from exc
            return
        try:
            self.run_combat_interleaved("arena")
        except LeaseBlocked as exc:
            raise PendingError("post-team combat quarantined; post-team stage stopped") from exc
        if not self._accounts_module_complete("arena"):
            raise PendingError("post-team combat unresolved: arena")
        if self.check_pause_boundary():
            raise PendingError("post-team combat paused before survival")
        try:
            self.run_combat_interleaved("survival")
        except LeaseBlocked as exc:
            raise PendingError("post-team combat quarantined; post-team stage stopped") from exc
        if not self._accounts_module_complete("survival"):
            raise PendingError("post-team combat unresolved: survival")
        self.state.resolve_pending("global", "post_team_combat")

    def dissolve_team_after_activities(self, leader: Account) -> None:
        """Dissolve only after every configured leader activity is terminal."""
        if self.state.action_status(leader.name, "dissolve_team_after_activities") == "done":
            self.event("team_dissolve_skip_done", leader)
            return
        self.ensure_assets(["team_toolbar.bmp", "team_room_close.bmp", "team_dissolve_button.bmp"])
        frame = self.capture()
        if not self.main_ready(frame) or not wanted_task_list_is_empty(frame):
            raise PendingError("team activity task list is not positively empty; team retained")
        if not self.click_template(leader, "team_toolbar.bmp", (1685, 70, 1735, 120), 12, 11, "open_team_room_after_wanted", ["anchor_top_menu.bmp"]):
            raise PendingError("team toolbar immediately left of Friends is missing")
        if not self.wait_template("team_room_close.bmp", (1200, 260, 1280, 340), 6):
            raise PendingError("team room did not open")
        if not self.click_template(leader, "team_dissolve_button.bmp", (1060, 315, 1160, 380), 25, 10, "dissolve_team_after_activities", ["team_room_close.bmp"], irreversible=True):
            raise PendingError("leader dissolve button missing; team retained")
        if self.wait_template("team_room_close.bmp", (1200, 260, 1280, 340), 3):
            raise PendingError("dissolve outcome unknown; refusing a second click")
        self.commit_action(leader, "dissolve_team_after_activities", "team room closed immediately after one dissolve click")

    def dissolve_team_after_wanted(self, leader: Account) -> None:
        """Compatibility wrapper retained for old callers; never called mid-flow."""
        self.dissolve_team_after_activities(leader)

    def read_power(self, account: Account) -> int:
        names = [f"account_power_digit_{digit}.bmp" for digit in range(10)]
        self.ensure_assets(names)
        self.switch_account(account)
        frame = self.capture()
        found: list[tuple[float, int, int]] = []
        for digit in range(10):
            template = ASSETS / names[digit]
            for score, x, _y in self.templates.find_all(frame, template, POWER_ROI):
                found.append((score, x, digit))
                self.event("power_digit", account, digit=digit, x=x, score=score)
        if not found:
            raise PendingError("main power unreadable")
        found.sort(key=lambda row: (-row[0], row[1]))
        accepted: list[tuple[int, int]] = []
        for _score, x, digit in found:
            if any(abs(x - prior_x) <= 3 for prior_x, _prior_digit in accepted):
                continue
            accepted.append((x, digit))
        accepted.sort()
        text = "".join(str(digit) for _x, digit in accepted)
        return int(text)

    def select_team_leader(self, team: Team) -> str:
        powers = {name: self.read_power(self.by_name[name]) for name in team.members}
        leader = choose_leader(team.members, powers)
        self.state.global_state.setdefault("teams", {})[str(team.index)] = {"members": list(team.members), "powers": powers, "leader": leader}
        self.state.save_global()
        self.event("team_leader", team=team.index, leader=leader, powers=powers)
        return leader

    def verified_team_profile(self, team: Team) -> tuple[Account, list[Account], list[str], str, str]:
        leader_name = self.config.get(f"Team{team.index}VerifiedLeader", "")
        if leader_name not in team.members or leader_name not in self.by_name:
            raise PendingError(f"Team{team.index} verified leader is not configured")
        invitees = [self.by_name[name] for name in team.members if name != leader_name]
        row_templates = parse_csv(self.config.get(f"Team{team.index}FriendRowTemplates", ""))
        invite_leader_template = self.config.get(f"Team{team.index}InviteLeaderTemplate", "")
        # Keep the historical configuration key so existing enrolled BMPs and
        # ledgers remain resumable.  Its content is a positive template for
        # the configured team size, including a two-person bond team.
        member_template = self.config.get(f"Team{team.index}MemberTemplate", "") or self.config.get(
            f"Team{team.index}ThreeMemberTemplate", ""
        )
        expected_invitees = team.member_count - 1
        if expected_invitees not in (1, 2) or len(row_templates) != expected_invitees or not invite_leader_template or not member_template:
            raise PendingError(f"Team{team.index} verified {team.member_label} profile is incomplete")
        return self.by_name[leader_name], invitees, row_templates, invite_leader_template, member_template

    def open_team_room(self, leader: Account, label: str) -> None:
        core = "team_toolbar_core_current.bmp"
        if (ASSETS / core).is_file() and self.click_template(
                leader, core, (1685, 70, 1735, 120), 9, 6, label,
                ["anchor_top_menu.bmp"], threshold=.98, anchor_threshold=.98):
            return
        if not self.click_template(leader, "team_toolbar.bmp", (1685, 70, 1735, 120), 12, 11, label,
                                   ["anchor_top_menu.bmp"], threshold=0.88):
            raise PendingError("team toolbar immediately left of Friends is missing")

    def select_team_create_type(self, team: Team, leader: Account) -> None:
        """Select the configured team activity before the irreversible create click.

        The team-room selector is not just a filter: a team created as 普通队伍
        cannot expose the 组队副本 daily entry.  Keep the dropdown and option
        clicks behind their own templates, then require the selected text to be
        visible before allowing the create confirmation click.
        """
        requested = self.config.get(f"Team{team.index}Type", "group_instance")
        if requested != "group_instance":
            raise PendingError(f"unsupported Team{team.index}Type: {requested}")
        self.ensure_assets([
            "team_create_type_dropdown.bmp",
            "team_create_group_option.bmp",
            "team_create_group_type.bmp",
        ])
        roi = (850, 480, 1120, 560)
        if self.template("team_create_group_type.bmp", roi, threshold=0.90)[0]:
            return
        if not self.click_template(
            leader,
            "team_create_type_dropdown.bmp",
            (850, 480, 1120, 560),
            122,
            17,
            f"team{team.index}_open_type_dropdown",
            ["team_create_confirm.bmp"],
            threshold=0.90,
        ):
            raise PendingError("team-create type dropdown missing")
        option_roi = (850, 525, 1120, 620)
        if not self.wait_template("team_create_group_option.bmp", option_roi, 4, threshold=0.90):
            raise PendingError("team-create 组队副本 option missing")
        if not self.click_template(
            leader,
            "team_create_group_option.bmp",
            option_roi,
            60,
            12,
            f"team{team.index}_select_group_instance",
            ["team_create_confirm.bmp", "team_create_group_option.bmp"],
            threshold=0.90,
        ):
            raise PendingError("team-create 组队副本 option disappeared")
        if not self.wait_template("team_create_group_type.bmp", roi, 4, threshold=0.90):
            raise PendingError("team-create 组队副本 selection was not verified")

    def ensure_team_room_type(self, team: Team, leader: Account) -> None:
        """Reconcile the activity type of an already-created team.

        Older ledger entries can prove that a team was created without proving
        that it is a 组队副本 team.  Re-open the room, change only the verified
        selector when needed, and close it through the room X before looking
        for the daily entry.  No invitation or create action is repeated.
        """
        requested = self.config.get(f"Team{team.index}Type", "group_instance")
        if requested != "group_instance":
            raise PendingError(f"unsupported Team{team.index}Type: {requested}")
        self.ensure_assets([
            "team_room_type_dropdown.bmp",
            "team_create_group_option.bmp",
            "team_room_group_type.bmp",
            "team_room_close.bmp",
        ])
        self.switch_account(leader)
        self.prepare_main(leader)
        self.open_team_room(leader, f"team{team.index}_open_type_reconcile")
        if not self.wait_template("team_room_close.bmp", (1200, 260, 1280, 340), 6):
            raise PendingError("team room did not open for type reconciliation")
        roi = (1000, 740, 1250, 830)
        if not self.template("team_room_group_type.bmp", roi, threshold=0.90)[0]:
            if not self.click_template(
                leader,
                "team_room_type_dropdown.bmp",
                roi,
                110,
                15,
                f"team{team.index}_open_room_type_dropdown",
                ["team_room_close.bmp"],
                threshold=0.90,
            ):
                raise PendingError("existing team type dropdown missing")
            option_roi = (1000, 800, 1300, 950)
            if not self.wait_template("team_create_group_option.bmp", option_roi, 4, threshold=0.90):
                raise PendingError("existing team 组队副本 option missing")
            if not self.click_template(
                leader,
                "team_create_group_option.bmp",
                option_roi,
                60,
                12,
                f"team{team.index}_select_existing_group_instance",
                ["team_room_close.bmp", "team_create_group_option.bmp"],
                threshold=0.90,
            ):
                raise PendingError("existing team 组队副本 option disappeared")
            if not self.wait_template("team_room_group_type.bmp", roi, 4, threshold=0.90):
                raise PendingError("existing team 组队副本 selection was not verified")
        if not self.click_template(
            leader,
            "team_room_close.bmp",
            (1200, 260, 1280, 340),
            20,
            20,
            f"team{team.index}_close_type_reconcile",
            ["team_room_close.bmp", "team_room_group_type.bmp"],
        ):
            raise PendingError("team-room X missing after type reconciliation")
        if not self.wait_until_main(6):
            raise PendingError("team room did not close after type reconciliation")

    def _team_room_identity_assignment(
        self,
        frame: Image.Image,
        expected: list[Account],
    ) -> tuple[tuple[Account, ...], tuple[int, int]] | None:
        """Find two or three distinct configured identities in room member rows.

        ``team*_three_member_anchor.bmp`` is intentionally an exact visual
        asset, not a generic member-count signal.  For a two-person bond team,
        the same historical configuration key can point to a two-person
        capture.
        When one is missing, the same enrolled account identity strips can
        prove the fixed members before we create the asset.  Every row must
        have a clear winner; ambiguous or partial rows return ``None`` and
        remain non-actionable.
        """
        member_count = len(expected)
        if member_count not in (2, 3) or len({account.name for account in expected}) != member_count:
            return None
        if any(not account.identity_path.exists() for account in expected):
            return None
        base_x, base_y = TEAM_ROOM_MEMBER_ORIGIN
        row_heights = TEAM_ROOM_MEMBER_ROW_HEIGHTS[:member_count]
        row_starts: list[int] = []
        offset = 0
        for height in row_heights:
            row_starts.append(offset)
            offset += height
        row_ends = tuple(start + height for start, height in zip(row_starts, row_heights))
        candidates: list[tuple[float, tuple[Account, ...], tuple[int, int]]] = []
        # A bounded ±4px search absorbs Flash repaint drift without scanning
        # unrelated UI regions where the same identity strip could occur.
        for dx in range(-4, 5, 2):
            for dy in range(-4, 5, 2):
                origin = (base_x + dx, base_y + dy)
                score_rows: list[list[float]] = []
                for start, end in zip(row_starts, row_ends):
                    roi = (origin[0], origin[1] + start, origin[0] + TEAM_ROOM_MEMBER_SIZE[0], origin[1] + end)
                    row_scores: list[float] = []
                    for account in expected:
                        _ok, score, _x, _y = self.templates.match(frame, account.identity_path, roi, threshold=0.0)
                        row_scores.append(score)
                    score_rows.append(row_scores)
                for permutation in itertools.permutations(range(member_count)):
                    chosen = tuple(score_rows[row][permutation[row]] for row in range(member_count))
                    if min(chosen) < 0.50:
                        continue
                    row_margins = []
                    for row, account_index in enumerate(permutation):
                        alternatives = [score for index, score in enumerate(score_rows[row]) if index != account_index]
                        row_margins.append(chosen[row] - max(alternatives, default=-1.0))
                    if min(row_margins) < 0.035:
                        continue
                    total = float(sum(chosen))
                    candidates.append((total, tuple(expected[index] for index in permutation), origin))
        if not candidates:
            return None
        total, assignment, origin = max(candidates, key=lambda row: (row[0], -abs(row[2][0] - base_x), -abs(row[2][1] - base_y)))
        self.event(
            "team_member_identity_assignment",
            team_members=[account.name for account in expected],
            assigned=[account.name for account in assignment],
            score=total,
            origin=origin,
        )
        return assignment, origin

    def capture_verified_three_member_template(
        self,
        team: Team,
        leader: Account,
        invitees: list[Account],
        template_name: str,
    ) -> bool:
        """Enroll a missing exact-member asset only after identity proof.

        This is a reversible room open/close probe.  It never starts an
        activity and never sends an invitation.  A failed identity assignment
        closes the room through its dedicated X and returns ``False`` so the
        caller can invite only the configured members; a successful assignment
        writes a lossless evidence frame and a fixed-size BMP for later exact
        replay.  The historical method name is retained for ledger and test
        compatibility; ``team`` may contain two bond members or three members.
        """
        target = ASSETS / template_name
        if target.exists():
            return True
        expected = [leader, *invitees]
        self.switch_account(leader)
        self.prepare_main(leader)
        self.open_team_room(leader, f"team{team.index}_open_template_enroll")
        if not self.wait_template("team_room_close.bmp", (1200, 260, 1280, 340), 6):
            raise PendingError("team room did not open for member-template enrollment")
        frame = self.capture()
        result = self._team_room_identity_assignment(frame, expected)
        if result is None:
            # Keep the historical evidence stem so old recovery tools can
            # still locate the probe; the event carries the actual member
            # count for two-person bond teams.
            evidence = self.save_raw_evidence(frame, leader, f"team{team.index}_three_member_probe", "probe")
            if not self.click_template(
                leader,
                "team_room_close.bmp",
                (1200, 260, 1280, 340),
                20,
                20,
                f"team{team.index}_close_template_enroll_empty",
                ["team_room_close.bmp"],
            ):
                raise PendingError("team-room X missing after incomplete member-template probe")
            if not self.wait_until_main(5):
                raise PendingError("team room did not close after incomplete member-template probe")
            self.event(
                "team_three_member_template_pending",
                team=team.index,
                members=[account.name for account in expected],
                member_count=team.member_count,
                evidence=str(evidence),
            )
            return False
        assignment, origin = result
        if {account.name for account in assignment} != {account.name for account in expected}:
            raise PendingError("team-room identity assignment did not prove the configured members")
        evidence = self.save_raw_evidence(frame, leader, f"team{team.index}_member_template", "enroll")
        crop = frame.crop((origin[0], origin[1], origin[0] + TEAM_ROOM_MEMBER_SIZE[0], origin[1] + TEAM_ROOM_MEMBER_SIZE[1]))
        temp = target.with_suffix(target.suffix + ".tmp")
        try:
            crop.save(temp, format="BMP")
            os.replace(temp, target)
        finally:
            if temp.exists():
                temp.unlink()
        self.templates.cache.pop(target, None)
        self.event(
            "team_three_member_template_enrolled",
            team=team.index,
            members=[account.name for account in assignment],
            member_count=team.member_count,
            path=str(target),
            evidence=str(evidence),
            origin=origin,
        )
        if not self.click_template(
            leader,
            "team_room_close.bmp",
            (1200, 260, 1280, 340),
            20,
            20,
            f"team{team.index}_close_template_enroll",
            ["team_room_close.bmp", template_name],
        ):
            raise PendingError("team-room X missing after member-template enrollment")
        if not self.wait_until_main(5):
            raise PendingError("team room did not close after member-template enrollment")
        return True

    def _team_activity_module(self, team: Team, spec: TeamActivitySpec) -> str:
        return f"team{team.index}_{spec.key}"

    def _team_activity_enabled(self, spec: TeamActivitySpec) -> bool:
        """Return whether this activity is in the current live run scope."""
        return spec.key != "rescue_jinchuriki" or self.rescue_enabled

    def _enabled_team_activity_specs(self) -> tuple[TeamActivitySpec, ...]:
        return tuple(spec for spec in TEAM_ACTIVITY_SPECS if self._team_activity_enabled(spec))

    def _deferred_team_activity_specs(self) -> tuple[TeamActivitySpec, ...]:
        return tuple(spec for spec in TEAM_ACTIVITY_SPECS if not self._team_activity_enabled(spec))

    @staticmethod
    def _team_activity_terminal(status: str) -> bool:
        return status in ("done", "done_no_reward")

    def _team_activity_status(self, team: Team, leader: Account, spec: TeamActivitySpec) -> str:
        return self.state.module_status(leader.name, self._team_activity_module(team, spec))

    def _set_team_activity_status(
        self,
        team: Team,
        leader: Account,
        spec: TeamActivitySpec,
        status: str,
        detail: str = "",
    ) -> None:
        module = self._team_activity_module(team, spec)
        self.state.set_module(leader.name, module, status, detail)
        teams = self.state.global_state.setdefault("teams", {})
        record = teams.setdefault(str(team.index), {})
        record.update(
            {
                "members": list(team.members),
                "member_count": team.member_count,
                "leader": leader.name,
                "updated_at": now_iso(),
            }
        )
        activities = record.setdefault("activities", {})
        activities[spec.key] = {"status": status, "detail": detail, "updated_at": now_iso()}
        self.state.save_global()

    def _team_activity_required_assets(self) -> list[str]:
        """Return assets required to build and preserve a verified team.

        Activity-specific assets are checked immediately before that activity.
        This lets verified activities run while a later uncaptured activity
        remains pending, and deliberately keeps the team assembled for resume.
        """
        required = [
            "team_toolbar.bmp", "team_create_button.bmp", "team_create_confirm.bmp", "friends_toolbar.bmp",
            "friends_panel_close.bmp", "friend_invite_team.bmp", "team_invite_accept_yes.bmp", "team_room_close.bmp",
            "team_dissolve_button.bmp", "team_create_type_dropdown.bmp", "team_create_group_option.bmp",
            "team_create_group_type.bmp", "team_room_type_dropdown.bmp", "team_room_group_type.bmp",
            "team_instance_left_entry.bmp", "team_instance_participate.bmp", "team_instance_close.bmp",
            TEAM_BATTLE_START_TEMPLATE,
            TEAM_REWARD_RESULT_TEMPLATE,
            "battle_auto_off.bmp", "battle_auto_on.bmp", "battle_auto_on_current.bmp", "battle_auto_on_current_core.bmp",
            "market_coupon_icon.bmp", "team_member_reduced_confirm.bmp",
        ]
        return list(dict.fromkeys(required))

    def _team_assets_for_spec(self, spec: TeamActivitySpec) -> list[str]:
        required = list(spec.entry_templates)
        if spec.entry_navigation == "daily_limited":
            required.extend(("daily_practice_toolbar.bmp", "daily_practice_anchor.bmp", "daily_limited_tab.bmp"))
        if spec.reward_only:
            # A reward-only activity must be able to prove the normal
            # no-reward state without fabricating combat templates.  If the
            # reward marker is later visible, the runner performs a second,
            # bounded preflight for the actual combat flow below.
            for name in (spec.page_anchor, spec.no_reward_template, spec.close_template):
                if name:
                    required.append(name)
        else:
            for name in (
                spec.page_anchor,
                spec.highest_unlocked,
                spec.free_template,
                spec.start_template,
                spec.reward_pending,
                spec.result_confirm,
                spec.return_anchor,
                spec.close_template,
                spec.leader_start_template,
                spec.participate_template,
            ):
                if name:
                    required.append(name)
        # ``closed_template`` is an optional no-reward/closed-state hint. It
        # is consulted only when an entry is absent and must not block a
        # positively captured normal activity from running.
        if spec.runner == "wanted":
            required.extend(("daily_practice_anchor.bmp", "wanted_team_auto.bmp"))
        return list(dict.fromkeys(required))

    def _team_battle_start_visible(self, frame: Image.Image | None = None) -> bool:
        """Return whether the enrolled leader-only team start control is visible."""
        return self.template(
            TEAM_BATTLE_START_TEMPLATE,
            TEAM_BATTLE_START_ROI,
            frame,
            threshold=0.90,
        )[0]

    def _team_result_template(self, frame: Image.Image | None = None) -> str | None:
        """Find a positively rendered team result acknowledgement button.

        Some clients reuse the arena skin for the team-result ``确认``
        button.  Prefer the dedicated team asset when it exists, and use the
        already enrolled arena skin only as a bounded visual fallback.
        """
        frame = frame or self.capture()
        for name in ("team_result_confirm.bmp", "arena_result_confirm.bmp"):
            if (ASSETS / name).exists() and self.template(name, TEAM_BATTLE_RESULT_ROI, frame, threshold=0.90)[0]:
                return name
        return None

    def _team_reward_result_visible(self, frame: Image.Image | None = None) -> bool:
        """Return whether the post-result team reward acknowledgement is visible."""
        return self.template(
            TEAM_REWARD_RESULT_TEMPLATE,
            TEAM_REWARD_RESULT_ROI,
            frame,
            threshold=0.90,
        )[0]

    def _team_reward_zero_action(self, team: Team, spec: TeamActivitySpec) -> str:
        return f"team{team.index}_{spec.key}_reward_zero"

    def _team_activity_completion_verified(self, team: Team, leader: Account, spec: TeamActivitySpec) -> bool:
        if not self._team_activity_terminal(self._team_activity_status(team, leader, spec)):
            return False
        if spec.reward_counter_prefix:
            return self.state.action_status(leader.name, self._team_reward_zero_action(team, spec)) == "done"
        return True

    def _team_attempt_base(self, team: Team, spec: TeamActivitySpec, attempt_index: int) -> str:
        return f"team{team.index}_{spec.key}_attempt_{attempt_index}"

    def _next_team_attempt_index(self, team: Team, leader: Account, spec: TeamActivitySpec) -> int:
        limit = max(1, spec.reward_counter_max)
        for attempt_index in range(1, limit + 1):
            action = f"{self._team_attempt_base(team, spec, attempt_index)}_complete"
            if self.state.action_status(leader.name, action) != "done":
                return attempt_index
        return limit + 1

    def _read_team_reward_remaining(
        self,
        account: Account,
        spec: TeamActivitySpec,
        frame: Image.Image | None = None,
    ) -> int:
        """Read one enrolled reward-count digit and reject ambiguity.

        Counter glyphs are activity-specific because the strong-approach page
        renders a larger font than the group-instance page.  Missing digits
        deliberately leave the activity pending with a lossless screenshot;
        the runner never guesses a count or starts another battle blindly.
        """
        if not spec.reward_counter_prefix or spec.reward_counter_roi is None or spec.reward_counter_max <= 0:
            raise PendingError(f"{spec.label} reward counter is not configured")
        frame = frame or self.capture()
        matches: list[tuple[float, int, int, int]] = []
        for digit in range(spec.reward_counter_max + 1):
            asset = ASSETS / f"{spec.reward_counter_prefix}{digit}.bmp"
            if not asset.exists():
                continue
            # Strong-approach 3 and 5 have similar borders. The old .88
            # floor accepted an unenrolled 3 as 5 at .881 on a live frame.
            threshold = .96 if spec.key == 'strong_approach' else .88
            ok, score, x, y = self.template(asset.name, spec.reward_counter_roi, frame, threshold=threshold)
            if ok:
                matches.append((score, digit, x, y))
        matches.sort(reverse=True)
        if not matches or (len(matches) > 1 and matches[0][0] - matches[1][0] < 0.04):
            evidence = self.save_raw_evidence(frame, account, f"{spec.key}_reward_counter_unreadable", "pending")
            self.event(
                "team_reward_counter_pending",
                account,
                activity=spec.key,
                candidates=[{"digit": digit, "score": round(score, 4)} for score, digit, _x, _y in matches],
                evidence=str(evidence),
            )
            raise PendingError(f"{spec.label}奖励次数无法正向识别，已保存证据：{evidence}")
        score, remaining, x, y = matches[0]
        self.event(
            "team_reward_counter_verified",
            account,
            activity=spec.key,
            remaining=remaining,
            maximum=spec.reward_counter_max,
            score=round(score, 4),
            x=x,
            y=y,
        )
        return remaining

    def _finish_team_activity_at_zero(
        self,
        team: Team,
        leader: Account,
        spec: TeamActivitySpec,
        frame: Image.Image,
    ) -> None:
        action = self._team_reward_zero_action(team, spec)
        evidence = self.save_raw_evidence(frame, leader, f"team{team.index}_{spec.key}_reward_zero", "verified")
        self.state.set_action(leader.name, action, "done", "奖励次数已正向识别为0")
        self._set_team_activity_status(team, leader, spec, "done", "奖励次数已正向识别为0")
        self.event(
            "team_reward_zero_verified",
            leader,
            team=team.index,
            activity=spec.key,
            remaining=0,
            evidence=str(evidence),
        )

    def _ensure_team_members_auto(
        self,
        team: Team,
        leader: Account,
        spec: TeamActivitySpec,
        attempt_index: int,
    ) -> None:
        """Switch every configured member tab and positively verify ``取消``.

        Team combat is rendered independently in each browser tab.  The
        leader's auto state therefore cannot stand in for the members.  A
        battle may continue only after every member tab shows one of the
        enrolled auto-on/``取消`` skins; an off state is clicked once and then
        rechecked.  Any member that cannot be proved remains pending.
        """
        verified: list[str] = []
        for member_name in team.members:
            member = self.by_name[member_name]
            frame = self.switch_account(member)
            deadline = time.monotonic() + float(self.config.get("TeamMemberAutoWaitSeconds", str(TEAM_MEMBER_AUTO_WAIT_SECONDS)))
            while time.monotonic() < deadline:
                self.check_stop()
                frame = self.capture()
                action = f"{self._team_attempt_base(team, spec, attempt_index)}_auto_{slug(member.name)}"
                if self._battle_auto_on_visible(frame):
                    self.state.set_action(member.name, action, "done", "战斗页面已显示取消，自动战斗开启")
                    evidence = self.save_raw_evidence(frame, member, f"team{team.index}_{spec.key}_attempt_{attempt_index}_auto_on", "verified")
                    self.event(
                        "team_member_auto_verified",
                        member,
                        team=team.index,
                        activity=spec.key,
                        attempt=attempt_index,
                        display="取消",
                        evidence=str(evidence),
                    )
                    verified.append(member.name)
                    break
                if self.template("battle_auto_off.bmp", BATTLE_AUTO_ROI, frame)[0]:
                    if self.state.action_status(member.name, action) == "pending":
                        raise PendingError(f"{spec.label}第{attempt_index}场 {member.name} 自动战斗结果未知")
                    if not self.click_template(
                        member,
                        "battle_auto_off.bmp",
                        BATTLE_AUTO_ROI,
                        29,
                        29,
                        action,
                        ["battle_auto_off.bmp"],
                    ):
                        raise PendingError(f"{spec.label}第{attempt_index}场 {member.name} 自动按钮消失")
                    if not self._wait_battle_auto_on(5):
                        raise PendingError(f"{spec.label}第{attempt_index}场 {member.name} 未显示取消")
                    continue
                if self._team_result_template(frame) or self._team_reward_result_visible(frame):
                    break
                self.wait_interruptible(0.2)
            if member.name not in verified:
                evidence = self.save_raw_evidence(frame, member, f"team{team.index}_{spec.key}_attempt_{attempt_index}_auto_unverified", "pending")
                self.event(
                    "team_member_auto_pending",
                    member,
                    team=team.index,
                    activity=spec.key,
                    attempt=attempt_index,
                    evidence=str(evidence),
                )
                raise PendingError(f"{spec.label}第{attempt_index}场未确认 {member.name} 显示取消")
        self.switch_account(leader)
        self.event(
            "team_all_members_auto_verified",
            leader,
            team=team.index,
            activity=spec.key,
            attempt=attempt_index,
            members=verified,
            member_count=len(verified),
        )

    def _team_members_auto_complete(self, team: Team, spec: TeamActivitySpec, attempt_index: int) -> bool:
        return all(
            self.state.action_status(
                member_name,
                f"{self._team_attempt_base(team, spec, attempt_index)}_auto_{slug(member_name)}",
            ) == "done"
            for member_name in team.members
        )

    def _resume_team_instance_battle(
        self,
        team: Team,
        leader: Account,
        frame: Image.Image | None = None,
        start_action: str | None = None,
        attempt_index: int | None = None,
    ) -> None:
        """Resume a verified leader battle left on the shared Team tab.

        A delayed Flash transition can leave the leader in a battle scene
        after the activity catalogue was closed.  The old runner treated that
        scene as a missing main page and could never resume it.  This helper
        accepts only the enrolled ``开战(2)`` control, enables auto battle once,
        acknowledges a positively rendered result, and waits for the known
        group-instance selection page before marking the activity complete.
        Every configured member is switched once after battle start so the
        visible ``取消`` state is positively verified before result polling.
        """
        spec = TEAM_ACTIVITY_BY_KEY["group_instance"]
        attempt_index = attempt_index or self._next_team_attempt_index(team, leader, spec)
        base = self._team_attempt_base(team, spec, attempt_index)
        start_action = start_action or f"{base}_battle_start"
        result_action = f"{base}_result_confirm"
        reward_action = f"{base}_reward_confirm"
        complete_action = f"{base}_complete"
        frame = frame or self.capture()
        if self._team_battle_start_visible(frame):
            start_status = self.state.action_status(leader.name, start_action)
            if start_status == "pending":
                evidence = self.save_raw_evidence(frame, leader, f"{base}_start_outcome_unknown", "pending")
                self.event("team_battle_start_pending", leader, team=team.index, evidence=str(evidence))
                raise PendingError(f"{base} battle-start outcome unknown; refusing duplicate")
            if start_status != "done":
                if not self.click_template(
                    leader,
                    TEAM_BATTLE_START_TEMPLATE,
                    TEAM_BATTLE_START_ROI,
                    65,
                    23,
                    start_action,
                    [TEAM_BATTLE_START_TEMPLATE],
                    irreversible=True,
                    threshold=0.90,
                ):
                    raise PendingError(f"{base} leader battle-start control disappeared")
                if not self.wait_absent(TEAM_BATTLE_START_TEMPLATE, TEAM_BATTLE_START_ROI, 12, threshold=0.90):
                    raise PendingError(f"{base} leader battle-start control remained; outcome is pending")
                self.commit_action(leader, start_action, "leader battle-start control disappeared")
        elif self.state.action_status(leader.name, start_action) != "done":
            raise PendingError(f"{base} has no positively matched battle-start state")

        if spec.require_all_member_auto and not self._team_members_auto_complete(team, spec, attempt_index):
            self._ensure_team_members_auto(team, leader, spec, attempt_index)

        unknown_since = time.monotonic()
        watchdog = BattleWatchdog.from_config(getattr(self, "config", {}), started_at=unknown_since)
        while True:
            self.check_stop()
            frame = self.capture()
            if not (self._team_result_template(frame)
                    and self.state.action_status(leader.name, result_action) != "done"
                    or self._team_reward_result_visible(frame)
                    and self.state.action_status(leader.name, reward_action) != "done"):
                check_battle_watchdog(self, watchdog, leader, frame, base, mode=spec.key)
            if self._battle_auto_on_visible(frame):
                unknown_since = time.monotonic()
            elif time.monotonic() - unknown_since >= 60:
                break
            if self.template("battle_auto_off.bmp", BATTLE_AUTO_ROI, frame)[0]:
                raise PendingError(f"{base} leader auto battle became disabled after all-member verification")

            result_template = self._team_result_template(frame)
            if result_template:
                result_status = self.state.action_status(leader.name, result_action)
                if result_status == "pending":
                    raise PendingError(f"{base} result confirmation outcome unknown; refusing duplicate")
                if result_status != "done":
                    if not self.click_template(
                        leader,
                        result_template,
                        TEAM_BATTLE_RESULT_ROI,
                        55 if result_template.startswith("team_") else 64,
                        18 if result_template.startswith("team_") else 19,
                        result_action,
                        [result_template],
                        irreversible=True,
                    ):
                        raise PendingError(f"{base} result confirmation disappeared")
                    if not self.wait_absent(result_template, TEAM_BATTLE_RESULT_ROI, 5):
                        raise PendingError(f"{base} result confirmation remained")
                    self.commit_action(leader, result_action, "team result confirmation disappeared")
                continue

            if self._team_reward_result_visible(frame):
                reward_status = self.state.action_status(leader.name, reward_action)
                if reward_status == "pending":
                    raise PendingError(f"{base} reward confirmation outcome unknown; refusing duplicate")
                if reward_status == "done":
                    raise PendingError(f"{base} reward confirmation remained after the recorded click")
                if not self.click_template(
                    leader,
                    TEAM_REWARD_RESULT_TEMPLATE,
                    TEAM_REWARD_RESULT_ROI,
                    64,
                    19,
                    reward_action,
                    [TEAM_REWARD_RESULT_TEMPLATE],
                    irreversible=True,
                    threshold=0.90,
                ):
                    raise PendingError(f"{base} reward confirmation disappeared")
                if not self.wait_absent(TEAM_REWARD_RESULT_TEMPLATE, TEAM_REWARD_RESULT_ROI, 8, threshold=0.90):
                    raise PendingError(f"{base} reward confirmation remained after click")
                self.commit_action(leader, reward_action, "post-result team reward confirmation disappeared")
                continue

            if self.template("team_instance_close.bmp", (1370, 240, 1460, 330), frame)[0]:
                if self.state.action_status(leader.name, start_action) != "done":
                    raise PendingError(f"{base} returned without a recorded battle start")
                if not self._team_members_auto_complete(team, spec, attempt_index):
                    raise PendingError(f"{base} returned without all members showing 取消")
                self.state.set_action(leader.name, complete_action, "done", "本场结果已确认并返回副本选择页")
                self._set_team_activity_status(team, leader, spec, "running", f"第{attempt_index}场完成，等待本日奖励次数归零")
                self.event(
                    "team_activity_attempt_completed",
                    leader,
                    team=team.index,
                    activity=spec.key,
                    attempt=attempt_index,
                    member_count=team.member_count,
                )
                return
            self.wait_interruptible(0.4)
        evidence = self.save_raw_evidence(frame, leader, f"{base}_timeout", "pending")
        self.event("team_battle_timeout", leader, team=team.index, evidence=str(evidence))
        raise PendingError(f"{base} battle timeout; activity remains pending")

    def _team_activity_visible(self, spec: TeamActivitySpec, frame: Image.Image) -> bool:
        probes: list[tuple[str, tuple[int, int, int, int]]] = []
        if spec.page_anchor:
            probes.append((spec.page_anchor, (250, 80, 1720, 950)))
        if spec.result_confirm:
            probes.append((spec.result_confirm, (650, 400, 1250, 850)))
        if spec.return_anchor:
            probes.append((spec.return_anchor, (250, 80, 1720, 950)))
        if spec.leader_start_template:
            probes.append((spec.leader_start_template, (650, 400, 1250, 850)))
        if spec.participate_template:
            probes.append((spec.participate_template, (1050, 620, 1350, 740)))
        probes.append((TEAM_BATTLE_START_TEMPLATE, TEAM_BATTLE_START_ROI))
        probes.append((TEAM_REWARD_RESULT_TEMPLATE, TEAM_REWARD_RESULT_ROI))
        probes.extend((name, BATTLE_AUTO_ROI) for name in ("battle_auto_off.bmp", "battle_auto_on.bmp", "battle_auto_on_current.bmp", "battle_auto_on_current_core.bmp"))
        return any(
            (self.active_template(name, roi, frame)[0] if name == "daily_participate.bmp" else self.template(name, roi, frame)[0])
            for name, roi in probes
        )

    def _close_team_activity_page(self, account: Account, spec: TeamActivitySpec) -> None:
        frame = self.capture()
        if self.main_ready(frame):
            return
        if not spec.close_template:
            raise PendingError(f"{spec.label} close control is not configured")
        if not self.click_template(
            account,
            spec.close_template,
            (1300, 280, 1420, 390) if spec.key == "strong_approach" else (1350, 180, 1750, 350),
            20,
            20,
            f"team_activity_{spec.key}_close",
            [spec.page_anchor, spec.close_template],
        ):
            raise PendingError(f"{spec.label} close control is missing")
        if not self.wait_until_main(8):
            raise PendingError(f"{spec.label} did not close through a verified X")

    def _resume_generic_team_battle(
        self,
        team: Team,
        leader: Account,
        spec: TeamActivitySpec,
        frame: Image.Image | None = None,
        start_action: str | None = None,
        attempt_index: int | None = None,
    ) -> None:
        """Resume a leader-only battle started by a generic team activity.

        The activity catalogue and the battle lobby are separate Flash
        scenes. The catalogue start is first recorded as pending, then the
        positively rendered ``开战(2)`` control proves that the start reached
        the leader lobby. Only the leader tab is touched after that point.
        """
        repeating = bool(spec.reward_counter_prefix)
        attempt_index = attempt_index or (self._next_team_attempt_index(team, leader, spec) if repeating else 1)
        base = self._team_attempt_base(team, spec, attempt_index) if repeating else f"team{team.index}_{spec.key}"
        start_action = start_action or f"{base}_start"
        battle_start_action = f"{base}_battle_start"
        auto_action = f"{base}_battle_auto"
        result_action = f"{base}_result_confirm"
        reward_action = f"{base}_reward_confirm"
        complete_action = f"{base}_complete"
        frame = frame or self.capture()

        if self._team_battle_start_visible(frame):
            start_status = self.state.action_status(leader.name, start_action)
            if start_status == "pending":
                self.commit_action(leader, start_action, "activity start reached the verified leader battle lobby")
            elif start_status != "done":
                raise PendingError(f"{base} leader battle lobby appeared without a recorded activity start")
            battle_status = self.state.action_status(leader.name, battle_start_action)
            if battle_status == "pending":
                raise PendingError(f"{base} battle-start outcome unknown; refusing duplicate")
            if battle_status != "done":
                if not self.click_template(
                    leader,
                    TEAM_BATTLE_START_TEMPLATE,
                    TEAM_BATTLE_START_ROI,
                    65,
                    23,
                    battle_start_action,
                    [TEAM_BATTLE_START_TEMPLATE],
                    irreversible=True,
                    threshold=0.90,
                ):
                    raise PendingError(f"{base} leader battle-start control disappeared")
                if not self.wait_absent(TEAM_BATTLE_START_TEMPLATE, TEAM_BATTLE_START_ROI, 12, threshold=0.90):
                    raise PendingError(f"{base} leader battle-start control remained; outcome is pending")
                self.commit_action(leader, battle_start_action, "leader battle-start control disappeared")
        elif self.state.action_status(leader.name, start_action) == "new":
            raise PendingError(f"{base} has no positively matched battle-start state")

        if spec.require_all_member_auto and not self._team_members_auto_complete(team, spec, attempt_index):
            self._ensure_team_members_auto(team, leader, spec, attempt_index)

        unknown_since = time.monotonic()
        watchdog = BattleWatchdog.from_config(getattr(self, "config", {}), started_at=unknown_since)
        while True:
            self.check_stop()
            frame = self.capture()
            if not (self._team_result_template(frame)
                    and self.state.action_status(leader.name, result_action) != "done"
                    or self._team_reward_result_visible(frame)
                    and self.state.action_status(leader.name, reward_action) != "done"):
                check_battle_watchdog(self, watchdog, leader, frame, base, mode=spec.key)
            if self._battle_auto_on_visible(frame):
                unknown_since = time.monotonic()
            elif time.monotonic() - unknown_since >= 60:
                break
            if self.template("battle_auto_off.bmp", BATTLE_AUTO_ROI, frame)[0]:
                if spec.require_all_member_auto:
                    raise PendingError(f"{base} leader auto battle became disabled after all-member verification")
                if self.state.action_status(leader.name, auto_action) == "pending":
                    raise PendingError(f"{base} auto-battle outcome unknown; refusing duplicate")
                if self.state.action_status(leader.name, auto_action) == "done":
                    raise PendingError(f"{base} auto-battle is still off after the recorded click")
                if not self.click_template(
                    leader,
                    "battle_auto_off.bmp",
                    BATTLE_AUTO_ROI,
                    29,
                    29,
                    auto_action,
                    ["battle_auto_off.bmp"],
                ):
                    raise PendingError(f"{base} auto-battle control disappeared")
                if not self._wait_battle_auto_on(5):
                    raise PendingError(f"{base} auto-battle was not positively enabled")
                self.state.set_action(leader.name, auto_action, "done", "leader auto-battle visibly enabled")
                self.event("team_auto_enabled", leader, team=team.index, activity=spec.key, leader_only=True)
                continue
            if self._battle_auto_on_visible(frame) and self.state.action_status(leader.name, auto_action) != "done":
                self.state.set_action(leader.name, auto_action, "done", "leader auto-battle already visibly enabled")
                self.event("team_auto_already_enabled", leader, team=team.index, activity=spec.key, leader_only=True)
                continue

            result_template = self._team_result_template(frame)
            if result_template:
                result_status = self.state.action_status(leader.name, result_action)
                if result_status == "pending":
                    raise PendingError(f"{base} result confirmation outcome unknown; refusing duplicate")
                if result_status != "done":
                    if not self.click_template(
                        leader,
                        result_template,
                        TEAM_BATTLE_RESULT_ROI,
                        55 if result_template.startswith("team_") else 64,
                        18 if result_template.startswith("team_") else 19,
                        result_action,
                        [result_template],
                        irreversible=True,
                    ):
                        raise PendingError(f"{base} result confirmation disappeared")
                    if not self.wait_absent(result_template, TEAM_BATTLE_RESULT_ROI, 5):
                        raise PendingError(f"{base} result confirmation remained")
                    self.commit_action(leader, result_action, "team result confirmation disappeared")
                continue

            # The team battle result and the richer cooperative reward are
            # separate Flash dialogs.  The first confirmation can disappear
            # while the reward dialog is still being painted, so persist a
            # distinct idempotency record for this second, one-time click.
            if self._team_reward_result_visible(frame):
                reward_status = self.state.action_status(leader.name, reward_action)
                if reward_status == "pending":
                    raise PendingError(f"{base} reward confirmation outcome unknown; refusing duplicate")
                if reward_status == "done":
                    raise PendingError(f"{base} reward confirmation remained after the recorded click")
                if not self.click_template(
                    leader,
                    TEAM_REWARD_RESULT_TEMPLATE,
                    TEAM_REWARD_RESULT_ROI,
                    64,
                    19,
                    reward_action,
                    [TEAM_REWARD_RESULT_TEMPLATE],
                    irreversible=True,
                    threshold=0.90,
                ):
                    raise PendingError(f"{base} reward confirmation disappeared")
                if not self.wait_absent(TEAM_REWARD_RESULT_TEMPLATE, TEAM_REWARD_RESULT_ROI, 8, threshold=0.90):
                    raise PendingError(f"{base} reward confirmation remained after click")
                self.commit_action(leader, reward_action, "post-result team reward confirmation disappeared")
                continue

            # A reward confirmation may return directly to the shared main
            # page instead of repainting the activity catalogue.  Treat that
            # as the verified return only after the battle result has already
            # been acknowledged; this prevents a failed/ignored start click
            # from being mistaken for completion.
            if (self.main_ready(frame) and self.state.action_status(leader.name, result_action) == "done"
                    and (not spec.require_reward_confirmation
                         or self.state.action_status(leader.name, reward_action) == "done")):
                if self.state.action_status(leader.name, start_action) == "pending":
                    self.commit_action(leader, start_action, "leader activity returned after verified result")
                elif self.state.action_status(leader.name, start_action) != "done":
                    raise PendingError(f"{base} returned without a recorded start")
                if repeating:
                    if spec.require_all_member_auto and not self._team_members_auto_complete(team, spec, attempt_index):
                        raise PendingError(f"{base} returned without all members showing 取消")
                    self.state.set_action(leader.name, complete_action, "done", "本场结果已确认并返回")
                    self._set_team_activity_status(team, leader, spec, "running", f"第{attempt_index}场完成，等待奖励次数归零")
                    self.event("team_activity_attempt_completed", leader, team=team.index, activity=spec.key, attempt=attempt_index, member_count=team.member_count)
                else:
                    self._set_team_activity_status(team, leader, spec, "done", f"{spec.label} leader activity completed")
                    self.event("team_activity_done", leader, team=team.index, activity=spec.key, leader_only=True)
                return

            if spec.return_anchor and self.template(spec.return_anchor, (250, 80, 1720, 950), frame)[0]:
                if self.state.action_status(leader.name, result_action) != "done":
                    raise PendingError(f"{base} returned without a committed result confirmation")
                if spec.require_reward_confirmation and self.state.action_status(leader.name,reward_action) != "done":
                    self.wait_interruptible(.3)
                    continue
                if self.state.action_status(leader.name, start_action) == "pending":
                    self.commit_action(leader, start_action, "leader activity returned after verified result")
                elif self.state.action_status(leader.name, start_action) != "done":
                    raise PendingError(f"{base} returned without a recorded start")
                if repeating:
                    if spec.require_all_member_auto and not self._team_members_auto_complete(team, spec, attempt_index):
                        raise PendingError(f"{base} returned without all members showing 取消")
                    self.state.set_action(leader.name, complete_action, "done", "本场结果已确认并返回活动页")
                    self._set_team_activity_status(team, leader, spec, "running", f"第{attempt_index}场完成，等待奖励次数归零")
                    self.event("team_activity_attempt_completed", leader, team=team.index, activity=spec.key, attempt=attempt_index, member_count=team.member_count)
                else:
                    self._set_team_activity_status(team, leader, spec, "done", f"{spec.label} leader activity completed")
                    self.event("team_activity_done", leader, team=team.index, activity=spec.key, leader_only=True)
                    self._close_team_activity_page(leader, spec)
                return
            self.wait_interruptible(0.4)
        evidence = self.save_raw_evidence(frame, leader, f"{base}_timeout", "pending")
        self.event("team_battle_timeout", leader, team=team.index, activity=spec.key, evidence=str(evidence))
        raise PendingError(f"{base} battle timeout; activity remains pending")

    def _retry_generic_start_after_same_page(
        self,
        team: Team,
        leader: Account,
        spec: TeamActivitySpec,
        frame: Image.Image,
    ) -> None:
        """An unchanged page cannot prove an earlier start was not received."""
        base = f"team{team.index}_{spec.key}"
        evidence = self.save_raw_evidence(frame, leader, f"{base}_start_unresolved", "pending")
        self.event("team_activity_start_pending", leader, team=team.index,
                   activity=spec.key, evidence=str(evidence))
        raise PendingError(f"{base} start outcome unknown; refusing duplicate; evidence={evidence}")

    def _poll_generic_team_activity(
        self,
        team: Team,
        leader: Account,
        spec: TeamActivitySpec,
        attempt_index: int | None = None,
    ) -> None:
        """Poll results on the leader tab after any required all-member auto gate."""
        repeating = bool(spec.reward_counter_prefix)
        attempt_index = attempt_index or (self._next_team_attempt_index(team, leader, spec) if repeating else 1)
        base = self._team_attempt_base(team, spec, attempt_index) if repeating else f"team{team.index}_{spec.key}"
        start_action = f"{base}_start"
        final_start_action = f"{base}_leader_start"
        result_action = f"{base}_result_confirm"
        reward_action = f"{base}_reward_confirm"
        unknown_since = time.monotonic()
        watchdog = BattleWatchdog.from_config(getattr(self, "config", {}), started_at=unknown_since)
        while True:
            self.check_stop()
            frame = self.capture()
            if not (self._team_result_template(frame)
                    and self.state.action_status(leader.name, result_action) != "done"
                    or self._team_reward_result_visible(frame)
                    and self.state.action_status(leader.name, reward_action) != "done"):
                check_battle_watchdog(self, watchdog, leader, frame, base, mode=spec.key)
            if self._battle_auto_on_visible(frame):
                unknown_since = time.monotonic()
            elif time.monotonic() - unknown_since >= 60:
                break
            if self._team_battle_start_visible(frame):
                self._resume_generic_team_battle(
                    team,
                    leader,
                    spec,
                    frame=frame,
                    start_action=start_action,
                    attempt_index=attempt_index,
                )
                return
            if spec.require_all_member_auto and (
                self.template("battle_auto_off.bmp", BATTLE_AUTO_ROI, frame)[0]
                or self._battle_auto_on_visible(frame)
            ):
                self._resume_generic_team_battle(
                    team,
                    leader,
                    spec,
                    frame=frame,
                    start_action=start_action,
                    attempt_index=attempt_index,
                )
                return
            if spec.leader_start_template and self.template(spec.leader_start_template, (650, 400, 1250, 850), frame)[0]:
                if self.state.action_status(leader.name, final_start_action) == "pending":
                    raise PendingError(f"{base} leader-start outcome unknown; refusing duplicate")
                if self.state.action_status(leader.name, final_start_action) != "done":
                    if not self.click_template(
                        leader,
                        spec.leader_start_template,
                        (650, 400, 1250, 850),
                        55,
                        18,
                        final_start_action,
                        [spec.leader_start_template, spec.page_anchor],
                        irreversible=True,
                    ):
                        raise PendingError(f"{base} leader-start control disappeared")
                    if not self.wait_absent(spec.leader_start_template, (650, 400, 1250, 850), 5):
                        raise PendingError(f"{base} leader-start control remained after click")
                    self.commit_action(leader, final_start_action, "leader-only final start control disappeared")
                self.wait_interruptible(0.3)
                continue

            if self.template("battle_auto_off.bmp", BATTLE_AUTO_ROI, frame)[0]:
                if not self.click_template(
                    leader,
                    "battle_auto_off.bmp",
                    BATTLE_AUTO_ROI,
                    29,
                    29,
                    f"{base}_auto",
                    [spec.page_anchor, spec.result_confirm or spec.return_anchor or "battle_auto_off.bmp"],
                ):
                    raise PendingError(f"{base} auto control disappeared")
                if not self._wait_battle_auto_on(5):
                    raise PendingError(f"{base} auto mode was not positively enabled")
                continue

            if spec.result_confirm and self.template(spec.result_confirm, (650, 400, 1250, 850), frame)[0]:
                result_status = self.state.action_status(leader.name, result_action)
                if result_status == "pending":
                    raise PendingError(f"{base} result confirmation outcome unknown; refusing duplicate")
                if result_status != "done":
                    if not self.click_template(
                        leader,
                        spec.result_confirm,
                        (650, 400, 1250, 850),
                        55,
                        18,
                        result_action,
                        [spec.result_confirm],
                        irreversible=True,
                    ):
                        raise PendingError(f"{base} result confirmation disappeared")
                    if not self.wait_absent(spec.result_confirm, (650, 400, 1250, 850), 5):
                        raise PendingError(f"{base} result confirmation remained after click")
                    self.commit_action(leader, result_action, "team result confirmation disappeared")
                self.wait_interruptible(0.3)
                continue

            if self._team_reward_result_visible(frame):
                reward_status = self.state.action_status(leader.name, reward_action)
                if reward_status == "pending":
                    raise PendingError(f"{base} reward confirmation outcome unknown; refusing duplicate")
                if reward_status == "done":
                    raise PendingError(f"{base} reward confirmation remained after the recorded click")
                if not self.click_template(
                    leader,
                    TEAM_REWARD_RESULT_TEMPLATE,
                    TEAM_REWARD_RESULT_ROI,
                    64,
                    19,
                    reward_action,
                    [TEAM_REWARD_RESULT_TEMPLATE],
                    irreversible=True,
                    threshold=0.90,
                ):
                    raise PendingError(f"{base} reward confirmation disappeared")
                if not self.wait_absent(TEAM_REWARD_RESULT_TEMPLATE, TEAM_REWARD_RESULT_ROI, 8, threshold=0.90):
                    raise PendingError(f"{base} reward confirmation remained after click")
                self.commit_action(leader, reward_action, "post-result team reward confirmation disappeared")
                self.wait_interruptible(0.3)
                continue

            if (self.main_ready(frame) and self.state.action_status(leader.name, result_action) == "done"
                    and (not spec.require_reward_confirmation
                         or self.state.action_status(leader.name, reward_action) == "done")):
                if self.state.action_status(leader.name, start_action) == "pending":
                    self.commit_action(leader, start_action, "leader activity returned after verified result")
                elif self.state.action_status(leader.name, start_action) != "done":
                    raise PendingError(f"{base} returned without a recorded start")
                if repeating:
                    if spec.require_all_member_auto and not self._team_members_auto_complete(team, spec, attempt_index):
                        raise PendingError(f"{base} returned without all members showing 取消")
                    self.state.set_action(leader.name, f"{base}_complete", "done", "本场结果已确认并返回")
                    self._set_team_activity_status(team, leader, spec, "running", f"第{attempt_index}场完成，等待奖励次数归零")
                    self.event("team_activity_attempt_completed", leader, team=team.index, activity=spec.key, attempt=attempt_index, member_count=team.member_count)
                else:
                    self._set_team_activity_status(team, leader, spec, "done", f"{spec.label} leader activity completed")
                    self.event("team_activity_done", leader, team=team.index, activity=spec.key, leader_only=True)
                return

            if spec.return_anchor and self.template(spec.return_anchor, (250, 80, 1720, 950), frame)[0]:
                if (self.state.action_status(leader.name, result_action) != "done"
                        or spec.require_reward_confirmation and self.state.action_status(leader.name,reward_action) != "done"):
                    # The direct-start activity may keep its catalogue anchor
                    # visible for several repaint frames. It is not a return
                    # proof until a result acknowledgement has been recorded.
                    self.wait_interruptible(0.4)
                    continue
                if self.state.action_status(leader.name, start_action) == "pending":
                    self.commit_action(leader, start_action, "leader activity returned after verified result")
                elif self.state.action_status(leader.name, start_action) != "done":
                    raise PendingError(f"{base} returned without a recorded start")
                if repeating:
                    if spec.require_all_member_auto and not self._team_members_auto_complete(team, spec, attempt_index):
                        raise PendingError(f"{base} returned without all members showing 取消")
                    self.state.set_action(leader.name, f"{base}_complete", "done", "本场结果已确认并返回活动页")
                    self._set_team_activity_status(team, leader, spec, "running", f"第{attempt_index}场完成，等待奖励次数归零")
                    self.event("team_activity_attempt_completed", leader, team=team.index, activity=spec.key, attempt=attempt_index, member_count=team.member_count)
                else:
                    self._set_team_activity_status(team, leader, spec, "done", f"{spec.label} leader activity completed")
                    self.event("team_activity_done", leader, team=team.index, activity=spec.key, leader_only=True)
                    self._close_team_activity_page(leader, spec)
                return
            self.wait_interruptible(0.4)
        raise PendingError(f"{base} battle timeout; activity remains pending")

    def open_strong_approach(self, team: Team, leader: Account, spec: TeamActivitySpec) -> None:
        if self.template(spec.page_anchor, (250, 80, 1720, 950))[0]:
            return
        self.prepare_main(leader)
        entries = (("strong_approach_entry_core.bmp", (1000, 69, 1450, 150), 17, 19),
                   (spec.entry_templates[0], (1000, 69, 1450, 165), 35, 20))
        for name, roi, dx, dy in entries:
            if self.click_template(leader, name, roi, dx, dy,
                                   f"team{team.index}_{spec.key}_entry", ["anchor_top_menu.bmp"]):
                if not self.wait_template(spec.page_anchor, (250, 80, 1720, 950), 15):
                    raise PendingError(f"{spec.label}页面打开超时")
                self.event("strong_entry_verified", leader, template=name)
                return
        raise PendingError(f"{spec.label}入口不可用")

    def _strong_all_member_rewards_zero(self, team: Team, leader: Account, spec: TeamActivitySpec) -> bool:
        """Specialized team clients can require each selected member's counter."""
        return True

    def run_verified_strong_approach(self, team: Team, leader: Account, spec: TeamActivitySpec, target_count: int | None = None) -> None:
        """Run strong approach until the weekly normal-reward counter is 0."""
        if self._team_activity_completion_verified(team, leader, spec):
            return
        if self._team_activity_terminal(self._team_activity_status(team, leader, spec)):
            self._set_team_activity_status(team, leader, spec, "running", "旧完成记录缺少本周奖励次数为0证据，重新核验")

        first_attempt = self._next_team_attempt_index(team, leader, spec)
        selected_stop_attempt = (
            first_attempt + target_count
            if target_count is not None
            else None
        )
        for _ in range(spec.reward_counter_max + 1):
            attempt_index = self._next_team_attempt_index(team, leader, spec)
            self.switch_account(leader)
            frame = self.capture()
            if (
                self._team_battle_start_visible(frame)
                or self.template("battle_auto_off.bmp", BATTLE_AUTO_ROI, frame)[0]
                or self._battle_auto_on_visible(frame)
                or self._team_result_template(frame)
                or self._team_reward_result_visible(frame)
            ):
                self._poll_generic_team_activity(team, leader, spec, attempt_index=attempt_index)
                continue

            if not self.template(spec.page_anchor, (250, 80, 1720, 950), frame)[0]:
                self.open_strong_approach(team, leader, spec)
                frame = self.capture()

            remaining = self._read_team_reward_remaining(leader, spec, frame)
            if remaining == 0:
                if self._strong_all_member_rewards_zero(team, leader, spec):
                    self.switch_account(leader)
                    self._close_team_activity_page(leader, spec)
                    self._finish_team_activity_at_zero(team, leader, spec, frame)
                    return
                self.switch_account(leader)
                if not self.template(spec.page_anchor, (250, 80, 1720, 950))[0]:
                    self.open_strong_approach(team, leader, spec)

            attempt_index = self._next_team_attempt_index(team, leader, spec)
            if selected_stop_attempt is not None and attempt_index >= selected_stop_attempt:
                self._close_team_activity_page(leader, spec)
                self.event(
                    "team_selected_activity_target_complete",
                    leader,
                    team=team.index,
                    activity=spec.key,
                    target_count=target_count,
                    completed_count=attempt_index - first_attempt,
                    remaining_rewards=remaining,
                    home_returned=True,
                )
                return
            if attempt_index > spec.reward_counter_max:
                evidence = self.save_raw_evidence(frame, leader, f"team{team.index}_{spec.key}_nonzero_after_limit", "pending")
                raise PendingError(f"{spec.label}已完成{spec.reward_counter_max}场但本周奖励次数仍为{remaining}：{evidence}")
            base = self._team_attempt_base(team, spec, attempt_index)
            start_action = f"{base}_start"
            start_status = self.state.action_status(leader.name, start_action)
            if start_status == "done":
                self._poll_generic_team_activity(team, leader, spec, attempt_index=attempt_index)
                continue
            if start_status == "pending":
                if not self.template(spec.start_template, (600, 500, 1450, 1000), frame, threshold=0.90)[0]:
                    self._poll_generic_team_activity(team, leader, spec, attempt_index=attempt_index)
                    continue
                evidence = self.save_raw_evidence(frame, leader, f"{base}_start_unresolved", "pending")
                raise PendingError(f"{spec.label}第{attempt_index}场进入战斗结果未知，禁止重复开战：{evidence}")
            else:
                if not self.click_template(
                    leader,
                    spec.start_template,
                    (600, 500, 1450, 1000),
                    55,
                    18,
                    start_action,
                    [spec.page_anchor, spec.start_template],
                    irreversible=True,
                ):
                    raise PendingError(f"{spec.label}第{attempt_index}场进入战斗按钮缺失")
                if not self.wait_absent(spec.start_template, (600, 500, 1450, 1000), 12, threshold=0.90):
                    evidence = self.save_raw_evidence(self.capture(), leader, f"{base}_start_still_visible", "pending")
                    raise PendingError(f"{spec.label}第{attempt_index}场点击后仍在原页，保留未决、不重复开战：{evidence}")
                self.commit_action(leader, start_action, "已离开强者挑战页面并进入战斗流程")
            self._set_team_activity_status(team, leader, spec, "running", f"第{attempt_index}场战斗进行中")
            self._poll_generic_team_activity(team, leader, spec, attempt_index=attempt_index)
        raise PendingError(f"{spec.label}达到单次运行安全上限，本周奖励次数仍未确认为0")

    def run_generic_team_activity(self, team: Team, leader: Account, spec: TeamActivitySpec) -> None:
        """Run a generic activity; repeated battles also verify member auto tabs."""
        if spec.key == "strong_approach":
            self.run_verified_strong_approach(team, leader, spec)
            return
        if self._team_activity_completion_verified(team, leader, spec):
            return
        base = f"team{team.index}_{spec.key}"
        start_action = f"{base}_start"
        self.switch_account(leader)
        frame = self.capture()
        start_status = self.state.action_status(leader.name, start_action)
        result_status = self.state.action_status(leader.name, f"{base}_result_confirm")
        on_activity_page = self._team_activity_visible(spec, frame)
        if start_status == "pending" and on_activity_page:
            self._retry_generic_start_after_same_page(team, leader, spec, frame)
            if self.state.action_status(leader.name, start_action) == "done":
                self._poll_generic_team_activity(team, leader, spec)
                return
        if start_status in ("pending", "done") or result_status in ("pending", "done"):
            if not on_activity_page:
                raise PendingError(f"{base} has a recorded action but no positive activity page")
            self._poll_generic_team_activity(team, leader, spec)
            return

        if not on_activity_page:
            self.prepare_main(leader)
            entry = spec.entry_templates[0]
            entry_roi = VIEW
            entry_anchor = ["anchor_top_menu.bmp"]
            if spec.entry_navigation == "daily_limited":
                if not self.click_template(
                    leader,
                    "daily_practice_toolbar.bmp",
                    (651, 69, 851, 169),
                    20,
                    20,
                    f"{base}_open_daily_practice",
                    ["anchor_top_menu.bmp"],
                ):
                    raise PendingError(f"{spec.label} 日常任务入口不可用")
                if not self.wait_template("daily_practice_anchor.bmp", (951, 119, 1501, 369), 8):
                    raise PendingError(f"{spec.label} 日常任务页面未打开")
                if not self.click_template(
                    leader,
                    "daily_limited_tab.bmp",
                    (500, 220, 1000, 340),
                    70,
                    24,
                    f"{base}_open_limited_activity",
                    ["daily_practice_anchor.bmp"],
                ):
                    raise PendingError(f"{spec.label} 限时活动标签不可用")
                if not self.wait_template(entry, VIEW, 8):
                    raise PendingError(f"{spec.label} 限时活动入口未出现")
                entry_anchor = ["daily_practice_anchor.bmp"]
            if not self.click_template(leader, entry, entry_roi, 35, 20, f"{base}_entry", entry_anchor):
                if spec.reward_only and spec.closed_template and self.template(spec.closed_template, VIEW)[0]:
                    self._set_team_activity_status(team, leader, spec, "done_no_reward", f"{spec.label} has no available reward")
                    self.event("team_activity_skip_no_reward", leader, team=team.index, activity=spec.key)
                    return
                raise PendingError(f"{spec.label} entry is unavailable")
            if not self.wait_template(spec.page_anchor, (250, 80, 1720, 950), 15):
                raise PendingError(f"{spec.label} page timeout")
        else:
            self.event("team_activity_resume_page", leader, team=team.index, activity=spec.key, leader_only=True)

        frame = self.capture()
        if spec.participate_template and self.active_template(spec.participate_template, (1050, 620, 1350, 740), frame)[0]:
            participate_action = f"{base}_participate"
            participate_status = self.state.action_status(leader.name, participate_action)
            if participate_status == "pending":
                raise PendingError(f"{base} 参与结果未知，为避免重复操作已停止")
            if participate_status == "done":
                raise PendingError(f"{base} 已记录参与但目录仍显示参与按钮，页面状态未知")
            if self.template("market_coupon_icon.bmp", (500, 300, 1450, 1000), frame)[0]:
                raise SafetyError(f"{base} 参与页面显示货币选项，拒绝进入")
            if not self.click_template(
                leader,
                spec.participate_template,
                (1050, 620, 1350, 740),
                51,
                18,
                participate_action,
                [spec.page_anchor, spec.participate_template],
                irreversible=True,
            ):
                raise PendingError(f"{base} 参与按钮消失，结果未知")
            if not self.wait_absent(spec.participate_template, (1050, 620, 1350, 740), 12):
                raise PendingError(f"{base} 参与后页面未切换，结果未知")
            self.commit_action(leader, participate_action, "已确认队长免费参与并离开活动目录")
            self.event("team_activity_participated", leader, team=team.index, activity=spec.key, leader_only=True)
            self.wait_interruptible(0.5)
            frame = self.capture()
        if spec.reward_only:
            # The live bond page uses ``参与`` when there is no claimable
            # reward.  This positive no-reward marker must be checked before
            # requiring the combat-flow templates, otherwise a normal empty
            # day is incorrectly reported as a missing battle asset.
            if spec.no_reward_template and self.active_template(spec.no_reward_template, (1050, 620, 1350, 740), frame)[0]:
                self._close_team_activity_page(leader, spec)
                self._set_team_activity_status(team, leader, spec, "done_no_reward", f"{spec.label} has no pending reward")
                self.event("team_activity_skip_no_reward", leader, team=team.index, activity=spec.key)
                return
            if not spec.reward_pending or not (ASSETS / spec.reward_pending).exists():
                evidence = self.save_raw_evidence(frame, leader, f"{base}_reward_state_missing", "pending")
                raise PendingError(f"{spec.label} reward-state template is missing; reward status is unknown (evidence: {evidence})")
            if not self.template(spec.reward_pending, (250, 80, 1720, 950), frame)[0]:
                self._close_team_activity_page(leader, spec)
                self._set_team_activity_status(team, leader, spec, "done_no_reward", f"{spec.label} has no pending reward")
                self.event("team_activity_skip_no_reward", leader, team=team.index, activity=spec.key)
                return
            if spec.required_member_count is not None and team.member_count != spec.required_member_count:
                detail = f"{spec.label}要求{spec.required_member_count}人队伍，当前为{team.member_count}人，未执行"
                evidence = self.save_raw_evidence(frame, leader, f"{base}_wrong_member_count", "pending")
                self._set_team_activity_status(team, leader, spec, "pending", detail)
                self.state.add_pending(f"Team{team.index}", spec.key, detail)
                self.event(
                    "team_activity_team_size_pending",
                    leader,
                    team=team.index,
                    activity=spec.key,
                    member_count=team.member_count,
                    required_member_count=spec.required_member_count,
                    evidence=str(evidence),
                )
                raise PendingError(detail)
            # A claimable reward is a different path and may need the normal
            # free-entry/combat templates.  Validate them only after the
            # reward marker is positively visible.
            flow_assets = [
                spec.reward_pending,
                spec.highest_unlocked,
                spec.free_template,
                spec.start_template,
                spec.result_confirm,
                spec.return_anchor,
                spec.close_template,
                spec.leader_start_template,
            ]
            self.ensure_assets([name for name in flow_assets if name])
        if spec.direct_start:
            # The live strong-approach page exposes a single free team
            # entrance. The user-confirmed flow is to press it directly; no
            # extra difficulty or reward selector click is needed here.
            self.event("team_activity_direct_start", leader, team=team.index, activity=spec.key, leader_only=True)
        else:
            if not spec.highest_unlocked or not self.click_template(
                leader,
                spec.highest_unlocked,
                (450, 150, 1500, 850),
                45,
                18,
                f"{base}_highest_unlocked",
                [spec.page_anchor],
            ):
                raise PendingError(f"{spec.label} highest unlocked difficulty is unreadable")
            if spec.free_template and not self.wait_template(spec.free_template, (600, 500, 1450, 1000), 8):
                raise PendingError(f"{spec.label} free-entry status is not verified")
            if self.template("market_coupon_icon.bmp", (500, 300, 1450, 1000))[0]:
                raise SafetyError(f"{spec.label} displayed a currency option; refusing entry")
        if not spec.start_template or not self.wait_template(spec.start_template, (600, 500, 1450, 1000), 8):
            raise PendingError(f"{spec.label} start control is missing")
        start_clicked = False
        start_deadline = time.monotonic() + 8
        while time.monotonic() < start_deadline:
            if self.click_template(
                leader,
                spec.start_template,
                (600, 500, 1450, 1000),
                55,
                18,
                start_action,
                [spec.page_anchor, spec.start_template],
                irreversible=True,
            ):
                start_clicked = True
                break
            self.wait_interruptible(0.3)
        if not start_clicked:
            raise PendingError(f"{spec.label} start control disappeared")
        self._set_team_activity_status(team, leader, spec, "running", f"{spec.label} start recorded; leader poll active")
        self._poll_generic_team_activity(team, leader, spec)

    def create_verified_team(self, team: Team, leader: Account) -> None:
        action = f"team{team.index}_create"
        if self.state.action_status(leader.name, action) == "done":
            return
        self.switch_account(leader)
        self.prepare_main(leader)
        self.open_team_room(leader, f"team{team.index}_open_create")
        if not self.wait_template("team_create_button.bmp", (951, 650, 1301, 850), 6):
            raise PendingError("team-create page did not open")
        if not self.click_template(leader, "team_create_button.bmp", (951, 650, 1301, 850), 63, 16, f"team{team.index}_create_open", ["team_create_button.bmp"]):
            raise PendingError("team-create button missing")
        if not self.wait_template("team_create_confirm.bmp", (751, 529, 1051, 729), 5):
            raise PendingError("team-create confirmation missing")
        self.select_team_create_type(team, leader)
        if not self.click_template(leader, "team_create_confirm.bmp", (751, 529, 1051, 729), 50, 16, action, ["team_create_confirm.bmp"], irreversible=True):
            raise PendingError("team-create confirmation disappeared")
        if self.wait_template("team_create_confirm.bmp", (751, 529, 1051, 729), 3):
            raise PendingError("team-create outcome unknown; refusing duplicate")
        self.commit_action(leader, action, "create confirmation disappeared")

    def invite_verified_member(
        self,
        team: Team,
        leader: Account,
        member: Account,
        row_template: str,
        invite_leader_template: str,
        scroll: bool,
    ) -> None:
        """Invite until success when a visible activity overlay blocked the click.

        Only the explicit ``invite_obstructed_retry`` signal is retried.  A
        delivered or otherwise unknown invitation remains pending and cannot
        be duplicated.
        """
        while True:
            try:
                return self._invite_verified_member_once(
                    team, leader, member, row_template, invite_leader_template, scroll
                )
            except PendingError as exc:
                if str(exc) != "invite_obstructed_retry":
                    raise
                self.check_stop()
                self.event("invite_retry_wait", member, team=team.index, leader=leader.name)
                self.wait_interruptible(0.5)

    def _invite_verified_member_once(
        self,
        team: Team,
        leader: Account,
        member: Account,
        row_template: str,
        invite_leader_template: str,
        scroll: bool,
    ) -> None:
        invite_action = f"team{team.index}_invite_{slug(member.name)}"
        accept_action = f"team{team.index}_accept_{slug(member.name)}"
        if self.state.action_status(member.name, accept_action) == "done":
            return
        invite_status = self.state.action_status(leader.name, invite_action)
        if invite_status == "done":
            self.event("invite_already_sent", member, team=team.index, leader=leader.name)
        else:
            if invite_status == "pending":
                raise PendingError(f"invitation outcome unknown for {member.name}; refusing duplicate")
            self.switch_account(leader)
            self.prepare_main(leader)
            # The handshake glyph is the only toolbar asset with a stable
            # one-pixel Flash anti-aliasing drift between accounts.  Keep its
            # ROI and the main-page anchor guard unchanged, but allow the
            # observed 0.898 render while still rejecting unrelated icons.
            if not self.click_template(
                leader,
                "friends_toolbar.bmp",
                (1715, 70, 1770, 120),
                12,
                11,
                f"team{team.index}_open_friends",
                ["anchor_top_menu.bmp"],
                threshold=0.88,
            ):
                raise PendingError("Friends toolbar immediately left of Mail is missing")
            if not self.wait_template("friends_panel_close.bmp", (1870, 260, 1920, 350), 5):
                raise PendingError("Friends panel did not open")
            # The lower-right activity notice is a known cause of missed friend
            # menu clicks.  Clear it while the Friends page is open as well as
            # on the main page, and verify its own X disappeared.
            self.clear_transient_notifications(leader, self.capture())
            # The Friends shell can report its close X before the account rows
            # finish painting.  Wait only for the configured exact row; this
            # is read-only and still fails closed when the row never appears.
            found = self.wait_template(row_template, (1715, 320, 1920, 900), 4)
            if not found and scroll:
                for _ in range(5):
                    self.content_guard(leader, ["friends_panel_close.bmp"])
                    Win32.wheel(*self.screen_point(1830, 550), -120)
                    self.event("content_wheel", leader, target=f"find_friend:{member.name}", x=1830, y=550, delta=-120)
                    self.wait_interruptible(0.25)
                    if self.template(row_template, (1715, 320, 1920, 900))[0]:
                        found = True
                        break
            if not found:
                if self.click_template(
                    leader,
                    "friends_panel_close.bmp",
                    (1870, 260, 1920, 350),
                    20,
                    20,
                    f"team{team.index}_close_friends_missing_row",
                    ["friends_panel_close.bmp"],
                ):
                    if not self.wait_absent("friends_panel_close.bmp", (1870, 260, 1920, 350), 5):
                        raise PendingError("Friends panel remained after missing-row cleanup")
                raise PendingError(f"configured friend row is missing for {member.name}")
            if not self.click_template(leader, row_template, (1715, 320, 1920, 900), 55, 18, f"team{team.index}_select_{member.name}", ["friends_panel_close.bmp"]):
                raise PendingError(f"friend row disappeared for {member.name}")
            self.clear_transient_notifications(leader, self.capture())
            if not self.wait_template("friend_invite_team.bmp", (1760, 350, 1920, 850), 4):
                raise PendingError(f"Invite Team menu missing for {member.name}")
            if not self.click_template(leader, "friend_invite_team.bmp", (1760, 350, 1920, 850), 30, 11, invite_action, ["friend_invite_team.bmp"], irreversible=True):
                raise PendingError(f"Invite Team menu disappeared for {member.name}")
            self.commit_action(leader, invite_action, f"single configured friend invitation sent to {member.name}")
            if not self.click_template(leader, "friends_panel_close.bmp", (1870, 260, 1920, 350), 20, 20, f"team{team.index}_close_friends", ["friends_panel_close.bmp"]):
                raise PendingError("Friends panel X missing before invited account switch")

        self.expected_invitation_members.add(member.name)
        self.switch_account(member, verify=False)
        self.active = member
        member_frame = self.capture()
        if not self.template("team_invite_accept_yes.bmp", (751, 500, 1051, 700), member_frame)[0]:
            # The invite dialog can be painted immediately after the lower
            # right notice.  Do not close or reclassify that notice while the
            # explicit accept control is already visible.
            self.clear_transient_notifications(member, member_frame)
        try:
            invite_wait_seconds = min(max(float(self.config.get("TeamInviteWaitSeconds", "30")), 15.0), 60.0)
        except (TypeError, ValueError):
            invite_wait_seconds = 30.0
        if not self.wait_template(invite_leader_template, (601, 350, 1301, 750), invite_wait_seconds):
            frame = self.capture()
            notice = self.template(
                "activity_notice_anchor.bmp",
                (1680, 730, 1919, 930),
                frame,
                threshold=0.50,
            )[0]
            notice_x = self._activity_notice_close_template(frame) is not None
            evidence = self.save_raw_evidence(frame, member, "team_invite_not_visible_after_wait", "pending")
            self.event(
                "invite_not_visible_after_wait",
                member,
                team=team.index,
                leader=leader.name,
                wait_seconds=invite_wait_seconds,
                notice=notice,
                notice_x=notice_x,
                evidence=str(evidence),
            )
            if notice and notice_x and invite_status != "done":
                # A visible notice after the send is positive evidence that the
                # click was obstructed.  Re-open the leader path once the notice
                # is cleared; delivered/unknown sends remain pending.
                self.clear_transient_notifications(member, frame)
                self.state.set_action(leader.name, invite_action, "new", "send was visibly blocked by activity notice")
                self.event("invite_retry_after_overlay", member, team=team.index, leader=leader.name)
                # The overlay was positively visible and its own X was closed;
                # no invite click was delivered.  Let the wrapper retry the
                # complete verified path without growing the Python stack.
                raise PendingError("invite_obstructed_retry")
            suffix = "; prior invitation is not repeated" if invite_status == "done" else ""
            raise PendingError(f"invite from verified leader did not appear for {member.name}{suffix}")
        if not self.template("team_invite_accept_yes.bmp", (751, 500, 1051, 700))[0]:
            raise PendingError(f"invite accept control missing for {member.name}")
        if self.state.action_status(member.name, accept_action) == "pending":
            raise PendingError(f"accept outcome unknown for {member.name}; refusing duplicate")
        if not self.click_template(member, "team_invite_accept_yes.bmp", (751, 500, 1051, 700), 50, 16, accept_action, [invite_leader_template, "team_invite_accept_yes.bmp"], irreversible=True):
            raise PendingError(f"invite accept control disappeared for {member.name}")
        if self.wait_template(invite_leader_template, (601, 350, 1301, 750), 4):
            raise PendingError(f"invite dialog remained after accept for {member.name}")
        self.commit_action(member, accept_action, f"accepted invitation from configured leader {leader.name}")
        self.expected_invitation_members.discard(member.name)

    def verify_three_member_team(self, team: Team, leader: Account, three_member_template: str) -> None:
        """Verify the configured team-size template before content clicks."""
        self.switch_account(leader)
        self.prepare_main(leader)
        self.open_team_room(leader, f"team{team.index}_open_verify")
        if not self.wait_template("team_room_close.bmp", (1200, 260, 1280, 340), 6):
            raise PendingError("team room did not open for member verification")
        if not self.template(three_member_template, (650, 340, 1250, 800))[0]:
            raise PendingError(f"Team{team.index} {team.member_label} template did not match; no activity start")
        if not self.click_template(leader, "team_room_close.bmp", (1200, 260, 1280, 340), 20, 20, f"team{team.index}_close_verified_room", ["team_room_close.bmp", three_member_template]):
            raise PendingError("team-room X missing after member verification")
        if not self.wait_until_main(5):
            raise PendingError("team room did not close after member verification")

    def reconcile_three_member_team(
        self,
        team: Team,
        leader: Account,
        invitees: list[Account],
        three_member_template: str,
    ) -> bool:
        """Reconcile a previously completed team without resending invites.

        A crash or a prior run can leave the UI with the configured members
        present while the per-action ledger only contains the leader's send
        record.  The exact room template is positive proof that all configured
        invitations arrived and were accepted, so it is safe to commit missing
        ledger entries without another invite/accept click.
        """
        self.switch_account(leader)
        self.prepare_main(leader)
        self.open_team_room(leader, f"team{team.index}_open_reconcile")
        if not self.wait_template("team_room_close.bmp", (1200, 260, 1280, 340), 6):
            raise PendingError("team room did not open for membership reconciliation")
        present = self.template(three_member_template, (650, 340, 1250, 800))[0]
        if not present:
            if not self.click_template(leader, "team_room_close.bmp", (1200, 260, 1280, 340), 20, 20, f"team{team.index}_close_reconcile_empty", ["team_room_close.bmp"]):
                raise PendingError("team-room X missing after empty membership reconciliation")
            if not self.wait_until_main(5):
                raise PendingError("team room did not close after empty reconciliation")
            return False
        for member in invitees:
            invite_action = f"team{team.index}_invite_{slug(member.name)}"
            accept_action = f"team{team.index}_accept_{slug(member.name)}"
            if self.state.action_status(leader.name, invite_action) != "done":
                proof = (
                    f"exact three-member room proves invitation delivered to {member.name}"
                    if team.member_count == 3
                    else f"exact {team.member_label} room proves invitation delivered to {member.name}"
                )
                self.commit_action(leader, invite_action, proof)
            if self.state.action_status(member.name, accept_action) != "done":
                proof = (
                    f"exact three-member room proves {member.name} accepted"
                    if team.member_count == 3
                    else f"exact {team.member_label} room proves {member.name} accepted"
                )
                self.commit_action(member, accept_action, proof)
        self.event("team_membership_reconciled", team=team.index, leader=leader.name, members=list(team.members), member_count=team.member_count)
        if not self.click_template(leader, "team_room_close.bmp", (1200, 260, 1280, 340), 20, 20, f"team{team.index}_close_reconcile", ["team_room_close.bmp", three_member_template]):
            raise PendingError("team-room X missing after positive membership reconciliation")
        if not self.wait_until_main(5):
            raise PendingError("team room did not close after positive reconciliation")
        return True

    def _rebuild_team_after_membership_loss(
        self,
        team: Team,
        leader: Account,
        invitees: list[Account],
    ) -> None:
        """Clear a positively detected stale team before rebuilding it.

        The team room probe has already proved that the configured members are
        absent.  If a one-person shell remains, dissolve that shell through
        its verified button, then reset only create/invite ledger entries.  A
        completed activity such as wanted is intentionally left done so resume
        continues at the next activity.
        """
        self.switch_account(leader)
        self.prepare_main(leader)
        self.open_team_room(leader, f"team{team.index}_open_rebuild")
        if not self.wait_template("team_room_close.bmp", (1200, 260, 1280, 340), 6):
            raise PendingError("team room did not open for stale-team rebuild")
        if self.template("team_dissolve_button.bmp", (1060, 315, 1160, 380))[0]:
            action = f"team{team.index}_dissolve_stale_before_rebuild"
            if self.state.action_status(leader.name, action) == "pending":
                raise PendingError("stale team dissolution outcome unknown; refusing duplicate")
            if self.state.action_status(leader.name, action) == "done":
                # The button is still positively visible, proving the prior
                # click did not transition the room.  This is the only case in
                # which the already-recorded irreversible action is safely
                # retryable.
                self.state.set_action(leader.name, action, "new", "解散按钮仍可见，确认上次点击未生效，允许一次重试")
            if self.state.action_status(leader.name, action) != "done":
                if not self.click_template(
                    leader,
                    "team_dissolve_button.bmp",
                    (1060, 315, 1160, 380),
                    25,
                    10,
                    action,
                    ["team_room_close.bmp", "team_dissolve_button.bmp"],
                    irreversible=True,
                ):
                    raise PendingError("stale team dissolve button disappeared")
                if not self.wait_absent("team_dissolve_button.bmp", (1060, 315, 1160, 380), 8):
                    raise PendingError("stale team dissolve button remained after click")
                if not self.wait_absent("team_room_close.bmp", (1200, 260, 1280, 340), 8):
                    raise PendingError("stale team dissolution outcome unknown")
                self.commit_action(leader, action, "verified one-person stale team dissolved before rebuild")
        elif not self.template("team_create_button.bmp", (951, 650, 1301, 850))[0]:
            raise PendingError("stale team room has neither dissolve nor create state")
        self.state.set_action(leader.name, f"team{team.index}_create", "new", "三人队伍成员缺失，已清理旧队伍后重建")
        for member in invitees:
            self.state.set_action(leader.name, f"team{team.index}_invite_{slug(member.name)}", "new", "旧队伍成员缺失，允许重新邀请")
            self.state.set_action(member.name, f"team{team.index}_accept_{slug(member.name)}", "new", "旧队伍成员缺失，允许重新接受邀请")
        if self.template("team_room_close.bmp", (1200, 260, 1280, 340))[0]:
            if not self.click_template(leader, "team_room_close.bmp", (1200, 260, 1280, 340), 20, 20, f"team{team.index}_close_rebuild_room", ["team_room_close.bmp"]):
                raise PendingError("team-room X missing after stale-team rebuild")
            if not self.wait_until_main(5):
                raise PendingError("stale-team room did not close before rebuild")
        self.event("team_rebuild_after_membership_loss", leader, team=team.index, members=list(team.members), member_count=team.member_count)

    def run_verified_group_instance(self, team: Team, leader: Account, target_count: int | None = None) -> None:
        spec = TEAM_ACTIVITY_BY_KEY["group_instance"]
        if self._team_activity_completion_verified(team, leader, spec):
            return
        if self._team_activity_terminal(self._team_activity_status(team, leader, spec)):
            self._set_team_activity_status(team, leader, spec, "running", "旧完成记录缺少本日奖励次数为0证据，重新核验")
        self.switch_account(leader)
        frame = self.capture()
        first_attempt = self._next_team_attempt_index(team, leader, spec)
        selected_stop_attempt = (
            first_attempt + target_count
            if target_count is not None
            else None
        )
        attempt_index = first_attempt
        if (
            self._team_battle_start_visible(frame)
            or self.template("battle_auto_off.bmp", BATTLE_AUTO_ROI, frame)[0]
            or self._battle_auto_on_visible(frame)
            or self._team_result_template(frame)
            or self._team_reward_result_visible(frame)
        ):
            # The leader can be left in a delayed battle scene by the prior
            # activity.  Resume that positively identified scene before
            # attempting to reopen the group-instance catalogue.
            self._resume_team_instance_battle(team, leader, frame, attempt_index=attempt_index)

        first_attempt = self._next_team_attempt_index(team, leader, spec)
        for _ in range(spec.reward_counter_max + 1):
            self.switch_account(leader)
            frame = self.capture()
            if not self.template("team_instance_close.bmp", (1370, 240, 1460, 330), frame)[0]:
                self.prepare_main(leader)
                if not self.click_template(
                    leader,
                    "team_instance_left_entry.bmp",
                    (0, 220, 240, 340),
                    93,
                    14,
                    f"team{team.index}_open_group_instance",
                    ["anchor_top_menu.bmp"],
                ):
                    raise PendingError("左侧组队副本入口缺失")
                if not self.wait_template("team_instance_participate.bmp", (1051, 569, 1351, 819), 8):
                    raise PendingError("组队副本参与按钮缺失")
                if not self.click_template(
                    leader,
                    "team_instance_participate.bmp",
                    (1051, 569, 1351, 819),
                    51,
                    18,
                    f"team{team.index}_instance_participate",
                    ["team_instance_participate.bmp"],
                ):
                    raise PendingError("组队副本参与按钮消失")
                if not self.wait_template("team_instance_close.bmp", (1370, 240, 1460, 330), 15):
                    raise PendingError("组队副本选择页打开超时")
                frame = self.capture()

            remaining = self._read_team_reward_remaining(leader, spec, frame)
            if remaining == 0:
                if not self.click_template(
                    leader,
                    "team_instance_close.bmp",
                    (1370, 240, 1460, 330),
                    20,
                    20,
                    f"team{team.index}_close_group_instance_reward_zero",
                    ["team_instance_close.bmp"],
                ):
                    raise PendingError("组队副本奖励次数为0，但关闭按钮缺失")
                if not self.wait_until_main(8):
                    raise PendingError("组队副本奖励次数为0，但未返回主界面")
                self._finish_team_activity_at_zero(team, leader, spec, frame)
                return

            attempt_index = self._next_team_attempt_index(team, leader, spec)

            if selected_stop_attempt is not None and attempt_index >= selected_stop_attempt:
                if not self.click_template(
                    leader,
                    "team_instance_close.bmp",
                    (1370, 240, 1460, 330),
                    20,
                    20,
                    f"team{team.index}_close_group_instance_selected_target",
                    ["team_instance_close.bmp"],
                ):
                    raise PendingError("组队副本已完成所选场数，但关闭按钮缺失")
                if not self.wait_until_main(8):
                    raise PendingError("组队副本已完成所选场数，但未返回主界面")
                self.event(
                    "team_selected_activity_target_complete",
                    leader,
                    team=team.index,
                    activity=spec.key,
                    target_count=target_count,
                    completed_count=attempt_index - first_attempt,
                    remaining_rewards=remaining,
                    home_returned=True,
                )
                return

            if attempt_index > spec.reward_counter_max:
                evidence = self.save_raw_evidence(frame, leader, f"team{team.index}_group_instance_nonzero_after_limit", "pending")
                raise PendingError(f"组队副本已完成{spec.reward_counter_max}场但本日奖励次数仍为{remaining}：{evidence}")
            action = f"{self._team_attempt_base(team, spec, attempt_index)}_start"
            if self.state.action_status(leader.name, action) == "pending":
                raise PendingError(f"组队副本第{attempt_index}场结果未知，拒绝重复开始")
            if not self.click_template(
                leader,
                "team_instance_hard_card.bmp",
                (550, 300, 950, 650),
                200,
                130,
                action,
                ["team_instance_close.bmp", "team_instance_hard_card.bmp"],
                irreversible=True,
            ):
                raise PendingError("已验证的困难组队副本卡片缺失")
            if not self.wait_absent("team_instance_close.bmp", (1370, 240, 1460, 330), 30):
                raise PendingError("组队副本未离开选择页，开始结果待处理")
            self.commit_action(leader, action, f"第{attempt_index}场已离开选择页并进入战斗")
            self._set_team_activity_status(team, leader, spec, "running", f"第{attempt_index}场战斗进行中")
            self._resume_team_instance_battle(
                team,
                leader,
                self.capture(),
                attempt_index=attempt_index,
            )
        raise PendingError("组队副本达到单次运行安全上限，本日奖励次数仍未确认为0")

    def run_verified_wanted(self, team: Team, leader: Account) -> None:
        spec = TEAM_ACTIVITY_BY_KEY["wanted"]
        if self._team_activity_terminal(self._team_activity_status(team, leader, spec)):
            return
        action = f"team{team.index}_wanted_auto"
        if self.active != leader:
            self.switch_account(leader)
        if self.state.action_status(leader.name, action) == 'new':
            self.prepare_main(leader)
            if not self.click_template(leader, "daily_practice_toolbar.bmp", (651, 69, 851, 169), 20, 20, f"team{team.index}_daily_practice", ["anchor_top_menu.bmp"]):
                raise PendingError("daily-practice toolbar missing before wanted")
            if not self.wait_template("daily_practice_anchor.bmp", (951, 119, 1501, 369), 8):
                raise PendingError("daily-practice page timeout before wanted")
            if not self.click_template(leader, "daily_wanted_row.bmp", (451, 300, 1101, 800), 72, 24, f"team{team.index}_wanted_row", ["daily_practice_anchor.bmp"]):
                raise PendingError("wanted daily row missing")
            if not self.wait_template("daily_participate.bmp", (1051, 569, 1351, 819), 5):
                raise PendingError("wanted Participate button missing")
            if not self.click_template(leader, "daily_participate.bmp", (1051, 569, 1351, 819), 51, 18, f"team{team.index}_wanted_participate", ["daily_participate.bmp"]):
                raise PendingError("wanted Participate button disappeared")
            if not self.wait_template("wanted_team_anchor.bmp", (500, 200, 1450, 850), 15):
                raise PendingError("team wanted page timeout")
            if not self.click_template(leader, "wanted_team_auto.bmp", (1050, 300, 1300, 500), 58, 17, action, ["wanted_team_anchor.bmp"], irreversible=True):
                raise PendingError("wanted Auto Complete button missing")
        else:
            self.event('wanted_pending_observation_resumed', leader, target=action,
                       detail='观察原自动链；不重复入口、自动或开战')
        deadline = time.monotonic() + 1200
        empty_stable = 0
        member_auto_verified = self._team_members_auto_complete(team, spec, 1)
        unknown_since = None
        battle_watchdog = None
        while time.monotonic() < deadline:
            self.check_stop()
            frame = self.capture()
            battle_visible = self._combat_active_visible('arena', frame)
            if battle_visible:
                if battle_watchdog is None:
                    battle_watchdog = BattleWatchdog.from_config(getattr(self, 'config', {}))
                check_battle_watchdog(self, battle_watchdog, leader, frame, action, mode="wanted")
            elif self.main_ready(frame) or self._team_result_template(frame) or self._team_reward_result_visible(frame):
                battle_watchdog = None
            chain_visible = (battle_visible or self._team_result_template(frame)
                             or self._team_reward_result_visible(frame)
                             or self.template('wanted_running_anchor.bmp', (790,130,1110,230), frame)[0]
                             or (self.main_ready(frame) and self.template('wanted_task_prefix_live.bmp',
                                  (1715,480,1910,550),frame,threshold=.98)[0])
                             or self.main_ready(frame) and wanted_task_list_is_empty(frame))
            if chain_visible:
                unknown_since = None
            elif unknown_since is None:
                unknown_since = time.monotonic()
            elif time.monotonic() - unknown_since >= 60:
                evidence = self.save_raw_evidence(frame, leader, 'wanted_chain_unverified', 'pending')
                self.event('wanted_pending', leader, evidence=str(evidence), detail='原自动链画面60秒未确认')
                raise PendingError('通缉原自动链未确认，保留未决动作，未重复启动')
            if team.member_count > 1 and not member_auto_verified and (
                self.template("battle_auto_off.bmp", BATTLE_AUTO_ROI, frame)[0]
                or self._battle_auto_on_visible(frame)
            ):
                self._ensure_team_members_auto(team, leader, spec, 1)
                member_auto_verified = True
                frame = self.capture()
            if self.main_ready(frame) and wanted_task_list_is_empty(frame) and self.verify_identity(leader,frame)[0]:
                empty_stable += 1
                if empty_stable >= 5:
                    if team.member_count > 1 and not member_auto_verified:
                        raise PendingError("通缉已结束，但未逐一确认队员自动战斗；保留断点")
                    self.commit_action(leader, action, "automatic wanted chain reached a stably empty task list")
                    self._set_team_activity_status(team, leader, spec, "done", "wanted task row disappeared on verified main page")
                    self.event("wanted_task_row_disappeared", leader, team=team.index, stable_frames=empty_stable)
                    return
            else:
                empty_stable = 0
            self.wait_interruptible(3)
        raise PendingError("wanted automatic chain timeout; action remains pending")

    def run_verified_wanted_and_dissolve(self, team: Team, leader: Account) -> None:
        """Compatibility wrapper; dissolution is deferred until all activities finish."""
        self.run_verified_wanted(team, leader)

    def resolve_team_pending(self, team_index: int) -> None:
        """Resolve historical team pending rows after the full verified flow."""
        for scope, module in (
            (f"Team{team_index}", "verified_daily"),
            ("global", f"team{team_index}"),
        ):
            self.state.resolve_pending(scope, module)

    def run_verified_team_daily(self, team: Team) -> None:
        leader, invitees, row_templates, invite_leader_template, three_member_template = self.verified_team_profile(team)
        selected_mode = self.team_battle_targets is not None
        if selected_mode and not any(self.team_battle_targets.values()):
            self.selected_team_commands_done.add(team.index)
            self.event("team_selected_battles_complete", leader, team=team.index, targets=dict(self.team_battle_targets), team_preserved=True, content_clicks=0)
            return
        if not selected_mode and self._team_complete(team.index):
            self.resolve_team_pending(team.index)
            deferred_specs = self._deferred_team_activity_specs()
            if deferred_specs:
                self.state.global_state.setdefault("teams", {})[str(team.index)] = {
                    "members": list(team.members),
                    "member_count": team.member_count,
                    "leader": leader.name,
                    "status": "deferred_activities",
                    "deferred": [spec.key for spec in deferred_specs],
                    "updated_at": now_iso(),
                    "activities": {
                        spec.key: {
                            "status": self._team_activity_status(team, leader, spec),
                            "updated_at": now_iso(),
                        }
                        for spec in TEAM_ACTIVITY_SPECS
                    },
                }
                self.state.save_global()
                self.event(
                    "team_verified_daily_deferred",
                    team=team.index,
                    leader=leader.name,
                    member_count=team.member_count,
                    deferred=[spec.key for spec in deferred_specs],
                    team_preserved=True,
                )
            else:
                self.event("team_verified_daily_skip_done", team=team.index, leader=leader.name, member_count=team.member_count)
            return
        team_records = self.state.global_state.get("teams", {})
        previous_team = team_records.get(str(team.index), {}) if isinstance(team_records, dict) else {}
        if isinstance(previous_team, dict) and previous_team.get("status") == "dissolved_after_wanted":
            detail = f"Team{team.index} was dissolved before all team activities were implemented; manual rebuild required"
            self.state.add_pending(f"Team{team.index}", "verified_daily", detail)
            self.event("team_rebuild_required", team=team.index, leader=leader.name, detail=detail)
            return
        required = [
            "team_toolbar.bmp", "team_create_button.bmp", "team_create_confirm.bmp", "friends_toolbar.bmp",
            "friends_panel_close.bmp", "friend_invite_team.bmp", "team_invite_accept_yes.bmp", "team_room_close.bmp",
            "team_dissolve_button.bmp", "team_create_type_dropdown.bmp", "team_create_group_option.bmp",
            "team_create_group_type.bmp", "team_room_type_dropdown.bmp", "team_room_group_type.bmp",
            invite_leader_template, *row_templates,
        ]
        required.extend(self._team_activity_required_assets())
        required = list(dict.fromkeys(required))
        try:
            self.ensure_assets(required)
        except PendingError as exc:
            detail = f"Team{team.index} preflight failed before content clicks: {exc}"
            self.state.add_pending(f"Team{team.index}", "verified_daily", detail)
            self.event(
                "team_pending",
                team=team.index,
                leader=leader.name,
                member_count=team.member_count,
                detail=detail,
                content_clicks=0,
            )
            return
        self.create_verified_team(team, leader)
        # A prior run can leave the leader in a positively identified team
        # battle before the room can be reopened.  Preserve that known team
        # membership and resume the battle first; attempting to open the room
        # over a live battle would otherwise turn a recoverable checkpoint into
        # an identity/page-anchor failure.
        self.switch_account(leader)
        leader_frame = self.capture()
        team_battle_resume = self._team_battle_start_visible(leader_frame)
        team_activity_resume: TeamActivitySpec | None = None
        if team_battle_resume:
            reconciled = True
            self.event(
                "team_battle_membership_resume",
                leader,
                team=team.index,
                members=list(team.members),
                member_count=team.member_count,
                mapping_source="verified_team_battle_page",
            )
        else:
            team_activity_resume = next(
                (
                    spec
                    for spec in self._enabled_team_activity_specs()
                    if spec.runner == "generic" and self._team_activity_visible(spec, leader_frame)
                ),
                None,
            )
            if team_activity_resume is not None:
                reconciled = True
                self.event(
                    "team_activity_membership_resume",
                    leader,
                    team=team.index,
                    activity=team_activity_resume.key,
                    members=list(team.members),
                    member_count=team.member_count,
                    mapping_source="verified_team_activity_page",
                )
            if team_activity_resume is None and (ASSETS / three_member_template).exists():
                reconciled = self.reconcile_three_member_team(team, leader, invitees, three_member_template)
            elif team_activity_resume is None:
                # A missing exact asset is enrollable only after the room
                # identity probe proves every configured member is already
                # present. If the probe sees fewer members, proceed with the
                # configured invitation path; it will enroll the template
                # after acceptance.
                enrolled = self.capture_verified_three_member_template(team, leader, invitees, three_member_template)
                reconciled = enrolled and self.reconcile_three_member_team(team, leader, invitees, three_member_template)
        if not reconciled:
            if self.state.action_status(leader.name, f"team{team.index}_create") == "done":
                self._rebuild_team_after_membership_loss(team, leader, invitees)
                self.create_verified_team(team, leader)
            for index, (member, row_template) in enumerate(zip(invitees, row_templates)):
                self.invite_verified_member(team, leader, member, row_template, invite_leader_template, scroll=index > 0)
            if not self.capture_verified_three_member_template(team, leader, invitees, three_member_template):
                raise PendingError(f"Team{team.index} {team.member_label} identities were not verified after invitations")
        if not team_battle_resume and team_activity_resume is None:
            self.verify_three_member_team(team, leader, three_member_template)
            self.ensure_team_room_type(team, leader)
        active_specs = self._enabled_team_activity_specs()
        if selected_mode:
            active_specs = tuple(
                spec for spec in active_specs
                if spec.key in self.team_battle_targets and self.team_battle_targets[spec.key] > 0
            )
        deferred_specs = self._deferred_team_activity_specs()
        for spec in active_specs:
            try:
                self.ensure_assets(self._team_assets_for_spec(spec))
            except PendingError as exc:
                detail = f"Team{team.index} {spec.label}模板预检失败，队伍保持不解散：{exc}"
                self._set_team_activity_status(team, leader, spec, "pending", detail)
                self.state.add_pending(f"Team{team.index}", spec.key, detail)
                self.event("team_activity_pending_missing_assets", leader, team=team.index, activity=spec.key, detail=detail)
                raise PendingError(detail) from exc
            if spec.runner == "group_instance":
                self.run_verified_group_instance(team, leader, self.team_battle_targets.get(spec.key) if selected_mode else None)
            elif spec.runner == "wanted":
                self.run_verified_wanted(team, leader)
            elif spec.key == "strong_approach" and selected_mode:
                self.run_verified_strong_approach(team, leader, spec, self.team_battle_targets.get(spec.key))
            else:
                self.run_generic_team_activity(team, leader, spec)
            if not selected_mode and not self._team_activity_completion_verified(team, leader, spec):
                raise PendingError(f"Team{team.index} {spec.label}未达到奖励次数为0的完整完成条件")
        if selected_mode:
            self.selected_team_commands_done.add(team.index)
            self.event("team_selected_battles_complete", leader, team=team.index, targets=dict(self.team_battle_targets), team_preserved=True)
            return
        if deferred_specs:
            self.state.global_state.setdefault("teams", {})[str(team.index)] = {
                "members": list(team.members),
                "member_count": team.member_count,
                "leader": leader.name,
                "status": "deferred_activities",
                "deferred": [spec.key for spec in deferred_specs],
                "updated_at": now_iso(),
                "activities": {
                    spec.key: {
                        "status": self._team_activity_status(team, leader, spec),
                        "updated_at": now_iso(),
                    }
                    for spec in TEAM_ACTIVITY_SPECS
                },
            }
            self.state.save_global()
            self.resolve_team_pending(team.index)
            self.event(
                "team_verified_daily_deferred",
                team=team.index,
                leader=leader.name,
                member_count=team.member_count,
                deferred=[spec.key for spec in deferred_specs],
                team_preserved=True,
            )
            return
        self.dissolve_team_after_activities(leader)
        if self.state.action_status(leader.name, "dissolve_team_after_activities") != "done":
            raise PendingError(f"Team{team.index} dissolution was not positively verified")
        self.state.global_state.setdefault("teams", {})[str(team.index)] = {
            "members": list(team.members),
            "member_count": team.member_count,
            "leader": leader.name,
            "status": "dissolved_after_activities",
            "updated_at": now_iso(),
            "activities": {
                spec.key: {
                    "status": self._team_activity_status(team, leader, spec),
                    "updated_at": now_iso(),
                }
                for spec in TEAM_ACTIVITY_SPECS
            },
        }
        self.state.save_global()
        self.resolve_team_pending(team.index)
        self.event("team_verified_daily_complete", team=team.index, leader=leader.name, member_count=team.member_count)

    def run_team_selection(self, team_index: int) -> None:
        team = next((item for item in self.teams if item.index == team_index), None)
        if team is None:
            raise ValueError(f"unknown team index: {team_index}")
        self.run_verified_team_daily(team)

    def run_team(self) -> None:
        for team in self.teams:
            if self.check_pause_boundary():
                raise PendingError("team stage paused before completion")
            self.run_team_selection(team.index)
        unfinished = [str(team.index) for team in self.teams if not self._team_complete(team.index)]
        if unfinished:
            detail = "team stage unresolved: Team" + ", Team".join(unfinished)
            self.state.add_pending("global", "team", detail)
            raise PendingError(detail)
        self.state.resolve_pending("global", "team")
        self.state.global_state["stage"] = "full"
        self.state.save_global()
        self.event("stage_complete", stage="team")

    def current_market_cycle(self) -> str:
        now = datetime.now().strftime("%H:%M")
        current = "PRE08"
        for value in parse_csv(self.config.get("MarketRefreshTimes", "08:00,11:00,14:00,17:00,20:00,23:00")):
            if now >= value:
                current = value.replace(":", "")
        return current

    def market_offer_key(self, cycle: str, slot: int) -> str:
        return f"market_{date.today():%Y%m%d}_{cycle}_slot{slot}"

    def market_slot_rois(self) -> tuple[tuple[int, int, int, int], ...]:
        # Coordinates are relative to the trusted 1920x1032 client geometry.
        return (
            (621, 369, 886, 544),
            (871, 369, 1136, 544),
            (1121, 369, 1391, 544),
            (621, 539, 886, 714),
            (871, 539, 1136, 714),
            (1121, 539, 1391, 714),
        )

    def market_balance(self, frame: Image.Image, coupon: bool) -> int:
        # The market modal dims the world behind it.  Restrict the search to
        # the numeric text after each currency icon, not the icon itself.  A
        # previous 48/35px crop truncated five-digit coupon and three-digit
        # ingot balances, which could turn a real delta into an unknown result.
        # These bounds are relative to the trusted 1920x1032 client geometry.
        roi = (220, 154, 300, 184) if coupon else (301, 154, 380, 184)
        return read_template_digits(frame, self.templates, ASSETS, "market_balance_digit_", roi, threshold=0.65, min_digits=1, max_digits=8)

    def market_price(self, frame: Image.Image, slot: tuple[int, int, int, int]) -> int:
        _x1, _y1, x2, _y2 = slot
        # Coupon and item art can shift by a few pixels between cards.  Anchor
        # the numeric ROI to the positively matched coupon icon instead of a
        # card-relative constant; the alternate ingot/coin price to the right
        # remains outside this bounded region.
        ok, _score, icon_x, icon_y = self.template("market_coupon_icon.bmp", slot, frame)
        if not ok:
            raise PendingError("coupon icon disappeared before price read")
        roi = (icon_x + 25, icon_y - 2, min(x2, icon_x + 63), icon_y + 25)
        return read_template_digits(frame, self.templates, ASSETS, "market_price_digit_", roi, threshold=0.65, min_digits=1, max_digits=5)

    def market_purchase_prerequisites(self) -> None:
        self.ensure_assets(
            [
                "market_sold_out.bmp",
                "market_purchase_confirm_coupon.bmp",
                "market_stamina_potion.bmp",
                "market_coupon_icon.bmp",
                "market_buy_button.bmp",
                *[f"market_price_digit_{digit}.bmp" for digit in range(10)],
                *[f"market_balance_digit_{digit}.bmp" for digit in range(10)],
            ]
        )
        if self.market_currency != "coupon_only":
            raise PendingError("market purchase policy must remain coupon_only")
        if not self.allow_purchase or not self.market_direct_buy_verified:
            raise PendingError("market purchase is not enabled by a verified configuration")

    def find_exchange_store_notification(self, frame: Image.Image) -> dict[str, object] | None:
        """Locate the separate organization exchange-store notice.

        This is deliberately not used by the coupon-only black-market flow:
        the live ``兑换商店新进货了哦`` card opens an organization-points shop,
        not the black-market coupon modal.  It remains available as a
        diagnostic classifier so the two products cannot be conflated.
        """
        self.ensure_assets(["market_entry_icon_current.bmp", "market_entry_view_current.bmp"])
        icon_roi = MARKET_NOTICE_ROI
        icon_candidates = (
            ("market_entry_icon_current.bmp", 0.88),
        )
        for icon_name, icon_threshold in icon_candidates:
            icon_ok, icon_score, icon_x, icon_y = self.template(icon_name, icon_roi, frame, threshold=icon_threshold)
            if not icon_ok:
                continue
            # The button is immediately below the icon.  Search a generous
            # but still in-card ROI to allow the transient card's small slide.
            button_roi = (
                max(MARKET_NOTICE_ROI[0], icon_x - 35),
                max(MARKET_NOTICE_ROI[1], icon_y + 90),
                min(MARKET_NOTICE_ROI[2], icon_x + 180),
                min(MARKET_NOTICE_ROI[3], icon_y + 165),
            )
            button_ok, button_score, button_x, button_y = self.template(
                "market_entry_view_current.bmp",
                button_roi,
                frame,
                threshold=0.88,
            )
            if not button_ok:
                # The old View asset is a safe compatibility fallback for a
                # live animation frame; the icon-to-button geometry remains
                # the independent positive gate.
                button_ok, button_score, button_x, button_y = self.template(
                    "market_entry_view.bmp",
                    button_roi,
                    frame,
                    threshold=0.70,
                )
                button_name = "market_entry_view.bmp"
            else:
                button_name = "market_entry_view_current.bmp"
            if not button_ok:
                continue
            if not (
                abs(button_x - (icon_x + 8)) <= 55
                and 90 <= button_y - icon_y <= 145
            ):
                continue
            return {
                "anchor": icon_name,
                "anchor_score": icon_score,
                "anchor_x": icon_x,
                "anchor_y": icon_y,
                "view": button_name,
                "view_score": button_score,
                "view_x": button_x,
                "view_y": button_y,
            }
        return None

    def find_exchange_store_dialog_view(self, frame: Image.Image) -> dict[str, object] | None:
        """Find the first, reversible ``让我看看你的商品`` dialog option.

        On the current client the notification View control opens the NPC
        conversation before the market modal.  Require both the dialog's own
        close control and the first option text in their expected relationship
        before clicking; the second ``以后再说`` option is deliberately not
        enrolled and can never be selected by this path.
        """
        self.ensure_assets(["market_dialog_close_current.bmp", "market_dialog_view_option_current.bmp"])
        close_ok, close_score, close_x, close_y = self.template(
            "market_dialog_close_current.bmp",
            (1100, 350, 1250, 500),
            frame,
            threshold=0.88,
        )
        if not close_ok:
            return None
        option_ok, option_score, option_x, option_y = self.template(
            "market_dialog_view_option_current.bmp",
            (700, 500, 1150, 680),
            frame,
            threshold=0.88,
        )
        if not option_ok:
            return None
        if not (
            close_x - 500 <= option_x <= close_x - 200
            and close_y + 100 <= option_y <= close_y + 210
        ):
            return None
        return {
            "close": "market_dialog_close_current.bmp",
            "close_score": close_score,
            "close_x": close_x,
            "close_y": close_y,
            "option": "market_dialog_view_option_current.bmp",
            "option_score": option_score,
            "option_x": option_x,
            "option_y": option_y,
        }

    def advance_exchange_store_dialog(self, account: Account) -> None:
        """Advance the exchange-store conversation only when explicitly probed."""
        for _ in range(40):
            current = self.capture()
            if self.template("market_anchor.bmp", (651, 69, 1251, 319), current)[0]:
                return
            dialog = self.find_exchange_store_dialog_view(current)
            if dialog is not None:
                self.event(
                    "market_dialog_match",
                    account,
                    close=dialog["close"],
                    close_score=dialog["close_score"],
                    option=dialog["option"],
                    option_score=dialog["option_score"],
                    option_x=dialog["option_x"],
                    option_y=dialog["option_y"],
                )
                self.click_point(
                    account,
                    int(dialog["option_x"]) + 84,
                    int(dialog["option_y"]) + 14,
                    "black_market_dialog_view",
                    [str(dialog["close"]), str(dialog["option"])],
                    anchor_threshold=0.88,
                )
                return
            self.wait_interruptible(0.2)

    def record_market_offer(
        self,
        account: Account,
        cycle: str,
        slot_index: int,
        status: str,
        frame: Image.Image,
        **fields: object,
    ) -> Path:
        """Write a structured quote ledger row and its visual evidence."""
        key = self.market_offer_key(cycle, slot_index)
        evidence = self.save_evidence(frame, account, f"market_{cycle}_slot{slot_index}_quote", "quote")
        record: dict[str, object] = {
            "date": date.today().isoformat(),
            "cycle": cycle,
            "slot": slot_index,
            "item": "unknown",
            "currency": "unknown",
            "price": None,
            "coupon_before": None,
            "coupon_after": None,
            "ingot_before": None,
            "ingot_after": None,
            "status": status,
            "evidence": str(evidence),
        }
        record.update(fields)
        self.state.set_market_offer(account.name, key, record)
        return evidence

    def market_preconfirm_is_uncharged(
        self,
        frame: Image.Image,
        slot: tuple[int, int, int, int],
        quote: dict[str, object],
    ) -> tuple[bool, str]:
        """Prove that a missing purchase dialog did not charge the account.

        Clicking a card's ``购买`` button is reversible: it should only open
        the coupon confirmation dialog.  Older builds could swallow that
        first click while the Flash card was repainting, leaving the action in
        ``pending`` forever.  A bounded retry is safe only when the same card
        and the same quote are still visible, the balances are unchanged, and
        the confirmation button is positively absent.  Anything else remains
        an unknown outcome and is never retried automatically.
        """
        confirm_roi = (800, 540, 950, 620)
        if self.template("market_purchase_confirm_coupon.bmp", confirm_roi, frame, threshold=0.60)[0]:
            return False, "purchase confirmation is visible but was not positively settled"
        if not self.template("market_anchor.bmp", (651, 69, 1251, 319), frame, threshold=0.90)[0]:
            return False, "black-market page is no longer visible"
        x1, y1, x2, _y2 = slot
        if not self.template("market_stamina_potion.bmp", (x1, y1, x2, y1 + 85), frame, threshold=0.90)[0]:
            return False, "the quoted stamina potion card is no longer visible"
        if not self.template("market_buy_button.bmp", slot, frame, threshold=0.65)[0]:
            return False, "the quoted purchase button is no longer visible"
        try:
            expected_price = int(quote["price"])
            expected_coupon = int(quote["coupon_before"])
            expected_ingot = int(quote["ingot_before"])
            current_price = self.market_price(frame, slot)
            current_coupon = self.market_balance(frame, True)
            current_ingot = self.market_balance(frame, False)
        except (KeyError, TypeError, ValueError, PendingError) as exc:
            return False, f"market quote could not be re-read: {exc}"
        if current_price != expected_price:
            return False, f"market price changed from {expected_price} to {current_price}"
        if current_coupon != expected_coupon or current_ingot != expected_ingot:
            return False, "coupon or ingot balance changed after the click"
        return True, "confirmation dialog absent and coupon/ingot balances unchanged"

    def process_market_slot(self, account: Account, cycle: str, slot_index: int, slot: tuple[int, int, int, int], frame: Image.Image) -> None:
        action = self.market_offer_key(cycle, slot_index)
        status = self.state.action_status(account.name, action)
        x1, y1, x2, y2 = slot
        pending_record = self.state.action_record(account.name, action) if status == "pending" else {}
        pending_detail = str(pending_record.get("detail", ""))
        known_no_click_pending = pending_detail.startswith("coupon insufficient for ")
        pending_quote: dict[str, object] | None = None
        if status == "pending" and pending_detail.startswith("{"):
            try:
                decoded = json.loads(pending_detail)
            except json.JSONDecodeError:
                decoded = None
            if isinstance(decoded, dict) and decoded.get("phase") == "awaiting_confirm":
                pending_quote = decoded
        # A pending action with an unknown outcome is never resolved merely
        # because the refreshed card now looks sold out or no longer contains
        # the potion.  Either state can be caused by a prior click completing
        # outside our observation window, so keep the action isolated and
        # refuse a second charge.  The only additional resumable case is the
        # explicitly recorded pre-confirmation phase, where a bounded
        # unchanged-balance probe proves that the first reversible click was
        # swallowed before a charge could occur.
        if status == "pending" and pending_quote is None and not known_no_click_pending:
            if self.template("market_sold_out.bmp", slot, frame)[0]:
                self.record_market_offer(
                    account,
                    cycle,
                    slot_index,
                    "pending_unknown_sold_out",
                    frame,
                    item="体力药水（小）",
                    currency="coupon",
                )
            elif not self.template("market_stamina_potion.bmp", (x1, y1, x2, y1 + 85), frame)[0]:
                self.record_market_offer(
                    account,
                    cycle,
                    slot_index,
                    "pending_unknown_offer_missing",
                    frame,
                    item="体力药水（小）",
                    currency="coupon",
                )
            raise PendingError(f"market offer {action} has unknown prior outcome; refusing duplicate")
        dialog_retry_count = 0
        if pending_quote is not None:
            self.market_purchase_prerequisites()
            try:
                dialog_retry_count = int(pending_quote.get("dialog_retry_count", 0))
            except (TypeError, ValueError):
                raise PendingError(f"market offer {action} has an invalid confirmation retry ledger")
            if dialog_retry_count >= 2:
                raise PendingError(f"market offer {action} confirmation did not appear after bounded retries; manual review required")
            unchanged, reason = self.market_preconfirm_is_uncharged(frame, slot, pending_quote)
            if not unchanged:
                raise PendingError(f"market offer {action} confirmation outcome unknown; {reason}")
            self.event(
                "market_buy_confirmation_retry",
                account,
                cycle=cycle,
                slot=slot_index,
                retry=dialog_retry_count + 1,
            )
        if status == "done":
            self.event("market_offer_skip_done", account, cycle=cycle, slot=slot_index)
            return
        if self.template("market_sold_out.bmp", slot, frame)[0]:
            self.state.set_action(account.name, action, "done", "sold_out_before_purchase")
            self.record_market_offer(account, cycle, slot_index, "sold_out", frame, item="unknown", currency="unknown")
            self.event("market_offer_sold_out", account, cycle=cycle, slot=slot_index)
            return
        potion = self.template("market_stamina_potion.bmp", (x1, y1, x2, y1 + 85), frame)[0]
        if not potion:
            self.state.set_action(account.name, action, "done", "no_stamina_potion_offer")
            self.record_market_offer(account, cycle, slot_index, "skipped_no_stamina_potion", frame, item="not_stamina_potion", currency="unknown")
            self.event("market_offer_skip_no_item", account, cycle=cycle, slot=slot_index)
            return
        self.market_purchase_prerequisites()
        if known_no_click_pending:
            self.event("market_offer_recheck_known_insufficient", account, cycle=cycle, slot=slot_index)
        if not self.template("market_coupon_icon.bmp", (x1, y1, x2, y2), frame)[0]:
            self.state.set_action(account.name, action, "done", "skipped_stamina_potion_without_coupon_price")
            self.record_market_offer(account, cycle, slot_index, "skipped_non_coupon", frame, item="体力药水（小）", currency="non_coupon")
            self.event("market_offer_skip_non_coupon", account, cycle=cycle, slot=slot_index)
            return
        price = self.market_price(frame, slot)
        coupon_before = self.market_balance(frame, True)
        ingot_before = self.market_balance(frame, False)
        if coupon_before < price:
            detail = f"coupon insufficient for {action}: price={price} coupon={coupon_before}"
            self.state.set_action(account.name, action, "pending", detail)
            self.record_market_offer(account, cycle, slot_index, "pending_insufficient_coupon", frame, item="体力药水（小）", currency="coupon", price=price, coupon_before=coupon_before, ingot_before=ingot_before)
            self.event("market_offer_skip_insufficient_coupon", account, cycle=cycle, slot=slot_index, price=price, coupon=coupon_before)
            raise PendingError(detail)
        quote_evidence = self.record_market_offer(account, cycle, slot_index, "pending", frame, item="体力药水（小）", currency="coupon", price=price, coupon_before=coupon_before, ingot_before=ingot_before)
        self.state.set_action(
            account.name,
            action,
            "pending",
            json.dumps(
                {
                    "phase": "awaiting_confirm",
                    "cycle": cycle,
                    "slot": slot_index,
                    "item": "体力药水（小）",
                    "price": price,
                    "currency": "coupon",
                    "coupon_before": coupon_before,
                    "ingot_before": ingot_before,
                    "dialog_retry_count": dialog_retry_count,
                },
                ensure_ascii=False,
            ),
        )
        # The Flash confirmation button has a pulsing highlight and the whole
        # modal is dimmed by a translucent overlay.  Keep the match bounded to
        # the confirmation-modal ROI but allow the enrolled live button to
        # vary within that animation range.  The coupon-only balance delta
        # below remains the final settlement gate.
        confirm_threshold = 0.78
        remaining_attempts = 2 - dialog_retry_count
        confirmation_seen = False
        for attempt_index in range(remaining_attempts):
            if not self.click_template(
                account,
                "market_buy_button.bmp",
                (x1, y1 + 65, x2, y2),
                52,
                15,
                action,
                ["market_anchor.bmp", "market_stamina_potion.bmp"],
                irreversible=False,
            ):
                raise PendingError(f"market buy button missing in slot {slot_index}")
            if self.wait_template("market_purchase_confirm_coupon.bmp", (551, 319, 1301, 869), 5, threshold=confirm_threshold):
                confirmation_seen = True
                break
            current = self.capture()
            quote = {
                "price": price,
                "coupon_before": coupon_before,
                "ingot_before": ingot_before,
            }
            unchanged, reason = self.market_preconfirm_is_uncharged(current, slot, quote)
            if not unchanged:
                self.event("market_buy_confirmation_missing", account, cycle=cycle, slot=slot_index, detail=reason)
                raise PendingError(f"coupon purchase confirmation outcome unknown; {reason}")
            dialog_retry_count += 1
            self.state.set_action(
                account.name,
                action,
                "pending",
                json.dumps(
                    {
                        "phase": "awaiting_confirm",
                        "cycle": cycle,
                        "slot": slot_index,
                        "item": "体力药水（小）",
                        "price": price,
                        "currency": "coupon",
                        "coupon_before": coupon_before,
                        "ingot_before": ingot_before,
                        "dialog_retry_count": dialog_retry_count,
                    },
                    ensure_ascii=False,
                ),
            )
            self.record_market_offer(
                account,
                cycle,
                slot_index,
                "pending_confirmation_missing",
                current,
                item="体力药水（小）",
                currency="coupon",
                price=price,
                coupon_before=coupon_before,
                ingot_before=ingot_before,
                dialog_retry_count=dialog_retry_count,
            )
            self.event("market_buy_confirmation_missing", account, cycle=cycle, slot=slot_index, retry=dialog_retry_count, detail="已确认余额未变化，允许有限重试")
            if attempt_index + 1 < remaining_attempts:
                self.wait_interruptible(0.4)
        if not confirmation_seen:
            raise PendingError("coupon purchase confirmation did not appear; balance unchanged and bounded retries exhausted")
        self.state.set_action(
            account.name,
            action,
            "pending",
            json.dumps(
                {
                    "phase": "awaiting_settlement",
                    "cycle": cycle,
                    "slot": slot_index,
                    "item": "体力药水（小）",
                    "price": price,
                    "currency": "coupon",
                    "coupon_before": coupon_before,
                    "ingot_before": ingot_before,
                    "dialog_retry_count": dialog_retry_count,
                },
                ensure_ascii=False,
            ),
        )
        if not self.click_template(
            account,
            "market_purchase_confirm_coupon.bmp",
            (551, 319, 1301, 869),
            50,
            16,
            f"{action}_confirm",
            ["market_purchase_confirm_coupon.bmp"],
            threshold=confirm_threshold,
        ):
            raise PendingError("coupon purchase confirmation disappeared")
        deadline = time.monotonic() + 15
        while time.monotonic() < deadline:
            self.check_stop()
            current = self.capture()
            if self.template("market_sold_out.bmp", slot, current)[0]:
                coupon_after = self.market_balance(current, True)
                ingot_after = self.market_balance(current, False)
                if coupon_after == coupon_before - price and ingot_after == ingot_before:
                    after_evidence = self.save_evidence(current, account, f"market_{cycle}_slot{slot_index}_purchased", "after")
                    self.state.set_market_offer(
                        account.name,
                        action,
                        {
                            "date": date.today().isoformat(),
                            "cycle": cycle,
                            "slot": slot_index,
                            "item": "体力药水（小）",
                            "currency": "coupon",
                            "price": price,
                            "coupon_before": coupon_before,
                            "coupon_after": coupon_after,
                            "ingot_before": ingot_before,
                            "ingot_after": ingot_after,
                            "status": "purchased",
                            "evidence": str(after_evidence),
                            "quote_evidence": str(quote_evidence),
                        },
                    )
                    self.state.set_action(
                        account.name,
                        action,
                        "done",
                        json.dumps({"cycle": cycle, "slot": slot_index, "item": "体力药水（小）", "price": price, "currency": "coupon", "coupon_before": coupon_before, "coupon_after": coupon_after, "ingot_before": ingot_before, "ingot_after": ingot_after, "result": "purchased", "evidence": str(after_evidence)}, ensure_ascii=False),
                    )
                    self.event("market_offer_purchased", account, cycle=cycle, slot=slot_index, price=price, coupon_before=coupon_before, coupon_after=coupon_after, ingot_before=ingot_before, ingot_after=ingot_after)
                    return
                raise PendingError("market purchase result appeared but coupon/ingot delta was not verified")
            self.wait_interruptible(0.3)
        raise PendingError("market purchase result not verified; pending preserved")

    def run_market_read_only(self, account: Account, purchase: bool = False, cycle: str | None = None) -> None:
        # Long-lived all-day recovery can intentionally process an earlier
        # refresh after the wall clock has advanced.  Keep the selected cycle
        # explicit so quote/action keys and settlement checks cannot drift to
        # the later current clock batch.
        cycle = cycle or self.current_market_cycle()
        module = f"market_{cycle}"
        if cycle == "PRE08":
            raise PendingError("no active market refresh batch before 08:00")
        if self.state.module_status(account.name, module) == "done":
            # A read-only probe is useful evidence but must never suppress a
            # later verified purchase in the same refresh batch.  Only a
            # completed coupon-only settlement is idempotently skippable.
            detail = str(self.state.accounts[account.name].get("modules", {}).get(module, {}).get("detail", ""))
            if not purchase or detail == "coupon-only offers settled":
                return
        self.switch_account(account)
        self.prepare_main(account)
        frame = self.capture()
        if not self.template("market_anchor.bmp", (651, 69, 1251, 319), frame)[0]:
            # If the player is already in South Konoha and the transient
            # notification has expired, the independently enrolled NPC label
            # is sufficient to open the market without another map round-trip.
            self.ensure_assets(["market_npc_label.bmp"])
            # The NPC can render over the right edge of the South Konoha map
            # (including beside the minimap).  Keep the search inside the game
            # viewport but include that edge; the exact label plus the later
            # market-anchor wait are the two positive gates.
            # Keep the fallback NPC search away from the lower-right
            # notification card, whose ``黑市商人`` title can otherwise look
            # like the map NPC label and redirect the click into the card.
            npc_roi = MARKET_NPC_ROI
            npc_ok, _npc_score, npc_x, npc_y = self.template("market_npc_label.bmp", npc_roi, frame, threshold=0.60)
            if npc_ok:
                # The label sits above the NPC's clickable hitbox.  The
                # historical fallback used a point roughly (28,55) below the
                # label; a lower (40,75) point is often occupied by another
                # player and silently leaves the page unchanged.
                self.click_point(account, npc_x + 28, npc_y + 55, "black_market_npc_entry", ["market_npc_label.bmp"], anchor_threshold=0.60)
                if not self.wait_template("market_anchor.bmp", (651, 69, 1251, 319), 90):
                    raise PendingError("black-market page timeout after NPC entry")
                frame = self.capture()
            elif not self.template("leaf_map_south.bmp", (851, 519, 1101, 819), frame)[0]:
                if not self.click_template(account, "world_map_button.bmp", (1769, 69, 1839, 169), 7, 7, "market_world_map", ["anchor_top_menu.bmp"]):
                    raise PendingError("world-map button missing before black-market check")
                if not self.wait_template("world_map_training_field.bmp", (751, 719, 1051, 1031), 8):
                    raise PendingError("world map timeout")
                if not self.click_template(account, "world_map_leaf_village.bmp", (601, 669, 851, 919), 28, 13, "market_leaf_village", ["world_map_training_field.bmp"]):
                    raise PendingError("leaf-village map entry missing")
                if not self.wait_template("leaf_map_south.bmp", (851, 519, 1101, 819), 8):
                    raise PendingError("leaf-village submap timeout")
            if not npc_ok:
                if not self.click_template(account, "leaf_map_south.bmp", (851, 519, 1101, 819), 14, 37, "market_leaf_village_south", ["leaf_map_south.bmp"]):
                    raise PendingError("leaf-village south entry missing")
                if not self.wait_until_main(90):
                    raise PendingError("south Konoha load timeout")
                frame = self.capture()
                entry_ok, _score, card_x, card_y = self.template("market_entry.bmp", VIEW, frame)
                if entry_ok:
                    view_ok, _view_score, view_x, view_y = self.template("market_entry_view.bmp", (card_x + 55, card_y + 110, card_x + 155, card_y + 160), frame)
                    if not view_ok:
                        raise SafetyError("black-market card found but its View button was not verified in-card")
                    self.click_point(account, view_x + 36, view_y + 14, "black_market_entry_view", ["market_entry.bmp"])
                else:
                    # The notification is transient.  When it has already
                    # been dismissed, wait for the independently enrolled NPC
                    # label, then click only after the exact label is present.
                    self.wait_template("market_npc_label.bmp", npc_roi, 8, threshold=0.60)
                    frame = self.capture()
                    npc_ok, _npc_score, npc_x, npc_y = self.template("market_npc_label.bmp", npc_roi, frame, threshold=0.60)
                    if not npc_ok:
                        raise PendingError("black-market notification and NPC entry are both unavailable")
                    self.click_point(account, npc_x + 28, npc_y + 55, "black_market_npc_entry", ["market_npc_label.bmp"], anchor_threshold=0.60)
                if not self.wait_template("market_anchor.bmp", (651, 69, 1251, 319), 90):
                    raise PendingError("black-market page timeout")
                frame = self.capture()
        if purchase:
            # Fail closed before iterating slots. Without this early preflight
            # a refreshed page containing no potion could be incorrectly
            # recorded as a completed purchase cycle while safety templates
            # were still missing.
            self.market_purchase_prerequisites()
        # Keep a lossless copy of the settled modal before any slot processing
        # or close click. JPEG evidence remains useful for the audit trail, but
        # PNG is required when enrolling small price/balance glyph templates.
        raw_evidence = self.save_raw_evidence(frame, account, f"market_{cycle}", "raw")
        matches = {}
        for name in ("market_entry.bmp", "market_entry_view.bmp", "market_anchor.bmp", "market_stamina_potion_any.bmp", "market_coupon_icon.bmp"):
            ok, score, x, y = self.template(name, VIEW, frame)
            matches[name] = {"ok": ok, "score": score, "x": x, "y": y}
        if purchase:
            for slot_index, slot in enumerate(self.market_slot_rois(), 1):
                self.process_market_slot(account, cycle, slot_index, slot, frame)
                frame = self.capture()
        evidence = self.save_evidence(frame, account, f"market_{cycle}", "purchase" if purchase else "readonly")
        if self.template("market_anchor.bmp", (651, 69, 1251, 319), frame)[0]:
            if not self.click_template(account, "market_close.bmp", (1301, 219, 1919, 369), 15, 15, "market_close_readonly", ["market_anchor.bmp"]):
                raise PendingError("black-market close control missing")
            if not self.wait_until_main(8):
                raise PendingError("black-market page did not close")
        self.state.set_module(account.name, module, "done", "coupon-only offers settled" if purchase else "read-only initial screen")
        self.event("market_purchase_cycle_done" if purchase else "market_read_only", account, cycle=cycle, matches=matches, evidence=str(evidence), raw_evidence=str(raw_evidence), purchase_clicks=self.content_click_count if purchase else 0)

    def market_cycle_settled(self, account: Account, cycle: str) -> bool:
        """Require a settled module and no unresolved slot action for a cycle."""
        module = f"market_{cycle}"
        if self.state.module_status(account.name, module) != "done":
            return False
        detail = str(self.state.accounts.get(account.name, {}).get("modules", {}).get(module, {}).get("detail", "")) if isinstance(getattr(self.state, "accounts", None), dict) else ""
        if detail and detail != "coupon-only offers settled":
            return False
        prefix = f"market_{date.today():%Y%m%d}_{cycle}_slot"
        account_state = self.state.accounts.get(account.name, {}) if isinstance(getattr(self.state, "accounts", None), dict) else {}
        actions = account_state.get("actions", {}) if isinstance(account_state, dict) else {}
        return not any(
            str(key).startswith(prefix)
            and isinstance(value, dict)
            and str(value.get("status", "")) == "pending"
            for key, value in actions.items()
        )

    def managed_active_score(self, frame: Image.Image | None = None) -> float:
        """Return an edge-only score for the fixed ``托管中`` toolbar badge.

        A grayscale NCC against ``managed_active.bmp`` is overly dependent on
        the map pixels behind the badge.  The badge glyphs themselves are
        stable, so compare their edge masks in a small fixed search window.
        """
        path = ASSETS / "managed_active.bmp"
        if not path.exists():
            return 0.0
        frame = frame or self.capture()
        with Image.open(path) as reference_image:
            reference = reference_image.convert("L")
        ref_edge = edge_binary(reference)
        rh, rw = ref_edge.shape
        # The asset was enrolled from the game at its fixed 1920x1032 origin.
        # Search a few pixels around that origin to tolerate Flash antialiasing.
        best = 0.0
        for dx in range(-4, 5):
            for dy in range(-3, 4):
                x1, y1 = 895 + dx, 145 + dy
                crop = frame.crop((x1, y1, x1 + rw, y1 + rh))
                got_edge = edge_binary(crop)
                ref_count = int(ref_edge.sum())
                got_count = int(got_edge.sum())
                if not ref_count or not got_count:
                    continue
                intersection = int((ref_edge & got_edge).sum())
                score = 2.0 * intersection / (ref_count + got_count)
                best = max(best, score)
        return best

    def run_managed_gate(self, account: Account) -> None:
        self.switch_account(account)
        if not (ASSETS / "managed_active.bmp").exists() or not (ASSETS / "managed_exit.bmp").exists():
            raise PendingError("managed_active.bmp and managed_exit.bmp are required")
        # The text is fixed in the top toolbar, but its map background changes
        # between accounts.  Match glyph edges (not the yellow map texture)
        # and retain the bitmap as an existence/safety asset requirement.
        frame = self.capture()
        stable_samples = 0
        seen_active = False
        for sample in range(10):
            if self.managed_active_score(frame) >= 0.70:
                stable_samples += 1
                seen_active = True
            if sample < 9:
                self.wait_interruptible(0.3)
                frame = self.capture()
        if stable_samples == 10:
            if self.state.action_status(account.name, "start_managed") != "done":
                self.commit_action(account, "start_managed", "managed active was positively reconciled across ten consecutive samples")
            self.state.set_module(account.name, "managed", "done")
            return
        if seen_active:
            raise PendingError("managed active recognition was not stable for ten consecutive samples; no start click made")
        if self.config.get("ManagedSafetyVerified", "0") != "1":
            raise PendingError("managed state not active and ManagedSafetyVerified is not enabled; no managed click made")
        unresolved = [key for key, value in self.state.accounts[account.name].get('actions', {}).items()
                      if isinstance(value, dict) and value.get('status') == 'pending'
                      and key != 'start_managed' and not key.startswith('start_managed_network_')]
        if unresolved:
            raise PendingError('原操作结果尚未核实，不能开始托管：'+', '.join(unresolved))
        self.ensure_assets(["managed_mode_button.bmp", "managed_start_button.bmp"])
        self.prepare_main(account)
        self.open_managed_panel_with_retry(account)
        from hyol_managed import preserve_existing_options
        preserve_existing_options(self, account)
        from hyol_network_retry import run as retry_network
        hits = 0
        def active_verified():
            nonlocal hits
            hits = hits + 1 if self.managed_active_score(self.capture()) >= .70 else 0
            return hits >= 10
        retry_network(self, account, 'start_managed', '开始托管', active_verified,
                      lambda: self.managed_active_score(self.capture()) < .70
                      and self.template('managed_start_button.bmp', (651,619,1051,919))[0],
                      lambda label: self.click_template(account, 'managed_start_button.bmp', (651,619,1051,919),
                                                        50,12,label,['managed_start_button.bmp'],irreversible=True))
        self.state.set_module(account.name, 'managed', 'done')

    def open_managed_panel_with_retry(self, account: Account) -> None:
        from hyol_network_retry import run as retry_network
        retry_network(self, account, 'managed_panel', '打开托管面板',
                      lambda: self.template('managed_start_button.bmp', (651,619,1051,919))[0],
                      lambda: self.main_ready() and self.template('managed_mode_button.bmp', (351,109,561,239))[0],
                      lambda label: self.click_template(account, 'managed_mode_button.bmp', (351,109,561,239),
                                                        12,11,label,['anchor_top_menu.bmp']))

    def exit_managed_for_market(self, account: Account) -> None:
        """Leave managed mode only through its verified exit control."""
        self.ensure_assets(["managed_active.bmp", "managed_exit.bmp"])
        self.switch_account(account)
        frame = self.capture()
        active = self.managed_active_score(frame) >= 0.70
        panel_roi = (750,190,1170,290)
        close_roi = (1320,230,1450,330)
        panel = (self.template('managed_settings_title_current.bmp', panel_roi, frame, threshold=.98)[0]
                 and self.template('managed_settings_close_current.bmp', close_roi, frame, threshold=.98)[0])
        if not active and not panel:
            return
        record = self.state.accounts[account.name].setdefault('managed_exit', {})
        record.update(status='pending', started_at=record.get('started_at', now_iso()))
        self.state.save_account(account.name)
        # ``退出托管`` is only rendered inside the托管 settings panel.  Open
        # that panel through its stable toolbar icon before looking for the
        # destructive exit control; searching the full page can otherwise
        # produce a false negative and leave the caller stuck in managed mode.
        self.ensure_assets(["managed_mode_button.bmp"])
        if not panel and not self.click_template(
            account,
            "managed_mode_button.bmp",
            (351, 109, 561, 239),
            12,
            11,
            "managed_panel_for_exit",
            ["anchor_top_menu.bmp"],
        ):
            raise PendingError("managed toolbar missing while exiting managed mode")
        exit_roi = (651, 619, 1251, 919)
        if active and not self.wait_template("managed_exit.bmp", exit_roi, 6):
            raise PendingError("managed exit control did not appear")
        if active and record.get('phase') != 'exit_requested':
            record.update(phase='exit_requested', requested_at=now_iso())
            self.state.save_account(account.name)
            if not self.click_template(account, "managed_exit.bmp", exit_roi, 20, 12,
                    "exit_managed_for_market", ["anchor_top_menu.bmp"]):
                raise PendingError("managed exit control missing")
        # The current client leaves settings open after stopping. A restart
        # observes the original exit; it never repeats an unknown exit click.
        stopped = 0
        deadline = time.monotonic() + 12
        from hyol_managed import stopped_panel_visible
        while time.monotonic() < deadline:
            frame = self.capture()
            known = stopped_panel_visible(self, frame) or (
                self.main_ready(frame) and self.verify_identity(account, frame)[0])
            stopped = stopped + 1 if known and self.managed_active_score(frame) < .70 else 0
            if stopped == 3:
                break
            self.wait_interruptible(.35)
        if stopped != 3:
            raise PendingError('退出托管尚未三帧确认，保留原退出动作')
        record.update(phase='stopped_verified')
        self.state.save_account(account.name)
        frame = self.capture()
        if self.template('managed_settings_title_current.bmp', panel_roi, frame, threshold=.98)[0]:
            if not self.click_template(account, 'managed_settings_close_current.bmp', close_roi,
                    18,18,'managed_exit_close_settings',['managed_settings_title_current.bmp'],
                    threshold=.98, anchor_threshold=.98):
                raise PendingError('停止托管后设置页X未确认')
            if not self.wait_absent('managed_settings_title_current.bmp', panel_roi, 5, threshold=.98):
                raise PendingError('停止托管后设置页尚未关闭，不重复退出')
        if not self.wait_until_main(12):
            raise PendingError("managed mode did not close through verified exit")
        record.update(status='done', phase='main_verified', finished_at=now_iso())
        self.state.save_account(account.name)
        self.event("managed_exited_for_market", account)

    def _run_full_order(self, include_recognize: bool = True) -> None:
        """Run the resumable full-flow order shared by CLI and the panel.

        The order is deliberately kept in one method.  A previous CLI path
        assumed that teams had already completed and skipped mail/welfare,
        which made ``--stage full`` behave differently from the panel's Full
        button.  Every boundary below is a completion gate, so a pending or
        quarantined account stops the flow without advancing later modules.
        """
        if include_recognize and not self.state.global_state.get("recognize_completed_at"):
            self.recognize_two_passes()
        if self.check_pause_boundary():
            raise PendingError("full flow paused before market")

        cycle = self.current_market_cycle()
        if cycle == "PRE08":
            self.event("market_wait", cycle=cycle, reason="full_flow_before_first_refresh")
            raise PendingError("full flow market batch is not open before 08:00")
        # Keep completion tied to the batch that this full run actually
        # settled.  If the wall clock crosses 11:00 while managed warm-up or
        # combat is still running, the run must not be retroactively judged
        # against a newer market batch.
        self._full_market_cycle = cycle
        self.run_market_all()
        if not self.command_is_complete(SchedulerCommand("market")):
            raise PendingError("full flow halted after unresolved market module")

        if self.check_pause_boundary():
            raise PendingError("full flow paused before managed warm-up")
        self.run_managed_then_main()
        if not self._accounts_module_complete("managed"):
            raise PendingError("full flow halted after unresolved managed module")

        if self.check_pause_boundary():
            raise PendingError("full flow paused before mail")
        self.run_accounts_module("mail", self.module_mail)
        if not self._accounts_module_complete("mail"):
            raise PendingError("full flow halted after unresolved mail module")

        if self.check_pause_boundary():
            raise PendingError("full flow paused before welfare")
        self.run_accounts_module("welfare", self.module_welfare)
        if not self._accounts_module_complete("welfare"):
            raise PendingError("full flow halted after unresolved welfare module")

        for team_index in (1, 2):
            if self.check_pause_boundary():
                raise PendingError(f"full flow paused before Team{team_index}")
            self.run_team_selection(team_index)
            if not self._team_complete(team_index):
                raise PendingError(f"full flow halted after unresolved Team{team_index}")

        if self.check_pause_boundary():
            raise PendingError("full flow paused before arena")
        self.run_combat_interleaved("arena")
        if not self._accounts_module_complete("arena"):
            raise PendingError("full flow halted after unresolved arena module")

        if self.check_pause_boundary():
            raise PendingError("full flow paused before survival")
        self.run_combat_interleaved("survival")
        if not self._accounts_module_complete("survival"):
            raise PendingError("full flow halted after unresolved survival module")

    def run_full(self) -> None:
        """Run the same full-flow order for CLI ``full`` and ``auto`` stages."""
        self._run_full_order(include_recognize=True)
        if not self.command_is_complete(SchedulerCommand("full")):
            detail = "full stage target scope is not complete"
            self.state.add_pending("global", "full", detail)
            raise PendingError(detail)
        self.state.global_state["stage"] = "all_day"
        self.state.save_global()
        self.state.resolve_pending("global", "full")
        self.event("stage_complete", stage="full")

    def run_all_day_once(self) -> bool:
        if not self.allow_purchase or not self.market_direct_buy_verified:
            raise PendingError("all-day requires verified coupon-only market purchase configuration")
        latest_cycle = self.current_market_cycle()
        if latest_cycle == "PRE08":
            self.event("all_day_wait", cycle=latest_cycle, reason="before_first_refresh")
            return False
        cycle_state = self.state.global_state.setdefault("all_day_cycles", {})
        config = getattr(self, "config", {})
        configured_cycles = [value.replace(":", "") for value in parse_csv(config.get("MarketRefreshTimes", "08:00,11:00,14:00,17:00,20:00,23:00"))]
        try:
            latest_index = configured_cycles.index(latest_cycle)
        except ValueError:
            latest_index = len(configured_cycles) - 1
        # If an account was pending during an earlier refresh, catch it up
        # before touching a later batch.  This prevents a slow recovery around
        # 08:00 from silently skipping that batch once the clock reaches 11:00.
        due_cycles = configured_cycles[: latest_index + 1]
        cycle = next(
            (candidate for candidate in due_cycles if not (isinstance(cycle_state.get(candidate), dict) and cycle_state[candidate].get("status") == "done")),
            latest_cycle,
        )
        self._all_day_cycle = cycle
        if cycle != latest_cycle:
            self.event("all_day_catch_up", cycle=cycle, latest_cycle=latest_cycle)
        if isinstance(cycle_state.get(cycle), dict) and cycle_state[cycle].get("status") == "done":
            complete = cycle == "2300"
            self.event("all_day_cycle_skip_done", cycle=cycle, settled=True)
            self.state.resolve_pending("global", f"all_day_{cycle}")
            if complete and not self.state.global_state.get("all_day_completed_at"):
                self.state.global_state["all_day_completed_at"] = now_iso()
                self.state.save_global()
            return complete
        account_failures = 0
        account_success: dict[str, bool] = {}
        for account in self.accounts:
            try:
                self.exit_managed_for_market(account)
                self.run_market_read_only(account, purchase=self.allow_purchase, cycle=cycle)
                self.run_managed_gate(account)
                self.state.resolve_pending(account.name, f"all_day_{cycle}")
                account_success[account.name] = True
            except (PendingError, SafetyError) as exc:
                account_failures += 1
                account_success[account.name] = False
                self.state.add_pending(account.name, f"all_day_{cycle}", str(exc))
                self.event("all_day_account_pending", account, cycle=cycle, detail=str(exc))
                # A failed market/exit path may leave a map, modal, or
                # settings panel open.  Do not release the account lease by
                # simply switching to the next tab.  Attempt only enrolled,
                # reversible X/toggle recovery; if the main-page gate cannot
                # be proved, quarantine the run and stop before any further
                # tab switch.
                if self.active == account and not self.ensure_account_main_before_release(account, f"all_day_{cycle}"):
                    detail = f"{account.name} all-day failure did not return through a verified main page"
                    self.state.add_pending(account.name, f"all_day_{cycle}", detail)
                    self.event("all_day_account_quarantined", account, cycle=cycle, detail=detail, action="no_further_tab_switch")
                    raise PendingError(detail)
            if self.check_pause_boundary():
                self.event("all_day_paused", cycle=cycle, after_account=account.name)
                return False
        settled = all(account_success.get(account.name, False) for account in self.accounts) and all(
            self.market_cycle_settled(account, cycle) for account in self.accounts
        ) and all(
            self.state.module_status(account.name, "managed") == "done" for account in self.accounts
        )
        if settled:
            cycle_state[cycle] = {"status": "done", "updated_at": now_iso()}
            self.state.save_global()
            self.state.resolve_pending("global", f"all_day_{cycle}")
        self.event("all_day_cycle", cycle=cycle, settled=settled, failures=account_failures)
        if cycle == "2300" and settled:
            self.state.global_state["all_day_completed_at"] = now_iso()
            self.state.save_global()
            return True
        return False

    def run_all_day(self) -> None:
        if self.state.global_state.get("stage") != "all_day":
            self.state.global_state["stage"] = "all_day"
            self.state.save_global()
        interval = max(5, int(self.config.get("AllDayPollSeconds", "60")))
        while True:
            self.check_stop()
            if self.run_all_day_once():
                self.state.resolve_pending("global", "all_day")
                self.event("all_day_complete", reason="2300_cycle_and_all_accounts_managed")
                return
            if self.pause_after_module.is_set():
                raise PendingError("all-day paused after account cycle")
            self.wait_interruptible(interval)

    def run_market_all(self) -> None:
        purchase = self.allow_purchase and self.market_direct_buy_verified
        if not purchase:
            detail = "market purchase is disabled until coupon-only live verification completes"
            self.state.add_pending("global", "market", detail)
            self.event("market_pending", detail=detail, action="read_only_navigation", content_clicks=0)
        for account in self.accounts:
            if self.check_pause_boundary():
                return
            try:
                # Even when purchase is gated, navigate and capture a
                # read-only market screen so the button has an observable,
                # auditable effect instead of silently returning.
                self.run_market_read_only(account, purchase=purchase)
            except (PendingError, SafetyError) as exc:
                self.state.add_pending(account.name, "market", str(exc))
                self.event("market_pending", account, detail=str(exc))
                # A failed market selection may have left the shared window
                # on an unresolved page (for example an active battle or a
                # manual-login prompt).  Do not switch to the next tab after
                # that point; the command remains resumable and fail-closed.
                raise
        cycle = self.current_market_cycle()
        if cycle != "PRE08" and all(self.market_cycle_settled(account, cycle) for account in self.accounts):
            self.state.resolve_pending("global", "market")

    def run_managed_all(self) -> None:
        missing = [name for name in ("managed_active.bmp", "managed_exit.bmp") if not (ASSETS / name).exists()]
        if missing:
            detail = "missing managed safety assets: " + ",".join(missing)
            for account in self.accounts:
                self.state.set_module(account.name, "managed", "pending", detail)
                self.state.add_pending(account.name, "managed", detail)
                self.event("managed_pending", account, detail=detail, content_clicks=0)
            return
        for account in self.accounts:
            if self.check_pause_boundary():
                return
            try:
                self.run_managed_gate(account)
            except (PendingError, SafetyError) as exc:
                self.state.set_module(account.name, "managed", "pending", str(exc))
                self.state.add_pending(account.name, "managed", str(exc))
                self.event("managed_pending", account, detail=str(exc))
        if self._accounts_module_complete("managed"):
            self.state.resolve_pending("global", "managed")

    def run_managed_then_main(self) -> None:
        """Start managed mode, let it settle, then return every tab to main.

        The managed command itself remains useful as a standalone action and
        leaves accounts managed.  The full daily workflow uses this variant so
        black-market inspection is followed immediately by managed mode, then
        combat begins only after a bounded managed warm-up and verified exits.
        """
        self.run_managed_all()
        if not self._accounts_module_complete("managed"):
            unfinished = [account.name for account in self.accounts if self.state.module_status(account.name, "managed") != "done"]
            raise PendingError("managed warm-up unresolved: " + ", ".join(unfinished))
        seconds = max(0, int(self.config.get("ManagedWarmupSeconds", "20")))
        self.event("managed_warmup_start", seconds=seconds)
        if seconds:
            self.wait_interruptible(seconds)
        for account in self.accounts:
            if self.check_pause_boundary():
                raise PendingError("managed warm-up paused before verified main return")
            self.exit_managed_for_market(account)
            if self.active != account or not self.ensure_account_main_before_release(account, "managed_warmup"):
                raise LeaseBlocked(f"{account.name} managed warm-up did not return through a verified main page")
        self.event("managed_warmup_complete", seconds=seconds)

    def _team_complete(self, team_index: int) -> bool:
        team = next((item for item in self.teams if item.index == team_index), None)
        if team is None:
            return False
        leader_name = self.config.get(f"Team{team_index}VerifiedLeader", "")
        if leader_name not in team.members or leader_name not in self.by_name:
            return False
        leader = self.by_name[leader_name]
        active_specs = self._enabled_team_activity_specs()
        activities_done = all(
            self._team_activity_completion_verified(team, leader, spec)
            for spec in active_specs
        )
        if self._deferred_team_activity_specs():
            # A deliberately deferred activity keeps the verified team room
            # assembled for a later resumable run; it must not make the
            # already completed daily activities appear unfinished.
            return activities_done
        return activities_done and self.state.action_status(leader.name, "dissolve_team_after_activities") == "done"

    def _accounts_module_complete(self, module: str) -> bool:
        eligible = self.available_accounts()
        return bool(eligible) and all(self._account_module_complete(account, module) for account in eligible)

    def _account_module_complete(self, account: Account, module: str) -> bool:
        if self.state.module_status(account.name, module) != "done":
            return False
        if module == "arena":
            return self._arena_goal_complete(account) and self._arena_daily_rewards_complete(account)
        if module == 'survival':
            # The module is marked done only after stable 220/220, reward
            # claim confirmation, and the verified close/home return.
            actions = self.state.accounts.get(account.name, {}).get('actions', {})
            claimed = any(key.startswith('survival_claim_rewards')
                          and isinstance(value, dict)
                          and value.get('status') == 'done'
                          for key, value in actions.items())
            return claimed
        return module != "welfare" or self.welfare_claims_complete(account)

    def command_is_complete(self, command: SchedulerCommand) -> bool:
        """Return true only when the command's entire target scope is settled."""
        if command.kind == "recognize":
            return bool(self.state.global_state.get("recognize_completed_at"))
        if command.kind == "arena" and self.arena_eligible_accounts is not None:
            return bool(self.arena_eligible_accounts) and all(
                self._account_module_complete(self.by_name[name], "arena")
                for name in self.arena_eligible_accounts
            )
        if command.kind in ("mail", "welfare", "arena", "survival"):
            return self._accounts_module_complete(command.kind)
        if command.team_index is not None:
            if self.team_battle_targets is not None:
                return command.team_index in self.selected_team_commands_done
            return self._team_complete(command.team_index)
        if command.kind == "market":
            cycle = self.current_market_cycle()
            if cycle == "PRE08" or not (self.allow_purchase and self.market_direct_buy_verified):
                return False
            eligible = self.available_accounts()
            return bool(eligible) and all(self.market_cycle_settled(account, cycle) for account in eligible)
        if command.kind == "managed":
            eligible = self.available_accounts()
            return bool(eligible) and all(self.state.module_status(account.name, "managed") == "done" for account in eligible)
        if command.kind == "all_day":
            return bool(self.state.global_state.get("all_day_completed_at"))
        if command.kind == "full":
            cycle = self._full_market_cycle or self.current_market_cycle()
            eligible = self.available_accounts()
            independent = all(
                self._account_module_complete(account, module)
                for account in eligible
                for module in ("mail", "welfare", "arena", "survival")
            )
            market_done = bool(eligible) and self.allow_purchase and self.market_direct_buy_verified and cycle != "PRE08" and all(
                self.market_cycle_settled(account, cycle) for account in eligible
            )
            return bool(eligible) and independent and self._team_complete(1) and self._team_complete(2) and market_done and all(
                self.state.module_status(account.name, "managed") == "done" for account in eligible
            )
        return False

    def _finish_command(self, command: SchedulerCommand, result: int, status: str, detail: str = "") -> int:
        self.command_summary(command, status, result, detail)
        return result

    def bind_current_account_only(self, name: str, tab_x: int) -> None:
        """Verify the already visible account without probing or clicking tabs."""
        if name not in self.by_name:
            raise PendingError(f"unknown test account: {name}")
        account = self.by_name[name]
        frame = self.capture()
        ok, scores = self.verify_identity(account, frame)
        battle_identity = f"accounts/{slug(name)}_battle_identity.bmp"
        if not ok and (ASSETS / battle_identity).exists():
            ok = (
                self.template(battle_identity, (300, 250, 850, 650), frame, threshold=0.98)[0]
                and self._combat_active_visible("arena", frame)
            )
        if not ok or not self.healthy(frame):
            raise PendingError(f"visible account does not positively match {name}: {scores}")
        if Win32.hit_test(self.hwnd, *self.screen_point(tab_x, 10)) != 1:
            raise PendingError("selected current tab position is not client content")
        rebound = Account(account.index, name, tab_x)
        self.accounts = [rebound if item.name == name else item for item in self.accounts]
        self.by_name[name] = rebound
        self.current_account_only = name
        self.joint_identity_bound = {name}
        self.active = rebound
        self.event("single_account_test_bound", rebound, scores=scores, tab_clicks=0)

    def bind_visible_account_only(self) -> Account:
        """Bind whichever configured account is visibly on the current page.

        This is read-only: it never probes or clicks a tab.  A unique identity
        score and margin are required before any arena content action.
        """
        deadline = time.monotonic() + 8
        scores: dict[str, float] = {}
        ordered: list[tuple[str, float]] = []
        frame: Image.Image | None = None
        detected_name: str | None = None
        detection = ""
        while time.monotonic() < deadline:
            frame = self.capture()
            if self.template("offline_login_prompt.bmp", (780, 450, 1140, 660), frame, threshold=0.98)[0]:
                raise PendingError("current account is offline and requires manual login")
            scores = self.identity_scores(frame)
            ordered = sorted(scores.items(), key=lambda row: row[1], reverse=True)
            if ordered:
                second = ordered[1][1] if len(ordered) > 1 else -1.0
                margin = ordered[0][1] - second
                if ordered[0][1] >= self.identity_assignment_threshold and margin >= max(self.identity_margin, 0.08):
                    detected_name = ordered[0][0]
                    detection = "visible_current_page"
                    break
                relaxed_floor = max(0.72, self.identity_assignment_threshold - 0.08)
                if ordered[0][1] >= relaxed_floor and margin >= 0.12 and self.main_ready(frame):
                    detected_name = ordered[0][0]
                    detection = "visible_current_main_with_strong_margin"
                    break
            self.event("visible_account_identity_wait", scores=scores)
            self.wait_interruptible(0.4)
        if detected_name is None:
            raise PendingError(f"当前画面无法唯一识别账号: scores={scores}")
        name = detected_name
        configured = self.by_name[name]
        # The coordinate is retained only for audit/state display.  No tab
        # click is sent in this mode, and combat switching bypasses it.
        rebound = Account(configured.index, name, configured.tab_x)
        self.accounts = [rebound if item.name == name else item for item in self.accounts]
        self.by_name[name] = rebound
        self.current_account_only = name
        self.joint_identity_bound = {name}
        self.active = rebound
        self.event("single_account_test_bound", rebound, scores=scores, tab_clicks=0, detection=detection)
        return rebound

    def _generic_tab_measurements(self, frame: Image.Image) -> list[tuple[float, float, int]]:
        """Measure selected-tab paint without reading a title or account name."""
        if getattr(self, 'automatic_accounts', False):
            candidates = sorted(set(parse_int_csv(self.config.get('TabXs',''))))
        elif getattr(self, "stage", None) == "multi_solo":
            candidates = sorted({account.tab_x for account in self.accounts
                                 if 50 <= account.tab_x <= 1800})
        else:
            candidates = sorted(set(parse_int_csv(self.config.get("TabXs", "180,310,445,590,730,860"))))
        cache = getattr(frame, '_hyol_observations', None)
        key = ('tab_measurements', tuple(candidates))
        if cache is not None and key in cache:
            return cache[key]
        measurements: list[tuple[float, float, int]] = []
        for x in candidates:
            patch = np.asarray(frame.crop((x - 12, 12, x + 13, 28)).convert("RGB"), dtype=np.float64)
            luminance = patch.mean(axis=2)
            measurements.append((float((luminance >= 245).mean()), float(np.median(luminance)), x))
        if cache is not None:
            cache[key] = measurements
        return measurements

    def _generic_active_tab_x(self, frame: Image.Image) -> int:
        measurements = sorted(self._generic_tab_measurements(frame), reverse=True)
        if not measurements:
            raise PendingError("未找到可用的浏览器标签位置")
        best = measurements[0]
        second = measurements[1] if len(measurements) > 1 else (0.0, 0.0, -1)
        white_selected = best[0] >= 0.45 and best[0] - second[0] >= 0.25
        bright_selected = best[1] >= 235 and best[1] - second[1] >= 15
        if not (white_selected or bright_selected):
            raise PendingError("当前浏览器标签高亮无法唯一确认")
        return best[2]

    def _generic_current_tab_is_active(self, frame: Image.Image) -> bool:
        try:
            return self.generic_current_tab_x is not None and self._generic_active_tab_x(frame) == self.generic_current_tab_x
        except PendingError:
            return False

    def bind_generic_current_window(self, require_game_context: bool = True) -> Account:
        """Bind the visible tab without configured names or identity assets.

        Standalone task clients may bind before they normalize a known modal or
        task page.  In that case the selected browser-tab highlight is the
        lease, and every later content click still requires its page anchor.
        """
        frame = self.capture()
        if getattr(self,'automatic_accounts',False):
            from hyol_multi_solo import tab_close_positions, active_tab_centers
            markers = tab_close_positions(frame)
            self.wait_interruptible(.3)
            if markers != tab_close_positions(self.capture()):
                raise PendingError('当前窗口标签位置变化，未关联账号')
            self.config['TabXs'] = ','.join(map(str,active_tab_centers(markers)))
        if self.offline_dialog_visible(frame):
            tab_x = self._generic_active_tab_x(frame)
            candidates = sorted(set(parse_int_csv(self.config.get("TabXs", "180,310,445,590,730,860"))))
            slot_index = candidates.index(tab_x) + 1
            account = Account(slot_index, f"当前窗口标签{slot_index}", tab_x)
            evidence = self.save_raw_evidence(frame, account, 'offline_dialog_unbound_role', 'pending')
            self.event('account_disconnected_skipped', tab_slot=slot_index, evidence=str(evidence),
                       identity='unconfirmed', detail='中央掉线弹窗；只按标签跳过，不关联角色旧断点')
            raise AccountOffline(account, str(evidence))
        if require_game_context and not (self.main_ready(frame) or self._combat_ready_visible("arena", frame) or self._combat_active_visible("arena", frame)):
            raise PendingError("当前标签未确认游戏主界面、竞技场页面或进行中的战斗")
        tab_x = self._generic_active_tab_x(frame)
        candidates = sorted(set(parse_int_csv(self.config.get("TabXs", "180,310,445,590,730,860"))))
        slot_index = candidates.index(tab_x) + 1
        name = f"当前窗口标签{slot_index}"
        account = Account(slot_index, name, tab_x)
        self.accounts = [account]
        self.by_name = {name: account}
        self.teams = []
        # Anonymous tab numbers are not account identities across client restarts.
        # Keep resumable records inside this specific browser process lifetime.
        pid = Win32.pid(self.hwnd)
        handle = Win32.kernel32.OpenProcess(Win32.PROCESS_QUERY_LIMITED_INFORMATION, False, pid)
        if not handle:
            raise PendingError('无法核实浏览器会话，不能载入匿名标签断点')
        try:
            creation, end, kernel, user = (ctypes.c_ulonglong() for _ in range(4))
            if not Win32.kernel32.GetProcessTimes(handle, ctypes.byref(creation), ctypes.byref(end), ctypes.byref(kernel), ctypes.byref(user)):
                raise PendingError('无法读取浏览器会话起点，不能混用旧标签断点')
        finally:
            Win32.kernel32.CloseHandle(handle)
        directory = self.shared_state_directory / f'window_{pid}_{self.hwnd}_{creation.value:x}'
        self.state = StateStore([name], directory=directory,
                                restore_history=getattr(self, 'run_context', None) is None)
        self.account_logs = {name: JsonlLogger(LOGS / "accounts" / f"{slug(name)}.jsonl")}
        self.current_account_only = name
        self.generic_current_tab_x = tab_x
        self.joint_identity_bound = set()
        self.active = account
        self._capture_account = account
        self._capture_binding_probe = False
        self.event("generic_current_window_bound", account, tab_slot=slot_index, tab_x=tab_x, tab_clicks=0)
        from hyol_checkpoints import bind_current_identity, guard_legacy_local_unresolved
        if getattr(self,'automatic_accounts',False):
            account = bind_current_identity(self,account)
            guard_legacy_local_unresolved(self)
            return account
        guard_legacy_local_unresolved(self)
        return bind_current_identity(self, account)

    def prepare_additional_arena_request(self, account: Account, wins: int, *, target_mode: str = 'wins') -> None:
        if target_mode not in ('tickets', 'wins'):
            raise ValueError('竞技场结束条件须为战令归零或指定胜场')
        self.arena_target_mode = target_mode
        requests = self.state.global_state.setdefault("arena_requests", {})
        record = requests.get(account.name, {})
        current_wins = self._arena_victory_count(account)
        actions = self.state.accounts.get(account.name, {}).get("actions", {})
        prefix = "arena_casual_battle_"
        last_attempt = max((int(key[len(prefix):]) for key in actions if key.startswith(prefix) and key[len(prefix):].isdigit()), default=0)
        target_mode_changed = isinstance(record, dict) and record.get('target_mode', 'wins') != target_mode
        if not record or record.get("status") == "done" or target_mode_changed:
            record = {
                "status": "running", "target_mode": target_mode, "requested_wins": wins,
                "base_wins": current_wins,
                "target": current_wins + wins,
                "attempt_limit": last_attempt + (wins if target_mode == 'wins' else 0) + ARENA_MAX_ATTEMPTS,
                "tickets_exhausted": False,
                "created_at": now_iso(),
            }
            requests[account.name] = record
        else:
            previous_requested = int(record.get("requested_wins", wins))
            previous_target = int(record.get("target", current_wins + previous_requested))
            base_wins = int(record.get("base_wins", max(0, previous_target - previous_requested)))
            record["base_wins"] = base_wins
            if target_mode == 'wins' and previous_requested != wins:
                record.update(
                    requested_wins=wins, target=base_wins + wins,
                    attempt_limit=max(int(record.get("attempt_limit", 0)), last_attempt + wins + ARENA_MAX_ATTEMPTS),
                    updated_at=now_iso(),
                )
                self.event("arena_request_updated", account, previous_requested_wins=previous_requested,
                           requested_wins=wins, target=record["target"])
            else:
                record.update(requested_wins=wins, target_mode=target_mode,
                              attempt_limit=max(int(record.get("attempt_limit", 0)),
                                                last_attempt + (wins if target_mode == 'wins' else 0) + ARENA_MAX_ATTEMPTS))
        self.arena_victory_target = int(record["target"])
        self.arena_attempt_limit = int(record["attempt_limit"])
        self.state.save_global()
        remaining = max(0, self.arena_victory_target - current_wins)
        detail = (f"本轮目标为战令剩余次数归零；指定胜场{wins}仅在辅助模式生效"
                  if target_mode == 'tickets'
                  else f"本轮选择新增{record['requested_wins']}场胜利，尚需{remaining}场；将执行到目标完成")
        self.event("arena_request_ready", account, target_mode=target_mode, target=self.arena_victory_target,
                   requested_wins=record["requested_wins"], remaining_wins=remaining, detail=detail)

    def execute_command(self, command: SchedulerCommand) -> int:
        """Run one finite panel command under this scheduler's single lease."""
        self.command_kind = command.kind
        self.command_started_at = now_iso()
        self._full_market_cycle = None
        self.arena_victory_target = command.arena_victory_target
        self.team_battle_targets = (
            {
                "group_instance": int(command.group_instance_count or 0),
                "strong_approach": int(command.strong_approach_count or 0),
            }
            if command.team_index is not None
            and (command.group_instance_count is not None or command.strong_approach_count is not None)
            else None
        )
        lease = ForegroundRuntimeLease()
        try:
            try:
                acquired = lease.acquire()
            except OSError as exc:
                detail = f"shared-window runtime lease unavailable: {exc}"
                self.event("runtime_lease_error", scope="command", detail=detail)
                return self._finish_command(command, 2, "pending", detail)
            if not acquired:
                detail = "another scheduler owns the shared-window runtime lease"
                self.event("runtime_lease_blocked", scope="command", detail=detail)
                return self._finish_command(command, 2, "pending", detail)
            self.event("runtime_lease_acquired", scope="command")
            self.start_f12_monitor()
            self.bind_window(
                wait=command.current_account is None,
                timeout_seconds=15 if command.arena_additional_victories else None,
            )
            self.event("command_start", command=command.kind, readonly_preflight=command.readonly_preflight)
            if command.kind != "recognize":
                if command.generic_current_window:
                    visible = self.bind_generic_current_window()
                    self.prepare_additional_arena_request(visible, command.arena_victory_target)
                elif command.current_account is not None:
                    self.bind_current_account_only(command.current_account, command.current_tab_x)
                    if command.arena_additional_victories:
                        self.prepare_additional_arena_request(self.by_name[command.current_account], command.arena_victory_target)
                elif command.arena_additional_victories and command.kind == "arena":
                    visible = self.bind_visible_account_only()
                    self.prepare_additional_arena_request(visible, command.arena_victory_target)
                elif command.team_index is not None:
                    team = next((item for item in self.teams if item.index == command.team_index), None)
                    if team is None:
                        raise PendingError(f"unknown team index: {command.team_index}")
                    # A team command only operates on its configured leader
                    # and members.  Rebinding that bounded scope allows an
                    # unrelated white tab to remain quarantined without ever
                    # switching to it; a complete saved map is still required
                    # before this shortcut is permitted.
                    self.rebind_tabs_by_identity(team.members)
                elif command.kind == "arena":
                    # Arena is best-effort per account: offline, white or
                    # identity-unknown tabs are recorded and skipped, while
                    # positively identified online accounts continue.
                    self.rebind_tabs_by_identity(skip_unavailable=True)
                else:
                    self.rebind_tabs_by_identity(skip_unavailable=True)
            if command.kind == "recognize":
                self.recognize_two_passes()
            elif command.kind == "mail":
                self.run_accounts_module("mail", self.module_mail)
            elif command.kind == "welfare":
                self.run_accounts_module("welfare", self.module_welfare)
            elif command.kind == "arena":
                self.run_combat_interleaved("arena")
            elif command.kind == "survival":
                self.run_combat_interleaved("survival")
            elif command.team_index is not None:
                self.run_team_selection(command.team_index)
            elif command.kind == "market":
                self.run_market_all()
            elif command.kind == "managed":
                self.run_managed_all()
            elif command.kind == "all_day":
                self.run_all_day()
            elif command.kind == "full":
                self._run_full_order(include_recognize=True)
            if not self.command_is_complete(command):
                detail = f"{command.kind} finished with unresolved module or action state"
                self.state.add_pending("global", command.kind, detail)
                self.event("command_pending", command=command.kind, detail=detail)
                return self._finish_command(command, 2, "pending", detail)
            self.state.resolve_pending("global", command.kind)
            if command.arena_additional_victories:
                request_account = command.current_account or self.current_account_only
                if request_account and request_account in self.state.global_state.get("arena_requests", {}):
                    record = self.state.global_state["arena_requests"][request_account]
                    record.update(status="done", completed_at=now_iso())
                    self.state.save_global()
            self.event("command_complete", command=command.kind, clicks=self.click_count, content_clicks=self.content_click_count)
            return self._finish_command(command, 0, "complete")
        except (PendingError, LeaseBlocked, SafetyError) as exc:
            self.state.add_pending("global", command.kind, str(exc))
            self.event("command_pending", command=command.kind, detail=str(exc))
            return self._finish_command(command, 2, "pending", str(exc))
        except KeyboardInterrupt:
            self.event("scheduler_stopped", reason="F12_or_keyboard_interrupt", command=command.kind)
            return self._finish_command(command, 130, "stopped", "F12_or_keyboard_interrupt")
        except Exception as exc:
            self.event("command_pending", command=command.kind, detail=f"{type(exc).__name__}: {exc}")
            return self._finish_command(command, 3, "error", f"{type(exc).__name__}: {exc}")
        finally:
            self.event("command_exit", command=command.kind, clicks=self.click_count, content_clicks=self.content_click_count)
            self.stop.set()
            lease.release()
            self.event("runtime_lease_released", scope="command")

    def run(self) -> int:
        lease = ForegroundRuntimeLease()
        try:
            try:
                acquired = lease.acquire()
            except OSError as exc:
                detail = f"shared-window runtime lease unavailable: {exc}"
                self.event("runtime_lease_error", scope="stage", detail=detail)
                self.event("stage_pending", stage=self.stage, detail=detail)
                return 2
            if not acquired:
                detail = "another scheduler owns the shared-window runtime lease"
                self.event("runtime_lease_blocked", scope="stage", detail=detail)
                self.event("stage_pending", stage=self.stage, detail=detail)
                return 2
            self.event("runtime_lease_acquired", scope="stage")
            self.start_f12_monitor()
            self.bind_window(wait=True)
            self.event("scheduler_start", stage=self.stage, enroll=self.enroll, no_content_clicks=self.no_content_clicks, accounts=[a.name for a in self.accounts])
            # A direct non-recognize CLI stage (especially the long-lived
            # all-day monitor) must not trust a stale tab map.  Rebind is
            # chrome-only and therefore safe before any game-content action;
            # ``auto`` already performs the same probe in recognize_two_passes.
            if self.stage not in ("recognize", "auto"):
                self.rebind_tabs_by_identity()
            if self.stage in ("recognize", "auto"):
                while True:
                    try:
                        self.recognize_two_passes()
                        break
                    except PendingError as exc:
                        self.state.add_pending("global", "recognize", str(exc))
                        self.event("recognize_wait", detail=str(exc))
                        if self.stage == "recognize":
                            raise
                        self.wait_interruptible(max(5, int(self.config.get("PendingRetryMinutes", "10")) * 60))
                if self.stage == "recognize":
                    self.state.resolve_pending("global", "recognize")
                    return 0
            if self.stage in ("independent", "auto"):
                self.run_independent()
                if self.stage == "independent":
                    return 0
            if self.stage in ("team", "auto"):
                self.run_team()
                if self.stage == "team":
                    return 0
            if self.stage in ("full", "auto"):
                self.run_full()
                if self.stage == "full":
                    return 0
            if self.stage in ("all_day", "auto"):
                self.run_all_day()
            return 0
        except PendingError as exc:
            self.state.add_pending("global", self.stage, str(exc))
            self.event("stage_pending", stage=self.stage, detail=str(exc))
            return 2
        except KeyboardInterrupt:
            self.event("scheduler_stopped", reason="F12_or_keyboard_interrupt")
            return 130
        finally:
            self.event("scheduler_exit", clicks=self.click_count, content_clicks=self.content_click_count)
            self.stop.set()
            lease.release()
            self.event("runtime_lease_released", scope="stage")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="火影忍者OL六标签共享窗口日常调度器")
    parser.add_argument("--config", type=Path, default=CONFIG_PATH)
    parser.add_argument("--stage", choices=("recognize", "independent", "team", "full", "all_day", "auto"), default="auto")
    parser.add_argument("--enroll", action="store_true", help="重新采集六标签身份模板；只点击微端标签栏")
    parser.add_argument("--no-content-clicks", action="store_true", help="禁止游戏内容点击，标签切换仍允许")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    config = read_config(args.config)
    try:
        scheduler = Scheduler(config, args.stage, args.enroll, args.no_content_clicks)
        return scheduler.run()
    except (ValueError, SafetyError) as exc:
        print(f"CONFIG_OR_SAFETY_ERROR: {exc}", file=sys.stderr)
        return 3


if __name__ == "__main__":
    raise SystemExit(main())

