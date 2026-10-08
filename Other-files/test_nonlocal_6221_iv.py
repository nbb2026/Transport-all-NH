"""Non-local page, 6221-IV: the RT-E Current scan on the page's own 6221,
SR830 #8/#9 and Zurich, with RT-E's settings.  No Tk and no instrument: a
fake 6221/lock-in controller records what the scan asks of it, and a
stand-in canvas records the plots.
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
    """The 6221 with SR830 #8/#9 and the Zurich.  Each SR830 reads X = the
    current × 1e-6 × its harmonic; the Zurich demodulators read X = the
    current × 1e-7 × their order."""

    def __init__(self, fail_read=False, readings=None, locked=True, keys=("8",),
                 mfli=False, sr830_tau=0.1):
        self.calls = []
        # Optional list of per-read overrides for each SR830 (consumed in turn).
        self.readings = list(readings or [])
        self.locked = locked
        self.source_connected = True
        self.connected_lockin_keys = tuple(keys)
        self.mfli_connected = mfli
        self.mfli_configured = False
        self.output_on = False
        self.current = 0.0
        self.fail_read = fail_read
        self.sr830_tau = sr830_tau
        self.harmonics = {}
        self.zurich_orders = (1,)

    def configure(self, initial_rms_ma, frequency_hz, compliance_v, lockin_configs,
                  marker_line, **options):
        self.calls.append(("configure", initial_rms_ma, frequency_hz, compliance_v,
                           dict(lockin_configs), marker_line, options))
        self.harmonics = {key: config["harmonic"] for key, config in lockin_configs.items()}
        self.mfli_configured = bool(options.get("mfli_config"))
        readbacks = {
            key: {"harmonic": config["harmonic"], "meter_type": "SR830",
                  "time_constant_s": self.sr830_tau}
            for key, config in lockin_configs.items()
        }
        if self.mfli_configured:
            self.zurich_orders = (options["mfli_config"]["harmonic"],)
            readbacks["mfli"] = {"meter_type": "MFLI", "sample_rate_hz": 25.0}
        return frequency_hz, readbacks

    def configure_second_harmonic(self, tau, rate=1.0):
        self.calls.append(("configure_second_harmonic", tau, rate))
        self.zurich_orders = (1, 2)

    def configure_third_harmonic(self, tau, rate=1.0):
        self.calls.append(("configure_third_harmonic", tau, rate))
        self.zurich_orders = (1, 2, 3)

    def disable_unused_ppms_demods(self, mode):
        self.calls.append(("disable_unused_ppms_demods", mode))

    def set_lockin_harmonic(self, key, harmonic):
        self.calls.append(("set_lockin_harmonic", key, harmonic))
        self.harmonics[key] = harmonic

    def set_current(self, rms_ma, frequency_hz):
        self.calls.append(("set_current", rms_ma))
        self.output_on, self.current = True, rms_ma
        return rms_ma

    def update_amplitude(self, rms_ma):
        # RT-E's live amplitude change: only while the sine runs.
        assert self.output_on
        self.calls.append(("update_amplitude", rms_ma))
        self.current = rms_ma
        return rms_ma

    def wait_references(self, frequency_hz, stop_event, keys):
        self.calls.append(("wait_references", tuple(keys)))
        result = {key: {"valid": self.locked, "frequency_hz": frequency_hz} for key in keys}
        if self.mfli_configured:
            result["mfli"] = {"valid": self.locked, "frequency_hz": frequency_hz}
        return result

    def read_samples(self, frequency_hz, keys):
        if self.fail_read:
            raise RuntimeError("VI_ERROR_TMO")
        assert self.output_on
        override = self.readings.pop(0) if self.readings else {}
        result = {}
        for key in keys:
            if key == "mfli":
                order = self.zurich_orders[0]
                result[key] = {"X": self.current * 1e-7 * order, "Y": 0.0,
                               "measurement_valid": True, "auto_range": "",
                               "in_compliance": False, "status_text": "OK"}
                continue
            sample = {"X": self.current * 1e-6 * self.harmonics[key], "Y": 0.0,
                      "R": self.current * 1e-6, "phase_deg": 5.0, "reference_valid": True,
                      "auto_range": "", "measurement_valid": True, "in_compliance": False,
                      "status_text": "OK"}
            sample.update(override)
            result[key] = sample
        return result

    def read_mfli_four_demods(self):
        self.calls.append(("read_mfli_four_demods",))
        fields = {}
        # Demods 1-3 on Signal Input 1 at orders 1, 2, 3; Demod 4 the
        # external reference (Aux Input 1, adcselect 8) at order 1.
        for index, (order, adc) in enumerate(((1, 0), (2, 0), (3, 0), (1, 8)), 1):
            enabled = order in self.zurich_orders or index == 4
            prefix = f"zurich_demod{index}_"
            fields.update({prefix + "harmonic": order if enabled else "",
                           prefix + "input": adc if enabled else "",
                           prefix + "x_v": self.current * 1e-7 * order if enabled else "",
                           prefix + "y_v": 0.0 if enabled else ""})
        return fields

    def output_off(self):
        self.calls.append(("output_off",))
        self.output_on = False

    def confirm_output_off(self, context):
        self.calls.append(("confirm_output_off",))
        assert not self.output_on


def page(source, **values):
    """A page with the 6221-IV variables at RT-E's start-up values, but the
    SR830 #8 measured at its 2nd harmonic unless told otherwise."""
    a = bare_app()
    a.nonlocal_vi_ac_controller = source
    a.nonlocal_iv6221_start_var, a.nonlocal_iv6221_end_var = Var("0.01"), Var("1")
    a.nonlocal_iv6221_step_var, a.nonlocal_iv6221_loops_var = Var("0.01"), Var("1")
    a.nonlocal_iv6221_round_trip_var = Var(False)
    a.nonlocal_iv6221_current_var = Var("0.1")
    a.nonlocal_iv6221_frequency_var, a.nonlocal_iv6221_compliance_var = Var("17.3"), Var("50")
    a.nonlocal_iv6221_settle_var, a.nonlocal_iv6221_acquire_var = Var("0.5"), Var("1")
    a.nonlocal_vi_marker_line_var = Var("4")
    a.nonlocal_iv6221_harmonic_vars = {"8": Var("2"), "9": Var("2"), "mfli": Var("1+2+3")}
    a.nonlocal_iv6221_measure_vars = {"8": Var(True), "9": Var(False), "mfli": Var(False)}
    a.nonlocal_mfli_tc_var = Var("0.1")
    a.nonlocal_vi_mfli_reference_source_var = Var("Aux Input 1")
    a.nonlocal_vi_lockin_resource_vars = {"8": Var("GPIB0::8::INSTR"), "9": Var("GPIB0::9::INSTR")}
    a.nonlocal_vi_mfli_device_var = Var("DEV4435 (Normal)")
    for name, value in values.items():
        if name in ("harmonics", "measure"):
            target = (a.nonlocal_iv6221_harmonic_vars if name == "harmonics"
                      else a.nonlocal_iv6221_measure_vars)
            for key, item in value.items():
                target[key].set(item)
        else:
            getattr(a, f"nonlocal_iv6221_{name}_var").set(value)
    return a


