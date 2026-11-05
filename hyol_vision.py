"""Pure image recognition and bounded caches. No window or ledger access."""
from __future__ import annotations
from collections import OrderedDict
import functools
import hashlib
from pathlib import Path
import sys
from typing import Iterable
from hyol_errors import PendingError
from hyol_environment import configure_numeric_runtime
configure_numeric_runtime()
import numpy as np
from PIL import Image
from hyol_opencv import NUMERIC_MARGIN, correlation_map, exact_patch_score

IDENTITY_SERVER_WIDTH = 18
IDENTITY_TEXT_THRESHOLD = 120
IDENTITY_TEXT_SATURATION = 35


def _matcher(name):
    # Existing code instruments the public scheduler facade. New callers can
    # use this module directly without importing any Windows/runtime code.
    facade = sys.modules.get('hyol_scheduler')
    return getattr(facade, name, globals()[name]) if facade else globals()[name]


def dedupe_digit_matches(matches: Iterable[tuple[float, int, int]], min_x_gap: int = 3) -> list[tuple[float, int, int]]:
    """Keep one strongest template hit per horizontal digit position."""
    ordered = sorted(matches, key=lambda row: (-row[0], row[1], row[2]))
    accepted: list[tuple[float, int, int]] = []
    for match in ordered:
        if any(abs(match[1] - prior[1]) <= min_x_gap for prior in accepted):
            continue
        accepted.append(match)
    return sorted(accepted, key=lambda row: (row[1], row[2]))


def read_template_digits(
    frame: Image.Image,
    templates: "TemplateStore",
    asset_dir: Path,
    prefix: str,
    roi: tuple[int, int, int, int],
    threshold: float = 0.94,
    min_digits: int = 1,
    max_digits: int = 8,
) -> int:
    """Read a bounded integer using per-digit image templates.

    This intentionally rejects missing/ambiguous digits instead of guessing. A
    caller can then keep a purchase or balance check in ``pending`` safely.
    """
    found: list[tuple[float, int, int]] = []
    digit_min_gap = 3
    for digit in range(10):
        path = asset_dir / f"{prefix}{digit}.bmp"
        if not path.exists():
            raise PendingError(f"missing digit template: {path.name}")
        # Same-glyph side lobes sit a few pixels apart; a gap tied to the
        # glyph width keeps one hit per position without merging real
        # adjacent digits (whose spacing equals the full glyph width).
        try:
            digit_min_gap = max(digit_min_gap, int(templates.load(path).shape[1] * 0.6))
        except (AttributeError, TypeError, IndexError):
            pass  # non-array template stub (tests); keep the default gap
        if prefix.startswith("market_"):
            # Market glyphs are adjacent and sit on a textured, dimmed map.
            # Use the edge matcher with a stronger edge cutoff; its bounded
            # local non-max suppression keeps repeated digits such as ``00``.
            edge_threshold = 30 if prefix == "market_balance_digit_" else 70
            variant_paths = [path, *sorted(asset_dir.glob(f"{prefix}{digit}_*.bmp"))]
            matches: list[tuple[float, int, int]] = []
            for variant in variant_paths:
                matches.extend(templates.find_all_edges(frame, variant, roi, threshold, edge_threshold=edge_threshold))
        else:
            matches = templates.find_all(frame, path, roi, threshold)
        found.extend((score, x, digit) for score, x, _y in matches)
    selected = dedupe_digit_matches(found, min_x_gap=digit_min_gap)
    if not (min_digits <= len(selected) <= max_digits):
        raise PendingError(f"digit count outside bounds for {prefix}: {len(selected)}")
    return int("".join(str(digit) for _score, _x, digit in selected))


def edge_binary(image: Image.Image, threshold: int = 70) -> np.ndarray:
    """Return a small, translation-stable edge mask for UI text/icons."""
    gray = np.asarray(image.convert("L"), dtype=np.int16)
    dx = np.abs(np.diff(gray, axis=1, prepend=gray[:, :1]))
    dy = np.abs(np.diff(gray, axis=0, prepend=gray[:1, :]))
    return (dx + dy) > threshold


