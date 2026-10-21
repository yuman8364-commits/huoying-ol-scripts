"""Observe one account/step for 30 seconds without meaningful visual change."""
from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
import math

import numpy as np
from PIL import Image


def _seconds(config, key, default, minimum, maximum):
    try:
        value = float(config.get(key, default))
    except (TypeError, ValueError, OverflowError):
        return float(default)
    if not math.isfinite(value) or value <= 0:
        return float(default)
    return min(float(maximum), max(float(minimum), value))


@dataclass
class BattleWatchdog:
    idle_seconds: float = 30.0
    # Kept for callers that calculate a deadline. Progressing battles have no
    # total-duration cutoff; old CombatMaxSeconds values are intentionally ignored.
    max_seconds: float = math.inf
    started_at: float | None = None
    changed_at: float | None = None
    last_observed_at: float | None = None
    reference: np.ndarray | None = field(default=None, repr=False)
    mode: str = "battle"

    @classmethod
    def from_config(cls, config, *, started_at=None):
        config = config if isinstance(config, Mapping) else {}
        return cls(
            idle_seconds=30.0,
            max_seconds=math.inf,
            started_at=started_at,
        )

    @staticmethod
    def sample(frame, mode="battle"):
        # Ignore browser chrome, chat, the auto badge and corner clocks. A
        # coarse battlefield sample also ignores tiny cursor/hover changes.
        if not isinstance(frame, Image.Image):
            return None
        w, h = frame.size
        box = (round(w * .14), round(h * .21), round(w * .86), round(h * .77))
        sampled = np.asarray(frame.crop(box).convert('L').resize(
            (96, 48), Image.Resampling.BILINEAR), dtype=np.int16).copy()
        # The cross-server announcement runs across the top of this region.
        sampled[:7, :] = 0
        sampled[-10:, :32] = 0  # Bottom-left chat inside the combat frame.
        pieces = [sampled.ravel()]
        extra = {
            'survival': ((820, 865, 1130, 980),),
            'ranking': ((1430, 150, 1620, 190),),
            'managed': ((850, 130, 1070, 190),),
        }.get(mode, ())
        for roi in extra:
            scaled = tuple(round(v * (w / 1920 if i % 2 == 0 else h / 1032))
                           for i, v in enumerate(roi))
            pieces.append(np.asarray(frame.crop(scaled).convert('L').resize(
                (48, 12), Image.Resampling.BILINEAR), dtype=np.int16).ravel())
        return np.concatenate(pieces)

    def observe(self, frame, now, *, backgrounded=False):
        """Return a stall record, or None; only observed visual change resets idle."""
        if self.started_at is None:
            self.started_at = now
        elapsed = max(0.0, now - self.started_at)
        sample = self.sample(frame, self.mode)
        # A background tab can run for over 30 seconds without being sampled.
        # Unobserved time is not evidence of a frozen frame: resume observation
        # from a fresh baseline instead of declaring a stall immediately.
        if (backgrounded and self.last_observed_at is not None
                and now - self.last_observed_at > self.idle_seconds):
            self.reference = None
            self.changed_at = None
        self.last_observed_at = now
        if sample is not None:
            if self.reference is None:
                self.reference = sample
                self.changed_at = now
            elif np.count_nonzero(np.abs(sample - self.reference) >= 12) >= sample.size * .02:
                # Compare with the last meaningful sample, so slow changes
                # accumulate rather than disappearing between adjacent polls.
                self.reference = sample
                self.changed_at = now
        idle = max(0.0, now - self.changed_at) if self.changed_at is not None else 0.0
        reason = 'unchanged_frame' if sample is not None and idle >= self.idle_seconds else None
        if reason is None:
            return None
        return dict(reason=reason, elapsed_seconds=round(elapsed, 3),
                    unchanged_seconds=round(idle, 3),
                    max_seconds=None, idle_seconds=self.idle_seconds)