class WorkerTests(unittest.TestCase):
    def make(self, source=None, **values):
        self.source = source or FakeSource()
        self.app = a = page(self.source, **values)
        a.nonlocal_vi_2410_controller = mock.Mock(connected=False)
        a.nonlocal_vi_interlock = app.NonLocalVIInterlock()
        a.nonlocal_keithley_stop_event = threading.Event()
        a.nonlocal_keithley_output_off = False
        self.posted = []
        a.post_ui = self.posted.append
        a._publish_reference_sync = mock.Mock()
        a._show_nonlocal_mfli_lock = mock.Mock()
        a.nonlocal_iv6221_source_var, a.nonlocal_iv6221_point_var = Var(), Var()
        return a

    def setUp(self):
        self.folder = Path(tempfile.mkdtemp())
        # No 10-time-constant wait at far jumps, except where tested.
        for name, value in (("NONLOCAL_IV6221_FIRST_SETTLE_TAUS", 0.0),
                            ("NONLOCAL_IV6221_REFERENCE_WAIT_S", 0.0)):
            patcher = mock.patch.object(app, name, value)
            patcher.start()
            self.addCleanup(patcher.stop)
        self.make()

    def run_scan(self, name="6221-IV_test.csv", **overrides):
        settings = self.app._parse_nonlocal_iv6221_settings()
        settings.update({"settle_s": 0.0, "acquire_s": 1e-9, **overrides})
        path = self.folder / name
        self.app._nonlocal_iv6221_worker(settings, path)
        return path, settings

    def rows(self, path):
        with path.open(encoding="utf-8-sig") as file:
            return list(csv.reader(file))

    def appended(self):
        rows = []
        self.app._append_nonlocal_iv6221_point = rows.append
        for callback in self.posted:
            callback()
        self.posted.clear()
        return rows

    def test_the_current_is_stepped_and_each_point_saved_then_the_6221_is_off(self):
        self.make(start="0.1", end="0.3", step="0.1")
        path, _settings = self.run_scan()
        calls = self.source.calls
        self.assertEqual(calls[0][0], "configure")
        self.assertEqual(calls[0][1], 0.1, "configured at the first current")
        options = calls[0][6]
        self.assertEqual(options["output_mode"], "AC")
        self.assertFalse(options["use_nanovoltmeter"])
        self.assertTrue(options["sr830_front_panel_time_constant"],
                        "the SR830 keeps its front-panel time constant, as on RT-E")
        self.assertEqual(calls[0][4]["8"]["harmonic"], 2)
        # As the RT-E Current scan: one fixed range for the largest current,
        # the sine started once, then only its amplitude changed, so the
        # Trigger Link reference never stops.
        self.assertAlmostEqual(options["sweep_peak_a"], 2 ** 0.5 * 0.3e-3)
        self.assertEqual([c[:2] for c in calls if c[0] in ("set_current", "update_amplitude")],
                         [("set_current", 0.1), ("update_amplitude", 0.2), ("update_amplitude", 0.3)])
        self.assertEqual(sum(c[0] == "wait_references" for c in calls), 1, "the first point only")
        self.assertEqual(calls[-2:], [("output_off",), ("confirm_output_off",)])
        self.assertTrue(self.app.nonlocal_keithley_output_off)
        self.assertEqual(self.app.nonlocal_vi_interlock.phase, "IDLE")
        rows = self.rows(path)
        self.assertEqual(rows[0][:6], ["point", "harmonic_pass", "loop", "loop_point",
                                       "set_current_rms_mA", "current_rms_mA"])
        self.assertIn("SR830_8_X_V", rows[0])
        self.assertNotIn("SR830_9_X_V", rows[0], "a meter not ticked Measure has no columns")
        self.assertEqual(len(rows), 4)
        self.assertAlmostEqual(float(rows[3][rows[0].index("SR830_8_X_V")]), 0.3e-6 * 2)
        appended = self.appended()
        self.assertEqual([row["current_rms_ma"] for row in appended], [0.1, 0.2, 0.3])
        self.assertEqual(set(appended[0]["series"]), {("8", 2)})

    def test_loops_and_round_trip_follow_the_rte_plan(self):
        self.make(start="0.1", end="0.2", step="0.1", loops="2", round_trip=True)
        self.run_scan()
        self.assertEqual([c[1] for c in self.source.calls if c[0] in ("set_current", "update_amplitude")],
                         [0.1, 0.2, 0.1, 0.1, 0.2, 0.1])
        self.assertEqual(sum(c[0] == "set_current" for c in self.source.calls), 1)

    def test_an_sr830_with_several_orders_runs_one_pass_per_order(self):
        """As on RT-E: SR830 #8 at 1+2+3 and #9 at 2 take three passes; #9
        drops out after the first."""
        self.make(FakeSource(keys=("8", "9")), start="0.1", end="0.2", step="0.1",
                  harmonics={"8": "1+2+3", "9": "2"}, measure={"9": True})
        path, settings = self.run_scan()
        self.assertEqual([run["keys"] for run in settings["passes"]], [("8", "9"), ("8",), ("8",)])
        calls = self.source.calls
        self.assertEqual({k: v["harmonic"] for k, v in calls[0][4].items()}, {"8": 1, "9": 2})
        self.assertEqual([c[1:] for c in calls if c[0] == "set_lockin_harmonic"],
                         [("8", 2), ("8", 3)])
        self.assertEqual([c[1] for c in calls if c[0] in ("set_current", "update_amplitude")],
                         [0.1, 0.2] * 3)
        self.assertEqual(sum(c[0] == "set_current" for c in calls), 1, "the sine runs through every pass")
        self.assertEqual([c[1] for c in calls if c[0] == "wait_references"],
                         [("8", "9"), ("8",), ("8",)], "the reference is checked at each pass")
        rows = self.rows(path)
        header = rows[0]
        self.assertEqual([row[1] for row in rows[1:]], ["1", "1", "2", "2", "3", "3"])
        self.assertEqual([row[header.index("SR830_8_harmonic")] for row in rows[1:]],
                         ["1", "1", "2", "2", "3", "3"])
        self.assertEqual([row[header.index("SR830_9_X_V")] == "" for row in rows[1:]],
                         [False, False, True, True, True, True])
        appended = self.appended()
        self.assertEqual(set(appended[0]["series"]), {("8", 1), ("9", 2)})
        self.assertEqual(set(appended[-1]["series"]), {("8", 3)})

    def test_the_zurich_reads_1_2_and_3_at_once_as_on_rte(self):
        source = FakeSource(keys=(), mfli=True)
        self.make(source, start="0.1", end="0.2", step="0.1",
                  measure={"8": False, "mfli": True})
        self.app.nonlocal_mfli_tc_var.set("0.3")
        path, settings = self.run_scan()
        self.assertEqual(len(settings["passes"]), 1)
        calls = source.calls
        mfli = calls[0][6]["mfli_config"]
        self.assertEqual((mfli["harmonic"], mfli["external_reference_demodulator"],
                          mfli["timeconstant_s"], mfli["reference_source"], mfli["coupling"]),
                         (1, 3, 0.3, "Aux Input 1", "AC"))
        # The extra orders at Demod 1's transfer rate, not PPMS's 1 Sa/s.
        self.assertEqual([c for c in calls if c[0] in ("configure_second_harmonic",
                                                        "configure_third_harmonic",
                                                        "disable_unused_ppms_demods")],
                         [("configure_second_harmonic", 0.3, 25.0),
                          ("configure_third_harmonic", 0.3, 25.0),
                          ("disable_unused_ppms_demods", "1+2+3")])
        rows = self.rows(path)
        header = rows[0]
        for order in (1, 2, 3):
            self.assertAlmostEqual(float(rows[2][header.index(f"Zurich_{order}w_X_V")]),
                                   0.2e-7 * order)
        self.assertNotIn("SR830_8_X_V", header)
        appended = self.appended()
        self.assertEqual(set(appended[0]["series"]), {("mfli", 1), ("mfli", 2), ("mfli", 3)})
        # Locked at the start: the rack row says so, as on RT-E.
        self.app._show_nonlocal_mfli_lock.assert_called_with(True)

    def test_a_single_zurich_order_is_read_on_demod_1(self):
        source = FakeSource(keys=(), mfli=True)
        self.make(source, start="0.1", end="0.1", step="0.1",
                  harmonics={"mfli": "2"}, measure={"8": False, "mfli": True})
        path, _settings = self.run_scan()
        mfli = source.calls[0][6]["mfli_config"]
        self.assertEqual(mfli["harmonic"], 2)
        # One order needs no MF-MD: Gate-VI's reference routing, as before.
        self.assertIsNone(mfli["external_reference_demodulator"])
        self.assertNotIn(("read_mfli_four_demods",), source.calls)
        self.assertFalse([c for c in source.calls if c[0] in (
            "configure_second_harmonic", "configure_third_harmonic", "disable_unused_ppms_demods")])
        rows = self.rows(path)
        self.assertAlmostEqual(float(rows[1][rows[0].index("Zurich_2w_X_V")]), 0.1e-7 * 2)

    def test_stop_or_a_failed_read_switches_the_6221_off(self):
        self.make(start="0.1", end="0.3", step="0.1")
        self.app.nonlocal_keithley_stop_event.set()
        with self.assertRaises(InterruptedError):
            self.run_scan()
        self.assertEqual(self.source.calls, [("output_off",), ("confirm_output_off",)],
                         "a Stop before the start only makes sure the 6221 is off")
        self.make(FakeSource(fail_read=True), start="0.1", end="0.3", step="0.1")
        with self.assertRaisesRegex(RuntimeError, "VI_ERROR_TMO"):
            self.run_scan("failed.csv")
        self.assertEqual(self.source.calls[-2:], [("output_off",), ("confirm_output_off",)])
        self.assertFalse(self.source.output_on)
        self.assertEqual(self.app.nonlocal_vi_interlock.phase, "IDLE")

    def test_a_connected_keithley_is_checked_off_first(self):
        keithley = self.app.nonlocal_vi_2410_controller = mock.Mock(connected=True)
        self.app.nonlocal_iv6221_end_var.set("0.01")
        self.run_scan()
        contexts = [call.args[0] for call in keithley.confirm_output_off.call_args_list]
        self.assertEqual(contexts[0], "before the 6221-IV scan")

    def test_far_jumps_settle_ten_time_constants_of_the_slowest_lock_in(self):
        # The SR830's front-panel time constant, read back: 3 s.
        self.make(FakeSource(sr830_tau=3.0), start="0.1", end="0.2", step="0.1", loops="2")
        waits = []
        stop = self.app.nonlocal_keithley_stop_event
        original = stop.wait
        stop.wait = lambda timeout=None: (waits.append(timeout), original(0))[1]
        # A zero acquire window takes one reading per point, so the only
        # other waits are the 0 s ones before each reading.
        with mock.patch.object(app, "NONLOCAL_IV6221_FIRST_SETTLE_TAUS", 10.0):
            self.run_scan(settle_s=0.5, acquire_s=0.0)
        settles = [w for w in waits if w in (0.5, 30.0)]
        # Loop 1: 30, 0.5; loop 2 starts again from Start after End: 30, 0.5.
        self.assertEqual(settles, [30.0, 0.5, 30.0, 0.5])


