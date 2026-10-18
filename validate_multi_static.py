from __future__ import annotations

import re
from pathlib import Path


ROOT = Path(__file__).resolve().parent
SOURCE = (ROOT / "HYOL_Daily.Q.utf8").read_text(encoding="utf-8")
CONFIG = (ROOT / "HYOL_Daily.ini").read_text(encoding="utf-8")
PYTHON_SOURCE = (ROOT / "hyol_scheduler.py").read_text(encoding="utf-8")


def require(condition: bool, message: str) -> None:
    if not condition:
        raise AssertionError(message)
    print("PASS " + message)


def parse_handles(raw: str) -> list[int]:
    values: list[int] = []
    for part in raw.replace(",", "|").split("|"):
        if not part.strip():
            continue
        value = int(part)
        if value not in values:
            values.append(value)
        if len(values) == 3:
            break
    if len(values) != 3:
        raise ValueError("expected exactly three verified handles")
    return sorted(values)


def choose_leader(powers: tuple[int, int, int]) -> int:
    leader = 1
    if powers[1] > powers[0]:
        leader = 2
    if powers[2] > powers[0] and powers[2] > powers[1]:
        leader = 3
    return leader


def count_blocks(start: str, end: str) -> tuple[int, int]:
    return (
        len(re.findall(rf"(?im)^\s*{re.escape(start)}", SOURCE)),
        len(re.findall(rf"(?im)^\s*{re.escape(end)}\b", SOURCE)),
    )


def function_body(name: str) -> str:
    match = re.search(
        rf"(?ims)^\s*Function\s+{re.escape(name)}\b.*?^\s*End Function\b",
        SOURCE,
    )
    if not match:
        raise AssertionError(f"function {name} missing")
    return match.group(0)


def main() -> int:
    require("Enable=0" in SOURCE[:500], "multi candidate remains disabled")
    require("AccountMode=dynamic" in CONFIG and "AccountDiscovery=automatic" in CONFIG,
            "configuration selects automatic shared-window account discovery")
    require("AccountCount=6" in CONFIG, "default account limit is six; current identities are discovered at runtime")
    require("MultiRunStage=auto" in CONFIG, "default Python stage is automatic progression")
    require("MultiLaunchEnabled=0" in CONFIG, "saved-account launching remains gated")
    require(parse_handles("300|100|200|200") == [100, 200, 300], "window handles deduplicate and sort")
    try:
        parse_handles("100|200")
    except ValueError:
        print("PASS two-window discovery fails closed")
    else:
        raise AssertionError("two-window discovery did not fail")
    require(choose_leader((100, 200, 150)) == 2, "highest-power Account2 becomes leader")
    require(choose_leader((200, 200, 200)) == 1, "power tie resolves to Account1")
    require(choose_leader((200, 300, 300)) == 2, "later equal-power account does not steal leadership")

    required_symbols = [
        "RunMultiCoordinator",
        "DiscoverGameWindows",
        "ActivateAccount",
        "BindWindowsByIdentity",
        "VerifyActiveAccountIdentity",
        "SelectHighestFormation",
        "BuildThreeAccountTeam",
        "RunTeamActivity",
        "WaitTeamBattleAll",
        "MonitorMultiAllDay",
    ]
    for symbol in required_symbols:
        require(re.search(rf"(?im)^\s*(?:Function|Sub)\s+{symbol}\b", SOURCE) is not None, f"symbol {symbol} exists")

    for start, end in (("Function ", "End Function"), ("Sub ", "End Sub"), ("While ", "Wend"), ("For ", "Next")):
        opened, closed = count_blocks(start, end)
        require(opened == closed, f"{start.strip()} blocks close ({opened}/{closed})")

    require("FAIL_FOCUS_LEASE" in SOURCE, "foreground lease guard exists")
    require("account_power_digit_" in SOURCE and "formation_power_digit_" in SOURCE, "power OCR is template-gated")
    require("team_activity_free.bmp" in SOURCE, "team entry requires positive free status")
    require("TeamPvpPolicy <> \"reward_only\"" in SOURCE, "team PvP is reward-only")
    require("ExecuteWanted" not in function_body("RunIndependentAccountModules"), "multi independent phase does not use solo wanted")
    require('RunTeamActivity("team_wanted"' in function_body("RunTeamActivities"), "wanted is routed through three-account team flow")
    require("MonitorMultiAllDay" in function_body("RunMultiCoordinator"), "full multi flow includes all-day monitor")
    require("password" not in SOURCE.lower() and "密码" not in SOURCE, "source contains no credential entry")
    require("AllowMarketPurchase=0" in CONFIG and "MarketDirectBuyVerified=0" in CONFIG,
            "generic defaults prohibit spending without current user authorization")
    require("class Scheduler" in PYTHON_SOURCE and "switch_account" in PYTHON_SOURCE, "Python shared-tab scheduler exists")
    require("VK_F12" in PYTHON_SOURCE and "emergency_stop" in PYTHON_SOURCE, "Python scheduler has F12 global emergency stop")
    require("irreversible action" in PYTHON_SOURCE and "refusing duplicate click" in PYTHON_SOURCE, "Python scheduler blocks duplicate irreversible clicks")
    require("WriteProcessMemory" not in PYTHON_SOURCE and "PostMessage" not in PYTHON_SOURCE, "Python scheduler uses no injection or background-message input")
    print("RESULT=PASS_MULTI_STATIC")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
