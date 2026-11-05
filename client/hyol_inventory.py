"""Account-bound fragment capacity recovery shared by every mail entry."""
from __future__ import annotations

from copy import deepcopy
import hashlib
import json
import re
import time

import numpy as np

TITLE = 'fragment_refinery_title_current.bmp'
TITLE_ROI = (680,220,950,310)
SURPLUS = 'fragment_surplus_title_current.bmp'
SURPLUS_ROI = (1190,290,1410,340)
CLOSE = 'fragment_refinery_close_current.bmp'
CLOSE_ROI = (1090,260,1160,330)
NINJA = 'ninja_owned_title_current.bmp'
NINJA_ROI = (1070,280,1280,340)
NINJA_CLOSE_ROI = (1570,100,1640,175)
PROMPT_ROI = (780,500,1135,560)
BUTTON_ROI = (805,555,1110,620)
EMPTY = 'fragment_surplus_empty_current.bmp'
EMPTY_ROI = (1155,330,1405,750)
POINTS_ROI = (641,380,708,402)
RECORD = 'inventory_recovery'


class CapacityFull(Exception):
    """A positively recognized mail capacity notice, not a network timeout."""


def visible(s, name, roi, frame):
    return s.template(name, roi, frame, threshold=.98)[0]


def refinery_visible(s, frame):
    return (visible(s, TITLE, TITLE_ROI, frame)
            and visible(s, SURPLUS, SURPLUS_ROI, frame)
            and visible(s, CLOSE, CLOSE_ROI, frame))


def exchange_prompt_visible(s, frame):
    # The changing point amount is excluded. Every retained phrase must say
    # ALL EXTRA FIVE-STAR fragments -> points, together with both yes/no.
    return all(visible(s, name, roi, frame) for name, roi in (
        ('fragment_exchange_scope_current.bmp', PROMPT_ROI),
        ('fragment_exchange_effect_current.bmp', PROMPT_ROI),
        ('fragment_exchange_points_label_current.bmp', PROMPT_ROI),
        ('fragment_exchange_yes_current.bmp', BUTTON_ROI),
        ('fragment_exchange_no_current.bmp', BUTTON_ROI)))


def capacity_notice_visible(s, frame):
    # Until a real capacity notice has been enrolled, generic errors cannot
    # authorize consumption of fragments. Explicit user-confirmed tickets can.
    return visible(s, 'mail_fragment_capacity_full_text.bmp', (600,250,1350,850), frame)


def close_shells(s, account, *, cancel_prompt=False):
    """Known reversible shells reveal identity; never confirms conversion."""
    if cancel_prompt and exchange_prompt_visible(s, s.capture()):
        click(s, account, 'fragment_exchange_no_current.bmp', BUTTON_ROI, 52,17,
              'inventory_bootstrap_cancel_exchange', ['fragment_exchange_scope_current.bmp'])
        if not s.wait_absent('fragment_exchange_scope_current.bmp', PROMPT_ROI, 6, threshold=.98):
            pending('碎片兑换取消未确认')
        s._fragment_prompt_cancelled = True
    if refinery_visible(s, s.capture()):
        click(s, account, CLOSE, CLOSE_ROI, 18,18, 'inventory_close_refinery', [TITLE])
        if not s.wait_absent(TITLE, TITLE_ROI, 6, threshold=.98):
            pending('碎片炼化面板未关闭')
    frame = s.capture()
    if visible(s, NINJA, NINJA_ROI, frame) and visible(s, 'ninja_close_current.bmp', NINJA_CLOSE_ROI, frame):
        click(s, account, 'ninja_close_current.bmp', NINJA_CLOSE_ROI, 20,20,
              'inventory_close_ninja', [NINJA])
        if not s.wait_main_after_close(account, NINJA, NINJA_ROI):
            pending('忍者页关闭后主界面未确认')


def click(s, account, name, roi, dx, dy, label, anchors, *, irreversible=False):
    if not s.click_template(account, name, roi, dx,dy,label,anchors,
                            irreversible=irreversible, threshold=.98, anchor_threshold=.98):
        pending('碎片恢复按钮未核实：' + name)


def pending(message):
    from hyol_scheduler import PendingError
    raise PendingError(message)


def fingerprint(value):
    return hashlib.sha256(json.dumps(value, ensure_ascii=False, sort_keys=True).encode('utf-8')).hexdigest()


