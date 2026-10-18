"""Lossless compact traces: timing bar, bottom cylinder and reward tiles."""
from __future__ import annotations

import io
from pathlib import Path
import statistics
import uuid
import zipfile

import numpy as np

from .vision import BAR, BOTTOM, SLOTS, TRACE, Pending
from .journal import replace_file


REWARDS = (SLOTS[0][0], SLOTS[0][1], SLOTS[-1][2], SLOTS[-1][3])


def crop_roi(pixels, roi, source=TRACE):
    return pixels[roi[1]-source[1]:roi[3]-source[1], roi[0]-source[0]:roi[2]-source[0]].copy()


def save_trace(path, bars, bottoms, rewards, times):
    """Standard .npz with fast, lossless ZIP compression and atomic publication."""
    path = Path(path)
    temporary = path.with_name(path.name+'.'+uuid.uuid4().hex+'.tmp')
    arrays = dict(frames=np.stack(bars), roi=np.asarray(BAR), timestamps=np.asarray(times),
                  bottom_frames=np.stack(bottoms), bottom_roi=np.asarray(BOTTOM),
                  reward_frames=np.stack(rewards), reward_roi=np.asarray(REWARDS))
    with zipfile.ZipFile(temporary, 'w', compression=zipfile.ZIP_DEFLATED, compresslevel=1) as archive:
        for name, array in arrays.items():
            buffer = io.BytesIO()
            np.save(buffer, array, allow_pickle=False)
            archive.writestr(name+'.npy', buffer.getvalue())
    replace_file(temporary, path)


def measured_latency(record, action, judgment):
    """Median of three independent measurements; replay cannot double-weight one hit."""
    estimate = action['latency']+(judgment-sum(action['zone'])/2)/action['velocity']
    if not 0 <= estimate <= .35:
        raise Pending('敲击实测延迟超出可信范围')
    history = [row for row in record.get('latency_history', []) if row['action'] != action['id']]
    history.append(dict(action=action['id'], estimate=round(estimate, 6), judgment_x=judgment))
    history = history[-5:]
    return round(statistics.median(row['estimate'] for row in history[-3:]), 6), history
