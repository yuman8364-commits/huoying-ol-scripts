"""Preserve auditable hashes of existing desktop/dist checkpoints and logs.

Read-only for runtime files: no game input, checkpoint migration or deletion.
Added files are listed separately; every original file must remain byte-identical.
"""
from __future__ import annotations

import argparse
from datetime import datetime
import hashlib
import json
import os
from pathlib import Path
import re


PACKAGES = (
    ('daily', 'dist_daily', '火影OL日常三合一', '火影OL日常任务'),
    ('team', 'dist_team_client', '火影OL组队任务通用版', '火影OL组队任务'),
    ('arena', 'dist_arena_generic_v10', '火影OL竞技场通用v5', '火影OL竞技场'),
    ('bond', 'dist_bond_v1', '火影OL羁绊连续作战v1', '火影OL羁绊'),
    ('unified', 'dist_unified_client', '火影OL完整客户端', '火影OL完整客户端'),
)
RUNTIME_FILE = re.compile(r'_(state|result|failure)\.json$', re.IGNORECASE)


def digest(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def runtime_files(package_root):
    package_root = Path(package_root)
    if not package_root.is_dir():
        return []
    files = []
    for child in package_root.iterdir():
        if child.is_dir() and child.name in ('state', 'logs'):
            files.extend(path for path in child.rglob('*') if path.is_file())
        elif child.is_file() and RUNTIME_FILE.search(child.name):
            files.append(child)
    return sorted(files, key=str)


def package_roots(root, desktop):
    for key, dist, name, folder in PACKAGES:
        yield key+'_dist', Path(root)/dist/name
        yield key+'_desktop', Path(desktop)/'可用脚本'/folder


def capture(root, desktop):
    roots = dict(package_roots(root, desktop))
    rows = [dict(path=str(path.resolve()), sha256=digest(path), size=path.stat().st_size, scope=scope)
            for scope, package in roots.items() for path in runtime_files(package)]
    return dict(schema_version=1, captured_at=datetime.now().astimezone().isoformat(),
                roots={key: str(value.resolve()) for key, value in roots.items()},
                files=rows, runtime_files=len(rows), input_operations=0)


def verify(snapshot):
    missing, changed = [], []
    original = set()
    for row in snapshot['files']:
        path = Path(row['path'])
        original.add(str(path.resolve()))
        if not path.is_file():
            missing.append(str(path))
        elif digest(path) != row['sha256']:
            changed.append(str(path))
    current = {str(path.resolve()) for package in snapshot['roots'].values() for path in runtime_files(package)}
    return dict(result='PASS' if not missing and not changed else 'FAIL',
                runtime_files_checked=len(snapshot['files']), missing=missing, changed=changed,
                added=sorted(current-original), input_operations=0)


def default_desktop():
    # Respect redirected Desktop folders rather than assuming USERPROFILE/Desktop.
    if os.name == 'nt':
        import winreg
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, r'Software\Microsoft\Windows\CurrentVersion\Explorer\User Shell Folders') as key:
            value = winreg.QueryValueEx(key, 'Desktop')[0]
        return Path(os.path.expandvars(value))
    return Path.home()/'Desktop'


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--root', type=Path, default=Path(__file__).resolve().parents[1])
    parser.add_argument('--desktop', type=Path)
    parser.add_argument('--capture', type=Path, help='new evidence path; existing evidence is not overwritten')
    parser.add_argument('--verify', type=Path, help='prior capture file to compare')
    args = parser.parse_args()
    if (args.capture is None) == (args.verify is None):
        parser.error('select exactly one of --capture/--verify')
    if args.capture is not None:
        if args.capture.exists():
            raise ValueError('preserve prior snapshot; choose a new output path')
        result = capture(args.root, args.desktop or default_desktop())
        args.capture.parent.mkdir(parents=True, exist_ok=True)
        with args.capture.open('x', encoding='utf-8') as target:
            json.dump(result, target, ensure_ascii=False, indent=2)
            target.write('\n')
        print(json.dumps(dict(snapshot=str(args.capture), runtime_files=result['runtime_files'], input_operations=0), ensure_ascii=False))
        return 0
    result = verify(json.loads(args.verify.read_text(encoding='utf-8-sig')))
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0 if result['result'] == 'PASS' else 1


if __name__ == '__main__':
    raise SystemExit(main())
