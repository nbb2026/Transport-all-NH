"""Auto focus with the Cell-VSDM motor on the MOKE difference display (user,
2026-10-20): "add a auto focus function ... In MOKE magnet and CIDWM module.
the range should be less than 10um change in absolute position and step is
0.5um", "change the motor position to realize it".

- The score: normalised variance of the Laplacian of the whole image.
- The search and the Keep in focus check: only reference + k x 0.5 µm,
  |target - reference| <= 10 µm, 0 <= target <= MAX_POSITION_UM; every
  scored position reached moving up (backlash).
- The manager: refusals; a failed or unconfirmed move stops it and unticks
  Keep in focus; a cancel sends no further move; frames begun before a move
  had settled are ignored; motor writes only on the motor's executor, sent
  from the Tk thread; the between-points hook never runs while a point is
  acquiring; focus_position_um is the last CSV column of MOKE magnet,
  MOKE-24 and CIDWM.

Window-free: fake motor (the real MotorApp methods on a stand-in), fake
camera; the test's main thread plays the Tk thread."""
import math
import queue
import threading
import time
import types
import unittest
from concurrent.futures import ThreadPoolExecutor
from unittest import mock

import numpy as np

from test_ppms_motor_debug import app

MotorApp, _motor_self_test = app.create_motor_subsystem()
C = app.CombinedControlApp
COUNTS_PER_UM = 102.4

_no_tk = mock.patch.object(app.tk, "Tk", side_effect=AssertionError("a test reached a real Tk window"))


def setUpModule():
    _no_tk.start()


def tearDownModule():
    _no_tk.stop()


class Var:
    """A Tk variable stand-in that records the threads that set it."""

    def __init__(self, value=None):
        self.value, self.threads = value, []

    def get(self):
        return self.value

    def set(self, value):
        self.threads.append(threading.current_thread())
        self.value = value


def textured(amplitude, size=48, mean=1000.0):
    """A checkerboard of the given contrast on a flat level."""
    board = np.indices((size, size)).sum(axis=0) % 2 * 2.0 - 1.0
    return mean + amplitude * board


def blurred(image, passes):
    out = np.asarray(image, dtype=np.float64)
    for _ in range(passes):
        out = (out + np.roll(out, 1, 0) + np.roll(out, -1, 0) + np.roll(out, 1, 1) + np.roll(out, -1, 1)) / 5.0
    return out


class NumpyPatch(unittest.TestCase):
    def setUp(self):
        patcher = mock.patch.object(app, "np", np)
        patcher.start()
        self.addCleanup(patcher.stop)


class SharpnessTests(NumpyPatch):
    def test_a_sharper_image_scores_higher(self):
        rng = np.random.default_rng(1)
        sharp = 1000.0 + 300.0 * rng.random((200, 300))
        scores = [app.focus_sharpness(blurred(sharp, passes)) for passes in (0, 1, 3, 8)]
        self.assertEqual(scores, sorted(scores, reverse=True))
        self.assertGreater(scores[0], 3 * scores[-1])

    def test_scale_free(self):
        rng = np.random.default_rng(2)
        image = 500.0 + 200.0 * rng.random((128, 128))
        self.assertAlmostEqual(app.focus_sharpness(3.0 * image) / app.focus_sharpness(image), 1.0, places=9)
        counts = image.astype(np.uint16)                  # a camera's raw frame
        self.assertAlmostEqual(app.focus_sharpness(counts * 2) / app.focus_sharpness(counts), 1.0, places=9)

    def test_a_large_image_is_downsampled_and_a_flat_or_dark_one_is_zero(self):
        big = np.tile(textured(100.0, size=64), (40, 40)).astype(np.uint16)   # 2560 x 2560
        self.assertGreater(app.focus_sharpness(big), 0.0)
        self.assertEqual(app.focus_sharpness(np.full((64, 64), 700.0)), 0.0)
        self.assertEqual(app.focus_sharpness(np.zeros((64, 64))), 0.0)
        with self.assertRaises(ValueError):
            app.focus_sharpness(np.ones(10))

    def test_only_the_roi_counts(self):
        """User, 2026-10-20: "not full screen focus but ROI selected"."""
        rng = np.random.default_rng(3)
        image = np.full((100, 100), 500.0)
        image[60:90, 60:90] += 300.0 * rng.random((30, 30))     # sharp texture outside the ROI
        roi = {"cx": 25.0, "cy": 25.0, "w": 30.0, "h": 30.0, "angle": 0.0}
        self.assertGreater(app.focus_sharpness(image), 0.0)
        self.assertEqual(app.focus_sharpness(image, roi), 0.0, "the flat ROI scores 0")
        image[10:40, 10:40] += 300.0 * rng.random((30, 30))     # now texture inside it
        inside = app.focus_sharpness(image, roi)
        self.assertGreater(inside, 0.0)
        image[60:90, 60:90] = 500.0                              # the outside no longer matters
        self.assertAlmostEqual(app.focus_sharpness(image, roi), inside, places=12)
        rotated = dict(roi, angle=30.0)
        self.assertGreater(app.focus_sharpness(image, rotated), 0.0)
        with self.assertRaises(ValueError):
            app.focus_sharpness(image, {"cx": 500.0, "cy": 500.0, "w": 10.0, "h": 10.0, "angle": 0.0})