class ReadingTests(WorkerTests):
    """Validity, averaging and settling (the 2026-10-08 review)."""

    def scan_one(self, source, **overrides):
        self.make(source, start="0.1", end="0.1", step="0.1")
        self.run_scan(f"one_{len(list(self.folder.iterdir()))}.csv", **overrides)
        return self.appended()

    def test_theta_and_r_come_from_the_mean_x_and_y(self):
        # A negative signal near ±180°: a plain mean of the phases is 0°.
        source = FakeSource(readings=[
            {"X": -1e-6, "Y": 1e-8, "phase_deg": 179.4},
            {"X": -1e-6, "Y": -1e-8, "phase_deg": -179.4},
        ])
        rows = self.scan_one(source, acquire_s=0.0)
        result = rows[0]["series"][("8", 2)]
        self.assertGreater(abs(result["theta"]), 179.0)
        self.assertAlmostEqual(result["R"], 1e-6, delta=1e-9)

    def test_a_late_lock_is_waited_for(self):
        source = FakeSource()
        replies = iter([False, False, True])
        original = source.wait_references

        def late(frequency_hz, stop_event, keys):
            source.locked = next(replies)
            return original(frequency_hz, stop_event, keys)

        source.wait_references = late
        shown = []
        with mock.patch.object(app, "NONLOCAL_IV6221_REFERENCE_WAIT_S", 60.0):
            self.make(source, start="0.1", end="0.1", step="0.1")
            self.app.nonlocal_iv6221_point_var.set = shown.append
            self.run_scan("late.csv")
            rows = self.appended()
        self.assertEqual(sum(c[0] == "wait_references" for c in source.calls), 3)
        self.assertEqual(len(rows), 1)
        self.assertIn("Transport acquisition: waiting for the reference lock (SR830 #8)", shown)

    def test_an_unlocked_zurich_names_its_ref_input(self):
        source = FakeSource(keys=(), mfli=True, locked=False)
        self.make(source, start="0.1", end="0.1", step="0.1", measure={"8": False, "mfli": True})
        with self.assertRaisesRegex(RuntimeError, "Zurich; check .*the Zurich Ref. is Aux Input 1"):
            self.run_scan()

    def test_an_unlocked_reference_stops_the_scan_with_the_6221_off(self):
        with self.assertRaisesRegex(RuntimeError, "not locked at 17.3 Hz: SR830 #8; check Trigger Link line 4"):
            self.scan_one(FakeSource(locked=False))
        self.assertEqual(self.source.calls[-2:], [("output_off",), ("confirm_output_off",)])

    def test_readings_before_an_autorange_step_and_invalid_ones_are_left_out(self):
        source = FakeSource(readings=[
            {"X": 9.0, "measurement_valid": False},              # unlocked: left out
            {"X": 9.0, "auto_range": "SENS 3->4 (overload/high)", "measurement_valid": False},
        ])
        rows = self.scan_one(source)
        self.assertAlmostEqual(rows[0]["series"][("8", 2)]["X"], 0.2e-6)
        self.assertEqual(rows[0]["discarded"], 2)

    def test_sustained_compliance_stops_the_scan(self):
        source = FakeSource(readings=[{"in_compliance": True, "measurement_valid": False}] * 3)
        with mock.patch.object(app, "NONLOCAL_IV6221_SAMPLE_INTERVAL_S", 0.001):
            with self.assertRaisesRegex(RuntimeError, "6221 is in compliance"):
                self.scan_one(source, acquire_s=5.0)
        self.assertFalse(self.source.output_on)

    def test_no_valid_reading_fails_the_point(self):
        source = FakeSource(readings=[{"measurement_valid": False, "status_text": "reference not locked"}] * 50)
        with mock.patch.object(app, "NONLOCAL_IV6221_MAX_EXTRA_S", 0.0), \
                mock.patch.object(app, "NONLOCAL_IV6221_SAMPLE_INTERVAL_S", 0.001):
            with self.assertRaisesRegex(RuntimeError, "No valid lock-in reading.*SR830 #8: reference not locked"):
                self.scan_one(source)


