from pathlib import Path
import sys,json
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from PIL import Image
import hyol_scheduler as hs
slot=int(sys.argv[1]); assert slot in (1,2,3)
source=Image.open(hs.LOGS/'ranking_home_20260916.png')
boxes={1:(132,19,198,33),2:(267,19,333,33),3:(396,19,468,33)}
name=f'acceptance_tab_{slot}.bmp'
source.crop(boxes[slot]).save(hs.ASSETS/name)
s=hs.Scheduler(hs.read_config(hs.ROOT/'DAILY_Client.ini'),'acceptance_tab',False,False)
lease=hs.ForegroundRuntimeLease()
try:
    if not lease.acquire(): raise hs.PendingError('occupied')
    s.start_f12_monitor();s.bind_window(wait=True,timeout_seconds=10)
    box=boxes[slot];roi=(box[0]-8,8,box[2]+8,38)
    ok,score,x,y=s.template(name,roi,threshold=.97)
    if not ok:raise hs.PendingError('当前标签文字与已检查页面不一致')
    s.chrome_click(x+25,y+6,'acceptance_visible_tab')
    s.wait_interruptible(1.5)
    a=s.bind_generic_current_window(require_game_context=False)
    if a.index != slot:raise hs.PendingError('标签高亮不一致')
    path=hs.LOGS/f'acceptance_tab{slot}_20260916.png';s.capture().save(path)
    print(json.dumps({'account':a.name,'evidence':str(path),'healthy':s.healthy(s.capture())},ensure_ascii=False))
finally:s.stop.set();lease.release()
