"""Windows window/input boundary; game orchestration lives in Scheduler."""
from __future__ import annotations
import ctypes
from pathlib import Path
import time
from typing import Callable
from hyol_errors import SafetyError

GAME_TITLE = "火影忍者online"
GAME_EXE = "qqmicrogamebox.exe"
VK_F12 = 0x7B


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
            # An exception inside a ctypes callback would silently terminate
            # the whole enumeration; skip the failing window and keep walking.
            try:
                if cls.user32.IsWindowVisible(hwnd) and cls.title(hwnd) == GAME_TITLE:
                    path = cls.process_path(cls.pid(hwnd))
                    if Path(path).name.lower() == GAME_EXE:
                        found.append(int(hwnd))
            except Exception:
                pass
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
            # Same as game_windows: never let one failing child window abort
            # the enumeration and masquerade as "no content surface".
            try:
                if cls.user32.IsWindowVisible(child):
                    name = ctypes.create_unicode_buffer(256)
                    cls.user32.GetClassNameW(child, name, len(name))
                    if name.value == "CefBrowserWindow":
                        candidates.add(cls.rect(child))
            except Exception:
                pass
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
    def wheel(cls, x: int, y: int, delta: int, *, before_down=None, expected_hwnd=None) -> None:
        if not cls.user32.SetCursorPos(x, y):
            raise SafetyError("SetCursorPos failed before wheel")
        time.sleep(0.15)
        # Same foreground/F12 gate as click: a scroll is real input too.
        if before_down is not None:
            before_down()
        if expected_hwnd is not None:
            if cls.foreground() != expected_hwnd:
                raise SafetyError("game window lost foreground during pre-wheel hover")
            if cls.f12_down():
                raise KeyboardInterrupt("F12 before wheel")
        cls.user32.mouse_event(cls.MOUSEEVENTF_WHEEL, 0, 0, ctypes.c_uint32(delta).value, 0)

    @classmethod
    def f12_down(cls) -> bool:
        return bool(cls.user32.GetAsyncKeyState(VK_F12) & 0x8000)
