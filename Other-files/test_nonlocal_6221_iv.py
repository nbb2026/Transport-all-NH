"""Non-local page, 6221-IV: the RT-E Current scan on the page's own 6221 and
lock-ins.  No Tk and no instrument: a fake 6221/lock-in controller records
what the scan asks of it, and a stand-in canvas records the plots.
"""
import csv
import tempfile
import threading
import unittest
from concurrent.futures import Future
from pathlib import Path
from unittest import mock

from test_nonlocal_keithley import Canvas, Var, bare_app
from test_ppms_motor_debug import app


class FakeSource:
    """The 6221 with SR830 #8 at its 2nd harmonic: X follows the current."""

    def __init__(self, fail_read=False):
        self.calls = []
        self.source_connected = True
        self.connected_lockin_keys = ("8",)
        self.mfli_connected = False
        self.output_on = False
        self.current = 0.0
        self.fail_read = fail_read

    def configure(self, initial_rms_ma, frequency_hz, compliance_v, lockin_configs,
                  marker_line, **options):
        self.calls.append(("configure", initial_rms_ma, frequency_hz, compliance_v,
                           dict(lockin_configs), marker_line, options))
        return frequency_hz, {"8": {"harmonic": 2, "meter_type": "SR830"}}

    def set_current(self, rms_ma, frequency_hz):
        self.calls.append(("set_current", rms_ma))
        self.output_on, self.current = True, rms_ma
        return rms_ma

    def wait_references(self, frequency_hz, stop_event, keys):
        self.calls.append(("wait_references", tuple(keys)))
        return {"8": {"valid": True, "frequency_hz": frequency_hz, "status": 0}}

    def read_samples(self, frequency_hz, keys):
        if self.fail_read:
            raise RuntimeError("VI_ERROR_TMO")
        assert self.output_on
        return {"8": {"X": self.current * 1e-6, "Y": 0.0, "R": self.current * 1e-6,
                      "phase_deg": 5.0, "reference_valid": True,
                      "measurement_valid": True, "in_compliance": False}}

    def output_off(self):
        self.calls.append(("output_off",))
        self.output_on = False

    def confirm_output_off(self, context):
        self.calls.append(("confirm_output_off",))
        assert not self.output_on


def settings(plan, **overrides):
    values = {
        "plan": plan, "frequency_hz": 17.3, "compliance_v": 50.0,
        "lockin_configs": {"8": {"coupling": "AC", "harmonic": 2, "channel": "X",
                                 "resource": "GPIB0::8::INSTR"}},
        "mfli_config": None, "marker_line": 4, "measurement_keys": ("8",),
        "settle_s": 0.0, "acquire_s": 1e-9,
    }
    values.update(overrides)
    return values


