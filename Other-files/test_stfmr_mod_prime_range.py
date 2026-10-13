"""STFMR page, Mod. start / Mod. end (user, 2026-10-13).

The single "Mod. Hz" box became "Mod. start Hz" and "Mod. end Hz", both
317.3 by default.  Equal, they give that one modulation frequency, as
before; different, every whole-number prime between them (no step), from
start towards end, and the whole f x A sweep runs at each in turn.

No Tk window and no instrument: the real sweep runs on the shared STFMR
harness (stand-in devices that record what they were sent, a Root that runs
callbacks at once but keeps each wait), the real SMB100A controller talks to
a fake instrument, and the table is the real page build on stand-in widgets.
Every message box and file dialog is mocked, and creating a Tk root fails
the test."""
import math
import re
import sys
import types
import unittest
from pathlib import Path

_HERE = Path(__file__).resolve().parent
for _path in (_HERE / "test", _HERE.parent):
    if str(_path) not in sys.path:
        sys.path.insert(0, str(_path))
from test_stfmr_realtime_recording import Device, Variable, app, events, harness, read_csv
from test_stfmr_sr830_tc_fixed_current import History, NoWindows, TimedRoot, build_stfmr_page

App = app.CombinedControlApp
primes = app.stfmr_modulation_frequencies


class Recording(Device):
    """The harness's stand-in device, also keeping every call, in order, in
    one list shared by all the devices, and answering at the expected
    reference frequency."""

    def __init__(self, harness, name, log):
        super().__init__(harness)
        self.name, self.log = name, log
        self.expected = []          # the expected frequency of every read
        self.fail_on = {}           # call name -> the frequency it fails at

    def record(self, call, *args):
        self.log.append((self.name, call) + args)
        if call in self.fail_on and args and args[0] == self.fail_on[call]:
            raise RuntimeError(f"{self.name} {call} failed at {args[0]:g}")

    def configure(self, *args):
        super().configure(*args)
        self.record("configure", *args)

    def set_modulation_frequency(self, hz):
        self.calls.append(("modulation", hz))
        self.record("modulation", hz)

    def wait_for_reference(self, expected, timeout_s=5.0):
        self.calls.append(("wait_for_reference", expected, timeout_s))
        self.record("wait_for_reference", expected)

    def set_frequency(self, value):
        super().set_frequency(value)
        self.record("frequency", value)

    def set_output(self, *args):
        super().set_output(*args)
        self.record("output", *args)

    def zero_output(self):
        super().zero_output()
        self.record("zero")

    def rf_off(self):
        super().rf_off()
        self.record("rf_off")

    def read_sample(self, expected):
        self.expected.append(expected)
        sample = super().read_sample(expected)
        sample["reference_frequency_hz"] = expected
        return sample


class SweepTests(NoWindows):
    def sweep(self, start="307", end="313", meter="SR830", stop="6", end_current="0.5",
              tc="100 ms", fail=None, start_only=False):
        h = harness(self.tmp.name, meter=meter, stop=stop, end_current=end_current)
        h.root = TimedRoot()
        h.stfmr_lockin_status_var = History()
        h.stfmr_lockin_delay_var.set("0.5")
        h.stfmr_lockin_sr830_time_constant_var = Variable(tc)
        h.stfmr_lockin_mod_start_var.set(start)
        h.stfmr_lockin_mod_end_var.set(end)
        h.log = []
        h.stfmr_lockin_controllers = {
            key: Recording(h, key, h.log) for key in ("kepco", "gauss", "smb", "830")}
        h.stfmr_lockin_mfli_controller = Recording(h, "mfli", h.log)
        if fail:
            device, call, value = fail
            (h.stfmr_lockin_controllers.get(device) or h.stfmr_lockin_mfli_controller
             ).fail_on[call] = value
        h.progress = []
        h._set_stfmr_progress = lambda _bar, done, total: h.progress.append((done, total))
        h.start_stfmr_lockin_scan()
        recorder = h.stfmr_lockin_recorder
        if not start_only:
            h.root.drain()
        return h, recorder

    def message(self):
        self.confirm.assert_called_once()
        return self.confirm.call_args.args[1]


