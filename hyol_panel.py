from __future__ import annotations

import json
import queue
import re
import threading
from datetime import date, datetime
from pathlib import Path
import tkinter as tk
from tkinter import messagebox, ttk

import hyol_scheduler as hs


COMMAND_LABELS = {
    "full": "一键全流程",
    "recognize": "识别六标签（零内容点击）",
    "mail": "邮箱",
    "welfare": "福利",
    "team1": "第一队组队",
    "team2": "第二队组队",
    "arena": "竞技场",
    "survival": "生存",
    "market": "黑市查看/购买",
    "managed": "托管",
    "all_day": "全天值守",
}

MODULE_COLUMNS = ("mail", "welfare", "team1", "team2", "arena", "survival", "market", "managed", "all_day")
PANEL_COMMAND_ORDER = ("full", "recognize", "mail", "welfare", "team1", "team2", "arena", "survival", "market", "managed", "all_day")
COMPOSITE_ORDER = ("recognize", "mail", "welfare", "team1", "team2", "arena", "survival", "market", "managed")


def composite_commands(selected, arena_wins=1, group_count=5, strong_count=7):
    selected = tuple(selected)
    if not selected or len(set(selected)) != len(selected) or any(k not in COMPOSITE_ORDER for k in selected):
        raise ValueError("请至少勾选一项有效任务")
    return tuple(hs.SchedulerCommand(
        kind, arena_victory_target=arena_wins if kind == "arena" else 1,
        group_instance_count=group_count if kind in ("team1", "team2") else None,
        strong_approach_count=strong_count if kind in ("team1", "team2") else None,
    ) for kind in COMPOSITE_ORDER if kind in selected)

STATUS_LABELS = {
    "new": "未开始",
    "running": "运行中",
    "pending": "待处理",
    "quarantined": "已隔离",
    "done": "完成",
    "done_no_reward": "无奖励已跳过",
    "waiting": "等待中",
    "complete": "完成",
    "stopped": "已停止",
    "unknown": "未知",
}

MODULE_LABELS = {
    "mail": "邮箱",
    "welfare": "福利",
    "arena": "竞技场",
    "survival": "生存",
    "market": "黑市",
    "managed": "托管",
    "all_day": "全天值守",
    "full": "全流程",
    "independent": "独立任务",
    "team": "组队任务",
    "team1": "第一队",
    "team2": "第二队",
    "post_team_combat": "组队后战斗",
}

TEAM_ACTIVITY_LABELS = {
    "wanted": "组队通缉",
    "group_instance": "组队副本",
    "strong_approach": "强者挑战",
    "rescue_jinchuriki": "营救人柱力",
    "team_pvp": "组队竞技",
    "bond": "羁绊玩法",
}

