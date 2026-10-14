"""Non-local page: every lock-in reading settles at least 1 x TC.

User, 2026-10-14: "every lock-in reading waits at least 7 x TC (99 %
settled at 12 dB/oct) after a change, on every page; Wait/pt or Settle s
still apply when longer; ... the Start confirmation shows the wait".  Later
that day the user chose 1 x TC instead: "change all to 1*TC please".  On
the Non-local page that is 6221-IV (after each current step) and Gate-VI
(after the 6221 read current switches on after each pulse); 2400-IV and |R|
vs time are DC and have no lock-in.  The rule uses the shared
LOCKIN_SETTLE_TAUS (1.0).  Where a test checks that the time constant sets
the wait, 1 x TC is longer than Settle s / the 6221 pre-run.  The 10 x TC
waits (first point, harmonic switch, Zurich range step) and RT-E's 5 x TC
after an SR830 range step are unchanged.

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
    """An executor whose job has not finished yet; ``finish`` runs it."""

    def __init__(self):
        super().__init__()
        self.futures = []

    def submit(self, function, *args):
        self.jobs.append((function, args))
        self.futures.append(Future())
        return self.futures[-1]

    def finish(self, index=0):
        function, args = self.jobs[index]
        self.futures[index].set_result(function(*args))


# ------------------------------------------------------------- the rule
class RuleTests(NoWindows):
    settle = staticmethod(app.CombinedControlApp._nonlocal_lockin_settle_s)

    def test_the_shared_constant_is_1_tc_about_a_quarter_of_an_isolated_step(self):
        """The user's 1 x TC (2026-10-14): an isolated step is about 26 %
        settled at 12 dB/oct, but a sweep of small steps lags by only a
        fraction of a step."""
        self.assertEqual(app.LOCKIN_SETTLE_TAUS, 1.0)
        self.assertEqual(app.NONLOCAL_LOCKIN_FILTER_ORDER, 2)      # 12 dB/oct
        x = app.LOCKIN_SETTLE_TAUS
        self.assertAlmostEqual(1.0 - math.exp(-x) * (1.0 + x), 1.0 - 2.0 / math.e, places=12)

    def test_the_slowest_time_constant_or_the_page_wait_if_longer(self):
        self.assertEqual(self.settle(0.5, [1.0]), 1.0)
        self.assertEqual(self.settle(0.5, [1.0, 3.0]), 3.0, "the slowest lock-in")
        self.assertEqual(self.settle(30.0, [1.0]), 30.0, "Settle s when longer")
        self.assertEqual(self.settle(5.0, []), 5.0, "no lock-in: the page's wait")
        self.assertEqual(self.settle(0.5, [math.nan, None, 0.0, "x", 3.0]), 3.0, "unknown ones skipped")

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

    def test_at_the_defaults_each_point_waits_1_s_and_the_first_10_s(self):
        a = iv_page()
        settings = self.timing(a)
        self.assertEqual((settings["settle_s"], settings["acquire_s"]), (0.5, 1.0), "the boxes as before")
        self.assertEqual(settings["lockin_time_constant_s"], 1.0)
        self.assertEqual(settings["slowest_lockin"], "Zurich")
        self.assertEqual(settings["point_settle_s"], 1.0, "1 x the 1 s TC, longer than Settle s 0.5 s")
        self.assertEqual(settings["first_settle_s"], 10.0, "the far point keeps its 10 x TC")
        # 100 points: 99 x (1 + 1 + 0.3) + (10 + 1 + 0.3) + a second window
        # of 1 s at the first point = 240 s (834 s at 7 x TC; 191 s before).
        self.assertAlmostEqual(a._nonlocal_iv6221_estimate_s(settings), 240.0)
        self.assertEqual(a._format_scan_duration(240.0), "4 min 00 s")

    def test_a_slower_sr830_front_panel_sets_the_wait(self):
        a = iv_page(FakeSource(keys=("8",), mfli=True), measure={"8": True, "mfli": True})
        settings = self.timing(a, {"8": 3.0})
        self.assertEqual((settings["slowest_lockin"], settings["lockin_time_constant_s"]), ("SR830 #8", 3.0))
        self.assertEqual((settings["point_settle_s"], settings["first_settle_s"]), (3.0, 30.0))
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
        # 1 s; round trip (0.1-0.3-0.1 twice, 10 points): 1 far point and 9.
        self.assertEqual((len(one_way["plan"]), len(round_trip["plan"])), (6, 10))
        self.assertAlmostEqual(a._nonlocal_iv6221_estimate_s(one_way), 2 * 11.3 + 4 * 2.3 + 1.0)
        self.assertAlmostEqual(a._nonlocal_iv6221_estimate_s(round_trip), 1 * 11.3 + 9 * 2.3 + 1.0)


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

    def test_ordinary_points_wait_1_time_constant_read_back(self):
        # SR830 #8's front-panel time constant, read back at the set-up: 2 s.
        waits, rows = self.run_scan(FakeSource(sr830_tau=2.0))
        self.assertEqual(waits, [20.0, 2.0, 2.0], "first point 10 x 2 s, then 1 x 2 s (not 0.5 s)")
        self.assertEqual(rows[0][-1], "settle_waited_s", "appended at the end")
        self.assertEqual(rows[0][-2], "compliance_readings")
        self.assertEqual([row[-1] for row in rows[1:]], ["20", "2", "2"])

    def test_never_shorter_than_the_start_confirmation_showed(self):
        # Confirmed at 3 s on the front panel; read back 2 s at the set-up.
        waits, rows = self.run_scan(FakeSource(sr830_tau=2.0), point_settle_s=3.0, first_settle_s=30.0)
        self.assertEqual(waits, [30.0, 3.0, 3.0])
        self.assertEqual([row[-1] for row in rows[1:]], ["30", "3", "3"])

    def test_a_longer_settle_s_is_kept(self):
        waits, _rows = self.run_scan(FakeSource(sr830_tau=0.1), settle_s=2.5)
        self.assertEqual(waits, [2.5, 2.5, 2.5], "Settle s above 1 x 0.1 s and 10 x 0.1 s")


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
        # The dialogs open from the Tk event loop (root.after), not inside
        # the UI queue's drain; here the "loop" runs them at once.
        a.root = mock.Mock()
        a.root.after.side_effect = lambda _ms, callback: callback()
        a._update_nonlocal_vi_controls = mock.Mock()
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

    def test_the_zurich_alone_shows_1_s_without_reading_anything(self):
        a = self.page(FakeSource(keys=(), mfli=True))
        messagebox = self.start(a)
        messagebox.showwarning.assert_not_called()
        text = messagebox.askyesno.call_args.args[1]
        self.assertIn("Each point waits 1 s after its current step (1 × the slowest lock-in TC, "
                      "Zurich TC 1 s, or Settle 0.5 s if longer), then averages Acquire 1 s", text)
        self.assertIn("The first point of each pass waits 10 s (10 × the slowest lock-in TC, "
                      "or Settle 0.5 s if longer)", text)
        self.assertIn("Estimated time: about 4 min 00 s", text)
        self.assertNotIn("SR830 front panel", text, "no SR830 measured")
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
        self.assertIn("Each point waits 3 s after its current step (1 × the slowest lock-in TC, "
                      "SR830 #8 front panel 3 s, or Settle 0.5 s if longer)", text)
        self.assertIn("The first point of each pass and each loop's jump back to Start waits 30 s", text)
        self.assertIn("SR830 front panel (read back): SR830 #8 TC 3 s, 12 dB/oct\n", text)
        self.assertNotIn("Note:", text, "12 dB/oct, the slope the rule is worked out for")
        self.assertEqual(a.nonlocal_iv6221_status_var.get(), "SR830 front panel: SR830 #8 3 s, 12 dB/oct")

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
        future.set_result(({"8": 1.0}, {"8": 12.0}))
        with mock.patch.object(app, "messagebox") as messagebox:
            a._nonlocal_iv6221_time_constants_read(settings, future)
        messagebox.askyesno.assert_not_called()
        self.assertEqual(a.nonlocal_iv6221_status_var.get(),
                         "6221-IV not started (SR830 front panel: SR830 #8 1 s, 12 dB/oct)")

    def test_the_parameter_log_and_the_run_get_the_effective_settle(self):
        source = FakeSource(keys=("8",), mfli=True)
        source.read_sr830_time_constants = mock.Mock(return_value={"8": 3.0})
        a = self.page(source, measure={"8": True, "mfli": True})
        folder = Path(tempfile.mkdtemp())
        self.start(a, accept=True, folder=str(folder))
        a._begin_nonlocal_keithley_task.assert_called_once()
        self.assertEqual((a.nonlocal_iv6221_settings["point_settle_s"],
                          a.nonlocal_iv6221_settings["first_settle_s"]), (3.0, 30.0))
        log = next(folder.glob("6221-IV_*/*_parameters.log")).read_text(encoding="utf-8")
        for line in ("point_settle_s=3", "first_settle_s=30", "lockin_time_constant_s=3",
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
        self.assertEqual((settings["lockin_time_constant_s"], settings["lockin_settle_s"]), (1.0, 1.0))
        self.assertEqual(a._nonlocal_vi_pre_run_s(settings, 17.3), 1.0, "6221 pre-run 0 s here")
        a.nonlocal_mfli_tc_var.set("3")
        settings = a._parse_nonlocal_vi_settings()
        self.assertEqual((settings["lockin_time_constant_s"], settings["lockin_settle_s"]), (3.0, 3.0))
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
        self.assertIn("the 6221 runs 5 s before the reading (6221 pre-run 5 s; no lock-in measured)",
                      a._nonlocal_vi_settle_text(settings))

    def test_the_estimate_counts_the_settle(self):
        a = gate_page("1", "2")                     # 6221 pre-run 0 s, 3 points
        settings = a._parse_nonlocal_vi_settings()
        with mock.patch.object(app, "LOCKIN_SETTLE_TAUS", 0.0):
            without = a._parse_nonlocal_vi_settings()
        self.assertAlmostEqual(settings["estimated_s"] - without["estimated_s"],
                               len(settings["voltage_scan"]) * 1.0)       # 1 x the SR830s' 1 s

    def test_a_frequency_scan_waits_the_longest_of_the_three(self):
        settings = {"settle_s": 0.0, "lockin_settle_s": 0.3, "frequency_scan": True}
        pre_run = app.CombinedControlApp._nonlocal_vi_pre_run_s
        self.assertAlmostEqual(pre_run(settings, 7.0), 5 / 7, msg="5 periods at 7 Hz")
        self.assertAlmostEqual(pre_run(settings, 17.0), 0.3, msg="1 x 0.3 s")

    def test_the_confirmation_shows_the_wait(self):
        # Zurich TC 3 s against a 2 s pre-run, so 1 x TC sets the wait.
        a = gate_page("1", "2")
        a.nonlocal_mfli_tc_var.set("3")
        a.nonlocal_vi_settle_var.set("2")
        a.nonlocal_keithley_task = None
        a.nonlocal_vi_running = a.nonlocal_vi_busy = False
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
        # The 1 x TC counts from the reference lock (review, 2026-10-14).
        self.assertIn("Each point: the 6221 runs at least 3 s before the reading (1 × the slowest lock-in "
                      "TC 3 s = 3 s from the reference lock, or 6221 pre-run 2 s from the switch-on if "
                      "longer), then reads Measure 0.05 s\n", text)
        self.assertLess(text.index("the 6221 runs at least 3 s"), text.index("Estimated total time"))


class GateVIWorkerTests(NoWindows):
    def run_scan(self, ac=None, tc="2", settle=None, event=None, measure=None):
        a = gate_page("1+2", "2")                   # SR830 #8 1+2, the Zurich 2
        if measure is not None:
            # A window long enough for several readings on any OS: Windows'
            # 15.6 ms timer let the 0.05 s default hold one (CI, 2026-10-14).
            a.nonlocal_vi_measure_var.set(measure)
        if ac is not None:
            ac.mfli_device = "dev4414"
            a.nonlocal_vi_ac_controller = ac
        a.nonlocal_mfli_tc_var.set(tc)
        if settle is not None:
            a.nonlocal_vi_settle_var.set(settle)    # 6221 pre-run s
        settings = a._parse_nonlocal_vi_settings()
        settings["sample_interval_s"] = 0.01
        a.nonlocal_vi_stop_event = event or RecordingEvent()
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

    def test_each_reading_waits_1_tc_after_the_6221_switches_on(self):
        a, settings, tables, log = self.run_scan()
        self.assertEqual(settings["lockin_settle_s"], 2.0, "1 x the Zurich's 2 s")
        waits = a.nonlocal_vi_stop_event.waits
        # Three points (0 -> 4 -> 0 V): the pre-run after each 6221 switch-on.
        self.assertEqual(waits.count(2.0), 3)
        self.assertIn("effective_settle_s=2", log)
        self.assertIn("lockin_settle_s=2.0", log)
        raw = tables["VI_raw.csv"]
        self.assertEqual(list(raw[0])[-1], "settle_waited_s", "appended at the end")
        # 1st pass (SR830 1ω, the Zurich): the pre-run, counted from the
        # switch-on, so it includes the real (ms) time of the lock check and
        # is compared to 0.1 s; the SR830's 2ω pass: its own 10 τ settle (τ
        # read back 0.001 s here).
        first = {round(float(row["settle_waited_s"]), 1) for row in raw
                 if row["meter_key"] == "mfli" or row["harmonic"] == "1"}
        second = {row["settle_waited_s"] for row in raw if row["meter_key"] == "8" and row["harmonic"] == "2"}
        self.assertEqual((first, second), ({2.0}, {"0.01"}))
        summary = {(row["meter_key"], row["harmonic"]): row["settle_waited_s"] for row in tables["VI_summary.csv"]}
        self.assertEqual(summary[("8", "2")], "0.01")
        self.assertEqual({key: round(float(value), 1) for key, value in summary.items() if key != ("8", "2")},
                         {("8", "1"): 2.0, ("mfli", "2"): 2.0})

    def test_a_longer_time_constant_read_back_waits_longer(self):
        a, settings, _tables, log = self.run_scan(SlowZurichAC(), tc="2")
        self.assertEqual(settings["lockin_settle_s"], 2.0, "the confirmation's 1 x 2 s")
        self.assertEqual(a.nonlocal_vi_stop_event.waits.count(3.0), 3, "1 x the 3 s read back")
        self.assertIn("effective_settle_s=3", log)



# ------------------------------------------- review fixes (2026-10-14)
class RangeSettleTests(NoWindows):
    """A range step is a change too: the reading after it waits RT-E's
    5 x TC on an SR830 and 10 x TC on the Zurich, or LOCKIN_SETTLE_TAUS x TC
    if that is more.  Under the earlier 7 x TC rule an SR830 waited 7 x TC;
    at the user's 1 x TC (2026-10-14) the 5 x TC is the longer again."""
    settle = staticmethod(app.CombinedControlApp._nonlocal_range_settle_s)

    def test_5_tc_of_an_sr830_and_10_of_the_zurich(self):
        self.assertEqual(self.settle("8", 1.0), 5.0)
        self.assertEqual(self.settle("9", 2.0), 10.0)
        self.assertEqual(self.settle("mfli", 1.0), 10.0)
        for unknown in (math.nan, None, 0.0, -1.0, "x"):
            self.assertEqual(self.settle("8", unknown), 0.0)

    def test_it_follows_the_shared_constant(self):
        with mock.patch.object(app, "LOCKIN_SETTLE_TAUS", 12.0):
            self.assertEqual((self.settle("8", 1.0), self.settle("mfli", 1.0)), (12.0, 12.0))
        with mock.patch.object(app, "LOCKIN_SETTLE_TAUS", 0.0):
            self.assertEqual(self.settle("8", 1.0), 5.0, "RT-E's figure without the rule")

    def test_a_6221_iv_sr830_range_step_waits_5_tc(self):
        source = FakeSource(sr830_tau=1.0, readings=[
            {"auto_range": "SENS 9->10 (overload/high)", "measurement_valid": False}])
        waits, rows = IV6221WorkerTests.run_scan(self, source)
        # First point 10 x 1 s, its range step 5 x 1 s (RT-E's 5 x TC; 7 s
        # under the 7 x TC rule), then 1 x 1 s a point.
        self.assertEqual(waits, [10.0, 5.0, 1.0, 1.0])
        self.assertEqual([row[-1] for row in rows[1:]], ["10", "1", "1"], "the current step's wait")


