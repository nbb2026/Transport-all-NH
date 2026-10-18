"""MOKE magnet, MOKE-24 and CIDWM scans on the shared run base, and the
area's review fixes (user and review, 2026-10-18).

- Save (user): every scan makes <folder>/<name>/ with a live CSV (the
  page's columns, unchanged) and a JSON-lines log, through begin_scan_run
  after the Yes/No (prefixes MOKE, MOKE24, CIDWM); Save CSV opens in the
  run folder.
- Zero at every end (user): the KEPCOs to 0 A, the 2410 to 0 V and OFF, the
  6221 and the Avtech off, read back where the instrument allows; the
  finish says "Completed; " / "Stopped; " + how each output ended, or
  "Failed: ...", and its dialog says CHECK IT for an output not at zero.
- M1 Zero / Zero both / Disconnect with a dropped gaussmeter, Connect's 0 A
  first; M2 a failed zero never shown as 0 A, the notices in the page log;
  M3 a failed 2410 off shown as "check the 2410"; C2-2 MOKE-24 frames from
  before the voltage step not averaged; M4 the Avtech's unknown output holds
  the 6221; M5 dialogs from root.after(0, ...); M6 "1e400" / "inf" in the
  boxes no longer end a timer.

Window-free: no Tk window, no instrument; every dialog is answered here."""
import csv
import json
import math
import tempfile
import threading
import types
import unittest
from pathlib import Path
from unittest import mock

import numpy as np

from test_k6221_failed_off_interlock import Now, Stuck6221
from test_k6221_failed_off_interlock import page as k6221_page
from test_magnet_zero_and_frames import Var, bind, magnet_app, run_dialogs_now, scan_app
from test_moke24_current_limit import page as moke24_page
from test_ppms_motor_debug import app

C = app.CombinedControlApp

_no_tk = mock.patch.object(app.tk, "Tk", side_effect=AssertionError("a test reached a real Tk window"))


def setUpModule():
    _no_tk.start()


def tearDownModule():
    _no_tk.stop()


def log_events(recorder):
    with recorder.log_path.open(encoding="utf-8") as handle:
        return [json.loads(line) for line in handle]


def csv_rows(recorder):
    with recorder.csv_path.open(encoding="utf-8-sig", newline="") as handle:
        return list(csv.reader(handle))


