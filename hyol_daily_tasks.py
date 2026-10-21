"""Current-window daily tasks: independent runners sharing visual navigation."""
from __future__ import annotations

import argparse
import json
import queue
import sys
import threading
import time
from datetime import datetime
from pathlib import Path
import tkinter as tk
from tkinter import ttk
import numpy as np

import hyol_scheduler as hs
from hyol_daruma_task import verified as daruma_verified

VERSION = '2026.10.20.1'
TASKS = ("group", "survival", "wanted")
TEAM_OPTIONS = ("group", "wanted", "strong", "sealed")
SOLO_OPTIONS = ('mail', 'welfare', 'daruma', 'ranking', 'survival', 'quiz', 'arena', 'weekend', 'treasure')
DEFAULT_SOLO_OPTIONS = ('mail', 'welfare', 'ranking', 'survival', 'quiz', 'arena')
TASK_SEQUENCES = {"all": DEFAULT_SOLO_OPTIONS, "team": ("group", "wanted")}
LABELS = {"group": "组队副本", "survival": "生存演习", "wanted": "通缉任务", "all": "日常三合一", "team": "组队副本＋通缉任务"}
LABELS.update(team="组队任务自选", sealed="击败被封印的忍者（每日最多2次）")
LABELS['strong'] = '强者降临'
LABELS.update(quiz='忍者测验（仅单人，全部选第一项）', all='单人日常任务自选')
LABELS.update(mail='邮件附件', welfare='在线福利', ranking='排位赛（第一位，剩余次数归零）', arena='竞技场（本次追加胜场可设置）')
LABELS['treasure'] = '藏宝图（每账号5分钟，到时暂停并回村，默认不选）'
LABELS['weekend'] = '周末任务（拼图三难度＋达摩，自动识别）'
LABELS['daruma'] = '达摩（奖励次数归零）'
FORMATION_LABELS = {"保持不变": "keep", "主力阵容": "main", "速刷阵容（鲨鱼／速刷阵容）": "fast"}
SPEED_ROI = (1260, 890, 1335, 975)
GROUP_RETURN_ROI = (1110, 750, 1380, 890)
FORMATION_MAIN_ROI = (500, 290, 580, 430)
DAILY_ROI = (951, 119, 1501, 369)
ROW_ROI = (510, 310, 650, 710)
WANTED_SELECTED_ROI = (500, 310, 705, 710)
PAGE_ROIS = {"group": (850, 210, 1100, 290), "survival": (201, 69, 601, 419), "wanted": (500, 200, 1450, 850)}
PAGE_ANCHORS = {"group": "group_title_current.bmp", "survival": "survival_anchor.bmp", "wanted": "wanted_team_anchor.bmp"}


def read_json(path):
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
        return value if isinstance(value, dict) else {}
    except (OSError, ValueError):
        return {}


def latest_client_result(root, task):
    records = []
    for suffix in ('result', 'failure'):
        path = root / f'daily_{task}_{suffix}.json'
        value = read_json(path)
        if value:
            try:
                records.append((path.stat().st_mtime_ns, value))
            except OSError:
                continue
    return max(records, key=lambda entry: entry[0])[1] if records else {}


class DailyScheduler(hs.Scheduler):
    """Only this client changes the legacy direct survival entry."""
    navigator = None

    def template(self, name, roi=hs.VIEW, frame=None, threshold=None):
        result = super().template(name, roi, frame, threshold=threshold)
        if name == 'strong_approach_anchor.bmp' and not result[0]:
            return super().template('strong_mirror_anchor.bmp', roi, frame, threshold=threshold)
        return result

    def switch_account(self, account, verify=True):
        context = getattr(self, 'run_context', None)
        if context and context.binding and not context.probing:
            return super().switch_account(account, verify=verify)
        if self.generic_current_tab_x is not None:
            frame = self.content_guard(account)
            self.dismiss_unsolicited_team_invite(account, frame)
            return frame
        if (self.command_kind == 'multi_solo' and verify
                and self.active == account
                and self._saved_tab_mapping().get(account.name) == account.tab_x):
            self._validate_tab_binding(account)
            frame = self.capture()
            if (self.healthy(frame) and self.tab_is_visually_active(account, frame)
                    and self.verify_identity(account, frame)[0]):
                self.event('account_bound', account, tab_click_suppressed=True)
                return frame
        return super().switch_account(account, verify=verify)

    def _team_activity_completion_verified(self, team, leader, spec):
        if spec.key == 'strong_approach':
            return False  # Every user request re-reads the actual reward counter.
        return super()._team_activity_completion_verified(team, leader, spec)

    def _open_verified_survival(self, account):
        self.navigator.enter("survival")

    def _finish_survival_slot(self, slot):
        # The single-person daily target is one 220-point reward, not a reset.
        if slot.complete:
            return
        account = slot.account
        if not self.template('survival_anchor.bmp', (201,69,601,419))[0]:
            raise hs.PendingError('生存完成页未确认')
        if not self.wait_template('survival_complete_220.bmp', (751,769,1151,1031), 8, threshold=.98):
            raise hs.PendingError('生存积分尚未确认220/220')
        if not self._survival_full_score_visible():
            evidence = self.save_raw_evidence(self.capture(), account, 'survival_full_digits_unverified', 'pending')
            raise hs.PendingError(f'生存220分数字未严格确认；画面={evidence}')
        actions = self.state.accounts[account.name].get('actions', {})
        if any(key.startswith(('survival_battle_step_', 'survival_result_confirm_'))
               and isinstance(row, dict) and row.get('status') == 'pending'
               for key, row in actions.items()):
            raise hs.PendingError('生存仍有未决开战或结算，禁止领奖、重置或覆盖原动作')
        self.claim_survival_rewards(account)
        self.reconcile_survival_result_confirmations(account)
        if getattr(self, 'survival_inline_two_runs', False) is True:
            runner = self.navigator
            rec = runner.record('survival')
            if self.state.action_status(account.name, 'survival_second_run_started') != 'done':
                rec.update(completed=1, task_completed=True, home_returned=False,
                           followup_check_due=True)
                runner.save()
            # Reuse the audited reset/second-run flow on the same open page.
            hs.Scheduler._finish_survival_slot(self, slot)
            if slot.complete:
                runner.survival_inline_complete = True
                rec.update(followup_check_due=False,
                           completed=2 if self.state.action_status(
                               account.name, 'survival_second_run_started') == 'done' else 1)
                runner.save()
            return
        self.close_survival(account)
        slot.complete = True
        self.state.set_module(account.name, 'survival', 'done')
        self.event('combat_module_done', account, mode='survival')

    def _poll_survival_slot(self, slot):
        frame = self.capture()
        hs.check_combat_slot_watchdog(self, slot, frame)
        return super()._poll_survival_slot(slot, frame)


def selected_sequence(task, selected_tasks=None):
    allowed = TEAM_OPTIONS if task == "team" else SOLO_OPTIONS if task == 'all' else TASK_SEQUENCES.get(task, (task,))
    if selected_tasks is None:
        return TASK_SEQUENCES.get(task, (task,))
    selected = tuple(selected_tasks)
    if not selected or len(set(selected)) != len(selected) or any(t not in allowed for t in selected):
        raise ValueError("请至少勾选一项有效任务")
    return tuple(t for t in allowed if t in selected)


