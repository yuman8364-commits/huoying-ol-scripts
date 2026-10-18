"""Audit existing six tabs without content input. Requires ready preflight."""
import json
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import hyol_scheduler as hs
from hyol_diagnostics import manual_pause_status
from hyol_multi_solo import MultiSoloRunner


def main():
    ready = json.loads((hs.LOGS / 'preflight_live_latest.json').read_text(encoding='utf-8'))
    if ready.get('status') != 'ready' or ready.get('content_clicks') != 0 or manual_pause_status(hs.ROOT)['paused']:
        raise hs.PendingError('需要当前只读预检通过且未暂停')
    runner = MultiSoloRunner(('mail', 'welfare', 'ranking', 'survival', 'quiz', 'arena'), account_count=6)
    s = runner.scheduler
    s.no_content_clicks = True
    lease = hs.ForegroundRuntimeLease()
    if not lease.acquire():
        raise hs.PendingError('已有进程占用窗口')
    results = []
    try:
        s.start_f12_monitor()
        s.bind_window(wait=True, timeout_seconds=10)
        runner.discover_tabs()
        runner.bind_accounts()
        for account in tuple(s.accounts):
            try:
                task = runner.unresolved_combat_task(account)
                if task in ('arena', 'survival'):
                    frame = s.switch_account_for_combat(account, task, running=True)
                else:
                    s.switch_account(account)
                    frame = s.capture()
                evidence = s.save_raw_evidence(frame, account, 'solo_page_audit', 'readonly')
                scores = {name: s.template(name, (450, 69, 1670, 169), frame, threshold=.92)[1:]
                          for name in ('ranking_toolbar.bmp', 'arena_toolbar_core.bmp', 'survival_toolbar_current_core.bmp')}
                results.append(dict(account=account.name, task=task, evidence=str(evidence), main=s.main_ready(frame), scores=scores))
            except (hs.PendingError, hs.SafetyError) as exc:
                results.append(dict(account=account.name, pending=str(exc)))
        output = dict(run_id=s.diagnostics.identifier, content_clicks=s.content_click_count, accounts=results)
        hs.atomic_json(hs.LOGS / 'solo_page_audit_latest.json', output)
        print(json.dumps(output, ensure_ascii=False))
    finally:
        s.stop.set()
        lease.release()


if __name__ == '__main__':
    main()
