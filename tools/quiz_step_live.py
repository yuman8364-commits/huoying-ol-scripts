"""One visually verified quiz action for collecting reviewed UI templates."""
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import hyol_scheduler as hs
from hyol_diagnostics import manual_pause_status

if __name__ == '__main__':
    name = sys.argv[1]
    assert name in ('quiz_start.bmp', 'quiz_answer_a.bmp', 'quiz_next.bmp', 'quiz_finish.bmp')
    if manual_pause_status(hs.ROOT)['paused']:
        raise SystemExit('manual pause')
    s = hs.Scheduler(hs.read_config(hs.ROOT/'DAILY_Client.ini'), 'quiz_step', False, False)
    lease = hs.ForegroundRuntimeLease()
    if not lease.acquire():
        raise SystemExit('occupied')
    try:
        s.start_f12_monitor()
        s.bind_window(wait=True, timeout_seconds=10)
        a = s.bind_generic_current_window(require_game_context=False)
        if s.template('quiz_team_leave.bmp',(280,165,455,240))[0] or not s.template('quiz_solo_layout.bmp',(40,195,205,240))[0]:
            raise hs.PendingError('未确认单人状态')
        if not s.click_template(a, name, (500,300,1250,850), 25,8,
                                'quiz_step_'+s.diagnostics.identifier, [name], irreversible=True):
            raise hs.PendingError('quiz control missing')
        s.wait_interruptible(2)
        s.capture().save(hs.LOGS/'quiz_step_observation.png')
    finally:
        s.stop.set()
        lease.release()
