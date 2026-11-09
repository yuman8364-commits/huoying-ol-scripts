"""Two-person Bond follow-up, using the pipeline's lease and named ledger."""
from pathlib import Path
import time

import hyol_scheduler as hs
from hyol_bond import BondRunner, BOND_READY_ROI, BOND_RESULT_ROI

CLOSE = 'login_update_notice_close_core_current.bmp'
CLOSE_ROI = (1550, 80, 1700, 165)


def verified(rec, slot, requested, goal=None):
    from hyol_bond_goals import observation_verified
    goal = goal or dict(mode='battles', target=requested)
    completed = rec.get('completed')
    limit = goal['target'] if goal['mode'] == 'battles' else 99
    progress_ok = (type(completed) is int and 0 <= completed <= limit
                   and (completed == limit if goal['mode'] == 'battles'
                        else observation_verified(rec, slot.get('members', ()), goal)))
    results = rec.get('verified_results', [])
    subjects=(slot.get('owner'),) if rec.get('observation_scope')=='captain' else slot.get('members',())
    if goal['mode']!='battles':
        progress_ok=type(completed) is int and 0<=completed<=limit and observation_verified(rec,subjects,goal)
    return (type(requested) is int and 1 <= requested <= 99 and isinstance(results, list)
            and all(isinstance(r, dict) for r in results)
            and rec.get('status') == 'done' and rec.get('phase') == 'done' and rec.get('task_completed') is True
            and rec.get('home_returned') is True and bool(rec.get('evidence'))
            and rec.get('owner') == slot.get('owner') and bool(slot.get('owner'))
            and len(slot.get('members', ())) == len(set(slot.get('members', ()))) == 2
            and set(rec.get('members', ())) == set(slot['members'])
            and rec.get('goal', dict(mode='battles', target=requested)) == goal
            and type(rec.get('requested')) is int and rec['requested'] == limit
            and progress_ok and len(results) == completed
            and (goal['mode'] == 'battles' or set(rec.get('member_home_evidence', {})) == set(subjects)
                 and all(rec['member_home_evidence'].values()))
            and [r.get('battle') for r in results] == list(range(1, completed + 1))
            and all(r.get('result_evidence') and r.get('ready_evidence')
                    and r.get('team_evidence') for r in results))


