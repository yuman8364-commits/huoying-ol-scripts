"""Inspect the authorized ninja fragment-recovery UI without exchanging fragments."""
import json
import argparse
from pathlib import Path
import sys
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import hyol_scheduler as hs
from hyol_daily_tasks import DailyRunner
from hyol_diagnostics import manual_pause_status
from tools.preflight_live import run as preflight


def run(exchange_dialog=False):
    check = preflight()
    if not (check.get('status') == 'ready' and check.get('healthy') is True
            and check.get('content_clicks') == 0):
        return {'status': 'blocked', 'preflight': check, 'content_clicks': 0}
    r = DailyRunner('mail')
    s = r.scheduler
    lease = hs.ForegroundRuntimeLease()
    result = {'status': 'pending', 'run_id': s.diagnostics.identifier}
    owned = False
    try:
        if manual_pause_status(hs.ROOT)['paused']:
            raise hs.PendingError('人工暂停仍有效')
        owned = lease.acquire()
        if not owned:
            raise hs.PendingError('前台租约已有持有者')
        s.start_f12_monitor()
        s.bind_window(wait=True, timeout_seconds=10)
        frame = s.capture()
        if (s.template('fragment_refinery_title_current.bmp', (680,220,950,310), frame, threshold=.98)[0]
                and s.template('fragment_refinery_close_current.bmp', (1090,260,1160,330), frame, threshold=.98)[0]):
            account = s.bind_generic_current_window(require_game_context=False)
            if not s.click_template(account, 'fragment_refinery_close_current.bmp', (1090,260,1160,330),
                                    18,18, 'inspect_fragment_close_refinery', ['fragment_refinery_title_current.bmp'], threshold=.98):
                raise hs.PendingError('炼化窗口关闭未确认')
            s.wait_interruptible(.6)
            frame = s.capture()
        if not s.main_ready(frame):
            if not (s.template('ninja_owned_title_current.bmp', (1070,280,1280,340), frame, threshold=.98)[0]
                    and s.template('ninja_close_current.bmp', (1570,100,1640,175), frame, threshold=.98)[0]):
                raise hs.PendingError('当前不是已知忍者页，未操作其他页面')
            account = s.bind_generic_current_window(require_game_context=False)
            if not s.click_template(account, 'ninja_close_current.bmp', (1570,100,1640,175),
                                    20,20, 'inspect_fragment_close_shell', ['ninja_owned_title_current.bmp'], threshold=.98):
                raise hs.PendingError('忍者页安全关闭未确认')
            if not s.wait_main_after_close(account, 'ninja_owned_title_current.bmp', (1070,280,1280,340)):
                raise hs.PendingError('忍者页关闭后主界面未确认')
        r.bind()
        if getattr(s, '_verified_current_mask', None) is None:
            raise hs.PendingError('未确认当前实名角色')
        result['account'] = r.account.name
        r.click('ninja_toolbar_current.bmp', (1380,950,1490,1030), 20,14,
                'inspect_fragment_ninja', ['anchor_top_menu.bmp'])
        r.wait('ninja_page_current.bmp', (300,270,490,345))
        r.click('ninja_cultivation_current.bmp', (1390,95,1475,175), 29,28,
                'inspect_fragment_cultivation', ['ninja_page_current.bmp'], threshold=.98)
        s.wait_interruptible(3)
        r.click('fragment_refinery_entry_current.bmp', (1480,300,1610,345), 28,7,
                'inspect_fragment_refinery', ['ninja_owned_title_current.bmp'], threshold=.98)
        s.wait_interruptible(3)
        frame = s.capture()
        if exchange_dialog:
            if not s.template('fragment_surplus_title_current.bmp', (1190,290,1410,340), frame, threshold=.98)[0]:
                raise hs.PendingError('未确认多余五星碎片范围')
            action = 'inventory_exchange_open_' + s.diagnostics.identifier
            result['action'] = action
            r.click('fragment_exchange_current.bmp', (1210,735,1370,820), 51,16,
                    action, ['fragment_surplus_title_current.bmp'], irreversible=True, threshold=.98)
            s.wait_interruptible(3)
            frame = s.capture()
        result.update(status='fragment_refinery_observed', evidence=str(s.save_raw_evidence(
            frame, r.account, 'fragment_refinery_entry_observed', 'readonly')))
    except KeyboardInterrupt:
        result.update(status='manual_pause', reason='F12')
    except Exception as exc:
        result['reason'] = str(exc)
    finally:
        s.stop.set()
        if owned:
            lease.release()
        result['content_clicks'] = s.content_click_count
        hs.atomic_json(hs.LOGS/f'fragment_refinery_inspect_{s.diagnostics.identifier}.json', result)
    return result


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--exchange-dialog', action='store_true')
    args = parser.parse_args()
    print(json.dumps(run(args.exchange_dialog), ensure_ascii=False))