class WorkerTests(unittest.TestCase):
    def setUp(self):
        self.app = a = bare_app()
        self.source = FakeSource()
        a.nonlocal_vi_ac_controller = self.source
        a.nonlocal_vi_2410_controller = mock.Mock(connected=False)
        a.nonlocal_vi_interlock = app.NonLocalVIInterlock()
        a.nonlocal_keithley_stop_event = threading.Event()
        a.nonlocal_keithley_output_off = False
        self.posted = []
        a.post_ui = self.posted.append
        a._publish_reference_sync = mock.Mock()
        a.nonlocal_vi_output_var = Var()
        self.folder = Path(tempfile.mkdtemp())

    def run_scan(self, plan, **overrides):
        path = self.folder / "6221-IV_test.csv"
        self.app._nonlocal_iv6221_worker(settings(plan, **overrides), path)
        return path

    def test_the_current_is_stepped_and_each_point_saved_then_the_6221_is_off(self):
        plan = app.rte_scan_plan("Current", 0.1, 0.3, 0.1, 1, False)
        path = self.run_scan(plan)
        calls = self.source.calls
        self.assertEqual(calls[0][0], "configure")
        self.assertEqual(calls[0][1], 0.1, "configured at the first current")
        self.assertEqual(calls[0][6]["output_mode"], "AC")
        self.assertFalse(calls[0][6]["use_nanovoltmeter"])
        self.assertEqual([c[1] for c in calls if c[0] == "set_current"], [0.1, 0.2, 0.3])
        self.assertEqual(sum(c[0] == "wait_references" for c in calls), 1, "the first point only")
        self.assertEqual(calls[-2:], [("output_off",), ("confirm_output_off",)])
        self.assertTrue(self.app.nonlocal_keithley_output_off)
        self.assertEqual(self.app.nonlocal_vi_interlock.phase, "IDLE")
        with path.open(encoding="utf-8-sig") as file:
            rows = list(csv.reader(file))
        self.assertEqual(rows[0][:5], ["point", "loop", "loop_point", "set_current_rms_mA", "current_rms_mA"])
        self.assertIn("SR830_8_X_V", rows[0])
        self.assertEqual(len(rows), 4)
        self.assertAlmostEqual(float(rows[3][rows[0].index("SR830_8_X_V")]), 0.3e-6)
        appended = []
        self.app._append_nonlocal_iv6221_point = appended.append
        for callback in self.posted:
            callback()
        self.assertEqual([row["current_rms_ma"] for row in appended], [0.1, 0.2, 0.3])
        self.assertEqual(appended[0]["8"]["harmonic"], 2)

    def test_loops_and_round_trip_follow_the_rte_plan(self):
        plan = app.rte_scan_plan("Current", 0.1, 0.2, 0.1, 2, True)
        self.run_scan(plan)
        self.assertEqual([c[1] for c in self.source.calls if c[0] == "set_current"],
                         [0.1, 0.2, 0.1, 0.1, 0.2, 0.1])

    def test_stop_or_a_failed_read_switches_the_6221_off(self):
        plan = app.rte_scan_plan("Current", 0.1, 0.3, 0.1, 1, False)
        self.app.nonlocal_keithley_stop_event.set()
        with self.assertRaises(InterruptedError):
            self.run_scan(plan)
        self.assertEqual(self.source.calls, [], "a Stop before the start sends nothing")
        self.app.nonlocal_keithley_stop_event.clear()
        self.source = FakeSource(fail_read=True)
        self.app.nonlocal_vi_ac_controller = self.source
        with self.assertRaisesRegex(RuntimeError, "VI_ERROR_TMO"):
            self.app._nonlocal_iv6221_worker(settings(plan), self.folder / "failed.csv")
        self.assertEqual(self.source.calls[-2:], [("output_off",), ("confirm_output_off",)])
        self.assertFalse(self.source.output_on)
        self.assertEqual(self.app.nonlocal_vi_interlock.phase, "IDLE")

    def test_a_connected_keithley_is_checked_off_first(self):
        keithley = self.app.nonlocal_vi_2410_controller = mock.Mock(connected=True)
        self.run_scan(app.rte_scan_plan("Current", 0.1, 0.1, 0.1, 1, False))
        contexts = [call.args[0] for call in keithley.confirm_output_off.call_args_list]
        self.assertEqual(contexts[0], "before the 6221-IV scan")


