"""Reproducible reviewed-session dataset preparation and YOLO11 training/export.

No automatic clicks, uploads, labelling approvals or activation of weights.
"""
from __future__ import annotations
import argparse
import hashlib
import json
from pathlib import Path
import shutil
import sys

from PIL import Image


def read(path):
    return json.loads(Path(path).read_text(encoding='utf-8'))


def write(path, data):
    Path(path).write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding='utf-8')


def prepare(manifest_path, output):
    manifest_path, output = Path(manifest_path).resolve(), Path(output).resolve()
    data = read(manifest_path)
    classes = data['classes']
    if not classes or len(classes) != len(set(classes)):
        raise ValueError('classes must be nonempty and unique')
    sessions, hashes, rows = {}, {}, []
    for sample in data['samples']:
        if sample.get('reviewed') is not True:
            raise ValueError('unreviewed sample: ' + sample['image'])
        split, session = sample['split'], sample['session']
        if split not in ('train', 'val', 'test') or not session:
            raise ValueError('invalid split/session')
        if session in sessions and sessions[session] != split:
            raise ValueError('session leakage: ' + session)
        sessions[session] = split
        source = (manifest_path.parent / sample['image']).resolve()
        digest = hashlib.sha256(source.read_bytes()).hexdigest()
        if digest in hashes:
            raise ValueError('duplicate image; remove before splitting: ' + sample['image'])
        hashes[digest] = split
        with Image.open(source) as image:
            width, height = image.size
        labels = []
        for box in sample['boxes']:
            cls = classes.index(box['target'])
            x1, y1, x2, y2 = box['xyxy']
            if not (0 <= x1 < x2 <= width and 0 <= y1 < y2 <= height):
                raise ValueError('box outside image: ' + sample['image'])
            labels.append(f'{cls} {(x1+x2)/2/width:.8f} {(y1+y2)/2/height:.8f} {(x2-x1)/width:.8f} {(y2-y1)/height:.8f}')
        rows.append((source, digest, split, labels))
    if set(sessions.values()) != {'train', 'val', 'test'}:
        raise ValueError('reviewed independent train, val and test sessions are required')
    for split in ('train', 'val', 'test'):
        seen = {int(line.split()[0]) for _, _, s, labels in rows if s == split for line in labels}
        if seen != set(range(len(classes))):
            raise ValueError(f'{split} is missing positive examples for a class')
    if output.exists():
        raise ValueError('use a new output directory; existing datasets are immutable')
    output.mkdir(parents=True)
    for source, digest, split, labels in rows:
        images, annotations = output/'images'/split, output/'labels'/split
        images.mkdir(parents=True, exist_ok=True)
        annotations.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source, images/(digest+source.suffix.lower()))
        (annotations/(digest+'.txt')).write_text('\n'.join(labels), encoding='utf-8')
    write(output/'data.yaml', {'path': output.as_posix(), 'train': 'images/train', 'val': 'images/val', 'test': 'images/test', 'names': dict(enumerate(classes))})
    shutil.copy2(manifest_path, output/'source_manifest.json')
    write(output/'audit.json', {'source_manifest_sha256': hashlib.sha256(manifest_path.read_bytes()).hexdigest(),
          'sessions': sessions, 'images': hashes, 'sample_count': len(rows)})
    return output/'data.yaml'


def train(data, project, epochs):
    from ultralytics import YOLO
    config = dict(data=str(Path(data).resolve()), epochs=epochs, imgsz=960, batch=4,
        seed=42, deterministic=True, workers=0, device=0, project=str(Path(project).resolve()),
        name='game_ui', exist_ok=False, fliplr=0., flipud=0., degrees=0., mosaic=0.,
        translate=.05, scale=.2, hsv_h=.0, hsv_s=.15, hsv_v=.2, patience=20)
    model = YOLO('yolo11n.pt')
    model.train(**config)
    write(Path(model.trainer.save_dir)/'reproduction.json', {'base': 'yolo11n.pt', 'config': config, 'python': sys.version})


def evaluate(weights, data, output):
    from ultralytics import YOLO
    model = YOLO(weights)
    report = {}
    for split in ('val', 'test'):
        metrics = model.val(data=str(Path(data).resolve()), split=split, imgsz=960, workers=0, device='cpu', plots=True)
        report[split] = {'metrics': metrics.results_dict, 'per_class_ap': metrics.box.maps.tolist(), 'speed': metrics.speed}
    write(output, report)


def export(weights, output):
    from ultralytics import YOLO
    destination = Path(output).resolve()
    if destination.exists():
        raise ValueError('export into a new candidate folder; never overwrite validated weights')
    model = YOLO(weights)
    exported = Path(model.export(format='onnx', imgsz=960, batch=1, dynamic=False,
        simplify=False, nms=False, opset=17, half=False, device='cpu'))
    destination.mkdir(parents=True)
    target = destination/'game_ui.onnx'
    shutil.copy2(exported, target)
    write(destination/'detector.json', {'schema_version': 1, 'mode': 'shadow',
        'format': 'yolo11_raw_detection', 'file': target.name,
        'sha256': hashlib.sha256(target.read_bytes()).hexdigest(), 'image_size': 960,
        'classes': [model.names[i] for i in range(len(model.names))],
        'thresholds': {}, 'click_validated_targets': [], 'status': 'candidate; live validation required'})


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest='command', required=True)
    p = commands.add_parser('prepare'); p.add_argument('manifest'); p.add_argument('output')
    p = commands.add_parser('train'); p.add_argument('data'); p.add_argument('project'); p.add_argument('--epochs', type=int, default=100)
    p = commands.add_parser('evaluate'); p.add_argument('weights'); p.add_argument('data'); p.add_argument('output')
    p = commands.add_parser('export'); p.add_argument('weights'); p.add_argument('output')
    args = vars(parser.parse_args()); command = args.pop('command')
    if command == 'prepare': args['manifest_path'] = args.pop('manifest')
    result = globals()[command](**args)
    if result: print(result)


if __name__ == '__main__':
    main()
