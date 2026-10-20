"""Reveal a hidden identity by closing only positively recognized UI overlays."""
import hashlib
import time
import uuid


def _trial_notice_fingerprint(frame):
    import hyol_scheduler as hs
    mask = hs.identity_text_mask(frame.crop((875,630,1080,658)),threshold=210)
    return hashlib.sha256(mask.tobytes()).hexdigest() if int(mask.sum()) >= 30 else None


def _same_pending_notice(s, row, expected):
    if expected[0] != 'arena_trial_notice':
        return True
    fingerprint = row.get('notice_fingerprint')
    if fingerprint is None:
        from PIL import Image
        try:
            with Image.open(row['before_evidence']) as before:
                observed = inspect(s,before.convert('RGB'))
            if observed and observed[0] == 'arena_trial_notice':
                fingerprint = observed[7]
        except (KeyError,OSError):
            return True
    return fingerprint is None or fingerprint == expected[7]


def clear_hover_preview(s):
    """Move off a tooltip into the live-proven empty arena area; no clicks."""
    import hyol_scheduler as hs
    if (getattr(s,'no_content_clicks',True) or getattr(s,'_identity_observation_only',False)
            or not isinstance(getattr(s,'hwnd',None),int) or not s.hwnd):
        return False
    s.ensure_window()
    s.check_input_pause()
    if hs.Win32.foreground()!=s.hwnd:
        raise hs.SafetyError('清除悬浮说明前游戏窗口失去前台，未移动鼠标')
    hs.Win32.move(*s.screen_point(550,200))
    s.wait_interruptible(.2)
    return True


def inspect(s, frame):
    import hyol_scheduler as hs
    title = s.template('login_update_notice_title_current.bmp', (900,380,1230,490), frame, threshold=.98)
    body = s.template('login_update_notice_body_current.bmp', (760,550,1040,640), frame, threshold=.98)
    close = s.template('login_update_notice_close_core_current.bmp', (1350,415,1425,490), frame, threshold=.98)
    if title[0] or body[0]:
        if title[0] and body[0] and close[0]:
            return ('update_notice', 'login_update_notice_close_core_current.bmp',
                    (1350,415,1425,490), 12, 12, close[2], close[3])
        return ('blocked_update_notice',)
    if s.template('arena_trial_notice_title_current.bmp',(780,270,1190,390),frame,threshold=.98)[0]:
        close = s.template('arena_trial_notice_close_current.bmp',(1190,290,1290,390),frame,threshold=.98)
        fingerprint = _trial_notice_fingerprint(frame)
        if not close[0] or fingerprint is None:
            return ('blocked_arena_trial_notice',)
        return ('arena_trial_notice','arena_trial_notice_close_current.bmp',(1190,290,1290,390),11,11,close[2],close[3],fingerprint)
    if (s.template('team_public_room_title_current.bmp',(900,230,1100,315),frame,threshold=.98)[0]
            and s.template('team_create_button.bmp',(951,650,1301,850),frame,threshold=.98)[0]):
        close = s.template('team_room_close.bmp',(1360,250,1450,340),frame,threshold=.98)
        if not close[0]:
            return ('blocked_public_team_room',)
        return ('public_team_room','team_room_close.bmp',(1360,250,1450,340),20,20,close[2],close[3])
    if (s.template('quiz_page.bmp',(820,380,1060,450),frame,threshold=.98)[0]
            and s.template('quiz_zero.bmp',(1150,413,1250,445),frame,threshold=.98)[0]
            and s.template('quiz_finish.bmp',(550,300,1350,850),frame,threshold=.98)[0]):
        # A timed-out quiz can finish while another tab is running. Close its
        # proven terminal page to reveal the name, without claiming rewards or
        # changing the previous quiz/submission receipts.
        close = s.template('market_close.bmp',(1250,325,1350,410),frame,threshold=.98)
        if not close[0]:
            return ('blocked_quiz_finished',)
        return ('quiz_finished','market_close.bmp',(1250,325,1350,410),15,15,close[2],close[3])
    for kind, anchor, roi, button, close_roi, offset in (
            ('black_market', 'market_anchor.bmp', (651,69,1251,319),
             'market_close.bmp', (1301,219,1919,369), 15),
            ('welfare', 'welfare_title_current.bmp', (1120,230,1320,380),
             'welfare_close_current.bmp', (1320,250,1450,350), 15),
            ('mail', 'mail_ready_anchor.bmp', (801,199,1101,369),
             'mail_close.bmp', (1300,230,1420,350), 15)):
        anchor_match = s.template(anchor, roi, frame, threshold=.98)
        close_match = s.template(button, close_roi, frame, threshold=.98)
        if anchor_match[0]:
            if not close_match[0]:
                return ('blocked_'+kind,)
            return (kind, button, close_roi, offset, offset, close_match[2], close_match[3])
    if s.template('survival_anchor.bmp',(201,69,601,419),frame,threshold=.95)[0]:
        # A stopped run can leave the full-screen map covering the name.
        # Close the ready map only; battle/result controls keep it blocked.
        blocked = any(s.template(name,roi,frame,threshold=.98)[0]
                for name,roi in (
                    ('survival_result_confirm.bmp',(701,519,1151,819)),
                    ('survival_reward_result_confirm.bmp',(701,569,1151,819)),
                    ('survival_fight_start.bmp',(751,169,1151,419)),
                    ('survival_fight_start_core.bmp',(751,169,1151,419))))
        if s._battle_hud_visible(frame) or blocked:
            return ('blocked_survival_lobby',)
        for button in ('market_close.bmp','survival_close.bmp'):
            close = s.template(button,(1585,77,1685,177),frame,threshold=.98)
            if close[0]:
                return ('survival_lobby',button,(1585,77,1685,177),15,15,close[2],close[3])
        return ('blocked_survival_lobby',)
    if s.template('arena_ready_anchor.bmp',(851,199,1451,449),frame,threshold=.98)[0]:
        blocked=any(s.template(name,roi,frame,threshold=.98)[0] for name,roi in (
            ('arena_matching_anchor.bmp',(601,349,1201,751)),
            ('arena_loading_anchor.bmp',hs.ARENA_LOADING_ROI),
            ('arena_result_confirm.bmp',(651,449,1151,829)),
            ('arena_defeat_confirm.bmp',(801,580,1101,760)),
            ('arena_rankup_confirm.bmp',(801,600,1101,760)),
            ('arena_rankdown_banner.bmp',(700,220,1250,430))))
        waiting=getattr(s,'_arena_waiting_battle_visible',None)
        hud=getattr(s,'_battle_hud_visible',None)
        if blocked or (callable(waiting) and waiting(frame)) or (callable(hud) and hud(frame)):
            return ('blocked_arena_lobby',)
        button=any(s.template(name,roi,frame,threshold=.98)[0] for name,roi in (
            ('arena_ranked_button_current.bmp',(521,719,851,919)),
            ('arena_casual_button_current.bmp',(760,719,1050,919))))
        close=s.template('market_close.bmp',(1585,77,1685,177),frame,threshold=.98)
        if button and close[0]:
            return ('arena_lobby','market_close.bmp',(1585,77,1685,177),15,15,close[2],close[3])
        return ('blocked_arena_lobby',)
    return None


