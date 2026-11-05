"""Live F12/resume/mutex checks on an already completed account; no mouse input."""
from __future__ import annotations

import argparse
import json
from pathlib import Path
import threading
import time

from hyol_scheduler import ForegroundRuntimeLease, Win32

from .engine import DarumaRunner
from .journal import atomic_json, stamp
from .runtime import Desktop, RUNTIME
from .vision import Pending


class ZeroOnlyDesktop(Desktop):
    def pulse(self, *args, **kwargs):
        raise Pending('安全验收禁止任何鼠标点击')

    def move(self, *args, **kwargs):
        raise Pending('安全验收禁止移动鼠标')


def check(hwnd, observation):
    observed = json.loads(Path(observation).read_text(encoding='utf-8'))
    if not (observed['page'] == 'weekend' and observed['remaining'] == 0
            and observed['content_clicks'] == 0 and observed['binding']['hwnd'] == hwnd):
        raise ValueError('必须使用同一账号已完成、零点击的活动页观察')
    report = dict(status='pending', started_at=stamp(), observation=str(observation), checks={})
    output = RUNTIME/'verification/safety_live.json'
    options = dict(root=RUNTIME/'verification/safety_runtime',
                   expected_binding=observed['binding'], desktop_factory=ZeroOnlyDesktop)

    def confirmed_zero(runner):
        frame = runner.desktop.capture()
        if not (runner.desktop.vision.page(frame) == 'weekend'
                and runner.desktop.vision.digit(frame, 'reward') == 0
                and runner.desktop.vision.reward_completed(frame)):
            raise Pending('当前画面未正向核实为奖励0，停止安全验收')

    def press_f12(row):
        if row['event'] != 'bound':
            return
        confirmed_zero(first)
        try:
            Win32.user32.keybd_event(0x7B, 0, 0, 0)
            time.sleep(.065)
        finally:
            Win32.user32.keybd_event(0x7B, 0, 2, 0)

    first = DarumaRunner(hwnd, sink=press_f12, **options)
    stopped = first.run()
    assert stopped['status'] == 'stopped' and stopped['content_clicks'] == 0, stopped
    assert first.stop.is_set() and first.journal.data['intent'] is None
    assert not (Win32.user32.GetAsyncKeyState(1) & 0x8000)
    report['checks']['f12'] = stopped
    atomic_json(output, report)

    second = DarumaRunner(hwnd, **options)
    resumed = second.run()
    assert resumed['status'] == 'done' and resumed['content_clicks'] == 0, resumed
    assert resumed['checkpoint'] == stopped['checkpoint']
    report['checks']['zero_input_resume'] = resumed

    ready, release = threading.Event(), threading.Event()
    ownership = []

    def hold_lease():
        lease = ForegroundRuntimeLease()
        try:
            ownership.append(lease.acquire())
            ready.set()
            release.wait(10)
        finally:
            lease.release()

    holder = threading.Thread(target=hold_lease, daemon=True)
    holder.start()
    try:
        assert ready.wait(3) and ownership == [True], '测试输入锁未能取得；未执行碰撞测试'
        occupied = DarumaRunner(hwnd, **options).run()
        assert occupied['status'] == 'pending' and occupied['content_clicks'] == 0, occupied
        assert '等待其他脚本' in occupied['reason']
        report['checks']['occupied_lock'] = occupied
    finally:
        release.set()
        holder.join(3)
    lease = ForegroundRuntimeLease()
    try:
        assert lease.acquire(), '停止后输入锁未释放'
        report['checks']['lock_released'] = True
    finally:
        lease.release()
    report.update(status='pass', completed_at=stamp(), content_clicks=0)
    atomic_json(output, report)
    return report


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--hwnd', type=int, required=True)
    parser.add_argument('--observation', type=Path, required=True)
    args = parser.parse_args()
    print(json.dumps(check(args.hwnd, args.observation), ensure_ascii=False))


if __name__ == '__main__':
    main()
