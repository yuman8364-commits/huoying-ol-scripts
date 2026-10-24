"""Weekend Daruma challenge. All coordinates are normalized 1920x1032 client pixels."""
from __future__ import annotations

import statistics
import time
import atexit
import ctypes
from pathlib import Path
from dataclasses import dataclass
from datetime import datetime

import numpy as np
from PIL import Image, ImageGrab

import hyol_scheduler as hs


HOTSPOT = (450, 69, 550, 145)
# Limited-time activities rotate the menu rows. Match the same text template
# anywhere in the proven left menu rather than assuming its September row.
MENU = (475, 340, 690, 745)
WEEKEND_PAGE = (880, 245, 1190, 360)
GAME_PAGE = (920, 220, 1300, 325)
REWARD_DIGIT = (1289, 642, 1317, 666)
CHANCE_DIGIT = (1258, 328, 1288, 358)
BAR = (765, 717, 1324, 762)
TARGET_X = 1044
DRUM_POINT = (1045, 645)
DRUM_POINT_OPPOSITE = (1080, 650)
# The lower cylinder shifts sideways as upper layers are removed.
BOTTOM_SELECTION = (960, 610, 1190, 690)
BOTTOM_TRACK_ROI = (900, 615, 1200, 690)
OBTAINED_SLOTS = tuple((754, 402 + 45 * i, 798, 444 + 45 * i) for i in range(6))


class _BarCapture:
    """Reusable screen-sized GDI surface for the timing bar only."""

    class _BitmapInfoHeader(ctypes.Structure):
        _fields_ = [('size', ctypes.c_uint32), ('width', ctypes.c_int32),
                    ('height', ctypes.c_int32), ('planes', ctypes.c_uint16),
                    ('bits_per_pixel', ctypes.c_uint16), ('compression', ctypes.c_uint32),
                    ('image_size', ctypes.c_uint32), ('x_pixels_per_meter', ctypes.c_int32),
                    ('y_pixels_per_meter', ctypes.c_int32), ('colors_used', ctypes.c_uint32),
                    ('colors_important', ctypes.c_uint32)]

    def __init__(self, bounds: tuple[int, int, int, int]):
        self.bounds = bounds
        self.width = bounds[2] - bounds[0]
        self.height = bounds[3] - bounds[1]
        self.user32 = ctypes.windll.user32
        self.gdi32 = ctypes.windll.gdi32
        self.user32.GetDC.argtypes = [ctypes.c_void_p]
        self.user32.GetDC.restype = ctypes.c_void_p
        self.user32.ReleaseDC.argtypes = [ctypes.c_void_p, ctypes.c_void_p]
        self.gdi32.CreateCompatibleDC.argtypes = [ctypes.c_void_p]
        self.gdi32.CreateCompatibleDC.restype = ctypes.c_void_p
        self.gdi32.CreateDIBSection.argtypes = [ctypes.c_void_p, ctypes.POINTER(self._BitmapInfoHeader),
                                                 ctypes.c_uint, ctypes.POINTER(ctypes.c_void_p),
                                                 ctypes.c_void_p, ctypes.c_uint]
        self.gdi32.CreateDIBSection.restype = ctypes.c_void_p
        self.gdi32.SelectObject.argtypes = [ctypes.c_void_p, ctypes.c_void_p]
        self.gdi32.SelectObject.restype = ctypes.c_void_p
        self.gdi32.BitBlt.argtypes = [ctypes.c_void_p, ctypes.c_int, ctypes.c_int,
                                     ctypes.c_int, ctypes.c_int, ctypes.c_void_p,
                                     ctypes.c_int, ctypes.c_int, ctypes.c_uint32]
        self.gdi32.BitBlt.restype = ctypes.c_int
        self.gdi32.DeleteObject.argtypes = [ctypes.c_void_p]
        self.gdi32.DeleteDC.argtypes = [ctypes.c_void_p]
        self.screen_dc = self.user32.GetDC(None)
        self.memory_dc = self.gdi32.CreateCompatibleDC(self.screen_dc)
        header = self._BitmapInfoHeader(ctypes.sizeof(self._BitmapInfoHeader), self.width,
                                        -self.height, 1, 32, 0, self.width * self.height * 4,
                                        0, 0, 0, 0)
        self.bits = ctypes.c_void_p()
        self.bitmap = self.gdi32.CreateDIBSection(self.screen_dc, ctypes.byref(header),
                                                   0, ctypes.byref(self.bits), None, 0)
        if not (self.screen_dc and self.memory_dc and self.bitmap and self.bits.value):
            self.close()
            raise hs.SafetyError('达摩低延迟截图设备初始化失败')
        self.old_bitmap = self.gdi32.SelectObject(self.memory_dc, self.bitmap)
        self.pixel_buffer = (ctypes.c_uint8 * (self.width * self.height * 4)).from_address(self.bits.value)

    def capture(self) -> tuple[np.ndarray, float, float]:
        began = time.perf_counter()
        ok = self.gdi32.BitBlt(self.memory_dc, 0, 0, self.width, self.height,
                               self.screen_dc, self.bounds[0], self.bounds[1], 0x00CC0020)
        ended = time.perf_counter()
        if not ok:
            raise hs.SafetyError('达摩低延迟判定条截图失败')
        bgra = np.ctypeslib.as_array(self.pixel_buffer).reshape(self.height, self.width, 4)
        rgb = bgra[:, :, 2::-1].copy().astype(np.int16)
        return rgb, (began + ended) / 2, ended - began

    def close(self) -> None:
        if getattr(self, 'memory_dc', None) and getattr(self, 'old_bitmap', None):
            self.gdi32.SelectObject(self.memory_dc, self.old_bitmap)
        if getattr(self, 'bitmap', None):
            self.gdi32.DeleteObject(self.bitmap)
            self.bitmap = None
        if getattr(self, 'memory_dc', None):
            self.gdi32.DeleteDC(self.memory_dc)
            self.memory_dc = None
        if getattr(self, 'screen_dc', None):
            self.user32.ReleaseDC(None, self.screen_dc)
            self.screen_dc = None


