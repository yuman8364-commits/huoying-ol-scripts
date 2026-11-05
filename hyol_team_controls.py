"""Per-account automatic combat and the captain's x2 control."""
import time
import hyol_scheduler as hs

SPEED_ROI = (1260,890,1335,975)
AUTO_ANCHORS = ['battle_auto_off.bmp','battle_auto_on.bmp',
                'battle_auto_on_current.bmp','battle_auto_on_current_core.bmp']


def combat_visible(s, frame):
    return s._battle_auto_on_visible(frame) or s.template('battle_auto_off.bmp',hs.BATTLE_AUTO_ROI,frame)[0]


def finished_visible(s, frame, task=None):
    return (s._team_result_template(frame) or s._team_reward_result_visible(frame)
            or s.template('group_return_game_current.bmp',(1110,750,1380,890),frame,threshold=.90)[0]
            or task=='bond'
            and s.template('bond_points_body_20261030.bmp',(835,365,1070,430),frame,threshold=.98)[0]
            and s.template('bond_points_label_20261030.bmp',(870,545,1000,610),frame,threshold=.98)[0])


def bond_member_loading_visible(s, frame):
    return (s.template('bond_member_loading_wait_20261030.bmp',(800,425,1130,485),frame,threshold=.98)[0]
            and s.template('bond_member_loading_bar_20261030.bmp',(720,525,990,600),frame,threshold=.98)[0])


def member_loading_visible(s, frame):
    from PIL import Image
    if not isinstance(frame,Image.Image):
        return False
    return (s.template('team_member_loading_text_20261104.bmp',(800,350,1140,440),frame,threshold=.98)[0]
            and s.template('team_member_loading_bar_20261104.bmp',(600,510,1340,590),frame,threshold=.98)[0])


def defer_preinput_loading(owner, driver, control, member, name, task, battle, base, action, before):
    """A vanished auto target on a proven loading page authorizes observation only."""
    s=owner.scheduler
    if type(before) is not int or s.content_click_count!=before or s.state.action_status(member.name,action)!='new':
        return False
    s.ensure_window()
    frame=s.capture()
    if s._generic_active_tab_x(frame)!=member.tab_x or not member_loading_visible(s,frame):
        return False
    wait=control.setdefault('member_loading_waits',{}).setdefault(name,dict(started_at=time.time()))
    if time.time()-wait['started_at']>120:
        raise hs.PendingError('该账号原战斗加载120秒仍未完成：'+str(member.index))
    wait['evidence']=str(s.save_raw_evidence(frame,member,base+'_member_loading','observed'))
    control['all_members_checked']=False
    driver.next_poll_seconds=1
    owner.save()
    s.event('pipeline_member_auto_transition_wait',member,task=task,battle=battle,
            evidence=wait['evidence'],content_clicks=0,battle_credit=False,
            detail='自动按钮点击前已转入本场加载页；确认未发出输入，保留原战斗和自动状态等待')
    return True


def ensure_bond_captain(owner, driver, rec, frame):
    s,account=owner.scheduler,driver.account
    if finished_visible(s,frame,'bond') or not combat_visible(s,frame):return frame
    battle=rec.get('completed',0)+1
    name=s._bound_identity_names.get(account.index)
    if not name or name!=driver.slot.get('owner',name):
        raise hs.PendingError('羁绊队长身份未绑定')
    control=rec.setdefault('combat_controls',{}).setdefault(str(battle),dict(members={}))
    control.update(scope='captain',leader_speed_policy='not_available',all_members_checked=False)
    if not s._battle_auto_on_visible(frame):
        action=f'pipeline_{owner.record["id"]}_bond_{battle}_auto_'+hs.slug(name)
        if s.state.action_status(account.name,action)!='new':
            raise hs.PendingError('队长原自动开启尚未核实，不重复切换')
        if not s.click_template(account,'battle_auto_off.bmp',hs.BATTLE_AUTO_ROI,29,29,action,['battle_auto_off.bmp']):
            raise hs.PendingError('队长自动战斗控件变化')
        if not s._wait_battle_auto_on(5):
            frame=s.capture()
            if finished_visible(s,frame,'bond'):
                proof=str(s.save_raw_evidence(frame,account,f'bond_{battle}_captain_finished_after_auto','observed'))
                control['members'][name]=dict(auto_on=None,battle_ended=True,enable_requested=True,
                                             finished_evidence=proof,at=hs.now_iso())
                control.update(captain_checked=True,captain_auto=False,captain_finished=True)
                owner.save()
                s.event('pipeline_bond_captain_finished_after_auto_request',account,battle=battle,evidence=proof,
                        detail='自动开启复核期间本场已结束；仅记录真实结算，不推定自动已开')
                return frame
            raise hs.PendingError('队长自动战斗开启尚未确认')
        s.commit_action(account,action,'队长自动战斗开启已确认')
        frame=s.capture()
    if control['members'].get(name,{}).get('auto_on') is not True:
        proof=str(s.save_raw_evidence(frame,account,f'bond_{battle}_captain_auto','verified'))
        control['members'][name]=dict(auto_on=True,evidence=proof,at=hs.now_iso())
        control.update(captain_auto=True,captain_checked=True)
        owner.save()
        s.event('pipeline_bond_captain_auto_verified',account,battle=battle,evidence=proof,
                detail='仅检查队长，不切换队员')
    return frame