EVENT_LABELS = {
    "command_start": "开始执行命令",
    "arena_request_ready": "本轮胜利目标已登记",
    "arena_auto_enabled": "自动战斗已开启并确认取消按钮",
    "combat_transition_wait": "等待战斗过渡画面",
    "single_account_test_bound": "当前账号已核实",
    "single_account_tab_switch_suppressed": "保持当前账号，不切换标签",
    "command_complete": "命令完成",
    "command_pending": "命令待处理",
    "command_summary": "命令结果",
    "command_exit": "命令退出",
    "panel_worker_error": "任务线程异常",
    "panel_worker_finished": "任务线程结束",
    "pause_requested": "已请求模块完成后暂停",
    "scheduler_paused": "调度已暂停",
    "scheduler_start": "调度器启动",
    "scheduler_exit": "调度器退出",
    "scheduler_stopped": "调度器已停止",
    "runtime_lease_acquired": "已取得前台操作锁",
    "runtime_lease_released": "已释放前台操作锁",
    "runtime_lease_blocked": "前台操作锁被占用",
    "runtime_lease_error": "前台操作锁不可用",
    "window_bound": "已绑定游戏窗口",
    "window_wait": "等待唯一健康游戏窗口",
    "window_unavailable": "游戏窗口不可用",
    "account_bound": "已绑定账号标签",
    "combat_account_bound": "已绑定战斗账号标签",
    "combat_account_bound_by_page": "已通过战斗页面绑定账号标签",
    "combat_identity_wait": "等待战斗页身份稳定",
    "identity_rebound": "六标签身份重绑完成",
    "identity_rebind_offline": "发现账号掉线",
    "identity_rebind_recovered_page": "已恢复已验证页面",
    "identity_rebind_recovery_rejected": "页面恢复被安全门拒绝",
    "identity_rebind_occluded_page": "战斗页身份遮挡，沿用已验证标签",
    "identity_rebind_white_tab_refresh": "白屏标签准备一次可信刷新",
    "identity_rebind_pending": "身份重绑待处理",
    "recognize_pass": "身份识别完成一轮",
    "recognize_pending": "身份识别待处理",
    "stage_complete": "阶段完成",
    "stage_pending": "阶段待处理",
    "module_start": "模块开始",
    "module_done": "模块完成",
    "module_pending": "模块待处理",
    "module_retry": "模块准备重试",
    "module_skip_done": "模块已完成，跳过重复操作",
    "module_repair_required": "模块需要补齐历史记录",
    "module_safety_pending": "模块触发安全待处理",
    "module_exception_pending": "模块异常待处理",
    "module_no_repeat_after_click": "点击后结果未知，禁止重复点击",
    "content_click": "已执行内容点击",
    "chrome_click": "已执行标签栏操作",
    "template_match": "已匹配界面模板",
    "irreversible_committed": "不可逆动作已确认",
    "lease_release_ready": "已确认回到主界面",
    "lease_release_wait": "等待确认回到主界面",
    "lease_release_blocked": "暂不释放账号操作锁",
    "lease_recovery_rejected": "账号页面恢复被拒绝",
    "combat_interleaved_start": "交错战斗开始",
    "combat_interleaved_complete": "交错战斗完成",
    "combat_slot_prepared": "战斗账号准备完成",
    "combat_slot_pending": "战斗账号待处理",
    "combat_battle_started": "战斗开始",
    "combat_battle_finished": "战斗结束",
    "combat_module_done": "战斗模块完成",
    "arena_victory_confirmed": "竞技场胜利已确认",
    "arena_defeat_confirmed": "竞技场失败已确认",
    "arena_daily_reward_claimed": "竞技场每日奖励已领取",
    "arena_daily_rewards_verified": "竞技场每日奖励已核对",
    "emergency_stop": "已触发紧急停止",
    "team_leader": "已确认队长",
    "team_membership_reconciled": "队伍成员核对完成",
    # The event codes are historical and remain in JSONL for compatibility;
    # their panel wording must also be correct for two-person bond teams.
    "team_three_member_template_enrolled": "已登记队伍成员模板",
    "team_three_member_template_pending": "队伍成员模板待处理",
    "team_activity_done": "组队活动完成",
    "team_activity_attempt_completed": "组队活动单场完成",
    "team_activity_skip_no_reward": "组队活动无奖励，按策略跳过",
    "team_reward_counter_verified": "组队活动奖励次数已确认",
    "team_reward_counter_pending": "组队活动奖励次数待识别",
    "team_reward_zero_verified": "组队活动奖励次数已归零",
    "team_member_auto_verified": "队员自动战斗已确认",
    "team_member_auto_pending": "队员自动战斗待确认",
    "team_all_members_auto_verified": "全队自动战斗已确认",
    "team_rebuild_required": "队伍需要人工重建",
    "team_pending": "队伍待处理",
    "team_verified_daily_complete": "队伍全部活动完成",
    "team_verified_daily_skip_done": "队伍已完成，跳过重复操作",
    "team_verified_daily_deferred": "队伍其余活动完成，暂缓项目保留",
    "team_dissolve_skip_done": "队伍已解散，跳过重复操作",
    "invite_already_sent": "邀请已记录，跳过重复发送",
    "invite_retry_after_overlay": "清理遮挡后准备重试邀请",
    "invite_retry_wait": "等待邀请遮挡重试",
    "invite_not_visible_after_wait": "等待后仍未出现组队邀请",
    "managed_warmup_start": "托管暖机开始",
    "managed_warmup_complete": "托管暖机完成",
    "managed_pending": "托管待处理",
    "market_pending": "黑市待处理",
    "market_read_only": "黑市只读核对完成",
    "market_purchase_cycle_done": "黑市批次完成",
    "market_wait": "等待黑市批次开放",
    "market_offer_purchased": "黑市商品购买完成",
    "market_offer_skip_done": "黑市商品已完成，跳过重复操作",
    "market_offer_sold_out": "黑市商品已售罄",
    "market_offer_skip_no_item": "黑市没有目标商品",
    "market_offer_skip_non_coupon": "黑市货币类型不符合策略",
    "market_offer_skip_insufficient_coupon": "黑市点券不足",
    "all_day_wait": "全天值守等待批次",
    "all_day_catch_up": "全天值守补做历史批次",
    "all_day_cycle": "全天值守批次结果",
    "all_day_cycle_skip_done": "全天值守批次已完成",
    "all_day_account_pending": "全天值守账号待处理",
    "all_day_account_quarantined": "全天值守账号已隔离",
    "all_day_paused": "全天值守已暂停",
    "all_day_complete": "全天值守完成",
    "activity_notice_closed": "活动提示已关闭",
    "white_tab_confirmed": "确认标签白屏",
    "white_tab_recovered": "标签白屏已恢复",
    "account_bound_by_verified_team_room": "已沿用已核验队伍标签绑定",
    "account_bound_by_verified_team_battle": "已沿用已核验队伍战斗标签绑定",
    "account_bound_by_verified_team_activity": "已沿用已核验队伍活动标签绑定",
    "team_battle_membership_resume": "已沿用战斗画面核验队伍成员",
    "team_activity_membership_resume": "已沿用活动页面核验队伍成员",
    "team_activity_resume_page": "已从活动页面继续执行",
    "team_activity_direct_start": "已按页面直接进入战斗",
    "team_activity_start_retry_same_page": "首次点击未切页，执行一次有界重试",
    "team_rebuild_after_membership_loss": "队伍成员减少后已准备重建",
    "team_activity_pending_missing_assets": "组队活动模板缺失，队伍保留",
    "team_activity_team_size_pending": "组队人数不符，活动待处理",
    "rescue_template_pending": "营救难度模板待处理",
    "rescue_template_captured": "营救难度模板采集完成",
    "rescue_participate_disabled_recovered": "营救未开放按钮已安全恢复",
    "rescue_participate_outcome_pending": "营救参与结果未知，已暂停",
    "rescue_calendar_pending": "营救今日未开放",
    "rescue_template_source_after_participate": "营救参与后页面证据已保存",
    "team_activity_participated": "队长已确认免费参与组队活动",
    "team_battle_start_pending": "队长战斗开始结果未知，队伍保留",
    "team_auto_enabled": "队长已确认自动战斗开启",
    "team_auto_already_enabled": "队长已确认自动战斗原已开启",
    "team_battle_timeout": "队长战斗等待超时，队伍保留",
    "market_offer_recheck_known_insufficient": "黑市点券不足状态已重新核对",
    "market_offer_skip_insufficient_coupon": "黑市点券不足，暂不购买",
    "market_buy_confirmation_retry": "黑市购买确认框未出现，准备有限重试",
    "market_buy_confirmation_missing": "黑市购买确认框未出现",
}