class ZurichRangingAC(FakeAC):
    """At the first point the Zurich reads a valid 9 V on its old range,
    then steps its range, then reads the signal (2e-4 V).  With ``always``
    it steps its range on every reading."""

    def __init__(self, always=False):
        super().__init__()
        self.always = always
        self.script = [
            dict(X=9.0, R=9.0, voltage=9.0),
            dict(X=9.0, R=9.0, voltage=9.0, measurement_valid=False,
                 auto_range="RANGE 0.01->0.1 V (overload/high)"),
        ]

    def read_samples(self, frequency, keys):
        samples = super().read_samples(frequency, keys)
        if "mfli" in samples:
            if self.always:
                samples["mfli"].update(measurement_valid=False, auto_range="RANGE 3->3 V (overload/high)")
            elif self.script:
                samples["mfli"].update(self.script.pop(0))
        return samples


class SlowLockAC(FakeAC):
    """The lock check takes 0.1 s; the switch-on and the lock are logged."""

    def __init__(self, log):
        super().__init__()
        self.log = log

    def set_current(self, current, frequency):
        self.log.append("6221 on")
        return current

    def wait_references(self, frequency, stop, keys):
        time.sleep(0.1)
        self.log.append("locked")
        return {}


class LoggingEvent(RecordingEvent):
    """Logs the long waits beside the stand-in's steps."""

    def __init__(self, log):
        super().__init__()
        self.log = log

    def wait(self, timeout=None):
        if timeout and timeout >= 0.05:
            self.log.append(("wait", timeout))
        return super().wait(timeout)