class AcquisitionTests(ReadingTests):
    """Ranging, first-point windows and loops, as on RT-E (review, 2026-10-08)."""

    def test_after_an_autorange_step_the_meter_settles_as_on_rte(self):
        source = FakeSource(readings=[{"auto_range": "SENS 9->10 (overload/high)",
                                        "measurement_valid": False}], sr830_tau=0.3)
        self.make(source, start="0.1", end="0.2", step="0.1")
        waits = []
        stop = self.app.nonlocal_keithley_stop_event
        original = stop.wait
        stop.wait = lambda timeout=None: (waits.append(timeout), original(0))[1]
        with mock.patch.object(app, "NONLOCAL_IV6221_SAMPLE_INTERVAL_S", 0.001):
            self.run_scan(acquire_s=0.0)
        self.assertIn(1.5, [round(w, 6) for w in waits], "5 × 0.3 s after the SR830 range step")

    def test_the_first_point_of_a_pass_repeats_until_two_windows_agree(self):
        source = FakeSource(readings=[{"X": 9e-7}])   # settling, then steady
        self.make(source, start="0.1", end="0.2", step="0.1")
        path, _settings = self.run_scan(acquire_s=0.0)
        rows = self.rows(path)
        windows = [row[rows[0].index("settle_windows")] for row in rows[1:]]
        self.assertEqual(windows, ["3", "1"], "first point: 3 windows; later points: 1")
        self.assertIn("compliance_readings", rows[0])

    def test_each_loop_is_its_own_line(self):
        a = bare_app()
        canvas = mock.Mock()
        a.nonlocal_iv6221_canvases = {(1, "X"): canvas}
        a.nonlocal_iv6221_rows = [
            {"pass": 1, "loop": loop, "current_rms_ma": current,
             "series": {("mfli", 1): {"X": current, "Y": 0.0, "theta": 0.0}}}
            for loop in (1, 2) for current in (0.1, 0.2)
        ]
        a._draw_nonlocal_series = mock.Mock()
        a._redraw_nonlocal_iv6221_plots()
        series = a._draw_nonlocal_series.call_args[0][1]
        self.assertEqual(series, {"mfli_loop_1": [(0.1, 0.1), (0.2, 0.2)],
                                  "mfli_loop_2": [(0.1, 0.1), (0.2, 0.2)]})
        self.assertFalse(a._draw_nonlocal_series.call_args[1]["show_legend"], "one meter: no legend")

