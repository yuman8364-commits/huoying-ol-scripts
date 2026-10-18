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


def _world_visible(s, frame):
    return (not hot_spring_visible(s, frame)
            and not s.template('hot_spring_leave_prompt_text.bmp', SPRING_PROMPT_ROI, frame, threshold=.98)[0]
            and not s.template('hot_spring_leave_confirm.bmp', SPRING_PROMPT_ROI, frame, threshold=.98)[0]
            and not s.template('home_leave.bmp', HOME_LEAVE_ROI, frame)[0]
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
            prompt = s.template('hot_spring_leave_prompt_text.bmp', SPRING_PROMPT_ROI, frame, threshold=.98)[0]
            confirm = s.template('hot_spring_leave_confirm.bmp', SPRING_PROMPT_ROI, frame, threshold=.98)[0]
            panel = hot_spring_visible(s, frame)
            home = s.template('home_leave.bmp', HOME_LEAVE_ROI, frame)[0]
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
            elif prompt and confirm and phase in ('close_requested', 'home_exit_requested'):
                after = 'close_confirm_requested' if phase == 'close_requested' else 'home_confirm_requested'
                _step(s, account, record, after, frame, '退出提示正文和确定按钮均已核实')
                if not s.click_template(account, 'hot_spring_leave_confirm.bmp', SPRING_PROMPT_ROI,
                                        45,22,'navigation_'+after,
                                        ['hot_spring_leave_prompt_text.bmp'], threshold=.98, anchor_threshold=.98):
                    raise hs.PendingError('温泉退出确认点击前提示变化')
                move_cursor_clear(s)
            elif confirm and not prompt:
                raise hs.PendingError('退出提示正文尚未识别，保留当前提示和导航阶段；不猜测确定按钮')
            elif not panel and not prompt and not confirm:
                if home and phase in ('close_requested', 'close_confirm_requested'):
                    _step(s, account, record, 'home_exit_requested', frame, '温泉窗口已消失，当前家园离开按钮已确认')
                    if not s.click_template(account, 'home_leave.bmp', HOME_LEAVE_ROI,15,17,
                                            'navigation_hot_spring_leave_home', ['home_leave.bmp']):
                        raise hs.PendingError('家园离开按钮点击前画面变化')
                    move_cursor_clear(s)
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


def return_to_leaf(scheduler, account, *, allow_pending_navigation=False, timeout=60):
    audit = {}
    try:
        return _return_to_leaf(scheduler, account, allow_pending_navigation, timeout, audit)
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


def _return_to_leaf(scheduler, account, allow_pending_navigation, timeout, audit):
    import hyol_scheduler as hs
    s = scheduler
    # A pending irreversible action must be reconciled by its own task runner.
    account_state = s.state.accounts.get(account.name, {})
    unresolved = [key for key, value in account_state.get('actions', {}).items()
                  if isinstance(value, dict) and value.get('status') == 'pending']
    only_mail_pending = all(key == 'mail_claim_all' or key.startswith('mail_claim_all_network_')
                            for key in unresolved)
    if unresolved and not only_mail_pending and not allow_pending_navigation:
        # Already being home does not settle an unrelated battle. Observe
        # identity and Leaf across three frames, without sending navigation.
        move_cursor_clear(s)
        frame = s.capture()
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
            if sample == 2:
                evidence = s.save_raw_evidence(frame, account, 'return_leaf', 'verified')
                record = _record(s, account, 'return_leaf')
                record.update(status='done', phase='leaf_verified', evidence=str(evidence),
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
    )
    clicked = set()
    record = _record(s, account, 'return_leaf')
    if record.get('status') == 'done':
        history = record.get('history', [])[-9:]
        history.append({key:value for key,value in record.items() if key != 'history'})
        record.clear()
        record['history'] = history
    record.setdefault('phase', 'observing')
    record['status'] = 'pending'
    audit['record'] = record
    returned = record.get('phase') == 'return_requested'
    stable = 0
    move_cursor_clear(s)
    while time.monotonic() < deadline:
        s.check_stop()
        frame = s.capture()
        audit['frame'] = frame
        if close_black_market(s, account, frame=frame):
            move_cursor_clear(s)
            continue
        main_ready = s.main_ready(frame)
        if main_ready and not s.verify_identity(account, frame)[0]:
            raise hs.PendingError('回村账号身份未确认，保留原导航和动作')
        if s._combat_active_visible('arena', frame) or s.template('arena_loading_anchor.bmp', hs.ARENA_LOADING_ROI, frame)[0]:
            raise hs.PendingError('战斗或加载仍在进行，安全回村不会强行退出')
        if main_ready:
            if s.template('leaf_village_name_current.bmp', LEAF_NAME_ROI, frame, threshold=.92)[0]:
                stable += 1
                if stable >= 3:
                    previous = s.state.accounts[account.name].get('navigation', {}).get('hot_spring_exit', {})
                    if previous.get('status') == 'pending':
                        leave_hot_spring(s, account)
                    evidence = s.save_raw_evidence(frame, account, 'return_leaf', 'verified')
                    record.update(status='done', phase='leaf_verified', evidence=str(evidence),
                                  unrelated_pending_preserved=list(unresolved))
                    s.state.save_account(account.name)
                    s.event('return_leaf_verified', account, evidence=str(evidence), detail='已连续确认木叶村主界面')
                    return True
            else:
                stable = 0
                if not returned:
                    for name, dx, dy, threshold in (
                            ('leaf_return_core_current.bmp', 10, 10, .98),
                            ('leaf_return_core_forest2_current.bmp', 10, 10, .98),
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
            if s.template('home_leave.bmp', HOME_LEAVE_ROI, frame)[0]:
                if not s.verify_identity(account, frame)[0]:
                    raise hs.PendingError('家园离开前账号身份未确认，零输入')
                if record.get('phase') != 'home_exit_requested':
                    _step(s, account, record, 'home_exit_requested', frame, '已核实家园离开按钮和账号，先保存退出请求')
                    if not s.click_template(account, 'home_leave.bmp', HOME_LEAVE_ROI, 15,17,
                                            'safe_leave_home', ['home_leave.bmp']):
                        raise hs.PendingError('家园离开按钮点击前变化，保留请求，不重复点击')
                    move_cursor_clear(s)
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
    raise hs.PendingError(f'{timeout:g}秒内未确认木叶村主界面，未重复点击回村按钮')


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
