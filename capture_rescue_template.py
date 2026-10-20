"""Capture the live rescue difficulty card after the activity opens.

This is a bounded, leader-only enrollment helper.  It may click only the
verified free ``参与`` control; it never clicks the battle control.  A new
template is written only when the verified, enrolled rescue page contains the
already-known difficulty-card shape from the same client.  Otherwise the
lossless source frame is retained and the command remains pending.
"""

from __future__ import annotations

import argparse
from datetime import datetime, time
from pathlib import Path

from PIL import Image

import hyol_scheduler as hs


OPEN_FROM = time(18, 0)
OPEN_UNTIL = time(22, 30)
OPEN_WEEKDAYS = frozenset((3, 4, 6))  # 周四、周五、周日；datetime.weekday() 周一为0
RESCUE_NAV_ASSETS = (
    "daily_practice_toolbar.bmp",
    "daily_practice_anchor.bmp",
    "daily_limited_tab.bmp",
    "rescue_jinchuriki_entry.bmp",
    "rescue_jinchuriki_anchor.bmp",
    "daily_participate.bmp",
    "market_coupon_icon.bmp",
    "limited_activity_close.bmp",
)


def _open_now(current: time) -> bool:
    return OPEN_FROM <= current <= OPEN_UNTIL


def _calendar_open(now: datetime) -> bool:
    return _open_now(now.time()) and now.weekday() in OPEN_WEEKDAYS


