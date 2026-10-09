"""Discover preformed teams, run their captains, then verified solo play."""
from __future__ import annotations

from pathlib import Path
from dataclasses import replace
from PIL import Image
import time
import uuid
import re
import json
import hashlib
from copy import deepcopy

import hyol_scheduler as hs
from hyol_checkpoints import decode_mask, read_checkpoint
from hyol_daily_tasks import DailyRunner, DEFAULT_SOLO_OPTIONS, SOLO_OPTIONS, LABELS, PAGE_ANCHORS, PAGE_ROIS, DAILY_ROI, ROW_ROI, WANTED_SELECTED_ROI, GROUP_RETURN_ROI
from hyol_diagnostics import manual_pause_status
from hyol_multi_solo import MultiSoloRunner
from hyol_multi_solo import tab_close_positions, active_tab_centers
from hyol_team_six import SixTabScheduler, room_member_assignment

TEAM_TASKS = ('group_instance', 'wanted')
TASK_KEYS = {'group_instance': 'group', 'wanted': 'wanted'}
ROOM = (1200, 260, 1280, 340)
PUBLIC_ROOM = (1360, 250, 1450, 340)
CREATE = (951, 650, 1301, 850)
DISSOLVE = (1060, 315, 1160, 380)
PERSONAL_AVATAR_ROI = (0, 69, 45, 149)
PERSONAL_LEADER_ASSETS = ('team_personal_leader_core.bmp','team_personal_leader_white_core.bmp',
                          'team_personal_leader_text_core_current.bmp')
# These solo receipts are carried unchanged. A new team must still pass the
# main-page and preformed-room checks; its own unresolved actions always block.
INDEPENDENT_PENDING_PREFIXES = ('arena_', 'survival_', 'daily_ranking_', 'ranking_', 'mail_', 'welfare_')


def legacy_wanted_navigation(key, row, day):
    """Only an old named page-entry receipt can be reconciled on that page."""
    return (bool(re.fullmatch(r'team[12]_wanted_participate',key))
            and isinstance(row.get('updated_at'),str) and len(row['updated_at']) >= 10
            and row['updated_at'][:10] < day and isinstance(row.get('origin'),dict)
            and bool(row['origin'].get('token')))


def portrait_leader_score(scheduler,frame):
    return max(float(scheduler.template(asset,PERSONAL_AVATAR_ROI,frame,threshold=.95)[1])
               for asset in PERSONAL_LEADER_ASSETS)


def portrait_leader_present(scheduler, frame):
    return portrait_leader_score(scheduler,frame) >= .95


def daily_page_present(scheduler,frame):
    return ((scheduler.template('daily_practice_anchor.bmp',DAILY_ROI,frame,threshold=.98)[0]
             or scheduler.template('daily_practice_title_core_current.bmp',(1190,140,1420,275),frame,threshold=.98)[0])
            and scheduler.template('daily_practice_close.bmp',(1370,200,1460,280),frame,threshold=.98)[0])


def daily_list_signature(frame):
    """Observe row labels, excluding animated icons and the pointer."""
    if not isinstance(frame, Image.Image):
        return None
    edges = hs.edge_binary(frame.crop((528, 318, 687, 712)))
    return hashlib.sha256(edges.tobytes()).hexdigest()


def wanted_daily_zero(scheduler, frame):
    """Match a completed row without its variable gray background and metadata."""
    if scheduler.template('daily_wanted_completed_zero_current.bmp',
                          (500,310,1090,720),frame,threshold=.98)[0]:
        return True
    ok,_score,x,y = scheduler.template('daily_wanted_zero_label_core_current.bmp',
                                       (510,310,650,720),frame,threshold=.98)
    if not ok:
        return False
    # Counter and completion badge must belong to that same wanted row.
    # The small 0/5 crop makes a nonzero digit materially different.
    return (scheduler.template('daily_wanted_zero_count_core_current.bmp',
                               (x+300,y+5,x+343,y+37),frame,threshold=.98)[0]
            and scheduler.template('daily_wanted_completed_badge_core_current.bmp',
                                   (x+455,y-12,x+535,y+40),frame,threshold=.98)[0])


def same_named_members(current, expected):
    return (len(current) == len(expected) == len(set(current)) == len(set(expected))
            and set(current) == set(expected))


def pipeline_action(record,slot,name):
    attempt = slot.get('tasks',{}).get('wanted',{}).get('attempt_id') if name.startswith('wanted_') else None
    return 'pipeline_'+record['id']+'_'+(('attempt_'+attempt+'_') if attempt else '')+name


def detect_leaders(*, event_sink=None, stop_event=None):
    """Read only tab titles and the active role portrait, never the party list."""
    s = PipelineScheduler(event_sink)
    s.no_content_clicks = True
    lease = hs.ForegroundRuntimeLease()
    if not lease.acquire():
        raise hs.PendingError('已有脚本占用游戏窗口，队长识别不争用控制权')
    result = dict(leaders=[],skipped=[],evidence={},portrait_scores={},content_clicks=0)
    try:
        s.start_f12_monitor()
        s.bind_window(wait=True,timeout_seconds=15)
        first = tab_close_positions(s.capture())
        s.wait_interruptible(.3)
        if first != tab_close_positions(s.capture()):
            raise hs.PendingError('标签位置发生变化，队长识别保持待处理')
        centers = active_tab_centers(first)
        if not 1 <= len(centers) <= 6:
            raise hs.PendingError('队长识别支持1～6个稳定游戏标签')
        s.accounts = [hs.Account(index,f'当前窗口标签{index}',center) for index,center in enumerate(centers,1)]
        s.by_name = {a.name:a for a in s.accounts}
        s.config['TabXs'] = ','.join(map(str,centers))
        for account in s.accounts:
            index = account.index
            if stop_event is not None and stop_event.is_set():
                raise KeyboardInterrupt('用户停止队长识别')
            s.check_stop()
            try:
                frame = s.switch_account(account)
                observations = []
                scores = []
                for sample in range(3):
                    if stop_event is not None and stop_event.is_set():
                        raise KeyboardInterrupt('用户停止队长识别')
                    if s._generic_active_tab_x(frame) != account.tab_x:
                        raise hs.PendingError('队长识别时标签变化，原选择保留')
                    observations.append(portrait_leader_present(s,frame))
                    scores.append(portrait_leader_score(s,frame))
                    if sample < 2:
                        s.wait_interruptible(.35)
                        frame = s.capture()
                result['portrait_scores'][str(index)] = scores
                result['evidence'][str(index)] = str(s.save_raw_evidence(frame,account,'pipeline_portrait_leader',
                                                                       'verified' if all(observations) else 'observed'))
                if all(observations):
                    result['leaders'].append(index)
                elif any(observations):
                    result['skipped'].append(index)
            except hs.AccountOffline:
                result['skipped'].append(index)
        result['content_clicks'] = s.content_click_count
        if result['content_clicks'] != 0:
            raise hs.SafetyError('队长只读识别出现内容输入')
        hs.atomic_json(hs.LOGS/'team_leaders_latest.json',result)
        return result
    finally:
        s.stop.set()
        lease.release()


