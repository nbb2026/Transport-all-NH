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
    any pulse.  ``stale`` reads first give a frame begun before the pulse.
    Camera Live shows the latest frame (``drawing``), on a 4 x 3 canvas."""

    def __init__(self, frames=True, stale=0, drawing=True):
        self.frames, self.stale, self.drawing = frames, stale, drawing

    @property
    def latest_display(self):
        if not self.drawing:
            return None
        shown = self.latest_frame
        return (np.full((3, 4), 9, dtype=np.uint8), (0, 0, 4, 3), shown)

    @property
    def latest_frame(self):
        if not self.frames:
            return frame(0.0)        # the average began long before any pulse
        if self.stale:
            self.stale -= 1
            return frame(time.monotonic() - 10.0, value=100)
        return frame(time.monotonic())


class Source:
    """The 6221; its OUTP? reads 0 (off) for the scan end's read back."""

    connected = True

    def __init__(self, stop_on=None, event=None):
        self.calls = []
        self.stop_on, self.event = stop_on, event
        self.lock = threading.RLock()
        self.instrument = types.SimpleNamespace(query=lambda command: "0")
        self.logger = lambda message: None

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
    a.thread_log = mock.Mock()
    return a


def run_recorder(test):
    """A run folder <tmp>/run/ with run.csv and run.log, through the shared
    recorder (user, 2026-10-18), closed when the test ends."""
    parent = Path(tempfile.mkdtemp())
    recorder = app.ScanRunRecorder(parent, "CIDWM", "run", {"test": True})
    test.addCleanup(recorder.close, "test")
    return recorder


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
        self.recorder = run_recorder(self)
        self.folder = self.recorder.directory
        # The program imports numpy and Pillow with the camera stack.
        for name, module in (("np", np), ("Image", Image)):
            patcher = mock.patch.object(app, name, module)
            patcher.start()
            self.addCleanup(patcher.stop)

    def test_each_pulse_has_its_sign_and_its_two_tiffs(self):
        a = page()
        a.k6221_roi = {"cx": 1.5, "cy": 1.0, "w": 4.0, "h": 3.0, "angle": 0.0}
        result = a._cidwm_pulse_scan_worker(SETTINGS, self.recorder)
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
        self.assertEqual(rows[1]["display_png"], "CIDWM_pulse00002_neg_-2mA_display8.png",
                         "the display too (user, 2026-10-12)")
        np.testing.assert_array_equal(np.asarray(Image.open(self.folder / rows[1]["display_png"])),
                                      np.full((3, 4), 9, dtype=np.uint8))
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
        self.assertEqual(len(list(self.folder.glob("*_display8.png"))), 3)

    def test_a_stop_ends_after_the_pulse_with_the_6221_off(self):
        event = threading.Event()
        source = Source(stop_on=2, event=event)
        a = page(source)
        a.k6221_stop_event = event
        source.event = event
        result = a._cidwm_pulse_scan_worker(SETTINGS, self.recorder)
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
                a._cidwm_pulse_scan_worker(SETTINGS, self.recorder)
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
        a._cidwm_pulse_scan_worker(dict(SETTINGS, signs=(1,)), self.recorder)
        raw = np.asarray(Image.open(next(self.folder.glob("*_raw16.tiff"))))
        np.testing.assert_array_equal(raw, frame(0.0).raw, "not the stale frame (raw + 100)")

    def test_without_camera_live_drawing_the_tiffs_and_a_note(self):
        a = page(camera=Camera(drawing=False))
        with mock.patch.object(app, "CIDWM_SCAN_DISPLAY_TIMEOUT_S", 0.1):
            a._cidwm_pulse_scan_worker(dict(SETTINGS, signs=(1,)), self.recorder)
        with (self.folder / "run.csv").open(encoding="utf-8-sig") as file:
            row = next(csv.DictReader(file))
        self.assertEqual(row["display_png"], "")
        self.assertTrue(row["raw_tiff"] and row["processed_tiff"])
        self.assertIn("display not saved", row["note"])

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


