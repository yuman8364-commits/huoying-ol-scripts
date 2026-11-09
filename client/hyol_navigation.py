"""Explicit safe return action shared by clients. Never starts or settles combat."""
import time


SPRING_CLOSE_ROI = (550, 70, 1650, 450)
SPRING_TITLE_ROI = (780, 230, 1100, 430)
SPRING_PROMPT_ROI = (600, 350, 1300, 800)
HOME_LEAVE_ROI = (1800, 930, 1920, 1032)
LEAF_NAME_ROI = (1690, 110, 1920, 148)
LEAF_RETURN_ROI = (1870, 69, 1920, 125)
MARKET_TITLE_ROI = (651, 69, 1251, 319)
MARKET_CLOSE_ROI = (1301, 219, 1919, 369)

# Only reversible page closes. Never include reward, start or dissolve controls.
PREPARE_CLOSE_CONTROLS = (
    ('daruma_weekend_page.bmp', (880,245,1190,360), 'daruma_weekend_close.bmp', (1330,288,1385,345)),
    ('anniversary_title_20261105.bmp', (880,210,1180,310), 'anniversary_close_20261105.bmp', (1325,280,1370,325)),
    ('anniversary_catalogue_title_20261105.bmp', (880,210,1180,310), 'anniversary_close_20261105.bmp', (1325,280,1370,325)),
    ('training_record_title.bmp', (390,120,770,280), 'training_record_close.bmp', (1350,215,1420,290)),
    ('escort_page_title.bmp', (1,69,1919,1031), 'escort_close.bmp', (1,69,1919,1031)),
    ('arena_ready_anchor.bmp', (851,199,1451,449), 'survival_close.bmp', (1550,60,1700,200)),
    ('ranking_page.bmp', (790,95,1080,195), 'ranking_close.bmp', (1600,95,1670,160)),
    ('team_dissolve_button.bmp', (1060,315,1160,380), 'team_room_close.bmp', (1200,260,1280,340)),
    ('team_public_room_title_current.bmp', (900,230,1100,315), 'team_room_close.bmp', (1360,250,1450,340)),
    ('team_public_room_title_current.bmp', (900,230,1100,315), 'team_room_close.bmp', (1200,260,1280,340)),
    ('team_create_button.bmp', (951,650,1301,850), 'team_room_close.bmp', (1200,260,1280,340)),
    ('ninja_page_current.bmp', (1,69,1919,1031), 'ninja_close_current.bmp', (1570,100,1640,175)),
    ('grocery_title_current.bmp', (840,90,1100,185), 'grocery_close_current.bmp', (1570,110,1660,205)),
    ('merchant_title_20261109.bmp', (800,230,1130,315), 'market_close.bmp', (1350,270,1420,340)),
    ('mail_warehouse_anchor.bmp', (1,69,1919,1031), 'mail_warehouse_close.bmp', (1174,259,1275,359)),
    ('mail_ready_anchor.bmp', (1,69,1919,1031), 'mail_close.bmp', (1301,249,1401,349)),
    ('welfare_title_current.bmp', (1,69,1919,1031), 'welfare_close_current.bmp', (1320,250,1450,350)),
    ('daily_practice_anchor.bmp', (1,69,1919,1031), 'daily_practice_close.bmp', (1370,200,1460,280)),
    ('group_title_current.bmp', (850,210,1100,290), 'team_instance_close.bmp', (1370,240,1460,330)),
    ('survival_anchor.bmp', (201,69,601,419), 'survival_close.bmp', (1585,77,1685,177)),
    ('wanted_team_anchor.bmp', (500,200,1450,850), 'wanted_close_current.bmp', (1310,290,1400,390)),
    ('strong_reward_label_core_current.bmp', (1,69,1919,1031), 'team_activity_close.bmp', (1300,280,1420,390)),
    ('managed_settings_title_current.bmp', (750,190,1170,290), 'managed_settings_close_current.bmp', (1320,230,1450,330)),
)

# Measured on three live frames per affected tab. A miss always falls back to
# the original search region; this hint never authorizes a click on its own.
PREPARE_FAST_ANCHOR_ROIS = {'ninja_page_current.bmp': (330,275,490,350)}


def content_loading_visible(s, frame):
    """A resource loader may cover a perfectly recognizable village page."""
    roi = (600,525,1360,635)
    if (s.template('content_loading_word_current.bmp', roi, frame, threshold=.98)[0]
            and s.template('content_loading_path_current.bmp', roi, frame, threshold=.98)[0]):
        return True
    if not s.template('map_loading_word_current.bmp', roi, frame, threshold=.98)[0]:
        return False
    return (any(s.template(name, roi, frame, threshold=.98)[0] for name in
                ('map_loading_path_current.bmp', 'map_loading_finished_current.bmp'))
            or (s.template('map_loading_bar_left_current.bmp', (730,520,790,560), frame, threshold=.98)[0]
                and s.template('map_loading_bar_right_current.bmp', (1140,520,1190,560), frame, threshold=.98)[0]))


def group_card_result_visible(s, frame):
    """The card settlement dims the village and hides its identity strip."""
    return (s.template('group_card_result_title_20261109.bmp', (790,190,1140,330), frame, threshold=.98)[0]
            and s.template('group_return_game_current.bmp', (1100,750,1400,880), frame, threshold=.98)[0])