class Curve:
    """A fake focus stage for FocusSearch: the score is a function of the
    position; every move is recorded with the direction it arrived from."""

    def __init__(self, start_um, peak_um, width_um=2.0, floor=0.05, max_um=100_000.0):
        self.position, self.peak, self.width, self.floor, self.max = start_um, peak_um, width_um, floor, max_um
        self.moves, self.scored = [], []      # (target), (position, arrived_moving_up)
        self.last_up = None

    def move(self, target):
        self.last_up = target > self.position
        self.moves.append(target)
        self.position = target

    def measure(self):
        self.scored.append((self.position, self.last_up))
        if self.peak is None:
            return 1.0
        return self.floor + math.exp(-((self.position - self.peak) / self.width) ** 2)

    def engine(self, reference_um, cancelled=lambda: False):
        return app.FocusSearch(reference_um, self.position, self.max, self.move, self.measure, cancelled)


class SearchTests(unittest.TestCase):
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

    def test_the_peak_is_found_on_either_side(self):
        for peak in (7253.5, 7246.0, 7250.0, 7259.5, 7240.5):
            with self.subTest(peak=peak):
                curve = Curve(7250.0, peak)
                engine = curve.engine(7250.0)
                result = engine.search()
                self.assertTrue(result.peak_found)
                self.assertEqual(result.best_um, peak)
                self.assertEqual(curve.position, peak)
                self.assert_in_window(curve, 7250.0)
                self.assert_backlash(curve, engine)
                self.assertLessEqual(len({p for p, _ in curve.scored}), 41)

    def test_a_peak_beyond_the_window_stops_at_its_edge(self):
        for peak, edge in ((7265.0, 7260.0), (7232.0, 7240.0)):
            with self.subTest(peak=peak):
                curve = Curve(7250.0, peak, width_um=6.0)
                result = curve.engine(7250.0).search()
                self.assertEqual((result.best_um, result.at_window_edge), (edge, True))
                self.assert_in_window(curve, 7250.0)

    def test_no_peak_sweeps_the_window_once_and_returns_to_start(self):
        curve = Curve(7250.0, None)
        result = curve.engine(7250.0).search()
        self.assertFalse(result.peak_found)
        self.assertEqual((result.best_um, curve.position), (7250.0, 7250.0))
        self.assert_in_window(curve, 7250.0)
        self.assertEqual({p for p, _ in curve.scored}, {7240.0 + 0.5 * k for k in range(41)})

    def test_never_below_zero_or_above_the_motor_maximum(self):
        curve = Curve(2.0, None, max_um=100.0)
        curve.engine(2.0).search()
        self.assertEqual(min(curve.moves), 0.0)
        self.assert_in_window(curve, 2.0)
        curve = Curve(99.0, None, max_um=100.0)
        curve.engine(99.0).search()
        self.assertEqual(max(curve.moves), 100.0)
        self.assert_in_window(curve, 99.0)

    def test_the_window_is_the_reference_not_the_start(self):
        # A second search from +8 µm may still only reach reference + 10 µm.
        curve = Curve(7258.0, 7275.0, width_um=8.0)
        result = curve.engine(7250.0).search()
        self.assertEqual(result.best_um, 7260.0)
        self.assert_in_window(curve, 7250.0)

    def test_off_grid_or_outside_the_window_is_refused(self):
        with self.assertRaisesRegex(app.FocusError, "grid"):
            Curve(7250.3, 7251.0).engine(7250.0)
        with self.assertRaisesRegex(app.FocusError, "outside"):
            Curve(7261.0, 7251.0).engine(7250.0)

    def test_a_cancel_stops_before_the_next_move(self):
        curve = Curve(7250.0, 7255.0)
        engine = curve.engine(7250.0, cancelled=lambda: len(curve.moves) >= 3)
        with self.assertRaises(app.FocusCancelled):
            engine.search()
        self.assertEqual(len(curve.moves), 3)


