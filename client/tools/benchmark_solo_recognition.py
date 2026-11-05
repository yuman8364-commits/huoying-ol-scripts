"""Benchmark immutable real solo captures without a game worker or state writes."""
import argparse
import hashlib
import json
from pathlib import Path
import statistics
import sys
import time
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from PIL import Image
import hyol_scheduler as hs


def run():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--out', type=Path, required=True)
    parser.add_argument('--baseline', type=Path)
    parser.add_argument('--paired', action='store_true', help='interleave the original uncached ROI calculation in the same process')
    args = parser.parse_args()
    paths = [Path(row['evidence']) for row in json.loads((hs.LOGS/'solo_page_audit_latest.json').read_text(encoding='utf-8'))['accounts'] if 'evidence' in row]
    paths += [hs.LOGS/'preflight_20261005_040859_865526_b9a474a2.png',
              hs.LOGS/'preflight_20261003_232527_624315_2febdf4a.png',
              hs.LOGS/'preflight_20261004_063821_062492_fbdc0dc8.png']
    report = {'scope': 'immutable real capture replay; no live task pass',
              'source_sha256': hashlib.sha256((hs.ROOT/'hyol_scheduler.py').read_bytes()).hexdigest(), 'frames': []}
    for path in paths:
        frame = Image.open(path).convert('RGB')
        s = object.__new__(hs.Scheduler)
        s.templates, s.detector = hs.TemplateStore(.90), None
        samples, outcomes, reference_samples, reference_outcomes = [], [], [], []
        original_ncc = hs.ncc
        def observe():
            outcome = {'main_ready': s.main_ready(frame), 'offline': s.offline_dialog_visible(frame)}
            for name, roi, cutoff in (('arena_toolbar_core.bmp', (450,69,1670,169), .92),
                ('survival_toolbar_current_core.bmp', (450,69,1670,169), .92),
                ('leaf_village_name_current.bmp', (1690,110,1920,148), .92)):
                outcome[name] = s.template(name, roi, frame, threshold=cutoff)
            return outcome
        for _ in range(3):
            s.templates.begin_frame(frame)
            started = time.perf_counter()
            outcome = observe()
            samples.append((time.perf_counter()-started)*1000)
            outcomes.append(outcome)
            if args.paired:
                s.templates.begin_frame(frame)
                with patch.object(hs, 'ncc', side_effect=lambda image, target, **_kwargs: original_ncc(image, target)):
                    started = time.perf_counter()
                    reference_outcomes.append(observe())
                    reference_samples.append((time.perf_counter()-started)*1000)
        report['frames'].append({'path': str(path), 'sha256': hashlib.sha256(path.read_bytes()).hexdigest(),
            'samples_ms': samples, 'median_ms': statistics.median(samples), 'outcomes': outcomes})
        if args.paired:
            report['frames'][-1].update(reference_samples_ms=reference_samples,
                reference_median_ms=statistics.median(reference_samples), reference_outcomes=reference_outcomes,
                paired_reduction_percent=(1-statistics.median(samples)/statistics.median(reference_samples))*100,
                paired_decisions_scores_coordinates_identical=reference_outcomes == outcomes)
    if args.baseline:
        before = json.loads(args.baseline.read_text(encoding='utf-8'))
        consistent = len(before['frames']) == len(report['frames'])
        comparisons = []
        for old, new in zip(before['frames'], report['frames']):
            same = old['sha256'] == new['sha256']
            for lhs, rhs in zip(old['outcomes'], new['outcomes']):
                for key in lhs:
                    if isinstance(lhs[key], list):
                        same = same and lhs[key][0] == rhs[key][0] and lhs[key][2:] == list(rhs[key][2:]) and abs(lhs[key][1]-rhs[key][1]) < 1e-10
                    else:
                        same = same and lhs[key] == rhs[key]
            consistent = consistent and same
            comparisons.append({'path': new['path'], 'before_ms': old['median_ms'],
                'after_ms': new['median_ms'], 'reduction_percent': (1-new['median_ms']/old['median_ms'])*100,
                'same_sha_decisions_coordinates_scores': same})
        report.update(consistent=consistent, comparisons=comparisons)
    hs.atomic_json(args.out, report)
    print(json.dumps({'report': str(args.out), 'frames': len(paths), 'consistent': report.get('consistent'),
        'median_ms': [round(row['median_ms'], 2) for row in report['frames']]}, ensure_ascii=False))
    if args.baseline and not report['consistent']:
        raise SystemExit(2)


if __name__ == '__main__':
    run()
