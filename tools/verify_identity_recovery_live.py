"""Rebind all tabs, recover known shells and preserve every original action."""
from copy import deepcopy
import json
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import hyol_scheduler as hs
from hyol_diagnostics import manual_pause_status
from hyol_multi_solo import MultiSoloRunner
from hyol_navigation import LEAF_NAME_ROI, move_cursor_clear
from tools.preflight_live import run as preflight


def run():
    check = preflight()
    # This probe starts with browser chrome only. A healthy managed page need
    # not be a main page; no task is started, paused, returned, or settled.
    ready = (check.get('status') == 'ready' and check.get('healthy') is True
             and check.get('content_clicks') == 0)
    offline_chrome_only = (check.get('status') == 'offline'
                          and check.get('offline_dialog') is True
                          and check.get('content_clicks') == 0)
    if not (ready or offline_chrome_only):
        return {'status': 'blocked', 'preflight': check, 'content_clicks': 0}
    runner = MultiSoloRunner(('mail',))
    s = runner.scheduler
    lease = hs.ForegroundRuntimeLease()
    acquired = False
    result = {'run_id': s.diagnostics.identifier, 'accounts': [], 'status': 'pending'}
    before = deepcopy({name: row.get('actions', {}) for name, row in s.state.accounts.items()})
    try:
        if manual_pause_status(hs.ROOT)['paused']:
            raise hs.PendingError('人工暂停仍在')
        acquired = lease.acquire()
        if not acquired:
            raise hs.PendingError('已有工作线程占用游戏窗口')
        s.start_f12_monitor()
        s.bind_window(wait=True, timeout_seconds=10)
        runner.discover_tabs()
        runner.bind_accounts()
        result['unavailable_candidate_names'] = sorted(runner.initial_unavailable)
        result['all_six_verified'] = not runner.initial_unavailable and len(s.accounts) == 6
        # Unavailable slots do not prevent observing the positively bound ones.
        # Their configured names are candidates, never a verified offline identity.
        for account in s.available_accounts():
            frame = s.switch_account(account)
            was_weekend = s.template('daruma_weekend_page.bmp', (850,230,1180,360), frame, threshold=.98)[0]
            was_welfare = s.template('welfare_title_current.bmp', (1120,230,1320,380), frame, threshold=.98)[0]
            # Only the positively known reversible shell is recovered here.
            if was_weekend or was_welfare:
                s.prepare_main(account, clear_notifications=False)
            move_cursor_clear(s)
            evidence = []
            main_samples, leaf_samples = [], []
            for sample in range(3):
                frame = s.capture()
                if not s.verify_identity(account, frame)[0]:
                    raise hs.PendingError(account.name+'三帧身份尚未确认')
                main_samples.append(s.main_ready(frame))
                leaf_samples.append(s.template('leaf_village_name_current.bmp', LEAF_NAME_ROI, frame, threshold=.92)[0])
                path = s.save_raw_evidence(frame, account, 'identity_shell_recovery', 'verified')
                evidence.append(str(path))
                if sample < 2:
                    s.wait_interruptible(3)
            result['accounts'].append({'name': account.name, 'tab_x': account.tab_x,
                'identity_verified': True, 'main_ready': all(main_samples), 'evidence': evidence,
                'main_samples': main_samples, 'leaf_samples': leaf_samples,
                'shell_recovered': 'weekend' if was_weekend else 'welfare' if was_welfare else '',
                'leaf_visible': all(leaf_samples)})
        result['status'] = ('verified' if result['all_six_verified']
            and all(row['main_ready'] and row['leaf_visible'] for row in result['accounts']) else 'partial')
    except KeyboardInterrupt:
        result.update(status='manual_pause', reason='F12')
    except Exception as exc:
        result['reason'] = f'{type(exc).__name__}: {exc}'
    finally:
        s.stop.set()
        if acquired:
            lease.release()
        result.update(hwnd=s.hwnd, content_clicks=s.content_click_count,
            original_actions_preserved=all(s.state.accounts.get(name, {}).get('actions', {}) == actions
                                           for name, actions in before.items()))
        if result['original_actions_preserved'] is not True:
            result['status'] = 'pending'
        hs.atomic_json(hs.LOGS/f'identity_recovery_{s.diagnostics.identifier}.json', result)
    return result


if __name__ == '__main__':
    result = run()
    print(json.dumps(result, ensure_ascii=False))
    raise SystemExit(0 if result['status'] == 'verified' else 2)
