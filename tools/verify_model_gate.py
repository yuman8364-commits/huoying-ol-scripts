"""Read-only audit of model evidence and conservative click authorization.

No window discovery, keyboard/mouse input, training, activation, or file writes.
--replay additionally reruns CPU inference on the existing held-out photographs.
An offline PASS never grants model clicks or records a live PASS.
"""
from __future__ import annotations

import argparse
from collections import Counter, defaultdict
import copy
import csv
import hashlib
import json
import math
from pathlib import Path
import sys
import time

from PIL import Image

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from hyol_recognition import Detection, OnnxDetector, fuse
from tools.replay_detector import score_frame


def digest(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def read_json(path):
    return json.loads(Path(path).read_text(encoding='utf-8'))


def require(condition, message):
    if not condition:
        raise ValueError(message)


def audit_dataset(path):
    """Verify original photographs, prepared images/labels, and session isolation."""
    path = Path(path)
    data = read_json(path)
    classes = data['classes']
    require(classes and len(classes) == len(set(classes)), 'duplicate or empty classes')
    session_splits, image_splits, samples_by_hash = {}, {}, {}
    sessions = defaultdict(set)
    counts = Counter()
    for row in data['samples']:
        require(row.get('reviewed') is True, 'unreviewed sample')
        split, session = row['split'], row['session']
        require(split in ('train', 'val', 'test') and bool(session), 'invalid split/session')
        require(session not in session_splits or session_splits[session] == split, 'session leakage')
        session_splits[session] = split
        sessions[split].add(session)
        counts[split] += 1
        image = (path.parent / row['image']).resolve()
        sha = digest(image)
        require(sha not in image_splits, 'duplicate source image')
        image_splits[sha] = split
        samples_by_hash[sha] = row
        prepared = path.parent / 'images' / split / (sha + image.suffix.lower())
        label = path.parent / 'labels' / split / (sha + '.txt')
        require(digest(prepared) == sha, 'prepared image bytes differ from reviewed source')
        with Image.open(image) as source, Image.open(prepared) as exported:
            require(source.size == exported.size and source.convert('RGB').tobytes() == exported.convert('RGB').tobytes(),
                    'prepared image differs from reviewed source: ' + sha)
            width, height = source.size
        expected = []
        for box in row['boxes']:
            x1, y1, x2, y2 = box['xyxy']
            require(box['target'] in classes and 0 <= x1 < x2 <= width and 0 <= y1 < y2 <= height,
                    'invalid reviewed box')
            expected.append((classes.index(box['target']), (x1+x2)/(2*width),
                             (y1+y2)/(2*height), (x2-x1)/width, (y2-y1)/height))
        actual = [tuple(map(float, line.split())) for line in label.read_text(encoding='utf-8').splitlines() if line.strip()]
        require(len(actual) == len(expected), 'prepared label count differs')
        for observed, truth in zip(actual, expected):
            require(len(observed) == 5 and all(abs(a-b) <= 1e-6 for a, b in zip(observed, truth)),
                    'prepared label differs from reviewed box')
    audit = read_json(path.parent / 'audit.json')
    require(audit['source_manifest_sha256'] == digest(path), 'dataset audit manifest drift')
    require(audit['sessions'] == session_splits and audit['images'] == image_splits,
            'dataset audit image/session drift')
    require(audit['sample_count'] == len(samples_by_hash), 'dataset audit count differs')
    for split in ('train', 'val', 'test'):
        expected_hashes = {sha for sha, current in image_splits.items() if current == split}
        image_files = [item for item in (path.parent / 'images' / split).iterdir() if item.is_file()]
        label_files = list((path.parent / 'labels' / split).glob('*.txt'))
        require({item.stem for item in image_files} == expected_hashes and len(image_files) == len(expected_hashes),
                'prepared image directory has missing/extra samples')
        require({item.stem for item in label_files} == expected_hashes, 'prepared label directory has missing/extra samples')
    return data, samples_by_hash, {'images': dict(counts),
                                  'sessions': {k: sorted(v) for k, v in sessions.items()},
                                  'unique_images': len(image_splits),
                                  'grouping': 'acquisition day; exact capture-session IDs not supplied'}


def detector_stub(manifest, *, healthy=True):
    """Exercise the pure authorization decision without opening an ONNX session."""
    detector = object.__new__(OnnxDetector)
    detector.manifest = copy.deepcopy(manifest)
    detector.session = object() if healthy else None
    detector.error = ''
    return detector


def authorization_cases(classes):
    """Synthetic live proofs exist only in memory to test the authorization contract."""
    cases = []
    for target in classes:
        sha = 'a' * 64
        base = {'mode': 'validated', 'sha256': sha, 'classes': [target],
                'thresholds': {target: .9}, 'click_validated_targets': [target],
                'disabled_calibration_targets': [],
                'click_validation_evidence': {target: {'status': 'live_pass', 'model_sha256': sha,
                                                     'evidence': 'OFFLINE_FIXTURE_ONLY'}}}
        variants = [('valid_contract_fixture', {}, True),
                    ('shadow', {'mode': 'shadow'}, False),
                    ('unlisted', {'click_validated_targets': []}, False),
                    ('missing_class', {'classes': []}, False),
                    ('duplicate_class', {'classes': [target, target]}, False),
                    ('disabled', {'disabled_calibration_targets': [target]}, False),
                    ('missing_threshold', {'thresholds': {}}, False),
                    ('nonfinite_threshold', {'thresholds': {target: float('nan')}}, False),
                    ('zero_threshold', {'thresholds': {target: 0}}, False),
                    ('boolean_threshold', {'thresholds': {target: True}}, False),
                    ('out_of_range_threshold', {'thresholds': {target: 1.01}}, False),
                    ('missing_live_proof', {'click_validation_evidence': {}}, False),
                    ('offline_proof', {'click_validation_evidence': {target: dict(status='offline_pass', model_sha256=sha, evidence='fixture')}}, False),
                    ('wrong_model_proof', {'click_validation_evidence': {target: dict(status='live_pass', model_sha256='b'*64, evidence='fixture')}}, False),
                    ('empty_evidence', {'click_validation_evidence': {target: dict(status='live_pass', model_sha256=sha, evidence='  ')}}, False)]
        for name, changes, expected in variants:
            manifest = copy.deepcopy(base)
            manifest.update(changes)
            actual = detector_stub(manifest).enabled_for(target)
            cases.append(dict(target=target, case=name, expected=expected, actual=bool(actual)))
        unhealthy = detector_stub(base, healthy=False)
        cases.append(dict(target=target, case='missing_session', expected=False, actual=bool(unhealthy.enabled_for(target))))
        unhealthy = detector_stub(base)
        unhealthy.error = 'inference failure'
        cases.append(dict(target=target, case='session_error', expected=False, actual=bool(unhealthy.enabled_for(target))))
    return cases


def fusion_cases(classes):
    cases = []
    for target in classes:
        candidate = Detection(target, (10., 10., 30., 30.), .99, 'yolo', True)
        scenarios = [(reason, Detection(target, None, 0, 'template', False, reason), [candidate], True)
                     for reason in ('missing_template', 'disabled_control', 'disabled_calibration_target',
                                    'template_model_conflict', 'ambiguous_model_candidates', 'invalid_box')]
        scenarios += [('classification', Detection(target, None, 0, 'classification', False), [candidate], True),
                      ('shadow_model_only', Detection(target, None, 0, 'template', False), [candidate], False),
                      ('conflict', Detection(target, (100., 100., 120., 120.), .99, 'template', True), [candidate], True),
                      ('ambiguous', Detection(target, None, 0, 'template', False), [candidate, candidate], True),
                      ('invalid_model_box', Detection(target, None, 0, 'template', False),
                       [Detection(target, (10., 10., float('nan'), 30.), .99, 'yolo', True)], True)]
        for name, template, predictions, enabled in scenarios:
            decision = fuse(template, predictions, model_enabled=enabled)
            cases.append(dict(target=target, case=name, expected=False, actual=bool(decision.accepted), reason=decision.reason))
    return cases


def audit_validation(data, dataset_path, manifest_path, calibration, *, replay=False):
    """Recheck every frozen trial using validation images only, without recalibrating."""
    classes = data['classes']
    grid = (.50, .55, .60, .65, .70, .75, .80, .85, .90, .95, .99, 1.0)
    for target in classes:
        trials = calibration[target]['trials']
        require([row['threshold'] for row in trials] == list(grid), 'validation threshold grid drift: ' + target)
        for row in trials:
            require(all(type(row[key]) is int and row[key] >= 0 for key in ('tp', 'fp', 'fn')),
                    'invalid validation metric: ' + target)
            require(math.isfinite(row['threshold']), 'invalid validation threshold: ' + target)
    samples = [row for row in data['samples'] if row['split'] == 'val']
    result = {'replayed': replay, 'images': len(samples), 'trial_count': len(classes)*len(grid),
              'thresholds_changed': False, 'cpu_latency_ms': None}
    if not replay:
        return result
    detector = OnnxDetector(manifest_path)
    require(detector.session is not None and not detector.error,
            'validation CPU session unavailable: ' + detector.error)
    # Match the original proposal pass in calibrate_detector. These changes stay
    # in memory; the frozen manifest, ONNX bytes and click allowlist never change.
    detector.manifest['thresholds'] = {target: .05 for target in classes}
    rows, latency = [], []
    for sample in samples:
        with Image.open(dataset_path.parent/sample['image']) as source:
            frame = source.convert('RGB')
        started = time.perf_counter()
        predictions = detector.predict(frame)
        latency.append((time.perf_counter()-started)*1000)
        rows.append((sample['boxes'], predictions))
    for target in classes:
        for frozen in calibration[target]['trials']:
            totals = dict(tp=0, fp=0, fn=0)
            for truth, predictions in rows:
                score = score_frame(truth, [item for item in predictions
                                           if item.target == target and item.score >= frozen['threshold']], [target])[target]
                for key in totals:
                    totals[key] += score[key]
            require(totals == {key: frozen[key] for key in totals},
                    'validation replay count drift: ' + target + ' @ ' + str(frozen['threshold']))
    result['cpu_latency_ms'] = {'mean': sum(latency)/len(latency), 'max': max(latency)}
    return result


def audit_training(root, dataset_path, classes):
    """Verify that recorded training used the audited train/validation folders."""
    config_path = dataset_path.parent/'data.yaml'
    config = read_json(config_path)  # The generated YAML is deliberately JSON-compatible.
    require(Path(config['path']).resolve() == dataset_path.parent.resolve(), 'training dataset root drift')
    require([config['names'][str(index)] for index in range(len(classes))] == classes,
            'training class order drift')
    require(len(config['names']) == len(classes), 'training extra classes')
    for split in ('train', 'val', 'test'):
        require(config[split] == 'images/' + split, 'training split path drift: ' + split)
    args_path = root/'runs/multiclass-20261003/game_ui/args.yaml'
    # Only scalar keys are needed; retain their source hash in the audit evidence.
    args = dict(line.partition(':')[::2] for line in args_path.read_text(encoding='utf-8').splitlines()
                if line and not line[0].isspace() and ':' in line)
    args = {key.strip(): value.strip().strip("\"'") for key, value in args.items()}
    require(Path(args['data']).resolve() == config_path.resolve(), 'training used a different dataset')
    require(args['task'] == 'detect' and args['mode'] == 'train'
            and args['val'] == 'true' and args['split'] == 'val', 'training validation split drift')
    with (root/'runs/multiclass-20261003/game_ui/results.csv').open(encoding='utf-8') as source:
        training = list(csv.DictReader(source))
    require(bool(training), 'training metrics absent')
    epochs = [int(row['epoch']) for row in training]
    require(epochs == list(range(1, len(epochs)+1)) and len(epochs) <= int(args['epochs']),
            'training epoch history drift')
    return {'completed_epochs': len(training), 'validation_split': 'val',
            'data_config_sha256': digest(config_path), 'training_args_sha256': digest(args_path)}


def audit_model(root=ROOT, *, replay=False):
    root = Path(root)
    dataset_path = root / 'datasets/multiclass-20261003/source_manifest.json'
    candidate_dir = root / 'models/candidates/multiclass-20261003'
    manifest_path = candidate_dir / 'detector.fixed.json'
    manifest, calibration = read_json(manifest_path), read_json(candidate_dir / 'detector.fixed_calibration.json')
    report = read_json(root / 'logs/multiclass_test_20261003.json')
    data, samples, splits = audit_dataset(dataset_path)
    classes = data['classes']
    model_sha = digest(candidate_dir / manifest['file'])
    require(manifest['classes'] == classes == list(report['classes']), 'class order drift')
    require(manifest['sha256'] == report['model_sha256'] == model_sha, 'model drift')
    require(digest(root / 'runs/multiclass-20261003/game_ui/weights/best.onnx') == model_sha,
            'export and candidate differ')
    require(report['detector_manifest_sha256'] == digest(manifest_path), 'fixed threshold manifest drift')
    require(report['dataset_sha256'] == manifest['calibration']['dataset_sha256'] == digest(dataset_path), 'dataset provenance drift')
    require(manifest['calibration']['source_manifest_sha256'] == digest(candidate_dir / 'detector.json'), 'calibration source drift')
    source_manifest = read_json(candidate_dir/'detector.json')
    require(source_manifest['classes'] == classes and source_manifest['sha256'] == model_sha
            and source_manifest['file'] == manifest['file'], 'calibration model/class drift')
    require(manifest['calibration']['split'] == 'val' and manifest['calibration']['images'] == splits['images']['val'],
            'calibration was not validation-only')
    require(report['split'] == 'test' and report['images'] == splits['images']['test'] and report['clicks'] == 0,
            'test report is not input-free held-out test')
    thresholds = manifest['thresholds']
    require(thresholds == report['thresholds'], 'thresholds changed after calibration')
    validation_replay = audit_validation(data, dataset_path, candidate_dir/'detector.json', calibration, replay=replay)
    training = audit_training(root, dataset_path, classes)
    disabled = []
    for target in classes:
        trials = calibration[target]['trials']
        valid = [trial for trial in trials if trial['fp'] == 0 and trial['tp'] > 0]
        chosen = max(valid, key=lambda trial: (trial['tp'], -trial['threshold'])) if valid else trials[-1]
        require(calibration[target]['chosen'] == chosen and thresholds[target] == chosen['threshold'],
                'threshold selection differs from validation trials: ' + target)
        if not valid:
            disabled.append(target)
    require(disabled == manifest['disabled_calibration_targets'], 'disabled class list differs')
    totals = {target: dict(tp=0, fp=0, fn=0) for target in classes}
    shadow_cases, seen = [], set()
    detector = OnnxDetector(manifest_path) if replay else detector_stub(manifest)
    if replay:
        require(detector.session is not None and not detector.error, 'candidate CPU session unavailable: ' + detector.error)
    latency = []
    for row in report['rows']:
        sha = digest(row['image'])
        require(sha == row['sha256'] and sha in samples and sha not in seen, 'test row hash/count drift')
        seen.add(sha)
        sample = samples[sha]
        require(sample['split'] == 'test' and sample['session'] == row['session'], 'test session drift')
        if replay:
            with Image.open(row['image']) as source:
                frame = source.convert('RGB')
            started = time.perf_counter()
            predictions = detector.predict(frame)
            latency.append((time.perf_counter()-started)*1000)
        else:
            predictions = [Detection(**prediction) for prediction in row['predictions']]
        scores = score_frame(sample['boxes'], predictions, classes)
        for target in classes:
            for key in ('tp', 'fp', 'fn'):
                require(scores[target][key] == row['scores'][target][key], 'test replay count drift: ' + target)
                totals[target][key] += scores[target][key]
            decision = fuse(Detection(target, None, 0, 'template', False), predictions,
                            model_enabled=detector.enabled_for(target))
            shadow_cases.append(dict(target=target, image_sha256=sha, expected=False, actual=bool(decision.accepted)))
    require(len(seen) == report['images'], 'missing held-out row')
    for target in classes:
        require(totals[target] == {k: report['classes'][target][k] for k in ('tp','fp','fn')}, 'aggregate test metrics drift')
    manifests = {'candidate': manifest, 'shipped': read_json(root / 'models/detector.json')}
    real_authorizations = {}
    for name, current in manifests.items():
        require(current['mode'] == 'shadow' and current['click_validated_targets'] == [], name + ' model activation changed')
        require(digest((manifest_path if name == 'candidate' else root / 'models/detector.json').parent / current['file']) == current['sha256'],
                name + ' model checksum mismatch')
        real_authorizations[name] = [target for target in current['classes'] if detector_stub(current).enabled_for(target)]
    cases = authorization_cases(classes) + fusion_cases(classes) + shadow_cases
    failures = [case for case in cases if case['actual'] != case['expected']]
    quality_blockers = []
    if disabled:
        quality_blockers.append('disabled_calibration_classes')
    if data['pending_classes']:
        quality_blockers.append('missing_train_val_test_classes')
    if sum(value['fn'] for value in totals.values()):
        quality_blockers.append('held_out_false_negatives')
    if sum(value['fp'] for value in totals.values()):
        quality_blockers.append('held_out_false_positives')
    # Date groups prevent same-day leakage, but cannot prove an acquisition
    # session did not cross midnight. Do not upgrade them to verified sessions.
    quality_blockers.append('exact_acquisition_session_ids_missing')
    return {'schema_version': 2, 'offline_result': 'PASS' if not failures else 'FAIL',
            'live_pass': False, 'model_quality_pass': not quality_blockers,
            'model_quality_blockers': quality_blockers, 'capture_session_identity_verified': False,
            'input_operations': 0, 'activation_changed': False, 'replayed_inference': replay,
            'hashes': {'model': model_sha, 'fixed_manifest': digest(manifest_path), 'dataset': digest(dataset_path),
                       'recognition_source': digest(root / 'hyol_recognition.py'), 'scheduler_source': digest(root / 'hyol_scheduler.py')},
            'splits': splits, 'completed_epochs': training['completed_epochs'], 'training_provenance': training,
            'validation_replay': validation_replay, 'thresholds': thresholds,
            'validation': {target: calibration[target]['chosen'] for target in classes},
            'test': totals, 'total': {key: sum(v[key] for v in totals.values()) for key in ('tp','fp','fn')},
            'disabled': disabled, 'pending_classes': data['pending_classes'],
            'real_model_authorizations': real_authorizations,
            'gate_cases': {'authorization_contract': len(authorization_cases(classes)), 'fusion': len(fusion_cases(classes)),
                           'real_held_out_model_only': len(shadow_cases), 'failures': failures},
            'historical_test_latency_ms': report['latency_ms'],
            'current_cpu_latency_ms': {'mean': sum(latency)/len(latency), 'max': max(latency)} if latency else None,
            'scope': 'offline hash/split/threshold/runtime-gate checks; no game or live click validation'}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--root', type=Path, default=ROOT)
    parser.add_argument('--replay', action='store_true', help='rerun the existing 24 test photographs with CPU ONNX')
    args = parser.parse_args()
    try:
        result = audit_model(args.root, replay=args.replay)
    except (ValueError, OSError, KeyError, TypeError) as exc:
        print(json.dumps({'offline_result': 'FAIL', 'live_pass': False, 'input_operations': 0,
                          'error': str(exc)}, ensure_ascii=False, indent=2))
        return 1
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0 if result['offline_result'] == 'PASS' else 1


if __name__ == '__main__':
    raise SystemExit(main())
