from pathlib import Path
import sys
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from PIL import Image
import hyol_daily_tasks as d
import hyol_scheduler as hs
im=Image.open(hs.LOGS/'ranking_result_20260916.png')
im.crop((903,656,1023,688)).save(hs.ASSETS/'ranking_result_confirm.bmp')
im.crop((917,621,1006,643)).save(hs.ASSETS/'ranking_result_stats.bmp')
r=d.DailyRunner('all');s=r.scheduler;lease=hs.ForegroundRuntimeLease()
try:
    if not lease.acquire():raise hs.PendingError('occupied')
    s.start_f12_monitor();s.bind_window(wait=True,timeout_seconds=10)
    r.account=s.bind_generic_current_window(require_game_context=False)
    r.click('ranking_result_confirm.bmp',(860,640,1060,710),55,15,'ranking_probe_result_20260916',['ranking_result_stats.bmp'],True)
    s.wait_interruptible(2);s.capture().save(hs.LOGS/'ranking_counter4_20260916.png')
finally:s.stop.set();lease.release()
