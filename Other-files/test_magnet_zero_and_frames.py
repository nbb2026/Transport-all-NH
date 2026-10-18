"""MOKE magnet review fixes (2026-10-13).  No window, no instrument: the
real methods run on stand-ins.

- "Zero both now" pressed while another magnet command runs used to be
  refused (status line only), so 0 A was never sent; now it queues behind
  that command, a running current write gives up at its next attempt, and
  the page stays busy until both are done.
- A current-scan point keeps only ROI samples whose camera average began
  after the point's current was set (the 4-frame average and its
  cross-fade reach back about 0.7 s; at Settle 0.1 s / Acquire 0.1 s the
  recorded ROI was mostly the previous point)."""
from concurrent.futures import Future
import math
import threading
import time
import types
import unittest
from unittest import mock

import numpy as np

from test_ppms_motor_debug import app

C = app.CombinedControlApp


def bind(target, *names):
    for name in names:
        setattr(target, name, types.MethodType(getattr(C, name), target))


def run_dialogs_now(a):
    """A finish's dialog opens from root.after(0, ...), not inside
    drain_ui_queue (M5, review, 2026-10-18); here it opens at once."""
    root = getattr(a, "root", None)
    if root is None:
        a.root = root = types.SimpleNamespace()
    root.after = lambda _delay, callback: callback()
    a.closing = False
    bind(a, "_dialog_after_drain")


def scan_end(a, writes=None):
    """What a magnet scan's end needs (user, 2026-10-18: both KEPCOs to 0 A
    at every end): the two BOPs (0 A writes recorded in ``writes``), the
    page's boxes and the real zeroing and log methods."""
    writes = [] if writes is None else writes
    a.zero_writes = writes
    a.magnet_channels = {
        key: types.SimpleNamespace(bop=types.SimpleNamespace(
            connected=True, set_current=lambda value, key=key: writes.append((key, value))))
        for key in ("OUT", "IN")
    }
    a.magnet_last_currents = {"OUT": None, "IN": None}
    a.magnet_target_vars = {"OUT": Var("1.500"), "IN": Var("0.000")}
    a.magnet_current_vars = {"OUT": Var(), "IN": Var()}
    a.magnet_status_var = Var()
    a.logged = []
    a.thread_log = a.logged.append
    a._magnet_kepco_labels = C._magnet_kepco_labels
    bind(a, "_magnet_scan_points", "_magnet_zero_outputs_after_scan", "_magnet_scan_zero_readback",
         "_scan_log", "_scan_page_log")
    return a


class Var:
    def __init__(self, value=None):
        self.value = value

    def get(self):
        return self.value

    def set(self, value):
        self.value = value


class QueueExecutor:
    """The single magnet worker, run one task at a time by the test."""

    def __init__(self, fail=False):
        self.tasks = []
        self.fail = fail

    def submit(self, function):
        if self.fail:
            raise RuntimeError("cannot schedule new futures after shutdown")
        future = Future()
        self.tasks.append((function, future))
        return future

    def run_next(self):
        function, future = self.tasks.pop(0)
        try:
            future.set_result(function())
        except BaseException as error:
            future.set_exception(error)


class Bop:
    connected = True
    output_armed = True

    def __init__(self, name, log, query=None):
        self.name, self.log, self.query = name, log, query
        self.value = 0.0

    def set_current(self, value):
        self.log.append((self.name, value))
        self.value = value

    def query_current(self):
        return self.query() if self.query is not None else self.value


def magnet_app(executor=None):
    a = mock.Mock()
    a.__dict__.update(
        magnet_closed=False, closing=False, magnet_scan_running=False,
        magnet_field_zero_active=False, magnet_poll_inflight=False,
        magnet_busy=False, magnet_pending_tasks=0,
        magnet_zero_pending=False, magnet_disconnect_pending=False,
        magnet_abort_event=threading.Event(),
        magnet_last_currents={"OUT": None, "IN": None},
    )
    a.magnet_auto_poll_var = Var(True)
    a.magnet_executor = executor or QueueExecutor()
    a.ui = []
    a.post_ui = a.ui.append
    a.writes = []
    a.magnet_channels = {
        "OUT": types.SimpleNamespace(bop=Bop("OUT", a.writes)),
        "IN": types.SimpleNamespace(bop=Bop("IN", a.writes)),
    }
    a.magnet_target_vars = {"OUT": Var(), "IN": Var()}
    a.magnet_current_vars = {"OUT": Var(), "IN": Var()}
    a._selected_magnet_switch_key.return_value = "OUT"
    bind(a, "_submit_magnet_task", "_finish_magnet_task", "zero_both_magnets",
         "_write_and_verify_magnet_current", "_require_magnet_bop", "set_magnet_current")
    run_dialogs_now(a)
    return a


