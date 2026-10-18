"""Windows desktop adapter. Shares only the input mutex and read-only utilities."""
from __future__ import annotations

import ctypes
from dataclasses import asdict
from datetime import datetime
import hashlib
import json
from pathlib import Path
import threading
import time

import numpy as np
from PIL import Image

from hyol_geometry import GameGeometry
from hyol_scheduler import Win32, ForegroundRuntimeLease
from hyol_accounts import game_session_token

from .journal import atomic_json
from .paths import RUNTIME
from .vision import (ASSETS, IDENTITY, Pending, Stopped, Vision, f1, identity_mask)


class GDICapture:
    class Header(ctypes.Structure):
        _fields_ = [('size', ctypes.c_uint32), ('width', ctypes.c_int32),
                    ('height', ctypes.c_int32), ('planes', ctypes.c_uint16),
                    ('bpp', ctypes.c_uint16), ('compression', ctypes.c_uint32),
                    ('image_size', ctypes.c_uint32), ('xppm', ctypes.c_int32),
                    ('yppm', ctypes.c_int32), ('colors', ctypes.c_uint32), ('important', ctypes.c_uint32)]

    def __init__(self, bounds):
        self.bounds = bounds
        self.width, self.height = bounds[2]-bounds[0], bounds[3]-bounds[1]
        self.screen = self.memory = self.bitmap = self.previous = None
        self.user, self.gdi = ctypes.windll.user32, ctypes.windll.gdi32
        self.user.GetDC.argtypes, self.user.GetDC.restype = [ctypes.c_void_p], ctypes.c_void_p
        self.user.ReleaseDC.argtypes = [ctypes.c_void_p, ctypes.c_void_p]
        self.gdi.CreateCompatibleDC.argtypes = [ctypes.c_void_p]
        self.gdi.CreateCompatibleDC.restype = ctypes.c_void_p
        self.gdi.CreateDIBSection.argtypes = [ctypes.c_void_p, ctypes.POINTER(self.Header), ctypes.c_uint,
                                             ctypes.POINTER(ctypes.c_void_p), ctypes.c_void_p, ctypes.c_uint]
        self.gdi.CreateDIBSection.restype = ctypes.c_void_p
        self.gdi.SelectObject.argtypes = [ctypes.c_void_p, ctypes.c_void_p]
        self.gdi.SelectObject.restype = ctypes.c_void_p
        self.gdi.BitBlt.argtypes = [ctypes.c_void_p, ctypes.c_int, ctypes.c_int, ctypes.c_int, ctypes.c_int,
                                   ctypes.c_void_p, ctypes.c_int, ctypes.c_int, ctypes.c_uint32]
        self.gdi.BitBlt.restype = ctypes.c_int
        self.gdi.DeleteObject.argtypes = self.gdi.DeleteDC.argtypes = [ctypes.c_void_p]
        try:
            self.screen = self.user.GetDC(None)
            self.memory = self.gdi.CreateCompatibleDC(self.screen)
            bits = ctypes.c_void_p()
            header = self.Header(ctypes.sizeof(self.Header), self.width, -self.height, 1, 32, 0,
                                 self.width*self.height*4, 0, 0, 0, 0)
            self.bitmap = self.gdi.CreateDIBSection(self.screen, ctypes.byref(header), 0,
                                                   ctypes.byref(bits), None, 0)
            if not (self.screen and self.memory and self.bitmap and bits.value):
                raise Pending('局部截图设备初始化失败')
            self.previous = self.gdi.SelectObject(self.memory, self.bitmap)
            if not self.previous or self.previous == ctypes.c_void_p(-1).value:
                self.previous = None
                raise Pending('局部截图设备绑定失败')
            self.pixels = (ctypes.c_uint8*(self.width*self.height*4)).from_address(bits.value)
        except BaseException:
            self.close()
            raise

    def grab(self):
        began = time.perf_counter()
        ok = self.gdi.BitBlt(self.memory, 0, 0, self.width, self.height, self.screen,
                              self.bounds[0], self.bounds[1], 0x00CC0020)
        copied = time.perf_counter()
        if not ok:
            raise Pending('局部截图失败')
        bgra = np.ctypeslib.as_array(self.pixels).reshape(self.height, self.width, 4)
        rgb = bgra[:, :, 2::-1].copy()
        # Midpoint estimates when pixels were sampled; duration includes conversion.
        return rgb, (began+copied)/2, time.perf_counter()-began

    def close(self):
        if self.memory and self.previous:
            self.gdi.SelectObject(self.memory, self.previous)
            self.previous = None
        if self.bitmap:
            self.gdi.DeleteObject(self.bitmap)
            self.bitmap = None
        if self.memory:
            self.gdi.DeleteDC(self.memory)
            self.memory = None
        if self.screen:
            self.user.ReleaseDC(None, self.screen)
            self.screen = None


