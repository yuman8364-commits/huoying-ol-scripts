"""Timed treasure automation shared by current-tab and multi-account clients."""
from __future__ import annotations

import time

import numpy as np

import hyol_scheduler as hs

DURATION_SECONDS = 300
START_ACTION = "daily_treasure_auto_dig_once"
PAUSE_ACTION = "daily_treasure_pause_after_300s"
PAGE_ROI = (560, 250, 1380, 810)
INVENTORY_ROI = (995, 365, 1285, 750)
AUTO_ROI = (800, 720, 970, 795)
CLOSE_ROI = (1290, 295, 1370, 375)
RESULT_ROI = (760, 440, 1170, 670)
REWARD_TITLE_ROI = (850, 480, 1080, 535)
REWARD_CORNERS_ROI = (760, 450, 1170, 510)
TRAP_ROI = (760, 470, 1170, 640)
BATTLE_RESULT_ROI = (800, 330, 1130, 590)
POSITION_ROI = (1745, 119, 1898, 140)
MAP_LOADING_ROI = (600, 525, 1360, 635)
AUTO_PATH_ROI = (800, 140, 1100, 200)
BATTLE_LEFT_CORNER_ROI = (280, 110, 350, 180)
BATTLE_RIGHT_CORNER_ROI = (1570, 110, 1650, 180)
CENTRAL_MODAL_ROI = (760, 470, 1170, 640)


def central_modal_visible(runner, frame):
    return all(runner.visible(name, CENTRAL_MODAL_ROI, frame, .98) for name in (
        'treasure_modal_left_corner_current.bmp',
        'treasure_modal_right_corner_current.bmp'))


def transformation_visible(runner, frame):
    return all(runner.visible(name, CENTRAL_MODAL_ROI, frame, .98) for name in (
        'treasure_transformation_seal_current.bmp',
        'treasure_transformation_duration_current.bmp'))


def loading_visible(runner, frame):
    if not runner.visible('map_loading_word_current.bmp', MAP_LOADING_ROI, frame, .98):
        return False
    return (any(runner.visible(name, MAP_LOADING_ROI, frame, .98) for name in (
                'map_loading_path_current.bmp', 'map_loading_finished_current.bmp'))
            or (runner.visible('map_loading_bar_left_current.bmp', (730,520,790,560), frame, .98)
                and runner.visible('map_loading_bar_right_current.bmp', (1140,520,1190,560), frame, .98)))


def reward_layout(runner,frame):
    """Identify the fixed reward shell without depending on its item icon."""
    if runner.visible('treasure_result_anchor.bmp',RESULT_ROI,frame,.96):
        return 'treasure_result_anchor.bmp',RESULT_ROI,.96
    if (runner.visible('treasure_reward_title_20261030.bmp',REWARD_TITLE_ROI,frame,.98)
            and runner.visible('treasure_reward_left_corner_20261030.bmp',REWARD_CORNERS_ROI,frame,.98)
            and runner.visible('treasure_reward_right_corner_20261030.bmp',REWARD_CORNERS_ROI,frame,.98)
            and runner.visible('treasure_result_confirm.bmp',(880,570,1040,650),frame,.98)):
        return 'treasure_reward_title_20261030.bmp',REWARD_TITLE_ROI,.98
    return None


def settle_result(runner, action):
    frame = runner.scheduler.capture()
    found=reward_layout(runner,frame)
    if not found:return False
    anchor,roi,threshold=found
    status = runner.scheduler.state.action_status(runner.account.name, action)
    if status == 'done':
        raise hs.PendingError('已确认的挖宝奖励再次可见，需对账，不重复点击')
    if status == "pending":
        if not runner.scheduler.wait_absent(anchor,roi,3,threshold=.92):
            raise hs.PendingError("挖宝奖励确认仍未核实，禁止重复点击")
    else:
        runner.click("treasure_result_confirm.bmp", (880,570,1040,650), 60,25,
                     action, [anchor, "treasure_result_confirm.bmp"],
                     irreversible=True, threshold=.96,
                     frame_predicate=lambda fresh:reward_layout(runner,fresh)==found)
        if not runner.scheduler.wait_absent(anchor,roi,8,threshold=.92):
            raise hs.PendingError("挖宝奖励结算框未消失")
    runner.scheduler.commit_action(runner.account, action, "挖宝奖励结算框已确认并消失")
    return True