class ParseTests(unittest.TestCase):
    def test_the_rte_defaults(self):
        """RT-E's start-up values: the Zurich alone, 1+2+3, one pass, 100 points."""
        source = FakeSource(keys=(), mfli=True)
        a = page(source, harmonics=app.NONLOCAL_IV6221_DEFAULT_HARMONICS,
                 measure=app.NONLOCAL_IV6221_DEFAULT_MEASURE)
        parsed = a._parse_nonlocal_iv6221_settings()
        self.assertEqual(parsed["measured"], ("mfli",))
        self.assertEqual(parsed["passes"], [{"pass": 1, "keys": ("mfli",), "harmonics": {}}])
        self.assertEqual(parsed["lockin_configs"], {})
        self.assertEqual(parsed["zurich_orders"], (1, 2, 3))
        self.assertEqual((parsed["current_ma"], parsed["frequency_hz"], parsed["compliance_v"],
                          parsed["settle_s"], parsed["acquire_s"]), (0.1, 17.3, 50.0, 0.5, 1.0))
        self.assertEqual(len(parsed["plan"]), 100)
        self.assertAlmostEqual(parsed["plan"][0][2], 0.01)
        self.assertAlmostEqual(parsed["plan"][-1][2], 1.0)

    def test_settings_that_are_refused(self):
        for values, message in (
            ({"start": "0"}, "above 0 mA RMS"),
            ({"end": "80"}, "105 mA sine peak"),
            ({"measure": {"9": True}}, "SR830 #9 is ticked Measure but is not connected"),
            ({"measure": {"mfli": True}}, "The Zurich is ticked Measure but is not connected"),
            ({"measure": {"8": False}}, "Tick Measure for at least one lock-in"),
            ({"acquire": "0"}, "Acquire time"),
            ({"loops": "1.5"}, "Loops"),
            ({"frequency": "x"}, "valid numbers"),
            ({"harmonics": {"8": "4"}}, "Harmonic must be one of 1, 2, 3, 1\\+2, 1\\+2\\+3"),
            ({"frequency": "40000", "harmonics": {"8": "1+2+3"}}, "SR830 #8 demodulation frequency exceeds 102 kHz"),
        ):
            with self.subTest(message=message):
                a = page(FakeSource(), **values)
                with self.assertRaisesRegex(ValueError, message):
                    a._parse_nonlocal_iv6221_settings()
        a = page(FakeSource(mfli=True), measure={"mfli": True})
        for value in ("0", ""):
            a.nonlocal_mfli_tc_var.set(value)
            with self.assertRaisesRegex(ValueError, "The Zurich TC \\(s\\) on the rack"):
                a._parse_nonlocal_iv6221_settings()
        # Not measured, the Zurich's TC does not matter.
        a = page(FakeSource())
        a.nonlocal_mfli_tc_var.set("")
        self.assertIsNone(a._parse_nonlocal_iv6221_settings()["mfli_config"])


class RunFolderTests(unittest.TestCase):
    def test_each_run_gets_its_own_folder(self):
        parent = Path(tempfile.mkdtemp())
        make = app.CombinedControlApp._nonlocal_make_run_directory
        first = make(parent, "2400-IV", "1µm Pt/Pt")
        self.assertTrue(first.is_dir())
        self.assertEqual(first.parent, parent)
        self.assertTrue(first.name.startswith("2400-IV_1µm_Pt_Pt_"), first.name)
        with mock.patch.object(app.datetime_module, "datetime") as clock:
            clock.now.return_value.strftime.return_value = "20261008_120000"
            self.assertEqual(make(parent, "6221-IV").name, "6221-IV_20261008_120000")
            self.assertEqual(make(parent, "6221-IV").name, "6221-IV_20261008_120000_01")


