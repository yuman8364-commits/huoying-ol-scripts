"""Explicit preparation: selected tabs, reversible navigation, separate receipts."""
from __future__ import annotations

from pathlib import Path


def validate_scope(value=None):
    if value is None:
        return dict(scope='current', slots=[])
    if not isinstance(value, dict) or set(value) != {'scope','slots'}:
        raise ValueError('检查范围格式无效')
    slots = value['slots']
    if (value['scope'] not in ('current','tabs') or not isinstance(slots,(list,tuple))
            or any(type(x) is not int or not 1 <= x <= 6 for x in slots)
            or len(slots) != len(set(slots))
            or (value['scope'] == 'current' and slots)
            or (value['scope'] == 'tabs' and not slots)):
        raise ValueError('检查账号范围须为1～6个互不重复标签')
    return dict(scope=value['scope'], slots=list(slots))


def scope_for_request(request):
    mode = request.mode
    if mode == 'team_then_multi' and getattr(request,'preformed_trio',()):
        slots=list(request.preformed_trio)
    elif mode == 'team_then_multi':
        slots = list(range(1,7))
    elif mode in ('multi','anniversary'):
        slots = list(range(1,request.account_count+1))
    elif mode == 'team':
        slots = list(dict.fromkeys((request.leader, *(() if request.friend_shortcut else request.members))))
    else:
        slots = []
    return validate_scope(dict(scope='tabs' if slots else 'current', slots=slots))


def expose_identity(s, account, frame):
    """Close known shells through the persistent, guarded overlay journal."""
    import hyol_scheduler as hs
    from hyol_accounts import _final_result, dismiss_current_final_result
    from hyol_navigation import preparation_blocked
    from hyol_identity_overlays import dismiss_identity_overlays, inspect
    s.check_stop()
    # A previous Bond exit can leave a finished points popup and then its
    # idle matching lobby. Close only these exact shells, without battle credit.
    for kind in ('bond_points_result','bond_idle_lobby'):
        observed=inspect(s,frame)
        if observed and observed[0]==kind:
            frame=dismiss_identity_overlays(s,account,frame,only_kind=kind)
    if _final_result(s,frame) is not None:
        frame = dismiss_current_final_result(s,account,frame,allow_reward_confirmation=False)
    if preparation_blocked(s,frame):
        raise hs.PendingError('战斗、加载或未决结算仍在进行，保留原页面与凭据')
    return dismiss_identity_overlays(s, account, frame,
                                    frame_predicate=lambda current: not preparation_blocked(s, current))


def bind_observed(s,centers,observations):
    """Bind only this Start's observed roles, retaining early toggle receipts."""
    import hyol_scheduler as hs
    from hyol_accounts import register_observations
    mapping,refs=register_observations(s,observations)
    accounts=[hs.Account(i,mapping[i],centers[i-1]) for i in sorted(mapping)]
    s.accounts[:]=accounts
    s.by_name={a.name:a for a in accounts}
    s.state=hs.StateStore(list(s.by_name),directory=s.shared_state_directory,
                        restore_history=s.run_context is None)
    s._dynamic_identity_masks={i:v[-1] for i,v in observations.items() if i in mapping}
    s._dynamic_identity_names=mapping
    s._dynamic_identity_materials=refs
    s.joint_identity_bound=set(s.by_name)
    s.available_account_names=set(s.by_name)
    s.state.global_state['tab_map']={a.name:a.tab_x for a in accounts}
    return mapping,refs,accounts


