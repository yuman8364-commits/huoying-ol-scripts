"""Bounded observation of one battle; never clicks or changes game results."""
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
    idle_seconds: float = 180.0
    max_seconds: float = 600.0
    started_at: float | None = None
    changed_at: float | None = None
    reference: np.ndarray | None = field(default=None, repr=False)

    @classmethod
    def from_config(cls, config, *, started_at=None):
        config = config if isinstance(config, Mapping) else {}
        return cls(
            idle_seconds=_seconds(config, 'CombatStallSeconds', 180, 30, 600),
            max_seconds=_seconds(config, 'CombatMaxSeconds', 600, 60, 1200),
            started_at=started_at,
        )

    @staticmethod
    def sample(frame):
        # Ignore browser chrome, chat, the auto badge and corner clocks. A
        # coarse battlefield sample also ignores tiny cursor/hover changes.
        if not isinstance(frame, Image.Image):
            return None
        w, h = frame.size
        box = (round(w * .14), round(h * .21), round(w * .86), round(h * .77))
        return np.asarray(frame.crop(box).convert('L').resize(
            (96, 48), Image.Resampling.BILINEAR), dtype=np.int16)

    def observe(self, frame, now):
        """Return a timeout record, or None. Animation never resets total time."""
        if self.started_at is None:
            self.started_at = now
        elapsed = max(0.0, now - self.started_at)
        sample = self.sample(frame)
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
        reason = ('max_duration' if elapsed >= self.max_seconds else
                  'unchanged_frame' if sample is not None and idle >= self.idle_seconds else None)
        if reason is None:
            return None
        return dict(reason=reason, elapsed_seconds=round(elapsed, 3),
                    unchanged_seconds=round(idle, 3),
                    max_seconds=self.max_seconds, idle_seconds=self.idle_seconds)
