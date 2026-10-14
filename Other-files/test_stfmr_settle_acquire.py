"""STFMR page: Settle s and Acquire s replace Wait/pt s (user, 2026-10-14).

The user asked: "add a acquire time in the ST-FMR scan mode, default is 2s.
also add a settle time and default 1s. delete the delay time."  With the 1 s
lock-in time constants of the same day the user chose one rule for every
page: each lock-in reading waits at least LOCKIN_SETTLE_TAUS (7) time
constants after a change (99 % settled at 12 dB/oct), or Settle s when that
is longer.  So after each current step a point settles
max(Settle s, 7 x the TC of every lock-in it reads), then reads the
lock-in(s) every 0.1 s for Acquire s and saves the mean X and Y (R and theta
from the mean).  A range change in the window restarts the point after the
range re-read wait; a failed reading ends the sweep as a failed read did;
discarded readings (the first two of each loop, carrier and modulation
frequency) stay one reading each.

No Tk window and no instrument: the real sweep runs on the shared STFMR
harness (stand-in devices, a Root that runs callbacks at once but keeps the
time, so the Acquire s window runs on that clock), and the real SR830
controller talks to a fake SR830.  Every message box and file dialog is
mocked, and creating a Tk root fails the test."""
import math
import re
import sys
import unittest
from pathlib import Path
from unittest import mock

_HERE = Path(__file__).resolve().parent
for _path in (_HERE / "test", _HERE.parent):
    if str(_path) not in sys.path:
        sys.path.insert(0, str(_path))
from test_stfmr_realtime_recording import Device, Variable, app, events, harness, read_csv
from test_stfmr_sr830_tc_fixed_current import History, NoWindows, TimedRoot

App = app.CombinedControlApp
settle_rule = App._stfmr_lockin_effective_settle_s
SR830_1S = app.STFMR_LOCKIN_SR830_TIME_CONSTANTS["1 s"]


class PatternSR830(Device):
    """The harness's stand-in SR830, but each reading of a point takes its X
    and Y from a pattern, by its place in the point's window: X 1, 2, 6 uV
    and Y 4, -2, 1 uV, repeating, so 21 readings (7 rounds) average to
    X = 3 uV, Y = 1 uV.  It keeps the options of every reading."""

    X = (1e-6, 2e-6, 6e-6)
    Y = (4e-6, -2e-6, 1e-6)

    def __init__(self, harness):
        super().__init__(harness)
        self.options = []
        self.before = None          # (point index, reading, sample) -> sample

    def place(self):
        acquisition = self.harness.stfmr_lockin_acquisition
        return 0 if acquisition is None else len(acquisition["samples"])

    def read_sample(self, expected, **options):
        self.options.append(options)
        sample = super().read_sample(expected, **options)
        k = self.place()
        x, y = self.X[k % 3], self.Y[k % 3]
        sample.update(x_v=x, y_v=y, r_v=math.hypot(x, y), phase_deg=math.degrees(math.atan2(y, x)))
        if self.before is not None:
            sample = self.before(self.harness.stfmr_lockin_index, k, sample)
        return sample


class PatternMFLI(PatternSR830):
    """The same pattern, spelt as the real MFLI reading is: X, Y, R and the
    read channel's voltage (R), upper case.  Its raw input extrema are
    unknown (so no range rule acts on them) and at half the range, unless
    the test's hook gives a reading its own."""

    def read_mfli(self, expected):
        raw = self.read_sample(expected)
        return {
            "X": raw["x_v"], "Y": raw["y_v"], "R": raw["r_v"], "voltage": raw["r_v"],
            "phase_deg": raw["phase_deg"], "reference_frequency_hz": raw["reference_frequency_hz"],
            "reference_valid": raw["reference_valid"], "input_range_v": self.mfli_config["input_range_v"],
            "input_overload": raw.get("input_overload", False),
            "input_utilization": raw.get("input_utilization", 0.5),
            "input_extrema_valid": raw.get("input_extrema_valid", False),
            "device_status_flags": raw.get("device_status_flags", 0),
            "auto_range": "",
        }


class RangingMFLI(Device):
    """A stand-in MFLI with the real controller's range rules (read_stfmr_mfli,
    _auto_range_mfli, and _run_mfli_autorange's count and cool-down reset),
    always reading a weak signal: 1 % of its input range."""

    _auto_range_mfli = app.NonLocalACController._auto_range_mfli

    def __init__(self, harness):
        super().__init__(harness)
        self.mfli_low_range_count = 0
        self.mfli_range_cooldown = 0
        self.stfmr_reads = []

    def read_stfmr_mfli(self, expected, **kwargs):
        self.stfmr_reads.append(kwargs.get("auto_range"))
        return super().read_stfmr_mfli(expected, **kwargs)

    def read_mfli(self, expected):
        sample = super().read_mfli(expected)
        sample.update(input_extrema_valid=True, input_utilization=0.01)
        return sample

    def _run_mfli_autorange(self, reason):
        self.mfli_low_range_count = 0
        self.mfli_range_cooldown = 3
        return super()._run_mfli_autorange(reason)


