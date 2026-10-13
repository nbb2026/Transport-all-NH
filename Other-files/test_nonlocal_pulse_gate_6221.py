"""Non-local review fixes (2026-10-13), each run through the changed code.

- Keithley pulse: the 0 V comes right after Width s; the :OUTP? read-back
  (50 ms query delay on the lab PC) comes after it, not before.
- Gate-VI pulse gate: a sample above the range at 1 nA (about 4.9 GΩ: an
  intact gate insulator or an open gap) is a lower bound the gate passes,
  not an error that stops the scan.
- Set 6221 / 6221-IV: a failed Keithley read-back still switches off a sine
  left on by Set 6221, and "6221: OFF" only follows its own read-back.

No Tk and no instrument: fake instruments answer the SCPI commands and
stand-in controllers record what the page asks of them."""
import csv
import math
import tempfile
import threading
import types
import unittest
from concurrent.futures import Future
from pathlib import Path
from unittest import mock

from test_nonlocal_6221_iv import FakeSource, page
from test_nonlocal_gate_vi_harmonics import Clock, gate_page
from test_nonlocal_keithley import FakeSourceMeter, Var, connected_controller
from test_ppms_motor_debug import app


# ------------------------------------------------- the pulse ends at Width s
class TimedMeter(FakeSourceMeter):
    """Every write and query in one log, with simulated time: each query
    takes the 50 ms query_delay the program sets on connect (pyvisa sleeps
    between the write and the read)."""

    QUERY_DELAY_S = 0.05

    def __init__(self):
        super().__init__()
        self.now = 0.0
        self.log = []
        self.fail_zero = False

    def write(self, command):
        self.log.append(("write", command, self.now))
        if self.fail_zero and command == ":SOUR:VOLT 0":
            raise RuntimeError("VI_ERROR_TMO")
        super().write(command)

    def query(self, command):
        self.now += self.QUERY_DELAY_S
        self.log.append(("query", command, self.now))
        return super().query(command)


class PulseEndTests(unittest.TestCase):
    def pulse(self, voltage_v, width_s):
        meter = TimedMeter()
        controller = connected_controller(meter)
        controller.model = "2400"
        controller.setup_voltage_mode(1e-3)

        def wait(duration, stop):
            meter.log.append(("wait", duration, meter.now))
            meter.now += duration

        def sleep(seconds):
            meter.now += seconds

        controller._wait = wait
        meter.log.clear()
        with mock.patch.object(app.time, "sleep", sleep):
            controller.apply_voltage_pulse(voltage_v, width_s, threading.Event())
        return meter, controller

    def test_the_zero_is_written_right_after_the_width(self):
        # The user's 2400-IV pulse (-30 V, 0.01 s) and the Gate-VI default
        # width (0.1 s) at 5 V.
        for voltage_v, width_s in ((-30.0, 0.01), (5.0, 0.1)):
            with self.subTest(voltage_v=voltage_v, width_s=width_s):
                meter, controller = self.pulse(voltage_v, width_s)
                log = [entry[:2] for entry in meter.log]
                level = log.index(("write", f":SOUR:VOLT {voltage_v:g}"))
                wait = log.index(("wait", width_s))
                self.assertEqual(wait, level + 1)
                self.assertEqual(log[wait + 1], ("write", ":SOUR:VOLT 0"),
                                 "nothing between the width and the 0 V")
                level_at, zero_at = meter.log[level][2], meter.log[wait + 1][2]
                self.assertAlmostEqual(zero_at - level_at, width_s, places=9,
                                       msg="the level lasts Width s, not 50 ms more")
                # Still drained at 0 V with the output on, then switched off.
                outp = log.index(("query", ":OUTP?"), wait + 1)
                first_read = log.index(("query", ":READ?"), wait + 1)
                switch_off = log.index(("write", ":OUTP OFF"), wait + 1)
                self.assertLess(wait + 1, outp)
                self.assertLess(outp, first_read)
                self.assertLess(first_read, switch_off)
                self.assertEqual(meter.output, 0)
                self.assertFalse(controller.high_voltage_active, "drained: no second drain")

    def test_a_failed_zero_still_drains_and_switches_off(self):
        # As before the change: the drain and :OUTP OFF follow even if the
        # 0 V write fails, and the shutdown then fails, naming it.
        meter = TimedMeter()
        controller = connected_controller(meter)
        meter.output, meter.voltage = 1, 30.0
        controller.high_voltage_active = True
        meter.fail_zero = True
        with mock.patch.object(app.time, "sleep", lambda seconds: None), \
                self.assertRaisesRegex(RuntimeError, r"safe shutdown failed: :SOUR:VOLT 0: VI_ERROR_TMO"):
            controller.safe_shutdown(reset=False)
        log = [entry[:2] for entry in meter.log]
        self.assertEqual(log[0], ("write", ":SOUR:VOLT 0"))
        self.assertIn(("query", ":READ?"), log, "drained")
        self.assertEqual(log[-1], ("write", ":OUTP OFF"))
        self.assertEqual(meter.output, 0)
        self.assertTrue(controller.high_voltage_active, "not known to be drained at 0 V")