def ensure(owner, driver, task, rec, frame):
    s, leader = owner.scheduler, driver.account
    if task=='bond':return ensure_bond_captain(owner,driver,rec,frame)
    if finished_visible(s,frame,task):
        return frame
    if not combat_visible(s, frame):
        return frame
    bound = {s._bound_identity_names.get(a.index):a for a in s.accounts}
    members = driver.slot.get('members', ())
    if not members or any(name not in bound for name in members):
        raise hs.PendingError('全员自动检查缺少对应账号标签，不以队长代替队员')
    battle = rec.get('completed',0)+1
    base = f'pipeline_{owner.record["id"]}_{task}_{battle}'
    control = rec.setdefault('combat_controls',{}).setdefault(str(battle),dict(members={}))
    speed_available = task not in ('bond','arena')
    known = control.get('members',{})
    already_checked = all(known.get(name,{}).get('auto_on') is True
                          or known.get(name,{}).get('battle_ended') is True for name in members)
    if (already_checked and s._battle_auto_on_visible(frame)
            and (not speed_available or s.template('battle_speed_x2.png',SPEED_ROI,frame,threshold=.84)[0])):
        return frame  # Same battle: inspect only the captain, no member tab visits.
    if speed_available and not s.template('battle_speed_x2.png',SPEED_ROI,frame,threshold=.84)[0]:
        if not s.template('battle_speed_x1.png',SPEED_ROI,frame,threshold=.84)[0]:
            raise hs.PendingError('队长战斗速度未知，未确认x2')
        action=base+'_leader_speed_x2'
        if s.state.action_status(leader.name,action)=='pending':
            raise hs.PendingError('队长原x2切换仍未决，不重复切换')
        if not s.click_template(leader,'battle_speed_x1.png',SPEED_ROI,14,14,action,AUTO_ANCHORS,
                                threshold=.84,anchor_threshold=.90):
            raise hs.PendingError('队长x1控件变化，x2待核实')
        if not s.wait_template('battle_speed_x2.png',SPEED_ROI,5,threshold=.84):
            raise hs.PendingError('队长x2切换已发送，但尚未确认')
        s.commit_action(leader,action,'已确认队长战斗速度x2')
        frame=s.capture()
    if speed_available:
        control['leader_x2_evidence']=str(s.save_raw_evidence(frame,leader,base+'_leader_x2','verified'))
        s.event('pipeline_leader_x2_verified',leader,task=task,battle=battle,
                evidence=control['leader_x2_evidence'])
    else:
        control['leader_speed_policy']='not_available'
    owner.save()
    deferred=False
    try:
        remaining = [name for name in members if not (known.get(name,{}).get('auto_on') is True
                     or known.get(name,{}).get('battle_ended') is True)]
        leader_name=s._bound_identity_names.get(leader.index)
        if leader_name not in remaining and not s._battle_auto_on_visible(frame):
            remaining.insert(0,leader_name)
        for name in remaining:
            member=bound[name]
            frame=s.switch_account(member,verify=False)
            deadline=time.monotonic()+20
            while True:
                s.check_stop()
                if (control['members'].get(name,{}).get('auto_on') is True
                        and not combat_visible(s,frame)):
                    # A member may finish before the captain's bonus stage.
                    # Keep this same battle's already observed auto proof.
                    break
                if s._battle_auto_on_visible(frame):
                    evidence=str(s.save_raw_evidence(frame,member,base+'_auto_on','verified'))
                    control['members'][name]=dict(auto_on=True,evidence=evidence,at=hs.now_iso())
                    owner.save()
                    s.event('pipeline_member_auto_verified',member,task=task,battle=battle,evidence=evidence)
                    break
                if s.template('battle_auto_off.bmp',hs.BATTLE_AUTO_ROI,frame)[0]:
                    action=base+'_auto_'+hs.slug(name)
                    if s.state.action_status(member.name,action)=='pending':
                        raise hs.PendingError('该账号原自动战斗开启仍未决：'+str(member.index))
                    before=getattr(s,'content_click_count',None)
                    try:
                        sent=s.click_template(member,'battle_auto_off.bmp',hs.BATTLE_AUTO_ROI,29,29,action,
                                              ['battle_auto_off.bmp'])
                    except (hs.SafetyError,hs.PendingError):
                        if defer_preinput_loading(owner,driver,control,member,name,task,battle,base,action,before):
                            deferred=True
                            break
                        raise
                    if not sent:
                        raise hs.PendingError('自动战斗按钮变化：'+str(member.index))
                    if not s._wait_battle_auto_on(5):
                        frame=s.capture()
                        if finished_visible(s,frame,task):
                            evidence=str(s.save_raw_evidence(frame,member,base+'_finished_after_auto_request','observed'))
                            control['members'][name]=dict(auto_on=None,battle_ended=True,
                                enable_requested=True,finished_evidence=evidence,at=hs.now_iso())
                            owner.save()
                            break
                        raise hs.PendingError('该账号自动战斗未确认开启：'+str(member.index))
                    s.commit_action(member,action,'已确认该账号自动战斗开启')
                elif finished_visible(s,frame,task):
                    if control['members'].get(name,{}).get('auto_on') is True:
                        break
                    evidence=str(s.save_raw_evidence(frame,member,base+'_finished_before_auto_observation','observed'))
                    control['members'][name]=dict(auto_on=None,battle_ended=True,finished_evidence=evidence,at=hs.now_iso())
                    owner.save()
                    break
                elif member_loading_visible(s,frame) or task=='bond' and bond_member_loading_visible(s,frame):
                    wait=control.setdefault('member_loading_waits',{}).setdefault(name,dict(started_at=time.time()))
                    wait['evidence']=str(s.save_raw_evidence(frame,member,base+'_member_loading','observed'))
                    control['all_members_checked']=False
                    owner.save()
                    if time.time()-wait['started_at']>120:
                        raise hs.PendingError('该账号原羁绊战斗加载120秒仍未完成：'+str(member.index))
                    driver.next_poll_seconds=3
                    deferred=True
                    s.event('pipeline_member_auto_loading_wait',member,task=task,battle=battle,
                            evidence=wait['evidence'],detail='等待该成员原战斗加载；尚未确认其自动状态')
                    break
                elif time.monotonic()>=deadline:
                    control.setdefault('member_unverified',{})[name]=dict(evidence=str(s.save_raw_evidence(
                        frame,member,base+'_auto_unverified','pending')),at=hs.now_iso())
                    owner.save()
                    raise hs.PendingError('该账号20秒内尚未确认自动战斗：'+str(member.index))
                s.wait_interruptible(.35)
                frame=s.capture()
            if deferred:
                break
        all_auto=all(control['members'].get(name,{}).get('auto_on') is True for name in members)
        control.update(all_members_auto=all_auto,all_members_checked=not deferred,checked_at=hs.now_iso())
        owner.save()
        if not deferred:
            s.event('pipeline_all_members_auto_verified' if all_auto else 'pipeline_all_members_checked',leader,task=task,battle=battle,
                    member_count=len(members),members=list(members))
    finally:
        frame=s.switch_account(leader,verify=False)
    return frame
