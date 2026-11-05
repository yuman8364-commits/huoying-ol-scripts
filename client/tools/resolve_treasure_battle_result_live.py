"""Confirm an already-finished treasure battle, then return to Leaf safely."""
import json
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import hyol_scheduler as hs
from hyol_navigation import return_to_leaf


def run():
    scheduler = hs.Scheduler(hs.read_config(hs.ROOT / "DAILY_Client.ini"), "treasure_battle_result", False, False)
    lease = hs.ForegroundRuntimeLease()
    result = {"status": "pending"}
    try:
        if not lease.acquire():
            raise hs.PendingError("已有脚本占用窗口")
        scheduler.start_f12_monitor()
        scheduler.bind_window(wait=True, timeout_seconds=10)
        account = scheduler.bind_generic_current_window(require_game_context=False)
        if not scheduler.click_template(
            account,
            "treasure_battle_confirm.bmp",
            (880, 600, 1050, 690),
            62,
            20,
            "treasure_battle_result_confirm",
            ["treasure_battle_victory.bmp", "treasure_battle_confirm.bmp"],
            threshold=.96,
            anchor_threshold=.96,
        ):
            raise hs.PendingError("藏宝图战斗结算页未稳定识别")
        if not scheduler.wait_absent("treasure_battle_victory.bmp", (800, 330, 1130, 590), 10, threshold=.92):
            raise hs.PendingError("藏宝图战斗结算确认后未消失")
        if not return_to_leaf(scheduler, account):
            raise hs.PendingError("结算已确认，但安全回村未完成")
        result.update(status="returned", account=account.name)
    except Exception as exc:
        result.update(status="blocked", detail=f"{type(exc).__name__}: {exc}")
    finally:
        scheduler.stop.set()
        lease.release()
        result["content_clicks"] = scheduler.content_click_count
        hs.atomic_json(hs.LOGS / "treasure_battle_result_live.json", result)
    return result


if __name__ == "__main__":
    value = run()
    print(json.dumps(value, ensure_ascii=False))
    raise SystemExit(0 if value["status"] == "returned" else 2)