_bar_capture: _BarCapture | None = None


def _close_bar_capture() -> None:
    global _bar_capture
    if _bar_capture is not None:
        _bar_capture.close()
        _bar_capture = None


atexit.register(_close_bar_capture)


def is_weekend(at: datetime | None = None) -> bool:
    return (at or datetime.now()).weekday() >= 5


def _visible(r, name: str, roi: tuple[int, int, int, int], frame: Image.Image | None = None,
             threshold: float = .91) -> bool:
    return r.visible(name, roi, frame, threshold)


def _single_digit(r, prefix: str, roi: tuple[int, int, int, int], frame: Image.Image) -> int:
    matches = [number for number in range(4)
               if (hs.ASSETS / f'{prefix}{number}.bmp').exists()
               and _visible(r, f'{prefix}{number}.bmp', roi, frame, .95)]
    if len(matches) != 1:
        evidence = r.scheduler.save_raw_evidence(frame, r.account, prefix + 'unknown', 'pending')
        raise hs.PendingError(f'达摩次数无法唯一识别：{matches}；画面：{evidence}')
    return matches[0]


def reward_remaining(r, frame: Image.Image | None = None) -> int:
    frame = frame or r.scheduler.capture()
    if not _visible(r, 'daruma_weekend_page.bmp', WEEKEND_PAGE, frame, .93):
        raise hs.PendingError('达摩剩余奖励次数读取时，周末福利页未确认')
    return _single_digit(r, 'daruma_reward_digit_', REWARD_DIGIT, frame)


def chance_remaining(r, frame: Image.Image | None = None) -> int:
    frame = frame or r.scheduler.capture()
    if not _visible(r, 'daruma_game_page.bmp', GAME_PAGE, frame, .93):
        raise hs.PendingError('达摩今日剩余机会读取时，挑战页未确认')
    return _single_digit(r, 'daruma_chance_digit_', CHANCE_DIGIT, frame)


def _stable_count(read, r) -> int:
    first = read(r)
    r.scheduler.wait_interruptible(.25)
    second = read(r)
    if first != second:
        raise hs.PendingError(f'达摩次数尚未稳定：{first}→{second}')
    return second


def _open_weekend(r) -> None:
    s = r.scheduler
    frame = s.capture()
    if _visible(r, 'daruma_game_page.bmp', GAME_PAGE, frame, .93):
        return
    if _visible(r, 'daruma_weekend_page.bmp', WEEKEND_PAGE, frame, .93):
        return
    if _visible(r, 'home_leave.bmp', (1800, 930, 1920, 1032), frame, .92):
        r.click('home_leave.bmp', (1800, 930, 1920, 1032), 15, 17,
                'daruma_return_from_home', ['home_leave.bmp'])
        if not s.wait_until_main(8):
            raise hs.PendingError('达摩结算后离开家园，尚未确认木叶主界面')
        frame = s.capture()
    if not s.main_ready(frame):
        raise hs.PendingError('达摩入口要求当前账号在木叶主界面或已核实的达摩页面')
    r.click('daruma_hotspot.bmp', HOTSPOT, 27, 27, 'daruma_open_hotspot',
            ['anchor_top_menu.bmp'], threshold=.91)
    try:
        r.wait('daruma_weekend_menu.bmp', MENU, 8)
    except hs.PendingError as exc:
        # Opening the hotspot can leave its initial activity over the role
        # strip when the requested entry is absent. Close only the independent
        # title/X pair proven for that initial page; an unknown activity stays
        # untouched. This cleanup gives neither task nor reward credit.
        from hyol_identity_overlays import inspect, dismiss_identity_overlays
        frame = getattr(s, '_last_frame', None)
        frame = frame if frame is not None else s.capture()
        overlay = inspect(s, frame)
        if overlay is None or overlay[0] != 'recharge_activity':
            raise
        evidence = s.save_raw_evidence(frame, r.account,
                                       'daruma_visible_menu_unconfirmed', 'pending')
        r.event('daruma_entry_unconfirmed', evidence=str(evidence),
                menu_roi=MENU, task_credit=False, reward_credit=False,
                detail='当前可见活动菜单未确认周末活动；不推断全天未开放')
        dismiss_identity_overlays(s, r.account, frame, only_kind='recharge_activity')
        if not s.wait_until_main(8):
            raise hs.PendingError('达摩入口未确认，关闭已核实活动页后仍未确认木叶；'
                                  f'原任务保留pending；截图={evidence}') from exc
        returned = s.save_raw_evidence(s.capture(), r.account,
                                      'daruma_menu_unconfirmed_returned', 'verified')
        r.event('daruma_entry_unconfirmed_returned', evidence=str(returned),
                entry_evidence=str(evidence), task_credit=False, reward_credit=False)
        raise hs.PendingError('达摩当前可见活动菜单未确认周末活动；已关闭已核实活动页并回村，'
                              f'原任务保留pending；截图={evidence}') from exc
    r.click('daruma_weekend_menu.bmp', MENU, 65, 15, 'daruma_open_weekend',
            ['daruma_weekend_menu.bmp'], threshold=.91)
    r.wait('daruma_weekend_page.bmp', WEEKEND_PAGE, 8)


