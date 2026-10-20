"""Super, the MOKE difference display's fourth mode (user, 2026-10-20): "the
DC is work but the current focus is lose so the edge of my device is shown. I
want to add a super mode in MOKE difference display to make sure the edge of
device is as narrow as possible!"

Super is Drift tracking plus a focus servo that moves the Cell-VSDM motor to
the lowest edge residual E of the difference image, around the motor's
position when the BG was captured, with the auto focus's limits and hooks.

- The metric: an exact copy of the BG scores (near) 0; a blurred or shifted
  copy of a step-edge pattern scores higher; a uniform offset inside a region
  (magnetic contrast) or a brightness change barely moves it.
- The walk: the minimum of a synthetic E curve inside the ±10 µm window,
  never outside it or the motor's limits; Keep in focus steps towards lower E.
- The manager: the reference is taken when the BG arrives (and unknown when
  the motor was not still); refusals; a failed move stops it; a cancel and a
  recaptured BG send no further move; stale frames are ignored; no motor
  command on the Tk thread; the other three modes are unchanged.

Window-free: the fakes of test_auto_focus (a stand-in motor with the real
MotorApp methods), a synthetic Hall cross for the camera; the test's main
thread plays the Tk thread."""
import math
import queue
import threading
import time
import types
import unittest
from unittest import mock

import numpy as np

from test_ppms_motor_debug import app
from test_auto_focus import COUNTS_PER_UM, ManagerCase, MotorApp, Var, blurred, focus_app, motor

C = app.CombinedControlApp
_no_tk = mock.patch.object(app.tk, "Tk", side_effect=AssertionError("a test reached a real Tk window"))


def setUpModule():
    _no_tk.start()


def tearDownModule():
    _no_tk.stop()


def hall(size=192, low=1000.0, high=3000.0):
    """A Hall cross with four pads: the device of the user's screenshot."""
    image = np.full((size, size), low)
    c, arm, inset = size // 2, max(6, size // 16), size // 8
    image[c - arm:c + arm, inset:size - inset] = high
    image[inset:size - inset, c - arm:c + arm] = high
    pad = size // 6
    for y, x in ((inset, inset), (inset, size - inset - pad), (size - inset - pad, inset),
                 (size - inset - pad, size - inset - pad)):
        image[y:y + pad, x:x + pad] = high
    return image


def gaussian(image, sigma):
    """A separable Gaussian blur (numpy only)."""
    radius = int(math.ceil(4 * sigma))
    x = np.arange(-radius, radius + 1)
    kernel = np.exp(-x * x / (2.0 * sigma * sigma))
    kernel /= kernel.sum()
    padded = np.pad(image, radius, mode="reflect")
    rows = sum(k * padded[:, i:i + image.shape[1]] for i, k in enumerate(kernel))
    return sum(k * rows[i:i + image.shape[0], :] for i, k in enumerate(kernel))


def fourier_shift(image, dx):
    kx = np.fft.fftfreq(image.shape[1])
    return np.real(np.fft.ifft2(np.fft.fft2(image) * np.exp(-2j * np.pi * kx * dx)[None, :]))


class NumpyPatch(unittest.TestCase):
    def setUp(self):
        patcher = mock.patch.object(app, "np", np)
        patcher.start()
        self.addCleanup(patcher.stop)


# ---------------------------------------------------------------------------
# The metric
# ---------------------------------------------------------------------------

