"""Non-local scans on the shared save and end (user, 2026-10-18), and the
Non-local audit fixes C2-1, C2-3, C2-5 and C2-6 (review, 2026-10-18).

- Save: |R| vs time, 2400-IV, 6221-IV and Gate-VI each go through
  begin_scan_run after their Yes/No and write <folder>/<name>/<name>.csv and
  <name>.log (blank name: NL_RvsT / NL_2400IV / NL_6221IV / NL_GateVI_<date>_
  <time>), the log's events in the convention's order, the columns as before.
- Zero at every end (completed, stopped, failed): on the page's worker, after
  the scan's own clean-up, the 6221 off and the Keithley at 0 V and off, each
  read back and each tried whatever the other did; the finish says how.
- C2-1: the Non-local 6221 floats its output low after every *RST.
- C2-3: LIAS? is asked once; a misaligned reply counts as overloaded.
- C2-5: a pulse status callback that raises never skips the switch-off.
- C2-6: a failed Keithley connect closes its VISA session.

Window-free: tkinter.Tk raises, every dialog is mocked, no instrument."""
import csv
import json
import math
import tempfile
import threading
import unittest
from concurrent.futures import Future
from pathlib import Path
from unittest import mock

from test_nonlocal_6221_iv import FakeSource, page as iv6221_page
from test_nonlocal_gate_vi_harmonics import gate_page
from test_nonlocal_keithley import FakeSourceMeter, Var, bare_app, connected_controller
from test_ppms_motor_debug import app


class NoWindows(unittest.TestCase):
    def setUp(self):
        patcher = mock.patch("tkinter.Tk", side_effect=AssertionError("a test tried to open a Tk window"))
        patcher.start()
        self.addCleanup(patcher.stop)
        sleeper = mock.patch.object(app.time, "sleep")
        sleeper.start()
        self.addCleanup(sleeper.stop)
        self.folder = Path(tempfile.mkdtemp())


def events(recorder):
    return [json.loads(line) for line in recorder.log_path.read_text(encoding="utf-8").splitlines()]


def names(recorder):
    return [event["event"] for event in events(recorder) if event["event"] != "note"]


class Meter(FakeSourceMeter):
    """The 2400 that also answers the end's level read-back."""

    def __init__(self, *args, level_stuck=False, **kwargs):
        super().__init__(*args, **kwargs)
        self.level_stuck = level_stuck

    def query(self, command):
        if command.upper() == ":SOUR:VOLT?":
            return "5.0" if self.level_stuck else str(self.voltage)
        if command.upper() == ":SOUR:CURR?":
            return str(self.current_source)
        return super().query(command)


class ZeroSource:
    """The page's 6221 for the Keithley scans: off, read back off."""

    def __init__(self, log, fail=False, connected=True):
        self.log, self.fail, self.source_connected = log, fail, connected

    def output_off(self):
        self.log.append("6221 off")

    def confirm_output_off(self, context):
        if self.fail:
            raise RuntimeError("6221 output is still ON")
        self.log.append(f"6221 confirmed off {context}")


def keithley_page(meter, log, source=None):
    a = bare_app()
    a.closing = False
    a.nonlocal_vi_closed = False
    a.thread_log = log.append
    a.nonlocal_vi_2410_controller = connected_controller(meter)
    a.nonlocal_vi_2410_controller.model = "2400"
    a.nonlocal_vi_2410_controller.logger = log.append
    a.nonlocal_vi_ac_controller = source or ZeroSource(log)
    a.nonlocal_vi_interlock = app.NonLocalVIInterlock()
    a.nonlocal_keithley_stop_event = threading.Event()
    a.nonlocal_keithley_output_off = False
    a.posted = []
    a.post_ui = a.posted.append
    for name in ("nonlocal_vi_status_var", "nonlocal_vi_output_var", "nonlocal_iv6221_source_var",
                 "nonlocal_iv_point_var", "nonlocal_iv_eta_var", "nonlocal_iv_keithley_var"):
        setattr(a, name, Var())
    a._update_nonlocal_vi_controls = mock.Mock()
    a._redraw_nonlocal_iv_plot = a._redraw_nonlocal_resistance_plot = mock.Mock()
    a.root = mock.Mock()
    a.dialogs = []
    a.root.after.side_effect = lambda _ms, callback: a.dialogs.append(callback)
    return a