def _go_game(r) -> None:
    frame = r.scheduler.capture()
    if _visible(r, 'daruma_game_page.bmp', GAME_PAGE, frame, .93):
        return
    if not _visible(r, 'daruma_weekend_page.bmp', WEEKEND_PAGE, frame, .93):
        raise hs.PendingError('达摩挑战入口页未确认')
    r.click('daruma_go.bmp', (1190, 635, 1330, 710), 47, 18, 'daruma_go',
            ['daruma_weekend_page.bmp'], threshold=.91)
    r.wait('daruma_game_page.bmp', GAME_PAGE, 8)


def _trusted_geometry(s):
    checked = getattr(s, '_health_checked_at', None)
    if isinstance(checked, (float, int)) and time.monotonic()-checked >= 3:
        s.capture()  # Even the tight timing loop checks the central disconnect dialog.
    geometry = getattr(s, '_capture_geometry', None)
    if (geometry is None or hs.Win32.foreground() != s.hwnd
            or hs.Win32.rect(s.hwnd) != geometry.window
            or hs.Win32.game_content_rect(s.hwnd) != geometry.content):
        raise hs.SafetyError('达摩计时期间窗口焦点或几何发生变化')
    return geometry


def _screen_roi(geometry, roi):
    x1, y1 = geometry.point(roi[0], roi[1])
    x2, y2 = geometry.point(roi[2]-1, roi[3]-1)
    return x1, y1, x2+1, y2+1


def _normalized_roi_capture(capture, roi):
    frame, at, elapsed = capture.capture()
    size = (roi[2]-roi[0], roi[3]-roi[1])
    if (frame.shape[1], frame.shape[0]) != size:
        started = time.perf_counter()
        frame = np.asarray(Image.fromarray(frame.astype(np.uint8)).resize(size, Image.Resampling.BILINEAR), dtype=np.int16)
        elapsed += time.perf_counter()-started
    return frame, at, elapsed


def _raw_bar(s) -> tuple[np.ndarray, float, float]:
    """Only the timing ROI is copied; full-frame matching is outside this loop."""
    geometry = _trusted_geometry(s)
    s.check_stop()
    global _bar_capture
    bounds = _screen_roi(geometry, BAR)
    if _bar_capture is not None and _bar_capture.bounds != bounds:
        _close_bar_capture()
    if _bar_capture is None:
        _bar_capture = _BarCapture(bounds)
    return _normalized_roi_capture(_bar_capture, BAR)


def moving_column(baseline: np.ndarray, frame: np.ndarray) -> int | None:
    if frame.shape != baseline.shape or frame.ndim != 3:
        return None
    columns = _flame_columns(frame)
    return columns[0] if len(columns) == 1 else None


def _flame_columns(frame: np.ndarray) -> list[int]:
    # The flame is brighter than all five static bar bands. Starting the game
    # repaints several wide bands, so pre-game frame subtraction is unreliable.
    red, green, blue = frame[:, :, 0], frame[:, :, 1], frame[:, :, 2]
    score = ((red >= 220) & (green >= 155) & (blue < 115)).sum(axis=0)
    mask = score >= 5
    groups: list[tuple[int, int]] = []
    start = None
    for x, active in enumerate(mask):
        if active and start is None:
            start = x
        elif not active and start is not None:
            groups.append((start, x))
            start = None
    if start is not None:
        groups.append((start, len(mask)))
    narrow = [(a, b) for a, b in groups if 5 <= b - a <= 30 and int(score[a:b].max()) >= 8]
    return [BAR[0] + int((a + b - 1) / 2) for a, b in narrow]


def judgment_column_from_trace(path: str) -> int | None:
    with np.load(path) as trace:
        frames = trace['frames']
        left, top, right, bottom = (int(x) for x in trace['roi'])
        if not (left <= BAR[0] and top <= BAR[1] and right >= BAR[2] and bottom >= BAR[3]):
            return None
        fixed: list[int] = []
        for frame in frames:
            bar = frame[BAR[1]-top:BAR[3]-top, BAR[0]-left:BAR[2]-left]
            columns = _flame_columns(bar.astype(np.int16))
            near = [x for x in columns if abs(x-TARGET_X) <= 95]
            far = [x for x in columns if abs(x-TARGET_X) >= 150]
            if len(near) == 1 and far:
                fixed.append(near[0])
    if len(fixed) < 3 or max(fixed) - min(fixed) > 8:
        return None
    return int(round(statistics.median(fixed)))


def bottom_selected(frame: np.ndarray) -> bool:
    if frame.shape != (BOTTOM_SELECTION[3] - BOTTOM_SELECTION[1],
                       BOTTOM_SELECTION[2] - BOTTOM_SELECTION[0], 3):
        return False
    white = (frame[:, :, :3] >= 210).all(axis=2)
    return int(white.sum()) >= 180


def selected_bottom_bounds(frame: np.ndarray) -> tuple[int, int] | None:
    if frame.shape != (BOTTOM_TRACK_ROI[3] - BOTTOM_TRACK_ROI[1],
                       BOTTOM_TRACK_ROI[2] - BOTTOM_TRACK_ROI[0], 3):
        return None
    bright = (frame[:, :, :3] >= 210).all(axis=2)
    columns = np.flatnonzero(bright.sum(axis=0) >= 2)
    if len(columns) < 65:
        return None
    left = BOTTOM_TRACK_ROI[0] + int(columns[0])
    right = BOTTOM_TRACK_ROI[0] + int(columns[-1])
    if not 70 <= right - left <= 155 or right >= 1190 or left <= 910:
        return None
    return left, right