FIELD_LABELS = {
    "account": "账号",
    "module": "模块",
    "command": "命令",
    "mode": "模式",
    "stage": "阶段",
    "activity": "活动",
    "team": "队伍",
    "leader": "队长",
    "status": "状态",
    "result": "结果码",
    "pending": "待处理数",
    "clicks": "点击数",
    "content_clicks": "内容点击数",
    "attempt": "尝试次数",
    "index": "序号",
    "next_index": "下一序号",
    "seconds": "秒数",
    "cycle": "批次",
    "latest_cycle": "最新批次",
    "failures": "失败数",
    "reason": "原因",
    "detail": "详情",
    "evidence": "证据",
    "raw_evidence": "原始证据",
    "claims": "领取数",
    "wins": "胜利数",
    "member_count": "成员数",
    "required_member_count": "要求人数",
    "remaining": "剩余奖励次数",
    "maximum": "次数上限",
    "display": "界面状态",
}


def _label_status(value: object) -> str:
    return STATUS_LABELS.get(str(value), "待处理")


def _label_module(value: object) -> str:
    text = str(value)
    if text in MODULE_LABELS:
        return MODULE_LABELS[text]
    match = re.fullmatch(r"team([12])_(.+)", text)
    if match:
        team = "第一队" if match.group(1) == "1" else "第二队"
        activity = TEAM_ACTIVITY_LABELS.get(match.group(2), "组队活动")
        return f"{team}{activity}"
    if text.startswith("market_"):
        return "黑市批次"
    if text.startswith("all_day_"):
        return "全天值守批次"
    return "当前模块"


def _label_command(value: object) -> str:
    return COMMAND_LABELS.get(str(value), "当前命令")


def _label_mode(value: object) -> str:
    return {"arena": "竞技场", "survival": "生存"}.get(str(value), "当前战斗")


def _label_stage(value: object) -> str:
    return _label_module(value)


def _label_activity(value: object) -> str:
    return TEAM_ACTIVITY_LABELS.get(str(value), "组队活动")


def _label_cycle(value: object) -> str:
    """Render internal batch keys without exposing machine-only codes."""
    text = str(value)
    if text == "PRE08":
        return "未开放"
    if re.fullmatch(r"\d{4}", text):
        return f"{text[:2]}:{text[2:]}"
    return "当前批次"


def _label_action(value: object) -> str:
    text = str(value)
    if text.startswith("arena_ranked_battle_"):
        return "竞技场第" + text.rsplit("_", 1)[-1] + "场"
    if text.startswith("arena_daily_reward_claim_") or text.startswith("arena_reward_claim_"):
        return "竞技场每日奖励第" + text.rsplit("_", 1)[-1] + "项"
    if text == "arena_daily_rewards":
        return "竞技场每日奖励核对"
    if text == "arena_victory_1":
        return "竞技场胜利确认"
    if text.startswith("arena_rankup_confirm_"):
        return "竞技场段位确认"
    if text.startswith("arena_defeat_confirm_"):
        return "竞技场失败确认"
    if text.startswith("arena_result_confirm_"):
        return "竞技场结果确认"
    if text.startswith("survival_battle_step_"):
        return "生存第" + text.rsplit("_", 1)[-1] + "关"
    if text.startswith("welfare_claim_"):
        return "福利第" + text.rsplit("_", 1)[-1] + "项"
    if text.startswith("team"):
        match = re.match(r"team([12])_(.+)", text)
        if match:
            team = "第一队" if match.group(1) == "1" else "第二队"
            return team + "组队操作"
    if text.startswith("market_"):
        return "黑市操作"
    if text.startswith("managed") or text.startswith("start_managed"):
        return "托管操作"
    return "当前操作"


