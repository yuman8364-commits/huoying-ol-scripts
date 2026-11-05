"""Publish the reviewed 2026.10.13.2 binaries while preserving user runtime data.

Run --check before closing the idle old GUI, then --publish. No game input.
Only the EXE and base_library.zip are eligible for replacement.
"""
from __future__ import annotations

import argparse
from datetime import datetime
import hashlib
import json
from pathlib import Path
import re
import shutil
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from tools.release_runtime_snapshot import PACKAGES, default_desktop, verify
from tools.verify_packaged_source import verify_package
from hyol_scheduler import ForegroundRuntimeLease


def read_json(path):
    return Path(path).read_text(encoding='utf-8-sig')


def digest(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def within(path, parent):
    path, parent = Path(path).resolve(), Path(parent).resolve()
    if not path.is_relative_to(parent):
        raise ValueError(f'Unexpected publication path: {path}')
    return path


def runtime_ended(logdir, events):
    if any(row.get('event') == 'multi_solo_run_end' for row in events):
        return True
    # A team run can fail during identity binding, before solo ever starts.
    # Its controller still writes a terminal receipt. The publication lease
    # separately excludes an active worker, including one in another entry.
    path = Path(logdir)/'unified_latest.json'
    if not path.is_file() or not events:
        return False
    receipt = json.loads(read_json(path))
    try:
        last = max(datetime.fromisoformat(row['ts']) for row in events)
        ended = datetime.fromisoformat(receipt['at'])
        return (receipt.get('status') in ('pending', 'stopped', 'complete')
                and type(receipt.get('result')) is int and ended >= last)
    except (KeyError, TypeError, ValueError):
        return False


def prepare(package_keys=None, *, baseline_path='logs/stall_fix_baseline_20261013.json',
            staged_path='logs/stall_fix_staged_20261013.json',
            regression_path='logs/stall_fix_full_delivery_console_20261013.log',
            staging_directory='dist_stop_cleanup_20261013', expected_counts=None):
    baseline = json.loads(read_json(within(ROOT/baseline_path, ROOT)))
    staged = json.loads(read_json(within(ROOT/staged_path, ROOT)))
    console = within(ROOT/regression_path, ROOT).read_text(encoding='utf-8-sig')
    counts = [int(value) for value in re.findall(r'Ran (\d+) tests', console)]
    if counts != (expected_counts or [15, 16, 6, 113, 39, 97, 800]) or 'PASS：全部离线测试通过' not in console:
        raise ValueError('Final full regression has not passed')
    if staged['Version'] != __import__('hyol_runtime').VERSION or len(staged['Packages']) != 5:
        raise ValueError('Unexpected staged version or package count')
    snapshot = json.loads(read_json(baseline['runtime_snapshot']))
    runtime = verify(snapshot)
    if runtime['result'] != 'PASS':
        raise ValueError(f'User runtime changed; stop publication: {runtime}')
    for config, expected in baseline['configs'].items():
        if digest(config) != expected:
            raise ValueError(f'User configuration changed: {config}')
    desktop = default_desktop()/'可用脚本'
    packages, replacements = [], []
    for key, dist, name, folder in PACKAGES:
        if package_keys and key not in package_keys:
            continue
        item = next(row for row in staged['Packages'] if row['Key'] == key)
        source = within(item['Exe'], within(ROOT/staging_directory, ROOT))
        if source.name != name+'.exe' or digest(source).upper() != item['Sha256']:
            raise ValueError(f'Staged EXE hash mismatch: {key}')
        check = verify_package(ROOT, source)
        if not check['source_code_matches']:
            raise ValueError(f'Staged embedded source mismatch: {key}')
        staged_internal = source.parent/'_internal'
        source_files = {path.relative_to(staged_internal).as_posix().casefold(): path
                        for path in staged_internal.rglob('*') if path.is_file()}
        for scope, target in [('desktop', within(desktop/folder, desktop)),
                              ('dist', within(ROOT/dist/name, ROOT))]:
            installed_internal = target/'_internal'
            installed_files = {path.relative_to(installed_internal).as_posix().casefold(): path
                               for path in installed_internal.rglob('*') if path.is_file()}
            if installed_files.keys() != source_files.keys():
                raise ValueError(f'Dependency inventory differs: {scope}/{key}')
            changed = [relative for relative in source_files
                       if digest(source_files[relative]) != digest(installed_files[relative])]
            if any(relative != 'base_library.zip' for relative in changed):
                raise ValueError(f'Unexpected dependency replacement: {scope}/{key}: {changed}')
            for relative in (name+'.exe', '_internal/base_library.zip'):
                original = target/relative
                if not original.is_file():
                    raise ValueError(f'Missing installed file: {original}')
                replacements.append(dict(key=key, scope=scope, source=str(source.parent/relative),
                                         target=str(original), relative=relative,
                                         previous_sha256=digest(original), sha256=digest(source.parent/relative)))
        packages.append(dict(key=key, modules=check['modules_checked'], source_code_matches=True))
    logdir = desktop/'火影OL完整客户端/logs'
    latest = max(logdir.glob('HYOL_Scheduler_*.jsonl'), key=lambda path: path.stat().st_mtime)
    events = [json.loads(line) for line in latest.read_text(encoding='utf-8-sig').splitlines() if line.strip()]
    if not runtime_ended(logdir, events):
        raise ValueError(f'Latest game run has not ended: {latest}')
    return baseline, dict(version=staged['Version'], packages=packages, replacements=replacements,
                         offline_tests=sum(counts), latest_ended_run=str(latest),
                         original_runtime_files=runtime['runtime_files_checked'],
                         original_state_files=baseline['state_files'], configs_preserved=len(baseline['configs']),
                         game_tasks_started=False, game_input_operations=0)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--publish', action='store_true')
    parser.add_argument('--packages', nargs='+', choices=[item[0] for item in PACKAGES])
    parser.add_argument('--receipt', default='stall_fix_published_20261013.json')
    parser.add_argument('--backup-subdir', default='installed')
    parser.add_argument('--baseline', default='logs/stall_fix_baseline_20261013.json')
    parser.add_argument('--staged', default='logs/stall_fix_staged_20261013.json')
    parser.add_argument('--regression', default='logs/stall_fix_full_delivery_console_20261013.log')
    parser.add_argument('--staging-directory', default='dist_stop_cleanup_20261013')
    parser.add_argument('--expected-test-counts', nargs='+', type=int)
    args = parser.parse_args()
    lease = ForegroundRuntimeLease()
    if not lease.acquire():
        raise ValueError('A game worker is active; preserve all installed clients')
    try:
        return execute(args)
    finally:
        lease.release()


def execute(args):
    baseline, report = prepare(args.packages, baseline_path=args.baseline, staged_path=args.staged,
                              regression_path=args.regression, staging_directory=args.staging_directory,
                              expected_counts=args.expected_test_counts)
    if not args.publish:
        print(json.dumps({key: value for key, value in report.items() if key != 'replacements'}, ensure_ascii=False))
        return
    receipt = within(ROOT/'logs'/args.receipt, ROOT/'logs')
    if receipt.exists():
        raise ValueError('Publication receipt already exists; preserve prior delivery evidence')
    backup_root = within(Path(baseline['backup'])/args.backup_subdir, Path(baseline['backup']))
    # Back up every original before replacing any file.
    for row in report['replacements']:
        backup = within(backup_root/row['scope']/row['key']/row['relative'], backup_root)
        if backup.exists():
            raise ValueError(f'Preserve existing backup: {backup}')
        backup.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(row['target'], backup)
        if digest(backup) != row['previous_sha256']:
            raise ValueError(f'Installed backup hash mismatch: {backup}')
        row['backup'] = str(backup)
    for row in report['replacements']:
        target = Path(row['target'])
        temporary = target.with_name(target.name+'.stop_cleanup_update')
        if temporary.exists():
            raise ValueError(f'Unexpected previous temporary update: {temporary}')
        shutil.copy2(row['source'], temporary)
        if digest(temporary) != row['sha256']:
            raise ValueError(f'Copied binary hash mismatch: {temporary}')
        temporary.replace(target)
    runtime = verify(json.loads(read_json(baseline['runtime_snapshot'])))
    if runtime['result'] != 'PASS':
        raise ValueError(f'Runtime preservation verification failed: {runtime}')
    for config, expected in baseline['configs'].items():
        if digest(config) != expected:
            raise ValueError(f'Configuration preservation verification failed: {config}')
    report.update(status='published', at=datetime.now().astimezone().isoformat(), runtime_preserved=True)
    with receipt.open('x', encoding='utf-8') as target:
        json.dump(report, target, ensure_ascii=False, indent=2)
        target.write('\n')
    print(json.dumps(dict(status='published', version=report['version'], files=len(report['replacements']),
                          original_runtime_files=report['original_runtime_files'], receipt=str(receipt)), ensure_ascii=False))


if __name__ == '__main__':
    main()
