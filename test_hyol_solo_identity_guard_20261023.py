"""Current-window identity must use the same glyph normalization as binding."""
import unittest
from types import SimpleNamespace
from unittest.mock import Mock

import numpy as np
from PIL import Image, ImageDraw
import hyol_checkpoints as cp
import hyol_scheduler as hs


class SoloIdentityGuardTests(unittest.TestCase):
    def frame(self, *, dim=False, changed=False, animated=False):
        image = Image.new('RGB', (1920, 1032), (25, 45, 30))
        draw = ImageDraw.Draw(image)
        left, top, right, bottom = hs.IDENTITY_ROI
        for column in range(2, right-left-3, 7):
            offset = 3 if changed and column > hs.IDENTITY_SERVER_WIDTH else 0
            draw.rectangle((left+column+offset, top+15, left+column+offset+2, bottom-3),
                           fill=(250, 250, 250))
        if animated:
            draw.rectangle((left, top, right-1, top+10), fill=(250, 250, 250))
        if dim:
            image = image.point(lambda value: round(value*.4))
        return image

    def templates(self, *, title=True, close=True, close_score=.97):
        def match(frame, path, roi, threshold=None):
            score = {'quiz_page.bmp': .999 if title else 0,
                     'quiz_close.bmp': close_score if close else 0}.get(path.name, 0)
            return score >= (threshold or .98), score, 0, 0
        return SimpleNamespace(match=Mock(side_effect=match))

    def scheduler(self, frame, templates=None):
        account = hs.Account(6, 'bound-account', 855)
        s = object.__new__(hs.Scheduler)
        s.active = account
        s.generic_current_tab_x = 855
        s.ensure_window = Mock()
        s.capture = Mock(return_value=frame)
        s._generic_current_tab_is_active = Mock(return_value=True)
        s._saved_tab_mapping = Mock(return_value={})
        s.template = Mock(side_effect=lambda name, *_args, **_kwargs:
                          (name == 'quiz_page.bmp', 1., 0, 0))
        s.templates = templates or self.templates()
        s.main_ready = Mock(return_value=True)
        s._verified_current_mask = cp.current_identity_mask(self.frame(), self.templates(title=False, close=False))
        return s, account

    def test_anchored_dim_quiz_keeps_same_identity_and_allows_guard(self):
        frame = self.frame(dim=True)
        s, account = self.scheduler(frame)
        self.assertIs(frame, s._content_guard_once(account, ['quiz_page.bmp']))

    def test_dim_quiz_mask_matches_binding_mask(self):
        normal = cp.current_identity_mask(self.frame(), self.templates(title=False, close=False))
        dim = cp.current_identity_mask(self.frame(dim=True), self.templates())
        np.testing.assert_array_equal(normal, dim)

    def test_title_and_close_are_both_required_for_compensation(self):
        for settings in ({'title': False}, {'close': False}, {'close_score': .949}):
            with self.subTest(settings=settings):
                s, account = self.scheduler(self.frame(dim=True), self.templates(**settings))
                with self.assertRaisesRegex(hs.SafetyError, '身份发生变化'):
                    s._content_guard_once(account, ['quiz_page.bmp'])

    def test_different_role_on_same_tab_still_blocks(self):
        s, account = self.scheduler(self.frame(dim=True, changed=True))
        with self.assertRaisesRegex(hs.SafetyError, '身份发生变化'):
            s._content_guard_once(account, ['quiz_page.bmp'])

    def test_animated_pixels_above_glyphs_are_excluded_consistently(self):
        frame = self.frame(animated=True)
        s, account = self.scheduler(frame)
        self.assertIs(frame, s._content_guard_once(account, ['quiz_page.bmp']))

    def test_different_tab_is_rejected_before_identity_normalization(self):
        s, account = self.scheduler(self.frame(dim=True))
        s._generic_current_tab_is_active.return_value = False
        with self.assertRaisesRegex(hs.SafetyError, 'tab changed'):
            s._content_guard_once(account, ['quiz_page.bmp'])

    def test_missing_click_page_anchor_still_blocks_same_role(self):
        s, account = self.scheduler(self.frame())
        with self.assertRaisesRegex(hs.SafetyError, 'page anchor guard failed'):
            s._content_guard_once(account, ['unknown_page.bmp'])

    def test_anchored_treasure_page_keeps_dimmed_identity(self):
        def match(frame, path, roi, threshold=None):
            score = 1. if path.name in ('treasure_page_anchor.bmp', 'treasure_close.bmp') else 0.
            return score >= (threshold or .98), score, 0, 0
        frame = self.frame(dim=True)
        s, account = self.scheduler(frame, SimpleNamespace(match=Mock(side_effect=match)))
        s.template = Mock(side_effect=lambda name, *_args, **_kwargs:
                          (name == 'treasure_page_anchor.bmp', 1., 0, 0))
        self.assertIs(frame, s._content_guard_once(account, ['treasure_page_anchor.bmp']))

    def test_treasure_requires_both_title_and_close(self):
        for missing in ('treasure_page_anchor.bmp', 'treasure_close.bmp'):
            with self.subTest(missing=missing):
                def match(frame, path, roi, threshold=None):
                    score = 1. if path.name in ('treasure_page_anchor.bmp', 'treasure_close.bmp') and path.name != missing else 0.
                    return score >= (threshold or .98), score, 0, 0
                s, account = self.scheduler(self.frame(dim=True), SimpleNamespace(match=Mock(side_effect=match)))
                with self.assertRaisesRegex(hs.SafetyError, '身份发生变化'):
                    s._content_guard_once(account, ['treasure_page_anchor.bmp'])


if __name__ == '__main__':
    unittest.main()