class Sweeps(NoWindows):
    def sweep(self, meter="SR830", sr830=None, mfli=None, settle="1", acquire="2", tc="1 s",
              mfli_tc="1", loop=None, start_only=False, **kwargs):
        """A sweep at the page's Settle s 1 and Acquire s 2 unless given, on
        the stand-ins (or the given SR830 / MFLI stand-in classes)."""
        h = harness(self.tmp.name, meter=meter, **kwargs)
        h.root = TimedRoot()
        h.stfmr_lockin_status_var = History()
        h.stfmr_lockin_settle_var.set(settle)
        h.stfmr_lockin_acquire_var.set(acquire)
        h.stfmr_lockin_sr830_time_constant_var = Variable(tc)
        h.stfmr_lockin_mfli_timeconstant_var.set(mfli_tc)
        if loop is not None:
            h.stfmr_lockin_loop_var.set(loop)
        if sr830 is not None:
            h.stfmr_lockin_controllers["830"] = sr830(h)
        if mfli is not None:
            h.stfmr_lockin_mfli_controller = mfli(h)
        h.start_stfmr_lockin_scan()
        recorder = h.stfmr_lockin_recorder
        if not start_only:
            h.root.drain()
        return h, recorder

    def message(self):
        self.confirm.assert_called_once()
        return self.confirm.call_args.args[1]


class SettleRuleTests(NoWindows):
    def test_the_settle_is_the_longest_of_settle_s_and_seven_tcs_of_the_lock_ins_read(self):
        codes = app.STFMR_LOCKIN_SR830_TIME_CONSTANTS
        for meter, settle, sr830, mfli_tc, expected in (
                ("SR830", 1.0, "1 s", 1.0, 7.0),
                ("SR830", 1.0, "100 ms", 1.0, 1.0),          # 0.7 s < Settle s
                ("SR830", 0.0, "10 ms", 1.0, 0.07),
                ("SR830", 1.0, "1 s", 100.0, 7.0),            # the MFLI TC is not read
                ("MFLI", 1.0, "10 s", 1.0, 7.0),              # nor the SR830 TC
                ("MFLI", 1.0, "1 s", 3.0, 21.0),
                ("MFLI", 1.0, "1 s", 0.1, 1.0),
                ("MFLI + SR830", 1.0, "10 s", 1.0, 70.0),     # the SR830's 7 x 10 s
                ("MFLI + SR830", 1.0, "100 ms", 3.0, 21.0),   # the MFLI's 7 x 3 s
                ("MFLI + SR830", 30.0, "1 s", 1.0, 30.0)):    # Settle s
            with self.subTest(meter=meter, settle=settle, sr830=sr830, mfli_tc=mfli_tc):
                self.assertAlmostEqual(settle_rule(meter, settle, codes[sr830], mfli_tc), expected)

    def test_it_is_the_shared_seven_tc_rule(self):
        self.assertEqual(app.LOCKIN_SETTLE_TAUS, 7.0)
        self.assertFalse(hasattr(app, "STFMR_LOCKIN_SR830_SETTLE_TAUS"), "the 5 x TC constant is gone")
        with mock.patch.object(app, "LOCKIN_SETTLE_TAUS", 9.0):
            self.assertEqual(settle_rule("MFLI + SR830", 0.0, SR830_1S, 2.0), 18.0)
        with open(app.__file__, encoding="utf-8") as source:
            text = source.read()
        self.assertEqual(len(re.findall(r"^LOCKIN_SETTLE_TAUS = ", text, re.M)), 1, "defined once")

    def test_the_timing_line_of_the_start_confirmation(self):
        text = App._stfmr_lockin_timing_text
        self.assertEqual(text("SR830", 1.0, 2.0, SR830_1S, 1.0),
                         "SR830 TC: 1 s (12 dB/oct); each point settles 7 s (7 × TC 1 s; Settle s 1 s) "
                         "then averages 2 s")
        self.assertEqual(text("MFLI", 1.0, 2.0, SR830_1S, 1.0),
                         "MFLI TC: 1 s; each point settles 7 s (7 × TC 1 s; Settle s 1 s) then averages 2 s")
        self.assertEqual(text("MFLI + SR830", 1.0, 0.5, app.STFMR_LOCKIN_SR830_TIME_CONSTANTS["100 ms"], 3.0),
                         "SR830 TC: 100 ms (12 dB/oct), MFLI TC: 3 s; each point settles 21 s "
                         "(7 × SR830 TC 100 ms, 7 × MFLI TC 3 s; Settle s 1 s) then averages 0.5 s")
        self.assertEqual(text("SR830", 10.0, 2.0, SR830_1S, 1.0),
                         "SR830 TC: 1 s (12 dB/oct); each point settles 10 s (7 × TC 1 s; Settle s 10 s) "
                         "then averages 2 s")