class GateVIReviewTests(NoWindows):
    run_scan = GateVIWorkerTests.run_scan

    def test_a_range_step_drops_the_pass_and_waits_before_reading_on(self):
        a, _settings, tables, _log = self.run_scan(ZurichRangingAC(), tc="2", measure="0.5")
        self.assertEqual(a.nonlocal_vi_stop_event.waits.count(20.0), 1,
                         "10 x the Zurich's 2 s after its range step")
        raw = [row for row in tables["VI_raw.csv"] if row["meter_key"] == "mfli" and row["point_index"] == "1"]
        self.assertEqual([row["R_V"] for row in raw[:3]], ["9.0", "9.0", "0.0002"], "the raw CSV keeps all")
        self.assertEqual(raw[1]["auto_range_action"], "RANGE 0.01->0.1 V (overload/high)")
        summary = {(row["point_index"], row["meter_key"], row["harmonic"]): row
                   for row in tables["VI_summary.csv"]}
        # The valid 9 V before the step is not in the mean (it was).
        self.assertEqual(float(summary[("1", "mfli", "2")]["mean_voltage_V"]), 2e-4)
        self.assertEqual(float(summary[("1", "8", "1")]["mean_voltage_V"]), 1e-3)

    def test_a_meter_that_ranges_on_every_reading_does_not_stop_the_scan(self):
        a, _settings, tables, _log = self.run_scan(ZurichRangingAC(always=True), tc="2")
        # At most NONLOCAL_IV6221_MAX_RANGE_STEPS restarts a pass, then it reads on.
        self.assertEqual(a.nonlocal_vi_stop_event.waits.count(20.0), 3 * app.NONLOCAL_IV6221_MAX_RANGE_STEPS)
        self.assertEqual(len({row["point_index"] for row in tables["VI_summary.csv"]}), 3)

    def test_the_settle_counts_from_the_lock_not_the_switch_on(self):
        log = []
        _a, _settings, tables, _log = self.run_scan(SlowLockAC(log), tc="2", event=LoggingEvent(log))
        # Each point: the 6221 on, the lock 0.1 s later, then 1 x 2 s.
        steps = [entry for entry in log if entry in ("6221 on", "locked", ("wait", 2.0))]
        self.assertEqual(steps, ["6221 on", "locked", ("wait", 2.0)] * 3)
        waited = {float(row["settle_waited_s"]) for row in tables["VI_raw.csv"] if row["meter_key"] == "mfli"}
        self.assertTrue(all(2.09 <= value < 2.5 for value in waited), waited)

    def test_a_longer_pre_run_still_counts_from_the_switch_on(self):
        log = []
        _a, _settings, tables, _log = self.run_scan(SlowLockAC(log), tc="2", settle="30",
                                                    event=LoggingEvent(log))
        # The wait right after each lock (the SR830's 2ω pass then waits
        # max(Settle s, 10 τ) = 30 s of its own).
        waits = [log[index + 1][1] for index, entry in enumerate(log) if entry == "locked"]
        self.assertEqual(len(waits), 3)
        self.assertTrue(all(29.5 < wait < 29.95 for wait in waits), "30 s less the 0.1 s lock check")
        self.assertEqual({row["settle_waited_s"] for row in tables["VI_raw.csv"] if row["meter_key"] == "mfli"},
                         {"30.0"})