# --------------------------------------------- the gate above the 1 nA range
class ClampedSample(FakeSourceMeter):
    """A real 2410 sourcing current into a high resistance: V stops at the
    5 V limit and the current is then V / R (none for an open gap)."""

    def query(self, command):
        if command.upper() == ":READ?" and self.mode == "CURR" and self.output \
                and math.isfinite(self.resistance_ohm):
            voltage = min(self.current_source * self.resistance_ohm, app.NONLOCAL_VI_RES_COMPLIANCE_V)
            return f"{voltage:.6e},{voltage / self.resistance_ohm:.6e}"
        return super().query(command)


BOUND_OHM = 0.98 * app.NONLOCAL_VI_RES_COMPLIANCE_V / app.NONLOCAL_VI_RES_CURRENT_LEVELS_A[0]


def measure(resistance_ohm):
    clock = Clock()
    meter = ClampedSample(resistance_ohm)
    controller = connected_controller(meter)
    sources = []
    with mock.patch.object(app, "time", types.SimpleNamespace(monotonic=clock.monotonic, sleep=clock.sleep)):
        result = controller.measure_resistance(clock, status_callback=sources.append)
    return result, meter, sources


class OverRangeTests(unittest.TestCase):
    def test_above_the_range_at_1_na_is_a_finite_lower_bound(self):
        self.assertAlmostEqual(BOUND_OHM, 4.9e9)
        for resistance_ohm in (5e9, 2e10, 1e12, math.inf):
            with self.subTest(resistance_ohm=resistance_ohm):
                result, meter, sources = measure(resistance_ohm)
                self.assertTrue(result["over_range"])
                self.assertTrue(math.isfinite(result["resistance_ohm"]))
                self.assertGreaterEqual(result["resistance_ohm"], BOUND_OHM)
                if math.isfinite(resistance_ohm):
                    # A current read at the limit is a real reading: kept.
                    self.assertAlmostEqual(result["resistance_ohm"], resistance_ohm,
                                           delta=resistance_ohm * 1e-5)
                else:
                    self.assertAlmostEqual(result["resistance_ohm"], BOUND_OHM, delta=1.0,
                                           msg="no current: the bound itself")
                self.assertEqual(sources, [1e-9], "held at 1 nA")
                self.assertGreater(result["sample_count"], 0)
                self.assertEqual((meter.output, meter.mode), (0, "VOLT"),
                                 "off and back in the pulse set-up")

    def test_within_the_range_nothing_changes(self):
        for resistance_ohm in (3e9, 4.8e9):
            with self.subTest(resistance_ohm=resistance_ohm):
                result, _meter, _sources = measure(resistance_ohm)
                self.assertFalse(result["over_range"])
                self.assertAlmostEqual(result["resistance_ohm"], resistance_ohm,
                                       delta=resistance_ohm * 1e-5)

    def test_no_current_below_the_limit_is_still_an_error(self):
        meter = FakeSourceMeter()
        controller = connected_controller(meter)
        meter.output = 1
        meter.query = (lambda original: lambda command: (
            "1.000000e+00,0.000000e+00" if command.upper() == ":READ?" else original(command)))(meter.query)
        with self.assertRaisesRegex(RuntimeError, "too small"):
            controller._read_resistance_sample()


class RecordingVar(Var):
    def __init__(self, value=""):
        super().__init__(value)
        self.history = []

    def set(self, value):
        super().set(value)
        self.history.append(value)


