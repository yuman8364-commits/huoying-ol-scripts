"""Continue the remaining accounts while one audited arena queue is deferred."""
import json
import argparse
from datetime import date
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import hyol_scheduler as hs
from hyol_diagnostics import manual_pause_status
from hyol_multi_solo import MultiSoloRunner


def main() -> int:
    if manual_pause_status(hs.ROOT)['paused']:
        raise hs.PendingError('F12暂停仍在，须先通过审计恢复入口')
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--defer-account',required=True)
    parser.add_argument('--pending-action',required=True)
    args = parser.parse_args()
    account = args.defer_account
    from hyol_accounts import state_directory
    directory = state_directory(hs.read_config(hs.CONFIG_PATH),hs.ROOT)
    ledger=directory/date.today().isoformat()/f'{hs.slug(account)}.json'
    actions=json.loads(ledger.read_text(encoding='utf-8')).get('actions',{})
    if not args.pending_action.startswith('arena_') or actions.get(args.pending_action,{}).get('status')!='pending':
        raise hs.PendingError('指定竞技场未决动作不符，禁止跳过未知场次')
    tasks=('mail','welfare','ranking','survival','quiz','arena')
    runner=MultiSoloRunner(tasks,account_count=6,arena_wins=5,
        event_sink=lambda event: print(json.dumps(event,ensure_ascii=False),flush=True)
        if str(event.get('event','')).startswith('multi_solo_') else None)
    runner.deferred[account]='arena'
    runner.manual_deferred.add(account)
    runner.event('multi_solo_manual_defer_requested',
        task='arena',identity=account,action=args.pending_action,
        detail='明确请求暂缓指定竞技场未决；保留原记录并继续其他账号')
    return runner.run()


if __name__=='__main__':
    raise SystemExit(main())