def obtained_count(before: Image.Image, current: Image.Image) -> int:
    first = np.asarray(before.convert('RGB'), dtype=np.int16)
    now = np.asarray(current.convert('RGB'), dtype=np.int16)
    filled = []
    for x1, y1, x2, y2 in OBTAINED_SLOTS:
        difference = float(np.abs(first[y1:y2, x1:x2] - now[y1:y2, x1:x2]).mean())
        filled.append(difference >= 30.0)
    if any(filled[i] and not all(filled[:i]) for i in range(1, len(filled))):
        raise hs.PendingError(f'达摩奖励图标顺序异常：{filled}')
    return sum(filled)


@dataclass(frozen=True)
class Motion:
    x: int
    velocity: float
    direction: int
    captured_at: float
    capture_seconds: float


def estimate_motion(samples: list[tuple[float, int, float]]) -> Motion | None:
    if len(samples) < 6:
        return None
    recent = samples[-40:]
    # The pointer teleports to the left edge at the end of a sweep. Fit only
    # the current uninterrupted sweep; adjacent-frame deltas are too noisy.
    for i in range(len(recent) - 1, 0, -1):
        if abs(recent[i][1] - recent[i - 1][1]) > 100:
            recent = recent[i:]
            break
    if len(recent) < 6 or recent[-1][0] - recent[0][0] < .18:
        return None
    elapsed = np.asarray([row[0] - recent[0][0] for row in recent], dtype=float)
    positions = np.asarray([row[1] for row in recent], dtype=float)
    velocity, intercept = np.polyfit(elapsed, positions, 1)
    if float(np.max(np.abs(positions - (velocity * elapsed + intercept)))) > 9:
        return None
    if not 80 <= abs(velocity) <= 2500:
        return None
    captured_at, _observed_x, capture_seconds = recent[-1]
    fitted_x = int(round(velocity * elapsed[-1] + intercept))
    return Motion(fitted_x, velocity, 1 if velocity > 0 else -1,
                  captured_at, capture_seconds)


def predicted_hit_seconds(motion: Motion, target_x: int, latency_seconds: float,
                          now: float) -> float | None:
    crossing = (target_x - motion.x) / motion.velocity
    dispatch_in = motion.captured_at + crossing - latency_seconds - now
    if crossing < 0 or crossing > .42 or dispatch_in < -.012:
        return None
    return max(0.0, dispatch_in)


def _pointer_still_moving(s, baseline: np.ndarray) -> bool:
    first, _at, _duration = _raw_bar(s)
    s.wait_interruptible(.12)
    second, _at, _duration = _raw_bar(s)
    a = moving_column(baseline, first)
    b = moving_column(baseline, second)
    return a is not None and b is not None and abs(a - b) >= 4


def _fast_strike(r, motion: Motion, delay: float, click_label: str) -> float | None:
    s = r.scheduler
    if s.no_content_clicks or s.active != r.account:
        raise hs.SafetyError('达摩敲击没有当前账号内容点击权限')
    _trusted_geometry(s)
    if s.state.action_status(r.account.name, click_label) != 'new':
        raise hs.PendingError('达摩敲击动作结果未核实，禁止重复输入')
    target_at = time.perf_counter() + delay
    s.state.set_action(r.account.name, click_label, 'pending',
                       f'敲击前落盘 x={motion.x},v={motion.velocity:.1f},dir={motion.direction}')
    remaining = target_at - time.perf_counter()
    if remaining < -.012:
        s.state.set_action(r.account.name, click_label, 'new', '落盘耗时超过本次时窗，未发送敲击')
        return None
    if remaining > 0:
        time.sleep(remaining)
    s.check_stop()
    _trusted_geometry(s)
    s.check_input_pause()
    t = time.perf_counter()
    hs.Win32.user32.mouse_event(hs.Win32.MOUSEEVENTF_LEFTDOWN, 0, 0, 0, 0)
    time.sleep(.018)
    hs.Win32.user32.mouse_event(hs.Win32.MOUSEEVENTF_LEFTUP, 0, 0, 0, 0)
    s.click_count += 1
    s.content_click_count += 1
    post_bar, post_at, _post_duration = _raw_bar(s)
    post_x = moving_column(post_bar, post_bar)
    s.event('daruma_strike_sent', r.account, x=motion.x, velocity=motion.velocity,
            direction=motion.direction, scheduled_wait_ms=round(delay * 1000, 2), action=click_label,
            post_x=post_x, post_elapsed_ms=round((post_at-t)*1000, 2))
    rec = r.record('weekend')
    if rec.get('calibration_retry_authorized'):
        rec['calibration_retry_authorized'] = False
        rec['calibration_retry_used_at'] = hs.now_iso()
        r.save()
    return t


def _record_strike_animation(r, action: str) -> str:
    """Capture a short read-only trace so an ambiguous hit can be reviewed."""
    trace_roi = (740, 420, 1340, 785)
    geometry = _trusted_geometry(r.scheduler)
    capture = _BarCapture(_screen_roi(geometry, trace_roi))
    frames: list[np.ndarray] = []
    timestamps: list[float] = []
    try:
        deadline = time.perf_counter() + .75
        while time.perf_counter() < deadline:
            r.scheduler.check_stop()
            _trusted_geometry(r.scheduler)
            frame, at, _elapsed = _normalized_roi_capture(capture, trace_roi)
            frames.append(frame.astype(np.uint8))
            timestamps.append(at)
            time.sleep(.012)
    finally:
        capture.close()
    directory = hs.LOGS / 'daruma_traces'
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / f'{action}.npz'
    np.savez_compressed(path, frames=np.stack(frames), timestamps=np.asarray(timestamps),
                        roi=np.asarray(trace_roi), screen_roi=np.asarray(capture.bounds),
                        window=np.asarray(geometry.window), content=np.asarray(geometry.content))
    return str(path)