def hide_first_world(s,account,frame,row):
    import hyol_scheduler as hs
    from hyol_navigation import organization_world_visible,preparation_blocked
    from hyol_player_display import ensure_hidden,observe
    if s.config.get('HideOtherPlayers','1')!='1':return
    if not (s.main_ready(frame) or organization_world_visible(s,frame)):return
    if preparation_blocked(s,frame):
        raise hs.PendingError('首次关闭其他忍者前仍有加载、战斗或结果遮挡')
    current=observe(s,frame)
    if row.get('player_display_first') and current and current[0]=='closed':return
    result=ensure_hidden(s,account,require_leaf=False,timeout=10)
    row['player_display_first']=result
    row['player_display']=dict(s.state.accounts[account.name]['player_display'])
    if result.get('skipped'):
        s.event('preparation_player_display_skipped',account,evidence=result['evidence'],
                clicks=0,detail='其他忍者显示开关未确认，保持原显示状态继续准备')
        return
    s.event('preparation_player_display_first',account,evidence=result['evidence'],
            samples=result['samples'],clicks=result['clicks'],
            detail='首次可用主界面已先关闭其他忍者显示；此后才处理其他标签、回村和任务')


def prepare_home_startup(s,account,frame,row):
    """A bound home/spring role must stop managed movement before leaving."""
    import hyol_scheduler as hs
    from hyol_managed import exit_visible_status,active_observation
    from hyol_navigation import hot_spring_visible,home_visible,leave_hot_spring,return_to_leaf
    if not (hot_spring_visible(s,frame) or home_visible(s,frame)):return frame
    if active_observation(s,frame)['active']:
        if not exit_visible_status(s,account,frame):
            raise hs.PendingError('家园/温泉仍在托管，但退出托管控件未确认，未先关闭温泉')
        row['managed_stopped_before_home_exit']=True
        frame=s.capture()
    if hot_spring_visible(s,frame):
        if leave_hot_spring(s,account) is not True:
            raise hs.PendingError('启动时温泉退出尚未核实')
    elif home_visible(s,frame):
        if return_to_leaf(s,account,allow_pending_navigation=True) is not True:
            raise hs.PendingError('启动时家园离开尚未核实')
    frame=s.capture()
    row['home_startup_exit']=dict(evidence=str(s.save_raw_evidence(
        frame,account,'prepare_home_startup_exit','verified')),
        managed_stopped=bool(row.get('managed_stopped_before_home_exit')))
    s.event('preparation_home_startup_exited',account,**row['home_startup_exit'])
    return frame


def observe_home_identity(s,account,frame):
    """One bounded re-observation handles the spring's tab-entry fade."""
    import hyol_scheduler as hs
    from hyol_accounts import collect_current_identity
    from hyol_navigation import hot_spring_visible,home_visible,preparation_blocked
    try:return collect_current_identity(s,account,frame)
    except hs.PendingError as exc:
        if '身份被遮挡或变化' not in str(exc):raise
        frame=s.capture()
        if (preparation_blocked(s,frame) or s.offline_dialog_visible(frame)
                or not (hot_spring_visible(s,frame) or home_visible(s,frame))):raise
        s.event('preparation_home_identity_reobserving',account,
            detail='温泉切入时姓名未稳定，保持零内容输入，重新观察一次三帧身份')
        s.wait_interruptible(.3)
        return collect_current_identity(s,account,s.capture())


