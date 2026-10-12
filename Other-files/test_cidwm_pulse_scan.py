"""CIDWM pulse sign scan (user, 2026-10-12): Pulses pulses, each with its
sign from Signs, and after each the raw 16-bit and the processed TIFF of a
camera frame whose processed image holds only frames taken after the pulse.

No Tk and no instrument: a stand-in 6221 and camera."""
import csv
import math
import tempfile
import threading
import time
import types
import unittest
from pathlib import Path
from unittest import mock

import numpy as np
from PIL import Image

from test_nonlocal_keithley import Var, bare_app
from test_ppms_motor_debug import app


def frame(window_start, value=0):
    return types.SimpleNamespace(
        raw=np.arange(12, dtype=np.uint16).reshape(3, 4) + value,
        display_difference=np.linspace(-1.0, 1.0, 12, dtype=np.float32).reshape(3, 4) + value,
        background_mode="tracking", temporal_average_count=4, spatial_binning=2,
        mean_filter_size=1, exposure_ms=1.0, window_start_monotonic=window_start,
    )


class Camera:
    """The running camera: the latest frame's average began just now, after
    any pulse.  ``stale`` reads first give a frame begun before the pulse."""

    def __init__(self, frames=True, stale=0):
        self.frames, self.stale = frames, stale

    @property
    def latest_frame(self):
        if not self.frames:
            return frame(0.0)        # the average began long before any pulse
        if self.stale:
            self.stale -= 1
            return frame(time.monotonic() - 10.0, value=100)
        return frame(time.monotonic())


class Source:
    def __init__(self, stop_on=None, event=None):
        self.calls = []
        self.stop_on, self.event = stop_on, event

    def set_dc_pulse(self, current_a, width_s, compliance_v, count):
        self.calls.append(("pulse", current_a, width_s, compliance_v, count))
        if self.stop_on == len([c for c in self.calls if c[0] == "pulse"]):
            self.event.set()
        return {"mode": "PULSE", "current_a": current_a, "total_time_s": 2 * width_s,
                "completed_before_readback": True, "in_compliance": False, "output_on": False}

    def output_off(self):
        self.calls.append(("off",))


def page(source=None, camera=None):
    a = bare_app()
    a.k6221_stop_event = threading.Event()
    a.k6221_controller = source or Source()
    a.camera_window = camera or Camera()
    a.k6221_roi = None
    a.post_ui = lambda callback: callback()
    a._publish_k6221_readback = mock.Mock()
    a._publish_k6221_off_state = mock.Mock()
    a.cidwm_scan_status_var = Var()
    return a


SETTINGS = {"current_ma": 2.0, "width_s": 50e-6, "compliance_v": 100.0, "signs": (1, -1, 1),
            "wait_s": 0.0, "signs_text": "+-"}


class SignTests(unittest.TestCase):
    def test_signs_repeat_to_fill_the_count(self):
        self.assertEqual(app.cidwm_sign_sequence("5", "+-"), (1, -1, 1, -1, 1))
        self.assertEqual(app.cidwm_sign_sequence(6, "++--"), (1, 1, -1, -1, 1, 1))
        self.assertEqual(app.cidwm_sign_sequence("3", "−"), (-1, -1, -1))
        self.assertEqual(app.cidwm_sign_sequence("4", "+, -, -"), (1, -1, -1, 1))

    def test_what_is_refused(self):
        for count, signs, message in (("0", "+", "whole number"), ("1.5", "+", "whole number"),
                                      ("x", "+", "whole number"), ("2", "", "at least one"),
                                      ("2", "+a", "only hold")):
            with self.assertRaisesRegex(ValueError, message):
                app.cidwm_sign_sequence(count, signs)

    def test_the_defaults(self):
        self.assertEqual((app.CIDWM_SCAN_PULSES, app.CIDWM_SCAN_SIGNS, app.CIDWM_SCAN_WAIT_S), (10, "+-", 1.0))


