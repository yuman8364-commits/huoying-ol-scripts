"""Daily sealed-ninja attempt budget. Unknown attempts consume a slot until reconciled."""
DAILY_LIMIT = 2


def unresolved_previous_days(state_dir, account_slug, today):
    import json
    pending = []
    for path in state_dir.glob(f'????-??-??/{account_slug}.json'):
        if path.parent.name >= today:
            continue
        data = json.loads(path.read_text(encoding='utf-8'))
        if any(key.startswith('sealed_ninja_attempt_') and value.get('status') == 'pending'
               for key, value in data.get('actions', {}).items()):
            pending.append(str(path))
    return pending


def next_attempt(actions):
    used = []
    for index in range(1, DAILY_LIMIT+1):
        status = actions.get(f'sealed_ninja_attempt_{index}', {}).get('status', 'new')
        if status == 'pending':
            raise ValueError('封印忍者上一场结果未核实，不能再次开战')
        if status not in ('new', 'done'):
            raise ValueError('封印忍者次数记录异常，不能开战')
        if status == 'done':
            used.append(index)
    return next((i for i in range(1, DAILY_LIMIT+1) if i not in used), None)


def run(runner):
    import hyol_scheduler as hs
    import time
    s, account = runner.scheduler, runner.account
    previous = unresolved_previous_days(s.state.directory, s.state.account_paths[account.name].stem, s.state.day)
    carried = any(key.startswith('sealed_ninja_attempt_') and value.get('status') == 'pending'
                  for key, value in s.state.accounts[account.name].get('actions', {}).items())
    if previous and not carried:
        raise hs.PendingError('封印忍者存在跨天未核实记录，先核对旧战斗：' + '; '.join(previous))
    while True:
        actions = s.state.accounts.get(account.name, {}).get('actions', {})
        pending = next((i for i in (1,2) if actions.get(f'sealed_ninja_attempt_{i}',{}).get('status') == 'pending'), None)
        if pending is not None:
            index = pending
        else:
            if verify_zero(runner):
                return
            index = next_attempt(actions)
        if index is None:
            runner.record('sealed').update(completed=2)
            runner.save()
            raise hs.PendingError('封印忍者已记录两场，但尚无游戏剩余次数为0证据，不能标记完成')
        action = f'sealed_ninja_attempt_{index}'
        if pending is None:
            frame = s.capture()
            if not runner.visible('sealed_leader_badge.bmp',(0,69,65,150),frame):
                raise hs.PendingError('封印忍者仅队长执行；未确认队长标识，队员不重复开战')
            npc_roi = (0,160,1690,730)
            npc_name = next((name for name in ('sealed_name_current.bmp', 'sealed_name.bmp')
                             if runner.visible(name,npc_roi,frame)), None)
            if npc_name is None:
                if not runner.visible('sealed_chat_five_seals.bmp',(15,740,350,960),frame):
                    raise hs.PendingError('未找到封印忍者或已标定的五封结界公告链接，等待公告刷新')
                runner.click('sealed_chat_five_seals.bmp',(15,740,350,960),25,7,
                             'sealed_travel', ['sealed_chat_five_seals.bmp'])
                deadline = time.monotonic() + 60
                while time.monotonic() < deadline and npc_name is None:
                    frame = s.capture()
                    npc_name = next((name for name in ('sealed_name_current.bmp', 'sealed_name.bmp')
                                     if runner.visible(name,npc_roi,frame)), None)
                    if npc_name is None:
                        s.wait_interruptible(3)
                if npc_name is None:
                    raise hs.PendingError('公告寻路后60秒未确认封印忍者名称，未点击其他目标')
            # Match the NPC's exact red name, never a nearby generic ninja.
            runner.click(npc_name,npc_roi,40,75,action,[npc_name],True)
            runner.wait('sealed_warning.bmp',(760,480,1160,630),12)
        frame = s.capture()
        if runner.visible('sealed_warning.bmp',(760,480,1160,630),frame):
            confirm = f'sealed_confirm_{index}'
            if s.state.action_status(account.name,confirm) == 'pending':
                raise hs.PendingError('封印忍者确认结果未核实，禁止重复确认')
            runner.click('sealed_confirm.bmp',(780,550,960,630),49,16,confirm,
                         ['sealed_warning.bmp'],True)
        observed_at = time.monotonic()
        victory = s.state.action_status(account.name,f'sealed_victory_{index}') == 'done'
        from dataclasses import replace
        spec = replace(hs.TEAM_ACTIVITY_BY_KEY['group_instance'], key='sealed', label='封印忍者')
        auto_verified = bool(getattr(runner, 'team', None)
                             and s._team_members_auto_complete(runner.team, spec, index))
        watchdog = hs.BattleWatchdog.from_config(s.config, started_at=observed_at)
        while True:
            s.check_stop()
            frame = s.capture()
            if not (runner.visible('sealed_victory.bmp',(790,330,1160,630),frame) or s.main_ready(frame)):
                hs.check_battle_watchdog(s, watchdog, account, frame, f'sealed_battle_{index}', mode='sealed')
            if runner.visible('sealed_victory.bmp',(790,330,1160,630),frame):
                if getattr(runner, 'team', None) and not auto_verified:
                    raise hs.PendingError('封印已出现结果，但全队自动尚未逐一核实，保留本场结果')
                evidence = s.save_raw_evidence(frame,account,f'sealed_victory_{index}','verified')
                s.state.set_action(account.name,f'sealed_victory_{index}','done',str(evidence))
                victory = True
                result_action = f'sealed_result_{index}'
                if s.state.action_status(account.name,result_action) != 'new':
                    raise hs.PendingError('封印忍者结算确认已发送，等待结果，不重复点击')
                runner.click('team_result_confirm.bmp',(800,580,1120,720),55,18,
                             result_action,['sealed_victory.bmp'],True)
            elif s.main_ready(frame):
                if not victory:
                    raise hs.PendingError('封印忍者已回地图但未确认胜利，保留次数，禁止重新开战')
                for key in (action,f'sealed_confirm_{index}',f'sealed_result_{index}'):
                    s.state.set_action(account.name,key,'done','胜利证据及返回地图已核实')
                runner.record('sealed')['completed'] = index
                runner.save()
                runner.event('daily_sealed_completed',detail=f'封印忍者完成{index}/2，队伍共享')
                break
            elif runner.visible('battle_auto_off.bmp',hs.BATTLE_AUTO_ROI,frame):
                runner.once('battle_auto_off.bmp',hs.BATTLE_AUTO_ROI,29,29,f'sealed_auto_{index}')
            elif s._battle_auto_on_visible(frame) and getattr(runner, 'team', None) and not auto_verified:
                s._ensure_team_members_auto(runner.team, account, spec, index)
                auto_verified = True
                s.switch_account(account)
            elif not s._combat_active_visible('arena', frame) and time.monotonic()-observed_at >= 60:
                evidence = s.save_raw_evidence(frame,account,'sealed_transition_unverified','pending')
                raise hs.PendingError('封印战斗过渡60秒未核实，保留原场次：'+str(evidence))
            if s._combat_active_visible('arena', frame):
                observed_at = time.monotonic()
            s.wait_interruptible(.5)


def verify_zero(runner):
    """Only a genuine enrolled whole counter can prove exhaustion, never two clicks."""
    import hyol_scheduler as hs
    name = 'sealed_remaining_zero.bmp'
    if not (hs.ASSETS/name).exists():
        return False
    s, account = runner.scheduler, runner.account
    for _ in range(3):
        frame = s.capture()
        if not runner.visible(name, hs.VIEW, frame, .995):
            return False
        s.wait_interruptible(.3)
    evidence = s.save_raw_evidence(frame,account,'sealed_remaining_zero','verified')
    s.commit_action(account, 'sealed_real_remaining_zero', '游戏封印剩余次数连续三帧为0')
    runner.record('sealed').update(zero_verified=True, remaining=0, task_completed=True,
                                  zero_evidence=str(evidence))
    runner.save()
    runner.event('daily_sealed_zero_verified', remaining=0, evidence=str(evidence))
    return True
