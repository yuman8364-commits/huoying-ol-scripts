"""Double-click GUI for the existing-window multi-account solo runner."""
from __future__ import annotations

import queue
import json
import threading
import traceback
import tkinter as tk
from datetime import datetime
from tkinter import ttk

import hyol_scheduler as hs
from hyol_daily_tasks import DEFAULT_SOLO_OPTIONS, SOLO_OPTIONS, FORMATION_LABELS
from hyol_multi_solo import MultiSoloRunner
from hyol_progress_view import ProgressView
from tools.preflight_live import run as preflight_live


TASK_LABELS = {
    "mail": "邮件", "welfare": "在线福利", "ranking": "排位赛",
    "survival": "生存演习", "quiz": "忍者测验", "arena": "竞技场",
    "weekend": "周末（拼图＋达摩）",
    "daruma": "达摩",
    "puzzle": "忍者拼图（三难度）",
    "training_record": "修行录目标",
    "escort": "押镖（两次点券＋一次铜币）",
    "treasure": "挖宝（每号5分钟）",
    "anniversary": "周年庆签到",
    "grocery": "杂货屋（请在新版桌面设置）",
}


def record_gui_error(exc: BaseException) -> str:
    """Keep worker tracebacks when pythonw has no visible stderr."""
    hs.LOGS.mkdir(parents=True, exist_ok=True)
    path = hs.LOGS / 'multi_solo_gui_errors.jsonl'
    detail = {
        'at': datetime.now().astimezone().isoformat(timespec='seconds'),
        'error_type': type(exc).__name__,
        'detail': str(exc),
        'traceback': traceback.format_exc(),
    }
    with path.open('a', encoding='utf-8') as handle:
        handle.write(json.dumps(detail, ensure_ascii=False) + '\n')
    return str(path)


def parse_account_count(value: str, candidate_count: int) -> int:
    try:
        count = int(value.strip())
    except ValueError as exc:
        raise ValueError("账号数量请输入整数") from exc
    if not 1 <= count <= candidate_count:
        raise ValueError(f"账号数量须在1到{candidate_count}之间")
    return count


def parse_arena_wins(value: str) -> int:
    try:
        wins = int(value.strip())
    except ValueError as exc:
        raise ValueError("竞技场追加胜场请输入整数") from exc
    if not 1 <= wins <= hs.ARENA_MAX_ATTEMPTS:
        raise ValueError(f"竞技场追加胜场须在1到{hs.ARENA_MAX_ATTEMPTS}之间")
    return wins


