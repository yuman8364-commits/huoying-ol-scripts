"""Shared native Tk progress browser; reads files and opens user-selected evidence."""
from __future__ import annotations

import os
from pathlib import Path
import tkinter as tk
from tkinter import ttk

from hyol_task_progress import ProgressReader, TASK_LABELS, STATUSES, local_day, task_status_text


class ProgressView(ttk.Frame):
    def __init__(self, master, root, *, reader=None, current_only=False):
        super().__init__(master, padding=4)
        self.reader = reader or ProgressReader(root)
        self.current_only = current_only
        self.current_run_id = None
        self.snapshot = {'rows': []}
        self.selection = None
        self.run_ids = {}
        self.day = tk.StringVar(value=local_day())
        self.run = tk.StringVar(value='今日累计')
        self.note = tk.StringVar(value='只读浏览；查看进度不会点击游戏。')
        controls = ttk.Frame(self)
        controls.pack(fill='x')
        ttk.Label(controls, text='日期').pack(side='left')
        self.days = ttk.Combobox(controls, width=12, state='readonly', textvariable=self.day)
        self.days.pack(side='left', padx=4)
        self.runs = ttk.Combobox(controls, width=28, state='readonly', textvariable=self.run)
        self.runs.pack(side='left', padx=4)
        if current_only:
            for widget in controls.winfo_children():
                widget.pack_forget()
            ttk.Label(controls, text='本轮运行').pack(side='left')
        ttk.Button(controls, text='刷新', command=self.refresh).pack(side='left')
        ttk.Button(controls, text='打开截图', command=self.open_evidence).pack(side='right')
        self.days.bind('<<ComboboxSelected>>', self._change_day)
        self.runs.bind('<<ComboboxSelected>>', lambda _e: self.refresh())
        table = ttk.Frame(self)
        table.pack(fill='both', expand=True, pady=4)
        columns = ('label', 'health', *TASK_LABELS)
        self.tree = ttk.Treeview(table, columns=columns, show='headings', height=6, selectmode='browse')
        for column in columns:
            self.tree.heading(column, text={'label': '标签', 'health': '账号状态'}.get(column, TASK_LABELS.get(column, column)))
            self.tree.column(column, width=112 if column == 'mail' else 100 if column == 'health' else 72, minwidth=60, stretch=False)
        vertical = ttk.Scrollbar(table, orient='vertical', command=self.tree.yview)
        horizontal = ttk.Scrollbar(table, orient='horizontal', command=self.tree.xview)
        self.tree.configure(yscrollcommand=vertical.set, xscrollcommand=horizontal.set)
        self.tree.grid(row=0, column=0, sticky='nsew')
        vertical.grid(row=0, column=1, sticky='ns')
        horizontal.grid(row=1, column=0, sticky='ew')
        table.rowconfigure(0, weight=1)
        table.columnconfigure(0, weight=1)
        self.tree.bind('<ButtonRelease-1>', self._select)
        self.detail = tk.Text(self, height=4, wrap='word', state='disabled', font=('Microsoft YaHei UI', 9))
        self.detail.pack(fill='x')
        ttk.Label(self, textvariable=self.note, wraplength=1000).pack(anchor='w', pady=(3, 0))
        self.refresh()
        self.after(2000, self._tick)

    def _change_day(self, _event=None):
        self.run.set('今日累计')
        self.refresh()

    def set_run(self, identifier):
        self.current_run_id = identifier
        self.day.set(local_day())
        self.selection = None
        self.snapshot = {'rows': []}
        self.detail.configure(state='normal')
        self.detail.delete('1.0','end')
        self.detail.configure(state='disabled')
        self.refresh()

    def _tick(self):
        self.refresh()
        self.after(2000, self._tick)

    def refresh(self):
        try:
            if self.current_only:
                snapshot = (self.reader.load(self.day.get(), self.current_run_id) if self.current_run_id else
                            dict(rows=[], runs=[], updated_at='', warnings=[]))
                run_id = self.current_run_id
            else:
                self.days.configure(values=self.reader.days())
                run_id = self.run_ids.get(self.run.get())
                snapshot = self.reader.load(self.day.get(), run_id)
            self.run_ids = {f'{stamp[11:19]}  {identifier[-8:]}': identifier
                            for identifier, stamp in snapshot['runs']}
            # A selected run stays selectable even while its log is being written.
            if run_id:
                self.run_ids[self.run.get()] = run_id
            self.runs.configure(values=('今日累计', *self.run_ids))
            self.snapshot = snapshot
            current = set(self.tree.get_children())
            for row in snapshot['rows']:
                key = str(row['index'])
                values = [row['label'], row['health']]
                values.extend(task_status_text(task, row['tasks'][task]) for task in TASK_LABELS)
                if key in current:
                    self.tree.item(key, values=values)
                    current.remove(key)
                else:
                    self.tree.insert('', 'end', iid=key, values=values)
            for key in current:
                self.tree.delete(key)
            updated = snapshot['updated_at'] or '暂无记录'
            warning = '；'.join(snapshot['warnings'][:2])
            self.note.set(f'最近记录：{updated}  |  {warning or "只读浏览；标签位置以所选运行的核验记录为准。"}')
            if self.selection:
                self._show_detail(*self.selection)
        except (OSError, ValueError, TypeError, KeyError) as exc:
            self.note.set('待刷新，保留已有显示：' + str(exc))

    def _select(self, event):
        key = self.tree.identify_row(event.y)
        column = self.tree.identify_column(event.x)
        if not key or not column:
            return
        index = int(column[1:]) - 3
        if 0 <= index < len(TASK_LABELS):
            self.selection = (int(key), tuple(TASK_LABELS)[index])
            self._show_detail(*self.selection)

    def _show_detail(self, index, task):
        row = next((row for row in self.snapshot['rows'] if row['index'] == index), None)
        if row is None:
            return
        rec = row['tasks'][task]
        lines = [f'{row["label"]} · {row.get("account") or "身份待核实"} · {TASK_LABELS[task]} · {task_status_text(task, rec)}']
        fields = dict(completed='已核实轮次/次数', verified_battles='已核实战斗总数',
                      verified_first_round_battles='已归档首轮战斗', verified_current_round_battles='当前轮战斗',
                      remaining='剩余次数', verified_wins='已确认新增胜场',
                      requested_wins='目标胜场', phase='当前阶段', task_completed='任务确认',
                      home_returned='回村确认', followup_check_due='仍需生存补跑', finished_at='完成时间')
        lines.append('  '.join(f'{label}：{("是" if rec[key] else "否") if isinstance(rec[key], bool) else rec[key]}'
                              for key, label in fields.items()
                              if key in rec and not (task in ('arena', 'survival') and key == 'completed')))
        if rec['pending_actions']:
            lines.append('未决动作：' + '、'.join(rec['pending_actions']))
        if rec['last_error']:
            lines.append('原因：' + rec['last_error'])
        for key, label in (('verified_results','已核实结果'), ('verified_claims','已核实领奖')):
            if rec.get(key):
                lines.append(label+'：'+'、'.join(rec[key]))
        for event in rec.get('events', [])[-4:]:
            lines.append(f'{str(event.get("ts", ""))[11:19]} {event.get("event", "")} {event.get("detail", "")}')
        if rec['evidence_paths']:
            lines.append('截图：' + rec['evidence_paths'][-1])
        self.detail.configure(state='normal')
        self.detail.delete('1.0', 'end')
        self.detail.insert('end', '\n'.join(lines))
        self.detail.configure(state='disabled')

    def open_evidence(self):
        if not self.selection:
            self.note.set('请先点击要检查的任务单元格。')
            return
        index, task = self.selection
        row = next((row for row in self.snapshot['rows'] if row['index'] == index), None)
        if row:
            for value in reversed(row['tasks'][task]['evidence_paths']):
                path = Path(value)
                if path.suffix.lower() in ('.png', '.jpg', '.jpeg') and path.is_file():
                    os.startfile(str(path))
                    return
        self.note.set('该环节尚无可打开的本地截图，原记录保留。')
