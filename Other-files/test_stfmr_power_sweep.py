"""STFMR page, P start / P stop / P step (user, 2026-10-14).

The user asked: "also add power start, stop, step in ST-FMR module", "when
sweep power, can you do not close the SMA100? like 6221 in the current scan,
just change its output value" and "the default power change to 12dBm in the
ST-FMR".  The single "Power dBm" box became "P start dBm", "P stop dBm" and
"P step dBm" (12, 12, 1 by default: one power, 12 dBm).  The power list
follows the f start / f stop / f step rules.  The sweep runs modulation
(outermost), then power, then carrier, then the magnet current; at a power
change the 4886 is zeroed, only the SMB100A's level changes (":POW", read
back with ":POW?"), RF, AM and LFO stay on, the 4886 comes back on at Start
A and the first two readings are discarded, as after a carrier change.

No Tk window and no instrument: the real sweep runs on the shared STFMR
harness (stand-in devices that record what they were sent, a Root that runs
callbacks at once but keeps each wait), the real SMB100A controller talks to
a fake instrument, and the table is the real page build on stand-in widgets.
Every message box and file dialog is mocked, and creating a Tk root fails
the test."""
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
from test_stfmr_realtime_recording import Variable, app, events, harness, read_csv
from test_stfmr_sr830_tc_fixed_current import History, NoWindows, TimedRoot, build_stfmr_page
from test_stfmr_mod_prime_range import Recording, width_in_zeros

App = app.CombinedControlApp
levels = app.stfmr_power_levels


class PowerRecording(Recording):
    """The modulation test's recording device, which also takes a power."""

    def set_power(self, dbm):
        self.calls.append(("power", dbm))
        self.record("power", dbm)
        return dbm


class FakeSmb100a:
    """An SMB100A that keeps the level it was last sent and answers :POW?
    with it.  refuses: levels it ignores (as a generator does with a level
    out of its range), keeping the old one."""

    def __init__(self, refuses=(), reply=None):
        self.writes, self.queries = [], []
        self.level = None
        self.refuses = set(refuses)
        self.reply = reply

    def write(self, command):
        self.writes.append(command)
        if command.startswith(":POW "):
            value = float(command.split()[1])
            if value not in self.refuses:
                self.level = value

    def query(self, command):
        self.queries.append(command)
        if command != ":POW?":
            raise AssertionError(f"unexpected query {command}")
        if self.reply is not None:
            return self.reply
        return f"{self.level:g}"


