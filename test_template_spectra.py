import unittest
from unittest.mock import patch
import numpy as np
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
                np.testing.assert_allclose(reference, hs.ncc_map(frame, target, template_cache=cache), rtol=1e-11, atol=1e-11)
            self.assertEqual(4, transform.call_count)

    def test_mutable_templates_never_use_stale_spectra(self):
        rng = np.random.default_rng(95)
        target = rng.uniform(0, 255, (7, 11))
        frame = rng.uniform(0, 255, (55, 83))
        cache = hs.TemplateSpectra()
        hs.ncc_map(frame, target, template_cache=cache)
        target[:] = rng.uniform(0, 255, target.shape)
        np.testing.assert_allclose(hs.ncc_map(frame, target), hs.ncc_map(frame, target, template_cache=cache), rtol=1e-11, atol=1e-11)
        self.assertFalse(cache.entries)

    def test_template_spectra_obey_memory_and_entry_bounds(self):
        cache = hs.TemplateSpectra(max_bytes=18000, max_entries=2)
        rng = np.random.default_rng(96)
        for _ in range(8):
            target = rng.uniform(0, 255, (3, 5))
            target.setflags(write=False)
            cache.get(target, (16, 24))
            self.assertLessEqual(cache.bytes, cache.max_bytes)
            self.assertLessEqual(len(cache.entries), 2)
        before = cache.bytes
        large = rng.uniform(0, 255, (3, 5))
        large.setflags(write=False)
        cache.get(large, (128, 128))
        self.assertEqual(before, cache.bytes)