class GateWorkerTests(unittest.TestCase):
    def run_scan(self, gate_megohm, result):
        a = gate_page("1", "2")
        a.nonlocal_vi_gate_resistance_var.set(gate_megohm)
        a.nonlocal_vi_2410_controller.measure_resistance.return_value = result
        settings = a._parse_nonlocal_vi_settings()
        settings["sample_interval_s"] = 0.01
        a.nonlocal_vi_stop_event = threading.Event()
        a.nonlocal_vi_interlock = app.NonLocalVIInterlock()
        a.nonlocal_vi_data_lock = threading.Lock()
        a.nonlocal_vi_time_rows, a.nonlocal_vi_summary_rows = [], []
        a.nonlocal_vi_pulse_rows, a.nonlocal_vi_current_rows, a.nonlocal_vi_resistance_rows = [], [], []
        a.post_ui = lambda callback: callback()
        a._redraw_nonlocal_vi_plots = mock.Mock()
        a._publish_reference_sync = mock.Mock()
        a.nonlocal_vi_progress = mock.Mock()
        a.nonlocal_vi_eta_var, a.nonlocal_vi_output_var = Var(), Var()
        a.nonlocal_vi_status_var = RecordingVar()
        directory = Path(tempfile.mkdtemp())
        return a, settings, directory

    def test_the_default_gate_passes_a_sample_above_the_range(self):
        result, _meter, _sources = measure(math.inf)   # the real controller's result
        a, settings, directory = self.run_scan("100", result)
        a._nonlocal_vi_scan_worker(settings, directory)
        self.assertEqual(a.nonlocal_vi_2410_controller.apply_voltage_pulse.call_count,
                         len(settings["voltage_scan"]), "every pulse given")
        with (directory / "VI_resistance_gate.csv").open(encoding="utf-8-sig") as file:
            rows = list(csv.reader(file))
        stages = [row[1] for row in rows[1:]]
        self.assertEqual(stages[0], "initial_gate", "the stage text the plot filters on")
        self.assertAlmostEqual(float(rows[1][7]), BOUND_OHM, delta=1.0)
        self.assertIn("Keithley 2400 |R|≥4.900000e+09 Ω (above the range at 1 nA)",
                      a.nonlocal_vi_status_var.history)

    def test_a_gate_above_the_range_says_why_it_blocks(self):
        result, _meter, _sources = measure(math.inf)
        a, settings, directory = self.run_scan("5000", result)
        with self.assertRaisesRegex(
                RuntimeError,
                r"Pulse 1 blocked by the resistance gate: \|R\|≥4\.900000e\+09 Ω "
                r"\(above the range at 1 nA\), must be >5\.000000e\+09 Ω"):
            a._nonlocal_vi_scan_worker(settings, directory)
        a.nonlocal_vi_2410_controller.apply_voltage_pulse.assert_not_called()

    def test_a_measured_value_keeps_its_old_message(self):
        result = {"source_current_a": 1e-9, "voltage_v": 0.05, "current_a": 1e-9,
                  "resistance_ohm": 5e7, "sample_count": 25, "over_range": False}
        a, settings, directory = self.run_scan("100", result)
        with self.assertRaisesRegex(RuntimeError, r"\|R\|=5\.000000e\+07 Ω, must be >1\.000000e\+08 Ω$"):
            a._nonlocal_vi_scan_worker(settings, directory)
        a.nonlocal_vi_2410_controller.apply_voltage_pulse.assert_not_called()


# -------------------------- a failed Keithley read-back still ends the 6221
class Executor:
    """Runs the worker at once, as the page's executor thread would."""

    def submit(self, worker):
        future = Future()
        try:
            future.set_result(worker())
        except BaseException as error:
            future.set_exception(error)
        return future


def keithley_read_back_fails():
    keithley = mock.Mock(connected=True, model="2410")
    keithley.confirm_output_off.side_effect = RuntimeError("VI_ERROR_TMO")
    return keithley


def sine_left_on(source):
    """As after Set 6221: the sine on, left on until a scan or OFF."""
    source.output_on, source.current = True, 0.1
    return source


class StuckSource(FakeSource):
    """A 6221 that does not read back off."""

    def confirm_output_off(self, context):
        self.calls.append(("confirm_output_off",))
        raise RuntimeError("6221 output still on")