class CheckTests(unittest.TestCase):
    def test_it_steps_towards_the_sharper_side(self):
        for peak in (7252.0, 7248.0):
            with self.subTest(peak=peak):
                curve = Curve(7250.0, peak, width_um=3.0)
                engine = curve.engine(7250.0)
                result = engine.check()
                self.assertEqual((result.best_um, curve.position), (peak, peak))
                self.assertFalse(result.at_window_edge)
                SearchTests.assert_backlash(self, curve, engine)

    def test_in_focus_it_scores_three_and_comes_back(self):
        curve = Curve(7250.0, 7250.0)
        result = curve.engine(7250.0).check()
        self.assertEqual(result.best_um, 7250.0)
        self.assertEqual(sorted({p for p, _ in curve.scored}), [7249.5, 7250.0, 7250.5])
        self.assertEqual(curve.moves, [7250.5, 7248.5, 7249.5, 7250.0], "up, below, up, back up")

    def test_it_stops_at_the_window_edge(self):
        curve = Curve(7259.0, 7280.0, width_um=10.0)
        result = curve.engine(7250.0).check()
        self.assertEqual((result.best_um, result.at_window_edge), (7260.0, True))
        self.assertLessEqual(max(curve.moves), 7260.0)


# ---------------------------------------------------------------------------
# The manager, with the real MotorApp methods on a stand-in motor
# ---------------------------------------------------------------------------

class Controller:
    """The motor's MotorController: records each target write and its thread."""

    def __init__(self, position_um, fail_at=None, hang_at=None):
        self.connected = True
        self.position_um = position_um
        self.writes, self.threads = [], []
        self.fail_at, self.hang_at = fail_at, hang_at
        self.release = threading.Event()

    def write_target_counts(self, counts, description):
        self.threads.append(threading.current_thread())
        self.writes.append(counts)
        if len(self.writes) == self.fail_at:
            raise RuntimeError("Read timeout: no reply")
        if len(self.writes) == self.hang_at:
            self.release.wait(5.0)
        self.position_um = counts / COUNTS_PER_UM


def motor(position_um=7250.0, **controller):
    m = types.SimpleNamespace()
    m.controller = Controller(position_um, **controller)
    m.initialized = m.homed = True
    m.closing = m.command_busy = m.init_off_busy = False
    m.safety_var = Var(True)
    m.command_counts = round(position_um * COUNTS_PER_UM)
    m.pending_target = None
    m.abs_counts_per_um, m.max_position_um = COUNTS_PER_UM, 100_000.0
    m.ui_queue = queue.Queue()
    m.executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="motor-command")
    m.motion_stop_listeners = []
    m.poll_pause = threading.Event()
    m.bottom_status_var = m.command_position_var = Var()
    m.log = lambda text: None
    m.root = None
    m._update_controls = lambda: None

    def set_busy(busy, text=None):
        m.command_busy = busy

    m._set_busy = set_busy
    for name in ("focus_move_refusal", "submit_focus_target", "_submit", "_finish_task", "_notify_motion_stop"):
        setattr(m, name, types.MethodType(getattr(MotorApp, name), m))
    m._focus_travel_s = lambda delta: 0.0
    return m


class Camera:
    """Camera Live's latest_frame: a new frame on every read, begun now,
    whose sharpness peaks at ``focus_um`` of the controller's position."""

    def __init__(self, controller, focus_um=7252.0):
        self.controller, self.focus_um = controller, focus_um
        self.video_recorder = None
        self.worker = types.SimpleNamespace(background_capture_requested=False)
        # The auto focus scores inside the camera ROI (user, 2026-10-20).
        self.roi = {"cx": 32.0, "cy": 32.0, "w": 40.0, "h": 40.0, "angle": 0.0}

    @property
    def latest_frame(self):
        defocus = (self.controller.position_um - self.focus_um) / 2.0
        return types.SimpleNamespace(
            raw=textured(5.0 + 300.0 * math.exp(-defocus * defocus)),
            exposure_start_monotonic=time.monotonic(), exposure_ms=1.0,
        )


def focus_app(m=None, camera=None):
    a = types.SimpleNamespace()
    a.ui = queue.Queue()
    a.post_ui = a.ui.put
    a.logs, a.dialogs, a.events = [], [], []
    a.thread_log = a.logs.append
    a.closing = False
    a.root = types.SimpleNamespace(after=lambda ms, callback: "after")
    a.motor_app = m
    a.camera_window = camera
    a.camera_connected = camera is not None
    a.magnet_scan_running = a.keithley_scan_running = a.k6221_scan_running = False
    a.magnet_scan_collecting = a.keithley_collecting = False
    a._dialog_after_drain = lambda kind, title, text: a.dialogs.append((kind, title, text))
    a._scan_log = lambda attribute, name, **details: a.events.append((attribute, name, details))
    a.auto_focus = app.AutoFocusManager(a, keep_var=Var(False), status_var=Var(""))
    return a


