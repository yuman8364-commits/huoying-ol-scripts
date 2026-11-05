from pathlib import Path
import sys
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from PIL import Image
import hyol_daily_tasks as d
import hyol_scheduler as hs
im=Image.open(hs.LOGS/'ranking_combat_20260916.png')
im.crop((1450,884,1533,952)).save(hs.ASSETS/'ranking_end_battle.bmp')
im.crop((1420,218,1498,239)).save(hs.ASSETS/'ranking_battle_anchor.bmp')
r=d.DailyRunner('all');s=r.scheduler;lease=hs.ForegroundRuntimeLease()
try:
    if not lease.acquire():raise hs.PendingError('occupied')
    s.start_f12_monitor();s.bind_window(wait=True,timeout_seconds=10)
    r.account=s.bind_generic_current_window(require_game_context=False)
    r.click('ranking_end_battle.bmp',(1410,850,1550,980),40,32,'ranking_probe_end_20260916',['ranking_battle_anchor.bmp'],True)
    s.wait_interruptible(2);s.capture().save(hs.LOGS/'ranking_ended_20260916.png')
finally:s.stop.set();lease.release()