def run_job(a, recorder, scan, body):
    """The page's worker as the Start submits it, and the future the finish gets."""
    a._nonlocal_run_started(recorder)
    future = Future()
    try:
        future.set_result(a._nonlocal_scan_job(recorder, scan, body))
    except BaseException as error:
        future.set_exception(error)
    return future


# ----------------------------------------------------------------- 2400-IV
class SweepRunTests(NoWindows):
    def make(self, **meter):
        self.log = []
        self.meter = Meter(**meter)
        self.a = keithley_page(self.meter, self.log)
        self.a.nonlocal_iv_total_points = 3
        self.recorder = app.ScanRunRecorder(self.folder, "NL_2400IV", "", {"points": 3}, label="2400-IV")
        return self.a

    def sweep(self, stop_after=None, read_error=None):
        settings = {"voltages": [-1.0, 0.0, 1.0], "current_limit_a": 1e-3, "settle_s": 0.0}
        if read_error is not None:
            original = self.meter.query
            self.meter.query = lambda command: (_ for _ in ()).throw(read_error) if command == ":READ?" \
                else original(command)
        if stop_after is not None:
            post = self.a.post_ui

            def counting(callback):
                post(callback)
                if len(self.a.posted) >= stop_after:
                    self.a.nonlocal_keithley_stop_event.set()
            self.a.post_ui = counting
        future = run_job(self.a, self.recorder, "iv", lambda: self.a._nonlocal_iv_worker(settings, self.recorder))
        self.a.nonlocal_keithley_task = "iv"
        self.a._finish_nonlocal_iv_sweep(future)
        return future

    def test_completed_saves_every_point_and_zeroes_both_after_the_sweeps_own_clean_up(self):
        self.make()
        self.sweep()
        self.assertTrue(self.recorder.directory.name.startswith("NL_2400IV_"))
        with self.recorder.csv_path.open(encoding="utf-8-sig") as file:
            rows = list(csv.reader(file))
        self.assertEqual(rows[0], ["point", "set_voltage_V", "source_voltage_V", "current_A",
                                   "elapsed_s", "at_current_limit"], "the columns as before, in order")
        self.assertEqual([row[:2] for row in rows[1:]], [["1", "-1"], ["2", "0"], ["3", "1"]])
        self.assertEqual(names(self.recorder), ["run_prepared", "scan_started"] + ["point_saved"] * 3
                         + ["outputs_zeroed", "run_finished"])
        last = events(self.recorder)[-1]
        self.assertEqual((last["outcome"], last["points_saved"]), ("completed", 3))
        self.assertEqual(last["outputs"], {"6221": "zeroed", "Keithley 2400": "zeroed"})
        zeroed = [event for event in events(self.recorder) if event["event"] == "outputs_zeroed"][0]
        self.assertEqual((zeroed["6221"], zeroed["keithley"], zeroed["keithley_model"]),
                         ("zeroed", "zeroed", "2400"))
        # The sweep's own clean-up (the pulse setup again) first, then the
        # end's zeroing: 6221 off read back, Keithley 0 V, off, both read back.
        commands = self.meter.commands
        last_reset = len(commands) - 1 - commands[::-1].index("*RST")
        self.assertEqual(commands[last_reset + 1:][-2:], [":SOUR:VOLT 0", ":OUTP OFF"])
        self.assertEqual([line for line in self.log if line.startswith("6221")][-2:],
                         ["6221 off", "6221 confirmed off at the scan end"])
        self.assertEqual((self.meter.output, self.meter.voltage), (0, 0.0))
        self.assertEqual(self.a.nonlocal_vi_status_var.get(),
                         f"2400-IV: Completed; Keithley 2400 and 6221 zeroed; 3 points saved in "
                         f"{self.recorder.directory.name}")
        self.assertEqual(self.a.nonlocal_vi_output_var.get(), "Keithley 2400 and 6221 outputs: OFF")
        self.assertEqual(self.a.dialogs, [], "nothing to check: no dialog")
        self.assertTrue(self.recorder.closed)
        self.assertIsNone(self.a.nonlocal_recorder)

    def test_stopped_zeroes_both_and_says_stopped(self):
        self.make()
        self.sweep(stop_after=3)   # the Keithley's state, the 6221's, then point 1
        last = events(self.recorder)[-1]
        self.assertEqual(last["outcome"], "stopped")
        self.assertIn("2400-IV sweep stopped", last["stop_reason"])
        self.assertTrue(self.a.nonlocal_vi_status_var.get().startswith(
            "2400-IV: Stopped; Keithley 2400 and 6221 zeroed; 1/3 points saved in NL_2400IV_"))
        self.assertEqual(self.meter.output, 0)

    def test_failed_keeps_the_error_and_its_dialog_says_how_each_output_ended(self):
        self.make()
        self.sweep(read_error=RuntimeError("VI_ERROR_TMO"))
        last = events(self.recorder)[-1]
        self.assertEqual((last["outcome"], last["error"]), ("error", "VI_ERROR_TMO"))
        self.assertTrue(self.a.nonlocal_vi_status_var.get().startswith(
            "2400-IV: Failed: VI_ERROR_TMO; Keithley 2400 and 6221 zeroed; 0/3 points saved"))
        # The dialog opens from the Tk event loop, after the log is closed.
        self.assertEqual(len(self.a.dialogs), 1)
        with mock.patch.object(app.messagebox, "showerror") as showerror:
            self.a.dialogs[0]()
        title, text = showerror.call_args.args
        self.assertEqual(title, "2400-IV failed")
        self.assertIn("VI_ERROR_TMO\n\nAt the end: 6221 zeroed; Keithley 2400 zeroed", text)

    def test_a_failing_zero_is_reported_and_the_other_output_still_zeroed(self):
        self.make(level_stuck=True)
        self.sweep()
        results = events(self.recorder)[-1]["outputs"]
        self.assertEqual(results["6221"], "zeroed")
        self.assertTrue(results["Keithley 2400"].startswith("zero FAILED: Keithley 2400 level reads 5 V"))
        self.assertIn("6221 zeroed; Keithley 2400 zero FAILED: check it", self.a.nonlocal_vi_status_var.get())
        self.assertEqual(self.a.nonlocal_vi_output_var.get(), "6221 output: OFF; Keithley 2400 output: check it")
        with mock.patch.object(app.messagebox, "showerror") as showerror:
            self.a.dialogs[0]()
        self.assertEqual(showerror.call_args.args[0], "2400-IV outputs")
        self.assertIn("not confirmed at zero: CHECK IT", showerror.call_args.args[1])

    def test_a_6221_that_will_not_read_back_off_does_not_stop_the_keithley_zero(self):
        self.make()
        self.a.nonlocal_vi_ac_controller = ZeroSource(self.log, fail=True)
        results = self.a._nonlocal_zero_outputs(self.recorder)
        self.assertEqual(results, {"6221": "zero FAILED: 6221 output is still ON", "Keithley 2400": "zeroed"})
        self.assertEqual(list(results), ["6221", "Keithley 2400"], "the 6221 first, as the page's OFF")
        self.assertEqual(self.meter.commands[-2:], [":SOUR:VOLT 0", ":OUTP OFF"])
        self.a.nonlocal_vi_ac_controller = ZeroSource(self.log, connected=False)
        self.a.nonlocal_vi_2410_controller.instrument = None
        self.assertEqual(self.a._nonlocal_zero_outputs(), {"6221": "not connected",
                                                           "Keithley 2400": "not connected"})


