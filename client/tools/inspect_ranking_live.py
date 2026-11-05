from pathlib import Path
import sys
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from PIL import Image
import hyol_daily_tasks as d
import hyol_scheduler as hs
Image.open(hs.LOGS/'current_desktop_20260916.png').crop((960,73,1007,132)).save(hs.ASSETS/'ranking_toolbar.bmp')
r=d.DailyRunner('all')
s=r.scheduler
lease=hs.ForegroundRuntimeLease()
try:
    if not lease.acquire(): raise hs.PendingError('occupied')
    s.start_f12_monitor()
    s.bind_window(wait=True,timeout_seconds=10)
    r.account=s.bind_generic_current_window()
    r.click('ranking_toolbar.bmp',(920,69,1060,150),23,25,'ranking_inspect_entry',['anchor_top_menu.bmp'])
    s.wait_interruptible(2)
    s.capture().save(hs.LOGS/'ranking_entry_20260916.png')
finally:
    s.stop.set()
    lease.release()
