"""Non-local page: every lock-in reading settles at least 7 x TC.

User, 2026-10-14: "every lock-in reading waits at least 7 x TC (99 %
settled at 12 dB/oct) after a change, on every page; Wait/pt or Settle s
still apply when longer; ... the Start confirmation shows the wait".  On the
Non-local page that is 6221-IV (after each current step) and Gate-VI (after
the 6221 read current switches on after each pulse); 2400-IV and |R| vs time
are DC and have no lock-in.  The rule uses the shared LOCKIN_SETTLE_TAUS.

No Tk window and no instrument: the page stand-ins of test_nonlocal_6221_iv
and test_nonlocal_gate_vi_harmonics, and waits that are recorded, not slept.
Creating a Tk root fails the test, and every dialog is mocked."""
import csv
import math
import tempfile
import threading
import time
import unittest
from concurrent.futures import Future
from pathlib import Path
from unittest import mock

from test_nonlocal_6221_iv import FakeSource, page
from test_nonlocal_gate_vi_harmonics import FakeAC, gate_page
from test_nonlocal_keithley import Var
from test_ppms_motor_debug import app


class NoWindows(unittest.TestCase):
    def setUp(self):
        patcher = mock.patch("tkinter.Tk", side_effect=AssertionError("a test tried to open a Tk window"))
        patcher.start()
        self.addCleanup(patcher.stop)


class RecordingEvent(threading.Event):
    """A stop event that records each wait and sleeps only the short ones
    (the sample timing), so a 7 s settle costs nothing."""

    def __init__(self):
        super().__init__()
        self.waits = []

    def wait(self, timeout=None):
        self.waits.append(timeout)
        if timeout and timeout < 0.05:
            time.sleep(timeout)
        return self.is_set()


class Executor:
    """The page's executor, running each job at once."""

    def __init__(self):
        self.jobs = []

    def submit(self, function, *args):
        self.jobs.append((function, args))
        future = Future()
        try:
            future.set_result(function(*args))
        except BaseException as error:
            future.set_exception(error)
        return future


class HeldExecutor(Executor):
    """An executor whose job has not finished yet."""

    def submit(self, function, *args):
        self.jobs.append((function, args))
        return Future()


# ------------------------------------------------------------- the rule
class RuleTests(NoWindows):
    settle = staticmethod(app.CombinedControlApp._nonlocal_lockin_settle_s)

    def test_the_shared_constant_is_7_and_settles_99_percent(self):
        self.assertEqual(app.LOCKIN_SETTLE_TAUS, 7.0)
        self.assertEqual(app.NONLOCAL_LOCKIN_FILTER_ORDER, 2)      # 12 dB/oct
        x = app.LOCKIN_SETTLE_TAUS
        self.assertGreater(1.0 - math.exp(-x) * (1.0 + x), 0.99)

    def test_the_slowest_time_constant_or_the_page_wait_if_longer(self):
        self.assertEqual(self.settle(0.5, [1.0]), 7.0)
        self.assertEqual(self.settle(0.5, [1.0, 3.0]), 21.0, "the slowest lock-in")
        self.assertEqual(self.settle(30.0, [1.0]), 30.0, "Settle s when longer")
        self.assertEqual(self.settle(5.0, []), 5.0, "no lock-in: the page's wait")
        self.assertEqual(self.settle(0.5, [math.nan, None, 0.0, "x", 3.0]), 21.0, "unknown ones skipped")

    def test_it_reads_the_shared_constant_when_called(self):
        with mock.patch.object(app, "LOCKIN_SETTLE_TAUS", 9.0):
            self.assertEqual(self.settle(0.5, [1.0]), 9.0)

    def test_the_constant_is_defined_once(self):
        with open(app.__file__, encoding="utf-8") as source:
            text = source.read()
        self.assertEqual(text.count("\nLOCKIN_SETTLE_TAUS = "), 1)


# ------------------------------------------------------------- 6221-IV
def iv_page(source=None, **values):
    """6221-IV at its start-up values (the Zurich alone, 1+2+3, rack TC
    1 s), unless told otherwise."""
    values.setdefault("harmonics", app.NONLOCAL_IV6221_DEFAULT_HARMONICS)
    values.setdefault("measure", app.NONLOCAL_IV6221_DEFAULT_MEASURE)
    return page(source or FakeSource(keys=(), mfli=True), **values)