class DefaultTests(Sweeps):
    def test_the_page_starts_at_settle_1_s_and_acquire_2_s(self):
        self.assertEqual((app.STFMR_LOCKIN_DEFAULT_SETTLE_S, app.STFMR_LOCKIN_DEFAULT_ACQUIRE_S), (1.0, 2.0))
        self.assertEqual(app.STFMR_LOCKIN_SAMPLE_INTERVAL_S, 0.1)
        self.assertEqual((app.STFMR_LOCKIN_MAX_SETTLE_S, app.STFMR_LOCKIN_MAX_ACQUIRE_S), (3600.0, 3600.0))
        with open(app.__file__, encoding="utf-8") as source:
            text = source.read()
        for name, constant in (("stfmr_lockin_settle_var", "STFMR_LOCKIN_DEFAULT_SETTLE_S"),
                               ("stfmr_lockin_acquire_var", "STFMR_LOCKIN_DEFAULT_ACQUIRE_S")):
            self.assertRegex(text, rf"self\.{name} = tk\.StringVar\(\s*value=f\"{{{constant}:g}}\"")
        for gone in ("stfmr_lockin_delay", "STFMR_LOCKIN_DEFAULT_DELAY_S", "STFMR_LOCKIN_SR830_SETTLE_TAUS",
                     "_stfmr_sr830_point_wait_s", "stfmr_lockin_point_wait_s", '"Wait/pt s:"'):
            self.assertNotIn(gone, text)

    def test_a_default_sweep_settles_7_s_then_averages_2_s(self):
        """MFLI (the default meter), both TCs 1 s, Settle s 1, Acquire s 2."""
        h, recorder = self.sweep(meter="MFLI", stop="5",
                                 settle=f"{app.STFMR_LOCKIN_DEFAULT_SETTLE_S:g}",
                                 acquire=f"{app.STFMR_LOCKIN_DEFAULT_ACQUIRE_S:g}")
        text = self.message()
        self.assertIn("MFLI TC: 1 s; each point settles 7 s (7 × TC 1 s; Settle s 1 s) then averages 2 s\n", text)
        self.assertIn("Estimated at least 34.0 s (4 settles of 7 s + 3 averages of 2 s).", text)
        # The first point follows the pre-read at once; the stand-in MFLI's
        # first-point range check re-reads it after 10 x TC; every point
        # after a step settles 7 s.
        self.assertEqual(h.root.reads, [1, 10000, 7000, 7000, 7000, 7000])
        self.assertEqual(h.root.samples, [100] * 60, "20 readings 0.1 s apart after each saved point's first")
        rows = read_csv(recorder.csv_path)
        self.assertEqual([(r["settle_s"], r["effective_settle_s"], r["acquire_s"], r["sample_count"])
                          for r in rows], [("1.0", "7.0", "2.0", "21")] * 3)
        settings = events(recorder)[0]["settings"]
        self.assertEqual({key: settings[key] for key in (
            "settle_s", "acquire_s", "settle_time_constants", "effective_settle_s",
            "acquisition_sample_interval_s", "mfli_sample_rate_hz")},
            {"settle_s": 1.0, "acquire_s": 2.0, "settle_time_constants": 7.0, "effective_settle_s": 7.0,
             "acquisition_sample_interval_s": 0.1, "mfli_sample_rate_hz": 10.0})
        self.assertNotIn("point_delay_s", settings)
        # The MFLI's demodulator rate follows the 0.1 s read pace (it was
        # 1 / Wait/pt).
        (configure,) = [c for c in h.stfmr_lockin_mfli_controller.calls if c[0] == "configure_mfli"]
        self.assertEqual(configure[2]["sample_rate_hz"], 10.0)
        self.assertEqual(configure[2]["timeconstant_s"], 1.0)
        self.error.assert_not_called()

    def test_the_page_default_sweep_estimate(self):
        """The page's own sweep: 4 GHz, -3 to 3 A in 0.01 A (601 currents),
        one loop, the MFLI; declined at the confirmation."""
        self.confirm.return_value = False
        h = harness(self.tmp.name, meter="MFLI", stop="4")
        for name, value in (("frequency_start", "4"), ("start", "-3"), ("stop", "3"), ("step", "0.01"),
                            ("settle", "1"), ("acquire", "2")):
            getattr(h, f"stfmr_lockin_{name}_var").set(value)
        h.start_stfmr_lockin_scan()
        text = self.message()
        self.assertIn("601 points per frequency (start→stop), first two points of each loop discarded, "
                      "about 599 points saved\n", text)
        self.assertIn("Estimated at least 5398.0 s (600 settles of 7 s + 599 averages of 2 s; about 1.5 h).",
                      text)
        self.assertIsNone(h.stfmr_lockin_recorder)
        self.assertEqual(h.stfmr_lockin_controllers["kepco"].calls, [], "nothing sent")