def points_fingerprint(frame):
    rgb = np.asarray(frame.crop(POINTS_ROI).convert('RGB'), dtype=np.int16)
    white = (rgb.min(axis=2) > 145) & (rgb.max(axis=2)-rgb.min(axis=2) < 40)
    return hashlib.sha256(np.packbits(white).tobytes()).hexdigest()


class FragmentRecovery:
    def __init__(self, s, account):
        self.s, self.account = s, account
        self.record = s.state.accounts[account.name].get(RECORD, {})
        if not isinstance(self.record, dict):
            pending('碎片恢复断点格式异常，保留原文件')
        if self.active and self.record.get('account') != account.name:
            pending('碎片恢复断点身份不一致')

    @property
    def active(self):
        return self.record.get('status') == 'pending'

    def save(self, phase=None, **fields):
        from hyol_scheduler import now_iso
        if phase:
            self.record['phase'] = phase
        self.record.update(fields, updated_at=now_iso())
        self.s.state.accounts[self.account.name][RECORD] = self.record
        self.s.state.save_account(self.account.name)

    def request(self, evidence, *, reason='verified_capacity_notice'):
        from hyol_scheduler import now_iso
        if self.active:
            return
        if re.fullmatch(r'(当前窗口标签\d+|第\d+标签|标签\d+|当前窗口|日常入口核对)', self.account.name):
            pending('未确认实名账号，不执行碎片兑换')
        if reason not in ('verified_capacity_notice', 'user_confirmed_fragment_capacity') or not evidence:
            pending('仓库满原因未核实，不执行碎片兑换')
        actions = self.s.state.accounts[self.account.name].get('actions', {})
        originals = {key: deepcopy(value) for key, value in actions.items()
                     if (key == 'mail_claim_all' or key.startswith('mail_claim_all_network_'))
                     and value.get('status') == 'pending'}
        history = self.record.get('history', [])[-9:]
        if self.record:
            history.append({key:value for key,value in self.record.items() if key != 'history'})
        self.record = dict(status='pending', phase='requested', account=self.account.name,
                           ticket=self.s.diagnostics.identifier, started_at=now_iso(),
                           reason=reason, trigger_evidence=str(evidence), original_mail_actions=originals,
                           original_account_path=str(self.s.state.account_paths[self.account.name]),
                           resolved_mail_actions=[], history=history)
        self.save()

    def open_refinery(self):
        s, a = self.s, self.account
        frame = s.capture()
        if refinery_visible(s, frame):
            return frame
        if exchange_prompt_visible(s, frame):
            return frame
        if s._combat_active_visible('arena', frame):
            pending('碎片恢复遇到战斗，交回原任务，不退出或重新开战')
        if not visible(s, NINJA, NINJA_ROI, frame):
            for name, roi, anchors in (
                ('mail_warehouse_close.bmp', (1174,259,1275,359), ['mail_warehouse_anchor.bmp']),
                ('mail_close.bmp', (1301,249,1401,349), ['mail_ready_anchor.bmp'])):
                if visible(s, name, roi, s.capture()):
                    click(s,a,name,roi,20,20,'inventory_close_'+name,anchors)
            frame = s.capture()
            if not s.main_ready(frame) or not s.verify_identity(a, frame)[0]:
                pending('进入培养前主界面身份未核实')
            click(s,a,'ninja_toolbar_current.bmp',(1380,950,1490,1030),20,14,
                  'inventory_open_ninja',['anchor_top_menu.bmp'])
            if not s.wait_template(NINJA, NINJA_ROI, 8, threshold=.98):
                pending('忍者页未确认')
        frame = s.capture()
        if not visible(s, 'ninja_cultivation_selected_current.bmp', (1390,95,1475,175), frame):
            click(s,a,'ninja_cultivation_current.bmp',(1390,95,1475,175),29,28,
                  'inventory_open_cultivation',[NINJA])
            if not s.wait_template('ninja_cultivation_selected_current.bmp',(1390,95,1475,175),8,threshold=.98):
                pending('忍者培养页未确认')
        click(s,a,'fragment_refinery_entry_current.bmp',(1480,300,1610,345),28,7,
              'inventory_open_refinery',[NINJA])
        if not s.wait_template(TITLE, TITLE_ROI, 8, threshold=.98):
            pending('碎片炼化面板未确认')
        frame = s.capture()
        if not refinery_visible(s, frame):
            pending('多余五星碎片范围未确认')
        return frame

    def ensure_space(self):
        if not self.active:
            return
        phase = self.record.get('phase')
        if phase == 'space_verified':
            close_shells(self.s,self.account)
            return
        if phase in ('mail_claim_requested', 'mail_claim_verified'):
            return
        if phase not in ('requested', 'conversion_requested'):
            pending('碎片恢复阶段未知，不重复兑换')
        s, a = self.s, self.account
        frame = self.open_refinery()
        action = 'inventory_fragments_convert_' + self.record['ticket']
        try:
            if phase == 'requested':
                if exchange_prompt_visible(s, frame):
                    from pathlib import Path
                    if not self.record.get('points_before') or not Path(self.record.get('before_evidence','')).is_file():
                        pending('兑换确认已出现但原始碎片/积分证据缺失')
                else:
                    if not refinery_visible(s, frame):
                        pending('未取得兑换前原始碎片/积分画面')
                    evidence = s.save_raw_evidence(frame,a,'fragment_conversion_before','verified')
                    self.save(points_before=points_fingerprint(frame), before_evidence=str(evidence))
                    click(s,a,'fragment_exchange_current.bmp',(1210,735,1370,820),51,16,
                          'inventory_exchange_open_'+self.record['ticket'],[SURPLUS])
                    if not s.wait_template('fragment_exchange_scope_current.bmp',PROMPT_ROI,8,threshold=.98):
                        pending('碎片换积分确认正文未出现')
                    frame = s.capture()
            if s.state.action_status(a.name, action) == 'new':
                if not exchange_prompt_visible(s, frame):
                    pending('兑换正文/是/否未共同确认，不消耗碎片')
                evidence = s.save_raw_evidence(frame,a,'fragment_conversion_scope','verified')
                self.save('conversion_requested', conversion_action=action, prompt_evidence=str(evidence))
                click(s,a,'fragment_exchange_yes_current.bmp',BUTTON_ROI,52,17,action,
                      ['fragment_exchange_scope_current.bmp'],irreversible=True)
            elif phase != 'conversion_requested':
                pending('原碎片兑换结果不明，不重复确认')
            deadline = time.monotonic()+15
            stable = 0
            while time.monotonic() < deadline:
                s.check_stop()
                frame = s.capture()
                verified = (refinery_visible(s, frame) and not exchange_prompt_visible(s, frame)
                            and s.template(EMPTY,EMPTY_ROI,frame,threshold=.995)[0]
                            and visible(s,'fragment_exchange_disabled_current.bmp',(1210,735,1370,820),frame)
                            and self.record.get('points_before') != points_fingerprint(frame))
                stable = stable+1 if verified else 0
                if stable >= 3:
                    evidence = s.save_raw_evidence(frame,a,'fragment_space_verified','verified')
                    s.commit_action(a,action,'多余五星碎片清空与积分变化连续三帧核实')
                    self.save('space_verified', space_evidence=str(evidence), verified_frames=stable)
                    close_shells(s,a)
                    s.event('inventory_space_verified',a,ticket=self.record['ticket'],evidence=str(evidence))
                    return
                s.wait_interruptible(3)
            pending('碎片兑换结果未确认，保留原确认动作，不重新兑换')
        except (Exception, KeyboardInterrupt) as exc:
            evidence = s.save_raw_evidence(frame,a,'fragment_recovery_pending','pending')
            self.save(last_error=str(exc), pending_evidence=str(evidence))
            raise

    def claim_remaining(self, succeeded, ready, click_claim):
        """One new claim after verified capacity change; old retry budget stays."""
        if not self.active or self.record.get('phase') not in ('space_verified', 'mail_claim_requested', 'mail_claim_verified'):
            pending('腾仓证据或原领取阶段缺失')
        self.validate_originals()
        s, a = self.s, self.account
        label = 'mail_capacity_claim_' + self.record['ticket']
        if self.record['phase'] == 'mail_claim_verified':
            return
        if succeeded():
            self.settle_claim(label, sent=False)
            return
        if s.state.action_status(a.name,label) == 'new':
            if not ready():
                pending('腾仓后领取按钮未核实')
            self.save('mail_claim_requested', claim_action=label)
            if not click_claim(label):
                pending('腾仓后领取按钮发生变化，停止')
        deadline = time.monotonic()+30
        while time.monotonic() < deadline:
            s.check_stop()
            frame = s.capture()
            # Preserve the immediately observed response for future enrollment.
            if 'claim_response_evidence' not in self.record:
                self.save(claim_response_evidence=str(s.save_raw_evidence(frame,a,'mail_after_capacity_claim','observed')))
            if succeeded():
                self.settle_claim(label)
                return
            s.wait_interruptible(3)
        evidence = s.save_raw_evidence(frame,a,'mail_capacity_claim_pending','pending')
        self.save(pending_evidence=str(evidence))
        pending('腾仓后领取结果仍未知，保留原记录，不再次领取或兑换')

    def validate_originals(self):
        from hyol_checkpoints import action_versions, source_token, read_checkpoint
        if not self.record.get('original_account_path'):
            # Metadata-only migration for a ticket written before path support.
            # The original file must still contain this exact owner and ticket.
            from datetime import date
            from pathlib import Path
            day = date.fromisoformat(self.record['started_at'][:10]).isoformat()
            path = self.s.state.directory/day/self.s.state.account_paths[self.account.name].name
            source = read_checkpoint(path)
            if (source.get('account') != self.account.name
                    or source.get(RECORD,{}).get('ticket') != self.record['ticket']):
                pending('原邮件来源文件无法核实，保留恢复断点')
            self.save(original_account_path=str(Path(path)))
        actions = self.s.state.accounts[self.account.name].get('actions', {})
        settled = self.record.get('resolved_mail_actions', [])
        for key, old in self.record.get('original_mail_actions', {}).items():
            if key in settled:
                continue
            current = actions.get(key)
            if (isinstance(current,dict) and current.get('status') == 'done'
                    and self.record.get('mail_evidence')
                    and self.record['ticket'] in current.get('detail','')):
                self.record['resolved_mail_actions'].append(key)
                self.save()
                continue
            versions = [value for value in action_versions(current) if value.get('status') == 'pending']
            if len(versions) != 1:
                pending('邮件包含多个不同来源未决动作，不能用一次领取补记')
            version = versions[0]
            clean = lambda row: {k:v for k,v in row.items() if k not in ('origin','checkpoint_conflict','continuation_context')}
            old_token = source_token(self.record['original_account_path'],key,old)
            current_token = version.get('origin',{}).get('token',old_token)
            if fingerprint(clean(version)) != fingerprint(clean(old)) or current_token != old_token:
                pending('原邮件领取断点已变化，停止补记：'+key)

    def settle_claim(self, label, *, sent=True):
        s, a = self.s, self.account
        self.validate_originals()
        evidence = s.save_raw_evidence(s.capture(),a,'mail_capacity_result','verified')
        self.save(mail_evidence=str(evidence))
        if sent:
            s.commit_action(a,label,'腾仓后的邮件提取结果已正向核实')
        for key in self.record.get('original_mail_actions', {}):
            if key not in self.record['resolved_mail_actions']:
                s.commit_action(a,key,'腾仓后全部附件领取核实；恢复票据：'+self.record['ticket'])
                self.record['resolved_mail_actions'].append(key)
                self.save()
        budget = s.state.accounts[a.name].get('network_retries', {}).get('mail_claim_all')
        if budget is not None:
            budget['verified'] = True
        self.save('mail_claim_verified', mail_evidence=str(evidence), outcome='claimed' if sent else 'observed_already_claimed')
        s.event('inventory_mail_claim_verified',a,ticket=self.record['ticket'],evidence=str(evidence))

    def finish(self):
        if not self.active or self.record.get('phase') != 'mail_claim_verified':
            return
        from hyol_navigation import LEAF_NAME_ROI
        frame = None
        for sample in range(3):
            frame = self.s.capture()
            if (not self.s.main_ready(frame) or not self.s.verify_identity(self.account,frame)[0]
                    or not self.s.template('leaf_village_name_current.bmp',LEAF_NAME_ROI,frame,threshold=.92)[0]):
                pending('满仓恢复后身份/木叶主界面未核实')
            if sample < 2:
                self.s.wait_interruptible(.35)
        evidence = self.s.save_raw_evidence(frame,self.account,'inventory_mail_home','verified')
        self.save('done',status='done',home_evidence=str(evidence),home_frames=3)
