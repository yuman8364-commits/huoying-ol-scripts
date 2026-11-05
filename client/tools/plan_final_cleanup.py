"""Plan removal of superseded files; retain traced fixtures and provenance.

This tool only reads files and writes a reviewable manifest. File removal is
performed by prune_final_workspace.ps1 after checking the manifest and hashes.
"""
from __future__ import annotations

import ast
from collections import defaultdict
from datetime import datetime
import hashlib
import json
from pathlib import Path
import re
import sys

ROOT = Path(__file__).resolve().parents[1]
LOGS = ROOT / 'logs'
ARCHIVE = Path(r'D:\火影OL脚本归档\20261009_文件整理')
OUTPUT = LOGS / 'final_cleanup_plan_20261009.json'
TRACE = LOGS / 'final_cleanup_fixture_trace_v2_20261009.json'
REASONS: dict[Path, set[str]] = defaultdict(set)
ALL_LOGS = {p.resolve(): p.stat().st_size for p in LOGS.rglob('*') if p.is_file()}
BY_NAME: dict[str, list[Path]] = defaultdict(list)
for _path in ALL_LOGS:
    BY_NAME[_path.name].append(_path)


def preserve(path: Path, reason: str) -> None:
    path = path.resolve()
    if path in ALL_LOGS:
        REASONS[path].add(reason)


def references(value: str, origin: Path, reason: str) -> None:
    if len(value) > 32768:
        return
    if value.startswith(('{', '[')):
        try:
            visit(json.loads(value), origin, reason)
        except (ValueError, TypeError):
            pass
    variants = [value]
    variants.extend(re.findall(r'(?:logs|scheduler_evidence)[/\\][^\s\"\'`<>]+', value))
    for item in variants:
        item = item.strip(' \"\'`;,.)').replace('\\\\', '\\')
        if not item or '\n' in item or '{' in item or '}' in item:
            continue
        candidates = [origin.parent / item, ROOT / item, LOGS / item]
        for candidate in candidates:
            if candidate.is_file():
                preserve(candidate, reason)
            elif any(char in item for char in '*?[') and ('/' in item or '\\' in item):
                try:
                    for matched in candidate.parent.glob(candidate.name):
                        preserve(matched, reason)
                except (OSError, ValueError):
                    pass
        if '/' not in item and '\\' not in item and len(item) > 8 and '*' not in item:
            for candidate in BY_NAME.get(item, []):
                preserve(candidate, reason)


def visit(value, origin: Path, reason: str) -> None:
    if isinstance(value, str):
        references(value, origin, reason)
    elif isinstance(value, dict):
        for nested in value.values():
            visit(nested, origin, reason)
    elif isinstance(value, list):
        for nested in value:
            visit(nested, origin, reason)


