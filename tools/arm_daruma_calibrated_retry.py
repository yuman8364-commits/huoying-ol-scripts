"""Arm exactly one audited Daruma continuation after read-only live checks."""
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
        raise hs.SafetyError('达摩窗口已改变，不能设置单次续敲')
    frame = Image.open(check['evidence']).convert('RGB')
    templates = hs.TemplateStore(.9)
    if not templates.match(frame, hs.ASSETS / 'daruma_game_page.bmp', d.GAME_PAGE, .93)[0]:
        raise hs.PendingError('当前不是同一达摩局')
    if not templates.match(frame, hs.ASSETS / 'daruma_chance_digit_0.bmp', d.CHANCE_DIGIT, .95)[0]:
        raise hs.PendingError('当前游戏机会数字不是已核实的0')
    candidate = sorted(hs.STATE_DIR.glob('window_*_*_*/2026-09-27/account_00a0b00ccc_daily_client.json'),
                       key=lambda path: path.stat().st_mtime, reverse=True)
    if not candidate:
        raise hs.PendingError('找不到本局断点')
    path = candidate[0]
    if f'window_{hs.Win32.pid(hwnd)}_{hwnd}_' not in str(path):
        raise hs.PendingError('断点对应的浏览器进程或窗口不一致')
    records = json.loads(path.read_text(encoding='utf-8'))
    rec = records.get('weekend', {})
    expected = (rec.get('attempts_started'), rec.get('hits_verified'),
                rec.get('remaining'), rec.get('layer_retry'))
    if expected != (1, 4, 1, 10) or rec.get('calibration_retry_authorized'):
        raise hs.PendingError(f'断点与本次校准条件不符：{expected}')
    prior_trace = Path(rec.get('last_animation_trace', ''))
    if not prior_trace.is_file() or not prior_trace.name.endswith('strike_5_retry_9.npz'):
        raise hs.PendingError('缺少上次带判定条的敲击动画')
    judgment_x = d.judgment_column_from_trace(str(prior_trace))
    if judgment_x != 1067:
        raise hs.PendingError(f'上次判定锁定点未复现：{judgment_x}')
    with Image.open(rec['last_before_evidence']) as before:
        if d.obtained_count(before, frame) != 4:
            raise hs.PendingError('已获得图标不等于断点中的4')
    hs.Win32.move(1045, 650)
    time.sleep(.2)
    selected = np.asarray(ImageGrab.grab(bbox=d.BOTTOM_TRACK_ROI, all_screens=True).convert('RGB'),
                          dtype=np.int16)
    bounds = d.selected_bottom_bounds(selected)
    if bounds is None or not (1002 <= bounds[0] <= 1022 and 1118 <= bounds[1] <= 1138):
        raise hs.PendingError(f'最下层白边未回到已测中心位置：{bounds}')
    # The new GDI capture has been measured passively at 5-7 ms on this window.
    previous_latency = float(rec['last_capture_latency_ms']) / 2000 + .008
    corrected_latency = round(previous_latency + (judgment_x - d.TARGET_X) /
                              float(rec['last_motion']['velocity']), 5)
    if not .14 <= corrected_latency <= .18:
        raise hs.PendingError(f'由实测锁定点计算的输入延迟异常：{corrected_latency}')
    rec.update(calibration_retry_authorized=True, calibration_single_strike=True,
               hammer_side='right', measured_judgment_latency_seconds=corrected_latency,
               calibration_reason='2026-09-27 trace fixed marker x1067: late 23px; calibrated judgment delay from input',
               calibration_preflight=check['evidence'])
    hs.atomic_json(path, records)
    audit = {'at': hs.now_iso(), 'state': str(path), 'preflight': check['evidence'],
             'attempt': expected[0], 'hits': expected[1], 'reward_remaining': expected[2],
             'layer_retry': expected[3], 'content_clicks': check['content_clicks'],
             'reason': rec['calibration_reason']}
    audit['bottom_bounds'] = bounds
    audit['prior_trace'] = str(prior_trace)
    audit['judgment_x'] = judgment_x
    audit['measured_judgment_latency_seconds'] = corrected_latency
    audit_path = hs.LOGS / 'daruma_timing_retry_arm_20260927.json'
    hs.atomic_json(audit_path, audit)
    print(json.dumps({'armed': True, 'audit': str(audit_path)}, ensure_ascii=False))


if __name__ == '__main__':
    main()
