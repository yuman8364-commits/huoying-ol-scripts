"""Resume one account's saved ranking flow from its verified ranking page."""
import argparse
import json
from datetime import date
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import hyol_ranking as ranking
import hyol_scheduler as hs
from hyol_daily_tasks import DailyRunner


def run(account_name: str, completed: int, before: int) -> dict:
    s=hs.Scheduler(hs.read_config(hs.ROOT/'HYOL_Daily.ini'),'multi_solo',False,False)
    account=s.by_name[account_name]
    state_path=hs.STATE_DIR/date.today().isoformat()/f'{hs.slug(account_name)}_multi_solo.json'
    recs=json.loads(state_path.read_text(encoding='utf-8'))
    rec=recs.get('ranking',{})
    if (rec.get('status')!='pending' or rec.get('phase')!='result_sent'
            or rec.get('completed')!=completed or rec.get('before')!=before):
        raise ValueError('排位断点与预期的已结算场次不一致')
    index=completed+1
    if (s.state.action_status(account_name,f'daily_ranking_{index}_result')!='pending'
            or s.state.global_state.get('tab_map',{}).get(account_name)!=account.tab_x):
        raise ValueError('结果动作或账号标签映射未核实')
    lease=hs.ForegroundRuntimeLease()
    if not lease.acquire():
        raise hs.LeaseBlocked('已有脚本占用共享窗口')
    try:
        s.bind_window(wait=True,timeout_seconds=10)
        frame=s.capture()
        if not s.healthy(frame) or not s.tab_is_visually_active(account,frame):
            raise hs.SafetyError('窗口或账号标签未核实')
        if not (s.template('ranking_page.bmp',ranking.PAGE,frame,threshold=.95)[0]
                and s.template('ranking_counter_label.bmp',ranking.COUNTER,frame,threshold=.95)[0]):
            raise hs.PendingError('排位页及次数标签未确认')
        s.active=account
        runner=object.__new__(DailyRunner)
        runner.scheduler=s
        runner.account=account
        runner.path=state_path
        runner.records=recs
        runner.current='ranking'
        s.navigator=runner
        try:
            ranking.run(runner)
            frame=s.capture()
            if not (s.main_ready(frame) and s.template('leaf_village_name_current.bmp',
                    (1690,110,1920,148),frame,threshold=.92)[0]):
                raise hs.PendingError('次数已归零，但木叶村主界面未确认')
            rec.update(status='done',task_completed=True,home_returned=True,
                       finished_at=hs.now_iso(),last_error='')
            runner.save()
            outcome='done'
        except (hs.PendingError,hs.SafetyError) as exc:
            rec.update(status='pending',last_error=str(exc))
            runner.save()
            outcome='pending'
        return {'account':account_name,'outcome':outcome,'phase':rec.get('phase'),
                'completed':rec.get('completed'),'remaining':rec.get('remaining'),
                'content_clicks':s.content_click_count,'run_id':s.diagnostics.identifier,
                'last_error':rec.get('last_error','')}
    finally:
        s.stop.set()
        lease.release()


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--account',required=True)
    parser.add_argument('--completed',required=True,type=int)
    parser.add_argument('--before',required=True,type=int)
    args=parser.parse_args()
    print(json.dumps(run(args.account,args.completed,args.before),ensure_ascii=False))
