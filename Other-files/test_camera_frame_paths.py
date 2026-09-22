"""Offline checks for the per-frame camera helpers; no camera, VISA or GUI.

Covers the 2026-09-17 acquisition-path optimisation: cached rotated-ROI
masks, the prepared drift background, cv2-backed display binning, in-place
preview scaling and the strided AUTO-contrast percentile.
"""
import importlib.util
import sys
import types
import unittest
from pathlib import Path

import numpy as np

# The vendor ``pco`` package is Windows-only; a stub keeps NumPy/PIL usable.
sys.modules.setdefault("pco", types.SimpleNamespace(Camera=object))
source = Path(__file__).resolve().parents[1] / "Transport_all_NH_dev.py"
spec = importlib.util.spec_from_file_location("transport_camera_paths_test", source)
app = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = app
spec.loader.exec_module(app)


def _reference_bin(image, factor):
    source = np.asarray(image, dtype=np.float32)
    height, width = source.shape
    bh, bw = height - height % factor, width - width % factor
    reduced = source[:bh, :bw].reshape(bh // factor, factor, bw // factor, factor).mean(axis=(1, 3))
    expanded = np.repeat(np.repeat(reduced, factor, axis=0), factor, axis=1)
    return np.pad(expanded, ((0, height - bh), (0, width - bw)), mode="edge")


def _reference_roi(image, roi):
    height, width = image.shape[:2]
    yy, xx = np.mgrid[0:height, 0:width]
    rx, ry = xx - roi["cx"], yy - roi["cy"]
    angle = np.deg2rad(roi["angle"])
    lx = rx * np.cos(angle) + ry * np.sin(angle)
    ly = -rx * np.sin(angle) + ry * np.cos(angle)
    return image[(np.abs(lx) <= roi["w"] / 2.0) & (np.abs(ly) <= roi["h"] / 2.0)]


class CameraFramePathTests(unittest.TestCase):
    def setUp(self):
        self.rng = np.random.default_rng(7)
        self.background = self.rng.integers(500, 3000, (257, 131), dtype=np.uint16)
        shifted = np.roll(self.background, (2, -3), (0, 1)).astype(np.int32)
        noise = self.rng.integers(-30, 30, self.background.shape)
        self.frame = np.clip(shifted + noise, 0, 65535).astype(np.uint16)
        self.difference = self.rng.normal(0.0, 25.0, self.background.shape).astype(np.float32)

    def test_rotated_roi_values_match_reference_and_cache(self):
        app._ROTATED_ROI_MASK_CACHE.clear()
        rois = [
            {"cx": 65.0, "cy": 128.0, "w": 30.0, "h": 20.0, "angle": 33.0},
            {"cx": 3.0, "cy": 2.0, "w": 30.0, "h": 20.0, "angle": 0.0},
            {"cx": 129.0, "cy": 256.0, "w": 9.0, "h": 4.0, "angle": 100.0},
        ]
        for roi in rois:
            for image in (self.frame, self.difference):
                expected = np.sort(_reference_roi(image, roi))
                for _ in range(2):  # second call is served from the cache
                    got = app.values_inside_rotated_roi(image, roi)
                    self.assertEqual(got.dtype, image.dtype)
                    np.testing.assert_array_equal(np.sort(got), expected)
        self.assertEqual(len(app._ROTATED_ROI_MASK_CACHE), len(rois))
        outside = {"cx": -100.0, "cy": -100.0, "w": 10.0, "h": 10.0, "angle": 0.0}
        self.assertEqual(app.values_inside_rotated_roi(self.frame, outside).size, 0)

    def test_roi_cache_stays_bounded_while_dragging(self):
        app._ROTATED_ROI_MASK_CACHE.clear()
        for step in range(3 * app._ROTATED_ROI_MASK_CACHE_LIMIT):
            roi = {"cx": 40.0 + step, "cy": 50.0, "w": 12.0, "h": 8.0, "angle": 0.0}
            app.values_inside_rotated_roi(self.frame, roi)
        self.assertLessEqual(len(app._ROTATED_ROI_MASK_CACHE), app._ROTATED_ROI_MASK_CACHE_LIMIT)

    def test_prepared_background_matches_raw_background(self):
        prepared = app.DriftBackground(self.background)
        self.assertIs(prepared.raw, self.background)
        self.assertEqual(prepared.float32.dtype, np.float32)
        raw = app.subtract_background_after_drift(self.frame, self.background)
        fast = app.subtract_background_after_drift(self.frame, prepared)
        np.testing.assert_allclose(raw[0], fast[0], atol=1e-3)
        self.assertEqual(raw[1:], fast[1:])
        self.assertEqual(
            app.estimate_drift_shift(self.background, self.frame),
            app.estimate_drift_shift(prepared, self.frame),
        )
        # The prepared float32 copy must not be modified by a subtraction.
        np.testing.assert_array_equal(prepared.float32, self.background.astype(np.float32))

    def test_drift_paths_agree_with_and_without_cv2(self):
        if app.cv2 is None:
            self.skipTest("cv2 not installed")
        with_cv2 = app.subtract_background_after_drift(self.frame, self.background)
        saved, app.cv2 = app.cv2, None
        try:
            without = app.subtract_background_after_drift(self.frame, self.background)
        finally:
            app.cv2 = saved
        # Both paths must recover the same integer-pixel shift direction.
        self.assertEqual(np.sign(with_cv2[1]), np.sign(without[1]))
        self.assertEqual(np.sign(with_cv2[2]), np.sign(without[2]))
        self.assertEqual(with_cv2[0].shape, without[0].shape)

    def test_display_binning_matches_numpy_reference(self):
        for factor in (1, 2, 4, 8):
            for image in (self.difference, self.difference[:64, :96]):
                got = app.spatial_bin_average_full_size(image, factor)
                self.assertEqual(got.shape, image.shape)
                np.testing.assert_allclose(got, _reference_bin(image, factor), atol=1e-4)
        with self.assertRaises(ValueError):
            app.spatial_bin_average_full_size(self.difference, 3)

    def test_preview_scaling_is_exact(self):
        low, high = 600.0, 2500.0
        expected = np.clip(
            (self.frame.astype(np.float32) - low) * 255.0 / (high - low), 0, 255
        ).astype(np.uint8)
        got = app.CameraAcquisitionWorker._make_preview(self.frame, low, high)
        np.testing.assert_array_equal(got, expected)
        self.assertEqual(got.dtype, np.uint8)
        degenerate = app.CameraAcquisitionWorker._make_preview(self.frame, low, low)
        self.assertEqual(degenerate.dtype, np.uint8)

        center, half = 0.5, 30.0
        expected = np.clip(
            127.5 + (self.difference - center) * 127.5 / half, 0, 255
        ).astype(np.uint8)
        got = app.difference_preview(self.difference, max_abs=half, center=center)
        np.testing.assert_array_equal(got, expected)
        # Endpoints map to exact 0/255 and the input array is left untouched.
        edges = np.array([[center - half, center, center + half]], dtype=np.float32)
        np.testing.assert_array_equal(
            app.difference_preview(edges, max_abs=half, center=center), [[0, 127, 255]]
        )
        untouched = self.difference.copy()
        app.difference_preview(self.difference, max_abs=half, center=center)
        np.testing.assert_array_equal(self.difference, untouched)

    def test_auto_contrast_is_exact_on_small_regions_and_close_on_large(self):
        small = self.frame[:100, :100]
        expected = np.percentile(
            small, (app.CONTRAST_LOW_PERCENTILE, app.CONTRAST_HIGH_PERCENTILE)
        )
        self.assertEqual(app.auto_contrast_limits(small), (float(expected[0]), float(expected[1])))
        large = self.rng.integers(1000, 3000, (1024, 1024), dtype=np.uint16)
        low, high = app.auto_contrast_limits(large, (10, 10, 1000, 1000))
        exact = np.percentile(
            large[10:1000, 10:1000],
            (app.CONTRAST_LOW_PERCENTILE, app.CONTRAST_HIGH_PERCENTILE),
        )
        self.assertLess(abs(low - exact[0]), 5.0)
        self.assertLess(abs(high - exact[1]), 5.0)

    def test_frame_mean_matches_numpy(self):
        self.assertAlmostEqual(app._frame_mean(self.frame), float(np.mean(self.frame)), places=6)

    def test_worker_rebuilds_prepared_background_only_when_replaced(self):
        worker = app.CameraAcquisitionWorker(lambda _message: None)
        self.assertIsNone(worker._background_prepared)
        worker.background = self.background
        prepared = app.DriftBackground(worker.background)
        worker._background_prepared = prepared
        self.assertIs(worker._background_prepared.raw, worker.background)
        worker.background = self.background.copy()
        self.assertIsNot(worker._background_prepared.raw, worker.background)


if __name__ == "__main__":
    unittest.main()