def preparation_blocked(s, frame):
    """A ready screen is navigable; a battle or unaccounted result is not."""
    import hyol_scheduler as hs
    if content_loading_visible(s, frame) or group_card_result_visible(s, frame):
        return True
    if s.template('quiz_page.bmp', (820,380,1060,450), frame, threshold=.92)[0]:
        return True
    result_controls = (
        ('arena_result_confirm.bmp', (651,449,1151,829)),
        ('team_result_confirm.bmp', hs.TEAM_BATTLE_RESULT_ROI),
        ('team_reward_result_confirm.bmp', hs.TEAM_REWARD_RESULT_ROI),
        ('team_reward_result_header_current.bmp', (780,340,1140,470)),
        ('strong_score_title_20261109.bmp', (780,230,1140,370)),
        ('ranking_result_confirm.bmp', (860,640,1060,710)),
        ('survival_result_confirm.bmp', (701,519,1151,819)),
        ('survival_reward_result_confirm.bmp', (701,569,1151,819)),
        ('arena_rankup_confirm.bmp', (801,600,1101,760)),
        ('arena_rankdown_banner.bmp', (700,220,1250,430)),
    )
    if any(s.template(asset, roi, frame, threshold=.98)[0] for asset,roi in result_controls):
        return True
    if (s.template('arena_matching_anchor.bmp',(601,349,1201,751),frame)[0]
            or s.template('arena_loading_anchor.bmp',hs.ARENA_LOADING_ROI,frame)[0]
            or s._arena_waiting_battle_visible(frame)
            or s._battle_hud_visible(frame)
            or s.template('ranking_battle_anchor.bmp',(1400,205,1510,251),frame,threshold=.98)[0]):
        return True
    if (s._combat_active_visible('arena',frame) and not s._combat_ready_visible('arena',frame)):
        return True
    if (s._combat_active_visible('survival',frame) and not s._combat_ready_visible('survival',frame)):
        return True
    return False


def preparation_panel(s, frame):
    for anchor, anchor_roi, close, roi in PREPARE_CLOSE_CONTROLS:
        fast_roi = PREPARE_FAST_ANCHOR_ROIS.get(anchor)
        anchor_ok = bool(fast_roi and s.template(anchor,fast_roi,frame,threshold=.98)[0])
        if ((anchor_ok or s.template(anchor,anchor_roi,frame,threshold=.98)[0])
                and s.template(close,roi,frame,threshold=.98)[0]):
            if (anchor == 'arena_ready_anchor.bmp' and not s._combat_ready_visible('arena', frame)):
                continue
            return anchor, anchor_roi, close, roi
    return None


def move_cursor_clear(scheduler):
    """Remove hover text using the same normalized geometry as game input."""
    import hyol_scheduler as hs
    if isinstance(scheduler.hwnd, int):
        scheduler.check_input_pause()
        if hs.Win32.foreground() != scheduler.hwnd:
            raise hs.SafetyError('移除悬停前游戏窗口失去前台')
        hs.Win32.move(*scheduler.screen_point(1100, 50))


def _record(s, account, key):
    return s.state.accounts[account.name].setdefault('navigation', {}).setdefault(key, {})


def _step(s, account, record, phase, frame, detail):
    import hyol_scheduler as hs
    evidence = s.save_raw_evidence(frame, account, 'navigation_'+phase, 'verified')
    row = {'phase': phase, 'at': hs.now_iso(), 'evidence': str(evidence), 'detail': detail}
    record.update(phase=phase, updated_at=row['at'])
    record.setdefault('steps', []).append(row)
    s.state.save_account(account.name)
    s.event('navigation_step_verified', account, **row)


def hot_spring_visible(s, frame):
    return (s.template('hot_spring_title_current.bmp', SPRING_TITLE_ROI, frame, threshold=.98)[0]
            and s.template('hot_spring_panel_close.bmp', SPRING_CLOSE_ROI, frame, threshold=.98)[0])


def home_visible(s, frame):
    return (s.template('home_map_name_current.bmp',(1690,110,1850,148),frame,threshold=.98)[0]
            and s.template('home_leave_text_current.bmp',HOME_LEAVE_ROI,frame,threshold=.98)[0])


def organization_world_visible(s, frame):
    # Organization maps have a different toolbar, so main_ready intentionally
    # stays false. Require its map name and the existing return control.
    title=any(s.template(name,LEAF_NAME_ROI,frame,threshold=.98)[0] for name in (
        'organization_map_name_current.bmp','organization_map_name_20261028.bmp'))
    return title and any(s.template(name,LEAF_RETURN_ROI,frame,threshold=threshold)[0]
        for name,threshold in (('leaf_return_current.bmp',.92),('leaf_return_hover.bmp',.92),
            ('leaf_return_core_current.bmp',.98),('leaf_return_core_forest2_current.bmp',.98),
            ('leaf_return_core_desert_current.bmp',.98),('leaf_return_core_desert2_current.bmp',.98)))


def _world_visible(s, frame):
    return (not hot_spring_visible(s, frame)
            and not s.template('hot_spring_leave_prompt_text.bmp', SPRING_PROMPT_ROI, frame, threshold=.98)[0]
            and not s.template('home_leave_prompt_text.bmp', SPRING_PROMPT_ROI, frame, threshold=.98)[0]
            and not s.template('hot_spring_leave_confirm.bmp', SPRING_PROMPT_ROI, frame, threshold=.98)[0]
            and not home_visible(s,frame)
            and s.main_ready(frame))


def _stable_world(s, account, frame):
    for sample in range(3):
        if not s.verify_identity(account, frame)[0] or not _world_visible(s, frame):
            return None
        if sample < 2:
            s.wait_interruptible(.35)
            frame = s.capture()
    return frame


