"""Enroll explicitly reviewed, real counter crops; never sends game input."""
import hashlib
import json
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from PIL import Image
import hyol_scheduler as hs
import hyol_ranking as ranking


def enroll(samples, report):
    if report.exists():
        raise ValueError('Preserve prior enrollment evidence')
    rows = []
    for count, source in samples:
        if count not in (6, 7, 8):
            raise ValueError('Only the reviewed missing counters 6/7/8 are eligible')
        with Image.open(source) as image:
            frame = image.convert('RGB')
        store = hs.TemplateStore(.985)
        if not all(store.match(frame, hs.ASSETS/name, roi, .95)[0] for name, roi in (
                ('ranking_page.bmp', ranking.PAGE), ('ranking_counter_label.bmp', ranking.COUNTER))):
            raise ValueError('The source is not an anchored ranking counter')
        target = hs.ASSETS/f'ranking_count_{count}_current.bmp'
        crop = frame.crop(ranking.CURRENT_COUNTER)
        if target.exists():
            with Image.open(target) as existing:
                if existing.convert('RGB').tobytes() != crop.tobytes():
                    raise ValueError('Do not replace an existing counter silently')
        else:
            crop.save(target)
        rows.append(dict(count=count, source=str(source), source_sha256=hashlib.sha256(source.read_bytes()).hexdigest(),
                         asset=str(target), asset_sha256=hashlib.sha256(target.read_bytes()).hexdigest(),
                         crop=list(ranking.CURRENT_COUNTER), threshold=.985))
    hs.atomic_json(report, dict(rows=rows, game_inputs=0, labels='explicitly reviewed real game text'))
    return rows


if __name__ == '__main__':
    import argparse
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--sample', action='append', required=True, help='reviewed count=absolute source path')
    parser.add_argument('--report', type=Path, required=True)
    args = parser.parse_args()
    samples = [(int(value.split('=', 1)[0]), Path(value.split('=', 1)[1])) for value in args.sample]
    print(json.dumps(enroll(samples, args.report), ensure_ascii=False))