class PowerListTests(NoWindows):
    def test_the_default_is_one_power_of_12_dbm(self):
        self.assertEqual(app.STFMR_LOCKIN_DEFAULT_POWER_DBM, 12.0)     # user, 2026-10-14 (6 before)
        self.assertEqual(app.STFMR_LOCKIN_DEFAULT_POWER_STEP_DB, 1.0)
        with open(app.__file__, encoding="utf-8") as source:
            text = source.read()
        for name, constant in (("power_start", "STFMR_LOCKIN_DEFAULT_POWER_DBM"),
                               ("power_stop", "STFMR_LOCKIN_DEFAULT_POWER_DBM"),
                               ("power_step", "STFMR_LOCKIN_DEFAULT_POWER_STEP_DB")):
            self.assertRegex(text, rf"self\.stfmr_lockin_{name}_var = tk\.StringVar\(\s*"
                                   rf"value=f\"{{{constant}:g}}\"")
        self.assertNotIn("stfmr_lockin_power_var", text, "every user of the old box was moved")
        self.assertEqual(levels("12", "12", "1"), (12.0,))

    def test_equal_start_and_stop_give_that_one_power(self):
        for start, stop, step in (("12", "12", "1"), (" 12 ", "12.0", "5"), (12.0, 12, 0.5),
                                  ("-30", "-30", "1"), ("25", "25", "1"), ("12", "12", "-1")):
            with self.subTest(start=start, stop=stop, step=step):
                self.assertEqual(levels(start, stop, step), (float(start),))

    def test_up_down_and_the_step_follow_the_carrier_list(self):
        self.assertEqual(levels("0", "4", "1"), (0.0, 1.0, 2.0, 3.0, 4.0))
        self.assertEqual(levels("4", "0", "1"), (4.0, 3.0, 2.0, 1.0, 0.0), "descending")
        self.assertEqual(levels("0", "4", "-1"), (0.0, 1.0, 2.0, 3.0, 4.0), "the step's sign is ignored")
        self.assertEqual(levels("0", "5", "2"), (0.0, 2.0, 4.0, 5.0), "ends exactly on P stop")
        self.assertEqual(levels("10", "-10", "5"), (10.0, 5.0, 0.0, -5.0, -10.0))
        self.assertEqual(levels("0", "1", "0.1"), tuple(n / 10 for n in range(11)), "no float dust")
        self.assertEqual(levels("10", "14", "2"), (10.0, 12.0, 14.0))
        # The same rule as the f start / f stop / f step list, value for value.
        for start, stop, step in (("1", "5", "1.5"), ("7", "2", "2"), ("2.5", "3.25", "0.25")):
            with self.subTest(start=start, stop=stop, step=step):
                carriers = App._build_stfmr_frequency_points(start, stop, step)
                self.assertEqual(levels(start, stop, step), tuple(f / 1e9 for f in carriers))

    def test_every_power_stays_within_the_smb100a_limit(self):
        self.assertEqual(app.STFMR_SMB100A_MAX_POWER_DBM, 25.0)
        self.assertEqual(levels("20", "25", "1")[-1], 25.0, "25 dBm itself is allowed")
        for start, stop in (("24", "26"), ("26", "24"), ("26", "26"), ("30", "20"), ("12", "25.01")):
            with self.subTest(start=start, stop=stop):
                with self.assertRaisesRegex(ValueError, r"^SMB100A power must not exceed 25 dBm"):
                    levels(start, stop, "1")

    def test_bad_text_is_refused(self):
        for start, stop, step in (("", "12", "1"), ("abc", "12", "1"), ("12", "x", "1"), ("12", "12", ""),
                                  ("nan", "12", "1"), ("12", "inf", "1"), ("-inf", "12", "1"),
                                  ("12", "14", "nan"), (None, "12", "1")):
            with self.subTest(start=start, stop=stop, step=step):
                with self.assertRaisesRegex(
                        ValueError, r"^P start dBm, P stop dBm and P step dBm must be finite numbers$"):
                    levels(start, stop, step)
        for start, stop in (("10", "14"), ("12", "12")):    # as f step: refused even for one value
            with self.subTest(start=start, stop=stop):
                with self.assertRaisesRegex(ValueError, r"^P step dBm must be greater than 0 dB$"):
                    levels(start, stop, "0")

    def test_the_list_is_capped_at_1000_powers(self):
        self.assertEqual(app.STFMR_LOCKIN_MAX_POWERS, 1000)
        values = levels("0", "9.99", "0.01")
        self.assertEqual((len(values), values[0], values[-1]), (1000, 0.0, 9.99))
        self.assertEqual(len(levels("24", "-975", "1")), 1000)
        with self.assertRaisesRegex(ValueError, r"gives 1001 powers; a sweep takes at most 1000 "
                                                r"\(each power is a whole f × A sweep\); increase P step"):
            levels("0", "10", "0.01")
        for step in ("0.001", "1e-300", "5e-324"):     # the last two overflow the count
            with self.subTest(step=step):
                with self.assertRaisesRegex(ValueError, r"gives (more than 1000|\d+) powers; a sweep takes "
                                                        r"at most 1000"):
                    levels("0", "20", step)

    def test_the_confirmation_line(self):
        summary = app.stfmr_power_summary
        self.assertEqual(summary([12.0], 12, 12, 1), "Power dBm: 12")
        self.assertEqual(summary(levels(10, 14, 2), 10, 14, 2),
                         "Power dBm: 10, 12, 14 (3 powers from 10 to 14 in 2 dB steps); the whole "
                         "f × A sweep runs at each, in this order, with the RF on between them")
        self.assertEqual(summary(levels(5, -5, -2.5), 5, -5, -2.5),
                         "Power dBm: 5, 2.5, 0, -2.5, -5 (5 powers from 5 to -5 in 2.5 dB steps); the "
                         "whole f × A sweep runs at each, in this order, with the RF on between them")
        long = summary(levels(0, 20, 1), 0, 20, 1)
        self.assertTrue(long.startswith("Power dBm: 0, 1, 2, 3, 4, …, 19, 20 (21 powers from 0 to 20 in "
                                        "1 dB steps)"), long)


class Smb100aPowerTests(NoWindows):
    def controller(self, **options):
        controller = app.STFMRSMB100AController(lambda _message: None)
        controller.instrument = fake = FakeSmb100a(**options)
        return controller, fake

    def test_only_the_level_is_sent_and_read_back(self):
        controller, fake = self.controller()
        controller.configure(5.0, 12.0, 117.3)
        self.assertIn(":POW 12", fake.writes, "configure() sends the first power")
        fake.writes.clear()
        self.assertEqual(controller.set_power(14.0), 14.0)
        self.assertEqual(fake.writes, [":POW 14"], "no OUTPUT, AM or LFO ON/OFF")
        self.assertEqual(fake.queries, [":POW?"])
        # The same format configure() uses.
        for value, command in ((12.5, ":POW 12.5"), (-3.25, ":POW -3.25"), (0.0, ":POW 0"), (25.0, ":POW 25")):
            with self.subTest(value=value):
                controller.configure(5.0, value, 117.3)
                self.assertIn(command, fake.writes)
                fake.writes.clear()
                controller.set_power(value)
                self.assertEqual(fake.writes, [command])
                fake.writes.clear()

    def test_a_level_the_generator_did_not_take_is_refused(self):
        controller, fake = self.controller(refuses={14.0})
        controller.set_power(12.0)
        with self.assertRaisesRegex(RuntimeError, r"^SMB100A did not take the new power: sent ':POW 14', "
                                                  r":POW\? read back '12' \(more than 0\.01 dB from 14 dBm\)"):
            controller.set_power(14.0)
        self.assertEqual(fake.writes, [":POW 12", ":POW 14"], "nothing else was sent")

    def test_the_read_back_tolerance_is_0_01_db(self):
        self.assertEqual(app.STFMR_SMB100A_POWER_READBACK_TOLERANCE_DB, 0.01)
        for reply, accepted in (("14", True), ("14.01", True), ("13.99", True), ("1.4E+01", True),
                                ("14.02", False), ("13.98", False), ("", False), ("abc", False),
                                ("nan", False)):
            with self.subTest(reply=reply):
                controller, _fake = self.controller(reply=reply)
                if accepted:
                    self.assertAlmostEqual(controller.set_power(14.0), float(reply))
                else:
                    with self.assertRaisesRegex(RuntimeError, "did not take the new power"):
                        controller.set_power(14.0)

    def test_bad_values_send_nothing(self):
        controller, fake = self.controller()
        for bad in (math.nan, math.inf, -math.inf, 25.01, 30.0):
            with self.subTest(value=bad):
                with self.assertRaises(ValueError):
                    controller.set_power(bad)
        self.assertEqual((fake.writes, fake.queries), ([], []))
        controller.instrument = None
        with self.assertRaisesRegex(RuntimeError, "not connected"):
            controller.set_power(12.0)


