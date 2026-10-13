"""MOKE magnet minor audit/review fixes (2026-10-13).  No window, no
instrument: the real methods run on stand-ins, every dialog is mocked.

- Save CSV + PNG: the PNG shows the scan in the CSV (its channel, every
  row), not the page's plot (empty after Clear curve or an ROI nudge,
  following the selector, the last 2000 points only).
- The scan's field read reopens the gaussmeter once on a missed reply, as
  every other field read does (one FTDI timeout aborted the whole scan).
- After Stop or an error the Target box shows the 0 A the scan wrote, so
  Up/Down/Flip no longer step from the pre-scan target.
- "Zero both now" pressed while Disconnect runs is refused on the status
  line (it was queued and showed a false "not connected" error dialog);
  repeated presses queue one zero, not one per press.
- The start dialog's estimate includes the wait for a camera average begun
  after each point's current was set."""
import math
from pathlib import Path
import tempfile
import threading
import types
import unittest
from unittest import mock

import numpy as np

from test_magnet_scan_start import stand_in
from test_magnet_zero_and_frames import Var, bind, magnet_app, scan_app
from test_ppms_motor_debug import app

C = app.CombinedControlApp
OUT_COLOUR = "#c62828"


class Canvas:
    """Records what _redraw_moke_plot draws."""

    def __init__(self):
        self.items = []

    def delete(self, _tag):
        self.items = []

    def winfo_width(self):
        return 480

    def winfo_height(self):
        return 300

    def _record(self, kind):
        return lambda *args, **kwargs: self.items.append((kind, args, kwargs))

    def __getattr__(self, name):
        if name.startswith("create_"):
            return self._record(name[len("create_"):])
        raise AttributeError(name)

    def points(self):
        return [kwargs.get("fill") for kind, _args, kwargs in self.items if kind == "oval"]

    def texts(self):
        return [kwargs.get("text") for kind, _args, kwargs in self.items if kind == "text"]


def scan_row(index, current):
    # (elapsed, loop, point, points, target, readback, field, ROI mean, std, N)
    return (float(index), 1, index + 1, 0, current, current, 10.0 * current, 100.0 + current, 0.1, 3)


def save_app(rows, settings):
    a = mock.Mock()
    a.__dict__.update(
        active_file_exports=set(), magnet_scan_data_lock=threading.Lock(),
        magnet_scan_rows=list(rows), magnet_scan_settings=dict(settings),
        magnet_scan_total_points=len(rows), moke_plot_override=None,
        # The page after Clear curve with the selector moved to In-plane.
        moke_field_rows={"OUT": [], "IN": []}, moke_roi=object(),
        ui_font_family="Segoe UI", ui_small_font_size=9,
    )
    a.moke_field_source_var = Var("In-plane")
    a.moke_plot_canvas = Canvas()
    a.moke_hardware_parameter_snapshot.return_value = {}
    a.camera_scan_parameter_snapshot.return_value = {}
    bind(a, "save_magnet_scan_csv", "_redraw_moke_plot")
    return a


