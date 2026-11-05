"""Exercise actual Tk checkboxes with a mocked runner; never click game content."""
import json
from pathlib import Path
import sys
import time
from unittest.mock import Mock, patch
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from PIL import ImageGrab
import hyol_daily_tasks as daily
import hyol_panel as panel
import hyol_scheduler as hs


def snapshot(ui, name):
    ui.attributes('-topmost', True)
    ui.update()
    time.sleep(.6)
    ui.update()
    x, y = ui.winfo_rootx(), ui.winfo_rooty()
    path = hs.LOGS / f'composite_ui_{name}_20260915.png'
    ImageGrab.grab(bbox=(x, y, x + ui.winfo_width(), y + ui.winfo_height())).save(path)
    return str(path)


results = []
def close_ui(ui):
    for callback in ui.tk.call('after', 'info'):
        ui.tk.call('after', 'cancel', callback)
    ui.destroy()

for task, chosen in [('all', ('ranking', 'quiz')), ('team', ('wanted', 'strong'))]:
    ui = daily.DailyClient(task)
    for key, variable in ui.task_options.items():
        variable.set(False)
    for key in chosen:
        index = list(ui.task_options).index(key)
        ui.task_checks[index].invoke()
    ui.count.set(2)
    screenshot = snapshot(ui, task)
    runner = Mock()
    with patch.object(daily, 'DailyRunner', return_value=runner) as factory:
        ui.start()
        ui.worker.join(2)
        assert factory.call_args.kwargs['selected_tasks'] == list(chosen)
        assert factory.call_args.args[1] == 0
        assert runner.run.call_count == 1
    results.append({'task': task, 'selected': chosen, 'count': 2, 'screenshot': screenshot, 'passed': True})
    close_ui(ui)

ui = panel.HyolPanel()
for variable in ui.composite_options.values():
    variable.set(False)
for key in ('mail', 'team2', 'arena'):
    ui.composite_options[key].set(True)
ui.arena_wins.set(3)
ui.group_instance_count.set(2)
ui.strong_approach_count.set(4)
screenshot = snapshot(ui, 'panel')
with patch.object(ui.controller, 'start', return_value=True) as start:
    ui.start_command('full')
    commands = start.call_args.kwargs['sequence']
    assert [c.kind for c in commands] == ['mail', 'team2', 'arena']
    assert commands[1].group_instance_count == 2 and commands[1].strong_approach_count == 4
    assert commands[2].arena_victory_target == 3
results.append({'task': 'panel', 'selected': [c.kind for c in commands], 'screenshot': screenshot, 'passed': True})
close_ui(ui)
hs.atomic_json(hs.LOGS / 'composite_ui_20260915.json', {'content_clicks': 0, 'runner': 'mock', 'results': results})
print(json.dumps(results, ensure_ascii=False))