class ControllerTests(unittest.TestCase):
    """The controller options 6221-IV uses; Gate-VI's do not change."""

    class FakeSR830:
        def __init__(self, oflt=11):
            self.commands = []
            self.harmonic = 1
            self.oflt = oflt

        def write(self, command):
            self.commands.append(command)
            if command.startswith("HARM "):
                self.harmonic = int(command.split()[1])

        def query(self, command):
            return {"*ESR?": "0", "HARM?": str(self.harmonic), "OFLT?": str(self.oflt),
                    "SENS?": "9"}[command]

    def controller(self, lockin):
        controller = app.NonLocalACController(lambda _m: None)
        controller.lockins = {"8": lockin}
        return controller

    def test_mfli_autorange_switch(self):
        controller = app.NonLocalACController(lambda _m: None)
        self.assertTrue(controller.mfli_auto_range_enabled)
        controller.mfli_auto_range_enabled = False
        self.assertEqual(controller._auto_range_mfli({"input_utilization": 0.99}), "")

    def test_the_next_pass_harmonic_is_set_and_read_back(self):
        lockin = self.FakeSR830()
        controller = self.controller(lockin)
        controller.low_range_counts["8"] = controller.range_cooldowns["8"] = 3
        controller.sensitivity_indices["8"] = 22
        with mock.patch.object(app.time, "sleep"):
            controller.set_lockin_harmonic("8", 3)
        # The next order starts at the range configure() starts at.
        self.assertEqual(lockin.commands, ["HARM 3", "SENS 9"])
        self.assertEqual(controller.sensitivity_indices["8"], 9)
        self.assertEqual((controller.low_range_counts["8"], controller.range_cooldowns["8"]), (0, 0))
        lockin.query = lambda command: {"*ESR?": "0", "HARM?": "1"}[command]
        with mock.patch.object(app.time, "sleep"), \
                self.assertRaisesRegex(RuntimeError, "harmonic readback mismatch: requested 2, readback 1"):
            controller.set_lockin_harmonic("8", 2)
        with self.assertRaisesRegex(RuntimeError, "SR830 #9 is not connected"):
            controller.set_lockin_harmonic("9", 2)

    def test_sr830_time_constants_follow_the_oflt_codes(self):
        self.assertEqual(len(app.SR830_TIME_CONSTANTS_S), 20)
        self.assertEqual((app.SR830_TIME_CONSTANTS_S[0], app.SR830_TIME_CONSTANTS_S[9],
                          app.SR830_TIME_CONSTANTS_S[10], app.SR830_TIME_CONSTANTS_S[19]),
                         (10e-6, 0.3, 1.0, 30e3))


class SetCurrentTests(unittest.TestCase):
    def test_set_6221_switches_the_sine_on_with_the_lock_ins_set_up(self):
        source = FakeSource()
        a = page(source)
        a.nonlocal_vi_2410_controller = mock.Mock(connected=True)
        a.nonlocal_keithley_stop_event = threading.Event()
        settings = a._parse_nonlocal_iv6221_lockins()
        actual, frequency = a._nonlocal_iv6221_set_worker(settings)
        self.assertEqual((actual, frequency), (0.1, 17.3))
        self.assertEqual(source.calls[0][1], 0.1)
        self.assertTrue(source.calls[0][6]["sr830_front_panel_time_constant"])
        self.assertEqual(source.calls[-1], ("set_current", 0.1))
        self.assertIsNone(source.calls[0][6]["sweep_peak_a"], "Set 6221 keeps one current")
        a.nonlocal_vi_2410_controller.confirm_output_off.assert_called_once()
        self.assertTrue(source.output_on, "left on until 6221 / 2400 OFF or a scan")

    def test_a_stop_while_set_6221_configures_leaves_the_6221_off(self):
        source = FakeSource()
        a = page(source)
        a.nonlocal_vi_2410_controller = mock.Mock(connected=False)
        a.nonlocal_keithley_stop_event = threading.Event()
        a.nonlocal_keithley_output_off = False
        original = source.configure

        def configure_then_stop(*args, **kwargs):
            a.nonlocal_keithley_stop_event.set()   # "6221 / 2400 OFF" pressed meanwhile
            return original(*args, **kwargs)

        source.configure = configure_then_stop
        with self.assertRaises(InterruptedError):
            a._nonlocal_iv6221_set_worker(a._parse_nonlocal_iv6221_lockins())
        self.assertNotIn("set_current", [c[0] for c in source.calls])
        self.assertEqual(source.calls[-2:], [("output_off",), ("confirm_output_off",)])
        self.assertTrue(a.nonlocal_keithley_output_off)
        # Pressed just as it switched on: switched off again.
        source = FakeSource()
        a.nonlocal_vi_ac_controller = source
        a.nonlocal_keithley_stop_event.clear()
        a.nonlocal_keithley_output_off = False
        original_set = source.set_current

        def set_then_stop(*args):
            a.nonlocal_keithley_stop_event.set()
            return original_set(*args)

        source.set_current = set_then_stop
        with self.assertRaises(InterruptedError):
            a._nonlocal_iv6221_set_worker(a._parse_nonlocal_iv6221_lockins())
        self.assertFalse(source.output_on)
        self.assertEqual(source.calls[-2:], [("output_off",), ("confirm_output_off",)])

    def test_finish_of_a_stopped_set_says_the_6221_is_off(self):
        a = bare_app()
        a.closing = True
        a.nonlocal_keithley_task = "iv6221set"
        a.nonlocal_keithley_output_off = True
        a.nonlocal_vi_2410_controller = app.NonLocalVI2410Controller(lambda _m: None, "2400")
        a.nonlocal_iv6221_source_var, a.nonlocal_vi_output_var, a.nonlocal_vi_status_var = Var(), Var(), Var()
        a._update_nonlocal_vi_controls = mock.Mock()
        future = Future()
        future.set_exception(InterruptedError("stopped"))
        a._finish_nonlocal_iv6221_set(future)
        self.assertEqual(a.nonlocal_iv6221_source_var.get(), "6221: OFF")
        self.assertEqual(a.nonlocal_vi_status_var.get(), "Set 6221 stopped; 6221 output off (read back)")
        self.assertIsNone(a.nonlocal_keithley_task)

    def test_a_failed_set_switches_the_6221_off(self):
        source = FakeSource()
        source.set_current = mock.Mock(side_effect=RuntimeError("compliance"))
        a = page(source)
        a.nonlocal_vi_2410_controller = mock.Mock(connected=False)
        a.nonlocal_keithley_stop_event = threading.Event()
        a.nonlocal_keithley_output_off = False
        with self.assertRaisesRegex(RuntimeError, "compliance"):
            a._nonlocal_iv6221_set_worker(a._parse_nonlocal_iv6221_lockins())
        self.assertEqual(source.calls[-2:], [("output_off",), ("confirm_output_off",)])
        self.assertTrue(a.nonlocal_keithley_output_off)


