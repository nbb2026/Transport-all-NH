"""Drift tracking (user, 2026-10-13: "not working very well"): the estimate
on simulated frames with known shifts.  No camera, no window.

The frame is like the CIDWM view, smaller: a device edge, particles, noise,
and vignetting fixed to the optics (it does not move with the sample).  The
old estimate (the 4x-downsampled frame's phase correlation, no window) was
off by up to half a pixel on such frames, which left every particle a
bright/dark pair in the difference.  Other-files/ci/drift_benchmark.py runs
the full-size comparison.
"""
import importlib.util
import math
import sys
import types
import unittest
from pathlib import Path

import numpy as np

sys.modules.setdefault("pco", types.SimpleNamespace(Camera=object))
source = Path(__file__).resolve().parents[1] / "Transport_all_NH_dev.py"
spec = importlib.util.spec_from_file_location("transport_drift_test", source)
app = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = app
spec.loader.exec_module(app)

N = 512


def scene(seed=5):
    rng = np.random.default_rng(seed)
    y, x = np.mgrid[0:N, 0:N].astype(np.float64)
    erf = np.vectorize(math.erf)
    image = 7000.0 + 5000.0 * 0.5 * (1.0 + erf((x - 230.0 + 0.3 * (y - 256.0)) / 1.5))
    for _ in range(160):
        px, py = rng.uniform(0, N, 2)
        sigma, amplitude = rng.uniform(1.2, 2.5), rng.choice([-1.0, 0.6]) * rng.uniform(800, 3000)
        image += amplitude * np.exp(-((x - px) ** 2 + (y - py) ** 2) / (2 * sigma ** 2))
    return image


def moved(image, dx, dy):
    fy = np.fft.fftfreq(N)[:, None]
    fx = np.fft.fftfreq(N)[None, :]
    return np.fft.ifft2(np.fft.fft2(image) * np.exp(-2j * math.pi * (fx * dx + fy * dy))).real


VIGNETTING = 1.0 - 0.25 * ((np.mgrid[0:N, 0:N][1] - N / 2) ** 2 + (np.mgrid[0:N, 0:N][0] - N / 2) ** 2) / (N / 2) ** 2


def frame(sample, seed):
    signal = sample * VIGNETTING
    noisy = signal + np.random.default_rng(seed).normal(0, 1, signal.shape) * np.sqrt(signal)
    return np.clip(noisy, 0, 65535).astype(np.uint16)


class DriftTrackingTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.sample = scene()
        cls.background = frame(cls.sample, 1)
        cls.prepared = app.DriftBackground(cls.background)

    def estimate(self, dx, dy, seed=2):
        return app.estimate_drift_shift(self.prepared, frame(moved(self.sample, dx, dy), seed))

    def test_sub_pixel_shifts_to_a_twentieth_of_a_pixel(self):
        self.assertIsNotNone(self.prepared.fine, "the full-resolution step runs at this size")
        for dx, dy in ((0.0, 0.0), (0.4, -0.35), (0.85, 0.6), (1.37, -2.81), (-5.62, 3.15)):
            with self.subTest(shift=(dx, dy)):
                got_dx, got_dy, match = self.estimate(dx, dy)
                self.assertLess(math.hypot(got_dx - dx, got_dy - dy), 0.05)
                self.assertGreater(match, 0.3, "noise leaves a good frame near 0.6")

    def test_a_large_shift_through_the_coarse_step(self):
        got_dx, got_dy, _match = self.estimate(31.3, -22.6)
        self.assertLess(math.hypot(got_dx - 31.3, got_dy + 22.6), 0.05)

    def test_without_opencv_the_same_shift(self):
        if app.cv2 is None:
            self.skipTest("cv2 not installed")
        image = frame(moved(self.sample, 1.37, -2.81), 3)
        with_cv2 = app.estimate_drift_shift(self.background, image)
        saved, app.cv2 = app.cv2, None
        try:
            without = app.estimate_drift_shift(self.background, image)
        finally:
            app.cv2 = saved
        self.assertLess(math.hypot(with_cv2[0] - without[0], with_cv2[1] - without[1]), 0.05)

    def test_the_match_tells_a_good_frame_from_an_unrelated_one(self):
        _dx, _dy, same = app.estimate_drift_shift(self.prepared, self.background)
        self.assertGreater(same, 0.99)
        unrelated = frame(scene(seed=9), 4)
        _dx, _dy, other = app.estimate_drift_shift(self.prepared, unrelated)
        self.assertLess(other, 0.15)

    def test_a_drift_beyond_the_limit_or_a_dark_frame_is_not_shifted(self):
        """Review, 2026-10-13: a peak only searched near zero gave such frames
        a plausible wrong shift; as before, they get none."""
        for dx, dy in ((100.0, 0.0), (0.0, -110.0)):
            with self.subTest(shift=(dx, dy)):
                self.assertEqual(self.estimate(dx, dy)[:2], (0.0, 0.0))
        dark = np.random.default_rng(6).normal(100, 3, (N, N)).clip(0).astype(np.uint16)
        got = app.estimate_drift_shift(self.prepared, dark)
        self.assertEqual(got[:2], (0.0, 0.0))
        self.assertLess(got[2], app.DRIFT_MIN_MATCH)

    def test_a_frame_of_another_size_is_not_shifted(self):
        self.assertEqual(app.estimate_drift_shift(self.prepared, self.background[:256, :256]), (0.0, 0.0, 0.0))


if __name__ == "__main__":
    unittest.main()
