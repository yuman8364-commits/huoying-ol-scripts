"""Manually invoked, bounded group-entry repair check. Never starts a battle."""
import argparse
import json
from pathlib import Path
import sys
import tempfile
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import hyol_daily_tasks as daily
import hyol_scheduler as hs
from hyol_diagnostics import manual_pause_status


def run():
    lease = hs.ForegroundRuntimeLease()
    result = {'scope': 'current-tab group entry and return only; no battle',
              'authorization': 'current user requested group-instance error repair after manual GUI start at 20:39',
              'previous_pause': manual_pause_status(hs.ROOT)}
    # Keep the existing task's combat ledger untouched during a navigation probe.
    with tempfile.TemporaryDirectory(prefix='hyol_group_entry_') as folder:
        hs.STATE_DIR = Path(folder) / 'state'
        r = daily.DailyRunner('team', 1, selected_tasks=['group'])
        s = r.scheduler
        try:
            if not lease.acquire():
                raise hs.PendingError('已有脚本运行，入口验证不争用窗口')
            s.start_f12_monitor()
            s.bind_window(wait=True, timeout_seconds=10)
            frame = s.capture()
            if not s.healthy(frame):
                raise hs.PendingError('当前页面不健康，未执行入口验证')
            frame.save(hs.LOGS/'group_entry_preflight_20260915.png')
            result.update(preflight='ready', hwnd=s.hwnd, main_before=s.main_ready(frame),
                          target_card_before=s._target_card_visible(frame))
            hs.atomic_json(hs.LOGS/'group_entry_preflight_20260915.json', result)
            real_click = s.click_point
            allowed = {'target_card_close_recover', 'daily_open', 'daily_tab_limited',
                       'daily_select_group', 'daily_scroll_up', 'daily_scroll_down',
                       'daily_participate_group', 'safe_close_team_instance_close.bmp',
                       'close_lower_right_activity_notice',
                       'safe_close_daily_practice_close.bmp', 'safe_return_leaf'}
            def bounded_click(account, x, y, label, anchors, irreversible=False, **kwargs):
                if irreversible or label not in allowed:
                    raise hs.PendingError('入口验证未授权该操作：' + label)
                return real_click(account, x, y, label, anchors, irreversible, **kwargs)
            s.click_point = bounded_click
            r.bind()
            result.update(account=r.account.name, main_after_cleanup=s.main_ready())
            r.enter('group')
            s.capture().save(hs.LOGS/'group_entry_opened_20260915.png')
            result['group_entry_verified'] = r.visible(daily.PAGE_ANCHORS['group'], daily.PAGE_ROIS['group'])
            r.return_to_main()
            result.update(returned=True, result=0)
        except (Exception, KeyboardInterrupt) as exc:
            result.update(result=2, error=f'{type(exc).__name__}: {exc}')
            s.event('stage_pending', detail=result['error'])
        finally:
            s.stop.set()
            lease.release()
            result.update(content_clicks=s.content_click_count, log=str(s.log.path))
            hs.atomic_json(hs.LOGS/f'group_entry_live_{s.diagnostics.identifier}.json', result)
            hs.atomic_json(hs.LOGS/'group_entry_live_20260915.json', result)
    return result


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--live', action='store_true', required=True)
    parser.parse_args()
    result = run()
    print(json.dumps(result, ensure_ascii=False))
    raise SystemExit(result['result'])
