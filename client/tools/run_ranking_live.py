"""Explicitly invoked current-tab ranking acceptance; persistent real ledger."""
from pathlib import Path
import sys
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import json
import hyol_daily_tasks as d
import hyol_scheduler as hs
import hyol_ranking as ranking

r = d.DailyRunner('ranking')
s = r.scheduler
lease = hs.ForegroundRuntimeLease()
result = {'scope': 'current visible account ranking to verified zero'}
try:
    if not lease.acquire():
        raise hs.PendingError('已有运行程序占用游戏')
    s.start_f12_monitor()
    s.bind_window(wait=True, timeout_seconds=10)
    r.account = s.bind_generic_current_window(require_game_context=False)
    r.path = s.state.account_paths[r.account.name].with_name(hs.slug(r.account.name)+'_daily_client.json')
    r.records = d.read_json(r.path)
    result['account'] = r.account.name
    # Reconcile only the inspected first probe with its actual 5 -> 4 evidence.
    probes = ('ranking_probe_first_20260916','ranking_probe_confirm_20260916',
              'ranking_probe_end_20260916','ranking_probe_result_20260916')
    if all(s.state.action_status(r.account.name,key)=='pending' for key in probes):
        if ranking.stable_remaining(r) != 4:
            raise hs.PendingError('实拍探测第一场次数不一致')
        for key in probes:
            s.commit_action(r.account,key,'人工逐帧核实5→4；logs/ranking_counter4_20260916.png')
        r.record('ranking').update(completed=1, phase='ready', remaining=4)
        r.save()
    r.record('ranking')['status']='running'
    r.save()
    ranking.run(r)
    r.return_to_main()
    r.record('ranking').update(status='done',task_completed=True,home_returned=True,last_error='')
    result['result']=0
except (Exception, KeyboardInterrupt) as exc:
    result.update(result=2, error=f'{type(exc).__name__}: {exc}')
    r.record('ranking').update(status='pending',last_error=result['error'])
    s.event('stage_pending',detail=result['error'])
finally:
    r.save()
    result.update(record=r.record('ranking'), clicks=s.content_click_count, log=str(s.log.path))
    s.stop.set()
    lease.release()
    hs.atomic_json(hs.LOGS/f'ranking_live_{s.diagnostics.identifier}.json', result)
    hs.atomic_json(hs.LOGS/'ranking_live_latest.json', result)
    print(json.dumps(result,ensure_ascii=False))
raise SystemExit(result['result'])
