"""Every lock-in time constant the program starts with is 1 s.

User, 2026-10-14: "all default lock-in time constant change to 1s pls."
Before, three boxes started at 0.1 s (STFMR "MFLI TC s", the Non-local rack's
Zurich "TC (s)", the PPMS / RT-E Zurich "Time constant s" / "TC (s)") and
STFMR's "SR830 TC" at 100 ms.  Only the defaults changed: every wait computed
from a time constant keeps its rule, so the waits that follow from the new
defaults are checked here too, as the README lists them.

No Tk window and no instrument: the boxes are checked in the source (as
test_stfmr_mod_prime_range checks Mod. start / end) and, for the PPMS / RT-E
page, in the real panel constructor on stand-in widgets (test_ppms_two_panels'
make_panel).  Creating a Tk root fails the test."""
import inspect
import math
import re
import sys
import threading
import tkinter
import unittest
from pathlib import Path
from unittest import mock

_HERE = Path(__file__).resolve().parent
for _path in (_HERE, _HERE.parent):
    if str(_path) not in sys.path:
        sys.path.insert(0, str(_path))

import Transport_all_NH_dev as app  # noqa: E402
import test_ppms_two_panels as two_panels  # noqa: E402  (its own copy of the program)


class NoWindows(unittest.TestCase):
    def setUp(self):
        patcher = mock.patch("tkinter.Tk", side_effect=AssertionError("a test tried to open a Tk window"))
        patcher.start()
        self.addCleanup(patcher.stop)


class DefaultsAreOneSecond(NoWindows):
    def test_every_default_constant_is_1_s(self):
        for name in ("STFMR_LOCKIN_MFLI_TIME_CONSTANT_S",      # STFMR "MFLI TC s" (was 0.1)
                     "NONLOCAL_MFLI_TIME_CONSTANT_S",          # Non-local rack "TC (s)" (was 0.1)
                     "PPMS_ZURICH_TIME_CONSTANT_S",            # PPMS / RT-E Zurich TC (was 0.1)
                     "NONLOCAL_LOCKIN_TIME_CONSTANT_S"):       # configure_mfli's default (already 1)
            with self.subTest(name=name):
                self.assertEqual(getattr(app, name), 1.0)
        # STFMR "SR830 TC" (was 100 ms, OFLT 8): 1 s, OFLT 10.
        self.assertEqual(app.STFMR_LOCKIN_SR830_DEFAULT_TIME_CONSTANT, "1 s")
        self.assertEqual(app.STFMR_LOCKIN_SR830_TIME_CONSTANT_CODE, 10)
        self.assertEqual(
            app.STFMR_LOCKIN_SR830_TIME_CONSTANTS[app.STFMR_LOCKIN_SR830_DEFAULT_TIME_CONSTANT],
            app.STFMR_LOCKIN_SR830_TIME_CONSTANT_CODE)
        self.assertEqual(app.SR830_TIME_CONSTANTS_S[app.STFMR_LOCKIN_SR830_TIME_CONSTANT_CODE], 1.0)

    def test_the_methods_default_to_1_s(self):
        """What the program sends when the caller gives no time constant."""
        self.assertEqual(inspect.signature(app.STFMRSR830Controller.configure)
                         .parameters["time_constant_code"].default, 10)
        self.assertEqual(inspect.signature(app.NonLocalACController.configure_mfli)
                         .parameters["timeconstant_s"].default, 1.0)

    def test_the_stfmr_and_non_local_boxes_start_from_the_constants(self):
        with open(app.__file__, encoding="utf-8") as source:
            text = source.read()
        for variable, constant in (("stfmr_lockin_mfli_timeconstant_var", "STFMR_LOCKIN_MFLI_TIME_CONSTANT_S"),
                                   ("nonlocal_mfli_tc_var", "NONLOCAL_MFLI_TIME_CONSTANT_S")):
            with self.subTest(box=variable):
                self.assertRegex(text, rf"self\.{variable} = tk\.StringVar\(\s*value=f\"{{{constant}:g}}\"")
                self.assertEqual(f"{getattr(app, constant):g}", "1")
        self.assertRegex(text, r"self\.stfmr_lockin_sr830_time_constant_var = tk\.StringVar\(\s*"
                               r"value=STFMR_LOCKIN_SR830_DEFAULT_TIME_CONSTANT\s*\)")
        # The running sweep's values before any Start, and the fallbacks when
        # a lock-in is not used, are the same constants.
        for line in ("self.stfmr_lockin_mfli_timeconstant_s = STFMR_LOCKIN_MFLI_TIME_CONSTANT_S",
                     "self.stfmr_lockin_sr830_time_constant_code = STFMR_LOCKIN_SR830_TIME_CONSTANT_CODE",
                     "mfli_timeconstant_s = STFMR_LOCKIN_MFLI_TIME_CONSTANT_S",
                     "sr830_time_constant = STFMR_LOCKIN_SR830_DEFAULT_TIME_CONSTANT"):
            with self.subTest(line=line):
                self.assertRegex(text, re.compile(rf"^\s+{re.escape(line)}$", re.M))
        # No lock-in time constant box starts at 0.1 s any more.
        self.assertIsNone(re.search(r"(timeconstant|_tc)\w* = tk\.StringVar\(value=\"0?\.1\"\)", text))

    def test_the_ppms_and_rte_zurich_box_shows_1(self):
        """RTEPanel builds its Zurich row through PPMSPanel.__init__, so this
        is the RT-E "TC (s)" box too."""
        _parent, panel, controller = two_panels.TwoPanelTests.make_panel(None)
        self.assertEqual(panel.zurich_timeconstant.get(), "1")
        panel.zurich_harmonic.set("1+2+3")
        panel.connect_ppms2_zurich()
        self.assertEqual(controller.configure_mfli.call_args.kwargs["timeconstant_s"], 1.0)
        controller.configure_second_harmonic.assert_called_once_with(1.0)
        controller.configure_third_harmonic.assert_called_once_with(1.0)