def _write_crop(frame: Image.Image, source: Path, x: int, y: int, output: Path) -> tuple[Path, tuple[int, int]]:
    with Image.open(source) as reference:
        width, height = reference.size
    crop = frame.crop((x, y, x + width, y + height)).convert("RGB")
    temporary = output.with_name(output.name + ".capture.tmp")
    crop.save(temporary, format="BMP")
    return temporary, (width, height)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="开放时段采集营救人柱力最高难度模板")
    parser.add_argument("--team", type=int, default=2, choices=(2,))
    args = parser.parse_args(argv)

    now = datetime.now()
    if not _open_now(now.time()):
        print(f"待处理：当前时间 {now:%H:%M} 不在营救开放时段 18:00-22:30")
        return 2
    if not _calendar_open(now):
        print(f"待处理：今天不是营救开放日（仅周四、周五、周日），当前为 {now:%Y-%m-%d %A}")
        return 2
    calendar_open = _calendar_open(now)

    config = hs.read_config(hs.CONFIG_PATH)
    scheduler = hs.Scheduler(config, "team2", False, False)
    lease = hs.ForegroundRuntimeLease()
    if not lease.acquire():
        print("待处理：共享游戏窗口正在被其他调度任务使用")
        return 2

    leader: hs.Account | None = None
    page_open = False
    handoff_page = False
    try:
        scheduler.command_kind = "team2"
        scheduler.bind_window(wait=True)
        team = next(team for team in scheduler.teams if team.index == args.team)
        scheduler.rebind_tabs_by_identity(team.members)
        leader = scheduler.by_name[config["Team2VerifiedLeader"]]
        scheduler.switch_account(leader)
        scheduler.prepare_main(leader)
        scheduler.ensure_assets(RESCUE_NAV_ASSETS)

        if not scheduler.click_template(
            leader,
            "daily_practice_toolbar.bmp",
            (651, 69, 851, 169),
            20,
            20,
            "rescue_capture_open_daily_practice",
            ["anchor_top_menu.bmp"],
        ):
            raise hs.PendingError("营救模板采集：日常任务入口不可用")
        if not scheduler.wait_template("daily_practice_anchor.bmp", (951, 119, 1501, 369), 8):
            raise hs.PendingError("营救模板采集：日常任务页面未确认")
        if not scheduler.click_template(
            leader,
            "daily_limited_tab.bmp",
            (500, 220, 1000, 340),
            70,
            24,
            "rescue_capture_open_limited",
            ["daily_practice_anchor.bmp"],
        ):
            raise hs.PendingError("营救模板采集：限时活动标签不可用")
        if not scheduler.wait_template("rescue_jinchuriki_entry.bmp", hs.VIEW, 8):
            raise hs.PendingError("营救模板采集：营救入口未确认")
        if not scheduler.click_template(
            leader,
            "rescue_jinchuriki_entry.bmp",
            hs.VIEW,
            35,
            20,
            "rescue_capture_open_detail",
            ["daily_practice_anchor.bmp"],
        ):
            raise hs.PendingError("营救模板采集：营救入口点击结果未知")
        page_open = True
        if not scheduler.wait_template("rescue_jinchuriki_anchor.bmp", hs.VIEW, 15):
            raise hs.PendingError("营救模板采集：详情标题未确认")

        frame = scheduler.capture()
        participate_roi = (1050, 620, 1350, 740)
        raw_participate = scheduler.template("daily_participate.bmp", participate_roi, frame)
        active_participate = scheduler.active_template("daily_participate.bmp", participate_roi, frame)
        if raw_participate[0] and not active_participate[0]:
            participate_action = "team2_rescue_jinchuriki_participate"
            if scheduler.state.action_status(leader.name, participate_action) == "pending":
                evidence = scheduler.save_raw_evidence(frame, leader, "rescue_disabled_preview_recovery", "pending")
                scheduler.state.set_action(
                    leader.name,
                    participate_action,
                    "new",
                    "上次点击命中未开放的灰色预告按钮，已由颜色证据确认未执行",
                )
                scheduler.event(
                    "rescue_participate_disabled_recovered",
                    leader,
                    evidence=str(evidence),
                    score=raw_participate[1],
                    orange_ratio="below_0.12",
                )
                print(f"已根据灰色未开放按钮证据恢复未执行状态：{evidence}")

        if not calendar_open:
            evidence = scheduler.save_raw_evidence(frame, leader, "rescue_closed_weekday", "pending")
            scheduler.event("rescue_calendar_pending", leader, evidence=str(evidence), weekday=now.weekday())
            print(f"待处理：今天不是营救开放日（仅周四、周五、周日），证据={evidence}")
            return 2

        if active_participate[0]:
            participate_action = "team2_rescue_jinchuriki_participate"
            participate_status = scheduler.state.action_status(leader.name, participate_action)
            if participate_status == "pending":
                evidence = scheduler.save_raw_evidence(frame, leader, "rescue_participate_outcome_pending", "pending")
                scheduler.event(
                    "rescue_participate_outcome_pending",
                    leader,
                    evidence=str(evidence),
                    reason="participate_button_still_visible_after_previous_click",
                )
                print(f"待处理：参与按钮仍可见，保留未知结果证据={evidence}")
                raise hs.PendingError("营救模板采集：参与结果未知，为避免重复操作已停止")
            if participate_status == "done":
                raise hs.PendingError("营救模板采集：已记录参与但页面仍显示参与按钮，页面状态未知")
            if scheduler.template("market_coupon_icon.bmp", (500, 300, 1450, 1000), frame)[0]:
                raise hs.SafetyError("营救模板采集：参与页面显示货币选项，拒绝进入")
            if not scheduler.click_template(
                leader,
                "daily_participate.bmp",
                participate_roi,
                51,
                18,
                participate_action,
                ["rescue_jinchuriki_anchor.bmp", "daily_participate.bmp"],
                irreversible=True,
            ):
                raise hs.PendingError("营救模板采集：参与按钮消失，结果未知")
            if not scheduler.wait_absent("daily_participate.bmp", participate_roi, 12):
                raise hs.PendingError("营救模板采集：参与后页面未切换，结果未知")
            scheduler.commit_action(leader, participate_action, "已确认队长免费参与并进入营救难度页")
            scheduler.event("team_activity_participated", leader, team=2, activity="rescue_jinchuriki", leader_only=True)
            scheduler.wait_interruptible(0.5)
            handoff_page = True
            frame = scheduler.capture()
            after_evidence = scheduler.save_raw_evidence(frame, leader, "rescue_after_participate_source", "pending")
            scheduler.event("rescue_template_source_after_participate", leader, evidence=str(after_evidence))

        evidence = scheduler.save_raw_evidence(frame, leader, "rescue_highest_template_source", "pending")
        matched, score, x, y = scheduler.template(
            "strong_approach_highest_unlocked.bmp",
            (450, 150, 1500, 850),
            frame,
            threshold=0.90,
        )
        if not matched:
            scheduler.event("rescue_template_pending", leader, evidence=str(evidence), reason="difficulty_card_shape_not_confirmed")
            print(f"待处理：未从真实营救页确认最高难度卡片，证据={evidence}")
            return 2

        output = hs.ASSETS / "rescue_jinchuriki_highest_unlocked.bmp"
        temporary, (width, height) = _write_crop(
            frame,
            hs.ASSETS / "strong_approach_highest_unlocked.bmp",
            x,
            y,
            output,
        )
        verified, verified_score, _, _ = scheduler.templates.match(
            frame,
            temporary,
            (x, y, x + width, y + height),
            threshold=0.98,
        )
        if not verified:
            temporary.unlink(missing_ok=True)
            scheduler.event("rescue_template_pending", leader, evidence=str(evidence), reason="captured_template_recheck_failed")
            print(f"待处理：模板写入前复核失败，证据={evidence}")
            return 2
        temporary.replace(output)
        scheduler.event(
            "rescue_template_captured",
            leader,
            evidence=str(evidence),
            template=str(output),
            source_template="strong_approach_highest_unlocked.bmp",
            source_score=score,
            verified_score=verified_score,
        )
        print(f"已采集真实营救最高难度模板：{output}，证据={evidence}")
        return 0
    except (hs.PendingError, hs.SafetyError) as exc:
        print(f"待处理：{exc}")
        return 2
    finally:
        # Close only when the known activity page and its own X are positive;
        # after a verified participation, leave the difficulty page open for
        # the formal resumable team2 runner to start the battle.
        if page_open and not handoff_page and leader is not None:
            try:
                if scheduler.template("rescue_jinchuriki_anchor.bmp", hs.VIEW)[0]:
                    scheduler.click_template(
                        leader,
                        "limited_activity_close.bmp",
                        (1350, 180, 1750, 350),
                        20,
                        20,
                        "rescue_capture_close",
                        ["rescue_jinchuriki_anchor.bmp", "limited_activity_close.bmp"],
                    )
                    scheduler.wait_until_main(8)
            except (hs.PendingError, hs.SafetyError):
                pass
        scheduler.stop.set()
        lease.release()


if __name__ == "__main__":
    raise SystemExit(main())
