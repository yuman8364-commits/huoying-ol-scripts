"""Hide other players only from a verified village and a known toggle state."""
import time

import hyol_scheduler as hs


OPEN = 'other_players_open.bmp'
CLOSED = 'other_players_closed.bmp'
TOGGLE_ROI = (1825, 69, 1875, 120)
THRESHOLD = .98


def observe(s, frame):
    """An absent eye or conflicting templates cannot authorize a toggle."""
    opened = s.template(OPEN, TOGGLE_ROI, frame, threshold=THRESHOLD)
    closed = s.template(CLOSED, TOGGLE_ROI, frame, threshold=THRESHOLD)
    if bool(opened[0]) == bool(closed[0]):
        return None
    match = opened if opened[0] else closed
    return ('open' if opened[0] else 'closed', match[2], match[3])


def _guard(s, account, frame, *, allow_leaf_occlusion=False,require_leaf=True):
    from hyol_navigation import LEAF_NAME_ROI, preparation_blocked,organization_world_visible
    s.check_stop()
    if (s._generic_active_tab_x(frame) != account.tab_x
            or not s.verify_identity(account, frame)[0]
            or not (s.main_ready(frame) or not require_leaf and organization_world_visible(s,frame))
            or preparation_blocked(s, frame)):
        raise hs.PendingError('关闭玩家显示前，当前标签、账号或木叶主界面未确认')
    leaf = s.template('leaf_village_name_current.bmp', LEAF_NAME_ROI,
                      frame, threshold=.92)[0]
    if require_leaf and not leaf and not allow_leaf_occlusion:
        raise hs.PendingError('关闭玩家显示前，当前标签、账号或木叶主界面未确认')
    return leaf or not require_leaf


