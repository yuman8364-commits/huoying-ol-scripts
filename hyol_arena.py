"""Standalone, local arena client for whichever configured account is visible."""
from __future__ import annotations

import json
import os
import queue
import threading
from datetime import datetime
import tkinter as tk
from tkinter import ttk

import hyol_scheduler as hs

VERSION = "2026.10.17.8"
RESULT_PATH = hs.ROOT / "arena_client_result.json"
CONFIG_PATH = hs.ROOT / "ARENA_Client.ini"


def explain_failure(detail: object) -> str:
    raw = str(detail or "未返回具体原因")
    for token, message in (
        ("expected one healthy game window", "未找到唯一可用的火影OL窗口。请打开要测试的账号并停留在主界面。"),
        ("visible account does not positively match", "当前画面无法确认账号身份。请回到要测试账号的主界面，再点击开始。"),
        ("game geometry changed", "游戏窗口尺寸变化，已停止点击。请恢复1920×1032窗口，再点击开始。"),
        ("位置或尺寸不符", "游戏窗口位置或尺寸不符合要求。请恢复到左上角1920×1032后重试。"),
        ("游戏窗口未取得前台焦点", "游戏窗口没有保持前台焦点，请点击游戏主界面后重试。"),
        ("game lost foreground", "无法取得游戏窗口焦点，请恢复游戏窗口后重试。"),
        ("no positive battle/page anchor", "战斗画面等待后仍无法识别。请检查掉线、遮挡或加载情况。"),
        ("auto-battle", "自动战斗未能确认开启，请检查游戏画面。"),
        ("arena casual battle button did not stabilize", "结算或领奖动画后，休闲战斗按钮未在30秒内稳定恢复。请保持竞技场页面可见后继续。"),
        ("arena casual action unavailable", "休闲战斗按钮在点击前发生刷新。新版会自动等待稳定画面并重试。"),
        ("another scheduler owns", "已有其他脚本正在运行。请等它结束后再开始。"),
        ("missing assets", "客户端缺少图像资源。请使用完整解压的客户端文件夹。"),
        ("offline", "当前账号掉线。请手动登录回主界面后重试。"),
        ("F12_or_keyboard_interrupt", "已按紧急停止键，本轮尚未完成。"),
    ):
        if token in raw:
            return message + "\n原始诊断：" + raw
    return raw


def arena_command(wins: int) -> hs.SchedulerCommand:
    return hs.SchedulerCommand("arena", arena_victory_target=wins,
                               arena_additional_victories=True,
                               generic_current_window=True)