class AcquisitionTests(Sweeps):
    def assert_pattern_mean(self, row):
        self.assertAlmostEqual(float(row["x_v"]), 3e-6, delta=1e-15)
        self.assertAlmostEqual(float(row["y_v"]), 1e-6, delta=1e-15)
        # R and theta of the mean X, Y, not the mean of each reading's.
        self.assertAlmostEqual(float(row["voltage_v"]), math.sqrt(10) * 1e-6, delta=1e-15)
        self.assertAlmostEqual(float(row["phase_deg"]), math.degrees(math.atan2(1, 3)), places=9)
        mean_r = sum(math.hypot(x, y) for x, y in zip(PatternSR830.X, PatternSR830.Y)) / 3
        self.assertGreater(abs(mean_r - math.sqrt(10) * 1e-6), 1e-7)
        self.assertEqual(row["sample_count"], "21")

    def test_an_sr830_point_is_the_mean_of_its_window(self):
        h, recorder = self.sweep(meter="SR830", sr830=PatternSR830, stop="5")
        rows = read_csv(recorder.csv_path)
        self.assertEqual(len(rows), 3)
        for row in rows:
            self.assert_pattern_mean(row)
        sr830 = h.stfmr_lockin_controllers["830"]
        # The pre-read, two discarded readings (one each, no window) and
        # 3 saved points of 21 readings.
        self.assertEqual(len(sr830.options), 1 + 2 + 3 * 21)
        # Only each point's first reading may step the range down.
        self.assertEqual(sr830.options[:3], [{}, {}, {}])
        windows = sr830.options[3:]
        for point in range(3):
            window = windows[21 * point:21 * (point + 1)]
            self.assertEqual(window[0], {}, "the first reading")
            self.assertEqual(window[1:], [{"allow_down": False}] * 20)
        self.assertEqual(sum(e["event"] == "startup_point_discarded" for e in events(recorder)), 2)

    def test_an_mfli_point_is_the_mean_of_its_window_in_its_own_spelling(self):
        h, recorder = self.sweep(meter="MFLI", mfli=PatternMFLI, stop="5")
        rows = read_csv(recorder.csv_path)
        self.assertEqual(len(rows), 3)
        for row in rows:
            self.assert_pattern_mean(row)
            self.assertEqual(row["mfli_input_overload"], "False")
            self.assertEqual(row["mfli_input_range_v"], "0.1")      # after the first-point range check

    def test_both_meters_are_averaged_over_the_same_window(self):
        h, recorder = self.sweep(meter="MFLI + SR830", sr830=PatternSR830, mfli=PatternMFLI, stop="5")
        rows = read_csv(recorder.csv_path)
        self.assertEqual([r["meter"] for r in rows], ["MFLI", "SR830"] * 3)
        for row in rows:
            self.assert_pattern_mean(row)
        for mfli_row, sr830_row in zip(rows[::2], rows[1::2]):
            self.assertEqual(mfli_row["index"], sr830_row["index"])
            self.assertEqual(mfli_row["field_g"], sr830_row["field_g"])
            # Each row's time is its lock-in's last reading of the window.
            self.assertLessEqual(mfli_row["timestamp_iso"], sr830_row["timestamp_iso"])
        # Read in pairs: as many SR830 readings as MFLI readings, the SR830's
        # after the pre-read.
        mfli = h.stfmr_lockin_mfli_controller
        sr830 = h.stfmr_lockin_controllers["830"]
        self.assertEqual(len(sr830.options), len(mfli.options))

    def test_a_short_acquire_s_keeps_the_0_1_s_pace(self):
        h, recorder = self.sweep(meter="SR830", stop="5", acquire="0.35")
        # Readings at 0, 0.1, 0.2, 0.3 and 0.4 s: the window ends with the
        # first reading 0.35 s or more after the first one started.
        self.assertEqual({r["sample_count"] for r in read_csv(recorder.csv_path)}, {"5"})
        self.assertEqual(h.root.samples, [100] * 12)