def edge_f1(reference: Image.Image, candidate: Image.Image, threshold: int = 70) -> float:
    """Compare UI glyph edges while ignoring map/background colour changes."""
    ref = edge_binary(reference, threshold)
    got = edge_binary(candidate, threshold)
    if ref.shape != got.shape:
        return 0.0
    intersection = int((ref & got).sum())
    denominator = int(ref.sum()) + int(got.sum())
    return (2.0 * intersection / denominator) if denominator else 0.0


def identity_text_mask(image: Image.Image, threshold: int = IDENTITY_TEXT_THRESHOLD) -> np.ndarray:
    """Extract the bright, low-saturation identity glyphs from a name strip.

    Grayscale NCC over the complete strip is dominated by the changing map
    texture.  The name itself is white/gray and low-saturation, so this mask
    keeps the server prefix and character name while discarding most of the
    background.
    """
    rgb = np.asarray(image.convert("RGB"), dtype=np.int16)
    mask = (
        (rgb.min(axis=2) >= threshold)
        & ((rgb.max(axis=2) - rgb.min(axis=2)) <= IDENTITY_TEXT_SATURATION)
    ).astype(np.uint8)
    # The enrolled strip is 32 px high and the glyphs start below the
    # animated map edge.  The first eight rows contain only background on the
    # current client; retaining them makes a repaint alter the identity score
    # even when the server/name text is unchanged.  Suppress that fixed
    # background band while keeping the complete glyph region for all tabs.
    if mask.shape[0] > 8:
        mask[:8, :] = 0
    return mask


def mask_f1(reference: np.ndarray, candidate: np.ndarray) -> float:
    """Return F1 overlap for two equal-sized binary masks."""
    if reference.shape != candidate.shape:
        return 0.0
    intersection = int((reference & candidate).sum())
    denominator = int(reference.sum()) + int(candidate.sum())
    return (2.0 * intersection / denominator) if denominator else 0.0


def wanted_task_list_is_empty(image: Image.Image) -> bool:
    """Recognize the tested post-wanted state without OCR or a click.

    The ROI is the area below the fixed ``任务列表`` heading. While a wanted
    task is active it contains bright green task text and many character edges;
    after the automatic chain finishes the translucent panel is nearly blank.
    """
    # The translucent left border at x=1715..1724 exposes map vegetation.
    # Actual task text starts to its right; including that border can keep an
    # already empty list pending solely because the map behind it is green.
    roi = np.asarray(image.convert("RGB"), dtype=np.int16)[480:590, 1725:1910]
    green = (roi[:, :, 1] > 110) & (roi[:, :, 1] * 100 > roi[:, :, 0] * 135) & (roi[:, :, 1] * 100 > roi[:, :, 2] * 115)
    # The panel is translucent.  Once its task row disappears, grass, rocks,
    # NPCs, or animation can still produce arbitrarily many image edges.  Edge
    # density therefore cannot be a completion requirement: it caused the
    # wanted loop to wait forever on textured maps.  The active wanted row in
    # every recorded replay contains the distinctive bright-green task text;
    # completion is accepted only after that text is absent for consecutive
    # frames while the caller independently verifies the main page.
    return int(green.sum()) <= 10


def image_hash(image: Image.Image) -> str:
    return hashlib.sha256(image.convert("RGB").tobytes()).hexdigest()


@functools.lru_cache(maxsize=256)
def _fast_fft_length(minimum: int) -> int:
    """Smallest 2/3/5-smooth length; padding keeps valid correlation intact."""
    best = 1 << (minimum - 1).bit_length()
    power5 = 1
    while power5 < best:
        power3 = power5
        while power3 < best:
            needed = (minimum + power3 - 1) // power3
            candidate = power3 * (1 << (needed - 1).bit_length())
            best = min(best, candidate)
            power3 *= 3
        power5 *= 5
    return best


