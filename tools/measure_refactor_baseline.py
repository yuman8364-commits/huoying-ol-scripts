"""Read-only source/package inventory and bounded recognition replay timing.

This tool does not bind a game window, instantiate Scheduler, or write state.
Only the requested JSON report is created. Timing is a local offline baseline,
not proof that a task has passed live acceptance.
"""
from __future__ import annotations

import argparse
import ast
from collections import defaultdict
from datetime import datetime
import hashlib
import json
from pathlib import Path
import statistics
import sys
import time

ROOT = Path(__file__).resolve().parents[1]


def source_inventory():
    files, duplicates, large = [], defaultdict(list), []
    for path in sorted(ROOT.glob('hyol_*.py*')):
        source = path.read_text(encoding='utf-8-sig')
        tree = ast.parse(source, filename=str(path))
        functions = [n for n in ast.walk(tree)
                     if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))]
        files.append({'path': path.name, 'bytes': path.stat().st_size,
                      'lines': len(source.splitlines()), 'functions': len(functions),
                      'classes': sum(isinstance(n, ast.ClassDef) for n in ast.walk(tree)),
                      'sha256': hashlib.sha256(path.read_bytes()).hexdigest()})
        for node in functions:
            lines = node.end_lineno-node.lineno+1
            if lines < 12:
                continue
            body = node.body
            if (body and isinstance(body[0], ast.Expr)
                    and isinstance(body[0].value, ast.Constant)
                    and isinstance(body[0].value.value, str)):
                body = body[1:]
            signature = ast.dump(ast.Module(body=body, type_ignores=[]),
                                 include_attributes=False)
            entry = {'path': path.name, 'function': node.name,
                     'line': node.lineno, 'lines': lines}
            duplicates[hashlib.sha256(signature.encode()).hexdigest()].append(entry)
            large.append(entry)
    return {'files': files, 'bytes': sum(f['bytes'] for f in files),
            'lines': sum(f['lines'] for f in files),
            'largest_functions': sorted(large, key=lambda f: -f['lines'])[:20],
            'exact_duplicate_bodies_at_least_12_lines':
                [group for group in duplicates.values() if len(group) > 1]}


def runtime_files(directory):
    for path in directory.rglob('*'):
        if not path.is_file():
            continue
        relative = path.relative_to(directory)
        if any(part in ('state', 'logs') for part in relative.parts):
            continue
        if path.name.endswith(('_state.json', '_result.json', '_failure.json')):
            continue
        yield path


def package_inventory():
    evidence_path = ROOT/'logs/release_verified_20261003.json'
    if not evidence_path.exists():
        return {'error': 'release inventory missing'}
    release = json.loads(evidence_path.read_text(encoding='utf-8-sig'))
    packages, copies = [], defaultdict(list)
    for package in release['Packages']:
        entry = {'client': package['Client']}
        for key in ('Exe', 'Dist'):
            exe = Path(package[key])
            paths = list(runtime_files(exe.parent))
            sizes = [(path, path.stat().st_size) for path in paths]
            entry[key.lower()] = {'path': str(exe), 'exe_bytes': exe.stat().st_size,
                                  'runtime_files': len(paths),
                                  'runtime_bytes': sum(size for _, size in sizes),
                                  'largest_files': [
                                      {'path': str(path.relative_to(exe.parent)), 'bytes': size}
                                      for path, size in sorted(sizes, key=lambda row: -row[1])[:10]]}
            if key == 'Exe':
                for path, size in sizes:
                    digest = hashlib.sha256(path.read_bytes()).hexdigest()
                    copies[(size, digest)].append({'client': package['Client'],
                                                  'path': str(path.relative_to(exe.parent))})
        packages.append(entry)
    duplicated = []
    potential = 0
    for (size, digest), locations in copies.items():
        clients = {entry['client'] for entry in locations}
        if len(clients) < 2:
            continue
        # Count one kept copy per unique byte-identical file. No deletion occurs.
        potential += size*(len(locations)-1)
        duplicated.append({'bytes_each': size, 'sha256': digest,
                           'locations': locations})
    return {'scope': 'current four verified desktop/dist packages; runtime only, no state/logs',
            'packages': packages,
            'desktop_runtime_bytes': sum(p['exe']['runtime_bytes'] for p in packages),
            'identical_desktop_runtime_redundant_bytes': potential,
            'largest_identical_files': sorted(duplicated, key=lambda row: -row['bytes_each'])[:15]}