class PrimeListTests(NoWindows):
    def test_equal_boxes_give_that_one_value_as_before(self):
        self.assertEqual(app.STFMR_LOCKIN_DEFAULT_LFO_HZ, 317.3)
        for start, end in (("317.3", "317.3"), (" 317.30", "317.3 "), (317.3, 317.3)):
            self.assertEqual(primes(start, end), (317.3,), (start, end))
        self.assertEqual(primes("1000", "1000"), (1000.0,), "not a prime, but the one value asked for")
        self.assertEqual(primes("0.25", "0.25"), (0.25,))

    def test_ascending_and_descending(self):
        self.assertEqual(primes("300", "330"), (307.0, 311.0, 313.0, 317.0))
        self.assertEqual(primes("330", "300"), (317.0, 313.0, 311.0, 307.0))
        self.assertEqual(primes("2", "20"), (2.0, 3.0, 5.0, 7.0, 11.0, 13.0, 17.0, 19.0))

    def test_both_bounds_are_included(self):
        self.assertEqual(primes("307", "317"), (307.0, 311.0, 313.0, 317.0))
        self.assertEqual(primes("317", "307"), (317.0, 313.0, 311.0, 307.0))
        self.assertEqual(primes("306.5", "317.9"), (307.0, 311.0, 313.0, 317.0), "between the bounds")
        self.assertEqual(primes("308", "312"), (311.0,))
        self.assertEqual(primes("2", "3"), (2.0, 3.0))

    def test_the_same_primes_as_the_non_local_sieve(self):
        self.assertEqual(primes("2", "5000"), app.nonlocal_prime_frequencies(2, 5000))
        self.assertEqual(primes("5000", "2"), tuple(reversed(app.nonlocal_prime_frequencies(2, 5000))))

    def test_a_range_without_a_prime_is_refused(self):
        for start, end in (("24", "28"), ("28", "24"), ("300", "306"), ("1", "1.5"), ("2.1", "2.9")):
            with self.subTest(start=start, end=end):
                with self.assertRaisesRegex(ValueError, r"^No whole-number prime from Mod\. start "):
                    primes(start, end)
        with self.assertRaisesRegex(ValueError, "from Mod. start 24 to Mod. end 28 Hz; widen the range, "
                                                "or set Mod. end = Mod. start"):
            primes("24", "28")

    def test_a_box_that_is_not_a_finite_positive_number_is_refused(self):
        for start, end in (("", "317.3"), ("317.3", "abc"), ("nan", "nan"), ("inf", "inf"),
                           ("300", "inf"), ("-5", "20"), ("0", "0"), ("-317.3", "-317.3"),
                           (None, "317.3")):
            with self.subTest(start=start, end=end):
                with self.assertRaisesRegex(
                        ValueError, r"^Mod\. start Hz and Mod\. end Hz must be finite positive numbers$"):
                    primes(start, end)

    def test_the_cap_is_the_microwave_sweeps_10000(self):
        self.assertEqual(app.STFMR_LOCKIN_MAX_MODULATION_FREQUENCIES, 10_000)
        with self.assertRaisesRegex(ValueError, "exceed 10000"):
            App._build_stfmr_frequency_points(1, 10001, 1)    # the microwave limit it follows
        values = primes("2", "104729")                         # the 10000th prime
        self.assertEqual((len(values), values[0], values[-1]), (10_000, 2.0, 104729.0))
        self.assertEqual(len(primes("104729", "2")), 10_000)
        for start, end in (("2", "104743"), ("104743", "2"), ("1", "1e7")):   # 10001 primes, more
            with self.subTest(start=start, end=end):
                with self.assertRaisesRegex(ValueError, "holds more than 10000 primes, and a sweep takes "
                                                        "at most 10000 modulation frequencies"):
                    primes(start, end)

    def test_a_prime_range_beyond_whole_float_numbers_is_refused(self):
        with self.assertRaisesRegex(ValueError, "must stay below 9007199254740992 Hz"):
            primes("1e300", "2e300")
        self.assertEqual(primes("1e20", "1e20"), (1e20,), "one value is still any positive number")

    def test_the_confirmation_line(self):
        summary = app.stfmr_modulation_summary
        self.assertEqual(summary([317.3], 317.3, 317.3), "Mod. Hz: 317.3")
        self.assertEqual(summary(primes(300, 330), 300, 330),
                         "Mod. Hz: 307, 311, 313, 317 (4 primes from 300 to 330); "
                         "the whole f × A sweep runs at each, in this order")
        self.assertEqual(summary(primes(330, 300), 330, 300),
                         "Mod. Hz: 317, 313, 311, 307 (4 primes from 330 to 300); "
                         "the whole f × A sweep runs at each, in this order")
        self.assertEqual(summary([311.0], 308, 312), "Mod. Hz: 311 (the only prime from 308 to 312)")
        long = summary(primes(2, 1000), 2, 1000)
        self.assertEqual(long, "Mod. Hz: 2, 3, 5, 7, 11, …, 991, 997 (168 primes from 2 to 1000); "
                               "the whole f × A sweep runs at each, in this order")