def prepare_accounts(s, scope):
    import hyol_scheduler as hs
    from hyol_accounts import collect_current_identity, _capture_observed_account
    from hyol_multi_solo import tab_close_positions, active_tab_centers
    from hyol_navigation import return_to_leaf, preparation_blocked
    scope = validate_scope(scope)
    frame = _capture_observed_account(s)
    markers = tab_close_positions(frame)
    s.wait_interruptible(.2)
    if markers != tab_close_positions(_capture_observed_account(s)):
        raise hs.PendingError('检查期间标签布局变化，未操作账号内容')
    centers = active_tab_centers(markers)
    s.config['TabXs'] = ','.join(map(str,centers))
    if scope['scope'] == 'current':
        active_x = s._generic_active_tab_x(frame)
        slots = [centers.index(active_x)+1]
    else:
        slots = scope['slots']
    rows, observations, frames = {}, {}, {}
    s._preparation_rows = rows
    def prepare_slot(slot):
        s.check_stop()
        row = rows.setdefault(slot,dict(tab_index=slot,status='pending',leaf_verified=False,navigation_clicks=0))
        row['preparation_attempts'] = row.get('preparation_attempts',0)+1
        row.update(status='pending',leaf_verified=False)
        row.pop('reason',None)
        if slot > len(centers):
            row['reason'] = '所选标签未打开'
            return
        a = hs.Account(slot,f'当前窗口标签{slot}',centers[slot-1])
        before = s.content_click_count
        try:
            frame = _capture_observed_account(s)
            if s._generic_active_tab_x(frame) != a.tab_x:
                s.chrome_click(a.tab_x,10,f'observe_account:{slot}',a)
                s.wait_interruptible(.35)
                frame = _capture_observed_account(s)
            if getattr(frame,'_hyol_chrome_only',False) or not s.healthy(frame):
                from hyol_browser_recovery import recover_white_browser
                if recover_white_browser(s,a):
                    frame = _capture_observed_account(s)
            if getattr(frame,'_hyol_chrome_only',False):
                raise hs.PendingError('当前标签没有可确认的游戏内容')
            if s.offline_dialog_visible(frame):
                raise hs.AccountOffline(a,'当前标签已确认掉线')
            row['before_evidence'] = str(s.save_raw_evidence(frame,a,'prepare_before','pending'))
            s.by_name[a.name] = a
            from hyol_arena_3v3 import TASK, page_visible
            request=getattr(getattr(s,'run_context',None),'pipeline_request',None)
            if request and TASK in request.team_tasks and page_visible(frame,s.templates):
                observations[slot],frames[slot]=collect_current_identity(s,a,frame)
                mapping,refs,accounts=bind_observed(s,centers,observations)
                if slot not in mapping:raise hs.PendingError('3v3活动内身份尚未唯一核实')
                proof=str(s.save_raw_evidence(frame,a,'prepare_arena3v3_preserved','verified'))
                row.update(status='ready',activity_ready=TASK,account=mapping[slot],evidence=proof)
                s.event('preparation_activity_preserved',tab_index=slot,
                        detail='已在3v3准备页，保留现有队伍与活动页面')
                return
            from hyol_navigation import hot_spring_visible,home_visible
            if hot_spring_visible(s,frame) or home_visible(s,frame):
                # Spring leaves the role strip visible. Bind its three fresh
                # observations before the exit; never stop an anonymous role.
                observations[slot],frames[slot]=observe_home_identity(s,a,frame)
                _mapping,_refs,bound=bind_observed(s,centers,observations)
                if slot not in _mapping:
                    raise hs.PendingError(getattr(s,'_identity_unavailable_reasons',{}).get(slot,'当前账号身份尚未唯一确认'))
                a=next(item for item in bound if item.index==slot)
                row['account']=a.name;s.active=a;s._capture_binding_probe=False
                frame=prepare_home_startup(s,a,s.capture(),row)
            frame = expose_identity(s,a,frame)
            observations[slot], frames[slot] = collect_current_identity(s,a,frame)
            # Normalize this role immediately. The user must not wait for a
            # full discovery pass before the first return-to-village request.
            mapping,refs,accounts=bind_observed(s,centers,observations)
            if slot not in mapping:
                raise hs.PendingError(getattr(s,'_identity_unavailable_reasons',{}).get(slot,'当前账号身份尚未唯一确认'))
            observed=next(bound for bound in accounts if bound.index==slot)
            row['account']=observed.name
            s.active=observed;s._capture_binding_probe=False
            relogin_proof=getattr(s,'_recovered_offline_proofs',{}).get(observed.name)
            if relogin_proof:
                from hyol_navigation import restart_return_after_offline
                restart_return_after_offline(s,observed,relogin_proof)
            s.event('preparation_return_started',observed,detail='先确认返回木叶村，再进行其他检查')
            if return_to_leaf(s,observed,allow_pending_navigation=True) is not True:
                raise hs.PendingError('回村尚未核实')
            proof=s.state.accounts[observed.name]['navigation']['return_leaf']
            row.update(status='ready',leaf_verified=True,evidence=proof['evidence'],
                       samples=list(proof.get('samples',())))
            fresh=s.capture()
            if s.main_ready(fresh) and not preparation_blocked(s,fresh):
                s.clear_transient_notifications(observed,fresh)
            hide_first_world(s,observed,s.capture(),row)
            s.event('preparation_account_result',observed,
                    **{k:v for k,v in row.items() if k not in ('account','tab_index')})
        except hs.AccountOffline as exc:
            row.update(status='offline',reason=str(exc))
        except hs.SafetyError:
            raise  # Lost foreground/changed geometry ends all preparation.
        except hs.PendingError as exc:
            row.update(status='pending',reason=str(exc))
        finally:
            row['navigation_clicks'] += s.content_click_count-before
            s.generic_current_tab_x = None
            s.current_account_only = None
            s.active = None
            s._capture_binding_probe = True
    for slot in slots:
        prepare_slot(slot)
    # A role hidden by loading/a finished result may become recoverable while
    # the other selected roles return. Reobserve every pending open tab once;
    # successful roles are not navigated again, and original UI receipts guard
    # against repeating any close whose outcome is still unknown.
    retry_slots=[slot for slot in slots if slot<=len(centers) and rows[slot]['status']=='pending']
    for slot in retry_slots:
        s.check_stop()
        s.event('preparation_account_retry',tab_index=slot,attempt=2,
                previous_reason=rows[slot].get('reason',''),
                detail='首轮检查未恢复，重新观察当前标签并沿原凭据尝试回到主界面')
        s.wait_interruptible(.5)
        prepare_slot(slot)
    if observations:
        mapping,refs,accounts=bind_observed(s,centers,observations)
        rejected = getattr(s,'_identity_unavailable_reasons',{})
        for slot, reason in (rejected.items() if isinstance(rejected,dict) else ()):
            rows[slot].update(status='pending',leaf_verified=False,reason=reason)
            rows[slot].pop('account',None)
        s._capture_binding_probe = False
        last_ready = None
        # A follower can remain on the old map until its captain returns.
        # Use the three-frame portrait observations; returned rows keep the
        # user's tab order and no task selection/order is changed here.
        navigation_order=sorted(accounts,key=lambda a:(
            not getattr(s,'_observed_captain_tabs',{}).get(a.tab_x,False),a.index))
        for a in navigation_order:
            s.check_stop()
            row = rows[a.index]
            row['account'] = a.name
            if row.get('leaf_verified') or row.get('activity_ready'):
                if row.get('status')=='ready':last_ready=a
                continue
            before = s.content_click_count
            try:
                if scope['scope'] == 'current':
                    # Discovery already observed this tab three times. Verify a
                    # fresh frame before reusing it instead of clicking the
                    # already selected browser tab and waiting for a reload.
                    fresh = s.capture()
                    if (s._generic_active_tab_x(fresh) != a.tab_x
                            or s.offline_dialog_visible(fresh)
                            or not s.healthy(fresh)
                            or not s.verify_identity(a, fresh)[0]):
                        raise hs.PendingError('当前标签或身份在准备时发生变化，未切换标签')
                    s.active = a
                    s.event('account_bound', a, tab_click_suppressed=True,
                            detail='当前账号已核实，无需重复点击当前标签')
                else:
                    s.switch_account(a)
                fresh = s.capture()
                relogin_proof=getattr(s,'_recovered_offline_proofs',{}).get(a.name)
                if relogin_proof:
                    from hyol_navigation import restart_return_after_offline
                    restart_return_after_offline(s,a,relogin_proof)
                if (s.main_ready(fresh) and not preparation_blocked(s, fresh)):
                    s.clear_transient_notifications(a, fresh)
                if return_to_leaf(s,a,allow_pending_navigation=True) is not True:
                    raise hs.PendingError('回村尚未核实')
                proof = s.state.accounts[a.name]['navigation']['return_leaf']
                row.update(status='ready',account=a.name,leaf_verified=True,evidence=proof['evidence'],
                           samples=list(proof.get('samples',())))
                hide_first_world(s,a,s.capture(),row)
                if s.config.get('HideOtherPlayers', '0') == '1':
                    row['player_display'] = dict(s.state.accounts[a.name]['player_display'])
                last_ready = a
            except hs.AccountOffline as exc:
                row.update(status='offline',reason=str(exc))
            except hs.SafetyError:
                raise
            except hs.PendingError as exc:
                row['reason'] = str(exc)
                proof=s.state.accounts.get(a.name,{}).get('navigation',{}).get('return_leaf',{})
                if proof.get('pending_evidence'):row['evidence']=proof['pending_evidence']
            finally:
                row['navigation_clicks'] += s.content_click_count-before
                s.event('preparation_account_result',a,**{k:v for k,v in row.items() if k not in ('account','tab_index')})
        if last_ready is not None and s.active != last_ready:
            fresh=s.capture()
            if (s._generic_active_tab_x(fresh)==last_ready.tab_x
                    and s.verify_identity(last_ready,fresh)[0]):
                s.active=last_ready
            else:s.switch_account(last_ready)
        if s.run_context is not None:
            ready_mapping={i:name for i,name in mapping.items() if
                           (rows[i]['leaf_verified'] or rows[i].get('activity_ready')) and rows[i]['status']=='ready'}
            s.run_context.remember(centers,ready_mapping,{i:observations[i] for i in ready_mapping},frames,refs,
                                   {i:r.get('reason','准备未完成') for i,r in rows.items() if r['status']!='ready'})
            s.run_context.binding['prepared_leaf_verified'] = bool(ready_mapping)
            s.run_context.binding['prepared_accounts'] = {
                name:dict(evidence=rows[i].get('evidence',''),samples=rows[i].get('samples',[]))
                for i,name in ready_mapping.items() if rows[i]['leaf_verified']}
            s.run_context.binding['prepared_activity_accounts']={name:rows[i].get('activity_ready')
                for i,name in ready_mapping.items() if rows[i].get('activity_ready')}
    return list(rows.values())


