"""Control the supplied QuickMacro solver through its verified native controls.

The original x86 executable is retained byte-for-byte. Its encrypted script is
not represented as recovered Python source. No global F9/F10/F12 is synthesized.
Every delegated run is contained in a Windows job, including Runner.exe.
"""
from __future__ import annotations

import ctypes
from ctypes import wintypes as wt
import hashlib
from pathlib import Path
import struct
import threading
import time
from PIL import Image, ImageGrab

import hyol_scheduler as hs


BACKEND_NAME = '拼图 忍术训练V2.1.exe'
BACKEND_SHA256 = 'ffa2ca6cd1eb8c3cc4185bd8cd52b5d85e3d9ad6864d779e1295a548db3425be'
SOURCE_PATH = Path(r'C:\Users\Administrator\Desktop\可用脚本') / BACKEND_NAME
REQUIRED = ('启动', '中止', '保存设置', '拼图', '忍术修炼', '自动识别')


def backend_path():
    return hs.ASSETS / 'puzzle_backend' / BACKEND_NAME


def verify_executable(path):
    path = Path(path)
    if not path.is_file() or hashlib.sha256(path.read_bytes()).hexdigest() != BACKEND_SHA256:
        raise hs.PendingError('拼图自动解题程序缺失或版本校验失败；未启动游戏')
    return path.resolve()


def require_account_tab(scheduler, account, frame):
    """A puzzle page can hide identity; it cannot replace the selected-tab lease."""
    expected = account.tab_x
    if not expected:
        expected = (getattr(scheduler, 'generic_current_tab_x', None)
                    or getattr(scheduler, '_connection_tab_x', None))
    if not expected or scheduler._generic_active_tab_x(frame) != expected:
        raise hs.SafetyError('拼图期间选中的账号标签发生变化，停止操作并保留原局')