class WorkerTests(unittest.TestCase):
    def setUp(self):
        self.folder = Path(tempfile.mkdtemp()) / "run"
        self.folder.mkdir()
        # The program imports numpy and Pillow with the camera stack.
        for name, module in (("np", np), ("Image", Image)):
            patcher = mock.patch.object(app, name, module)
            patcher.start()
            self.addCleanup(patcher.stop)

    def test_each_pulse_has_its_sign_and_its_two_tiffs(self):
        a = page()
        a.k6221_roi = {"cx": 1.5, "cy": 1.0, "w": 4.0, "h": 3.0, "angle": 0.0}
        result = a._cidwm_pulse_scan_worker(SETTINGS, self.folder)
        self.assertEqual(result["done"], 3)
        pulses = [c for c in a.k6221_controller.calls if c[0] == "pulse"]
        self.assertEqual([(c[1], c[4]) for c in pulses], [(2e-3, 1), (-2e-3, 1), (2e-3, 1)],
                         "one pulse each, |I| with its sign")
        self.assertEqual({(c[2], c[3]) for c in pulses}, {(50e-6, 100.0)})
        calls = [c[0] for c in a.k6221_controller.calls]
        self.assertEqual(calls, ["pulse", "off"] * 3 + ["off"], "off after each pulse and at the end")
        with (self.folder / "run.csv").open(encoding="utf-8-sig") as file:
            rows = list(csv.DictReader(file))
        self.assertEqual([(r["pulse"], r["sign"], r["set_current_mA"]) for r in rows],
                         [("1", "+", "2"), ("2", "-", "-2"), ("3", "+", "2")])
        self.assertEqual(rows[1]["raw_tiff"], "CIDWM_pulse00002_neg_-2mA_raw16.tiff")
        self.assertEqual(rows[1]["processed_tiff"],
                         "CIDWM_pulse00002_neg_-2mA_TrackingDiff_Avg4_Bin2x2_Mean1x1_float32.tiff")
        self.assertTrue(rows[0]["roi_mean_processed"], "the ROI mean of the processed image")
        expected = frame(0.0)
        raw = np.asarray(Image.open(self.folder / rows[0]["raw_tiff"]))
        processed = np.asarray(Image.open(self.folder / rows[0]["processed_tiff"]))
        self.assertEqual(raw.dtype, np.uint16)
        np.testing.assert_array_equal(raw, expected.raw)
        np.testing.assert_allclose(processed, expected.display_difference)
        self.assertEqual({r["note"] for r in rows}, {""})
        self.assertEqual(len(list(self.folder.glob("*.tiff"))), 6)

    def test_a_stop_ends_after_the_pulse_with_the_6221_off(self):
        event = threading.Event()
        source = Source(stop_on=2, event=event)
        a = page(source)
        a.k6221_stop_event = event
        source.event = event
        result = a._cidwm_pulse_scan_worker(SETTINGS, self.folder)
        self.assertEqual(result["done"], 1)
        self.assertEqual([c[0] for c in source.calls], ["pulse", "off", "pulse", "off", "off"])
        with (self.folder / "run.csv").open(encoding="utf-8-sig") as file:
            rows = list(csv.DictReader(file))
        # Review, 2026-10-12: the second pulse was applied, so it has its row.
        self.assertEqual([(r["pulse"], r["note"], r["raw_tiff"]) for r in rows],
                         [("1", "", rows[0]["raw_tiff"]), ("2", "stopped before the images were saved", "")])
        self.assertTrue(a.cidwm_scan_off_confirmed)

    def test_no_frame_after_the_pulse_fails_with_a_row_and_the_6221_off(self):
        a = page(camera=Camera(frames=False))
        with mock.patch.object(app, "CIDWM_SCAN_FRAME_TIMEOUT_S", 0.1):
            with self.assertRaisesRegex(RuntimeError, "No camera frame taken wholly after the pulse"):
                a._cidwm_pulse_scan_worker(SETTINGS, self.folder)
        self.assertEqual(a.k6221_controller.calls[-1], ("off",))
        with (self.folder / "run.csv").open(encoding="utf-8-sig") as file:
            rows = list(csv.DictReader(file))
        self.assertEqual(len(rows), 1)
        self.assertTrue(rows[0]["note"].startswith("images not saved: No camera frame"))

    def test_a_frame_begun_before_the_pulse_is_not_saved(self):
        """Review, 2026-10-12: the processed image averages a block of
        frames with a cross-fade, so the scan waits for a frame whose oldest
        frame began after the pulse."""
        a = page(camera=Camera(stale=3))
        a._cidwm_pulse_scan_worker(dict(SETTINGS, signs=(1,)), self.folder)
        raw = np.asarray(Image.open(next(self.folder.glob("*_raw16.tiff"))))
        np.testing.assert_array_equal(raw, frame(0.0).raw, "not the stale frame (raw + 100)")

    def test_no_difference_image_is_refused(self):
        a = page()
        stale = frame(0.0)
        stale.display_difference = None
        with self.assertRaisesRegex(RuntimeError, "enable plain or drift-tracking difference"):
            a._cidwm_snapshot(stale)