class DailyRunner:
    def __init__(self, task="all", group_count=5, event_sink=None, *, formation="keep", config_path=None, generic_current_window=True, selected_tasks=None, arena_wins=5, managed_enabled=True):
        if task not in (*TASKS, *TASK_SEQUENCES, *SOLO_OPTIONS) or not 0 <= group_count <= 99 or not 1 <= arena_wins <= hs.ARENA_MAX_ATTEMPTS or formation not in ("keep", "main", "fast"):
            raise ValueError("任务无效，次数应为1—99，或0表示打到本日奖励次数归零")
        self.task, self.group_count = task, group_count
        self.arena_wins = arena_wins
        if not isinstance(managed_enabled, bool):
            raise ValueError("最后托管选项必须为开启或关闭")
        self.managed_enabled = managed_enabled
        self.selected_tasks = selected_sequence(task, selected_tasks)
        from hyol_weekend import is_weekend
        if not is_weekend():
            self.selected_tasks = tuple(t for t in self.selected_tasks if t != 'weekend')
        self.track_friendship = task == 'team'
        self.formation = formation
        self.formation_checked = False
        self.generic_current_window = generic_current_window
        config_path = config_path or (hs.ROOT / "DAILY_Client.ini")
        from hyol_run_context import RunContext, current_run
        config = hs.read_config(config_path)
        self.run_context = current_run() or RunContext.create(Path(config_path).parent,config)
        with self.run_context.activate():
            self.scheduler = DailyScheduler(config, "daily_client", False, False, event_sink=event_sink)
        # Single-person daily completion explicitly includes starting managed mode.
        # The gate still requires the live toolbar, start control and active badge.
        if task == 'all' and managed_enabled:
            self.scheduler.config['ManagedSafetyVerified'] = '1'
        self.scheduler.navigator = self
        self.account = None
        self.records = {}
        self.path = None
        self.current = ""
        self.group_selection_cancelled = False
        self.wanted_selection_cancelled = False

    @classmethod
    def for_bound_account(cls, scheduler, account, tasks, *, formation='keep', arena_wins=5, managed_enabled=True):
        """Share one verified account's task ledger without creating another worker."""
        runner = object.__new__(cls)
        runner.scheduler, runner.account = scheduler, account
        runner.task, runner.current = 'all', ''
        runner.group_count, runner.arena_wins = 0, arena_wins
        runner.managed_enabled = managed_enabled
        runner.selected_tasks = tuple(tasks)
        runner.formation, runner.formation_checked = formation, False
        runner.generic_current_window = scheduler.generic_current_tab_x is not None
        runner.track_friendship = False
        runner.group_selection_cancelled = runner.wanted_selection_cancelled = False
        from hyol_checkpoints import load_tasks
        runner.path, runner.records = load_tasks(scheduler.state, account.name)
        return runner

    def event(self, name, **fields):
        self.scheduler.event(name, self.account, **fields)

    def save(self):
        if self.path:
            self.records["updated_at"] = hs.now_iso()
            hs.atomic_json(self.path, self.records)

    def visible(self, name, roi=hs.VIEW, frame=None, threshold=.92):
        return self.scheduler.template(name, roi, frame, threshold=threshold)[0]

    def click(self, name, roi, dx, dy, label, anchors, irreversible=False,
              threshold=.90, anchor_threshold=.90):
        if not self.scheduler.click_template(self.account, name, roi, dx, dy, label, anchors,
                                              irreversible=irreversible, threshold=threshold,
                                              anchor_threshold=anchor_threshold):
            raise hs.PendingError(f"未识别按钮：{name}")

    def wait(self, name, roi, seconds=12):
        if not self.scheduler.wait_template(name, roi, seconds, threshold=.90):
            detail = f"页面未确认：{name}"
            # Keep the final observed frame. A second capture could already
            # show another page or replace the original failure with a pause.
            frame = getattr(self.scheduler, '_last_frame', None)
            evidence = None
            if frame is not None:
                try:
                    evidence = self.scheduler.save_raw_evidence(
                        frame, self.account, 'daily_page_wait_'+Path(name).stem, 'pending')
                    detail += f'；截图={evidence}'
                except OSError as exc:
                    detail += f'；截图保存失败：{exc}'
            task = next((key for key, value in PAGE_ANCHORS.items() if value == name), None)
            self.event('daily_page_wait_pending', task=task, template=name,
                       evidence=str(evidence) if evidence else None, detail=detail)
            raise hs.PendingError(detail)

    def close_page(self, task):
        name, roi = {"group": ("team_instance_close.bmp", (1370, 240, 1460, 330)),
                     "survival": ("survival_close.bmp", (1585, 77, 1685, 177)),
                     "wanted": ("wanted_close_current.bmp", (1310, 290, 1400, 390))}[task]
        self.click(name, roi, 17, 17, f"daily_close_{task}", [PAGE_ANCHORS[task], name])
        if not self.scheduler.wait_until_main(10):
            raise hs.PendingError(f"{LABELS[task]}未确认返回主界面")

    def enter(self, task):
        s = self.scheduler
        if self.visible(PAGE_ANCHORS[task], PAGE_ROIS[task]):
            return
        if task == 'survival' and s.main_ready():
            hs.Scheduler._open_verified_survival(s,self.account)
            self.event('daily_entry_verified',task=task,detail='生存演习顶部入口已核实')
            return
        if not self.visible("daily_practice_anchor.bmp", DAILY_ROI):
            if not s.main_ready():
                raise hs.PendingError("请停留在游戏主界面、日常秘炼或当前任务页面")
            self.click("daily_practice_toolbar.bmp", (651,69,851,169), 20,20,
                       "daily_open", ["anchor_top_menu.bmp"])
            self.wait("daily_practice_anchor.bmp", DAILY_ROI)
        tab = "limited" if task == "group" else "normal"
        # A tab can already be selected. The task-specific detail is the final gate.
        tab_name = f"daily_{tab}_tab_current.bmp"
        if s.wait_template(tab_name, (510,250,800,310), 3, threshold=.90):
            self.click(tab_name, (510,250,800,310),40,15,"daily_tab_"+tab,["daily_practice_anchor.bmp"])
        self.wait(f"daily_{tab}_tab_selected.bmp",(510,250,800,310),5)
        s.wait_interruptible(.6)
        row = f"daily_{task}_current_row.bmp"
        found = False
        for direction in ("up", "down"):
            for _ in range(14):
                if self.visible(row, ROW_ROI, threshold=.90):
                    found = True
                    break
                arrow = f"daily_scroll_{direction}_current.bmp"
                roi = (1060,310,1095,350) if direction == "up" else (1060,660,1095,720)
                if not self.visible(arrow, roi, threshold=.90):
                    break
                before = hs.image_hash(s.capture().crop((510,315,1060,710)))
                self.click(arrow, roi,10,10,"daily_scroll_"+direction,["daily_practice_anchor.bmp"])
                s.wait_interruptible(.4)
                if before == hs.image_hash(s.capture().crop((510,315,1060,710))):
                    break
            if found:
                break
        if not found:
            raise hs.PendingError(f"日常秘炼未找到{LABELS[task]}，未点击参与")
        self.click(row, ROW_ROI,45,15,"daily_select_"+task,["daily_practice_anchor.bmp"])
        if task == "wanted":
            # The right-hand wanted detail header is painted over rotating
            # activity artwork.  Gate on the stable selected row instead.
            detail = "daily_wanted_selected_current.bmp"
            # The row moves vertically as the daily list changes.  Search the
            # whole bounded list instead of assuming yesterday's row position.
            self.wait(detail,WANTED_SELECTED_ROI,5)
        else:
            detail = f"daily_{task}_detail_current.bmp"
            self.wait(detail,(1090,310,1220,350),5)
        participate_roi = (1051,569,1351,819)
        if self.visible("daily_participate_disabled_current.bmp", participate_roi, threshold=.98):
            if task == "group":
                raise hs.PendingError("组队副本当前未开放（开放时间08:00—24:00），未点击灰色参与按钮")
            raise hs.PendingError(f"{LABELS[task]}当前不可参与，未点击灰色参与按钮")
        self.click("daily_participate.bmp", participate_roi,51,18,
                   "daily_participate_"+task,["daily_practice_anchor.bmp",detail],
                   threshold=.97)
        if task == "wanted":
            deadline = time.monotonic()+45
            while time.monotonic() < deadline:
                frame = s.capture()
                if self.visible(PAGE_ANCHORS[task],PAGE_ROIS[task],frame):
                    break
                if self.visible("wanted_available_auto_current.bmp",(1650,450,1910,560),frame,.92):
                    self.click("wanted_available_auto_current.bmp",(1650,450,1910,560),108,15,
                               "daily_open_wanted_from_task_auto",["wanted_available_auto_current.bmp"])
                    self.wait(PAGE_ANCHORS[task],PAGE_ROIS[task],45)
                    break
                s.wait_interruptible(.4)
            else:
                raise hs.PendingError(f"页面未确认：{PAGE_ANCHORS[task]}")
        else:
            self.wait(PAGE_ANCHORS[task], PAGE_ROIS[task], 15)
        self.event("daily_entry_verified", task=task, detail=LABELS[task]+"入口已核实")

    def record(self, task):
        record = self.records.setdefault(task, {"status":"new", "completed":0, "phase":"ready"})
        if task == 'weekend':
            from hyol_puzzle import normalize_weekend_record
            normalize_weekend_record(record)
        return record

    def once(self, name, roi, dx, dy, action, seconds=12):
        s = self.scheduler
        status = s.state.action_status(self.account.name, action)
        if status == "pending":
            raise hs.PendingError(f"{action}结果未知，禁止重复点击")
        if status == "done":
            raise hs.PendingError(f"{action}已点击但按钮仍可见，停止重复操作")
        self.click(name, roi, dx,dy,action,[name],True)
        if not s.wait_absent(name,roi,seconds,threshold=.90):
            raise hs.PendingError(f"{name}点击后未消失，结果待确认")
        s.commit_action(self.account,action,"按钮消失，动作已确认")

    def friendship_remaining(self, frame=None):
        from dataclasses import replace
        spec = replace(hs.TEAM_ACTIVITY_BY_KEY['group_instance'],
                       reward_counter_roi=(916,780,941,803))
        return self.scheduler._read_team_reward_remaining(self.account,spec,frame)

    def group_needs_more(self, rec):
        return not rec.get('reward_exhausted')

    def prepare_request(self):
        """A completed request is repeatable; an unfinished request keeps its target."""
        tasks = getattr(self, 'selected_tasks', TASK_SEQUENCES.get(self.task, (self.task,)))
        if self.task == 'all' and self.record('managed').get('status') in ('pending', 'running') and all(
            self.record(t).get('status') == 'done' for t in tasks
        ):
            return
        if any(self.record(t).get('status') in ('pending','running') for t in tasks):
            return
        for task in tasks:
            rec = self.record(task)
            if rec.get('status') != 'done':
                continue
            if task == 'group':
                rec.setdefault('history', []).append({k: v for k, v in rec.items() if k != 'history'})
                rec.update(status='new', phase='ready', task_completed=False,
                           home_returned=False, reward_exhausted=False,
                           request_target=rec.get('completed', 0) + self.group_count)
                self.event('daily_request_reopened', task=task, detail='再次启动：保留累计场数，重新读取游戏次数，归零才完成')
            elif task in ('strong','wanted','quiz','ranking','arena'):
                rec.update(status='new', task_completed=False, home_returned=False)
                rec.pop('attempt_target', None)
                if task == 'wanted':
                    actions = self.scheduler.state.accounts[self.account.name].get('actions', {})
                    keys = [key for key in actions if key.startswith('daily_wanted_')]
                    if any(actions[key].get('status') == 'pending' for key in keys):
                        raise hs.PendingError('通缉上次操作仍未核实，先续接结果')
                    rec.setdefault('action_history', []).append({key: actions.pop(key) for key in keys})
                    rec['phase'] = 'ready'
                    self.scheduler.state.save_account(self.account.name)
        self.save()

    def friendship_settled(self, rec):
        before = rec.get('friendship_before')
        return isinstance(before,int) and before > 0 and self.friendship_remaining() == before-1

    def group(self):
        s, rec = self.scheduler, self.record("group")
        frame = s.capture()
        active_battle = (s._team_battle_start_visible(frame) or s._battle_auto_on_visible(frame)
                         or self.visible("battle_auto_off.bmp",hs.BATTLE_AUTO_ROI,frame)
                         or self.visible("group_return_game_current.bmp",GROUP_RETURN_ROI,frame,.90)
                         or s._team_result_template(frame) or s._team_reward_result_visible(frame))
        if rec["phase"] in ("running", "result_confirmed") and active_battle:
            battle = rec["completed"] + 1
            self.poll_group(battle)
            after = s._read_team_reward_remaining(self.account,hs.TEAM_ACTIVITY_BY_KEY["group_instance"],s.capture())
            if after != rec["reward_before"] - 1 and not self.friendship_settled(rec):
                raise hs.PendingError("组队续接后奖励次数不符")
            rec.update(completed=battle,phase="ready")
            self.save()
        self.enter("group")
        if rec["phase"] in ("running","result_confirmed"):
            after = s._read_team_reward_remaining(self.account,hs.TEAM_ACTIVITY_BY_KEY["group_instance"],s.capture())
            reward_before = rec.get("reward_before",-1)
            reward_delta = reward_before - after
            if reward_delta == 0 and rec['phase'] == 'result_confirmed' and self.friendship_settled(rec):
                reward_delta = 1
            if 1 <= reward_delta <= reward_before:
                s.wait_interruptible(1)
                if self.visible(PAGE_ANCHORS["group"],PAGE_ROIS["group"]) and s._read_team_reward_remaining(self.account,hs.TEAM_ACTIVITY_BY_KEY["group_instance"],s.capture()) == after and (reward_before-after == reward_delta or self.friendship_settled(rec)):
                    rec.update(completed=rec["completed"]+reward_delta,phase="ready")
                    self.save()
                    self.event("daily_group_settlement_reconciled",detail=f"稳定返回选择页且奖励次数减少{reward_delta}，补记{reward_delta}场自动结算")
            elif reward_delta == 0 and rec["phase"] == "running":
                # A manual stop can land after the selection/solo-confirm
                # clicks but before the game actually enters battle. Reopen
                # that one attempt only after a stable selection page proves
                # there is no battle/result and the reward count is unchanged.
                abandoned = True
                for _ in range(6):
                    s.wait_interruptible(1)
                    f = s.capture()
                    active = (s._team_battle_start_visible(f) or s._battle_auto_on_visible(f)
                              or self.visible("battle_auto_off.bmp",hs.BATTLE_AUTO_ROI,f)
                              or self.visible("group_return_game_current.bmp",GROUP_RETURN_ROI,f,.90)
                              or s._team_result_template(f) or s._team_reward_result_visible(f))
                    remaining = s._read_team_reward_remaining(
                        self.account,hs.TEAM_ACTIVITY_BY_KEY["group_instance"],f)
                    friendship_changed = (getattr(self,'track_friendship',False)
                                          and self.friendship_remaining(f) != rec.get('friendship_before'))
                    if active or remaining != reward_before or friendship_changed or not self.visible(PAGE_ANCHORS["group"],PAGE_ROIS["group"],f):
                        abandoned = False
                        break
                if abandoned:
                    battle = rec["completed"] + 1
                    for suffix in ("select", "solo_confirm"):
                        s.state.set_action(self.account.name,f"daily_group_{battle}_{suffix}","new",
                                           "暂停恢复：稳定选择页且奖励未减少，确认尚未入场")
                    rec["phase"] = "ready"
                    self.save()
                    self.event("daily_group_unstarted_reopened",detail="暂停发生在实际入场前，已安全恢复本场重试")
        if self.group_selection_cancelled and rec["phase"] in ("start_pending", "running"):
            remaining = s._read_team_reward_remaining(self.account, hs.TEAM_ACTIVITY_BY_KEY["group_instance"],s.capture())
            if remaining == rec.get("reward_before") and (not getattr(self,'track_friendship',False)
                    or self.friendship_remaining() == rec.get('friendship_before')):
                battle = rec["completed"] + 1
                s.state.set_action(self.account.name,f"daily_group_{battle}_select","new","实机未组队确认框已取消，奖励次数未变化，尚未入场")
                rec["phase"] = "ready"
                self.save()
        if rec["phase"] != "ready":
            raise hs.PendingError("上次组队副本战斗未完整结算，保留断点，不能重复开战")
        if self.group_needs_more(rec):
            remaining_now = s._read_team_reward_remaining(self.account,hs.TEAM_ACTIVITY_BY_KEY["group_instance"],s.capture())
            if remaining_now == 0:
                rec["reward_exhausted"] = True
                self.save()
            else:
                self.close_page("group")
                self.ensure_formation_once()
                self.enter("group")
        while self.group_needs_more(rec):
            remaining = s._read_team_reward_remaining(self.account, hs.TEAM_ACTIVITY_BY_KEY["group_instance"],s.capture())
            if remaining == 0:
                rec["reward_exhausted"] = True
                self.save()
                break
            battle = rec["completed"] + 1
            rec.update(phase="start_pending", reward_before=remaining)
            if getattr(self,'track_friendship',False):
                rec['friendship_before'] = self.friendship_remaining()
            self.save()
            self.once("team_instance_hard_card.bmp",(550,300,950,650),200,130,
                      f"daily_group_{battle}_select")
            rec["phase"] = "running"
            self.save()
            self.poll_group(battle)
            after = s._read_team_reward_remaining(self.account, hs.TEAM_ACTIVITY_BY_KEY["group_instance"],s.capture())
            if after != remaining - 1 and not self.friendship_settled(rec):
                raise hs.PendingError(f"组队结算后奖励次数未减少1：{remaining}→{after}，本场未计完成")
            rec.update(completed=battle,phase="ready")
            self.save()
            self.event("daily_battle_complete", task="group", completed=battle, requested=self.group_count)
        self.close_page("group")

    def poll_group(self, battle):
        s = self.scheduler
        result_seen = self.record("group")["phase"] == "result_confirmed"
        loading_since = None
        transition_since = None
        unknown_since = time.monotonic()
        while True:
            s.check_stop()
            f = s.capture()
            if s._battle_auto_on_visible(f):
                unknown_since = time.monotonic()
            elif time.monotonic()-unknown_since >= 60:
                raise hs.PendingError("组队副本60秒未确认阶段进展，保留原战斗")
            if self.visible("arena_loading_anchor.bmp", hs.ARENA_LOADING_ROI,f, .90):
                loading_since = loading_since or time.monotonic()
                if time.monotonic()-loading_since >= 60:
                    raise hs.PendingError("游戏超时：玩家加载60秒无进展")
                s.wait_interruptible(.4)
                continue
            loading_since = None
            result = s._team_result_template(f)
            reward = s._team_reward_result_visible(f)
            if self.visible("group_return_game_current.bmp",GROUP_RETURN_ROI,f,.90):
                self.once("group_return_game_current.bmp",GROUP_RETURN_ROI,45,20,
                          f"daily_group_{battle}_return_game",seconds=8)
                result_seen = True
                self.record("group")["phase"] = "result_confirmed"
                self.save()
                self.event("daily_group_return_game",detail="结算页已点击返回游戏")
                continue
            if self.visible("group_solo_warning_current.bmp",(760,480,1150,540),f,.95):
                self.dismiss_login_warning()
                self.once("group_solo_confirm_current.bmp",(810,530,960,600),50,17,f"daily_group_{battle}_solo_confirm")
                continue
            if result or reward:
                name = result or hs.TEAM_REWARD_RESULT_TEMPLATE
                roi = hs.TEAM_BATTLE_RESULT_ROI if result else hs.TEAM_REWARD_RESULT_ROI
                self.once(name,roi,55 if result and name.startswith("team_") else 64,
                          18 if result and name.startswith("team_") else 19,
                          f"daily_group_{battle}_"+("result" if result else "reward"))
                result_seen = True
                self.record("group")["phase"] = "result_confirmed"
                self.save()
                continue
            if s._team_battle_start_visible(f):
                self.once(hs.TEAM_BATTLE_START_TEMPLATE,hs.TEAM_BATTLE_START_ROI,65,23,f"daily_group_{battle}_start")
            elif self.visible("team_leader_final_start.bmp",(650,400,1250,850),f,.90):
                self.once("team_leader_final_start.bmp",(650,400,1250,850),55,18,f"daily_group_{battle}_final_start")
            elif self.visible("battle_auto_off.bmp",hs.BATTLE_AUTO_ROI,f):
                self.click("battle_auto_off.bmp",hs.BATTLE_AUTO_ROI,29,29,f"daily_group_{battle}_auto",["battle_auto_off.bmp"])
                if not s._wait_battle_auto_on(5):
                    raise hs.PendingError("组队副本自动战斗未确认开启")
            elif self.ensure_battle_x2(f,battle):
                continue
            elif self.visible(PAGE_ANCHORS["group"],PAGE_ROIS["group"],f):
                if not result_seen:
                    remaining = s._read_team_reward_remaining(self.account,hs.TEAM_ACTIVITY_BY_KEY["group_instance"],f)
                    if remaining == self.record("group").get("reward_before",-1)-1:
                        result_seen = True
                        self.event("daily_group_auto_settled",detail="奖励次数减少1，游戏已自动结算")
                        continue
                    if transition_since is None:
                        transition_since = time.monotonic()
                    if time.monotonic()-transition_since >= 60:
                        raise hs.PendingError("组队副本60秒未离开选择页或未确认结算，不计数")
                    s.wait_interruptible(.4)
                    continue
                stable = True
                for _ in range(3):
                    s.wait_interruptible(.6)
                    f = s.capture()
                    if s._team_result_template(f) or s._team_reward_result_visible(f) or not self.visible(PAGE_ANCHORS["group"],PAGE_ROIS["group"],f):
                        stable = False
                        break
                if stable:
                    return
            else:
                transition_since = None
            s.wait_interruptible(.4)
        raise hs.PendingError("游戏超时：组队副本600秒内未完整结算")

    def dismiss_login_warning(self):
        roi = (805,588,840,620)
        if self.visible("group_login_unchecked_current.bmp",roi,threshold=.98):
            self.click("group_login_unchecked_current.bmp",roi,9,9,
                       "group_hide_warning_this_login",["group_solo_warning_current.bmp"])
            if not self.scheduler.wait_absent("group_login_unchecked_current.bmp",roi,3,threshold=.98):
                raise hs.PendingError("本次登录不再提示勾选后未变化")
            self.event("daily_login_warning_disabled",detail="已勾选本次登录不再提示")

    def survival(self):
        self.ensure_formation_once()
        self.enter("survival")
        # Use the tested score-checkpoint state machine, scoped to this account.
        self.scheduler.accounts = [self.account]
        self.scheduler.run_combat_interleaved("survival")
        if self.scheduler.state.module_status(self.account.name, "survival") != "done":
            raise hs.PendingError("生存演习仍有未核实战斗或奖励，保留断点")
        if not self.scheduler.wait_until_main(8):
            # The reward page can repaint after the module close click. Use the
            # shared guarded return path to finish the transition; it never
            # retries a combat or reward action.
            self.return_to_main()
        if not self.scheduler.main_ready():
            raise hs.PendingError("生存演习结束后未确认主界面")
        if not getattr(self, 'survival_inline_complete', False):
            self.record("survival")["completed"] = 1

    def wanted(self):
        s, rec = self.scheduler, self.record("wanted")
        if rec["phase"] == "running":
            if self.visible("wanted_available_auto_current.bmp",(1650,450,1910,560),threshold=.92):
                rec["phase"] = "ready"
                self.save()
                self.click("wanted_available_auto_current.bmp",(1650,450,1910,560),108,15,
                           "daily_recover_wanted_from_task_auto",["wanted_available_auto_current.bmp"])
                self.wait(PAGE_ANCHORS["wanted"],PAGE_ROIS["wanted"],45)
                self.event("daily_wanted_navigation_reconciled",detail="自动寻路已到达NPC，进入通缉页继续")
            else:
                self.event("daily_wanted_resumed",detail="继续观察已启动的通缉链，不重复点击自动完成")
                return self.poll_wanted(rec)
        if not self.visible(PAGE_ANCHORS["wanted"],PAGE_ROIS["wanted"]):
            self.ensure_formation_once()
            self.enter("wanted")
        if self.wanted_selection_cancelled and rec["phase"] == "auto_pending":
            s.state.set_action(self.account.name,"daily_wanted_auto","new","单人确认框已取消，自动链尚未启动")
            rec["phase"] = "ready"
        if rec["phase"] != "ready":
            raise hs.PendingError("上次通缉自动链结果未知，禁止重新点击自动完成")
        rec["phase"] = "auto_pending"
        self.save()
        action = "daily_wanted_auto"
        self.click("wanted_team_auto.bmp",(1050,300,1300,500),58,17,action,["wanted_team_anchor.bmp"],True)
        start_deadline = time.monotonic()+30
        while self.visible("wanted_team_anchor.bmp",PAGE_ROIS["wanted"]):
            if time.monotonic() >= start_deadline:
                raise hs.PendingError("通缉自动完成未离开任务页，保留待确认")
            if self.visible("wanted_solo_warning_current.bmp",(780,500,1135,570),threshold=.95):
                self.once("wanted_solo_yes_current.bmp",(810,555,945,620),50,15,"daily_wanted_solo_yes")
            s.wait_interruptible(.4)
        s.commit_action(self.account,action,"已确认单人提示并离开通缉任务页")
        rec["phase"] = "running"
        self.save()
        return self.poll_wanted(rec)

    def poll_wanted(self, rec):
        s = self.scheduler
        deadline = time.monotonic()+1200
        next_report = time.monotonic()
        stable = 0
        while time.monotonic() < deadline:
            s.check_stop()
            f = s.capture()
            if self.visible("offline_login_prompt.bmp",(780,450,1140,660),f,.98):
                raise hs.PendingError("通缉过程中账号掉线，保留待处理")
            if time.monotonic() >= next_report:
                self.event("daily_wanted_observed",detail="通缉自动链运行中，等待任务列表清空")
                next_report = time.monotonic()+15
            if s.main_ready(f) and hs.wanted_task_list_is_empty(f):
                stable += 1
                if stable >= 5:
                    rec.update(phase="ready",completed=1)
                    self.save()
                    return
            else:
                stable = 0
            s.wait_interruptible(.6)
        raise hs.PendingError("游戏超时：通缉任务列表未确认清空，保留断点")

    def ensure_battle_x2(self, frame, battle):
        """Return True only when this call changed x1 to x2."""
        if self.visible("battle_speed_x2.png",SPEED_ROI,frame,.84):
            return False
        if not self.visible("battle_speed_x1.png",SPEED_ROI,frame,.84):
            return False
        if not self.scheduler._battle_auto_on_visible(frame):
            return False
        anchors = ["battle_auto_on.bmp", "battle_auto_on_current.bmp",
                   "battle_auto_on_current_core.bmp"]
        self.click("battle_speed_x1.png",SPEED_ROI,14,14,
                   f"daily_group_{battle}_speed_x2",anchors,
                   threshold=.84, anchor_threshold=.90)
        if not self.scheduler.wait_template("battle_speed_x2.png",SPEED_ROI,5,threshold=.84):
            raise hs.PendingError("战斗速度未确认切换到x2")
        self.event("daily_battle_speed_verified",detail=f"第{battle}场战斗已切换x2")
        return True

    def ensure_formation_once(self):
        if self.formation_checked:
            return
        self.verify_main_formation()
        self.formation_checked = True

    def verify_main_formation(self):
        if self.formation == 'keep':
            self.event('daily_formation_preserved', detail='保持当前阵容，不进入忍者页面')
            return
        s = self.scheduler
        page_roi = (300,270,490,345)
        if not self.visible("ninja_page_current.bmp",page_roi):
            if not s.main_ready():
                raise hs.PendingError("阵容核对前未确认主界面，停止开战")
            self.click("ninja_toolbar_current.bmp",(1380,950,1490,1030),20,14,
                       "daily_open_ninja",["anchor_top_menu.bmp"])
            self.wait("ninja_page_current.bmp",page_roi)
        selected = "ninja_main_selected_current.bmp"
        # Live capture places the main-preset button at y=390.  The former
        # 295..340 range covered the title area and could never select it.
        roi = FORMATION_MAIN_ROI
        if self.formation == "main" and not self.visible(selected,roi,threshold=.98) and not s.wait_template(selected,roi,4,threshold=.98):
            # The preset buttons paint a moment after the page anchor.  Wait
            # for the selected appearance before treating it as unselected.
            self.click("ninja_main_unselected_current.bmp",roi,28,14,"daily_select_main_formation",["ninja_page_current.bmp"])
        # A fast preset must be identified by its selected button.  When the
        # verified 速刷 button is present, select it before the 4/4 gate.
        fast_selected = ("ninja_shark_selected_current.bmp", "ninja_fast_selected_current.bmp")
        def fast_gold(frame):
            point = getattr(self, "fast_preset_point", None)
            if point is None:
                return False
            x, y = point
            a = np.asarray(frame.crop((x, y, x+58, y+27)).convert("RGB"), dtype=np.int16)
            gold = ((a[:, :, 0] > 115) & (a[:, :, 1] > 75) & (a[:, :, 2] < 90)
                    & (a[:, :, 0] > a[:, :, 2] + 50))
            return float(gold.mean()) >= .15
        if self.formation == "fast" and not any(
            (hs.ASSETS / name).exists() and self.visible(name, (490,295,595,760), threshold=.96)
            for name in fast_selected
        ) and not fast_gold(s.capture()):
            ok, _score, x, y = s.template("ninja_fast_unselected_current.bmp", (490,295,595,760), threshold=.95)
            if not ok:
                raise hs.PendingError("速刷／鲨鱼预设按钮未确认，停止开战")
            self.click("ninja_fast_unselected_current.bmp", (490,295,595,760), 27, 13,
                       "daily_select_fast_formation", ["ninja_page_current.bmp"])
            self.fast_preset_point = (x, y)
        # Require stable selected preset and a full four-ninja deployment.
        for _ in range(3):
            s.wait_interruptible(.7)
            f = s.capture()
            selected_ok = (self.visible(selected,roi,f,.98) if self.formation == "main" else
                           (any((hs.ASSETS / name).exists() and self.visible(name,(490,295,595,760),f,.96)
                                for name in fast_selected) or fast_gold(f)))
            if not selected_ok or not self.visible("ninja_four_deployed_current.bmp",(870,310,1020,360),f,.95):
                raise hs.PendingError("未确认所选阵容上阵4/4，停止开战")
        detail = "主力预设已选中，上阵4/4" if self.formation == "main" else "鲨鱼／速刷预设已选中，上阵4/4"
        evidence = s.save_raw_evidence(s.capture(),self.account,self.formation+"_formation_verified","verified")
        self.event("daily_formation_verified",detail=detail+"；本次启动不再重复查询",evidence=str(evidence))
        self.click("ninja_close_current.bmp",(1570,100,1640,175),20,20,"daily_close_ninja",["ninja_page_current.bmp"])
        if not s.wait_main_after_close(self.account, "ninja_page_current.bmp", page_roi):
            raise hs.PendingError("阵容核对后未返回主界面")

    def bind(self):
        s = self.scheduler
        s.bind_window(wait=True,timeout_seconds=15)
        frame = s.capture()
        binding_main_ready = s.main_ready(frame)
        if self.generic_current_window:
            self.account = s.bind_generic_current_window(require_game_context=False)
            from hyol_checkpoints import load_tasks
            self.path, self.records = load_tasks(s.state, self.account.name)
            frame = s.capture()
            if 'ranking' in getattr(self, 'selected_tasks', ()):
                from hyol_ranking import PAGE, END, RESULT
                if (self.visible('ranking_page.bmp', PAGE, frame)
                        or self.record('ranking').get('phase', 'ready') != 'ready'):
                    return
            if 'treasure' in getattr(self, 'selected_tasks', ()):
                if (self.visible('treasure_page_anchor.bmp', (560,250,1380,810), frame, .96)
                        or self.visible('treasure_result_anchor.bmp', (760,440,1170,670), frame, .96)):
                    return
            if set(getattr(self, 'selected_tasks', ())) & {'daruma', 'weekend'}:
                if (self.visible('daruma_game_page.bmp', (920,220,1300,325), frame, .93)
                        or self.visible('daruma_weekend_page.bmp', (880,245,1190,360), frame, .93)
                        or self.visible('puzzle_game_anchor.bmp', hs.VIEW, frame, .95)
                        or self.visible('puzzle_select_title.bmp', hs.VIEW, frame, .95)):
                    return
        else:
            self.account = None
        battle_accounts = [a for a in s.accounts if any(
            (hs.ASSETS/f"accounts/{hs.slug(a.name)}_{suffix}_identity.bmp").exists()
            and self.visible(f"accounts/{hs.slug(a.name)}_{suffix}_identity.bmp",(300,250,850,650),frame,.95)
            for suffix in ("battle","group"))]
        if not self.generic_current_window and len(battle_accounts) == 1:
            a = battle_accounts[0]
            from hyol_checkpoints import load_tasks
            path, records = load_tasks(s.state, a.name)
            if self.task in ("group","all") and records.get("group",{}).get("phase") in ("running","result_confirmed"):
                s.current_account_only = a.name
                s.active = a
                s.joint_identity_bound = {a.name}
                self.event("daily_battle_identity_bound",detail=a.name+"战斗角色文字核实")
                self.account,self.path,self.records = a,path,records
                return
        # Close only known non-battle pages to reveal identity before binding.
        if not self.generic_current_window:
            bootstrap = hs.Account(0,"日常入口核对",0)
            s.current_account_only = bootstrap.name
            s.active = bootstrap
            s.state.account_paths[bootstrap.name] = hs.STATE_DIR/s.state.day/(hs.slug(bootstrap.name)+".json")
            s.state.accounts[bootstrap.name] = {"date":s.state.day,"account":bootstrap.name,"modules":{},"pending":[]}
            s.account_logs[bootstrap.name] = hs.JsonlLogger(hs.LOGS/"accounts"/(hs.slug(bootstrap.name)+".jsonl"))
            self.account = bootstrap
        if self.visible("wanted_solo_warning_current.bmp",(780,500,1135,570),threshold=.95):
            self.click("wanted_solo_no_current.bmp",(980,555,1110,620),50,15,
                       "daily_cancel_unstarted_wanted",["wanted_solo_warning_current.bmp"])
            if not s.wait_absent("wanted_solo_warning_current.bmp",(780,500,1135,570),5,threshold=.95):
                raise hs.PendingError("通缉单人确认框未关闭")
            self.wanted_selection_cancelled = True
        if (self.visible('ninja_owned_title_current.bmp',(1070,280,1280,340),threshold=.98)
                or self.visible('fragment_refinery_title_current.bmp',(680,220,950,310),threshold=.98)
                or self.visible('fragment_exchange_scope_current.bmp',(780,500,1135,560),threshold=.98)):
            from hyol_inventory import close_shells
            close_shells(s,self.account,cancel_prompt=True)
        if self.visible("ninja_page_current.bmp",(300,270,490,345)):
            self.click("ninja_close_current.bmp",(1570,100,1640,175),20,20,"daily_bootstrap_close_ninja",["ninja_page_current.bmp"])
            if not s.wait_until_main(8):
                raise hs.PendingError("关闭忍者界面后未返回主界面")
        if self.visible("group_solo_warning_current.bmp",(760,480,1150,540),threshold=.95):
            self.click("group_solo_cancel_current.bmp",(960,530,1100,600),50,17,
                       "daily_cancel_unstarted_group",["group_solo_warning_current.bmp"])
            if not s.wait_absent("group_solo_warning_current.bmp",(760,480,1150,540),5,threshold=.95):
                raise hs.PendingError("未组队确认框未关闭")
            self.group_selection_cancelled = True
        for task in TASKS:
            if self.visible(PAGE_ANCHORS[task],PAGE_ROIS[task]):
                self.close_page(task)
                break
        if self.visible('strong_approach_anchor.bmp',(250,80,1720,950)):
            self.click('team_activity_close.bmp',(1300,280,1420,390),17,17,
                       'daily_bootstrap_close_strong',['strong_approach_anchor.bmp'])
            if not s.wait_absent('strong_approach_anchor.bmp',(250,80,1720,950),8):
                raise hs.PendingError('强者页面未关闭')
        if self.visible("daily_practice_anchor.bmp",DAILY_ROI):
            self.click("daily_practice_close.bmp",(1370,200,1460,280),20,20,"daily_bootstrap_close",["daily_practice_anchor.bmp"])
            if not s.wait_until_main(8):
                raise hs.PendingError("关闭日常秘炼后未返回主界面")
        # Ranking is a solo-only page and is therefore not part of TASKS.
        # A previous safe stop can leave it open and hide the normal identity
        # strip; close only after its page anchor is positively matched.
        if not self.generic_current_window:
            from hyol_ranking import PAGE as ranking_page
            if self.visible("ranking_page.bmp", ranking_page):
                self.click("ranking_close.bmp",(1600,95,1670,160),17,17,
                           "daily_bootstrap_close_ranking",["ranking_page.bmp"])
                if not s.wait_until_main(8):
                    raise hs.PendingError("关闭排位赛后未返回主界面")
        if self.generic_current_window and not s.main_ready() and not any(
            self.visible(PAGE_ANCHORS[task],PAGE_ROIS[task]) for task in TASKS
        ):
            # Team/friend panels and lower-right notices can cover the neutral
            # main-page anchors while the player is already standing in a team.
            # Closing these views does not alter or dissolve the current team.
            s.prepare_main(self.account)
        if (not binding_main_ready and self.generic_current_window and self.account.name.startswith('当前窗口标签')
                and s.main_ready()):
            # A known full-screen shell hid the role at initial binding. Once
            # it closes, repeat unique two-frame binding before task actions.
            from hyol_checkpoints import bind_current_identity, load_tasks
            self.account = bind_current_identity(s,self.account)
            self.path,self.records = load_tasks(s.state,self.account.name)
        if not self.generic_current_window:
            self.account = s.bind_visible_account_only()
            from hyol_checkpoints import load_tasks
            self.path, self.records = load_tasks(s.state, self.account.name)

    def return_to_main(self):
        from hyol_navigation import return_to_leaf
        return return_to_leaf(self.scheduler, self.account)

    def sealed(self):
        from hyol_sealed import run
        self.ensure_formation_once()
        return run(self)

    def quiz(self):
        from hyol_quiz import run
        return run(self)

    def ranking(self):
        from hyol_ranking import run
        return run(self)

    def treasure(self):
        from hyol_treasure import run
        return run(self)

    def weekend(self):
        from hyol_weekend import run_all
        return run_all(self)

    def daruma(self):
        from hyol_daruma_task import run
        return run(self)

    def mail(self):
        self.scheduler.module_mail(self.account)

    def welfare(self):
        self.scheduler.module_welfare(self.account)
        if not self.scheduler.welfare_claims_complete(self.account):
            raise hs.PendingError('在线福利尚未全部核实领取')

    def arena(self):
        self.scheduler.arena_victory_target = self.arena_wins
        self.scheduler.accounts = [self.account]
        self.scheduler.prepare_additional_arena_request(self.account, self.arena_wins)
        self.scheduler.run_combat_interleaved('arena')
        if self.scheduler.state.module_status(self.account.name, 'arena') != 'done':
            raise hs.PendingError('竞技场尚未核实每日目标')

    def strong(self):
        from dataclasses import replace
        self.ensure_formation_once()
        # Reuse the established start/result/counter state machine. This
        # current-window client controls only the selected account's auto UI.
        spec = replace(hs.TEAM_ACTIVITY_BY_KEY['strong_approach'], require_all_member_auto=True,
                       return_anchor='strong_approach_anchor.bmp')
        team = hs.Team(0,(self.account.name,))
        rec = self.record('strong')
        self.scheduler.run_verified_strong_approach(team, self.account, spec, target_count=None)
        rec['completed'] = self.scheduler._next_team_attempt_index(team,self.account,spec)-1
        self.save()

    def run(self):
        if not getattr(self, 'selected_tasks', TASK_SEQUENCES.get(self.task, (self.task,))):
            self.scheduler.event('weekend_calendar_skip', detail='非周末；未绑定或识别游戏窗口')
            return 0
        lease = hs.ForegroundRuntimeLease()
        owned = False
        code, detail = 2, ""
        skipped = []
        try:
            owned = lease.acquire()
            if not owned:
                raise hs.PendingError("已有脚本占用游戏窗口，请等其结束")
            if getattr(self, 'run_context', None):
                self.run_context.begin()
            self.scheduler.start_f12_monitor()
            self.bind()
            self.prepare_request()
            for task in getattr(self, 'selected_tasks', TASK_SEQUENCES.get(self.task, (self.task,))):
                self.current = task
                rec = self.record(task)
                if task == 'quiz':
                    from hyol_quiz import solo_verified
                    if not solo_verified(self, self.scheduler.capture()):
                        rec.update(status='pending',last_error='忍者测验仅限单人：当前组队或单人状态未确认，已跳过')
                        skipped.append('忍者测验需要单人状态')
                        self.save()
                        self.event('daily_task_pending',task=task,detail=rec['last_error'])
                        continue
                from hyol_checkpoints import complete
                if (complete(rec) and (task != "group" or not self.group_needs_more(rec))
                        and (task != 'ranking' or rec.get('zero_verified'))
                        and (task != 'daruma' or daruma_verified(rec))):
                    rec["last_error"] = ""
                    self.save()
                    self.event("daily_task_skipped",task=task,detail="本日已有完整完成记录")
                    continue
                rec["status"] = "running"
                self.save()
                self.event("daily_task_started",task=task,detail=LABELS[task]+"开始")
                task_already_finished = rec.get("task_completed", False) and (
                    task != "group" or not self.group_needs_more(rec)) and (task != 'ranking' or rec.get('zero_verified')) and (
                    task != 'daruma' or daruma_verified(rec))
                if task == "survival" and task_already_finished:
                    self.scheduler.reconcile_survival_result_confirmations(self.account)
                if not task_already_finished:
                    try:
                        observation = self.scheduler.begin_task_observation(self.account, task)
                        getattr(self,task)()
                    except hs.WelfareNotReady as exc:
                        rec.update(status='pending', last_error=str(exc))
                        skipped.append(str(exc))
                        self.save()
                        self.event('daily_task_pending', task=task, detail=str(exc))
                        continue
                    finally:
                        self.scheduler.end_task_observation(observation, complete=rec.get('task_completed', False))
                    rec.update(task_completed=True)
                    if task != 'treasure':
                        rec['home_returned'] = False
                    self.save()
                if not rec.get('home_returned'):
                    self.return_to_main()
                rec["home_returned"] = True
                rec.update(status="done",finished_at=hs.now_iso(),last_error="")
                self.save()
                evidence = self.scheduler.save_raw_evidence(self.scheduler.capture(),self.account,"daily_"+task+"_complete","verified")
                self.event("daily_task_complete",task=task,detail=LABELS[task]+"完成并返回主界面",evidence=str(evidence))
            # Even an all-skipped request must verify the current physical
            # page.  Do not invoke the navigation reconciler when the account
            # is already positively on the village main page: an unrelated
            # unresolved action (for example an arena result awaiting audit)
            # must not turn a safe quiz skip into a false return error.
            final_frame = self.scheduler.capture()
            if self.scheduler.main_ready(final_frame):
                self.event("daily_request_main_verified", detail="当前已在木叶主界面，无需重复回村")
            else:
                self.return_to_main()
            if self.task == 'all' and not skipped and getattr(self, 'managed_enabled', True):
                self.finish_solo_managed()
            code = 2 if skipped else 0
            detail = '；'.join(skipped)
        except KeyboardInterrupt:
            code, detail = 130,"F12紧急停止，进度已保留"
        except Exception as exc:
            detail = f"{type(exc).__name__}: {exc}"
            self.event("daily_error", detail=detail)
            if self.current:
                self.record(self.current).update(status="pending",last_error=detail)
                self.save()
            if owned and self.account:
                try:
                    self.scheduler.save_raw_evidence(self.scheduler.capture(),self.account,"daily_client_failure","pending")
                except Exception:
                    pass
        finally:
            self.scheduler.stop.set()
            if owned:
                lease.release()
            summary = dict(result=code,task=self.task,current=self.current,detail=detail,
                           records=self.records,managed_enabled=getattr(self, 'managed_enabled', True),
                           external_input_sessions=getattr(self.scheduler, 'external_input_sessions', 0),
                           external_input_count=getattr(self.scheduler, 'external_input_count', 0),
                           clicks=self.scheduler.content_click_count,ts=hs.now_iso())
            self.last_summary = summary
            result_path = hs.ROOT/("daily_"+self.task+"_result.json")
            previous = read_json(result_path)
            if code != 0 and previous.get("result") == 0:
                hs.atomic_json(hs.ROOT/("daily_"+self.task+"_failure.json"),summary)
            else:
                hs.atomic_json(result_path,summary)
            self.event("daily_summary",**summary)
        return code

    def finish_solo_managed(self):
        """Leave the account managed only after every selected task settled."""
        tasks = self.selected_tasks
        if not tasks or any(
            self.record(task).get('status') != 'done'
            or not self.record(task).get('task_completed')
            or not self.record(task).get('home_returned')
            or task == 'daruma' and not daruma_verified(self.record(task))
            for task in tasks
        ):
            raise hs.PendingError('单人日常仍有未完成或未回村任务，不能开始托管')
        self.scheduler.check_stop()
        self.current = 'managed'
        rec = self.record('managed')
        rec.update(status='running', task_completed=False, last_error='')
        self.save()
        self.event('daily_task_started', task='managed', detail='单人日常完成，开始托管')
        self.scheduler.run_managed_gate(self.account)
        evidence = self.scheduler.save_raw_evidence(
            self.scheduler.capture(), self.account, 'solo_managed_complete', 'verified')
        rec.update(status='done', task_completed=True, finished_at=hs.now_iso(),
                   evidence=str(evidence), last_error='')
        self.save()
        self.event('daily_task_complete', task='managed', detail='已确认托管中', evidence=str(evidence))