class NativeApi:
    """Small, bounded Win32 surface; injectable for lifecycle/safety tests."""

    def __init__(self):
        self.u, self.k = ctypes.windll.user32, ctypes.windll.kernel32
        self.callback = ctypes.WINFUNCTYPE(wt.BOOL, wt.HWND, wt.LPARAM)
        self.u.GetWindowTextW.argtypes = [wt.HWND, wt.LPWSTR, ctypes.c_int]
        self.u.GetClassNameW.argtypes = [wt.HWND, wt.LPWSTR, ctypes.c_int]
        self.u.GetParent.argtypes = [wt.HWND]
        self.u.GetParent.restype = wt.HWND
        self.u.GetWindowThreadProcessId.argtypes = [wt.HWND, ctypes.POINTER(wt.DWORD)]
        self.u.EnumWindows.argtypes = [self.callback, wt.LPARAM]
        self.u.EnumChildWindows.argtypes = [wt.HWND, self.callback, wt.LPARAM]
        self.u.GetDlgCtrlID.argtypes = [wt.HWND]
        self.u.IsWindow.argtypes = [wt.HWND]
        self.u.IsWindowEnabled.argtypes = [wt.HWND]
        self.u.ShowWindow.argtypes = [wt.HWND, ctypes.c_int]
        self.u.SendMessageTimeoutW.argtypes = [wt.HWND, wt.UINT, wt.WPARAM, wt.LPARAM,
                                               wt.UINT, wt.UINT, ctypes.POINTER(ctypes.c_size_t)]
        self.k.OpenProcess.argtypes = [wt.DWORD, wt.BOOL, wt.DWORD]
        self.k.OpenProcess.restype = wt.HANDLE
        self.k.CloseHandle.argtypes = [wt.HANDLE]
        self.k.QueryFullProcessImageNameW.argtypes = [wt.HANDLE, wt.DWORD, wt.LPWSTR,
                                                     ctypes.POINTER(wt.DWORD)]
        self.k.CreateJobObjectW.argtypes = [ctypes.c_void_p, wt.LPCWSTR]
        self.k.CreateJobObjectW.restype = wt.HANDLE
        self.k.SetInformationJobObject.argtypes = [wt.HANDLE, ctypes.c_int, ctypes.c_void_p, wt.DWORD]
        self.k.AssignProcessToJobObject.argtypes = [wt.HANDLE, wt.HANDLE]
        self.k.IsProcessInJob.argtypes = [wt.HANDLE, wt.HANDLE, ctypes.POINTER(wt.BOOL)]
        self.k.TerminateJobObject.argtypes = [wt.HANDLE, wt.UINT]
        self.k.TerminateProcess.argtypes = [wt.HANDLE, wt.UINT]
        self.k.WaitForSingleObject.argtypes = [wt.HANDLE, wt.DWORD]
        self.k.WaitForSingleObject.restype = wt.DWORD
        self.k.QueryInformationJobObject.argtypes = [wt.HANDLE, ctypes.c_int, ctypes.c_void_p,
                                                     wt.DWORD, ctypes.POINTER(wt.DWORD)]
        self.k.CreateToolhelp32Snapshot.argtypes = [wt.DWORD, wt.DWORD]
        self.k.CreateToolhelp32Snapshot.restype = wt.HANDLE

    def send(self, hwnd, message, wparam=0, lparam=0):
        result = ctypes.c_size_t()
        if not self.u.SendMessageTimeoutW(hwnd, message, wparam, lparam, 2, 750,
                                          ctypes.byref(result)):
            raise hs.PendingError('拼图程序控件无响应；保留当前游戏')
        return ctypes.c_ssize_t(result.value).value

    def window_text(self, hwnd):
        text = ctypes.create_unicode_buffer(2048)
        self.u.GetWindowTextW(hwnd, text, len(text))
        return text.value

    def process_path(self, pid):
        handle = self.k.OpenProcess(0x1000, False, pid)
        if not handle:
            return ''
        try:
            text, size = ctypes.create_unicode_buffer(32768), wt.DWORD(32768)
            if not self.k.QueryFullProcessImageNameW(handle, 0, text, ctypes.byref(size)):
                return ''
            return text.value
        finally:
            self.k.CloseHandle(handle)

    def describe(self, hwnd):
        name, pid = ctypes.create_unicode_buffer(128), wt.DWORD()
        self.u.GetClassNameW(hwnd, name, len(name))
        self.u.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
        result = dict(hwnd=int(hwnd), pid=pid.value, text=self.window_text(hwnd),
                      kind=name.value, identifier=self.u.GetDlgCtrlID(hwnd),
                      enabled=bool(self.u.IsWindowEnabled(hwnd)))
        if name.value == 'Button':
            result['checked'] = self.send(hwnd, 0xF0)
        if name.value == 'ComboBox':
            result['selection'] = self.send(hwnd, 0x147)
            result['items'] = []
            count = self.send(hwnd, 0x146)
            for index in range(max(0, min(30, count))):
                text = ctypes.create_unicode_buffer(256)
                self.send(hwnd, 0x148, index, ctypes.addressof(text))
                result['items'].append(text.value)
        return result

    def children(self, hwnd):
        items = []
        @self.callback
        def each(child, _):
            row = self.describe(child)
            if row['kind'] in ('Button', 'ComboBox'):
                items.append(row)
            return True
        self.u.EnumChildWindows(hwnd, each, 0)
        return items

    def windows(self):
        items = []
        @self.callback
        def each(hwnd, _):
            if '拼图 忍术训练V2.1' in self.window_text(hwnd):
                row = self.describe(hwnd)
                controls = self.children(hwnd)
                if all(any(c['text'] == label for c in controls) for label in REQUIRED):
                    row['controls'] = controls
                    row['path'] = self.process_path(row['pid'])
                    items.append(row)
            return True
        self.u.EnumWindows(each, 0)
        return items

    def process_tree(self, root_pid):
        class Entry(ctypes.Structure):
            _fields_ = [('size', wt.DWORD), ('usage', wt.DWORD), ('pid', wt.DWORD),
                        ('heap', ctypes.c_size_t), ('module', wt.DWORD), ('threads', wt.DWORD),
                        ('parent', wt.DWORD), ('priority', wt.LONG), ('flags', wt.DWORD),
                        ('exe', wt.WCHAR * 260)]
        snapshot = self.k.CreateToolhelp32Snapshot(2, 0)
        if snapshot == ctypes.c_void_p(-1).value:
            raise hs.PendingError('拼图程序的子进程归属无法核实')
        parents = {}
        try:
            entry = Entry(size=ctypes.sizeof(Entry))
            self.k.Process32FirstW.argtypes = [wt.HANDLE, ctypes.POINTER(Entry)]
            self.k.Process32NextW.argtypes = [wt.HANDLE, ctypes.POINTER(Entry)]
            ok = self.k.Process32FirstW(snapshot, ctypes.byref(entry))
            while ok:
                parents[entry.pid] = entry.parent
                ok = self.k.Process32NextW(snapshot, ctypes.byref(entry))
        finally:
            self.k.CloseHandle(snapshot)
        members = {root_pid}
        while True:
            expanded = members | {pid for pid, parent in parents.items() if parent in members}
            if expanded == members:
                return members
            members = expanded

    def create_job(self):
        class Basic(ctypes.Structure):
            _fields_ = [('process_time', ctypes.c_int64), ('job_time', ctypes.c_int64),
                        ('flags', wt.DWORD), ('min_working', ctypes.c_size_t),
                        ('max_working', ctypes.c_size_t), ('process_limit', wt.DWORD),
                        ('affinity', ctypes.c_size_t), ('priority', wt.DWORD),
                        ('scheduling', wt.DWORD)]
        class Io(ctypes.Structure):
            _fields_ = [(name, ctypes.c_uint64) for name in
                        ('read_ops', 'write_ops', 'other_ops', 'read_bytes', 'write_bytes', 'other_bytes')]
        class Extended(ctypes.Structure):
            _fields_ = [('basic', Basic), ('io', Io), ('process_memory', ctypes.c_size_t),
                        ('job_memory', ctypes.c_size_t), ('peak_process', ctypes.c_size_t),
                        ('peak_job', ctypes.c_size_t)]
        job = self.k.CreateJobObjectW(None, None)
        limits = Extended()
        limits.basic.flags = 0x2000  # JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE
        if not job or not self.k.SetInformationJobObject(job, 9, ctypes.byref(limits), ctypes.sizeof(limits)):
            if job:
                self.k.CloseHandle(job)
            raise hs.PendingError('拼图程序进程隔离失败，未启动解题')
        return job

    def assign(self, job, pid):
        handle = self.k.OpenProcess(0x100 | 0x1 | 0x1000, False, pid)
        if not handle:
            raise hs.PendingError('无法控制拼图程序的完整进程树')
        try:
            if not self.k.AssignProcessToJobObject(job, handle):
                raise hs.PendingError('拼图程序子进程无法纳入急停，未启动解题')
        finally:
            self.k.CloseHandle(handle)

    def contained(self, job, pid):
        handle = self.k.OpenProcess(0x1000, False, pid)
        if not handle:
            return False
        try:
            included = wt.BOOL()
            return bool(self.k.IsProcessInJob(handle, job, ctypes.byref(included)) and included.value)
        finally:
            self.k.CloseHandle(handle)

    def spawn(self, executable, job):
        class Startup(ctypes.Structure):
            _fields_ = [('size', wt.DWORD), ('reserved', wt.LPWSTR), ('desktop', wt.LPWSTR),
                        ('title', wt.LPWSTR), ('x', wt.DWORD), ('y', wt.DWORD),
                        ('width', wt.DWORD), ('height', wt.DWORD), ('x_chars', wt.DWORD),
                        ('y_chars', wt.DWORD), ('fill', wt.DWORD), ('flags', wt.DWORD),
                        ('show', wt.WORD), ('reserved_size', wt.WORD), ('reserved_data', ctypes.c_void_p),
                        ('stdin', wt.HANDLE), ('stdout', wt.HANDLE), ('stderr', wt.HANDLE)]
        class Process(ctypes.Structure):
            _fields_ = [('process', wt.HANDLE), ('thread', wt.HANDLE), ('pid', wt.DWORD), ('tid', wt.DWORD)]
        startup, process = Startup(), Process()
        startup.size, startup.flags, startup.show = ctypes.sizeof(Startup), 1, 0
        command = ctypes.create_unicode_buffer('"' + str(executable) + '"')
        self.k.CreateProcessW.argtypes = [wt.LPCWSTR, wt.LPWSTR, ctypes.c_void_p, ctypes.c_void_p,
                                          wt.BOOL, wt.DWORD, ctypes.c_void_p, wt.LPCWSTR,
                                          ctypes.POINTER(Startup), ctypes.POINTER(Process)]
        if not self.k.CreateProcessW(str(executable), command, None, None, False, 4, None,
                                    str(executable.parent), ctypes.byref(startup), ctypes.byref(process)):
            raise hs.PendingError('拼图解题程序启动失败')
        try:
            if not self.k.AssignProcessToJobObject(job, process.process):
                self.k.TerminateProcess(process.process, 2)
                raise hs.PendingError('拼图进程启动前隔离失败；未执行解题')
            self.k.ResumeThread.argtypes = [wt.HANDLE]
            self.k.ResumeThread(process.thread)
            return process.pid
        finally:
            self.k.CloseHandle(process.thread)
            self.k.CloseHandle(process.process)

    def click(self, control):
        if not self.u.IsWindow(control['hwnd']) or self.window_text(control['hwnd']) != control['text']:
            raise hs.PendingError('拼图程序控件发生变化，停止发送命令')
        if control['text'] in ('拼图', '忍术修炼', '自动识别'):
            self.send(control['hwnd'], 0xF5)  # BM_CLICK updates both check state and script variables.
        else:
            parent = self.u.GetParent(control['hwnd'])
            self.send(parent, 0x111, control['identifier'], control['hwnd'])  # WM_COMMAND / BN_CLICKED

    def hide(self, hwnd):
        self.u.ShowWindow(hwnd, 0)

    def geometry(self, hwnd):
        return hs.GameGeometry(hs.Win32.rect(hwnd), hs.Win32.game_content_rect(hwnd))

    def chrome(self, geometry):
        left, top, right, _ = geometry.window
        raw = ImageGrab.grab(bbox=(left, top, right, geometry.content[1]), all_screens=True)
        return raw.convert('RGB').resize((1920, 69), Image.Resampling.BILINEAR)

    def job_members(self, job):
        buffer = ctypes.create_string_buffer(65536)
        size = wt.DWORD()
        if not self.k.QueryInformationJobObject(job, 3, buffer, len(buffer), ctypes.byref(size)):
            raise hs.PendingError('无法核实拼图急停进程清单；禁止切换到下一账号')
        assigned, listed = struct.unpack_from('<II', buffer)
        if assigned != listed or 8 + listed * ctypes.sizeof(ctypes.c_size_t) > len(buffer):
            raise hs.PendingError('拼图急停进程清单不完整；禁止切换到下一账号')
        return list((ctypes.c_size_t * listed).from_buffer(buffer, 8))

    def kill(self, job):
        handles, members = [], []
        try:
            try:
                members = self.job_members(job)
                for pid in members:
                    handle = self.k.OpenProcess(0x100000 | 0x1000, False, pid)
                    if handle:
                        handles.append((pid, handle))
                    elif self.process_path(pid):
                        raise hs.PendingError('无法核实解题进程已退出；禁止切换账号')
            finally:
                # Closing the final job handle also kills members if termination fails.
                self.k.TerminateJobObject(job, 2)
                self.k.CloseHandle(job)
                self.u.mouse_event(4, 0, 0, 0, 0)
            deadline = time.monotonic() + 2
            for _, handle in handles:
                milliseconds = max(0, round((deadline-time.monotonic())*1000))
                if self.k.WaitForSingleObject(handle, milliseconds) != 0:
                    raise hs.PendingError('拼图解题进程急停尚未确认；禁止切换账号')
            return dict(members=members, all_exited=True, mouse_released=True)
        finally:
            for _, handle in handles:
                self.k.CloseHandle(handle)