class ManagerCase(NumpyPatch):
    def setUp(self):
        super().setUp()
        for name, value in (("FOCUS_SETTLE_S", 0.0), ("FOCUS_FRAMES_PER_POSITION", 1)):
            patcher = mock.patch.object(app, name, value)
            patcher.start()
            self.addCleanup(patcher.stop)
        self.yes = mock.patch.object(app.messagebox, "askyesno", return_value=True)
        self.warn = mock.patch.object(app.messagebox, "showwarning")
        self.error = mock.patch.object(app.messagebox, "showerror")
        self.asked = self.yes.start()
        self.warned = self.warn.start()
        self.error.start()
        self.addCleanup(self.yes.stop)
        self.addCleanup(self.warn.stop)
        self.addCleanup(self.error.stop)

    def page(self, focus_um=7252.0, **controller):
        m = motor(**controller)
        self.addCleanup(m.executor.shutdown, wait=False, cancel_futures=True)
        self.addCleanup(m.controller.release.set)
        a = focus_app(m, Camera(m.controller, focus_um))
        return a, m, a.auto_focus

    def pump(self, a, m, until, timeout=10.0, each=None):
        """The Tk thread: post_ui callbacks and the motor page's queue."""
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            if each is not None:
                each()
            while True:
                try:
                    a.ui.get_nowait()()
                except queue.Empty:
                    break
            while True:
                try:
                    item = m.ui_queue.get_nowait()
                except queue.Empty:
                    break
                if item[0] == "task_done":
                    m._finish_task(item[1], item[2], item[3])
            if until():
                return
            time.sleep(0.001)
        self.fail("timed out")

    def idle(self, focus):
        return lambda: focus.running_kind is None

    def search(self, a, m, focus, each=None):
        focus.toggle_search()
        self.assertEqual(focus.running_kind, "search")
        self.pump(a, m, self.idle(focus), each=each)
        self.pump(a, m, lambda: a.ui.empty())