class PipelineScheduler(SixTabScheduler):
    """Keep the existing identity/foreground guards; never touch another tab to bind."""

    def reassociate_captains(self, expected, completed=()):
        """Map the saved real captains onto current tabs before binding old actions."""
        from hyol_checkpoints import current_identity_mask, joint_identity_assignment, identity_mask_scores
        from hyol_accounts import dismiss_current_final_result
        self.bind_window(wait=True,timeout_seconds=15)
        markers = tab_close_positions(self.capture())
        self.wait_interruptible(.3)
        if markers != tab_close_positions(self.capture()):
            raise hs.PendingError('队长恢复时标签位置变化，保留原断点')
        centers = active_tab_centers(markers)
        self.accounts = [hs.Account(i,f'当前窗口标签{i}',x) for i,x in enumerate(centers,1)]
        self.by_name = {a.name:a for a in self.accounts}
        self.config['TabXs'] = ','.join(map(str,centers))
        refs = read_checkpoint(self.shared_state_directory/'account_identities.json')
        samples = [{},{},{}]
        leaders,evidence = set(),{}
        for account in self.accounts:
            try:
                frame = self.switch_account(account)
                frame = dismiss_current_final_result(self, account, frame)
                masks,badges = [],[]
                for sample in range(3):
                    self.check_stop()
                    if self._generic_active_tab_x(frame) != account.tab_x:
                        raise hs.PendingError('队长恢复观察时当前标签变化')
                    masks.append(current_identity_mask(frame,getattr(self,'templates',None)))
                    badges.append(portrait_leader_present(self,frame))
                    if sample < 2:
                        self.wait_interruptible(.35);frame=self.capture()
                if max(int(m.sum()) for m in masks) < 50:
                    continue
                if min(int(m.sum()) for m in masks) < 50 or any(hs.mask_f1(masks[0],m) < .92 for m in masks[1:]):
                    raise hs.PendingError('队长恢复三帧实名不一致，保留原断点')
                if any(max(identity_mask_scores(m,refs).values(),default=0) < .92 for m in masks):
                    continue
                for rows,mask in zip(samples,masks):rows[account.index] = mask
                if all(badges):leaders.add(account.index)
                evidence[account.index] = str(self.save_raw_evidence(frame,account,'pipeline_captain_position','verified'))
            except hs.AccountOffline:
                continue
        mappings = [joint_identity_assignment(rows,refs)[0] for rows in samples]
        if not mappings[0] or any(mapping != mappings[0] for mapping in mappings[1:]):
            raise hs.PendingError('队长恢复三帧联合实名未唯一确认，保留原断点')
        physical = {name:index for index,name in mappings[0].items()}
        located = {slot:physical[name] for slot,name in expected.items() if name in physical
                   and (physical[name] in leaders or slot in completed)}
        if len(set(located.values())) != len(located):
            raise hs.PendingError('原队长映射重叠，保留原断点')
        positions = {slot:centers[index-1] for slot,index in located.items()}
        remaining = [x for x in centers if x not in positions.values()]
        for slot in range(1,len(centers)+1):
            if slot not in positions:positions[slot]=remaining.pop(0)
        result = dict(physical_tabs=located,positions=positions,identity_mapping=mappings[0],
                      evidence=evidence,unavailable=sorted(set(expected)-set(located)))
        self.event('pipeline_captains_reassociated',mapping=located,evidence=evidence,
                   detail='原实名队长三帧已核实；仅恢复当前标签位置，原批次及场次保留')
        return result

    def switch_account(self, account, verify=True):
        prior = getattr(self,'_capture_account',None)
        self._capture_account = account
        try:
            frame = None
            if (getattr(self,'active',None) == account
                    and account.index in getattr(self,'_tab_title_masks',{})):
                current = self.capture()
                if self._generic_active_tab_x(current) == account.tab_x:
                    frame = self._observe_selected_account(account,current)
            if frame is None:
                frame = super().switch_account(account, verify)
            if self.offline_dialog_visible(frame):
                evidence = str(self.save_raw_evidence(frame,account,'pipeline_offline','pending'))
                raise hs.AccountOffline(account,evidence)
            return self.clear_pipeline_interference(account, frame)
        except hs.PendingError as exc:
            if '需要登录' in str(exc):
                raise hs.AccountOffline(account, str(exc)) from exc
            raise
        finally:
            self._capture_account = prior

    def clear_pipeline_interference(self, account, frame):
        """Close only the recognized X the user approved as a task interference."""
        if getattr(self, 'no_content_clicks', False) or not isinstance(frame, Image.Image):
            return frame
        room = ('team_room_close.bmp',ROOM)
        if (self.template(*room,frame,threshold=.98)[0]
                and any(self.template(asset,roi,frame,threshold=.98)[0] for asset,roi in (
                    ('team_dissolve_button.bmp',DISSOLVE),('team_create_button.bmp',CREATE)))):
            before = str(self.save_raw_evidence(frame,account,'pipeline_residual_team_room','before'))
            if not self.click_template(account,room[0],room[1],20,20,'pipeline_interference_team_room_close',
                                       [room[0],'team_dissolve_button.bmp','team_create_button.bmp'],
                                       threshold=.98,anchor_threshold=.98):
                raise hs.PendingError('残留队伍房间关闭前变化')
            if not self.wait_absent(*room,5,threshold=.98):
                raise hs.PendingError('残留队伍房间原关闭尚未确认')
            frame = self.capture()
            evidence = str(self.save_raw_evidence(frame,account,'pipeline_residual_team_room','verified'))
            self.event('pipeline_interference_closed',account,before_evidence=before,evidence=evidence,
                       detail='关闭用户授权的残留队伍窗口，未解散或改变成员')
        anchor = 'pipeline_spirit_reward_text_current.bmp'
        close = 'pipeline_spirit_reward_close_current.bmp'
        anchor_roi, close_roi = (940,425,1200,545), (1195,408,1250,465)
        if not self.template(anchor, anchor_roi, frame, threshold=.98)[0]:
            return frame
        from hyol_navigation import move_cursor_clear
        move_cursor_clear(self)
        for sample in range(3):
            self.check_stop()
            frame = self.capture()
            if not (self.template(anchor, anchor_roi, frame, threshold=.98)[0]
                    and self.template(close, close_roi, frame, threshold=.98)[0]):
                raise hs.PendingError('干扰弹窗或专用关闭按钮变化，重新核验')
            if sample < 2:
                self.wait_interruptible(.2)
        before = str(self.save_raw_evidence(frame, account, 'pipeline_interference', 'before'))
        if not self.click_template(account, close, close_roi, 15,16,
                                   'pipeline_interference_spirit_reward_close', [anchor],
                                   threshold=.98, anchor_threshold=.98):
            raise hs.PendingError('已识别干扰弹窗关闭前变化')
        if not self.wait_absent(anchor, anchor_roi, 5, threshold=.98):
            raise hs.PendingError('干扰弹窗原关闭已发送，不重复点击')
        frame = self.capture()
        evidence = str(self.save_raw_evidence(frame, account, 'pipeline_interference', 'verified'))
        self.event('pipeline_interference_closed', account, before_evidence=before,
                   evidence=evidence, detail='已关闭用户授权的通灵兽奖励干扰弹窗，不计入所选任务完成')
        return frame


