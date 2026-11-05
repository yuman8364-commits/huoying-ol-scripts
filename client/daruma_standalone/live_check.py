"""Native GUI acceptance against one explicitly observed test account."""
from __future__ import annotations

import argparse
import ctypes
from datetime import datetime
import hashlib
import json
from pathlib import Path
import time

from PIL import ImageGrab
from hyol_scheduler import Win32

from .gui import DarumaApp
from . import VERSION
from .journal import atomic_json, stamp
from .runtime import RUNTIME


class LiveCheckApp(DarumaApp):
    def __init__(self, observation, hwnd, resume_report=None):
        source = json.loads(Path(observation).read_text(encoding='utf-8'))
        if source['binding']['hwnd'] != hwnd or source.get('content_clicks') != 0:
            raise ValueError('本轮必须关联同一明确窗口的零点击观察')
        super().__init__(expected_binding=source['binding'])
        self.began = time.perf_counter()
        self.stage = 'starting'
        self.target_checkpoint = None
        self.previous = RUNTIME/'runs/20261017_213006_569069_81784f/checkpoint.json'
        self.previous_hash = hashlib.sha256(self.previous.read_bytes()).hexdigest() if self.previous.exists() else None
        self.report = dict(status='running', stage=self.stage, started_at=stamp(), version=VERSION,
            observation=str(Path(observation).resolve()), expected_binding_key=source['binding']['key'],
            stop_and_resume=False, previous_checkpoint_sha256=self.previous_hash)
        self.previous_elapsed = 0
        if resume_report:
            previous_report = json.loads(Path(resume_report).read_text(encoding='utf-8'))
            if (previous_report['expected_binding_key'] != source['binding']['key']
                    or not previous_report.get('stop_and_resume') or previous_report.get('status') != 'pending'):
                raise ValueError('只能续接同一账号已完成停止测试的待处理验收')
            checkpoint = Path(previous_report['checkpoint'])
            if RUNTIME.resolve() not in checkpoint.resolve().parents:
                raise ValueError('不能续接独立目录之外的断点')
            saved = json.loads(checkpoint.read_text(encoding='utf-8'))
            if saved['binding']['key'] != source['binding']['key']:
                raise ValueError('原验收断点账号不一致')
            self.target_checkpoint = str(checkpoint)
            self.previous_hash = previous_report['previous_checkpoint_sha256']
            self.previous_elapsed = previous_report['elapsed_seconds']
            self.report = {**previous_report, 'status': 'running', 'resumed_at': stamp(),
                           'resumed_from_report': str(Path(resume_report).resolve()),
                           'prior_stop_reason': previous_report.get('reason')}
            self.stage = 'resuming'
        self.report_path = RUNTIME/'verification'/('live_gui_'+datetime.now().strftime('%Y%m%d_%H%M%S')+'.json')
        self.publish()
        for i, row in enumerate(self.targets):
            if row['hwnd'] == hwnd:
                self.selector.current(i)
                break
        else:
            raise ValueError('指定测试窗口已消失')
        self.after(300, self.start_button.invoke)

    def publish(self):
        self.report.update(stage=self.stage, updated_at=stamp(),
                           elapsed_seconds=round(self.previous_elapsed+time.perf_counter()-self.began, 3))
        atomic_json(self.report_path, self.report)
        atomic_json(RUNTIME/'verification/new_account_live_latest.json', self.report)

    def after_event(self, kind, row):
        try:
            self.observe_event(kind, row)
        except Exception as exc:
            self.cancel.set()
            self.stage = 'pending'
            self.report.update(status='pending', reason=f'{type(exc).__name__}: {exc}')
            self.publish()
            self.after(1200, self.close)

    def observe_event(self, kind, row):
        if self.closing:
            self.stage = 'stopped'
            self.report.update(status='stopped', reason='用户关闭独立窗口')
            self.publish()
            return
        if kind == 'event' and row['event'] == 'bound':
            if row['binding_key'] != self.expected_binding['key']:
                raise RuntimeError('绑定到了错误的账号标签')
            if self.target_checkpoint is not None and row['checkpoint'] != self.target_checkpoint:
                raise RuntimeError('继续时没有恢复同一独立断点')
            self.target_checkpoint = row['checkpoint']
            self.report['checkpoint'] = self.target_checkpoint
            self.publish()
        if kind == 'event' and row['event'] == 'progress':
            self.report.update(progress={k:row.get(k) for k in ('phase', 'round', 'layers', 'chance', 'remaining')})
            if row['phase'] == 'layer_verified' and row['layers'] == 1 and self.stage == 'starting':
                self.stage = 'stopping'
                self.report['stop_requested_at'] = stamp()
                self.stop_button.invoke()
            self.publish()
        if kind != 'finished':
            return
        if self.stage == 'stopping' and row['status'] == 'stopped':
            saved = json.loads(Path(self.target_checkpoint).read_text(encoding='utf-8'))
            if saved['layers'] != 1 or saved.get('intent') is not None:
                raise RuntimeError('按钮停止后断点未停在已核实的第一层')
            self.report.update(stop_and_resume=True, stopped_layers=saved['layers'], stopped_at=stamp())
            self.stage = 'resuming'
            self.publish()
            self.after(300, self.start_button.invoke)
            return
        if row['status'] != 'done':
            self.stage = 'pending'
            self.report.update(status='pending', reason=row.get('reason'))
            self.publish()
            self.after(1200, self.close)
            return
        saved = json.loads(Path(self.target_checkpoint).read_text(encoding='utf-8'))
        starts = [a for a in saved['actions'] if a['kind'] == 'start' and a['outcome'] == 'started']
        hits = [a for a in saved['actions'] if a['kind'] == 'strike' and a['outcome'] == 'layer_hit']
        if not (saved['remaining'] == 0 and saved['zero_verified'] and saved['chance'] == 0
                and saved['round'] == 3 and len(starts) == 3 and len(hits) == 18
                and self.report['stop_and_resume'] and saved.get('intent') is None):
            raise RuntimeError('三局/18层/零奖励/停止续接证据未全部齐全')
        if self.previous_hash is not None:
            if hashlib.sha256(self.previous.read_bytes()).hexdigest() != self.previous_hash:
                raise RuntimeError('上一账号已完成的独立断点被修改')
        self.stage = 'done'
        self.report.update(status='pass', completed_at=stamp(), starts=len(starts), hits=len(hits),
                           zero_evidence=saved['zero_evidence'], previous_checkpoint_unchanged=True,
                           reason='剩余奖励已核实为0，停留活动界面')
        self.publish()
        self.after(300, self.capture_result)

    def capture_result(self):
        try:
            self.lift()
            self.focus_force()
            self.update()
            Win32.user32.GetAncestor.restype = ctypes.c_void_p
            hwnd = int(Win32.user32.GetAncestor(self.winfo_id(), 2))
            Win32.activate(hwnd)
            self.update()
            if Win32.foreground() != hwnd:
                raise RuntimeError('结果GUI未取得前台，视觉检查待补')
            self.after(220, lambda: self.capture_pixels(hwnd))
        except Exception as exc:
            self.report.update(gui_visual_status='pending', gui_reason=str(exc))
            self.publish()

    def capture_pixels(self, hwnd):
        try:
            if Win32.foreground() != hwnd:
                raise RuntimeError('结果截图前台变化，视觉检查待补')
            box = (self.winfo_rootx(), self.winfo_rooty(),
                   self.winfo_rootx()+self.winfo_width(), self.winfo_rooty()+self.winfo_height())
            screenshot = self.report_path.with_suffix('.png')
            ImageGrab.grab(bbox=box, all_screens=True).save(screenshot)
            if Win32.foreground() != hwnd:
                raise RuntimeError('结果截图前台变化，视觉检查待补')
            self.report.update(gui_screenshot=str(screenshot), gui_visual_status='captured')
        except Exception as exc:
            self.report.update(gui_visual_status='pending', gui_reason=str(exc))
        self.publish()


def main():
    parser = argparse.ArgumentParser(description='对已只读绑定的一个账号做独立GUI验收')
    parser.add_argument('--observation', type=Path, required=True)
    parser.add_argument('--hwnd', type=int, required=True)
    parser.add_argument('--resume-report', type=Path)
    args = parser.parse_args()
    LiveCheckApp(args.observation, args.hwnd, args.resume_report).mainloop()


if __name__ == '__main__':
    main()
