from pathlib import Path
import sys
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from PIL import Image
import hyol_daily_tasks as d
import hyol_scheduler as hs
im=Image.open(hs.LOGS/'ranking_first_battle_20260916.png')
im.crop((803,517,1116,553)).save(hs.ASSETS/'ranking_power_warning.bmp')
im.crop((839,576,911,596)).save(hs.ASSETS/'ranking_power_confirm.bmp')
r=d.DailyRunner('all');s=r.scheduler;lease=hs.ForegroundRuntimeLease()
try:
    if not lease.acquire():raise hs.PendingError('occupied')
    s.start_f12_monitor();s.bind_window(wait=True,timeout_seconds=10)
    r.account=s.bind_generic_current_window(require_game_context=False)
    r.click('ranking_power_confirm.bmp',(810,560,940,610),30,10,'ranking_probe_confirm_20260916',['ranking_power_warning.bmp'],True)
    s.wait_interruptible(5);s.capture().save(hs.LOGS/'ranking_combat_20260916.png')
finally:s.stop.set();lease.release()