class OneTimeConstantSettle(NoWindows):
    def test_every_page_waits_at_least_1_x_tc_as_the_user_chose(self):
        """User, 2026-10-14: with TC 1 s and Acquire s 2 s a STFMR point took
        9 s (7 x TC = 7 s settle, then 2 s).  The user asked "3-4s is not
        okay?", was shown that at 3-4 s a point a sweep reading lags by only
        about 0.2-0.5 of a field step, and answered "change all to 1*TC
        please".  So LOCKIN_SETTLE_TAUS is 1 on every page (STFMR, RT-E,
        PPMS, 6221-IV, Gate-VI), while the 10 x TC waits after large jumps
        and RT-E's range-step waits are unchanged."""
        self.assertEqual(app.LOCKIN_SETTLE_TAUS, 1.0)
        self.assertEqual(app.NONLOCAL_IV6221_FIRST_SETTLE_TAUS, 10.0)
        self.assertEqual(app.NONLOCAL_VI_HARMONIC_SETTLE_TAUS, 10.0)
        self.assertEqual(app.NONLOCAL_IV6221_RANGE_SETTLE_TAUS, {"sr830": 5.0, "mfli": 10.0})
        # A default STFMR point: 1 s settle + 2 s average = 3 s (9 s before).
        settle = app.CombinedControlApp._stfmr_lockin_effective_settle_s(
            "MFLI", app.STFMR_LOCKIN_DEFAULT_SETTLE_S, app.STFMR_LOCKIN_SR830_TIME_CONSTANT_CODE,
            app.STFMR_LOCKIN_MFLI_TIME_CONSTANT_S)
        self.assertEqual(settle + app.STFMR_LOCKIN_DEFAULT_ACQUIRE_S, 3.0)
        # The constant's comment gives the user's reason.
        with open(app.__file__, encoding="utf-8") as source:
            text = source.read()
        lines = text[:text.index("\nLOCKIN_SETTLE_TAUS = 1.0\n")].splitlines()
        comment = []
        while lines and lines[-1].startswith("#"):
            comment.insert(0, lines.pop())
        comment = " ".join(line.lstrip("# ") for line in comment)
        for words in ('"change all to 1*TC please"', "26 %", "0.5 step", "2026-10-14"):
            self.assertIn(words, comment)


