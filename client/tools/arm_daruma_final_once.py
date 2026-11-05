"""Arm one final-layer strike only after matching the active live round."""
from __future__ import annotations

import json
from pathlib import Path
import sys
import time

import numpy as np
from PIL import Image, ImageGrab

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import hyol_daruma as d
import hyol_scheduler as hs
from tools.preflight_live import run as preflight


def main() -> None:
    check = preflight()
    if not (check.get('status') == 'ready' and check.get('healthy') is True
            and check.get('content_clicks') == 0 and check.get('main_ready') is False):
        raise hs.PendingError(f'达摩只读预检未通过：{check}')
    hwnd = int(check['hwnd'])
    if hs.Win32.foreground() != hwnd or hs.Win32.rect(hwnd) != hs.EXPECTED_WINDOW:
        raise hs.SafetyError('游戏窗口焦点或尺寸变化')
    frame = Image.open(check['evidence']).convert('RGB')
    templates = hs.TemplateStore(.9)
    if not templates.match(frame, hs.ASSETS / 'daruma_game_page.bmp', d.GAME_PAGE, .93)[0]:
        raise hs.PendingError('达摩游戏页未确认')
    if not templates.match(frame, hs.ASSETS / 'daruma_chance_digit_0.bmp', d.CHANCE_DIGIT, .95)[0]:
        raise hs.PendingError('今日机会不是已核实的0')
    candidates = sorted(hs.STATE_DIR.glob('window_*_*_*/2026-09-27/account_00a0b00ccc_daily_client.json'),
                        key=lambda path: path.stat().st_mtime, reverse=True)
    if not candidates:
        raise hs.PendingError('缺少当前局断点')
    path = candidates[0]
    if f'window_{hs.Win32.pid(hwnd)}_{hwnd}_' not in str(path):
        raise hs.PendingError('断点不属于当前浏览器进程')
    records = json.loads(path.read_text(encoding='utf-8'))
    rec = records.get('weekend', {})
    expected = (rec.get('attempts_started'), rec.get('hits_verified'),
                rec.get('remaining'), rec.get('layer_retry'))
    if expected != (1, 5, 1, 0) or rec.get('calibration_single_strike'):
        raise hs.PendingError(f'断点不是待敲最后一层：{expected}')
    delay = float(rec.get('measured_judgment_latency_seconds', -1))
    if not .12 <= delay <= .17 or rec.get('last_judgment_x') != 1041:
        raise hs.PendingError(f'缺少成功第五击的实测判定延迟：{delay}')
    prior_trace = Path(rec.get('last_animation_trace', ''))
    if not prior_trace.is_file() or d.judgment_column_from_trace(str(prior_trace)) != 1041:
        raise hs.PendingError('第五击判定条录屏未复核')
    with Image.open(rec['last_before_evidence']) as before:
        if d.obtained_count(before, frame) != 5:
            raise hs.PendingError('当前画面不是5个获得图标')
    hs.Win32.move(1045, 645)
    time.sleep(.2)
    selection = np.asarray(ImageGrab.grab(bbox=d.BOTTOM_TRACK_ROI, all_screens=True).convert('RGB'),
                           dtype=np.int16)
    bounds = d.selected_bottom_bounds(selection)
    if bounds is None or not (990 <= bounds[0] <= 1007 and 1107 <= bounds[1] <= 1122):
        raise hs.PendingError(f'最后一层位置变化或未选中：{bounds}')
    rec['calibration_single_strike'] = True
    rec['hammer_side'] = 'right'
    rec['final_strike_preflight'] = check['evidence']
    hs.atomic_json(path, records)
    audit_path = hs.LOGS / 'daruma_final_once_arm_20260927.json'
    hs.atomic_json(audit_path, {'at': hs.now_iso(), 'state': str(path), 'preflight': check['evidence'],
                                'hits': 5, 'reward_remaining': 1, 'chance_remaining': 0,
                                'bottom_bounds': bounds, 'hammer_side': 'right',
                                'measured_judgment_latency_seconds': delay,
                                'prior_trace': str(prior_trace), 'content_clicks': 0})
    print(json.dumps({'armed': True, 'audit': str(audit_path)}, ensure_ascii=False))


if __name__ == '__main__':
    main()
