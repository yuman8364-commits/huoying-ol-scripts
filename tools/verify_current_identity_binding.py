"""Verify current-role binding on copied state, with zero game or tab inputs."""
import argparse
import hashlib
import json
from pathlib import Path
import shutil
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from PIL import Image
import hyol_scheduler as hs
from hyol_accounts import state_directory
from hyol_checkpoints import bind_current_identity, match_identity, identity_mask_scores
from hyol_multi_solo import tab_close_positions, active_tab_centers
from hyol_runtime import VERSION


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    scope = parser.add_mutually_exclusive_group(required=True)
    scope.add_argument('--live', action='store_true')
    scope.add_argument('--replay', type=Path)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    if args.output.exists():
        raise ValueError('Preserve previous verification receipts')
    config = hs.read_config(ROOT/'DAILY_Client.ini')
    original = state_directory(config, ROOT)
    evidence = ROOT/'logs'/('current_identity_probe_'+hs.run_id())
    copied = evidence/'state'
    copied.mkdir(parents=True)
    hashes = {str(path): digest(path) for path in original.rglob('*') if path.is_file()}
    for source in hashes:
        path = Path(source)
        target = copied/path.relative_to(original)
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(path, target)
        if digest(target) != hashes[source]:
            raise hs.PendingError('原状态在复制时变化，重新核实后再验证')
    config.update(SharedStateDirectory=str(copied), AllowMarketPurchase='0')
    s = hs.Scheduler(config, 'identity_binding_verification', False, True)
    lease = hs.ForegroundRuntimeLease()
    report = dict(at=hs.now_iso(), version=VERSION, status='pending',
                  scope='live' if args.live else 'replay', state_copy=str(copied),
                  original_state_files=len(hashes), game_tasks_started=False,
                  game_content_clicks=0, tab_clicks=0)
    # This probe cannot scan or dismiss another tab, even during fallback.
    def reject_input(*args, **kwargs):
        raise hs.PendingError('只读当前账号核验不允许切换标签或输入游戏内容')
    s.chrome_click = reject_input
    try:
        if args.live:
            if not lease.acquire():
                raise hs.PendingError('已有脚本占用游戏窗口，不争用控制权')
            s.start_f12_monitor()
            s.bind_window(wait=True, timeout_seconds=10)
            s._capture_binding_probe = True
            real_capture = s.capture
            frames = []
            def capture():
                image = real_capture()
                path = evidence/('frame_'+str(len(frames)+1)+'.png')
                image.save(path)
                frames.append(str(path))
                return image
            s.capture = capture
            first = s.capture()
            if not s.healthy(first) or not s.main_ready(first) or s.offline_dialog_visible(first):
                raise hs.PendingError('当前主界面未确认，保持零输入')
            report.update(hwnd=s.hwnd, frames=frames)
        else:
            first = Image.open(args.replay).convert('RGB')
            s.capture = lambda: first.copy()
            s.wait_interruptible = lambda seconds: s.check_stop()
            report['replay'] = str(args.replay)
        centers = active_tab_centers(tab_close_positions(first))
        s.config['TabXs'] = ','.join(map(str, centers))
        tab_x = s._generic_active_tab_x(first)
        account = hs.Account(centers.index(tab_x)+1, '当前窗口标签'+str(centers.index(tab_x)+1), tab_x)
        s.state = hs.StateStore([account.name], copied/'window_probe')
        s.accounts, s.by_name = [account], {account.name: account}
        s.active = s._capture_account = account
        s.current_account_only, s.generic_current_tab_x = account.name, account.tab_x
        refs = json.loads((copied/'account_identities.json').read_text('utf-8-sig'))
        legacy_name, mask = match_identity(first, refs)
        report.update(legacy_single_match=legacy_name, initial_scores=identity_mask_scores(mask, refs),
                      selected_tab=account.index, tab_x=tab_x)
        bound = bind_current_identity(s, account)
        fresh = s.capture()
        verified, scores = s.verify_identity(bound, fresh)
        if not verified or s.content_click_count:
            raise hs.PendingError('绑定后身份未确认或出现意外输入')
        report.update(status='PASS', account=bound.name, final_scores=scores,
                      pending_actions={key: value for key, value in s.state.accounts[bound.name].get('actions', {}).items()
                                       if isinstance(value, dict) and value.get('status') == 'pending'},
                      task_credit=False, battle_credit=False)
    except Exception as exc:
        report.update(status='pending', detail=f'{type(exc).__name__}: {exc}')
    finally:
        s.stop.set()
        lease.release()
        changed = [path for path, value in hashes.items()
                   if not Path(path).is_file() or digest(Path(path)) != value]
        report.update(changed_original_state=changed, game_content_clicks=s.content_click_count)
        if changed:
            report['status'] = 'FAIL'
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(json.dumps(report, ensure_ascii=False, indent=2)+'\n', encoding='utf-8')
    print(json.dumps({key: report.get(key) for key in
                     ('status', 'scope', 'account', 'legacy_single_match', 'detail', 'game_content_clicks', 'changed_original_state')},
                     ensure_ascii=False))
    return 0 if report['status'] == 'PASS' else 2


if __name__ == '__main__':
    raise SystemExit(main())