class IV6221StartReviewTests(NoWindows):
    page = IV6221StartTests.page
    start = IV6221StartTests.start

    def sr830_page(self, executor=None, tau=1.0, slope=12.0, **values):
        source = FakeSource(keys=("8",), sr830_slope=slope)
        source.read_sr830_time_constants = mock.Mock(return_value={"8": tau})
        values.setdefault("measure", {"8": True, "mfli": False})
        return self.page(source, executor=executor, **values)

    def test_the_page_is_busy_while_the_sr830_is_read(self):
        a = self.sr830_page(executor=HeldExecutor())
        self.start(a)
        self.assertTrue(a.nonlocal_vi_busy)
        a._update_nonlocal_vi_controls.assert_called()
        self.assertEqual(a.nonlocal_iv6221_status_var.get(),
                         "Reading the SR830 front-panel filters (OFLT?, OFSL?)")
        # Gate-VI Start and Set 6221 are refused meanwhile.
        with mock.patch.object(app, "messagebox") as messagebox, \
                mock.patch.object(app, "filedialog") as filedialog:
            a.start_nonlocal_vi_scan()
            a.set_nonlocal_iv6221_current()
        self.assertEqual(len(messagebox.showwarning.call_args_list), 2)
        for call in messagebox.showwarning.call_args_list:
            self.assertIn("busy", call.args[1])
        filedialog.askdirectory.assert_not_called()
        messagebox.askyesno.assert_not_called()
        a._begin_nonlocal_keithley_task.assert_not_called()
        # The read ends: the page is free again and the confirmation shows.
        with mock.patch.object(app, "messagebox") as messagebox:
            messagebox.askyesno.return_value = False
            a.nonlocal_vi_executor.finish()
        self.assertFalse(a.nonlocal_vi_busy)
        self.assertFalse(a.nonlocal_iv6221_tc_reading)
        messagebox.askyesno.assert_called_once()

    def test_an_off_pressed_during_the_read_cancels_the_start(self):
        a = self.sr830_page(executor=HeldExecutor())
        self.start(a)
        a.nonlocal_vi_off_pending = 1                 # as stop_nonlocal_vi queues it
        with mock.patch.object(app, "messagebox") as messagebox:
            a.nonlocal_vi_executor.finish()
        messagebox.askyesno.assert_not_called()
        messagebox.showwarning.assert_not_called()
        self.assertTrue(a.nonlocal_vi_busy, "busy until the OFF has run")
        self.assertEqual(a.nonlocal_iv6221_status_var.get(),
                         "6221-IV not started: the outputs are being switched off "
                         "(SR830 front panel: SR830 #8 1 s, 12 dB/oct)")

    def test_a_failed_read_frees_the_page(self):
        a = self.sr830_page()
        a.nonlocal_vi_ac_controller.read_sr830_filter_slopes = mock.Mock(side_effect=RuntimeError("VI_ERROR_TMO"))
        messagebox = self.start(a, accept=True)
        self.assertIn("VI_ERROR_TMO", messagebox.showwarning.call_args.args[1])
        self.assertFalse(a.nonlocal_vi_busy)
        self.assertEqual(a.nonlocal_iv6221_status_var.get(), "6221-IV not started: the SR830 filter was not read")
        a._begin_nonlocal_keithley_task.assert_not_called()

    def test_the_dialogs_open_from_the_event_loop_not_inside_the_ui_queue(self):
        a = self.sr830_page()
        posted, scheduled = [], []
        a.post_ui = posted.append
        a.root.after.side_effect = lambda ms, callback: scheduled.append((ms, callback))
        with mock.patch.object(app, "messagebox") as messagebox, mock.patch.object(app, "filedialog"):
            messagebox.askyesno.return_value = False
            a.start_nonlocal_iv6221()
            self.assertEqual(len(posted), 1)
            posted[0]()                               # what drain_ui_queue runs
            messagebox.askyesno.assert_not_called()
            self.assertEqual([ms for ms, _callback in scheduled], [0])
            self.assertTrue(a.nonlocal_vi_busy, "still busy until the event loop runs it")
            scheduled[0][1]()                         # the Tk event loop
            messagebox.askyesno.assert_called_once()

    def test_a_steeper_slope_is_shown_with_a_note(self):
        """The note compares the slope with 12 dB/oct after the same wait:
        at the user's 1 x TC 12 dB/oct itself shows about 26 % of an
        isolated step, so a fixed 99 % mark (7 x TC's) would note every
        scan (user, 2026-10-14)."""
        a = self.sr830_page(tau=3.0, slope=24.0)
        text = self.start(a).askyesno.call_args.args[1]
        self.assertIn("SR830 front panel (read back): SR830 #8 TC 3 s, 24 dB/oct\n", text)
        self.assertIn("Note: at 24 dB/oct SR830 #8 shows only about 1 % of each step when the point is "
                      "read, against about 26 % at 12 dB/oct (the wait is 1 × the slowest TC, or Settle s); "
                      "set 12 dB/oct on its front panel, or raise Settle s\n", text)
        self.assertEqual(a.nonlocal_iv6221_status_var.get(), "SR830 front panel: SR830 #8 3 s, 24 dB/oct")
        text = self.start(self.sr830_page(tau=3.0, slope=18.0)).askyesno.call_args.args[1]
        self.assertIn("Note: at 18 dB/oct SR830 #8 shows only about 8 % of each step", text)
        # A Settle s long enough (60 s = 20 x 3 s) needs no note; 6 and
        # 12 dB/oct neither.
        for page in (self.sr830_page(tau=3.0, slope=24.0, settle="60"), self.sr830_page(tau=3.0, slope=6.0),
                     self.sr830_page(tau=3.0, slope=12.0)):
            self.assertNotIn("Note:", self.start(page).askyesno.call_args.args[1])

    def test_the_slope_is_in_the_parameter_log(self):
        a = self.sr830_page(tau=3.0, slope=18.0)
        folder = Path(tempfile.mkdtemp())
        self.start(a, accept=True, folder=str(folder))
        log = next(folder.glob("6221-IV_*/*_parameters.log")).read_text(encoding="utf-8")
        self.assertIn("sr830_filter_slopes_db_per_oct={'8': 18.0}", log)

    def test_the_far_point_line_names_settle_s_when_it_sets_the_wait(self):
        a = self.page(FakeSource(keys=(), mfli=True), settle="30")
        text = self.start(a).askyesno.call_args.args[1]
        self.assertIn("Each point waits 30 s after its current step", text)
        self.assertIn("The first point of each pass waits 30 s (10 × the slowest lock-in TC, "
                      "or Settle 30 s if longer)", text)

    def test_the_settled_fraction_by_slope(self):
        fraction = app.CombinedControlApp._lockin_step_settled_fraction
        # After 1 time constant (the user's 1 x TC, 2026-10-14).
        self.assertAlmostEqual(fraction(1.0, 6), 1 - math.exp(-1), places=12)
        self.assertAlmostEqual(fraction(1.0, 12), 1 - 2 / math.e, places=12)
        self.assertAlmostEqual(fraction(1.0, 18), 0.0803, places=4)
        self.assertAlmostEqual(fraction(1.0, 24), 0.0190, places=4)
        self.assertAlmostEqual(fraction(7.0, 6), 1 - math.exp(-7), places=12)
        self.assertGreater(fraction(7.0, 12), 0.99)
        self.assertAlmostEqual(fraction(7.0, 18), 0.9704, places=4)
        self.assertAlmostEqual(fraction(7.0, 24), 0.9182, places=4)
        self.assertEqual(fraction(0.0, 12), 0.0)