class SizeTests(unittest.TestCase):
    """The scan's own |I| (user, 2026-10-12); width and compliance from the
    pulse output."""

    def page(self, size):
        a = bare_app()
        a.k6221_controller = app.Keithley6221Controller(lambda _message: None)
        a.k6221_current_var, a.k6221_width_var = Var("1"), Var("50")
        a.k6221_compliance_var, a.k6221_count_var = Var("100"), Var("1")
        a.cidwm_scan_current_var = Var(size)
        a.cidwm_scan_pulses_var, a.cidwm_scan_signs_var, a.cidwm_scan_wait_var = Var("4"), Var("+-"), Var("1")
        return a

    def test_the_size_is_the_scans_own(self):
        settings = self.page("3.5")._parse_cidwm_pulse_scan()
        self.assertEqual((settings["current_ma"], settings["width_s"], settings["compliance_v"]),
                         (3.5, 50e-6, 100.0))
        self.assertEqual(self.page("-3.5")._parse_cidwm_pulse_scan()["current_ma"], 3.5, "the size only")

    def test_what_is_refused(self):
        for size, message in (("0", "above 0"), ("x", "a number"), ("200", "software limit")):
            with self.assertRaisesRegex(ValueError, message):
                self.page(size)._parse_cidwm_pulse_scan()

    def test_the_default(self):
        self.assertEqual(app.K6221_DEFAULT_CURRENT_MA, 2.0)


class AvtechSource:
    """The Avtech: one pulse per call, off again when it returns."""

    connected = True

    def __init__(self, trip_on=None):
        self.calls, self.trip_on = [], trip_on

    def single_pulse(self, amplitude_v, width_s):
        self.calls.append(("pulse", amplitude_v, width_s))
        pulses = len([c for c in self.calls if c[0] == "pulse"])
        return {"amplitude_v": amplitude_v, "width_s": width_s, "overload": pulses == self.trip_on,
                "pulse_end": time.monotonic()}

    def switch_off(self):
        self.calls.append(("off",))


AVTECH_SETTINGS = {"source": "avtech", "amplitude_v": 20.0, "width_s": 10e-9, "signs": (1, -1, 1),
                   "wait_s": 0.0, "signs_text": "+-"}


class AvtechWorkerTests(unittest.TestCase):
    """The pulse sign scan on the Avtech (user, 2026-10-13)."""

    def setUp(self):
        self.recorder = run_recorder(self)
        self.folder = self.recorder.directory
        for name, module in (("np", np), ("Image", Image)):
            patcher = mock.patch.object(app, name, module)
            patcher.start()
            self.addCleanup(patcher.stop)

    def page(self, avtech):
        a = page()
        a.avtech_controller = avtech
        a._publish_avtech_off_state = mock.Mock()
        return a

    def test_each_pulse_its_sign_its_images_and_the_avtech_off_at_the_end(self):
        avtech = AvtechSource()
        a = self.page(avtech)
        result = a._cidwm_pulse_scan_worker(AVTECH_SETTINGS, self.recorder)
        self.assertEqual(result["done"], 3)
        self.assertEqual(avtech.calls, [("pulse", 20.0, 10e-9), ("pulse", -20.0, 10e-9),
                                        ("pulse", 20.0, 10e-9), ("off",)])
        # Not used for pulses; only switched off at the end, as every
        # source of the page is at every scan end (user, 2026-10-18).
        self.assertEqual(a.k6221_controller.calls, [("off",)], "the 6221 only switched off at the end")
        self.assertEqual(a.cidwm_scan_outputs, {"Avtech": "zeroed", "6221": "zeroed"})
        with (self.folder / "run.csv").open(encoding="utf-8-sig") as file:
            rows = list(csv.DictReader(file))
        self.assertEqual(list(rows[0])[:7], ["pulse", "sign", "set_amplitude_V", "amplitude_V",
                                             "pulse_width_ns", "elapsed_s", "overload_tripped"])
        self.assertEqual([(r["sign"], r["set_amplitude_V"], r["pulse_width_ns"], r["overload_tripped"])
                          for r in rows], [("+", "20", "10", "False"), ("-", "-20", "10", "False"),
                                           ("+", "20", "10", "False")])
        self.assertEqual(rows[1]["raw_tiff"], "CIDWM_pulse00002_neg_-20V_raw16.tiff")
        self.assertTrue((self.folder / rows[1]["processed_tiff"]).exists())
        self.assertEqual(rows[1]["display_png"], "CIDWM_pulse00002_neg_-20V_display8.png")
        self.assertTrue(a.cidwm_scan_off_confirmed)

    def test_a_pulse_that_fired_then_failed_has_its_row(self):
        """Review, 2026-10-13: a failure after the trigger lost the row."""
        class Failing(AvtechSource):
            def single_pulse(self, amplitude_v, width_s):
                self.calls.append(("pulse", amplitude_v, width_s))
                raise app.AvtechPulseError("pulse fired, then: output? failed", fired=True,
                                           amplitude_v=amplitude_v, width_s=width_s)

        avtech = Failing()
        a = self.page(avtech)
        with self.assertRaisesRegex(app.AvtechPulseError, "pulse fired"):
            a._cidwm_pulse_scan_worker(AVTECH_SETTINGS, self.recorder)
        with (self.folder / "run.csv").open(encoding="utf-8-sig") as file:
            rows = list(csv.DictReader(file))
        self.assertEqual([(r["pulse"], r["amplitude_V"], r["pulse_width_ns"]) for r in rows], [("1", "20", "10")])
        self.assertIn("pulse fired, then", rows[0]["note"])
        self.assertEqual(avtech.calls[-1], ("off",))

    def test_an_overload_stops_the_scan_with_its_row(self):
        avtech = AvtechSource(trip_on=2)
        a = self.page(avtech)
        with self.assertRaisesRegex(RuntimeError, "output protection tripped"):
            a._cidwm_pulse_scan_worker(AVTECH_SETTINGS, self.recorder)
        self.assertEqual([c[0] for c in avtech.calls], ["pulse", "pulse", "off"])
        with (self.folder / "run.csv").open(encoding="utf-8-sig") as file:
            rows = list(csv.DictReader(file))
        self.assertEqual([(r["pulse"], r["overload_tripped"]) for r in rows], [("1", "False"), ("2", "True")])
        self.assertIn("overload", rows[1]["note"])
        self.assertTrue(a.cidwm_scan_off_confirmed)