class SaveSnapshotTests(unittest.TestCase):
    def setUp(self):
        # Without the camera SDK on this Mac the program's np is None.
        for patcher in (mock.patch.object(app, "np", np),
                        mock.patch.object(app.messagebox, "showinfo"),
                        mock.patch.object(app.messagebox, "showwarning"),
                        mock.patch.object(app.messagebox, "showerror")):
            patcher.start()
            self.addCleanup(patcher.stop)
        folder = tempfile.TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        self.folder = Path(folder.name)

    def save(self, a, snapshot=None):
        """Run Save CSV + PNG up to its background export, then its end."""
        a.select_csv_save_directory.return_value = self.folder
        drawn = []

        def grab(canvas, path):
            drawn.append((canvas.points(), canvas.texts()))
            if snapshot is not None:
                snapshot()

        a.save_plot_canvas_snapshot.side_effect = grab
        a.save_magnet_scan_csv()
        export_key, _camera, _work, saved, _failed = a.submit_file_export.call_args.args
        self.assertEqual(export_key, "moke-csv")
        saved(None)
        return drawn

    def test_the_png_shows_the_saved_scan_after_clear_curve_and_a_selector_change(self):
        rows = [scan_row(i, current) for i, current in enumerate((-1.0, -0.5, 0.0, 0.5, 1.0))]
        a = save_app(rows, {"scan.channel_key": "OUT"})
        ((points, texts),) = self.save(a)
        self.assertEqual(points, [OUT_COLOUR] * 5, "the out-of-plane scan, every row")
        self.assertNotIn("Current / Field–ROI data is recorded once a field scan starts", texts)
        # Afterwards the page shows its own (cleared) plot again.
        self.assertIsNone(a.moke_plot_override)
        self.assertEqual(a.moke_plot_canvas.points(), [])
        app.messagebox.showinfo.assert_called_once()

    def test_a_scan_longer_than_the_pages_2000_points(self):
        rows = [scan_row(i, round(-1.0 + i * 0.001, 3)) for i in range(2500)]
        a = save_app(rows, {"scan.channel_key": "OUT"})
        a.moke_field_source_var = Var("Out-of-plane")
        a.moke_field_rows["OUT"] = [(r[0], r[4], r[5], r[6], r[7]) for r in rows[-2000:]]
        ((points, _texts),) = self.save(a)
        self.assertEqual(len(points), 2500)
        self.assertEqual(len(a.moke_plot_canvas.points()), 2000, "the page keeps its 2000")

    def test_a_failed_snapshot_still_restores_the_page(self):
        a = save_app([scan_row(0, 1.0), scan_row(1, 0.0)], {"scan.channel_key": "OUT"})

        def fail():
            raise RuntimeError("The plot is not visible; cannot capture a snapshot")

        self.save(a, snapshot=fail)
        self.assertIsNone(a.moke_plot_override)
        self.assertEqual(a.moke_plot_canvas.points(), [])
        app.messagebox.showwarning.assert_called_once()
        self.assertIn("snapshot failed", a.magnet_status_var.set.call_args.args[0])

    def test_without_a_scan_channel_the_page_plot_is_kept(self):
        a = save_app([scan_row(0, 1.0)], {})
        a.moke_field_source_var = Var("Out-of-plane")
        a.moke_field_rows["OUT"] = [(0.0, 1.0, 1.0, 10.0, 101.0), (1.0, 0.0, 0.0, 0.0, 100.0)]
        ((points, _texts),) = self.save(a)
        self.assertEqual(points, [OUT_COLOUR] * 2)


class Meter:
    port = "COM13"

    def __init__(self, *replies):
        self.replies, self.events = list(replies), []

    def query_field(self):
        self.events.append("query")
        reply = self.replies.pop(0)
        if isinstance(reply, BaseException):
            raise reply
        return reply

    def close(self):
        self.events.append("close")

    def connect(self):
        self.events.append("connect")


class Never:
    """A stop event that is never set and never sleeps."""

    def is_set(self):
        return False

    def wait(self, _timeout):
        return False


def worker_app(meter, target="1.500"):
    a = scan_app(None)
    a.camera_window = None        # no camera window: no wait for frames
    a._require_gauss_meter = lambda key: meter
    a.magnet_target_vars = {"OUT": Var(target)}
    a.magnet_status_var = Var()
    a.magnet_scan_stop_event = Never()
    a.zero_writes = []
    a._require_magnet_bop = lambda key: types.SimpleNamespace(
        set_current=lambda value: a.zero_writes.append(value))
    return a


