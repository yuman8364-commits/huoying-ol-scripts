"""Read enrolled Survival grade counters; never infer attempts from score."""
from __future__ import annotations

import json
from functools import lru_cache
from pathlib import Path

import numpy as np
from PIL import Image

# Counter positions relative to the enrolled grade badge, in canonical pixels.
GRADES = (
    ("SS", "survival_grade_ss.bmp", -12, -15),
    ("S", "survival_grade_s.bmp", -28, -7),
    ("A", "survival_grade_a_current.bmp", -15, -4),
    ("B", "survival_grade_b_current.bmp", -10, -6),
    ("C", "survival_grade_c.bmp", -14, 1),
)


@lru_cache(maxsize=4)
def _counter_glyphs(path: str, modified: int):
    return tuple((int(digit), np.pad(np.asarray(pixels,dtype=bool),1))
                 for digit,pixels in json.loads(Path(path).read_text(encoding='utf-8')).items())


def read_counter(frame: Image.Image, x: int, y: int, assets: Path) -> int | None:
    """Match a single white counter glyph with dark padding and a clear margin."""
    path = assets/'survival_counter_glyphs.json'
    glyphs = _counter_glyphs(str(path),path.stat().st_mtime_ns)
    region = np.asarray(frame.crop((x - 3, y - 3, x + 10, y + 12)).convert("L")) > 180
    candidates = []
    for digit, glyph in glyphs:
        height, width = glyph.shape
        best = 0.
        for top in range(region.shape[0] - height + 1):
            for left in range(region.shape[1] - width + 1):
                best = max(best, float(np.mean(region[top:top + height, left:left + width] == glyph)))
        candidates.append((best, int(digit)))
    candidates.sort(reverse=True)
    if candidates[0][0] >= .96 and candidates[0][0] - candidates[1][0] >= .07:
        return candidates[0][1]
    return None


def grade_candidates(frame: Image.Image, templates, assets: Path) -> list[dict]:
    from hyol_scheduler import ncc_map
    result = []
    roi = (650, 180, 1350, 760)
    canvas = np.asarray(frame.crop(roi).convert('L'), dtype=np.float64)
    for rank, (grade, name, dx, dy) in enumerate(GRADES):
        badge = templates.load(assets / name)
        scores = ncc_map(canvas, badge)
        matches = []
        for _ in range(12):
            py, px = np.unravel_index(np.argmax(scores), scores.shape)
            score = float(scores[py, px])
            if score < .95:
                break
            matches.append((score, roi[0] + int(px), roi[1] + int(py)))
            scores[max(0, py - 25):py + 26, max(0, px - 25):px + 26] = -1
        accepted = []
        for score, x, y in sorted(matches, key=lambda item: -item[0]):
            if any(abs(x - px) < 25 and abs(y - py) < 25 for px, py in accepted):
                continue
            accepted.append((x, y))
            remaining = read_counter(frame, x + dx, y + dy, assets)
            result.append(dict(grade=grade, rank=rank, template=name, x=x, y=y,
                               remaining=remaining, match_score=round(score, 4)))
    return sorted(result, key=lambda item: (item["rank"], item["x"], item["y"]))


def choose_grade(candidates: list[dict]) -> dict | None:
    """An unreadable higher grade blocks a lower-grade click."""
    for candidate in sorted(candidates, key=lambda item: (item["rank"], item["x"], item["y"])):
        if candidate["remaining"] is None:
            raise ValueError(f'{candidate["grade"]}级剩余次数未确认')
        if candidate["remaining"] > 0:
            return candidate
    return None