def timed(call, repetitions):
    values, outcomes = [], []
    for _ in range(repetitions):
        started = time.perf_counter()
        result = call()
        values.append((time.perf_counter()-started)*1000)
        outcomes.append(result)
    return {'samples_ms': values, 'median_ms': statistics.median(values),
            'min_ms': min(values), 'max_ms': max(values), 'outcomes': outcomes}


def recognition_inventory(frame_path, repetitions, budget):
    from PIL import Image
    sys.path.insert(0, str(ROOT))
    import hyol_scheduler as hs

    frame = Image.open(frame_path).convert('RGB')
    if frame.size != (1920, 1032):
        raise ValueError('Replay must be an existing canonical 1920x1032 capture')
    # Methods used below only inspect an explicitly supplied image. Construction
    # is intentionally bypassed: no state/log setup, HWND, F12, or game worker.
    scheduler = object.__new__(hs.Scheduler)
    scheduler.templates = hs.TemplateStore(.92)
    scheduler.detector = None
    deadline = time.monotonic()+budget
    report = {'frame': str(frame_path), 'frame_sha256': hashlib.sha256(frame_path.read_bytes()).hexdigest(),
              'dimensions': list(frame.size), 'repetitions': repetitions,
              'no_capture_no_click_no_state_write': True, 'checks': {}}
    checks = [
        ('top_anchor', lambda: scheduler.template('anchor_top_menu.bmp', (500,69,1500,189), frame)),
        ('right_anchor', lambda: scheduler.template('anchor_right_panel.bmp', (1569,289,1919,489), frame)),
        ('offline_monitor_all_variants', lambda: scheduler.offline_dialog_visible(frame)),
        ('leaf_return_with_variants', lambda: scheduler.template('leaf_return_current.bmp', (1870,69,1920,125), frame)),
        ('main_ready_all_overlays', lambda: scheduler.main_ready(frame)),
    ]
    for name, call in checks:
        if time.monotonic() >= deadline:
            report['skipped_after_budget'] = name
            break
        report['checks'][name] = timed(call, repetitions)
    # Test the existing same-frame result cache as an offline observation only.
    if time.monotonic() < deadline:
        from hyol_recognition import OnnxDetector
        detector = OnnxDetector(ROOT/'models/detector.json')
        report['detector_mode'] = detector.manifest.get('mode', 'off')
        report['detector_error'] = detector.error
        if detector.session is not None:
            report['checks']['yolo_same_frame_cache'] = timed(
                lambda: len(detector.predict(frame)), repetitions)
            if time.monotonic() < deadline:
                report['checks']['yolo_new_frame_object'] = timed(
                    lambda: len(detector.predict(frame.copy())), repetitions)
    return report


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--frame', type=Path, default=ROOT/'logs/scheduler_evidence/2026-10-03/account_b76494cf2c/215843_128773_verified_formation_page_closed.png')
    parser.add_argument('--out', type=Path, default=ROOT/'logs/refactor_baseline_20261003.json')
    parser.add_argument('--repetitions', type=int, default=3)
    parser.add_argument('--recognition-budget-seconds', type=float, default=20)
    args = parser.parse_args()
    if not 1 <= args.repetitions <= 5 or not 1 <= args.recognition_budget_seconds <= 60:
        parser.error('bounded measurement requires repetitions 1..5 and budget 1..60')
    started = time.perf_counter()
    report = {'generated_at': datetime.now().isoformat(timespec='seconds'),
              'python': sys.executable, 'scope': 'offline baseline, not live acceptance',
              'source': source_inventory(), 'packages': package_inventory(),
              'recognition': recognition_inventory(args.frame.resolve(), args.repetitions,
                                                    args.recognition_budget_seconds)}
    report['elapsed_seconds'] = time.perf_counter()-started
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(report, ensure_ascii=False, indent=2)+'\n', encoding='utf-8')
    print(json.dumps({'output': str(args.out), 'source_bytes': report['source']['bytes'],
                      'source_lines': report['source']['lines'],
                      'desktop_runtime_bytes': report['packages'].get('desktop_runtime_bytes'),
                      'redundant_identical_runtime_bytes': report['packages'].get('identical_desktop_runtime_redundant_bytes'),
                      'elapsed_seconds': report['elapsed_seconds'],
                      'recognition_median_ms': {name: row['median_ms']
                                               for name, row in report['recognition']['checks'].items()}},
                     ensure_ascii=False, indent=2))


if __name__ == '__main__':
    main()
