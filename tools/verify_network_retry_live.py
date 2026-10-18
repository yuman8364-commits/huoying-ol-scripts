"""Current-tab managed-panel retry acceptance; does not start managed mode."""
import json
from pathlib import Path
import sys
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import hyol_scheduler as hs


def run():
    s = hs.Scheduler(hs.read_config(hs.ROOT/'DAILY_Client.ini'), 'network_retry_probe', False, False)
    lease = hs.ForegroundRuntimeLease()
    owned = False
    result = {'result': 2, 'run_id': s.diagnostics.identifier}
    try:
        owned = lease.acquire()
        if not owned:
            raise hs.PendingError('已有脚本运行')
        s.start_f12_monitor()
        s.bind_window(wait=True, timeout_seconds=10)
        account = s.bind_generic_current_window(require_game_context=False)
        if s.template('mail_warehouse_anchor.bmp', (801,199,1101,389))[0]:
            if not s.click_template(account,'mail_warehouse_close.bmp',(1174,259,1275,359),20,20,
                                    'network_probe_close_warehouse',['mail_warehouse_anchor.bmp']):
                raise hs.PendingError('邮件仓库关闭按钮未确认')
            if not s.wait_template('mail_ready_anchor.bmp',(801,199,1101,369),5):
                raise hs.PendingError('邮件仓库未关闭')
        if s.template('mail_ready_anchor.bmp',(801,199,1101,369))[0]:
            if not s.click_template(account,'mail_close.bmp',(1301,249,1401,349),20,20,
                                    'network_probe_close_mail',['mail_ready_anchor.bmp']):
                raise hs.PendingError('邮件关闭按钮未确认')
            if not s.wait_until_main(5):
                raise hs.PendingError('邮件未回村')
        s.open_managed_panel_with_retry(account)
        result['result'] = 0
    except (Exception, KeyboardInterrupt) as exc:
        result['detail'] = str(exc) or 'F12停止'
    finally:
        if owned:
            try:
                path = hs.LOGS/'network_retry_managed_live.png'
                s.capture().save(path)
                result['evidence'] = str(path)
            except Exception:
                pass
            s.stop.set()
            lease.release()
        result['clicks'] = s.content_click_count
        hs.atomic_json(hs.LOGS/'network_retry_managed_live.json', result)
    print(json.dumps(result, ensure_ascii=False))
    return result['result']


if __name__ == '__main__':
    raise SystemExit(run())