class LeaderActivityRunner(DailyRunner):
    """One visual transition per tick. Attempts, deadlines and intent are durable."""

    def __init__(self, owner, account, slot):
        self.owner, self.scheduler, self.account, self.slot = owner, owner.scheduler, account, slot
        self.records = slot.setdefault('tasks', {})
        self.formation = owner.formation
        self.formation_checked = True  # Preformed teams are prepared by the user.
        self.track_friendship = False
        self.prefix = 'pipeline_' + owner.record['id'] + '_'
        self.samples = {}
        self.next_poll_seconds = .6

    def save(self):
        self.owner.save()

    def group_rewards(self, frame):
        s = self.scheduler
        if not self.visible('group_friendship_reward_label_current.bmp',(775,775,925,810),frame,.98):
            raise hs.PendingError('副本友情奖励次数标签未确认，不能忽略另一奖励池')
        spec = hs.TEAM_ACTIVITY_BY_KEY['group_instance']
        daily = s._read_team_reward_remaining(self.account,spec,frame)
        friend = s._read_team_reward_remaining(self.account,replace(
            spec,key='group_friendship',label='组队副本友情',reward_counter_roi=(918,775,952,810)),frame)
        return daily,friend

    def recover_group_counter(self, rec):
        """Upgrade a running old counter only from its own pre-click evidence."""
        if rec.get('counter_scope') == 'daily_and_friendship':
            return
        label = self.action(f'group_{rec["completed"]+1}_select')
        if self.scheduler.state.action_status(self.account.name,label) != 'done':
            raise hs.PendingError('原副本选择未核实，不能补记友情奖励次数')
        directory = hs.LOGS/'scheduler_evidence'/self.scheduler.state.day/hs.slug(self.account.name)
        candidates = list(directory.glob('*_before_'+label+'.jpg'))
        if not candidates:
            raise hs.PendingError('原场次缺少友情次数的开战前实拍，保留原场次')
        counts = set()
        for path in candidates:
            with Image.open(path) as image:
                frame = image.convert('RGB')
            if (not self.scheduler.verify_identity(self.account,frame)[0]
                    and not self.archived_click_owner(path,label)):
                raise hs.PendingError('原副本开战前实拍身份未确认')
            counts.add(self.group_rewards(frame))
        if len(counts) != 1:
            raise hs.PendingError('原副本开战前奖励次数实拍存在冲突')
        daily,friend = counts.pop()
        if daily != rec.get('reward_before'):
            raise hs.PendingError('原日常次数与开战前实拍不一致')
        rec.update(daily_before=daily,friend_before=friend,reward_before=daily+friend,
                   counter_scope='daily_and_friendship',counter_before_evidence=[str(p) for p in candidates])
        self.save()
        self.event('pipeline_group_counter_recovered',daily=daily,friendship=friend,
                   detail='按原场次开战前实拍补齐友情次数，未重新入场')

    def archived_click_owner(self, evidence, label):
        # Old before-click JPEGs can lose the small dimmed server glyph. Only
        # their exact original journal, preceded by a positive identity bind,
        # can associate that archive; this never authorizes a live input.
        day = self.scheduler.state.day.replace('-','')
        for path in hs.LOGS.glob('HYOL_Scheduler_'+day+'_*.jsonl'):
            mapping = {}
            for line in path.read_text(encoding='utf-8').splitlines():
                try:
                    row = json.loads(line)
                except ValueError:
                    continue
                if row.get('event') in ('generic_team_joint_identity_verified','generic_team_checkpoints_bound'):
                    mapping = row.get('mapping',mapping)
                if (row.get('event') == 'content_click' and row.get('target') == label
                        and row.get('account') == self.account.name and row.get('tab_index') == self.account.index
                        and row.get('hwnd') == self.scheduler.hwnd and row.get('evidence')
                        and Path(row['evidence']).resolve() == evidence.resolve()
                        and mapping.get(str(self.account.index)) == self.slot.get('owner')):
                    return True
        return False

    def action(self, name):
        return pipeline_action(self.owner.record,self.slot,name)

    def click(self, name, roi, dx, dy, label, anchors, irreversible=False,
              threshold=.90, anchor_threshold=.90):
        return super().click(name, roi, dx, dy, self.action(label), anchors,
                             irreversible, threshold, anchor_threshold)

    def send(self, asset, roi, dx, dy, label, *, irreversible=True):
        action = self.action(label)
        if irreversible and self.scheduler.state.action_status(self.account.name, action) != 'new':
            raise hs.PendingError('原操作结果未核实，不重复发送：' + label)
        self.click(asset, roi, dx, dy, label, [asset], irreversible)

    def absent_intent(self, rec, frame):
        intent = rec.get('intent')
        if not intent:
            return False
        action = self.action(intent['label'])
        if self.scheduler.state.action_status(self.account.name,action) == 'new':
            raise hs.PendingError('原操作没有发送凭据，保留意图，不补记结果：' + intent['label'])
        if self.visible(intent['asset'], tuple(intent['roi']), frame):
            if time.time() >= intent['deadline']:
                raise hs.PendingError('原按钮未确认消失，保留操作：' + intent['label'])
            return True
        self.scheduler.commit_action(self.account, action, '原匹配按钮已确认消失')
        rec.pop('intent')
        rec.update(phase='running', last_progress=time.time())
        self.save()
        return True

    def send_once(self, rec, asset, roi, dx, dy, label):
        # Save intent before sending. A restart observes it, never sends it again.
        rec['intent'] = dict(asset=asset, roi=list(roi), label=label, deadline=time.time()+12)
        self.save()
        self.send(asset, roi, dx, dy, label)

    def cancel_rejected_group_entry(self, rec, frame):
        """A positively refused selection can be cancelled without a battle."""
        intent = rec.get('intent', {})
        pattern = rf'group_{rec["completed"]+1}_select(?:_retry[1-3])?'
        if (not re.fullmatch(pattern, intent.get('label', ''))
                or intent.get('asset') != 'team_instance_hard_card.bmp'
                or self.scheduler.state.action_status(self.account.name, self.action(intent['label'])) != 'pending'):
            raise hs.PendingError('已有队伍无法继续；原选择无法关联，该队整体待续')
        if not self.visible('group_solo_cancel_current.bmp', (960,530,1100,600), frame, .98):
            raise hs.PendingError('队伍未满提示取消按钮未确认，该队整体待续')
        rec.setdefault('entry_rejection', dict(selection=dict(intent),
            before_evidence=str(self.scheduler.save_raw_evidence(frame,self.account,'pipeline_group_entry_rejected','before'))))
        self.save()
        self.click('group_solo_cancel_current.bmp',(960,530,1100,600),50,17,
                   intent['label']+'_cancel_rejected',['group_solo_warning_current.bmp'],
                   threshold=.98,anchor_threshold=.95)
        if not self.scheduler.wait_absent('group_solo_warning_current.bmp',(760,480,1150,540),5,threshold=.95):
            raise hs.PendingError('原入场取消已发送，尚未确认关闭，该队整体待续')
        rec.update(phase='rejected_entry',last_progress=time.time())
        self.save()

    def reconcile_rejected_group_entry(self, rec, frame):
        s = self.scheduler
        rejected = rec.get('entry_rejection')
        if (not rejected or not self.visible(PAGE_ANCHORS['group'],PAGE_ROIS['group'],frame,.98)
                or not s.verify_identity(self.account,frame)[0]):
            raise hs.PendingError('原被拒入场的副本页面或实名未确认，保留原选择')
        daily,friend = self.group_rewards(frame)
        if (daily,friend) != (rec.get('daily_before'),rec.get('friend_before')):
            raise hs.PendingError('原被拒入场后的奖励次数变化，结果仍未决')
        self.samples['entry_rejection'] = self.samples.get('entry_rejection',0)+1
        if self.samples['entry_rejection'] < 3:
            return
        evidence = str(s.save_raw_evidence(frame,self.account,'pipeline_group_entry_rejected','verified'))
        selection = rejected['selection']
        if rec.get('intent') != selection:
            raise hs.PendingError('原被拒入场的选择凭据变化，保留原选择')
        s.commit_action(self.account,self.action(selection['label']),
                        '队伍未满入场已取消，三帧双奖励池未变化；没有开战或新增场次：'+evidence)
        rejected.update(evidence=evidence,daily_remaining=daily,friendship_remaining=friend,
                        outcome='rejected_without_battle',at=hs.now_iso())
        rec.setdefault('entry_rejections',[]).append(rec.pop('entry_rejection'))
        rec.pop('intent')
        rec.update(phase='verify_members',last_progress=time.time())
        self.save()
        self.event('pipeline_group_entry_reconciled',detail='原缺员入场取消，次数不变；重新核验原队伍',evidence=evidence)

    def scene_transition(self, frame):
        from hyol_treasure import loading_visible, AUTO_PATH_ROI, MAP_LOADING_ROI
        return (loading_visible(self,frame)
                or (self.visible('map_loading_word_current.bmp',MAP_LOADING_ROI,frame,.98)
                    and self.visible('map_loading_npc_path_current.bmp',MAP_LOADING_ROI,frame,.98))
                or self.visible('treasure_auto_path_running.bmp',AUTO_PATH_ROI,frame,.98))

    def wanted_task_present(self, frame):
        return any(self.visible(name,(1715,480,1910,550),frame,.98) for name in (
            'wanted_task_prefix_current.bmp','wanted_task_prefix_live.bmp'))

    def daily_page_present(self,frame):
        return ((self.visible('daily_practice_anchor.bmp',DAILY_ROI,frame,.98)
                 or self.visible('daily_practice_title_core_current.bmp',(1190,140,1420,275),frame,.98))
                and self.visible('daily_practice_close.bmp',(1370,200,1460,280),frame,.98))

    def select_daily_row(self, nav, row, label):
        if nav.get('row_selection_count',0) >= 3:
            raise hs.PendingError('活动行已点选三次，详情尚未确认；保留入口断点，不重复点选')
        self.send(row,ROW_ROI,45,15,label+'_row',irreversible=False)
        nav['row_selection_count'] = nav.get('row_selection_count',0)+1
        nav.update(last_progress_at=time.time(),deferred_at_progress=nav.get('deferred_seconds',0))
        self.save()

    def observe_existing_wanted(self, rec, frame):
        s = self.scheduler
        if (not s.main_ready(frame) or not s.verify_identity(self.account,frame)[0]
                or not self.wanted_task_present(frame)
                or not self.visible('treasure_auto_path_running.bmp',(800,140,1100,200),frame,.98)):
            return False
        nav = rec.get('navigation',{})
        started = min(nav.get('started_at',time.time()),rec.get('last_progress',time.time()))
        # This is observation of a chain already running in the game. Its
        # original navigation clock is retained; no Auto Complete is sent.
        rec.setdefault('deadline_at',started+1200)
        rec.update(phase='running',observed_existing_chain=True,
                   running_evidence=str(s.save_raw_evidence(frame,self.account,'pipeline_wanted_running','observed')))
        rec.pop('navigation',None)
        self.save()
        self.event('pipeline_wanted_observed',detail='接续观察已在运行的通缉链；不重复启动自动完成')
        return True

    def navigate(self, task, rec, frame):
        s = self.scheduler
        nav = rec.setdefault('navigation', {'phase': 'open', 'started_at': time.time(), 'scrolls': 0})
        label = f'{task}_{rec.get("completed", 0)}_entry'
        if self.visible(PAGE_ANCHORS[task], PAGE_ROIS[task], frame):
            action = self.action(label + '_participate')
            if s.state.action_status(self.account.name, action) == 'pending':
                sample_key = task+'_entry_arrived'
                self.samples[sample_key] = self.samples.get(sample_key,0)+1
                if self.samples[sample_key] < 3:
                    return
                s.commit_action(self.account, action, '活动页已正向出现')
            rec.pop('navigation', None)
            rec.update(phase='ready', last_progress=time.time())
            self.save()
            return
        deferred = max(0,nav.get('deferred_seconds',0)-nav.get('deferred_at_progress',0))
        if time.time()-nav.get('last_progress_at',nav['started_at'])-deferred >= 60:
            raise hs.PendingError('活动入口60秒未确认，保留原参与动作')
        if self.scene_transition(frame):
            self.next_poll_seconds = 6
            return  # Loading and automatic movement never authorize a page click.
        if task == 'wanted' and self.visible('wanted_available_auto_current.bmp', (1650,450,1910,560), frame):
            if nav.get('route_requested_at'):
                self.next_poll_seconds = 3
                return
            nav.update(phase='arriving',route_requested_at=time.time())
            self.save()
            self.send('wanted_available_auto_current.bmp', (1650,450,1910,560), 108,15,
                      label+'_navigate', irreversible=False)
            return
        if nav['phase'] == 'arriving':
            self.next_poll_seconds = 3
            return
        if not daily_page_present(s, frame):
            if not s.main_ready(frame):
                if not nav.get('unknown_page_evidence'):
                    nav['unknown_page_evidence'] = str(s.save_raw_evidence(
                        frame,self.account,'pipeline_entry_transition','observed'))
                    self.save()
                self.next_poll_seconds = 3
                return  # A render/foreground transition gets the bounded wait.
            self.send('daily_practice_toolbar.bmp', (651,69,851,169),20,20,label+'_open',irreversible=False)
            nav.update(last_progress_at=time.time(),deferred_at_progress=nav.get('deferred_seconds',0))
            self.save()
            return
        tab = 'limited' if task == 'group' else 'normal'
        selected = f'daily_{tab}_tab_selected.bmp'
        if not self.visible(selected,(510,250,800,310),frame):
            asset = f'daily_{tab}_tab_current.bmp'
            if self.visible(asset,(510,250,800,310),frame):
                self.send(asset,(510,250,800,310),40,15,label+'_tab',irreversible=False)
                nav.update(last_progress_at=time.time(),deferred_at_progress=nav.get('deferred_seconds',0))
                self.save()
            return
        detail = 'daily_wanted_selected_current.bmp' if task == 'wanted' else 'daily_group_detail_current.bmp'
        roi = WANTED_SELECTED_ROI if task == 'wanted' else (1090,310,1220,350)
        if self.visible(detail,roi,frame):
            participate = (1051,569,1351,819)
            if self.visible('daily_participate_disabled_current.bmp',participate,frame,.98):
                raise hs.PendingError('活动未开放或不可参与，队伍保持待续')
            nav['phase'] = 'arriving'
            nav.update(last_progress_at=time.time(),deferred_at_progress=nav.get('deferred_seconds',0))
            self.save()
            # This button opens the activity selection page. Battle selection,
            # auto-start, claims and dissolution retain irreversible intents.
            self.send('daily_participate.bmp',participate,51,18,label+'_participate',irreversible=False)
            return
        row = f'daily_{task}_current_row.bmp'
        if self.visible(row,ROW_ROI,frame):
            self.select_daily_row(nav,row,label)
            return
        # A hovered arrow changes appearance. Clear the pointer and resample
        # before searching; an observation is never counted as a scroll.
        from hyol_navigation import move_cursor_clear
        move_cursor_clear(s)
        s.wait_interruptible(.15)
        frame = s.capture()
        if not daily_page_present(s, frame):
            self.next_poll_seconds = 3
            return
        if self.visible(row, ROW_ROI, frame):
            self.select_daily_row(nav,row,label)
            return
        signature = daily_list_signature(frame)
        pending_scroll = nav.get('pending_scroll')
        if pending_scroll:
            if signature is None:
                raise hs.PendingError('日常列表滚动后的画面未核实')
            if signature == pending_scroll['before']:
                pending_scroll['unchanged'] = pending_scroll.get('unchanged', 0)+1
                if pending_scroll['unchanged'] < 2:
                    self.save()
                    return
                if pending_scroll['direction'] == 'down':
                    raise hs.PendingError('日常列表已扫描至底，未找到所选活动')
                nav['scroll_direction'] = 'down'
            else:
                nav['scrolls'] += 1
                nav.update(last_progress_at=time.time(), deferred_at_progress=nav.get('deferred_seconds', 0))
            nav.pop('pending_scroll')
            self.save()
        if nav['scrolls'] >= 28 or nav.get('scroll_attempts', 0) >= 32:
            raise hs.PendingError('日常列表未找到所选活动')
        direction = nav.get('scroll_direction', 'up')
        arrow_roi = (1060,310,1095,350) if direction == 'up' else (1060,660,1095,720)
        arrow = f'daily_scroll_{direction}_current.bmp'
        if self.visible(arrow,arrow_roi,frame):
            if signature is None:
                raise hs.PendingError('日常列表画面未核实，不发送滚动')
            self.send(arrow,arrow_roi,10,10,label+'_scroll',irreversible=False)
            nav['scroll_attempts'] = nav.get('scroll_attempts', 0)+1
            nav['pending_scroll'] = dict(before=signature, direction=direction, unchanged=0)
            move_cursor_clear(s)
        self.save()

    def finish_task(self, task, rec):
        s = self.scheduler
        if task == 'group' and self.visible(PAGE_ANCHORS[task],PAGE_ROIS[task]):
            self.close_page(task)
            return
        from hyol_navigation import return_to_leaf
        return_to_leaf(s,self.account,allow_pending_navigation=True)
        members, evidence = self.owner.snapshot(self.account)
        if not same_named_members(members,self.slot['members']):
            raise hs.PendingError('结束时队伍成员改变，该队整体待续')
        rec.update(status='done', task_completed=True, home_returned=True, phase='done',
                   evidence=evidence, finished_at=hs.now_iso(), evidence_scope='captain')
        self.save()

    def tick(self, task):
        s, rec = self.scheduler, self.record(task)
        self.next_poll_seconds = .6
        f = s.switch_account(self.account)
        if task == 'group' and self.visible('group_solo_warning_current.bmp',(760,480,1150,540),f,.95):
            self.cancel_rejected_group_entry(rec,f)
            return
        if self.visible('wanted_solo_warning_current.bmp',(780,500,1135,570),f,.95):
            raise hs.PendingError('已有队伍无法继续；不确认单人参战，该队整体待续')
        if task == 'group' and rec.get('phase') == 'rejected_entry':
            self.reconcile_rejected_group_entry(rec,f)
            return
        if rec.get('phase') == 'finishing':
            self.finish_task(task,rec)
            return
        if rec.get('phase') == 'verify_members':
            if self.visible(PAGE_ANCHORS[task],PAGE_ROIS[task],f):
                self.close_page(task)
                return
            members,evidence = self.owner.snapshot(self.account)
            if not same_named_members(members,self.slot['members']):
                raise hs.PendingError('下一场开始前成员改变，该队整体待续')
            rec.update(phase='ready',last_progress=time.time())
            self.save()
            return
        if self.absent_intent(rec,f):
            return
        if (task == 'wanted' and rec.get('phase','ready') == 'ready'
                and self.daily_page_present(f)
                and wanted_daily_zero(s,f)):
            if not s.verify_identity(self.account,f)[0]:
                raise hs.PendingError('通缉完成行可见，但队长实名未确认')
            self.samples['wanted_daily_zero'] = self.samples.get('wanted_daily_zero',0)+1
            if self.samples['wanted_daily_zero'] >= 3:
                rec.update(phase='checking_empty',daily_remaining=0,
                           daily_zero_evidence=str(s.save_raw_evidence(f,self.account,'pipeline_wanted_daily_zero','verified')))
                rec.setdefault('deadline_at',time.time()+1200)
                rec.pop('navigation',None)
                self.save()
            return
        self.samples['wanted_daily_zero'] = 0
        if task == 'wanted' and rec.get('phase','ready') == 'ready' and self.observe_existing_wanted(rec,f):
            self.next_poll_seconds = 10
            return
        if rec.get('phase','ready') == 'ready' or 'navigation' in rec:
            if 'navigation' in rec or not self.visible(PAGE_ANCHORS[task],PAGE_ROIS[task],f):
                self.samples['group_zero'] = 0
                self.navigate(task,rec,f)
                return
            if task == 'group':
                daily,friend = self.group_rewards(f)
                remaining = daily+friend
                if remaining == 0:
                    self.samples['group_zero'] = self.samples.get('group_zero',0)+1
                    if self.samples['group_zero'] >= 3:
                        evidence = str(s.save_raw_evidence(f,self.account,'pipeline_group_reward_zero','verified'))
                        rec.update(phase='finishing',reward_exhausted=True,zero_evidence=evidence,
                                   daily_remaining=daily,friendship_remaining=friend)
                        self.save()
                    return
                self.samples['group_zero'] = 0
                rec.update(phase='running',reward_before=remaining,daily_before=daily,friend_before=friend,
                           counter_scope='daily_and_friendship',last_progress=time.time())
                self.save()
                label = f'group_{rec["completed"]+1}_select'
                retries = sum(r['selection']['label'].startswith(label) for r in rec.get('entry_rejections',[]))
                if retries > 3:
                    raise hs.PendingError('副本缺员入场反复被拒，保留队伍待续')
                if retries:
                    label += f'_retry{retries}'
                self.send_once(rec,'team_instance_hard_card.bmp',(550,300,950,650),200,130,label)
            else:
                if rec.get('deadline_at') is not None and time.time() >= rec['deadline_at']:
                    raise hs.PendingError('通缉原1200秒截止已到，不重新启动自动完成')
                legacy = [key for key in self.slot.get('legacy_navigation',[])
                          if s.state.action_status(self.account.name,key) == 'pending']
                if legacy:
                    self.samples['legacy_wanted_page'] = self.samples.get('legacy_wanted_page',0)+1
                    if self.samples['legacy_wanted_page'] >= 3:
                        evidence = str(s.save_raw_evidence(f,self.account,'pipeline_legacy_wanted_page','verified'))
                        for key in legacy:
                            s.commit_action(self.account,key,'当前通缉页连续三帧正向核实原导航；未计战斗或奖励：'+evidence)
                        self.slot['legacy_navigation_evidence'] = evidence
                        self.save()
                    return
                rec.update(phase='auto_pending',last_progress=time.time())
                rec.setdefault('deadline_at',time.time()+1200)
                self.save()
                self.send_once(rec,'wanted_team_auto.bmp',(1050,300,1300,500),58,17,'wanted_auto')
            return
        if task == 'wanted':
            if rec.get('phase') == 'checking_empty' and self.daily_page_present(f):
                self.click('daily_practice_close.bmp',(1360,210,1470,285),20,20,
                           'wanted_finished_daily_close',['daily_practice_anchor.bmp','daily_practice_title_core_current.bmp'],
                           threshold=.98,anchor_threshold=.98)
                return
            if (rec.get('observed_existing_chain') and not rec.get('intent')
                    and self.visible(PAGE_ANCHORS[task],PAGE_ROIS[task],f,.98)
                    and self.visible('wanted_team_close.bmp',PAGE_ROIS[task],f,.98)):
                self.samples['wanted_catalogue'] = self.samples.get('wanted_catalogue',0)+1
                if self.samples['wanted_catalogue'] >= 3:
                    rec.update(phase='ready',observed_existing_chain=False)
                    self.save()
                return
            self.samples['wanted_catalogue'] = 0
            if (s.main_ready(f) and hs.wanted_task_list_is_empty(f) and not self.scene_transition(f)
                    and not self.wanted_task_present(f)):
                self.samples[task] = self.samples.get(task,0)+1
                if self.samples[task] >= 5:
                    evidence = str(s.save_raw_evidence(f,self.account,'pipeline_wanted_empty','verified'))
                    rec.update(phase='finishing',completed=1,empty_evidence=evidence)
                    self.save()
                return  # A late, positive empty list is reconciled without starting a new chain.
            self.samples[task] = 0
            if time.time() >= rec['deadline_at']:
                raise hs.PendingError('通缉原1200秒截止已到，任务列表尚未核实清空')
            known_progress = (s._battle_auto_on_visible(f)
                or self.visible('battle_auto_off.bmp',hs.BATTLE_AUTO_ROI,f)
                or self.scene_transition(f) or self.wanted_task_present(f))
            if known_progress:
                rec['last_progress'] = time.time()
                self.save()
            elif time.time()-rec.get('last_progress',time.time()) >= 60:
                raise hs.PendingError('通缉60秒未确认活动进展，保留原自动操作和截止时间')
            if self.visible('battle_auto_off.bmp',hs.BATTLE_AUTO_ROI,f):
                rec['auto_sequence'] = rec.get('auto_sequence',0)+1
                self.send_once(rec,'battle_auto_off.bmp',hs.BATTLE_AUTO_ROI,29,29,
                               f'wanted_battle_auto_{rec["auto_sequence"]}')
            else:
                self.next_poll_seconds = 10
            return
        battle = rec['completed']+1
        if self.visible(PAGE_ANCHORS['group'],PAGE_ROIS['group'],f):
            self.recover_group_counter(rec)
            daily,friend = self.group_rewards(f)
            remaining = daily+friend
            if remaining == rec['reward_before']-1:
                self.samples['group'] = self.samples.get('group',0)+1
                if self.samples['group'] >= 3:
                    rec.update(completed=battle,phase='verify_members',last_progress=time.time())
                    self.samples['group'] = 0
                    self.save()
                    self.event('pipeline_battle_complete',task='group',completed=battle,remaining=remaining,
                               daily_remaining=daily,friendship_remaining=friend,
                               detail=f'副本第{battle}场结算；本日剩余{daily}，友情剩余{friend}')
                return
            self.samples['group'] = 0
        else:
            self.samples['group'] = 0
        result = s._team_result_template(f)
        reward = s._team_reward_result_visible(f)
        if self.visible('group_return_game_current.bmp',GROUP_RETURN_ROI,f,.90):
            self.send_once(rec,'group_return_game_current.bmp',GROUP_RETURN_ROI,45,20,f'group_{battle}_return')
        elif result or reward:
            name = result or hs.TEAM_REWARD_RESULT_TEMPLATE
            roi = hs.TEAM_BATTLE_RESULT_ROI if result else hs.TEAM_REWARD_RESULT_ROI
            self.send_once(rec,name,roi,55 if result else 64,18 if result else 19,f'group_{battle}_'+('result' if result else 'reward'))
        elif s._team_battle_start_visible(f):
            self.send_once(rec,hs.TEAM_BATTLE_START_TEMPLATE,hs.TEAM_BATTLE_START_ROI,65,23,f'group_{battle}_start')
        elif self.visible('team_leader_final_start.bmp',(650,400,1250,850),f,.90):
            self.send_once(rec,'team_leader_final_start.bmp',(650,400,1250,850),55,18,f'group_{battle}_final')
        elif self.visible('battle_auto_off.bmp',hs.BATTLE_AUTO_ROI,f):
            self.send_once(rec,'battle_auto_off.bmp',hs.BATTLE_AUTO_ROI,29,29,f'group_{battle}_auto')
        elif s._battle_auto_on_visible(f):
            rec['last_progress'] = time.time()
            self.next_poll_seconds = 8
            self.save()
        elif time.time()-rec['last_progress'] >= 60:
            raise hs.PendingError('副本60秒未确认进展，保留原场次')
        else:
            self.next_poll_seconds = 2