class DailyClient(tk.Tk):
    def __init__(self,task, *, config_path=None, generic_current_window=True):
        super().__init__()
        self.task = task
        self.title("火影OL "+LABELS[task]+" "+VERSION)
        self.geometry("800x680+50+100")
        self.events = queue.Queue()
        self.runner = None
        self.worker = None
        self.config_path = config_path
        self.generic_current_window = generic_current_window
        self.count = tk.IntVar(value=5)
        self.arena_wins = tk.IntVar(value=5)
        self.until_zero = tk.BooleanVar(value=True)
        self.managed_enabled = tk.BooleanVar(value=True)
        self.formation = tk.StringVar(value="保持不变")
        self.status = tk.StringVar(value="就绪：请打开当前账号的游戏主界面")
        body = ttk.Frame(self,padding=16)
        body.pack(fill="both",expand=True)
        ttk.Label(body,text=LABELS[task],font=("Microsoft YaHei UI",20,"bold")).pack(anchor="w")
        intro = "锁定当前游戏标签，从日常秘炼进入；沿用已组好的队伍，不校验三人数量。" if task == "team" else "锁定当前游戏标签，不识别账号名称；F12或停止按钮可中断。"
        if task == 'all':
            intro += ' 所选任务全部完成并回木叶后，可按选项进入托管。'
        ttk.Label(body,text=intro,wraplength=710).pack(anchor="w",pady=8)
        self.task_options = {}
        self.task_checks = []
        if task in TASK_SEQUENCES:
            options = TEAM_OPTIONS if task == "team" else SOLO_OPTIONS
            box = ttk.LabelFrame(body, text="勾选本次要执行的任务", padding=6)
            box.pack(fill="x")
            for option in options:
                value = tk.BooleanVar(value=option in TASK_SEQUENCES[task])
                self.task_options[option] = value
                check = ttk.Checkbutton(box, text=LABELS[option]+(' · 队长执行，试运行' if option == 'sealed' else ''), variable=value)
                check.pack(anchor="w")
                self.task_checks.append(check)
        if task in ("group","team"):
            ttk.Label(body,text="消耗次数的任务持续执行，识别游戏剩余次数为0才完成。").pack(anchor="w")
        if task == "all" or task == "arena":
            arena_controls = ttk.Frame(body)
            arena_controls.pack(fill="x", pady=(8, 0))
            ttk.Label(arena_controls, text="竞技场本次追加胜场：").pack(side="left")
            self.arena_spin = ttk.Spinbox(arena_controls, from_=1, to=hs.ARENA_MAX_ATTEMPTS,
                                          textvariable=self.arena_wins, width=6)
            self.arena_spin.pack(side="left")
        ttk.Label(body,text="开战阵容（保持不变时不进入忍者页）：").pack(anchor="w",pady=(8,0))
        self.formation_box = ttk.Combobox(body,textvariable=self.formation,values=tuple(FORMATION_LABELS),state="readonly",width=26)
        self.formation_box.pack(anchor="w")
        if task == 'all':
            self.managed_control = ttk.Checkbutton(body,text='最后进入托管',variable=self.managed_enabled)
            self.managed_control.pack(anchor='w',pady=(8,0))
        controls = ttk.Frame(body)
        controls.pack(fill="x",pady=12)
        self.start_button = ttk.Button(controls,text="开始 / 继续",command=self.start)
        self.start_button.pack(side="left")
        ttk.Button(controls,text="停止",command=self.stop).pack(side="left",padx=12)
        ttk.Label(body,textvariable=self.status,wraplength=710).pack(anchor="w",pady=8)
        from hyol_client_tools import add_diagnostic_controls
        add_diagnostic_controls(body, hs.ROOT, lambda: self.runner.scheduler if self.runner else None)
        self.log = tk.Text(body,state="disabled",wrap="word")
        self.log.pack(fill="both",expand=True)
        self.protocol("WM_DELETE_WINDOW",self.close)
        self.after(150,self.drain)

    def start(self):
        if self.worker and self.worker.is_alive():
            return
        try:
            self.runner = DailyRunner(self.task,0,self.events.put,
                                      formation=FORMATION_LABELS[self.formation.get()],
                                      config_path=self.config_path,
                                      generic_current_window=self.generic_current_window,
                                      selected_tasks=[key for key,value in self.task_options.items() if value.get()] if self.task_options else None,
                                      arena_wins=self.arena_wins.get(),managed_enabled=self.managed_enabled.get())
        except (ValueError,tk.TclError) as exc:
            self.status.set(str(exc))
            return
        self.start_button.configure(state="disabled")
        for check in self.task_checks:
            check.configure(state="disabled")
        if hasattr(self,"spin"):
            self.spin.configure(state="disabled")
            self.until_zero_check.configure(state="disabled")
        if hasattr(self, "arena_spin"):
            self.arena_spin.configure(state="disabled")
        self.formation_box.configure(state="disabled")
        self.status.set("运行中，请保持游戏窗口可见，不切换账号")
        self.iconify()
        self.worker = threading.Thread(target=self.runner.run,daemon=False)
        self.worker.start()

    def stop(self):
        if self.runner:
            self.runner.scheduler.stop.set()

    def close(self):
        if self.worker and self.worker.is_alive():
            self.stop()
            self.after(200,self.close)
        else:
            self.destroy()

    def drain(self):
        try:
            while True:
                event = self.events.get_nowait()
                name = event.get("event","")
                if name.startswith("daily_") or name in ("combat_module_done","combat_slot_pending"):
                    self.log.configure(state="normal")
                    self.log.insert("end",f"{datetime.now():%H:%M:%S} {event.get('detail') or name}\n")
                    self.log.see("end")
                    self.log.configure(state="disabled")
                if name == "daily_summary":
                    self.status.set("完成：所选任务均已核实" if event["result"] == 0 else "未完成："+event["detail"])
                    if event.get("diagnostic_path"):
                        self.status.set(self.status.get()+"\n诊断包："+event["diagnostic_path"])
                    self.start_button.configure(state="normal")
                    for check in self.task_checks:
                        check.configure(state="normal")
                    if hasattr(self,"spin"):
                        self.spin.configure(state="normal")
                        self.until_zero_check.configure(state="normal")
                    if hasattr(self, "arena_spin"):
                        self.arena_spin.configure(state="normal")
                    self.formation_box.configure(state="readonly")
                    self.deiconify()
        except queue.Empty:
            pass
        self.after(150,self.drain)


def main(task="all", *, config_path=None, generic_current_window=True):
    parser = argparse.ArgumentParser(description=LABELS[task]+"独立视觉脚本")
    parser.add_argument("--cli",action="store_true")
    parser.add_argument("--group-count",type=int,default=5)
    parser.add_argument("--arena-wins",type=int,default=5)
    parser.add_argument("--tasks",nargs='+',choices=(*TASKS,'sealed','strong',*SOLO_OPTIONS),help="只执行所选任务，例如 --tasks ranking")
    parser.add_argument("--formation",choices=("keep","main","fast"),default="keep")
    parser.add_argument("--task",choices=(*TASKS,*TASK_SEQUENCES,*SOLO_OPTIONS),default=task)
    args = parser.parse_args()
    task = args.task
    if args.cli:
        return DailyRunner(task,args.group_count,lambda e: print(json.dumps(e,ensure_ascii=False),flush=True) if sys.stdout and e.get("event","").startswith("daily_") else None, formation=args.formation,
                           config_path=config_path,generic_current_window=generic_current_window,selected_tasks=args.tasks,arena_wins=args.arena_wins).run()
    DailyClient(task,config_path=config_path,generic_current_window=generic_current_window).mainloop()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