class ZurichRowTests(unittest.TestCase):
    """The rack's Zurich as on RT-E (user, 2026-10-08): Ref. follows the
    Zurich's wiring, and the row reads the lock once a second when idle."""

    class Root:
        def __init__(self):
            self.scheduled = []

        def after(self, delay, callback):
            self.scheduled.append((delay, callback))
            return f"after#{len(self.scheduled)}"

    class Executor:
        def submit(self, function, *args):
            future = Future()
            try:
                future.set_result(function(*args))
            except BaseException as error:
                future.set_exception(error)
            return future

    def page(self, sample=None, error=None):
        a = bare_app()
        a.root, a.closing, a.nonlocal_vi_closed = self.Root(), False, False
        a.nonlocal_vi_running = a.nonlocal_vi_busy = False
        a.nonlocal_keithley_task = None
        a.nonlocal_mfli_poll_inflight = a.nonlocal_mfli_applying = False
        a.nonlocal_vi_executor = self.Executor()
        a.post_ui = lambda callback: callback()
        controller = mock.Mock(mfli_connected=True, mfli_config={"harmonic": 1},
                               configured_frequency_hz=17.3)
        controller.lock = threading.RLock()
        if error is not None:
            controller.read_mfli.side_effect = error
        else:
            controller.read_mfli.return_value = sample
        a.nonlocal_vi_ac_controller = controller
        a.nonlocal_vi_device_connection_vars = {"mfli": Var("Connected")}
        a.nonlocal_iv6221_zurich_range_var = Var("Autorange ON")
        a._set_mfli_lock_state = mock.Mock()
        return a

    def test_the_ref_follows_the_chosen_zurich(self):
        a = bare_app()
        a.nonlocal_vi_mfli_reference_source_var = Var("Aux Input 1")
        a.nonlocal_vi_mfli_device_var = Var("DEV4414 (Super)")
        a._on_nonlocal_mfli_device_changed()
        self.assertEqual(a.nonlocal_vi_mfli_reference_source_var.get(), "Trigger In 1",
                         "as on PPMS and RT-E")
        a.nonlocal_vi_mfli_device_var.set("DEV4435 (Normal)")
        a._on_nonlocal_mfli_device_changed()
        self.assertEqual(a.nonlocal_vi_mfli_reference_source_var.get(), "Aux Input 1",
                         "as on STFMR and Gate-VI")

    def test_an_idle_page_shows_locked_or_warning(self):
        a = self.page({"reference_valid": True, "input_range_v": 0.01})
        a._poll_nonlocal_mfli()
        a.nonlocal_vi_ac_controller.read_mfli.assert_called_once_with(17.3)
        self.assertEqual(a.nonlocal_vi_device_connection_vars["mfli"].get(), "Locked")
        self.assertEqual(a.nonlocal_iv6221_zurich_range_var.get(), "Autorange 10 mV")
        a._set_mfli_lock_state.assert_called_with(True, vi=True)
        self.assertEqual(a.root.scheduled[-1][0], app.NONLOCAL_MFLI_POLL_MS, "polls again")
        a = self.page({"reference_valid": False, "input_range_v": 0.01})
        a._poll_nonlocal_mfli()
        self.assertEqual(a.nonlocal_vi_device_connection_vars["mfli"].get(), "Warning")
        a = self.page(error=RuntimeError("timeout"))
        a._poll_nonlocal_mfli()
        self.assertEqual(a.nonlocal_vi_device_connection_vars["mfli"].get(), "Read failed")

    def test_no_read_while_a_scan_runs_or_before_the_zurich_is_set_up(self):
        for setup in (lambda a: setattr(a, "nonlocal_keithley_task", "iv6221"),
                      lambda a: setattr(a, "nonlocal_vi_running", True),
                      lambda a: setattr(a.nonlocal_vi_ac_controller, "mfli_config", {})):
            a = self.page({"reference_valid": True})
            setup(a)
            a._poll_nonlocal_mfli()
            a.nonlocal_vi_ac_controller.read_mfli.assert_not_called()
            self.assertEqual(len(a.root.scheduled), 1, "still scheduled")
        a = self.page({"reference_valid": True})
        a.closing = True
        a._poll_nonlocal_mfli()
        self.assertEqual(a.root.scheduled, [], "stops with the program")

    def test_the_zurich_is_set_up_as_the_rte_row_does(self):
        a = self.page()
        controller = a.nonlocal_vi_ac_controller
        controller.mfli_props = {"options": "MD"}
        controller.configure_mfli.return_value = {"sample_rate_hz": 25.0}
        a._apply_nonlocal_mfli_config({"reference": "Trigger In 1", "tc_s": 0.1, "mode": "1+2+3"})
        args, kwargs = controller.configure_mfli.call_args
        self.assertEqual(args, ("AC", 1, "R"))
        self.assertEqual((kwargs["reference_source"], kwargs["timeconstant_s"],
                          kwargs["external_reference_demodulator"]), ("Trigger In 1", 0.1, 3))
        controller.configure_second_harmonic.assert_called_once_with(0.1, rate=25.0)
        controller.configure_third_harmonic.assert_called_once_with(0.1, rate=25.0)
        controller.disable_unused_ppms_demods.assert_called_once_with("1+2+3")
        a = self.page()
        controller = a.nonlocal_vi_ac_controller
        controller.mfli_props = {"options": "MD"}
        controller.configure_mfli.return_value = {}
        a._apply_nonlocal_mfli_config({"reference": "Aux Input 1", "tc_s": 1.0, "mode": "2"})
        self.assertEqual(controller.configure_mfli.call_args[0], ("AC", 2, "R"))
        self.assertIsNone(controller.configure_mfli.call_args[1]["external_reference_demodulator"])
        controller.configure_second_harmonic.assert_not_called()
        # Without MF-MD the rack's lock display uses the 1st harmonic alone.
        a = self.page()
        controller = a.nonlocal_vi_ac_controller
        controller.mfli_props = {"options": "", "has_demod_2": False}
        controller.configure_mfli.return_value = {}
        a._apply_nonlocal_mfli_config({"reference": "Aux Input 1", "tc_s": 0.1, "mode": "1+2+3"})
        self.assertEqual(controller.configure_mfli.call_args[0], ("AC", 1, "R"))
        self.assertIsNone(controller.configure_mfli.call_args[1]["external_reference_demodulator"])
        controller.configure_second_harmonic.assert_not_called()