class PowerSweepCase(NoWindows):
    """A sweep on the shared harness, with recording devices; Settle s 0.5,
    the harness's Acquire s 0.2 (three readings a point)."""

    def sweep(self, powers=("10", "14", "2"), mod=("117.3", "117.3"), meter="SR830", stop="6",
              end_current="0.5", tc="100 ms", fail=None, smb=None, start_only=False):
        h = harness(self.tmp.name, meter=meter, stop=stop, end_current=end_current)
        h.root = TimedRoot()
        h.stfmr_lockin_status_var = History()
        h.stfmr_lockin_settle_var.set("0.5")
        h.stfmr_lockin_sr830_time_constant_var = Variable(tc)
        h.stfmr_lockin_mod_start_var.set(mod[0])
        h.stfmr_lockin_mod_end_var.set(mod[1])
        h.stfmr_lockin_power_start_var.set(powers[0])
        h.stfmr_lockin_power_stop_var.set(powers[1])
        h.stfmr_lockin_power_step_var.set(powers[2])
        h.log = []
        h.stfmr_lockin_controllers = {
            key: PowerRecording(h, key, h.log) for key in ("kepco", "gauss", "smb", "830")}
        if smb is not None:
            h.stfmr_lockin_controllers["smb"] = smb
        h.stfmr_lockin_mfli_controller = PowerRecording(h, "mfli", h.log)
        if fail:
            device, call, value = fail
            (h.stfmr_lockin_controllers.get(device) or h.stfmr_lockin_mfli_controller
             ).fail_on[call] = value
        h.progress = []
        h._set_stfmr_progress = lambda _bar, done, total: h.progress.append((done, total))
        h._append_stfmr_lockin_live_sample = mock.Mock()
        h.start_stfmr_lockin_scan()
        recorder = h.stfmr_lockin_recorder
        if not start_only:
            h.root.drain()
        return h, recorder

    def message(self):
        self.confirm.assert_called_once()
        return self.confirm.call_args.args[1]