class SaveImagesTests(unittest.TestCase):
    """Save images (user, 2026-10-12): the display, the raw 16-bit and the
    difference raw image of the same moment, in one folder."""

    def setUp(self):
        # No real dialog can open (Tk-free), whatever the code does; the tests
        # patch the dialog they expect on top of these stand-ins.
        for name, module in (("np", np), ("Image", Image), ("messagebox", mock.Mock()),
                             ("filedialog", mock.Mock())):
            patcher = mock.patch.object(app, name, module)
            patcher.start()
            self.addCleanup(patcher.stop)
        self.folder = Path(tempfile.mkdtemp())
        a = self.app = bare_app()
        # Camera Live drew the latest frame (latest_display is the matching
        # (image, geometry, frame) set the real _draw keeps).
        shown = frame(0.0)
        a.camera_window = types.SimpleNamespace(
            latest_frame=shown,
            latest_display=(np.full((3, 4), 7, dtype=np.uint8), (0, 0, 4, 3), shown),
            pause_rendering=mock.Mock(), resume_rendering=mock.Mock(),
        )
        # The dialogs are mocked; they open from root.after(0, ...) (M5,
        # review, 2026-10-18), here at once.
        a.root = types.SimpleNamespace(after=lambda _delay, callback: callback())
        a.closing = False
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

    # Camera Live hidden (review, 2026-10-13): it draws no new frames, so its
    # display is an older moment than the raw and processed images.

    def test_a_display_of_an_older_frame_is_left_out(self):
        camera = self.app.camera_window
        camera.latest_display = (np.full((3, 4), 25, dtype=np.uint8), (0, 0, 4, 3), frame(0.0, value=100))
        names = self.save()
        self.assertEqual(sorted(name.split("_", 4)[4] for name in names),
                         ["TrackingDiff_Avg4_Bin2x2_Mean1x1_float32.tiff", "raw16.tiff"])
        by_kind = {name.split("_", 4)[4]: name for name in names}
        # Both from the latest frame, not from the one Camera Live last drew.
        np.testing.assert_array_equal(np.asarray(Image.open(self.folder / by_kind["raw16.tiff"])),
                                      camera.latest_frame.raw)
        np.testing.assert_allclose(
            np.asarray(Image.open(self.folder / by_kind["TrackingDiff_Avg4_Bin2x2_Mean1x1_float32.tiff"])),
            camera.latest_frame.display_difference)
        status = self.app.k6221_status_var.get()
        self.assertTrue(status.startswith("Saved 2 images in "), status)
        self.assertIn("no display image: Camera Live has not drawn the newest frame (it is hidden", status)
        camera.resume_rendering.assert_called_once()

    def test_never_shown_still_saves_raw_and_processed(self):
        self.app.camera_window.latest_display = None   # Camera Live starts hidden
        names = self.save()
        self.assertEqual(sorted(name.split("_", 4)[4] for name in names),
                         ["TrackingDiff_Avg4_Bin2x2_Mean1x1_float32.tiff", "raw16.tiff"])
        self.assertIn("no display image: Camera Live has not drawn a frame yet",
                      self.app.k6221_status_var.get())
        raw_name = next(name for name in names if name.endswith("raw16.tiff"))
        np.testing.assert_array_equal(np.asarray(Image.open(self.folder / raw_name)),
                                      self.app.camera_window.latest_frame.raw)

    def test_hidden_camera_live_poll_then_save(self):
        """The real TkCameraWindow._poll with the window hidden: a new frame
        replaces latest_frame but is not drawn, so the save leaves the
        display (still of the old frame) out and takes the new frame's raw."""
        camera = self.app.camera_window
        newer = frame(1.0, value=4000)
        newer.drift_dx = newer.drift_dy = newer.drift_response = 0.0
        frames = mock.Mock()
        frames.get_nowait.side_effect = [newer, app.queue.Empty()]
        errors = mock.Mock()
        errors.get_nowait.side_effect = app.queue.Empty()
        camera.worker = types.SimpleNamespace(frames=frames, errors=errors)
        camera.error_shown, camera.connection_announced = False, True
        camera.window_visible, camera.video_recorder = False, None
        camera.selecting_roi, camera.rendering_paused = False, False
        camera._draw, camera._ingest_roi_values = mock.Mock(), mock.Mock()
        camera._poll = mock.Mock()     # only rescheduled, never run here
        camera.app = mock.Mock()
        camera.window = mock.Mock()
        app.TkCameraWindow._poll(camera)
        self.assertIs(camera.latest_frame, newer)
        camera._draw.assert_not_called()
        camera._ingest_roi_values.assert_called_once_with(newer)
        names = self.save()
        self.assertFalse(any(name.endswith("display8.png") for name in names), names)
        raw_name = next(name for name in names if name.endswith("raw16.tiff"))
        np.testing.assert_array_equal(np.asarray(Image.open(self.folder / raw_name)), newer.raw)

    def save_display(self):
        destination = self.folder / "display.png"
        a = self.app
        a.operation_var = Var()
        with mock.patch.object(app.filedialog, "asksaveasfilename",
                               return_value=str(destination)) as ask, \
                mock.patch.object(app.messagebox, "showwarning") as warning, \
                mock.patch.object(app.messagebox, "showerror"), \
                mock.patch.object(app.messagebox, "showinfo"):
            a.save_current_display_image("MOKE")
        return destination, ask, warning

    def test_save_current_display_refuses_an_older_frame(self):
        camera = self.app.camera_window
        camera.latest_display = (np.full((3, 4), 25, dtype=np.uint8), (0, 0, 4, 3), frame(0.0, value=100))
        destination, ask, warning = self.save_display()
        warning.assert_called_once()
        self.assertIn("(it is hidden, or paused while a file is saved)", warning.call_args.args[1])
        ask.assert_not_called()
        self.assertFalse(destination.exists())
        camera.resume_rendering.assert_called_once()

    def test_save_current_display_refuses_when_never_drawn(self):
        self.app.camera_window.latest_display = None   # Camera Live starts hidden
        destination, ask, warning = self.save_display()
        self.assertIn("has not drawn a frame yet", warning.call_args.args[1])
        ask.assert_not_called()
        self.assertFalse(destination.exists())

    def test_save_current_display_of_the_latest_frame(self):
        destination, ask, warning = self.save_display()
        warning.assert_not_called()
        np.testing.assert_array_equal(np.asarray(Image.open(destination)),
                                      np.full((3, 4), 7, dtype=np.uint8))
