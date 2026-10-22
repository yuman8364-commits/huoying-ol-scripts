"""One desktop window for current Python task runners. No automatic game start."""
from __future__ import annotations

from datetime import datetime
import os
from pathlib import Path
import queue
import tkinter as tk
from tkinter import ttk

import hyol_scheduler as hs
from hyol_runtime import RuntimeController, RunRequest, VERSION
from hyol_progress_view import ProgressView
from hyol_daily_tasks import SOLO_OPTIONS, DEFAULT_SOLO_OPTIONS
from hyol_team_six import TASKS as TEAM_TASKS, DEFAULT_TASKS, TASK_LABELS as TEAM_LABELS
from hyol_team_pipeline import TEAM_TASKS as PIPELINE_DEFAULTS, SUPPORTED_TEAM_TASKS

SOLO_LABELS = {'mail': '邮件', 'welfare': '在线福利', 'ranking': '排位赛',
               'survival': '生存演习', 'quiz': '忍者测验', 'arena': '竞技场',
               'weekend': '周末（拼图＋达摩）', 'treasure': '挖宝（每号5分钟）'}
SOLO_LABELS['daruma'] = '达摩'
INDEPENDENT = {'竞技场': 'arena', '羁绊': 'bond', '周末任务': 'weekend', '挖宝（5分钟）': 'treasure'}
EVENT_LABELS = {'daily_task_started': '任务开始', 'daily_task_complete': '任务与回村已确认',
                'daily_task_pending': '任务待处理', 'generic_team_task_started': '组队任务开始',
                'generic_team_task_done': '全队任务已确认', 'generic_team_task_pending': '组队任务待处理',
                'generic_team_managed_started': '最后托管', 'generic_team_managed_complete': '托管已确认',
                'generic_team_managed_pending': '托管待处理', 'multi_solo_managed_complete': '托管已确认',
                'multi_solo_managed_pending': '托管待处理', 'account_disconnected_skipped': '账号掉线，已跳过',
                'navigation_step_verified': '退出与回村阶段已确认', 'navigation_pending': '回村待处理',
                'combat_battle_start_requested': '已发送开战请求，稍后轮询确认',
                'combat_battle_started': '已确认进入战斗或匹配',
                'combat_slot_quarantined_after_pending': '该账号状态待核实，已继续处理其他账号',
                'combat_battle_start_transition_pending': '开战过渡中，稍后复查',
                'multi_solo_arena_no_eligible_accounts': '没有可执行竞技场的账号',
                'multi_solo_arena_stage_pending': '竞技场部分账号待核实',
                'multi_solo_arena_stage_elapsed': '竞技场轮询阶段结束',
                'arena_tickets_exhausted_verified': '战令次数已核实归零',
                'arena_victory_confirmed': '胜利已确认',
                'arena_defeat_confirmed': '失败，不计入胜场', 'treasure_started': '挖宝计时已开始',
                'treasure_complete': '挖宝已暂停并回村', 'bond_command_summary': '羁绊结果',
                'puzzle_difficulty_complete': '拼图该难度已通关并领奖',
                'puzzle_daily_complete': '拼图三个难度今日已完成',
                'puzzle_daily_already_complete': '今日拼图已有完成证据',
                'puzzle_script_configured': '拼图自动识别已确认',
                'puzzle_script_started': '正在自动解拼图',
                'bond_identity_wait': '正在观察原战斗；等待身份恢复',
                'emergency_stop': 'F12停止，保留断点', 'daily_error': '任务待处理'}
EVENT_LABELS.update(pipeline_team_tick='轮换队长', pipeline_team_pending='该队整体待续',
                    pipeline_battle_complete='队长本场已结算', pipeline_team_dissolved='已完成队伍解散',
                    pipeline_solo_eligible='已确认无队，进入单人', pipeline_solo_skipped='该账号本轮暂缓',
                    pipeline_navigation_retry='恢复活动入口',pipeline_group_counter_recovered='原次数已核实',
                    multi_solo_stage_started='开始单人任务', multi_solo_task_started='任务开始',
                    multi_solo_task_complete='任务与回村已确认', multi_solo_task_pending='任务待处理',
                    multi_solo_task_skip_done='已有完成证据，继续下一项',
                    multi_solo_task_skipped_for_later='该任务保留待续',
                    pipeline_wanted_observed='已接续原通缉自动链')