class FinishAndPlotTests(unittest.TestCase):
    def test_finish_messages_name_the_run_folder_and_the_6221(self):
        a = bare_app()
        a.closing = True
        a.nonlocal_keithley_task, a.nonlocal_last_task = "iv6221", "iv6221"
        a.nonlocal_vi_2410_controller = app.NonLocalVI2410Controller(lambda _m: None, "2400")
        a.nonlocal_vi_output_var, a.nonlocal_vi_status_var = Var(), Var()
        a.nonlocal_iv6221_status_var, a.nonlocal_iv6221_point_var = Var(), Var()
        a.nonlocal_iv6221_source_var = Var()
        a.nonlocal_iv6221_rows, a.nonlocal_iv6221_total_points = [{}] * 3, 100
        run = Path(tempfile.mkdtemp()) / "6221-IV_20261008_120000"
        run.mkdir()
        csv_path = run / "6221-IV_20261008_120000.csv"
        csv_path.write_text("point\n", encoding="utf-8")
        a.nonlocal_iv6221_csv_path = csv_path
        a._redraw_nonlocal_iv6221_plots = mock.Mock()
        a._update_nonlocal_vi_controls = mock.Mock()
        a._set_mfli_lock_state = mock.Mock()
        for error, off, expected in (
            (InterruptedError("stopped"), True,
             "6221-IV scan stopped after 3/100 points; 6221 output off. "
             "Run folder: 6221-IV_20261008_120000"),
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
        self.assertEqual(a.nonlocal_iv6221_source_var.get(), "6221: check the output")
        a._set_mfli_lock_state.assert_called_with(None, vi=True)
        # Stopped before the first point: no CSV was made.
        csv_path.unlink()
        a.nonlocal_keithley_task, a.nonlocal_keithley_output_off = "iv6221", True
        future = Future()
        future.set_exception(InterruptedError("stopped"))
        a._finish_nonlocal_iv6221(future)
        self.assertTrue(a.nonlocal_vi_status_var.get().endswith("6221 output off. No CSV was written."))
        self.assertEqual(a.nonlocal_iv6221_source_var.get(), "6221: OFF")

    def test_each_harmonic_order_has_its_x_y_theta_row_with_the_meters_overlaid(self):
        a = bare_app()
        a.nonlocal_iv6221_canvases = {
            (order, channel): Canvas(900, 400)
            for order in (1, 2, 3) for channel in ("X", "Y", "theta")
        }
        a.nonlocal_iv6221_rows = [
            {"pass": 1, "current_rms_ma": i / 10, "series": {
                ("8", 2): {"X": i * 1e-7, "Y": 0.0, "theta": 3.0},
                ("mfli", 2): {"X": i * 2e-7, "Y": 0.0, "theta": 4.0},
                ("mfli", 1): {"X": i * 1e-6, "Y": 0.0, "theta": 1.0},
            }}
            for i in range(1, 6)
        ] + [
            # The Zurich in a later pass is not drawn again.
            {"pass": 2, "current_rms_ma": 0.1, "series": {("mfli", 1): {"X": 9.0, "Y": 0.0, "theta": 0.0}}},
        ]
        a._redraw_nonlocal_iv6221_plots()
        second = a.nonlocal_iv6221_canvases[(2, "X")].texts()
        self.assertIn("6221 current (mA RMS)", second)
        self.assertIn("SR830 #8", second)
        self.assertIn("Zurich", second)
        self.assertTrue(any(text.startswith("X (µV)") for text in second if text), second)
        first = a.nonlocal_iv6221_canvases[(1, "X")].texts()
        self.assertNotIn("SR830 #8", first)
        self.assertNotIn("Zurich", first, "one meter: named in the frame title, no legend")
        self.assertTrue(any(text.startswith("X (µV)") for text in first if text),
                        "the second pass's 9 V Zurich point is not drawn")
        self.assertIn("θ (°)", a.nonlocal_iv6221_canvases[(2, "theta")].texts())


if __name__ == "__main__":
    unittest.main()