class ManagerSearchTests(ManagerCase):
    def test_a_search_moves_the_motor_to_focus_through_the_motor_page(self):
        a, m, focus = self.page(focus_um=7252.0)
        self.search(a, m, focus)
        self.assertEqual(m.command_counts, round(7252.0 * COUNTS_PER_UM))
        self.assertAlmostEqual(m.controller.position_um, 7252.0, places=2)   # whole counts
        self.assertTrue(focus.status_var.get().startswith("Focus: best at 7252 µm (+2.0 µm)"),
                        focus.status_var.get())
        targets = [counts / COUNTS_PER_UM for counts in m.controller.writes]
        self.assertTrue(all(abs(t - 7250.0) <= 10.0 for t in targets))
        self.assertTrue(any("Auto focus move: absolute" in line for line in a.logs), "every move logged")
        self.asked.assert_called_once()
        self.assertEqual(focus.position_for_row(), round(round(7252.0 * COUNTS_PER_UM) / COUNTS_PER_UM, 4))

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
        self.assertTrue(submits and all(thread is tk_thread for thread in submits), "sent from the Tk thread")
        self.assertTrue(m.controller.threads)
        for thread in m.controller.threads:
            self.assertIsNot(thread, tk_thread)
            self.assertTrue(thread.name.startswith("motor-command"), thread.name)
        self.assertTrue(all(thread is tk_thread for thread in focus.status_var.threads), "Tk vars on Tk only")

    def test_the_button_reads_stop_focus_while_it_searches(self):
        a, m, focus = self.page()
        button, tick = mock.Mock(), mock.Mock()
        focus.buttons.append(button)
        focus.ticks.append(tick)
        focus.set_camera_ready(True)
        self.assertEqual(button.configure.call_args.kwargs,
                         {"text": "Auto focus", "style": "TButton", "state": "normal"})
        focus.toggle_search()
        self.assertEqual(button.configure.call_args.kwargs,
                         {"text": "Stop focus", "style": "Danger.TButton", "state": "normal"})
        self.pump(a, m, self.idle(focus))
        self.pump(a, m, lambda: a.ui.empty())
        self.assertEqual(button.configure.call_args.kwargs["text"], "Auto focus")
        self.assertEqual(button.configure.call_args.kwargs["style"], "TButton")
        focus.set_camera_ready(False)
        self.assertEqual(button.configure.call_args.kwargs["state"], "disabled")
        self.assertEqual(tick.configure.call_args.kwargs["state"], "disabled")

    def test_the_reference_stays_for_a_second_search(self):
        a, m, focus = self.page(focus_um=7258.0)
        self.search(a, m, focus)
        self.assertEqual(focus.reference_um, 7250.0)
        m.controller.writes.clear()
        a.camera_window.focus_um = 7275.0          # the sample drifts far away
        self.search(a, m, focus)
        targets = [counts / COUNTS_PER_UM for counts in m.controller.writes]
        self.assertLessEqual(max(targets), 7260.0, "never beyond the first reference + 10 µm")
        # Moved by the motor page: a new reference.
        m.command_counts = round(7300.0 * COUNTS_PER_UM)
        m.controller.position_um = 7300.0
        a.camera_window.focus_um = 7300.0
        self.search(a, m, focus)
        self.assertEqual(focus.reference_um, 7300.0)

    def test_refusals(self):
        cases = (
            ("motor", None, "not attached"),
            ("connected", False, "not connected"),
            ("initialized", False, "not initialised"),
            ("homed", False, "not homed"),
            ("safety", False, "safety tick"),
            ("busy", True, "busy"),
            ("camera", None, "camera is not live"),
            ("scan", True, "a scan is running"),
            ("recording", True, "recording"),
        )
        for what, value, text in cases:
            with self.subTest(what=what):
                a, m, focus = self.page()
                if what == "motor":
                    a.motor_app = None
                elif what == "connected":
                    m.controller.connected = value
                elif what == "initialized":
                    m.initialized = value
                elif what == "homed":
                    m.homed = value
                elif what == "safety":
                    m.safety_var.value = value
                elif what == "busy":
                    m.command_busy = value
                elif what == "camera":
                    a.camera_window = None
                elif what == "scan":
                    a.magnet_scan_running = True
                elif what == "recording":
                    a.camera_window.video_recorder = object()
                self.warned.reset_mock()
                focus.toggle_search()
                self.warned.assert_called_once()
                self.assertIn(text, self.warned.call_args.args[1])
                self.assertIn(text, focus.status_var.get())
                self.assertIsNone(focus.running_kind)
                self.assertEqual(m.controller.writes, [])
                self.assertTrue(a.ui.empty())
                # The tick refuses the same way and stays off.
                if what not in ("scan", "recording"):
                    focus.keep_var.set(True)
                    focus.keep_changed()
                    self.assertFalse(focus.keep_var.get())
                    self.assertFalse(focus.keep_on)

    def test_the_motor_refuses_a_target_outside_its_range(self):
        m = motor()
        self.addCleanup(m.executor.shutdown, wait=False)
        for target in (-0.5, 100_000.5):
            with self.assertRaisesRegex(Exception, "outside"):
                m.submit_focus_target(target, "test")
        self.assertEqual(m.controller.writes, [])
        m.safety_var.value = False
        with self.assertRaisesRegex(Exception, "safety"):
            m.submit_focus_target(7250.0, "test")

    def test_a_failed_move_stops_and_unticks(self):
        a, m, focus = self.page(fail_at=3)
        focus.keep_var.set(True)
        focus.keep_changed()
        self.assertTrue(focus.keep_on)
        self.search(a, m, focus)
        self.assertEqual(len(m.controller.writes), 3, "no move after the failed one")
        self.assertFalse(focus.keep_var.get())
        self.assertFalse(focus.keep_on)
        self.assertIn("failed", focus.status_var.get())
        self.assertEqual(len(a.dialogs), 1)
        self.assertEqual(a.dialogs[0][0], "showwarning")
        self.assertEqual(focus.position_for_row(), "", "unknown after a failed write")

    def test_no_roi_no_focus(self):
        """No camera ROI: nothing moves, and the status says to draw one."""
        a, m, focus = self.page()
        a.camera_window.roi = None
        self.assertIn("ROI", focus.refusal())
        focus.toggle_search()
        self.assertIsNone(focus.running_kind, "refused: nothing started")
        self.assertEqual(m.controller.writes, [], "no move without an ROI")

    def test_an_unconfirmed_move_stops_and_unticks(self):
        with mock.patch.object(app, "FOCUS_MOVE_TIMEOUT_S", 0.3):
            a, m, focus = self.page(hang_at=2)
            focus.keep_var.set(True)
            focus.keep_changed()
            self.search(a, m, focus)
        self.assertEqual(len(m.controller.writes), 2)
        self.assertIn("not confirmed", focus.status_var.get())
        self.assertFalse(focus.keep_var.get())
        self.assertEqual(len(a.dialogs), 1)

    def test_a_cancel_mid_search_sends_no_more_moves(self):
        a, m, focus = self.page(focus_um=7259.0)
        stopped = []

        def press_stop():
            if len(m.controller.writes) >= 4 and not stopped:
                stopped.append(True)
                focus.toggle_search()               # the button reads Stop focus
        self.search(a, m, focus, each=press_stop)
        time.sleep(0.05)
        self.pump(a, m, lambda: a.ui.empty())
        self.assertEqual(len(m.controller.writes), 4)
        self.assertEqual(focus.status_var.get(), "Focus: auto focus stopped")
        self.assertEqual(a.dialogs, [], "a stop is not a failure")

    def test_init_off_or_disconnect_cancels_and_unticks(self):
        for method in ("init_off", "disconnect"):
            with self.subTest(method=method):
                a, m, focus = self.page(focus_um=7259.0)
                m.motion_stop_listeners.append(focus.motor_stopping)
                focus.keep_var.set(True)
                focus.keep_changed()
                stand_in = types.SimpleNamespace(
                    _notify_motion_stop=m._notify_motion_stop,
                    controller=types.SimpleNamespace(connected=False, disconnect=lambda: None),
                    init_off_busy=False,
                    poll_stop=threading.Event(), home_cancel=threading.Event(),
                    _set_busy=mock.Mock(), executor=mock.Mock(), ui_queue=queue.Queue(),
                )
                getattr(MotorApp, method)(stand_in)
                self.assertTrue(focus.cancel_event.is_set())
                self.assertFalse(focus.keep_var.get())

    def test_the_camera_going_cancels_and_unticks(self):
        a, m, focus = self.page()
        focus.camera_ready = True
        focus.keep_var.set(True)
        focus.keep_changed()
        focus.set_camera_ready(False)
        self.assertTrue(focus.cancel_event.is_set())
        self.assertFalse(focus.keep_on)