class PowerSweepTests(PowerSweepCase):
    """Three powers (10, 12, 14 dBm), two carriers (5, 6 GHz), four currents
    (-1 to 0.5 A), the SR830: six passes, power outside the carrier."""

    def test_power_runs_outside_the_carrier_and_the_rf_stays_on(self):
        h, recorder = self.sweep()
        self.error.assert_not_called()
        self.assertEqual(h.stfmr_lockin_controllers["smb"].calls, [
            ("configure", (5.0, 10.0, 117.3)), ("frequency", 6.0),
            ("power", 12.0), ("frequency", 5.0), ("frequency", 6.0),
            ("power", 14.0), ("frequency", 5.0), ("frequency", 6.0),
            ("rf_off",)], "the SMB100A is switched off only at the end")
        # Each power change: the 4886 to zero, then only the level and the
        # first carrier, then the 4886 back on at Start A; no reference wait
        # (the SR830 follows the LFO, which a power change leaves alone).
        for dbm in (12.0, 14.0):
            at = h.log.index(("smb", "power", dbm))
            self.assertEqual(h.log[at - 1], ("kepco", "zero"))
            self.assertEqual(h.log[at + 1:at + 3], [("smb", "frequency", 5.0),
                                                    ("kepco", "output", "CURR", -1.0, 10.0)])
        self.assertEqual([c for c in h.stfmr_lockin_controllers["830"].calls if c[0] == "wait_for_reference"],
                         [("wait_for_reference", 117.3, 5.0, None)], "only at the start")
        self.assertEqual(sum(c == ("zero",) for c in h.stfmr_lockin_controllers["kepco"].calls), 6,
                         "before each of the five changes and at the end")
        self.assertEqual(sum(c[0] == "output" for c in h.stfmr_lockin_controllers["kepco"].calls), 6,
                         "on at Start A at each of the six passes")
        # Each pass: a pre-read, two discarded readings and two saved points
        # of three readings each.
        self.assertEqual(h.stfmr_lockin_controllers["830"].expected, [117.3] * 54)

    def test_settling_readings_are_discarded_and_rows_carry_their_power(self):
        h, recorder = self.sweep()
        rows = read_csv(recorder.csv_path)
        self.assertEqual(len(rows), 12)
        self.assertEqual([float(r["microwave_power_dbm"]) for r in rows], [10.0] * 4 + [12.0] * 4 + [14.0] * 4)
        self.assertEqual([float(r["microwave_frequency_ghz"]) for r in rows], [5, 5, 6, 6] * 3)
        self.assertEqual([int(r["frequency_index"]) for r in rows], [1, 1, 2, 2] * 3)
        self.assertEqual([int(r["point_in_loop"]) for r in rows], [3, 4] * 6, "the first two of each pass dropped")
        self.assertEqual([int(r["index"]) for r in rows],
                         [4 * n + k for n in range(6) for k in (3, 4)], "one index across the run")
        self.assertEqual([r["microwave_power_dbm"] for r in h.stfmr_lockin_rows][::4], [10.0, 12.0, 14.0])
        history = events(recorder)
        discarded = [e for e in history if e["event"] == "startup_point_discarded"]
        self.assertEqual([(e["power_dbm"], e["frequency_ghz"], e["point_index"]) for e in discarded],
                         [(p, f, k) for p in (10.0, 12.0, 14.0) for f in (5.0, 6.0) for k in (1, 2)])
        self.assertEqual(sum(e["event"] == "startup_prime_discarded" for e in history), 6)
        configured = [e for e in history if e["event"] == "frequency_configure"]
        self.assertEqual([(e["power_dbm"], e["power_index"], e["power_change"], e["frequency_ghz"],
                           e["initial"], e["modulation_change"]) for e in configured],
                         [(10.0, 1, False, 5.0, True, False), (10.0, 1, False, 6.0, False, False),
                          (12.0, 2, True, 5.0, False, False), (12.0, 2, False, 6.0, False, False),
                          (14.0, 3, True, 5.0, False, False), (14.0, 3, False, 6.0, False, False)])
        ready = [e for e in history if e["event"] == "frequency_ready"]
        self.assertEqual([(e["power_dbm"], e.get("power_readback_dbm")) for e in ready],
                         [(10.0, None), (10.0, None), (12.0, 12.0), (12.0, None), (14.0, 14.0), (14.0, None)])
        self.assertNotIn("power_readback_dbm", ready[0], "only a changed power is read back")
        self.assertEqual([(e["power_dbm"], e["saved_points"]) for e in history
                          if e["event"] == "frequency_complete"],
                         [(10.0, 2), (10.0, 2), (12.0, 2), (12.0, 2), (14.0, 2), (14.0, 2)])
        self.assertEqual((history[-1]["outcome"], history[-1]["saved_points"], history[-1]["outputs_off"]),
                         ("completed", 12, True))
        self.assertEqual(h.progress[-1], (24, 24))
        # The live plots get each point's power.
        live = h._append_stfmr_lockin_live_sample.call_args_list
        self.assertEqual([c.kwargs["power_dbm"] for c in live], [10.0] * 4 + [12.0] * 4 + [14.0] * 4)

    def test_the_csv_keeps_its_columns(self):
        """The rows already had a microwave_power_dbm column, so it now
        carries each row's power and no column is added."""
        h, recorder = self.sweep()
        with recorder.csv_path.open(encoding="utf-8-sig") as handle:
            header = handle.readline().strip().split(",")
        self.assertEqual(header, list(app.STFMRScanRecorder.FIELDNAMES))
        self.assertEqual(len(header), 34)
        self.assertEqual(header.index("microwave_power_dbm"), 15)

    def test_the_confirmation_lists_the_powers_and_the_estimate_counts_them(self):
        h, recorder = self.sweep()
        text = self.message()
        self.assertIn("Microwave: 5 → 6 GHz, 2 frequencies\nMod. Hz: 117.3\n"
                      "Power dBm: 10, 12, 14 (3 powers from 10 to 14 in 2 dB steps); the whole f × A "
                      "sweep runs at each, in this order, with the RF on between them\n", text)
        self.assertIn("about 12 points saved", text)
        # 3 powers x 2 carriers = 6 passes of 4 readings; all but each pass's
        # first settle max(Settle s 0.5, 7 x 100 ms) = 0.7 s, and each of the
        # 12 saved points averages 0.2 s.
        self.assertIn("Estimated at least 15.0 s (18 settles of 0.7 s + 12 averages of 0.2 s).", text)
        self.assertEqual(h.root.reads, [1, 700, 700, 700] * 6)
        settings = events(recorder)[0]["settings"]
        self.assertEqual((settings["powers_dbm"], settings["power_dbm"], settings["power_start_dbm"],
                          settings["power_stop_dbm"], settings["power_step_db"],
                          settings["rf_on_between_powers"], settings["expected_saved_points"]),
                         ([10.0, 12.0, 14.0], 10.0, 10.0, 14.0, 2.0, True, 12))

    def test_status_lines_name_the_power_and_fit(self):
        h, recorder = self.sweep()
        statuses = h.stfmr_lockin_status_var.history
        self.assertIn("10 dBm done; zeroing; RF on, to 12 dBm at 5 GHz", statuses)
        self.assertIn("12 dBm done; zeroing; RF on, to 14 dBm at 5 GHz", statuses)
        self.assertIn("5 GHz/12 dBm: I=-1 A sent; pre-read now", statuses)
        self.assertIn("5 GHz/14 dBm done; zeroing; to 6 GHz", statuses)
        self.assertTrue(any(s.startswith("6 GHz/10 dBm discarded start-transition point 2/2") for s in statuses))
        scanning = [s for s in statuses if re.search(r"; \d+/24; ", s)]
        self.assertEqual(len(scanning), 12)
        for status in scanning:
            self.assertRegex(status, r"^[56] GHz/1[024] dBm; \d+/24; I=[-+][0-9.]+ A; "
                                     r"~(\d+ s|\d+ min|\d+\.\d h) left$")
        self.assertEqual(statuses[-1], "Multi-frequency scan complete: 2 frequencies, 3 powers, 12 points "
                                       "saved; 4886/SMB100A outputs OFF; CSV saved")
        # The 52-character status label, with long labels and hours left
        # (Arial 15 px, a little wider than the lab's YaHei UI).
        long_run = [
            "10.5 GHz/-12.5 dBm; 1234/4800; I=-2.92 A; ~10.5 h left",
            "-12.5 dBm done; zeroing; RF on, to -10.5 dBm at 10.25 GHz",
            "10 GHz/12.5 dBm: I=-2.92 A sent; pre-read now",
            "10 GHz/12.5 dBm done; zeroing; to 11 GHz",
            "10 GHz/313 Hz/12.5 dBm; 1234/4800; I=-2.92 A; ~10.5 h left",
            "10 GHz/313 Hz/12.5 dBm: I=-2.92 A sent; pre-read now",
        ]
        for status in [s for s in statuses if "; zeroing" in s or "pre-read now" in s] + scanning + long_run:
            with self.subTest(status=status):
                self.assertLessEqual(width_in_zeros(status), 52)

    def test_plot_series_keep_the_powers_apart(self):
        h, recorder = self.sweep(stop="5")
        series = h._stfmr_lockin_field_component_series(
            h.stfmr_lockin_rows, "SR830", "8", "x_v", group_frequency=True)
        self.assertEqual(set(series), {"8_pow_10_freq_5_loop_1", "8_pow_12_freq_5_loop_1",
                                       "8_pow_14_freq_5_loop_1"})
        labels, colors = App._stfmr_lockin_frequency_series_styles(series, "x_v")
        self.assertEqual(labels, {"8_pow_10_freq_5_loop_1": "5 GHz, 10 dBm",
                                  "8_pow_12_freq_5_loop_1": "5 GHz, 12 dBm",
                                  "8_pow_14_freq_5_loop_1": "5 GHz, 14 dBm"})
        self.assertEqual(len(set(colors.values())), 3, "one shade per power")
        current = h._stfmr_lockin_current_field_series(h.stfmr_lockin_rows, "SR830", "8")
        self.assertEqual(set(current), set(series))

    def test_negative_and_decimal_powers_parse_back_from_the_series_names(self):
        rows = [{"meter": "MFLI", "loop": 1, "field_g": 10.0 * k, "x_v": 1e-6 * k,
                 "microwave_frequency_ghz": 5.0, "modulation_frequency_hz": 307.0 + 4 * (k % 2),
                 "microwave_power_dbm": power}
                for k, power in enumerate((-12.5, -12.5, 0.25, 0.25))]
        series = App._stfmr_lockin_field_component_series(rows, "MFLI", "mfli", "x_v", group_frequency=True)
        self.assertEqual(set(series), {"mfli_mod_307_pow_-12.5_freq_5_loop_1", "mfli_mod_311_pow_-12.5_freq_5_loop_1",
                                       "mfli_mod_307_pow_0.25_freq_5_loop_1", "mfli_mod_311_pow_0.25_freq_5_loop_1"})
        labels, colors = App._stfmr_lockin_frequency_series_styles(series, "x_v")
        self.assertEqual(labels["mfli_mod_311_pow_-12.5_freq_5_loop_1"], "5 GHz, 311 Hz, -12.5 dBm")
        self.assertEqual(labels["mfli_mod_307_pow_0.25_freq_5_loop_1"], "5 GHz, 307 Hz, 0.25 dBm")
        self.assertEqual(len(set(colors.values())), 4)

    def test_the_live_plot_rows_keep_their_power(self):
        a = object.__new__(App)
        a.stfmr_lockin_live_rows, a.stfmr_lockin_live_started = [], 0.0
        a._set_stfmr_lockin_reference_state = a._set_stfmr_lockin_overload_state = lambda *_a: None
        a._redraw_stfmr_lockin_live_plots = lambda: None
        a.stfmr_lockin_voltage_var, a.stfmr_lockin_reference_status_var = Variable(""), Variable("")
        sample = {"x_v": 1e-6, "y_v": 2e-6, "r_v": 3e-6, "phase_deg": 10.0,
                  "reference_frequency_hz": 117.3, "reference_valid": True}
        for power in (10.0, 12.0):
            App._append_stfmr_lockin_live_sample(a, "SR830", sample, field_g=5.0, frequency_ghz=5.0,
                                                 modulation_frequency_hz=117.3, power_dbm=power)
        self.assertEqual([row["microwave_power_dbm"] for row in a.stfmr_lockin_live_rows], [10.0, 12.0])
        series = App._stfmr_lockin_field_component_series(
            a.stfmr_lockin_live_rows, "SR830", "8", "x_v", group_frequency=True)
        self.assertEqual(set(series), {"8_pow_10_freq_5_loop_1", "8_pow_12_freq_5_loop_1"})

    def test_modulation_power_carrier_current_in_that_order(self):
        """Two primes (307, 311 Hz) outside two powers (10, 12 dBm), one
        carrier: at a modulation change the LFO and then the first power are
        set, RF still on, before the lock is checked again."""
        h, recorder = self.sweep(powers=("10", "12", "2"), mod=("307", "311"), stop="5")
        self.error.assert_not_called()
        self.assertEqual(h.stfmr_lockin_controllers["smb"].calls, [
            ("configure", (5.0, 10.0, 307.0)),
            ("power", 12.0), ("frequency", 5.0),
            ("modulation", 311.0), ("power", 10.0), ("frequency", 5.0),
            ("power", 12.0), ("frequency", 5.0),
            ("rf_off",)])
        at = h.log.index(("smb", "modulation", 311.0))
        self.assertEqual(h.log[at - 1], ("kepco", "zero"))
        self.assertEqual(h.log[at + 1:at + 5], [
            ("smb", "power", 10.0), ("smb", "frequency", 5.0), ("830", "wait_for_reference", 311.0),
            ("kepco", "output", "CURR", -1.0, 10.0)])
        rows = read_csv(recorder.csv_path)
        self.assertEqual([(float(r["modulation_frequency_hz"]), float(r["microwave_power_dbm"])) for r in rows],
                         [(m, p) for m in (307.0, 311.0) for p in (10.0, 12.0) for _k in (3, 4)])
        self.assertEqual([int(r["index"]) for r in rows], [4 * n + k for n in range(4) for k in (3, 4)])
        text = self.message()
        self.assertIn("Mod. Hz: 307, 311 (2 primes from 307 to 311); the whole P × f × A sweep runs at each, "
                      "in this order\nPower dBm: 10, 12 (2 powers from 10 to 12 in 2 dB steps)", text)
        self.assertIn("Estimated at least 10.0 s (12 settles of 0.7 s + 8 averages of 0.2 s).", text)
        statuses = h.stfmr_lockin_status_var.history
        self.assertIn("Mod. 307 Hz done; zeroing; LFO to 311 Hz at 5 GHz", statuses)
        self.assertIn("5 GHz/311 Hz/10 dBm: I=-1 A sent; pre-read now", statuses)
        self.assertTrue(statuses[-1].startswith(
            "Multi-frequency scan complete: 1 frequencies, 2 modulation frequencies, 2 powers, 8 points saved"))
        series = h._stfmr_lockin_field_component_series(
            h.stfmr_lockin_rows, "SR830", "8", "x_v", group_frequency=True)
        self.assertEqual(set(series), {f"8_mod_{m}_pow_{p}_freq_5_loop_1" for m in (307, 311) for p in (10, 12)})
        labels, _colors = App._stfmr_lockin_frequency_series_styles(series, "x_v")
        self.assertEqual(labels["8_mod_311_pow_12_freq_5_loop_1"], "5 GHz, 311 Hz, 12 dBm")

    def test_a_dual_meter_sweep_saves_pairs_at_each_power(self):
        h, recorder = self.sweep(meter="MFLI + SR830", stop="5", powers=("12", "4", "4"))   # descending
        self.error.assert_not_called()
        rows = read_csv(recorder.csv_path)
        self.assertEqual([(r["meter"], float(r["microwave_power_dbm"])) for r in rows],
                         [(m, p) for p in (12.0, 8.0, 4.0) for _k in (3, 4) for m in ("MFLI", "SR830")])
        self.assertEqual([c for c in h.stfmr_lockin_controllers["smb"].calls if c[0] == "power"],
                         [("power", 8.0), ("power", 4.0)])

    def test_a_power_the_smb100a_refuses_ends_with_the_outputs_off(self):
        h, recorder = self.sweep(fail=("smb", "power", 14.0))
        self.error.assert_called_once()
        self.assertIn("smb power failed at 14", self.error.call_args.args[1])
        failed = h.log.index(("smb", "power", 14.0))
        self.assertEqual(h.log[failed - 1], ("kepco", "zero"), "the 4886 was already at zero")
        self.assertEqual(h.log[failed + 1:], [("kepco", "zero"), ("smb", "rf_off")],
                         "the 4886 is not switched back on")
        self.assertEqual({float(r["microwave_power_dbm"]) for r in read_csv(recorder.csv_path)}, {10.0, 12.0})
        self.assertEqual(events(recorder)[-1]["outcome"], "error")
        self.assertFalse(h.stfmr_lockin_running)

    def test_stop_right_after_a_power_change_ends_with_the_outputs_off(self):
        h, recorder = self.sweep(start_only=True)
        while len(h.stfmr_lockin_rows) < 4:      # the last saved point at 10 dBm
            h.root.step()
        # The stand-in executor has already run the change to 12 dBm.
        self.assertIn(("smb", "power", 12.0), h.log)
        h.stop_stfmr_lockin_scan()
        h.root.drain()
        self.assertFalse(h.stfmr_lockin_running)
        self.assertEqual(h.log[-2:], [("kepco", "zero"), ("smb", "rf_off")])
        self.assertEqual(len(read_csv(recorder.csv_path)), 4)
        self.assertEqual(events(recorder)[-1]["outcome"], "stopped")
        self.error.assert_not_called()