class ZeroBothWhileBusyTests(unittest.TestCase):
    def test_zero_during_a_retrying_write_follows_it_at_once(self):
        a = magnet_app()
        a._parse_magnet_target = lambda key: 1.0
        pressed = []

        def readback():
            # The user presses "Zero both now" while the Write's readback
            # is still waiting for a reply.
            if not pressed:
                pressed.append(True)
                a.zero_both_magnets()
                raise TimeoutError("no IQ reply")
            return a.magnet_channels["OUT"].bop.value

        a.magnet_channels["OUT"].bop.query = readback
        with mock.patch.object(app.time, "sleep"), \
                mock.patch.object(app.messagebox, "showerror") as shown:
            a.set_magnet_current("OUT")
            self.assertEqual((a.magnet_busy, a.magnet_pending_tasks), (True, 1))
            a.magnet_executor.run_next()          # the Write
            # The zero is queued (it was refused before), and the Write gave
            # up at its next attempt: no second +1 A, no reconnect recovery,
            # 0 A written by its failure tail.
            self.assertEqual(len(a.magnet_executor.tasks), 1)
            self.assertEqual(a.magnet_pending_tasks, 2)
            self.assertFalse(a.magnet_auto_poll_var.get())
            self.assertEqual(a.writes, [("OUT", 1.0), ("OUT", 0.0)])
            a._recover_magnet_bop_at_zero.assert_not_called()
            a.magnet_executor.run_next()          # the zero
            self.assertFalse(a.magnet_abort_event.is_set())
            self.assertEqual(a.writes[2:], [("OUT", 0.0), ("IN", 0.0)])
            a.ui.pop(0)()                         # the Write's end
            shown.assert_called_once()
            self.assertIn('"Zero both now"', shown.call_args.args[1])
            self.assertTrue(a.magnet_busy, "the queued zero has not finished")
            a.ui.pop(0)()                         # the zero's end
        self.assertEqual((a.magnet_busy, a.magnet_pending_tasks), (False, 0))
        status = a.magnet_status_var.set.call_args.args[0]
        self.assertIn("0 A sent to both paths", status)
        self.assertEqual(a.magnet_current_vars["OUT"].get(), "+0.000 A")

    def test_other_commands_are_still_refused_while_busy(self):
        a = magnet_app()
        a.magnet_busy, a.magnet_pending_tasks = True, 1
        self.assertFalse(a._submit_magnet_task("Read", lambda: None))
        self.assertEqual(a.magnet_executor.tasks, [])
        a.magnet_status_var.set.assert_called_with("The previous magnet operation has not finished")

    def test_a_zero_not_submitted_restores_continuous(self):
        for state in ({"magnet_closed": True}, {"executor": QueueExecutor(fail=True)}):
            with self.subTest(state=list(state)):
                a = magnet_app(state.get("executor"))
                a.magnet_closed = state.get("magnet_closed", False)
                a.magnet_busy, a.magnet_pending_tasks = True, 1
                with mock.patch.object(app.messagebox, "showerror"):
                    a.zero_both_magnets()
                self.assertTrue(a.magnet_auto_poll_var.get())
                self.assertFalse(a.magnet_abort_event.is_set(), "a later write is not aborted")
                self.assertEqual((a.magnet_busy, a.magnet_pending_tasks), (True, 1))


def scan_app(frame):
    a = types.SimpleNamespace()
    a.magnet_scan_data_lock = threading.Lock()
    a.magnet_scan_running = True
    a.magnet_scan_collecting = False
    a.magnet_scan_point_set_monotonic = math.inf
    a.magnet_scan_current_samples = []
    a.magnet_scan_rows = []
    a.magnet_scan_start_time = time.time()
    a.ui = []
    a.post_ui = a.ui.append
    a.camera_window = types.SimpleNamespace(latest_frame=frame)
    a._require_gauss_meter = lambda key: types.SimpleNamespace(query_field=lambda: 12.0)
    a.magnet_thread_log = lambda text: None
    a._require_magnet_bop = lambda key: types.SimpleNamespace(set_current=lambda value: None)
    a.set_at = []

    def write(key, value):
        a.set_at.append(time.monotonic())
        return value

    a._write_and_verify_magnet_current = write
    # What ingest_moke_roi_sample touches besides the scan samples.
    a.moke_roi_mean_var = a.keithley_roi_mean_var = a.k6221_roi_mean_var = Var()
    a.roi_live_sample_sum, a.roi_live_sample_count = 0.0, 0
    a.roi_live_last_sample_time = time.monotonic()
    a._roi_live_settings = lambda: (3600.0, 10)
    bind(a, "ingest_moke_roi_sample", "_magnet_scan_worker", "_query_gauss_with_recovery")
    a._magnet_log = lambda text: None
    scan_end(a)
    return a


