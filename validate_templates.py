from __future__ import annotations

from pathlib import Path

import numpy as np
from PIL import Image


ROOT = Path(__file__).resolve().parent
ASSETS = ROOT / "assets"
LOGS = ROOT / "logs"
THRESHOLD = 0.90


def gray(path: Path) -> np.ndarray:
    return np.asarray(Image.open(path).convert("L"), dtype=np.float64)


def rgb(path: Path) -> np.ndarray:
    return np.asarray(Image.open(path).convert("RGB"))


def ncc(image_path: Path, template_path: Path) -> tuple[float, int, int]:
    image = gray(image_path)
    template = gray(template_path)
    height, width = image.shape
    th, tw = template.shape
    centered = template - template.mean()
    template_var = np.sum(centered * centered)
    shape = (height + th - 1, width + tw - 1)
    correlation = np.fft.irfft2(
        np.fft.rfft2(image, shape) * np.fft.rfft2(centered[::-1, ::-1], shape),
        shape,
    )
    numerator = correlation[th - 1 : height, tw - 1 : width]
    integral = np.pad(image, ((1, 0), (1, 0))).cumsum(0).cumsum(1)
    integral2 = np.pad(image * image, ((1, 0), (1, 0))).cumsum(0).cumsum(1)
    sums = integral[th:, tw:] - integral[:-th, tw:] - integral[th:, :-tw] + integral[:-th, :-tw]
    sums2 = integral2[th:, tw:] - integral2[:-th, tw:] - integral2[th:, :-tw] + integral2[:-th, :-tw]
    variance = np.maximum(sums2 - sums * sums / (th * tw), 0)
    denominator = np.sqrt(variance * template_var)
    scores = np.full_like(numerator, -1.0)
    np.divide(numerator, denominator, out=scores, where=denominator > 1e-12)
    y, x = np.unravel_index(np.argmax(scores), scores.shape)
    return float(scores[y, x]), int(x), int(y)


def exact_matches(image_path: Path, template_path: Path) -> list[tuple[int, int]]:
    image = rgb(image_path)
    template = rgb(template_path)
    th, tw = template.shape[:2]
    oy, ox = th // 2, tw // 2
    ys, xs = np.where(np.all(image == template[oy, ox], axis=2))
    matches: list[tuple[int, int]] = []
    for py, px in zip(ys.tolist(), xs.tolist()):
        y, x = py - oy, px - ox
        if y < 0 or x < 0 or y + th > image.shape[0] or x + tw > image.shape[1]:
            continue
        if np.array_equal(image[y : y + th, x : x + tw], template):
            matches.append((x, y))
    return matches