class PuzzleScriptSession:
    def __init__(self, scheduler, account, *, api=None, executable=None):
        self.s, self.account = scheduler, account
        self.api = api or NativeApi()
        self.executable = executable or backend_path()
        self.job = None
        self.window = None
        self.started = False
        self.geometry = None
        self.abort_reason = None
        self.close_error = None
        self.cleanup = None
        self.finished = threading.Event()
        self.lock = threading.Lock()

    def _permit(self):
        self.s.check_input_pause()
        if self.s.no_content_clicks:
            raise hs.PendingError('本次调用禁止内容操作，未启动拼图程序')

    def _controls(self):
        items = self.api.children(self.window['hwnd'])
        mapping = {}
        for label in REQUIRED:
            matched = [item for item in items if item['text'] == label and item['kind'] == 'Button']
            if len(matched) != 1:
                raise hs.PendingError('拼图程序控件无法唯一识别：' + label)
            mapping[label] = matched[0]
        if any(not self.api.contained(self.job, control['pid']) for control in mapping.values()):
            raise hs.PendingError('拼图运行控件未全部纳入急停进程树')
        return mapping, items

    def __enter__(self):
        try:
            self._permit()
            frame = self.s.content_guard(self.account, ['puzzle_game_anchor.bmp'], anchor_threshold=.95)
            require_account_tab(self.s, self.account, frame)
            if not self.s.template('puzzle_game_start.bmp', hs.VIEW, frame, threshold=.95)[0]:
                raise hs.PendingError('必须先进入开始拼图的界面；未启动或接管原程序')
            self.executable = verify_executable(self.executable)
            existing = self.api.windows()
            trusted = [row for row in existing if Path(row['path']).resolve() in
                       (self.executable, SOURCE_PATH.resolve())]
            if len(trusted) != len(existing):
                # An unknown copy can still click the same shared game surface.
                # Never start a second solver while its input ownership is unclear.
                raise hs.PendingError('发现来源不明的拼图脚本窗口；请先关闭原程序，避免多个脚本同时控制游戏')
            if len(trusted) > 1:
                raise hs.PendingError('存在多个拼图程序窗口，无法确定解题控制权')
            self.job = self.api.create_job()
            if trusted:
                self.window = trusted[0]
                verify_executable(self.window['path'])
                tree = self.api.process_tree(self.window['pid'])
                if any(item['pid'] not in tree for item in self.window['controls']):
                    raise hs.PendingError('拼图控件的进程归属不明，未接管其他脚本')
                # Assign the parent first so any new descendants inherit the job.
                for pid in [self.window['pid']] + sorted(tree-{self.window['pid']}):
                    self._permit()
                    self.api.assign(self.job, pid)
            else:
                pid = self.api.spawn(self.executable, self.job)
                deadline = time.monotonic() + 12
                while time.monotonic() < deadline:
                    self._permit()
                    matched = [row for row in self.api.windows() if row['pid'] == pid]
                    if len(matched) == 1:
                        self.window = matched[0]
                        break
                    self.s.wait_interruptible(.1)
                if not self.window:
                    raise hs.PendingError('拼图自动识别控件加载超时，未启动解题')
            controls, _ = self._controls()
            self._permit()
            self.api.click(controls['中止'])
            for label in ('拼图', '自动识别'):
                self._permit()
                controls, _ = self._controls()
                if controls[label]['checked'] != 1:
                    self.api.click(controls[label])
            controls, items = self._controls()
            if (controls['拼图']['checked'] != 1 or controls['忍术修炼']['checked'] != 0
                    or controls['自动识别']['checked'] != 1):
                raise hs.PendingError('拼图/自动识别选项未确认；未启动游戏')
            grids = [item for item in items if item['kind'] == 'ComboBox'
                     and item.get('items') == ['4×4', '5×5', '6×6']]
            if len(grids) != 1 or grids[0]['enabled']:
                raise hs.PendingError('自动识别未接入难度选择，未启动解题')
            self._permit()
            self.api.click(controls['保存设置'])
            self.api.hide(self.window['hwnd'])
            self.s.event('puzzle_script_configured', self.account, mode='拼图', automatic=True,
                         executable_sha256=BACKEND_SHA256, pid=self.window['pid'],
                         content_clicks=0, input_scope='native_script_settings')
            threading.Thread(target=self._watch_stop, name='PuzzleScriptStop', daemon=True).start()
            return self
        except BaseException:
            self.close()
            raise

    def _watch_stop(self):
        next_tab_check = 0.
        while not self.finished.wait(.025):
            try:
                if self.s.stop.is_set():
                    self.abort_reason = 'F12或客户端停止'
                elif self.started:
                    if hs.Win32.foreground() != self.s.hwnd:
                        raise hs.SafetyError('拼图解题期间游戏失去前台焦点，已停止原程序')
                    if time.monotonic() >= next_tab_check:
                        if self.api.geometry(self.s.hwnd) != self.geometry:
                            raise hs.SafetyError('拼图解题期间窗口区域发生变化，已停止原程序')
                        require_account_tab(self.s, self.account, self.api.chrome(self.geometry))
                        next_tab_check = time.monotonic() + .1
            except BaseException as exc:
                self.abort_reason = str(exc)
            if self.abort_reason:
                try:
                    self.close()
                except BaseException:
                    pass  # Report the saved cleanup error on the owning worker.
                return

    def check_running(self):
        self.s.check_input_pause()
        if self.close_error:
            raise self.close_error
        if self.abort_reason:
            raise hs.SafetyError(self.abort_reason)

    def start(self):
        self._permit()
        hs.Win32.activate(self.s.hwnd)
        frame = self.s.content_guard(self.account, ['puzzle_game_anchor.bmp'], anchor_threshold=.95)
        require_account_tab(self.s, self.account, frame)
        if not self.s.template('puzzle_game_start.bmp', hs.VIEW, frame, threshold=.95)[0]:
            raise hs.PendingError('必须先进入开始拼图的界面；未发送启动命令')
        controls, _ = self._controls()
        self.geometry = self.api.geometry(self.s.hwnd)
        self.check_running()
        self.started = True
        self.s.external_input_sessions = getattr(self.s, 'external_input_sessions', 0) + 1
        self.s.external_input_count = None
        self.s.event('puzzle_script_started', self.account, pid=self.window['pid'],
                     external_input_count=None, native_control='启动', automatic=True)
        self.api.click(controls['启动'])

    def __exit__(self, *_):
        self.close()

    def close(self):
        with self.lock:
            try:
                if self.job is not None:
                    job, self.job = self.job, None
                    try:
                        self.cleanup = self.api.kill(job)
                    except BaseException as exc:
                        self.close_error = hs.LeaseBlocked('原拼图进程退出未确认，停止账号轮转：'+str(exc))
                if self.close_error:
                    raise self.close_error
            finally:
                self.finished.set()
