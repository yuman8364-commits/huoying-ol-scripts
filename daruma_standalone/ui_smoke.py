"""Native Tk checks and a screenshot; no game task is started."""
from PIL import ImageGrab
import ctypes
import time
from hyol_scheduler import Win32

from .journal import atomic_json


def schedule(app):
    directory = app.runtime_root/'verification'
    directory.mkdir(parents=True, exist_ok=True)
    app.smoke_status = 'pending'

    def check():
        report = dict(status='pending', game_task_started=False, content_clicks=0)
        try:
            assert str(app.start_button['state']) == 'normal'
            assert str(app.observe_button['state']) == 'normal'
            assert str(app.stop_button['state']) == 'disabled'
            app.busy(True)
            assert str(app.start_button['state']) == 'disabled'
            assert str(app.stop_button['state']) == 'normal'
            app.stop_button.invoke()
            assert app.cancel.is_set()
            app.busy(False)
            app.cancel.clear()
            app.status.set('独立界面检查通过；尚未启动游戏任务。')
            app.append('窗口选择、只读识别、开始／继续、停止控件检查通过。')
            app.geometry('720x520+50+90')
            app.lift()
            app.focus_force()
            app.update()
            Win32.user32.GetAncestor.restype = ctypes.c_void_p
            hwnd = int(Win32.user32.GetAncestor(app.winfo_id(), 2))
            Win32.activate(hwnd)
            app.update()
            time.sleep(.22)  # Let DWM present the raised window before capture.
            app.update()
            if Win32.foreground() != hwnd:
                raise RuntimeError('独立GUI未在前台，不能把背景截图记为视觉通过')
            box = (app.winfo_rootx(), app.winfo_rooty(),
                   app.winfo_rootx()+app.winfo_width(), app.winfo_rooty()+app.winfo_height())
            path = directory/'gui.png'
            ImageGrab.grab(bbox=box, all_screens=True).save(path)
            if Win32.foreground() != hwnd:
                raise RuntimeError('GUI截图期间前台已变化，视觉验收待重试')
            report.update(status='pass', screenshot=str(path), title=app.title(),
                          windows=len(app.targets), stop_and_rearm=True, geometry=list(box))
            app.smoke_status = 'pass'
        except Exception as exc:
            report['reason'] = f'{type(exc).__name__}: {exc}'
        finally:
            atomic_json(directory/'gui.json', report)
            app.after(250, app.close)

    app.after(450, check)