class TeamPipelineRunner:
    def __init__(self, leaders=(), tasks=TEAM_TASKS, solo_tasks=DEFAULT_SOLO_OPTIONS,
                 *, arena_wins=5, formation='main', managed_enabled=True, event_sink=None, root=hs.ROOT,
                 new_request=False):
        if (len(leaders) > 6 or len(set(leaders)) != len(leaders)
                or any(type(slot) is not int or slot not in range(1,7) for slot in leaders)):
            raise ValueError('队长标签须互不重复；留空可自动识别')
        if not tasks or len(set(tasks)) != len(tasks) or any(t not in TEAM_TASKS for t in tasks):
            raise ValueError('双队长衔接仅支持副本和通缉')
        if not solo_tasks or len(set(solo_tasks)) != len(solo_tasks) or any(t not in SOLO_OPTIONS for t in solo_tasks):
            raise ValueError('请至少选择一项有效的后续单人任务')
        if type(arena_wins) is not int or not 1 <= arena_wins <= hs.ARENA_MAX_ATTEMPTS:
            raise ValueError('竞技场追加胜场须为1～10')
        if formation not in ('main','fast') or type(managed_enabled) is not bool:
            raise ValueError('阵容或最后托管选项无效')
        if type(new_request) is not bool:
            raise ValueError('新批次选项必须为开启或关闭')
        self.new_request = new_request
        self.leaders = tuple(leaders)
        self.tasks = tuple(t for t in TEAM_TASKS if t in tasks)
        self.solo_tasks, self.arena_wins, self.formation = tuple(solo_tasks), arena_wins, formation
        self.managed_enabled, self.root, self.sink = managed_enabled, Path(root), event_sink
        self.scheduler = PipelineScheduler(event_sink)
        self.scheduler.command_kind = 'team_pipeline'
        self.scheduler.config['AllowMarketPurchase'] = '0'
        self.path = self.scheduler.shared_state_directory / 'team_pipeline_latest.json'
        self.record = {}
        self.drivers = {}
        self._poll_clock = time.monotonic
        self.solo = None
        self.solo_exclusions = {}

    def save(self):
        self.record['updated_at'] = hs.now_iso()
        hs.atomic_json(self.path,self.record)

    def event(self, name, account=None, **fields):
        self.scheduler.event(name,account,**fields)

    def request_key(self):
        return dict(leaders=list(self.leaders),team_tasks=list(self.tasks),solo_tasks=list(self.solo_tasks),
                    arena_wins=self.arena_wins,formation=self.formation,final_managed=self.managed_enabled)

    def restore(self):
        saved = read_checkpoint(self.path)
        key = self.request_key()
        if getattr(self, 'new_request', False) and saved:
            original = self.path.read_bytes()
            identifier = saved.get('id')
            if not isinstance(identifier, str) or not re.fullmatch('[0-9a-f]{32}', identifier):
                raise hs.PendingError('原双队长批次编号无法核验，不覆盖原断点')
            archive = self.path.parent/'team_pipeline_archive'/(
                identifier+'_'+hashlib.sha256(original).hexdigest()[:16]+'.json')
            archive.parent.mkdir(parents=True, exist_ok=True)
            if archive.exists():
                if archive.read_bytes() != original:
                    raise hs.PendingError('原双队长批次归档冲突，不覆盖原断点')
            else:
                with archive.open('xb') as handle:
                    handle.write(original)
            self.previous_request = dict(id=identifier, archive=str(archive), status=saved.get('phase'),
                                         source_day=saved.get('source_day'))
            self.event('pipeline_previous_request_preserved', detail='新批次已明确请求；原任务及未决动作保留',
                       archive=str(archive))
            saved = {}
        if saved:
            if saved.get('request') != key:
                if saved.get('phase') != 'done':
                    raise hs.PendingError('已有双队长流程未结束，先恢复原队长和任务选择')
                saved = {}
            elif saved.get('phase') == 'done' and saved.get('execution_day',saved.get('source_day')) != self.scheduler.state.day:
                saved = {}
            elif saved.get('phase') != 'done':
                prior = saved.get('active_session_token',saved.get('session_token'))
                day_changed = saved.get('execution_day',saved.get('source_day')) != self.scheduler.state.day
                if prior != self.scheduler.session_token or day_changed:
                    from hyol_accounts import session_is_alive
                    from hyol_history import archive_bytes, session_started_at
                    if (self.scheduler.config.get('HistoricalPendingPolicy') != 'isolate_inactive'
                            or session_started_at(prior) is None or session_is_alive(prior)
                            or session_started_at(self.scheduler.session_token) is None
                            or session_started_at(prior) >= session_started_at(self.scheduler.session_token)):
                        raise hs.PendingError('原游戏会话仍活跃或起点无法核实，保留跨日断点，不重新开战')
                    confirmed = {index:slot['owner'] for index,slot in saved.get('teams',{}).items()
                                 if slot.get('owner') and slot.get('owner') ==
                                 self.scheduler._bound_identity_names.get(int(index))}
                    if not confirmed:
                        raise hs.PendingError('跨会话恢复的原队长未连续确认，保留断点')
                    archive = archive_bytes(self.path,self.path.parent)
                    saved.setdefault('resume_history',[]).append(dict(
                        previous_session=prior,execution_day=self.scheduler.state.day,archive=archive,
                        outcome='historical_unknown' if day_changed else 'resume_same_day',
                        confirmed_captains=confirmed))
                    for slot in saved['teams'].values():
                        if slot.get('status') != 'done':
                            prior_need = slot.get('needs_inactive_resume',{})
                            slot['needs_inactive_resume'] = dict(
                                cross_day=day_changed or prior_need.get('cross_day',False),
                                archive=prior_need.get('archive',archive))
                    saved['active_session_token'] = self.scheduler.session_token
                    saved['execution_day'] = self.scheduler.state.day
        self.record = saved or dict(id=uuid.uuid4().hex,request=key,source_day=self.scheduler.state.day,
                                   session_token=self.scheduler.session_token,
                                   phase='teams',teams={str(i):dict(status='new',tasks={}) for i in self.leaders})
        self.record.setdefault('execution_day',self.record['source_day'])
        if not saved:
            self.record['required_account_count'] = len(self.scheduler._bound_identity_names)
            self.record['expected_accounts'] = sorted(set(self.scheduler._bound_identity_names.values()))
        if getattr(self, 'new_request', False):
            self.record['required_account_count'] = len(self.scheduler._bound_identity_names)
            self.record['expected_accounts'] = sorted(set(self.scheduler._bound_identity_names.values()))
            if getattr(self, 'previous_request', None):
                self.record['previous_request'] = self.previous_request
            self.new_request = False
        for index in self.leaders:
            owner = self.record['teams'][str(index)].get('owner')
            current = self.scheduler._bound_identity_names.get(index)
            if owner and current and owner != current:
                self.record['teams'][str(index)].update(status='pending',
                    last_error='原队长角色未确认，整队保留断点；其他已确认队伍继续')
        if self.record['phase'] == 'solo' and any(t.get('status') == 'pending' for t in self.record['teams'].values()):
            self.record['phase'] = 'teams'
        self.save()

    def snapshot(self, account):
        s = self.scheduler
        if not s.main_ready():
            raise hs.PendingError('队长未确认主界面，不能打开队伍覆盖战斗')
        if not portrait_leader_present(s,s.capture()):
            raise hs.PendingError('当前角色头像未确认队长标记；队员栏标记不作为队长证据')
        s.open_team_room(account,'pipeline_snapshot_open')
        if not s.wait_template('team_room_close.bmp',ROOM,6):
            raise hs.PendingError('已有队伍房间未确认')
        refs = {name:decode_mask(row['mask'],tuple(row['shape']))
                for name,row in s._known_identity_materials.items()}
        result = None
        try:
            for sample in range(3):
                s.check_stop()
                frame = s.capture()
                if not s.template('team_dissolve_button.bmp',DISSOLVE,frame,threshold=.98)[0]:
                    raise hs.PendingError('所选标签没有正向队长权限')
                got = room_member_assignment(frame,refs,tuple(refs))
                owner = s.state.aliases.get(account.name)
                required_size = self.record.get('required_team_size')
                if (owner not in got or len(got) not in (2,3)
                        or required_size is not None and len(got) != required_size
                        or result is not None and got != result):
                    raise hs.PendingError('已有队伍人数或实名成员未连续核实')
                result = got
                if sample < 2:
                    s.wait_interruptible(.35)
            evidence = str(s.save_raw_evidence(frame,account,'pipeline_existing_team','verified'))
            return result,evidence
        finally:
            if not s.click_template(account,'team_room_close.bmp',ROOM,20,20,'pipeline_snapshot_close',['team_room_close.bmp']):
                raise hs.PendingError('队伍房间关闭未确认')
            if not s.wait_until_main(6):
                raise hs.PendingError('队伍房间关闭后未确认主界面')

    def block(self, slot, detail, offline=False):
        slot.update(status='pending',last_error=str(detail))
        if offline:
            slot['offline'] = True
        account = getattr(self.scheduler,'active',None)
        if account is not None and account.index == slot.get('leader_slot'):
            try:
                slot['last_error_evidence'] = str(self.scheduler.save_raw_evidence(
                    self.scheduler.capture(),account,'pipeline_team_pending','pending'))
            except (hs.AccountOffline,hs.PendingError,hs.SafetyError):
                pass
        self.save()
        self.event('pipeline_team_pending',tab_index=slot.get('leader_slot'),detail=str(detail),offline=offline)

    def prepare_teams(self):
        s = self.scheduler
        for index in self.leaders:
            slot = self.record['teams'][str(index)]
            slot['leader_slot'] = index
            if slot.get('status') == 'done':
                continue
            account = s.accounts[index-1]
            try:
                if index not in s._bound_identity_names:
                    reason = getattr(s,'_unavailable_reasons',{}).get(index)
                    if reason == 'offline_prompt':
                        raise hs.AccountOffline(account, '该队长当前显示掉线弹窗')
                    raise hs.PendingError('队长实名未唯一确认，本队待续；请查看身份核验截图')
                owner = s._bound_identity_names[index]
                if slot.get('owner') and slot['owner'] != owner:
                    raise hs.PendingError('队长角色与原请求不一致')
                slot['owner'] = owner
                if slot.get('needs_inactive_resume',{}).get('cross_day'):
                    from hyol_history import prove_idle_identity, isolate_inactive_work
                    s.switch_account(account)
                    proof = prove_idle_identity(s,account)
                    members,evidence = self.snapshot(account)
                    if not same_named_members(slot.get('members',[]),members):
                        raise hs.PendingError('跨日恢复的原队伍成员改变，整队待续')
                    if any(task != 'wanted' and rec.get('status') != 'done'
                           for task,rec in slot['tasks'].items()):
                        raise hs.PendingError('原副本结果未核实，保留场次，不重开副本')
                    isolate_inactive_work(s,account,proof=proof)
                    rec = slot['tasks'].get('wanted',{})
                    if rec and rec.get('status') != 'done':
                        slot.setdefault('historical_tasks',[]).append(dict(
                            task='wanted',record=deepcopy(rec),outcome='historical_unknown',proof=proof,
                            archive=slot['needs_inactive_resume']['archive']))
                        slot['tasks']['wanted'] = dict(status='new',phase='ready',completed=0,
                                                      source_day=s.state.day,attempt_id=uuid.uuid4().hex)
                    slot.update(team_evidence=evidence,members=list(members))
                    slot.pop('needs_inactive_resume')
                    self.save()
                actions = s.state.accounts[account.name].get('actions',{})
                navigation = [key for key,row in actions.items() if isinstance(row,dict)
                              and row.get('status') == 'pending' and 'wanted' in self.tasks
                              and legacy_wanted_navigation(key,row,s.state.day)]
                conflicting = [key for key,row in actions.items() if isinstance(row,dict)
                               and row.get('status') == 'pending'
                               and not key.startswith('pipeline_'+self.record['id']+'_')
                               and not key.startswith(INDEPENDENT_PENDING_PREFIXES)]
                conflicting = [key for key in conflicting if key not in navigation]
                if conflicting:
                    raise hs.PendingError('队长存在原队伍或未知动作未决，先按原流程对账：' + ', '.join(conflicting))
                slot['legacy_navigation'] = navigation
                if (slot.get('phase') == 'dissolve' or all(
                        slot['tasks'].get(TASK_KEYS[t],{}).get('status') == 'done' for t in self.tasks)):
                    slot['status'] = 'complete'
                    self.save()
                    continue
                frame = s.switch_account(account)
                for task, rec in slot['tasks'].items():
                    nav = rec.get('navigation')
                    action = pipeline_action(self.record,slot,f'{task}_{rec.get("completed",0)}_entry_participate')
                    if (nav and nav.get('phase') == 'arriving' and rec.get('phase','ready') == 'ready'
                            and not rec.get('intent') and task == 'group'
                            and s.state.action_status(account.name,'pipeline_'+self.record['id']+'_'
                                +f'group_{rec.get("completed",0)+1}_select') == 'new'
                            and daily_page_present(s,frame)
                            and s.template('daily_group_detail_current.bmp',(1090,310,1220,350),frame,threshold=.98)[0]
                            and s.template('daily_participate.bmp',(1051,569,1351,819),frame,threshold=.98)[0]
                            and not s.template('daily_participate_disabled_current.bmp',(1051,569,1351,819),frame,threshold=.98)[0]):
                        nav.update(phase='open',started_at=time.time(),last_progress_at=time.time(),scrolls=0,
                                   deferred_seconds=0,deferred_at_progress=0,
                                   recovered_entry_only=True)
                        self.event('pipeline_navigation_retry',account,task=task,
                                   detail='当前仍是未入场的副本导航页；只恢复导航，原参与记录等活动页正向核实')
                    if (nav and nav.get('phase') == 'open' and rec.get('phase','ready') == 'ready'
                            and not rec.get('intent') and s.state.action_status(account.name,action) == 'new'):
                        nav.update(started_at=time.time(),last_progress_at=time.time(),scrolls=0,
                                   deferred_seconds=0,deferred_at_progress=0)
                        self.event('pipeline_navigation_retry',account,task=task,
                                   detail='尚未发送参与动作，重新核验可恢复的导航')
                if (any(rec.get('navigation',{}).get('phase') == 'open'
                        and rec.get('phase','ready') == 'ready' and not rec.get('intent')
                        for rec in slot['tasks'].values())
                        and daily_page_present(s,frame)):
                    if not s.click_template(account,'daily_practice_close.bmp',(1370,200,1460,280),
                                            20,20,'pipeline_navigation_retry_close',
                                            ['daily_practice_anchor.bmp','daily_practice_title_core_current.bmp'],
                                            threshold=.98,anchor_threshold=.98):
                        raise hs.PendingError('原导航页面关闭未确认')
                    if not s.wait_until_main(6):
                        raise hs.PendingError('原导航页面关闭后主界面未确认')
                active = any(rec.get('phase') not in ('ready','done') for rec in slot['tasks'].values())
                if not active:
                    members,evidence = self.snapshot(account)
                    if slot.get('members') and not same_named_members(slot['members'],members):
                        raise hs.PendingError('已有队伍与原成员快照不同，保留整队')
                    slot.update(members=list(members),team_evidence=evidence)
                elif not slot.get('members'):
                    raise hs.PendingError('原活动缺少队伍快照，不根据标签顺序补成员')
                slot.update(status='running')
                slot.pop('offline',None)
                slot.pop('last_error',None)
                self.drivers[index] = LeaderActivityRunner(self,account,slot)
                self.save()
            except hs.AccountOffline as exc:
                self.block(slot,exc,True)
            except hs.PendingError as exc:
                self.block(slot,exc)
        occupied = set()
        for index in self.leaders:
            slot = self.record['teams'][str(index)]
            members = set(slot.get('members',[]))
            if occupied & members:
                raise hs.PendingError('队长所属队伍重叠，不能重复执行同一队伍')
            occupied.update(members)

    def defer_navigation(self,active_index,seconds):
        """Charge another team's foreground work only to unsent UI navigation."""
        if seconds <= 0:return
        changed=False
        for index in self.leaders:
            slot=self.record['teams'][str(index)]
            if index==active_index or slot.get('status')!='running':continue
            for task,rec in slot.get('tasks',{}).items():
                nav=rec.get('navigation')
                if (not nav or nav.get('phase')!='open' or rec.get('phase','ready')!='ready'
                        or rec.get('intent')):continue
                action=pipeline_action(self.record,slot,f'{task}_{rec.get("completed",0)}_entry_participate')
                account=self.scheduler.accounts[index-1]
                if self.scheduler.state.action_status(account.name,action)!='new':continue
                nav['deferred_seconds']=nav.get('deferred_seconds',0)+seconds
                changed=True
        if changed:self.save()

    def run_teams(self):
        # Poll only unfinished teams. Due times are transient observation
        # scheduling; persisted action deadlines and counters stay untouched.
        clock = getattr(self,'_poll_clock',time.monotonic)
        due = {index: 0 for index in self.leaders}
        while True:
            for index in self.leaders:
                self.scheduler.check_stop()
                slot = self.record['teams'][str(index)]
                if slot['status'] not in ('running','complete'):
                    continue
                if clock() < due[index]:
                    continue
                entered=clock()
                try:
                    task = next((TASK_KEYS[t] for t in self.tasks
                                 if slot['tasks'].get(TASK_KEYS[t],{}).get('status') != 'done'),None)
                    if task is not None:
                        if slot['status'] == 'complete':
                            raise hs.PendingError('原解散阶段仍有任务结果未核实，保留队伍断点')
                        driver = self.drivers[index]
                        self.event('pipeline_team_tick',driver.account,task=task)
                        driver.tick(task)
                    if all(slot['tasks'].get(TASK_KEYS[t],{}).get('status') == 'done' for t in self.tasks):
                        slot['status'] = 'complete'
                        self.save()
                        self.dissolve(index,slot)
                    else:
                        due[index] = clock()+getattr(self.drivers[index],'next_poll_seconds',.6)
                except hs.AccountOffline as exc:
                    self.block(slot,exc,True)
                except hs.PendingError as exc:
                    self.block(slot,exc)
                finally:
                    self.defer_navigation(index,max(0,clock()-entered))
            remaining = [index for index in self.leaders
                         if self.record['teams'][str(index)]['status'] in ('running','complete')]
            if not remaining:
                return
            self.scheduler.wait_interruptible(max(.05,min(due[index] for index in remaining)-clock()))

    def dissolve(self,index,slot):
        s = self.scheduler
        account = s.accounts[index-1]
        s.switch_account(account)
        action = 'pipeline_'+self.record['id']+'_dissolve'
        status = s.state.action_status(account.name,action)
        if status == 'new':
            members,_ = self.snapshot(account)
            if not same_named_members(members,slot['members']):
                raise hs.PendingError('解散前队伍成员改变，整队待续')
            slot['phase'] = 'dissolve'
            self.save()
            s.open_team_room(account,'pipeline_dissolve_open')
            if not s.wait_template('team_room_close.bmp',ROOM,6):
                raise hs.PendingError('解散前房间未确认')
            if not s.click_template(account,'team_dissolve_button.bmp',DISSOLVE,25,10,action,
                                    ['team_room_close.bmp'],irreversible=True,threshold=.98):
                raise hs.PendingError('解散按钮未确认')
            if not s.wait_absent('team_room_close.bmp',ROOM,6):
                raise hs.PendingError('解散后的房间尚未关闭')
        evidence = verify_unteamed(s,account)
        if status != 'done':
            s.commit_action(account,action,'队长连续三帧已无队，解散已确认')
        from hyol_navigation import return_to_leaf
        return_to_leaf(s,account,allow_pending_navigation=True)
        slot.update(status='done',phase='done',dissolve_evidence=evidence,home_returned=True)
        self.save()
        self.event('pipeline_team_dissolved',account,detail='已完成队伍解散，等待单人交接')

    def handoff(self,solo):
        pending_members = {name for slot in self.record['teams'].values() if slot.get('status') != 'done'
                           for name in slot.get('members',[])}
        s = solo.scheduler
        eligible = []
        verified_origins = set()
        for account in tuple(s.accounts):
            s.check_stop()
            try:
                if account.name in pending_members:
                    raise hs.PendingError('属于原待续队伍，保留队伍和任务')
                s.switch_account(account)
                evidence = verify_unteamed(s,account)
                from hyol_navigation import return_to_leaf
                if return_to_leaf(s,account,allow_pending_navigation=True) is not True:
                    raise hs.PendingError('无队已确认，但单人开场木叶原点尚未核实')
                verified_origins.add(account.name)
                eligible.append(account)
                self.event('pipeline_solo_eligible',account,evidence=evidence)
            except hs.AccountOffline as exc:
                self.solo_exclusions[account.name] = 'offline_prompt'
                self.event('pipeline_solo_skipped',account,detail=str(exc))
            except hs.PendingError as exc:
                self.solo_exclusions[account.name] = str(exc)
                self.event('pipeline_solo_skipped',account,detail=str(exc))
        s.accounts = eligible
        s.available_account_names = {a.name for a in eligible}
        # The pipeline just proved the same identity, a positively empty team
        # room and the stable village. Historical solo receipts stay pending
        # for their own task runners and do not prevent independent work.
        solo.startup_returned.update(verified_origins)
        self.record['solo_exclusions'] = self.solo_exclusions
        self.record['solo_eligible'] = [a.name for a in eligible]
        self.save()

    def completed(self):
        if self.record.get('required_account_count'):
            teams = self.record.get('teams', {})
            if set(teams) != set(map(str, self.leaders)):
                return False
            members = [name for slot in teams.values() for name in slot.get('members', [])]
            count = self.record['required_account_count']
            expected = self.record.get('expected_accounts',members)
            size = self.record.get('required_team_size')
            if (any(len(slot.get('members', [])) not in (2,3) or size is not None
                    and len(slot.get('members', [])) != size for slot in teams.values())
                    or len(set(members)) != len(members) or not set(members).issubset(expected)
                    or len(expected) != count or len(set(expected)) != count
                    or set(self.record.get('solo_eligible', [])) != set(expected)
                    or len(self.record.get('solo_eligible', [])) != count
                    or self.record.get('solo_initial_unavailable') or self.record.get('solo_disconnected')):
                return False
            records = self.record.get('solo_records', {})
            for name in expected:
                for task in self.solo_tasks:
                    rec = records.get(name, {}).get(task, {})
                    if (rec.get('status') != 'done' or rec.get('task_completed') is not True
                            or rec.get('home_returned') is not True or rec.get('followup_check_due')):
                        return False
                managed = records.get(name, {}).get('managed', {})
                if self.managed_enabled and (managed.get('status') != 'done'
                        or managed.get('task_completed') is not True or not managed.get('evidence')):
                    return False
        return (self.record.get('phase') == 'done' and bool(self.record.get('solo_eligible'))
                and not self.record.get('solo_exclusions')
                and all(slot.get('status') == 'done' and slot.get('dissolve_evidence')
                        and slot.get('home_returned') and all(
                            slot.get('tasks',{}).get(TASK_KEYS[t],{}).get('status') == 'done'
                            and slot['tasks'][TASK_KEYS[t]].get('task_completed') is True
                            and slot['tasks'][TASK_KEYS[t]].get('home_returned') is True
                            and slot['tasks'][TASK_KEYS[t]].get('evidence')
                            and slot['tasks'][TASK_KEYS[t]].get('zero_evidence' if t == 'group_instance' else 'empty_evidence')
                            for t in self.tasks)
                        for slot in self.record['teams'].values())
                and self.record.get('solo_result') == 0)

    def run(self):
        s = self.scheduler
        lease = hs.ForegroundRuntimeLease()
        if manual_pause_status(self.root).get('paused'):
            raise hs.PendingError('F12暂停仍在，原断点保留')
        if not lease.acquire():
            raise hs.PendingError('已有脚本占用游戏窗口')
        stop = s.stop
        try:
            s.start_f12_monitor()
            saved = read_checkpoint(self.path)
            if not self.leaders:
                if saved.get('phase') not in (None,'done') and not self.new_request:
                    self.leaders = tuple(saved.get('request',{}).get('leaders',()))
                else:
                    s.bind_session((),allow_unavailable=True,leaders_only=True)
                    self.leaders = s._automatic_captain_slots
                    if not self.leaders:
                        raise hs.PendingError('未识别到可核实队长，请先在游戏组成队伍')
            expected = {i:saved.get('teams',{}).get(str(i),{}).get('owner') for i in self.leaders}
            reassociation = None
            if (not self.new_request and saved.get('phase') not in (None,'done')
                    and saved.get('request') == self.request_key() and all(expected.values())):
                completed = tuple(i for i in self.leaders if saved['teams'][str(i)].get('status') == 'done')
                reassociation = s.reassociate_captains(expected,completed)
                located = tuple(i for i in self.leaders if i in reassociation['physical_tabs'])
                s.bind_session(located,allow_unavailable=True,leaders_only=True,
                               tab_positions=reassociation['positions'])
            else:
                s.bind_session(self.leaders,allow_unavailable=True,leaders_only=True)
            self.restore()
            if reassociation:
                self.record['captain_reassociation'] = reassociation
                self.save()
            if self.record['phase'] == 'done':
                return 0 if self.completed() else 2
            if self.record['phase'] == 'teams':
                self.prepare_teams()
                self.run_teams()
                self.record['phase'] = 'solo'
                self.save()
            s.check_stop()
            self.solo = MultiSoloRunner(self.solo_tasks,config_path=self.root/'HYOL_Daily.ini',
                                       account_count=6,arena_wins=self.arena_wins,formation=self.formation,
                                       managed_enabled=self.managed_enabled,event_sink=self.sink)
            self.solo.scheduler.stop = stop
            if self.solo.scheduler.state.day != self.record.get('execution_day',self.record['source_day']):
                raise hs.PendingError('交接期间日期改变；保留原队伍结果，后续单人任务待核实')
            self.scheduler = self.solo.scheduler
            result = self.solo.run(manage_lease=False,before_prepare=self.handoff)
            if self.record.get('required_account_count'):
                selected = (*self.solo_tasks, 'managed')
                self.record['solo_records'] = {
                    name: deepcopy({task: runner.records.get(task, {}) for task in selected})
                    for name, runner in self.solo.runners.items()}
            self.record.update(solo_result=result,solo_exclusions=self.solo_exclusions,
                               solo_initial_unavailable=sorted(self.solo.initial_unavailable),
                               solo_disconnected=sorted(self.solo.disconnected),
                               phase='done' if result == 0 and not self.solo_exclusions
                                   and all(t['status'] == 'done' for t in self.record['teams'].values()) else 'solo')
            if not self.completed():
                self.record['phase'] = 'solo'
            self.save()
            return 0 if self.completed() else 2
        finally:
            stop.set()
            lease.release()

    def summary(self):
        teams = self.record.get('teams',{})
        done = sum(t.get('status') == 'done' for t in teams.values())
        pending = len(teams)-done
        eligible = len(self.record.get('solo_eligible',[]))
        excluded = len(self.record.get('solo_exclusions',{}))
        offline = len(set(self.record.get('solo_initial_unavailable',[])) | set(self.record.get('solo_disconnected',[])))
        reasons = '；'.join(f'标签{index}：{slot["last_error"]}' for index,slot in teams.items()
                           if slot.get('status') == 'pending' and slot.get('last_error'))
        solo_pending = []
        for account,runner in getattr(getattr(self,'solo',None),'runners',{}).items():
            for task in self.solo_tasks:
                rec = runner.records.get(task,{})
                if (rec.get('status') == 'done' and rec.get('task_completed')
                        and rec.get('home_returned') and not rec.get('followup_check_due')):
                    continue
                detail = rec.get('last_error') or ('生存补跑未结束' if rec.get('followup_check_due')
                         else '回村尚未确认' if rec.get('task_completed') else '任务尚未完成')
                solo_pending.append(dict(account=account,task=task,status=rec.get('status','new'),detail=detail))
            managed = runner.records.get('managed',{})
            if self.managed_enabled and managed.get('status') == 'pending':
                solo_pending.append(dict(account=account,task='managed',status='pending',
                                         detail=managed.get('last_error') or '托管尚未确认'))
        solo_reasons = '；'.join(f'{row["account"]}/{LABELS.get(row["task"],row["task"])}：{row["detail"]}'
                                for row in solo_pending[:6])
        return dict(teams=teams,solo_eligible=self.record.get('solo_eligible',[]),
                    solo_exclusions=self.record.get('solo_exclusions',{}),
                    solo_pending=solo_pending,
                    offline_skipped=offline,phase=self.record.get('phase'),
                    detail=f'已完成并解散{done}队；整队待续{pending}队；单人可运行{eligible}号、暂缓{excluded}号、掉线或未绑定跳过{offline}号。'
                           + (reasons+'。' if reasons else '')
                           + (f'单人待处理{len(solo_pending)}项：'+solo_reasons
                              +('；其余原因见运行日志。' if len(solo_pending)>6 else '。') if solo_pending else ''))


