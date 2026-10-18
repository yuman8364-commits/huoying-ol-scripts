"""Confirm the visible result of one already ended ranking battle."""
import argparse
import json
from datetime import date
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import hyol_ranking as ranking
import hyol_scheduler as hs


def run(account_name: str, index: int, before: int) -> dict:
    s = hs.Scheduler(hs.read_config(hs.ROOT / 'HYOL_Daily.ini'), 'multi_solo', False, False)
    account = s.by_name[account_name]
    state_path = hs.STATE_DIR / date.today().isoformat() / f'{hs.slug(account_name)}_multi_solo.json'
    recs = json.loads(state_path.read_text(encoding='utf-8'))
    rec = recs.get('ranking', {})
    if (rec.get('status') != 'pending' or rec.get('phase') != 'end_sent'
            or rec.get('completed') != index - 1 or rec.get('before') != before):
        raise ValueError('排位断点与指定结算不一致')
    base = f'daily_ranking_{index}'
    if (s.state.action_status(account_name,base+'_end') != 'pending'
            or s.state.action_status(account_name,base+'_result') != 'new'):
        raise ValueError('结束战斗或结果动作账本不允许确认')
    if s.state.global_state.get('tab_map',{}).get(account_name) != account.tab_x:
        raise ValueError('账号标签映射未核实')
    lease = hs.ForegroundRuntimeLease()
    if not lease.acquire():
        raise hs.LeaseBlocked('已有脚本占用共享窗口')
    try:
        s.bind_window(wait=True,timeout_seconds=10)
        frame=s.capture()
        if not s.healthy(frame) or not s.tab_is_visually_active(account,frame):
            raise hs.SafetyError('窗口或账号标签未核实')
        anchors=(('ranking_result_confirm.bmp',ranking.RESULT,.95),
                 ('ranking_result_stats.bmp',(890,605,1035,653),.95),
                 ('arena_defeat_banner.bmp',(650,320,1250,800),.90))
        for name,roi,threshold in anchors:
            if not s.template(name,roi,frame,threshold=threshold)[0]:
                raise hs.PendingError(f'结果画面锚点未确认：{name}')
        s.active=account
        clicked=s.click_template(account,'ranking_result_confirm.bmp',ranking.RESULT,55,15,
            base+'_result',['ranking_result_stats.bmp','ranking_result_confirm.bmp'],
            irreversible=True,threshold=.95)
        if not clicked:
            raise hs.PendingError('结果确认按钮即时复查失败，未点击')
        rec['phase']='result_sent'
        recs['updated_at']=hs.now_iso()
        hs.atomic_json(state_path,recs)
        return {'account':account_name,'index':index,'before':before,
                'result_action':s.state.action_status(account_name,base+'_result'),
                'phase':rec['phase'],'content_clicks':s.content_click_count,
                'run_id':s.diagnostics.identifier}
    finally:
        s.stop.set()
        lease.release()


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--account',required=True)
    parser.add_argument('--index',required=True,type=int)
    parser.add_argument('--before',required=True,type=int)
    args=parser.parse_args()
    print(json.dumps(run(args.account,args.index,args.before),ensure_ascii=False))