class ScanWorkerTests(unittest.TestCase):
    def setUp(self):
        for patcher in (mock.patch.object(app, "np", np), mock.patch.object(app.time, "sleep")):
            patcher.start()
            self.addCleanup(patcher.stop)

    def run_ui(self, a):
        while a.ui:
            a.ui.pop(0)()

    def test_one_missed_gaussmeter_reply_does_not_end_the_scan(self):
        meter = Meter(TimeoutError("no reply"), 12.0, 0.5)
        a = worker_app(meter)
        a._magnet_scan_worker([(1, 1, 2, 1.0), (1, 2, 2, 0.0)], 0.0, 0.0, "OUT", None)
        self.assertEqual(meter.events, ["query", "close", "connect", "query", "query"])
        self.assertEqual([row[6] for row in a.magnet_scan_rows], [12.0, 0.5])
        self.assertEqual(len(a.set_at), 3, "both points, then the verified 0 A at the end")

    def test_stop_puts_0_in_the_target_box(self):
        a = worker_app(Meter(12.0))
        a.magnet_scan_stop_event = threading.Event()

        def write(key, value):
            a.magnet_scan_stop_event.set()      # Stop pressed during the first write
            return value

        a._write_and_verify_magnet_current = write
        a._magnet_scan_worker([(1, 1, 2, 1.0), (1, 2, 2, 0.0)], 0.0, 0.0, "OUT", None)
        self.assertEqual(a.zero_writes, [0.0])
        self.run_ui(a)
        self.assertEqual(a.magnet_target_vars["OUT"].get(), "0.000")

    def test_an_error_puts_0_in_the_target_box(self):
        a = worker_app(Meter(TimeoutError("no reply"), TimeoutError("still no reply")))
        with self.assertRaises(TimeoutError):
            a._magnet_scan_worker([(1, 1, 1, 1.0)], 0.0, 0.0, "OUT", None)
        self.assertEqual(a.zero_writes, [0.0])
        self.run_ui(a)
        self.assertEqual(a.magnet_target_vars["OUT"].get(), "0.000")

    def test_a_failed_zero_leaves_the_target_box(self):
        a = worker_app(Meter(RuntimeError("x"), RuntimeError("y")))

        def refuse(value):
            raise RuntimeError("BOP gone")

        a._require_magnet_bop = lambda key: types.SimpleNamespace(set_current=refuse)
        with self.assertRaises(RuntimeError):
            a._magnet_scan_worker([(1, 1, 1, 1.0)], 0.0, 0.0, "OUT", None)
        self.run_ui(a)
        self.assertEqual(a.magnet_target_vars["OUT"].get(), "1.500")


def zero_app():
    a = magnet_app()
    for channel in a.magnet_channels.values():
        channel.close = lambda bop=channel.bop: setattr(bop, "connected", False)
    a.magnet_field_vars = {"OUT": Var(), "IN": Var()}
    bind(a, "disconnect_magnets")
    return a


class ZeroPressTests(unittest.TestCase):
    def setUp(self):
        for patcher in (mock.patch.object(app.time, "sleep"),
                        mock.patch.object(app.messagebox, "showerror"),
                        mock.patch.object(app.messagebox, "showwarning")):
            patcher.start()
            self.addCleanup(patcher.stop)

    def test_zero_during_disconnect_is_refused_on_the_status_line(self):
        a = zero_app()
        a.disconnect_magnets()
        a.zero_both_magnets()                       # pressed while Disconnect runs
        self.assertEqual(len(a.magnet_executor.tasks), 1, "no zero queued behind Disconnect")
        self.assertIn("Disconnect is running", a.magnet_status_var.set.call_args.args[0])
        self.assertFalse(a.magnet_abort_event.is_set())
        a.magnet_executor.run_next()
        a.ui.pop(0)()
        app.messagebox.showerror.assert_not_called()
        app.messagebox.showwarning.assert_not_called()
        self.assertEqual(a.writes, [("OUT", 0.0), ("IN", 0.0)], "Disconnect wrote 0 A first")
        self.assertEqual(a.magnet_status_var.set.call_args.args[0],
                         "Magnet devices disconnected; 0 A written first")
        self.assertEqual((a.magnet_busy, a.magnet_disconnect_pending), (False, False))

    def test_a_refused_disconnect_does_not_block_the_zero(self):
        a = zero_app()
        a.magnet_busy, a.magnet_pending_tasks = True, 1
        a.disconnect_magnets()                      # refused: a command is running
        self.assertFalse(a.magnet_disconnect_pending)
        a.zero_both_magnets()
        self.assertEqual(len(a.magnet_executor.tasks), 1, "the zero is queued")

    def test_repeated_presses_queue_one_zero(self):
        a = zero_app()
        a._parse_magnet_target = lambda key: 1.0
        a.set_magnet_current("OUT")                 # a Write runs
        for _press in range(4):
            a.zero_both_magnets()
        self.assertEqual(len(a.magnet_executor.tasks), 2, "the Write and one zero")
        self.assertEqual(a.magnet_pending_tasks, 2)
        self.assertIn("already queued", a.magnet_status_var.set.call_args.args[0])
        a.magnet_executor.run_next()                # the Write stops at once
        # A press while the zero itself runs adds nothing and does not leave
        # the abort set for the next command.
        in_bop = a.magnet_channels["IN"].bop
        original = in_bop.set_current

        def press_then_write(value):
            a.zero_both_magnets()
            original(value)

        in_bop.set_current = press_then_write
        a.magnet_executor.run_next()                # the zero
        self.assertEqual(a.magnet_executor.tasks, [])
        self.assertFalse(a.magnet_abort_event.is_set())
        a.ui.pop(0)()                               # the Write's end
        a.ui.pop(0)()                               # the zero's end
        self.assertEqual((a.magnet_busy, a.magnet_zero_pending), (False, False))
        self.assertIn("0 A sent to both paths", a.magnet_status_var.set.call_args.args[0])
        in_bop.set_current = original
        a.zero_both_magnets()                       # a new press after it ended
        self.assertEqual(len(a.magnet_executor.tasks), 1)

    def test_a_zero_not_submitted_clears_the_flag(self):
        a = zero_app()
        a.magnet_closed = True
        a.zero_both_magnets()
        self.assertFalse(a.magnet_zero_pending)


