"""Click the already visible end-battle button for one audited ranking attempt."""
import argparse
import json
from datetime import date
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import hyol_ranking as ranking
import hyol_scheduler as hs


def run(account_name: str, index: int, before: int) -> dict:
    config = hs.read_config(hs.ROOT / 'HYOL_Daily.ini')
    s = hs.Scheduler(config, 'multi_solo', False, False)
    account = s.by_name[account_name]
    state_path = hs.STATE_DIR / date.today().isoformat() / f'{hs.slug(account_name)}_multi_solo.json'
    recs = json.loads(state_path.read_text(encoding='utf-8'))
    rec = recs.get('ranking', {})
    if (rec.get('status') != 'pending' or rec.get('phase') != 'end_pending'
            or rec.get('completed') != index - 1 or rec.get('before') != before):
        raise ValueError('排位断点与指定场次或次数不一致')
    base = f'daily_ranking_{index}'
    if (s.state.action_status(account_name, base+'_start') != 'pending'
            or s.state.action_status(account_name, base+'_end') != 'new'):
        raise ValueError('排位动作账本不允许发送结束战斗点击')
    if s.state.global_state.get('tab_map', {}).get(account_name) != account.tab_x:
        raise ValueError('账号标签映射未核实')
    lease = hs.ForegroundRuntimeLease()
    if not lease.acquire():
        raise hs.LeaseBlocked('已有脚本占用共享窗口')
    try:
        s.bind_window(wait=True, timeout_seconds=10)
        frame = s.capture()
        if not s.healthy(frame) or not s.tab_is_visually_active(account, frame):
            raise hs.SafetyError('窗口或账号标签未核实')
        if not s.template('ranking_battle_anchor.bmp',(1400,205,1510,251),frame,threshold=.95)[0]:
            raise hs.PendingError('排位战斗锚点未确认')
        if not s.template('ranking_end_battle.bmp',ranking.END,frame,threshold=.93)[0]:
            raise hs.PendingError('结束战斗按钮未确认')
        s.active = account
        clicked = s.click_template(account,'ranking_end_battle.bmp',ranking.END,40,32,
            base+'_end',['ranking_battle_anchor.bmp','ranking_end_battle.bmp'],
            irreversible=True,threshold=.93)
        if not clicked:
            raise hs.PendingError('结束战斗按钮即时复查失败，未点击')
        rec['phase'] = 'end_sent'
        recs['updated_at'] = hs.now_iso()
        hs.atomic_json(state_path, recs)
        return {'account':account_name,'index':index,'before':before,
                'end_action':s.state.action_status(account_name,base+'_end'),
                'phase':rec['phase'],'content_clicks':s.content_click_count,
                'run_id':s.diagnostics.identifier}
    finally:
        s.stop.set()
        lease.release()


if __name__ == '__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--account',required=True)
    parser.add_argument('--index',required=True,type=int)
    parser.add_argument('--before',required=True,type=int)
    args=parser.parse_args()
    print(json.dumps(run(args.account,args.index,args.before),ensure_ascii=False))
