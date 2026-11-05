"""Double-click current-account weekend activity window."""
from __future__ import annotations

import queue
import threading
import tkinter as tk
from tkinter import ttk

from hyol_daily_tasks import DailyRunner
from hyol_weekend import is_weekend
from tools.preflight_live import run as preflight_live


class WeekendApp(tk.Tk):
    def __init__(self):
        super().__init__()
        self.title('火影OL周末任务')
        self.geometry('600x360')
        self.resizable(False, False)
        self.events: queue.Queue[tuple[str, object]] = queue.Queue()
        self.worker: threading.Thread | None = None
        self.status = tk.StringVar(value='自动识别当前账号的周末活动；仅周六、周日运行。')
        box = ttk.Frame(self, padding=18)
        box.pack(fill='both', expand=True)
        ttk.Label(box, text='周末任务', font=('Microsoft YaHei UI', 17, 'bold')).pack(anchor='w')
        ttk.Label(box, text='自动识别：忍者拼图（三个难度）＋达摩挑战。').pack(anchor='w', pady=(9, 3))
        ttk.Label(box, text='每天完成简单、普通、困难并领奖，达摩奖励归零及回村后才完成。F12 急停。',
                  wraplength=550).pack(anchor='w')
        self.start_button = ttk.Button(box, text='开始 / 继续', command=self.start)
        self.start_button.pack(anchor='w', pady=(15, 7))
        ttk.Label(box, textvariable=self.status, wraplength=550).pack(anchor='w')
        self.log = tk.Text(box, height=9, width=69, state='disabled')
        self.log.pack(fill='both', expand=True, pady=(8, 0))
        self.after(200, self.drain)
        self.protocol('WM_DELETE_WINDOW', self.close)

    def append(self, line: str) -> None:
        self.log.configure(state='normal')
        self.log.insert('end', line + '\n')
        self.log.see('end')
        self.log.configure(state='disabled')

    def start(self) -> None:
        if self.worker and self.worker.is_alive():
            return
        self.start_button.configure(state='disabled')
        self.worker = threading.Thread(target=self.run_worker, daemon=False)
        self.worker.start()

    def run_worker(self) -> None:
        try:
            if not is_weekend():
                self.events.put(('finished', '今天不是周六或周日；未识别游戏，周末任务已跳过。'))
                return
            check = preflight_live()
            if check.get('status') != 'ready' or check.get('healthy') is not True or check.get('content_clicks') != 0:
                self.events.put(('finished', f'游戏预检未通过：{check.get("reason", check.get("status"))}'))
                return
            self.events.put(('status', '预检通过，核实当前账号、三个拼图难度及达摩次数。'))
            runner = DailyRunner('weekend', generic_current_window=True,
                                 event_sink=lambda event: self.events.put(('event', event)))
            result = runner.run()
            self.events.put(('finished', '三个拼图难度已领奖、达摩归零并已回村。' if result == 0
                             else '周末任务待续；保留未决拼图、领奖及达摩断点。'))
        except Exception as exc:
            self.events.put(('finished', f'运行待处理：{type(exc).__name__}: {exc}'))

    def drain(self) -> None:
        try:
            while True:
                kind, payload = self.events.get_nowait()
                if kind == 'event':
                    event = payload
                    name = str(event.get('event', ''))
                    if name.startswith(('puzzle_', 'daruma_', 'weekend_', 'daily_task_')):
                        self.append(f'{name}: {event.get("detail", event.get("remaining", ""))}')
                elif kind == 'finished':
                    self.status.set(str(payload))
                    self.append(str(payload))
                    self.start_button.configure(state='normal')
                else:
                    self.status.set(str(payload))
        except queue.Empty:
            pass
        self.after(200, self.drain)

    def close(self) -> None:
        if self.worker and self.worker.is_alive():
            self.status.set('任务正在运行；按 F12 保存断点后再关闭。')
            return
        self.destroy()


if __name__ == '__main__':
    WeekendApp().mainloop()