class SixTwoTwoOneOffTests(unittest.TestCase):
    def page(self, source):
        a = page(source)
        a.closing = True   # no dialogs
        a.nonlocal_vi_2410_controller = keithley_read_back_fails()
        a.nonlocal_vi_interlock = app.NonLocalVIInterlock()
        a.nonlocal_keithley_stop_event = threading.Event()
        a.nonlocal_keithley_task = None
        a.nonlocal_vi_executor = Executor()
        a.post_ui = lambda callback: callback()
        a._update_nonlocal_vi_controls = mock.Mock()
        a._set_mfli_lock_state = mock.Mock()
        a._redraw_nonlocal_iv6221_plots = mock.Mock()
        a.nonlocal_iv_keithley_var = Var("Keithley 2410: OFF")
        a.nonlocal_iv6221_source_var = Var("6221 ON: 0.1 mA RMS / 17.3 Hz")
        a.nonlocal_vi_output_var, a.nonlocal_vi_status_var = Var(), Var()
        a.nonlocal_iv6221_status_var, a.nonlocal_iv6221_point_var = Var(), Var()
        a.nonlocal_iv6221_rows, a.nonlocal_iv6221_total_points = [], 100
        a.nonlocal_iv6221_csv_path = None
        return a

    def set_6221(self, source):
        a = self.page(source)
        settings = a._parse_nonlocal_iv6221_lockins()
        a._begin_nonlocal_keithley_task(
            "iv6221set", lambda: a._nonlocal_iv6221_set_worker(settings),
            a._finish_nonlocal_iv6221_set)
        return a

    def scan_6221(self, source):
        a = self.page(source)
        settings = a._parse_nonlocal_iv6221_settings()
        path = Path(tempfile.mkdtemp()) / "6221-IV_test.csv"
        a._begin_nonlocal_keithley_task(
            "iv6221", lambda: a._nonlocal_iv6221_worker(settings, path),
            a._finish_nonlocal_iv6221)
        return a

    def test_set_6221_again_switches_the_sine_off_and_reads_it_back(self):
        source = sine_left_on(FakeSource())
        a = self.set_6221(source)
        self.assertEqual(source.calls, [("output_off",), ("confirm_output_off",)],
                         "only switched off: nothing set up or switched on")
        self.assertFalse(source.output_on)
        self.assertIsNone(a.nonlocal_keithley_task)
        self.assertEqual(a.nonlocal_iv6221_source_var.get(), "6221: OFF")
        self.assertEqual(a.nonlocal_vi_output_var.get(), "Keithley 2410 output: check it")
        self.assertEqual(a.nonlocal_iv_keithley_var.get(), "Keithley 2410: check the output")
        self.assertEqual(a.nonlocal_vi_status_var.get(),
                         "Set 6221 failed: VI_ERROR_TMO; 6221 output off (read back); "
                         "check the Keithley 2410 output")

    def test_6221_iv_start_switches_the_sine_off_and_reads_it_back(self):
        source = sine_left_on(FakeSource())
        a = self.scan_6221(source)
        self.assertEqual(source.calls, [("output_off",), ("confirm_output_off",)])
        self.assertFalse(source.output_on)
        self.assertEqual(a.nonlocal_vi_interlock.phase, "IDLE")
        self.assertIsNone(a.nonlocal_keithley_task)
        self.assertEqual(a.nonlocal_iv6221_source_var.get(), "6221: OFF")
        self.assertEqual(a.nonlocal_vi_output_var.get(), "Keithley 2410 output: check it")
        self.assertEqual(a.nonlocal_iv6221_status_var.get(), "Failed: VI_ERROR_TMO")
        self.assertTrue(a.nonlocal_vi_status_var.get().endswith("; check the Keithley 2410 output"))

    def test_without_a_read_back_the_6221_is_never_shown_off(self):
        for start in (self.set_6221, self.scan_6221):
            with self.subTest(start=start.__name__):
                source = sine_left_on(StuckSource())
                a = start(source)
                self.assertEqual(source.calls, [("output_off",), ("confirm_output_off",)])
                self.assertEqual(a.nonlocal_iv6221_source_var.get(), "6221: check the output")
                self.assertNotIn("6221 output off", a.nonlocal_vi_status_var.get())

    def test_a_stop_during_the_keithley_check_also_switches_the_sine_off(self):
        source = sine_left_on(FakeSource())
        a = self.page(source)
        a.nonlocal_vi_2410_controller = mock.Mock(connected=True, model="2410")
        a.nonlocal_vi_2410_controller.confirm_output_off.side_effect = (
            lambda context: a.nonlocal_keithley_stop_event.set())
        settings = a._parse_nonlocal_iv6221_lockins()
        a._begin_nonlocal_keithley_task(
            "iv6221set", lambda: a._nonlocal_iv6221_set_worker(settings),
            a._finish_nonlocal_iv6221_set)
        self.assertEqual(source.calls, [("output_off",), ("confirm_output_off",)])
        self.assertFalse(source.output_on)
        self.assertEqual(a.nonlocal_iv6221_source_var.get(), "6221: OFF")
        self.assertEqual(a.nonlocal_vi_status_var.get(), "Set 6221 stopped; 6221 output off (read back)")


if __name__ == "__main__":
    unittest.main()