class Smb100aTests(NoWindows):
    class Instrument:
        def __init__(self):
            self.writes = []

        def write(self, command):
            self.writes.append(command)

    def test_only_the_lfo_frequency_is_sent_as_configure_sends_it(self):
        controller = app.STFMRSMB100AController(lambda _message: None)
        controller.instrument = fake = self.Instrument()
        controller.configure(5.0, 6.0, 317.3)
        self.assertIn("LFO:FREQ 317.3 Hz", fake.writes)
        fake.writes.clear()
        controller.set_modulation_frequency(311.0)
        self.assertEqual(fake.writes, ["LFO:FREQ 311 Hz"], "no OUTPUT, AM or LFO ON/OFF")
        for bad in (math.nan, 0.0, -311.0, math.inf):
            with self.subTest(value=bad):
                with self.assertRaises(ValueError):
                    controller.set_modulation_frequency(bad)
        self.assertEqual(fake.writes, ["LFO:FREQ 311 Hz"], "nothing sent for a bad value")
        controller.instrument = None
        with self.assertRaisesRegex(RuntimeError, "not connected"):
            controller.set_modulation_frequency(311.0)


class ModulationSweepTests(SweepTests):
    """Three primes (307, 311, 313 Hz), two carriers (5, 6 GHz), four
    currents (-1 to 0.5 A), the SR830: six passes, modulation outermost."""

    def test_the_whole_sweep_runs_at_each_modulation_frequency_in_order(self):
        h, recorder = self.sweep()
        self.error.assert_not_called()
        smb = [call for call in h.stfmr_lockin_controllers["smb"].calls]
        self.assertEqual(smb, [
            ("configure", (5.0, 10.0, 307.0)), ("frequency", 6.0),
            ("modulation", 311.0), ("frequency", 5.0), ("frequency", 6.0),
            ("modulation", 313.0), ("frequency", 5.0), ("frequency", 6.0),
            ("rf_off",)])
        waits = [c for c in h.stfmr_lockin_controllers["830"].calls if c[0] == "wait_for_reference"]
        self.assertEqual(waits, [("wait_for_reference", 307.0, 5.0), ("wait_for_reference", 311.0, 5.0),
                                 ("wait_for_reference", 313.0, 5.0)], "re-checked as at the start")
        self.assertEqual(sum(c[0] == "configure" for c in h.stfmr_lockin_controllers["830"].calls), 1)
        # Each change: the 4886 to zero, then the LFO and the first carrier,
        # then the lock, and only then the 4886 back on at Start A.
        for hz in (311.0, 313.0):
            at = h.log.index(("smb", "modulation", hz))
            self.assertEqual(h.log[at - 1], ("kepco", "zero"))
            self.assertEqual(h.log[at + 1:at + 4], [
                ("smb", "frequency", 5.0), ("830", "wait_for_reference", hz),
                ("kepco", "output", "CURR", -1.0, 10.0)])
        # Every read expects the modulation of its pass: a pre-read and four
        # points at each of the two carriers.
        self.assertEqual(h.stfmr_lockin_controllers["830"].expected,
                         [307.0] * 10 + [311.0] * 10 + [313.0] * 10)

    def test_settling_readings_after_each_change_are_discarded_and_rows_are_tagged(self):
        h, recorder = self.sweep()
        rows = read_csv(recorder.csv_path)
        self.assertEqual(len(rows), 12)
        self.assertEqual([float(r["modulation_frequency_hz"]) for r in rows],
                         [307.0] * 4 + [311.0] * 4 + [313.0] * 4)
        self.assertEqual([float(r["microwave_frequency_ghz"]) for r in rows], [5, 5, 6, 6] * 3)
        self.assertEqual([int(r["frequency_index"]) for r in rows], [1, 1, 2, 2] * 3)
        self.assertEqual([int(r["point_in_loop"]) for r in rows], [3, 4] * 6, "the first two of each pass dropped")
        self.assertEqual([int(r["index"]) for r in rows],
                         [4 * n + k for n in range(6) for k in (3, 4)], "one index across the run")
        self.assertEqual([float(r["reference_frequency_hz"]) for r in rows],
                         [float(r["modulation_frequency_hz"]) for r in rows])
        history = events(recorder)
        discarded = [e for e in history if e["event"] == "startup_point_discarded"]
        self.assertEqual([(e["modulation_frequency_hz"], e["point_index"]) for e in discarded],
                         [(hz, k) for hz in (307.0, 311.0, 313.0) for _f in (5, 6) for k in (1, 2)])
        self.assertEqual(sum(e["event"] == "startup_prime_discarded" for e in history), 6)
        configured = [e for e in history if e["event"] == "frequency_configure"]
        self.assertEqual([(e["modulation_frequency_hz"], e["frequency_ghz"], e["initial"], e["modulation_change"],
                           e["modulation_index"]) for e in configured],
                         [(307.0, 5.0, True, False, 1), (307.0, 6.0, False, False, 1),
                          (311.0, 5.0, False, True, 2), (311.0, 6.0, False, False, 2),
                          (313.0, 5.0, False, True, 3), (313.0, 6.0, False, False, 3)])
        self.assertEqual([(e["modulation_frequency_hz"], e["saved_points"]) for e in history
                          if e["event"] == "frequency_complete"],
                         [(307.0, 2), (307.0, 2), (311.0, 2), (311.0, 2), (313.0, 2), (313.0, 2)])
        self.assertEqual((history[-1]["outcome"], history[-1]["saved_points"], history[-1]["outputs_off"]),
                         ("completed", 12, True))
        self.assertEqual(h.progress[-1], (24, 24))
        statuses = h.stfmr_lockin_status_var.history
        self.assertIn("Mod. 307 Hz done; zeroing and switching to Mod. 311 Hz at 5 GHz", statuses)
        self.assertIn("Mod. 311 Hz done; zeroing and switching to Mod. 313 Hz at 5 GHz", statuses)
        self.assertTrue(any(s.startswith("5 GHz, Mod. 311 Hz discarded start-transition point 1/2") for s in statuses))
        self.assertTrue(statuses[-1].startswith(
            "Multi-frequency scan complete: 2 frequencies, 3 modulation frequencies, 12 points saved"))

    def test_the_csv_keeps_its_columns(self):
        """The rows already had a modulation_frequency_hz column, so no
        column is added and every old column keeps its place."""
        h, recorder = self.sweep()
        with recorder.csv_path.open(encoding="utf-8-sig") as handle:
            header = handle.readline().strip().split(",")
        self.assertEqual(header, list(app.STFMRScanRecorder.FIELDNAMES))
        self.assertEqual(len(header), 30)
        self.assertEqual(header.index("modulation_frequency_hz"), 16)
        self.assertEqual(header[-1], "sr830_time_constant_s")

    def test_the_confirmation_lists_them_and_the_estimate_counts_them(self):
        h, recorder = self.sweep()
        text = self.message()
        self.assertIn("Microwave: 5 → 6 GHz, 2 frequencies\n"
                      "Mod. Hz: 307, 311, 313 (3 primes from 307 to 313); "
                      "the whole f × A sweep runs at each, in this order\n", text)
        self.assertIn("about 12 points saved", text)
        # 3 x 2 passes of 4 readings; all but each pass's first wait 0.5 s.
        self.assertIn("Estimated at least 9.0 s.", text)
        self.assertEqual(h.root.reads, [1, 500, 500, 500] * 6)
        settings = events(recorder)[0]["settings"]
        self.assertEqual((settings["modulation_frequencies_hz"], settings["modulation_start_hz"],
                          settings["modulation_end_hz"], settings["modulation_frequency_hz"],
                          settings["expected_saved_points"]),
                         ([307.0, 311.0, 313.0], 307.0, 313.0, 307.0, 12))

    def test_a_descending_dual_meter_sweep_keeps_the_frequencies_apart(self):
        h, recorder = self.sweep(start="13", end="11", meter="MFLI + SR830", stop="5")
        self.error.assert_not_called()
        self.assertEqual([c for c in h.stfmr_lockin_controllers["smb"].calls if c[0] == "modulation"],
                         [("modulation", 11.0)])
        self.assertEqual([c[1] for c in h.stfmr_lockin_controllers["830"].calls if c[0] == "wait_for_reference"],
                         [13.0, 11.0])
        self.assertIn("Mod. Hz: 13, 11 (2 primes from 13 to 11)", self.message())
        rows = read_csv(recorder.csv_path)
        self.assertEqual([(r["meter"], float(r["modulation_frequency_hz"])) for r in rows],
                         [(m, hz) for hz in (13.0, 11.0) for _k in (3, 4) for m in ("MFLI", "SR830")])
        series = h._stfmr_lockin_field_component_series(
            h.stfmr_lockin_rows, "MFLI + SR830", "mfli", "x_v", group_frequency=True)
        self.assertEqual(set(series), {"mfli_mod_13_freq_5_loop_1", "8_mod_13_freq_5_loop_1",
                                       "mfli_mod_11_freq_5_loop_1", "8_mod_11_freq_5_loop_1"})
        labels, colors = App._stfmr_lockin_frequency_series_styles(series, "x_v")
        self.assertEqual(labels["mfli_mod_13_freq_5_loop_1"], "5 GHz, 13 Hz MFLI")
        self.assertEqual(labels["8_mod_11_freq_5_loop_1"], "5 GHz, 11 Hz SR830")
        self.assertNotEqual(colors["mfli_mod_13_freq_5_loop_1"], colors["mfli_mod_11_freq_5_loop_1"])
        current = h._stfmr_lockin_current_field_series(h.stfmr_lockin_rows, "MFLI + SR830", "mfli")
        self.assertEqual(set(current), {"mfli_mod_13_freq_5_loop_1", "mfli_mod_11_freq_5_loop_1"})

    def test_the_mfli_alone_follows_the_lfo_without_an_sr830(self):
        h, recorder = self.sweep(start="311", end="317", meter="MFLI", stop="5")
        self.error.assert_not_called()
        self.assertEqual(h.stfmr_lockin_controllers["830"].calls, [], "the SR830 is not used")
        self.assertEqual([c for c in h.stfmr_lockin_controllers["smb"].calls if c[0] == "modulation"],
                         [("modulation", 313.0), ("modulation", 317.0)])
        expected = h.stfmr_lockin_mfli_controller.expected
        self.assertEqual(sorted(set(expected), key=expected.index), [311.0, 313.0, 317.0])
        self.assertEqual(expected, sorted(expected), "each read expects its own pass's modulation")
        self.assertEqual({float(r["modulation_frequency_hz"]) for r in read_csv(recorder.csv_path)},
                         {311.0, 313.0, 317.0})

    def test_a_lost_lock_after_a_change_stops_with_the_outputs_off(self):
        h, recorder = self.sweep(fail=("830", "wait_for_reference", 311.0))
        self.error.assert_called_once()
        self.assertIn("830 wait_for_reference failed at 311", self.error.call_args.args[1])
        failed = h.log.index(("830", "wait_for_reference", 311.0))
        self.assertEqual(h.log[failed + 1:], [("kepco", "zero"), ("smb", "rf_off")],
                         "the 4886 is not switched back on")
        self.assertEqual({float(r["modulation_frequency_hz"]) for r in read_csv(recorder.csv_path)}, {307.0})
        self.assertEqual(events(recorder)[-1]["outcome"], "error")
        self.assertFalse(h.stfmr_lockin_running)

    def test_a_failed_lfo_change_stops_with_the_outputs_off(self):
        h, recorder = self.sweep(fail=("smb", "modulation", 313.0))
        self.error.assert_called_once()
        failed = h.log.index(("smb", "modulation", 313.0))
        self.assertEqual(h.log[failed - 1], ("kepco", "zero"), "the 4886 was already at zero")
        self.assertEqual(h.log[failed + 1:], [("kepco", "zero"), ("smb", "rf_off")])
        self.assertEqual(events(recorder)[-1]["outcome"], "error")

    def test_stop_right_after_a_change_ends_with_the_outputs_off(self):
        h, recorder = self.sweep(start_only=True)
        while len(h.stfmr_lockin_rows) < 4:      # the last saved point at 307 Hz
            h.root.step()
        # The stand-in executor has already run the change to 311 Hz.
        self.assertIn(("smb", "modulation", 311.0), h.log)
        h.stop_stfmr_lockin_scan()
        h.root.drain()
        self.assertFalse(h.stfmr_lockin_running)
        self.assertEqual(h.log[-2:], [("kepco", "zero"), ("smb", "rf_off")])
        self.assertNotIn(311.0, h.stfmr_lockin_controllers["830"].expected, "nothing read at 311 Hz")
        self.assertEqual(len(read_csv(recorder.csv_path)), 4)
        self.assertEqual(events(recorder)[-1]["outcome"], "stopped")
        self.error.assert_not_called()


