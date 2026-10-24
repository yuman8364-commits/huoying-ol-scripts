"""Bounded offline memory comparison; never bind or send input to a game.

Source replays use one shared production TemplateStore across simulated account
slots. GUI checks open hidden idle executables, observe memory/window response,
then close only those owned windows. These are not live game measurements.
"""
from __future__ import annotations
import argparse
import ctypes
from datetime import datetime, timedelta, timezone
import gc
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import time
from types import SimpleNamespace


class MemoryCounters(ctypes.Structure):
    _fields_ = [('cb', ctypes.c_ulong), ('PageFaultCount', ctypes.c_ulong)] + [
        (name, ctypes.c_size_t) for name in (
            'PeakWorkingSetSize', 'WorkingSetSize', 'QuotaPeakPagedPoolUsage', 'QuotaPagedPoolUsage',
            'QuotaPeakNonPagedPoolUsage', 'QuotaNonPagedPoolUsage', 'PagefileUsage',
            'PeakPagefileUsage', 'PrivateUsage')]


def memory(handle=None):
    if handle is None:
        kernel = ctypes.windll.kernel32
        kernel.GetCurrentProcess.restype = ctypes.c_void_p
        handle = kernel.GetCurrentProcess()
    counters = MemoryCounters()
    counters.cb = ctypes.sizeof(counters)
    call = ctypes.windll.psapi.GetProcessMemoryInfo
    call.argtypes = [ctypes.c_void_p, ctypes.POINTER(MemoryCounters), ctypes.c_ulong]
    if not call(handle, ctypes.byref(counters), counters.cb):
        raise ctypes.WinError()
    return dict(working_set=counters.WorkingSetSize, private_commit=counters.PrivateUsage,
                process_peak_working_set=counters.PeakWorkingSetSize)


def buffer_bytes(store):
    arrays = {}
    def visit(value):
        import numpy as np
        if isinstance(value, np.ndarray):
            arrays[id(value)] = value.nbytes
        elif isinstance(value, dict):
            for item in value.values():
                visit(item)
    for name in ('_frame_crops','_frame_correlation','_frame_edges'):
        visit(getattr(store, name, {}))
    return dict(frame_array_bytes=sum(arrays.values()), spectrum_bytes=store._template_spectra.bytes)


def child(project, accounts):
    sys.path.insert(0, str(project))
    import hyol_scheduler as hs
    from hyol_runtime import RuntimeController
    from hyol_task_progress import ProgressReader
    from PIL import Image
    phases = {'imports': memory()}
    fixture = project/'fixtures/survival_highest/return_51.png'
    frame = Image.open(fixture).convert('RGB')
    if frame.size != (1920,1032):
        raise ValueError('Expected canonical replay dimensions')
    store = hs.TemplateStore(.92)
    scheduler = object.__new__(hs.Scheduler)
    scheduler.templates = store
    scheduler.detector = None
    scheduler._last_frame = frame
    controller = RuntimeController(lambda _:None)
    controller.runner = SimpleNamespace(scheduler=scheduler, runners={
        str(i): SimpleNamespace(scheduler=scheduler) for i in range(accounts)})
    controller.scheduler = scheduler
    results = []
    checks = [('anchor_top_menu.bmp',(500,69,1500,189)),
              ('daily_practice_toolbar.bmp',(200,80,1720,950)),
              ('arena_toolbar_icon.bmp',(250,100,1750,940)),
              ('offline_login_prompt.bmp',(150,150,1780,930))]
    for slot in range(accounts):
        capture = frame.copy()
        store.begin_frame(capture)
        scheduler._last_frame = capture
        for name, roi in checks:
            result = store.match(capture, project/'assets'/name, roi)
            results.append([slot,name,*result])
    phases['fixed_frame_replay'] = {**memory(), **buffer_bytes(store)}
    with tempfile.TemporaryDirectory(prefix='hyol-memory-fixture-') as folder:
        root = Path(folder)
        logs = root/'logs'; logs.mkdir()
        log = logs/'HYOL_Scheduler_fixture.jsonl'
        start = datetime(2026,10,24,12,tzinfo=timezone(timedelta(hours=8)))
        with log.open('w', encoding='utf-8') as handle:
            def emit(index, **fields):
                handle.write(json.dumps(dict(run_id='fixture', ts=(start+timedelta(seconds=index)).isoformat(),
                                             **fields), ensure_ascii=False)+'\n')
            emit(0,event='multi_solo_run_started', tasks=['arena'], managed_enabled=False)
            for index in range(accounts):
                emit(index+1, event='account_bound', account=f'fixture_{index}',tab_index=index+1)
                emit(index+8, event='arena_victory_confirmed',account=f'fixture_{index}',target='verified-win')
            for index in range(24000):
                emit(index+30,event='arena_observed',task='arena',account=f'fixture_{index%accounts}',
                     detail='离线固定回放；未输入游戏内容',sample=index)
        reader = ProgressReader(root)
        snapshot = reader.load('2026-10-24','fixture')
        retained = sum(len(row['tasks']['arena']['events']) for row in snapshot['rows'])
        wins = [row['tasks']['arena']['verified_wins'] for row in snapshot['rows']]
        phases['progress_log_24000_events'] = {**memory(), 'retained_detail_events': retained,
                                              'verified_wins': wins, 'log_bytes': log.stat().st_size}
        if hasattr(controller, '_release_run_resources'):
            controller._release_run_resources()
        else:
            # The original production finally block leaves runner referenced.
            controller.scheduler = None
            controller.run_context = None
        capture = frame = None
        gc.collect()
        phases['after_stop'] = {**memory(), **buffer_bytes(store), 'runner_retained':controller.runner is not None}
        if hasattr(reader, 'close'):
            reader.close()
    return dict(accounts=accounts, phases=phases, recognition_results=results,
                fixture_sha256=hashlib.sha256(fixture.read_bytes()).hexdigest(),
                scenario='offline shared scheduler, fixed frame and synthetic audit log',
                game_input_count=0, live_game_acceptance=False)