class WaitsAtTheNewDefaults(NoWindows):
    """The rules are unchanged; these are their values at the 1 s defaults."""

    def test_stfmr_points(self):
        """Since 2026-10-14 a point settles max(Settle s, 1 x the TC of every
        lock-in read), the user's 1 x TC (7 x TC earlier that day); Wait/pt
        (0.5 s) and the SR830's 5 x TC are gone.  With Acquire s 2 s a
        default point takes 1 + 2 = 3 s (9 s at 7 x TC)."""
        settle = app.CombinedControlApp._stfmr_lockin_effective_settle_s
        settle_s, code = app.STFMR_LOCKIN_DEFAULT_SETTLE_S, app.STFMR_LOCKIN_SR830_TIME_CONSTANT_CODE
        mfli_tc = app.STFMR_LOCKIN_MFLI_TIME_CONSTANT_S
        self.assertEqual(settle("SR830", settle_s, code, mfli_tc), 1.0)        # max(1 s, 1 x 1 s); 5 s before
        self.assertEqual(settle("MFLI + SR830", settle_s, code, mfli_tc), 1.0)
        self.assertEqual(settle("MFLI", settle_s, code, mfli_tc), 1.0)         # Wait/pt (0.5 s) before
        self.assertEqual(settle("MFLI", settle_s, code, mfli_tc) + app.STFMR_LOCKIN_DEFAULT_ACQUIRE_S, 3.0)
        # An MFLI range change: max(the settle, 10 x MFLI TC, 0.2 s) = 10 s,
        # unchanged.
        self.assertEqual(max(1.0, 10.0 * mfli_tc, 0.2), 10.0)

    def test_non_local_6221_iv(self):
        tc = app.NONLOCAL_MFLI_TIME_CONSTANT_S
        self.assertEqual(app.NONLOCAL_IV6221_FIRST_SETTLE_TAUS * tc, 10.0)          # far jumps (was 1 s)
        self.assertEqual(app.NONLOCAL_IV6221_RANGE_SETTLE_TAUS["mfli"] * tc, 10.0)  # after a range step
        # Other points: max(Settle s, 1 x TC) = 1 s (Settle s 0.5 s alone
        # before, 7 x TC = 7 s earlier on 2026-10-14; user, 2026-10-14;
        # test_nonlocal_lockin_settle has the rest).
        self.assertEqual(app.CombinedControlApp._nonlocal_lockin_settle_s(app.NONLOCAL_IV6221_SETTLE_S, [tc]), 1.0)

    def test_ppms_zurich_range_wait_falls_back_to_1_s(self):
        """After a Zurich range change the PPMS / RT-E reading waits
        max(0.2 s, 10 x TC); with no configured TC, 10 x the 1 s default."""
        _parent, panel, controller = two_panels.TwoPanelTests.make_panel(None)
        controller.mfli_config = {}
        controller.read_stfmr_mfli.side_effect = [
            {"auto_range": "RANGE 0.01->0.1 V", "X": 999},
            {"auto_range": "", "X": 1e-6, "input_range_v": 0.1}]
        clock = [0.0]

        def sleep(seconds):
            clock[0] += seconds

        module = two_panels.app
        with mock.patch.object(module.time, "monotonic", lambda: clock[0]), \
                mock.patch.object(module.time, "sleep", sleep):
            result = panel._read_ppms2_zurich_ranged(317.3, scanning=True)
        self.assertEqual(result["X"], 1e-6)
        self.assertAlmostEqual(clock[0], 10.0, places=6)


def _step_seen(t_s, tau_s):
    """Fraction of a current step a 12 dB/oct (2nd-order) lock-in shows t_s
    after the step."""
    x = t_s / tau_s
    return 1.0 - math.exp(-x) * (1.0 + x)


def _iv6221_sample_times(settle_s, acquire_s):
    """When _nonlocal_iv6221_read_point reads after a step (no read delay):
    after Settle s, every NONLOCAL_IV6221_SAMPLE_INTERVAL_S until Acquire s
    has passed since the first reading."""
    interval = app.NONLOCAL_IV6221_SAMPLE_INTERVAL_S
    count = int(math.ceil(acquire_s / interval - 1e-9)) + 1
    return [settle_s + k * interval for k in range(count)]


