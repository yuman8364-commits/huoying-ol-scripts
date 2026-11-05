"""Recover a white microclient by switching its native Blue Diamond/game links."""
from __future__ import annotations

import hashlib
from pathlib import Path

import numpy as np
from PIL import ImageGrab

from hyol_errors import PendingError, SafetyError
from hyol_geometry import GameGeometry
from hyol_platform import Win32

WINDOW = (0, 0, 1920, 1032)
LINKS = {
    'blue': ('browser_blue_link_20261031.bmp', (135, 39, 178, 66)),
    'game': ('browser_game_link_20261031.bmp', (6, 39, 78, 66)),
}


def white_content(frame, *, native_blank=False):
    if frame.size != (1920, 1032):
        return False
    # Desktop status/pet overlays can cover the lower part of an otherwise
    # empty browser. Use the upper sample only when Win32 independently proves
    # that this browser has no visible game surface.
    roi = (100, 180, 1800, 700 if native_blank else 900)
    pixels = np.asarray(frame.crop(roi).resize((170, 72)).convert('RGB'))
    return float((pixels.min(axis=2) >= 245).mean()) >= .995


def recover_white_browser(s, account=None):
    """Switch Blue Diamond then game once; never refresh or authorize game input.

    The native links stay outside Flash. A write-ahead receipt prevents another
    toggle after a lost acknowledgement, including across worker restarts.
    Normal identity and action guards still run after the content returns.
    """
    if (getattr(s, 'no_content_clicks', True)
            or s.config.get('WhiteTabRecovery', 'toolbar_toggle') != 'toolbar_toggle'
            or getattr(s, '_white_browser_recovery', False)
            or not isinstance(getattr(s, 'identity_directory', None), Path)):
        return False
    import hyol_scheduler as hs
    from hyol_accounts import game_session_token
    from hyol_checkpoints import read_checkpoint
    from hyol_multi_solo import active_tab_centers, tab_close_positions

    token = None
    markers = None
    selected = None

    def capture_native():
        s.check_input_pause()
        if (not s.hwnd or Win32.game_windows() != [s.hwnd]
                or Win32.foreground() != s.hwnd or Win32.rect(s.hwnd) != WINDOW
                or Win32.user32.IsIconic(s.hwnd)):
            raise SafetyError('白屏恢复前游戏窗口、前台或尺寸变化')
        context = getattr(s, 'run_context', None)
        if context is not None:
            context.check_window(s)
        if token is not None and game_session_token(s) != token:
            raise SafetyError('白屏恢复期间游戏会话变化')
        frame = ImageGrab.grab(bbox=WINDOW, all_screens=True).convert('RGB')
        if (Win32.game_windows() != [s.hwnd] or Win32.foreground() != s.hwnd
                or Win32.rect(s.hwnd) != WINDOW
                or token is not None and game_session_token(s) != token):
            raise SafetyError('白屏恢复抓图期间游戏失去前台或尺寸变化')
        current_markers = tab_close_positions(frame)
        centers = active_tab_centers(current_markers)
        # Derive coordinates from the current native layout, not an old map.
        original = s.config.get('TabXs')
        s.config['TabXs'] = ','.join(map(str, centers))
        try:
            active = s._generic_active_tab_x(frame)
        finally:
            if original is None:
                s.config.pop('TabXs', None)
            else:
                s.config['TabXs'] = original
        if (markers is not None and current_markers != markers
                or selected is not None and active != selected
                or account is not None and account.tab_x != active):
            raise SafetyError('白屏恢复期间标签布局或当前账号标签变化')
        return frame, current_markers, active

    def link_position(frame, kind):
        asset, roi = LINKS[kind]
        ok, _, x, y = s.templates.match(frame, hs.ASSETS / asset, roi, threshold=.98)
        if not ok:
            raise PendingError('白屏恢复顶部' + ('蓝钻' if kind == 'blue' else '游戏窗口') + '入口未核实')
        return x, y

    s._white_browser_recovery = True
    try:
        frame, markers, selected = capture_native()
        native_blank = False
        try:
            Win32.game_content_rect(s.hwnd)
        except SafetyError as exc:
            if str(exc) != 'game content surface is ambiguous: 0 visible regions':
                raise
            native_blank = True
        if not white_content(frame, native_blank=native_blank):
            return False
        token = game_session_token(s)
        for kind in LINKS:
            link_position(frame, kind)
        s.wait_interruptible(.3)
        frame, _, _ = capture_native()
        if not white_content(frame, native_blank=native_blank):
            return False
        for kind in LINKS:
            link_position(frame, kind)
        folder = s.identity_directory / 'browser_recovery' / hashlib.sha256(token.encode()).hexdigest()[:20]
        path = folder / ('tab_' + str(selected) + '.json')
        previous = read_checkpoint(path)
        if previous.get('status') in ('pending', 'failed'):
            raise PendingError('本会话此标签原白屏切换结果未核实，保留凭据，不重复发送')
        record = dict(status='pending', session=token, tab_x=selected,
            attempts=previous.get('attempts', 0) + 1, sequence=['blue', 'game'],
            game_content_clicks=0, started_at=hs.now_iso())
        hs.atomic_json(path, record)
        s.event('white_tab_toolbar_recovery_started', account, tab_x=selected,
                detail='先切到蓝钻，再返回游戏窗口；保留原登录、镖车和动作凭据')
        for kind in ('blue', 'game'):
            frame, _, _ = capture_native()
            x, y = link_position(frame, kind)
            point = (x + (12 if kind == 'blue' else 25), y + 7)
            record['requested_link'] = kind
            hs.atomic_json(path, record)

            def guard(kind=kind, point=point):
                fresh, _, _ = capture_native()
                fx, fy = link_position(fresh, kind)
                if (fx + (12 if kind == 'blue' else 25), fy + 7) != point:
                    raise SafetyError('白屏恢复入口位置在点击前变化')
                if Win32.hit_test(s.hwnd, *point) != 1:
                    raise SafetyError('白屏恢复入口不在已核实的窗口客户区')

            Win32.click(*point, expected_hwnd=s.hwnd, before_down=guard)
            s.click_count += 1
            record.setdefault('sent_links', []).append(kind)
            hs.atomic_json(path, record)
            s.event('chrome_click', account, target='white_tab_toolbar_' + kind,
                    x=point[0], y=point[1])
            s.wait_interruptible(.5)
        for _ in range(12):
            frame, _, _ = capture_native()
            try:
                content = Win32.game_content_rect(s.hwnd)
            except SafetyError as exc:
                if str(exc) != 'game content surface is ambiguous: 0 visible regions':
                    raise
            else:
                normalized = GameGeometry(WINDOW, content).normalize(frame)
                if not white_content(frame) and s.healthy(normalized):
                    proof = folder / ('tab_' + str(selected) + '_recovered_' + str(record['attempts']) + '.png')
                    frame.save(proof)
                    record.update(status='recovered', evidence=str(proof), finished_at=hs.now_iso())
                    hs.atomic_json(path, record)
                    s.event('white_tab_recovered', account, method='blue_then_game', evidence=str(proof),
                            detail='原游戏内容已恢复；后续仍须重新核实身份及原任务')
                    return True
            s.wait_interruptible(.5)
        record.update(status='failed', finished_at=hs.now_iso())
        hs.atomic_json(path, record)
        return False
    finally:
        s._white_browser_recovery = False