# ------------------------------------------------------------- |R| vs time
class ResistanceRunTests(NoWindows):
    def test_a_continuous_measurement_saves_every_reading_and_its_stop_zeroes_both(self):
        log = []
        meter = Meter(resistance_ohm=1e6)
        a = keithley_page(meter, log)
        recorder = app.ScanRunRecorder(self.folder, "NL_RvsT", "", {}, label="|R| vs time")
        stop = a.nonlocal_keithley_stop_event
        post = a.post_ui

        def counting(callback):
            post(callback)
            if len(a.posted) > 8:
                stop.set()
                a.nonlocal_keithley_task = "resistance"
                a.stop_nonlocal_keithley_task("Stop measurement")

        a.post_ui = counting
        with mock.patch.object(stop, "wait", side_effect=lambda timeout=None: stop.is_set()):
            future = run_job(a, recorder, "resistance", lambda: a._nonlocal_resistance_worker(recorder))
        a.nonlocal_keithley_task = "resistance"
        a._finish_nonlocal_resistance(future)
        self.assertTrue(recorder.directory.name.startswith("NL_RvsT_"), "the prefix, not the label")
        sequence = names(recorder)
        self.assertEqual(sequence[:2], ["run_prepared", "scan_started"])
        self.assertEqual(sequence[-3:], ["stop_requested", "outputs_zeroed", "run_finished"])
        stops = [event for event in events(recorder) if event["event"] == "stop_requested"]
        self.assertEqual(stops[0]["button"], "Stop measurement")
        with recorder.csv_path.open(encoding="utf-8-sig") as file:
            rows = list(csv.DictReader(file))
        self.assertEqual(len(rows), sequence.count("point_saved"))
        self.assertGreater(len(rows), 0)
        self.assertEqual(list(rows[0]), list(app.CombinedControlApp.NONLOCAL_RESISTANCE_COLUMNS))
        self.assertEqual(events(recorder)[-1]["outcome"], "stopped")
        self.assertTrue(a.nonlocal_vi_status_var.get().startswith(
            "|R| vs time: Stopped; Keithley 2400 and 6221 zeroed; "))
        self.assertEqual((meter.output, meter.mode), (0, "VOLT"))

    def test_the_stop_text_says_both_are_zeroed(self):
        a = keithley_page(Meter(), [])
        for task, start in (("resistance", "Stopping |R| vs time"), ("iv", "Stopping the 2400-IV sweep"),
                            ("iv6221", "Stopping the 6221-IV scan")):
            a.nonlocal_keithley_task = task
            a.nonlocal_keithley_stop_event.clear()
            a._update_nonlocal_keithley_controls = mock.Mock()
            a.stop_nonlocal_keithley_task("Stop")
            self.assertTrue(a.nonlocal_keithley_stop_event.is_set())
            self.assertTrue(a.nonlocal_vi_status_var.get().startswith(start))
            self.assertIn("zeroed next", a.nonlocal_vi_status_var.get())