class TemplateSpectra:
    """Bounded transforms of immutable templates, reusable across captures."""
    def __init__(self, max_bytes=64 * 1024 * 1024, max_entries=64):
        self.max_bytes = max_bytes
        self.max_entries = max_entries
        self.bytes = 0
        self.entries = OrderedDict()

    def get(self, template, shape):
        # Mutable arrays are deliberately uncached; replay callers can edit them.
        if template.flags.writeable:
            centered = template - template.mean()
            return float(np.sum(centered * centered)), np.fft.rfft2(centered[::-1, ::-1], shape)
        key = (id(template), shape)
        cached = self.entries.get(key)
        if cached is not None and cached[0] is template:
            self.entries.move_to_end(key)
            return cached[1], cached[2]
        centered = template - template.mean()
        variance = float(np.sum(centered * centered))
        transform = np.fft.rfft2(centered[::-1, ::-1], shape)
        size = template.nbytes + transform.nbytes
        if size <= self.max_bytes and self.max_entries > 0:
            while self.entries and (self.bytes + size > self.max_bytes
                                    or len(self.entries) >= self.max_entries):
                _, old = self.entries.popitem(last=False)
                self.bytes -= old[3]
            # Retain the original array so its identity cannot be recycled.
            self.entries[key] = template, variance, transform, size
            self.bytes += size
        return variance, transform


def ncc_map(image: np.ndarray, template: np.ndarray, *, prepared: dict | None = None,
            template_cache: TemplateSpectra | None = None, backend: str = 'numpy',
            cutoff: float | None = None) -> np.ndarray:
    """Return the normalized-correlation score at every valid position."""
    if backend == 'opencv':
        scores = correlation_map(image, template, prepared=prepared)
        if scores is not None and (cutoff is None or not np.any(np.abs(scores-cutoff) <= NUMERIC_MARGIN)):
            return scores
        # Boundary scores and low-contrast templates retain the original
        # float64 decision instead of loosening any recognition threshold.
    elif backend != 'numpy':
        raise ValueError(f'unknown recognition backend: {backend}')
    image = image.astype(np.float64, copy=False)
    template = template.astype(np.float64, copy=False)
    height, width = image.shape
    th, tw = template.shape
    if th > height or tw > width:
        return np.empty((0, 0), dtype=np.float64)
    shape = (_fast_fft_length(height + th - 1), _fast_fft_length(width + tw - 1))
    if template_cache is None:
        centered = template - template.mean()
        template_var = float(np.sum(centered * centered))
        template_transform = None
    else:
        template_var, template_transform = template_cache.get(template, shape)
    if template_var <= 1e-12:
        return np.full((height - th + 1, width - tw + 1), -1.0, dtype=np.float64)
    if prepared is not None:
        if prepared.get('image') is not image:
            prepared.clear()
            prepared['image'] = image
        transforms = prepared.setdefault('transforms', {})
        if shape not in transforms:
            # Bound memory even when many template sizes share a large ROI.
            if len(transforms) >= 2:
                transforms.pop(next(iter(transforms)))
            transforms[shape] = np.fft.rfft2(image, shape)
        image_transform = transforms[shape]
    else:
        image_transform = np.fft.rfft2(image, shape)
    if template_transform is None:
        template_transform = np.fft.rfft2(centered[::-1, ::-1], shape)
    correlation = np.fft.irfft2(
        image_transform * template_transform,
        shape,
    )
    numerator = correlation[th - 1 : height, tw - 1 : width]
    if prepared is not None and 'integrals' in prepared:
        integral, integral2 = prepared['integrals']
    else:
        integral = np.pad(image, ((1, 0), (1, 0))).cumsum(0).cumsum(1)
        integral2 = np.pad(image * image, ((1, 0), (1, 0))).cumsum(0).cumsum(1)
        if prepared is not None:
            prepared['integrals'] = integral, integral2
    sums = integral[th:, tw:] - integral[:-th, tw:] - integral[th:, :-tw] + integral[:-th, :-tw]
    sums2 = integral2[th:, tw:] - integral2[:-th, tw:] - integral2[th:, :-tw] + integral2[:-th, :-tw]
    variance = np.maximum(sums2 - sums * sums / (th * tw), 0)
    denominator = np.sqrt(variance * template_var)
    scores = np.full_like(numerator, -1.0)
    np.divide(numerator, denominator, out=scores, where=denominator > 1e-12)
    return scores


