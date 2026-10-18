from pathlib import Path


ROOT = Path(__file__).resolve().parent
SOURCE = (ROOT / "HYOL_Daily.Q.utf8").read_text(encoding="utf-8")
CONFIG = (ROOT / "HYOL_Daily.ini").read_text(encoding="utf-8")


def require(condition: bool, label: str) -> None:
    if not condition:
        raise AssertionError(label)
    print("PASS " + label)


require('RunMode = "market_probe"' in SOURCE, "isolated market probe entry exists")
require('LogFile = LogDir & "\\HYOL_Market_Probe.log"' in SOURCE, "probe has isolated log")
require('StateFile = LogDir & "\\HYOL_Market_Probe.state"' in SOURCE, "probe has isolated state")
require('market_entry_view.bmp' in SOURCE, "notification View button is independently matched")
require('cardX + 55, cardY + 110, cardX + 155, cardY + 160' in SOURCE, "View match is constrained to notification card")
require('FindTemplate("market_buy_button.bmp", x1, y1 + 65, x2, y2)' in SOURCE, "buy button is constrained to current item slot")
require('Replace(RunDate, "-", "") & "_" & cycleKey & "_slot"' in SOURCE, "purchase key includes date cycle and slot")
require('StateGet(offerKey & "_pending") = "1"' in SOURCE, "unknown purchase blocks duplicate click")
require('couponAfter = couponBefore - price And ingotAfter = ingotBefore' in SOURCE, "purchase verifies coupon delta and unchanged ingot")
require('MarketCurrency <> "coupon_only"' in SOURCE, "non-coupon policy fails closed")
require('MarketMaxPrice=none' in CONFIG, "no price ceiling is configured")
require('AllowMarketPurchase=0' in CONFIG and 'MarketDirectBuyVerified=0' in CONFIG, "clean generic defaults prohibit purchases without per-user authorization")
require('MarketMonitorMode=all_day' in CONFIG, "all-day monitoring armed after live single-trial evidence")
require('ManagedActiveStable(10, 10)' in SOURCE, "managed start requires stable positive recognition")
require('managed active template pending; refusing managed click' in SOURCE, "missing managed-positive template blocks click")

print("RESULT=PASS_MARKET_SAFETY_STATIC")