class FrameTests(ManagerCase):
    def test_frames_begun_before_the_move_settled_are_ignored(self):
        a, m, focus = self.page()
        old = types.SimpleNamespace(raw=textured(300.0), exposure_start_monotonic=time.monotonic(),
                                    exposure_ms=1.0)
        fresh = types.SimpleNamespace(raw=textured(30.0), exposure_start_monotonic=time.monotonic() + 1e3,
                                      exposure_ms=1.0)
        camera = types.SimpleNamespace(latest_frame=old)
        a.camera_window = camera
        focus.frames_after = time.monotonic() + 0.1
        timer = threading.Timer(0.3, lambda: setattr(camera, "latest_frame", fresh))
        timer.start()
        self.addCleanup(timer.cancel)
        self.assertAlmostEqual(focus._measure(), app.focus_sharpness(textured(30.0)))

    def test_two_distinct_frames_are_averaged(self):
        a, m, focus = self.page()
        frames = iter([textured(10.0), textured(10.0), textured(50.0)])
        state = {"frame": None}

        class Source:
            @property
            def latest_frame(self):
                if state["frame"] is None or time.monotonic() > state["at"]:
                    state["frame"] = types.SimpleNamespace(
                        raw=next(frames, textured(50.0)), exposure_start_monotonic=time.monotonic(),
                        exposure_ms=1.0)
                    state["at"] = time.monotonic() + 0.05
                return state["frame"]

        a.camera_window = Source()
        with mock.patch.object(app, "FOCUS_FRAMES_PER_POSITION", 2):
            score = focus._measure()
        self.assertAlmostEqual(score, app.focus_sharpness(textured(10.0)), msg="the same frame never twice")

    def test_no_fresh_frame_is_a_failure(self):
        a, m, focus = self.page()
        a.camera_window = types.SimpleNamespace(latest_frame=types.SimpleNamespace(
            raw=textured(5.0), exposure_start_monotonic=math.nan, exposure_ms=1.0))
        with mock.patch.object(app, "FOCUS_FRAME_TIMEOUT_S", 0.1):
            with self.assertRaisesRegex(app.FocusError, "no camera frame"):
                focus._measure()