class RangeAndFailureTests(Sweeps):
    def test_an_sr830_range_change_in_the_window_restarts_the_point(self):
        state = {"done": False}

        def before(index, k, sample):
            if index == 2 and not state["done"]:
                if k < 5:
                    sample = dict(sample, x_v=999e-6)        # readings on the old range
                else:
                    state["done"] = True
                    sample = dict(sample, auto_range="SENS 13->14 (overload/high)", measurement_valid=False)
            return sample

        def device(h):
            sr830 = PatternSR830(h)
            sr830.before = before
            return sr830

        h, recorder = self.sweep(meter="SR830", sr830=device, stop="5")
        retries = [e for e in events(recorder) if e["event"] == "sr830_autorange_retry"]
        self.assertEqual([(e["point_index"], e["action"]) for e in retries], [(3, "SENS 13->14 (overload/high)")])
        # The point settles 7 s again before its readings start over.
        self.assertEqual(h.root.reads, [1, 7000, 7000, 7000, 7000, 7000])
        rows = read_csv(recorder.csv_path)
        self.assertEqual(len(rows), 3)
        for row in rows:
            self.assert_pattern_mean(row)                     # nothing of the old range
        self.assertEqual(events(recorder)[-1]["outcome"], "completed")
        self.error.assert_not_called()

    assert_pattern_mean = AcquisitionTests.assert_pattern_mean

    def test_an_mfli_overload_in_the_window_ranges_and_restarts_the_point(self):
        state = {"done": False}

        def before(index, k, sample):
            if index == 2 and k == 4 and not state["done"]:
                state["done"] = True
                sample = dict(sample, input_overload=True)
            return sample

        def device(h):
            mfli = PatternMFLI(h)
            mfli.before = before
            return mfli

        h, recorder = self.sweep(meter="MFLI", mfli=device, stop="5")
        retries = [e for e in events(recorder) if e["event"] == "mfli_autorange_retry"]
        self.assertEqual([e["point_index"] for e in retries], [1, 3])     # the first-point check, then this
        self.assertIn("STFMR input overload", retries[1]["action"])
        self.assertEqual(retries[1]["settle_s"], 10.0)                    # max(7 s, 10 x TC, 0.2 s)
        self.assertEqual(h.root.reads, [1, 10000, 7000, 7000, 10000, 7000, 7000])
        rows = read_csv(recorder.csv_path)
        self.assertEqual(len(rows), 3)
        for row in rows:
            self.assert_pattern_mean(row)
        self.assertIn("STFMR input overload", rows[0]["mfli_auto_range_action"])
        self.assertEqual(rows[1]["mfli_auto_range_action"], "")

    def test_a_weak_mfli_signal_ranges_down_only_on_each_points_first_reading(self):
        """Counted on every reading, 1 % of the range would run the
        firmware autorange (a change even when it keeps the range) after
        every three low readings and restart the point, up to three times
        a point; counted on each point's first reading only, as when a point
        was one reading, it runs once in ten points here."""
        h, recorder = self.sweep(meter="MFLI", mfli=RangingMFLI, stop="5", loop=2)   # 10 points
        mfli = h.stfmr_lockin_mfli_controller
        self.assertEqual([c[1] for c in mfli.calls if c[0] == "autorange"],
                         ["STFMR frequency start", "low"])
        self.assertEqual(len(read_csv(recorder.csv_path)), 6)
        self.assertEqual(mfli.stfmr_reads.count(False), 6 * 20, "the later readings rang no range rule")
        self.error.assert_not_called()

    def test_a_failed_reading_in_the_window_stops_with_the_outputs_off(self):
        def before(index, k, sample):
            if index == 2 and k == 5:
                raise RuntimeError("SR830 read failed")
            return sample

        def device(h):
            sr830 = PatternSR830(h)
            sr830.before = before
            return sr830

        h, recorder = self.sweep(meter="SR830", sr830=device, stop="5")
        self.error.assert_called_once()
        self.assertIn("SR830 read failed", self.error.call_args.args[1])
        self.assertEqual(read_csv(recorder.csv_path), [], "no part of the point is saved")
        end = events(recorder)[-1]
        self.assertEqual((end["outcome"], end["outputs_off"]), ("error", True))
        self.assertIn(("zero",), h.stfmr_lockin_controllers["kepco"].calls)
        self.assertIn(("rf_off",), h.stfmr_lockin_controllers["smb"].calls)
        self.assertFalse(h.stfmr_lockin_running)
        self.assertIsNone(h.stfmr_lockin_acquisition)

    def test_an_unlocked_reading_in_the_window_stops_as_before(self):
        def before(index, k, sample):
            if index == 3 and k == 7:
                sample = dict(sample, reference_valid=False, measurement_valid=False, status=8)
            return sample

        def device(h):
            sr830 = PatternSR830(h)
            sr830.before = before
            return sr830

        h, recorder = self.sweep(meter="SR830", sr830=device, stop="5")
        self.error.assert_called_once()
        self.assertIn("SR830 reference unlocked or input overload; current point not saved",
                      self.error.call_args.args[1])
        self.assertEqual(len(read_csv(recorder.csv_path)), 1, "the points before it stay saved")
        self.assertEqual(events(recorder)[-1]["outcome"], "error")
        self.assertIn(("rf_off",), h.stfmr_lockin_controllers["smb"].calls)

    def test_a_dual_mfli_unlock_in_the_window_stops_both(self):
        def before(index, k, sample):
            if index == 2 and k == 3:
                sample = dict(sample, reference_valid=False)
            return sample

        def device(h):
            mfli = PatternMFLI(h)
            mfli.before = before
            return mfli

        h, recorder = self.sweep(meter="MFLI + SR830", mfli=device, stop="5")
        self.error.assert_called_once()
        self.assertIn("MFLI reference unlocked or overloaded; current dual-meter point not saved",
                      self.error.call_args.args[1])
        self.assertEqual(read_csv(recorder.csv_path), [])

    def test_stop_between_readings_ends_at_once_without_a_partial_point(self):
        h, recorder = self.sweep(meter="SR830", stop="5", start_only=True)
        while not (h.stfmr_lockin_acquisition and len(h.stfmr_lockin_acquisition["samples"]) >= 4
                   and h.stfmr_lockin_after_id is not None):
            h.root.step()
        h.stop_stfmr_lockin_scan()
        self.assertFalse(h.stfmr_lockin_running, "stopped at once, not after the window")
        h.root.drain()
        self.assertEqual(read_csv(recorder.csv_path), [])
        end = events(recorder)[-1]
        self.assertEqual((end["outcome"], end["outputs_off"]), ("stopped", True))
        self.assertIsNone(h.stfmr_lockin_acquisition)