def close_black_market(s, account, *, frame=None, timeout=12):
    """Close only the verified market X, preserving every task/action record."""
    import hyol_scheduler as hs
    frame = frame if frame is not None else s.capture()
    visible = s.template('market_anchor.bmp', MARKET_TITLE_ROI, frame, threshold=.98)[0]
    previous = s.state.accounts[account.name].get('navigation', {}).get('black_market_exit', {})
    pending_close = previous.get('status') == 'pending' and previous.get('phase') == 'close_requested'
    if not visible and not pending_close:
        return False
    record = _record(s, account, 'black_market_exit')
    deadline = time.monotonic() + timeout
    if visible and not pending_close:
        expected = None
        for sample in range(3):
            s.check_stop()
            close = s.template('market_close.bmp', MARKET_CLOSE_ROI, frame, threshold=.98)
            if (not s.verify_identity(account, frame)[0]
                    or not s.template('market_anchor.bmp', MARKET_TITLE_ROI, frame, threshold=.98)[0]
                    or not close[0] or s._combat_active_visible('arena', frame)
                    or s._combat_active_visible('survival', frame)):
                raise hs.PendingError('黑市身份、标题或关闭X未连续确认，未点击关闭')
            position = close[2:4]
            if expected is not None and position != expected:
                raise hs.PendingError('黑市关闭X位置变化，未点击关闭')
            expected = position
            if sample < 2:
                s.wait_interruptible(.35)
                frame = s.capture()
        record.update(status='pending')
        _step(s, account, record, 'close_requested', frame, '黑市身份、标题和X连续三帧确认，仅关闭页面')
        if not s.click_template(account, 'market_close.bmp', MARKET_CLOSE_ROI, 15, 15,
                                'navigation_black_market_close', ['market_anchor.bmp', 'market_close.bmp'],
                                threshold=.98, anchor_threshold=.98,
                                frame_predicate=lambda fresh: (
                                    s.verify_identity(account, fresh)[0]
                                    and s.template('market_anchor.bmp', MARKET_TITLE_ROI, fresh, threshold=.98)[0]
                                    and not s._combat_active_visible('arena', fresh)
                                    and not s._combat_active_visible('survival', fresh))):
            raise hs.PendingError('黑市关闭X点击前变化，保留请求，不重复点击')
    while time.monotonic() < deadline:
        s.check_stop()
        s.wait_interruptible(.35)
        frame = s.capture()
        if not s.template('market_anchor.bmp', MARKET_TITLE_ROI, frame, threshold=.98)[0]:
            stable = _stable_world(s, account, frame)
            if stable is not None:
                _step(s, account, record, 'main_verified', stable, '黑市X已关闭，实名主界面连续三帧确认')
                record.update(status='done')
                s.state.save_account(account.name)
                s.event('black_market_exit_verified', account, evidence=record['steps'][-1]['evidence'],
                        detail='仅关闭黑市；原任务、次数和未决操作保留')
                return True
    raise hs.PendingError('黑市关闭后主界面未确认，保留原关闭请求，不重复点击')


def leaf_return_button_template(s, frame):
    """Which enrolled rendering of the top-right home return button matches.

    20261105 ZCode: some tab sessions draw the same button with its arrow
    about 17px further left (pending evidence account_1174a0679d 005418
    scored .977 against the 20261030 core). Enroll that rendering as a
    second core and accept either; the .98 threshold is unchanged.
    """
    for name in ('leaf_return_home_core_20261030.bmp',
                 'leaf_return_home_core_shifted_20261105.bmp'):
        if s.template(name, LEAF_RETURN_ROI, frame, threshold=.98)[0]:
            return name
    return None


def request_home_return(s,account,record,frame,*,label):
    """Use the user's home top-right shortcut once; never click bottom Leave."""
    import hyol_scheduler as hs
    name=leaf_return_button_template(s,frame)
    if (name is None
            or not home_visible(s,frame) or hot_spring_visible(s,frame)
            or preparation_blocked(s,frame) or not s.verify_identity(account,frame)[0]):
        raise hs.PendingError('家园右上角返回木叶按钮、身份或页面未核实，未点击离开家园')
    _step(s,account,record,'home_return_requested',frame,'温泉已退出，直接使用家园右上角返回木叶村')
    record['route']='spring_then_top_right_leaf';s.state.save_account(account.name)
    if not s.click_template(account,name,LEAF_RETURN_ROI,10,10,label,[name],
            threshold=.98,anchor_threshold=.98,
            frame_predicate=lambda fresh:home_visible(s,fresh) and not hot_spring_visible(s,fresh)
                and not preparation_blocked(s,fresh)):
        raise hs.PendingError('家园右上角回村按钮点击前变化，保留原请求，不重复点击')
    move_cursor_clear(s)