class Folder(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.folder = Path(self.tmp.name)
        patcher = mock.patch.object(app, "np", np)
        patcher.start()
        self.addCleanup(patcher.stop)

    def recorder(self, page, name="B1"):
        recorder = app.ScanRunRecorder(self.folder, page, name, {"test": True})
        self.addCleanup(recorder.close, "test")
        return recorder


class DialogTests(unittest.TestCase):
    """M5: a finish's dialog opens from the Tk event loop, not inside
    drain_ui_queue, where it held every page's post_ui callbacks."""

    def test_it_opens_from_root_after_and_not_once_closing(self):
        a = types.SimpleNamespace(closing=False, root=mock.Mock())
        bind(a, "_dialog_after_drain")
        with mock.patch.object(app.messagebox, "showerror") as shown:
            a._dialog_after_drain("showerror", "Title", "text")
            shown.assert_not_called()
            (delay, callback), _ = a.root.after.call_args
            self.assertEqual(delay, 0)
            callback()
            shown.assert_called_once_with("Title", "text", parent=a.root)
            a.closing = True
            callback()                            # closed meanwhile: nothing more
            a._dialog_after_drain("showerror", "Title", "text")
        self.assertEqual(shown.call_count, 1)
        self.assertEqual(a.root.after.call_count, 1, "nothing booked once closing")

    def test_a_magnet_command_error_is_not_shown_inside_the_drain(self):
        a = magnet_app()
        a.root = mock.Mock()
        with mock.patch.object(app.messagebox, "showerror") as shown:
            a._finish_magnet_task("Read", None, RuntimeError("no reply"), None, False)
            shown.assert_not_called()
            a.root.after.call_args.args[1]()
        shown.assert_called_once()
        self.assertEqual(shown.call_args.args[:2], ("MOKE magnet control", "no reply"))


class MagnetScanRunTests(Folder):
    """The MOKE current scan: its live CSV, log, zeroing and finish."""

    def page(self, recorder, points=((1, 1, 2, 1.0), (1, 2, 2, 0.0))):
        a = scan_app(None)
        a.camera_window = None
        a.magnet_scan_stop_event = threading.Event()
        a.magnet_scan_recorder = recorder
        a.magnet_scan_outputs = None
        a.magnet_scan_stop_reason = ""
        a.magnet_scan_active_key = "OUT"
        a.magnet_scan_completed_points, a.magnet_scan_total_points = 0, len(points)
        a.magnet_scan_eta_var = Var()
        a.magnet_auto_poll_var = Var(False)
        a.magnet_scan_estimated_finish, a.magnet_scan_eta_after_id = None, None
        a.append_log = mock.Mock()
        for name in ("_update_magnet_controls", "_update_keithley_controls", "_update_k6221_controls",
                     "_update_moke_roi_controls", "_append_magnet_scan_point", "_update_magnet_scan_controls"):
            setattr(a, name, mock.Mock())
        a._scan_outputs_detail = C._scan_outputs_detail
        bind(a, "_finish_magnet_scan", "_finish_scan_run", "_close_scan_recorder", "stop_magnet_scan")
        run_dialogs_now(a)
        a.points = list(points)
        return a

    def run_scan(self, a, error=None):
        try:
            a._magnet_scan_worker(a.points, 0.0, 0.0, "OUT", None)
        except Exception as caught:
            error = caught
        while a.ui:
            a.ui.pop(0)()
        with mock.patch.object(app.messagebox, "showerror") as shown:
            a._finish_magnet_scan(error)
        return shown

    def test_completed_files_log_and_both_kepcos_zeroed(self):
        recorder = self.recorder("MOKE")
        a = self.page(recorder)
        shown = self.run_scan(a)
        shown.assert_not_called()
        rows = csv_rows(recorder)
        self.assertEqual(tuple(rows[0]), app.MAGNET_SCAN_CSV_COLUMNS, "Save CSV's columns, unchanged")
        self.assertEqual([row[4] for row in rows[1:]], ["1.0", "0.0"], "every point, live")
        events = [event["event"] for event in log_events(recorder)]
        self.assertEqual(events[:4], ["run_prepared", "scan_started", "point_saved", "point_saved"])
        self.assertLess(events.index("outputs_zeroed"), events.index("run_finished"))
        self.assertEqual(events[-1], "run_finished")
        finished = log_events(recorder)[-1]
        self.assertEqual((finished["outcome"], finished["points_saved"]), ("completed", 2))
        self.assertEqual(finished["outputs"], {"Out-of-plane KEPCO": "zeroed", "In-plane KEPCO": "zeroed"})
        self.assertEqual(a.magnet_status_var.get(), "Completed; both KEPCOs zeroed")
        self.assertTrue(recorder.closed)
        self.assertEqual(a.zero_writes, [("IN", 0.0)])

    def test_stopped_logs_the_button_and_zeroes(self):
        recorder = self.recorder("MOKE")
        a = self.page(recorder)
        a.magnet_scan_running = True
        written = a._write_and_verify_magnet_current

        def write(key, value):
            if value == 1.0:
                a.stop_magnet_scan("Zero both now")    # pressed during the first write
            return written(key, value)

        a._write_and_verify_magnet_current = write
        self.run_scan(a)
        events = log_events(recorder)
        stop = next(event for event in events if event["event"] == "stop_requested")
        self.assertEqual(stop["button"], "Zero both now")
        self.assertEqual((events[-1]["outcome"], events[-1]["stop_reason"]), ("stopped", "Zero both now"))
        self.assertEqual(a.magnet_status_var.get(), "Stopped; both KEPCOs zeroed")
        self.assertEqual(len(a.set_at), 2, "the first point, then the verified 0 A")

    def test_failed_keeps_the_error_and_the_dialog_says_how_each_ended(self):
        recorder = self.recorder("MOKE")
        a = self.page(recorder)
        a._query_gauss_with_recovery = mock.Mock(side_effect=RuntimeError("no GS reply"))
        shown = self.run_scan(a)
        self.assertEqual(a.magnet_status_var.get(), "Failed: no GS reply")
        shown.assert_called_once()
        self.assertEqual(shown.call_args.args[0], "MOKE current scan")
        self.assertIn("no GS reply", shown.call_args.args[1])
        self.assertIn("At the end: Out-of-plane KEPCO zeroed; In-plane KEPCO zeroed", shown.call_args.args[1])
        self.assertEqual(log_events(recorder)[-1]["outcome"], "error")
        self.assertEqual(log_events(recorder)[-1]["error"], "no GS reply")

    def test_a_failing_zero_says_check_it(self):
        recorder = self.recorder("MOKE")
        a = self.page(recorder, points=((1, 1, 2, 1.0), (1, 2, 2, 0.5)))

        def write(key, value):
            if value == 0.0:
                raise RuntimeError("IQ timeout")
            a.set_at.append(0)
            return value

        a._write_and_verify_magnet_current = write
        shown = self.run_scan(a)
        self.assertEqual(a.magnet_status_var.get(),
                         "Completed; Out-of-plane KEPCO zero FAILED; In-plane KEPCO zeroed: check it")
        shown.assert_called_once()
        self.assertEqual(shown.call_args.args[0], "MOKE outputs")
        self.assertIn("CHECK IT", shown.call_args.args[1])
        self.assertIn("Out-of-plane KEPCO zero FAILED: IQ timeout", shown.call_args.args[1])
        self.assertEqual(a.magnet_current_vars["OUT"].get(), "0 A not confirmed")
        self.assertIsNone(a.magnet_last_currents["OUT"], "never kept as 0 A (M2)")

    def test_a_failed_log_write_ends_the_scan_through_its_failure_path(self):
        recorder = self.recorder("MOKE")
        a = self.page(recorder)
        recorder.failure = OSError("disk full")
        shown = self.run_scan(a)
        self.assertTrue(a.magnet_status_var.get().startswith("Failed: The MOKE log could not be written"))
        self.assertEqual(a.magnet_scan_rows, [], "no instrument step after the failed write")
        self.assertEqual(a.magnet_scan_outputs["Out-of-plane KEPCO"], "zeroed", "and the zero still ran")
        shown.assert_called_once()

    def test_the_program_closing_closes_the_log(self):
        recorder = self.recorder("MOKE")
        a = self.page(recorder)
        a.magnet_closed = False
        a.magnet_field_zero_stop_event = threading.Event()
        a.magnet_executor = mock.Mock()
        a.append_magnet_log = mock.Mock()
        for channel in a.magnet_channels.values():
            channel.close = lambda: None
        bind(a, "safe_close_magnets")
        a.safe_close_magnets()
        events = log_events(recorder)
        self.assertEqual([e["event"] for e in events][-2:], ["stop_requested", "run_finished"])
        self.assertEqual(events[-1]["outcome"], "stopped")
        self.assertEqual(events[-1]["outputs"]["In-plane KEPCO"], "0 A written on exit (not read back)")
        self.assertEqual(a.zero_writes, [("OUT", 0.0), ("IN", 0.0)])


class MagnetStartTests(Folder):
    """The Start's order: the inputs, the Yes/No, then begin_scan_run."""

    def stand_in(self):
        from test_magnet_scan_start import stand_in
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
        a.camera_window = types.SimpleNamespace(worker=types.SimpleNamespace(background_enabled=True),
                                                latest_frame=None)
        a._parse_magnet_scan.return_value = ([(1, 1, 3, 1.0), (1, 2, 3, 0.0), (1, 3, 3, 1.0)], 0.1, 0.1, "OUT")
        a.camera_scan_parameter_snapshot.return_value = {"camera": 1}
        a.moke_hardware_parameter_snapshot.return_value = {}
        a._snapshot_variable = C._snapshot_variable
        a._format_scan_duration = C._format_scan_duration
        a._scan_run_parent = C._scan_run_parent
        a.magnet_csv_directory = self.folder
        a.closing = False
        return a

    def test_yes_then_the_run_folder_then_the_worker(self):
        a = self.stand_in()
        order = mock.Mock()
        recorder = mock.Mock(directory=self.folder / "MOKE_x")
        order.begin.return_value = recorder
        order.ask.return_value = True
        with mock.patch.object(app.messagebox, "askyesno", order.ask), \
                mock.patch.object(app, "begin_scan_run", order.begin):
            C.start_magnet_scan(a)
        self.assertEqual([name for name, _args, _kwargs in order.mock_calls], ["ask", "begin"])
        self.assertIn("At every end (completed, stopped or failed) the out-of-plane KEPCO is set to 0 A",
                      order.ask.call_args.args[1])
        args, kwargs = order.begin.call_args
        self.assertEqual((args[1], kwargs["prefix"]), ("MOKE", "MOKE"))
        self.assertEqual(args[3], self.folder, "the page's results folder before any run")
        self.assertEqual(args[4]["camera"], 1, "the page's settings in run_prepared")
        self.assertIs(a.magnet_scan_recorder, recorder)
        self.assertEqual(a.magnet_scan_run_directory, recorder.directory)
        a.magnet_executor.submit.assert_called_once()

    def test_a_cancelled_folder_starts_nothing_and_resumes_the_poll(self):
        a = self.stand_in()
        with mock.patch.object(app.messagebox, "askyesno", return_value=True), \
                mock.patch.object(app, "begin_scan_run", return_value=None):
            C.start_magnet_scan(a)
        a.magnet_executor.submit.assert_not_called()
        self.assertTrue(a.magnet_auto_poll_var.get())
        self.assertFalse(a.magnet_scan_running)

    def test_a_real_run_folder_gets_the_moke_prefix(self):
        a = self.stand_in()
        with mock.patch.object(app.messagebox, "askyesno", return_value=True), \
                mock.patch.object(app.filedialog, "askdirectory", return_value=str(self.folder)), \
                mock.patch.object(app.simpledialog, "askstring", return_value=""):
            C.start_magnet_scan(a)
        recorder = a.magnet_scan_recorder
        self.addCleanup(recorder.close, "test")
        self.assertRegex(recorder.directory.name, r"^MOKE_\d{8}_\d{6}$")
        self.assertEqual(log_events(recorder)[0]["event"], "run_prepared")

    def test_the_next_start_opens_beside_the_last_run(self):
        self.assertEqual(C._scan_run_parent(self.folder / "MOKE_1", "x"), self.folder)
        self.assertEqual(C._scan_run_parent(None, self.folder), self.folder)


class SaveCSVTests(Folder):
    def test_save_csv_opens_in_the_run_folder(self):
        from test_magnet_minor_review import save_app, scan_row
        a = save_app([scan_row(0, 1.0)], {"scan.channel_key": "OUT"})
        a.magnet_scan_run_directory = self.folder / "MOKE_run"
        a.select_csv_save_directory.return_value = None
        a.save_magnet_scan_csv()
        self.assertEqual(a.select_csv_save_directory.call_args.args[1], self.folder / "MOKE_run")

    def test_moke24_save_csv_opens_in_the_run_folder(self):
        a = mock.Mock()
        a.__dict__.update(active_file_exports=set(), keithley_data_lock=threading.Lock(),
                          keithley_step_rows=[(1.0, 2.0, 0.1, 3)],
                          keithley_scan_run_directory=self.folder / "MOKE24_run")
        a.select_csv_save_directory.return_value = None
        C.save_keithley_csv(a)
        self.assertEqual(a.select_csv_save_directory.call_args.args[1], self.folder / "MOKE24_run")


class Fake2410:
    """A 2410 whose readbacks follow its writes; ``fail`` names a write that
    times out, ``stuck`` keeps :OUTP? at 1."""

    def __init__(self, fail=None, stuck=False):
        self.commands, self.fail, self.stuck = [], fail, stuck
        self.voltage, self.output = "0", "0"

    def write(self, command):
        if command == self.fail:
            raise TimeoutError(f"VI_ERROR_TMO on {command}")
        self.commands.append(command)
        if command.startswith(":SOUR:VOLT ") and not command.startswith(":SOUR:VOLT:"):
            self.voltage = command.split(" ", 1)[1]
        elif command in (":OUTP ON", ":OUTP OFF"):
            self.output = "1" if command == ":OUTP ON" or self.stuck else "0"

    def query(self, command):
        return {"SYST:ERR?": "0,No error", ":SOUR:VOLT:PROT?": "200", ":SOUR:VOLT:RANG?": "20",
                ":SOUR:VOLT?": self.voltage, ":OUTP?": self.output}[command]


class Keithley2410ReadBackTests(unittest.TestCase):
    def controller(self, instrument):
        logged = []
        controller = app.Keithley2410Controller(logged.append)
        controller.instrument = instrument
        controller.source_mode = "VOLT"
        return controller, logged

    def test_0_v_and_off_read_back(self):
        instrument = Fake2410()
        instrument.voltage, instrument.output = "5", "1"
        controller, _logged = self.controller(instrument)
        controller.output_off_read_back()
        self.assertEqual(instrument.commands, [":SOUR:VOLT 0", ":OUTP OFF"])

    def test_an_output_still_on_raises(self):
        instrument = Fake2410(stuck=True)
        instrument.output = "1"
        controller, _logged = self.controller(instrument)
        with self.assertRaisesRegex(RuntimeError, r"did not read back 0 and OFF \(:OUTP\? 1"):
            controller.output_off_read_back()

    def test_a_failed_write_whose_readback_is_off_is_logged_not_raised(self):
        instrument = Fake2410(fail=":SOUR:VOLT 0")
        controller, logged = self.controller(instrument)
        controller.output_off_read_back()
        self.assertTrue(any("VI_ERROR_TMO" in line for line in logged))

    def test_not_connected_raises(self):
        with self.assertRaisesRegex(RuntimeError, "not connected"):
            app.Keithley2410Controller(lambda _m: None).output_off_read_back()


class StopEvent:
    """Each wait() runs the next step, never sleeps; set() stops."""

    def __init__(self, steps=()):
        self.steps, self.stopped = list(steps), False

    def is_set(self):
        return self.stopped

    def set(self):
        self.stopped = True

    def clear(self):
        self.stopped = False

    def wait(self, _timeout):
        if self.steps:
            self.steps.pop(0)()
        return self.stopped


class Moke24RunTests(Folder):
    def page(self, recorder, instrument=None):
        a = moke24_page()
        a.keithley_controller.instrument = instrument or Fake2410()
        a.keithley_controller.compliance_a = 0.001
        a.keithley_scan_recorder = recorder
        a.keithley_scan_settings = {"instrument.model": "2410"}
        a.keithley_scan_outputs = None
        a.keithley_scan_stop_reason = ""
        a.keithley_scan_running = True
        a.keithley_scan_start_time = 0.0
        a.keithley_raw_rows, a.keithley_step_rows, a.keithley_current_samples = [], [], []
        a.keithley_collecting, a.keithley_current_voltage = False, 0.0
        a.keithley_point_set_monotonic = math.inf
        a.keithley_stop_event = StopEvent()
        a.keithley_scan_eta_var = Var()
        a.keithley_scan_completed_points = a.keithley_scan_total_points = 0
        a.keithley_scan_estimated_finish = a.keithley_scan_eta_after_id = None
        a.thread_log = mock.Mock()
        a.append_log = mock.Mock()
        a._append_keithley_step_row = mock.Mock()
        a._update_k6221_controls = mock.Mock()
        a._update_keithley_controls = mock.Mock()
        a.camera_window = None
        a.closing = False
        return a

    def finish(self, a, error=None):
        with mock.patch.object(app.messagebox, "showerror") as shown:
            a._finish_keithley_scan(error)
        return shown

    def test_completed_live_csv_log_and_0_v_off_read_back(self):
        recorder = self.recorder("MOKE24")
        a = self.page(recorder)
        a._keithley_scan_worker([1.0, 2.0, 1.0], 0.0, 0.0, None)
        shown = self.finish(a)
        shown.assert_not_called()
        rows = csv_rows(recorder)
        self.assertEqual(tuple(rows[0]), app.KEITHLEY_SCAN_CSV_COLUMNS)
        self.assertEqual([row[0] for row in rows[1:]], ["1.0", "2.0", "1.0"])
        self.assertEqual(a.keithley_controller.instrument.commands[-2:], [":SOUR:VOLT 0", ":OUTP OFF"])
        events = [event["event"] for event in log_events(recorder)]
        self.assertEqual(events[:2], ["run_prepared", "scan_started"])
        self.assertEqual(events.count("point_saved"), 3)
        self.assertLess(events.index("outputs_zeroed"), events.index("run_finished"))
        self.assertEqual(a.keithley_status_var.get(), "Completed; 2410 zeroed")
        self.assertEqual(a.keithley_voltage_var.get(), "Output: 0 V / OFF")
        self.assertEqual(a.keithley_current_voltage, 0.0)

    def test_an_off_not_read_back_is_check_the_2410_and_unknown(self):
        recorder = self.recorder("MOKE24")
        instrument = Fake2410(stuck=True)
        a = self.page(recorder, instrument)
        a._keithley_scan_worker([1.0], 0.0, 0.0, None)
        shown = self.finish(a)
        self.assertEqual(a.keithley_voltage_var.get(), "Output: check the 2410")
        self.assertTrue(math.isnan(a.keithley_current_voltage), "the voltage is unknown (M3)")
        self.assertEqual(a.keithley_status_var.get(), "Completed; 2410 zero FAILED: check it")
        shown.assert_called_once()
        self.assertIn("CHECK IT", shown.call_args.args[1])

    def test_failed_keeps_the_scan_error_not_the_off(self):
        recorder = self.recorder("MOKE24")
        instrument = Fake2410(fail=":SOUR:VOLT 2.000000")
        a = self.page(recorder, instrument)
        with self.assertRaisesRegex(TimeoutError, "SOUR:VOLT 2.000000"):
            a._keithley_scan_worker([1.0, 2.0], 0.0, 0.0, None)
        shown = self.finish(a, TimeoutError("VI_ERROR_TMO on :SOUR:VOLT 2.000000"))
        self.assertTrue(a.keithley_status_var.get().startswith("Failed: VI_ERROR_TMO"))
        self.assertIn("At the end: 2410 zeroed", shown.call_args.args[1])
        self.assertEqual(log_events(recorder)[-1]["outcome"], "error")

    def test_stopped_by_the_red_button(self):
        recorder = self.recorder("MOKE24")
        a = self.page(recorder)
        a.keithley_stop_event = StopEvent([lambda: a.stop_keithley_scan("zero / output off")])
        a._keithley_scan_worker([1.0, 2.0], 1.0, 0.0, None)
        self.finish(a)
        stop = [e for e in log_events(recorder) if e["event"] == "stop_requested"]
        self.assertEqual([e["button"] for e in stop], ["zero / output off"])
        self.assertEqual(a.keithley_status_var.get(), "Stopped; 2410 zeroed")

    def test_frames_begun_before_the_voltage_step_are_not_averaged(self):
        """C2-2: as the magnet scan, only an average begun after set_voltage
        returned counts; with none in time the point is NaN."""
        recorder = self.recorder("MOKE24")
        a = self.page(recorder)
        a.camera_window = types.SimpleNamespace(latest_frame=None)

        def sample(value, after):
            a.ingest_2410_roi_sample(value, a.keithley_point_set_monotonic + after)

        a.keithley_stop_event = StopEvent([
            lambda: None,                    # settle
            lambda: sample(1.0, -0.3),       # acquire: begun before the step
            lambda: sample(2.0, +0.01),      # then one begun after it
        ])
        a._keithley_scan_worker([1.0], 0.0, 0.0, None)
        (row,) = a.keithley_step_rows
        self.assertEqual((row[1], row[3]), (2.0, 1), "the new-voltage sample only")
        self.assertEqual(len(a.keithley_raw_rows), 2, "the raw trace keeps every frame")

    def test_the_camera_passes_the_average_start(self):
        camera = types.SimpleNamespace(roi={"cx": 3.0, "cy": 3.0, "w": 4.0, "h": 4.0, "angle": 0.0},
                                       mapping=object(), selecting_roi=False, roi_start=None, roi_end=None,
                                       app=mock.Mock())
        frame = types.SimpleNamespace(raw=np.ones((6, 6), dtype=np.uint16),
                                      difference=np.full((6, 6), 3.0, dtype=np.float32),
                                      display_difference=np.full((6, 6), 3.0, dtype=np.float32),
                                      window_start_monotonic=42.0)
        app.TkCameraWindow._ingest_roi_values(camera, frame)
        camera.app.ingest_2410_roi_sample.assert_called_once_with(3.0, 42.0)


class Moke24OffDisplayTests(unittest.TestCase):
    """M3: a failed off is never shown as 0 V / OFF."""

    def test_the_red_button_and_disconnect(self):
        a = moke24_page()
        a.closing = False
        with mock.patch.object(app.messagebox, "showerror") as shown:
            a._finish_keithley_output_off(RuntimeError("2410 failed to turn output off"))
        self.assertEqual(a.keithley_voltage_var.get(), "Output: check the 2410")
        self.assertTrue(math.isnan(a.keithley_current_voltage))
        shown.assert_called_once()
        a._finish_keithley_output_off(None)
        self.assertEqual((a.keithley_voltage_var.get(), a.keithley_current_voltage), ("Output: 0 V / OFF", 0.0))
        with mock.patch.object(app.messagebox, "showerror"):
            a._finish_keithley_disconnect(RuntimeError("OUTP OFF timed out"))
        self.assertEqual(a.keithley_voltage_var.get(), "Output: check the 2410")

    def test_up_down_refuse_an_unknown_voltage(self):
        a = moke24_page()
        a.keithley_manual_step_var = Var("1")
        a.keithley_current_voltage = math.nan
        a._submit_keithley_manual_voltage = mock.Mock()
        with mock.patch.object(app.messagebox, "showwarning") as warned:
            a.step_keithley_manual_voltage(1)
        a._submit_keithley_manual_voltage.assert_not_called()
        self.assertIn("unknown", warned.call_args.args[1])


class CidwmRunTests(Folder):
    def page(self, recorder, instrument, avtech_off=None):
        a = k6221_page(instrument)
        a.closing = False
        run_dialogs_now(a)
        a.cidwm_scan_recorder = recorder
        a.cidwm_scan_source = "6221"
        a.cidwm_scan_status_var = Var()
        a.avtech_output_var = Var()
        a.avtech_output_unknown = False
        a.k6221_scan_running = True
        a.k6221_stop_event.set()           # stopped before the first pulse
        a.avtech_controller = mock.Mock(connected=True)
        if avtech_off is not None:
            a.avtech_controller.switch_off.side_effect = avtech_off
        a.append_log = mock.Mock()
        return a

    def finish(self, a, recorder):
        future = a.k6221_executor.submit(a._cidwm_pulse_scan_worker, {"signs": (1,)}, recorder)
        with mock.patch.object(app.messagebox, "showerror") as shown:
            a._finish_cidwm_pulse_scan(future)
        return shown

    def test_both_sources_off_read_back_and_the_log_closed(self):
        recorder = self.recorder("CIDWM")
        instrument = Stuck6221(fail_off=False)
        a = self.page(recorder, instrument)
        shown = self.finish(a, recorder)
        shown.assert_not_called()
        self.assertEqual(a.cidwm_scan_outputs, {"6221": "zeroed", "Avtech": "zeroed"})
        self.assertEqual(instrument.commands[-3:], ["SOUR:WAVE:ABOR", "CURR 0", "OUTP OFF"])
        a.avtech_controller.switch_off.assert_called_once_with()
        events = [event["event"] for event in log_events(recorder)]
        self.assertEqual(events[0], "run_prepared")
        self.assertIn("scan_started", events)
        self.assertLess(events.index("outputs_zeroed"), events.index("run_finished"))
        self.assertTrue(a.cidwm_scan_status_var.get().startswith("Stopped; 6221 and Avtech zeroed; 0/1"))
        self.assertEqual(a.k6221_output_var.get(), "Output: 0 mA / OFF")
        self.assertEqual(a.avtech_output_var.get(), "Output: OFF")

    def test_a_failed_avtech_off_holds_the_6221_and_frees_nothing_else(self):
        """M4 with the interlocks: the Avtech's failed off holds the 6221;
        the 6221's confirmed off clears only its own flag."""
        recorder = self.recorder("CIDWM")
        a = self.page(recorder, Stuck6221(fail_off=False), avtech_off=RuntimeError("output? failed"))
        a.k6221_output_unknown = True        # an earlier 6221 off had failed
        shown = self.finish(a, recorder)
        self.assertTrue(a.avtech_output_unknown)
        self.assertFalse(a.k6221_output_unknown, "the 6221 read back off")
        self.assertEqual(a.avtech_output_var.get(), "Output: check the Avtech")
        self.assertIn("CHECK IT", shown.call_args.args[1])
        self.assertFalse(a._cidwm_source_ready("6221"), "the 6221 waits for the Avtech")
        with mock.patch.object(app.messagebox, "showwarning") as warned:
            a.apply_k6221_output(1)
        self.assertIn("Avtech's output state is unknown", warned.call_args.args[1])
        self.assertFalse(a.k6221_busy, "nothing sent")

    def test_a_failed_6221_off_keeps_the_avtech_waiting(self):
        recorder = self.recorder("CIDWM")
        a = self.page(recorder, Stuck6221())
        self.finish(a, recorder)
        self.assertTrue(a.k6221_output_unknown)
        self.assertFalse(a.avtech_output_unknown)
        self.assertIn("6221 zero FAILED", a.cidwm_scan_status_var.get())

    def test_the_6221_readback_decides(self):
        logged = []
        controller = types.SimpleNamespace(lock=threading.RLock(), logger=logged.append,
                                           output_off=mock.Mock(side_effect=RuntimeError("OUTP OFF: TMO")),
                                           instrument=types.SimpleNamespace(query=lambda _c: "0"))
        C._k6221_off_read_back(controller)
        self.assertIn("OUTP? read back 0", logged[0])
        controller.instrument = types.SimpleNamespace(query=lambda _c: "1")
        with self.assertRaisesRegex(RuntimeError, r"did not read back OFF \(OUTP\? 1\)"):
            C._k6221_off_read_back(controller)


class CidwmStartTests(Folder):
    def test_yes_then_the_cidwm_run_folder(self):
        from test_cidwm_6221_row_and_fields import box_page
        a = box_page(current="2", count="1")
        a.k6221_controller.instrument = object()
        a.k6221_busy = a.avtech_busy = a.k6221_scan_running = False
        a.magnet_scan_running = a.keithley_scan_running = False
        a.k6221_output_unknown = a.avtech_output_unknown = False
        a.k6221_safety_var = Var(True)
        a.k6221_roi = None
        a._cidwm_snapshot = mock.Mock()
        a.cidwm_scan_directory = self.folder
        a.k6221_executor = mock.Mock()
        for name in ("_update_k6221_controls", "_update_keithley_controls", "_update_magnet_scan_controls",
                     "thread_log"):
            setattr(a, name, mock.Mock())
        a.cidwm_scan_status_var, a.k6221_status_var = Var(), Var()
        a.root = mock.Mock()
        a.k6221_stop_event = threading.Event()
        order = mock.Mock()
        order.ask.return_value = True
        order.begin.return_value = recorder = mock.Mock(directory=self.folder / "CIDWM_x")
        with mock.patch.object(app.messagebox, "askyesno", order.ask), \
                mock.patch.object(app, "begin_scan_run", order.begin):
            a.start_cidwm_pulse_scan()
        self.assertEqual([name for name, _a, _k in order.mock_calls][:2], ["ask", "begin"])
        self.assertIn("at every end (completed, stopped or failed) the 6221 and the Avtech are switched off",
                      order.ask.call_args.args[1])
        args, kwargs = order.begin.call_args
        self.assertEqual((args[1], kwargs["prefix"]), ("CIDWM", "CIDWM"))
        self.assertEqual(args[4]["pulse_source"], "6221")
        a.k6221_executor.submit.assert_called_once_with(a._cidwm_pulse_scan_worker, mock.ANY, recorder)
        self.assertTrue(a.k6221_scan_running)


class MagnetConnectionTests(unittest.TestCase):
    """M1: a dropped gaussmeter no longer locks Zero, Zero both and
    Disconnect; Connect writes 0 A to every connected, armed BOP first."""

    def controls_page(self):
        a = mock.Mock()
        bop = types.SimpleNamespace(connected=True, output_armed=True)
        dropped = types.SimpleNamespace(connected=False)
        gauss = types.SimpleNamespace(connected=True)
        a.magnet_channels = {"OUT": types.SimpleNamespace(bop=bop, gauss=dropped),
                             "IN": types.SimpleNamespace(bop=types.SimpleNamespace(connected=True,
                                                                                   output_armed=False),
                                                         gauss=gauss)}
        a.__dict__.update(magnet_busy=False, magnet_scan_running=False)
        a._selected_magnet_switch_key.return_value = "OUT"
        a.magnet_safety_var = Var(True)
        a.magnet_switch_buttons, a.magnet_port_combos = [], {}
        a.magnet_channel_control_widgets = {
            key: {name: mock.Mock() for name in ("target", "step", "write", "flip", "down", "up", "zero",
                                                  "field_zero")} for key in ("OUT", "IN")}
        bind(a, "_update_magnet_controls", "_magnet_devices_ready", "_magnet_bop_connected",
             "_magnet_any_device_connected")
        return a

    @staticmethod
    def state(widget):
        return widget.configure.call_args.kwargs["state"]

    def test_zero_zero_both_and_disconnect_stay_enabled(self):
        a = self.controls_page()
        a._update_magnet_controls()
        self.assertEqual(self.state(a.magnet_zero_button), "normal")
        self.assertEqual(self.state(a.magnet_disconnect_button), "normal")
        self.assertEqual(self.state(a.magnet_channel_control_widgets["OUT"]["zero"]), "normal")
        self.assertEqual(self.state(a.magnet_channel_control_widgets["OUT"]["write"]), "disabled")
        self.assertEqual(self.state(a.magnet_connect_button), "normal", "Connect can reopen all four")

    def test_the_summary_names_what_is_missing(self):
        a = self.controls_page()
        a.magnet_connection_var = Var()
        bind(a, "_refresh_magnet_connection_summary")
        a._refresh_magnet_connection_summary()
        self.assertEqual(a.magnet_connection_var.get(),
                         "Partly connected (no out-of-plane gaussmeter): press Connect again")

    def test_connect_writes_0_a_to_armed_bops_before_closing(self):
        a = magnet_app()
        events = []
        armed = types.SimpleNamespace(connected=True, output_armed=True,
                                      set_current=lambda value: events.append(("OUT 0 A", value)))
        idle = types.SimpleNamespace(connected=True, output_armed=False,
                                     set_current=lambda value: events.append(("IN 0 A", value)))
        for key, bop in (("OUT", armed), ("IN", idle)):
            a.magnet_channels[key] = types.SimpleNamespace(
                bop=bop, gauss=None, close=lambda key=key: events.append((f"{key} closed",)))
        a.magnet_port_vars = {k: Var(f"COM{i}") for i, k in enumerate(("OUT_BOP", "OUT_GAUSS", "IN_BOP",
                                                                          "IN_GAUSS"))}
        a._selected_magnet_switch_key.return_value = "OUT"
        a._selected_magnet_ports.return_value = {k: v.get() for k, v in a.magnet_port_vars.items()}
        bind(a, "connect_magnets", "_magnet_zero_armed_bops")
        with mock.patch.object(app, "MagnetKepcoBOP", side_effect=RuntimeError("COM0 busy")), \
                mock.patch.object(app.messagebox, "showerror") as shown:
            a.connect_magnets()
            a.magnet_executor.run_next()
            a.ui.pop(0)()
        self.assertEqual(events[:3], [("OUT 0 A", 0.0), ("OUT closed",), ("IN closed",)],
                         "0 A to the armed BOP only, before any port is closed")
        shown.assert_called_once()


class MagnetZeroReportTests(unittest.TestCase):
    """M2: a zero that was not confirmed is never shown or kept as 0 A, and
    Zero both's failed writes are reported."""

    def test_field_zero_stopped_with_a_failed_zero(self):
        a = types.SimpleNamespace()
        bop = types.SimpleNamespace(connected=True, output_armed=True, query_current=lambda: 0.3)
        a.magnet_channels = {"OUT": types.SimpleNamespace(bop=bop, gauss=types.SimpleNamespace(connected=True))}
        a.magnet_last_currents = {"OUT": 0.3}
        a.logged = []
        a.thread_log = a.logged.append
        stop = threading.Event()
        a.magnet_field_zero_stop_event = stop

        def write(key, value):
            if value == 0.0:
                raise RuntimeError("IQ timeout")
            stop.set()                     # Stop pressed after the probe current
            return value

        a._write_and_verify_magnet_current = write
        a._query_gauss_with_recovery = lambda key: 20.0
        bind(a, "_zero_magnet_field_worker", "_require_magnet_bop", "_require_gauss_meter", "_magnet_log",
             "_scan_page_log", "_scan_log")
        result = a._zero_magnet_field_worker("OUT")
        self.assertTrue(result["cancelled"])
        self.assertIsNone(result["current"], "0 A not confirmed: unknown, not 0.0")
        self.assertIsNone(a.magnet_last_currents["OUT"])
        self.assertTrue(any("0 A not confirmed" in line for line in a.logged), "in the page log")

    def test_field_zero_finish_shows_not_confirmed(self):
        a = magnet_app()
        a.magnet_status_var = Var()
        a.magnet_field_vars = {"OUT": Var(), "IN": Var()}
        a.magnet_safety_var = Var(True)
        a.magnet_field_zero_stop_event = threading.Event()
        a.append_log = mock.Mock()
        a._zero_magnet_field_worker = lambda key: {"current": None, "field": 3.0, "iterations": 0,
                                                    "cancelled": True}
        a._require_gauss_meter = lambda key: None
        bind(a, "zero_magnet_field")
        with mock.patch.object(app.messagebox, "showwarning") as warned:
            a.zero_magnet_field("OUT")
            a.magnet_executor.run_next()
            a.ui.pop(0)()
        self.assertEqual(a.magnet_current_vars["OUT"].get(), "0 A not confirmed")
        self.assertIsNone(a.magnet_last_currents["OUT"])
        self.assertIn("0 A NOT confirmed", a.append_log.call_args.args[0])
        warned.assert_called_once()

    def test_zero_both_reports_a_failed_inactive_write(self):
        a = magnet_app()
        a.magnet_status_var = Var()
        a.append_log = mock.Mock()

        def refuse(value):
            raise TimeoutError("write timeout")

        a.magnet_channels["IN"].bop.set_current = refuse
        with mock.patch.object(app.time, "sleep"), mock.patch.object(app.messagebox, "showwarning") as warned:
            a.zero_both_magnets()
            a.magnet_executor.run_next()
            a.ui.pop(0)()
        status = a.append_log.call_args.args[0]
        self.assertIn("In-plane: 0 A write failed (write timeout)", status)
        self.assertIn("check the KEPCO output", status)
        self.assertEqual(a.magnet_current_vars["IN"].get(), "-- A (0 A write failed)")
        warned.assert_called_once()

    def test_zero_both_with_the_active_bop_closed_says_so(self):
        a = magnet_app()
        a.magnet_channels["OUT"].bop = None
        with mock.patch.object(app.time, "sleep"), mock.patch.object(app.messagebox, "showerror") as shown:
            a.zero_both_magnets()
            a.magnet_executor.run_next()
            a.ui.pop(0)()
        self.assertIn("(the active path) is not connected", shown.call_args.args[1])
        self.assertIn(("IN", 0.0), a.writes, "the other path still got 0 A")


class OverflowTests(unittest.TestCase):
    """M6: "1e400" in an IntVar box raised OverflowError and "inf" passed;
    a timer loop ended for good."""

    class Box:
        def __init__(self, error=None, value=None):
            self.error, self.value = error, value

        def get(self):
            if self.error is not None:
                raise self.error
            return self.value

    def test_roi_live_settings_fall_back(self):
        a = types.SimpleNamespace(roi_live_interval_var=self.Box(value=math.inf),
                                  roi_live_max_points_var=self.Box(OverflowError("cannot convert float infinity")))
        self.assertEqual(C._roi_live_settings(a), (app.ROI_LIVE_DEFAULT_INTERVAL_S, app.ROI_LIVE_DEFAULT_MAX_POINTS))

    def test_the_poll_timer_reschedules(self):
        for value in (math.inf, 1e400, 1e300, "nan"):
            with self.subTest(value=value):
                a = mock.Mock()
                a.__dict__.update(closing=False, magnet_closed=False)
                a.magnet_auto_poll_var = Var(False)
                a.magnet_poll_interval_var = self.Box(value=value)
                C._magnet_poll_timer(a)
                delay = a.root.after.call_args.args[0]
                self.assertTrue(200 <= delay <= 3_600_000, delay)

    def test_the_loop_box_is_a_refusal(self):
        a = mock.Mock()
        for name in ("start_current", "end_current", "step_current", "settle", "collect"):
            setattr(a, f"magnet_scan_{name}_var", Var("1"))
        a.magnet_scan_loop_var = self.Box(OverflowError("cannot convert float infinity to integer"))
        with self.assertRaisesRegex(ValueError, "valid numbers"):
            C._parse_magnet_scan(a)


if __name__ == "__main__":
    unittest.main()