class WindowRangeReviewTests(Sweeps):
    """Review, 2026-10-14: the near-full-scale MFLI step of a later reading
    in the window, its abort after three range changes in one point, and an
    SR830 range change in a later reading of a dual window were untested."""

    assert_pattern_mean = AcquisitionTests.assert_pattern_mean

    def mfli(self, before):
        def device(h):
            mfli = PatternMFLI(h)
            mfli.before = before
            return mfli
        return device

    def assert_stopped_with_outputs_off(self, h, recorder, text):
        self.error.assert_called_once()
        self.assertIn(text, self.error.call_args.args[1])
        end = events(recorder)[-1]
        self.assertEqual((end["outcome"], end["outputs_off"]), ("error", True))
        self.assertIn(("zero",), h.stfmr_lockin_controllers["kepco"].calls)
        self.assertIn(("rf_off",), h.stfmr_lockin_controllers["smb"].calls)
        self.assertFalse(h.stfmr_lockin_running)
        self.assertIsNone(h.stfmr_lockin_acquisition)
        self.assertEqual(h.root.pending, {}, "nothing left scheduled")

    def test_a_near_full_scale_mfli_reading_in_the_window_ranges_and_restarts_the_point(self):
        state = {"done": False}

        def before(index, k, sample):
            if index == 2 and k == 4 and not state["done"]:
                state["done"] = True
                sample = dict(sample, input_extrema_valid=True, input_utilization=0.9)
            return sample

        h, recorder = self.sweep(meter="MFLI", mfli=self.mfli(before), stop="5")
        self.error.assert_not_called()
        retries = [e for e in events(recorder) if e["event"] == "mfli_autorange_retry"]
        self.assertEqual([e["point_index"] for e in retries], [1, 3])     # the first-point check, then this
        self.assertIn("overload/high", retries[1]["action"])
        self.assertEqual(retries[1]["settle_s"], 10.0)                    # max(7 s, 10 x TC, 0.2 s)
        self.assertEqual(retries[1]["input_overload"], False, "near full scale, not overloaded")
        mfli = h.stfmr_lockin_mfli_controller
        self.assertEqual([c[1] for c in mfli.calls if c[0] == "autorange"],
                         ["STFMR frequency start", "overload/high"])
        self.assertEqual(h.root.reads, [1, 10000, 7000, 7000, 10000, 7000, 7000])
        rows = read_csv(recorder.csv_path)
        self.assertEqual(len(rows), 3)
        for row in rows:
            self.assert_pattern_mean(row)                     # nothing of the readings before the change
        self.assertIn("overload/high", rows[0]["mfli_auto_range_action"])
        self.assertEqual(rows[1]["mfli_auto_range_action"], "")
        self.assertEqual(events(recorder)[-1]["outcome"], "completed")

    def test_a_fourth_range_change_in_one_point_ends_the_sweep_with_the_outputs_off(self):
        """Near full scale on every attempt: three range changes restart the
        point, each after 10 s; the fourth is refused (the limit of three
        range changes per point) and the sweep ends as a failed read does."""
        def before(index, k, sample):
            if index == 2 and k == 4:
                sample = dict(sample, input_extrema_valid=True, input_utilization=0.9)
            return sample

        h, recorder = self.sweep(meter="MFLI", mfli=self.mfli(before), stop="5")
        self.assert_stopped_with_outputs_off(
            h, recorder, "MFLI auto-range still overloaded or near full scale after 3 retries; "
            "the current point was not saved")
        retries = [e for e in events(recorder) if e["event"] == "mfli_autorange_retry"]
        self.assertEqual([(e["point_index"], "overload/high" in e["action"]) for e in retries],
                         [(1, False), (3, True), (3, True), (3, True)])
        self.assertEqual(h.root.reads, [1, 10000, 7000, 7000, 10000, 10000, 10000])
        self.assertEqual(read_csv(recorder.csv_path), [], "no part of the point is saved")

    def test_an_sr830_range_change_in_a_dual_window_restarts_both_meters(self):
        state = {"done": False}

        def mfli_before(index, k, sample):
            if index == 2 and not state["done"] and k < 5:
                sample = dict(sample, x_v=999e-6)            # read before the SR830's change
            return sample

        def sr830_before(index, k, sample):
            if index == 2 and not state["done"]:
                if k < 5:
                    sample = dict(sample, x_v=999e-6)        # readings on the old range
                else:
                    state["done"] = True
                    sample = dict(sample, auto_range="SENS 13->14 (overload/high)", measurement_valid=False)
            return sample

        def sr830(h):
            device = PatternSR830(h)
            device.before = sr830_before
            return device

        h, recorder = self.sweep(meter="MFLI + SR830", sr830=sr830, mfli=self.mfli(mfli_before), stop="5")
        self.error.assert_not_called()
        retries = [e for e in events(recorder) if e["event"] == "sr830_autorange_retry"]
        self.assertEqual([(e["point_index"], e["action"], e["dual"], e["mfli_action"]) for e in retries],
                         [(3, "SENS 13->14 (overload/high)", True, "")])
        # max(0.1 s, the 7 s settle, 10 x the 1 s MFLI TC).
        self.assertEqual(h.root.reads, [1, 10000, 7000, 7000, 10000, 7000, 7000])
        rows = read_csv(recorder.csv_path)
        self.assertEqual([r["meter"] for r in rows], ["MFLI", "SR830"] * 3, "no partial or unpaired row")
        for row in rows:
            self.assert_pattern_mean(row)
        self.assertEqual(events(recorder)[-1]["outcome"], "completed")


