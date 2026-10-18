"""Open the visually enrolled quiz link once; never submit an answer."""
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import hyol_scheduler as hs
from hyol_diagnostics import manual_pause_status

if __name__ == '__main__':
    if manual_pause_status(hs.ROOT)['paused']:
        raise SystemExit('manual pause')
    s = hs.Scheduler(hs.read_config(hs.ROOT/'DAILY_Client.ini'), 'quiz_observe', False, False)
    lease = hs.ForegroundRuntimeLease()
    if not lease.acquire():
        raise SystemExit('occupied')
    try:
        s.start_f12_monitor()
        s.bind_window(wait=True, timeout_seconds=10)
        a = s.bind_generic_current_window(require_game_context=False)
        if s.template('sealed_leader_badge.bmp',(0,69,65,150))[0]:
            raise hs.PendingError('组队状态禁止测验')
        if not s.click_template(a, 'quiz_task_link.bmp', (0,200,300,500), 60,7,
                                'quiz_observe_entry', ['quiz_task_link.bmp']):
            raise hs.PendingError('quiz link missing')
        s.wait_interruptible(5)
        s.capture().save(hs.LOGS/'quiz_open_observation.png')
    finally:
        s.stop.set()
        lease.release()