def handoff_start_window(s):
    """One focus handoff for an explicit Start/check, before any game input.

    AutoForeground stays disabled for preparation: losing focus after this
    initial handoff must still stop rather than pull another app forward.
    """
    import hyol_scheduler as hs
    s.check_input_pause()
    windows = hs.Win32.game_windows()
    if len(windows) != 1:
        raise hs.SafetyError(f'开始前须唯一确认游戏窗口，当前找到{len(windows)}个')
    s.hwnd = hwnd = windows[0]
    def native_blank():
        try:
            if hs.Win32.rect(hwnd) != hs.EXPECTED_WINDOW or hs.Win32.user32.IsIconic(hwnd):
                return False
            hs.Win32.game_content_rect(hwnd)
        except hs.SafetyError as exc:
            return str(exc) == 'game content surface is ambiguous: 0 visible regions'
        return False
    geometry_valid = s.validate_geometry(activate=False)
    blank = not geometry_valid and native_blank()
    if not blank and not geometry_valid:
        reason, _ = s._window_binding_failure(windows)
        raise hs.SafetyError(reason)
    pid = hs.Win32.pid(hwnd)
    previous = hs.Win32.foreground()
    if previous != hwnd:
        s.event('preparation_window_handoff', previous_foreground_hwnd=previous,
                detail='正在将本次开始操作交接给已核实的游戏窗口。')
        # Allow the native Start callback to finish its button repaint first.
        s.wait_interruptible(.2)
        s.check_input_pause()
        if hs.Win32.game_windows() != [hwnd] or hs.Win32.pid(hwnd) != pid:
            raise hs.SafetyError('开始交接前游戏窗口会话变化，未切换前台')
        hs.Win32.activate(hwnd)
        s.wait_interruptible(.4)
    for sample in range(3):
        s.check_input_pause()
        if (hs.Win32.game_windows() != [hwnd] or hs.Win32.pid(hwnd) != pid
                or hs.Win32.foreground() != hwnd
                or not (s.validate_geometry(activate=False) or blank and native_blank())):
            raise hs.SafetyError('开始交接未稳定取得游戏前台，请恢复游戏窗口后重试')
        if sample < 2:
            s.wait_interruptible(.2)
    s.event('preparation_window_handoff_verified', previous_foreground_hwnd=previous,
            samples=3, content_clicks=0)
    if blank:
        from hyol_browser_recovery import recover_white_browser
        if not recover_white_browser(s):
            # Keep the remaining account tabs accessible through the existing
            # read-only browser discovery path, without granting game input.
            return False
    return True