class RealSmb100aSweepTests(PowerSweepCase):
    """The real SMB100A controller on a fake generator, through a whole
    sweep over three powers."""

    def real(self, **options):
        controller = app.STFMRSMB100AController(lambda _message: None)
        controller.instrument = FakeSmb100a(**options)
        return controller

    def test_the_output_is_switched_on_once_and_off_only_at_the_end(self):
        smb = self.real()
        h, recorder = self.sweep(smb=smb)
        self.error.assert_not_called()
        writes = smb.instrument.writes
        self.assertEqual(writes[:8], [":AM:DEPT:LIN 1.0E+2", "LFO:VOLT 0.50 V", "LFO:FREQ 117.3 Hz", ":POW 10",
                                      "FREQ 5GHz", "LFO ON", "OUTPUT ON", "AM:STAT ON"])
        self.assertEqual(writes[8:-3], ["FREQ 6GHz", ":POW 12", "FREQ 5GHz", "FREQ 6GHz",
                                        ":POW 14", "FREQ 5GHz", "FREQ 6GHz"],
                         "between powers only the level and the carrier; nothing switched off")
        self.assertEqual(writes[-3:], ["AM:STAT OFF", "LFO OFF", "OUTPUT OFF"])
        self.assertEqual(smb.instrument.queries, [":POW?", ":POW?"])
        self.assertEqual(writes.count("OUTPUT ON"), 1)
        self.assertEqual(len(read_csv(recorder.csv_path)), 12)
        self.assertEqual(events(recorder)[-1]["outcome"], "completed")

    def test_a_level_the_generator_ignored_stops_the_sweep_with_the_outputs_off(self):
        smb = self.real(refuses={14.0})
        h, recorder = self.sweep(smb=smb)
        self.error.assert_called_once()
        self.assertIn("SMB100A did not take the new power: sent ':POW 14', :POW? read back '12'",
                      self.error.call_args.args[1])
        writes = smb.instrument.writes
        self.assertEqual(writes[writes.index(":POW 14"):], [":POW 14", "AM:STAT OFF", "LFO OFF", "OUTPUT OFF"],
                         "no carrier change, the RF off")
        kepco = h.stfmr_lockin_controllers["kepco"].calls
        self.assertEqual(kepco[-2:], [("zero",), ("zero",)], "zeroed before the change and at the end")
        self.assertEqual({float(r["microwave_power_dbm"]) for r in read_csv(recorder.csv_path)}, {10.0, 12.0})
        self.assertEqual(events(recorder)[-1]["outcome"], "error")