def leave_hot_spring(s, account, *, timeout=60):
    """Shared resumable X -> verified prompt -> home exit; never guesses a dialog."""
    import hyol_scheduler as hs
    frame = s.capture()
    visible = hot_spring_visible(s, frame)
    accounts = getattr(getattr(s, 'state', None), 'accounts', {})
    previous = (accounts.get(account.name, {}).get('navigation', {}).get('hot_spring_exit', {})
                if isinstance(accounts, dict) else {})
    if not visible and previous.get('status') != 'pending':
        return False
    if (not visible and previous.get('status') == 'pending'
            and s.template('managed_settings_title_current.bmp', (750,190,1170,290), frame, threshold=.98)[0]
            and s.template('managed_settings_close_current.bmp', (1320,230,1450,330), frame, threshold=.98)[0]):
        # A manually opened settings shell is reversible. Let the shared
        # navigator close that shell; keep the old spring phase untouched
        # until the unobstructed world is positively observed.
        return False
    record = _record(s, account, 'hot_spring_exit')
    if record.get('status') == 'done' and visible:
        history = record.get('history', [])[-9:]
        history.append({key:value for key,value in record.items() if key != 'history'})
        record.clear()
        record['history'] = history
    record.setdefault('phase', 'ready')
    record.setdefault('started_at', hs.now_iso())
    record['status'] = 'pending'
    s.state.save_account(account.name)
    deadline = time.monotonic() + timeout
    try:
        while time.monotonic() < deadline:
            s.check_stop()
            frame = s.capture()
            if s._combat_active_visible('arena', frame):
                raise hs.PendingError('温泉退出途中出现战斗，交回原任务核实')
            phase = record['phase']
            prompt_name=('home_leave_prompt_text.bmp' if phase in ('home_exit_requested','home_return_requested','home_confirm_requested')
                         else 'hot_spring_leave_prompt_text.bmp')
            prompt = s.template(prompt_name, SPRING_PROMPT_ROI, frame, threshold=.98)[0]
            confirm = s.template('hot_spring_leave_confirm.bmp', SPRING_PROMPT_ROI, frame, threshold=.98)[0]
            panel = hot_spring_visible(s, frame)
            home = home_visible(s,frame)
            if phase == 'ready':
                if not panel:
                    # No click was ever sent at this stage. An external/manual
                    # move to the world page is observable without inventing
                    # success for this script's exit sequence.
                    if not home and s.main_ready(frame):
                        check = _stable_world(s, account, frame)
                        if check is not None:
                            _step(s, account, record, 'world_verified', check,
                                  '当前已在主界面；原阶段未发送X，仅对账外部页面变化')
                            record.update(status='done', outcome='observed_external_exit', finished_at=hs.now_iso())
                            s.state.save_account(account.name)
                            return True
                    raise hs.PendingError('温泉标题与X未同时确认，未执行退出')
                _step(s, account, record, 'close_requested', frame, '确认温泉标题和X，先关闭温泉窗口')
                if not s.click_template(account, 'hot_spring_panel_close.bmp', SPRING_CLOSE_ROI,
                                        20,20,'navigation_hot_spring_close',
                                        ['hot_spring_title_current.bmp'], threshold=.98, anchor_threshold=.98):
                    raise hs.PendingError('温泉X点击前画面变化，退出待处理')
                move_cursor_clear(s)
            elif prompt and confirm and phase in ('close_requested', 'home_exit_requested','home_return_requested'):
                after = 'close_confirm_requested' if phase == 'close_requested' else 'home_confirm_requested'
                _step(s, account, record, after, frame, '退出提示正文和确定按钮均已核实')
                if not s.click_template(account, 'hot_spring_leave_confirm.bmp', SPRING_PROMPT_ROI,
                                        45,22,'navigation_'+after,
                                        [prompt_name], threshold=.98, anchor_threshold=.98):
                    raise hs.PendingError('温泉退出确认点击前提示变化')
                move_cursor_clear(s)
            elif confirm and not prompt:
                raise hs.PendingError('退出提示正文尚未识别，保留当前提示和导航阶段；不猜测确定按钮')
            elif not panel and not prompt and not confirm:
                if home and phase in ('close_requested', 'close_confirm_requested'):
                    request_home_return(s,account,record,frame,label='navigation_hot_spring_return_leaf')
                elif not home and s.main_ready(frame):
                    verified = _stable_world(s, account, frame)
                    if verified is not None:
                        _step(s, account, record, 'world_verified', verified, '温泉和退出提示消失，连续三帧非战斗主界面')
                        record.update(status='done', finished_at=hs.now_iso())
                        s.state.save_account(account.name)
                        return True
            s.wait_interruptible(.35)
        raise hs.PendingError('温泉退出结果未确认，保留阶段；恢复只观察原动作，不重复X或确认')
    except (Exception, KeyboardInterrupt) as exc:
        record.update(status='pending', last_error=str(exc))
        # F12 and a positively detected disconnect must not cause another
        # capture (or mask the original stop with a second exception).
        evidence = s.save_raw_evidence(frame, account, 'hot_spring_exit_pending', 'pending')
        record['pending_evidence'] = str(evidence)
        s.state.save_account(account.name)
        s.event('navigation_pending', account, phase=record['phase'], detail=str(exc), evidence=str(evidence))
        raise


def return_to_leaf(scheduler, account, *, allow_pending_navigation=False, timeout=60, stopped_treasure_return=False):
    audit = {}
    try:
        result = _return_to_leaf(scheduler, account, allow_pending_navigation, timeout, audit,
                                 stopped_treasure_return=stopped_treasure_return)
    except (Exception, KeyboardInterrupt) as exc:
        record, frame = audit.get('record'), audit.get('frame')
        if record is not None:
            record.update(status='pending', last_error=str(exc))
            if frame is not None:
                record['pending_evidence'] = str(scheduler.save_raw_evidence(
                    frame, account, 'return_leaf_pending', 'pending'))
                record['anchor_scores'] = {name: scheduler.template(name, roi, frame)[1]
                    for name, roi in (('anchor_top_menu.bmp', (500,69,1500,189)),
                                      ('anchor_right_panel.bmp', (1569,289,1919,489)))}
            scheduler.state.save_account(account.name)
            scheduler.event('navigation_pending', account, phase=record.get('phase'),
                            detail=str(exc), evidence=record.get('pending_evidence', ''))
        raise
    # Keep navigation proof separate: a missing display control must not undo
    # the already verified village return or alter any battle/reward receipt.
    if result is True and getattr(scheduler, 'config', {}).get('HideOtherPlayers', '0') == '1':
        from hyol_player_display import ensure_hidden
        ensure_hidden(scheduler, account)
    return result