class EdgeResidualTests(NumpyPatch):
    def setUp(self):
        super().setUp()
        self.rng = np.random.default_rng(11)
        self.scene = gaussian(hall(), 0.7)                     # the optics' own blur
        self.background = self.scene + self.rng.normal(0.0, 3.0, self.scene.shape)
        self.reference = app.EdgeResidualReference(self.background)

    def residual(self, frame):
        return app.edge_residual(frame - self.background, self.reference)

    def noisy(self, image):
        return image + self.rng.normal(0.0, 3.0, image.shape)

    def test_an_exact_copy_scores_lowest_and_zero_without_noise(self):
        self.assertEqual(app.edge_residual(np.zeros_like(self.background), self.reference), 0.0)
        exact = self.residual(self.noisy(self.scene))
        self.assertLess(exact, 0.02, "only the noise is left")
        for sigma in (0.6, 1.0, 2.0, 4.0):
            with self.subTest(blur=sigma):
                self.assertGreater(self.residual(self.noisy(gaussian(self.scene, sigma))), 5 * exact)
        for dx in (0.5, 1.0):
            with self.subTest(shift=dx):
                self.assertGreater(self.residual(self.noisy(fourier_shift(self.scene, dx))), 3 * exact)

    def test_it_grows_with_the_defocus(self):
        values = [self.residual(self.noisy(gaussian(self.scene, sigma))) for sigma in (0.3, 0.6, 1.0, 2.0, 4.0)]
        self.assertEqual(values, sorted(values), values)

    def test_a_uniform_offset_inside_a_region_barely_moves_it(self):
        """Magnetic contrast: a domain of +10 counts (over 3 counts of noise)."""
        frame = self.noisy(self.scene)
        exact = self.residual(frame)
        size = self.scene.shape[0]
        c, arm = size // 2, size // 16
        for name, region in (
            ("left arm", (slice(c - arm, c + arm), slice(size // 8, c))),
            ("square across the cross", (slice(60, 130), slice(60, 130))),
            ("whole device", self.scene > 2000.0),
        ):
            with self.subTest(region=name):
                domain = np.zeros_like(self.scene)
                domain[region] = 10.0
                self.assertLess(self.residual(frame + domain), 1.15 * exact)

    def test_a_brightness_change_and_a_tiny_registration_error_are_not_focus(self):
        exact = self.residual(self.noisy(self.scene))
        self.assertLess(self.residual(self.noisy(1.02 * self.scene + 15.0)), 1.15 * exact)
        # A 0.05 px leftover registration moves E far less than a 0.6 px blur.
        jitter = self.residual(self.noisy(fourier_shift(self.scene, 0.05)))
        blur = self.residual(self.noisy(gaussian(self.scene, 0.6)))
        self.assertLess(jitter, 2 * exact)
        self.assertGreater(blur, 5 * jitter)

    def test_scale_free(self):
        frame = self.noisy(gaussian(self.scene, 1.0))
        scaled = app.EdgeResidualReference(4.0 * self.background)
        self.assertAlmostEqual(app.edge_residual(4.0 * (frame - self.background), scaled),
                               self.residual(frame), places=9)

    def test_the_display_binning_and_a_large_frame(self):
        frame = self.noisy(gaussian(self.scene, 1.5))
        shown = app.mean_filter_full_size(app.spatial_bin_average_full_size(
            (frame - self.background).astype(np.float32), 2), 2)
        binned = app.EdgeResidualReference(self.background, 2, 2)
        self.assertEqual(binned.factor, 2)
        exact = app.mean_filter_full_size(app.spatial_bin_average_full_size(
            (self.noisy(self.scene) - self.background).astype(np.float32), 2), 2)
        self.assertGreater(app.edge_residual(shown, binned), 3 * app.edge_residual(exact, binned))
        big = np.tile(hall(256), (9, 9))[:2100, :2100]           # a 2100 x 2100 frame
        self.assertEqual(app.EdgeResidualReference(big).factor, 3, "about 1024 px per side")
        self.assertEqual(app.EdgeResidualReference(big, 4).factor, 4, "a multiple of the binning")

    def test_the_drift_margin_and_bad_inputs(self):
        rows, cols = self.reference.window(0.0)
        drift_rows, drift_cols = self.reference.window(30.0)
        self.assertEqual(drift_rows.start - rows.start, 30)
        self.assertTrue(math.isfinite(app.edge_residual(self.noisy(self.scene) - self.background,
                                                        self.reference, 30.0)))
        with self.assertRaisesRegex(ValueError, "no edges"):
            app.EdgeResidualReference(np.full((192, 192), 800.0))
        with self.assertRaisesRegex(ValueError, "too small"):
            app.EdgeResidualReference(hall(32))
        with self.assertRaisesRegex(ValueError, "differ in size"):
            app.edge_residual(np.zeros((100, 100)), self.reference)
        self.assertEqual(app.super_residual_text(0.0183), "1.8 %")
        self.assertEqual(app.super_residual_text(0.42), "42 %")
        self.assertEqual(app.super_residual_text(None), "--")


# ---------------------------------------------------------------------------
# The walk: SuperFocusSearch on a synthetic E curve
# ---------------------------------------------------------------------------

class ResidualCurve:
    """E as a function of the position, lowest at ``best_um``; moves recorded
    with the direction they arrived from."""

    def __init__(self, start_um, best_um, width_um=2.0, floor=0.01, max_um=100_000.0):
        self.position, self.best, self.width, self.floor, self.max = start_um, best_um, width_um, floor, max_um
        self.moves, self.scored = [], []
        self.last_up = None

    def move(self, target):
        self.last_up = target > self.position
        self.moves.append(target)
        self.position = target

    def measure(self):
        self.scored.append((self.position, self.last_up))
        if self.best is None:
            return 0.3
        return self.floor + 0.5 * (1.0 - math.exp(-((self.position - self.best) / self.width) ** 2))

    def engine(self, reference_um, cancelled=lambda: False):
        return app.SuperFocusSearch(reference_um, self.position, self.max, self.move, self.measure, cancelled)


class SuperSearchTests(unittest.TestCase):
    def assert_in_window(self, curve, reference):
        for target in curve.moves:
            self.assertLessEqual(abs(target - reference), 10.0 + 1e-9, target)
            self.assertGreaterEqual(target, 0.0)
            self.assertLessEqual(target, curve.max)
            steps = (target - reference) / 0.5
            self.assertAlmostEqual(steps, round(steps), places=6, msg="on the 0.5 µm grid")

    def assert_backlash(self, curve, engine):
        bottom = engine.position(engine.k_min)
        for position, up in curve.scored[1:]:
            if up is not None and position != bottom:
                self.assertTrue(up, f"{position} µm was scored after a downward move")

    def test_the_minimum_is_found_on_either_side(self):
        for best in (7251.5, 7246.0, 7250.0, 7259.5, 7240.5):
            with self.subTest(best=best):
                curve = ResidualCurve(7250.0, best)
                engine = curve.engine(7250.0)
                result = engine.search()
                self.assertTrue(result.peak_found)
                self.assertEqual((result.best_um, curve.position), (best, best))
                self.assertAlmostEqual(app.SuperFocusSearch.residual(result.best_score), 0.01)
                self.assertGreater(app.SuperFocusSearch.residual(result.start_score), 0.01 - 1e-12)
                self.assert_in_window(curve, 7250.0)
                self.assert_backlash(curve, engine)

    def test_a_minimum_beyond_the_window_stops_at_its_edge(self):
        for best, edge in ((7266.0, 7260.0), (7231.0, 7240.0)):
            with self.subTest(best=best):
                curve = ResidualCurve(7250.0, best, width_um=6.0)
                result = curve.engine(7250.0).search()
                self.assertEqual((result.best_um, result.at_window_edge), (edge, True))
                self.assert_in_window(curve, 7250.0)

    def test_never_below_zero_or_above_the_motor_maximum(self):
        for start, best in ((2.0, -5.0), (99.0, 120.0)):
            with self.subTest(start=start):
                curve = ResidualCurve(start, best, width_um=6.0, max_um=100.0)
                curve.engine(start).search()
                self.assert_in_window(curve, start)
                self.assertIn(min(curve.moves) if best < 0 else max(curve.moves), (0.0, 100.0))

    def test_no_clear_minimum_sweeps_once_and_returns_to_the_start(self):
        curve = ResidualCurve(7250.0, None)
        result = curve.engine(7250.0).search()
        self.assertFalse(result.peak_found)
        self.assertEqual((result.best_um, curve.position), (7250.0, 7250.0))
        self.assertEqual({p for p, _ in curve.scored}, {7240.0 + 0.5 * k for k in range(41)})

    def test_keep_steps_towards_lower_residual(self):
        for best in (7251.0, 7249.0):
            with self.subTest(best=best):
                curve = ResidualCurve(7250.0, best, width_um=3.0)
                engine = curve.engine(7250.0)
                result = engine.check()
                self.assertEqual((result.best_um, curve.position), (best, best))
                self.assert_backlash(curve, engine)

    def test_keep_stays_for_a_change_under_its_threshold(self):
        # 4 % lower E one step up: under SUPER_MIN_GAIN (5 %), so it stays.
        values = {7249.5: 1.0, 7250.0: 1.0, 7250.5: 0.96}
        curve = ResidualCurve(7250.0, None)
        curve.measure = lambda: values[curve.position]
        result = app.SuperFocusSearch(7250.0, 7250.0, 100_000.0, curve.move, curve.measure, lambda: False).check()
        self.assertEqual((result.best_um, curve.position), (7250.0, 7250.0))

    def test_a_cancel_stops_before_the_next_move(self):
        curve = ResidualCurve(7250.0, 7255.0)
        engine = curve.engine(7250.0, cancelled=lambda: len(curve.moves) >= 3)
        with self.assertRaises(app.FocusCancelled):
            engine.search()
        self.assertEqual(len(curve.moves), 3)

    def test_an_unusable_residual_is_a_failure(self):
        curve = ResidualCurve(7250.0, 7251.0)
        curve.measure = lambda: math.nan
        with self.assertRaises(app.FocusError):
            curve.engine(7250.0).search()


# ---------------------------------------------------------------------------
# The manager in Super
# ---------------------------------------------------------------------------

class SuperCamera:
    """Camera Live in drift tracking: a new displayed difference on every
    read, begun now, of a Hall cross blurred by the distance of the motor's
    physical position from ``focus_um`` (where the BG's focus now is)."""

    def __init__(self, controller, focus_um=7251.5, size=128):
        self.controller, self.focus_um = controller, focus_um
        self.rng = np.random.default_rng(3)
        self.scene = hall(size)
        self.background = self.scene + self.rng.normal(0.0, 2.0, self.scene.shape)
        self.serial, self.mode = 1, "tracking"
        self.video_recorder = None
        self.worker = types.SimpleNamespace(background_capture_requested=False, background_mode="tracking",
                                            background=self.background)
        self.roi = None              # Super judges the whole image: no ROI needed
        self.frozen = None

    def frame(self, start=None):
        passes = int(round(2.0 * abs(self.controller.position_um - self.focus_um)))
        image = blurred(self.scene, passes) + self.rng.normal(0.0, 2.0, self.scene.shape)
        now = time.monotonic() if start is None else start
        return types.SimpleNamespace(
            raw=image, difference=image - self.background, display_difference=image - self.background,
            background_raw=self.background, background_serial=self.serial, background_mode=self.mode,
            window_start_monotonic=now, exposure_start_monotonic=now, exposure_ms=1.0,
            temporal_average_count=1, spatial_binning=1, mean_filter_size=1, drift_dx=0.0, drift_dy=0.0,
        )

    @property
    def latest_frame(self):
        return self.frozen if self.frozen is not None else self.frame()


class SuperCase(ManagerCase):
    def page(self, focus_um=7251.5, super_on=True, **controller):
        m = motor(**controller)
        self.addCleanup(m.executor.shutdown, wait=False, cancel_futures=True)
        self.addCleanup(m.controller.release.set)
        camera = SuperCamera(m.controller, focus_um)
        a = focus_app(m, camera)
        focus = a.auto_focus
        focus.frame_arrived(camera.latest_frame)          # the BG arrives: motor at 7250 µm
        if super_on:
            focus.set_super(True)
        return a, m, focus

    def warned_text(self):
        return self.warned.call_args.args[1]


class ReferenceTests(SuperCase):
    def test_the_reference_is_the_motor_position_when_the_bg_arrived(self):
        a, m, focus = self.page(super_on=False)
        self.assertEqual(focus.background_focus, (1, 7250.0, round(7250.0 * COUNTS_PER_UM), None))
        m.command_counts = round(7253.0 * COUNTS_PER_UM)     # moved after the capture
        m.controller.position_um = 7253.0
        focus.set_super(True)
        self.assertEqual((focus.super_reference_um, focus.super_serial), (7250.0, 1),
                         "the BG's focus, not the position now")
        self.assertIn("reference 7250 µm (the BG's focus)", focus.status_var.get())
        self.assertTrue(any("Super focus reference: 7250 µm" in line for line in a.logs))

    def test_recapture_in_super_records_a_new_reference_and_unticks_keep(self):
        a, m, focus = self.page()
        focus.keep_var.set(True)
        focus.keep_changed()
        self.assertEqual((focus.keep_on, focus.reference_um), (True, 7250.0))
        focus.note_capture_request()                          # Recapture BG
        self.assertFalse(focus.keep_on, "Recapture BG in Super stops Keep in focus")
        self.assertFalse(focus.keep_var.get())
        a.camera_window.serial = 2
        focus.frame_arrived(a.camera_window.latest_frame)
        self.assertEqual((focus.super_reference_um, focus.super_serial), (7250.0, 2))
        # Moved on the motor page, then Recapture BG: the window follows.
        m.command_counts = round(7252.0 * COUNTS_PER_UM)
        focus.note_capture_request()
        a.camera_window.serial = 3
        focus.frame_arrived(a.camera_window.latest_frame)
        self.assertEqual(focus.super_serial, 3)
        self.assertAlmostEqual(focus.super_reference_um, 7252.0, places=2)   # whole counts
        self.assertIn("reference 7252 µm", focus.status_var.get())

    def test_the_focus_is_unknown_when_the_motor_was_not_still(self):
        cases = (
            ("busy", "busy with a command"),
            ("pending", "busy with a command"),
            ("not homed", "not connected and homed"),
            ("moved since the request", "moved while the BG was being captured"),
            ("settling", "still settling"),
            ("no motor", "not attached"),
        )
        for what, text in cases:
            with self.subTest(what=what):
                a, m, focus = self.page()
                if what == "busy":
                    m.command_busy = True
                elif what == "pending":
                    m.pending_target = (1, "x")
                elif what == "not homed":
                    m.homed = False
                elif what == "moved since the request":
                    focus.note_capture_request()
                    m.command_counts += 51
                elif what == "settling":
                    focus.frames_after = time.monotonic() + 100.0
                elif what == "no motor":
                    a.motor_app = None
                a.camera_window.serial = 2
                focus.frame_arrived(a.camera_window.latest_frame)
                self.assertIsNone(focus.super_reference_um)
                self.assertIn(text, focus.status_var.get())
                m.command_busy, m.pending_target, m.homed = False, None, True
                if a.motor_app is not None:
                    self.assertIn("BG's focus is unknown", focus.refusal())
                    self.warned.reset_mock()
                    focus.toggle_search()
                    self.assertIsNone(focus.running_kind)
                    self.assertIn("Recapture BG", self.warned_text())
                    self.assertEqual(m.controller.writes, [])

    def test_entering_super_without_a_bg_waits_for_it(self):
        a, m, focus = self.page(super_on=False)
        focus.set_super(True, background_pending=True)
        self.assertIsNone(focus.super_reference_um)
        self.assertIn("waiting for the BG", focus.status_var.get())
        a.camera_window.serial = 5
        focus.frame_arrived(a.camera_window.latest_frame)
        self.assertEqual(focus.super_reference_um, 7250.0)

    def test_a_new_camera_worker_still_gives_a_new_serial(self):
        first, second = app.next_background_serial(), app.next_background_serial()
        self.assertGreater(second, first)


class SuperSearchManagerTests(SuperCase):
    def test_super_focus_moves_back_to_the_bgs_focus(self):
        a, m, focus = self.page(focus_um=7251.5)
        button = mock.Mock()
        focus.buttons.append(button)
        focus.set_camera_ready(True)
        self.assertEqual(button.configure.call_args.kwargs["text"], "Super focus")
        self.search(a, m, focus)
        self.assertAlmostEqual(m.controller.position_um, 7251.5, places=2)
        status = focus.status_var.get()
        self.assertTrue(status.startswith("Super: edge residual "), status)
        self.assertIn("focus +1.5 µm from the BG's focus", status)
        self.assertLess(focus.super_live_residual, focus.super_start_residual)
        targets = [counts / COUNTS_PER_UM for counts in m.controller.writes]
        self.assertTrue(all(abs(t - 7250.0) <= 10.0 + 0.01 for t in targets))
        self.assertTrue(any("Super focus move: absolute" in line for line in a.logs))
        self.assertIn("the whole image; the ROI is not used", self.asked.call_args.args[1])
        self.assertIn("Edge", focus.super_overlay_text())
        self.assertIn("focus +1.5 µm", focus.super_detail_text())

    def test_the_window_is_around_the_bgs_focus(self):
        a, m, focus = self.page(focus_um=7266.0)
        self.search(a, m, focus)
        targets = [counts / COUNTS_PER_UM for counts in m.controller.writes]
        self.assertLessEqual(max(targets), 7260.0 + 0.01)
        self.assertIn("at the window edge", focus.status_var.get())
        # From +10 µm a second search still may not pass the BG's focus + 10 µm.
        m.controller.writes.clear()
        self.search(a, m, focus)
        self.assertLessEqual(max(counts / COUNTS_PER_UM for counts in m.controller.writes or [0]), 7260.01)

    def test_no_motor_command_on_the_tk_thread(self):
        a, m, focus = self.page()
        submits = []
        real_submit = m.submit_focus_target

        def submit(*args):
            submits.append(threading.current_thread())
            return real_submit(*args)

        m.submit_focus_target = submit
        self.search(a, m, focus)
        tk_thread = threading.current_thread()
        self.assertTrue(submits and all(thread is tk_thread for thread in submits))
        for thread in m.controller.threads:
            self.assertTrue(thread.name.startswith("motor-command"), thread.name)
        self.assertTrue(all(thread is tk_thread for thread in focus.status_var.threads))

    def test_refusals(self):
        cases = (
            ("motor", "not attached"),
            ("safety", "safety tick"),
            ("busy", "busy"),
            ("plain", "drift-tracking difference"),
            ("off grid", "grid"),
            ("scan", "a scan is running"),
            ("bg changing", "being recaptured"),
        )
        for what, text in cases:
            with self.subTest(what=what):
                a, m, focus = self.page()
                if what == "motor":
                    a.motor_app = None
                elif what == "safety":
                    m.safety_var.value = False
                elif what == "busy":
                    m.command_busy = True
                elif what == "plain":
                    a.camera_window.mode = "plain"
                elif what == "off grid":
                    m.command_counts = round(7250.3 * COUNTS_PER_UM)
                elif what == "scan":
                    a.magnet_scan_running = True
                elif what == "bg changing":
                    a.camera_window.serial = 9
                self.warned.reset_mock()
                focus.toggle_search()
                self.warned.assert_called_once()
                self.assertIn(text, self.warned_text())
                self.assertIsNone(focus.running_kind)
                self.assertEqual(m.controller.writes, [])
                if what != "scan":
                    focus.keep_var.set(True)
                    focus.keep_changed()
                    self.assertFalse(focus.keep_on)

    def test_a_failed_move_stops_and_unticks(self):
        a, m, focus = self.page(fail_at=3)
        focus.keep_var.set(True)
        focus.keep_changed()
        self.search(a, m, focus)
        self.assertEqual(len(m.controller.writes), 3, "no move after the failed one")
        self.assertFalse(focus.keep_on)
        self.assertIn("failed", focus.status_var.get())
        self.assertEqual([kind for kind, _title, _text in a.dialogs], ["showwarning"])
        self.assertIn("Super focus stopped", a.dialogs[0][2])

    def test_stop_focus_sends_no_more_moves(self):
        a, m, focus = self.page(focus_um=7258.0)
        pressed = []

        def press_stop():
            if len(m.controller.writes) >= 4 and not pressed:
                pressed.append(len(m.controller.writes))
                focus.toggle_search()
        self.search(a, m, focus, each=press_stop)
        time.sleep(0.05)
        self.pump(a, m, lambda: a.ui.empty())
        self.assertEqual(len(m.controller.writes), pressed[0])
        self.assertIn("stopped", focus.status_var.get())
        self.assertEqual(a.dialogs, [])

    def test_a_recaptured_bg_mid_search_stops_it(self):
        a, m, focus = self.page(focus_um=7258.0)
        sent = []

        def recapture():
            if len(m.controller.writes) >= 3 and not sent:
                sent.append(len(m.controller.writes))
                a.camera_window.serial = 2                   # [N] on Camera Live
                focus.frame_arrived(a.camera_window.latest_frame)
        self.search(a, m, focus, each=recapture)
        time.sleep(0.05)
        self.pump(a, m, lambda: a.ui.empty())
        self.assertEqual(len(m.controller.writes), sent[0])
        self.assertIsNone(focus.super_reference_um, "a BG taken while the servo moved has no known focus")
        self.assertIn(focus.super_reference_reason,
                      ("a focus search or check was running", "the focus motor was busy with a command"))
        self.assertEqual(a.dialogs, [], "a stop, not a failure")

    def test_leaving_super_cancels_and_unticks(self):
        a, m, focus = self.page()
        focus.keep_var.set(True)
        focus.keep_changed()
        focus.set_super(False, "Drift tracking chosen")
        self.assertTrue(focus.cancel_event.is_set())
        self.assertFalse(focus.keep_on)
        self.assertIsNone(focus.reference_um)
        self.assertEqual(focus.status_var.get(), "Focus: Super off; Keep in focus off")


class SuperFrameTests(SuperCase):
    def page(self, *args, **kwargs):
        a, m, focus = super().page(*args, **kwargs)
        focus.cancel_event.clear()    # as a search's claim does
        return a, m, focus

    def test_frames_whose_average_began_before_the_move_settled_are_ignored(self):
        a, m, focus = self.page()
        camera = a.camera_window
        focus.frames_after = time.monotonic() + 0.1
        # The newest raw frame is fresh, but its temporal average still holds
        # frames from before the move: not yet.
        stale = camera.frame(start=time.monotonic())
        stale.exposure_start_monotonic = time.monotonic() + 1e3
        stale.display_difference = stale.display_difference * 0.0 + 500.0 * (camera.scene > 2000)
        camera.frozen = stale
        fresh = camera.frame(start=time.monotonic() + 1e3)
        timer = threading.Timer(0.3, lambda: setattr(camera, "frozen", fresh))
        timer.start()
        self.addCleanup(timer.cancel)
        value = focus._measure_super(serial=1)
        expected = app.edge_residual(fresh.display_difference, focus._edge_reference(fresh))
        self.assertAlmostEqual(value, expected)

    def test_a_frame_of_another_bg_or_mode_stops_the_measurement(self):
        a, m, focus = self.page()
        a.camera_window.serial = 2
        with self.assertRaisesRegex(app.FocusCancelled, "BG was recaptured"):
            focus._measure_super(serial=1)
        a.camera_window.serial, a.camera_window.mode = 1, "plain"
        with self.assertRaisesRegex(app.FocusCancelled, "left Super"):
            focus._measure_super(serial=1)

    def test_no_fresh_frame_is_a_failure(self):
        a, m, focus = self.page()
        a.camera_window.frozen = a.camera_window.frame(start=math.nan)
        with mock.patch.object(app, "FOCUS_FRAME_TIMEOUT_S", 0.1):
            with self.assertRaisesRegex(app.FocusError, "no difference frame"):
                focus._measure_super(serial=1)

    def test_the_live_residual_is_rate_limited_and_off_the_tk_thread(self):
        a, m, focus = self.page()
        threads = []
        real = app.edge_residual

        def residual(*args, **kwargs):
            threads.append(threading.current_thread())
            return real(*args, **kwargs)

        with mock.patch.object(app, "edge_residual", residual), \
                mock.patch.object(app, "SUPER_LIVE_INTERVAL_S", 60.0):
            for _ in range(5):
                focus.frame_arrived(a.camera_window.latest_frame)
            self.pump(a, m, lambda: not focus._live_busy)
            for _ in range(5):
                focus.frame_arrived(a.camera_window.latest_frame)
        self.assertEqual(len(threads), 1, "at most one per SUPER_LIVE_INTERVAL_S")
        self.assertIsNot(threads[0], threading.current_thread())
        self.assertIsNotNone(focus.super_live_residual)
        self.assertIn("Super: edge residual", focus.super_detail_text())
        self.assertIn("whole image, the ROI is not used", focus.super_detail_text())


class SuperKeepTests(SuperCase):
    def test_the_idle_check_steps_towards_lower_residual(self):
        a, m, focus = self.page(focus_um=7251.0)
        focus.camera_ready = True
        focus.keep_var.set(True)
        focus.keep_changed()
        self.assertEqual(focus.reference_um, 7250.0)
        focus.idle_tick()
        self.assertEqual((focus.running_kind, focus.running_metric), ("check", "super"))
        self.pump(a, m, self.idle(focus))
        self.pump(a, m, lambda: a.ui.empty())
        self.assertAlmostEqual(m.controller.position_um, 7251.0, places=2)
        self.assertIn("focus +1.0 µm from the BG's focus", focus.status_var.get())

    def test_keep_takes_the_bgs_focus_as_its_window_after_a_search(self):
        a, m, focus = self.page(focus_um=7252.0)
        self.search(a, m, focus)
        focus.keep_var.set(True)
        focus.keep_changed()
        self.assertEqual(focus.reference_um, 7250.0, "the BG's focus, not the position now")
        self.assertIn("of the BG's focus 7250 µm", focus.status_var.get())

    def test_between_points_logs_the_moves_and_the_reference(self):
        a, m, focus = self.page(focus_um=7251.5)
        focus.keep_var.set(True)
        focus.keep_changed()
        a.magnet_scan_running = True
        stop = threading.Event()
        focus.before_scan("magnet_scan_recorder")
        worker = threading.Thread(target=focus.between_points, args=("magnet_scan_recorder", stop))
        worker.start()
        self.pump(a, m, lambda: not worker.is_alive())
        self.pump(a, m, lambda: a.ui.empty())
        self.assertAlmostEqual(m.controller.position_um, 7251.5, places=2)
        notes = [details["text"] for _attr, name, details in a.events if name == "note"]
        self.assertTrue(notes[0].startswith("Super focus reference: 7250 µm"), notes[0])
        self.assertTrue(any(text.startswith("Super focus move: absolute") for text in notes))
        focus_events = [details for _attr, name, details in a.events if name == "focus"]
        self.assertEqual(focus_events[0]["kind"], "super_reference")
        self.assertEqual(focus_events[-1]["metric"], "super")
        self.assertEqual(focus_events[-1]["position_um"], 7251.5)
        self.assertLess(focus_events[-1]["edge_residual"], focus_events[-1]["edge_residual_start"])


# ---------------------------------------------------------------------------
# The page: the mode radio and the other three modes
# ---------------------------------------------------------------------------

class PageModeTests(NumpyPatch):
    def page(self, worker_mode="off", background=object()):
        a = focus_app()
        worker = types.SimpleNamespace(background_mode=worker_mode, background=background,
                                       difference_contrast_state=lambda: ("AUTO", None))

        def set_mode(mode):
            worker.background_mode = mode
            return mode

        a.camera_window = types.SimpleNamespace(worker=worker, set_background_mode=mock.Mock(side_effect=set_mode))
        a.keithley_scan_running = a.keithley_manual_busy = False
        a.difference_mode_var = Var(worker_mode)
        a.difference_status_var, a.difference_detail_var = Var(""), Var("")
        a.difference_clim_status_var = Var("")
        a.difference_histogram_snapshot = None
        a.moke_roi = None
        a._difference_display_mode = types.MethodType(C._difference_display_mode, a)
        return a

    def choose(self, a, mode):
        a.difference_mode_var.set(mode)
        C.select_difference_mode(a)

    def test_super_is_drift_tracking_for_the_camera(self):
        a = self.page()
        self.choose(a, "super")
        a.camera_window.set_background_mode.assert_called_once_with("tracking")
        self.assertTrue(a.auto_focus.super_on)
        C.update_difference_status(a, "tracking", 0.02, -0.07, 0.17)
        self.assertEqual(a.difference_mode_var.get(), "super")
        self.assertTrue(a.difference_status_var.get().startswith("Super:"))
        self.assertIn("ROI is not used", a.difference_detail_var.get())

    def test_entering_super_without_a_bg_asks_for_one(self):
        a = self.page(background=None)
        self.choose(a, "super")
        self.assertIn("waiting for the BG", a.auto_focus.status_var.get())

    def test_the_other_three_modes_are_unchanged(self):
        texts = {
            "off": ("Difference mode: off", "Showing the plain live view."),
            "plain": ("Plain difference mode",
                      "The background is subtracted from the current frame; no drift correction."),
            "tracking": ("Drift-tracking difference mode",
                         "Drift dx=+0.02 px, dy=-0.07 px; match 0.17 (1 = identical)"),
        }
        for mode, (status, detail) in texts.items():
            with self.subTest(mode=mode):
                a = self.page()
                self.choose(a, "super")
                self.choose(a, mode)
                a.camera_window.set_background_mode.assert_called_with(mode)
                self.assertFalse(a.auto_focus.super_on)
                C.update_difference_status(a, mode, 0.02, -0.07, 0.17)
                self.assertEqual(a.difference_mode_var.get(), mode)
                self.assertEqual((a.difference_status_var.get(), a.difference_detail_var.get()), (status, detail))

    def test_b_on_camera_live_leaves_super(self):
        a = self.page()
        self.choose(a, "super")
        a.camera_window.worker.background_mode = "off"        # [B]: the camera worker's own toggle
        C.update_difference_status(a, "tracking", 0.0, 0.0, 1.0)   # a frame still in flight
        self.assertFalse(a.auto_focus.super_on)
        self.assertEqual(a.difference_mode_var.get(), "tracking")

    def test_no_mode_change_during_a_scan(self):
        a = self.page(worker_mode="tracking")
        a.magnet_scan_running = True
        self.choose(a, "super")
        a.camera_window.set_background_mode.assert_not_called()
        self.assertFalse(a.auto_focus.super_on)
        self.assertEqual(a.difference_mode_var.get(), "tracking")

    def test_plain_auto_focus_outside_super_still_needs_the_roi(self):
        a = self.page(worker_mode="tracking")
        self.choose(a, "super")
        self.choose(a, "tracking")
        a.motor_app = motor()
        self.addCleanup(a.motor_app.executor.shutdown, wait=False)
        a.camera_window.latest_frame = types.SimpleNamespace(raw=np.zeros((8, 8)))
        a.camera_window.roi = None
        a.camera_connected = True
        self.assertIn("ROI", a.auto_focus.refusal())
        button = mock.Mock()
        a.auto_focus.buttons.append(button)
        a.auto_focus._update_controls()
        self.assertEqual(button.configure.call_args.kwargs["text"], "Auto focus")


# ---------------------------------------------------------------------------
# The camera worker: a serial per BG
# ---------------------------------------------------------------------------

class FakePco:
    def __init__(self, frames):
        self.frames, self.index = frames, 0
        self.exposure_time = self.configuration = None

    def default_configuration(self):
        pass

    def record(self, *_args):
        pass

    def stop(self):
        pass

    def close(self):
        pass

    def wait_for_new_image(self, timeout=None):
        time.sleep(0.002)

    def image(self, *_args):
        frame = self.frames[min(self.index, len(self.frames) - 1)]
        self.index += 1
        return frame, {}


class WorkerSerialTests(NumpyPatch):
    def test_each_capture_is_a_new_serial_and_the_frame_carries_its_bg(self):
        frames = [np.full((64, 64), 1000 + k, dtype=np.uint16) for k in range(400)]
        camera = FakePco(frames)
        with mock.patch.object(app, "pco", types.SimpleNamespace(Camera=lambda: camera), create=True):
            worker = app.CameraAcquisitionWorker(lambda _text: None)
            worker.set_background_mode("tracking")
            worker.start()
            try:
                first = self.next_frame(worker, lambda f: f.background_serial > 0)
                self.assertIs(first.background_raw, worker.background)
                worker.capture_background()
                second = self.next_frame(worker, lambda f: f.background_serial > first.background_serial)
                self.assertIsNot(second.background_raw, first.background_raw)
                worker.set_background_mode("off")
                off = self.next_frame(worker, lambda f: f.background_mode == "off")
                self.assertEqual((off.background_serial, off.background_raw), (0, None))
            finally:
                worker.stop()

    def next_frame(self, worker, accept, timeout=5.0):
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            try:
                frame = worker.frames.get(timeout=0.1)
            except queue.Empty:
                continue
            if accept(frame):
                return frame
        self.fail("no such frame")


if __name__ == "__main__":
    unittest.main()