class DualInvalidReadingReviewTests(Sweeps):
    """Review, 2026-10-14: a non-finite X in a dual window raised out of the
    Tk callback and left the sweep running with nothing scheduled, so Stop
    could not zero the 4886 or switch the RF off."""

    assert_stopped = WindowRangeReviewTests.assert_stopped_with_outputs_off

    def test_a_non_finite_x_in_a_dual_window_ends_the_sweep_with_the_outputs_off(self):
        for meter_name, x in (("MFLI", math.nan), ("SR830", math.inf)):
            with self.subTest(meter=meter_name):
                self.error.reset_mock()

                def before(index, k, sample):
                    if index == 2 and k == 3:
                        sample = dict(sample, x_v=x)         # R stays finite
                    return sample

                def mfli(h):
                    device = PatternMFLI(h)
                    if meter_name == "MFLI":
                        device.before = before
                    return device

                def sr830(h):
                    device = PatternSR830(h)
                    if meter_name == "SR830":
                        device.before = before
                    return device

                h, recorder = self.sweep(meter="MFLI + SR830", sr830=sr830, mfli=mfli, stop="5")
                self.assert_stopped(h, recorder, "Dual-meter reading contains invalid X/Y/Theta; "
                                                 "current point not saved")
                self.assertEqual(read_csv(recorder.csv_path), [])

    def test_any_check_that_raises_ends_the_sweep_with_the_outputs_off(self):
        """The same for an unforeseen failure in a reading's checks."""
        h, recorder = self.sweep(meter="SR830", sr830=PatternSR830, stop="5", start_only=True)
        accept = h._accept_stfmr_lockin_sample
        calls = []

        def failing(sample, **point):
            calls.append(point["index"])
            if len(calls) == 10:
                raise ValueError("unexpected reading")
            return accept(sample, **point)

        h._accept_stfmr_lockin_sample = failing
        h.root.drain()
        self.assert_stopped(h, recorder, "unexpected reading")
        self.assertEqual(calls[-1], 2, "in the first saved point's window")
        self.assertEqual(read_csv(recorder.csv_path), [])


class TimeLeftReviewTests(NoWindows):
    """Review, 2026-10-14: at about 9 s a point the status line's time left
    ran to five digits of seconds; it is now in s, min or h, as the Start
    estimate is."""

    def test_the_shared_duration_text(self):
        text = App._stfmr_lockin_duration_text
        for seconds, expected in ((-5, "0 s"), (0, "0 s"), (45.4, "45 s"), (119.4, "119 s"),
                                  (120, "2 min"), (3000, "50 min"), (3599, "60 min"),
                                  (3600, "1.0 h"), (5300, "1.5 h"), (5398, "1.5 h"), (37800, "10.5 h")):
            with self.subTest(seconds=seconds):
                self.assertEqual(text(seconds), expected)


class ValidationTests(Sweeps):
    def test_bad_settle_or_acquire_is_refused_before_any_output_or_file(self):
        for settle, acquire, text in (
                ("-1", "2", "Settle s must be a number from 0 to 3600 s"),
                ("3601", "2", "Settle s must be a number from 0 to 3600 s"),
                ("abc", "2", "Settle s must be a number from 0 to 3600 s"),
                ("", "2", "Settle s must be a number from 0 to 3600 s"),
                ("nan", "2", "Settle s must be a number from 0 to 3600 s"),
                ("1", "0", "Acquire s must be a number above 0 and at most 3600 s"),
                ("1", "-2", "Acquire s must be a number above 0 and at most 3600 s"),
                ("1", "inf", "Acquire s must be a number above 0 and at most 3600 s"),
                ("1", "3601", "Acquire s must be a number above 0 and at most 3600 s"),
                ("1", "two", "Acquire s must be a number above 0 and at most 3600 s")):
            with self.subTest(settle=settle, acquire=acquire):
                self.error.reset_mock()
                self.confirm.reset_mock()
                h, recorder = self.sweep(settle=settle, acquire=acquire)
                self.error.assert_called_once()
                self.assertEqual(self.error.call_args.args[1], text)
                self.confirm.assert_not_called()
                self.assertIsNone(recorder)
                for key in ("kepco", "smb", "830"):
                    self.assertEqual(h.stfmr_lockin_controllers[key].calls, [], key)
                self.assertEqual(list(Path(self.tmp.name).iterdir()), [], "no files")

    def test_the_bounds_themselves_are_allowed(self):
        for settle, acquire in (("0", "0.05"), ("3600", "3600")):
            with self.subTest(settle=settle, acquire=acquire):
                h = harness(self.tmp.name, meter="SR830")
                h._stfmr_lockin_connected = lambda _key: True
                h.stfmr_lockin_settle_var.set(settle)
                h.stfmr_lockin_acquire_var.set(acquire)
                *_points, settle_s, acquire_s, meter = h._stfmr_lockin_inputs()
                self.assertEqual((settle_s, acquire_s, meter), (float(settle), float(acquire), "SR830"))