def retry_leaf_return_once(s, account, record, frame):
    """Retry one idempotent return only with fresh identity, X and tooltip proof."""
    import hyol_scheduler as hs
    if record.get('return_retry_used') or not isinstance(getattr(s,'hwnd',None),int):
        return False
    def safe(current, *, tooltip=False):
        return ((s.main_ready(current) or organization_world_visible(s,current))
                and s.verify_identity(account,current)[0]
                and not preparation_blocked(s,current)
                and not s.template('leaf_village_name_current.bmp',LEAF_NAME_ROI,current,threshold=.92)[0]
                and not s.template('treasure_auto_path_running.bmp',(800,140,1100,200),current,threshold=.98)[0]
                and (not tooltip or s.template('leaf_return_tooltip_text_current.bmp',
                     (1730,110,1919,180),current,threshold=.98)[0]))
    if not safe(frame):
        return False
    selected = None
    for name,dx,dy in (('leaf_return_core_current.bmp',10,10),
            ('leaf_return_core_forest2_current.bmp',10,10),
            ('leaf_return_core_desert_current.bmp',9,10),
            ('leaf_return_core_desert2_current.bmp',9,10)):
        found=s.template(name,LEAF_RETURN_ROI,frame,threshold=.98)
        if found[0]:
            selected=(name,dx,dy,found[2],found[3]);break
    if selected is None:return False
    name,dx,dy,x,y=selected
    s.ensure_window();s.check_input_pause()
    if hs.Win32.foreground()!=s.hwnd:
        raise hs.SafetyError('回村复核前游戏失去前台，未重试')
    hs.Win32.move(*s.screen_point(x+dx,y+dy))
    s.wait_interruptible(.6)
    # The button changes skin on hover. Keep its measured position, but
    # validate the hovered skin together with the independent tooltip.
    hover_name='leaf_return_hover_core_20261027.bmp'
    samples=[]
    for sample in range(3):
        frame=s.capture()
        found=s.template(hover_name,LEAF_RETURN_ROI,frame,threshold=.98)
        if not safe(frame,tooltip=True) or not found[0] or found[2:4]!=(x,y):
            s.event('return_leaf_retry_deferred',account, sample=sample+1,
                    hover_match=found, detail='悬停按钮、位置或独立回村提示未连续确认，未发送重试')
            return False
        samples.append(str(s.save_raw_evidence(frame,account,'return_retry_sample_'+str(sample+1),'verified')))
        if sample<2:s.wait_interruptible(.2)
    record.update(return_retry_used=True,return_retry_samples=samples)
    s.state.save_account(account.name)
    s.event('return_leaf_retry_requested',account,evidence=samples[-1],
            detail='原回村已超时；当前实名、非寻路/战斗、同一按钮及回村提示三帧确认，仅重试一次可逆导航',
            task_credit=False,battle_credit=False)
    if not s.click_template(account,hover_name,LEAF_RETURN_ROI,10,10,'safe_return_leaf_retry_once',
            [hover_name],threshold=.98,anchor_threshold=.98,
            frame_predicate=lambda current:safe(current,tooltip=True)):
        raise hs.PendingError('回村单次重试前画面变化，保留请求')
    move_cursor_clear(s)
    return True


def restart_return_after_offline(s, account, proof):
    """A reviewed offline receipt and a fresh explicit check bound a new return.

    Only the reversible navigation episode is archived. Task/action receipts
    and the original attempt history stay intact.
    """
    import hyol_scheduler as hs
    from PIL import Image
    record=s.state.accounts.get(account.name,{}).get('navigation',{}).get('return_leaf',{})
    marker=proof.get('ts')
    if (not marker or proof.get('account')!=account.name
            or proof.get('event')!='account_disconnected_skipped'
            or record.get('status')!='pending' or record.get('phase')!='return_requested'
            or any(row.get('offline_recovery_consumed')==marker
                   for row in [record,*record.get('history',())])):
        return False
    try:
        with Image.open(proof['evidence']) as offline:
            if not s.template('offline_login_prompt.bmp',(780,450,1140,660),offline,threshold=.98)[0]:
                return False
    except (OSError,KeyError):
        return False
    samples=[]
    for sample in range(3):
        frame=s.capture()
        if (not s.main_ready(frame) or not s.healthy(frame)
                or not s.verify_identity(account,frame)[0] or preparation_blocked(s,frame)):
            raise hs.PendingError('重登后实名主界面未连续确认，保留旧回村阶段')
        samples.append(str(s.save_raw_evidence(frame,account,'return_after_relogin_'+str(sample+1),'verified')))
        if sample<2:s.wait_interruptible(.2)
    previous={key:value for key,value in record.items() if key!='history'}
    history=[*record.get('history',())[-8:],previous]
    record.clear()
    record.update(status='pending',phase='observing',history=history,
                  offline_recovery_consumed=marker,offline_evidence=proof['evidence'],relogin_samples=samples)
    s.state.save_account(account.name)
    s.event('return_leaf_after_relogin_observed',account,evidence=samples[-1],
            offline_evidence=proof['evidence'],task_credit=False,battle_credit=False,
            detail='原掉线证据和本次实名三帧已核实；归档旧导航，不更改任务/动作凭据')
    return True


