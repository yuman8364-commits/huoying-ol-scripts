"""Close a positively recognized ninja panel to reveal its hidden identity.

This recovery never selects a formation or credits any game task. Without
--apply it only observes. Replay mode does not construct a game scheduler.
"""
import argparse
import hashlib
import json
from pathlib import Path
import sys
import time
from PIL import Image

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
import hyol_scheduler as hs
from hyol_accounts import _tab_title_mask, _same_tab_title
from hyol_diagnostics import manual_pause_status
from hyol_multi_solo import tab_close_positions, active_tab_centers

ANCHORS = (
    ('ninja_page_current.bmp', (270, 270, 510, 340)),
    ('ninja_owned_title_current.bmp', (1070, 280, 1280, 340)),
    ('ninja_close_current.bmp', (1570, 100, 1640, 175)),
)


def inspect(frame, template):
    matches = [template(name, roi, frame, threshold=.98) for name, roi in ANCHORS]
    return dict(visible=all(row[0] for row in matches), scores=[row[1] for row in matches],
                close=[matches[-1][2]+20, matches[-1][3]+20])


def run(apply=False):
    if manual_pause_status(ROOT)['paused']:
        raise hs.PendingError('人工暂停仍在，原断点保留')
    config = hs.read_config(ROOT/'HYOL_Daily.ini')
    config['AllowMarketPurchase'] = '0'
    s = hs.Scheduler(config, 'identity_panel_recovery', False, not apply)
    lease = hs.ForegroundRuntimeLease()
    if not lease.acquire():
        lease.release()
        raise hs.PendingError('已有前台任务，未处理页面')
    report = dict(at=hs.now_iso(), apply=apply, actions=[], game_inputs=0,
                  formation_changes=0, battle_credit=False, task_credit=False)
    path = ROOT/'logs'/('identity_panel_recovery_'+s.diagnostics.identifier+'.json')
    try:
        s.start_f12_monitor()
        s.bind_window(wait=True, timeout_seconds=10)
        markers = tab_close_positions(s.capture())
        s.wait_interruptible(.35)
        if markers != tab_close_positions(s.capture()):
            raise hs.PendingError('标签位置变化，未处理页面')
        for index, x in enumerate(active_tab_centers(markers), 1):
            account = hs.Account(index, f'当前窗口标签{index}', x)
            s.chrome_click(x, 10, f'observe_account:{index}', account)
            s.wait_interruptible(.8)
            frame = s.capture()
            expected = inspect(frame, s.template)
            if not expected['visible']:
                continue
            title = _tab_title_mask(frame, x)
            for sample in range(3):
                s.check_stop()
                s.ensure_window()
                frame = s.capture()
                got = inspect(frame, s.template)
                if (s.offline_dialog_visible(frame) or s._generic_active_tab_x(frame) != x
                        or not _same_tab_title(title, _tab_title_mask(frame, x))
                        or not got['visible'] or got['close'] != expected['close']):
                    raise hs.PendingError('忍者页面或当前标签变化，未点击关闭')
                if sample < 2:
                    s.wait_interruptible(.35)
            before = str(s.save_raw_evidence(frame, account, 'identity_panel_close', 'before'))
            row = dict(tab=index, tab_x=x, identity='unconfirmed', status='observed',
                       before=before, anchors=expected, operation='close_ninja_panel')
            report['actions'].append(row)
            hs.atomic_json(path, report)
            if not apply:
                continue
            row['status'] = 'pending'
            hs.atomic_json(path, report)
            # Only this selected, three-frame-verified close control is allowed.
            s.ensure_window()
            s.check_stop()
            point = s._capture_geometry.point(*expected['close'])
            hs.Win32.click(*point)
            s.content_click_count += 1
            report['game_inputs'] += 1
            s.event('content_click', account, target='identity_panel_ninja_close',
                    x=expected['close'][0], y=expected['close'][1], irreversible=False,
                    before_hash=hashlib.sha256(frame.tobytes()).hexdigest())
            stable = 0
            deadline = time.monotonic()+12
            while time.monotonic() < deadline:
                s.wait_interruptible(.35)
                s.ensure_window()
                frame = s.capture()
                if (s.offline_dialog_visible(frame) or s._generic_active_tab_x(frame) != x
                        or not _same_tab_title(title, _tab_title_mask(frame, x))):
                    raise hs.PendingError('关闭后标签或在线状态变化，保留关闭凭据')
                stable = stable+1 if not inspect(frame, s.template)['visible'] else 0
                if stable == 3:
                    row.update(status='done', after=str(s.save_raw_evidence(
                        frame, account, 'identity_panel_close', 'verified')))
                    hs.atomic_json(path, report)
                    break
            else:
                raise hs.PendingError('关闭后页面未确认，不重复点击')
    finally:
        s.stop.set()
        lease.release()
        hs.atomic_json(path, report)
    return dict(report=str(path), **report)


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--apply', action='store_true')
    parser.add_argument('--replay', nargs='+', type=Path)
    args = parser.parse_args()
    if args.replay:
        store = hs.TemplateStore(.98)
        match = lambda name, roi, frame, **kwargs: store.match(frame, ROOT/'assets'/name, roi, **kwargs)
        print(json.dumps([dict(path=str(p), **inspect(Image.open(p), match)) for p in args.replay],
                         ensure_ascii=False))
    else:
        print(json.dumps(run(args.apply), ensure_ascii=False))
