"""Close the exact reward shell the user explicitly classified as interference."""
from pathlib import Path
import sys

from PIL import Image

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import hyol_scheduler as hs
from hyol_multi_solo import active_tab_centers, tab_close_positions
from hyol_team_pipeline import PipelineScheduler


def main():
    enrolled = hs.LOGS/'scheduler_evidence/2026-10-07/account_00a0b00ccc/220431_884941_before_user_authorized_interference.png'
    folder = hs.LOGS/'pipeline_interference_templates_20261007'
    folder.mkdir(exist_ok=True)
    anchor, close = folder/'spirit_reward_text.bmp', folder/'spirit_reward_close.bmp'
    reference = Image.open(enrolled)
    reference.crop((948,440,1180,530)).save(anchor)
    reference.crop((1207,419,1237,451)).save(close)
    s = PipelineScheduler()
    lease = hs.ForegroundRuntimeLease()
    if not lease.acquire():
        raise hs.PendingError('已有前台工作线程')
    report = dict(at=hs.now_iso(), authorization='用户明确要求将已有通灵兽奖励弹窗作为干扰项处理',
                  intended_operation='close verified X', game_tasks_counted=False)
    try:
        s.start_f12_monitor()
        s.bind_window(timeout_seconds=15)
        centers = active_tab_centers(tab_close_positions(s.capture()))
        account = hs.Account(1, '当前窗口标签1', centers[0])
        frame = s.switch_account(account)
        for _ in range(3):
            if not (s.template(str(anchor), (940,425,1200,545), frame, threshold=.98)[0]
                    and s.template(str(close), (1195,408,1250,465), frame, threshold=.98)[0]):
                raise hs.PendingError('用户指出的干扰弹窗未正向匹配')
            s.wait_interruptible(.2)
            frame = s.capture()
        report['before'] = str(s.save_raw_evidence(frame, account, 'authorized_interference_close', 'before'))
        if not s.click_template(account, str(close), (1195,408,1250,465), 15,16,
                                'authorized_spirit_reward_shell_close', [str(anchor)],
                                threshold=.98, anchor_threshold=.98):
            raise hs.PendingError('干扰弹窗关闭前发生变化')
        if not s.wait_absent(str(anchor), (940,425,1200,545), 5, threshold=.98):
            raise hs.PendingError('原关闭已发送，不重复点击')
        report.update(result='closed', content_clicks=s.content_click_count,
                      after=str(s.save_raw_evidence(s.capture(), account, 'authorized_interference_close', 'verified')))
        print(report['after'])
    finally:
        hs.atomic_json(hs.LOGS/'pipeline_authorized_interference_20261007.json', report)
        s.stop.set()
        lease.release()


if __name__ == '__main__':
    main()