class KeepInFocusTests(ManagerCase):
    def test_the_idle_check_steps_towards_focus_every_30_s(self):
        a, m, focus = self.page(focus_um=7251.0)
        focus.camera_ready = True
        focus.keep_var.set(True)
        focus.keep_changed()
        self.assertEqual(focus.reference_um, 7250.0)
        focus.idle_tick()
        self.assertEqual(focus.running_kind, "check")
        self.pump(a, m, self.idle(focus))
        self.pump(a, m, lambda: a.ui.empty())
        self.assertAlmostEqual(m.controller.position_um, 7251.0, places=2)
        self.assertTrue(focus.status_var.get().startswith("Focus: checked at 7251 µm"), focus.status_var.get())
        # Not again within 30 s, nor during a scan.
        focus.idle_tick()
        self.assertIsNone(focus.running_kind)
        focus.last_check_monotonic = -math.inf
        a.keithley_scan_running = True
        focus.idle_tick()
        self.assertIsNone(focus.running_kind)

    def test_a_motor_moved_by_hand_switches_keep_in_focus_off(self):
        a, m, focus = self.page()
        focus.keep_var.set(True)
        focus.keep_changed()
        m.command_counts += 512                        # Up / Down on the motor page
        focus.idle_tick()
        self.assertIsNone(focus.running_kind)
        self.assertFalse(focus.keep_on)
        self.assertIn("moved by another command", a.dialogs[0][2])
        self.assertEqual(m.controller.writes, [])

    def test_between_points_runs_on_the_scan_worker(self):
        a, m, focus = self.page(focus_um=7251.5)
        focus.keep_var.set(True)
        focus.keep_changed()
        a.magnet_scan_running = True
        a.magnet_scan_recorder = object()
        stop = threading.Event()
        worker = threading.Thread(target=focus.between_points, args=("magnet_scan_recorder", stop))
        worker.start()
        self.pump(a, m, lambda: not worker.is_alive())
        self.pump(a, m, lambda: a.ui.empty())
        self.assertAlmostEqual(m.controller.position_um, 7251.5, places=2)
        notes = [details["text"] for attribute, name, details in a.events if name == "note"]
        self.assertTrue(any("Auto focus move" in text for text in notes), "moves in the run log")
        focus_events = [details for attribute, name, details in a.events if name == "focus"]
        self.assertEqual(focus_events[-1]["position_um"], 7251.5)

    def test_between_points_never_during_an_acquisition(self):
        a, m, focus = self.page()
        focus.keep_var.set(True)
        focus.keep_changed()
        for flag in ("magnet_scan_collecting", "keithley_collecting"):
            with self.subTest(flag=flag):
                setattr(a, flag, True)
                focus.between_points("magnet_scan_recorder", threading.Event())
                setattr(a, flag, False)
                self.assertIsNone(focus.running_kind)
                self.assertTrue(a.ui.empty(), "nothing sent")
        stopped = threading.Event()
        stopped.set()
        focus.between_points("magnet_scan_recorder", stopped)
        focus.keep_on = False
        focus.between_points("magnet_scan_recorder", threading.Event())
        self.assertTrue(a.ui.empty())
        self.assertEqual(m.controller.writes, [])

    def test_a_scan_start_stops_a_running_check_first(self):
        a, m, focus = self.page(focus_um=7259.0)
        focus.camera_ready = True
        focus.keep_var.set(True)
        focus.keep_changed()
        focus.idle_tick()
        self.pump(a, m, lambda: len(m.controller.writes) >= 1)
        worker = threading.Thread(target=focus.before_scan)
        worker.start()
        self.pump(a, m, lambda: not worker.is_alive())
        self.assertIsNone(focus.running_kind)
        count = len(m.controller.writes)
        self.pump(a, m, lambda: a.ui.empty())
        time.sleep(0.05)
        self.pump(a, m, lambda: a.ui.empty())
        self.assertEqual(len(m.controller.writes), count, "no move after the scan's start")
        self.assertTrue(focus.keep_on, "a stop for a scan is not a failure")


class TravelTests(unittest.TestCase):
    def test_the_wait_bound_from_the_profile(self):
        stand_in = types.SimpleNamespace(_parse_profile=lambda: (5.0, 10.0, 20.0))
        self.assertAlmostEqual(MotorApp._focus_travel_s(stand_in, 51), 51 / (5.0 * 51200) + 0.5)

        def bad():
            raise ValueError("x")

        stand_in._parse_profile = bad
        self.assertAlmostEqual(MotorApp._focus_travel_s(stand_in, 0), 1.0)


# ---------------------------------------------------------------------------
# The scans: the hook between points and the CSV column
# ---------------------------------------------------------------------------

class FakeFocus:
    """The scans' view of the manager."""

    def __init__(self, a, collecting_attr, rows_attr, position=7251.5):
        self.a, self.collecting_attr, self.rows_attr, self.position = a, collecting_attr, rows_attr, position
        self.calls, self.before, self.before_attrs = [], 0, []

    def before_scan(self, recorder_attr=None):
        # The scan's recorder: Super logs its reference there (2026-10-20).
        self.before += 1
        self.before_attrs.append(recorder_attr)

    def position_for_row(self):
        return self.position

    def between_points(self, recorder_attr, stop_event):
        rows = self.a.__dict__.get(self.rows_attr)
        self.calls.append((recorder_attr, bool(self.a.__dict__.get(self.collecting_attr)),
                           None if rows is None else len(rows)))