def selected_tab(frame: Image.Image, vision: Vision) -> int:
    matches = vision.templates.find_all(frame, ASSETS/'microclient_tab_close_core.bmp',
                                       (0, 0, 1800, 39), .98)
    candidates = sorted(set(x-38 for _score, x, _y in matches))
    if not candidates or any(not 70 <= b-a <= 200 for a, b in zip(candidates, candidates[1:])):
        raise Pending('浏览器标签布局无法确认')
    values = []
    for x in candidates:
        p = np.asarray(frame.crop((x-12, 12, x+13, 28)).convert('RGB'))
        lum = p.mean(2)
        values.append((float((lum >= 245).mean()), float(np.median(lum)), x))
    values.sort(reverse=True)
    best, second = values[0], values[1] if len(values) > 1 else (0, 0, -1)
    if not ((best[0] >= .45 and best[0]-second[0] >= .25)
            or (best[1] >= 235 and best[1]-second[1] >= 15)):
        raise Pending('当前选中标签无法唯一确认')
    return best[2]


def tab_is_selected(pixels):
    lum = np.asarray(pixels).mean(2)
    return bool((lum >= 245).mean() >= .45 or np.median(lum) >= 235)


def windows():
    return [dict(hwnd=h, pid=Win32.pid(h), title=Win32.title(h)) for h in Win32.game_windows()]