def open_page(runner):
    if runner.visible("treasure_page_anchor.bmp", PAGE_ROI, threshold=.96):
        return
    toolbar_roi = (651,69,1670,170)
    runner.scheduler.ensure_activity_toolbar(runner.account, 'treasure_toolbar_icon_core_current.bmp', toolbar_roi)
    frame = runner.scheduler.capture()
    # The label crosses animated map pixels. Prefer the strict icon interior,
    # which stays stable between detection and the final input guard.
    target, dx, dy = ('treasure_toolbar_icon_core_current.bmp', 12, 12)
    if not runner.visible(target, toolbar_roi, frame, .98):
        target, dx, dy = ('treasure_toolbar_core_current.bmp', 22, 25)
    runner.click(target, toolbar_roi,dx,dy,
                 "daily_treasure_open", ["anchor_top_menu.bmp", target],
                 threshold=.98, anchor_threshold=.98)
    runner.wait("treasure_page_anchor.bmp", PAGE_ROI,10)


def close_page(runner):
    runner.click("treasure_close.bmp", CLOSE_ROI,27,27,"daily_treasure_close",
                 ["treasure_page_anchor.bmp", "treasure_close.bmp"], threshold=.96)
    if not runner.scheduler.wait_absent("treasure_page_anchor.bmp", PAGE_ROI,8,threshold=.92):
        raise hs.PendingError("藏宝页未确认关闭")


def battle_visible(runner, frame):
    s = runner.scheduler
    # Both ornate corners belong to the normal battle shell, including its
    # opening cutscene before the combat HUD/automatic button is painted.
    # This classification authorizes observation only, never result credit.
    normal_battle = any(
        runner.visible(left, BATTLE_LEFT_CORNER_ROI, frame, .98)
        and runner.visible(right, BATTLE_RIGHT_CORNER_ROI, frame, .98)
        for left, right in (
            ('normal_battle_left_corner_current.bmp', 'normal_battle_right_corner_current.bmp'),
            ('normal_battle_black_left_corner_current.bmp', 'normal_battle_black_right_corner_current.bmp')))
    return (normal_battle or s._combat_active_visible("arena", frame)
            or s._battle_auto_on_visible(frame)
            or runner.visible("battle_auto_off.bmp", hs.BATTLE_AUTO_ROI, frame)
            or runner.visible("treasure_battle_victory.bmp", BATTLE_RESULT_ROI, frame, .96))


def world_visible(runner, frame):
    return (runner.scheduler.main_ready(frame)
            and not loading_visible(runner, frame)
            and not battle_visible(runner, frame)
            and not central_modal_visible(runner, frame)
            and not transformation_visible(runner, frame)
            and not reward_layout(runner,frame)
            and not any(runner.visible(name, roi, frame, .96) for name, roi in (
                ("treasure_page_anchor.bmp", PAGE_ROI),
                ("treasure_result_anchor.bmp", RESULT_ROI),
                ("treasure_trap_anchor.bmp", TRAP_ROI))))


def known_frame(runner, frame):
    return (loading_visible(runner, frame) or world_visible(runner, frame) or battle_visible(runner, frame)
            or transformation_visible(runner, frame)
            or reward_layout(runner,frame) is not None
            or any(runner.visible(name, roi, frame, .96) for name, roi in (
                ("treasure_page_anchor.bmp", PAGE_ROI),
                ("treasure_result_anchor.bmp", RESULT_ROI),
                ("treasure_trap_anchor.bmp", TRAP_ROI))))


def switch_to(runner):
    s, account = runner.scheduler, runner.account
    if s.generic_current_tab_x is not None:
        return s.content_guard(account)
    mapping = s._saved_tab_mapping()
    if not isinstance(mapping, dict) or mapping.get(account.name) != account.tab_x:
        raise hs.PendingError("挖宝账号没有本轮核实的标签映射")
    s._validate_tab_binding(account)
    if s.active != account or not s.tab_is_visually_active(account, s.capture()):
        s.chrome_click(account.tab_x, 10, "treasure_switch:" + account.name, account)
        s.wait_interruptible(.5)
    deadline = time.monotonic() + 12
    while time.monotonic() < deadline:
        s.check_stop()
        frame = s.capture()
        if s.healthy(frame) and s.tab_is_visually_active(account, frame) and known_frame(runner, frame):
            s.active = account
            s._record_tab_binding(account)
            return frame
        s.wait_interruptible(.3)
    raise hs.PendingError("挖宝标签或当前页面未核实，未执行内容点击")


