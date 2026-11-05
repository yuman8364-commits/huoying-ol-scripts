"""Bounded continuation preflight. Takes a lease; never clicks game content."""
import json
from pathlib import Path
import sys
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import hyol_scheduler as hs
from hyol_diagnostics import manual_pause_status


def run(*,find_ready_tab=False,prepare_scope=None,stop_event=None,event_sink=None,on_scheduler=None,preserve_bond=False):
    if prepare_scope is not None:
        if preserve_bond:
            from hyol_bond import prepare_current_window
            return prepare_current_window(hs.ROOT, stop_event=stop_event, event_sink=event_sink,
                                          on_scheduler=on_scheduler)
        from hyol_preparation import run as prepare
        return prepare(hs.ROOT,prepare_scope,stop_event=stop_event,event_sink=event_sink,on_scheduler=on_scheduler)
    pause = manual_pause_status(hs.ROOT)
    if pause['paused']:
        result = {'status': 'manual_pause', 'pause': pause, 'content_clicks': 0}
        hs.atomic_json(hs.LOGS/'preflight_live_latest.json', result)
        return result
    config = hs.read_config(hs.ROOT/'DAILY_Client.ini')
    config['AllowMarketPurchase'] = '0'
    # This is a read-only inspection, not authorization to bring a window
    # forward. Preparation already performs the one explicit Start handoff.
    config['AutoForeground'] = '0'
    s = hs.Scheduler(config, 'readonly_preflight', False, True)
    # Reuse the scheduler's browser-only discovery gate on an empty tab.
    # This permits only existing title selection, never content input.
    s._capture_binding_probe = bool(find_ready_tab)
    lease = hs.ForegroundRuntimeLease()
    result = {'run_id': s.diagnostics.identifier, 'status': 'pending'}

    def check_stop():
        # The read-only branch has no scheduler stop of its own; honor the
        # caller's stop_event so the UI stop button is not ignored here.
        if stop_event is not None and stop_event.is_set():
            raise KeyboardInterrupt('pre-flight stopped by caller')

    try:
        if not lease.acquire():
            raise hs.PendingError('已有脚本占用窗口，预检不争用控制权')
        # Recheck after acquiring the lease in case the prior runner stopped.
        if manual_pause_status(hs.ROOT)['paused']:
            result['status'] = 'manual_pause'
            return result
        check_stop()
        s.start_f12_monitor()
        s.bind_window(wait=True, timeout_seconds=10)
        check_stop()
        frame = s.capture_account_observation() if find_ready_tab else s.capture()
        check_stop()
        chrome_only = bool(getattr(frame, '_hyol_chrome_only', False))
        if find_ready_tab and (chrome_only or s.offline_dialog_visible(frame) or not s.main_ready(frame)):
            from hyol_multi_solo import tab_close_positions,active_tab_centers
            markers=tab_close_positions(frame)
            s.wait_interruptible(.2)
            if markers!=tab_close_positions(s.capture_account_observation()):
                raise hs.PendingError('预检时标签布局变化，未查找其他账号')
            centers=active_tab_centers(markers)
            for slot,center in enumerate(centers,1):
                s.chrome_click(center,10,f'observe_account:{slot}',hs.Account(slot,'preflight-tab',center))
                s.wait_interruptible(.5)
                try:
                    observed=s.capture_account_observation()
                except (hs.PendingError,hs.SafetyError):
                    continue
                if getattr(observed, '_hyol_chrome_only', False):
                    frame=observed
                    continue
                from hyol_navigation import hot_spring_visible
                if (s.healthy(observed) and not s.offline_dialog_visible(observed)
                        and (s.main_ready(observed) or hot_spring_visible(s, observed))):
                    frame=observed
                    result['ready_tab']=slot
                    break
                frame=observed
        if getattr(frame, '_hyol_chrome_only', False):
            raise hs.PendingError('所有标签均未确认可用游戏主界面或温泉页；未发送内容输入')
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
