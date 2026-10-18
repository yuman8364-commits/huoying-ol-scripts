"""Resolve the already-started treasure ambush, then return to Leaf."""
import json
from pathlib import Path
import sys
import time

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import hyol_scheduler as hs
from hyol_navigation import return_to_leaf


def run():
    scheduler = hs.Scheduler(hs.read_config(hs.ROOT / "DAILY_Client.ini"), "treasure_trap", False, False)
    lease = hs.ForegroundRuntimeLease()
    result = {"status": "pending"}
    try:
        if not lease.acquire():
            raise hs.PendingError("已有脚本占用窗口")
        scheduler.start_f12_monitor()
        scheduler.bind_window(wait=True, timeout_seconds=10)
        account = scheduler.bind_generic_current_window(require_game_context=False)
        frame = scheduler.capture()
        if not scheduler.template("treasure_trap_anchor.bmp", (760, 470, 1170, 640), frame, threshold=.96)[0]:
            raise hs.PendingError("藏宝图埋伏弹窗未稳定识别")
        if not scheduler.click_template(
            account,
            "treasure_trap_confirm.bmp",
            (880, 540, 1040, 630),
            60,
            27,
            "treasure_trap_confirm",
            ["treasure_trap_anchor.bmp", "treasure_trap_confirm.bmp"],
            irreversible=True,
            threshold=.96,
        ):
            raise hs.PendingError("藏宝图埋伏确认按钮消失")
        deadline = time.monotonic() + 600
        progress_deadline = time.monotonic() + 60
        previous = None
        battle_seen = False
        while time.monotonic() < deadline:
            scheduler.check_stop()
            frame = scheduler.capture()
            current = hs.image_hash(frame.crop((300, 150, 1650, 900)).resize((160, 90)))
            if current != previous:
                previous = current
                progress_deadline = time.monotonic() + 60
            active = scheduler._combat_active_visible("arena", frame)
            if active or scheduler.template("battle_auto_off.bmp", hs.BATTLE_AUTO_ROI, frame)[0] or scheduler._battle_auto_on_visible(frame):
                battle_seen = True
            if scheduler.template("battle_auto_off.bmp", hs.BATTLE_AUTO_ROI, frame)[0]:
                if not scheduler.click_template(
                    account, "battle_auto_off.bmp", hs.BATTLE_AUTO_ROI, 29, 29,
                    "treasure_trap_auto", ["battle_auto_off.bmp"]
                ):
                    raise hs.PendingError("埋伏战斗自动按钮消失")
            if battle_seen and not active and scheduler.main_ready(frame):
                if return_to_leaf(scheduler, account):
                    result.update(status="returned", battle_seen=True, account=account.name)
                    return result
            if time.monotonic() >= progress_deadline:
                raise hs.PendingError("游戏超时：藏宝图埋伏60秒无进展")
            scheduler.wait_interruptible(.5)
        raise hs.PendingError("游戏超时：藏宝图埋伏10分钟未结算")
    except Exception as exc:
        result.update(status="blocked", detail=f"{type(exc).__name__}: {exc}")
        return result
    finally:
        scheduler.stop.set()
        lease.release()
        result["content_clicks"] = scheduler.content_click_count
        hs.atomic_json(hs.LOGS / "treasure_trap_live.json", result)


if __name__ == "__main__":
    value = run()
    print(json.dumps(value, ensure_ascii=False))
    raise SystemExit(0 if value["status"] == "returned" else 2)
