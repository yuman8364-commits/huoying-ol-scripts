"""Exact correlation and per-capture cache safety, including real replay frames."""
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

import numpy as np
from PIL import Image

import hyol_scheduler as hs


class RecognitionPerformanceTests(unittest.TestCase):
    def test_template_spectrum_reused_across_frames_with_exact_scores(self):
        rng = np.random.default_rng(94)
        target = rng.uniform(0, 255, (7, 11))
        target.setflags(write=False)
        cache = hs.TemplateSpectra()
        frames = [rng.uniform(0, 255, (55, 83)) for _ in range(3)]
        references = [hs.ncc_map(frame, target) for frame in frames]
        with patch.object(np.fft, 'rfft2', wraps=np.fft.rfft2) as transform:
            for frame, reference in zip(frames, references):
                np.testing.assert_allclose(reference, hs.ncc_map(frame, target,
                    template_cache=cache), rtol=1e-11, atol=1e-11)
            # Three distinct image transforms and one persistent template transform.
            self.assertEqual(4, transform.call_count)

    def test_mutable_templates_never_use_stale_spectra(self):
        rng = np.random.default_rng(95)
        target = rng.uniform(0, 255, (7, 11))
        frame = rng.uniform(0, 255, (55, 83))
        cache = hs.TemplateSpectra()
        hs.ncc_map(frame, target, template_cache=cache)
        target[:] = rng.uniform(0, 255, target.shape)
        np.testing.assert_allclose(hs.ncc_map(frame, target), hs.ncc_map(frame, target,
            template_cache=cache), rtol=1e-11, atol=1e-11)
        self.assertFalse(cache.entries)

    def test_template_spectra_obey_memory_and_entry_bounds(self):
        cache = hs.TemplateSpectra(max_bytes=18000, max_entries=2)
        rng = np.random.default_rng(96)
        for _ in range(8):
            target = rng.uniform(0, 255, (3, 5)); target.setflags(write=False)
            cache.get(target, (16, 24))
            self.assertLessEqual(cache.bytes, cache.max_bytes)
            self.assertLessEqual(len(cache.entries), 2)
        before = cache.bytes
        large = rng.uniform(0, 255, (3, 5)); large.setflags(write=False)
        cache.get(large, (128, 128))
        self.assertEqual(before, cache.bytes)

    def test_shared_roi_preparation_preserves_each_template_and_new_image(self):
        rng = np.random.default_rng(82)
        image = rng.uniform(0, 255, (45, 63))
        prepared = {}
        for shape in ((5,7), (6,7), (13,19), (20,9), (5,7)):
            target = rng.uniform(0, 255, shape)
            np.testing.assert_allclose(hs.ncc_map(image, target),
                hs.ncc_map(image, target, prepared=prepared), rtol=1e-11, atol=1e-11)
        other = rng.uniform(0,255,image.shape)
        np.testing.assert_allclose(hs.ncc_map(other, target),
            hs.ncc_map(other, target, prepared=prepared), rtol=1e-11, atol=1e-11)

    def test_fft_padding_retains_direct_normalized_correlation(self):
        rng = np.random.default_rng(42)
        for height, width, th, tw in ((21, 29, 4, 7), (19, 31, 9, 5), (13, 17, 13, 17)):
            image = rng.uniform(0, 255, (height, width))
            template = rng.uniform(0, 255, (th, tw))
            windows = np.lib.stride_tricks.sliding_window_view(image, (th, tw))
            centered = windows - windows.mean(axis=(-1, -2), keepdims=True)
            target = template - template.mean()
            numerator = np.einsum('ijab,ab->ij', centered, target)
            denominator = np.sqrt((centered**2).sum(axis=(-1, -2)) * (target**2).sum())
            np.testing.assert_allclose(hs.ncc_map(image, template), numerator/denominator,
                                       rtol=1e-11, atol=1e-11)
        self.assertEqual((0, 0), hs.ncc_map(np.zeros((3, 4)), np.zeros((4, 5))).shape)
        self.assertTrue(np.all(hs.ncc_map(np.ones((8, 9)), np.ones((3, 4))) == -1))

    def test_fft_length_is_smallest_smooth_length(self):
        for minimum in (1, 2, 17, 67, 101, 257, 1919, 2033):
            expected = minimum
            while True:
                rest = expected
                for prime in (2, 3, 5):
                    while rest % prime == 0:
                        rest //= prime
                if rest == 1:
                    break
                expected += 1
            self.assertEqual(expected, hs._fast_fft_length(minimum))

    def test_real_frame_decisions_and_coordinates_match_original_transform(self):
        paths = (
            hs.ROOT/'logs/preflight_20261004_063821_062492_fbdc0dc8.png',
            hs.ROOT/'logs/preflight_20261003_232527_624315_2febdf4a.png',
            hs.ROOT/'logs/scheduler_evidence/2026-10-01/account_c9ba2069ff/214000_653728_pending_multi_solo_start_before_return.png',
        )
        checks = (('anchor_top_menu.bmp', (500,69,1500,189), .92),
                  ('hot_spring_title_current.bmp', (780,230,1100,430), .98),
                  ('offline_login_prompt.bmp', (650,350,1300,780), .98))
        for path in paths:
            frame = Image.open(path).convert('RGB')
            for name, roi, cutoff in checks:
                store = hs.TemplateStore(.92)
                actual = store.match(frame, hs.ASSETS/name, roi, cutoff)
                with patch.object(hs, '_fast_fft_length', side_effect=lambda n: n):
                    reference = store.match(frame, hs.ASSETS/name, roi, cutoff)
                self.assertEqual((reference[0], *reference[2:]), (actual[0], *actual[2:]))
                self.assertAlmostEqual(reference[1], actual[1], places=10)

    def test_only_registered_snapshot_is_cached_and_new_capture_invalidates(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory)/'target.bmp'
            target = Image.fromarray(np.random.default_rng(2).integers(0, 255, (4,5), dtype=np.uint8))
            target.save(path)
            frame = Image.new('L', (25,20))
            frame.paste(target, (12,9))
            store = hs.TemplateStore(.98)
            roi = (0,0,25,20)
            with patch.object(hs, 'ncc', wraps=hs.ncc) as correlate:
                store.begin_frame(frame)
                self.assertTrue(store.match(frame, path, roi)[0])
                self.assertTrue(store.match(frame, path, roi)[0])
                self.assertEqual(1, correlate.call_count)
                next_frame = Image.new('L', frame.size)
                store.begin_frame(next_frame)
                self.assertFalse(store.match(next_frame, path, roi)[0])
                self.assertEqual(2, correlate.call_count)
            # Unregistered offline images may be edited by replay callers.
            frame.paste(0, (0,0,25,20))
            self.assertFalse(store.match(frame, path, roi)[0])
            frame.paste(target, (12,9))
            self.assertTrue(store.match(frame, path, roi)[0])

    def test_cache_invalidates_when_asset_or_threshold_changes(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory)/'target.bmp'
            target = Image.fromarray(np.random.default_rng(3).integers(0, 255, (4,5), dtype=np.uint8))
            target.save(path)
            frame = Image.new('L', (25,20))
            frame.paste(target, (12,9))
            store = hs.TemplateStore(.98)
            store.begin_frame(frame)
            self.assertTrue(store.match(frame, path, (0,0,25,20))[0])
            stamp = path.stat().st_mtime_ns
            Image.new('L', (5,4), 100).save(path)
            os.utime(path, ns=(stamp+1_000_000, stamp+1_000_000))
            self.assertFalse(store.match(frame, path, (0,0,25,20))[0])
            self.assertTrue(store.match(frame, path, (0,0,25,20), -1)[0])
            path.unlink()
            self.assertFalse(store.match(frame, path, (0,0,25,20), -1)[0])

    def test_cached_scaled_result_restores_geometry_metadata(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory)/'target.bmp'
            Image.new('L', (3,4)).save(path)
            frame = Image.new('L', (25,20))
            store = hs.TemplateStore(.98)
            store.begin_frame(frame)
            def match(*_args):
                store.last_match_size = (7,8)
                store.last_match_source = 'template_scaled'
                return True, .99, 3, 4
            with patch.object(store, '_match', side_effect=match) as matcher:
                store.match(frame, path, (0,0,25,20))
                store.last_match_size = None
                store.last_match_source = 'other'
                store.match(frame, path, (0,0,25,20))
                self.assertEqual(1, matcher.call_count)
                self.assertEqual((7,8), store.last_match_size)
                self.assertEqual('template_scaled', store.last_match_source)


if __name__ == '__main__':
    unittest.main()
