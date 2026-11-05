"""Resume one original timed treasure run without starting another map budget.

Uses its recent named journal, unchanged browser process/tab and positive
current name glyphs. The original start/deadline are recovered only from the
actual timer event. Other tasks and historical pending actions stay untouched.
"""
import argparse
from copy import deepcopy
from datetime import datetime
import hashlib
import json
from pathlib import Path
import sys
import time

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import hyol_scheduler as hs
import hyol_treasure as treasure
from hyol_daily_tasks import DailyRunner
from hyol_diagnostics import manual_pause_status
from hyol_navigation import LEAF_NAME_ROI, move_cursor_clear
from tools.preflight_live import run as preflight

NAME_ROI = (518, 429, 633, 444)


def bound_tab_accounts(scheduler, mapping, positions):
    accounts = []
    for slot, position in enumerate(positions):
        name = next((name for name, x in mapping.items() if x == position), None)
        # Identity assets use the enrolled role index, not the current tab slot.
        # An offline unbound position gets no enrolled role/identity authority.
        original = scheduler.by_name.get(name)
        accounts.append(hs.Account(original.index if original else 7+slot,
                                   name or f'未绑定标签{slot+1}', position))
    return accounts


def yellow_glyphs(image):
    pixels = hs.np.asarray(image.convert('RGB'), dtype=hs.np.int16)
    return ((pixels[:, :, 0] > 180) & (pixels[:, :, 1] > 140)
            & (pixels[:, :, 2] < 135) & (pixels[:, :, 0]-pixels[:, :, 2] > 70))


def battle_owner_visible(runner, frame):
    asset = hs.ASSETS/f'accounts/{hs.slug(runner.account.name)}_battle_identity.bmp'
    if not treasure.battle_visible(runner, frame):
        return False
    enrollment = hs.LOGS/f'treasure_owner_{hs.slug(runner.account.name)}_enrollment.json'
    roi = NAME_ROI
    if enrollment.exists():
        data = json.loads(enrollment.read_text(encoding='utf-8'))
        source = Path(data['source'])
        if data['account'] != runner.account.name or hashlib.sha256(source.read_bytes()).hexdigest() != data['source_sha256']:
            return False
        roi = tuple(data['crop'])
        with hs.Image.open(source) as image:
            reference = yellow_glyphs(image.crop(roi))
    elif asset.exists():
        with hs.Image.open(asset) as image:
            reference = yellow_glyphs(image)
    else:
        return False
    if int(reference.sum()) < 200:
        return False
    # Name captions can bob by a few pixels with the character animation.
    # Match every foreground glyph at one common offset, with a sparse text
    # limit that rejects solid gold terrain. No missing glyph is accepted.
    for dy in range(-3, 4):
        for dx in range(-3, 4):
            current = yellow_glyphs(frame.crop(tuple(value+delta for value, delta in zip(roi, (dx, dy, dx, dy)))))
            if (reference.shape == current.shape and bool(hs.np.all(current[reference]))
                    and int(current.sum()) <= int(reference.sum())*1.35):
                return True
    return False


