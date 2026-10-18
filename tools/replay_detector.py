"""Evaluate the shipped CPU detector against reviewed held-out images, without input."""
import argparse
import hashlib
import json
from pathlib import Path
import sys
import time
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from PIL import Image
from hyol_recognition import OnnxDetector, iou


def score_frame(truth, predictions, classes, minimum_iou=.5):
    result = {}
    for target in classes:
        expected = [b['xyxy'] for b in truth if b['target'] == target]
        detected = [d for d in predictions if d.target == target]
        pairs = sorted(((iou(box, det.box), i, j)
                        for i, box in enumerate(expected)
                        for j, det in enumerate(detected)), reverse=True)
        used_truth, used_predictions, overlaps = set(), set(), []
        for overlap, i, j in pairs:
            if overlap >= minimum_iou and i not in used_truth and j not in used_predictions:
                used_truth.add(i); used_predictions.add(j); overlaps.append(overlap)
        result[target] = {'tp': len(overlaps), 'fp': len(detected)-len(overlaps),
                          'fn': len(expected)-len(overlaps), 'matched_iou': overlaps}
    return result


def replay(dataset, detector_manifest, output, split='test'):
    dataset, detector_manifest, output = map(Path, (dataset, detector_manifest, output))
    if output.exists():
        raise ValueError('use a new report path; preserve previous evidence')
    data = json.loads(dataset.read_text(encoding='utf-8'))
    classes = data['classes']
    if not classes or len(set(classes)) != len(classes):
        raise ValueError('classes must be unique and nonempty')
    sessions, hashes = {}, set()
    for row in data['samples']:
        if row.get('reviewed') is not True:
            raise ValueError('unreviewed data cannot be evaluation truth')
        if row['session'] in sessions and sessions[row['session']] != row['split']:
            raise ValueError('session leakage')
        sessions[row['session']] = row['split']
        digest = hashlib.sha256((dataset.parent/row['image']).read_bytes()).hexdigest()
        if digest in hashes:
            raise ValueError('duplicate image; evaluation split may be contaminated')
        hashes.add(digest)
    samples = [r for r in data['samples'] if r['split'] == split]
    if not samples:
        raise ValueError('requested split is empty')
    detector = OnnxDetector(detector_manifest)
    if detector.session is None:
        raise ValueError('model unavailable: ' + detector.error)
    if detector.manifest['classes'] != classes:
        raise ValueError('model and dataset class order differ')
    totals = {c: {'tp': 0, 'fp': 0, 'fn': 0, 'matched_iou': []} for c in classes}
    rows, latency = [], []
    for sample in samples:
        path = (dataset.parent/sample['image']).resolve()
        with Image.open(path) as source:
            frame = source.convert('RGB')
        for box in sample['boxes']:
            x1,y1,x2,y2 = box['xyxy']
            if box['target'] not in classes or not (0 <= x1 < x2 <= frame.width and 0 <= y1 < y2 <= frame.height):
                raise ValueError('invalid truth box: ' + sample['image'])
        started = time.perf_counter()
        predictions = detector.predict(frame)
        elapsed = (time.perf_counter()-started)*1000
        latency.append(elapsed)
        scores = score_frame(sample['boxes'], predictions, classes)
        for target, metrics in scores.items():
            for key in ('tp','fp','fn'):
                totals[target][key] += metrics[key]
            totals[target]['matched_iou'].extend(metrics['matched_iou'])
        rows.append({'image': str(path), 'sha256': hashlib.sha256(path.read_bytes()).hexdigest(),
                     'session': sample['session'], 'latency_ms': elapsed, 'scores': scores,
                     'predictions': [d.as_dict() for d in predictions]})
    for metrics in totals.values():
        tp,fp,fn = (metrics[k] for k in ('tp','fp','fn'))
        metrics['precision'] = tp/(tp+fp) if tp+fp else None
        metrics['recall'] = tp/(tp+fn) if tp+fn else None
        overlaps = metrics.pop('matched_iou')
        metrics['mean_matched_iou'] = sum(overlaps)/len(overlaps) if overlaps else None
    report = {'split': split, 'images': len(rows), 'classes': totals, 'rows': rows,
              'model_sha256': detector.manifest['sha256'],
              'dataset_sha256': hashlib.sha256(dataset.read_bytes()).hexdigest(),
              'detector_manifest_sha256': hashlib.sha256(detector_manifest.read_bytes()).hexdigest(),
              'thresholds': {c: detector.manifest.get('thresholds',{}).get(c,.85) for c in classes},
              'latency_ms': {'mean': sum(latency)/len(latency), 'max': max(latency)},
              'clicks': 0, 'activation': 'unchanged; this report never authorizes clicks'}
    output.parent.mkdir(parents=True, exist_ok=True)
    with output.open('x', encoding='utf-8') as handle:
        json.dump(report, handle, ensure_ascii=False, indent=2)
    return report


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('dataset'); parser.add_argument('detector_manifest'); parser.add_argument('output')
    parser.add_argument('--split', choices=['val','test'], default='test')
    args = parser.parse_args()
    report = replay(**vars(args))
    print(json.dumps({k:v for k,v in report.items() if k != 'rows'}, ensure_ascii=False, indent=2))