class SR830SlopeReadTests(NoWindows):
    class FakeSR830:
        def __init__(self, code):
            self.code, self.queries, self.writes = code, [], []

        def write(self, command):
            self.writes.append(command)

        def query(self, command):
            self.queries.append(command)
            return str(self.code)

    def test_ofsl_is_read_and_nothing_written(self):
        controller = app.NonLocalACController(lambda _message: None)
        controller.lockins = {"8": self.FakeSR830(3), "9": self.FakeSR830(1)}
        self.assertEqual(controller.read_sr830_filter_slopes(("8", "9")), {"8": 24.0, "9": 12.0})
        for lockin in controller.lockins.values():
            self.assertEqual((lockin.queries, lockin.writes), (["OFSL?"], []))

    def test_a_bad_code_or_a_missing_sr830_raises(self):
        controller = app.NonLocalACController(lambda _message: None)
        controller.lockins = {"8": self.FakeSR830(4)}
        with self.assertRaisesRegex(RuntimeError, "filter-slope readback is invalid: OFSL 4"):
            controller.read_sr830_filter_slopes(("8",))
        with self.assertRaisesRegex(RuntimeError, "SR830 #9 is not connected"):
            controller.read_sr830_filter_slopes(("9",))


if __name__ == "__main__":
    unittest.main()
