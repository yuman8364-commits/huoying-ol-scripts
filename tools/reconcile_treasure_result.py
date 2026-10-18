"""Reconcile one pending treasure action from a saved positive reward frame."""
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from PIL import Image
import hyol_scheduler as hs
from hyol_daily_tasks import DailyScheduler


EVIDENCE = hs.LOGS / "preflight_20260919_232112_238907_74272287.png"


def main():
    if not EVIDENCE.exists():
        raise SystemExit("positive treasure evidence is missing")
    frame = Image.open(EVIDENCE).convert("RGB")
    store = hs.TemplateStore(.90)
    if not store.match(frame, hs.ASSETS / "treasure_result_anchor.bmp", (760, 440, 1170, 670), .96)[0]:
        raise SystemExit("saved frame does not positively match treasure reward result")
    scheduler = DailyScheduler(hs.read_config(hs.ROOT / "DAILY_Client.ini"), "treasure_reconcile", False, True)
    scheduler.bind_window(wait=True, timeout_seconds=10)
    account = scheduler.bind_generic_current_window(require_game_context=False)
    scheduler.commit_action(
        account,
        "daily_treasure_auto_dig_once",
        f"事后核对：已保存实拍匹配挖宝奖励结算框；evidence={EVIDENCE}",
    )
    print(EVIDENCE)


if __name__ == "__main__":
    main()