class TreasureRun:
    def __init__(self, runner, *, clock=None):
        self.runner = runner
        self.scheduler = runner.scheduler
        self.record = runner.record("treasure")
        self.clock = clock or time.time
        self.battle_watchdog = None
        suffix = self.record.get('training_action_suffix', '')
        self.start_action = START_ACTION+('_'+suffix if suffix else '')
        self.pause_action = PAUSE_ACTION+('_'+suffix if suffix else '')

    def action_name(self, name):
        suffix = self.record.get('training_action_suffix', '')
        return name+('_'+suffix if suffix else '')

    def save(self, phase=None, **fields):
        if phase:
            self.record["phase"] = phase
        self.record.update(fields)
        self.runner.save()

    def begin(self):
        r, s, rec = self.runner, self.scheduler, self.record
        s.check_stop()
        if rec.get("deadline_at") is not None:
            # A deadline is evidence of a previous start intent, never a new budget.
            return
        old = s.state.action_status(r.account.name, self.start_action)
        if old != "new":
            raise hs.PendingError("旧挖宝动作缺少可核实的开始时间；先对账，不重新消耗地图")
        open_page(r)
        frame = s.capture()
        if not r.visible("treasure_map_item.bmp", INVENTORY_ROI, frame, .96):
            close_page(r)
            raise hs.PendingError("当前宝藏页没有可用藏宝图，未执行购买")
        request = getattr(r, 'training_request', None)
        if isinstance(request, dict) and request.get('task') == 'treasure':
            # Presence proves one minimum operation, not the stack's total size.
            s.wait_interruptible(.2)
            if not r.visible('treasure_map_item.bmp',INVENTORY_ROI,s.capture(),.96):
                raise hs.PendingError('藏宝图可用状态未连续确认，未启动挖宝')
            request.update(available=1,available_lower_bound=True,scheduled=1,quota_unit='maps',
                           quantity_state='verified')
        if not r.visible("treasure_auto_dig.bmp", AUTO_ROI, frame, .96):
            close_page(r)
            raise hs.PendingError("自动挖宝按钮未稳定识别")
        started = self.clock()
        self.save("start_pending", status="running", started_at=started,
                  deadline_at=started+DURATION_SECONDS, duration_seconds=DURATION_SECONDS,
                  task_completed=False, home_returned=False, rewards=0, traps=0)
        r.click("treasure_auto_dig.bmp", AUTO_ROI,62,21,self.start_action,
                ["treasure_page_anchor.bmp", "treasure_auto_dig.bmp", "treasure_map_item.bmp"],
                irreversible=True, threshold=.96)
        if not s.wait_absent("treasure_page_anchor.bmp", PAGE_ROI,10,threshold=.92):
            raise hs.PendingError("自动挖宝启动结果不明，保留原截止时间")
        s.commit_action(r.account, self.start_action, "自动挖宝已启动；任务尚未完成")
        self.save("running")
        r.event("treasure_timer_started", started_at=started,
                deadline_at=rec["deadline_at"], duration_seconds=DURATION_SECONDS)

    def _confirm_battle_result(self, frame):
        r, s, rec = self.runner, self.scheduler, self.record
        if not r.visible("treasure_battle_victory.bmp", BATTLE_RESULT_ROI, frame,.96):
            return False
        action = self.action_name("treasure_battle_result_" + str(rec.get("battle_results", 0)+1))
        if s.state.action_status(r.account.name, action) != "new":
            raise hs.PendingError("挖宝战斗确认已发送，等待原结果，不重复确认")
        self.save(awaiting={'action': action, 'kind': 'battle_results',
                           'index': rec.get('battle_results', 0)+1,
                           'anchor': 'treasure_battle_victory.bmp', 'roi': list(BATTLE_RESULT_ROI)})
        try:
            r.click("treasure_battle_confirm.bmp", (880,600,1050,690),62,20,action,
                    ["treasure_battle_victory.bmp", "treasure_battle_confirm.bmp"],
                    irreversible=True, threshold=.96)
        except (hs.SafetyError, hs.PendingError):
            # A result can auto-dismiss between detection and the input guard.
            # click_point writes pending before sending any irreversible input.
            # A still-new action therefore proves no confirmation was sent.
            current = s.capture()
            if (s.state.action_status(r.account.name, action) == 'new'
                    and not r.visible('treasure_battle_victory.bmp', BATTLE_RESULT_ROI, current, .96)
                    and known_frame(r, current)):
                self.save('running', awaiting=None)
                r.event('treasure_result_auto_dismissed', action=action,
                        confirmation_inputs=0, result_count_incremented=False)
                return True
            raise
        if not s.wait_absent("treasure_battle_victory.bmp", BATTLE_RESULT_ROI,8,threshold=.92):
            raise hs.PendingError("挖宝战斗结算尚未关闭")
        s.commit_action(r.account, action, "挖宝胜利结果已关闭")
        self.save("running", battle_results=rec.get("battle_results",0)+1, awaiting=None)
        return True

    def _pause(self, frame):
        r, s, rec = self.runner, self.scheduler, self.record
        if not world_visible(r, frame):
            return False
        if not (hs.ASSETS/'treasure_auto_path_running.bmp').exists():
            raise hs.PendingError('挖宝自动寻路识别素材缺失，不能发送暂停或证明停止')
        if not rec.get('stop_requested') and s.state.action_status(r.account.name, self.pause_action) == "new":
            # Ground points stay away from the HUD, map, task list, NPC cards and panels.
            candidates = [(x,y) for y in (550,650,740) for x in (820,1000,1180)]
            point = min(candidates, key=lambda p: float(np.asarray(
                frame.crop((p[0]-12,p[1]-12,p[0]+12,p[1]+12)).convert("L")).std()))
            ground = np.asarray(frame.crop((point[0]-12,point[1]-12,point[0]+12,point[1]+12)).convert('RGB'))
            if float(ground.std(axis=(0,1)).max()) > 15 or not 30 < float(ground.mean()) < 240:
                evidence = s.save_raw_evidence(frame, r.account, 'treasure_ground_unverified', 'pending')
                raise hs.PendingError('没有核实的非战斗空白区域，未发送暂停：' + str(evidence))
            guarded = s.content_guard(r.account, ["anchor_top_menu.bmp"])
            if not world_visible(r, guarded):
                return False
            guarded_ground = np.asarray(guarded.crop((point[0]-12,point[1]-12,point[0]+12,point[1]+12)).convert('RGB'))
            if float(guarded_ground.std(axis=(0,1)).max()) > 15 or not 30 < float(guarded_ground.mean()) < 240:
                return False
            self.save("stop_requested", pause_point=list(point), stop_requested=True,
                      stop_requested_at=self.clock())
            s.click_point(r.account, *point, self.pause_action, ["anchor_top_menu.bmp"], irreversible=True)
            r.event("treasure_pause_sent", point=list(point), deadline_at=rec["deadline_at"])
        # The user-confirmed ground click interrupts automatic digging. Verify
        # its effect with a settled map-position caption, no modal/battle and no
        # positive automatic-path indicator; a click or elapsed time alone is insufficient.
        # One observation per foreground visit: other accounts keep their own
        # deadlines and are never blocked by this account's pause verification.
        now = self.clock()
        if now - rec.get('stop_observed_at', 0) < 3:
            return False
        frame = s.capture()
        patch = frame.crop(POSITION_ROI).convert('L')
        nonempty = float(np.asarray(patch).std()) > 8
        running = r.visible('treasure_auto_path_running.bmp', AUTO_PATH_ROI, frame,.96)
        current = hs.image_hash(patch)
        stable = (rec.get('stop_stable_frames', 0)+1 if current == rec.get('stop_position_hash') else 1)
        if not world_visible(r, frame) or not nonempty or running:
            stable = 0
        self.save(stop_position_hash=current, stop_stable_frames=stable, stop_observed_at=now)
        if stable >= 3:
            evidence = s.save_raw_evidence(frame,r.account,'treasure_auto_stopped','verified')
            s.commit_action(r.account,self.pause_action,'暂停后位置连续三帧稳定，非战斗且无自动寻路标识')
            self.save('stopped', stop_verified=True, stop_evidence=str(evidence))
            return True
        if now - rec.get('stop_requested_at', now) >= 30:
            evidence = s.save_raw_evidence(frame,r.account,'treasure_stop_unverified','pending')
            raise hs.PendingError('挖宝暂停未核实，不重复点击；画面='+str(evidence))
        return False

    def _reconcile_awaiting(self, frame):
        rec, r, s = self.record, self.runner, self.scheduler
        waiting = rec.get('awaiting')
        if not waiting:
            return False
        status = s.state.action_status(r.account.name, waiting['action'])
        if status == 'new':
            if (waiting['kind']=='rewards'
                    and reward_layout(r,frame) is None
                    and known_frame(r,frame)):
                # A reward can auto-dismiss before its guarded click. No input
                # was sent, so release only this intent; award no reward credit.
                self.save('running',awaiting=None)
                r.event('treasure_reward_auto_dismissed',action=waiting['action'],
                        confirmation_inputs=0,reward_count_incremented=False)
                return True
            raise hs.PendingError('挖宝确认意图已保存但发送状态不明，保留断点')
        if ((waiting['kind']=='rewards' and reward_layout(r,frame) is not None)
                or r.visible(waiting['anchor'], tuple(waiting['roi']), frame, .96)):
            raise hs.PendingError('原挖宝确认框仍显示，不重复确认')
        advanced = battle_visible(r, frame) if waiting['kind'] == 'traps' else known_frame(r, frame)
        if waiting['kind'] == 'transformations':
            advanced = advanced and not central_modal_visible(r, frame) and not transformation_visible(r, frame)
        if not advanced:
            raise hs.PendingError('挖宝确认后页面未知，保留原结果')
        s.commit_action(r.account, waiting['action'], '原确认框消失且后续页面已核实')
        self.save('running', **{waiting['kind']: waiting['index']}, awaiting=None,
                  stop_due=bool(rec.get('training_single_cycle')))
        return True

    def step(self):
        r, s, rec = self.runner, self.scheduler, self.record
        s.check_stop()
        if rec.get("status") == "done" and rec.get("task_completed") and rec.get("home_returned"):
            return True
        if rec.get("deadline_at") is None:
            self.begin()
        frame = s.capture()
        expired = self.clock() >= rec["deadline_at"] or bool(rec.get("training_single_cycle") and rec.get("stop_due"))
        if expired:
            self.save(stop_due=True)
        if rec.get('stop_requested') and not world_visible(r, frame):
            self.save(stop_stable_frames=0, stop_position_hash=None)
        if loading_visible(r, frame):
            since = rec.get('transition_started_at')
            if since is None:
                since = self.clock()
            self.save('loading', transition_started_at=since)
            if self.clock()-since >= 60:
                raise hs.PendingError('挖宝地图加载60秒未推进，保留原截止时间，不暂停或重新启动')
            r.event('treasure_map_loading', remaining=max(0, int(rec['deadline_at']-self.clock())))
            return False
        if rec.get('transition_started_at') is not None:
            self.save(transition_started_at=None)
        if self._reconcile_awaiting(frame):
            return False
        reward=reward_layout(r,frame)
        if reward:
            index = rec.get("rewards",0)+1
            self.save(awaiting={'action': self.action_name('daily_treasure_reward_' + str(index)), 'kind': 'rewards',
                               'index': index, 'anchor': reward[0], 'roi': list(reward[1])})
            if settle_result(r, self.action_name("daily_treasure_reward_" + str(index))):
                self.save("running",rewards=index,awaiting=None,stop_due=bool(rec.get("training_single_cycle")))
            return False
        if self._confirm_battle_result(frame):
            self.battle_watchdog = None
            return False
        if transformation_visible(r, frame):
            index = rec.get('transformations', 0)+1
            action = self.action_name('treasure_transformation_' + str(index))
            if s.state.action_status(r.account.name, action) != 'new':
                raise hs.PendingError('变身提示确认已发送，等待原结果，不重复确认')
            self.save(awaiting=dict(action=action, kind='transformations', index=index,
                                    anchor='treasure_transformation_seal_current.bmp', roi=list(CENTRAL_MODAL_ROI)))
            r.click('treasure_transformation_confirm_current.bmp', (880, 565, 1040, 625), 50, 16,
                    action, ['treasure_transformation_seal_current.bmp', 'treasure_transformation_duration_current.bmp'],
                    irreversible=True, threshold=.98, anchor_threshold=.98)
            if not s.wait_absent('treasure_transformation_seal_current.bmp', CENTRAL_MODAL_ROI, 8, threshold=.98):
                raise hs.PendingError('变身提示尚未关闭，保留原确认和截止时间')
            current = s.capture()
            if not known_frame(r, current) or central_modal_visible(r, current) or transformation_visible(r, current):
                raise hs.PendingError('变身确认后页面未核实，保留原动作')
            s.commit_action(r.account, action, '变身提示已关闭且后续页面已核实')
            self.save('running', transformations=index, awaiting=None,
                      stop_due=bool(rec.get('training_single_cycle')))
            return False
        if r.visible("treasure_trap_anchor.bmp", TRAP_ROI, frame,.96):
            index = rec.get("traps",0)+1
            action = self.action_name("treasure_trap_" + str(index))
            if s.state.action_status(r.account.name,action) != "new":
                raise hs.PendingError("埋伏确认未核实，禁止重复发送")
            self.save(awaiting={'action': action, 'kind': 'traps', 'index': index,
                               'anchor': 'treasure_trap_anchor.bmp', 'roi': list(TRAP_ROI)})
            r.click("treasure_trap_confirm.bmp", (880,540,1040,630),60,27,action,
                    ["treasure_trap_anchor.bmp","treasure_trap_confirm.bmp"],
                    irreversible=True,threshold=.96)
            if not s.wait_absent("treasure_trap_anchor.bmp",TRAP_ROI,8,threshold=.92):
                raise hs.PendingError("埋伏确认后页面未变化")
            s.commit_action(r.account,action,"埋伏已进入原战斗")
            self.save("battle",traps=index,awaiting=None,stop_due=bool(rec.get('training_single_cycle')))
            return False
        if battle_visible(r,frame):
            if self.battle_watchdog is None:
                self.battle_watchdog = hs.BattleWatchdog.from_config(s.config)
            hs.check_battle_watchdog(s, self.battle_watchdog, r.account, frame,
                                    'treasure_battle', mode='treasure')
            if r.visible("battle_auto_off.bmp",hs.BATTLE_AUTO_ROI,frame):
                r.click("battle_auto_off.bmp",hs.BATTLE_AUTO_ROI,29,29,
                        "treasure_battle_auto",["battle_auto_off.bmp"])
                if not s.wait_template("battle_auto_on_current_core.bmp", hs.BATTLE_AUTO_ROI,5):
                    if not s._battle_auto_on_visible():
                        raise hs.PendingError("挖宝战斗自动状态未核实")
            self.save("battle")
            return False
        if not known_frame(r,frame):
            if expired:
                raise hs.PendingError("挖宝当前画面未知，未暂停、未重启")
            since=rec.get('unknown_started_at')
            if since is None:
                since=self.clock()
                evidence=s.save_raw_evidence(frame,r.account,'treasure_unknown_observation','pending')
                self.save(unknown_started_at=since,unknown_evidence=str(evidence))
            if not expired and self.clock()-since<10:
                r.event('treasure_unknown_observation',deadline_at=rec['deadline_at'],content_clicks=0)
                return False
            raise hs.PendingError("挖宝当前画面未知，未暂停、未重启")
        if rec.get('unknown_started_at') is not None:
            self.save(unknown_started_at=None)
        if world_visible(r, frame):
            self.battle_watchdog = None
        if r.visible('treasure_page_anchor.bmp', PAGE_ROI, frame, .96):
            raise hs.PendingError('挖宝启动或暂停状态未核实，保留原计时，不重新启动')
        if not expired:
            r.event("treasure_timer",remaining=max(0,int(rec["deadline_at"]-self.clock())))
            return False
        if rec.get("stop_verified") or self._pause(frame):
            from hyol_navigation import return_to_leaf
            pending = [key for key, row in s.state.accounts[r.account.name].get('actions', {}).items()
                       if isinstance(row, dict) and row.get('status') == 'pending']
            if any(key.startswith(('daily_treasure_', 'treasure_')) for key in pending):
                raise hs.PendingError('挖宝原动作尚未全部确认，保留已停止的计时，不回村或消耗新地图')
            # The paused run owns this navigation. Unrelated historical actions
            # remain pending; they are never used as evidence for this result.
            navigation_options = {'stopped_treasure_return': True}
            if pending:navigation_options['allow_pending_navigation']=True
            if not return_to_leaf(s,r.account, **navigation_options):
                raise hs.PendingError("挖宝已暂停，但安全回村未完成")
            self.save("returned", status="done",task_completed=True,home_returned=True,
                      finished_at=hs.now_iso(), last_error="")
            r.event("treasure_complete",rewards=rec.get("rewards",0),
                    started_at=rec["started_at"],deadline_at=rec["deadline_at"])
            return True
        return False


