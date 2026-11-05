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
from hyol_task_progress import mail_cleanup_required
from hyol_daily_tasks import SOLO_OPTIONS, DEFAULT_SOLO_OPTIONS
from hyol_team_six import TASKS as TEAM_TASKS, DEFAULT_TASKS, TASK_LABELS as TEAM_LABELS
from hyol_team_pipeline import TEAM_TASKS as PIPELINE_DEFAULTS, SUPPORTED_TEAM_TASKS

SOLO_LABELS = {'mail': '邮件', 'welfare': '在线福利', 'ranking': '排位赛',
               'survival': '生存演习', 'quiz': '忍者测验', 'arena': '竞技场',
               'weekend': '周末（拼图＋达摩）', 'treasure': '挖宝（每号5分钟）'}
SOLO_LABELS['daruma'] = '达摩'
SOLO_LABELS['puzzle'] = '忍者拼图（三难度）'
SOLO_LABELS['training_record'] = '修行录目标'
SOLO_LABELS['escort'] = '押镖（两次点券＋一次铜币）'
SOLO_LABELS['anniversary'] = '周年庆签到'
INDEPENDENT = {'竞技场': 'arena', '羁绊': 'bond', '周末任务': 'weekend', '挖宝（5分钟）': 'treasure'}
INDEPENDENT.update({'忍者拼图（全部账号）':'puzzle','修行录目标（全部账号）':'training_record'})
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
EVENT_LABELS.update(pipeline_member_auto_verified='该账号自动战斗已确认',
                    pipeline_all_members_checked='全员已检查，已结束场次单独记录',
                    pipeline_wanted_observation_deferred='通缉降频，优先其他队伍',
                    pipeline_wanted_observation_resumed='恢复通缉检查',
                    pipeline_all_members_auto_verified='全队自动战斗已确认',
                    pipeline_leader_x2_verified='队长二倍速已确认',
                    pipeline_duo_warning_confirmed='已勾选不再提示，二人队继续',
                    pipeline_bond_complete='所选羁绊场次与退出已确认',
                    pipeline_team_released='羁绊已退出，恢复单人状态')
EVENT_LABELS['pipeline_round_started'] = '两队开始本轮'
EVENT_LABELS['pipeline_round_focus'] = '固定检查当前队伍'
EVENT_LABELS['pipeline_round_completed'] = '两队本轮已完成'
EVENT_LABELS['mail_backpack_full'] = '邮件附件未领：普通背包已满，需清理'
EVENT_LABELS.update(training_supported_task_started='优先完成修行录中的已有任务',
                    training_goals_rechecked='修行录目标已复查',training_task_preserved='保留本轮已完成任务',
                    training_rewards_cleared='修行录奖励已领完并复扫',training_plan_detected='已更新本号日常计划')