def _translate_detail(value: object) -> str:
    """Return Chinese-only user-facing detail while keeping raw JSONL intact."""
    text = str(value or "")
    if not text:
        return ""
    exact = {
        "F12_or_keyboard_interrupt": "已按下紧急停止键或收到停止信号",
        "another scheduler owns the shared-window runtime lease": "其他调度任务正在使用共享窗口",
        "account is offline and requires manual login; content clicks disabled": "账号掉线，需要人工登录，已禁止内容点击",
        "offline login prompt requires manual login": "发现掉线登录提示，需要人工登录",
        "identity and page-anchor guard failed": "身份和页面锚点核对失败",
        "content clicks disabled by invocation": "本次调用已禁止内容点击",
        "team activity task list is not positively empty; team retained": "组队活动列表未确认为空，队伍保持不解散",
        "one arena victory and two daily reward claims verified": "已确认竞技场胜利1场并领取左侧每日2项奖励",
        "two left-side daily reward claims verified": "已确认左侧每日2项奖励",
        "arena daily reward claim outcome unknown; refusing duplicate": "竞技场每日奖励结果未知，为避免重复操作已停止",
        "arena battle is recorded but no positive victory was confirmed": "竞技场战斗已有记录，但尚未确认胜利",
        "arena returned without a positively confirmed victory or defeat": "竞技场返回页面但胜负结果未确认",
        "arena returned without a positive victory marker": "竞技场返回页面但未发现胜利凭证",
        "arena pending battle returned without a positive victory marker": "竞技场待处理战斗返回但未发现胜利凭证",
        "arena result confirmation disappeared": "竞技场结果确认框已消失",
        "rank-up confirmation matched and acknowledged; victory verified": "段位确认框已匹配并确认，胜利已核实",
        "arena rank-up confirmation outcome unknown; refusing duplicate": "竞技场段位确认结果未知，为避免重复操作已停止",
        "arena defeat confirmation outcome unknown; refusing duplicate": "竞技场失败确认结果未知，为避免重复操作已停止",
        "arena defeat confirmation remained after click": "竞技场失败确认框点击后仍存在",
        "arena configured one-win target was not positively completed": "竞技场一次胜利目标未能核实完成",
        "arena left-side daily rewards are not both positively claimed": "竞技场左侧每日两项奖励尚未全部确认领取",
    }
    if text in exact:
        return exact[text]
    for prefix, message in (
        ("visible account does not positively match", "当前账号身份未确认，请回到主界面后继续。"),
        ("running arena slot has no positive battle/page anchor", "战斗过渡等待后仍无法识别。请检查掉线或遮挡，保留当前战斗页面再重试。"),
        ("game geometry changed", "游戏窗口尺寸发生变化，已停止点击。请恢复1920×1032窗口后重试。"),
        ("game lost foreground", "游戏窗口无法取得前台，请关闭遮挡或恢复窗口后重试。"),
        ("chrome target is not client", "标签位置落在标题栏或窗口控制按钮，已拒绝点击。请核对当前标签布局。"),
        ("selected current tab position", "当前游戏标签位置变化，请重新识别当前账号。"),
        ("arena auto-battle", "未能确认自动战斗已开启，请检查游戏画面并重试。"),
        ("arena casual battle button", "休闲战斗按钮未能稳定识别，请等待竞技场加载完成后重试。"),
    ):
        if text.startswith(prefix):
            return message
    replacements = (
        ("missing assets:", "缺少必要界面模板："),
        ("missing managed safety assets:", "缺少托管安全模板："),
        ("expected one healthy game window; found", "未找到唯一健康游戏窗口，当前数量"),
        ("shared-window runtime lease unavailable:", "共享窗口操作锁不可用："),
        ("identity mismatch scores=", "身份核对不一致，评分"),
        ("scores=", "评分"),
        ("world map timeout", "世界地图等待超时"),
        ("leaf-village submap timeout", "木叶子地图等待超时"),
        ("timeout", "等待超时"),
        ("did not", "未能"),
        ("missing", "缺少"),
        ("unknown", "未知"),
        ("refusing duplicate", "为避免重复操作已停止"),
        ("pending", "待处理"),
        ("requires manual login", "需要人工登录"),
    )
    translated = text
    for before, after in replacements:
        translated = translated.replace(before, after)
    # Details originate in the machine-oriented scheduler and may contain
    # template names or exception class names.  Never show those raw tokens
    # in the task panel; the JSONL event retains the complete diagnostic.
    if re.search(r"[A-Za-z]", translated):
        return "详细原因请查看审计日志"
    return translated


def format_panel_event(event: dict[str, object]) -> str:
    """Render an event for the panel without exposing internal English codes."""
    name = str(event.get("event", ""))
    label = EVENT_LABELS.get(name, "内部调度事件")
    timestamp = str(event.get("ts", "")).replace("T", " ")
    parts = [timestamp, f"【{label}】"]
    if event.get("account"):
        parts.append(f"账号：{event.get('account_label') or event['account']}")
    if name in ("command_start", "command_complete", "command_pending", "command_summary", "command_exit") and event.get("command"):
        parts.append(f"命令：{_label_command(event['command'])}")
    if event.get("module"):
        parts.append(f"模块：{_label_module(event['module'])}")
    if event.get("mode"):
        parts.append(f"模式：{_label_mode(event['mode'])}")
    if event.get("stage"):
        parts.append(f"阶段：{_label_stage(event['stage'])}")
    if event.get("activity"):
        parts.append(f"活动：{_label_activity(event['activity'])}")
    if event.get("team") is not None:
        team_label = {1: "第一队", 2: "第二队"}.get(event["team"], "当前队伍")
        parts.append(f"队伍：{team_label}")
    if event.get("status"):
        parts.append(f"状态：{_label_status(event['status'])}")
    if event.get("result") is not None:
        parts.append(f"结果码：{event['result']}")
    if event.get("detail"):
        detail = _translate_detail(event["detail"])
        if detail:
            parts.append(f"详情：{detail}")
    if event.get("reason"):
        reason = _translate_detail(event["reason"])
        if reason:
            parts.append(f"原因：{reason}")
    if event.get("target"):
        parts.append(f"操作：{_label_action(event['target'])}")
    if event.get("template"):
        parts.append("模板：已匹配")
    for key in ("pending", "clicks", "content_clicks", "attempt", "seconds", "failures", "cycle", "latest_cycle", "claims", "wins", "member_count", "required_member_count", "remaining", "maximum", "display"):
        if event.get(key) is not None:
            value = _label_cycle(event[key]) if key in ("cycle", "latest_cycle") else event[key]
            parts.append(f"{FIELD_LABELS[key]}：{value}")
    if event.get("evidence") or event.get("raw_evidence"):
        parts.append("证据：已保存")
    return " ".join(parts).strip()


