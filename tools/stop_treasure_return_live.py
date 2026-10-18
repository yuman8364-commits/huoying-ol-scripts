"""Stop treasure auto-path through the verified top-right return-to-Leaf flow."""
import json
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import hyol_scheduler as hs
from hyol_navigation import return_to_leaf


def run():
    scheduler = hs.Scheduler(hs.read_config(hs.ROOT / "DAILY_Client.ini"), "treasure_stop", False, False)
    lease = hs.ForegroundRuntimeLease()
    result = {"status": "pending"}
    try:
        if not lease.acquire():
            raise hs.PendingError("已有脚本占用窗口")
        scheduler.start_f12_monitor()
        scheduler.bind_window(wait=True, timeout_seconds=10)
        account = scheduler.bind_generic_current_window(require_game_context=False)
        if not return_to_leaf(scheduler, account):
            raise hs.PendingError("安全回村未完成")
        result.update(status="returned", account=account.name)
    except Exception as exc:
        result.update(status="blocked", detail=f"{type(exc).__name__}: {exc}")
    finally:
        scheduler.stop.set()
        lease.release()
        result["content_clicks"] = scheduler.content_click_count
        hs.atomic_json(hs.LOGS / "treasure_stop_return_live.json", result)
    return result


if __name__ == "__main__":
    value = run()
    print(json.dumps(value, ensure_ascii=False))
    raise SystemExit(0 if value["status"] == "returned" else 2)
