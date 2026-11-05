"""Portable subset; private desktop replay fixtures remain local."""
"""OpenCV must preserve decision boundaries and input-authorizing locations."""
from pathlib import Path
import os
import tempfile
import unittest
from unittest.mock import patch
import numpy as np
from PIL import Image
import hyol_opencv as cv
import hyol_vision as vision

class OpenCVCorrelationTests(unittest.TestCase):

    def test_constant_images_and_templates_are_never_positive(self):
        image = np.ones((40, 60)) * 128
        varied = np.arange(35).reshape(5, 7)
        for target in (np.ones((5, 7)) * 128, varied):
            scores = vision.ncc_map(image, target, backend='opencv')
            self.assertTrue(np.all(scores == -1))

    def test_invalid_values_cannot_produce_positive_evidence(self):
        for value in (np.nan, np.inf, -np.inf):
            image = np.ones((40, 60))
            image[4, 5] = value
            scores = vision.ncc_map(image, np.arange(35).reshape(5, 7), backend='opencv')
            self.assertTrue(np.all(scores == -1))

    def test_too_large_template_and_missing_dependency_fail_closed(self):
        self.assertEqual((0, 0), vision.ncc_map(np.zeros((3, 4)), np.zeros((5, 6)), backend='opencv').shape)
        with patch.object(cv, 'cv2', None), self.assertRaisesRegex(RuntimeError, 'OpenCV'):
            vision.ncc(np.ones((40, 60)), np.arange(35).reshape(5, 7), backend='opencv')

    def test_exact_location_score_and_threshold_boundary(self):
        rng = np.random.default_rng(14)
        image = rng.integers(0, 256, (50, 70)).astype(float)
        target = image[13:21, 29:40].copy()
        target[0, 0] += 60
        reference = vision.ncc(image, target)
        for cutoff in (reference[0] - 1e-06, reference[0] + 1e-06, 0.98):
            actual = vision.ncc(image, target, backend='opencv', cutoff=cutoff)
            self.assertEqual(reference[1:], actual[1:])
            self.assertEqual(reference[0] >= cutoff, actual[0] >= cutoff)
            self.assertAlmostEqual(reference[0], actual[0], places=10)

    def test_positive_repeated_peaks_use_original_tie_resolution(self):
        rng = np.random.default_rng(15)
        image = rng.integers(0, 256, (50, 70)).astype(float)
        target = rng.integers(0, 256, (8, 11)).astype(float)
        image[4:12, 8:19] = target
        image[28:36, 42:53] = target
        reference = vision.ncc(image, target)
        actual = vision.ncc(image, target, backend='opencv', cutoff=0.98)
        self.assertEqual(reference, actual)

    def test_low_contrast_template_keeps_float64_result(self):
        rng = np.random.default_rng(16)
        target = np.ones((5, 7)) * 200 + np.arange(35).reshape(5, 7) * 0.0001
        image = np.ones((30, 40)) * 200 + rng.normal(0, 0.0001, (30, 40))
        image[8:13, 11:18] = target
        np.testing.assert_array_equal(vision.ncc_map(image, target), vision.ncc_map(image, target, backend='opencv'))

    def test_new_frame_and_changed_asset_invalidate_opencv_results(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / 'target.bmp'
            target = Image.fromarray(np.random.default_rng(17).integers(0, 256, (8, 11), dtype=np.uint8))
            target.save(path)
            frame = Image.new('L', (50, 40))
            frame.paste(target, (20, 12))
            store = vision.TemplateStore(0.98, backend='opencv')
            store.begin_frame(frame)
            self.assertTrue(store.match(frame, path, (0, 0, 50, 40))[0])
            blank = Image.new('L', frame.size)
            store.begin_frame(blank)
            self.assertFalse(store.match(blank, path, (0, 0, 50, 40))[0])
            Image.new('L', target.size, 100).save(path)
            self.assertFalse(store.match(frame, path, (0, 0, 50, 40))[0])

    def test_same_size_same_timestamp_replacement_invalidates_same_frame_cache(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / 'target.bmp'
            target = Image.fromarray(np.random.default_rng(71).integers(0, 256, (8, 11), dtype=np.uint8))
            target.save(path)
            stamp = path.stat()
            frame = Image.new('L', (50, 40))
            frame.paste(target, (20, 12))
            store = vision.TemplateStore(0.98, backend='opencv')
            store.begin_frame(frame)
            self.assertTrue(store.match(frame, path, (0, 0, 50, 40))[0])
            Image.new('L', target.size, 100).save(path)
            os.utime(path, ns=(stamp.st_atime_ns, stamp.st_mtime_ns))
            self.assertEqual(path.stat().st_size, stamp.st_size)
            self.assertEqual(path.stat().st_mtime_ns, stamp.st_mtime_ns)
            self.assertFalse(store.match(frame, path, (0, 0, 50, 40))[0])

    def test_integral_buffers_are_included_in_the_frame_memory_budget(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / 'target.bmp'
            frame = Image.fromarray(np.random.default_rng(18).integers(0, 256, (256, 256), dtype=np.uint8))
            frame.crop((80, 90, 95, 103)).save(path)
            store = vision.TemplateStore(0.98, backend='opencv', max_frame_bytes=100000)
            store.begin_frame(frame)
            self.assertTrue(store.match(frame, path, (0, 0, 256, 256))[0])
            arrays = {}

            def visit(value):
                if isinstance(value, np.ndarray):
                    arrays[id(value)] = value
                elif isinstance(value, dict):
                    for item in value.values():
                        visit(item)
                elif isinstance(value, (tuple, list)):
                    for item in value:
                        visit(item)
            for cache in (store._frame_crops, store._frame_correlation, store._frame_edges):
                visit(cache)
            self.assertLessEqual(sum((item.nbytes for item in arrays.values())), store.max_frame_bytes)
if __name__ == '__main__':
    unittest.main()
