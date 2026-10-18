"""Observe generic team identities; never creates a room or sends an invite."""
import json
import argparse
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import hyol_scheduler as hs
from hyol_checkpoints import read_checkpoint
from hyol_diagnostics import manual_pause_status
from hyol_team_six import SixTabScheduler
from tools.preflight_live import run as preflight


def run(slots=tuple(range(1, 7))):
    check = preflight()
    ready = (check.get('status') == 'ready' and check.get('healthy') is True
             and check.get('content_clicks') == 0)
    offline_chrome_only = (check.get('status') == 'offline'
                          and check.get('offline_dialog') is True
                          and check.get('content_clicks') == 0)
    if not (ready or offline_chrome_only):
        return {'status': 'blocked', 'preflight': check, 'content_clicks': 0}
    s = SixTabScheduler()
    s.no_content_clicks = True
    lease = hs.ForegroundRuntimeLease()
    owned = False
    identities = read_checkpoint(s.shared_state_directory/'account_identities.json')
    def actions():
        return {name: read_checkpoint(s.shared_state_directory/s.state.day/(hs.slug(name)+'.json')).get('actions', {})
                for name in identities}
    before = actions()
    result = {'status': 'pending', 'run_id': s.diagnostics.identifier, 'requested_slots': list(slots)}
    try:
        if manual_pause_status(hs.ROOT)['paused']:
            raise hs.PendingError('人工暂停仍在')
        owned = lease.acquire()
        if not owned:
            raise hs.PendingError('已有工作线程占用游戏窗口')
        s.start_f12_monitor()
        s.bind_session(slots)
        selected = [account for account in s.accounts if account.index in slots]
        binding = s.require_named_members(tuple(account.name for account in selected))
        rows = []
        for account in selected:
            frame = s.switch_account(account)
            verified = s.verify_identity(account, frame)[0]
            evidence = s.save_raw_evidence(frame, account, 'generic_team_identity_only', 'verified' if verified else 'pending')
            rows.append({'slot': account.index, 'name': binding[account.name], 'tab_x': account.tab_x,
                         'identity_verified': verified, 'evidence': str(evidence)})
        result.update(status='verified' if all(row['identity_verified'] for row in rows) else 'pending', accounts=rows,
                      role_binding=binding, observations=2)
    except KeyboardInterrupt:
        result.update(status='manual_pause', reason='F12')
    except Exception as exc:
        result['reason'] = f'{type(exc).__name__}: {exc}'
    finally:
        s.stop.set()
        if owned:
            lease.release()
        result.update(content_clicks=s.content_click_count, original_actions_preserved=before == actions(),
                      room_created=False, invite_sent=False)
        hs.atomic_json(hs.LOGS/f'team_identity_{s.diagnostics.identifier}.json', result)
    return result


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--slots', nargs='+', type=int, default=list(range(1, 7)))
    args = parser.parse_args()
    if (not 2 <= len(args.slots) <= 6 or len(set(args.slots)) != len(args.slots)
            or any(slot not in range(1, 7) for slot in args.slots)):
        parser.error('select 2..6 unique existing tab numbers in 1..6')
    result = run(tuple(args.slots))
    print(json.dumps(result, ensure_ascii=False))
    raise SystemExit(0 if result['status'] == 'verified' and result['content_clicks'] == 0
                     and result['original_actions_preserved'] else 2)