def _play_once(r, before_reward: int, before_chance: int, *, resume: bool = False) -> None:
    s, rec = r.scheduler, r.record('weekend')
    index = int(rec.get('attempts_started', 0)) if resume else int(rec.get('attempts_started', 0)) + 1
    base = f'daruma_{hs.slug(r.account.name)}_{s.state.day}_{index}'
    start_action = base + '_start'
    start_status = s.state.action_status(r.account.name, start_action)
    if (resume and start_status not in ('pending', 'done')) or (not resume and start_status != 'new'):
        raise hs.PendingError('达摩上局动作未对账；禁止重复启动或敲击')
    frame = s.capture()
    if resume:
        pre_path = rec.get('last_before_evidence', '')
        if not pre_path:
            raise hs.PendingError('达摩续局缺少开始前证据，不能猜测机会数')
    else:
        pre_path = s.save_raw_evidence(frame, r.account, base + '_before', 'before')
        rec['hits_verified'] = 0
        rec.update(last_before_evidence=str(pre_path), last_chance_before=before_chance,
                   last_reward_before=before_reward)
        r.save()
    with Image.open(pre_path) as image:
        before_game = image.convert('RGB')
    observed = obtained_count(before_game, frame)
    recorded = int(rec.get('hits_verified', 0))
    if observed == recorded + 1 and resume:
        prior_root = base + ('_strike' if recorded == 0 else f'_strike_{observed}')
        retry = int(rec.get('layer_retry', 0))
        prior = prior_root if retry == 0 else f'{prior_root}_retry_{retry}'
        if s.state.action_status(r.account.name, prior) != 'pending':
            raise hs.PendingError('达摩奖励图标增加，但上一敲击动作状态不符')
        s.commit_action(r.account, prior, f'已获得图标{recorded}→{observed}')
        rec['hits_verified'] = observed
        rec['layer_retry'] = 0
        recorded = observed
        r.save()
    elif observed != recorded:
        raise hs.PendingError(f'达摩已获得图标与断点不符：画面{observed}，断点{recorded}')
    if observed == len(OBTAINED_SLOTS) and _visible(
            r, 'daruma_game_over.bmp', (925, 415, 1170, 510), frame, .84):
        final_action = base + '_strike_6'
        trace = rec.get('last_animation_trace', '')
        if (s.state.action_status(r.account.name, final_action) != 'done'
                or not Path(trace).is_file()
                or not Path(rec.get('last_after_evidence', '')).is_file()):
            raise hs.PendingError('达摩六个图标与最终动作证据未对齐')
        current_chance = _stable_count(chance_remaining, r)
        if current_chance >= before_chance:
            raise hs.PendingError('达摩结算页机会变化未核实')
        if s.state.action_status(r.account.name, start_action) == 'pending':
            s.commit_action(r.account, start_action, f'游戏机会{before_chance}→{current_chance}')
        rec.update(six_icons_verified=True, game_over_verified=True,
                   final_game_evidence=rec['last_after_evidence'],
                   last_chance_after=current_chance)
        r.save()
        r.click('daruma_game_close.bmp', (1330, 288, 1385, 345), 16, 16,
                base + '_close', ['daruma_game_page.bmp'])
        _open_weekend(r)
        current_reward = _stable_count(reward_remaining, r)
        if current_reward != before_reward - 1:
            raise hs.PendingError(f'达摩六图标结算后奖励次数未按1减少：{before_reward}→{current_reward}')
        rec.update(remaining=current_reward,
                   perfect_count=int(rec.get('perfect_count', 0)) + 1,
                   last_error='')
        r.save()
        r.event('daruma_perfect_verified', before=before_reward, remaining=current_reward,
                chance_before=before_chance, chance_after=current_chance,
                evidence=rec['last_after_evidence'])
        return
    strike_root = base + ('_strike' if recorded == 0 else f'_strike_{recorded + 1}')
    retry = int(rec.get('layer_retry', 0))
    if retry > 2 and not rec.get('calibration_retry_authorized'):
        raise hs.PendingError('达摩同层空击已达安全上限；保留当前局，禁止再次敲击')
    strike_action = strike_root if retry == 0 else f'{strike_root}_retry_{retry}'
    baseline = np.asarray(frame.crop(BAR).convert('RGB'), dtype=np.int16)
    if s.state.action_status(r.account.name, strike_action) == 'pending' and resume:
        prior_path = rec.get('last_after_evidence', '')
        if not prior_path:
            raise hs.PendingError('达摩上次敲击未决且缺少敲击后证据')
        with Image.open(prior_path) as prior_image:
            prior_frame = prior_image.convert('RGB')
        if (obtained_count(before_game, prior_frame) != recorded
                or _visible(r, 'daruma_game_over.bmp', (925, 415, 1170, 510), prior_frame, .84)
                or not _pointer_still_moving(s, baseline)):
            raise hs.PendingError('达摩上次敲击结果仍不确定，禁止重复输入')
        s.commit_action(r.account, strike_action, '图标未增加、游戏未结束且指针仍在移动；本次为空击')
        rec['layer_retry'] = retry + 1
        if rec['layer_retry'] > 2:
            raise hs.PendingError('达摩同层空击超过2次，保留当前局人工核查')
        rec['hammer_side'] = 'right' if rec.get('hammer_side', 'left') == 'left' else 'left'
        rec.pop('preferred_direction', None)
        r.save()
        retry = rec['layer_retry']
        strike_action = f'{strike_root}_retry_{retry}'
    if s.state.action_status(r.account.name, strike_action) != 'new':
        raise hs.PendingError('达摩本层敲击尚未核实，禁止重复输入')
    if resume and start_status == 'pending' and chance_remaining(r, frame) < before_chance:
        s.commit_action(r.account, start_action, '游戏机会已减少，当前局仍在进行')
    if not resume:
        guarded = s.content_guard(r.account, ['daruma_game_page.bmp'], anchor_threshold=.92)
        ok, _score, sx, sy = s.template('daruma_start.bmp', (890, 480, 1200, 600), guarded, threshold=.92)
        if not ok:
            raise hs.PendingError('达摩开始按钮未确认，未消耗本局机会')
        start_point = s.screen_point(sx + 105, sy + 46)
        s.state.set_action(r.account.name, start_action, 'pending', '开始前写入，等待游戏机会变化核实')
        rec['attempts_started'] = index
        r.save()
        s.check_input_pause()
        hs.Win32.move(*start_point)
        time.sleep(.035)
        s.check_input_pause()
        hs.Win32.user32.mouse_event(hs.Win32.MOUSEEVENTF_LEFTDOWN, 0, 0, 0, 0)
        time.sleep(.018)
        hs.Win32.user32.mouse_event(hs.Win32.MOUSEEVENTF_LEFTUP, 0, 0, 0, 0)
        s.click_count += 1
        s.content_click_count += 1
        s.event('daruma_game_started', r.account, attempt=index, reward_before=before_reward,
                chance_before=before_chance, evidence=str(pre_path))
    if _visible(r, 'daruma_game_over.bmp', (925, 415, 1170, 510), frame, .84):
        raise hs.PendingError('达摩游戏已结束但结束结果未对账，禁止继续敲击')
    # Hover first, then locate the current lowest rim. A weak strike can slide
    # that rim sideways by 50 px, so a fixed click can land outside its body.
    s.check_input_pause()
    hs.Win32.move(*s.screen_point(*DRUM_POINT))
    s.wait_interruptible(.12)
    tracked = np.asarray(ImageGrab.grab(bbox=BOTTOM_TRACK_ROI, all_screens=True).convert('RGB'),
                         dtype=np.int16)
    bounds = selected_bottom_bounds(tracked)
    if bounds is None:
        raise hs.PendingError('达摩最下层当前位置无法测定，零敲击')
    centre = round((bounds[0] + bounds[1]) / 2)
    side = rec.get('hammer_side', 'left')
    strike_point = (centre + (20 if side == 'right' else -20), 650)
    rec['last_bottom_bounds'] = bounds
    rec['last_bottom_click'] = strike_point
    r.save()
    point = s.screen_point(*strike_point)
    s.check_input_pause()
    hs.Win32.move(*point)
    selection_deadline = time.perf_counter() + .28
    selected_ok = False
    while time.perf_counter() < selection_deadline:
        selected = np.asarray(ImageGrab.grab(bbox=BOTTOM_TRACK_ROI, all_screens=True).convert('RGB'),
                              dtype=np.int16)
        observed_bounds = selected_bottom_bounds(selected)
        if observed_bounds is not None and abs(sum(observed_bounds) / 2 - centre) <= 8:
            selected_ok = True
            break
        time.sleep(.015)
    if not selected_ok:
        evidence = s.save_raw_evidence(s.capture(), r.account, base + '_bottom_not_selected', 'pending')
        raise hs.PendingError(f'达摩最下层未出现白色选中轮廓，零敲击：{evidence}')
    samples: list[tuple[float, int, float]] = []
    timings: list[float] = []
    recent_velocities: list[float] = []
    deadline = time.perf_counter() + 7.0
    strike_at = None
    last_motion = None
    correction = float(rec.get('timing_correction_seconds', 0.0))
    while time.perf_counter() < deadline:
        bar, midpoint, elapsed = _raw_bar(s)
        timings.append(elapsed)
        column = moving_column(baseline, bar)
        if column is None:
            continue
        samples.append((midpoint, column, elapsed))
        samples = samples[-48:]
        motion = estimate_motion(samples)
        if motion is None:
            continue
        recent_velocities.append(motion.velocity)
        recent_velocities = recent_velocities[-5:]
        if len(recent_velocities) < 5 or max(recent_velocities) - min(recent_velocities) > 16:
            continue
        last_motion = motion
        capture_latency = statistics.median(timings[-8:]) / 2
        if capture_latency > .006:
            raise hs.PendingError('达摩低延迟截图超过12毫秒，本轮零敲击')
        # The event crosses the centre when the down message reaches Flash.
        latency = min(.30, max(0.0, float(rec.get(
            'measured_judgment_latency_seconds', capture_latency + .008 + correction))))
        wait = predicted_hit_seconds(motion, TARGET_X, latency, time.perf_counter())
        if wait is None:
            continue
        strike_at = _fast_strike(r, motion, wait, strike_action)
        if strike_at is not None:
            break
    rec['last_capture_latency_ms'] = round(statistics.median(timings) * 1000, 2) if timings else None
    rec['last_motion'] = vars(last_motion) if last_motion else None
    if strike_at is not None:
        rec['last_used_judgment_latency_seconds'] = round(latency, 5)
    r.save()
    if strike_at is None:
        evidence = s.save_raw_evidence(s.capture(), r.account, base + '_timing_unknown', 'pending')
        raise hs.PendingError(f'达摩移动指针未可靠定位，零敲击；保留现场：{evidence}')
    trace_path = _record_strike_animation(r, strike_action)
    rec['last_animation_trace'] = trace_path
    judgment_x = judgment_column_from_trace(trace_path)
    rec['last_judgment_x'] = judgment_x
    if judgment_x is not None and last_motion is not None:
        correction_by_trace = (judgment_x - TARGET_X) / last_motion.velocity
        rec['measured_judgment_latency_seconds'] = round(max(0.0, min(.30,
            latency + correction_by_trace)), 5)
        s.event('daruma_judgment_timing_measured', r.account, action=strike_action,
                judgment_x=judgment_x, centre=TARGET_X, velocity=last_motion.velocity,
                previous_latency=round(latency, 5),
                next_latency=rec['measured_judgment_latency_seconds'], trace=trace_path)
    r.save()
    # The large Perfect animation briefly paints over lower reward slots and
    # can make one hit look like two. Let it clear before counting icons.
    s.wait_interruptible(.7)
    end_deadline = time.monotonic() + (10 if recorded + 1 == len(OBTAINED_SLOTS) else 4)
    after = s.capture()
    ended = _visible(r, 'daruma_game_over.bmp', (925, 415, 1170, 510), after, .84)
    gained = obtained_count(before_game, after)
    while time.monotonic() < end_deadline and not ended and (
            gained != recorded + 1 or gained == len(OBTAINED_SLOTS)):
        s.wait_interruptible(.15)
        after = s.capture()
        ended = _visible(r, 'daruma_game_over.bmp', (925, 415, 1170, 510), after, .84)
        gained = obtained_count(before_game, after)
    after_path = s.save_raw_evidence(after, r.account, base + '_after', 'after')
    rec['last_after_evidence'] = str(after_path)
    rec['last_strike_at'] = strike_at
    r.save()
    if gained == recorded + 1:
        s.commit_action(r.account, strike_action, f'最下层命中，已获得图标{recorded}→{gained}')
        rec['hits_verified'] = gained
        rec['layer_retry'] = 0
        r.save()
        r.event('daruma_layer_hit_verified', layer=gained, evidence=str(after_path))
    elif gained != recorded:
        raise hs.PendingError(f'达摩敲击后图标数量跳变：{recorded}→{gained}；{after_path}')
    if not ended:
        if gained == recorded + 1:
            if rec.pop('calibration_single_strike', False):
                r.save()
                raise hs.PendingError(f'达摩校准敲击已核实图标{gained}，先复核动画再敲下一层：{trace_path}')
            if gained >= len(OBTAINED_SLOTS):
                raise hs.PendingError(f'达摩六层已敲中但结算页尚未出现；保留画面：{after_path}')
            return _play_once(r, before_reward, before_chance, resume=True)
        if rec.pop('calibration_single_strike', False):
            if _pointer_still_moving(s, baseline):
                s.commit_action(r.account, strike_action,
                                '校准单次敲击后图标未增加、游戏未结束且指针仍在移动')
                rec['layer_retry'] = retry + 1
                if judgment_x is not None:
                    rec['hammer_side'] = 'right' if rec.get('hammer_side', 'left') == 'left' else 'left'
            r.save()
            raise hs.PendingError(f'达摩校准敲击未增加图标，禁止继续输入；动画：{trace_path}')
        if _pointer_still_moving(s, baseline):
            s.commit_action(r.account, strike_action, '图标未增加、游戏未结束且指针仍在移动；本次为空击')
            rec['layer_retry'] = retry + 1
            rec['hammer_side'] = 'right' if rec.get('hammer_side', 'left') == 'left' else 'left'
            rec.pop('preferred_direction', None)
            r.save()
            if rec['layer_retry'] <= 2:
                r.event('daruma_miss_retry_opposite', layer=recorded + 1,
                        next_hammer_side=rec['hammer_side'], evidence=str(after_path))
                return _play_once(r, before_reward, before_chance, resume=True)
        raise hs.PendingError(f'达摩敲击后既无新图标，也未结束；禁止重复敲击：{after_path}')
    rec.pop('calibration_single_strike', None)
    r.save()
    # The pre-game bar itself prints "Perfect". Require the game-over overlay
    # and final stopped flame, then check the reward counter.
    stopped_x = moving_column(baseline, np.asarray(after.crop(BAR).convert('RGB'), dtype=np.int16))
    if stopped_x is None:
        stopped_x = judgment_x
    rec['last_stopped_x'] = stopped_x
    r.save()
    if stopped_x is None:
        raise hs.PendingError(f'达摩结束后指针位置未知，不能判定Perfect：{after_path}')
    current_chance = _stable_count(chance_remaining, r)
    if current_chance >= before_chance:
        raise hs.PendingError(f'达摩敲击后机会未减少，结果不明：{after_path}')
    if s.state.action_status(r.account.name, start_action) == 'pending':
        s.commit_action(r.account, start_action, f'游戏机会{before_chance}→{current_chance}')
    if s.state.action_status(r.account.name, strike_action) == 'pending':
        s.commit_action(r.account, strike_action, f'游戏已结束，图标{recorded}→{gained}，奖励待核实')
    r.click('daruma_game_close.bmp', (1330, 288, 1385, 345), 16, 16,
            base + '_close', ['daruma_game_page.bmp'])
    _open_weekend(r)
    current_reward = _stable_count(reward_remaining, r)
    if abs(stopped_x - TARGET_X) > 10 and last_motion is not None:
        adjustment = (stopped_x - TARGET_X) / last_motion.velocity
        rec['timing_correction_seconds'] = round(max(-.08, min(.08,
            float(rec.get('timing_correction_seconds', 0.0)) + adjustment)), 5)
        r.event('daruma_timing_adjusted', stopped_x=stopped_x,
                direction=last_motion.direction, correction=rec['timing_correction_seconds'])
    if current_reward != before_reward - 1:
        rec['remaining'] = current_reward
        r.save()
        if current_reward == before_reward and current_chance > 0:
            r.event('daruma_miss_verified', reward_remaining=current_reward,
                    chance_remaining=current_chance, stopped_x=stopped_x, evidence=str(after_path))
            return
        raise hs.PendingError(f'达摩敲击未核实奖励次数减少1：{before_reward}→{current_reward}；证据：{after_path}')
    if abs(stopped_x - TARGET_X) > 10:
        raise hs.PendingError(f'达摩奖励次数减少，但指针未停在Perfect中心：{stopped_x}；证据：{after_path}')
    rec.update(perfect_count=int(rec.get('perfect_count', 0)) + 1,
               remaining=current_reward, last_error='')
    r.save()
    r.event('daruma_perfect_verified', before=before_reward, remaining=current_reward,
            chance_before=before_chance, chance_after=current_chance, evidence=str(after_path))