def stopped_treasure_origin(s,account):
    """Prove an older escort was already left behind before this dig departed.

    This authorizes only the stopped dig's return. No escort receipt is
    archived, settled, modified or credited by this proof.
    """
    import hashlib
    import re
    from datetime import datetime,timezone,timedelta
    from pathlib import Path
    from PIL import Image
    import hyol_scheduler as hs
    from hyol_checkpoints import load_tasks,read_checkpoint
    from hyol_escort import pending_activity_records,deadline_at as escort_deadline
    from hyol_treasure import START_ACTION,PAUSE_ACTION,DURATION_SECONDS
    _,tasks=load_tasks(s.state,account.name)
    treasure=tasks.get('treasure',{})
    if (treasure.get('phase')!='stopped' or treasure.get('stop_verified') is not True
            or treasure.get('stop_stable_frames',0)<3 or not treasure.get('stop_evidence')
            or treasure.get('awaiting')):return None
    start,deadline,stopped=(treasure.get(key) for key in ('started_at','deadline_at','stop_observed_at'))
    if any(type(value) not in (int,float) for value in (start,deadline,stopped)):return None
    if abs(deadline-start-DURATION_SECONDS)>.01 or stopped<deadline:return None
    actions=s.state.accounts[account.name].get('actions',{})
    if any(actions.get(key,{}).get('status')!='done' for key in (START_ACTION,PAUSE_ACTION)):return None
    if any(key.startswith(('daily_treasure_','treasure_')) and row.get('status')=='pending'
           for key,row in actions.items()):return None
    origin=s.state.accounts[account.name].get('navigation',{}).get('return_leaf',{})
    samples=origin.get('samples',[])
    if (origin.get('status')!='done' or origin.get('phase')!='leaf_verified'
            or origin.get('escort_pending_preserved') is not True or origin.get('navigation_sent') is not False
            or not isinstance(samples,list) or len(samples)!=3):return None
    times=[]
    for sample in samples:
        path=Path(sample).resolve();match=re.match(r'(\d{6})_(\d{6})_verified_return_leaf',path.name)
        if (not path.is_relative_to(hs.LOGS.resolve()) or path.parent.parent.name!=s.state.day
                or not path.is_file() or not match):return None
        stamp=datetime.strptime(s.state.day+''.join(match.groups()),'%Y-%m-%d%H%M%S%f')
        times.append(stamp.replace(tzinfo=timezone(timedelta(hours=8))).timestamp())
        with Image.open(path) as source:frame=source.convert('RGB')
        if (not s.verify_identity(account,frame)[0] or not s.main_ready(frame)
                or not s.template('leaf_village_name_current.bmp',LEAF_NAME_ROI,frame,threshold=.92)[0]):return None
    if max(times)>=start:return None
    receipts=pending_activity_records(s,account)
    if not receipts:return None
    proofs=[]
    for receipt in receipts:
        record=receipt['record'];active=record['active']
        if record.get('execution_day')!=s.state.day or escort_deadline(active)>=min(times):return None
        directory=Path(active.get('state_directory','')).resolve();owner=active.get('source_account')
        if not owner or not directory.is_relative_to(s.identity_directory.resolve()):return None
        source=directory/s.state.day/(hs.slug(owner)+'.json');ledger=read_checkpoint(source)
        if (ledger.get('account')!=owner or ledger.get('date')!=s.state.day
                or ledger.get('actions',{}).get(active.get('action'),{}).get('status') not in ('pending','done')):return None
        proofs.append(dict(receipt=str(receipt['path']),receipt_sha256=hashlib.sha256(receipt['path'].read_bytes()).hexdigest(),
                           source=str(source),source_sha256=hashlib.sha256(source.read_bytes()).hexdigest()))
    return dict(origin_samples=samples,treasure_started_at=start,treasure_stopped_at=stopped,
                original_escort_receipts=proofs,escort_outcome='unknown',counted_as_completed=False)