class MultiSoloApp(tk.Tk):
    def __init__(self):
        super().__init__()
        self.title("火影OL多账号单人脚本")
        self.geometry("1060x850")
        self.minsize(900, 700)
        self.resizable(True, True)
        self.events: queue.Queue[tuple[str, object]] = queue.Queue()
        self.worker: threading.Thread | None = None
        config = hs.read_config(hs.ROOT/'HYOL_Daily.ini')
        self.automatic_accounts = config.get('AccountDiscovery','automatic') == 'automatic'
        self.candidate_count = 6 if self.automatic_accounts else len(hs.parse_csv(config.get('TabAccountNames','')))
        self.count = tk.StringVar(value=str(min(6, self.candidate_count)))
        self.arena_wins = tk.StringVar(value="5")
        self.managed_enabled = tk.BooleanVar(value=True)
        self.formation = tk.StringVar(value='保持不变')
        self.status = tk.StringVar(value="选择账号数量和任务，然后点击开始。")
        self.task_vars = {task: tk.BooleanVar(value=task in DEFAULT_SOLO_OPTIONS) for task in SOLO_OPTIONS}
        frame = ttk.Frame(self, padding=18)
        frame.pack(fill="both", expand=True)
        ttk.Label(frame, text="多账号单人日常", font=("Microsoft YaHei UI", 16, "bold")).pack(anchor="w")
        ttk.Label(frame, text='自动识别当前窗口最多6个账号；无需预先配置账号名单。'
                  if self.automatic_accounts else f'已配置{self.candidate_count}个候选账号。').pack(anchor="w", pady=(8,12))
        line = ttk.Frame(frame)
        line.pack(anchor="w", fill="x")
        ttk.Label(line, text="本轮最多运行账号数：").pack(side="left")
        ttk.Spinbox(line, from_=1, to=max(1, self.candidate_count), width=6,
                    textvariable=self.count).pack(side="left")
        ttk.Label(frame, text="选择任务：").pack(anchor="w", pady=(14, 4))
        task_line = ttk.Frame(frame)
        task_line.pack(anchor="w")
        for index, task in enumerate(SOLO_OPTIONS):
            ttk.Checkbutton(task_line, text=TASK_LABELS[task], variable=self.task_vars[task],state='disabled' if task=='grocery' else 'normal').grid(
                row=index//4, column=index%4, sticky='w', padx=(0, 16), pady=3)
        arena_line = ttk.Frame(frame)
        arena_line.pack(anchor="w", pady=(12, 0))
        ttk.Label(arena_line, text="每账号本次竞技场追加胜场：").pack(side="left")
        ttk.Spinbox(arena_line, from_=1, to=hs.ARENA_MAX_ATTEMPTS, width=6,
                    textvariable=self.arena_wins).pack(side="left")
        formation_line = ttk.Frame(frame)
        formation_line.pack(anchor='w', pady=(8,0))
        ttk.Label(formation_line, text='阵容：').pack(side='left')
        ttk.Combobox(formation_line, textvariable=self.formation,
                     values=tuple(FORMATION_LABELS), state='readonly', width=26).pack(side='left')
        self.managed_control = ttk.Checkbutton(frame, text='最后进入托管',variable=self.managed_enabled)
        self.managed_control.pack(anchor='w',pady=(8,0))
        ttk.Label(frame, text="所选任务和领奖确认、回木叶后按选项进入托管。F12 急停。",
                  foreground="#555555").pack(anchor="w", pady=(14, 8))
        self.start_button = ttk.Button(frame, text="开始运行", command=self.start)
        self.start_button.pack(anchor="w")
        ttk.Label(frame, textvariable=self.status, wraplength=535).pack(anchor="w", pady=(12, 6))
        history = ttk.Notebook(frame)
        history.pack(fill='both', expand=True)
        self.progress_view = ProgressView(history, hs.ROOT, current_only=True)
        history.add(self.progress_view, text='完成情况')
        logbox = ttk.Frame(history)
        history.add(logbox, text='运行日志')
        self.log = tk.Text(logbox, height=9, width=68, state="disabled")
        self.log.pack(fill="both", expand=True)
        self.after(200, self.drain)
        self.protocol("WM_DELETE_WINDOW", self.close)

    def append(self, message: str):
        self.log.configure(state="normal")
        self.log.insert("end", message + "\n")
        self.log.see("end")
        self.log.configure(state="disabled")

    def start(self):
        if self.worker and self.worker.is_alive():
            return
        try:
            count = parse_account_count(self.count.get(), self.candidate_count)
            tasks = tuple(task for task in SOLO_OPTIONS if self.task_vars[task].get())
            if not tasks:
                raise ValueError("请至少选择一项任务")
            wins = parse_arena_wins(self.arena_wins.get()) if "arena" in tasks else 5
        except ValueError as exc:
            self.status.set(str(exc))
            return
        from hyol_run_context import RunContext
        self.run_context = RunContext.create(hs.ROOT)
        self.progress_view.set_run(self.run_context.identifier)
        self.start_button.configure(state="disabled")
        self.status.set("正在只读检查游戏窗口……")
        self.worker = threading.Thread(target=self.run_worker,
            args=(count, tasks, wins, self.managed_enabled.get(), FORMATION_LABELS[self.formation.get()], self.run_context), daemon=False)
        self.worker.start()

    def run_worker(self, count: int, tasks: tuple[str, ...], arena_wins: int = 5,
                   managed_enabled: bool = True, formation: str = 'keep', run_context=None):
        from hyol_run_context import RunContext
        context = run_context or RunContext.create(hs.ROOT)
        lease = hs.ForegroundRuntimeLease()
        try:
            if not lease.acquire():
                raise hs.PendingError('已有脚本占用游戏窗口')
            context.begin()
            with context.activate():
                check = preflight_live()
            if not (check.get("status") == "ready" and check.get("healthy") is True
                    and check.get("content_clicks") == 0):
                self.events.put(("finished", "预检未通过：游戏窗口不健康或只读检查异常；未执行任务点击。"))
                return
            self.events.put(("status", f"预检通过，开始识别最多 {count} 个账号。"))
            runner = MultiSoloRunner(tasks, account_count=count, arena_wins=arena_wins,
                                     arena_target_mode='wins',
                                     formation=formation,
                                     managed_enabled=managed_enabled,run_context=context,
                                     event_sink=lambda event: self.events.put(("event", event)))
            result = runner.run()
            self.events.put(("finished", "所选账号任务已核实完成。" if result == 0
                             else "本轮有待处理项；详情见本轮进度和运行日志。"))
        except BaseException as exc:
            try:
                path = record_gui_error(exc)
                self.events.put(("finished", f"启动或运行失败：{type(exc).__name__}: {exc}；诊断日志：{path}"))
            except Exception:
                self.events.put(("finished", f"启动或运行失败：{type(exc).__name__}: {exc}"))

        finally:
            context.stop.set()
            lease.release()

    def drain(self):
        try:
            while True:
                kind, payload = self.events.get_nowait()
                if kind == "event":
                    event = payload
                    name = str(event.get("event", ""))
                    if name.startswith("multi_solo_"):
                        self.append(f"{event.get('account', '')} {name}: {event.get('detail', '')}")
                elif kind == "finished":
                    self.status.set(str(payload))
                    self.append(str(payload))
                    self.start_button.configure(state="normal")
                else:
                    self.status.set(str(payload))
        except queue.Empty:
            pass
        self.after(200, self.drain)

    def close(self):
        if self.worker and self.worker.is_alive():
            self.status.set("脚本正在运行；需要停止时按 F12，结束本轮后再关闭。")
            return
        self.destroy()


if __name__ == "__main__":
    MultiSoloApp().mainloop()
