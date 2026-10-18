"""Record actual local regression file dependencies before pruning old artifacts."""
from __future__ import annotations

import json
import os
from pathlib import Path
import stat
import sys
import unittest
from datetime import datetime

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
PREFIX = os.path.normcase(str(ROOT) + os.sep)
ACCESSED: dict[str, set[str]] = {}
ORIGINAL_STAT = os.stat
ACCESS_MODE_MASK = os.O_WRONLY | os.O_RDWR


def record(path, kind):
    try:
        if isinstance(path, int):
            return
        normalized = os.path.abspath(os.fsdecode(os.fspath(path)))
        if os.path.normcase(normalized).startswith(PREFIX):
            ACCESSED.setdefault(normalized, set()).add(kind)
    except (TypeError, ValueError):
        return


def audit(event, args):
    if event != 'open':
        return
    path, mode, flags = args
    if isinstance(mode, str) and ('r' in mode or '+' in mode):
        record(path, 'read')
    elif mode is None and (flags & ACCESS_MODE_MASK) != os.O_WRONLY:
        record(path, 'read')


def recorded_stat(path, *args, **kwargs):
    result = ORIGINAL_STAT(path, *args, **kwargs)
    if stat.S_ISREG(result.st_mode):
        record(path, 'stat')
    return result


def main():
    output = Path(sys.argv[1]).resolve()
    if output.exists():
        raise ValueError('Keep the previous trace; use a new output path')
    sys.addaudithook(audit)
    os.stat = recorded_stat
    started = datetime.now().astimezone().isoformat()
    try:
        suite = unittest.defaultTestLoader.discover(str(ROOT), pattern='test*.py')
        result = unittest.TextTestRunner(verbosity=1).run(suite)
    finally:
        os.stat = ORIGINAL_STAT
    paths = [dict(path=path, access=sorted(kinds)) for path, kinds in sorted(ACCESSED.items())]
    report = dict(started_at=started, finished_at=datetime.now().astimezone().isoformat(),
                  tests=result.testsRun, failures=len(result.failures), errors=len(result.errors),
                  result='PASS' if result.wasSuccessful() else 'FAIL', files=paths)
    output.write_text(json.dumps(report, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
    print(json.dumps(dict(result=report['result'], tests=report['tests'],
                          dependency_files=len(paths), trace=str(output)), ensure_ascii=False))
    return 0 if result.wasSuccessful() else 1


if __name__ == '__main__':
    raise SystemExit(main())