def idle_gui(executable):
    startup = subprocess.STARTUPINFO()
    startup.dwFlags |= subprocess.STARTF_USESHOWWINDOW
    startup.wShowWindow = 0
    with tempfile.TemporaryDirectory(prefix='hyol-gui-isolated-') as folder:
        env = dict(os.environ, LOCALAPPDATA=folder, USERPROFILE=folder)
        process = subprocess.Popen([str(executable)], cwd=executable.parent, env=env, startupinfo=startup)
        window, title, samples = None, '', []
        user = ctypes.windll.user32
        callback_type = ctypes.WINFUNCTYPE(ctypes.c_bool, ctypes.c_void_p, ctypes.c_void_p)
        def callback(hwnd, _):
            nonlocal window, title
            pid = ctypes.c_ulong()
            user.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
            if pid.value == process.pid:
                text = ctypes.create_unicode_buffer(1024)
                user.GetWindowTextW(hwnd, text, len(text))
                if '火影OL' in text.value:
                    window, title = int(hwnd), text.value
            return True
        try:
            deadline = time.monotonic()+20
            while time.monotonic() < deadline and process.poll() is None:
                user.EnumWindows(callback_type(callback), 0)
                if window:
                    break
                time.sleep(.1)
            if not window:
                raise ValueError('Owned GUI window did not initialize')
            for _ in range(30):
                samples.append(memory(int(process._handle)))
                time.sleep(.1)
            if user.IsHungAppWindow(ctypes.c_void_p(window)):
                raise ValueError('GUI is not responding')
            return dict(title=title, responding=True, samples=len(samples),
                        idle=samples[-1], observed_peak_working_set=max(x['working_set'] for x in samples),
                        game_input_count=0, game_task_started=False,
                        child_processes=[], game_process_memory_included=False)
        finally:
            if window:
                user.PostMessageW(ctypes.c_void_p(window),0x0010,0,0)
            try:
                process.wait(timeout=8)
            except subprocess.TimeoutExpired:
                process.terminate(); process.wait(timeout=5)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--project',type=Path)
    parser.add_argument('--accounts',type=int,choices=(1,6),default=1)
    parser.add_argument('--child',action='store_true')
    parser.add_argument('--baseline',type=Path)
    parser.add_argument('--optimized',type=Path)
    parser.add_argument('--baseline-exe',type=Path)
    parser.add_argument('--optimized-exe',type=Path)
    parser.add_argument('--output',type=Path)
    args=parser.parse_args()
    if args.child:
        print(json.dumps(child(args.project.resolve(),args.accounts),ensure_ascii=False));return
    report=dict(scope='offline only',python=sys.version, replays={}, gui={},game_input_count=0,
                puzzle_subprocess='Not launched: no live puzzle task is authorized or running',
                units='bytes; working set includes resident pages, private_commit is private committed memory')
    for accounts in (1,6):
        for label,project in [('baseline',args.baseline),('optimized',args.optimized)]:
            command=[sys.executable,'-B',str(Path(__file__).resolve()),'--child','--project',str(project.resolve()),'--accounts',str(accounts)]
            result=subprocess.run(command,capture_output=True,encoding='utf-8',env=dict(os.environ,PYTHONIOENCODING='utf-8'),timeout=120)
            if result.returncode:
                raise RuntimeError(result.stderr)
            report['replays'][f'{label}_{accounts}']=json.loads(result.stdout)
            print(f'Completed {label} offline {accounts}-account replay',flush=True)
        before,after=(report['replays'][f'{label}_{accounts}'] for label in ('baseline','optimized'))
        if before['recognition_results'] != after['recognition_results']:
            raise ValueError('Recognition decisions/scores/coordinates differ')
        if before['phases']['progress_log_24000_events']['verified_wins'] != after['phases']['progress_log_24000_events']['verified_wins']:
            raise ValueError('Progress counters differ')
    for label,path in [('baseline',args.baseline_exe),('optimized',args.optimized_exe)]:
        if path:
            report['gui'][label]=idle_gui(path.resolve())
            print(f'Completed {label} idle GUI measurement',flush=True)
    report['recognition_and_counters_match']=True
    args.output.parent.mkdir(parents=True,exist_ok=True)
    args.output.write_text(json.dumps(report,ensure_ascii=False,indent=2),'utf-8')


if __name__=='__main__':
    main()