def display_market_cycle(config: dict[str, str], global_state: dict[str, object], now: str | None = None) -> str:
    """Return the batch the panel should display, including catch-up work.

    The wall clock identifies the latest due refresh, but an all-day run may
    still be settling an earlier batch.  In that case the table must follow
    the same earliest-unsettled selection as the scheduler rather than showing
    a later batch whose ledger is not currently being processed.
    """
    configured = [value.replace(":", "") for value in hs.parse_csv(config.get("MarketRefreshTimes", "08:00,11:00,14:00,17:00,20:00,23:00"))]
    current_text = now or datetime.now().strftime("%H:%M")
    latest = "PRE08"
    for value in configured:
        if current_text >= value:
            latest = value.replace(":", "")
    if latest == "PRE08":
        return latest
    try:
        latest_index = configured.index(latest)
    except ValueError:
        return latest
    cycle_map = global_state.get("all_day_cycles", {})
    if not isinstance(cycle_map, dict):
        return latest
    for candidate in configured[: latest_index + 1]:
        record = cycle_map.get(candidate)
        if not (isinstance(record, dict) and record.get("status") == "done"):
            return candidate
    return latest


class PanelTaskController:
    """Own exactly one scheduler worker and expose a cooperative pause."""

    def __init__(self, events: queue.Queue[dict[str, object]]):
        self.events = events
        self._lock = threading.Lock()
        self._thread: threading.Thread | None = None
        self._scheduler: hs.Scheduler | None = None
        self._pause_sequence = threading.Event()

    @property
    def busy(self) -> bool:
        thread = self._thread
        return bool(thread and thread.is_alive())

    def start(self, command: hs.SchedulerCommand, *, sequence=None) -> bool:
        commands = tuple(sequence) if sequence is not None else (command,)
        if not commands:
            raise ValueError("请至少勾选一项任务")
        if not self._lock.acquire(blocking=False):
            return False
        self._pause_sequence.clear()

        def worker() -> None:
            result = 3
            try:
                for item in commands:
                    if self._pause_sequence.is_set():
                        result = 130
                        break
                    config = hs.read_config(hs.CONFIG_PATH)
                    scheduler = hs.Scheduler(config, "panel", False, False, event_sink=self.events.put)
                    self._scheduler = scheduler
                    self.last_scheduler = scheduler
                    if self._pause_sequence.is_set():
                        result = 130
                        break
                    result = scheduler.execute_command(item)
                    if result != 0:
                        break
                if self._pause_sequence.is_set() and result == 0:
                    result = 130
            except Exception as exc:
                self.events.put({"event": "panel_worker_error", "detail": f"{type(exc).__name__}: {exc}"})
            finally:
                self._scheduler = None
                self.events.put({"event": "panel_worker_finished", "command": command.kind, "result": result})
                self._lock.release()

        self._thread = threading.Thread(target=worker, name=f"HYOL-{command.kind}", daemon=False)
        self._thread.start()
        return True

    def request_pause(self) -> bool:
        if not self.busy:
            return False
        self._pause_sequence.set()
        scheduler = self._scheduler
        if scheduler is not None:
            scheduler.request_pause_after_module()
        return True


