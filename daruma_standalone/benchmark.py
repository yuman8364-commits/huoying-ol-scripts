"""Reproducible lossless trace comparison using an already-recorded full trace."""
from __future__ import annotations

import argparse
import json
from pathlib import Path
import statistics
import time

import numpy as np

from .journal import atomic_json
from .runtime import RUNTIME
from .trace import crop_roi, save_trace, REWARDS
from .vision import BAR, BOTTOM, TRACE, judgment_from_frames


def run(source, root=RUNTIME):
    directory = Path(root)/'verification'
    directory.mkdir(parents=True, exist_ok=True)
    with np.load(source, allow_pickle=False) as recording:
        frames, times, roi = recording['frames'], recording['timestamps'], tuple(recording['roi'])
    if roi != TRACE:
        raise ValueError('基准输入须为原始完整动画轨迹')
    bars = [crop_roi(f, BAR) for f in frames]
    bottoms = [crop_roi(f, BOTTOM) for f in frames]
    rewards = [crop_roi(f, REWARDS) for f in frames]
    old_ms, new_ms = [], []
    for i in range(3):
        old_path, new_path = directory/f'trace_baseline_{i}.npz', directory/f'trace_compact_{i}.npz'
        began = time.perf_counter()
        np.savez_compressed(old_path, frames=frames, timestamps=times, roi=np.asarray(roi))
        old_ms.append((time.perf_counter()-began)*1000)
        began = time.perf_counter()
        save_trace(new_path, bars, bottoms, rewards, times)
        new_ms.append((time.perf_counter()-began)*1000)
    before = judgment_from_frames(list(frames), (1025, 1064), roi=roi)
    with np.load(new_path, allow_pickle=False) as compact:
        after = judgment_from_frames(list(compact['frames']), (1025, 1064), roi=tuple(compact['roi']))
    if before is None or before != after:
        raise AssertionError('轨迹压缩后判定标记未复现')
    report = dict(source=str(Path(source).resolve()), frames=len(frames), judgment_before=before,
                  judgment_after=after, old_median_ms=statistics.median(old_ms),
                  new_median_ms=statistics.median(new_ms), old_bytes=old_path.stat().st_size,
                  new_bytes=new_path.stat().st_size, content_clicks=0)
    report['compression_speedup'] = report['old_median_ms']/report['new_median_ms']
    atomic_json(directory/'trace_benchmark.json', report)
    return report


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('source', type=Path)
    args = parser.parse_args()
    print(json.dumps(run(args.source), ensure_ascii=False))
