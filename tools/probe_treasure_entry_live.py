"""Open the current tab's Treasure page once; never use a map or buy anything."""
import json
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import hyol_scheduler as hs


def run():
    config = hs.read_config(hs.ROOT / "DAILY_Client.ini")
    config["AllowMarketPurchase"] = "0"
    scheduler = hs.Scheduler(config, "treasure_entry_probe", False, False)
    lease = hs.ForegroundRuntimeLease()
    result = {"status": "pending", "content_clicks": 0}
    try:
        if not lease.acquire():
            raise hs.PendingError("已有脚本占用窗口")
        scheduler.start_f12_monitor()
        scheduler.bind_window(wait=True, timeout_seconds=10)
        account = scheduler.bind_generic_current_window()
        if not scheduler.click_template(
            account,
            "treasure_toolbar_current.bmp",
            (1380, 69, 1530, 170),
            37,
            42,
            "treasure_entry_probe",
            ["anchor_top_menu.bmp", "treasure_toolbar_current.bmp"],
            threshold=0.96,
        ):
            raise hs.PendingError("顶部宝藏入口未稳定识别")
        scheduler.wait_interruptible(2)
        frame = scheduler.capture()
        evidence = hs.LOGS / "treasure_entry_probe_current.png"
        frame.save(evidence)
        result.update(status="opened", evidence=str(evidence), account=account.name)
    except Exception as exc:
        result.update(status="blocked", detail=f"{type(exc).__name__}: {exc}")
    finally:
        scheduler.stop.set()
        lease.release()
        result["content_clicks"] = scheduler.content_click_count
        hs.atomic_json(hs.LOGS / "treasure_entry_probe_current.json", result)
    return result


if __name__ == "__main__":
    value = run()
    print(json.dumps(value, ensure_ascii=False))
    raise SystemExit(0 if value["status"] == "opened" else 2)