EVENT_LABELS['pipeline_task_not_open'] = '当前不在开放时间，继续下一项'
EVENT_LABELS['pipeline_reformed_teams'] = '先执行当前已组好的队伍'
EVENT_LABELS['pipeline_group_remaining'] = '继续多人副本'
EVENT_LABELS['pipeline_round_started'] = '两队开始本轮'
EVENT_LABELS['pipeline_round_focus'] = '固定检查当前队伍'
EVENT_LABELS['pipeline_round_completed'] = '两队本轮已完成'


def format_task_event(event, mode):
    account = str(event.get('account_label') or event.get('account') or '')
    if mode in ('team', 'team_then_multi') and (not account or account.startswith('当前窗口标签')):
        index = event.get('tab_index')
        account = f'标签{index}' if index is not None else ''
    task = SOLO_LABELS.get(event.get('task'), '')
    return ' '.join(filter(None, (account, task, EVENT_LABELS[event['event']], str(event.get('detail') or ''))))


class UnifiedClient(tk.Tk):
    def __init__(self, *, root=hs.ROOT):
        super().__init__()
        self.root_path = Path(root)
        self.title('火影OL 完整客户端 '+VERSION)
        self.geometry('1060x900+35+45')
        self.minsize(800, 680)
        self.events = queue.Queue()
        self.controller = RuntimeController(self.events.put, self.root_path)
        self.active_mode = 'solo'
        self.scope = tk.StringVar(value='当前账号')
        self.account_count = tk.StringVar(value='6')
        self.arena_wins = tk.StringVar(value='5')
        self.arena_use_win_target = tk.BooleanVar(value=False)
        self.formation = tk.StringVar(value='保持不变')
        self.bond_count = tk.StringVar(value='5')
        self.new_bond_round = tk.BooleanVar(value=False)
        self.leader = tk.StringVar(value='1')
        self.team_mode = tk.StringVar(value='自动建队')
        self.pipeline_leaders = (tk.StringVar(value='1'),tk.StringVar(value='6'))
        self.pipeline_auto = tk.BooleanVar(value=True)
        self.pipeline_task_vars = {t:tk.BooleanVar(value=t in PIPELINE_DEFAULTS) for t in SUPPORTED_TEAM_TASKS}
        self.friend_shortcut = tk.BooleanVar(value=False)
        self.member_vars = {index: tk.BooleanVar(value=index in (2, 3)) for index in range(1, 7)}
        self.task_vars = {task: tk.BooleanVar(value=task in DEFAULT_SOLO_OPTIONS) for task in SOLO_OPTIONS}
        self.team_vars = {task: tk.BooleanVar(value=task in DEFAULT_TASKS) for task in TEAM_TASKS}
        self.independent = tk.StringVar(value='竞技场')
        self.final_managed = tk.BooleanVar(value=True)
        self.status = tk.StringVar(value='就绪。选择任务后点击开始；F12立即结束本轮。')
        self.result = tk.StringVar(value='尚未开始。未实测功能保留待验，活动未开放或掉线均不算完成。')
        self._build()
        self.after(150, self._drain)
        self.protocol('WM_DELETE_WINDOW', self._close)


    def _build(self):
        style = ttk.Style(self)
        if 'vista' in style.theme_names():
            style.theme_use('vista')
        box = ttk.Frame(self, padding=16)
        box.pack(fill='both', expand=True)
        ttk.Label(box, text='火影OL · 完整客户端', font=('Microsoft YaHei UI', 19, 'bold')).pack(anchor='w')
        ttk.Label(box, text='同一工作线程执行任务与收尾；次数、奖励、木叶回村和托管均逐账号核实。').pack(anchor='w', pady=(7, 11))
        self.tabs = ttk.Notebook(box)
        self.tabs.pack(fill='x')
        daily = ttk.Frame(self.tabs, padding=12)
        team = ttk.Frame(self.tabs, padding=12)
        independent = ttk.Frame(self.tabs, padding=12)
        self.tabs.add(daily, text='单人 / 多账号')
        self.tabs.add(team, text='组队任务')
        self.tabs.add(independent, text='独立任务')
        line = ttk.Frame(daily)
        line.pack(fill='x')
        for name in ('当前账号', '多个账号'):
            ttk.Radiobutton(line, text=name, variable=self.scope, value=name).pack(side='left', padx=(0, 14))
        ttk.Label(line, text='最多账号数').pack(side='left')
        ttk.Spinbox(line, from_=1, to=6, width=4, textvariable=self.account_count).pack(side='left', padx=8)
        tasks = ttk.Frame(daily)
        tasks.pack(fill='x', pady=(10, 5))
        for index, task in enumerate(SOLO_OPTIONS):
            ttk.Checkbutton(tasks, text=SOLO_LABELS[task], variable=self.task_vars[task]).grid(
                row=index//4, column=index%4, sticky='w', padx=(0, 22), pady=4)
        ttk.Label(daily, text='挖宝默认不选；每账号独立计时300秒，只用已有藏宝图。').pack(anchor='w', pady=(5, 0))
        mode_line = ttk.Frame(team)
        mode_line.pack(fill='x',pady=(0,8))
        for mode in ('自动建队','已有队伍 → 多人单人'):
            ttk.Radiobutton(mode_line,text=mode,variable=self.team_mode,value=mode,
                            command=self._team_mode_changed).pack(side='left',padx=(0,18))
        self.legacy_team_box = ttk.Frame(team)
        self.legacy_team_box.pack(fill='x')
        self.pipeline_box = ttk.Frame(team)
        line = ttk.Frame(self.pipeline_box)
        line.pack(fill='x')
        self.pipeline_leader_controls = []
        for i,variable in enumerate(self.pipeline_leaders,1):
            ttk.Label(line,text=f'第{i}队队长标签'+('（可不选）' if i == 2 else '')).pack(side='left')
            values = tuple(str(n) for n in range(1,7))
            control = ttk.Combobox(line,values=(('不选',)+values if i == 2 else values),state='readonly',
                                  width=4,textvariable=variable)
            control.pack(side='left',padx=(8,20))
            self.pipeline_leader_controls.append(control)
        ttk.Checkbutton(line,text='自动发现当前队伍',variable=self.pipeline_auto,
                        command=self._pipeline_auto_changed).pack(side='left')
        self._pipeline_auto_changed()
        self.detect_leaders_button = ttk.Button(line,text='查看当前队长',command=self._detect_leaders)
        self.detect_leaders_button.pack(side='left')
        line = ttk.Frame(self.pipeline_box)
        line.pack(fill='x',pady=8)
        for task,variable in self.pipeline_task_vars.items():
            ttk.Checkbutton(line,text=TEAM_LABELS[task],variable=variable).pack(side='left',padx=(0,20))
        ttk.Label(self.pipeline_box,text='沿用已有队伍，只操作队长。两队开战后固定等待一队结算，再等待另一队；两队完成后开始下一轮。',
                  wraplength=840).pack(anchor='w')
        next_tasks = ttk.LabelFrame(self.pipeline_box,text='解散后：所有可运行的在线标签执行单人任务',padding=5)
        next_tasks.pack(fill='x',pady=8)
        for i,task in enumerate(SOLO_OPTIONS):
            ttk.Checkbutton(next_tasks,text=SOLO_LABELS[task],variable=self.task_vars[task]).grid(
                row=i//4,column=i%4,sticky='w',padx=(0,18),pady=3)
        ttk.Label(self.pipeline_box,text='掉线影响的整队本轮暂缓；其他队伍和无队在线账号继续。周末、挖宝默认不选。',
                  wraplength=840).pack(anchor='w')
        line = ttk.Frame(self.legacy_team_box)
        line.pack(fill='x')
        ttk.Label(line, text='队长标签').pack(side='left')
        ttk.Combobox(line, values=tuple(str(index) for index in range(1, 7)), width=4,
                     state='readonly', textvariable=self.leader).pack(side='left', padx=8)
        ttk.Checkbutton(line, text='按好友列表邀请两名队员', variable=self.friend_shortcut,
                        command=self._friend_changed).pack(side='left', padx=15)
        line = ttk.Frame(self.legacy_team_box)
        line.pack(fill='x', pady=8)
        ttk.Label(line, text='队员标签（选1名或2名）：').pack(side='left')
        self.member_controls = []
        for index, variable in self.member_vars.items():
            button = ttk.Checkbutton(line, text=str(index), variable=variable)
            button.pack(side='left', padx=8)
            self.member_controls.append(button)
        tasks = ttk.Frame(self.legacy_team_box)
        tasks.pack(fill='x')
        for index, task in enumerate(TEAM_TASKS):
            ttk.Checkbutton(tasks, text=TEAM_LABELS[task], variable=self.team_vars[task]).grid(
                row=index//3, column=index%3, sticky='w', padx=(0, 20), pady=4)
        ttk.Label(self.legacy_team_box, text='只显示标签序号；全队真实次数、自动战斗和奖励核实后解散，逐成员进入托管。').pack(anchor='w', pady=(6, 0))
        line = ttk.Frame(independent)
        line.pack(fill='x', pady=8)
        ttk.Label(line, text='任务').pack(side='left')
        ttk.Combobox(line, values=tuple(INDEPENDENT), state='readonly', width=20,
                     textvariable=self.independent).pack(side='left', padx=12)
        ttk.Label(line, text='羁绊场数').pack(side='left', padx=(15, 0))
        ttk.Spinbox(line, from_=1, to=99, width=5, textvariable=self.bond_count).pack(side='left', padx=8)
        ttk.Checkbutton(line, text='开始新一轮羁绊', variable=self.new_bond_round).pack(side='left', padx=8)
        ttk.Label(independent, text='竞技场、周末与挖宝使用当前账号。羁绊从准备页续接；收尾未确认时保留待处理。',
                  wraplength=840).pack(anchor='w', pady=12)
        common = ttk.Frame(box)
        common.pack(fill='x', pady=(12, 8))
        ttk.Label(common, text='竞技场结束：战令归零（默认）').pack(side='left', padx=(0, 10))
        self.arena_target_control = ttk.Checkbutton(
            common, text='按指定胜场数停止（辅助）', variable=self.arena_use_win_target,
            command=self._arena_target_changed)
        self.arena_target_control.pack(side='left', padx=(0, 8))
        ttk.Label(common, text='胜场').pack(side='left')
        self.arena_wins_control = ttk.Spinbox(common, from_=1, to=10, width=5,
                                              textvariable=self.arena_wins, state='disabled')
        self.arena_wins_control.pack(side='left', padx=(6, 18))
        ttk.Label(common, text='阵容').pack(side='left')
        ttk.Combobox(common, values=('保持不变', '主力阵容', '速刷阵容'), state='readonly', width=12,
                     textvariable=self.formation).pack(side='left', padx=8)
        self.managed_control = ttk.Checkbutton(common, text='最后进入托管', variable=self.final_managed)
        self.managed_control.pack(side='right')
        ttk.Label(box, text='竞技场先逐号发起战斗，再轮询检查；默认耗尽战令，指定胜场为辅助模式。掉线账号本轮暂缓。',
                  foreground='#555555', wraplength=900).pack(anchor='w')
        controls = ttk.Frame(box)
        controls.pack(fill='x', pady=12)
        self.start_button = ttk.Button(controls, text='开始', command=self._start)
        self.start_button.pack(side='left', ipadx=15, ipady=5)
        self.check_button = ttk.Button(controls, text='检查游戏窗口', command=self._check)
        self.check_button.pack(side='left', padx=9)
        self.stop_button = ttk.Button(controls, text='立即停止', command=self._stop, state='disabled')
        self.stop_button.pack(side='right')
        ttk.Label(box, textvariable=self.status, wraplength=880, font=('Microsoft YaHei UI', 11, 'bold')).pack(anchor='w')
        ttk.Label(box, textvariable=self.result, wraplength=880).pack(anchor='w', pady=7)
        history = ttk.Notebook(box)
        history.pack(fill='both', expand=True)
        self.progress_view = ProgressView(history, self.root_path, current_only=True)
        history.add(self.progress_view, text='完成情况')
        logbox = ttk.Frame(history)
        history.add(logbox, text='运行日志')
        self.log = tk.Text(logbox, height=10, state='disabled', wrap='word', font=('Microsoft YaHei UI', 10))
        scrollbar = ttk.Scrollbar(logbox, command=self.log.yview)
        self.log.configure(yscrollcommand=scrollbar.set)
        scrollbar.pack(side='right', fill='y')
        self.log.pack(fill='both', expand=True)
        footer = ttk.Frame(box)
        footer.pack(side='bottom', fill='x', pady=(9, 0), before=history)
        ttk.Button(footer, text='打开证据与日志', command=self._open_logs).pack(side='left')
        ttk.Label(footer, text=VERSION+'  |  桌面独立运行').pack(side='right')

    def request(self):
        selected = self.tabs.index(self.tabs.select())
        mode = ('multi' if self.scope.get() == '多个账号' else 'solo') if selected == 0 else (
            'team' if selected == 1 else INDEPENDENT[self.independent.get()])
        if selected == 1 and self.team_mode.get() == '已有队伍 → 多人单人':
            mode = 'team_then_multi'
        leaders = (tuple(int(v.get()) for v in self.pipeline_leaders if v.get() != '不选')
                   if mode == 'team_then_multi' and not self.pipeline_auto.get() else ())
        return RunRequest(mode=mode, tasks=tuple(task for task, variable in self.task_vars.items() if variable.get()),
                          account_count=6 if mode == 'team_then_multi' else int(self.account_count.get()),
                          arena_wins=int(self.arena_wins.get()),
                          arena_target_mode='wins' if self.arena_use_win_target.get() else 'tickets',
                          formation={'保持不变':'keep', '主力阵容':'main', '速刷阵容':'fast'}[self.formation.get()],
                          leader=int(self.leader.get()), members=tuple(index for index, variable in self.member_vars.items() if variable.get()),
                          leaders=leaders,
                          friend_shortcut=self.friend_shortcut.get(),
                          team_tasks=tuple(task for task, variable in (self.pipeline_task_vars if mode == 'team_then_multi' else self.team_vars).items() if variable.get()),
                          bond_count=int(self.bond_count.get()), new_bond_round=mode == 'bond' and self.new_bond_round.get(),
                          final_managed=self.final_managed.get())

    def _arena_target_changed(self):
        self.arena_wins_control.configure(state='normal' if self.arena_use_win_target.get() else 'disabled')

    def _team_mode_changed(self):
        pipeline = self.team_mode.get() == '已有队伍 → 多人单人'
        self.legacy_team_box.pack_forget()
        self.pipeline_box.pack_forget()
        (self.pipeline_box if pipeline else self.legacy_team_box).pack(fill='x')

    def _pipeline_auto_changed(self):
        for control in self.pipeline_leader_controls:
            control.configure(state='disabled' if self.pipeline_auto.get() else 'readonly')

    def _detect_leaders(self):
        if self.controller.detect_team_leaders():
            self._busy()
            self.status.set('只读识别各标签当前角色头像中的队长标记……')

    def _busy(self):
        for button in (self.start_button, self.check_button,self.detect_leaders_button):
            button.configure(state='disabled')
        self.stop_button.configure(state='normal')

    def _start(self):
        try:
            request = self.request()
            if self.controller.start(request):
                self.new_bond_round.set(False)
                self.progress_view.set_run(self.controller.identifier)
                self.log.configure(state='normal')
                self.log.delete('1.0','end')
                self.log.configure(state='disabled')
                self.active_mode = request.mode
                self._busy()
        except (ValueError, tk.TclError) as exc:
            self.status.set(str(exc))

    def _check(self):
        if self.controller.check_window():
            self._busy()
            self.status.set('正在检查游戏窗口；不执行任务点击。')


    def _stop(self):
        try:
            self.controller.stop()
        except Exception as exc:
            self.status.set('停止已请求；暂停记录保存失败：'+str(exc))

    def _friend_changed(self):
        for button in self.member_controls:
            button.configure(state='disabled' if self.friend_shortcut.get() else 'normal')

    def _append(self, message):
        self.log.configure(state='normal')
        self.log.insert('end', f'{datetime.now():%H:%M:%S}  {message}\n')
        if int(self.log.index('end-1c').split('.')[0]) > 800:
            self.log.delete('1.0', '101.0')
        self.log.see('end')
        self.log.configure(state='disabled')

    def _drain(self):
        try:
            while True:
                event = self.events.get_nowait()
                name = event.get('event', '')
                if name.startswith('unified_') and event.get('run_id') != self.controller.identifier:
                    continue
                if name == 'unified_idle':
                    for button in (self.start_button, self.check_button):
                        button.configure(state='normal')
                    self.stop_button.configure(state='disabled')
                    self.detect_leaders_button.configure(state='normal')
                elif name == 'unified_leaders_detected':
                    leaders = event['check'].get('leaders',[])
                    if leaders:
                        for variable,index in zip(self.pipeline_leaders,leaders):
                            variable.set(str(index))
                        self.status.set('已识别队长标签：'+ '、'.join(map(str,leaders))+'。')
                    else:
                        self.status.set('当前未确认队长标记；先在游戏中完成组队。')
                    self.result.set(self.status.get())
                    self._append(self.status.get())
                elif name == 'unified_finished':
                    managed = event.get('request', {}).get('final_managed', True)
                    self.status.set(('本轮所选任务、回村与最后托管已确认完成。' if managed
                                     else '本轮所选任务与回村已确认完成。') if event['status'] == 'complete'
                                    else '本轮已停止。再次开始将创建新运行。' if event['status'] == 'stopped'
                                    else '本轮仍有待处理项。')
                    self.result.set(event.get('detail') or self.status.get())
                    self._append(self.result.get())
                elif name == 'unified_window_check':
                    check = event['check']
                    ready = check.get('status') == 'ready' and check.get('healthy') is True and check.get('content_clicks') == 0
                    self.status.set('窗口健康、只读检查通过。' if ready else '窗口检查待处理。')
                    self.result.set('主界面已确认。' if ready and check.get('main_ready') else
                                    str(check.get('reason') or '当前主界面尚未确认；开始后核实当前页面。'))
                    self._append(self.status.get()+' '+self.result.get())
                elif name.startswith('unified_'):
                    self.status.set(event.get('detail', '处理中'))
                    self._append(self.status.get())
                elif name in EVENT_LABELS:
                    self._append(format_task_event(event, self.active_mode))
                    if name in ('pipeline_group_remaining', 'pipeline_battle_complete'):
                        self.status.set(format_task_event(event, self.active_mode))
        except queue.Empty:
            pass
        self.after(150, self._drain)

    def _open_logs(self):
        path = self.root_path/'logs'
        path.mkdir(parents=True, exist_ok=True)
        os.startfile(str(path))

    def _close(self):
        if self.controller.busy:
            self.status.set('任务运行中；请先点击立即停止或按F12，结束本轮后再关闭。')
            return
        self.destroy()


if __name__ == '__main__':
    UnifiedClient().mainloop()
