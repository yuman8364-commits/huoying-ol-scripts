"""Single-inference page identification for the YOLO fast edition.

One ``OnnxDetector.predict()`` call returns every anchor class at once; this
module maps the surviving detections to page identifiers without any extra
template scan.  It is an observation layer only: it never authorizes clicks
and never overrides a template decision.  The scheduler keeps its OpenCV
gates; the page view feeds diagnostics/UI and future per-class-validated fast
paths after the existing model gate promotes those classes.
"""
from __future__ import annotations

# Page-unique anchors: seeing the anchor means the page/dialog is on screen.
PAGE_ANCHORS = {
    'survival_anchor.bmp': 'survival',
    'ranking_page.bmp': 'ranking',
    'mail_ready_anchor.bmp': 'mail',
    'welfare_online_anchor.bmp': 'welfare_online',
    'treasure_page_anchor.bmp': 'treasure',
    'daruma_game_page.bmp': 'daruma',
    'team_pvp_anchor.bmp': 'team_pvp',
    'daily_practice_toolbar.bmp': 'daily_practice_icon',
}

# Informative non-anchor detections reported as signals (state markers).
SIGNAL_CLASSES = {
    'arena_result_confirm.bmp': 'arena_result_dialog',
    'survival_result_confirm.bmp': 'survival_result_dialog',
    'ranking_result_confirm.bmp': 'ranking_result_dialog',
    'battle_auto_off.bmp': 'battle_auto_off_badge',
    'battle_auto_on_current_core.bmp': 'battle_auto_on_badge',
    'offline_login_text_current.bmp': 'offline_login',
    'leaf_return_current.bmp': 'leaf_return_icon',
    'mail_warehouse_empty_disabled.bmp': 'mail_warehouse_empty',
}


def _best_by_class(detections, mapping):
    best = {}
    for detection in detections:
        name = mapping.get(detection.target)
        if name is None:
            continue
        current = best.get(name)
        if current is None or detection.score > current.score:
            best[name] = detection
    return best


def page_view(detections):
    """Map anchor detections to {page_id: best Detection} (highest score wins)."""
    return _best_by_class(detections, PAGE_ANCHORS)


def signals(detections):
    """Map non-anchor detections to {signal_id: best Detection}."""
    return _best_by_class(detections, SIGNAL_CLASSES)


def identify(detector, frame):
    """One inference -> page view + signals + latency; read-only observation."""
    if detector is None or getattr(detector, 'session', None) is None:
        return {'available': False, 'page_view': {}, 'signals': {}, 'latency_ms': None}
    predictions = detector.predict(frame)
    return {'available': True,
            'page_view': page_view(predictions),
            'signals': signals(predictions),
            'latency_ms': round(float(getattr(detector, 'last_latency_ms', 0.0)), 2)}