# ----------------------------------------------------------------- 6221-IV
class IV6221RunTests(NoWindows):
    def setUp(self):
        super().setUp()
        for name, value in (("NONLOCAL_IV6221_FIRST_SETTLE_TAUS", 0.0), ("LOCKIN_SETTLE_TAUS", 0.0),
                            ("NONLOCAL_IV6221_REFERENCE_WAIT_S", 0.0)):
            patcher = mock.patch.object(app, name, value)
            patcher.start()
            self.addCleanup(patcher.stop)

    def run_scan(self, source, keithley_zero_error=None):
        a = iv6221_page(source, start="0.1", end="0.2", step="0.1")
        log = []
        keithley = mock.Mock(connected=True, model="2400")
        if keithley_zero_error is not None:
            keithley.zero_output.side_effect = keithley_zero_error
        a.nonlocal_vi_2410_controller = keithley
        a.nonlocal_vi_interlock = app.NonLocalVIInterlock()
        a.nonlocal_keithley_stop_event = threading.Event()
        a.closing = a.nonlocal_vi_closed = False
        a.thread_log = log.append
        a.post_ui = lambda callback: None
        a._publish_reference_sync = a._show_nonlocal_mfli_lock = mock.Mock()
        for name in ("nonlocal_iv6221_source_var", "nonlocal_iv6221_point_var", "nonlocal_vi_status_var",
                     "nonlocal_vi_output_var", "nonlocal_iv6221_status_var"):
            setattr(a, name, Var())
        a._set_mfli_lock_state = a._redraw_nonlocal_iv6221_plots = a._update_nonlocal_vi_controls = mock.Mock()
        a.root = mock.Mock()
        a.root.after.side_effect = lambda _ms, callback: None
        settings = a._parse_nonlocal_iv6221_settings()
        settings.update({"settle_s": 0.0, "acquire_s": 1e-9})
        a.nonlocal_iv6221_total_points = len(settings["plan"])
        recorder = app.ScanRunRecorder(self.folder, "NL_6221IV", "", {}, label="6221-IV")
        future = run_job(a, recorder, "iv6221", lambda: a._nonlocal_iv6221_worker(settings, recorder))
        a.nonlocal_keithley_task = "iv6221"
        a._finish_nonlocal_iv6221(future)
        return a, recorder, keithley

    def test_the_end_zeroes_the_6221_after_its_own_off_then_the_keithley(self):
        source = FakeSource()
        a, recorder, keithley = self.run_scan(source)
        self.assertEqual(source.calls[-4:], [("output_off",), ("confirm_output_off",)] * 2,
                         "the scan's own off, then the end's zeroing")
        keithley.zero_output.assert_called_once_with("at the scan end")
        self.assertEqual(names(recorder), ["run_prepared", "scan_started", "frequency_set_up", "point_saved",
                                           "point_saved", "outputs_zeroed", "run_finished"])
        with recorder.csv_path.open(encoding="utf-8-sig") as file:
            rows = list(csv.reader(file))
        self.assertEqual(rows[0], app.CombinedControlApp._nonlocal_iv6221_header(a._parse_nonlocal_iv6221_settings()))
        self.assertEqual(len(rows), 3)
        self.assertEqual(a.nonlocal_iv6221_status_var.get(), "Completed; Keithley 2400 and 6221 zeroed")
        self.assertEqual(a.nonlocal_iv6221_source_var.get(), "6221: OFF")

    def test_a_failed_keithley_zero_is_reported_beside_a_completed_scan(self):
        _a, recorder, _keithley = self.run_scan(FakeSource(), keithley_zero_error=RuntimeError("bus"))
        last = events(recorder)[-1]
        self.assertEqual((last["outcome"], last["outputs"]["Keithley 2400"]), ("completed", "zero FAILED: bus"))
        self.assertIn("Keithley 2400 zero FAILED: check it", last["status"])

    def test_a_failed_read_still_zeroes_both(self):
        source = FakeSource(fail_read=True)
        a, recorder, keithley = self.run_scan(source)
        self.assertEqual(events(recorder)[-1]["outcome"], "error")
        keithley.zero_output.assert_called_once()
        self.assertFalse(source.output_on)
        self.assertEqual(a.nonlocal_iv6221_status_var.get(), "Failed: VI_ERROR_TMO; Keithley 2400 and 6221 zeroed")