def verify_unteamed(scheduler,account):
    """Positive Create proof, never a leave click or absence of a team badge."""
    s = scheduler
    f=s.capture()
    if s.active!=account or s._generic_active_tab_x(f)!=account.tab_x:
        raise hs.SafetyError('无队检查标签与当前账号不一致')
    def room_region(frame):
        if (s.template('team_public_room_title_current.bmp',(900,230,1100,315),frame,threshold=.98)[0]
                and s.template('team_room_close.bmp',PUBLIC_ROOM,frame,threshold=.98)[0]):
            return PUBLIC_ROOM
        if s.template('team_room_close.bmp',ROOM,frame,threshold=.98)[0]:return ROOM
        return None
    room=room_region(f)
    known_room=room is not None and s.template('team_create_button.bmp',CREATE,f,threshold=.98)[0]
    if not known_room:
        for attempt in range(2):
            if not s.main_ready(f):
                raise hs.PendingError('账号不在可核实主界面，原结算/战斗保留')
            if attempt and (not s.verify_identity(account,f)[0]
                    or not s.template('team_toolbar.bmp',(1650,69,1770,149),f,threshold=.98)[0]):
                raise hs.PendingError('无队检查入口重试前身份或图标未确认')
            s.open_team_room(account,'pipeline_verify_solo_open')
            if s.wait_template('team_create_button.bmp',CREATE,6):
                f=s.capture();room=room_region(f)
                if room is not None:break
            f=s.capture()
        else:
            raise hs.PendingError('无队检查房间未确认')
    try:
        for sample in range(3):
            s.check_stop()
            f = s.capture()
            if (room_region(f)!=room or not s.template('team_create_button.bmp',CREATE,f,threshold=.98)[0]
                    or s.template('team_dissolve_button.bmp',DISSOLVE,f,threshold=.98)[0]):
                raise hs.PendingError('未正向确认无队；本账号保留待续')
            if sample < 2:
                s.wait_interruptible(.35)
        return str(s.save_raw_evidence(f,account,'pipeline_unteamed','verified'))
    finally:
        if not s.click_template(account,'team_room_close.bmp',room,20,20,'pipeline_verify_solo_close',
                                ['team_create_button.bmp','team_room_close.bmp'],threshold=.98,anchor_threshold=.98):
            raise hs.PendingError('无队检查房间关闭未确认')
        if not s.wait_until_main(6):
            raise hs.PendingError('无队检查后主界面未确认')