class ScanHookTests(NumpyPatch):
    def test_columns_end_with_the_focus_position(self):
        for columns in (app.MAGNET_SCAN_CSV_COLUMNS, app.KEITHLEY_SCAN_CSV_COLUMNS,
                        app.CIDWM_SCAN_6221_COLUMNS, app.CIDWM_SCAN_AVTECH_COLUMNS):
            self.assertEqual(columns[-1], "focus_position_um")
            self.assertEqual(columns.count("focus_position_um"), 1)
        self.assertEqual(app.CIDWM_SCAN_6221_COLUMNS[-2], "note", "the old columns keep their order")

    def magnet(self):
        import tempfile
        from pathlib import Path
        from test_moke_cidwm_runs import MagnetScanRunTests, csv_rows
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        recorder = app.ScanRunRecorder(Path(tmp.name), "MOKE", "B1", {"test": True})
        self.addCleanup(recorder.close, "test")
        helper = MagnetScanRunTests()
        a = helper.page(recorder, points=((1, 1, 3, 1.0), (1, 2, 3, 0.5), (1, 3, 3, 0.0)))
        return a, recorder, helper, csv_rows

    def test_magnet_scan_hook_between_points_and_the_column(self):
        a, recorder, helper, csv_rows = self.magnet()
        a.auto_focus = FakeFocus(a, "magnet_scan_collecting", "magnet_scan_rows")
        helper.run_scan(a)
        self.assertEqual(a.auto_focus.before, 1)
        self.assertEqual(a.auto_focus.before_attrs, ["magnet_scan_recorder"])
        self.assertEqual(a.auto_focus.calls, [("magnet_scan_recorder", False, 1),
                                              ("magnet_scan_recorder", False, 2)],
                         "after each point's row, not while collecting, not after the last")
        rows = csv_rows(recorder)
        self.assertEqual(rows[0][-1], "focus_position_um")
        self.assertEqual([row[-1] for row in rows[1:]], ["7251.5"] * 3)

    def test_magnet_scan_without_the_motor_leaves_it_blank(self):
        a, recorder, helper, csv_rows = self.magnet()
        helper.run_scan(a)
        rows = csv_rows(recorder)
        self.assertEqual(rows[0][-1], "focus_position_um")
        self.assertEqual([row[-1] for row in rows[1:]], [""] * 3)

    def test_moke24_scan_hook_and_column(self):
        import tempfile
        from pathlib import Path
        from test_moke_cidwm_runs import Moke24RunTests, csv_rows
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        recorder = app.ScanRunRecorder(Path(tmp.name), "MOKE24", "B1", {"test": True})
        self.addCleanup(recorder.close, "test")
        a = Moke24RunTests().page(recorder)
        a.auto_focus = FakeFocus(a, "keithley_collecting", "keithley_step_rows")
        a._keithley_scan_worker([1.0, 2.0], 0.0, 0.0, None)
        self.assertEqual(a.auto_focus.calls, [("keithley_scan_recorder", False, 1)])
        self.assertEqual(a.auto_focus.before_attrs, ["keithley_scan_recorder"])
        rows = csv_rows(recorder)
        self.assertEqual(rows[0][-1], "focus_position_um")
        self.assertEqual([row[-1] for row in rows[1:]], ["7251.5", "7251.5"])

    def test_cidwm_hook_after_the_images_and_column(self):
        from PIL import Image
        import csv
        from test_cidwm_pulse_scan import SETTINGS, page, run_recorder
        with mock.patch.object(app, "Image", Image):
            for with_focus in (True, False):
                with self.subTest(with_focus=with_focus):
                    recorder = run_recorder(self)
                    a = page()
                    a.k6221_roi = {"cx": 1.5, "cy": 1.0, "w": 4.0, "h": 3.0, "angle": 0.0}
                    saved = []
                    if with_focus:
                        a.auto_focus = FakeFocus(a, "none", "none")
                        real = a.auto_focus.between_points

                        def between(attr, stop, real=real):
                            saved.append(sorted(p.name for p in recorder.directory.glob("*.tiff")))
                            real(attr, stop)

                        a.auto_focus.between_points = between
                    a._cidwm_pulse_scan_worker(SETTINGS, recorder)
                    with recorder.csv_path.open(encoding="utf-8-sig", newline="") as handle:
                        rows = list(csv.reader(handle))
                    self.assertEqual(rows[0], list(app.CIDWM_SCAN_6221_COLUMNS))
                    self.assertEqual([row[-1] for row in rows[1:]],
                                     ["7251.5"] * 3 if with_focus else [""] * 3)
                    if with_focus:
                        self.assertEqual(len(a.auto_focus.calls), 2, "between pulses, not after the last")
                        self.assertEqual(a.auto_focus.before_attrs, ["cidwm_scan_recorder"])
                        self.assertEqual([len(names) for names in saved], [2, 4],
                                         "after the pulse's images are saved")


if __name__ == "__main__":
    unittest.main()
