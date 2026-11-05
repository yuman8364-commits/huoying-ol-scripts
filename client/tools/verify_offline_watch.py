"""Exercise the live disconnect gate on one tab without game-content input."""
import json
from pathlib import Path
import sys
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import hyol_scheduler as hs
from hyol_multi_solo import tab_close_positions, active_tab_centers
from hyol_diagnostics import manual_pause_status


def run():
    s = hs.Scheduler(hs.read_config(hs.ROOT/'DAILY_Client.ini'), 'offline_watch_validation', False, True)
    lease = hs.ForegroundRuntimeLease()
    result = {'run_id': s.diagnostics.identifier, 'status': 'pending'}
    original = None
    try:
        if manual_pause_status(hs.ROOT)['paused'] or not lease.acquire():
            raise hs.PendingError('F12暂停或前台工作线程仍有效')
        s.start_f12_monitor()
        s.bind_window(wait=True, timeout_seconds=10)
        frame = s.capture()
        centers = active_tab_centers(tab_close_positions(frame))
        s.config['TabXs'] = ','.join(map(str, centers))
        original = s._generic_active_tab_x(frame)
        if len(centers) != 6:
            raise hs.PendingError('本轮验证需要已核实的六标签几何')
        # Slot 6 was positively observed with the central dialog in this run.
        # Its role name remains unknown under the dialog; only the tab slot is recorded.
        account = hs.Account(6, '当前窗口标签6', centers[5])
        s.chrome_click(account.tab_x, 10, 'offline_watch_probe', None)
        s.wait_interruptible(3)
        frame = s.capture()
        if not s.offline_dialog_visible(frame):
            raise hs.PendingError('当前未显示掉线弹窗，不标记掉线')
        s.accounts, s.active = [account], account
        s._capture_account, s._capture_binding_probe = account, False
        s.state = hs.StateStore([account.name], directory=hs.LOGS/'connection_live'/s.diagnostics.identifier)
        s.account_logs = {account.name: hs.JsonlLogger(hs.LOGS/'connection_live'/s.diagnostics.identifier/'slot6.jsonl')}
        s.no_content_clicks = False
        try:
            s.check_offline_frame(frame)
        except hs.AccountOffline as exc:
            result.update(status='offline_skipped', tab_slot=6, evidence=exc.evidence,
                          account_identity='unconfirmed; only slot attributed')
    except KeyboardInterrupt:
        result.update(status='manual_pause', reason='F12')
    except Exception as exc:
        result.update(status='pending', reason=f'{type(exc).__name__}: {exc}')
    finally:
        s.no_content_clicks = True
        s.active = None
        if original is not None and not s.stop.is_set():
            try:
                s.chrome_click(original, 10, 'offline_watch_restore', None)
                s.wait_interruptible(.3)
            except Exception as exc:
                result['restore_pending'] = str(exc)
        s.stop.set()
        lease.release()
        result['content_clicks'] = s.content_click_count
        hs.atomic_json(hs.LOGS/f'offline_watch_{s.diagnostics.identifier}.json', result)
    return result


if __name__ == '__main__':
    print(json.dumps(run(), ensure_ascii=False))
