"""Shared Tk diagnostic controls; background work delivers results through a queue."""
import os
import queue
import threading
from tkinter import TclError, ttk, messagebox

from hyol_diagnostics import Diagnostics, export_from_scheduler


def add_diagnostic_controls(parent, root, get_scheduler):
    bar = ttk.Frame(parent)
    bar.pack(fill='x', pady=4)
    events = queue.Queue()
    operation = {'busy': False}

    def export():
        if operation['busy']:
            return
        operation['busy'] = True
        button.configure(state='disabled')
        scheduler = get_scheduler()
        def worker():
            try:
                path = export_from_scheduler(scheduler, {'reason': 'user export'}) if scheduler else Diagnostics(root).export(summary={'reason': 'client not started; no run attached'})
                events.put((bool(path), str(path) if path else '文件写入失败'))
            except Exception as exc:
                events.put((False, str(exc)))
        threading.Thread(target=worker, name='HYOL-diagnostic-export', daemon=True).start()

    def poll():
        try:
            ok, message = events.get_nowait()
            operation['busy'] = False
            button.configure(state='normal')
            (messagebox.showinfo if ok else messagebox.showerror)('诊断包', message, parent=parent.winfo_toplevel())
        except queue.Empty:
            pass
        except TclError:
            return  # the bar was destroyed while a result was pending
        bar.after(150, poll)

    def open_logs():
        folder = root / 'logs'
        try:
            folder.mkdir(parents=True, exist_ok=True)
            os.startfile(folder)
        except OSError as exc:
            messagebox.showerror('日志目录', str(exc), parent=parent.winfo_toplevel())

    button = ttk.Button(bar, text='导出诊断包', command=export)
    button.pack(side='left')
    ttk.Button(bar, text='打开日志目录', command=open_logs).pack(side='left', padx=8)
    def safe_return():
        if operation['busy']:
            return
        operation['busy'] = True
        button.configure(state='disabled')
        def worker():
            try:
                from hyol_navigation import manual_return
                events.put((True, manual_return(root)))
            except Exception as exc:
                events.put((False, str(exc)))
        # daemon=False on purpose: a click flow already sent to the game must
        # run to its own bounded end even if the window closes mid-way.
        threading.Thread(target=worker, name='HYOL-safe-return', daemon=False).start()
    ttk.Button(bar, text='安全返回木叶村', command=safe_return).pack(side='left')
    bar.after(150, poll)
    return bar
