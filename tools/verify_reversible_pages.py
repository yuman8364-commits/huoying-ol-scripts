"""Live validation of mailbox observation and formation-page closure only."""
import argparse
import json
from pathlib import Path
import sys
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import hyol_scheduler as hs
from hyol_diagnostics import manual_pause_status
from hyol_daily_tasks import DailyRunner


def run(kind):
    runner = DailyRunner('mail')
    s = runner.scheduler
    lease = hs.ForegroundRuntimeLease()
    result = {'kind': kind, 'status': 'pending', 'run_id': s.diagnostics.identifier}
    try:
        if manual_pause_status(hs.ROOT)['paused'] or not lease.acquire():
            raise hs.PendingError('人工暂停或前台租约尚未释放')
        s.start_f12_monitor()
        runner.bind()
        if getattr(s, '_verified_current_mask', None) is None:
            raise hs.PendingError('必须唯一确认账号后才执行本轮页面验证')
        result['account'] = runner.account.name
        if not s.main_ready():
            raise hs.PendingError('页面验证前主界面未确认')
        if kind == 'mail':
            s.module_mail(runner.account)
        else:
            runner.verify_main_formation()
        result['status'] = 'page_closed'
        result['evidence'] = str(s.save_raw_evidence(s.capture(),runner.account,kind+'_page_closed','verified'))
        result['main_ready'] = s.main_ready()
        # This tool deliberately reports page closure, never a full daily task
        # completion or village return; those require the normal runner's gates.
    except KeyboardInterrupt:
        result.update(status='manual_pause', reason='F12')
    except Exception as exc:
        result.update(status='pending', reason=f'{type(exc).__name__}: {exc}')
        if s.hwnd and runner.account:
            try:
                result['evidence'] = str(s.save_raw_evidence(s.capture(),runner.account,kind+'_validation_pending','pending'))
            except Exception:
                pass
    finally:
        s.stop.set()
        lease.release()
        result['content_clicks'] = s.content_click_count
        hs.atomic_json(hs.LOGS/f'reversible_{kind}_{s.diagnostics.identifier}.json', result)
    return result


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('kind', choices=('mail', 'formation'))
    args = parser.parse_args()
    result = run(args.kind)
    print(json.dumps(result, ensure_ascii=False))
    raise SystemExit(0 if result['status'] == 'page_closed' else 2)
