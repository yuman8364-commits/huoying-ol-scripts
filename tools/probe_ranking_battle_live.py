from pathlib import Path
import sys
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from PIL import Image
import hyol_daily_tasks as d
import hyol_scheduler as hs
im=Image.open(hs.LOGS/'ranking_entry_20260916.png')
for name,box in {'ranking_page.bmp':(817,103,1049,185),'ranking_counter_label.bmp':(1478,160,1561,179),'ranking_digit_5.bmp':(1566,159,1581,179),'ranking_challenge.bmp':(571,877,642,897),'ranking_close.bmp':(1618,106,1653,142)}.items(): im.crop(box).save(hs.ASSETS/name)
r=d.DailyRunner('all');s=r.scheduler;lease=hs.ForegroundRuntimeLease()
try:
    if not lease.acquire():raise hs.PendingError('occupied')
    s.start_f12_monitor();s.bind_window(wait=True,timeout_seconds=10)
    r.account=s.bind_generic_current_window(require_game_context=False)
    r.click('ranking_challenge.bmp',(535,845,681,932),30,10,'ranking_probe_first_20260916',['ranking_page.bmp'],True)
    s.wait_interruptible(3);s.capture().save(hs.LOGS/'ranking_first_battle_20260916.png')
finally:s.stop.set();lease.release()
