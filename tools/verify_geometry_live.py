"""Authorized read-only recognition check at resized windows; no game-content clicks."""
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import json
import time
import hyol_scheduler as hs
from hyol_diagnostics import manual_pause_status


def main():
    if manual_pause_status(hs.ROOT)['paused']:
        raise SystemExit('manual pause is active; live inspection not resumed')
    config = hs.read_config(hs.ROOT/'DAILY_Client.ini')
    config['AllowMarketPurchase'] = '0'
    s = hs.Scheduler(config, 'geometry_validation', False, True)
    lease = hs.ForegroundRuntimeLease()
    original, rows = None, []
    try:
        if not lease.acquire():
            raise hs.PendingError('game window already leased')
        s.start_f12_monitor()
        s.bind_window(wait=False)
        original = hs.Win32.rect(s.hwnd)
        for width,height in ((1600,900),(1280,800)):
            s.check_stop()
            if not hs.Win32.user32.SetWindowPos(s.hwnd,0,30,30,width,height,0x0014):
                raise hs.SafetyError('window resize failed')
            s.wait_interruptible(2)
            frame = s.capture()
            path = hs.LOGS/f'geometry_live_{width}x{height}_{s.diagnostics.identifier}.png'
            frame.save(path)
            rows.append({'requested_size':[width,height], 'window':s._capture_geometry.window,
                'content':s._capture_geometry.content, 'healthy':s.healthy(frame),
                'main_ready':s.main_ready(frame),
                'daily_toolbar':s.template('daily_practice_toolbar.bmp',hs.VIEW,frame),
                'yolo':[d.as_dict() for d in s.detector.predict(frame)],
                'evidence':str(path), 'content_clicks':s.content_click_count})
    except KeyboardInterrupt:
        s.event('scheduler_stopped', reason='F12 geometry validation')
        raise
    finally:
        if original:
            l,t,r,b = original
            hs.Win32.user32.SetWindowPos(s.hwnd,0,l,t,r-l,b-t,0x0014)
        s.stop.set()
        lease.release()
        hs.atomic_json(hs.LOGS/'geometry_live_latest.json', {'rows':rows,'restored_rect':hs.Win32.rect(s.hwnd) if s.hwnd else None,'content_clicks':s.content_click_count})
    print(json.dumps(rows,ensure_ascii=False))


if __name__=='__main__':
    main()
