"""CPU template correlation, with explicit handling of degenerate windows."""
from __future__ import annotations

import numpy as np

try:
    import cv2
except ImportError:
    cv2 = None
else:
    # The application owns one foreground worker. Avoid an independent GPU or
    # a large OpenCV thread pool for its many small UI regions.
    cv2.setNumThreads(1)
    cv2.ocl.setUseOpenCL(False)


NUMERIC_MARGIN = 2e-4


def backend_info():
    return dict(backend='opencv', available=cv2 is not None,
                version=cv2.__version__ if cv2 is not None else None,
                method='TM_CCOEFF_NORMED', numeric_margin=NUMERIC_MARGIN,
                threads=1, opencl=False)


def correlation_map(image, template, *, prepared=None):
    """Return OpenCV scores, or None when float64 evaluation is necessary.

    OpenCV returns ones for a constant template and zeros for constant image
    windows. Neither may be accepted as positive UI evidence. Keep the
    original matcher's -1 convention, and leave low-contrast templates to the
    original float64 implementation.
    """
    if cv2 is None:
        raise RuntimeError('OpenCV版缺少依赖，请安装 requirements.txt 或使用完整客户端')
    if image.ndim != 2 or template.ndim != 2:
        raise ValueError('template correlation requires two grayscale arrays')
    height, width = image.shape
    th, tw = template.shape
    if th > height or tw > width or min(th, tw) <= 0:
        return np.empty((0, 0), dtype=np.float64)
    shape = (height-th+1, width-tw+1)
    if ((image.dtype != np.uint8 and not np.isfinite(image).all())
            or (template.dtype != np.uint8 and not np.isfinite(template).all())):
        return np.full(shape, -1., dtype=np.float64)
    target64 = np.asarray(template, dtype=np.float64)
    centered = target64-target64.mean()
    variance = float(np.sum(centered*centered))
    if variance <= 1e-12:
        return np.full(shape, -1., dtype=np.float64)
    if variance < max(1., float(np.sum(target64*target64)))*1e-6:
        return None
    if prepared is not None:
        if prepared.get('image') is not image:
            prepared.clear()
            prepared['image'] = image
        pixels = prepared.get('opencv_pixels')
        if pixels is None:
            dtype = np.uint8 if image.dtype == np.uint8 and template.dtype == np.uint8 else np.float32
            pixels = np.ascontiguousarray(image, dtype=dtype)
            prepared['opencv_pixels'] = pixels
        integrals = prepared.get('opencv_integrals')
    else:
        dtype = np.uint8 if image.dtype == np.uint8 and template.dtype == np.uint8 else np.float32
        pixels = np.ascontiguousarray(image, dtype=dtype)
        integrals = None
    target = np.ascontiguousarray(template, dtype=pixels.dtype)
    if not np.isfinite(pixels).all() or not np.isfinite(target).all():
        return None
    scores = cv2.matchTemplate(pixels, target, cv2.TM_CCOEFF_NORMED).astype(np.float64)
    if integrals is None:
        integrals = cv2.integral2(pixels, sdepth=cv2.CV_64F, sqdepth=cv2.CV_64F)
        if prepared is not None:
            prepared['opencv_integrals'] = integrals
    integral, integral2 = integrals
    sums = integral[th:,tw:]-integral[:-th,tw:]-integral[th:,:-tw]+integral[:-th,:-tw]
    sums2 = integral2[th:,tw:]-integral2[:-th,tw:]-integral2[th:,:-tw]+integral2[:-th,:-tw]
    window_variance = sums2-sums*sums/(th*tw)
    scores[(window_variance <= 1e-12) | ~np.isfinite(scores)] = -1.
    return scores


def exact_patch_score(image, template, x, y):
    """Re-evaluate the selected location in float64 before exposing its score."""
    th, tw = template.shape
    patch = np.asarray(image[y:y+th,x:x+tw], dtype=np.float64)
    target = np.asarray(template, dtype=np.float64)
    patch = patch-patch.mean()
    target = target-target.mean()
    denominator = float(np.sqrt(np.sum(patch*patch)*np.sum(target*target)))
    return float(np.sum(patch*target)/denominator) if denominator > 1e-12 else -1.