class ParseTests(unittest.TestCase):
    def setUp(self):
        self.app = a = bare_app()
        a.nonlocal_vi_ac_controller = FakeSource()
        a.nonlocal_iv6221_start_var, a.nonlocal_iv6221_end_var = Var("0.01"), Var("1")
        a.nonlocal_iv6221_step_var, a.nonlocal_iv6221_loops_var = Var("0.01"), Var("1")
        a.nonlocal_iv6221_round_trip_var = Var(False)
        a.nonlocal_iv6221_frequency_var, a.nonlocal_iv6221_compliance_var = Var("17.3"), Var("50")
        a.nonlocal_iv6221_settle_var, a.nonlocal_iv6221_acquire_var = Var("5"), Var("2")
        a.nonlocal_vi_marker_line_var = Var("4")
        a.nonlocal_vi_measurement_enabled_vars = {"8": Var(True), "9": Var(False)}
        a.nonlocal_vi_lockin_coupling_vars = {"8": Var("AC"), "9": Var("AC")}
        a.nonlocal_vi_lockin_harmonic_vars = {"8": Var(2), "9": Var(2)}
        a.nonlocal_vi_lockin_channel_vars = {"8": Var("X"), "9": Var("R")}
        a.nonlocal_vi_lockin_resource_vars = {"8": Var("GPIB0::8::INSTR"), "9": Var("GPIB0::9::INSTR")}
        a.nonlocal_vi_use_mfli_var = Var(False)

    def test_the_rte_defaults_give_100_points(self):
        parsed = self.app._parse_nonlocal_iv6221_settings()
        self.assertEqual(parsed["measurement_keys"], ("8",))
        self.assertEqual(parsed["lockin_configs"]["8"]["harmonic"], 2)
        self.assertEqual(len(parsed["plan"]), 100)
        self.assertAlmostEqual(parsed["plan"][0][2], 0.01)
        self.assertAlmostEqual(parsed["plan"][-1][2], 1.0)

    def test_settings_that_are_refused(self):
        a = self.app
        for setup, message in (
            (lambda: a.nonlocal_iv6221_start_var.set("0"), "above 0 mA RMS"),
            (lambda: a.nonlocal_iv6221_end_var.set("80"), "105 mA sine peak"),
            (lambda: a.nonlocal_vi_measurement_enabled_vars["8"].set(False), "at least one"),
            (lambda: a.nonlocal_vi_measurement_enabled_vars["9"].set(True), "SR830 #9 is ticked"),
            (lambda: a.nonlocal_iv6221_acquire_var.set("0"), "Acquire time"),
            (lambda: a.nonlocal_iv6221_loops_var.set("1.5"), "Loops"),
            (lambda: a.nonlocal_iv6221_frequency_var.set("x"), "valid numbers"),
        ):
            with self.subTest(message=message):
                self.setUp()
                a = self.app
                setup()
                with self.assertRaisesRegex(ValueError, message):
                    a._parse_nonlocal_iv6221_settings()


class FinishAndPlotTests(unittest.TestCase):
    def test_finish_messages_name_the_csv_and_the_6221(self):
        a = bare_app()
        a.closing = True
        a.nonlocal_keithley_task, a.nonlocal_last_task = "iv6221", "iv6221"
        a.nonlocal_vi_2410_controller = app.NonLocalVI2410Controller(lambda _m: None, "2400")
        a.nonlocal_vi_output_var, a.nonlocal_vi_status_var, a.nonlocal_iv6221_eta_var = Var(), Var(), Var()
        a.nonlocal_iv6221_rows, a.nonlocal_iv6221_total_points = [{}] * 3, 100
        a.nonlocal_iv6221_csv_path = Path("/data/6221-IV_20261008_120000.csv")
        a._redraw_nonlocal_iv6221_plots = mock.Mock()
        a._update_nonlocal_vi_controls = mock.Mock()
        for error, off, expected in (
            (InterruptedError("stopped"), True,
             "6221-IV scan stopped after 3/100 points; 6221 output off. CSV: 6221-IV_20261008_120000.csv"),
            (RuntimeError("compliance; more"), False,
             "6221-IV scan stopped by an error after 3/100 points: compliance; check the 6221 output"),
        ):
            with self.subTest(expected=expected):
                a.nonlocal_keithley_task = "iv6221"
                a.nonlocal_keithley_output_off = off
                future = Future()
                future.set_exception(error)
                a._finish_nonlocal_iv6221(future)
                self.assertEqual(a.nonlocal_vi_status_var.get(), expected)
                self.assertIsNone(a.nonlocal_keithley_task)
        self.assertEqual(a.nonlocal_vi_output_var.get(), "6221 output: check it")

    def test_x_y_and_theta_are_plotted_against_the_current(self):
        a = bare_app()
        a.nonlocal_iv6221_canvases = {key: Canvas(900, 400) for key in ("X", "Y", "theta")}
        a.nonlocal_iv6221_settings = {"measurement_keys": ("8",)}
        a.nonlocal_iv6221_rows = [
            {"current_rms_ma": i / 10, "8": {"harmonic": 2, "X": i * 1e-7, "Y": 0.0, "theta": 3.0}}
            for i in range(1, 6)
        ]
        a._redraw_nonlocal_iv6221_plots()
        texts = a.nonlocal_iv6221_canvases["X"].texts()
        self.assertIn("6221 current (mA RMS)", texts)
        self.assertIn("SR830 #8 2ω", texts)
        self.assertTrue(any(text.startswith("X (") for text in texts if text), texts)
        self.assertIn("θ (°)", a.nonlocal_iv6221_canvases["theta"].texts())


if __name__ == "__main__":
    unittest.main()