def ncc(image: np.ndarray, template: np.ndarray, *, prepared: dict | None = None,
        template_cache: TemplateSpectra | None = None, backend: str = 'numpy',
        cutoff: float | None = None) -> tuple[float, int, int]:
    scores = ncc_map(image, template, prepared=prepared, template_cache=template_cache,
                     backend=backend)
    if not scores.size:
        return -1.0, -1, -1
    y, x = np.unravel_index(np.argmax(scores), scores.shape)
    score = float(scores[y, x])
    if backend == 'opencv' and score > -1.:
        # A clearly rejected result cannot authorize a click. Its approximate
        # peak need not trigger a full-frame FFT merely because two negative
        # peaks are close; positive/threshold-adjacent ties remain exact.
        ambiguous = ((cutoff is None or score >= cutoff-NUMERIC_MARGIN)
                     and np.count_nonzero(scores >= score-NUMERIC_MARGIN) > 1)
        boundary = cutoff is not None and abs(score-cutoff) <= NUMERIC_MARGIN
        if ambiguous or boundary:
            return ncc(image, template, prepared=prepared, template_cache=template_cache)
        score = exact_patch_score(image, template, int(x), int(y))
    return score, int(x), int(y)


class TemplateStore:
    def __init__(self, threshold: float, *, max_frame_bytes=32 * 1024 * 1024, backend='opencv'):
        if backend not in ('numpy', 'opencv'):
            raise ValueError(f'unknown recognition backend: {backend}')
        self.backend = backend
        self.max_frame_bytes = max_frame_bytes
        self.threshold = threshold
        self.cache: dict[Path, np.ndarray] = {}
        self.variants = {"leaf_return_current.bmp":
                         ("leaf_return_hover.bmp", "leaf_return_village_current.bmp", "leaf_return_rooftop_current.bmp", "leaf_return_rooftop_second.bmp", "leaf_return_rooftop_live.bmp", "leaf_return_rotation_1.bmp", "leaf_return_rotation_2.bmp", "leaf_return_rotation_3.bmp", "leaf_return_rotation_4.bmp", "leaf_return_rotation_5.bmp")}
        self.resize_scale = (1.0, 1.0)
        self.last_match_size = None
        self.last_match_source = "template"
        self._asset_versions = {}
        self._frame = None
        self._frame_matches = {}
        self._frame_crops = {}
        self._frame_correlation = {}
        self._frame_edges = {}
        self._edge_templates = OrderedDict()
        self._template_spectra = TemplateSpectra()

    def clear(self):
        self.begin_frame(None)
        self.cache.clear()
        self._asset_versions.clear()
        self._edge_templates.clear()
        self._template_spectra.entries.clear()
        self._template_spectra.bytes = 0

    def _trim_frame_cache(self):
        def memory():
            arrays = {}
            def visit(value):
                if isinstance(value, np.ndarray):
                    arrays[id(value)] = value.nbytes
                elif isinstance(value, dict):
                    for item in value.values():
                        visit(item)
                elif isinstance(value, (tuple, list)):
                    for item in value:
                        visit(item)
            for cache in (self._frame_crops, self._frame_correlation, self._frame_edges):
                visit(cache)
            return sum(arrays.values())
        for cache in (self._frame_correlation, self._frame_crops, self._frame_edges):
            while cache and memory() > self.max_frame_bytes:
                cache.pop(next(iter(cache)))

    def begin_frame(self, frame: Image.Image) -> None:
        # Only capture() opts immutable live snapshots into caching. Replay
        # callers can still edit an image between matches without stale results.
        self._frame = frame
        self._frame_matches.clear()
        self._frame_crops.clear()
        self._frame_correlation.clear()
        self._frame_edges.clear()

    def _crop(self, frame, roi):
        dtype = np.uint8 if self.backend == 'opencv' else np.float64
        if frame is not self._frame:
            return np.asarray(frame.crop(roi).convert("L"), dtype=dtype)
        if roi not in self._frame_crops:
            self._frame_crops[roi] = np.asarray(frame.crop(roi).convert("L"), dtype=dtype)
        return self._frame_crops[roi]

    @staticmethod
    def _asset_version(path):
        try:
            stat = path.stat()
            # Windows can coalesce rapid, same-size writes to one timestamp.
            # Input-authorizing templates must also identify their contents.
            digest = hashlib.blake2b(path.read_bytes(), digest_size=16).digest()
            return stat.st_mtime_ns, stat.st_size, digest
        except OSError:
            return None

    def load(self, path: Path) -> np.ndarray:
        version = self._asset_version(path)
        if path not in self.cache or self._asset_versions.get(path) != version:
            dtype = np.uint8 if self.backend == 'opencv' else np.float64
            self.cache[path] = np.asarray(Image.open(path).convert("L"), dtype=dtype)
            self.cache[path].setflags(write=False)
            self._asset_versions[path] = version
        return self.cache[path]

    def match(self, frame: Image.Image, path: Path, roi: tuple[int, int, int, int], threshold: float | None = None) -> tuple[bool, float, int, int]:
        self.last_match_size = None
        self.last_match_source = "template"
        version = self._asset_version(path)
        if version is None:
            return False, -1.0, -1, -1
        variants = tuple((name, self._asset_version(path.with_name(name)))
                         for name in self.variants.get(path.name, ()))
        cutoff = self.threshold if threshold is None else threshold
        key = (path, version, variants, roi, cutoff, self.resize_scale)
        cacheable = frame is self._frame
        if cacheable and key in self._frame_matches:
            result, self.last_match_size, self.last_match_source = self._frame_matches[key]
            return result
        result = self._match(frame, path, roi, cutoff)
        self._trim_frame_cache()
        if cacheable:
            self._frame_matches[key] = result, self.last_match_size, self.last_match_source
        return result

    def _match(self, frame, path, roi, cutoff):
        x1, y1, x2, y2 = roi
        crop = self._crop(frame, roi)
        prepared = self._frame_correlation.setdefault(roi, {}) if frame is self._frame else None
        hit_variant = None
        score, x, y = _matcher('ncc')(crop, self.load(path), prepared=prepared,
                          template_cache=self._template_spectra, backend=self.backend, cutoff=cutoff)
        if score < cutoff:
            for name in self.variants.get(path.name, ()):
                variant = path.with_name(name)
                if variant.exists():
                    candidate = _matcher('ncc')(crop, self.load(variant), prepared=prepared,
                                    template_cache=self._template_spectra, backend=self.backend, cutoff=cutoff)
                    if candidate[0] > score:
                        score, x, y = candidate
                        hit_variant = variant
        if hit_variant is not None:
            # Fusion consumers size the box from last_match_size; a variant
            # hit must report the variant's own shape, not the master's.
            self.last_match_size = self.load(hit_variant).shape
        sx, sy = self.resize_scale
        # Canonical game captures can enlarge/shrink native UI independently
        # of browser chrome. Keep the original cutoff, and reject duplicates.
        if score < cutoff and (abs(sx-1) > .01 or abs(sy-1) > .01):
            original = self.load(path)
            width, height = max(2,round(original.shape[1]*sx)), max(2,round(original.shape[0]*sy))
            pad_x, pad_y = round(abs(sx-1)*frame.width), round(abs(sy-1)*frame.height)
            expanded = (max(1,x1-pad_x), max(69,y1-pad_y), min(frame.width-1,x2+pad_x), min(frame.height-1,y2+pad_y))
            if width < expanded[2]-expanded[0] and height < expanded[3]-expanded[1]:
                resized = np.asarray(Image.fromarray(original.astype(np.uint8)).resize((width,height),Image.Resampling.BILINEAR),dtype=np.float64)
                canvas = self._crop(frame, expanded)
                scores = _matcher('ncc_map')(canvas,resized,backend=self.backend,cutoff=cutoff)
                by,bx = np.unravel_index(np.argmax(scores),scores.shape)
                best = float(scores[by,bx])
                if self.backend == 'opencv' and best >= cutoff:
                    # The opencv backend scores in float32; re-verify the peak
                    # exactly before authorizing input, as the main path does.
                    best = exact_patch_score(canvas, resized, int(bx), int(by))
                scores[max(0,by-height):by+height+1,max(0,bx-width):bx+width+1] = -1
                if best >= cutoff and float(scores.max()) < cutoff:
                    self.last_match_size = (height,width)
                    self.last_match_source = "template_scaled"
                    return True,best,expanded[0]+int(bx),expanded[1]+int(by)
        return score >= cutoff, score, x1 + x, y1 + y

    def find_all(self, frame: Image.Image, path: Path, roi: tuple[int, int, int, int], threshold: float | None = None) -> list[tuple[float, int, int]]:
        if not path.exists():
            return []
        x1, y1, x2, y2 = roi
        dtype = np.uint8 if self.backend == 'opencv' else np.float64
        image = np.asarray(frame.crop(roi).convert("L"), dtype=dtype)
        template = self.load(path)
        th, tw = template.shape
        cutoff = self.threshold if threshold is None else threshold
        results: list[tuple[float, int, int]] = []
        # Suppress locations in the score map. Painting a few source pixels
        # can leave the same large UI template above the cutoff indefinitely.
        scores = _matcher('ncc_map')(image, template, template_cache=self._template_spectra,
                                    backend=self.backend, cutoff=cutoff).copy()
        while scores.size:
            y, x = np.unravel_index(np.argmax(scores), scores.shape)
            score = float(scores[y, x])
            if score < cutoff or x < 0 or y < 0:
                break
            # UI glyphs can be adjacent (for example the ``00`` in a market
            # price).  Suppress only a small neighbourhood around the peak;
            # masking the whole template width would erase the next digit.
            radius_x = max(2, min(4, tw // 3))
            radius_y = max(2, min(4, th // 3))
            left = max(0, x - radius_x)
            top = max(0, y - radius_y)
            right = min(scores.shape[1], x + radius_x + 1)
            bottom = min(scores.shape[0], y + radius_y + 1)
            scores[top:bottom, left:right] = -np.inf
            if self.backend == 'opencv':
                score = exact_patch_score(image, template, int(x), int(y))
            if score < cutoff:
                continue
            results.append((score, x1 + int(x), y1 + int(y)))
            if len(results) >= 16:
                break
        return results

    def find_all_edges(
        self,
        frame: Image.Image,
        path: Path,
        roi: tuple[int, int, int, int],
        threshold: float | None = None,
        edge_threshold: int = 18,
    ) -> list[tuple[float, int, int]]:
        """Find repeated UI glyphs using edges rather than map-dependent color."""
        if not path.exists():
            return []
        x1, y1, x2, y2 = roi
        # Market digit templates share the same ROI; avoid recomputing the
        # edge mask, image FFT and immutable template FFT for each digit.
        edge_key = (roi, edge_threshold)
        if frame is self._frame:
            image = self._frame_edges.get(edge_key)
            if image is None:
                image = edge_binary(frame.crop(roi), threshold=edge_threshold).astype(np.float64)
                self._frame_edges[edge_key] = image
        else:
            image = edge_binary(frame.crop(roi), threshold=edge_threshold).astype(np.float64)
        template_key = (path, self._asset_version(path), edge_threshold)
        template = self._edge_templates.get(template_key)
        if template is None:
            with Image.open(path) as asset:
                template = edge_binary(asset.convert("L"), threshold=edge_threshold).astype(np.float64)
            template.setflags(write=False)
            self._edge_templates[template_key] = template
            if len(self._edge_templates) > 64:
                self._edge_templates.popitem(last=False)
        else:
            self._edge_templates.move_to_end(template_key)
        th, tw = template.shape
        cutoff = self.threshold if threshold is None else threshold
        prepared = (self._frame_correlation.setdefault(('edge', *edge_key), {})
                    if frame is self._frame else None)
        scores = _matcher('ncc_map')(image, template, prepared=prepared,
                         template_cache=self._template_spectra, backend=self.backend, cutoff=cutoff)
        candidates: list[tuple[float, int, int]] = []
        for y, x in zip(*np.where(scores >= cutoff)):
            candidates.append((float(scores[y, x]), x1 + int(x), y1 + int(y)))
        results: list[tuple[float, int, int]] = []
        radius_x = max(2, min(4, tw // 3))
        radius_y = max(2, min(4, th // 3))
        for score, x, y in sorted(candidates, key=lambda row: (-row[0], row[1], row[2])):
            if any(abs(x - prior_x) <= radius_x and abs(y - prior_y) <= radius_y for _prior_score, prior_x, prior_y in results):
                continue
            if self.backend == 'opencv':
                # Re-verify each surviving peak exactly; the edge map scored in
                # float32 can drift above the cutoff by a tiny margin.
                score = exact_patch_score(image, template, int(x) - x1, int(y) - y1)
                if score < cutoff:
                    continue
            results.append((score, x, y))
            if len(results) >= 16:
                break
        self._trim_frame_cache()
        return sorted(results, key=lambda row: (row[1], row[2]))