EVENT_LABELS['training_account_pending'] = '本号保留待核实，其他可用账号继续'


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
        self.active_request = None
        self.scope = tk.StringVar(value='当前账号')
        self.account_count = tk.StringVar(value='6')
        self.arena_wins = tk.StringVar(value='5')
        self.arena_use_win_target = tk.BooleanVar(value=False)
        self.formation = tk.StringVar(value='保持不变')
        self.bond_count = tk.StringVar(value='5')
        self.bond_target_mode = tk.StringVar(value='固定场数')
        self.bond_kill_target = tk.StringVar(value='160')
        self.bond_score_target = tk.StringVar(value='100')
        self.bond_team_modes = {i:tk.StringVar(value='沿用默认') for i in range(3)}
        self.bond_team_targets = {i:tk.StringVar(value='100') for i in range(3)}
        self.new_bond_round = tk.BooleanVar(value=False)
        self.bond_stop_mode = tk.StringVar(value='打5次')
        self.leader = tk.StringVar(value='1')
        self.team_mode = tk.StringVar(value='自动建队')
        self.pipeline_leaders = (tk.StringVar(value='1'),tk.StringVar(value='3'),tk.StringVar(value='不选'))
        self.preformed_members = (tk.StringVar(value='2'),tk.StringVar(value='4'),tk.StringVar(value='6'))
        self.pipeline_auto = tk.BooleanVar(value=True)
        self.preformed_duos = tk.BooleanVar(value=False)
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
        if 'clam' in style.theme_names():
            style.theme_use('clam')
        self.configure(background='#FFFFFF')
        font = ('Microsoft YaHei UI', 10)
        style.configure('.', font=font, background='#FFFFFF', foreground='#202124')
        style.configure('TFrame', background='#FFFFFF')
        style.configure('TLabel', background='#FFFFFF', foreground='#202124')
        style.configure('Muted.TLabel', foreground='#6B7280')
        style.configure('TLabelframe', background='#FFFFFF', bordercolor='#E5E7EB', relief='solid')
        style.configure('TLabelframe.Label', background='#FFFFFF', foreground='#202124',
                        font=('Microsoft YaHei UI', 10, 'bold'))
        style.configure('TButton', background='#FFFFFF', foreground='#202124',
                        bordercolor='#D1D5DB', padding=(9, 5))
        style.map('TButton', background=[('active', '#F3F4F6'), ('disabled', '#F9FAFB')],
                  foreground=[('disabled', '#9CA3AF')])
        style.configure('Primary.TButton', background='#202124', foreground='#FFFFFF',
                        bordercolor='#202124', padding=(14, 7), font=('Microsoft YaHei UI', 10, 'bold'))
        style.map('Primary.TButton', background=[('active', '#111827'), ('disabled', '#D1D5DB')],
                  foreground=[('disabled', '#FFFFFF')])
        style.configure('Nav.TButton', background='#FFFFFF', foreground='#374151',
                        bordercolor='#FFFFFF', anchor='w', padding=(10, 8))
        style.map('Nav.TButton', background=[('active', '#F3F4F6')])
        style.configure('Selected.Nav.TButton', background='#F3F4F6', foreground='#111827',
                        bordercolor='#E5E7EB', anchor='w', padding=(10, 8),
                        font=('Microsoft YaHei UI', 10, 'bold'))
        style.map('Selected.Nav.TButton', background=[('active', '#F3F4F6')])
        style.configure('TCheckbutton', background='#FFFFFF', foreground='#202124')
        style.configure('TRadiobutton', background='#FFFFFF', foreground='#202124')
        style.configure('TCombobox', fieldbackground='#FFFFFF', background='#FFFFFF',
                        foreground='#202124', arrowcolor='#374151')
        style.map('TCombobox', fieldbackground=[('readonly', '#FFFFFF')],
                  selectbackground=[('readonly', '#FFFFFF')],
                  selectforeground=[('readonly', '#202124')])
        style.configure('TSpinbox', fieldbackground='#FFFFFF', foreground='#202124')
        style.configure('Treeview', background='#FFFFFF', fieldbackground='#FFFFFF',
                        foreground='#202124', rowheight=25, borderwidth=0)
        style.configure('Treeview.Heading', background='#F9FAFB', foreground='#374151',
                        relief='flat', padding=(5, 5))
        style.map('Treeview', background=[('selected', '#E5E7EB')],
                  foreground=[('selected', '#111827')])
        try:
            style.layout('Hidden.TNotebook', [('Notebook.client', {'sticky': 'nswe'})])
            style.layout('Hidden.TNotebook.Tab', [])
        except tk.TclError:
            pass
        style.configure('Hidden.TNotebook', background='#FFFFFF', borderwidth=0, tabmargins=0)
        style.configure('Hidden.TNotebook.Tab', padding=0, borderwidth=0)

        self.geometry('1180x820+35+45')
        self.minsize(1000, 720)
        shell = ttk.Frame(self)
        shell.pack(fill='both', expand=True)

        sidebar = ttk.Frame(shell, width=158, padding=(12, 14))
        sidebar.pack(side='left', fill='y')
        sidebar.pack_propagate(False)
        ttk.Label(sidebar, text='火影OL', font=('Microsoft YaHei UI', 16, 'bold')).pack(anchor='w')
        ttk.Label(sidebar, text='完整客户端', style='Muted.TLabel').pack(anchor='w', pady=(1, 16))
        ttk.Separator(sidebar, orient='horizontal').pack(fill='x', pady=(0, 10))
        self.nav_buttons = []
        for index, label in enumerate(('单人 / 多账号', '组队任务', '独立任务')):
            button = ttk.Button(sidebar, text=label, style='Nav.TButton',
                                command=lambda page=index: self._show_page(page))
            button.pack(fill='x', pady=2)
            self.nav_buttons.append(button)
        ttk.Separator(sidebar, orient='horizontal').pack(fill='x', pady=10)
        ttk.Button(sidebar, text='运行日志', style='Nav.TButton',
                   command=self._focus_log).pack(fill='x', pady=2)
        ttk.Label(sidebar, text=VERSION, style='Muted.TLabel').pack(side='bottom', anchor='w')

        content = ttk.Frame(shell, padding=(16, 12))
        content.pack(side='left', fill='both', expand=True)
        self.page_title = tk.StringVar(value='单人 / 多账号')
        self.page_description = tk.StringVar(value='选择账号范围和任务；开始后按设置执行。')
        header = ttk.Frame(content)
        header.pack(fill='x', pady=(0, 10))
        ttk.Label(header, textvariable=self.page_title,
                  font=('Microsoft YaHei UI', 16, 'bold')).pack(anchor='w')
        ttk.Label(header, textvariable=self.page_description, style='Muted.TLabel').pack(anchor='w', pady=(3, 0))

        workspace = ttk.Frame(content)
        workspace.pack(fill='both', expand=True)
        left = ttk.Frame(workspace)
        left.pack(side='left', fill='both', expand=True, padx=(0, 12))
        right = ttk.Frame(workspace, width=325)
        right.pack(side='right', fill='both')
        right.pack_propagate(False)

        self.tabs = ttk.Notebook(left, style='Hidden.TNotebook')
        self.tabs.pack(fill='both', expand=True)
        daily = ttk.Frame(self.tabs, padding=(0, 2))
        team = ttk.Frame(self.tabs, padding=(0, 2))
        independent = ttk.Frame(self.tabs, padding=(0, 2))
        self.tabs.add(daily, text='单人 / 多账号')
        self.tabs.add(team, text='组队任务')
        self.tabs.add(independent, text='独立任务')

        scope_box = ttk.LabelFrame(daily, text='账号范围', padding=10)
        scope_box.pack(fill='x', pady=(0, 10))
        line = ttk.Frame(scope_box)
        line.pack(fill='x')
        for name in ('当前账号', '多个账号'):
            ttk.Radiobutton(line, text=name, variable=self.scope, value=name,
                            command=self._refresh_task_settings).pack(side='left', padx=(0, 14))
        ttk.Label(line, text='最多账号数').pack(side='left', padx=(8, 4))
        ttk.Spinbox(line, from_=1, to=6, width=4, textvariable=self.account_count).pack(side='left')
        ttk.Label(scope_box, text='多账号按当前配置顺序执行；账号身份会在运行时重新核实。',
                  style='Muted.TLabel', wraplength=560).pack(anchor='w', pady=(7, 0))

        task_box = ttk.LabelFrame(daily, text='任务选择', padding=10)
        task_box.pack(fill='x', pady=(0, 8))
        for index, task in enumerate(SOLO_OPTIONS):
            ttk.Checkbutton(task_box, text=SOLO_LABELS[task], variable=self.task_vars[task],
                            command=self._refresh_task_settings).grid(
                row=index // 3, column=index % 3, sticky='w', padx=(0, 12), pady=5)
        ttk.Label(daily, text='周末任务、达摩和挖宝默认不选；活动开放状态由运行时核实。',
                  style='Muted.TLabel', wraplength=560).pack(anchor='w', pady=(2, 0))

        mode_line = ttk.Frame(team)
        mode_line.pack(fill='x', pady=(0, 8))
        for mode in ('自动建队', '已有队伍 → 多人单人'):
            ttk.Radiobutton(mode_line, text=mode, variable=self.team_mode, value=mode,
                            command=self._team_mode_changed).pack(side='left', padx=(0, 16))
        self.legacy_team_box = ttk.LabelFrame(team, text='自动建队设置', padding=9)
        line = ttk.Frame(self.legacy_team_box)
        line.pack(fill='x')
        ttk.Label(line, text='队长标签').pack(side='left')
        ttk.Combobox(line, values=tuple(str(index) for index in range(1, 7)), width=4,
                     state='readonly', textvariable=self.leader).pack(side='left', padx=8)
        ttk.Checkbutton(line, text='按好友列表邀请两名队员', variable=self.friend_shortcut,
                        command=self._friend_changed).pack(side='left', padx=12)
        line = ttk.Frame(self.legacy_team_box)
        line.pack(fill='x', pady=8)
        ttk.Label(line, text='队员标签（选1名或2名）').pack(side='left')
        self.member_controls = []
        for index, variable in self.member_vars.items():
            button = ttk.Checkbutton(line, text=str(index), variable=variable)
            button.pack(side='left', padx=6)
            self.member_controls.append(button)
        team_tasks = ttk.LabelFrame(self.legacy_team_box, text='组队任务', padding=8)
        team_tasks.pack(fill='x')
        for index, task in enumerate(TEAM_TASKS):
            ttk.Checkbutton(team_tasks, text=TEAM_LABELS[task], variable=self.team_vars[task]).grid(
                row=index // 3, column=index % 3, sticky='w', padx=(0, 14), pady=4)
        ttk.Label(self.legacy_team_box, text='完成后核实奖励与回村；未确认的操作保留待处理。',
                  style='Muted.TLabel', wraplength=560).pack(anchor='w', pady=(8, 0))

        self.pipeline_box = ttk.LabelFrame(team, text='已有队伍 → 多人单人设置', padding=9)
        line = ttk.Frame(self.pipeline_box)
        line.pack(fill='x')
        self.pipeline_leader_controls = []
        for i, variable in enumerate(self.pipeline_leaders, 1):
            ttk.Label(line, text=f'第{i}队队长'+('（可不选）' if i > 1 else '')).pack(side='left')
            values = tuple(str(n) for n in range(1, 7))
            control = ttk.Combobox(line, values=(('不选',) + values if i > 1 else values),
                                   state='readonly', width=4, textvariable=variable)
            control.pack(side='left', padx=(6, 12))
            self.pipeline_leader_controls.append(control)
        ttk.Checkbutton(line, text='自动发现当前队伍', variable=self.pipeline_auto,
                        command=self._pipeline_auto_changed).pack(side='left', padx=(0, 8))
        self._pipeline_auto_changed()
        self.detect_leaders_button = ttk.Button(line, text='查看当前队长', command=self._detect_leaders)
        self.detect_leaders_button.pack(side='left')
        line = ttk.Frame(self.pipeline_box)
        line.pack(fill='x', pady=8)
        ttk.Checkbutton(line, text='已组好二人队，按队长和下方队员标签直接执行',
                        variable=self.preformed_duos).pack(anchor='w')
        pair_line=ttk.Frame(self.pipeline_box)
        pair_line.pack(fill='x',pady=4)
        for index,variable in enumerate(self.preformed_members):
            ttk.Label(pair_line,text=f'第{index+1}队队员').pack(side='left')
            ttk.Combobox(pair_line,values=tuple(map(str,range(1,7))),state='readonly',
                         textvariable=variable,width=4).pack(side='left',padx=(5,12))
        for task, variable in self.pipeline_task_vars.items():
            ttk.Checkbutton(line, text=TEAM_LABELS.get(task, '多人完成后羁绊（仅二人）'), variable=variable,
                            command=self._refresh_task_settings).pack(side='left', padx=(0, 12))
        ttk.Label(self.pipeline_box,
                  text='支持三支二人队轮换。多人完成后可打羁绊；现成二人队直接执行，全部场次结束才退出，退出即单人。',
                  style='Muted.TLabel', wraplength=560).pack(anchor='w')
        next_tasks = ttk.LabelFrame(self.pipeline_box, text='解散后执行的单人任务', padding=7)
        next_tasks.pack(fill='x', pady=(8, 0))
        for index, task in enumerate(SOLO_OPTIONS):
            ttk.Checkbutton(next_tasks, text=SOLO_LABELS[task], variable=self.task_vars[task],
                            command=self._refresh_task_settings).grid(
                row=index // 3, column=index % 3, sticky='w', padx=(0, 10), pady=3)

        independent_box = ttk.LabelFrame(independent, text='独立任务', padding=10)
        independent_box.pack(fill='x', pady=(0, 8))
        line = ttk.Frame(independent_box)
        line.pack(fill='x')
        ttk.Label(line, text='任务类型').pack(side='left')
        self.independent_combo = ttk.Combobox(line, values=tuple(INDEPENDENT), state='readonly',
                                              width=22, textvariable=self.independent)
        self.independent_combo.pack(side='left', padx=10)
        self.independent_combo.bind('<<ComboboxSelected>>',
                                    lambda _event: self._refresh_task_settings())
        ttk.Label(independent_box,
                  text='独立任务仅支持当前一个游戏窗口。羁绊请先手动进入准备页，目标可选一直打、打5次、5场胜利；不领取宝箱。',
                  style='Muted.TLabel', wraplength=560).pack(anchor='w', pady=(8, 0))

        self.task_settings_frame = ttk.LabelFrame(left, text='独立任务设置', padding=10)
        self.task_settings_frame.pack(fill='x', pady=(9, 0))
        self.task_specific_box = ttk.Frame(self.task_settings_frame)
        self.task_specific_box.pack(fill='x')
        common = ttk.Frame(self.task_settings_frame)
        common.pack(fill='x', pady=(8, 0))
        ttk.Separator(common, orient='horizontal').pack(fill='x', pady=(0, 8))
        self.formation_label = ttk.Label(common, text='阵容')
        self.formation_control = ttk.Combobox(
            common, values=('保持不变', '主力阵容', '速刷阵容'), state='readonly',
            width=12, textvariable=self.formation)
        self.managed_control = ttk.Checkbutton(common, text='最后进入托管', variable=self.final_managed)
        self.formation_label.pack(side='left')
        self.formation_control.pack(side='left', padx=(8, 18))
        self.managed_control.pack(side='right')

        account_box = ttk.LabelFrame(right, text='账号列表', padding=8)
        account_box.pack(fill='x')
        account_actions = ttk.Frame(account_box)
        account_actions.pack(fill='x')
        ttk.Button(account_actions, text='刷新', command=self._refresh_account_list).pack(side='right')
        ttk.Button(account_actions, text='详细进度', command=self._open_progress_window).pack(side='right', padx=(0, 6))
        account_table = ttk.Frame(account_box)
        account_table.pack(fill='x', pady=(6, 0))
        self.account_tree = ttk.Treeview(
            account_table, columns=('account', 'status', 'task', 'progress'),
            show='headings', height=6, selectmode='browse')
        for column, title, width in (
                ('account', '账号', 82), ('status', '状态', 62),
                ('task', '当前任务', 98), ('progress', '进度', 48)):
            self.account_tree.heading(column, text=title)
            self.account_tree.column(column, width=width, minwidth=48,
                                     anchor='w', stretch=column in ('account', 'task'))
        account_scroll = ttk.Scrollbar(account_table, orient='vertical', command=self.account_tree.yview)
        self.account_tree.configure(yscrollcommand=account_scroll.set)
        self.account_tree.grid(row=0, column=0, sticky='nsew')
        account_scroll.grid(row=0, column=1, sticky='ns')
        account_table.rowconfigure(0, weight=1)
        account_table.columnconfigure(0, weight=1)
        self.account_hint = tk.StringVar(value='开始后显示本轮核实账号；未识别身份不会显示为在线。')
        ttk.Label(account_box, textvariable=self.account_hint, style='Muted.TLabel',
                  wraplength=290).pack(anchor='w', pady=(6, 0))

        log_box = ttk.LabelFrame(right, text='运行日志', padding=8)
        log_box.pack(fill='both', expand=True, pady=(10, 0))
        log_actions = ttk.Frame(log_box)
        log_actions.pack(fill='x', pady=(0, 6))
        ttk.Button(log_actions, text='清空', command=self._clear_log).pack(side='right')
        ttk.Button(log_actions, text='打开目录', command=self._open_logs).pack(side='right', padx=(0, 6))
        log_body = ttk.Frame(log_box)
        log_body.pack(fill='both', expand=True)
        self.log = tk.Text(log_body, height=10, state='disabled', wrap='word',
                           font=('Microsoft YaHei UI', 9), bg='#FFFFFF', fg='#202124',
                           insertbackground='#202124', relief='solid', borderwidth=1,
                           highlightthickness=1, highlightbackground='#E5E7EB')
        log_scroll = ttk.Scrollbar(log_body, command=self.log.yview)
        self.log.configure(yscrollcommand=log_scroll.set)
        self.log.pack(side='left', fill='both', expand=True)
        log_scroll.pack(side='right', fill='y')

        status_frame = ttk.Frame(content)
        status_frame.pack(fill='x', pady=(8, 0))
        ttk.Label(status_frame, textvariable=self.status,
                  font=('Microsoft YaHei UI', 10, 'bold'), wraplength=850).pack(anchor='w')
        ttk.Label(status_frame, textvariable=self.result, style='Muted.TLabel',
                  wraplength=850).pack(anchor='w', pady=(3, 0))
        controls = ttk.Frame(content)
        controls.pack(fill='x', pady=(8, 0))
        self.start_button = ttk.Button(controls, text='开始', style='Primary.TButton', command=self._start)
        self.start_button.pack(side='left', ipadx=12, ipady=3)
        self.stop_button = ttk.Button(controls, text='停止', command=self._stop, state='disabled')
        self.stop_button.pack(side='left', padx=8, ipadx=8, ipady=3)
        self.check_button = ttk.Button(controls, text='检查窗口', command=self._check)
        self.check_button.pack(side='left', ipadx=4, ipady=3)
        ttk.Label(controls, text='运行中可按 F12 紧急停止', style='Muted.TLabel').pack(side='right')

        self.progress_window = tk.Toplevel(self)
        self.progress_window.title('详细进度')
        self.progress_window.geometry('1020x600')
        self.progress_window.withdraw()
        self.progress_view = ProgressView(self.progress_window, self.root_path, current_only=True)
        self.progress_view.pack(fill='both', expand=True)
        self.progress_window.protocol('WM_DELETE_WINDOW', self.progress_window.withdraw)
        self.tabs.bind('<<NotebookTabChanged>>', self._on_page_changed)
        self._team_mode_changed()
        self._on_page_changed()
        self.after(1000, self._tick_account_list)

    def request(self):
        self._refresh_task_settings()
        selected = self.tabs.index(self.tabs.select())
        mode = ('multi' if self.scope.get() == '多个账号' else 'solo') if selected == 0 else (
            'team' if selected == 1 else INDEPENDENT[self.independent.get()])
        if selected == 1 and self.team_mode.get() == '已有队伍 → 多人单人':
            mode = 'team_then_multi'
        leaders = (tuple(int(v.get()) for v in self.pipeline_leaders if v.get() != '不选')
                   if mode == 'team_then_multi' and not self.pipeline_auto.get() else ())
        return RunRequest(mode=mode, tasks=tuple(task for task, variable in self.task_vars.items() if variable.get()),
                          account_count=1 if selected == 2 else 6 if mode == 'team_then_multi' else int(self.account_count.get()),
                          arena_wins=int(self.arena_wins.get()),
                          arena_target_mode='wins' if self.arena_use_win_target.get() else 'tickets',
                          formation={'保持不变':'keep', '主力阵容':'main', '速刷阵容':'fast'}[self.formation.get()],
                          leader=int(self.leader.get()), members=tuple(index for index, variable in self.member_vars.items() if variable.get()),
                          leaders=leaders,
                          preformed_duos=mode == 'team_then_multi' and self.preformed_duos.get(),
                          preformed_pairs=(tuple((int(leader.get()),int(self.preformed_members[index].get()))
                                                   for index,leader in enumerate(self.pipeline_leaders) if leader.get()!='不选')
                                           if mode=='team_then_multi' and self.preformed_duos.get() and not self.pipeline_auto.get() else ()),
                          friend_shortcut=self.friend_shortcut.get(),
                          team_tasks=tuple(task for task, variable in (self.pipeline_task_vars if mode == 'team_then_multi' else self.team_vars).items() if variable.get()),
                          bond_count=5 if mode == 'bond' else int(self.bond_count.get()),
                          bond_stop_mode=({'一直打':'endless','打5次':'battles','5场胜利':'wins'}[self.bond_stop_mode.get()] if mode == 'bond' else 'battles'),
                          new_bond_round=mode == 'bond' and self.new_bond_round.get(),
                          bond_target_mode=({'固定场数':'battles','击杀数达标':'kills','积分降到指定值':'score'}[self.bond_target_mode.get()]
                                            if mode == 'team_then_multi' else 'battles'),
                          bond_kill_target=int(self.bond_kill_target.get()), bond_score_target=int(self.bond_score_target.get()),
                          bond_team_goals=(tuple((int(self.pipeline_leaders[i].get()),{'固定场数':'battles','击杀数达标':'kills','积分降到指定值':'score'}[v.get()],
                                                 int(self.bond_team_targets[i].get()))
                                                for i,v in self.bond_team_modes.items() if v.get() != '沿用默认' and self.pipeline_leaders[i].get()!='不选')
                                           if mode == 'team_then_multi' else ()),
                          final_managed=mode != 'bond' and self.final_managed.get())

    def _show_page(self, page):
        self.tabs.select(page)
        self._on_page_changed()

    def _on_page_changed(self, _event=None):
        page = self.tabs.index(self.tabs.select())
        titles = (
            ('单人 / 多账号', '选择账号范围和任务；开始后按设置执行。'),
            ('组队任务', '选择建队方式、队长与要执行的组队活动。'),
            ('独立任务', '每次只运行一个独立任务；参数显示在下方。'),
        )
        self.page_title.set(titles[page][0])
        self.page_description.set(titles[page][1])
        for index, button in enumerate(self.nav_buttons):
            button.configure(style='Selected.Nav.TButton' if index == page else 'Nav.TButton')
        self._refresh_task_settings()

    def _refresh_task_settings(self):
        if not hasattr(self, 'task_specific_box') or not hasattr(self, 'tabs'):
            return
        for child in self.task_specific_box.winfo_children():
            child.destroy()
        self.arena_target_control = None
        self.arena_wins_control = None
        page = self.tabs.index(self.tabs.select())
        if page == 2:
            codes = (INDEPENDENT.get(self.independent.get(), 'arena'),)
        elif page == 1 and self.team_mode.get() != '已有队伍 → 多人单人':
            codes = ()
        else:
            codes = tuple(task for task, variable in self.task_vars.items() if variable.get())
            if page == 1 and self.pipeline_task_vars['bond'].get():
                codes = (*codes, 'bond')
        self.current_setting_codes = tuple(codes)
        row = 0
        if 'arena' in codes:
            arena_box = ttk.LabelFrame(self.task_specific_box, text='竞技场（按实际战令或所选目标结束）', padding=7)
            arena_box.grid(row=row, column=0, sticky='ew', pady=(0, 5))
            self.task_specific_box.columnconfigure(0, weight=1)
            ttk.Label(arena_box, text='结束条件').pack(side='left', padx=(0, 8))
            self.arena_tickets_control = ttk.Radiobutton(
                arena_box, text='战令归零（默认）', variable=self.arena_use_win_target,
                value=False, command=self._arena_target_changed)
            self.arena_tickets_control.pack(side='left', padx=(0, 10))
            self.arena_target_control = ttk.Radiobutton(
                arena_box, text='指定胜场（辅助）', variable=self.arena_use_win_target,
                value=True, command=self._arena_target_changed)
            self.arena_target_control.pack(side='left', padx=(0, 8))
            ttk.Label(arena_box, text='胜场').pack(side='left')
            self.arena_wins_control = ttk.Spinbox(
                arena_box, from_=1, to=10, width=4, textvariable=self.arena_wins,
                state='normal' if self.arena_use_win_target.get() else 'disabled')
            self.arena_wins_control.pack(side='left', padx=(5, 0))
            row += 1
        if 'bond' in codes:
            bond_box = ttk.Frame(self.task_specific_box)
            bond_box.grid(row=row, column=0, sticky='w', pady=(0, 5))
            ttk.Label(bond_box, text='羁绊目标' if page == 2 else '羁绊场数').pack(side='left')
            self.bond_count_control = (ttk.Combobox(bond_box, values=('一直打','打5次','5场胜利'),
                state='readonly', width=12, textvariable=self.bond_stop_mode) if page == 2 else ttk.Spinbox(
                bond_box, from_=1, to=99, width=5, textvariable=self.bond_count))
            self.bond_count_control.pack(side='left', padx=8)
            self.new_bond_round_control = ttk.Checkbutton(
                bond_box, text='开始新一轮羁绊', variable=self.new_bond_round)
            self.new_bond_round_control.pack(side='left', padx=8)
            row += 1
            if page == 1:
                goals = ttk.Frame(self.task_specific_box)
                goals.grid(row=row, column=0, sticky='w', pady=(0,5))
                ttk.Label(goals, text='羁绊默认结束条件').grid(row=0,column=0,sticky='w')
                ttk.Combobox(goals, values=('固定场数','击杀数达标','积分降到指定值'), state='readonly',
                             textvariable=self.bond_target_mode, width=17).grid(row=0,column=1,padx=8)
                ttk.Label(goals,text='击杀宝箱领取至（打满160人档）').grid(row=1,column=0,sticky='w')
                ttk.Spinbox(goals,from_=0,to=999,textvariable=self.bond_kill_target,width=6).grid(row=1,column=1,sticky='w',padx=8)
                ttk.Label(goals,text='积分降至此值及以下').grid(row=2,column=0,sticky='w')
                ttk.Spinbox(goals,from_=0,to=99999,textvariable=self.bond_score_target,width=6).grid(row=2,column=1,sticky='w',padx=8)
                for n,i in enumerate(range(3),3):
                    ttk.Label(goals,text=f'第{i+1}队单队目标').grid(row=n,column=0,sticky='w')
                    ttk.Combobox(goals,values=('沿用默认','固定场数','击杀数达标','积分降到指定值'),state='readonly',
                        textvariable=self.bond_team_modes[i],width=17).grid(row=n,column=1,padx=8)
                    ttk.Spinbox(goals,from_=0,to=99999,textvariable=self.bond_team_targets[i],width=6).grid(row=n,column=2)
                row += 1
        for code, message in (
                ('weekend', '周末任务：拼图三难度＋达摩；仅在活动开放日执行。'),
                ('puzzle', '忍者拼图：逐账号完成简单、普通、困难并核实领奖；每日05:00刷新。'),
                ('training_record', '修行录：先领完奖励，再复扫缺项并定制各账号日常，每完成一项重新领取和计算。'),
                ('treasure', '挖宝：每账号固定 5 分钟，到时自动暂停并回村。'),
                ('daruma', '达摩：自动执行至奖励次数核实归零。'),
                ('quiz', '忍者测验：沿用现有规则，不增加额外参数。'),
                ('ranking', '排位赛：沿用现有规则，核实剩余次数后结束。')):
            if code in codes:
                ttk.Label(self.task_specific_box, text=message, style='Muted.TLabel',
                          wraplength=560).grid(row=row, column=0, sticky='w', pady=(0, 5))
                row += 1
        if not row:
            ttk.Label(self.task_specific_box, text='当前所选任务无需额外参数。',
                      style='Muted.TLabel').grid(row=0, column=0, sticky='w')
        bond_only = page == 2 and codes == ('bond',)
        if bond_only:
            self.formation_label.pack_forget()
            self.formation_control.pack_forget()
            self.managed_control.pack_forget()
        else:
            if not self.formation_label.winfo_manager():
                self.formation_label.pack(side='left')
            if not self.formation_control.winfo_manager():
                self.formation_control.pack(side='left', padx=(8, 18))
            if not self.managed_control.winfo_manager():
                self.managed_control.pack(side='right')

    def _arena_target_changed(self):
        if self.arena_wins_control is not None and self.arena_wins_control.winfo_exists():
            self.arena_wins_control.configure(
                state='normal' if self.arena_use_win_target.get() else 'disabled')

    def _team_mode_changed(self):
        pipeline = self.team_mode.get() == '已有队伍 → 多人单人'
        self.legacy_team_box.pack_forget()
        self.pipeline_box.pack_forget()
        (self.pipeline_box if pipeline else self.legacy_team_box).pack(fill='x')
        self._refresh_task_settings()

    def _pipeline_auto_changed(self):
        for control in self.pipeline_leader_controls:
            control.configure(state='disabled' if self.pipeline_auto.get() else 'readonly')

    def _active_task_codes(self):
        request = self.active_request
        if request is None:
            return ()
        if request.mode in ('solo', 'multi', 'team_then_multi'):
            codes = list(request.tasks)
        elif request.mode in ('arena', 'weekend', 'treasure','puzzle','training_record'):
            codes = [request.mode]
        elif request.mode == 'bond':
            codes = []
        else:
            codes = []
        if request.final_managed and request.mode not in ('bond', 'team'):
            codes.append('managed')
        return tuple(codes)

    def _refresh_account_list(self):
        if not hasattr(self, 'account_tree') or not hasattr(self, 'progress_view'):
            return
        try:
            self.progress_view.refresh()
            rows = self.progress_view.snapshot.get('rows', [])
            current = set(self.account_tree.get_children())
            if not rows:
                for item in current:
                    self.account_tree.delete(item)
                request = self.active_request
                if request is not None and request.mode in ('solo', 'arena', 'bond', 'weekend', 'treasure','puzzle','training_record'):
                    task_codes = [code for code in self._active_task_codes() if code != 'managed']
                    task_label = ('羁绊' if request.mode == 'bond' else
                                  SOLO_LABELS.get(task_codes[0], task_codes[0])
                                  if len(task_codes) == 1 else '多项任务')
                    run_status = str(self.status.get())
                    if self.controller.busy:
                        status = '运行中'
                    elif '待处理项' in run_status:
                        status = '待处理'
                    elif '已确认完成' in run_status:
                        status = '已完成'
                    elif '已停止' in run_status:
                        status = '已停止'
                    else:
                        status = '待核验'
                    self.account_tree.insert('', 'end', iid='current-window',
                                             values=('当前窗口', status, task_label, '—'))
                    self.account_hint.set('单窗口任务；账号身份与完成情况以日志和任务证据为准。')
                else:
                    self.account_hint.set('开始后显示本轮核实账号；未识别身份不会显示为在线。')
                return
            selected = self._active_task_codes()
            for row in rows:
                index = str(row.get('index', ''))
                if not index:
                    continue
                account = str(row.get('account') or row.get('label') or f'账号{index}')
                if account.startswith('当前窗口标签') or (account.startswith('account_') and len(account)==40):
                    account = str(row.get('label') or f'标签{index}')
                task_rows = row.get('tasks', {})
                relevant = [(task, task_rows.get(task, {})) for task in selected if task in task_rows]
                running = next(((task, rec) for task, rec in relevant if rec.get('status') == 'running'), None)
                pending = next(((task, rec) for task, rec in relevant if rec.get('status') in ('pending', 'offline')), None)
                done_count = sum(rec.get('status') == 'done' for _, rec in relevant)
                total = len(relevant)
                health = str(row.get('health') or '')
                if health and health not in ('已绑定', '身份待核实'):
                    status = '离线'
                    task_label = '—'
                elif health == '身份待核实':
                    status = '待核验'
                    task_label = '—'
                elif 'mail' in selected and mail_cleanup_required(task_rows.get('mail', {})):
                    status = '背包满需清理'
                    task_label = 'mail'
                elif pending:
                    status = '待处理'
                    task_label = pending[0]
                elif running:
                    status = '运行中'
                    task_label = running[0]
                elif total and done_count == total:
                    status = '已完成'
                    task_label = '—'
                elif self.active_request is not None and self.active_request.mode == 'team':
                    status = '运行中' if self.controller.busy else '待核验'
                    task_label = '组队任务'
                elif done_count:
                    status = '进行中'
                    task_label = next((task for task, rec in relevant if rec.get('status') not in ('done', 'unselected')), '—')
                elif self.active_request is not None and self.active_request.mode == 'team_then_multi' and self.controller.busy:
                    status = '运行中'
                    task_label = '组队阶段'
                else:
                    status = '待开始'
                    task_label = next((task for task, rec in relevant if rec.get('status') not in ('done', 'unselected')), '—')
                task_text = SOLO_LABELS.get(task_label, {'managed': '托管', 'bond': '羁绊'}.get(task_label, task_label))
                progress = f'{done_count}/{total}' if total else '—'
                values = (account, status, task_text, progress)
                if index in current:
                    self.account_tree.item(index, values=values)
                    current.remove(index)
                else:
                    self.account_tree.insert('', 'end', iid=index, values=values)
            for item in current:
                self.account_tree.delete(item)
            self.account_hint.set(f'本轮账号：{len(rows)} 个；状态仅来自已记录的识别与任务证据。')
        except (tk.TclError, OSError, ValueError, TypeError, KeyError):
            return

    def _tick_account_list(self):
        self._refresh_account_list()
        try:
            self.after(1000, self._tick_account_list)
        except tk.TclError:
            pass

    def _open_progress_window(self):
        if self.controller.busy:
            self.status.set('运行中暂不打开详细进度，避免抢占游戏前台。')
            return
        self.progress_window.deiconify()
        self.progress_window.lift()

    def _focus_log(self):
        self.log.focus_set()

    def _clear_log(self):
        self.log.configure(state='normal')
        self.log.delete('1.0', 'end')
        self.log.configure(state='disabled')

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
                self._active_run_id = self.controller.identifier
                self.progress_view.set_run(self._active_run_id)
                self.active_request = request
                self._refresh_account_list()
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
                if name == 'unified_run_started':
                    # A resumed training run rebinds the identifier inside the
                    # worker; the event carries the authoritative run id.
                    active = str(event.get('run_id') or self.controller.identifier or '')
                    if active and active != getattr(self, '_active_run_id', None):
                        self._active_run_id = active
                        self.progress_view.set_run(active)
                elif name.startswith('unified_') and event.get('run_id') != getattr(self, '_active_run_id', None):
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
        finally:
            # One unexpected event handler error must not permanently freeze
            # the whole UI event pump.
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


def main():
    import argparse
    parser=argparse.ArgumentParser(description='火影OL完整客户端')
    parser.add_argument('--task',choices=('puzzle','training_record'))
    parser.add_argument('--account-count',type=int,choices=range(1,7),default=6)
    parser.add_argument('--start',action='store_true',help='直接开始指定任务')
    args=parser.parse_args()
    app=UnifiedClient()
    if args.task:
        app.independent.set(next(label for label,code in INDEPENDENT.items() if code==args.task))
        app.account_count.set(str(args.account_count))
        app.tabs.select(2);app._on_page_changed()
        if args.start:app.after(750,app._start)
    app.mainloop()


if __name__ == '__main__':
    main()