def run(root, scope=None, *, stop_event=None, event_sink=None, on_scheduler=None):
    import hyol_scheduler as hs
    from hyol_diagnostics import clear_manual_pause
    scope = validate_scope(scope)
    config = hs.read_config(Path(root)/'DAILY_Client.ini')
    config.update(AccountMode='dynamic',AccountDiscovery='automatic',AllowMarketPurchase='0',AutoForeground='0')
    if scope['scope'] == 'current':
        config['CurrentWindowOnly'] = '1'
    config['HideOtherPlayers'] = '1'
    s = hs.Scheduler(config,'prepare_leaf',False,False,event_sink=event_sink)
    if getattr(s.run_context, 'preserve_activity_notices', False):
        s.config['PreserveActivityNotices'] = '1'
    if stop_event is not None:
        s.stop = stop_event
    if on_scheduler is not None:
        on_scheduler(s)
    lease = hs.ForegroundRuntimeLease()
    result = dict(status='pending',accounts=[],main_ready=False,healthy=False,content_clicks=0,
                  navigation_clicks=0,preparation=True,scope=scope,run_id=s.diagnostics.identifier)
    owned = False
    try:
        s.check_stop()
        owned = lease.acquire()
        if not owned:
            raise hs.PendingError('已有脚本占用游戏窗口，准备检查不争用控制权')
        # Only an explicit new check/start reaches this operation.
        clear_manual_pause(Path(root),'用户主动检查并回村')
        s.check_stop()
        s.start_f12_monitor()
        content_ready = handoff_start_window(s)
        if content_ready is False:
            previous = s.no_content_clicks
            s.no_content_clicks = True
            s._capture_binding_probe = True
            try:
                s.bind_window(wait=True,timeout_seconds=10)
            finally:
                s.no_content_clicks = previous
        else:
            s.bind_window(wait=True,timeout_seconds=10)
        s._capture_binding_probe = True
        rows = prepare_accounts(s,scope)
        ready = bool(rows) and all(r['status']=='ready' and (r['leaf_verified'] or r.get('activity_ready')) for r in rows)
        result.update(status='ready' if ready else 'pending',accounts=rows,main_ready=all(r['leaf_verified'] for r in rows),
                      healthy=any(r['status']=='ready' for r in rows))
    except KeyboardInterrupt:
        result.update(status='stopped',reason='检查回村已停止，未处理剩余账号')
    except Exception as exc:
        result.update(status='blocked',reason=f'{type(exc).__name__}: {exc}')
    finally:
        if not result['accounts']:
            result['accounts'] = list(getattr(s,'_preparation_rows',{}).values())
        result.update(content_clicks=s.content_click_count,navigation_clicks=s.content_click_count)
        hs.atomic_json(hs.LOGS/f'preparation_{s.diagnostics.identifier}.json',result)
        # A native check passes its caller's cancellation Event as s.stop.
        # Successful cleanup must not turn that shared signal into "用户停止".
        # release_resources disposes the local F12 monitor independently.
        if not s.run_context and stop_event is None:
            s.stop.set()
        lease.release()
        s.release_resources()
    return result