class DefaultSweepTests(PowerSweepCase):
    def test_the_default_single_power_sweep_is_otherwise_unchanged(self):
        """P start = P stop = 12 dBm (the page's defaults): configure() sets
        12 dBm, no power change, the old status lines, series names and
        legend, and one 'Power dBm: 12' line in the confirmation."""
        h, recorder = self.sweep(powers=("12", "12", "1"), meter="MFLI", end_current="1")
        self.error.assert_not_called()
        smb = h.stfmr_lockin_controllers["smb"].calls
        self.assertEqual(smb, [("configure", (5.0, 12.0, 117.3)), ("frequency", 6.0), ("rf_off",)])
        text = self.message()
        self.assertIn("Microwave: 5 → 6 GHz, 2 frequencies\nMod. Hz: 117.3\nPower dBm: 12\n", text)
        rows = read_csv(recorder.csv_path)
        self.assertEqual({float(r["microwave_power_dbm"]) for r in rows}, {12.0})
        self.assertEqual([int(r["index"]) for r in rows], [3, 4, 5, 8, 9, 10])
        statuses = h.stfmr_lockin_status_var.history
        self.assertFalse([s for s in statuses if "dBm" in s], "the status lines read as before")
        self.assertIn("5 GHz: start current I=-1 A sent; running the start pre-read now", statuses)
        self.assertIn("5 GHz done; zeroing and switching to 6 GHz", statuses)
        for status in [s for s in statuses if "; scanning " in s]:
            self.assertRegex(status, r"^[56] GHz; scanning \d+/10; I=[-+][0-9.]+ A; "
                                     r"remaining about (\d+ s|\d+ min|\d+\.\d h)$")
        self.assertEqual(statuses[-1], "Multi-frequency scan complete: 2 frequencies, 6 points saved; "
                                       "4886/SMB100A outputs OFF; CSV saved")
        series = h._stfmr_lockin_current_field_series(h.stfmr_lockin_rows, "MFLI", "mfli")
        self.assertEqual(set(series), {"mfli_freq_5_loop_1", "mfli_freq_6_loop_1"}, "the old names")
        labels, _colors = App._stfmr_lockin_frequency_series_styles(series, "current_a")
        self.assertEqual(labels, {"mfli_freq_5_loop_1": "5 GHz", "mfli_freq_6_loop_1": "6 GHz"})
        history = events(recorder)
        self.assertEqual((history[0]["settings"]["power_dbm"], history[0]["settings"]["powers_dbm"]),
                         (12.0, [12.0]))
        self.assertFalse([e for e in history if e.get("power_change")], "no power change")

    def test_bad_boxes_are_refused_before_any_output_or_file(self):
        for powers, text in (
                (("26", "26", "1"), "SMB100A power must not exceed 25 dBm"),
                (("20", "30", "5"), "SMB100A power must not exceed 25 dBm"),
                (("abc", "12", "1"), "P start dBm, P stop dBm and P step dBm must be finite numbers"),
                (("12", "", "1"), "P start dBm, P stop dBm and P step dBm must be finite numbers"),
                (("nan", "12", "1"), "P start dBm, P stop dBm and P step dBm must be finite numbers"),
                (("10", "14", "0"), "P step dBm must be greater than 0 dB"),
                (("0", "20", "0.001"), "a sweep takes at most 1000")):
            with self.subTest(powers=powers):
                self.error.reset_mock()
                h, recorder = self.sweep(powers=powers)
                self.error.assert_called_once()
                self.assertIn(text, self.error.call_args.args[1])
                self.confirm.assert_not_called()
                self.assertIsNone(recorder)
                self.assertFalse(h.stfmr_lockin_running)
                self.assertEqual(h.log, [], "nothing sent")
                self.assertEqual(list(Path(self.tmp.name).iterdir()), [], "no files")