# ----------------------------------------------------------------- Gate-VI
class GateVIRunTests(NoWindows):
    def scan_page(self):
        a = gate_page("1", "2")
        a.closing = a.nonlocal_vi_closed = False
        a.thread_log = lambda message: None
        a.nonlocal_vi_stop_event = threading.Event()
        a.nonlocal_vi_interlock = app.NonLocalVIInterlock()
        a.nonlocal_vi_data_lock = threading.Lock()
        a.nonlocal_vi_time_rows, a.nonlocal_vi_summary_rows = [], []
        a.nonlocal_vi_pulse_rows, a.nonlocal_vi_current_rows, a.nonlocal_vi_resistance_rows = [], [], []
        a.post_ui = lambda callback: callback()
        a._redraw_nonlocal_vi_plots = a._publish_reference_sync = a._publish_nonlocal_vi_samples = mock.Mock()
        a.nonlocal_vi_progress = mock.Mock()
        for name in ("nonlocal_vi_eta_var", "nonlocal_vi_status_var", "nonlocal_vi_output_var",
                     "nonlocal_iv6221_source_var"):
            setattr(a, name, Var())
        return a

    def test_the_summary_is_the_live_csv_with_its_two_other_csvs_beside_it(self):
        a = self.scan_page()
        settings = a._parse_nonlocal_vi_settings()
        settings["sample_interval_s"] = 0.01
        recorder = app.ScanRunRecorder(self.folder, "NL_GateVI", "B1 gate", {}, label="Gate-VI")
        with mock.patch.object(app, "LOCKIN_SETTLE_TAUS", 0.0):
            future = run_job(a, recorder, "gate", lambda: a._nonlocal_vi_scan_worker(settings, recorder))
        self.assertIsNone(future.exception())
        with recorder.csv_path.open(encoding="utf-8-sig") as file:
            summary = list(csv.reader(file))
        self.assertEqual(tuple(summary[0]), app.CombinedControlApp._nonlocal_vi_summary_header())
        files = sorted(path.name for path in recorder.directory.iterdir())
        self.assertEqual(files, ["B1 gate.csv", "B1 gate.log", "B1 gate_raw.csv", "B1 gate_resistance_gate.csv"])
        with (recorder.directory / "B1 gate_raw.csv").open(encoding="utf-8-sig") as file:
            self.assertEqual(tuple(next(csv.reader(file))), app.CombinedControlApp._nonlocal_vi_raw_header())
        with (recorder.directory / "B1 gate_resistance_gate.csv").open(encoding="utf-8-sig") as file:
            gate = list(csv.reader(file))
        self.assertEqual(tuple(gate[0]), app.CombinedControlApp.NONLOCAL_VI_RESISTANCE_HEADER)
        self.assertEqual([row[1] for row in gate[1:]], ["initial_gate"] + ["post_point"] * 3, "0 -> 4 -> 0 V")
        sequence = names(recorder)
        self.assertEqual(sequence[:4], ["run_prepared", "scan_started", "set_up", "gate_resistance"])
        self.assertEqual(sequence.count("point_saved"), 3)
        self.assertEqual(sequence[-1], "outputs_zeroed")
        a.nonlocal_vi_2410_controller.zero_output.assert_called_once_with("at the scan end")
        # The finish: the snapshots, then the log closed, then the status.
        a.nonlocal_vi_run_directory = recorder.directory
        a.nonlocal_vi_plot_orders, a.nonlocal_vi_plot_canvases = (1,), {}
        a.save_plot_canvas_snapshot = mock.Mock()
        a._set_mfli_lock_state = a._update_nonlocal_vi_controls = mock.Mock()
        a._update_k6221_controls = a._update_keithley_controls = mock.Mock()
        a.root = mock.Mock()
        a.nonlocal_vi_total_points = 3
        a._finish_nonlocal_vi_scan(None)
        self.assertEqual(names(recorder)[-2:], ["plot_snapshots", "run_finished"])
        self.assertTrue(a.nonlocal_vi_status_var.get().startswith(
            "Gate-VI: Completed; Keithley 2400 and 6221 zeroed; 3 points saved in B1 gate"))

    def test_the_yes_no_comes_before_the_folder_and_cancel_makes_nothing(self):
        a = self.scan_page()
        a.nonlocal_keithley_task = None
        a.nonlocal_vi_running = a.nonlocal_vi_busy = False
        a.nonlocal_vi_safety_var = Var(True)
        a.nonlocal_vi_output_directory = self.folder
        a.root = None
        order = []
        with mock.patch.object(app, "messagebox") as messagebox, \
                mock.patch.object(app, "filedialog") as filedialog, \
                mock.patch.object(app, "simpledialog") as simpledialog:
            messagebox.askyesno.side_effect = lambda *args, **kwargs: (order.append("yes/no"), True)[1]
            filedialog.askdirectory.side_effect = lambda **kwargs: (order.append("folder"), "")[1]
            a.start_nonlocal_vi_scan()
        self.assertEqual(order, ["yes/no", "folder"])
        simpledialog.askstring.assert_not_called()
        text = messagebox.askyesno.call_args.args[1]
        self.assertIn("At every end (completed, stopped or failed) the Keithley and the 6221 are zeroed", text)
        self.assertEqual(list(self.folder.iterdir()), [])
        self.assertFalse(a.nonlocal_vi_running)