def main() -> int:
    failures: list[str] = []
    positive = [
        ("market_entry_icon_current.bmp", "current_after_stop_20260829.png"),
        ("market_entry_view_current.bmp", "current_after_stop_20260829.png"),
        ("market_dialog_close_current.bmp", "current_dialog_after_probe_20260829.png"),
        ("market_dialog_view_option_current.bmp", "current_dialog_after_probe_20260829.png"),
        ("market_dialog_close_current.bmp", "market_probe_after_view_20260829.png"),
        ("market_dialog_view_option_current.bmp", "market_probe_after_view_20260829.png"),
        ("market_npc_label_current.bmp", "current_after_dialog_close_20260829.png"),
        ("market_npc_label_current.bmp", "current_dialog_after_probe_20260829.png"),
        ("market_entry_icon_current.bmp", "imagegrab_x310_20260829.png"),
        ("market_entry_view_current.bmp", "imagegrab_x310_20260829.png"),
        ("market_entry_icon_current.bmp", "imagegrab_x590_20260829.png"),
        ("market_entry_view_current.bmp", "imagegrab_x590_20260829.png"),
        ("market_entry.bmp", "south_market_prompt_0854.png"),
        ("market_entry_view.bmp", "south_market_prompt_0854.png"),
        ("market_anchor.bmp", "market_after_entry_run_8s.png"),
        ("market_stamina_potion.bmp", "market_after_entry_run_8s.png"),
        ("market_stamina_potion_any.bmp", "market_after_entry_run_8s.png"),
        ("market_coupon_icon.bmp", "market_after_entry_run_8s.png"),
        ("market_buy_button.bmp", "market_after_entry_run_8s.png"),
        ("market_close.bmp", "market_after_entry_run_8s.png"),
        ("market_anchor.bmp", "market_2300_2nv_live_modal_20260827.png"),
        ("market_stamina_potion.bmp", "market_2300_2nv_live_modal_20260827.png"),
        ("market_coupon_icon.bmp", "market_2300_2nv_live_modal_20260827.png"),
        ("market_buy_button.bmp", "market_2300_2nv_live_modal_20260827.png"),
        ("market_close.bmp", "market_2300_2nv_live_modal_20260827.png"),
        ("market_npc_label.bmp", "market_npc_fallback_live_20260827.png"),
        ("managed_mode_button.bmp", "assist_before.bmp"),
        ("microclient_account_multi.bmp", "multi_before_probe_20260820.png"),
    ]
    negative = [
        ("market_entry_icon_current.bmp", "current_live_20260828_0134.png"),
        ("market_entry_view_current.bmp", "current_live_20260828_0134.png"),
        ("market_dialog_close_current.bmp", "current_live_20260828_0134.png"),
        ("market_dialog_view_option_current.bmp", "current_live_20260828_0134.png"),
        ("market_npc_label_current.bmp", "current_live_20260828_0134.png"),
        ("market_entry.bmp", "market_after_entry_run.png"),
        ("market_entry.bmp", "market_run_second_state.png"),
        ("market_entry_view.bmp", "market_after_entry_run.png"),
        ("market_entry_view.bmp", "market_run_second_state.png"),
        ("market_npc_label.bmp", "market_after_entry_run_8s.png"),
    ]
    for asset, screenshot in positive:
        score, x, y = ncc(LOGS / screenshot, ASSETS / asset)
        print(f"PASS? positive {asset} vs {screenshot}: score={score:.6f} at=({x},{y})")
        if score < THRESHOLD:
            failures.append(f"positive score below threshold: {asset} vs {screenshot}")
    for asset, screenshot in negative:
        score, x, y = ncc(LOGS / screenshot, ASSETS / asset)
        print(f"PASS? negative {asset} vs {screenshot}: score={score:.6f} at=({x},{y})")
        if score >= THRESHOLD:
            failures.append(f"negative score reached threshold: {asset} vs {screenshot}")

    entry_score, entry_x, entry_y = ncc(LOGS / "south_market_prompt_0854.png", ASSETS / "market_entry.bmp")
    view_score, view_x, view_y = ncc(
        LOGS / "south_market_prompt_0854.png", ASSETS / "market_entry_view.bmp"
    )
    click = (view_x + 36, view_y + 14)
    print(f"entry safe click={click}")
    if not (1775 <= click[0] <= 1810 and 845 <= click[1] <= 880):
        failures.append(f"entry click is outside View button ROI: {click}")
    if not (
        entry_x + 55 <= view_x <= entry_x + 155
        and entry_y + 110 <= view_y <= entry_y + 160
        and view_score >= THRESHOLD
    ):
        failures.append("View button is not verified inside the matched notification card")

    current_frame = LOGS / "current_after_stop_20260829.png"
    current_icon_score, current_icon_x, current_icon_y = ncc(current_frame, ASSETS / "market_entry_icon_current.bmp")
    current_view_score, current_view_x, current_view_y = ncc(current_frame, ASSETS / "market_entry_view_current.bmp")
    print(f"current notification icon=({current_icon_x},{current_icon_y}) view=({current_view_x},{current_view_y})")
    if current_icon_score < THRESHOLD or current_view_score < THRESHOLD:
        failures.append("current notification icon/View assets did not match live evidence")
    if not (abs(current_view_x - current_icon_x - 8) <= 55 and 90 <= current_view_y - current_icon_y <= 145):
        failures.append("current notification View match is not linked to icon geometry")

    dialog_frame = LOGS / "current_dialog_after_probe_20260829.png"
    dialog_close_score, dialog_close_x, dialog_close_y = ncc(dialog_frame, ASSETS / "market_dialog_close_current.bmp")
    dialog_option_score, dialog_option_x, dialog_option_y = ncc(dialog_frame, ASSETS / "market_dialog_view_option_current.bmp")
    print(f"current notification dialog close=({dialog_close_x},{dialog_close_y}) option=({dialog_option_x},{dialog_option_y})")
    if dialog_close_score < THRESHOLD or dialog_option_score < THRESHOLD:
        failures.append("current notification dialog assets did not match live evidence")
    if not (
        dialog_close_x - 500 <= dialog_option_x <= dialog_close_x - 200
        and dialog_close_y + 100 <= dialog_option_y <= dialog_close_y + 210
    ):
        failures.append("dialog first option is not linked to its verified close control")

    npc_frame = LOGS / "current_after_dialog_close_20260829.png"
    npc_score, npc_x, npc_y = ncc(npc_frame, ASSETS / "market_npc_label_current.bmp")
    print(f"current NPC label=({npc_x},{npc_y}) score={npc_score:.6f}")
    if npc_score < THRESHOLD or not (800 <= npc_x <= 1500 and 250 <= npc_y <= 600):
        failures.append("current NPC label asset did not match live South Konoha evidence")

    multi_score, multi_x, multi_y = ncc(
        LOGS / "multi_before_probe_20260820.png", ASSETS / "microclient_account_multi.bmp"
    )
    multi_click = (multi_x + 45, multi_y + 14)
    print(f"account-multi safe click={multi_click}")
    if not (260 <= multi_click[0] <= 325 and 8 <= multi_click[1] <= 30):
        failures.append(f"account-multi click is outside toolbar button ROI: {multi_click}")

    buy_matches = exact_matches(LOGS / "market_after_entry_run_8s.png", ASSETS / "market_buy_button.bmp")
    local_matches = [(x, y) for x, y in buy_matches if 620 <= x <= 885 and 434 <= y <= 544]
    print(f"buy exact matches total={len(buy_matches)} first_slot={local_matches}")
    if len(buy_matches) != 6 or len(local_matches) != 1:
        failures.append("buy button must match six cards globally and exactly one first-slot ROI")

    required_live = [
        "market_sold_out.bmp",
        "managed_active.bmp",
        "managed_exit.bmp",
    ]
    required_live.extend(f"market_price_digit_{digit}.bmp" for digit in range(10))
    required_live.extend(f"market_balance_digit_{digit}.bmp" for digit in range(10))
    missing = [name for name in required_live if not (ASSETS / name).exists()]
    print("LIVE_ASSETS_PENDING=" + ",".join(missing))

    if failures:
        for failure in failures:
            print("FAIL " + failure)
        return 1
    print("RESULT=PASS_OFFLINE_REPLAY")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