class TableTests(NoWindows):
    """The real page build, on stand-in widgets (the CI GUI test checks the
    drawn page in the 1101 px and 2000 px windows)."""

    def test_the_power_row_sits_under_the_carrier_row(self):
        page = build_stfmr_page()
        a = page.app
        table = a.stfmr_lockin_meter_combo.master
        carrier = page.in_row(table, 0)
        self.assertEqual([w.options.get("text", "") for w in carrier[0::2]],
                         ["f start GHz:", "f stop GHz:", "f step GHz:"])
        power = page.in_row(table, 1)
        self.assertEqual([w.grid_options["column"] for w in power], [0, 1, 2, 3, 4, 5])
        self.assertEqual([w.options.get("text", "") for w in power[0::2]],
                         ["P start dBm:", "P stop dBm:", "P step dBm:"])
        for field, variable, entry in zip(
                power[1::2],
                (a.stfmr_lockin_power_start_var, a.stfmr_lockin_power_stop_var, a.stfmr_lockin_power_step_var),
                (a.stfmr_lockin_power_start_entry, a.stfmr_lockin_power_stop_entry,
                 a.stfmr_lockin_power_step_entry)):
            self.assertEqual((field.kind, field.options["width"], field.grid_options["sticky"]), ("Entry", 8, "ew"))
            self.assertIs(field.options["textvariable"], variable)
            self.assertIs(field, entry, "kept, so a sweep can lock it")
        everything = [str(w.options.get("text", "")) for row in range(7) for w in page.in_row(table, row)]
        self.assertNotIn("Power dBm:", everything, "the single box is gone")
        self.assertEqual(page.in_row(table, 6), [], "six rows")
        for row in range(6):
            self.assertLessEqual(max(w.grid_options["column"] for w in page.in_row(table, row)), 9,
                                 "at most five pairs a row")

    def test_the_power_labels_are_no_wider_than_the_columns_they_join(self):
        """The 1101 px window has little room left on Windows, so each power
        label is no wider than the widest label already in its column, but
        for "P step dBm:" against "f step GHz:" (a few px), which the
        column 6 label "Power dBm:" leaving more than pays for.  Arial 15 px
        stands in for the lab's YaHei UI."""
        try:
            from PIL import ImageFont
        except ImportError:
            raise unittest.SkipTest("Pillow is needed to measure the labels")
        for name in ("arial.ttf", "Arial.ttf", "/System/Library/Fonts/Supplemental/Arial.ttf", "DejaVuSans.ttf"):
            try:
                font = ImageFont.truetype(name, 15)
                break
            except OSError:
                continue
        else:
            raise unittest.SkipTest("no Arial or DejaVu Sans to measure the labels with")
        width = font.getlength
        self.assertLessEqual(width("P start dBm:"), width("MFLI reference:"))
        self.assertLessEqual(width("P stop dBm:"), width("SR830 REF IN:"))
        widest_before = {4: max(map(width, ("f step GHz:", "Step A:", "Settle s:", "SR830 TC:"))),
                         6: max(map(width, ("Power dBm:", "Loop:", "Acquire s:")))}
        widest_now = {4: max(map(width, ("f step GHz:", "P step dBm:", "Step A:", "Settle s:", "SR830 TC:"))),
                      6: max(map(width, ("Loop:", "Acquire s:")))}
        self.assertLess(sum(widest_now.values()), sum(widest_before.values()), "the table is not wider")


if __name__ == "__main__":
    unittest.main()