# ----------------------------------------------------------- the shared end
class EndTests(NoWindows):
    def test_closing_mid_scan_closes_the_log_on_the_worker(self):
        log = []
        a = keithley_page(Meter(), log)
        recorder = app.ScanRunRecorder(self.folder, "NL_RvsT", "", {})
        a.closing = True

        def body():
            raise InterruptedError("Resistance measurement stopped")

        future = run_job(a, recorder, "resistance", body)
        self.assertIsInstance(future.exception(), InterruptedError)
        last = events(recorder)[-1]
        self.assertEqual((last["event"], last["outcome"]), ("run_finished", "stopped"))
        self.assertIn("the program closed", last["stop_reason"])
        self.assertTrue(recorder.closed)

    def test_a_closing_page_opens_no_dialog(self):
        a = keithley_page(Meter(), [])
        a.closing = True
        a._nonlocal_dialog_later(mock.Mock(), "t", "x")
        a.root.after.assert_not_called()

    def test_the_log_lines_of_a_running_scan_are_notes(self):
        log = []
        a = keithley_page(Meter(), log)
        recorder = app.ScanRunRecorder(self.folder, "NL_RvsT", "", {})
        a.nonlocal_recorder = recorder
        a._nonlocal_log("SR830 #8 LIAS? reply unreadable")
        self.assertIn("SR830 #8 LIAS? reply unreadable", log)
        self.assertEqual(events(recorder)[-1], {**events(recorder)[-1], "event": "note",
                                                "text": "SR830 #8 LIAS? reply unreadable"})

    def test_a_row_of_the_wrong_length_is_refused(self):
        with self.assertRaisesRegex(RuntimeError, "2 cells for 3 columns"):
            app.CombinedControlApp._nonlocal_csv_row(("a", "b", "c"), (1, 2))

    def test_a_task_that_cannot_start_closes_its_run_not_started(self):
        a = keithley_page(Meter(), [])
        recorder = app.ScanRunRecorder(self.folder, "NL_2400IV", "", {})
        a._nonlocal_run_started(recorder)
        a._nonlocal_run_not_started(recorder, RuntimeError("worker gone"))
        last = events(recorder)[-1]
        self.assertEqual((last["outcome"], last["error"], last["points_saved"]), ("not_started", "worker gone", 0))
        self.assertIsNone(a.nonlocal_recorder)