class DefaultSweepTests(SweepTests):
    def test_the_default_317_3_sweep_is_unchanged(self):
        h, recorder = self.sweep(start="317.3", end="317.3", meter="MFLI", stop="6", end_current="1")
        self.error.assert_not_called()
        self.assertEqual([c for c in h.stfmr_lockin_controllers["smb"].calls if c[0] == "modulation"], [])
        self.assertEqual(h.stfmr_lockin_controllers["smb"].calls[0], ("configure", (5.0, 10.0, 317.3)))
        self.assertEqual(h.stfmr_lockin_controllers["830"].calls, [])
        text = self.message()
        self.assertIn("Microwave: 5 → 6 GHz, 2 frequencies\nMod. Hz: 317.3\n", text)
        rows = read_csv(recorder.csv_path)
        self.assertEqual({float(r["modulation_frequency_hz"]) for r in rows}, {317.3})
        self.assertEqual([int(r["index"]) for r in rows], [3, 4, 5, 8, 9, 10])
        statuses = h.stfmr_lockin_status_var.history
        self.assertFalse([s for s in statuses if "Mod." in s], "the status lines read as before")
        self.assertEqual(statuses[-1], "Multi-frequency scan complete: 2 frequencies, 6 points saved; "
                                       "4886/SMB100A outputs OFF; CSV saved")
        series = h._stfmr_lockin_current_field_series(h.stfmr_lockin_rows, "MFLI", "mfli")
        self.assertEqual(set(series), {"mfli_freq_5_loop_1", "mfli_freq_6_loop_1"}, "the old names")
        labels, _colors = App._stfmr_lockin_frequency_series_styles(series, "current_a")
        self.assertEqual(labels, {"mfli_freq_5_loop_1": "5 GHz", "mfli_freq_6_loop_1": "6 GHz"})
        settings = events(recorder)[0]["settings"]
        self.assertEqual((settings["modulation_frequency_hz"], settings["modulation_frequencies_hz"]),
                         (317.3, [317.3]))

    def test_bad_boxes_are_refused_before_any_output_or_file(self):
        for start, end, text in (
                ("300", "306", "No whole-number prime from Mod. start 300 to Mod. end 306 Hz"),
                ("abc", "317.3", "Mod. start Hz and Mod. end Hz must be finite positive numbers"),
                ("0", "0", "Mod. start Hz and Mod. end Hz must be finite positive numbers"),
                ("2", "104743", "holds more than 10000 primes")):
            with self.subTest(start=start, end=end):
                self.error.reset_mock()
                h, recorder = self.sweep(start=start, end=end)
                self.error.assert_called_once()
                self.assertIn(text, self.error.call_args.args[1])
                self.confirm.assert_not_called()
                self.assertIsNone(recorder)
                self.assertFalse(h.stfmr_lockin_running)
                self.assertEqual(h.log, [], "nothing sent")
                self.assertEqual(list(Path(self.tmp.name).iterdir()), [], "no files")


