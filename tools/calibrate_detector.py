"""Freeze validation-only thresholds before independently evaluating test sessions."""
import argparse
import hashlib
import json
from pathlib import Path
import sys
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from PIL import Image
from hyol_recognition import OnnxDetector
from tools.replay_detector import score_frame


def calibrate(dataset, manifest, output):
    dataset, manifest, output = map(Path, (dataset, manifest, output))
    if output.exists():
        raise ValueError('preserve prior thresholds; use a new output path')
    data = json.loads(dataset.read_text(encoding='utf-8'))
    detector = OnnxDetector(manifest)
    if detector.session is None:
        raise ValueError(detector.error)
    classes = data['classes']
    if classes != detector.manifest['classes']:
        raise ValueError('class order differs')
    sessions = {}
    for row in data['samples']:
        if row.get('reviewed') is not True:
            raise ValueError('unreviewed image')
        if row['session'] in sessions and sessions[row['session']] != row['split']:
            raise ValueError('session leakage')
        sessions[row['session']] = row['split']
    samples = [row for row in data['samples'] if row['split'] == 'val']
    if not samples:
        raise ValueError('validation images required')
    # Lower the in-memory proposal threshold only; original manifests stay unchanged.
    detector.manifest['thresholds'] = {c:.05 for c in classes}
    rows = []
    for row in samples:
        with Image.open(dataset.parent/row['image']) as source:
            predictions = detector.predict(source.convert('RGB'))
        rows.append((row['boxes'], predictions))
    grid = (.50,.55,.60,.65,.70,.75,.80,.85,.90,.95,.99,1.0)
    chosen, audit, disabled = {}, {}, []
    for target in classes:
        trials = []
        for threshold in grid:
            total = {'tp':0, 'fp':0, 'fn':0}
            for truth, predictions in rows:
                score = score_frame(truth, [p for p in predictions if p.target == target and p.score >= threshold], [target])[target]
                for key in total:
                    total[key] += score[key]
            trials.append(dict(threshold=threshold, **total))
        valid = [trial for trial in trials if trial['fp'] == 0 and trial['tp'] > 0]
        best = max(valid, key=lambda trial:(trial['tp'], -trial['threshold'])) if valid else trials[-1]
        chosen[target] = best['threshold']
        audit[target] = {'chosen':best, 'trials':trials}
        if not valid:
            disabled.append(target)
    result = json.loads(manifest.read_text(encoding='utf-8'))
    result.update(mode='shadow', thresholds=chosen, click_validated_targets=[],
                  status='validation thresholds fixed; test and live results do not authorize clicks',
                  disabled_calibration_targets=disabled,
                  calibration={'split':'val', 'images':len(samples),
                               'dataset_sha256':hashlib.sha256(dataset.read_bytes()).hexdigest(),
                               'source_manifest_sha256':hashlib.sha256(manifest.read_bytes()).hexdigest()})
    output.write_text(json.dumps(result,ensure_ascii=False,indent=2),encoding='utf-8')
    output.with_name(output.stem+'_calibration.json').write_text(json.dumps(audit,ensure_ascii=False,indent=2),encoding='utf-8')
    print(json.dumps({'validation_images':len(samples),'classes':len(classes),'disabled_targets':disabled,
                      'thresholds_frozen':str(output),'click_authorizations':0},ensure_ascii=False))


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('dataset'); parser.add_argument('manifest'); parser.add_argument('output')
    calibrate(**vars(parser.parse_args()))
