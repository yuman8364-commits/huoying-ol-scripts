"""Bounded cleanup of user-authorized interference, never an account login."""
from pathlib import Path
import sys
import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import hyol_scheduler as hs
from hyol_multi_solo import active_tab_centers, tab_close_positions
from hyol_team_pipeline import PipelineScheduler


def main():
    s = PipelineScheduler()
    lease = hs.ForegroundRuntimeLease()
    if not lease.acquire():
        raise hs.PendingError('已有前台工作线程')
    report = dict(at=hs.now_iso(), authorization='用户要求直接运行，已有奖励弹窗作为干扰项处理', observations=[])
    try:
        s.start_f12_monitor()
        s.bind_window(timeout_seconds=15)
        centers = active_tab_centers(tab_close_positions(s.capture()))
        if len(centers) != 6:
            raise hs.PendingError('原六标签发生变化')
        s.accounts = [hs.Account(i, f'当前窗口标签{i}', x) for i, x in enumerate(centers, 1)]
        s.by_name = {a.name: a for a in s.accounts}
        s.config['TabXs'] = ','.join(map(str, centers))
        for i in (2, 3):
            account = s.accounts[i-1]
            frame = s.switch_account(account)
            report['observations'].append(dict(tab=i, evidence=str(s.save_raw_evidence(
                frame, account, 'pipeline_page_recovery', 'observed'))))
        account = s.accounts[4]
        frame = s.switch_account(account)
        black = []
        for _ in range(3):
            black.append(float(np.asarray(frame.crop(hs.VIEW).convert('RGB')).max(axis=2).mean()))
            s.wait_interruptible(.25)
            frame = s.capture()
        report['black_before'] = str(s.save_raw_evidence(frame, account, 'pipeline_black_refresh', 'before'))
        if max(black) < 2:
            report['refresh'] = dict(at=hs.now_iso(), tab=5, identity='unverified', attempts=1,
                                     reason='三帧全黑页面，已实拍核实浏览器刷新控件')
            hs.atomic_json(hs.LOGS/'pipeline_page_recovery_20261007.json', report)
            s.chrome_click(1345, 52, 'pipeline_black_page_refresh_once', account)
            for _ in range(30):
                s.wait_interruptible(1)
                frame = s.capture()
                if s.main_ready(frame) or s.offline_dialog_visible(frame):
                    break
            report['refresh']['after'] = str(s.save_raw_evidence(frame, account, 'pipeline_black_refresh', 'observed'))
            report['refresh']['main_ready'] = s.main_ready(frame)
            report['refresh']['offline_dialog'] = s.offline_dialog_visible(frame)
        report['content_clicks'] = s.content_click_count
        print(report)
    finally:
        hs.atomic_json(hs.LOGS/'pipeline_page_recovery_20261007.json', report)
        s.stop.set()
        lease.release()


if __name__ == '__main__':
    main()
