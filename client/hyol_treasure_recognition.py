"""Strict treasure ambush layout shared by identity, navigation and actions."""
from __future__ import annotations

TEXT = 'treasure_trap_text_20261109.bmp'
CONFIRM = 'treasure_trap_confirm_20261109.bmp'
LEFT = 'treasure_trap_left_20261109.bmp'
RIGHT = 'treasure_trap_right_20261109.bmp'
TEXT_ROI = (840, 505, 1080, 560)
CONFIRM_ROI = (880, 540, 1040, 620)
LEFT_ROI = (745, 470, 825, 540)
RIGHT_ROI = (1090, 470, 1180, 540)
ROIS = {TEXT: TEXT_ROI, CONFIRM: CONFIRM_ROI, LEFT: LEFT_ROI, RIGHT: RIGHT_ROI}


def trap_layout(frame, templates=None):
    """The exact ambush words, button and two aligned corners are mandatory."""
    import hyol_scheduler as hs
    from PIL import Image
    if not isinstance(frame, Image.Image):
        return None
    templates = templates if templates is not None else hs.TemplateStore(.98)
    cache = getattr(frame, '_hyol_observations', None)
    key = ('treasure_trap_layout', id(templates), str(hs.ASSETS))
    if cache is not None and key in cache:
        return cache[key]
    result, positions = None, {}
    for name, roi in ROIS.items():
        path = hs.ASSETS/name
        if not path.is_file():
            break
        matched = templates.match(frame, path, roi, threshold=.98)
        if not isinstance(matched, tuple) or len(matched) != 4:
            break
        ok, score, x, y = matched
        if not ok:
            break
        positions[name] = (x, y)
    if len(positions) == len(ROIS):
        x, y = positions[TEXT]
        offsets = {CONFIRM: (41, 37), LEFT: (-102, -36), RIGHT: (250, -36)}
        if all(abs(positions[name][0]-x-dx) <= 2
               and abs(positions[name][1]-y-dy) <= 2
               for name, (dx, dy) in offsets.items()):
            result = dict(anchor=TEXT, anchor_roi=TEXT_ROI, confirm=CONFIRM,
                          confirm_roi=CONFIRM_ROI, anchors=tuple(ROIS),
                          offset=(x-868, y-526), threshold=.98)
    if cache is not None:
        cache[key] = result
    return result