def run(r) -> None:
    if not is_weekend():
        r.event('weekend_calendar_skip', detail='系统本地时间不是周六或周日；未识别游戏')
        return
    s, rec = r.scheduler, r.record('weekend')
    if int(rec.get('hits_verified', 0)) == 5:
        final_path = Path(rec.get('last_after_evidence', ''))
        before_path = Path(rec.get('last_before_evidence', ''))
        if final_path.is_file() and before_path.is_file():
            with Image.open(final_path) as final_image, Image.open(before_path) as before_image:
                if (obtained_count(before_image, final_image) == 6
                        and _visible(r, 'daruma_game_over.bmp', (925, 415, 1170, 510),
                                     final_image, .84)
                        and _visible(r, 'daruma_chance_digit_0.bmp', CHANCE_DIGIT,
                                     final_image, .95)):
                    base = f'daruma_{hs.slug(r.account.name)}_{s.state.day}_{int(rec["attempts_started"])}'
                    final_action = base + '_strike_6'
                    if s.state.action_status(r.account.name, final_action) != 'pending':
                        raise hs.PendingError('达摩六图标证据与最后一击动作状态不符')
                    s.commit_action(r.account, final_action,
                                    f'结算画面六图标、机会0；{final_path}')
                    start_action = base + '_start'
                    if s.state.action_status(r.account.name, start_action) == 'pending':
                        s.commit_action(r.account, start_action, '结算画面机会0')
                    rec.update(hits_verified=6, six_icons_verified=True,
                               game_over_verified=True, final_game_evidence=str(final_path),
                               last_chance_after=0)
                    r.save()
                    r.event('daruma_finished_evidence_reconciled', evidence=str(final_path),
                            hits=6, chance_remaining=0)
    if 'preferred_direction' in rec:
        # Older checkpoints treated the one-way pointer as bidirectional.
        # Preserve the requested opposite hammer side on an in-flight retry.
        if int(rec['preferred_direction']) == -1 and 'hammer_side' not in rec:
            rec['hammer_side'] = 'right'
        rec.pop('preferred_direction', None)
        r.save()
    _open_weekend(r)
    if _visible(r, 'daruma_game_page.bmp', GAME_PAGE):
        if not _visible(r, 'daruma_start.bmp', (890, 480, 1200, 600)):
            previous = rec.get('last_before_evidence', '')
            if not previous:
                raise hs.PendingError('达摩挑战仍在进行，但缺少上局开始前证据')
            try:
                before_frame = Image.open(previous).convert('RGB')
            except OSError as exc:
                raise hs.PendingError('达摩上局证据无法读取，不能猜测剩余机会') from exc
            previous_chance = chance_remaining(r, before_frame)
            previous_reward = int(rec.get('last_reward_before', rec.get('remaining', -1)))
            if not 1 <= previous_reward <= 3:
                raise hs.PendingError('达摩上局奖励次数未核实，禁止续敲')
            _play_once(r, previous_reward, previous_chance, resume=True)
        else:
            r.click('daruma_game_close.bmp', (1330, 288, 1385, 345), 16, 16,
                    'daruma_close_unstarted', ['daruma_game_page.bmp'])
            _open_weekend(r)
    remaining = _stable_count(reward_remaining, r)
    rec['remaining'] = remaining
    r.save()
    if remaining == 0:
        rec['zero_verified'] = True
    while remaining > 0:
        s.check_stop()
        _open_weekend(r)
        _go_game(r)
        chance = _stable_count(chance_remaining, r)
        if chance == 0:
            raise hs.PendingError(f'达摩奖励剩余{remaining}，但今日机会已耗尽')
        _play_once(r, remaining, chance)
        remaining = int(rec['remaining'])
    final = s.save_raw_evidence(s.capture(), r.account, 'daruma_reward_zero', 'verified')
    rec.update(remaining=0, zero_verified=True, zero_evidence=str(final))
    r.save()
    r.click('daruma_weekend_close.bmp', (1330, 288, 1385, 345), 16, 16,
            'daruma_weekend_close_zero', ['daruma_weekend_page.bmp'])
    if not s.wait_until_main(10):
        raise hs.PendingError('达摩奖励次数0，但关闭活动页后未确认回村')