class IdleReferenceTests(NoWindows):
    """The idle Ref lamp's expected frequency is Mod. start."""

    def live(self, mod_start):
        expected = []

        class SR830:
            connected = True

            def read_sample(self, hz, auto_range=True):
                expected.append(hz)
                return {"x_v": 1e-6, "y_v": 0.0, "r_v": 1e-6, "phase_deg": 0.0,
                        "reference_frequency_hz": hz, "reference_valid": True}

        class Now:
            def submit(self, function):
                from concurrent.futures import Future
                future = Future()
                future.set_result(function())
                return future

        a = object.__new__(App)
        a.closing = a.stfmr_lockin_closed = a.stfmr_lockin_running = False
        a.stfmr_lockin_live_future = None
        a.stfmr_lockin_meter_var = Variable("SR830")
        a.stfmr_lockin_gauss_continuous_var = Variable(False)
        a.stfmr_lockin_mod_start_var = Variable(mod_start)
        a.stfmr_lockin_mod_end_var = Variable("331")
        a.stfmr_lockin_controllers = {"830": SR830(), "gauss": types.SimpleNamespace(connected=False)}
        a.stfmr_lockin_mfli_controller = types.SimpleNamespace(mfli_connected=False)
        a.stfmr_lockin_executor = Now()
        a.post_ui = lambda callback: callback()
        a.root = types.SimpleNamespace(after=lambda _ms, _callback: None)
        a.stfmr_lockin_reference_indicator = a.stfmr_lockin_overload_indicator = None
        a.stfmr_lockin_voltage_var, a.stfmr_lockin_reference_status_var = Variable(""), Variable("")
        a.stfmr_lockin_status_var, a.stfmr_lockin_field_var = Variable(""), Variable("")
        a._stfmr_lockin_live_timer()
        return expected, a

    def test_it_expects_mod_start_or_the_default(self):
        self.assertEqual(self.live("311")[0], [311.0])
        expected, a = self.live("317.3")
        self.assertEqual(expected, [317.3])
        self.assertEqual(a.stfmr_lockin_reference_status_var.get(), "Ref: 317.3 Hz LOCK")
        for bad in ("abc", "", "nan", "-5", "0"):
            with self.subTest(value=bad):
                self.assertEqual(self.live(bad)[0], [app.STFMR_LOCKIN_DEFAULT_LFO_HZ])


