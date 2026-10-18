from pathlib import Path
import sys
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
import hyol_scheduler as hs
import hyol_daily_tasks as d
import hyol_ranking as rk
r=d.DailyRunner('ranking');s=r.scheduler;lease=hs.ForegroundRuntimeLease()
try:
    if not lease.acquire():raise hs.PendingError('occupied')
    s.start_f12_monitor();s.bind_window(wait=True,timeout_seconds=10)
    r.account=s.bind_generic_current_window(require_game_context=False)
    r.path=hs.STATE_DIR/s.state.day/(hs.slug(r.account.name)+'_daily_client.json');r.records=d.read_json(r.path)
    rec=r.record('ranking');base=f'daily_ranking_{rec["completed"]+1}'
    if r.account.index != 2 or rec['phase'] != 'result_sent':raise hs.PendingError('not reviewed pending result')
    if s.state.action_status(r.account.name,base+'_result_recover') != 'new':raise hs.PendingError('already recovered')
    for _ in range(3):
        if not r.visible('ranking_result_confirm.bmp',rk.RESULT) or not r.visible('ranking_result_stats.bmp',(890,605,1035,653)):raise hs.PendingError('result changed')
        s.wait_interruptible(1)
    s.save_raw_evidence(s.capture(),r.account,'ranking_result_after_skill_dialogue','verified')
    r.click('ranking_result_confirm.bmp',rk.RESULT,55,15,base+'_result_recover',['ranking_result_stats.bmp'],True)
    s.wait_interruptible(2);s.capture().save(hs.LOGS/'ranking_tab2_recovered.png')
finally:s.stop.set();lease.release()