class IV6221TimingTests(NoWindows):
    def timing(self, a, sr830=None):
        settings = a._parse_nonlocal_iv6221_settings()
        settings.update(a._nonlocal_iv6221_timing(settings, sr830 or {}))
        return settings

    def test_at_the_defaults_each_point_waits_7_s_and_the_first_10_s(self):
        a = iv_page()
        settings = self.timing(a)
        self.assertEqual((settings["settle_s"], settings["acquire_s"]), (0.5, 1.0), "the boxes as before")
        self.assertEqual(settings["lockin_time_constant_s"], 1.0)
        self.assertEqual(settings["slowest_lockin"], "Zurich")
        self.assertEqual(settings["point_settle_s"], 7.0)
        self.assertEqual(settings["first_settle_s"], 10.0)
        # 100 points: 99 x (7 + 1 + 0.3) + (10 + 1 + 0.3) + a second window
        # of 1 s at the first point = 834 s (it counted 191 s).
        self.assertAlmostEqual(a._nonlocal_iv6221_estimate_s(settings), 834.0)
        self.assertEqual(a._format_scan_duration(834.0), "13 min 54 s")

    def test_a_slower_sr830_front_panel_sets_the_wait(self):
        a = iv_page(FakeSource(keys=("8",), mfli=True), measure={"8": True, "mfli": True})
        settings = self.timing(a, {"8": 3.0})
        self.assertEqual((settings["slowest_lockin"], settings["lockin_time_constant_s"]), ("SR830 #8", 3.0))
        self.assertEqual((settings["point_settle_s"], settings["first_settle_s"]), (21.0, 30.0))
        self.assertEqual(settings["sr830_time_constants_s"], {"8": 3.0})

    def test_a_longer_settle_s_still_applies(self):
        a = iv_page(settle="30")
        settings = self.timing(a)
        self.assertEqual((settings["point_settle_s"], settings["first_settle_s"]), (30.0, 30.0))

    def test_the_estimate_counts_each_loops_jump_back_to_start(self):
        one_way = self.timing(iv_page(start="0.1", end="0.3", step="0.1", loops="2"))
        a = iv_page(start="0.1", end="0.3", step="0.1", loops="2", round_trip=True)
        round_trip = self.timing(a)
        # One way (0.1-0.3 twice, 6 points): 2 far points (10 s) and 4 at
        # 7 s; round trip (0.1-0.3-0.1 twice, 10 points): 1 far point and 9.
        self.assertEqual((len(one_way["plan"]), len(round_trip["plan"])), (6, 10))
        self.assertAlmostEqual(a._nonlocal_iv6221_estimate_s(one_way), 2 * 11.3 + 4 * 8.3 + 1.0)
        self.assertAlmostEqual(a._nonlocal_iv6221_estimate_s(round_trip), 1 * 11.3 + 9 * 8.3 + 1.0)