# -------------------------------------------- C2-1: the 6221's output low
class Stop(Exception):
    pass


class Fake6221:
    def __init__(self, low_to_earth=1, stuck=False):
        self.low_to_earth, self.stuck, self.log = low_to_earth, stuck, []

    def write(self, command):
        self.log.append(command)
        if command == app.K6221_OUTPUT_LOW_FLOATING and not self.stuck:
            self.low_to_earth = 0
        if command == "*RST":
            self.low_to_earth = 1
        if command.startswith("CURR:COMP"):
            raise Stop()

    def query(self, command):
        self.log.append(command)
        if command == app.K6221_OUTPUT_LOW_QUERY:
            return str(self.low_to_earth)
        if command == "SYST:ERR?":
            return '0,"No error"'
        raise AssertionError(command)


class OutputLowTests(NoWindows):
    def configure(self, fake):
        controller = app.NonLocalACController(lambda _m: None)
        controller.source = fake
        controller.nanovoltmeter = object()
        with self.assertRaises(Stop):
            controller.configure(1.0, 17.3, 10.0, {}, use_nanovoltmeter=True, output_mode="DC")
        return fake.log

    def test_every_reset_floats_the_output_low_read_back(self):
        log = self.configure(Fake6221())
        self.assertEqual(log[:7], ["*RST", "*CLS", "OUTP:LTE?", "OUTP:LTE OFF", "SYST:ERR?", "OUTP:LTE?",
                                   "CURR:COMP 10"], "floating before anything else is set")

    def test_floating_already_is_only_read(self):
        fake = Fake6221()
        fake.write = (lambda original: lambda command: (original(command), setattr(fake, "low_to_earth", 0))[0]
                      if command == "*CLS" else original(command))(fake.write)
        log = self.configure(fake)
        self.assertNotIn("OUTP:LTE OFF", log)
        self.assertEqual(log[:4], ["*RST", "*CLS", "OUTP:LTE?", "CURR:COMP 10"])

    def test_a_6221_that_stays_earthed_stops_the_set_up(self):
        controller = app.NonLocalACController(lambda _m: None)
        controller.source = fake = Fake6221(stuck=True)
        controller.nanovoltmeter = object()
        with self.assertRaisesRegex(RuntimeError, "TRIAX -> OUTPUT LOW -> FLOATING"):
            controller.configure(1.0, 17.3, 10.0, {}, use_nanovoltmeter=True, output_mode="DC")
        self.assertFalse(any(command.startswith("CURR:COMP") for command in fake.log))


# ------------------------------------------------ C2-3: LIAS? asked once
class SR830(object):
    def __init__(self, replies):
        self.replies, self.queries, self.cleared = dict(replies), [], 0

    def query(self, command):
        self.queries.append(command)
        reply = self.replies[command]
        return reply.pop(0) if isinstance(reply, list) else reply

    def write(self, command):
        pass

    def clear(self):
        self.cleared += 1