def main() -> None:
    if OUTPUT.exists():
        raise ValueError('Preserve the existing plan before creating another')
    trace = json.loads(TRACE.read_text('utf-8'))
    if trace['result'] != 'PASS' or trace['tests'] != 1016:
        raise ValueError('Cleanup requires successful dependency-traced regression')
    for row in trace['files']:
        preserve(Path(row['path']), '1016-test dependency trace')

    for folder in ('assets', 'models', 'datasets', 'training', 'runs', 'state'):
        for path in (ROOT / folder).rglob('*.json'):
            try:
                visit(json.loads(path.read_text('utf-8-sig')), path, folder + ' provenance/state reference')
            except (UnicodeError, ValueError):
                continue
    for path in ROOT.glob('*.json'):
        try:
            visit(json.loads(path.read_text('utf-8-sig')), path, 'current root result reference')
        except (UnicodeError, ValueError):
            continue

    source_files = list(ROOT.glob('*.py')) + list((ROOT / 'tools').rglob('*.py'))
    source_files += list(ROOT.glob('*.ps1')) + list((ROOT / 'tools').rglob('*.ps1'))
    for path in source_files:
        if path.name in ('plan_final_cleanup.py', 'prune_final_workspace.ps1', 'organize_workspace.ps1'):
            continue
        text = path.read_text('utf-8-sig')
        if path.suffix == '.py':
            try:
                for node in ast.walk(ast.parse(text)):
                    if isinstance(node, ast.Constant) and isinstance(node.value, str):
                        references(node.value, path, 'source/tool fixture reference')
            except SyntaxError:
                continue
        else:
            references(text, path, 'build/tool reference')

    for path in ALL_LOGS:
        relative = path.relative_to(LOGS).as_posix()
        if re.match(r'scheduler_evidence/2026-10-0[789]/', relative):
            preserve(path, 'current unresolved run evidence')
        if path.parent == LOGS and (
            path.name.startswith(('final_cleanup_', 'folder_cleanup_', 'solo_live_fix_'))
            or path.name in ('github_publication_ready_20261009.json',
                             'github_upload_verified_20261009.json')):
            preserve(path, 'current release/cleanup proof')

    # Resolve image/journal links from retained records, without retaining old
    # source backups mentioned only by historical packaging baseline reports.
    scanned: set[Path] = set()
    while True:
        candidates = [p for p in REASONS if p not in scanned and p.suffix in ('.json', '.jsonl')
                      and ALL_LOGS[p] <= 8 * 1024 * 1024]
        if not candidates:
            break
        for path in candidates:
            scanned.add(path)
            try:
                text = path.read_text('utf-8-sig')
                values = [json.loads(line) for line in text.splitlines() if line.strip()] if path.suffix == '.jsonl' else [json.loads(text)]
                for value in values:
                    visit(value, path, 'retained record dependency')
            except (UnicodeError, ValueError):
                continue

    # Source backups, retired expanded clients and the previously created old
    # release archive are explicitly superseded by the final release.
    directory_targets = [ROOT / name for name in ('dist_daily', 'dist_arena_generic_v10', 'dist_bond_v1')]
    for directory in LOGS.iterdir():
        if directory.is_dir() and ('source_before_' in directory.name or directory.name.startswith('rebuild_')):
            directory_targets.append(directory)
    if ARCHIVE.exists():
        directory_targets.append(ARCHIVE)
    targets = [p.resolve() for p in directory_targets if p.exists()]
    # Do not remove a backup that is unexpectedly a required regression fixture.
    for row in trace['files']:
        accessed = Path(row['path']).resolve()
        if any(accessed.is_relative_to(target) for target in targets):
            raise ValueError(f'Required fixture in a retired directory: {accessed}')
    for path in list(REASONS):
        if any(path.is_relative_to(target) for target in targets):
            del REASONS[path]

    obsolete_files = [p for p in ALL_LOGS if p not in REASONS
                      and not any(p.is_relative_to(target) for target in targets)]
    obsolete_files += [p for p in ROOT.glob('*.pyc*') if p.is_file()]
    obsolete_files += [ROOT / name for name in ('GENERIC_ACCEPTANCE_20261008.md', 'PIPELINE_ACCEPTANCE_20261007.md')
                       if (ROOT / name).exists()]
    rows = [dict(path=str(p), bytes=p.stat().st_size,
                 sha256=hashlib.sha256(p.read_bytes()).hexdigest()) for p in sorted(obsolete_files)]
    directories = []
    for path in targets:
        files = [p for p in path.rglob('*') if p.is_file()]
        directories.append(dict(path=str(path), files=len(files), bytes=sum(p.stat().st_size for p in files),
            fingerprint=hashlib.sha256('\n'.join(sorted(f'{p.relative_to(path)}|{p.stat().st_size}|{p.stat().st_mtime_ns}' for p in files)).encode()).hexdigest()))
    kept = [dict(path=str(path), bytes=ALL_LOGS[path], reasons=sorted(reasons),
                 sha256=hashlib.sha256(path.read_bytes()).hexdigest()) for path, reasons in sorted(REASONS.items())]
    report = dict(created_at=datetime.now().astimezone().isoformat(), root=str(ROOT),
        archive_root=str(ARCHIVE), dependency_trace=str(TRACE), tests=trace['tests'],
        preserve=kept, delete_files=rows, delete_directories=directories,
        retained_log_bytes=sum(row['bytes'] for row in kept),
        delete_bytes=sum(row['bytes'] for row in rows) + sum(row['bytes'] for row in directories),
        game_input_operations=0)
    OUTPUT.write_text(json.dumps(report, ensure_ascii=False, indent=2) + '\n', 'utf-8')
    print(json.dumps(dict(plan=str(OUTPUT), kept_log_files=len(kept),
        kept_log_gib=round(report['retained_log_bytes'] / 1024**3, 3),
        delete_files=len(rows), delete_directories=len(directories),
        delete_gib=round(report['delete_bytes'] / 1024**3, 3)), ensure_ascii=False))


if __name__ == '__main__':
    main()