class AverageTests(NoWindows):
    average = staticmethod(App._stfmr_lockin_average_samples)

    def test_sr830_readings(self):
        readings = [
            {"x_v": 1e-6, "y_v": 1e-6, "r_v": 9.0, "phase_deg": 99.0, "reference_frequency_hz": 317.0,
             "reference_valid": True, "measurement_valid": True, "sensitivity_index": 13, "auto_range": ""},
            {"x_v": 3e-6, "y_v": -1e-6, "r_v": 9.0, "phase_deg": 99.0, "reference_frequency_hz": 318.0,
             "reference_valid": True, "measurement_valid": True, "sensitivity_index": 13, "auto_range": ""},
        ]
        mean = self.average(readings)
        self.assertAlmostEqual(mean["x_v"], 2e-6)
        self.assertAlmostEqual(mean["y_v"], 0.0)
        self.assertAlmostEqual(mean["r_v"], 2e-6)
        self.assertAlmostEqual(mean["phase_deg"], 0.0)
        self.assertAlmostEqual(mean["reference_frequency_hz"], 317.5)
        self.assertEqual((mean["reference_valid"], mean["measurement_valid"], mean["sample_count"]),
                         (True, True, 2))
        self.assertEqual(mean["sensitivity_index"], 13)
        self.assertEqual(readings[0]["x_v"], 1e-6, "the readings are not changed")

    def test_mfli_readings_keep_any_overload_and_the_highest_use(self):
        base = {"X": 1e-3, "Y": 0.0, "R": 1e-3, "voltage": 1e-3, "phase_deg": 0.0,
                "reference_frequency_hz": 317.3, "reference_valid": True, "input_range_v": 0.1,
                "input_overload": False, "input_utilization": 0.2, "device_status_flags": 0, "auto_range": ""}
        readings = [dict(base), dict(base, X=-1e-3, Y=2e-3, input_overload=True, input_utilization=0.9,
                                     device_status_flags=1, reference_valid=False),
                    dict(base, input_utilization=float("nan"), device_status_flags=None, input_overload=None)]
        mean = self.average(readings)
        self.assertAlmostEqual(mean["X"], 1e-3 / 3)
        self.assertAlmostEqual(mean["Y"], 2e-3 / 3)
        self.assertAlmostEqual(mean["R"], math.hypot(1e-3 / 3, 2e-3 / 3))
        self.assertAlmostEqual(mean["voltage"], mean["R"])
        self.assertEqual((mean["input_overload"], mean["input_utilization"], mean["device_status_flags"]),
                         (True, 0.9, 1))
        self.assertFalse(mean["reference_valid"], "valid only if every reading was")
        unknown = self.average([dict(base), dict(base, input_overload=None)])
        self.assertIsNone(unknown["input_overload"], "unknown, never a false 'normal'")
        self.assertIs(self.average([dict(base)] * 2)["input_overload"], False)

    def test_dual_readings_drop_the_sr830_part_and_nothing_is_no_reading(self):
        mean = self.average([{"X": 1.0, "Y": 0.0, "_sr830": {"x_v": 5.0, "y_v": 0.0}}])
        self.assertNotIn("_sr830", mean)
        with self.assertRaisesRegex(RuntimeError, "No lock-in reading"):
            self.average([])
        with self.assertRaisesRegex(RuntimeError, "valid X"):
            self.average([{"X": float("nan"), "Y": 0.0}])


class Sr830WindowRangingTests(NoWindows):
    """The real STFMR SR830 controller on a fake SR830: a later reading of a
    point's window (allow_down=False) steps the range only up."""

    class Instrument:
        def __init__(self, r_v, status=0):
            self.r_v, self.status = r_v, status
            self.sens = 13
            self.writes = []

        def query(self, command):
            if command.startswith("SNAP?"):
                return f"{self.r_v},0,{self.r_v},0,317.3"
            if command == "LIAS?":
                return str(self.status)
            if command == "SENS?":
                return str(self.sens)
            raise AssertionError(command)

        def write(self, command):
            self.writes.append(command)
            if command.startswith("SENS "):
                self.sens = int(command.split()[1])

    def controller(self, r_v, status=0):
        controller = app.STFMRSR830Controller(lambda _message: None)
        controller.instrument = self.Instrument(r_v, status)
        controller.sensitivity_index = 13          # 50 uV full scale
        return controller

    def test_low_readings_in_the_window_neither_step_down_nor_count(self):
        controller = self.controller(1e-7)        # 0.2 % of 50 uV
        for _ in range(5):
            sample = controller.read_sample(317.3, allow_down=False)
            self.assertEqual(sample["auto_range"], "")
        self.assertEqual(controller.instrument.writes, [])
        self.assertEqual((controller.low_range_count, controller.range_cooldown), (0, 0))
        # Three first readings of points step it down, as before.
        for _ in range(3):
            sample = controller.read_sample(317.3)
        self.assertEqual(sample["auto_range"], "SENS 13->12 (low)")
        self.assertEqual(controller.instrument.writes, ["SENS 12"])

    def test_an_overload_in_the_window_still_steps_up(self):
        controller = self.controller(1e-7, status=1)
        sample = controller.read_sample(317.3, allow_down=False)
        self.assertEqual(sample["auto_range"], "SENS 13->14 (overload/high)")
        self.assertFalse(sample["measurement_valid"])
        near_full = self.controller(45e-6)        # 90 % of 50 uV
        self.assertEqual(near_full.read_sample(317.3, allow_down=False)["auto_range"],
                         "SENS 13->14 (overload/high)")


if __name__ == "__main__":
    unittest.main()