class StatusTests(NoWindows):
    def controller(self, lias):
        controller = app.NonLocalACController(lambda _m: None)
        controller.lockins = {"8": SR830({"SNAP? 1,2,3,4,9": "1e-6,0,1e-6,0,17.3", "LIAS?": lias,
                                          "FREQ?": ["x", "17.3"]})}
        controller.sensitivity_indices, controller.low_range_counts, controller.range_cooldowns = \
            {"8": 9}, {"8": 0}, {"8": 0}
        return controller

    def test_a_misaligned_lias_is_not_asked_again_and_counts_as_overloaded(self):
        for reply in ("1e-6,0", "4.2e-06", "garbage"):
            with self.subTest(reply=reply):
                controller = self.controller([reply, "0"])
                sample = controller._read_sample_one("8", 17.3, True, 0)
                lockin = controller.lockins["8"]
                self.assertEqual(lockin.queries.count("LIAS?"), 1, "never asked twice")
                self.assertEqual(lockin.cleared, 1, "the buffer cleared for the next query")
                self.assertEqual(sample["lia_status"], app.NonLocalACController.SR830_LIAS_UNREADABLE)
                self.assertFalse(sample["measurement_valid"])
                self.assertEqual(sample["status_text"], "LIAS? unreadable: counted as overloaded")
                self.assertEqual(sample["auto_range"], "", "no range step from an unknown status")

    def test_a_readable_status_and_other_queries_are_as_before(self):
        controller = self.controller("0")
        sample = controller._read_sample_one("8", 17.3, True, 0)
        self.assertEqual(sample["lia_status"], 0)
        self.assertTrue(sample["measurement_valid"])
        self.assertEqual(controller.lockins["8"].cleared, 0)
        self.assertEqual(controller._query_sr830_numbers("8", "FREQ?", 1), (17.3,), "FREQ? is still retried")

    def test_the_reference_wait_counts_an_unreadable_status_as_unlocked(self):
        controller = self.controller(["junk"] * 3)
        controller.lockins["8"].replies["FREQ?"] = "17.3"
        clock = iter([0.0, 0.0, 10.0])
        with mock.patch.object(app.time, "monotonic", side_effect=lambda: next(clock)):
            result = controller._wait_reference_one("8", 17.3, threading.Event())
        self.assertFalse(result["valid"])
        self.assertEqual(result["status"], app.NonLocalACController.SR830_LIAS_UNREADABLE)


# --------------------------------------- C2-5 and C2-6: the Keithley itself
class KeithleyTests(NoWindows):
    def test_a_status_callback_that_raises_never_leaves_the_pulse_on(self):
        for fail_at in (5.0, 0.0):
            with self.subTest(fail_at=fail_at):
                meter = Meter()
                logged = []
                controller = connected_controller(meter)
                controller.logger = logged.append

                def callback(value):
                    if value == fail_at:
                        raise RuntimeError("the window is gone")

                if fail_at:
                    with self.assertRaisesRegex(RuntimeError, "the window is gone"):
                        controller.apply_voltage_pulse(5.0, 0.0, threading.Event(), status_callback=callback)
                else:
                    controller.apply_voltage_pulse(5.0, 0.0, threading.Event(), status_callback=callback)
                    self.assertTrue(any("status update failed" in line for line in logged))
                self.assertEqual((meter.output, meter.voltage), (0, 0.0))
                self.assertEqual(meter.commands[-1], ":OUTP OFF")

    def test_a_failed_connect_closes_the_visa_session_even_when_a_write_fails(self):
        session = mock.Mock()
        session.query.return_value = "SOME OTHER DEVICE"
        session.write.side_effect = RuntimeError("bus error")
        manager = mock.Mock()
        manager.open_resource.return_value = session
        controller = app.NonLocalVI2410Controller(lambda _m: None, "2400")
        with mock.patch.object(app, "pyvisa", object()), \
                mock.patch.object(app, "acquire_shared_visa_manager", return_value=manager), \
                mock.patch.object(app, "release_shared_visa_manager"), \
                mock.patch.object(app, "acquire_instrument_lease"), \
                mock.patch.object(app, "release_instrument_lease"):
            with self.assertRaisesRegex(RuntimeError, "is not a Keithley 2400"):
                controller.connect("GPIB0::24::INSTR")
        session.close.assert_called_once()
        self.assertEqual([call.args[0] for call in session.write.call_args_list], [":SOUR:VOLT 0", ":OUTP OFF"])
        self.assertFalse(controller.connected)

    def test_zero_output_reads_the_output_and_the_level_back(self):
        meter = Meter()
        controller = connected_controller(meter)
        controller.zero_output("at the scan end")
        self.assertEqual(meter.commands[-2:], [":SOUR:VOLT 0", ":OUTP OFF"])
        stuck = connected_controller(Meter(level_stuck=True))
        with self.assertRaisesRegex(RuntimeError, "level reads 5 V at the scan end, not 0"):
            stuck.zero_output("at the scan end")


if __name__ == "__main__":
    unittest.main()