class DefaultSetupsAtTheNewTc(NoWindows):
    """Settle s, Acquire s and Wait/pt do not follow the TC, so these
    out-of-the-box setups read before a 1 s TC has settled (reviewed,
    user, 2026-10-14).  Only documented here; the waits are unchanged."""

    def test_default_meters_are_the_ones_that_read_at_the_new_tc(self):
        with open(app.__file__, encoding="utf-8") as source:
            text = source.read()
        # STFMR: "Meter" starts at MFLI, which waited Wait/pt alone; since
        # 2026-10-14 it settles 1 x TC (test_stfmr_mfli_alone_...).
        self.assertIn('self.stfmr_lockin_meter_var = tk.StringVar(value="MFLI")', text)
        # 6221-IV: the Zurich alone measures.
        self.assertEqual(app.NONLOCAL_IV6221_DEFAULT_MEASURE, {"8": False, "9": False, "mfli": True})
        self.assertRegex(text, r"key: tk\.BooleanVar\(value=value\) for key, value in "
                               r"NONLOCAL_IV6221_DEFAULT_MEASURE\.items\(\)")
        # SHV (RT-E): SR830-1 and SR830-2 measure (user's figure, 2026-10-20).
        self.assertRegex(text, r"for key, measured in \(\('830_a', True\), \('830_b', True\),\s*"
                               r"\('830_c', False\), \('zurich', False\)\):")

    def test_a_default_6221_iv_point_saw_about_a_quarter_of_its_step(self):
        """Before 2026-10-14 a 6221-IV point waited Settle s alone.  Since
        then it waits at least 1 x TC (1 s), the user's choice (7 x TC, 7 s
        and 99 % of the step, earlier that day): the curve then lags by
        about 0.7 of a point instead of about one (user, 2026-10-14); the
        last lines check that."""
        self.assertEqual(app.NONLOCAL_LOCKIN_FILTER_ORDER, 2)       # 12 dB/oct
        settle, acquire = app.NONLOCAL_IV6221_SETTLE_S, app.NONLOCAL_IV6221_ACQUIRE_S
        self.assertEqual((settle, acquire), (0.5, 1.0))
        times = _iv6221_sample_times(settle, acquire)
        self.assertAlmostEqual(times[0], 0.5)
        self.assertAlmostEqual(times[-1], 1.5)                       # 0.5-1.5 TC at 1 s

        def mean_seen(settle_s, tau_s):
            sample_times = _iv6221_sample_times(settle_s, acquire)
            return sum(_step_seen(t, tau_s) for t in sample_times) / len(sample_times)

        tau = app.NONLOCAL_MFLI_TIME_CONSTANT_S
        self.assertAlmostEqual(mean_seen(settle, tau), 0.26, delta=0.01)   # the README's 26 %
        self.assertGreater(mean_seen(settle, 0.1), 0.99)                   # the old 0.1 s
        self.assertGreater(mean_seen(5.0, tau), 0.96)                      # Settle s about 5 s

        # On a sweep of equal steps, each reading is short of the step just
        # made by about one whole step: the curve lags by about one point.
        period = settle + acquire

        def steps_behind(t_after_step):
            return sum(1.0 - _step_seen(t_after_step + m * period, tau) for m in range(200))

        lag = sum(steps_behind(t) for t in times) / len(times)
        self.assertGreater(lag, 1.0)
        self.assertLess(lag, 1.3)

        # Now: each point waits max(Settle s, 1 x TC) = 1 s first.  An
        # isolated step is then about 26 % settled at the first reading and
        # 44 % over the window, but a sweep lags by only about 0.7 step.
        wait = app.CombinedControlApp._nonlocal_lockin_settle_s(settle, [tau])
        self.assertEqual(wait, 1.0)
        self.assertAlmostEqual(_step_seen(wait, tau), 1 - 2 / math.e, places=12)
        self.assertAlmostEqual(mean_seen(wait, tau), 0.44, delta=0.01)
        sample_times = _iv6221_sample_times(wait, acquire)
        period = wait + acquire
        lag = sum(steps_behind(t) for t in sample_times) / len(sample_times)
        self.assertGreater(lag, 0.6)
        self.assertLess(lag, 0.8)

    def test_stfmr_mfli_alone_reads_after_one_time_constant(self):
        """With Wait/pt (0.5 s) a default MFLI point saw 9 % of its step; it
        now settles 1 x TC, then averages Acquire s (user, 2026-10-14; 7 x
        TC earlier that day): an isolated step is about 57 % settled over the
        2 s window (the README's 57.3 %), and a steady sweep lags by about
        half a step (the README's 0.48)."""
        tau = app.STFMR_LOCKIN_MFLI_TIME_CONSTANT_S
        self.assertAlmostEqual(_step_seen(0.5, tau), 0.09, delta=0.005)
        settle = app.CombinedControlApp._stfmr_lockin_effective_settle_s(
            "MFLI", app.STFMR_LOCKIN_DEFAULT_SETTLE_S, app.STFMR_LOCKIN_SR830_TIME_CONSTANT_CODE, tau)
        self.assertEqual(settle, 1.0)
        acquire, interval = app.STFMR_LOCKIN_DEFAULT_ACQUIRE_S, app.STFMR_LOCKIN_SAMPLE_INTERVAL_S
        readings = [settle + k * interval for k in range(int(round(acquire / interval)) + 1)]
        self.assertEqual(len(readings), 21)
        self.assertAlmostEqual(sum(_step_seen(t, tau) for t in readings) / 21, 0.573, delta=0.005)
        period = settle + acquire
        lag = sum(sum(1.0 - _step_seen(t + m * period, tau) for m in range(200)) for t in readings) / 21
        self.assertAlmostEqual(lag, 0.48, delta=0.01)

    def test_a_ranging_idle_ppms_read_holds_the_zurich_10_s_per_step(self):
        """The idle readout (after Set 6221 with a current) ranges under the
        Zurich lock, so Zurich actions and a scan Start's Zurich set-up wait
        for it: up to 4 x 10 s at the 1 s default, 4 x 1 s at 0.1 s."""
        for tc_s, held_s in ((app.PPMS_ZURICH_TIME_CONSTANT_S, 40.0), (0.1, 4.0)):
            with self.subTest(tc_s=tc_s):
                _parent, panel, controller = two_panels.TwoPanelTests.make_panel(None)
                panel.connect_ppms2_zurich()
                self.assertTrue(controller.mfli_connected)
                clock = [0.0]

                class TimedLock:
                    """The Zurich RLock, timing each outermost hold."""
                    def __init__(self):
                        self.inner = threading.RLock()
                        self.depth = 0
                        self.held = []

                    def __enter__(self):
                        self.inner.acquire()
                        if self.depth == 0:
                            self.since = clock[0]
                        self.depth += 1
                        return self

                    def __exit__(self, *exc):
                        self.depth -= 1
                        if self.depth == 0:
                            self.held.append(clock[0] - self.since)
                        self.inner.release()
                        return False

                controller.lock = TimedLock()
                controller.mfli_config = {"timeconstant_s": tc_s}
                controller.read_stfmr_mfli.side_effect = lambda frequency, **kwargs: {
                    "auto_range": "RANGE 0.01->0.1 V", "X": 999}
                panel.ppms_idle_autorange_enabled = True
                panel.ppms_6221_zero_output = False
                panel.ppms_field_scan_running = False

                def sleep(seconds):
                    clock[0] += seconds

                module = two_panels.app
                with mock.patch.object(module.time, "monotonic", lambda: clock[0]), \
                        mock.patch.object(module.time, "sleep", sleep):
                    panel._poll_ppms2_zurich()
                self.assertEqual(controller.read_stfmr_mfli.call_count, 4)
                self.assertTrue(all(call.kwargs["auto_range"]
                                    for call in controller.read_stfmr_mfli.call_args_list))
                self.assertEqual(len(controller.lock.held), 1)
                self.assertAlmostEqual(controller.lock.held[0], held_s, places=6)
                self.assertIn("did not settle", panel.zurich_status.get())


if __name__ == "__main__":
    unittest.main()