class IV6221WorkerTests(NoWindows):
    def run_scan(self, source, settle_s=0.5, **planned):
        a = page(source, start="0.1", end="0.3", step="0.1")
        a.nonlocal_vi_2410_controller = mock.Mock(connected=False)
        a.nonlocal_vi_interlock = app.NonLocalVIInterlock()
        a.nonlocal_keithley_stop_event = RecordingEvent()
        a.nonlocal_keithley_output_off = False
        a.post_ui = lambda callback: None
        a._publish_reference_sync = mock.Mock()
        a._show_nonlocal_mfli_lock = mock.Mock()
        a.nonlocal_iv6221_source_var, a.nonlocal_iv6221_point_var = Var(), Var()
        settings = a._parse_nonlocal_iv6221_settings()
        settings.update({"settle_s": settle_s, "acquire_s": 0.0, **planned})
        path = Path(tempfile.mkdtemp()) / "run.csv"
        with mock.patch.object(app, "NONLOCAL_IV6221_REFERENCE_WAIT_S", 0.0):
            a._nonlocal_iv6221_worker(settings, path)
        with path.open(encoding="utf-8-sig") as file:
            rows = list(csv.reader(file))
        waits = [w for w in a.nonlocal_keithley_stop_event.waits if w and w >= 0.05]
        return waits, rows

    def test_ordinary_points_wait_7_time_constants_read_back(self):
        # SR830 #8's front-panel time constant, read back at the set-up: 2 s.
        waits, rows = self.run_scan(FakeSource(sr830_tau=2.0))
        self.assertEqual(waits, [20.0, 14.0, 14.0], "first point 10 x 2 s, then 7 x 2 s (not 0.5 s)")
        self.assertEqual(rows[0][-1], "settle_waited_s", "appended at the end")
        self.assertEqual(rows[0][-2], "compliance_readings")
        self.assertEqual([row[-1] for row in rows[1:]], ["20", "14", "14"])

    def test_never_shorter_than_the_start_confirmation_showed(self):
        # Confirmed at 3 s on the front panel; read back 2 s at the set-up.
        waits, rows = self.run_scan(FakeSource(sr830_tau=2.0), point_settle_s=21.0, first_settle_s=30.0)
        self.assertEqual(waits, [30.0, 21.0, 21.0])
        self.assertEqual([row[-1] for row in rows[1:]], ["30", "21", "21"])

    def test_a_longer_settle_s_is_kept(self):
        waits, _rows = self.run_scan(FakeSource(sr830_tau=0.1), settle_s=2.5)
        self.assertEqual(waits, [2.5, 2.5, 2.5], "Settle s above 7 x 0.1 s and 10 x 0.1 s")