class EstimateTests(unittest.TestCase):
    def start(self, settle_s, collect_s, frame):
        a = stand_in(ready=True, armed=True, selected="OUT")
        a.magnet_scan_data_lock = threading.Lock()
        a.magnet_scan_eta_after_id = None
        a.moke_field_rows = {}
        a.magnet_port_vars = {k: Var(k) for k in ("OUT_BOP", "OUT_GAUSS", "IN_BOP", "IN_GAUSS")}
        a.magnet_auto_poll_var = Var(True)
        a.magnet_save_images_var = Var(False)
        a.magnet_scan_step_current_var = Var("0.5")
        a.magnet_safety_var = Var(True)
        a.magnet_poll_interval_var = Var(0.5)
        a.keithley_scan_running = a.k6221_scan_running = a.magnet_poll_inflight = False
        a.camera_window = types.SimpleNamespace(
            worker=types.SimpleNamespace(background_enabled=True), latest_frame=frame)
        a._parse_magnet_scan.return_value = (
            [(1, 1, 3, 1.0), (1, 2, 3, 0.0), (1, 3, 3, 1.0)], settle_s, collect_s, "OUT")
        a.camera_scan_parameter_snapshot.return_value = {}
        a.moke_hardware_parameter_snapshot.return_value = {}
        a._snapshot_variable = C._snapshot_variable
        a._format_scan_duration = C._format_scan_duration
        with mock.patch.object(app.messagebox, "askyesno", return_value=True) as asked, \
                mock.patch.object(app.messagebox, "showwarning") as warned, \
                mock.patch.object(app.messagebox, "showerror") as failed:
            C.start_magnet_scan(a)
        warned.assert_not_called()
        failed.assert_not_called()
        a.magnet_executor.submit.assert_called_once()
        return a, asked.call_args.args[1]

    def test_short_settle_and_acquire_wait_for_a_fresh_average(self):
        frame = types.SimpleNamespace(temporal_average_count=4, exposure_ms=100.0)
        a, summary = self.start(0.1, 0.1, frame)
        # Three points of max(0.2 s, 2 x 4 x 100 ms) + 0.5 s; was 3 x 0.7 s.
        self.assertAlmostEqual(a.magnet_scan_settings["scan.estimated_total_s_at_start"], 3.9)
        self.assertIn("Estimated duration: 4 s", summary)
        self.assertAlmostEqual(a.magnet_scan_estimated_finish - a.magnet_scan_start_monotonic, 3.9)

    def test_the_default_settings_are_unchanged(self):
        frame = types.SimpleNamespace(temporal_average_count=4, exposure_ms=100.0)
        a, _summary = self.start(2.0, 1.0, frame)
        self.assertAlmostEqual(a.magnet_scan_settings["scan.estimated_total_s_at_start"], 10.5)

    def test_the_block_from_the_camera_frame(self):
        self.assertAlmostEqual(app.camera_average_block_s(
            types.SimpleNamespace(temporal_average_count=4, exposure_ms=100.0)), 0.8)
        for frame in (None, types.SimpleNamespace(temporal_average_count=4, exposure_ms=math.nan),
                      mock.Mock()):
            self.assertEqual(app.camera_average_block_s(frame), 0.0)


if __name__ == "__main__":
    unittest.main()
