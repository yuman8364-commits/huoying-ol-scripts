"""Audit the open browser tabs without clicking game content."""
import json
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import hyol_scheduler as hs
from hyol_diagnostics import manual_pause_status
from hyol_multi_solo import MultiSoloRunner


def run():
    coordinator = MultiSoloRunner(('mail',))
    s = coordinator.scheduler
    s.no_content_clicks = True
    lease = hs.ForegroundRuntimeLease()
    result = {'run_id': s.diagnostics.identifier, 'accounts': [], 'status': 'pending'}
    try:
        if manual_pause_status(hs.ROOT)['paused']:
            raise hs.PendingError('F12暂停仍有效')
        if not lease.acquire():
            raise hs.PendingError('已有工作线程占用游戏窗口')
        s.start_f12_monitor()
        s.bind_window(wait=True, timeout_seconds=10)
        coordinator.discover_tabs()
        coordinator.bind_accounts()
        for account in s.accounts:
            s.chrome_click(account.tab_x, 10, 'observe_account:' + account.name, account)
            s.wait_interruptible(3)
            frame = s.capture()
            ok, scores = s.verify_identity(account, frame)
            path = s.save_raw_evidence(frame, account, 'account_observation', 'verified' if ok else 'pending')
            result['accounts'].append({'name': account.name, 'tab_x': account.tab_x,
                'identity_verified': ok, 'scores': scores, 'main_ready': s.main_ready(frame),
                'evidence': str(path)})
        result.update(status='observed', tab_count=coordinator.tab_count,
                      unavailable=sorted(coordinator.initial_unavailable))
    except KeyboardInterrupt:
        result.update(status='manual_pause', reason='F12')
    except Exception as exc:
        result.update(status='pending', reason=f'{type(exc).__name__}: {exc}')
    finally:
        s.stop.set()
        lease.release()
        result.update(hwnd=s.hwnd, content_clicks=s.content_click_count)
        hs.atomic_json(hs.LOGS / f'accounts_observed_{s.diagnostics.identifier}.json', result)
        hs.atomic_json(hs.LOGS / 'accounts_observed_latest.json', result)
    return result


if __name__ == '__main__':
    print(json.dumps(run(), ensure_ascii=False))
