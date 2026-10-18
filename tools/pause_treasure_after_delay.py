"""After a bounded delay, click the user-authorized original point once.

This is a recovery tool for the game's continuous treasure auto-path.  It
never opens the treasure page, uses a map, confirms a battle, or retries the
click.  The game HWND and capture geometry must remain unchanged.
"""
import argparse
import json
from pathlib import Path
import sys
import time

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import hyol_scheduler as hs
from hyol_navigation import return_to_leaf


def run(delay_seconds=300, x=882, y=756):
    scheduler = hs.Scheduler(hs.read_config(hs.ROOT / "DAILY_Client.ini"), "treasure_pause", False, False)
    lease = hs.ForegroundRuntimeLease()
    result = {"status": "pending", "delay_seconds": delay_seconds, "point": [x, y]}
    try:
        if not lease.acquire():
            raise hs.PendingError("已有脚本占用窗口")
        scheduler.start_f12_monitor()
        scheduler.bind_window(wait=True, timeout_seconds=10)
        account = scheduler.bind_generic_current_window(require_game_context=False)
        original_hwnd = scheduler.hwnd
        original_geometry = scheduler._capture_geometry
        deadline = time.monotonic() + delay_seconds
        while time.monotonic() < deadline:
            scheduler.check_stop()
            remaining = max(0, int(deadline - time.monotonic()))
            print(json.dumps({"event": "countdown", "remaining": remaining}, ensure_ascii=False), flush=True)
            scheduler.wait_interruptible(min(30, max(.1, deadline - time.monotonic())))
        scheduler.bind_window(wait=True, timeout_seconds=10)
        if scheduler.hwnd != original_hwnd or scheduler._capture_geometry != original_geometry:
            raise hs.SafetyError("等待期间游戏窗口或几何变化，取消原位点击")
        scheduler.click_point(account, x, y, "treasure_pause_original_point", [])
        scheduler.wait_interruptible(3)
        paused_evidence = scheduler.save_raw_evidence(
            scheduler.capture(), account, "treasure_pause_original_point", "after"
        )
        returned = return_to_leaf(scheduler, account)
        result.update(
            status="returned" if returned else "paused",
            account=account.name,
            evidence=str(paused_evidence),
        )
    except Exception as exc:
        result.update(status="blocked", detail=f"{type(exc).__name__}: {exc}")
    finally:
        scheduler.stop.set()
        lease.release()
        result["content_clicks"] = scheduler.content_click_count
        hs.atomic_json(hs.LOGS / "treasure_pause_after_delay.json", result)
    return result


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--delay", type=int, default=300)
    parser.add_argument("--x", type=int, default=882)
    parser.add_argument("--y", type=int, default=756)
    args = parser.parse_args()
    value = run(args.delay, args.x, args.y)
    print(json.dumps(value, ensure_ascii=False), flush=True)
    raise SystemExit(0 if value["status"] == "returned" else 2)