def run(runner):
    slot = TreasureRun(runner)
    slot.begin()
    while not slot.step():
        runner.scheduler.wait_interruptible(3)


def run_multi(coordinator, *, resume_only=False):
    """Start eligible accounts, then cooperatively poll each original five-minute run."""
    s = coordinator.scheduler
    slots = {}
    for account in tuple(s.accounts):
        r = coordinator.runner_for(account)
        rec = r.record('treasure')
        resuming = rec.get('deadline_at') is not None and not (
            rec.get('status') == 'done' and rec.get('task_completed') and rec.get('home_returned'))
        if resume_only and not resuming:
            continue
        if ((account.name not in coordinator.startup_returned and not resuming)
                or account.name in coordinator.deferred):
            coordinator.note_skipped(account,"treasure")
            continue
        if rec.get("status") == "done" and rec.get("task_completed") and rec.get("home_returned"):
            continue
        try:
            if rec.get("deadline_at") is not None:
                switch_to(r)
            elif not coordinator.check_account(account):
                coordinator.note_skipped(account,"treasure")
                continue
            slot = TreasureRun(r)
            slot.begin()
            slots[account.name] = slot
        except hs.AccountOffline as exc:
            rec.update(status='pending', last_error=str(exc))
            r.save()
            coordinator.skip_disconnected(account, 'treasure')
            continue
        except (hs.PendingError,hs.SafetyError) as exc:
            rec.update(status="pending",last_error=str(exc))
            r.save()
            coordinator.event("multi_solo_task_pending",account,task="treasure",detail=str(exc))
            if s.active == account and not known_frame(r,s.capture()):
                raise hs.LeaseBlocked("挖宝页面未核实，不能切换下一账号") from exc
    while slots:
        for name, slot in tuple(slots.items()):
            s.check_stop()
            try:
                switch_to(slot.runner)
                if slot.step():
                    coordinator.startup_returned.add(name)
                    coordinator.startup_pending.pop(name, None)
                    slots.pop(name)
            except hs.AccountOffline as exc:
                slot.save(status='pending', last_error=str(exc))
                coordinator.skip_disconnected(slot.runner.account, 'treasure')
                slots.pop(name)
                continue
            except (hs.PendingError,hs.SafetyError) as exc:
                slot.save(status="pending",last_error=str(exc))
                coordinator.event("multi_solo_task_pending",slot.runner.account,task="treasure",detail=str(exc))
                if not known_frame(slot.runner,s.capture()):
                    raise hs.LeaseBlocked("挖宝当前页面未知，保留所有计时断点") from exc
                slots.pop(name)
            coordinator.save_progress("treasure")
        if slots:
            try:
                s.wait_interruptible(3)
            except hs.AccountOffline as exc:
                coordinator.skip_disconnected(exc.account, 'treasure')
                slot = slots.pop(exc.account.name, None)
                if slot is not None:
                    slot.save(status='pending', last_error=str(exc))
