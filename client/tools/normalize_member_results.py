"""Observe current tabs, optionally acknowledge normal final result screens."""
import argparse
import json
from pathlib import Path
import sys

sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
import hyol_scheduler as hs
from hyol_accounts import observe_accounts, resolve_observations
from hyol_checkpoints import read_checkpoint
from hyol_diagnostics import manual_pause_status
from hyol_team_six import SixTabScheduler


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--confirm-results',action='store_true')
    args = parser.parse_args()
    if manual_pause_status(hs.ROOT)['paused']:
        raise hs.PendingError('人工暂停，原断点保留')
    scheduler = SixTabScheduler()
    scheduler.no_content_clicks = not args.confirm_results
    lease = hs.ForegroundRuntimeLease()
    if not lease.acquire():
        raise hs.PendingError('已有前台任务，未操作')
    report = dict(status='pending',content_clicks=0,task_credit=False)
    path = hs.LOGS/('normal_member_results_'+hs.run_id()+'.json')
    try:
        scheduler.start_f12_monitor()
        scheduler.bind_window(wait=True,timeout_seconds=10)
        first = scheduler.capture()
        if not scheduler.healthy(first):
            raise hs.PendingError('当前游戏页面不健康，未操作')
        centers,observations,frames,unavailable = observe_accounts(scheduler)
        refs = read_checkpoint(scheduler.shared_state_directory/'account_identities.json')
        mapping,_refs = resolve_observations(observations,refs)
        evidence = {slot:str(scheduler.save_raw_evidence(frame,
                    hs.Account(slot,f'当前窗口标签{slot}',centers[slot-1]),
                    'normal_member_identity','verified')) for slot,frame in frames.items()}
        report.update(status='verified' if not unavailable else 'partial',centers=centers,
                      mapping=mapping,captain_tabs=scheduler._observed_captain_tabs,
                      unavailable=unavailable,evidence=evidence,observations=3)
    except Exception as exc:
        report.update(reason=f'{type(exc).__name__}: {exc}')
        raise
    finally:
        scheduler.stop.set()
        lease.release()
        report.update(hwnd=scheduler.hwnd,content_clicks=scheduler.content_click_count)
        hs.atomic_json(path,report)
        print(json.dumps(dict(report=str(path),**report),ensure_ascii=False))


if __name__=='__main__':
    main()
