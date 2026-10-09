"""Common visual result and conservative fusion, independent of mouse control."""
from __future__ import annotations

from dataclasses import asdict, dataclass
import hashlib
import json
from pathlib import Path
import time

import numpy as np
from PIL import Image


@dataclass(frozen=True)
class Detection:
    target: str
    box: tuple[float, float, float, float] | None
    score: float
    source: str
    accepted: bool = False
    reason: str = ''

    def as_dict(self):
        return asdict(self)


def iou(a, b):
    x1, y1 = max(a[0], b[0]), max(a[1], b[1])
    x2, y2 = min(a[2], b[2]), min(a[3], b[3])
    overlap = max(0, x2-x1) * max(0, y2-y1)
    union = (a[2]-a[0])*(a[3]-a[1]) + (b[2]-b[0])*(b[3]-b[1]) - overlap
    return overlap / union if union > 0 else 0.0


def fuse(template: Detection, model: list[Detection], *, model_enabled=False):
    if template.accepted and (not _valid_box(template.box) or not np.isfinite(template.score)):
        return Detection(template.target, None, 0, 'fusion', False, 'invalid_box')
    if (not template.accepted and (template.source == 'classification'
            or template.reason in ('missing_template', 'disabled_control', 'disabled_calibration_target',
                                   'template_model_conflict', 'ambiguous_model_candidates', 'invalid_box'))):
        return template
    usable = [d for d in model if d.accepted and d.target == template.target]
    if any(not _valid_box(d.box) or not np.isfinite(d.score) for d in usable):
        return Detection(template.target, None, 0, 'fusion', False, 'invalid_box')
    if len(usable) > 1:
        return Detection(template.target, None, 0, 'fusion', False, 'ambiguous_model_candidates')
    if not usable:
        return template
    candidate = usable[0]
    if template.accepted:
        if not _valid_box(template.box):
            return Detection(template.target, None, 0, 'fusion', False, 'invalid_box')
        if iou(template.box, candidate.box) < .35:
            return Detection(template.target, None, 0, 'fusion', False, 'template_model_conflict')
        return Detection(template.target, template.box, template.score, 'template+yolo', True)
    if model_enabled:
        return candidate
    return Detection(template.target, None, candidate.score, 'yolo_shadow', False, 'model_not_validated_for_clicks')


def _valid_box(box):
    try:
        return (box is not None and len(box) == 4 and all(np.isfinite(box))
                and box[2] > box[0] and box[3] > box[1])
    except (TypeError, ValueError):
        return False


@dataclass(frozen=True)
class Viewport:
    """Mapping for an explicitly detected game content rectangle, not whole-window stretch."""
    screen_box: tuple[int, int, int, int]
    reference_size: tuple[int, int]

    def screen_point(self, point):
        x, y = point
        rw, rh = self.reference_size
        l, t, r, b = self.screen_box
        if rw <= 0 or rh <= 0 or r <= l or b <= t or not (0 <= x < rw and 0 <= y < rh):
            raise ValueError('point or viewport outside trusted content')
        return round(l + x*(r-l)/rw), round(t + y*(b-t)/rh)


class OnnxDetector:
    """YOLO11 detection export: float32 RGB letterbox, raw [1,4+C,N] output.

    Manifest pins file hash, class ordering and thresholds. No implicit downloads.
    Shadow mode can collect predictions but cannot authorize new clicks.
    """
    def __init__(self, manifest_path):
        self.path = Path(manifest_path)
        self.manifest = {}
        self.session = None
        self.error = ''
        self.cached_frame = None
        self.cached_predictions = []
        if not self.path.exists():
            return
        try:
            self.manifest = json.loads(self.path.read_text(encoding='utf-8'))
            if self.manifest.get('mode', 'off') == 'off':
                return
            model = (self.path.parent / self.manifest['file']).resolve()
            if not model.is_relative_to(self.path.parent.resolve()):
                raise ValueError('model outside manifest folder')
            if hashlib.sha256(model.read_bytes()).hexdigest() != self.manifest['sha256']:
                raise ValueError('model checksum mismatch')
            if self.manifest.get('format') != 'yolo11_raw_detection':
                raise ValueError('unsupported ONNX output contract')
            import onnxruntime as ort
            options = ort.SessionOptions()
            options.intra_op_num_threads = 2
            self.session = ort.InferenceSession(str(model), sess_options=options, providers=['CPUExecutionProvider'])
        except Exception as exc:
            self.error = f'{type(exc).__name__}: {exc}'

    def enabled_for(self, target):
        classes = self.manifest.get('classes', [])
        validated = self.manifest.get('click_validated_targets', [])
        disabled = self.manifest.get('disabled_calibration_targets', [])
        proofs = self.manifest.get('click_validation_evidence', {})
        thresholds = self.manifest.get('thresholds', {})
        threshold = thresholds.get(target) if isinstance(thresholds, dict) else None
        proof = proofs.get(target, {}) if isinstance(proofs, dict) else None
        return (self.session is not None and not self.error
                and self.manifest.get('mode') == 'validated'
                and isinstance(classes, list) and classes.count(target) == 1
                and isinstance(validated, list) and target in validated
                and isinstance(disabled, list) and target not in disabled
                and isinstance(threshold, (int, float)) and not isinstance(threshold, bool)
                and np.isfinite(threshold) and 0 < threshold <= 1
                and isinstance(proof, dict) and proof.get('status') == 'live_pass'
                and proof.get('model_sha256') == self.manifest.get('sha256')
                and isinstance(proof.get('evidence'), str) and bool(proof['evidence'].strip()))

    def predict(self, frame):
        if self.session is None:
            return []
        if frame is self.cached_frame:
            return self.cached_predictions
        start = time.monotonic()
        size = int(self.manifest.get('image_size', 960))
        ratio = min(size / frame.width, size / frame.height)
        width, height = round(frame.width*ratio), round(frame.height*ratio)
        left, top = (size-width)//2, (size-height)//2
        canvas = Image.new('RGB', (size, size), (114, 114, 114))
        canvas.paste(frame.convert('RGB').resize((width, height), Image.Resampling.BILINEAR), (left, top))
        tensor = np.asarray(canvas, dtype=np.float32).transpose(2, 0, 1)[None] / 255.0
        raw = self.session.run(None, {self.session.get_inputs()[0].name: tensor})[0]
        classes = self.manifest['classes']
        if raw.ndim != 3 or raw.shape[0] != 1 or raw.shape[1] != 4+len(classes):
            raise ValueError(f'unexpected detector output {raw.shape}')
        predictions = []
        for row in raw[0].T:
            cls = int(np.argmax(row[4:]))
            score = float(row[4+cls])
            target = classes[cls]
            if (not np.isfinite(score) or target in self.manifest.get('disabled_calibration_targets', [])
                    or score < float(self.manifest.get('thresholds', {}).get(target, .85))):
                continue
            cx, cy, w, h = map(float, row[:4])
            box = (max(0., (cx-w/2-left)/ratio), max(0., (cy-h/2-top)/ratio),
                   min(float(frame.width), (cx+w/2-left)/ratio), min(float(frame.height), (cy+h/2-top)/ratio))
            if box[2] <= box[0] or box[3] <= box[1] or not all(np.isfinite(box)):
                continue
            predictions.append(Detection(target, box, score, 'yolo', True))
        kept = []
        for det in sorted(predictions, key=lambda d: d.score, reverse=True):
            if not any(d.target == det.target and iou(d.box, det.box) > .45 for d in kept):
                kept.append(det)
        self.cached_frame, self.cached_predictions = frame, kept
        self.last_latency_ms = (time.monotonic()-start)*1000
        return kept