class IV6221StartTests(NoWindows):
    def page(self, source, executor=None, **values):
        a = iv_page(source, **values)
        a.root = None
        a.closing = a.nonlocal_vi_closed = False
        a.nonlocal_keithley_task = None
        a.nonlocal_vi_running = a.nonlocal_vi_busy = False
        a.nonlocal_iv6221_safety_var = Var(True)
        a.nonlocal_iv6221_status_var = Var()
        a.nonlocal_iv6221_point_var, a.nonlocal_vi_status_var, a.nonlocal_vi_output_var = Var(), Var(), Var()
        a.nonlocal_vi_source_resource_var = Var("GPIB0::12::INSTR")
        a.nonlocal_iv6221_progress = mock.Mock()
        a.nonlocal_iv6221_directory = Path(tempfile.mkdtemp())
        a.nonlocal_vi_executor = executor or Executor()
        a.post_ui = lambda callback: callback()
        a._redraw_nonlocal_iv6221_plots = mock.Mock()
        a._begin_nonlocal_keithley_task = mock.Mock()
        return a

    def start(self, a, accept=False, folder=None):
        with mock.patch.object(app, "messagebox") as messagebox, \
                mock.patch.object(app, "filedialog") as filedialog:
            messagebox.askyesno.return_value = accept
            filedialog.askdirectory.return_value = folder or ""
            a.start_nonlocal_iv6221()
        return messagebox

    def test_the_zurich_alone_shows_7_s_without_reading_anything(self):
        a = self.page(FakeSource(keys=(), mfli=True))
        messagebox = self.start(a)
        messagebox.showwarning.assert_not_called()
        text = messagebox.askyesno.call_args.args[1]
        self.assertIn("Each point waits 7 s after its current step (7 × the slowest lock-in TC, "
                      "Zurich TC 1 s, or Settle 0.5 s if longer), then averages Acquire 1 s", text)
        self.assertIn("The first point of each pass waits 10 s (10 × TC)", text)
        self.assertIn("Estimated time: about 13 min 54 s", text)
        self.assertEqual(a.nonlocal_vi_executor.jobs, [], "no SR830 to read")
        a._begin_nonlocal_keithley_task.assert_not_called()

    def test_an_sr830_front_panel_tc_is_read_before_the_confirmation(self):
        source = FakeSource(keys=("8",), mfli=True)
        source.read_sr830_time_constants = mock.Mock(return_value={"8": 3.0})
        a = self.page(source, measure={"8": True, "mfli": True}, loops="2")
        messagebox = self.start(a)
        source.read_sr830_time_constants.assert_called_once_with(("8",))
        self.assertFalse(a.nonlocal_iv6221_tc_reading)
        text = messagebox.askyesno.call_args.args[1]
        self.assertIn("Each point waits 21 s after its current step (7 × the slowest lock-in TC, "
                      "SR830 #8 front panel 3 s, or Settle 0.5 s if longer)", text)
        self.assertIn("The first point of each pass and each loop's jump back to Start waits 30 s", text)
        self.assertEqual(a.nonlocal_iv6221_status_var.get(), "SR830 front-panel TC: SR830 #8 3 s")

    def test_a_failed_read_starts_nothing(self):
        source = FakeSource(keys=("8",))
        source.read_sr830_time_constants = mock.Mock(side_effect=RuntimeError("VI_ERROR_TMO"))
        a = self.page(source, measure={"8": True, "mfli": False})
        messagebox = self.start(a, accept=True)
        messagebox.askyesno.assert_not_called()
        self.assertIn("VI_ERROR_TMO", messagebox.showwarning.call_args.args[1])
        self.assertFalse(a.nonlocal_iv6221_tc_reading)
        a._begin_nonlocal_keithley_task.assert_not_called()

    def test_a_second_start_while_reading_is_ignored(self):
        source = FakeSource(keys=("8",))
        source.read_sr830_time_constants = mock.Mock(return_value={"8": 1.0})
        a = self.page(source, executor=HeldExecutor(), measure={"8": True, "mfli": False})
        self.start(a)
        messagebox = self.start(a)
        self.assertEqual(len(a.nonlocal_vi_executor.jobs), 1)
        messagebox.askyesno.assert_not_called()
        self.assertTrue(a.nonlocal_iv6221_tc_reading)

    def test_a_scan_begun_meanwhile_is_not_confirmed_over(self):
        source = FakeSource(keys=("8",))
        a = self.page(source, measure={"8": True, "mfli": False})
        settings = a._parse_nonlocal_iv6221_settings()
        a.nonlocal_keithley_task = "iv6221set"
        future = Future()
        future.set_result({"8": 1.0})
        with mock.patch.object(app, "messagebox") as messagebox:
            a._nonlocal_iv6221_time_constants_read(settings, future)
        messagebox.askyesno.assert_not_called()

    def test_the_parameter_log_and_the_run_get_the_effective_settle(self):
        source = FakeSource(keys=("8",), mfli=True)
        source.read_sr830_time_constants = mock.Mock(return_value={"8": 3.0})
        a = self.page(source, measure={"8": True, "mfli": True})
        folder = Path(tempfile.mkdtemp())
        self.start(a, accept=True, folder=str(folder))
        a._begin_nonlocal_keithley_task.assert_called_once()
        self.assertEqual((a.nonlocal_iv6221_settings["point_settle_s"],
                          a.nonlocal_iv6221_settings["first_settle_s"]), (21.0, 30.0))
        log = next(folder.glob("6221-IV_*/*_parameters.log")).read_text(encoding="utf-8")
        for line in ("point_settle_s=21", "first_settle_s=30", "lockin_time_constant_s=3",
                     "slowest_lockin=SR830 #8"):
            self.assertIn(line, log)


class SR830ReadTests(NoWindows):
    class FakeSR830:
        def __init__(self, oflt):
            self.oflt, self.queries, self.writes = oflt, [], []

        def write(self, command):
            self.writes.append(command)

        def query(self, command):
            self.queries.append(command)
            return str(self.oflt)

    def controller(self, oflt):
        controller = app.NonLocalACController(lambda _message: None)
        controller.lockins = {"8": self.FakeSR830(oflt)}
        return controller

    def test_oflt_is_read_and_nothing_written(self):
        controller = self.controller(11)
        self.assertEqual(controller.read_sr830_time_constants(("8",)), {"8": 3.0})
        self.assertEqual(controller.lockins["8"].queries, ["OFLT?"])
        self.assertEqual(controller.lockins["8"].writes, [])

    def test_a_bad_code_or_a_missing_sr830_raises(self):
        with self.assertRaisesRegex(RuntimeError, "time-constant readback is invalid: OFLT 25"):
            self.controller(25).read_sr830_time_constants(("8",))
        with self.assertRaisesRegex(RuntimeError, "SR830 #9 is not connected"):
            self.controller(10).read_sr830_time_constants(("9",))

    def test_gate_vi_still_writes_1_s(self):
        self.assertEqual(app.SR830_TIME_CONSTANTS_S[app.NONLOCAL_VI_SR830_TIME_CONSTANT_CODE], 1.0)
        with open(app.__file__, encoding="utf-8") as source:
            text = source.read()
        self.assertIn('else (f"OFLT {NONLOCAL_VI_SR830_TIME_CONSTANT_CODE}", "OFSL 1")', text)