class TableTests(NoWindows):
    """The real page build, on stand-in widgets (the CI GUI test checks the
    drawn page in the 1101 px and 2000 px windows)."""

    def test_mod_start_and_end_take_the_mod_cell_and_the_one_below_it(self):
        page = build_stfmr_page()
        a = page.app
        table = a.stfmr_lockin_meter_combo.master
        expected = {
            0: ("Mod. start Hz:", a.stfmr_lockin_mod_start_var),
            1: ("Mod. end Hz:", a.stfmr_lockin_mod_end_var),
            2: ("Wait/pt s:", a.stfmr_lockin_delay_var),   # moved beside Loop
        }
        for row, (text, variable) in expected.items():
            with self.subTest(row=row):
                label, field = page.cell(table, row, 8), page.cell(table, row, 9)
                self.assertEqual((label.kind, label.options["text"]), ("Label", text))
                self.assertEqual(label.grid_options["sticky"], "e")
                self.assertEqual((field.kind, field.options["width"], field.grid_options["sticky"]),
                                 ("Entry", 8, "ew"))
                self.assertIs(field.options["textvariable"], variable)
                self.assertEqual(max(w.grid_options["column"] for w in page.in_row(table, row)), 9,
                                 "no sixth pair")
        self.assertEqual([w.grid_options["column"] for w in page.in_row(table, 3)], [0, 1, 2, 3, 4, 6, 7])
        self.assertEqual(page.cell(table, 2, 6).options["text"], "Loop:")
        # Like the f and A rows: start in the top row, end under it.
        for row, labels in ((0, ["f start GHz:", "f stop GHz:", "f step GHz:", "Power dBm:", "Mod. start Hz:"]),
                            (1, ["Start A:", "Stop A:", "Step A:", "Comp. V:", "Mod. end Hz:"])):
            texts = [w.options.get("text", "") for w in page.in_row(table, row)]
            self.assertEqual(texts[0::2], labels)
            self.assertEqual(texts[1::2], [""] * 5, "an entry right of each label")
        everything = [str(w.options.get("text", "")) for row in range(5) for w in page.in_row(table, row)]
        self.assertNotIn("Mod. Hz:", everything)
        self.assertFalse([t for t in everything if "step" in t.lower() and "Mod" in t], "no Mod. step box")
        self.assertEqual([w.grid_options["column"] for w in page.in_row(table, 1)], list(range(10)))

    def test_both_boxes_default_to_317_3(self):
        with open(app.__file__, encoding="utf-8") as source:
            text = source.read()
        for name in ("stfmr_lockin_mod_start_var", "stfmr_lockin_mod_end_var"):
            self.assertRegex(text, rf"self\.{name} = tk\.StringVar\(\s*value=f\"{{STFMR_LOCKIN_DEFAULT_LFO_HZ:g}}\"")
        self.assertEqual(f"{app.STFMR_LOCKIN_DEFAULT_LFO_HZ:g}", "317.3")
        self.assertNotIn("stfmr_lockin_lfo_var", text, "every user of the old box was moved")


if __name__ == "__main__":
    unittest.main()
