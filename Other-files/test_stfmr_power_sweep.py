"""STFMR page, P start / P stop / P step (user, 2026-10-14).

The user asked: "also add power start, stop, step in ST-FMR module", "when
sweep power, can you do not close the SMA100? like 6221 in the current scan,
just change its output value" and "the default power change to 12dBm in the
ST-FMR".  The single "Power dBm" box became "P start dBm", "P stop dBm" and
"P step dB" (12, 12, 1 by default: one power, 12 dBm; the step's unit is dB:
review, 2026-10-14).  The power list follows the f start / f stop / f step
rules, each power rounded to the SMB100A's 0.01 dB and repeats merged
(review, 2026-10-14).  The sweep runs modulation (outermost), then power,
then carrier, then the magnet current; at a power change the 4886 is zeroed,
only the SMB100A's level changes (":POW", read back with ":POW?"), RF, AM
and LFO stay on, the 4886 comes back on at Start A and the first two
readings are discarded, as after a carrier change.  The first power is read
back too, inside configure(), before the RF is switched on (review,
2026-10-14).

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

    def configure(self, *args):
        super().configure(*args)
        # The SMB100A's configure() returns its read-back of the first
        # power (review, 2026-10-14); the stand-in takes every level.
        return args[1] if self.name == "smb" else None

    def set_power(self, dbm):
        self.calls.append(("power", dbm))
        self.record("power", dbm)
        return dbm


class FakeSmb100a:
    """An SMB100A that keeps the level it was last sent and answers :POW?
    with it, starting at 6 dBm (the last run's level, say).  refuses: levels
    it ignores (as a generator does with a level out of its range), keeping
    the old one.  sequence: every write and query, in order."""

    def __init__(self, refuses=(), reply=None, level=6.0):
        self.writes, self.queries, self.sequence = [], [], []
        self.level = level
        self.refuses = set(refuses)
        self.reply = reply

    def write(self, command):
        self.writes.append(command)
        self.sequence.append(command)
        if command.startswith(":POW "):
            value = float(command.split()[1])
            if value not in self.refuses:
                self.level = value
        # The carrier, the LFO and the RF are read back too (2026-10-18):
        # the generator takes every one of them.
        elif command.startswith("FREQ "):
            self.carrier_hz = float(command[5:-3]) * 1e9
        elif command.startswith("LFO:FREQ "):
            self.lfo_hz = float(command.split()[1])
        elif command in ("OUTPUT ON", "OUTPUT OFF"):
            self.rf_on = command == "OUTPUT ON"

    def query(self, command):
        self.queries.append(command)
        self.sequence.append(command)
        if command == "FREQ?":
            return f"{self.carrier_hz:.12g}"
        if command == "LFO:FREQ?":
            return f"{self.lfo_hz:.12g}"
        if command == "OUTP?":
            return "1" if getattr(self, "rf_on", False) else "0"
        if command != ":POW?":
            raise AssertionError(f"unexpected query {command}")
        if self.reply is not None:
            return self.reply
        return f"{self.level:g}"


class PowerListTests(NoWindows):
    def test_the_default_is_12_dbm_alone(self):
        # The user's screenshot (2026-10-17): P 12 to 12 dBm, P step 3 dB;
        # current -6 to 6 A in 0.02 A (6 to 18 dBm and -2 to 2 A before).
        self.assertEqual(app.STFMR_LOCKIN_DEFAULT_POWER_DBM, 12.0)
        self.assertEqual(app.STFMR_LOCKIN_DEFAULT_POWER_STOP_DBM, 12.0)
        self.assertEqual(app.STFMR_LOCKIN_DEFAULT_POWER_STEP_DB, 3.0)
        self.assertEqual(levels(app.STFMR_LOCKIN_DEFAULT_POWER_DBM, app.STFMR_LOCKIN_DEFAULT_POWER_STOP_DBM,
                                app.STFMR_LOCKIN_DEFAULT_POWER_STEP_DB), (12.0,))
        self.assertEqual((app.STFMR_LOCKIN_DEFAULT_START_A, app.STFMR_LOCKIN_DEFAULT_STOP_A,
                          app.STFMR_LOCKIN_DEFAULT_STEP_A), (-6.0, 6.0, 0.02))
        with open(app.__file__, encoding="utf-8") as source:
            text = source.read()
        for name, constant in (("power_start", "STFMR_LOCKIN_DEFAULT_POWER_DBM"),
                               ("power_stop", "STFMR_LOCKIN_DEFAULT_POWER_STOP_DBM"),
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
                        ValueError, r"^P start dBm, P stop dBm and P step dB must be finite numbers$"):
                    levels(start, stop, step)
        for start, stop in (("10", "14"), ("12", "12")):    # as f step: refused even for one value
            with self.subTest(start=start, stop=stop):
                with self.assertRaisesRegex(ValueError, r"^P step dB must be greater than 0 dB$"):
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


class PowerResolutionTests(NoWindows):
    """Review, 2026-10-14: the SMB100A sets its level in 0.01 dB steps, so
    powers between them gave identical passes under different labels.  Each
    power is rounded to 0.01 dB (halves away from zero) and a repeat of the
    power before it is dropped; the confirmation says so."""

    def test_powers_are_rounded_to_0_01_db_and_repeats_merged(self):
        self.assertEqual(app.STFMR_SMB100A_POWER_RESOLUTION_DB, 0.01)
        # Eleven asked, six levels the generator can set.
        self.assertEqual(levels("0", "0.05", "0.005"), (0.0, 0.01, 0.02, 0.03, 0.04, 0.05))
        self.assertEqual(levels("0.05", "0", "0.005"), (0.05, 0.04, 0.03, 0.02, 0.01, 0.0), "descending")
        # A step of at least 0.01 dB off the grid: rounded, nothing merged.
        self.assertEqual(levels("0", "0.1", "0.015"), (0.0, 0.02, 0.03, 0.05, 0.06, 0.08, 0.09, 0.1))
        # The shorter last step lands on the grid point before it: merged.
        self.assertEqual(levels("0", "0.104", "0.01")[-2:], (0.09, 0.1))
        # A third decimal in P start or P stop, and the one-power case.
        self.assertEqual(levels("12.005", "12.005", "1"), (12.01,))
        self.assertEqual(levels("12", "12.004", "0.001"), (12.0,), "all five round to 12")
        self.assertEqual(levels("-12.345", "-12.345", "1"), (-12.35,))
        self.assertEqual(levels("-0.004", "0.02", "0.012"), (0.0, 0.01, 0.02))
        self.assertEqual(f"{levels('-0.004', '-0.004', '1')[0]:g}", "0", "not -0")
        # Already on the grid: unchanged (no float dust either).
        self.assertEqual(levels("0", "1", "0.1"), tuple(n / 10 for n in range(11)))
        self.assertEqual(levels("10", "-10", "2.5"), tuple(10 - 2.5 * n for n in range(9)))
        for value in levels("-30", "25", "0.37"):
            with self.subTest(value=value):
                self.assertEqual(value, round(value, 2))
                self.assertLessEqual(value, 25.0, "rounding never passes the 25 dBm limit")
        self.assertEqual(levels("24.996", "24.996", "1"), (25.0,))
        self.assertEqual(app.stfmr_power_on_grid(0.015), 0.02, "half away from zero, not round()'s 0.01")
        self.assertEqual(app.stfmr_power_on_grid(-0.015), -0.02)

    def test_the_cap_counts_the_powers_asked_for(self):
        # 501 asked, 51 levels: within the cap, merged.
        self.assertEqual(len(levels("0", "0.5", "0.001")), 51)
        # A slipped digit (0.001 for 1): 5001 asked would round to 501
        # levels, within the cap, but it is still refused rather than run.
        with self.assertRaisesRegex(ValueError, r"gives more than 1000 powers; a sweep takes at most 1000"):
            levels("0", "5", "0.001")

    def test_the_confirmation_says_when_powers_were_rounded_or_merged(self):
        summary = app.stfmr_power_summary
        self.assertEqual(
            summary(levels("0", "0.05", "0.005"), "0", "0.05", "0.005"),
            "Power dBm: 0, 0.01, 0.02, 0.03, 0.04, 0.05 (6 powers from 0 to 0.05 in 0.005 dB steps; "
            "rounded to the SMB100A's 0.01 dB steps, 5 repeated powers merged); the whole f × A sweep "
            "runs at each, in this order, with the RF on between them")
        self.assertEqual(
            summary(levels("0", "0.1", "0.015"), "0", "0.1", "0.015"),
            "Power dBm: 0, 0.02, 0.03, 0.05, 0.06, 0.08, 0.09, 0.1 (8 powers from 0 to 0.1 in 0.015 dB "
            "steps; rounded to the SMB100A's 0.01 dB steps); the whole f × A sweep runs at each, in this "
            "order, with the RF on between them")
        self.assertEqual(summary(levels("12.005", "12.005", "1"), "12.005", "12.005", "1"),
                         "Power dBm: 12.01 (from 12.005, rounded to the SMB100A's 0.01 dB steps)")
        self.assertEqual(summary(levels("12", "12.004", "0.001"), "12", "12.004", "0.001"),
                         "Power dBm: 12 (12 to 12.004 in 0.001 dB steps, rounded to the SMB100A's 0.01 dB "
                         "steps, 4 repeated powers merged)")
        self.assertIn("(2 powers from 0 to 0.01 in 0.005 dB steps; rounded to the SMB100A's 0.01 dB steps, "
                      "1 repeated power merged)", summary(levels("0", "0.01", "0.005"), "0", "0.01", "0.005"))
        # On the grid: no note, as before.
        self.assertEqual(summary([12.0], "12", "12", "1"), "Power dBm: 12")
        self.assertNotIn("rounded", summary(levels("10", "14", "2"), "10", "14", "2"))


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
        fake.queries.clear()
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

    def test_configure_reads_the_first_power_back_before_the_rf_is_switched_on(self):
        """Review, 2026-10-14: the first power was set by configure() and
        never read back, so a refused level left the RF on at the previous
        level under the new label.  The commands and their order are the
        LabVIEW trace's as before, with ':POW?' right after ':POW'."""
        controller, fake = self.controller()
        self.assertEqual(controller.configure(5.0, 12.0, 117.3), 12.0, "returns the read-back")
        # The LFO and the carrier are read back too, also before the RF is
        # switched on (review, 2026-10-18).
        self.assertEqual(fake.sequence, [":AM:DEPT:LIN 1.0E+2", "LFO:VOLT 0.50 V", "LFO:FREQ 117.3 Hz", "LFO:FREQ?",
                                         ":POW 12", ":POW?", "FREQ 5GHz", "FREQ?", "LFO ON", "OUTPUT ON",
                                         "AM:STAT ON"])
        self.assertEqual(fake.writes, [":AM:DEPT:LIN 1.0E+2", "LFO:VOLT 0.50 V", "LFO:FREQ 117.3 Hz",
                                       ":POW 12", "FREQ 5GHz", "LFO ON", "OUTPUT ON", "AM:STAT ON"],
                         "every write and its order as before")

    def test_configure_refuses_a_level_the_generator_did_not_take_with_nothing_switched_on(self):
        for options, reply_text in (({"refuses": {12.0}}, "'6'"),       # still at the last level
                                    ({"reply": "13.5"}, "'13.5'"),       # a front-panel :POW:LIM, say
                                    ({"reply": ""}, "''"), ({"reply": "abc"}, "'abc'"),
                                    ({"reply": "nan"}, "'nan'")):
            with self.subTest(options=options):
                controller, fake = self.controller(**options)
                with self.assertRaisesRegex(RuntimeError, r"^SMB100A did not take the new power: sent ':POW 12', "
                                                          rf":POW\? read back {re.escape(reply_text)}"):
                    controller.configure(5.0, 12.0, 117.3)
                self.assertEqual(fake.sequence, [":AM:DEPT:LIN 1.0E+2", "LFO:VOLT 0.50 V", "LFO:FREQ 117.3 Hz",
                                                 "LFO:FREQ?", ":POW 12", ":POW?"],
                                 "no carrier, no LFO ON, no OUTPUT ON, no AM:STAT ON")
        # Within 0.01 dB, the same tolerance as set_power().
        controller, fake = self.controller(reply="12.01")
        self.assertAlmostEqual(controller.configure(5.0, 12.0, 117.3), 12.01)
        self.assertIn("OUTPUT ON", fake.writes)

    def test_configure_and_set_power_share_one_read_back_check(self):
        for reply, accepted in (("12", True), ("12.01", True), ("11.99", True), ("1.2E+01", True),
                                ("12.02", False), ("11.98", False), ("", False), ("abc", False)):
            for name, call in (("configure", lambda c: c.configure(5.0, 12.0, 117.3)),
                               ("set_power", lambda c: c.set_power(12.0))):
                with self.subTest(reply=reply, call=name):
                    controller, _fake = self.controller(reply=reply)
                    if accepted:
                        self.assertAlmostEqual(call(controller), float(reply))
                    else:
                        with self.assertRaisesRegex(RuntimeError, "did not take the new power"):
                            call(controller)

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
    """A sweep on the shared harness, with recording devices; Settle s 0.05,
    below 1 x the 100 ms SR830 TC so the TC sets each settle (user,
    2026-10-14: 1 x TC; Settle s was 0.5 under the 7 x TC rule), and the
    harness's Acquire s 0.2 (three readings a point)."""

    def sweep(self, powers=("10", "14", "2"), mod=("117.3", "117.3"), meter="SR830", stop="6",
              end_current="0.5", tc="100 ms", fail=None, smb=None, start_only=False,
              smb_type=PowerRecording, mfli_type=PowerRecording):
        h = harness(self.tmp.name, meter=meter, stop=stop, end_current=end_current)
        h.root = TimedRoot()
        h.stfmr_lockin_status_var = History()
        h.stfmr_lockin_settle_var.set("0.05")
        h.stfmr_lockin_sr830_time_constant_var = Variable(tc)
        h.stfmr_lockin_mod_start_var.set(mod[0])
        h.stfmr_lockin_mod_end_var.set(mod[1])
        h.stfmr_lockin_power_start_var.set(powers[0])
        h.stfmr_lockin_power_stop_var.set(powers[1])
        h.stfmr_lockin_power_step_var.set(powers[2])
        h.log = []
        h.stfmr_lockin_controllers = {
            key: PowerRecording(h, key, h.log) for key in ("kepco", "gauss", "830")}
        h.stfmr_lockin_controllers["smb"] = smb if smb is not None else smb_type(h, "smb", h.log)
        h.stfmr_lockin_mfli_controller = mfli_type(h, "mfli", h.log)
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
        # The first power is read back by configure() and every changed one
        # by set_power(); a carrier change sends no level (review,
        # 2026-10-14: the first power was not read back).
        self.assertEqual([(e["power_dbm"], e.get("power_readback_dbm")) for e in ready],
                         [(10.0, 10.0), (10.0, None), (12.0, 12.0), (12.0, None), (14.0, 14.0), (14.0, None)])
        self.assertEqual(["power_readback_dbm" in e for e in ready], [True, False] * 3)
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
        # stage_angle_deg since 2026-10-17, then magnet_supply and
        # magnet_measured_current_a last since 2026-10-21 (user).
        self.assertEqual(len(header), 37)
        self.assertEqual(header.index("microwave_power_dbm"), 15)

    def test_the_confirmation_lists_the_powers_and_the_estimate_counts_them(self):
        h, recorder = self.sweep()
        text = self.message()
        self.assertIn("Microwave: 5 → 6 GHz, 2 frequencies\nMod. Hz: 117.3\n"
                      "Power dBm: 10, 12, 14 (3 powers from 10 to 14 in 2 dB steps); the whole f × A "
                      "sweep runs at each, in this order, with the RF on between them\n", text)
        self.assertIn("about 12 points saved", text)
        # 3 powers x 2 carriers = 6 passes of 4 readings; all but each pass's
        # first settle max(Settle s 0.05, 1 x 100 ms) = 0.1 s, and each of
        # the 12 saved points averages 0.2 s.
        self.assertIn("Estimated at least 4.2 s (18 settles of 0.1 s + 12 averages of 0.2 s).", text)
        self.assertEqual(h.root.reads, [1, 100, 100, 100] * 6)
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
        # The point count first, so it stays within the 52-character label
        # (review, 2026-10-14).
        # The outputs and the count first, within the label; what was
        # swept may run past it (user, 2026-10-18).
        self.assertEqual(statuses[-1], "Completed; 4886 and RF zeroed; 12 points saved; 2 frequencies, 3 powers")
        self.assertLessEqual(width_in_zeros(statuses[-1].split("; 2 frequencies")[0]), 52)
        # The 52-character status label, with long labels and hours left
        # (Arial 15 px, a little wider than the lab's YaHei UI).
        long_run = [
            "10.5 GHz/-12.5 dBm; 1234/4800; I=-2.92 A; ~10.5 h left",
            "10.25 GHz/-12.5 dBm; 12345/48000; I=-2.925 A; ~10.5 h left",
            "-12.5 dBm done; zeroing; RF on, to -10.5 dBm at 10.25 GHz",
            "10 GHz/12.5 dBm: I=-2.92 A sent; pre-read now",
            "10 GHz/12.5 dBm done; zeroing; to 11 GHz",
            # Modulation and power both swept: no "I=" and no " left"
            # (review, 2026-10-14).
            "10 GHz/313 Hz/12.5 dBm; 1234/4800; -2.92 A; ~10.5 h",
            "10.25 GHz/1013 Hz/-12.5 dBm; 12345/48000; -2.925 A; ~10.5 h",
            "10.25 GHz/1013 Hz/-12.5 dBm: I=-2.925 A sent; pre-read now",
            "10.25 GHz/1013 Hz/-12.5 dBm done; zeroing; to 10.5 GHz",
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
        # One carrier and one modulation frequency: only the power is named
        # (review, 2026-10-14; it was "5 GHz, 10 dBm").
        self.assertEqual(labels, {"8_pow_10_freq_5_loop_1": "10 dBm",
                                  "8_pow_12_freq_5_loop_1": "12 dBm",
                                  "8_pow_14_freq_5_loop_1": "14 dBm"})
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
        # One carrier: not named (review, 2026-10-14).
        self.assertEqual(labels["mfli_mod_311_pow_-12.5_freq_5_loop_1"], "311 Hz, -12.5 dBm")
        self.assertEqual(labels["mfli_mod_307_pow_0.25_freq_5_loop_1"], "307 Hz, 0.25 dBm")
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
        self.assertIn("Estimated at least 2.8 s (12 settles of 0.1 s + 8 averages of 0.2 s).", text)
        statuses = h.stfmr_lockin_status_var.history
        self.assertIn("Mod. 307 Hz done; zeroing; LFO to 311 Hz at 5 GHz", statuses)
        self.assertIn("5 GHz/311 Hz/10 dBm: I=-1 A sent; pre-read now", statuses)
        # Both swept: the scanning lines drop "I=" and " left" (review,
        # 2026-10-14).
        scanning = [s for s in statuses if re.search(r"; \d+/16; ", s)]
        self.assertEqual(len(scanning), 8)
        for status in scanning:
            self.assertRegex(status, r"^5 GHz/3(07|11) Hz/1[02] dBm; \d+/16; [-+][0-9.]+ A; "
                                     r"~(\d+ s|\d+ min|\d+\.\d h)$")
            self.assertLessEqual(width_in_zeros(status), 52)
        self.assertEqual(statuses[-1], "Completed; 4886 and RF zeroed; 8 points saved; 1 frequency, "
                                       "2 modulation frequencies, 2 powers")
        series = h._stfmr_lockin_field_component_series(
            h.stfmr_lockin_rows, "SR830", "8", "x_v", group_frequency=True)
        self.assertEqual(set(series), {f"8_mod_{m}_pow_{p}_freq_5_loop_1" for m in (307, 311) for p in (10, 12)})
        labels, _colors = App._stfmr_lockin_frequency_series_styles(series, "x_v")
        self.assertEqual(labels["8_mod_311_pow_12_freq_5_loop_1"], "311 Hz, 12 dBm")

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

    def test_stop_during_a_power_change_never_switches_the_4886_back_on(self):
        """Review, 2026-10-14: Stop pressed while the worker changes the
        level (here set_power sets the stop event, as Stop does while a
        worker call is in flight).  The 4886, already zeroed for the change,
        is not switched back on; the sweep ends with the zero and the RF
        off, and the run log says "stopped"."""
        class StopDuringPower(PowerRecording):
            def set_power(self, dbm):
                result = super().set_power(dbm)
                self.harness.stfmr_lockin_stop_event.set()
                return result

        h, recorder = self.sweep(smb_type=StopDuringPower)
        self.error.assert_not_called()
        self.assertFalse(h.stfmr_lockin_running)
        at = h.log.index(("smb", "power", 12.0))
        self.assertEqual(h.log[at - 1], ("kepco", "zero"), "zeroed for the change")
        self.assertEqual([entry for entry in h.log[at:] if entry[:2] == ("kepco", "output")], [],
                         "no 4886 output after the power command")
        self.assertEqual(h.log[-2:], [("kepco", "zero"), ("smb", "rf_off")])
        self.assertNotIn(("smb", "power", 14.0), h.log, "no further power")
        self.assertEqual({float(r["microwave_power_dbm"]) for r in read_csv(recorder.csv_path)}, {10.0})
        history = events(recorder)
        self.assertEqual((history[-1]["outcome"], history[-1]["outputs_off"]), ("stopped", True))
        self.assertEqual(h.stfmr_lockin_status_var.history[-1], "Stopped; 4886 and RF zeroed; 4 points saved")

    def test_the_mfli_first_point_range_check_runs_again_after_each_power_change(self):
        """Review, 2026-10-14: after a power change the MFLI's first reading
        is again read with initial=True (its first-point range check), as at
        a new carrier; the stand-in records each read_stfmr_mfli call."""
        class RecordingMfli(PowerRecording):
            def __init__(self, *args):
                super().__init__(*args)
                self.initial_reads = []

            def read_stfmr_mfli(self, expected, **kwargs):
                h = self.harness
                self.initial_reads.append(
                    (h.stfmr_lockin_power_dbm, h.stfmr_lockin_frequency_index, kwargs.get("initial", False)))
                return super().read_stfmr_mfli(expected, **kwargs)

        h, recorder = self.sweep(meter="MFLI", stop="5", mfli_type=RecordingMfli)
        self.error.assert_not_called()
        reads = h.stfmr_lockin_mfli_controller.initial_reads
        self.assertEqual([power for power, _f, _initial in reads if _initial], [10.0, 12.0, 14.0],
                         "one first-point check per power pass")
        for power in (10.0, 12.0, 14.0):
            with self.subTest(power=power):
                in_pass = [initial for dbm, _f, initial in reads if dbm == power]
                self.assertTrue(in_pass[0], "the first reading of the pass")
                self.assertFalse(any(in_pass[1:]), "only the first")
        # The stand-in MFLI ranges on that check, so the first point of each
        # pass is read again (logged), as after a carrier change.
        retries = [e for e in events(recorder) if e["event"] == "mfli_autorange_retry"]
        self.assertEqual([e["action"] for e in retries], ["RANGE 0.03->0.1 V (STFMR frequency start)"] * 3)
        self.assertEqual({float(r["microwave_power_dbm"]) for r in read_csv(recorder.csv_path)}, {10.0, 12.0, 14.0})


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
        # The first power is read back too, right after it is sent and
        # before the RF is switched on (review, 2026-10-14).
        # The LFO and every carrier are read back too, and the RF off at the
        # end (review and user, 2026-10-18).
        self.assertEqual(smb.instrument.queries, ["LFO:FREQ?", ":POW?", "FREQ?", "FREQ?", ":POW?", "FREQ?",
                                                  "FREQ?", ":POW?", "FREQ?", "FREQ?", "OUTP?"])
        sequence = smb.instrument.sequence
        self.assertEqual(sequence[4:8], [":POW 10", ":POW?", "FREQ 5GHz", "FREQ?"])
        self.assertLess(sequence.index(":POW?"), sequence.index("OUTPUT ON"))
        for level in (":POW 12", ":POW 14"):
            self.assertEqual(sequence[sequence.index(level) + 1], ":POW?")
        self.assertEqual(writes.count("OUTPUT ON"), 1)
        self.assertEqual(len(read_csv(recorder.csv_path)), 12)
        history = events(recorder)
        self.assertEqual(history[-1]["outcome"], "completed")
        self.assertEqual([e["power_readback_dbm"] for e in history
                          if e["event"] == "frequency_ready" and "power_readback_dbm" in e], [10.0, 12.0, 14.0])

    def test_a_first_power_the_generator_refused_ends_the_sweep_with_nothing_switched_on(self):
        """Review, 2026-10-14: configure() reads the first power back before
        "LFO ON" / "OUTPUT ON"; a refused level (a typo, a front-panel
        :POW:LIM, below the unit's minimum) ends the sweep through the usual
        path with the RF never switched on and the 4886 never on."""
        smb = self.real(refuses={10.0})
        h, recorder = self.sweep(smb=smb)
        self.error.assert_called_once()
        self.assertIn("SMB100A did not take the new power: sent ':POW 10', :POW? read back '6'",
                      self.error.call_args.args[1])
        self.assertEqual(smb.instrument.sequence, [
            ":AM:DEPT:LIN 1.0E+2", "LFO:VOLT 0.50 V", "LFO:FREQ 117.3 Hz", "LFO:FREQ?", ":POW 10", ":POW?",
            "AM:STAT OFF", "LFO OFF", "OUTPUT OFF", "OUTP?"], "nothing switched on, then the usual output-off")
        self.assertEqual([c for c in h.stfmr_lockin_controllers["kepco"].calls if c[0] == "output"], [],
                         "the 4886 never switched on")
        self.assertEqual(h.stfmr_lockin_controllers["830"].calls, [], "the lock-in not set up either")
        self.assertEqual(read_csv(recorder.csv_path), [])
        history = events(recorder)
        self.assertEqual((history[-1]["outcome"], history[-1]["outputs_off"]), ("error", True))
        self.assertFalse([e for e in history if e["event"] == "frequency_ready"])
        self.assertFalse(h.stfmr_lockin_running)

    def test_a_level_the_generator_ignored_stops_the_sweep_with_the_outputs_off(self):
        smb = self.real(refuses={14.0})
        h, recorder = self.sweep(smb=smb)
        self.error.assert_called_once()
        self.assertIn("SMB100A did not take the new power: sent ':POW 14', :POW? read back '12'",
                      self.error.call_args.args[1])
        writes = smb.instrument.writes
        self.assertEqual(writes[writes.index(":POW 14"):], [":POW 14", "AM:STAT OFF", "LFO OFF", "OUTPUT OFF"],
                         "no carrier change, the RF off")
        self.assertEqual([q for q in smb.instrument.queries if q == ":POW?"], [":POW?"] * 3,
                         "the first power, 12 and the refused 14")
        self.assertEqual(smb.instrument.queries[-1], "OUTP?", "the RF read back off (2026-10-18)")
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
        self.assertEqual(statuses[-1], "Completed; 4886 and RF zeroed; 6 points saved; 2 frequencies")
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
                (("abc", "12", "1"), "P start dBm, P stop dBm and P step dB must be finite numbers"),
                (("12", "", "1"), "P start dBm, P stop dBm and P step dB must be finite numbers"),
                (("nan", "12", "1"), "P start dBm, P stop dBm and P step dB must be finite numbers"),
                (("10", "14", "0"), "P step dB must be greater than 0 dB"),
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


class RoundedPowerSweepTests(PowerSweepCase):
    def test_a_sweep_sends_and_labels_the_rounded_powers(self):
        """Review, 2026-10-14: P start 10, P stop 10.01, P step 0.004 asks
        for 10, 10.004, 10.008 and 10.01, which round to 10, 10, 10.01 and
        10.01: two passes, sent as ':POW 10' and ':POW 10.01', read back,
        and saved under those labels."""
        smb = app.STFMRSMB100AController(lambda _message: None)
        smb.instrument = FakeSmb100a()
        h, recorder = self.sweep(powers=("10", "10.01", "0.004"), stop="5", smb=smb)
        self.error.assert_not_called()
        self.assertEqual([w for w in smb.instrument.writes if w.startswith(":POW")], [":POW 10", ":POW 10.01"])
        self.assertEqual({float(r["microwave_power_dbm"]) for r in read_csv(recorder.csv_path)}, {10.0, 10.01})
        self.assertIn("Power dBm: 10, 10.01 (2 powers from 10 to 10.01 in 0.004 dB steps; rounded to the "
                      "SMB100A's 0.01 dB steps, 2 repeated powers merged)", self.message())
        self.assertEqual(events(recorder)[0]["settings"]["powers_dbm"], [10.0, 10.01])


def plot_rows(carriers=(5.0,), powers=(10.0,)):
    """Saved SR830 rows: every power at every carrier, three fields."""
    return [{"meter": "SR830", "loop": 1, "microwave_frequency_ghz": carrier, "microwave_power_dbm": power,
             "modulation_frequency_hz": 117.3, "field_g": field, "current_a": field / 300.0,
             "x_v": 1e-6 * (field + power), "y_v": 2e-6, "phase_deg": 10.0}
            for power in powers for carrier in carriers for field in (-300.0, 0.0, 300.0)]


class CompactPlotTests(NoWindows):
    """Review, 2026-10-14: at 1101 x 1300 px (the CI screenshots) each
    STFMR plot canvas is about 105 px tall, and _draw_nonlocal_series drew
    an unflagged canvas as if it were at least 120 px tall, so the x-axis
    title and tick labels were cut.  The four canvases are now drawn
    compact, at their real height, as the PPMS and RT-E plots are."""

    def canvas(self, height, width=520):
        canvas = mock.Mock()
        app.CombinedControlApp._stfmr_lockin_compact_canvas(canvas)
        canvas.winfo_width.return_value = width
        canvas.winfo_height.return_value = height
        return canvas

    def draw(self, canvas, rows):
        a = object.__new__(App)
        a._draw_plot_grid = mock.Mock()
        a._draw_stfmr_lockin_frequency_component(canvas, rows, "SR830", "8", "x_v", "SR830 X",
                                                 "Waiting for SR830 X data", auto_voltage_unit=True)

    def test_the_page_flags_its_four_plots_compact(self):
        a = build_stfmr_page().app
        for name in ("stfmr_lockin_x_canvas", "stfmr_lockin_y_canvas", "stfmr_lockin_theta_canvas",
                     "stfmr_lockin_canvas"):
            with self.subTest(canvas=name):
                canvas = getattr(a, name)
                self.assertIs(canvas.ppms_compact_plot, True)
                self.assertIs(canvas.compact_legend_strip, True)

    def test_titles_and_axes_fit_the_real_canvas_height(self):
        for height in (60, 80, 105, 119, 145):
            for rows, legend in ((plot_rows(), "5 GHz"), (plot_rows(powers=(10.0, 12.0, 14.0)), "10 dBm"),
                                 (plot_rows(carriers=(5.0, 6.0)), "5 GHz")):
                with self.subTest(height=height, legend=legend):
                    canvas = self.canvas(height)
                    self.draw(canvas, rows)
                    top, bottom = canvas.create_rectangle.call_args.args[1::2]
                    # The legend strip above the plot even for one entry
                    # ("5 GHz" alone would run into a box starting at 14),
                    # 44 px below it for the tick labels and the title (a
                    # sliver of a plot under about 75 px, but nothing cut).
                    self.assertEqual((top, bottom), (26, max(27, height - 44)))
                    texts = canvas.create_text.call_args_list
                    for call in texts:
                        self.assertGreaterEqual(call.args[1], 0)
                        self.assertLess(call.args[1], height, call.kwargs.get("text"))
                    title = [c for c in texts if c.kwargs.get("text") == "Magnetic field (G)"]
                    self.assertEqual(title[0].args[1], height - 10, "the x-axis title inside the canvas")
                    self.assertIn(legend, [c.kwargs.get("text") for c in texts])

    def test_an_unflagged_canvas_still_draws_against_120_px(self):
        """What the STFMR plots did before: the title at 110 px on a 105 px
        canvas, cut off."""
        canvas = self.canvas(105)
        canvas.ppms_compact_plot = False
        canvas.compact_legend_strip = False
        self.draw(canvas, plot_rows())
        title = [c for c in canvas.create_text.call_args_list if c.kwargs.get("text") == "Magnetic field (G)"]
        self.assertEqual(title[0].args[1], 110)

    def test_too_short_a_canvas_asks_for_a_taller_window(self):
        canvas = self.canvas(54)
        self.draw(canvas, plot_rows())
        self.assertEqual([c.kwargs.get("text") for c in canvas.create_text.call_args_list],
                         ["Increase the window height"])

    def test_the_field_at_0_a_stays_on_a_short_canvas(self):
        """The 'Field at 0 A' value is placed against the real canvas: on a
        105 px canvas its bottom spots were at 110 px (the 120 px minimum
        less 10), below the canvas, where nothing overlapped them, so the
        value was placed out of sight."""
        class Frame:
            def __init__(self):
                self.text = "Measured field vs 4886 current (field sweep only)"

            def winfo_class(self):
                return "TLabelframe"

            def cget(self, _name):
                return self.text

            def configure(self, text):
                self.text = text

        class ShortCanvas(app.tk.Canvas):
            """Without Tk: the top strip is taken (by the legend, say), so
            the value must go to a bottom corner."""

            def __init__(self, height, compact=True):
                self.master, self.height, self.items = Frame(), height, {}
                if compact:
                    app.CombinedControlApp._stfmr_lockin_compact_canvas(self)

            def winfo_width(self):
                return 520

            def winfo_height(self):
                return self.height

            def create_text(self, x, y, **options):
                self.items[1] = dict(options, x=x, y=y)
                return 1

            def coords(self, _item, x, y):
                self.items[1].update(x=x, y=y)

            def itemconfigure(self, _item, **options):
                self.items[1].update(options)

            def bbox(self, _item):
                item = self.items[1]
                return (item["x"] - 100, item["y"] - 7, item["x"], item["y"] + 7)

            def find_overlapping(self, x0, y0, x1, y1):
                return (1, 2) if y0 < 20 else (1,)

            def delete(self, _item):
                self.items.pop(1, None)

        series = {"8_freq_5_loop_1": [(-1.0, -290.0), (1.0, 310.0)]}
        a = object.__new__(App)
        for height in (60, 105, 145):
            with self.subTest(height=height):
                canvas = ShortCanvas(height)
                a._label_field_at_zero_current(canvas, series)
                self.assertEqual(canvas.items[1]["text"], "Field at 0 A: +10.0 G")
                self.assertEqual(canvas.items[1]["y"], height - 10, "inside the canvas")
                self.assertEqual(canvas.master.text, "Measured field vs 4886 current (field sweep only)")
        # Too short to draw: the value goes in the plot's title bar.
        canvas = ShortCanvas(50)
        a._label_field_at_zero_current(canvas, series)
        self.assertEqual(canvas.items, {})
        self.assertEqual(canvas.master.text, "Measured field vs 4886 current (field sweep only); "
                                             "field at 0 A: +10.0 G")
        # Unflagged, as before: against 120 px, so at 110 px.
        canvas = ShortCanvas(105, compact=False)
        a._label_field_at_zero_current(canvas, series)
        self.assertEqual(canvas.items[1]["y"], 110)


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
        # "P step dB:": a level difference (review, 2026-10-14).
        self.assertEqual([w.options.get("text", "") for w in power[0::2]],
                         ["P start dBm:", "P stop dBm:", "P step dB:"])
        for field, variable, entry in zip(
                power[1::2],
                (a.stfmr_lockin_power_start_var, a.stfmr_lockin_power_stop_var, a.stfmr_lockin_power_step_var),
                (a.stfmr_lockin_power_start_entry, a.stfmr_lockin_power_stop_entry,
                 a.stfmr_lockin_power_step_entry)):
            self.assertEqual((field.kind, field.options["width"], field.grid_options["sticky"]), ("Entry", 8, "ew"))
            self.assertIs(field.options["textvariable"], variable)
            self.assertIs(field, entry, "kept, so a sweep can lock it")
        everything = [str(w.options.get("text", "")) for row in range(8) for w in page.in_row(table, row)]
        self.assertNotIn("Power dBm:", everything, "the single box is gone")
        # Seven since the angle row (user, 2026-10-17).
        self.assertEqual(page.in_row(table, 7), [], "seven rows")
        for row in range(7):
            self.assertLessEqual(max(w.grid_options["column"] for w in page.in_row(table, row)), 9,
                                 "at most five pairs a row")

    def test_the_power_labels_are_no_wider_than_the_columns_they_join(self):
        """The 1101 px window has little room left on Windows, so each power
        label is no wider than the widest label already in its column, and
        "P step dB:" (review, 2026-10-14; "P step dBm:" was a few px wider
        than "f step GHz:") is no wider either; the column 6 label "Power
        dBm:" is gone.  Arial 15 px stands in for the lab's YaHei UI."""
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
        self.assertLessEqual(width("P step dB:"), width("f step GHz:"))
        widest_before = {4: max(map(width, ("f step GHz:", "Step A:", "Settle s:", "SR830 TC:"))),
                         6: max(map(width, ("Power dBm:", "Loop:", "Acquire s:")))}
        widest_now = {4: max(map(width, ("f step GHz:", "P step dB:", "Step A:", "Settle s:", "SR830 TC:"))),
                      6: max(map(width, ("Loop:", "Acquire s:")))}
        self.assertLess(sum(widest_now.values()), sum(widest_before.values()), "the table is not wider")


if __name__ == "__main__":
    unittest.main()
