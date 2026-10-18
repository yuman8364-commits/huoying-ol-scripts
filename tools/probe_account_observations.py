"""Capture actual identity observations with only verified browser-tab input."""
from pathlib import Path
import json
import sys

sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
import hyol_scheduler as hs
from hyol_checkpoints import current_identity_mask, _identity_layout, read_checkpoint, identity_mask_scores
from hyol_diagnostics import manual_pause_status
from hyol_multi_solo import tab_close_positions, active_tab_centers


def main():
    if manual_pause_status(hs.ROOT)['paused']: raise hs.PendingError('人工暂停，未观察')
    config = hs.read_config(hs.ROOT/'HYOL_Daily.ini')
    config['AccountMode']='dynamic';config['AllowMarketPurchase']='0'
    s = hs.Scheduler(config,'multi_solo',False,True)
    lease = hs.ForegroundRuntimeLease()
    if not lease.acquire(): raise hs.PendingError('已有前台任务')
    output = hs.LOGS/('account_observations_'+hs.run_id())
    output.mkdir()
    report = dict(observations={},content_clicks=0)
    try:
        s.start_f12_monitor();s.bind_window(wait=True,timeout_seconds=10)
        first = s.capture()
        positions = tab_close_positions(first)
        s.wait_interruptible(.3)
        if positions != tab_close_positions(s.capture()): raise hs.PendingError('标签布局变化')
        centers = active_tab_centers(positions)
        s.config['TabXs'] = ','.join(map(str,centers))
        original = s._generic_active_tab_x(first)
        identities = read_checkpoint(s.shared_state_directory/'account_identities.json')
        for slot,x in enumerate(centers,1):
            account = hs.Account(slot,f'当前窗口标签{slot}',x)
            s.chrome_click(x,10,f'observe_account:{slot}',account);s.wait_interruptible(.8)
            masks,rows = [],[]
            for sample in range(3):
                s.check_stop();frame = s.capture()
                path = output/f'tab_{slot}_frame_{sample}.png';frame.save(path)
                mask = current_identity_mask(frame,s.templates);masks.append(mask)
                roi,threshold = _identity_layout(frame,s.templates)
                rows.append(dict(evidence=str(path),pixels=int(mask.sum()),roi=roi,threshold=threshold,
                                 active_x=s._generic_active_tab_x(frame),offline=s.offline_dialog_visible(frame),
                                 identity_scores=identity_mask_scores(mask,identities)))
                if sample<2:s.wait_interruptible(.35)
            report['observations'][str(slot)] = dict(x=x,frames=rows,
                stability=[hs.mask_f1(masks[0],mask) for mask in masks[1:]])
        original_slot = centers.index(original)+1
        s.chrome_click(original,10,'restore_current_account_observation',
                       hs.Account(original_slot,f'当前窗口标签{original_slot}',original))
        report.update(hwnd=s.hwnd,content_clicks=s.content_click_count)
    finally:
        s.stop.set();lease.release()
        hs.atomic_json(output/'report.json',report)
    print(json.dumps(dict(report=str(output/'report.json'),observations={slot:dict(
        pixels=[row['pixels'] for row in rec['frames']],roi=[row['roi'] for row in rec['frames']],
        stability=rec['stability']) for slot,rec in report['observations'].items()},
        content_clicks=report['content_clicks']),ensure_ascii=False))


if __name__=='__main__':main()