def dismiss_identity_overlays(s, account, frame=None, *, only_kind=None):
    """Acknowledge UI only; never identify an account or settle a task/result."""
    import hyol_scheduler as hs
    from hyol_accounts import _tab_title_mask, _same_tab_title, game_session_token
    from hyol_checkpoints import read_checkpoint
    frame = frame if frame is not None else s.capture()
    if s.no_content_clicks or getattr(s, '_identity_observation_only', False):
        return frame
    if (any(s.template(name,roi,frame,threshold=.98)[0] for name,roi in (
            ('arena_ranked_button_current.bmp',(521,719,851,919)),
            ('arena_casual_button_current.bmp',(760,719,1050,919))))
            and clear_hover_preview(s)):
        frame=s.capture()
    initial = inspect(s,frame)
    if initial is None or (only_kind is not None and initial[0] != only_kind):
        return frame
    title_mask = _tab_title_mask(frame, account.tab_x)
    title_hash = hashlib.sha256(title_mask.tobytes()).hexdigest()
    path = s.shared_state_directory/'identity_overlay_cleanup.json'
    nontrial_layers = 0
    for layer in range(8):
        expected = inspect(s, frame)
        if expected is None:
            return frame
        if only_kind is not None and expected[0] != only_kind:
            return frame
        if expected[0] != 'arena_trial_notice':
            nontrial_layers += 1
            if nontrial_layers > 3:
                raise hs.PendingError('身份遮挡页面超过三层，保留待核实')
        if expected[0].startswith('blocked_'):
            raise hs.PendingError('身份遮挡页面标题已确认，但其独立关闭X未确认；未点击下层窗口')
        def require(current):
            observed = inspect(s,current)
            same_close = (observed is not None and len(observed)==len(expected)
                          and observed[:5]==expected[:5]
                          and abs(observed[5]-expected[5])<=8 and abs(observed[6]-expected[6])<=8
                          and (expected[0] != 'arena_trial_notice' or observed[7] == expected[7]))
            if (s.offline_dialog_visible(current)
                    or s._generic_active_tab_x(current) != account.tab_x
                    or not _same_tab_title(title_mask, _tab_title_mask(current,account.tab_x))
                    or not same_close):
                evidence = str(s.save_raw_evidence(current,account,'identity_overlay_changed','pending'))
                s.event('identity_overlay_changed',account,expected=expected,observed=observed,
                        selected_tab=s._generic_active_tab_x(current),expected_tab=account.tab_x,
                        title_score=hs.mask_f1(title_mask[:21],_tab_title_mask(current,account.tab_x)[:21]),
                        evidence=evidence,content_clicks=0)
                raise hs.PendingError('身份遮挡页面或当前标签变化，未点击关闭；证据='+evidence)
        for sample in range(3):
            s.check_stop()
            s.ensure_window()
            frame = frame if sample == 0 else s.capture()
            require(frame)
            if sample < 2:
                s.wait_interruptible(.35)
        records = read_checkpoint(path)
        session = game_session_token(s)
        held = [key for key,row in records.items() if row.get('status') == 'pending'
                and row.get('session') == session and row.get('tab_x') == account.tab_x
                and row.get('title_hash') == title_hash and row.get('kind') == expected[0]]
        held = [key for key in held if _same_pending_notice(s,records[key],expected)]
        if held:
            raise hs.PendingError('原身份遮挡X关闭请求尚未核实，不重复点击')
        identifier = uuid.uuid4().hex
        before = str(s.save_raw_evidence(frame,account,'identity_overlay_'+expected[0],'before'))
        row = dict(status='pending',kind=expected[0],session=session,tab_x=account.tab_x,
                   title_hash=title_hash,before_evidence=before,started_at=hs.now_iso(),
                   battle_credit=False,task_credit=False,identity='unconfirmed',
                   notice_fingerprint=expected[7] if expected[0] == 'arena_trial_notice' else None)
        records[identifier] = row
        hs.atomic_json(path,records)
        names = ('active','generic_current_tab_x','current_account_only','_verified_current_mask')
        missing = object()
        prior = {name:getattr(s,name,missing) for name in names}
        guard_override = s.__dict__.get('content_guard',missing)
        original_guard = s.content_guard
        def overlay_guard(selected,page_anchors=(),anchor_threshold=None):
            if selected != account:
                raise hs.SafetyError('身份遮挡关闭仅允许当前已观察标签')
            current = original_guard(selected,page_anchors,anchor_threshold=anchor_threshold)
            require(current)
            return current
        try:
            s.active = account
            s.generic_current_tab_x = account.tab_x
            s.current_account_only = account.name
            s._verified_current_mask = None
            s.content_guard = overlay_guard
            if not s.click_template(account,expected[1],expected[2],expected[3],expected[4],
                    'identity_overlay_close_'+identifier,[expected[1]],threshold=.98,anchor_threshold=.98):
                records = read_checkpoint(path)
                records[identifier] = dict(row,status='not_sent',detail='点击接口正向确认未发送输入')
                hs.atomic_json(path,records)
                raise hs.PendingError('身份遮挡页面关闭X点击前变化，原请求保留')
        finally:
            if guard_override is missing:
                s.__dict__.pop('content_guard',None)
            else:
                s.content_guard = guard_override
            for name,value in prior.items():
                if value is missing:
                    s.__dict__.pop(name,None)
                else:
                    setattr(s,name,value)
        deadline = time.monotonic()+12
        stable = 0
        replacement_notice = None
        while time.monotonic() < deadline:
            s.check_stop()
            s.wait_interruptible(.35)
            frame = s.capture()
            if (s.offline_dialog_visible(frame) or s._generic_active_tab_x(frame) != account.tab_x
                    or not _same_tab_title(title_mask,_tab_title_mask(frame,account.tab_x))):
                raise hs.PendingError('关闭后标签或在线状态变化，关闭凭据保留')
            current = inspect(s,frame)
            replaced_trial = (expected[0] == 'arena_trial_notice' and current is not None
                              and current[0] == expected[0] and current[7] != expected[7])
            if replaced_trial:
                stable = stable+1 if replacement_notice == current[7] else 1
                replacement_notice = current[7]
            else:
                stable = stable+1 if current is None or current[0] != expected[0] else 0
                replacement_notice = None
            if stable == 3:
                after = str(s.save_raw_evidence(frame,account,'identity_overlay_'+expected[0],'verified'))
                records = read_checkpoint(path)
                records[identifier] = dict(row,status='done',evidence=after,finished_at=hs.now_iso(),
                                           main_ready=s.main_ready(frame))
                hs.atomic_json(path,records)
                s.event('identity_overlay_closed',account,kind=expected[0],evidence=after,
                        receipt=identifier,identity='unconfirmed',battle_credit=False,task_credit=False)
                break
        else:
            raise hs.PendingError('身份遮挡X已发送，页面关闭未连续确认；不重复点击')
    remaining = inspect(s,frame)
    if remaining is not None and (only_kind is None or remaining[0] == only_kind):
        raise hs.PendingError('已确认通知超过八层，保留待核实')
    return frame