def ensure_hidden(s, account, *, timeout=4,require_leaf=True):
    """Bounded observation; optional missing toggle never authorizes a click."""
    from hyol_navigation import move_cursor_clear
    row = s.state.accounts[account.name].setdefault('player_display', {})
    clicks_before = s.content_click_count
    frame = None
    context = getattr(s, 'run_context', None)
    cache_owner = context if context is not None else s
    cache = getattr(cache_owner, '_player_display_receipts', None)
    if not isinstance(cache, dict):
        cache = {}
        cache_owner._player_display_receipts = cache
    try:
        frame = s.capture()
        _guard(s, account, frame,require_leaf=require_leaf)
        proof = cache.get(account.name)
        current = observe(s, frame)
        if (isinstance(proof, dict) and proof.get('hwnd') == getattr(s, 'hwnd', None)
                and proof.get('tab_x') == account.tab_x and proof.get('evidence')
                and len(proof.get('samples', [])) == 3 and current is not None
                and current[0] == 'closed'):
            # A receipt belongs only to this live scheduler/Start. Identity,
            # page and the actual toggle are still verified on a fresh frame.
            row.update(status='done', phase='hidden_verified', hidden=True,
                       evidence=proof['evidence'], samples=proof['samples'],
                       last_check_clicks=0, last_error='')
            return dict(hidden=True, evidence=proof['evidence'], samples=proof['samples'],
                        clicks=0, reused=True)
        cache.pop(account.name, None)
        move_cursor_clear(s)
        original = None
        samples = []
        stable = 0
        for attempt in range(5):
            frame = s.capture()
            _guard(s, account, frame,require_leaf=require_leaf)
            current = observe(s, frame)
            if current is None or (original is not None and current != original):
                stable = 0
                samples.clear()
            original = current
            if current is not None:
                stable += 1
                if current[0] == 'closed':
                    samples.append(str(s.save_raw_evidence(
                        frame, account, 'player_display_hidden_'+str(stable), 'verified')))
                if stable == 3:
                    break
            if attempt < 4:
                s.wait_interruptible(.2)
        if stable < 3:
            # This cosmetic option is not a prerequisite for running tasks.
            # Never erase an earlier unconfirmed toggle or relax account/page guards.
            if row.get('status') == 'pending' and row.get('phase') == 'click_requested':
                raise hs.PendingError('上次关闭玩家显示的点击尚未确认，不重复切换')
            detail = '玩家显示开关五次观察仍未确认，跳过隐藏玩家，不发送点击；继续任务'
            evidence = str(s.save_raw_evidence(frame, account, 'player_display', 'skipped'))
            row.update(status='skipped', phase='not_sent', hidden=False, last_error=detail,
                       pending_evidence=evidence, last_check_clicks=0, updated_at=hs.now_iso())
            s.state.save_account(account.name)
            s.event('other_players_hide_skipped', account, detail=detail, evidence=evidence, clicks=0)
            return dict(hidden=False, skipped=True, evidence=evidence, samples=[], clicks=0)

        if original[0] == 'open':
            if row.get('status') == 'pending' and row.get('phase') == 'click_requested':
                raise hs.PendingError('上次关闭玩家显示的点击尚未确认，不重复切换')
            before = str(s.save_raw_evidence(frame, account, 'player_display', 'before'))
            row.update(status='pending', phase='click_requested', hidden=False,
                       before_evidence=before, requested_at=hs.now_iso())
            s.state.save_account(account.name)

            def still_open(fresh):
                _guard(s, account, fresh,require_leaf=require_leaf)
                return observe(s, fresh) == original

            if not s.click_template(account, OPEN, TOGGLE_ROI, 8, 8,
                    'hide_other_players', ['anchor_top_menu.bmp', OPEN],
                    threshold=THRESHOLD, anchor_threshold=THRESHOLD,
                    frame_predicate=still_open):
                row['phase'] = 'not_sent'
                raise hs.PendingError('玩家显示开关在点击前变化，未发送切换')
            move_cursor_clear(s)

        deadline = time.monotonic() + max(0., min(float(timeout), 10.))
        while len(samples) < 3:
            frame = s.capture()
            # Flash keeps the eye hover and its tooltip for a repaint after
            # the cursor leaves. It covers the village label too. Wait without
            # input; identity, tab, world and result guards remain mandatory.
            leaf_visible = _guard(s, account, frame, allow_leaf_occlusion=True,require_leaf=require_leaf)
            current = observe(s, frame)
            if leaf_visible and current == ('closed', original[1], original[2]):
                samples.append(str(s.save_raw_evidence(
                    frame, account, 'player_display_hidden_'+str(len(samples)+1), 'verified')))
                if len(samples) == 3:
                    break
            else:
                samples.clear()
            if time.monotonic() >= deadline:
                raise hs.PendingError('玩家显示关闭后未连续确认三帧，保留原点击且不重试')
            s.wait_interruptible(.2)
        clicks = s.content_click_count - clicks_before
        row.update(status='done', phase='hidden_verified', hidden=True,
                   evidence=samples[-1], samples=samples, verified_at=hs.now_iso(),
                   last_check_clicks=clicks, last_error='')
        s.state.save_account(account.name)
        cache[account.name] = dict(hwnd=getattr(s, 'hwnd', None), tab_x=account.tab_x,
                                   evidence=samples[-1], samples=list(samples))
        s.event('other_players_hidden_verified', account, evidence=samples[-1],
                samples=samples, clicks=clicks, already_hidden=original[0] == 'closed')
        return dict(hidden=True, evidence=samples[-1], samples=samples, clicks=clicks)
    except (Exception, KeyboardInterrupt) as exc:
        cache.pop(account.name, None)
        row.update(status='pending', hidden=False, last_error=str(exc), updated_at=hs.now_iso())
        if frame is not None:
            row['pending_evidence'] = str(s.save_raw_evidence(
                frame, account, 'player_display', 'pending'))
        s.state.save_account(account.name)
        s.event('other_players_hidden_pending', account, detail=str(exc),
                evidence=row.get('pending_evidence', ''),
                clicks=s.content_click_count-clicks_before)
        raise