class TeamBondDriver(BondRunner):
    """One bounded visual step per turn; never acquires a second runtime lease."""
    def __init__(self, owner, account, slot):
        from hyol_bond_goals import team_goal
        self.owner, self.scheduler, self._account, self.slot = owner, owner.scheduler, account, slot
        self.goal = team_goal(owner, slot.get('leader_slot', account.index))
        limit = self.goal['target'] if self.goal['mode'] == 'battles' else 99
        self.next_poll_seconds = .6
        self.request = slot.setdefault('tasks', {}).setdefault('bond', dict(
            status='new', phase='ready', completed=0, requested=limit, goal=self.goal,
            run_id=owner.record['id'] + '_team_' + str(account.index),
            owner=slot['owner'], members=list(slot['members']), verified_results=[]))
        rec = self.request
        if (rec.get('owner') != slot.get('owner') or rec.get('members') != slot.get('members')
                or rec.get('requested') != limit
                or rec.get('goal', dict(mode='battles', target=owner.bond_count)) != self.goal
                or type(rec.get('completed')) is not int or not 0 <= rec['completed'] <= limit):
            raise hs.PendingError('羁绊原队伍、身份或场数改变，保留原轮次')
        self.watchdog = None
        rec['observation_scope']='captain'
        rec['claim_policy']='after_kill_target'
        scopes=getattr(self.scheduler,'_bond_observation_requests',{})
        if not isinstance(scopes,dict):
            scopes={}
        scopes[account.name]=self.request
        self.scheduler._bond_observation_requests=scopes

    def member_accounts(self):
        bound = {self.scheduler._bound_identity_names.get(a.index): a for a in self.scheduler.accounts}
        if any(name not in bound for name in self.slot['members']):
            raise hs.PendingError('羁绊目标缺少队员标签，不能只读队长就结束')
        return [(name, bound[name]) for name in self.slot['members']]

    def settle_member_result(self, frame):
        """Acknowledge only this run's finished round on a known member tab."""
        s, rec = self.scheduler, self.request
        for _ in range(3):
            from hyol_identity_overlays import inspect_bond_shell, dismiss_identity_overlays
            shell = inspect_bond_shell(s, frame)
            if shell and shell[0] == 'bond_points_result':
                if rec['completed'] < 1 and not self.result_was_observed(rec['completed']+1):
                    raise hs.PendingError('积分弹窗尚未关联本轮真实结算，不补记场次')
                proof = str(s.save_raw_evidence(frame, self.account, 'bond_points_for_owned_round', 'verified'))
                rec.setdefault('member_points_results', {}).setdefault(str(rec['completed']), {})[self.account.name] = proof
                self.save_request()
                frame = dismiss_identity_overlays(s, self.account, frame, only_kind='bond_points_result')
                continue
            result = s._team_result_template(frame)
            reward = s._team_reward_result_visible(frame)
            if not result and not reward:
                return frame
            battle = rec['completed']
            if battle < 1 or not any(r.get('battle') == battle for r in rec.get('verified_results', ())):
                raise hs.PendingError('队员结算没有本轮对应场次，不关闭未知结果或新开战斗')
            asset = result or hs.TEAM_REWARD_RESULT_TEMPLATE
            evidence = str(s.save_raw_evidence(frame, self.account, f'bond_member_{battle}_result', 'verified'))
            rec.setdefault('member_results', {}).setdefault(str(battle), {})[self.account.name] = evidence
            self.save_request()
            self.click_once_and_wait_absent(asset, BOND_RESULT_ROI if result else hs.TEAM_REWARD_RESULT_ROI,
                55 if asset.startswith('team_result') else 64, 18 if asset.startswith('team_result') else 19,
                f'bond_{rec["run_id"]}_battle_{battle}_member_{self.account.index}_{asset}', 8)
            frame = s.capture()
        raise hs.PendingError('羁绊队员结算尚未收起，原场次保留')

    def pending_kill_chest(self, name, frame):
        """A sent chest claim must change its tier before any further match."""
        from hyol_bond_goals import counters
        s, rec = self.scheduler, self.request
        pending = [row for row in rec.get('kill_chest_claims',{}).get(name,{}).values() if row.get('status')=='pending']
        if not pending:
            return False
        for row in pending:
            header='bond_chest_reward_header_core_20261030.bmp'
            button='bond_chest_reward_confirm_core_20261030.bmp'
            if (s.template(header,(800,380,1120,500),frame,threshold=.98)[0]
                    and s.template(button,(850,580,1050,700),frame,threshold=.98)[0]):
                action=row['action']+'_reward_confirm'
                if s.state.action_status(self.account.name,action)!='new':
                    raise hs.PendingError('原击杀宝箱奖励确认仍未决或重现，不重复确认')
                row['reward_evidence']=str(s.save_raw_evidence(frame,self.account,'bond_kill_chest_reward','verified'))
                row['reward_action']=action
                self.save_request()
                if not s.click_template(self.account,button,(850,580,1050,700),20,9,action,[header,button],
                                        irreversible=True,threshold=.98,anchor_threshold=.98):
                    raise hs.PendingError('击杀宝箱奖励确认控件变化，原领取保留')
                if not s.wait_absent(header,(800,380,1120,500),8,threshold=.98):
                    raise hs.PendingError('击杀宝箱奖励确认已发送，窗口尚未消失')
                s.commit_action(self.account,action,'该次上方击杀宝箱奖励确认窗已消失；不计为战斗')
                self.next_poll_seconds=.6
                return True
            if self.ready_visible(frame):
                try:
                    after = counters(frame,s.templates)
                except hs.PendingError:
                    after = None
                if (after and after['kill_cap'] > row['before']['kill_cap']
                        and after['kills'] == row['before']['kills']-row['before']['kill_cap']):
                    samples=[str(s.save_raw_evidence(frame,self.account,'bond_kill_chest_next_tier_1','verified'))]
                    s.wait_interruptible(.2)
                    fresh=s.capture()
                    if not self.ready_visible(fresh) or counters(fresh,s.templates)!=after:
                        raise hs.PendingError('击杀宝箱新档位两帧不一致，原领取保留')
                    samples.append(str(s.save_raw_evidence(fresh,self.account,'bond_kill_chest_next_tier_2','verified')))
                    proof=samples[-1]
                    row.update(status='done',owner=name,after=after,evidence=proof,samples=samples)
                    s.commit_action(self.account,row['action'],'新档位两帧一致，显示击杀数恰好扣除已领取档位需求')
                    if row.get('reward_action') and s.state.action_status(self.account.name,row['reward_action'])=='pending':
                        s.commit_action(self.account,row['reward_action'],'该次宝箱新档位已正向核实，原奖励确认已生效')
                    self.save_request()
                    self.event('bond_kill_chest_claimed',member=name,after=after,evidence=proof)
                    continue
            row['after_evidence']=str(s.save_raw_evidence(frame,self.account,'bond_kill_chest_transition','observed'))
            self.save_request()
            if time.time()-row['sent_at']>20:
                raise hs.PendingError('击杀宝箱领取已发送，下一档奖励尚未核实；不重复领取或匹配')
            self.next_poll_seconds=.6
            return True
        return False

    def claim_kill_tier(self, name, frame, values):
        from hyol_bond_goals import KILLS_ROI, claimed_kills
        s, rec = self.scheduler, self.request
        cap=values['kill_cap']
        rows=rec.setdefault('kill_chest_claims',{}).setdefault(name,{})
        if str(cap) in rows:
            raise hs.PendingError('原击杀宝箱档位仍显示，不重复领取')
        anchor=s.template('bond_ready_anchor.bmp',(1000,200,1500,450),frame,threshold=.98)
        if not anchor[0] or values['kills']<cap:
            raise hs.PendingError('当前上方击杀宝箱尚未正向达标')
        dx,dy=anchor[2]-1111,anchor[3]-271
        roi=tuple(v+(dx if i%2==0 else dy) for i,v in enumerate(KILLS_ROI))
        signature=hs.image_hash(frame.crop(roi))
        action=f'bond_{rec["run_id"]}_kill_chest_{self.account.index}_{cap}'
        if s.state.action_status(self.account.name,action)!='new':
            raise hs.PendingError('原击杀宝箱领取动作仍需对账，不重复点击')
        row=rows[str(cap)]=dict(status='pending',before=values,action=action,sent_at=time.time(),
            cumulative_before=claimed_kills(rec,name)+values['kills'],
            before_evidence=str(s.save_raw_evidence(frame,self.account,'bond_kill_chest_before','verified')))
        self.save_request()
        s.click_point(self.account,anchor[2]+157,anchor[3]+148,action,['bond_ready_anchor.bmp'],
            irreversible=True,anchor_threshold=.98,
            frame_predicate=lambda fresh:self.ready_visible(fresh) and hs.image_hash(fresh.crop(roi))==signature)
        self.next_poll_seconds=.6
        self.event('bond_kill_chest_claim_requested',member=name,before=values,
                   detail=f'累计击杀{row["cumulative_before"]}，领取{cap}人档奖励并核对下一档')

    def observe_members(self):
        """The captain alone decides when to stop; no member-tab visits."""
        from hyol_bond_goals import counters, claimed_kills, claimed_tiers, lobby_identity
        s, rec, account = self.scheduler, self.request, self.account
        name=self.slot['owner']
        frame=s.capture()
        if self.pending_kill_chest(name,frame):return None
        frame=self.settle_member_result(frame)
        samples,values=[],[]
        for sample in range(2):
            if not self.ready_visible(frame) or s._team_result_template(frame) or s._team_reward_result_visible(frame):
                self.next_poll_seconds=.6
                return None
            samples.append(str(s.save_raw_evidence(frame,account,f'bond_goal_{rec["completed"]}_sample_{sample+1}','verified')))
            values.append(counters(frame,s.templates))
            if sample==0:
                s.wait_interruptible(.25)
                frame=s.capture()
        if values[0]!=values[1]:
            raise hs.PendingError('队长羁绊积分或击杀数两帧不一致')
        if not rec.get('captain_lobby_identity'):
            rec['captain_lobby_identity']=dict(owner=name,rows=lobby_identity(frame,s.templates),samples=samples,evidence=samples[-1])
        current=values[-1]
        if self.goal['mode']=='kills' and max(claimed_tiers(rec,name),default=0)<(self.goal['target'] or 160):
            if current['kills']>=(self.goal['target'] or 160):rec['final_claim_phase']=True
            if rec.get('final_claim_phase') and current['kills']>=current['kill_cap']:
                self.claim_kill_tier(name,frame,current)
                return None
            if current['kills']<current['kill_cap']:rec['final_claim_phase']=False
        banked=claimed_kills(rec,name)
        row=dict(owner=name,score=current['score'],kills=banked+current['kills'],tier_kills=current['kills'],
            banked_kills=banked,kill_cap=current['kill_cap'],claimed_tier=max(claimed_tiers(rec,name),default=0),
            samples=samples,evidence=samples[-1])
        observations={name:row}
        self.event('bond_goal_member_observed',member=name,**row,
            detail=f'仅队长：积分{current["score"]}，击杀{current["kills"]}/{current["kill_cap"]}；连续匹配，目标达成后集中领奖')
        rec['goal_observation']=dict(run_id=rec['run_id'],goal=self.goal,scope='captain',
            after_battle=rec['completed'],members=observations,at=hs.now_iso())
        self.save_request()
        return observations

    def goal_reached(self):
        from hyol_bond_goals import satisfied
        observations = self.observe_members()
        if observations is None:
            return None
        return satisfied(self.goal, observations, (self.slot['owner'],))

    def finish_members(self):
        s=self.scheduler
        frame=self.settle_member_result(s.capture())
        if self.ready_visible(frame):self.close_ready()
        else:
            from hyol_navigation import return_to_leaf
            if return_to_leaf(s,self.account,allow_pending_navigation=True) is not True:
                raise hs.PendingError('队长羁绊目标完成后回村尚未确认')
        self.request['member_home_evidence']={self.slot['owner']:str(s.save_raw_evidence(
            s.capture(),self.account,'bond_goal_captain_home','verified'))}
        self.save_request()

    def record(self, task):
        return self.request

    def start_battle(self, battle_no):
        super().start_battle(battle_no)
        self.request.pop('match_wait_started_at',None)
        self.request.pop('match_found_wait_started_at',None)
        self.save_request()

    def result_was_observed(self, battle_no):
        if super().result_was_observed(battle_no):
            return True
        row = self.request.get('result_observation', {})
        return (row.get('run_id') == self.request['run_id'] and row.get('battle') == battle_no
                and row.get('owner') == self.account.name and row.get('template') == 'bond_points_result'
                and isinstance(row.get('evidence'),str) and Path(row['evidence']).is_file())

    def ready_visible(self, frame=None):
        frame = frame if frame is not None else self.scheduler.capture()
        if not super().ready_visible(frame):
            return False
        from hyol_identity_overlays import inspect_bond_shell
        shell = inspect_bond_shell(self.scheduler, frame)
        return not (shell and shell[0] in ('bond_points_result','blocked_bond_points'))

    def save_request(self):
        self.request['updated_at'] = hs.now_iso()
        self.owner.save()

    def ensure_predecessors(self):
        from hyol_team_pipeline import TASK_KEYS, group_reward_zero_confirmed, strong_stop_confirmed
        for selected in self.owner.tasks:
            if selected == 'bond':
                continue
            rec = self.slot.get('tasks', {}).get(TASK_KEYS[selected], {})
            if (rec.get('status') != 'done' or rec.get('task_completed') is not True
                    or rec.get('home_returned') is not True or not rec.get('evidence')
                    or selected == 'group_instance' and not group_reward_zero_confirmed(rec)
                    or selected == 'strong_approach' and not strong_stop_confirmed(rec, self.slot['owner'])
                    or selected == 'wanted' and not rec.get('empty_evidence')):
                raise hs.PendingError('所选多人任务尚未完成，不能提前进入羁绊')

    def close_ready(self):
        s = self.scheduler
        if not self.ready_visible_stable():
            raise hs.PendingError('羁绊准备页未连续确认，不能退出或重复匹配')
        if not s.click_template(self.account, CLOSE, CLOSE_ROI, 12, 12,
                                'pipeline_bond_close', ['bond_ready_anchor.bmp', CLOSE],
                                threshold=.98, anchor_threshold=.98):
            raise hs.PendingError('羁绊退出按钮未确认')
        if not s.wait_absent('bond_ready_anchor.bmp', BOND_READY_ROI, 8, threshold=.92):
            raise hs.PendingError('羁绊退出尚未确认')
        from hyol_identity_overlays import dismiss_identity_overlays
        dismiss_identity_overlays(s, self.account, s.capture(), only_kind='bond_points_result')
        from hyol_navigation import return_to_leaf
        if return_to_leaf(s, self.account, allow_pending_navigation=True) is not True:
            raise hs.PendingError('羁绊退出后尚未确认木叶村')

    def enter_ready(self, frame):
        """Use the named 羁绊对决 daily row, never a lookalike toolbar icon."""
        from hyol_team_pipeline import daily_page_present
        s,rec=self.scheduler,self.request
        rec.setdefault('navigation_started_at',time.time())
        if (s.template('bond_npc_body_20261030.bmp',(780,400,1220,535),frame,threshold=.98)[0]
                and s.template('bond_npc_enter_20261030.bmp',(780,535,1170,630),frame,threshold=.98)[0]):
            if rec.get('npc_entry_sent'):
                return
            rec.update(npc_entry_sent=True,npc_entry_sent_at=time.time())
            self.save_request()
            if not s.click_template(self.account,'bond_npc_enter_20261030.bmp',(780,535,1170,630),63,11,
                    'pipeline_bond_npc_enter',['bond_npc_body_20261030.bmp','bond_npc_enter_20261030.bmp'],
                    threshold=.98,anchor_threshold=.98):
                rec['npc_entry_sent']=False
                self.save_request()
                raise hs.PendingError('伊鲁卡“参加羁绊对决”选项未确认')
            return
        if s.template('treasure_page_anchor.bmp',(560,250,1380,810),frame,threshold=.96)[0]:
            if not s.click_template(self.account,CLOSE,(1290,295,1370,375),12,12,
                    'pipeline_bond_correct_treasure_close',['treasure_page_anchor.bmp'],
                    threshold=.98,anchor_threshold=.96):
                raise hs.PendingError('原宝藏面板关闭未确认，未启动挖宝')
            if not s.wait_absent('treasure_page_anchor.bmp',(560,250,1380,810),8,threshold=.92):
                raise hs.PendingError('宝藏面板尚未关闭，未点击任何挖宝按钮')
            rec['navigation_correction']='closed_treasure_without_starting'
            self.save_request()
            return
        if rec.get('npc_entry_sent'):
            if time.time()-rec['npc_entry_sent_at']>90:
                raise hs.PendingError('伊鲁卡入场后90秒未确认羁绊准备页，保留原请求')
            return
        if rec.get('participate_sent'):
            return  # Original navigation awaits its named NPC dialogue.
        if time.time()-rec['navigation_started_at']>90:
            raise hs.PendingError('羁绊日常入口90秒未确认准备页，保留原导航')
        if not daily_page_present(s,frame):
            if s.main_ready(frame):
                if not s.click_template(self.account,'daily_practice_toolbar.bmp',(651,69,851,169),20,20,
                        'pipeline_bond_open_daily',['anchor_top_menu.bmp']):
                    raise hs.PendingError('羁绊的日常锻炼入口未确认')
            return
        if not s.template('daily_limited_tab_selected.bmp',(510,250,800,310),frame)[0]:
            if not s.click_template(self.account,'daily_limited_tab_current.bmp',(510,250,800,310),40,15,
                    'pipeline_bond_limited_tab',['daily_limited_tab_current.bmp']):
                raise hs.PendingError('羁绊限时活动标签未确认')
            return
        if s.template('team_pvp_anchor.bmp',(1080,290,1370,380),frame,threshold=.90)[0]:
            if s.template('daily_participate_disabled_current.bmp',(1051,569,1351,819),frame,threshold=.98)[0]:
                raise hs.PendingError('羁绊当前不可参与，未发送匹配')
            rec['participate_sent']=True
            self.save_request()
            if not s.click_template(self.account,'daily_participate.bmp',(1051,569,1351,819),51,18,
                    'pipeline_bond_participate',['team_pvp_anchor.bmp'],anchor_threshold=.90):
                rec['participate_sent']=False
                self.save_request()
                raise hs.PendingError('羁绊参与按钮未确认')
            return
        if s.template('team_pvp_entry.bmp',(490,300,1080,720),frame,threshold=.90)[0]:
            if rec.get('row_clicks',0)>=3:
                raise hs.PendingError('羁绊对决已点选三次，详情尚未确认')
            if not s.click_template(self.account,'team_pvp_entry.bmp',(490,300,1080,720),35,20,
                    'pipeline_bond_select_named_row',['team_pvp_entry.bmp']):
                raise hs.PendingError('羁绊对决活动行变化')
            rec['row_clicks']=rec.get('row_clicks',0)+1
            self.save_request()
            return
        from hyol_team_pipeline import daily_list_signature
        signature = daily_list_signature(frame)
        if (rec.get('scroll_direction', 'up') == 'up' and rec.get('scroll_up', 0) < 16
                and signature != rec.get('scroll_up_signature')
                and s.template('daily_scroll_up_current.bmp',(1060,310,1095,350),frame)[0]):
            if s.click_template(self.account,'daily_scroll_up_current.bmp',(1060,310,1095,350),10,10,
                    'pipeline_bond_scroll_up',['daily_scroll_up_current.bmp']):
                rec['scroll_up']=rec.get('scroll_up',0)+1
                rec['scroll_up_signature'] = signature
                self.save_request()
            return
        rec['scroll_direction'] = 'down'
        if (rec.get('scroll_down', 0) >= 24 or signature == rec.get('scroll_down_signature')
                or not s.template('daily_scroll_down_current.bmp',(1060,650,1095,725),frame)[0]):
            raise hs.PendingError('限时活动已到列表末端，羁绊对决活动行尚未确认，未发送匹配')
        if s.click_template(self.account,'daily_scroll_down_current.bmp',(1060,650,1095,725),10,26,
                'pipeline_bond_scroll_down',['daily_scroll_down_current.bmp']):
            rec['scroll_down'] = rec.get('scroll_down', 0)+1
            rec['scroll_down_signature'] = signature
            self.save_request()

    def tick(self, task='bond'):
        s, rec = self.scheduler, self.request
        self.next_poll_seconds = .6
        self.ensure_predecessors()
        if len(self.slot.get('members', ())) != 2:
            raise hs.PendingError('羁绊只能二人开打；当前人数不是二人，不匹配、不踢人')
        phase = rec.get('phase', 'ready')
        frame = s.switch_account(self.account, verify=False)
        self._account = s.by_name[self.account.name]
        if s.template('bond_rules_header_20261030.bmp',(750,270,1150,370),frame,threshold=.98)[0]:
            if not s.click_template(self.account,'bond_rules_close_20261030.bmp',(1240,300,1350,390),16,16,
                    'bond_rules_close',['bond_rules_header_20261030.bmp','bond_rules_close_20261030.bmp'],
                    threshold=.98,anchor_threshold=.98):
                raise hs.PendingError('羁绊规则关闭按钮变化')
            return
        from hyol_identity_overlays import inspect_bond_shell
        shell = inspect_bond_shell(s, frame)
        if shell and shell[0] == 'bond_points_result' and phase != 'ready':
            if phase in ('match_pending', 'running', 'result_confirmed'):
                battle = rec['completed']+1
                if not self.result_was_observed(battle):
                    action = f'bond_{rec["run_id"]}_battle_{battle}_match'
                    if s.state.action_status(self.account.name,action) not in ('pending','done'):
                        raise hs.PendingError('积分弹窗没有本轮对应匹配凭据，原结果保留')
                    self.observe_result(frame, battle, 'bond_points_result')
                rec['phase'] = 'result_confirmed'
                self.save_request()
            self.settle_member_result(frame)
            return
        if getattr(self.owner, 'verify_all_combatants', False):
            frame = self.owner.ensure_battle_controls(self, 'bond', rec, frame)
        if phase == 'finishing':
            if self.goal['mode'] != 'battles':
                self.finish_members()
            elif self.ready_visible(frame):
                self.close_ready()
            elif not s.main_ready(frame):
                raise hs.PendingError('羁绊最后一场结束后的页面未知，不重新开战')
            evidence = str(s.save_raw_evidence(s.capture(), self.account, 'pipeline_bond_home', 'verified'))
            rec.update(status='done', phase='done', task_completed=True, home_returned=True,
                       evidence=evidence, finished_at=hs.now_iso(), last_error='')
            self.save_request()
            self.event('pipeline_bond_complete', completed=rec['completed'], requested=rec['requested'], evidence=evidence)
            return
        if phase == 'ready':
            if self.ready_visible(frame):
                if not getattr(self.owner, 'preformed_duos', False):
                    raise hs.PendingError('羁绊准备页的原队伍未关联，不退出或重新开战')
                rec.update(status='running', phase='entering', entered_at=time.time(),
                           team_evidence=self.slot.get('team_evidence'))
                self.save_request()
                return
            if not s.main_ready():
                raise hs.PendingError('羁绊开场前尚未确认主界面')
            members = tuple(self.slot['members'])
            evidence = self.slot.get('team_evidence')
            if not getattr(self.owner, 'preformed_duos', False):
                members, evidence = self.owner.snapshot(self.account)
            if (len(members) != len(set(members)) or len(members) != 2
                    or set(members) != set(self.slot['members'])):
                raise hs.PendingError('羁绊开战前队伍变更或不是二人，不发送匹配')
            rec.update(status='running', phase='entering', team_evidence=evidence,
                       membership_source=self.slot.get('membership_source', 'verified_room'), entered_at=time.time())
            self.save_request()
            self.enter_ready(frame)
            return
        if phase == 'entering':
            if not self.ready_visible(frame):
                self.enter_ready(frame)
                return
            if not self.ready_visible_stable():
                return
            if not rec.get('team_evidence'):
                raise hs.PendingError('羁绊缺少当前二人成员凭据，不匹配')
            if self.goal['mode'] != 'battles':
                reached = self.goal_reached()
                if reached is None:
                    return
                if reached:
                    rec['phase'] = 'finishing'
                    self.save_request()
                    self.event('bond_goal_reached', goal=self.goal, completed=rec['completed'])
                    return
                if rec['completed'] >= rec['requested']:
                    raise hs.PendingError('羁绊已到99场观察上限但实际目标未达成，保留进度')
            # Bond hides the village role strip. The pipeline's bound tab
            # owns this page; do not reopen rooms or recheck party members.
            self.start_battle(rec['completed'] + 1)
            self.watchdog = hs.BattleWatchdog.from_config(s.config)
            return
        if phase not in ('match_pending', 'running', 'result_confirmed'):
            raise hs.PendingError('羁绊阶段未知，保留原轮次')
        battle = rec['completed'] + 1
        self.watchdog = self.watchdog or hs.BattleWatchdog.from_config(s.config)
        result = s._team_result_template(frame)
        reward = s._team_reward_result_visible(frame)
        if result or reward:
            asset = result or hs.TEAM_REWARD_RESULT_TEMPLATE
            self.observe_result(frame, battle, asset)
            self.click_once_and_wait_absent(asset,
                BOND_RESULT_ROI if result else hs.TEAM_REWARD_RESULT_ROI,
                55 if asset.startswith('team_result') else 64,
                18 if asset.startswith('team_result') else 19,
                f'bond_{rec["run_id"]}_battle_{battle}_' + ('result' if result else 'reward_result'), 8)
            rec['phase'] = 'result_confirmed'
            self.save_request()
            return
        waiting=(s.template('bond_wait_battle_header_20261030.bmp',(750,420,1150,540),frame,threshold=.98)[0]
                 and s.template('bond_wait_battle_hint_20261030.bmp',(780,580,1120,680),frame,threshold=.98)[0])
        searching=(s.template('bond_search_opponent_header_20261030.bmp',(750,420,1150,540),frame,threshold=.98)[0]
                   and s.template('bond_search_opponent_hint_20261030.bmp',(780,580,1120,680),frame,threshold=.98)[0])
        if waiting or searching:
            action=f'bond_{rec["run_id"]}_battle_{battle}_match'
            if s.state.action_status(self.account.name,action) not in ('pending','done'):
                raise hs.PendingError('等待战斗页没有本轮原匹配凭据，不重开')
            started=rec.setdefault('match_wait_started_at',time.time())
            found=rec.setdefault('match_found_wait_started_at',time.time()) if waiting else None
            self.save_request()
            if time.time()-started>600 or found is not None and time.time()-found>120:
                raise hs.PendingError('羁绊原匹配等待达到观察上限，原请求保留，未取消或重发')
            self.watchdog=None  # Waiting is bounded separately; combat retains its own idle guard.
            self.next_poll_seconds=3 if waiting else 10
            self.event('bond_original_match_wait',battle=battle,detail='等待同一原匹配进入战斗，未取消或重发')
            return
        if self.ready_visible(frame):
            if not self.result_was_observed(battle):
                raise hs.PendingError('羁绊返回准备页但没有该场真实结算，不计完成、不重开')
            if not self.ready_visible_stable():
                return
            self.reconcile_battle_actions(battle)
            ready = str(s.save_raw_evidence(s.capture(), self.account, f'pipeline_bond_{battle}_ready', 'verified'))
            rec.setdefault('verified_results', []).append(dict(battle=battle,
                result_evidence=rec['result_observation']['evidence'], ready_evidence=ready,
                team_evidence=rec['team_evidence']))
            # Remain inside Bond between battles: exiting ends the party.
            rec.update(completed=battle, phase='finishing' if self.goal['mode'] == 'battles'
                       and battle == rec['requested'] else 'entering')
            self.save_request()
            self.event('bond_battle_completed', battle=battle, completed=battle, requested=rec['requested'])
            return
        hs.check_battle_watchdog(s, self.watchdog, self.account, frame,
                                f'bond_{rec["run_id"]}_battle_{battle}', mode='bond')
        for asset, roi, dx, dy, suffix in (
            (hs.TEAM_BATTLE_START_TEMPLATE, hs.TEAM_BATTLE_START_ROI, 65, 23, 'leader_start'),
            ('team_leader_final_start.bmp', (650,400,1250,850), 55, 18, 'final_start')):
            if s.template(asset, roi, frame, threshold=.90)[0]:
                self.click_once_and_wait_absent(asset, roi, dx, dy,
                    f'bond_{rec["run_id"]}_battle_{battle}_{suffix}', 12)
                return
        if s.template('battle_auto_off.bmp', hs.BATTLE_AUTO_ROI, frame)[0]:
            if not s.click_template(self.account, 'battle_auto_off.bmp', hs.BATTLE_AUTO_ROI, 29,29,
                                    f'bond_{rec["run_id"]}_battle_{battle}_auto', ['battle_auto_off.bmp']):
                raise hs.PendingError('羁绊自动战斗按钮变化')
            if not s._wait_battle_auto_on(5):
                raise hs.PendingError('羁绊自动战斗开启尚未确认')
        self.next_poll_seconds = 3
