"""Bounded continuation preflight. Takes a lease; never clicks game content."""
import json
from pathlib import Path
import sys
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import hyol_scheduler as hs
from hyol_diagnostics import manual_pause_status


def run():
    pause = manual_pause_status(hs.ROOT)
    if pause['paused']:
        result = {'status': 'manual_pause', 'pause': pause, 'content_clicks': 0}
        hs.atomic_json(hs.LOGS/'preflight_live_latest.json', result)
        return result
    config = hs.read_config(hs.ROOT/'DAILY_Client.ini')
    config['AllowMarketPurchase'] = '0'
    s = hs.Scheduler(config, 'readonly_preflight', False, True)
    lease = hs.ForegroundRuntimeLease()
    result = {'run_id': s.diagnostics.identifier, 'status': 'pending'}
    try:
        if not lease.acquire():
            raise hs.PendingError('已有脚本占用窗口，预检不争用控制权')
        # Recheck after acquiring the lease in case the prior runner stopped.
        if manual_pause_status(hs.ROOT)['paused']:
            result['status'] = 'manual_pause'
            return result
        s.start_f12_monitor()
        s.bind_window(wait=True, timeout_seconds=10)
        frame = s.capture()
        healthy = s.healthy(frame)
        offline = s.offline_dialog_visible(frame)
        path = hs.LOGS/f'preflight_{s.diagnostics.identifier}.png'
        frame.save(path)
        result.update(status='offline' if offline else 'ready' if healthy else 'unhealthy', healthy=healthy,
                      offline_dialog=offline,
                      main_ready=s.main_ready(frame), evidence=str(path),
                      window=s._capture_geometry.window, content=s._capture_geometry.content)
    except KeyboardInterrupt:
        result.update(status='manual_pause', reason='F12')
        s.event('scheduler_stopped', reason='F12 preflight')
    except Exception as exc:
        result.update(status='blocked', reason=f'{type(exc).__name__}: {exc}')
        s.event('stage_pending', detail=result['reason'])
        result['diagnostic_path'] = str(s.diagnostics.last_path) if s.diagnostics.last_path else None
    finally:
        if not getattr(s, 'run_context', None):
            s.stop.set()
        lease.release()
        result.update(hwnd=s.hwnd, content_clicks=s.content_click_count)
        hs.atomic_json(hs.LOGS/f'preflight_{s.diagnostics.identifier}.json', result)
        hs.atomic_json(hs.LOGS/'preflight_live_latest.json', result)
    return result


if __name__ == '__main__':
    result = run()
    print(json.dumps(result, ensure_ascii=False))
    raise SystemExit(0 if result['status'] == 'ready' else 2)