class ArenaClient(tk.Tk):
    def __init__(self) -> None:
        super().__init__()
        self.title(f"火影OL 竞技场独立客户端 {VERSION}")
        self.geometry("850x660+40+90")
        self.minsize(750, 560)
        self.protocol("WM_DELETE_WINDOW", self.close)
        self.events: queue.Queue[dict] = queue.Queue()
        self.worker: threading.Thread | None = None
        self.wins = tk.IntVar(value=1)
        self.status = tk.StringVar(value="就绪")
        self.result = tk.StringVar(value="等待开始。完整任务结束后，结果和失败原因会保留在这里。")
        self.current_summary: dict = {}
        self.scheduler = None
        self.build_ui()
        try:
            previous = json.loads(RESULT_PATH.read_text(encoding="utf-8"))
            self.result.set("上次结果：" + str(previous.get("message", "无记录")))
        except (OSError, ValueError):
            pass
        self.after(150, self.drain)

    def build_ui(self) -> None:
        style = ttk.Style(self)
        if "vista" in style.theme_names():
            style.theme_use("vista")
        outer = ttk.Frame(self, padding=18)
        outer.pack(fill="both", expand=True)
        ttk.Label(outer, text="竞技场 · 当前窗口通用版", font=("Microsoft YaHei UI", 19, "bold")).pack(anchor="w")
        ttk.Label(outer, text="无需登记角色名或身份图片；脚本只操作当前高亮标签。请把要运行的账号停留在主界面。", wraplength=790).pack(anchor="w", pady=(8, 3))
        ttk.Label(outer, text="每次成功后再次点击会开始新一轮；中途失败则续接本轮。日常奖励只领一次。", wraplength=790).pack(anchor="w")
        controls = ttk.Frame(outer)
        controls.pack(fill="x", pady=15)
        ttk.Label(controls, text="本轮新增胜利场数：").pack(side="left")
        self.count = ttk.Spinbox(controls, from_=1, to=hs.ARENA_MAX_REQUESTED_WINS, width=5, textvariable=self.wins)
        self.count.pack(side="left", padx=(0, 16))
        self.start_button = ttk.Button(controls, text="开始 / 继续竞技场", command=self.start)
        self.start_button.pack(side="left", ipadx=20, ipady=7)
        ttk.Label(controls, text="F12 立即停止").pack(side="right")
        ttk.Label(outer, textvariable=self.status, font=("Microsoft YaHei UI", 12, "bold")).pack(anchor="w")
        result_box = ttk.LabelFrame(outer, text="本轮结果 / 失败原因", padding=10)
        result_box.pack(fill="x", pady=10)
        ttk.Label(result_box, textvariable=self.result, wraplength=760, justify="left").pack(fill="x")
        log_box = ttk.LabelFrame(outer, text="任务过程", padding=5)
        log_box.pack(fill="both", expand=True)
        self.log = tk.Text(log_box, wrap="word", height=12, state="disabled", font=("Microsoft YaHei UI", 10))
        scrollbar = ttk.Scrollbar(log_box, command=self.log.yview)
        self.log.configure(yscrollcommand=scrollbar.set)
        scrollbar.pack(side="right", fill="y")
        self.log.pack(fill="both", expand=True)
        footer = ttk.Frame(outer)
        footer.pack(fill="x", pady=(10, 0))
        from hyol_client_tools import add_diagnostic_controls
        add_diagnostic_controls(footer, hs.ROOT, lambda: self.scheduler)
        ttk.Button(footer, text="打开本地日志", command=self.open_logs).pack(side="left")
        ttk.Label(footer, text=f"独立运行，无需Codex或Python安装  |  {VERSION}").pack(side="right")

    def append(self, message: str) -> None:
        self.log.configure(state="normal")
        self.log.insert("end", f"{datetime.now():%H:%M:%S}  {message}\n")
        self.log.see("end")
        self.log.configure(state="disabled")

    def start(self) -> None:
        if self.worker and self.worker.is_alive():
            return
        try:
            command = arena_command(self.wins.get())
        except (tk.TclError, ValueError):
            self.status.set("未开始")
            self.result.set(f"胜利场数必须是1到{hs.ARENA_MAX_REQUESTED_WINS}的整数。请修改后重新点击开始。")
            return
        self.start_button.configure(state="disabled")
        self.count.configure(state="disabled")
        self.status.set("正在执行，请勿切换游戏账号")
        self.result.set("正在核对窗口和账号；只有胜利目标、每日奖励及返回主界面均核实后才算完成。")
        self.current_summary = {}
        self.append("你点击了开始；自动识别当前可见账号，不发送标签切换点击。")
        hs.LOGS.mkdir(parents=True, exist_ok=True)
        with (hs.LOGS / "arena_client_ui.jsonl").open("a", encoding="utf-8") as handle:
            handle.write(json.dumps({"event": "user_start", "account": "auto_visible", "wins": command.arena_victory_target, "ts": hs.now_iso()}, ensure_ascii=False) + "\n")

        def run() -> None:
            result = 3
            try:
                scheduler = hs.Scheduler(hs.read_config(CONFIG_PATH), "panel", False, False, event_sink=self.events.put)
                self.scheduler = scheduler
                result = scheduler.execute_command(command)
            except BaseException as exc:
                self.events.put({"event": "client_error", "detail": f"{type(exc).__name__}: {exc}"})
            finally:
                self.events.put({"event": "client_finished", "result": result})
        self.worker = threading.Thread(target=run, name="GenericArena", daemon=False)
        self.worker.start()

    def drain(self) -> None:
        labels = {
            "window_bound": "游戏窗口核对通过", "generic_current_window_bound": "当前高亮标签核对通过",
            "combat_battle_started": "已进入休闲战斗", "arena_auto_enabled": "自动战斗已开启，取消按钮核对通过",
            "arena_victory_confirmed": "本场胜利已确认", "arena_defeat_confirmed": "本场失败，不计入胜利数",
            "arena_daily_reward_claimed": "一项每日奖励已确认领取", "arena_daily_rewards_verified": "两项每日奖励均已核实",
            "combat_module_done": "胜利目标和奖励已完成，已返回主界面", "combat_transition_wait": "等待战斗画面加载",
        }
        try:
            while True:
                event = self.events.get_nowait()
                name = event.get("event")
                if name in labels:
                    self.append(labels[name])
                elif name == "arena_request_ready":
                    self.append(str(event.get("detail")))
                elif name == "client_error":
                    self.current_summary = {"detail": event.get("detail", "")}
                elif name == "command_summary":
                    self.current_summary = event
                elif name == "command_pending":
                    self.append("未完成：" + explain_failure(event.get("detail")))
                elif name == "client_finished":
                    ok = event.get("result") == 0
                    message = ("本轮完成：胜利目标已达到、每日奖励已核实、已返回主界面。\n再次点击可开始新一轮；已领取的每日奖励不会重复领取。"
                               if ok else "本轮未完成：" + explain_failure(self.current_summary.get("detail")))
                    self.status.set("完成，可以再次开始" if ok else "未完成，请按下方原因处理后重试")
                    self.result.set(message)
                    if self.current_summary.get("diagnostic_path"):
                        self.result.set(message+"\n诊断包："+self.current_summary["diagnostic_path"])
                    self.append(message)
                    hs.atomic_json(RESULT_PATH, {"result": event.get("result"), "message": message, "summary": self.current_summary, "ts": hs.now_iso(), "version": VERSION})
                    self.start_button.configure(state="normal")
                    self.count.configure(state="normal")
        except queue.Empty:
            pass
        self.after(150, self.drain)

    def open_logs(self) -> None:
        hs.LOGS.mkdir(parents=True, exist_ok=True)
        os.startfile(hs.LOGS)

    def close(self) -> None:
        if self.worker and self.worker.is_alive():
            self.iconify()
            return
        self.destroy()


if __name__ == "__main__":
    ArenaClient().mainloop()
