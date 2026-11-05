"""Open the installed client and save its owned window for manual review.

No game input or task start. Each review has a new receipt and screenshot.
"""
from pathlib import Path
import ctypes
import hashlib
import os
import sys
import time
from PIL import ImageGrab

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import hyol_scheduler as hs
from hyol_runtime import VERSION
from tools.release_runtime_snapshot import default_desktop


def main():
    desktop = default_desktop()
    exe = desktop/'可用脚本/火影OL完整客户端/火影OL完整客户端.exe'
    title = '火影OL 完整客户端 '+VERSION
    found = []
    callback_type = ctypes.WINFUNCTYPE(ctypes.c_bool, ctypes.c_void_p, ctypes.c_void_p)

    def collect(hwnd, unused):
        if (hs.Win32.user32.IsWindowVisible(hwnd) and hs.Win32.title(hwnd) == title
                and Path(hs.Win32.process_path(hs.Win32.pid(hwnd))).resolve() == exe.resolve()):
            found.append(int(hwnd))
        return True

    callback = callback_type(collect)
    hs.Win32.user32.EnumWindows(callback, 0)
    if found:
        raise hs.PendingError('已有完整客户端窗口，不另开第二个')
    os.startfile(str(desktop/'火影OL完整客户端.lnk'))
    deadline = time.monotonic()+20
    while time.monotonic() < deadline:
        found.clear()
        hs.Win32.user32.EnumWindows(callback, 0)
        if len(found) == 1:
            break
        time.sleep(.25)
    else:
        raise hs.PendingError('实际桌面客户端未唯一出现')
    hwnd = found[0]
    hs.Win32.activate(hwnd)
    time.sleep(.5)
    if hs.Win32.foreground() != hwnd:
        raise hs.SafetyError('客户端未在前台')
    identifier = hs.run_id()
    path = hs.LOGS/('owned_desktop_review_'+identifier+'.json')
    screenshot = path.with_suffix('.png')
    rect = hs.Win32.rect(hwnd)
    ImageGrab.grab(bbox=rect, all_screens=True).save(screenshot)
    hs.atomic_json(path, dict(at=hs.now_iso(), version=VERSION, hwnd=hwnd,
        pid=hs.Win32.pid(hwnd), title=title, exe=str(exe), rect=rect, before=str(screenshot),
        executable_sha256=hashlib.sha256(exe.read_bytes()).hexdigest(), inputs=[], game_clicks=0))
    print(path)
    print(screenshot)


if __name__ == '__main__':
    main()