# ------------------------------------------------------------- Gate-VI
class SlowZurichAC(FakeAC):
    """Reads back a Zurich time constant longer than the box asked for."""

    def configure(self, *args, **options):
        frequency, readbacks = super().configure(*args, **options)
        readbacks["mfli"]["timeconstant_s"] = 3.0
        return frequency, readbacks


class GateVIParseTests(NoWindows):
    def test_the_sr830s_1_s_or_the_zurich_tc_whichever_is_slower(self):
        a = gate_page("1", "2")                     # SR830 #8 and the Zurich (TC 0.001 s)
        settings = a._parse_nonlocal_vi_settings()
        self.assertEqual((settings["lockin_time_constant_s"], settings["lockin_settle_s"]), (1.0, 7.0))
        self.assertEqual(a._nonlocal_vi_pre_run_s(settings, 17.3), 7.0, "6221 pre-run 0 s here")
        a.nonlocal_mfli_tc_var.set("3")
        settings = a._parse_nonlocal_vi_settings()
        self.assertEqual((settings["lockin_time_constant_s"], settings["lockin_settle_s"]), (3.0, 21.0))
        a.nonlocal_vi_settle_var.set("30")
        self.assertEqual(a._nonlocal_vi_pre_run_s(a._parse_nonlocal_vi_settings(), 17.3), 30.0,
                         "a longer 6221 pre-run s still applies")

    def test_the_2182a_alone_keeps_the_pre_run(self):
        a = gate_page("1", "2")
        a.nonlocal_vi_measurement_enabled_vars["8"].set(False)
        a.nonlocal_vi_use_mfli_var.set(False)
        a.nonlocal_vi_use_2182_var.set(True)
        a.nonlocal_vi_ac_controller.nanovoltmeter_connected = True
        a.nonlocal_vi_settle_var.set("5")
        settings = a._parse_nonlocal_vi_settings()
        self.assertEqual((settings["lockin_time_constant_s"], settings["lockin_settle_s"]), (0.0, 0.0))
        self.assertEqual(a._nonlocal_vi_pre_run_s(settings, 17.3), 5.0)
        self.assertIn("6221 pre-run 5 s; no lock-in measured", a._nonlocal_vi_settle_text(settings))

    def test_the_estimate_counts_the_settle(self):
        a = gate_page("1", "2")                     # 6221 pre-run 0 s, 3 points
        settings = a._parse_nonlocal_vi_settings()
        with mock.patch.object(app, "LOCKIN_SETTLE_TAUS", 0.0):
            without = a._parse_nonlocal_vi_settings()
        self.assertAlmostEqual(settings["estimated_s"] - without["estimated_s"],
                               len(settings["voltage_scan"]) * 7.0)

    def test_a_frequency_scan_waits_the_longest_of_the_three(self):
        settings = {"settle_s": 0.0, "lockin_settle_s": 0.7, "frequency_scan": True}
        pre_run = app.CombinedControlApp._nonlocal_vi_pre_run_s
        self.assertAlmostEqual(pre_run(settings, 7.0), 5 / 7, msg="5 periods at 7 Hz")
        self.assertAlmostEqual(pre_run(settings, 11.0), 0.7, msg="7 x 0.1 s")

    def test_the_confirmation_shows_the_wait(self):
        a = gate_page("1", "2")
        a.nonlocal_vi_settle_var.set("5")
        a.nonlocal_keithley_task = None
        a.root = None
        a.nonlocal_vi_safety_var = Var(True)
        a.nonlocal_vi_output_directory = Path(tempfile.mkdtemp())
        a.nonlocal_vi_status_var = Var()
        with mock.patch.object(app, "filedialog") as filedialog, \
                mock.patch.object(app, "messagebox") as messagebox:
            filedialog.askdirectory.return_value = str(a.nonlocal_vi_output_directory)
            messagebox.askyesno.return_value = False
            a.start_nonlocal_vi_scan()
        text = messagebox.askyesno.call_args.args[1]
        self.assertIn("Each point: the 6221 runs 7 s before the reading (7 × the slowest lock-in TC 1 s "
                      "= 7 s, or 6221 pre-run 5 s if longer), then reads Measure 0.05 s\n", text)
        self.assertLess(text.index("the 6221 runs 7 s"), text.index("Estimated total time"))


