"""Capture the actual published client without starting a game worker."""
from pathlib import Path
import ctypes
import os
import sys
import time

from PIL import ImageGrab

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import hyol_scheduler as hs
from hyol_runtime import VERSION


def main():
    desktop = Path(os.environ['USERPROFILE'])/'Desktop'
    exe = desktop/'可用脚本/火影OL完整客户端/火影OL完整客户端.exe'
    title = '火影OL 完整客户端 '+VERSION
    previous = hs.Win32.foreground()
    old = set()
    found = []
    callback_type = ctypes.WINFUNCTYPE(ctypes.c_bool, ctypes.c_void_p, ctypes.c_void_p)

    def collect(hwnd, unused):
        if hs.Win32.user32.IsWindowVisible(hwnd) and hs.Win32.title(hwnd) == title:
            if Path(hs.Win32.process_path(hs.Win32.pid(hwnd))).resolve() == exe.resolve():
                found.append(int(hwnd))
        return True

    callback = callback_type(collect)
    hs.Win32.user32.EnumWindows(callback, 0)
    old.update(found)
    found.clear()
    os.startfile(str(desktop/'火影OL完整客户端.lnk'))
    deadline = time.monotonic()+20
    while time.monotonic() < deadline:
        found.clear()
        hs.Win32.user32.EnumWindows(callback, 0)
        fresh = [hwnd for hwnd in found if hwnd not in old]
        if len(fresh) == 1:
            break
        time.sleep(.25)
    else:
        raise hs.PendingError('实际桌面完整客户端未唯一出现')
    hwnd = fresh[0]
    try:
        hs.Win32.activate(hwnd)
        time.sleep(.5)
        if hs.Win32.foreground() != hwnd:
            raise hs.SafetyError('待核实客户端未在前台')
        rect = hs.Win32.rect(hwnd)
        evidence = hs.LOGS/('pipeline_desktop_'+VERSION+'.png')
        ImageGrab.grab(bbox=rect, all_screens=True).save(evidence)
        report = dict(at=hs.now_iso(), version=VERSION, hwnd=hwnd, pid=hs.Win32.pid(hwnd),
                      title=title, exe=str(exe), rect=rect, evidence=str(evidence),
                      inputs=[], game_task_started=False)
        hs.atomic_json(hs.LOGS/('pipeline_desktop_'+VERSION+'.json'), report)
        print(str(evidence))
    finally:
        # This HWND was opened by this probe and its title/path were verified.
        hs.Win32.user32.PostMessageW(hwnd, 0x0010, 0, 0)
        time.sleep(.3)
        if previous:
            hs.Win32.activate(previous)


if __name__ == '__main__':
    main()