class WindowStartTests(unittest.TestCase):
    """The camera's block average with its cross-fade: each displayed image
    carries the exposure start of its oldest frame."""

    def setUp(self):
        patcher = mock.patch.object(app, "np", np)
        patcher.start()
        self.addCleanup(patcher.stop)
        self.worker = app.CameraAcquisitionWorker.__new__(app.CameraAcquisitionWorker)
        self.worker._reset_difference_display_average()

    def starts(self, count, frames):
        starts = []
        for index in range(frames):
            self.worker._difference_for_display(
                np.full((2, 2), float(index), dtype=np.float32), count, 1, 1, False, float(index)
            )
            starts.append(self.worker.display_window_start)
        return starts

    def test_a_block_of_four_and_its_cross_fade(self):
        # Frames 0-3 fill the first block (from 0); then each cross-faded
        # image still holds that block (from 0) until frame 7 completes the
        # next one (from 4).
        self.assertEqual(self.starts(4, 12), [0, 0, 0, 0, 0, 0, 0, 4, 4, 4, 4, 8])

    def test_no_average(self):
        self.assertEqual(self.starts(1, 3), [0, 1, 2])

    def test_a_reset_starts_afresh(self):
        self.starts(4, 6)
        self.worker._difference_for_display(np.zeros((2, 2), np.float32), 4, 1, 1, True, 50.0)
        self.assertEqual(self.worker.display_window_start, 50.0)


if __name__ == "__main__":
    unittest.main()


class SaveImagesTests(unittest.TestCase):
    """Save images (user, 2026-10-12): the display, the raw 16-bit and the
    difference raw image of the same moment, in one folder."""

    def setUp(self):
        for name, module in (("np", np), ("Image", Image)):
            patcher = mock.patch.object(app, name, module)
            patcher.start()
            self.addCleanup(patcher.stop)
        self.folder = Path(tempfile.mkdtemp())
        a = self.app = bare_app()
        a.camera_window = types.SimpleNamespace(
            latest_frame=frame(0.0), latest_display_image=np.full((3, 4), 7, dtype=np.uint8),
            latest_display_geometry=(0, 0, 4, 3), pause_rendering=mock.Mock(),
            resume_rendering=mock.Mock(),
        )
        a.root, a.closing = None, False  # the dialogs are mocked
        a.active_file_exports = set()
        a._refresh_file_export_controls = mock.Mock()
        a._update_k6221_controls = mock.Mock()
        a.k6221_status_var = Var()
        a.cidwm_image_directory = self.folder
        a.post_ui = lambda callback: callback()
        a.file_executor = mock.Mock()
        a.file_executor.submit.side_effect = self.run_now

    @staticmethod
    def run_now(work):
        future = mock.Mock()
        result = work()
        future.result.return_value = result
        future.add_done_callback.side_effect = lambda callback: callback(future)
        return future

    def save(self):
        with mock.patch.object(app.filedialog, "askdirectory", return_value=str(self.folder)):
            self.app.save_cidwm_images()
        return sorted(path.name for path in self.folder.iterdir())

    def test_the_three_images(self):
        names = self.save()
        by_kind = {name.split("_", 4)[4]: name for name in names}
        self.assertEqual(set(by_kind), {"display8.png", "raw16.tiff",
                                        "TrackingDiff_Avg4_Bin2x2_Mean1x1_float32.tiff"})
        self.assertTrue(all(name.startswith("CIDWM_") for name in names))
        self.assertEqual(len({"_".join(name.split("_")[1:4]) for name in names}), 1, "one time stamp")
        np.testing.assert_array_equal(np.asarray(Image.open(self.folder / by_kind["raw16.tiff"])),
                                      frame(0.0).raw)
        np.testing.assert_allclose(
            np.asarray(Image.open(self.folder / by_kind["TrackingDiff_Avg4_Bin2x2_Mean1x1_float32.tiff"])),
            frame(0.0).display_difference)
        np.testing.assert_array_equal(np.asarray(Image.open(self.folder / by_kind["display8.png"])),
                                      np.full((3, 4), 7, dtype=np.uint8))
        self.assertTrue(self.app.k6221_status_var.get().startswith("Saved 3 images in "))
        self.app.camera_window.resume_rendering.assert_called_once()

    def test_without_a_difference_image_the_other_two(self):
        self.app.camera_window.latest_frame.display_difference = None
        names = self.save()
        self.assertEqual(sorted(name.split("_", 4)[4] for name in names), ["display8.png", "raw16.tiff"])
        self.assertIn("no difference image", self.app.k6221_status_var.get())

    def test_cancelling_the_folder_saves_nothing(self):
        with mock.patch.object(app.filedialog, "askdirectory", return_value=""):
            self.app.save_cidwm_images()
        self.assertEqual(list(self.folder.iterdir()), [])
        self.app.camera_window.resume_rendering.assert_called_once()