class GateVIWorkerTests(NoWindows):
    def run_scan(self, ac=None, tc="2"):
        a = gate_page("1+2", "2")                   # SR830 #8 1+2, the Zurich 2
        if ac is not None:
            ac.mfli_device = "dev4414"
            a.nonlocal_vi_ac_controller = ac
        a.nonlocal_mfli_tc_var.set(tc)
        settings = a._parse_nonlocal_vi_settings()
        settings["sample_interval_s"] = 0.01
        a.nonlocal_vi_stop_event = RecordingEvent()
        a.nonlocal_vi_interlock = app.NonLocalVIInterlock()
        a.nonlocal_vi_data_lock = threading.Lock()
        a.nonlocal_vi_time_rows, a.nonlocal_vi_summary_rows = [], []
        a.nonlocal_vi_pulse_rows, a.nonlocal_vi_current_rows, a.nonlocal_vi_resistance_rows = [], [], []
        a.post_ui = lambda callback: callback()
        a._redraw_nonlocal_vi_plots = mock.Mock()
        a._publish_reference_sync = mock.Mock()
        a._publish_nonlocal_vi_samples = mock.Mock()
        a.nonlocal_vi_progress = mock.Mock()
        a.nonlocal_vi_eta_var, a.nonlocal_vi_status_var, a.nonlocal_vi_output_var = Var(), Var(), Var()
        directory = Path(tempfile.mkdtemp())
        a._nonlocal_vi_scan_worker(settings, directory)
        tables = {}
        for name in ("VI_raw.csv", "VI_summary.csv"):
            with (directory / name).open(encoding="utf-8-sig") as file:
                tables[name] = list(csv.DictReader(file))
        log = (directory / "VI_parameters.log").read_text(encoding="utf-8")
        return a, settings, tables, log

    def test_each_reading_waits_7_tc_after_the_6221_switches_on(self):
        a, settings, tables, log = self.run_scan()
        self.assertEqual(settings["lockin_settle_s"], 14.0, "7 x the Zurich's 2 s")
        waits = a.nonlocal_vi_stop_event.waits
        # Three points (0 -> 4 -> 0 V): the pre-run after each 6221 switch-on.
        self.assertEqual(waits.count(14.0), 3)
        self.assertIn("effective_settle_s=14", log)
        self.assertIn("lockin_settle_s=14.0", log)
        raw = tables["VI_raw.csv"]
        self.assertEqual(list(raw[0])[-1], "settle_waited_s", "appended at the end")
        # 1st pass (SR830 1ω, the Zurich): the pre-run; the SR830's 2ω pass:
        # its own 10 τ settle (τ read back 0.001 s here).
        first = {row["settle_waited_s"] for row in raw if row["meter_key"] == "mfli" or row["harmonic"] == "1"}
        second = {row["settle_waited_s"] for row in raw if row["meter_key"] == "8" and row["harmonic"] == "2"}
        self.assertEqual((first, second), ({"14.0"}, {"0.01"}))
        summary = {(row["meter_key"], row["harmonic"]): row["settle_waited_s"] for row in tables["VI_summary.csv"]}
        self.assertEqual(summary, {("8", "1"): "14.0", ("8", "2"): "0.01", ("mfli", "2"): "14.0"})

    def test_a_longer_time_constant_read_back_waits_longer(self):
        a, settings, _tables, log = self.run_scan(SlowZurichAC(), tc="2")
        self.assertEqual(settings["lockin_settle_s"], 14.0, "the confirmation's 7 x 2 s")
        self.assertEqual(a.nonlocal_vi_stop_event.waits.count(21.0), 3, "7 x the 3 s read back")
        self.assertIn("effective_settle_s=21", log)


if __name__ == "__main__":
    unittest.main()