class StopEvent:
    """Each wait() runs the next step (feeds ROI samples), never sleeps."""

    def __init__(self, steps):
        self.steps, self.timeouts = list(steps), []

    def is_set(self):
        return False

    def wait(self, timeout):
        self.timeouts.append(timeout)
        if self.steps:
            self.steps.pop(0)()
        return False


class ScanFreshFrameTests(unittest.TestCase):
    def setUp(self):
        # Without the camera SDK on this Mac the program's np is None.
        patcher = mock.patch.object(app, "np", np)
        patcher.start()
        self.addCleanup(patcher.stop)

    def test_the_users_settings_record_only_the_new_current(self):
        a = scan_app(types.SimpleNamespace(temporal_average_count=4, exposure_ms=100.0))
        offered = []

        def sample(value, after_set):
            offered.append(value)
            a.ingest_moke_roi_sample(value, a.magnet_scan_point_set_monotonic + after_set)

        a.magnet_scan_stop_event = StopEvent([
            lambda: None,               # Settle 0.1 s
            lambda: sample(1.0, -0.3),  # Acquire 0.1 s: an average begun before the current was set
            lambda: sample(2.0, +0.01),  # waiting on: one begun after it
        ])
        a._magnet_scan_worker([(1, 1, 1, 1.0)], 0.1, 0.1, "OUT", None)
        self.assertEqual(offered, [1.0, 2.0])
        self.assertEqual(a.magnet_scan_stop_event.timeouts, [0.1, 0.1, 0.05])
        self.assertGreaterEqual(a.magnet_scan_point_set_monotonic, a.set_at[0],
                                "the point's time is taken after its current was verified")
        (row,) = a.magnet_scan_rows
        self.assertEqual((row[7], row[9]), (2.0, 1), "mean of the new-current sample only, N=1")
        self.assertFalse(a.magnet_scan_collecting)

    def test_no_new_frame_in_time_records_nan_not_the_old_current(self):
        a = scan_app(None)
        a.magnet_scan_stop_event = StopEvent([
            lambda: None,
            lambda: a.ingest_moke_roi_sample(1.0, a.magnet_scan_point_set_monotonic - 0.3),
        ])
        with mock.patch.object(app, "MAGNET_SCAN_FRAME_TIMEOUT_S", 0.0):
            a._magnet_scan_worker([(1, 1, 1, 1.0)], 0.0, 0.0, "OUT", None)
        (row,) = a.magnet_scan_rows
        self.assertTrue(math.isnan(row[7]))
        self.assertEqual(row[9], 0)

    def test_ingest_keeps_only_averages_begun_after_the_set(self):
        a = scan_app(None)
        a.magnet_scan_point_set_monotonic = 100.0
        a.magnet_scan_collecting = True
        for value, start in ((1.0, 99.0), (2.0, math.nan), (3.0, 100.5)):
            a.ingest_moke_roi_sample(value, start)
        a.ingest_moke_roi_sample(4.0)  # no window start: never a scan sample
        a.magnet_scan_collecting = False
        a.ingest_moke_roi_sample(5.0, 101.0)
        self.assertEqual(a.magnet_scan_current_samples, [3.0])
        self.assertEqual(a.moke_latest_roi_mean, 5.0, "the live value still follows every frame")

    def test_the_camera_passes_the_average_start(self):
        camera = types.SimpleNamespace(
            roi={"cx": 3.0, "cy": 3.0, "w": 4.0, "h": 4.0, "angle": 0.0},
            mapping=object(), selecting_roi=False, roi_start=None, roi_end=None,
            app=mock.Mock(),
        )
        frame = types.SimpleNamespace(
            raw=np.ones((6, 6), dtype=np.uint16),
            difference=np.full((6, 6), 3.0, dtype=np.float32),
            display_difference=np.full((6, 6), 3.0, dtype=np.float32),
            window_start_monotonic=42.0,
        )
        app.TkCameraWindow._ingest_roi_values(camera, frame)
        camera.app.ingest_moke_roi_sample.assert_called_once_with(3.0, 42.0)


if __name__ == "__main__":
    unittest.main()