def _return_to_leaf(scheduler, account, allow_pending_navigation, timeout, audit, *, stopped_treasure_return=False):
    import hyol_scheduler as hs
    s = scheduler
    # A pending irreversible action must be reconciled by its own task runner.
    account_state = s.state.accounts.get(account.name, {})
    from hyol_escort import pending_activity,reconcile_previous_for_navigation
    if pending_activity(s,account):
        # Already being in Leaf needs observation only. Keep every escort
        # credential untouched; never navigate or call its settlement runner.
        samples = []
        for sample in range(3):
            frame = s.capture()
            if (not s.main_ready(frame) or preparation_blocked(s, frame)
                    or not s.verify_identity(account, frame)[0]
                    or not s.template('leaf_village_name_current.bmp', LEAF_NAME_ROI, frame, threshold=.92)[0]):
                break
            samples.append(str(s.save_raw_evidence(frame, account, 'return_leaf_observe_escort_pending', 'verified')))
            if sample < 2:
                s.wait_interruptible(.25)
        if len(samples) == 3:
            record = _record(s, account, 'return_leaf')
            record.update(status='done', phase='leaf_verified', evidence=samples[-1], samples=samples,
                          escort_pending_preserved=True, navigation_sent=False)
            s.state.save_account(account.name)
            s.event('return_leaf_verified', account, evidence=samples[-1],
                    detail='仅观察到实名账号已在木叶；押镖未决原样保留，未发送导航或补记交付')
            return True
        origin=stopped_treasure_origin(s,account) if stopped_treasure_return else None
        if origin:
            audit['stopped_treasure_origin']=origin
            s.event('stopped_treasure_return_authorized',account,proof=origin,
                    detail='本次挖宝出村前已在木叶，旧镖此前已超时；仅回村，旧押镖凭据与未知结果不变')
        elif not reconcile_previous_for_navigation(s,account):
            raise hs.PendingError('押镖当日原场仍未确认完成，保留当前路线，不强行回村')
    from hyol_manual_continuation import quarantined_external_action
    unresolved = [key for key, value in account_state.get('actions', {}).items()
                  if isinstance(value, dict) and value.get('status') == 'pending'
                  and not quarantined_external_action(value,account.name)]
    if any(key.startswith('escort_') and key.endswith('_start') for key in unresolved):
        raise hs.PendingError('押镖原场仍未确认完成，保留当前路线，不强行回村')
    only_mail_pending = all(key == 'mail_claim_all' or key.startswith('mail_claim_all_network_')
                            for key in unresolved)
    if unresolved and not only_mail_pending and not allow_pending_navigation:
        # Already being home does not settle an unrelated battle. Observe
        # identity and Leaf across three frames, without sending navigation.
        move_cursor_clear(s)
        frame = s.capture()
        leaf_samples = []
        if close_black_market(s, account, frame=frame):
            frame = s.capture()
        for sample in range(3):
            audit['frame'] = frame
            if (not s.main_ready(frame)
                    or not s.template('leaf_village_name_current.bmp', LEAF_NAME_ROI, frame, threshold=.92)[0]
                    or s._combat_active_visible('arena', frame)
                    or s.template('arena_loading_anchor.bmp', hs.ARENA_LOADING_ROI, frame)[0]):
                break
            if not s.verify_identity(account, frame)[0]:
                raise hs.PendingError('回村账号身份未确认，保留原导航和动作')
            leaf_samples.append(frame.copy())
            if sample == 2:
                evidence = s.save_raw_evidence(frame, account, 'return_leaf', 'verified')
                record = _record(s, account, 'return_leaf')
                samples=[str(s.save_raw_evidence(f,account,'return_leaf_sample_'+str(i),'verified'))
                         for i,f in enumerate(leaf_samples,1)]
                record.update(status='done', phase='leaf_verified', evidence=str(evidence),samples=samples,
                              unrelated_pending_preserved=list(unresolved))
                s.state.save_account(account.name)
                s.event('return_leaf_verified', account, evidence=str(evidence),
                        detail='已连续确认实名木叶主界面；其他未决操作原样保留，未发送导航')
                return True
            s.wait_interruptible(.35)
            frame = s.capture()
        raise hs.PendingError('存在未核实操作，请先由原任务核对结果，不能强制回村：' + ', '.join(unresolved))
    leave_hot_spring(s, account)
    deadline = time.monotonic() + timeout
    close_controls = (
        ('anniversary_close_20261105.bmp', (1325,280,1370,325), 'anniversary_title_20261105.bmp'),
        ('anniversary_close_20261105.bmp', (1325,280,1370,325), 'anniversary_catalogue_title_20261105.bmp'),
        ('escort_close.bmp', (1,69,1919,1031), 'escort_page_title.bmp'),
        ('friends_panel_close.bmp', (1870,260,1920,350), 'friends_panel_close.bmp'),
        ('ninja_close_current.bmp', (1570,100,1640,175), 'ninja_page_current.bmp'),
        ('mail_warehouse_close.bmp', (1174,259,1275,359), 'mail_warehouse_anchor.bmp'),
        ('mail_close.bmp', (1301,249,1401,349), 'mail_ready_anchor.bmp'),
        ('daruma_weekend_close.bmp', (1290,260,1420,370), 'daruma_weekend_page.bmp'),
        ('welfare_close_current.bmp', (1320,250,1450,350), 'welfare_title_current.bmp'),
        ('managed_settings_close_current.bmp', (1320,230,1450,330), 'managed_settings_title_current.bmp'),
        ('daily_practice_close.bmp', (1370,200,1460,280), 'daily_practice_anchor.bmp'),
        ('team_instance_close.bmp', (1370,240,1460,330), 'team_instance_close.bmp'),
        ('survival_close.bmp', (1585,77,1685,177), 'survival_anchor.bmp'),
        ('wanted_close_current.bmp', (1310,290,1400,390), 'wanted_team_anchor.bmp'),
        ('team_activity_close.bmp', (1300,280,1420,390), 'strong_reward_label_core_current.bmp'),
    )
    clicked = set()
    record = _record(s, account, 'return_leaf')
    if record.get('status') == 'done':
        history = record.get('history', [])[-9:]
        history.append({key:value for key,value in record.items() if key != 'history'})
        record.clear()
        record['history'] = history
    record.setdefault('phase', 'observing')
    if audit.get('stopped_treasure_origin'):record['stopped_treasure_origin']=audit['stopped_treasure_origin']
    record['status'] = 'pending'
    audit['record'] = record
    returned = record.get('phase') == 'return_requested'
    retry_allowed = (returned and '未重复点击回村按钮' in str(record.get('last_error',''))
                     and not record.get('return_retry_used'))
    observation_seconds=timeout
    if returned and '未重复点击回村按钮' in str(record.get('last_error','')):
        # An unchanged, already-timed-out request needs a bounded recheck,
        # not another full minute per account on every preparation.
        observation_seconds=min(timeout,5)
        deadline=time.monotonic()+observation_seconds
    stable = 0
    leaf_samples = []
    move_cursor_clear(s)
    while time.monotonic() < deadline:
        s.check_stop()
        frame = s.capture()
        audit['frame'] = frame
        if s.template('interference_recharge_title_20261022.bmp',(900,235,1250,325),frame,threshold=.98)[0]:
            from hyol_identity_overlays import dismiss_identity_overlays
            dismiss_identity_overlays(s,account,frame,only_kind='recharge_activity')
            move_cursor_clear(s)
            continue
        if close_black_market(s, account, frame=frame):
            move_cursor_clear(s)
            continue
        main_ready = s.main_ready(frame)
        navigable_world = main_ready or organization_world_visible(s,frame)
        if navigable_world and not s.verify_identity(account, frame)[0]:
            raise hs.PendingError('回村账号身份未确认，保留原导航和动作')
        if returned and content_loading_visible(s,frame):
            # A positively recognized resource/map load after our return is
            # an expected transition. Observe it without any further input.
            s.wait_interruptible(.35)
            continue
        if preparation_blocked(s, frame):
            raise hs.PendingError('战斗或加载仍在进行，安全回村不会强行退出')
        # The organization title remains visible behind the escort panel.
        # Close that foreground panel before treating the map as navigable.
        panel=preparation_panel(s,frame)
        if panel is not None and panel[0]=='escort_page_title.bmp' and panel[2] not in clicked:
            anchor,_anchor_roi,close,roi=panel
            if s.click_template(account,close,roi,17,17,'safe_close_'+close,[anchor,close],
                    threshold=.98,anchor_threshold=.98,
                    frame_predicate=lambda fresh:not preparation_blocked(s,fresh)):
                clicked.add(close);move_cursor_clear(s);continue
        if navigable_world:
            if main_ready and s.template('leaf_village_name_current.bmp', LEAF_NAME_ROI, frame, threshold=.92)[0]:
                stable += 1
                leaf_samples.append(frame.copy())
                if stable >= 3:
                    previous = s.state.accounts[account.name].get('navigation', {}).get('hot_spring_exit', {})
                    if previous.get('status') == 'pending':
                        leave_hot_spring(s, account)
                    evidence = s.save_raw_evidence(frame, account, 'return_leaf', 'verified')
                    samples = [str(s.save_raw_evidence(f,account,'return_leaf_sample_'+str(i),'verified'))
                               for i,f in enumerate(leaf_samples[-3:],1)]
                    record.update(status='done', phase='leaf_verified', evidence=str(evidence), samples=samples,
                                  unrelated_pending_preserved=list(unresolved))
                    s.state.save_account(account.name)
                    s.event('return_leaf_verified', account, evidence=str(evidence), detail='已连续确认木叶村主界面')
                    return True
            else:
                stable = 0
                leaf_samples.clear()
                if retry_allowed:
                    retry_allowed=False
                    if retry_leaf_return_once(s,account,record,frame):
                        observation_seconds=timeout
                        deadline=time.monotonic()+timeout
                        continue
                if not returned:
                    for name, dx, dy, threshold in (
                            ('leaf_return_core_current.bmp', 10, 10, .98),
                            ('leaf_return_core_forest2_current.bmp', 10, 10, .98),
                            ('leaf_return_core_desert_current.bmp', 9, 10, .98),
                            ('leaf_return_core_desert2_current.bmp', 9, 10, .98),
                            ('leaf_return_current.bmp', 13, 17, .92),
                            ('leaf_return_hover.bmp', 13, 17, .92)):
                        if s.template(name, LEAF_RETURN_ROI, frame, threshold=threshold)[0]:
                            _step(s, account, record, 'return_requested', frame, '已核实右上角返回木叶村按钮')
                            record['status'] = 'pending'
                            s.state.save_account(account.name)
                            if s.click_template(account, name, LEAF_RETURN_ROI, dx,dy, 'safe_return_leaf', [name],
                                                threshold=threshold, anchor_threshold=threshold):
                                returned = True
                                move_cursor_clear(s)
                            else:
                                raise hs.PendingError('回村按钮点击前画面变化，保存阶段，不重复点击')
                            break
        else:
            stable = 0
            leaf_samples.clear()
            panel = preparation_panel(s, frame)
            if panel and panel[2] not in clicked:
                anchor, _anchor_roi, close, roi = panel
                if s.click_template(account, close, roi, 17,17, 'safe_close_'+close,
                                    [anchor, close], threshold=.98, anchor_threshold=.98,
                                    frame_predicate=lambda fresh: not preparation_blocked(s,fresh)):
                    clicked.add(close)
                    move_cursor_clear(s)
                    continue
            if (s.template('home_leave_prompt_text.bmp',SPRING_PROMPT_ROI,frame,threshold=.98)[0]
                    and s.template('hot_spring_leave_confirm.bmp',SPRING_PROMPT_ROI,frame,threshold=.98)[0]):
                if record.get('phase') in ('home_exit_requested','home_return_requested'):
                    _step(s,account,record,'home_confirm_requested',frame,'已确认家园退出提示正文和确定按钮')
                    if not s.click_template(account,'hot_spring_leave_confirm.bmp',SPRING_PROMPT_ROI,
                            45,22,'safe_leave_home_confirm',['home_leave_prompt_text.bmp'],
                            threshold=.98,anchor_threshold=.98):
                        raise hs.PendingError('家园退出提示在点击前变化，保留请求')
                    move_cursor_clear(s)
                s.wait_interruptible(.35)
                continue
            if home_visible(s,frame):
                if not s.verify_identity(account, frame)[0]:
                    raise hs.PendingError('家园离开前账号身份未确认，零输入')
                if record.get('phase') not in ('home_exit_requested','home_return_requested','home_confirm_requested'):
                    request_home_return(s,account,record,frame,label='safe_home_return_leaf')
                s.wait_interruptible(.35)
                continue
            for name, roi, anchor in close_controls:
                if name in clicked:
                    continue
                if s.template(anchor, hs.VIEW, frame)[0] and s.template(name, roi, frame)[0]:
                    if s.click_template(account, name, roi, 17,17, 'safe_close_'+name, [anchor]):
                        clicked.add(name)
                        move_cursor_clear(s)
                    break
        s.wait_interruptible(.35)
    raise hs.PendingError(f'{observation_seconds:g}秒内未确认木叶村主界面，未重复点击回村按钮')


def manual_return(root):
    import hyol_scheduler as hs
    config = hs.read_config(root/'DAILY_Client.ini')
    config['AllowMarketPurchase'] = '0'
    s = hs.Scheduler(config, 'manual_return', False, False)
    lease = hs.ForegroundRuntimeLease()
    try:
        if not lease.acquire():
            raise hs.PendingError('已有脚本占用游戏窗口，请停止或等待结束后再回村')
        s.start_f12_monitor()
        s.bind_window(wait=True, timeout_seconds=15)
        account = s.bind_generic_current_window(require_game_context=False)
        return_to_leaf(s, account, allow_pending_navigation=True)
        return '已确认返回木叶村主界面'
    except KeyboardInterrupt:
        s.event('scheduler_stopped', reason='manual return F12')
        raise RuntimeError('回村已停止，未继续执行游戏任务') from None
    except Exception as exc:
        s.event('stage_pending', detail=f'{type(exc).__name__}: {exc}')
        report = getattr(s.diagnostics, 'last_path', None)
        raise RuntimeError(f'未回村：{exc}\n诊断包：{report or "诊断文件写入失败"}') from exc
    finally:
        s.stop.set()
        lease.release()
