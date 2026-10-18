"""A small independent Tk window; UI callbacks never send game input."""
from __future__ import annotations

from pathlib import Path
import queue
import threading
import tkinter as tk
from tkinter import ttk

from . import VERSION
from .engine import DarumaRunner
from .runtime import RUNTIME, windows


PHASES = {'observe': '观察界面', 'adopted_round': '接手当前局', 'select_bottom': '定位最下层圆柱',
          'timing': '等待Perfect时机', 'playing': '挑战进行中', 'layer_verified': '已核实本层奖励',
          'round_finished': '本局已结束', 'reward_verified': '核对剩余奖励', 'ready': '准备下一局',
          'correcting': '调整时序与敲击方向', 'done': '奖励归零，已完成',
          'restart_round': '在当前挑战页直接开始下一局'}
PAGES = {'ready': '达摩开始页', 'playing': '达摩进行中', 'over': '达摩结算页',
         'weekend': '周末福利页', 'home': '家园', 'field': '场景页', 'menu': '活动菜单',
         'offline': '掉线提示', 'unknown': '未识别页面'}


class DarumaApp(tk.Tk):
    def __init__(self, *, runtime_root=RUNTIME, expected_binding=None):
        super().__init__()
        self.runtime_root = Path(runtime_root)
        self.expected_binding = expected_binding
        self.last_result = None
        self.title('火影OL · 达摩独立测试')
        self.geometry('720x520')
        self.minsize(660, 470)
        self.events = queue.Queue()
        self.worker = None
        self.cancel = threading.Event()
        self.closing = False
        self.targets = []
        self.status = tk.StringVar(value='先打开测试账号的达摩挑战页，再选择对应窗口。')
        self.layers = tk.StringVar(value='已获奖励：— / 6')
        self.chance = tk.StringVar(value='剩余机会：—')
        self.remaining = tk.StringVar(value='剩余奖励：—')
        self.checkpoint = tk.StringVar(value='')
        box = ttk.Frame(self, padding=18)
        box.pack(fill='both', expand=True)
        ttk.Label(box, text='达摩挑战 · 独立版', font=('Microsoft YaHei UI', 18, 'bold')).pack(anchor='w')
        ttk.Label(box, text=f'{VERSION}  ·  当前账号  ·  自动纠错  ·  F12急停').pack(anchor='w', pady=(5, 14))
        selector = ttk.Frame(box)
        selector.pack(fill='x')
        ttk.Label(selector, text='测试窗口').pack(side='left', padx=(0, 8))
        self.selector = ttk.Combobox(selector, state='readonly')
        self.selector.pack(side='left', fill='x', expand=True)
        self.refresh_button = ttk.Button(selector, text='刷新', command=self.refresh)
        self.refresh_button.pack(side='left', padx=(8, 0))
        buttons = ttk.Frame(box)
        buttons.pack(fill='x', pady=14)
        self.observe_button = ttk.Button(buttons, text='只读识别', command=lambda: self.start(True))
        self.observe_button.pack(side='left')
        self.start_button = ttk.Button(buttons, text='开始 / 继续', command=self.start)
        self.start_button.pack(side='left', padx=8)
        self.stop_button = ttk.Button(buttons, text='停止', command=self.stop, state='disabled')
        self.stop_button.pack(side='left')
        counts = ttk.Frame(box)
        counts.pack(fill='x', pady=(0, 10))
        for variable in (self.layers, self.chance, self.remaining):
            ttk.Label(counts, textvariable=variable, font=('Microsoft YaHei UI', 11)).pack(side='left', padx=(0, 24))
        ttk.Label(box, textvariable=self.status, wraplength=665).pack(anchor='w', fill='x')
        self.log = tk.Text(box, state='disabled', height=12, wrap='word', font=('Microsoft YaHei UI', 9))
        self.log.pack(fill='both', expand=True, pady=(10, 5))
        ttk.Label(box, textvariable=self.checkpoint, wraplength=665).pack(anchor='w')
        self.protocol('WM_DELETE_WINDOW', self.close)
        self.refresh()
        self.after(100, self.drain)

    def refresh(self):
        if self.worker and self.worker.is_alive():
            return
        old = self.selector.get()
        self.targets = windows()
        labels = [f'{row["title"]}  ·  PID {row["pid"]}  ·  窗口 {row["hwnd"]}' for row in self.targets]
        self.selector.configure(values=labels)
        if old in labels:
            self.selector.current(labels.index(old))
        elif len(labels) == 1:
            self.selector.current(0)
        else:
            self.selector.set('')
        if not labels:
            self.status.set('未发现游戏窗口；打开测试界面后点“刷新”。')
        elif len(labels) > 1 and not self.selector.get():
            self.status.set('发现多个游戏窗口，请选择测试用的窗口。')

    def append(self, text):
        self.log.configure(state='normal')
        self.log.insert('end', str(text)+'\n')
        self.log.see('end')
        self.log.configure(state='disabled')

    def busy(self, active):
        for widget in (self.observe_button, self.start_button, self.refresh_button):
            widget.configure(state='disabled' if active else 'normal')
        self.selector.configure(state='disabled' if active else 'readonly')
        self.stop_button.configure(state='normal' if active else 'disabled')

    def start(self, readonly=False):
        if self.worker and self.worker.is_alive():
            return
        index = self.selector.current()
        if not 0 <= index < len(self.targets):
            self.status.set('请先选择测试游戏窗口。')
            return
        target = self.targets[index]
        self.cancel = threading.Event()
        self.last_result = None
        self.layers.set('已获奖励：— / 6')
        self.chance.set('剩余机会：—')
        self.remaining.set('剩余奖励：—')
        self.checkpoint.set('')
        self.busy(True)
        self.status.set('只读核实当前界面…' if readonly else '核实窗口、账号和独立断点…')
        self.worker = threading.Thread(target=self.run_worker,
            args=(target['hwnd'], readonly, self.cancel), daemon=False, name='DarumaStandalone')
        self.worker.start()

    def run_worker(self, hwnd, readonly, cancel):
        try:
            runner = DarumaRunner(hwnd, stop=cancel, root=self.runtime_root,
                                  sink=lambda event: self.events.put(('event', event)),
                                  expected_binding=self.expected_binding)
            result = runner.run(readonly=readonly)
        except BaseException as exc:
            result = dict(status='pending', reason=f'{type(exc).__name__}: {exc}')
        self.events.put(('finished', result))

    def stop(self):
        self.cancel.set()
        self.status.set('正在停止，保存当前结果和未决动作…')
        self.stop_button.configure(state='disabled')

    def show_counts(self, row):
        if row.get('layers') is not None:
            self.layers.set(f'已获奖励：{row["layers"]} / 6')
        if row.get('chance') is not None:
            self.chance.set(f'剩余机会：{row["chance"]}')
        if row.get('remaining') is not None:
            text = f'剩余奖励：{row["remaining"]}'
            if row.get('reward_needs_check'):
                text += '（待复核）'
            self.remaining.set(text)

    def drain(self):
        try:
            while True:
                kind, row = self.events.get_nowait()
                self.show_counts(row)
                if kind == 'finished':
                    self.last_result = row
                    self.busy(False)
                    self.status.set(row.get('reason') or ('只读识别完成：'+PAGES.get(row.get('page'), '待核实')))
                    self.append(self.status.get())
                    if row.get('checkpoint'):
                        self.checkpoint.set('断点：'+row['checkpoint'])
                elif row['event'] == 'progress':
                    self.status.set(PHASES.get(row['phase'], row['phase']))
                    self.append(self.status.get())
                elif row['event'] == 'bound':
                    self.checkpoint.set('断点：'+row['checkpoint'])
                    self.append(f'已绑定测试窗口 {row["hwnd"]}，当前标签位置 {row["tab_x"]}')
                elif row['event'] == 'observation':
                    self.status.set('只读识别：'+PAGES.get(row['page'], row['page']))
                    self.append(self.status.get()+f'；游戏点击 {row["content_clicks"]}')
                elif row['event'] == 'timing_measured':
                    self.append(f'已核对判定位置 {row["judgment_x"]}，时延 {row["latency"]*1000:.1f} 毫秒')
                elif row['event'] == 'correction':
                    side = '右侧' if row['next_side'] == 'right' else '左侧'
                    self.append(f'{row["reason"]}，改为{side}敲击；本层纠正 {row["retries"]}/2')
                elif row['event'] == 'occlusion_cleared':
                    self.append('奖励栏遮挡已消失，重新采样敲击时机')
                self.after_event(kind, row)
        except queue.Empty:
            pass
        except Exception as exc:
            self.cancel.set()
            self.status.set(f'界面更新异常，正在停止：{type(exc).__name__}: {exc}')
            self.append(self.status.get())
            if not (self.worker and self.worker.is_alive()):
                self.busy(False)
        if self.closing and not (self.worker and self.worker.is_alive()):
            self.destroy()
            return
        self.after(100, self.drain)

    def after_event(self, kind, row):
        """UI-thread observation hook used by the native acceptance harness."""

    def close(self):
        self.closing = True
        if self.worker and self.worker.is_alive():
            self.stop()
        else:
            self.destroy()