class HyolPanel(tk.Tk):
    def __init__(self) -> None:
        super().__init__()
        self.title("火影OL 六账号视觉自动化")
        self.geometry("1180x760")
        self.minsize(980, 650)
        self.protocol("WM_DELETE_WINDOW", self.on_close)
        self.events: queue.Queue[dict[str, object]] = queue.Queue()
        self.controller = PanelTaskController(self.events)
        config = hs.read_config(hs.CONFIG_PATH)
        self.current_account = config.get("PanelCurrentAccount", "").strip() or None
        self.current_tab_x = int(config.get("PanelCurrentTabX", "180"))
        self.last_result_detail = tk.StringVar(value="等待开始。任务完成或失败原因会显示在这里。")
        if self.current_account:
            self.title("火影OL 当前账号独立客户端")
        self.active_account = "—"
        self.clicks = 0
        self.buttons: list[ttk.Button] = []
        self.arena_wins = tk.IntVar(value=1)
        self.group_instance_count = tk.IntVar(value=5)
        self.strong_approach_count = tk.IntVar(value=7)
        self.composite_options = {kind: tk.BooleanVar(value=kind in ("mail", "welfare", "arena", "survival")) for kind in COMPOSITE_ORDER}
        self.status_vars = {
            "window": tk.StringVar(value="游戏窗口：检查中"),
            "run": tk.StringVar(value="状态：空闲"),
            "account": tk.StringVar(value="当前账号：—"),
            "clicks": tk.StringVar(value="点击：0"),
            "f12": tk.StringVar(value="F12：待命"),
        }
        self._build_ui()
        from hyol_client_tools import add_diagnostic_controls
        add_diagnostic_controls(self, hs.ROOT, lambda: getattr(self.controller, "last_scheduler", None))
        self.refresh_state()
        self.after(150, self.drain_events)
        self.after(1000, self.poll_window)

    def _build_ui(self) -> None:
        style = ttk.Style(self)
        if "vista" in style.theme_names():
            style.theme_use("vista")
        style.configure("Title.TLabel", font=("Microsoft YaHei UI", 14, "bold"))
        style.configure("Warn.TLabel", foreground="#9b3b00")

        header = ttk.Frame(self, padding=(12, 10))
        header.pack(fill="x")
        ttk.Label(header, text="当前账号独立客户端" if self.current_account else "多账号共享标签日常调度", style="Title.TLabel").pack(side="left")
        for key in ("window", "run", "account", "clicks", "f12"):
            ttk.Label(header, textvariable=self.status_vars[key], padding=(12, 0)).pack(side="left")

        self.warning_label = ttk.Label(
            self,
            style="Warn.TLabel",
            padding=(14, 0, 14, 8),
        )
        self.warning_label.pack(fill="x")

        commands = ttk.LabelFrame(self, text="运行命令", padding=10)
        commands.pack(fill="x", padx=12, pady=(0, 8))
        kinds = ("arena",) if self.current_account else PANEL_COMMAND_ORDER
        for index, kind in enumerate(kinds):
            button = ttk.Button(commands, text="执行勾选任务" if kind == "full" else COMMAND_LABELS[kind], command=lambda value=kind: self.start_command(value))
            button.grid(row=index // 5, column=index % 5, padx=5, pady=5, sticky="ew")
            commands.columnconfigure(index % 5, weight=1)
            self.buttons.append(button)
        if not self.current_account:
            selection = ttk.LabelFrame(commands, text="复合任务（按排列顺序执行；托管最后运行）", padding=6)
            selection.grid(row=5, column=0, columnspan=5, sticky="ew")
            for index, (kind, variable) in enumerate(self.composite_options.items()):
                check = ttk.Checkbutton(selection, text=COMMAND_LABELS[kind], variable=variable)
                check.grid(row=index // 5, column=index % 5, padx=5, pady=3, sticky="w")
                self.buttons.append(check)
        counts = ttk.LabelFrame(commands, text="战斗次数（运行前可自由选择）", padding=6)
        counts.grid(row=3, column=0, columnspan=5, padx=5, pady=(8, 5), sticky="ew")
        for column, (label, variable, maximum) in enumerate((
            ("竞技场胜利次数", self.arena_wins, hs.ARENA_MAX_ATTEMPTS),
            ("组队副本场数", self.group_instance_count, hs.GROUP_INSTANCE_DAILY_REWARD_MAX),
            ("强者挑战场数", self.strong_approach_count, hs.STRONG_APPROACH_WEEKLY_REWARD_MAX),
        )):
            if self.current_account and column > 0:
                continue
            if self.current_account:
                label = "每次点击新增胜利次数"
            ttk.Label(counts, text=label).grid(row=0, column=column * 2, padx=(6, 2), sticky="e")
            if column > 0:
                ttk.Label(counts, text="游戏次数归零").grid(row=0, column=column * 2 + 1, padx=(2,12), sticky="w")
                continue
            ttk.Spinbox(counts, from_=1 if column == 0 else 0, to=maximum, width=5, textvariable=variable).grid(row=0, column=column * 2 + 1, padx=(2, 12), sticky="w")
        self.pause_button = ttk.Button(commands, text="完成当前模块后暂停", command=self.request_pause, state="disabled")
        self.pause_button.grid(row=4, column=0, columnspan=2, padx=5, pady=5, sticky="ew")
        ttk.Button(commands, text="刷新状态", command=self.refresh_state).grid(row=4, column=2, padx=5, pady=5, sticky="ew")
        ttk.Label(commands, text="立即急停请按 F12；面板不提供台账清除。", style="Warn.TLabel").grid(
            row=4, column=3, columnspan=2, padx=5, pady=5, sticky="w"
        )
        ttk.Label(self, textvariable=self.last_result_detail, wraplength=1100, padding=(14, 8), foreground="#9b3b00").pack(fill="x")

        content = ttk.Panedwindow(self, orient="vertical")
        content.pack(fill="both", expand=True, padx=12, pady=(0, 12))
        state_box = ttk.LabelFrame(content, text="六账号当日状态", padding=6)
        log_box = ttk.LabelFrame(content, text="实时日志", padding=6)
        content.add(state_box, weight=2)
        content.add(log_box, weight=3)

        columns = ("account", "tab", *MODULE_COLUMNS, "pending", "evidence")
        self.tree = ttk.Treeview(state_box, columns=columns, show="headings", height=8)
        if self.current_account:
            self.tree.configure(displaycolumns=("account", "arena", "pending", "evidence"))
        headings = {
            "account": "账号", "tab": "标签位置", "mail": "邮箱", "welfare": "福利", "team1": "第一队",
            "team2": "第二队", "arena": "竞技场", "survival": "生存", "market": "黑市", "managed": "托管", "all_day": "全天值守",
            "pending": "最近pending", "evidence": "最近证据",
        }
        widths = {"account": 125, "tab": 65, "mail": 58, "welfare": 58, "team1": 58, "team2": 58, "arena": 58, "survival": 58, "market": 58, "managed": 58, "all_day": 78, "pending": 260, "evidence": 150}
        for column in columns:
            self.tree.heading(column, text=headings[column])
            self.tree.column(column, width=widths[column], anchor="w", stretch=column in ("pending", "evidence"))
        self.tree.pack(fill="both", expand=True)

        self.log = tk.Text(log_box, height=18, wrap="none", state="disabled", font=("Consolas", 9), background="#111827", foreground="#d1fae5")
        log_scroll = ttk.Scrollbar(log_box, orient="vertical", command=self.log.yview)
        self.log.configure(yscrollcommand=log_scroll.set)
        self.log.pack(side="left", fill="both", expand=True)
        log_scroll.pack(side="right", fill="y")

    def start_command(self, kind: str) -> None:
        if self.current_account and kind != "arena":
            self.last_result_detail.set("此入口运行当前账号的竞技场任务。")
            return
        try:
            sequence = composite_commands(
                [key for key, value in self.composite_options.items() if value.get()],
                self.arena_wins.get(), self.group_instance_count.get(), self.strong_approach_count.get(),
            ) if kind == "full" else None
            command = hs.SchedulerCommand(
                kind,
                arena_victory_target=self.arena_wins.get() if kind == "arena" else 1,
                group_instance_count=self.group_instance_count.get() if kind in ("team1", "team2") else None,
                strong_approach_count=self.strong_approach_count.get() if kind in ("team1", "team2") else None,
                generic_current_window=bool(self.current_account),
                arena_additional_victories=bool(self.current_account),
            )
        except (tk.TclError, ValueError) as exc:
            messagebox.showerror("次数无效", str(exc), parent=self)
            return
        if not self.controller.start(command, **({"sequence": sequence} if sequence is not None else {})):
            messagebox.showwarning("任务运行中", "已有调度任务正在运行，不能启动第二个任务。", parent=self)
            return
        self.clicks = 0
        self.last_result_detail.set("正在执行完整任务；完成后可再次点击。未完成时再次点击会续接本轮。")
        if sequence is not None:
            self.last_result_detail.set("本次任务：" + " → ".join(COMMAND_LABELS[item.kind] for item in sequence))
        self.status_vars["run"].set(f"状态：运行 {COMMAND_LABELS[kind]}")
        self.status_vars["f12"].set("F12：待命")
        self._set_running(True)
        self.append_log(format_panel_event({"ts": datetime.now().isoformat(timespec="seconds"), "event": "command_start", "command": kind}))

    def request_pause(self) -> None:
        if self.controller.request_pause():
            self.status_vars["run"].set("状态：等待当前模块结束后暂停")
            self.pause_button.configure(state="disabled")
            self.append_log(format_panel_event({"ts": datetime.now().isoformat(timespec="seconds"), "event": "pause_requested"}))

    def _set_running(self, running: bool) -> None:
        state = "disabled" if running else "normal"
        for button in self.buttons:
            button.configure(state=state)
        self.pause_button.configure(state="normal" if running else "disabled")

    def append_log(self, line: str) -> None:
        self.log.configure(state="normal")
        self.log.insert("end", line + "\n")
        self.log.see("end")
        self.log.configure(state="disabled")

    def drain_events(self) -> None:
        try:
            while True:
                event = self.events.get_nowait()
                name = str(event.get("event", "event"))
                if event.get("account"):
                    self.active_account = str(event["account"])
                    self.status_vars["account"].set(f"当前账号：{self.active_account}")
                if name == "content_click":
                    self.clicks += 1
                    self.status_vars["clicks"].set(f"点击：{self.clicks}")
                if name in ("emergency_stop", "scheduler_stopped"):
                    self.status_vars["f12"].set("F12：已触发")
                if name == "panel_worker_finished":
                    result = int(event.get("result", 3))
                    self.status_vars["run"].set("状态：完成，可再次执行" if result == 0 else "状态：未完成，请查看原因")
                    self._set_running(False)
                    self.refresh_state()
                if name == "command_summary":
                    status = _label_status(event.get("status", "unknown"))
                    self.status_vars["run"].set(f"状态：{status}（结果码 {event.get('result', 3)}）")
                    if int(event.get("result", 3)) == 0:
                        self.last_result_detail.set("本轮任务已完成：胜利目标、每日奖励及返回主界面均已核实。再次点击将开始新一轮，已领日常奖励不会重复领取。")
                    else:
                        self.last_result_detail.set("本轮未完成：" + _translate_detail(event.get("detail", "")))
                if name == "panel_worker_error":
                    self.last_result_detail.set("运行异常：" + str(event.get("detail", "未知异常")))
                if name == "command_summary" and event.get("detail"):
                    # Unknown errors must remain inspectable inside the client.
                    self.append_log("原始诊断：" + str(event["detail"]))
                self.append_log(format_panel_event(event))
        except queue.Empty:
            pass
        self.after(150, self.drain_events)

    def poll_window(self) -> None:
        try:
            windows = hs.Win32.game_windows()
            if len(windows) == 1:
                hwnd = windows[0]
                rect = hs.Win32.rect(hwnd)
                fg = "前台" if hs.Win32.foreground() == hwnd else "后台"
                self.status_vars["window"].set(f"游戏窗口：{hwnd} {rect[2]}×{rect[3]} {fg}")
            else:
                self.status_vars["window"].set(f"游戏窗口：{len(windows)} 个（应为 1）")
        except Exception as exc:
            self.status_vars["window"].set(f"游戏窗口：检查失败 {_translate_detail(exc)}")
        self.after(1000, self.poll_window)

    def refresh_state(self) -> None:
        config = hs.read_config(hs.CONFIG_PATH)
        purchase_enabled = config.get("AllowMarketPurchase", "0") == "1"
        purchase_text = "黑市点券购买已启用（仅点券、无铜钱）；" if purchase_enabled else "黑市购买尚未启用；"
        self.warning_label.configure(
            text=(
                "自动识别当前账号；失败后再次点击可续接。" if self.current_account else "自动识别在线账号并依次运行；" + purchase_text
                + "缺模板、身份不明、掉线、付费框或未决动作只进入 pending。"
            )
        )
        names = hs.parse_csv(config.get("TabAccountNames", ""))
        tab_xs = hs.parse_int_csv(config.get("TabXs", ""))
        global_path = hs.STATE_DIR / f"{date.today().isoformat()}.json"
        try:
            global_state = json.loads(global_path.read_text(encoding="utf-8"))
        except (FileNotFoundError, json.JSONDecodeError, OSError):
            global_state = {"pending": []}
        pending = [
            row for row in global_state.get("pending", [])
            if isinstance(row, dict) and str(row.get("status", "pending")) not in ("resolved", "cleared")
        ]
        teams = global_state.get("teams", {})
        cycle = display_market_cycle(config, global_state)
        cycle_map = global_state.get("all_day_cycles", {})
        cycle_record = cycle_map.get(cycle, {}) if cycle != "PRE08" and isinstance(cycle_map, dict) else {}
        if global_state.get("all_day_completed_at"):
            all_day_status = "done"
        elif isinstance(cycle_record, dict) and cycle_record.get("status") == "done":
            all_day_status = "done"
        elif cycle == "PRE08":
            all_day_status = "waiting"
        else:
            all_day_status = "pending"
        self.tree.heading("market", text=f"黑市[{_label_cycle(cycle)}]")
        for item in self.tree.get_children():
            self.tree.delete(item)
        for index, name in enumerate(names):
            account_path = hs.STATE_DIR / date.today().isoformat() / f"{hs.slug(name)}.json"
            try:
                account_state = json.loads(account_path.read_text(encoding="utf-8"))
            except (FileNotFoundError, json.JSONDecodeError, OSError):
                account_state = {"modules": {}}
            modules = account_state.get("modules", {})
            recent = next((_translate_detail(row.get("detail", "")) for row in reversed(pending) if row.get("scope") == name), "")
            evidence_dir = hs.EVIDENCE_DIR / date.today().isoformat() / hs.slug(name)
            evidence = ""
            if evidence_dir.exists():
                files = sorted((path for path in evidence_dir.iterdir() if path.is_file()), key=lambda path: path.stat().st_mtime, reverse=True)
                if files:
                    evidence = files[0].name
            values = [name, tab_xs[index] if index < len(tab_xs) else "—"]
            team_values = []
            for team_index in (1, 2):
                team = teams.get(str(team_index), {}) if isinstance(teams, dict) else {}
                if team.get("leader") == name and team.get("status") == "dissolved_after_activities":
                    team_values.append("完成")
                elif team.get("leader") == name and team.get("status") == "deferred_activities":
                    team_values.append("其余完成")
                else:
                    team_values.append("—")
            module_values = [_label_status(modules.get(module, {}).get("status", "new")) for module in ("mail", "welfare")]
            module_values.extend(team_values)
            module_values.extend(_label_status(modules.get(module, {}).get("status", "new")) for module in ("arena", "survival"))
            module_values.append(_label_status(modules.get(f"market_{cycle}", {}).get("status", "new")) if cycle != "PRE08" else "等待中")
            module_values.append(_label_status(modules.get("managed", {}).get("status", "new")))
            account_all_day_pending = any(
                row.get("scope") == name
                and str(row.get("module", "")).startswith("all_day_")
                and (cycle == "PRE08" or str(row.get("module", "")) == f"all_day_{cycle}")
                for row in pending
            )
            module_values.append("待处理" if account_all_day_pending else _label_status(all_day_status))
            values.extend(module_values)
            values.extend((recent[:80], evidence))
            self.tree.insert("", "end", values=values)

    def on_close(self) -> None:
        if self.controller.busy:
            self.iconify()
            self.append_log(format_panel_event({"ts": datetime.now().isoformat(timespec="seconds"), "event": "command_exit", "detail": "面板已最小化，任务继续运行"}))
            return
        self.destroy()


def main() -> int:
    app = HyolPanel()
    app.mainloop()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
