"""Bounded UI cleanup of the two reviewed captain pages, without task credit."""
import json
import sys
import time
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import hyol_scheduler as hs
from hyol_accounts import _final_result
from hyol_multi_solo import active_tab_centers, tab_close_positions
from hyol_run_context import RunContext

ROOT = hs.ROOT
REPORT = ROOT/'logs/strong_captain_preparation_20261019.json'
CENTERS = (180,315,450,585,720,855)

def run():
    terminal = json.loads((ROOT/'logs/unified_latest.json').read_text(encoding='utf-8-sig'))
    assert terminal.get('result') == 2 and terminal.get('run_id') == '20261018_235249_624186_d2e68ec3'
    lease = hs.ForegroundRuntimeLease()
    if not lease.acquire():
        raise hs.PendingError('仍有运行占用窗口，不能准备页面')
    report = (json.loads(REPORT.read_text(encoding='utf-8-sig')) if REPORT.is_file()
              else dict(at=hs.now_iso(), battle_credit=False, task_credit=False, operations=[]))
    hs.atomic_json(REPORT, report)
    try:
        for slot in (3,1):
            context = RunContext.create(ROOT)
            with context.activate():
                config = hs.read_config(ROOT/'DAILY_Client.ini')
                config['TabXs'] = ','.join(map(str,CENTERS))
                config['AllowMarketPurchase'] = '0'
                s = hs.Scheduler(config,'readonly_preflight',False,False)
                s.automatic_accounts = False
                try:
                    s.bind_window()
                    assert s.hwnd == 67438 and hs.Win32.pid(s.hwnd) == 29700
                    assert tuple(active_tab_centers(tab_close_positions(s.capture()))) == CENTERS
                    hs.Win32.click(CENTERS[slot-1],10)
                    s.wait_interruptible(.4)
                    account = s.bind_generic_current_window(require_game_context=False)
                    assert account.index == slot and account.tab_x == CENTERS[slot-1]
                    def act(asset,roi,dx,dy,label,anchors):
                        assert not any(row['slot']==slot and row['operation']==label for row in report['operations']), '原关闭凭据已存在，禁止重复输入'
                        frames=[]
                        for sample in range(3):
                            frame=s.capture()
                            assert s._generic_active_tab_x(frame) == account.tab_x
                            assert s.template(asset,roi,frame,threshold=.98)[0]
                            assert all(s.template(a,hs.VIEW,frame,threshold=.95)[0] for a in anchors)
                            frames.append(frame)
                            if sample<2:s.wait_interruptible(.25)
                        row=dict(slot=slot,operation=label,status='pending',
                                 before=str(s.save_raw_evidence(frames[-1],account,label,'before')))
                        report['operations'].append(row);hs.atomic_json(REPORT,report)
                        assert s.click_template(account,asset,roi,dx,dy,label,anchors,
                                                threshold=.98,anchor_threshold=.95,irreversible=True)
                        assert s.wait_absent(asset,roi,8,threshold=.98)
                        s.commit_action(account,label,'当前已核实页面消失；battle_credit=false/task_credit=false')
                        row.update(status='done',after=str(s.save_raw_evidence(s.capture(),account,label,'verified')))
                        hs.atomic_json(REPORT,report)
                    frame=s.capture()
                    if _final_result(s,frame) or any(row['slot']==slot and row['operation']=='reviewed_captain_previous_result' for row in report['operations']):
                        previous=[row for row in report['operations'] if row['slot']==slot and row['operation']=='reviewed_captain_previous_result']
                        if previous:
                            assert len(previous)==1 and previous[0]['status']=='done', '原确认结果未决，不重试'
                        else:
                            result=_final_result(s,frame)
                            assert result, '上场胜利结果已改变，停止准备'
                            act(result[0],hs.TEAM_BATTLE_RESULT_ROI,55,18,'reviewed_captain_previous_result',
                                [result[0],result[3][0]])
                        deadline=time.monotonic()+12
                        while time.monotonic()<deadline:
                            frame=s.capture()
                            if s._team_reward_result_visible(frame) or s.main_ready(frame):break
                            s.wait_interruptible(.25)
                        frame=s.capture()
                        if s._team_reward_result_visible(frame):
                            act(hs.TEAM_REWARD_RESULT_TEMPLATE,hs.TEAM_REWARD_RESULT_ROI,64,19,
                                'reviewed_captain_previous_reward',[hs.TEAM_REWARD_RESULT_TEMPLATE])
                    deadline=time.monotonic()+12
                    while time.monotonic()<deadline:
                        frame=s.capture()
                        if s.main_ready(frame) or s.template('strong_reward_label_core_current.bmp',(700,800,960,850),frame,threshold=.98)[0]:break
                        s.wait_interruptible(.25)
                    frame=s.capture()
                    if not s.main_ready(frame):
                        assert s.template('strong_reward_label_core_current.bmp',(700,800,960,850),frame,threshold=.98)[0]
                        act('strong_mirror_close.bmp',(1300,280,1420,390),15,16,
                            'reviewed_captain_strong_page_close',
                            ['strong_reward_label_core_current.bmp','strong_mirror_close.bmp'])
                    for sample in range(3):
                        frame=s.capture()
                        assert s.main_ready(frame) and s._generic_active_tab_x(frame)==account.tab_x
                        if sample<2:s.wait_interruptible(.25)
                    report.setdefault('captains',{})[str(slot)]=dict(home_verified=True,
                        evidence=str(s.save_raw_evidence(frame,account,'reviewed_captain_home','verified')),
                        content_clicks=s.content_click_count)
                    hs.atomic_json(REPORT,report)
                finally:
                    s.stop.set()
        report['status']='ready';report.pop('error',None);hs.atomic_json(REPORT,report)
        return report
    except BaseException as error:
        report.update(status='pending',error=f'{type(error).__name__}: {error}')
        hs.atomic_json(REPORT,report)
        raise
    finally:
        lease.release()

if __name__=='__main__':
    print(json.dumps(run(),ensure_ascii=False))