class Desktop:
    def __init__(self, hwnd: int, stop: threading.Event, *, root=RUNTIME, readonly=False):
        self.hwnd, self.stop, self.root = int(hwnd), stop, Path(root)
        self.readonly = readonly
        self.vision = Vision()
        self.lease = ForegroundRuntimeLease()
        self.acquired = False
        self.geometry = None
        self.binding = None
        self.journal = None
        self.last_frame = None
        self.content_clicks = 0
        self.captures = {}
        self._mouse_down = False
        self._monitor_end = threading.Event()

    def __enter__(self):
        try:
            if not self.lease.acquire():
                raise Pending('等待其他脚本释放输入控制权；当前未操作游戏')
            self.acquired = True
            self.check_stop()
            if self.hwnd not in Win32.game_windows() or Win32.user32.IsIconic(self.hwnd):
                raise Pending('指定游戏窗口不可见，请先打开测试界面')
            Win32.activate(self.hwnd)  # Focus only; never move, maximize, or click a tab.
            self.wait(.20)
            if Win32.foreground() != self.hwnd:
                raise Pending('未能取得指定游戏窗口的前台焦点')
            self.geometry = GameGeometry(Win32.rect(self.hwnd), Win32.game_content_rect(self.hwnd))
            token = game_session_token(self)
            observations = []
            for _ in range(3):
                frame = self.capture(check_binding=False)
                observations.append((selected_tab(frame, self.vision), identity_mask(frame.crop(IDENTITY))))
                self.wait(.10)
            tab, role = observations[0]
            if any(x != tab or f1(role, mask) < .94 for x, mask in observations):
                raise Pending('当前标签或角色特征未连续三帧确认')
            self.binding = dict(key=hashlib.sha256(f'{token}:{tab}'.encode()).hexdigest()[:24],
                                session=token, hwnd=self.hwnd, pid=Win32.pid(self.hwnd), tab_x=tab,
                                identity=role.astype(int).tolist(), identity_version=2,
                                geometry=asdict(self.geometry))
            threading.Thread(target=self._monitor, daemon=True, name='DarumaF12').start()
            return self
        except BaseException:
            self.close()
            raise

    def __exit__(self, *args):
        self.close()

    def _monitor(self):
        while not self._monitor_end.wait(.015):
            if Win32.f12_down():
                self.stop.set()
                return

    def close(self):
        self._monitor_end.set()
        try:
            if self._mouse_down:
                Win32.user32.mouse_event(Win32.MOUSEEVENTF_LEFTUP, 0, 0, 0, 0)
                self._mouse_down = False
            for capture in self.captures.values():
                capture.close()
            self.captures.clear()
        finally:
            self.lease.release()
            self.acquired = False

    def check_stop(self):
        if self.stop.is_set() or Win32.f12_down():
            self.stop.set()
            raise Stopped('已停止；断点和未决动作已保留')

    def wait(self, seconds):
        deadline = time.perf_counter()+seconds
        while time.perf_counter() < deadline:
            self.check_stop()
            self.stop.wait(min(.01, max(0, deadline-time.perf_counter())))

    def wait_until(self, deadline):
        # Event.wait on Windows can wake a whole scheduler tick late. Python's
        # short sleeps use high-resolution timers; spin only the final 2ms.
        while True:
            self.check_stop()
            remaining = deadline-time.perf_counter()
            if remaining <= 0:
                return
            if remaining > .004:
                time.sleep(min(.003, remaining-.002))

    def guard_window(self):
        self.check_stop()
        if not self.acquired:
            raise Pending('尚未取得输入互斥锁')
        if (Win32.foreground() != self.hwnd or Win32.user32.IsIconic(self.hwnd)
                or Win32.rect(self.hwnd) != self.geometry.window
                or Win32.game_content_rect(self.hwnd) != self.geometry.content):
            raise Stopped('窗口失焦、移动或缩放；已停止输入')
        if self.binding and Win32.pid(self.hwnd) != self.binding['pid']:
            raise Stopped('游戏进程已改变')

    def capture(self, *, check_binding=True):
        began = time.perf_counter()
        self.guard_window()
        key = ('window', self.geometry.window)
        if key not in self.captures:
            self.captures[key] = GDICapture(self.geometry.window)
        pixels, _at, _elapsed = self.captures[key].grab()
        frame = self.geometry.normalize(Image.fromarray(pixels))
        self.guard_window()
        self.last_frame = frame
        self.vision.templates.begin_frame(frame)
        if self.vision.offline(frame):
            raise Stopped('当前窗口出现掉线提示')
        p = np.asarray(frame.crop((1, 69, 1919, 1031)).resize((96, 48)).convert('L'))
        if p.std() < 8:
            raise Pending('当前游戏画面尚未加载完成')
        if check_binding and self.binding:
            x = self.binding['tab_x']
            if not tab_is_selected(frame.crop((x-12, 12, x+13, 28))):
                raise Stopped('选中的游戏账号标签发生变化')
            if f1(identity_mask(frame.crop(IDENTITY)), np.asarray(self.binding['identity'], bool)) < .94:
                raise Stopped('角色特征发生变化或无法核实')
        self.last_capture_seconds = time.perf_counter()-began
        return frame

    def roi(self, roi):
        self.guard_window()
        a = self.geometry.point(roi[0], roi[1])
        b = self.geometry.point(roi[2]-1, roi[3]-1)
        bounds = (a[0], a[1], b[0]+1, b[1]+1)
        if roi not in self.captures:
            self.captures[roi] = GDICapture(bounds)
        p, at, elapsed = self.captures[roi].grab()
        size = (roi[2]-roi[0], roi[3]-roi[1])
        if (p.shape[1], p.shape[0]) != size:
            began = time.perf_counter()
            p = np.asarray(Image.fromarray(p).resize(size, Image.Resampling.BILINEAR))
            elapsed += time.perf_counter()-began
        self.guard_window()
        return p, at, elapsed

    def guard_fast(self):
        self.guard_window()
        role, _, _ = self.roi(IDENTITY)
        if f1(identity_mask(Image.fromarray(role)), np.asarray(self.binding['identity'], bool)) < .94:
            raise Stopped('敲击前角色特征不符')
        x = self.binding['tab_x']
        tab, _, _ = self.roi((x-12, 12, x+13, 28))
        if not tab_is_selected(tab):
            raise Stopped('敲击前当前标签不再选中')

    @staticmethod
    def cursor():
        class Point(ctypes.Structure):
            _fields_ = [('x', ctypes.c_long), ('y', ctypes.c_long)]
        point = Point()
        if not Win32.user32.GetCursorPos(ctypes.byref(point)):
            raise Pending('鼠标位置读取失败')
        return point.x, point.y

    def move(self, point):
        if self.readonly:
            raise Pending('只读观察禁止输入')
        self.guard_fast()
        Win32.move(*self.geometry.point(*point))

    def pulse(self, point, *, at=None):
        self._dispatch_attempted = False
        try:
            return self._pulse(point, at=at)
        except BaseException:
            if not self._dispatch_attempted and self.journal:
                self.journal.cancel_unsent('输入前停止或预检失败；确认未发送DOWN')
            raise

    def _pulse(self, point, *, at=None):
        """An intent must already be durable. Release UP even on interruption."""
        if self.readonly or not self.journal or not self.journal.data.get('intent'):
            raise Pending('输入缺少独立动作意图或处于只读模式')
        expected = self.geometry.point(*point)
        self.guard_fast()
        if at is None:
            Win32.move(*expected)
            self.wait(.06)
            self.guard_fast()
        elif time.perf_counter() > at-.003:
            self.journal.cancel_unsent('预检耗时超过本次时窗，等待下一周期')
            return None
        if at is not None:
            self.wait_until(at-.015)
        self.guard_window()
        self.check_stop()
        if self.cursor() != expected:
            raise Stopped('鼠标被移动，已取消输入')
        if at is not None:
            self.wait_until(at)
            self.check_stop()
            if Win32.foreground() != self.hwnd or self.cursor() != expected:
                raise Stopped('最终输入门前窗口或鼠标位置变化')
        if at is not None and time.perf_counter()-at > .006:
            self.journal.data['intent']['late_ms'] = round((time.perf_counter()-at)*1000, 3)
            self.journal.cancel_unsent('最终检查错过时窗，等待下一周期')
            return None
        sent_at = time.perf_counter()
        self._mouse_down = True
        self._dispatch_attempted = True
        try:
            Win32.user32.mouse_event(Win32.MOUSEEVENTF_LEFTDOWN, 0, 0, 0, 0)
            self.content_clicks += 1
            self.wait(.018)
        finally:
            try:
                Win32.user32.mouse_event(Win32.MOUSEEVENTF_LEFTUP, 0, 0, 0, 0)
            finally:
                self._mouse_down = False
                # Even interrupted DOWN/UP must remain a sent, unresolved intent.
                self.journal.sent()
        return sent_at

    def failure_report(self, reason):
        directory = self.root/'observations'
        directory.mkdir(parents=True, exist_ok=True)
        identifier = datetime.now().strftime('%Y%m%d_%H%M%S_%f')
        evidence = None
        if self.last_frame is not None:
            path = directory/(identifier+'.png')
            self.last_frame.save(path, compress_level=1)
            evidence = str(path)
        report = dict(reason=str(reason), hwnd=self.hwnd, evidence=evidence,
                      content_clicks=self.content_clicks, binding=self.binding)
        report['report_path'] = str(directory/(identifier+'.json'))
        atomic_json(Path(report['report_path']), report)
        return {key: value for key, value in report.items() if key != 'binding'}
