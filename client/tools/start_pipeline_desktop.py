"""Operate reviewed controls on an owned, actual desktop client window."""
from pathlib import Path
import ctypes
import os
import sys
import time

from PIL import ImageGrab

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import hyol_scheduler as hs
from hyol_checkpoints import read_checkpoint
from hyol_diagnostics import manual_pause_status
from hyol_runtime import VERSION

REPORT = hs.LOGS/('pipeline_native_started_'+VERSION+'.json')


def main():
    if sys.argv[1:] == ['--start']:
        report = read_checkpoint(REPORT)
        hwnd = report['hwnd']
        if (hs.Win32.pid(hwnd) != report['pid'] or hs.Win32.title(hwnd) != report['title']
                or hs.Win32.rect(hwnd) != tuple(report['rect'])):
            raise hs.SafetyError('已检查客户端窗口改变')
        lease = hs.ForegroundRuntimeLease()
        if manual_pause_status(hs.ROOT).get('paused') or not lease.acquire():
            raise hs.PendingError('人工暂停或已有前台任务')
        lease.release()
        hs.Win32.activate(hwnd)
        if hs.Win32.foreground() != hwnd:
            raise hs.SafetyError('已检查客户端未在前台')
        x, y = report['rect'][0]+82, report['rect'][1]+514
        report['inputs'].append(dict(at=hs.now_iso(), control='开始 / 继续', x=x, y=y))
        hs.atomic_json(REPORT, report)
        hs.Win32.click(x, y)
        print('actual desktop start pressed')
        return
    desktop = Path(os.environ['USERPROFILE'])/'Desktop'
    exe = desktop/'可用脚本/火影OL完整客户端/火影OL完整客户端.exe'
    title = '火影OL 完整客户端 '+VERSION
    found = []
    callback_type = ctypes.WINFUNCTYPE(ctypes.c_bool, ctypes.c_void_p, ctypes.c_void_p)

    def collect(hwnd, unused):
        if hs.Win32.user32.IsWindowVisible(hwnd) and hs.Win32.title(hwnd) == title:
            if Path(hs.Win32.process_path(hs.Win32.pid(hwnd))).resolve() == exe.resolve():
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
    rect = hs.Win32.rect(hwnd)
    before = hs.LOGS/('pipeline_native_review_'+VERSION+'.png')
    ImageGrab.grab(bbox=rect, all_screens=True).save(before)
    report = dict(at=hs.now_iso(), version=VERSION, hwnd=hwnd, pid=hs.Win32.pid(hwnd),
                  title=title, exe=str(exe), rect=rect, before=str(before), inputs=[])
    hs.atomic_json(REPORT, report)
    print(str(before))


if __name__ == '__main__':
    main()