def resume(original_report, *, max_seconds=300):
    receipt = json.loads(original_report.read_text(encoding='utf-8-sig'))
    if (receipt.get('task') != 'treasure' or receipt.get('status') != 'pending'
            or receipt.get('account') != receipt.get('expected_account')
            or receipt.get('unrelated_original_actions_preserved') is not True):
        raise hs.PendingError('原挖宝报告未绑定唯一实名账号')
    check = preflight()
    if not (check.get('status') == 'ready' and check.get('healthy') is True and check.get('content_clicks') == 0):
        return dict(status='blocked', preflight=check, content_clicks=0)
    journal = hs.LOGS/f'HYOL_Scheduler_{receipt["run_id"]}.jsonl'
    events = [json.loads(line) for line in journal.read_text(encoding='utf-8-sig').splitlines() if line.strip()]
    bound = next(row for row in events if row['event'] == 'window_bound')
    timer = next(row for row in events if row['event'] == 'treasure_timer_started')
    mapping = next(row['mapping'] for row in events if row['event'] == 'identity_rebound')
    positions = next(row['title_centers'] for row in events if row['event'] == 'multi_solo_tabs_counted')
    config = hs.read_config(hs.ROOT/'HYOL_Daily.ini')
    config.update(AllowMarketPurchase='0', AutoForeground='0', AutoRefreshWhiteTab='0')
    s = hs.Scheduler(config, 'multi_solo', False, False)
    s.auto_foreground = False
    old = s.by_name[receipt['account']]
    account = hs.Account(old.index, old.name, mapping[old.name])
    # Current discovery positions supersede the static installation defaults.
    # Retain all six painted tabs for the active-highlight check.
    s.config['TabXs'] = ','.join(str(x) for x in positions)
    s.accounts = bound_tab_accounts(s, mapping, positions)
    runner = DailyRunner.for_bound_account(s, account, ('treasure',), managed_enabled=False)
    before = deepcopy(s.state.accounts[account.name].get('actions', {}))
    other_tasks = deepcopy({key: value for key, value in runner.records.items()
                           if key not in ('treasure', 'updated_at')})
    result = dict(status='pending', task='treasure', account=account.name,
                  run_id=s.diagnostics.identifier, source_version=__import__('hyol_daily_tasks').VERSION,
                  preflight=check, original_report=str(original_report),
                  original_report_sha256=hashlib.sha256(original_report.read_bytes()).hexdigest(),
                  original_journal_sha256=hashlib.sha256(journal.read_bytes()).hexdigest())
    lease, owned = hs.ForegroundRuntimeLease(), False
    try:
        if manual_pause_status(hs.ROOT)['paused']:
            raise hs.PendingError('人工暂停仍在')
        owned = lease.acquire()
        if not owned:
            raise hs.PendingError('已有脚本占用窗口')
        s.start_f12_monitor()
        s.bind_window(wait=True, timeout_seconds=10)
        context_at = datetime.fromisoformat(bound['ts']).timestamp()
        if (s.hwnd != bound['hwnd'] or hs.Win32.pid(s.hwnd) != bound['pid']
                or not 0 <= time.time()-context_at <= 3600):
            raise hs.PendingError('原挖宝窗口、进程或一小时内实名会话不一致')
        handle = hs.Win32.kernel32.OpenProcess(hs.Win32.PROCESS_QUERY_LIMITED_INFORMATION, False, bound['pid'])
        if not handle:
            raise hs.PendingError('浏览器创建时间未核实')
        try:
            created, end, kernel, user = (hs.ctypes.c_ulonglong() for _ in range(4))
            if not hs.Win32.kernel32.GetProcessTimes(handle, hs.ctypes.byref(created), hs.ctypes.byref(end), hs.ctypes.byref(kernel), hs.ctypes.byref(user)):
                raise hs.PendingError('浏览器创建时间读取失败')
            if created.value/10000000-11644473600 > context_at:
                raise hs.PendingError('浏览器在原实名之后重新启动')
        finally:
            hs.Win32.kernel32.CloseHandle(handle)
        rec = runner.record('treasure')
        if (rec.get('deadline_at') != timer['deadline_at'] or timer['deadline_at']-timer['started_at'] != 300
                or s.state.action_status(account.name, treasure.START_ACTION) != 'done'):
            raise hs.PendingError('原挖宝开始或截止时间未完整核实，不重新启动')
        reference = hs.Image.open(receipt['pending_evidence']).convert('RGB')
        sample, identity_until = 0, time.monotonic()+45
        result['identity_evidence'] = []
        while sample < 3:
            frame = s.capture()
            current_named = s.verify_identity(account, frame)[0]
            # Hover tooltips can overlap y=28 onward. The actual title strip
            # and selected tab above it remain byte-for-byte unchanged.
            same_chrome = frame.crop((0, 0, 1920, 28)).tobytes() == reference.crop((0, 0, 1920, 28)).tobytes()
            if s._generic_active_tab_x(frame) != account.tab_x:
                raise hs.PendingError('原挖宝活动标签发生变化，零内容输入')
            if current_named or (same_chrome and battle_owner_visible(runner, frame)):
                sample += 1
                result['identity_evidence'].append(str(s.save_raw_evidence(frame, account, 'treasure_resume_identity', 'verified')))
            # Keep observing transitions with zero input. Three separately
            # positive name frames are required, while the tab/process lease
            # remains unchanged throughout; an occlusion is never an identity.
            if time.monotonic() >= identity_until:
                raise hs.PendingError('当前标签和实名文字未三帧确认，零内容输入')
            if sample < 3:
                s.wait_interruptible(3)
        s.active = account
        s.generic_current_tab_x = account.tab_x
        runner.current = 'treasure'
        s.navigator = runner
        waiting = rec.get('awaiting')
        if waiting and s.state.action_status(account.name, waiting['action']) == 'new':
            original = receipt.get('task_records', {}).get('treasure', {})
            not_sent = (waiting == original.get('awaiting') and waiting.get('kind') == 'battle_results'
                        and receipt.get('reason', '').startswith('SafetyError: page anchor guard failed:')
                        and not any(row.get('event') == 'content_click' and row.get('target') == waiting['action']
                                    for row in events))
            if not not_sent:
                raise hs.PendingError('原确认发送状态未核实，不清除意图或重复确认')
            backup = hs.LOGS/f'treasure_not_sent_recovery_{s.diagnostics.identifier}.json'
            hs.atomic_json(backup, dict(original_record=deepcopy(rec), original_report=str(original_report),
                                       action=waiting['action'], confirmation_inputs=0))
            rec['awaiting'] = None
            result['not_sent_intent_recovery_backup'] = str(backup)
            result['original_confirmation_inputs'] = 0
        if rec.get('started_at') != timer['started_at']:
            backup = hs.LOGS/f'treasure_timer_recovery_{s.diagnostics.identifier}.json'
            hs.atomic_json(backup, dict(original_record=deepcopy(rec), timer_event=timer,
                                       original_journal=str(journal), content_clicks=0))
            rec['started_at'] = timer['started_at']
            result['timer_recovery_backup'] = str(backup)
        rec.update(status='running', last_error='')
        runner.save()
        slot = treasure.TreasureRun(runner)
        until = time.monotonic()+max_seconds
        while not slot.step():
            if time.monotonic() >= until:
                raise hs.PendingError('原挖宝续观达到上限，保留原截止时间和结果')
            s.wait_interruptible(3)
        move_cursor_clear(s)
        homes = []
        for sample in range(3):
            frame = s.capture()
            if not (s.verify_identity(account, frame)[0] and s.main_ready(frame)
                    and s.template('leaf_village_name_current.bmp', LEAF_NAME_ROI, frame, threshold=.92)[0]):
                raise hs.PendingError('挖宝停止后实名及木叶尚未三帧确认')
            homes.append(str(s.save_raw_evidence(frame, account, 'treasure_resume_home', 'verified')))
            if sample < 2:
                s.wait_interruptible(3)
        result.update(status='live_pass', home_evidence=homes, task_completed=True,
                      home_returned=True, new_start_commands=0, purchases=0,
                      resumed_after_deadline=True, timer_deadline_preserved=True,
                      acceptance_kind='original_run_recovery',
                      stop_delay_seconds=max(0, rec.get('stop_requested_at', time.time())-rec['deadline_at']))
        rec.update(acceptance_run=s.diagnostics.identifier, home_evidence=homes)
        runner.save()
    except (Exception, KeyboardInterrupt) as exc:
        result['reason'] = f'{type(exc).__name__}: {exc}'
        if owned:
            runner.record('treasure').update(status='pending', last_error=result['reason'])
            runner.save()
        frame = getattr(s, '_last_frame', None)
        if frame is not None:
            result['pending_evidence'] = str(s.save_raw_evidence(frame, account, 'treasure_resume', 'pending'))
    finally:
        s.stop.set()
        if owned:
            lease.release()
        after = s.state.accounts[account.name].get('actions', {})
        result.update(content_clicks=s.content_click_count, treasure_run=deepcopy(runner.record('treasure')),
                      unrelated_original_actions_preserved=all(after.get(key) == value for key, value in before.items()
                          if not key.startswith(('daily_treasure_', 'treasure_'))),
                      other_task_records_preserved=all(runner.records.get(key) == value for key, value in other_tasks.items()))
        result['report_path'] = str(hs.LOGS/f'treasure_resume_{s.diagnostics.identifier}.json')
        hs.atomic_json(Path(result['report_path']), result)
    return result


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--original-report', type=Path, required=True)
    result = resume(parser.parse_args().original_report)
    print(json.dumps(result, ensure_ascii=False))
    raise SystemExit(0 if result['status'] == 'live_pass' else 2)
