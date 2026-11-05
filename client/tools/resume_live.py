"""Acknowledge an explicit user resume, preserving the F12 audit record."""
import argparse
import json
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import hyol_scheduler as hs
from hyol_diagnostics import clear_manual_pause, manual_pause_status


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--reason', required=True)
    parser.add_argument('--root', type=Path, default=hs.ROOT,
                        help='项目或已发布客户端根目录；默认当前源码项目')
    args = parser.parse_args()
    root = args.root.resolve()
    before = manual_pause_status(root)
    cleared = clear_manual_pause(root, args.reason)
    result = {'root': str(root), 'before': before, 'cleared': cleared,
              'after': manual_pause_status(root)}
    hs.atomic_json(root / 'logs' / 'resume_live_latest.json', result)
    print(json.dumps(result, ensure_ascii=False))
    return 0 if not result['after']['paused'] else 2


if __name__ == '__main__':
    raise SystemExit(main())
