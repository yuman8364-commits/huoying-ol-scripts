"""Close a known residual team room, then observe identities without action migration."""
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import hyol_scheduler as hs
from hyol_checkpoints import match_identity, read_checkpoint, joint_identity_assignment
from hyol_multi_solo import active_tab_centers, tab_close_positions
from hyol_team_pipeline import PipelineScheduler, portrait_leader_present, ROOM, DISSOLVE


def main():
    s = PipelineScheduler();lease = hs.ForegroundRuntimeLease()
    if not lease.acquire():raise hs.PendingError('前台仍在运行')
    result = dict(at=hs.now_iso(), frames={}, observations={})
    try:
        s.start_f12_monitor();s.bind_window(timeout_seconds=15)
        centers = active_tab_centers(tab_close_positions(s.capture()))
        s.config['TabXs'] = ','.join(map(str,centers))
        s.accounts = [hs.Account(i,f'当前窗口标签{i}',x) for i,x in enumerate(centers,1)]
        s.by_name = {a.name:a for a in s.accounts}
        refs = read_checkpoint(s.shared_state_directory/'account_identities.json')
        masks = {}
        for account in s.accounts:
            f = s.switch_account(account)
            if (s.template('team_room_close.bmp',ROOM,f,threshold=.98)[0]
                    and s.template('team_dissolve_button.bmp',DISSOLVE,f,threshold=.98)[0]):
                if not s.click_template(account,'team_room_close.bmp',ROOM,20,20,'authorized_residual_room_close',
                                        ['team_room_close.bmp','team_dissolve_button.bmp'],threshold=.98,anchor_threshold=.98):
                    raise hs.PendingError('残留队伍窗口改变')
                if not s.wait_absent('team_room_close.bmp',ROOM,5,threshold=.98):
                    raise hs.PendingError('残留队伍窗口未关闭')
                f = s.capture()
            name,mask = match_identity(f,refs)
            result['observations'][account.index] = dict(name=name,leader=portrait_leader_present(s,f),glyphs=int(mask.sum()))
            masks[account.index] = mask
            result['frames'][account.index] = str(s.save_raw_evidence(f,account,'pipeline_captain_reassociation','observed'))
        result['joint'],result['scores'],result['gap'] = joint_identity_assignment(masks,refs)
        result['content_clicks'] = s.content_click_count
        print(result)
    finally:
        hs.atomic_json(hs.LOGS/'pipeline_captain_reassociation_20261007.json',result)
        s.stop.set();lease.release()


if __name__ == '__main__':main()
