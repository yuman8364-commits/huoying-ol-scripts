"""Close an original anchored ranking page, reveal its owner, then reconcile.

Requires a recent named acceptance receipt from the same browser process,
unchanged counter evidence, unchanged browser chrome and own-formation panel.
Never starts a challenge, repeats a result click or creates a new battle.
"""
import argparse
from copy import deepcopy
from datetime import datetime
import hashlib
import json
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import hyol_scheduler as hs
import hyol_ranking as ranking
from hyol_daily_tasks import DailyRunner
from hyol_navigation import LEAF_NAME_ROI, move_cursor_clear
from tools.preflight_live import run as preflight

OWN_PANEL = (270, 105, 525, 424)


def same_owned_panel(frame, reference):
    # Flash sometimes changes one channel by one level on an otherwise
    # identical pixel. Accept only that rounding, never changed text/features.
    current = hs.np.asarray(frame.crop(OWN_PANEL), dtype=hs.np.int16)
    original = hs.np.asarray(reference.crop(OWN_PANEL), dtype=hs.np.int16)
    return bool(hs.np.max(hs.np.abs(current-original)) <= 2)


def recover(report_path):
    receipt = json.loads(report_path.read_text(encoding='utf-8-sig'))
    if (receipt.get('task') != 'ranking' or receipt.get('status') != 'pending'
            or receipt.get('unrelated_original_actions_preserved') is not True
            or not receipt.get('account') or receipt.get('account') != receipt.get('expected_account')):
        raise hs.PendingError('原排位报告未绑定唯一实名账号')
    check = preflight()
    if not (check.get('status') == 'ready' and check.get('healthy') is True and check.get('content_clicks') == 0):
        return dict(status='blocked', preflight=check, content_clicks=0)
    journal = hs.LOGS/('HYOL_Scheduler_'+receipt['run_id']+'.jsonl')
    events = [json.loads(line) for line in journal.read_text(encoding='utf-8-sig').splitlines() if line.strip()]
    bound = next(row for row in events if row['event'] == 'window_bound')
    mapping = next(row['mapping'] for row in events if row['event'] == 'identity_rebound')
    source = Path(receipt['pending_evidence'])
    with hs.Image.open(source) as image:
        reference = image.convert('RGB')
    config = hs.read_config(hs.ROOT/'HYOL_Daily.ini')
    config.update(AllowMarketPurchase='0', AutoForeground='0', AutoRefreshWhiteTab='0')
    s = hs.Scheduler(config, 'multi_solo', False, False)
    s.auto_foreground = False
    original = s.by_name[receipt['account']]
    account = hs.Account(original.index, original.name, mapping[original.name])
    s.accounts, s.by_name = [account], {account.name: account}
    runner = DailyRunner.for_bound_account(s, account, ('ranking',), managed_enabled=False)
    before = deepcopy(s.state.accounts[account.name].get('actions', {}))
    result = dict(status='pending', run_id=s.diagnostics.identifier, preflight=check,
                  original_report=str(report_path), original_report_sha256=hashlib.sha256(report_path.read_bytes()).hexdigest(),
                  source=str(source), source_sha256=hashlib.sha256(source.read_bytes()).hexdigest(), account=account.name)
    lease, owned = hs.ForegroundRuntimeLease(), False
    try:
        owned = lease.acquire()
        if not owned:
            raise hs.PendingError('已有脚本占用游戏窗口')
        s.start_f12_monitor()
        s.bind_window(wait=True, timeout_seconds=10)
        if s.hwnd != bound['hwnd'] or hs.Win32.pid(s.hwnd) != bound['pid']:
            raise hs.PendingError('浏览器进程与原实名运行不同，禁止按旧页面关联角色')
        context_at = datetime.fromisoformat(bound['ts'])
        if not 0 <= datetime.now().astimezone().timestamp()-context_at.timestamp() <= 3600:
            raise hs.PendingError('原实名运行超过一小时，只保留断点')
        handle = hs.Win32.kernel32.OpenProcess(hs.Win32.PROCESS_QUERY_LIMITED_INFORMATION, False, bound['pid'])
        if not handle:
            raise hs.PendingError('无法核实当前浏览器创建时间')
        try:
            created, end, kernel, user = (hs.ctypes.c_ulonglong() for _ in range(4))
            if not hs.Win32.kernel32.GetProcessTimes(handle, hs.ctypes.byref(created), hs.ctypes.byref(end), hs.ctypes.byref(kernel), hs.ctypes.byref(user)):
                raise hs.PendingError('浏览器创建时间读取失败')
            if created.value/10000000-11644473600 > context_at.timestamp():
                raise hs.PendingError('浏览器在原实名证据之后重新启动，禁止复用')
        finally:
            hs.Win32.kernel32.CloseHandle(handle)
        rec = runner.record('ranking')
        if rec != receipt['task_records']['ranking'] or not ranking.sent_result_can_reopen(runner, rec):
            raise hs.PendingError('原场状态、动作或次数证据已经变化，不关闭或对账')
        counts = []
        for sample in range(2):
            frame = s.capture()
            if not s.tab_is_visually_active(account, frame) or frame.crop((0, 0, 1920, 35)).tobytes() != reference.crop((0, 0, 1920, 35)).tobytes():
                raise hs.PendingError('浏览器标签或标题与原场不一致')
            if not same_owned_panel(frame, reference):
                raise hs.PendingError('当前排位自身阵容/战力与原实名页面不同')
            counts.append(ranking.remaining(runner, frame, save_unknown=False))
            if sample == 0:
                s.wait_interruptible(3)
        if counts != [rec['before']-1]*2:
            raise hs.PendingError('原场次数未恰好减一，保留原动作')
        s.active = account
        s.generic_current_tab_x = account.tab_x
        runner.click('ranking_close.bmp', (1600,95,1670,160), 17,17,
                     'ranking_recovery_close', ['ranking_page.bmp', 'ranking_counter_label.bmp'])
        if not s.wait_until_main(12):
            raise hs.PendingError('原排位关页后未恢复主界面，未提交结果')
        move_cursor_clear(s)
        homes = []
        for sample in range(3):
            frame = s.capture()
            if not (s.verify_identity(account, frame)[0] and s.main_ready(frame)
                    and s.template('leaf_village_name_current.bmp', LEAF_NAME_ROI, frame, threshold=.92)[0]):
                raise hs.PendingError('关页后实名或木叶未确认，原场不提交')
            homes.append(str(s.save_raw_evidence(frame, account, 'ranking_recovery_home', 'verified')))
            if sample < 2:
                s.wait_interruptible(3)
        index = rec['completed']+1
        base = f'daily_ranking_{index}'
        for suffix in ('start', 'confirm', 'end', 'result', 'result_recover'):
            if s.state.action_status(account.name, base+'_'+suffix) == 'pending':
                s.commit_action(account, base+'_'+suffix, f'原场同会话次数{rec["before"]}→{counts[0]}；关页后实名及木叶三帧确认')
        rec.update(completed=index, phase='ready', remaining=counts[0], status='pending',
                   home_returned=True, home_evidence=homes, last_error='原场已确认，尚需继续剩余次数', task_completed=False)
        runner.save()
        result.update(status='original_result_reconciled', completed=index, remaining=counts[0],
                      home_evidence=homes, task_completed=False, new_battles=0, repeated_result_inputs=0)
    except (Exception, KeyboardInterrupt) as exc:
        result['reason'] = f'{type(exc).__name__}: {exc}'
        frame = getattr(s, '_last_frame', None)
        if frame is not None:
            result['pending_evidence'] = str(s.save_raw_evidence(frame, account, 'ranking_page_recovery', 'pending'))
    finally:
        s.stop.set()
        if owned:
            lease.release()
        base = f'daily_ranking_{receipt["task_records"]["ranking"]["completed"]+1}_'
        after = s.state.accounts[account.name].get('actions', {})
        result.update(content_clicks=s.content_click_count, unrelated_original_actions_preserved=all(
            after.get(key) == value for key, value in before.items() if not key.startswith(base)))
        result['report_path'] = str(hs.LOGS/f'ranking_page_recovery_{s.diagnostics.identifier}.json')
        hs.atomic_json(Path(result['report_path']), result)
    return result


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--original-report', type=Path, required=True)
    result = recover(parser.parse_args().original_report)
    print(json.dumps(result, ensure_ascii=False))
    raise SystemExit(0 if result['status'] == 'original_result_reconciled' else 2)
