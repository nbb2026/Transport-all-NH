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
import re
import sys
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


class WaitsAtTheNewDefaults(NoWindows):
    """The rules are unchanged; these are their values at the 1 s defaults."""

    def test_stfmr_points(self):
        wait = app.CombinedControlApp._stfmr_sr830_point_wait_s
        delay, code = app.STFMR_LOCKIN_DEFAULT_DELAY_S, app.STFMR_LOCKIN_SR830_TIME_CONSTANT_CODE
        self.assertEqual(wait("SR830", delay, code), 5.0)          # max(0.5 s, 5 x 1 s); 0.5 s with 100 ms
        self.assertEqual(wait("MFLI + SR830", delay, code), 5.0)
        self.assertEqual(wait("MFLI", delay, code), 0.5)           # the MFLI alone waits Wait/pt
        # An MFLI range change: max(Wait/pt, 10 x MFLI TC, 0.2 s) = 10 s (was 1 s).
        self.assertEqual(max(delay, 10.0 * app.STFMR_LOCKIN_MFLI_TIME_CONSTANT_S, 0.2), 10.0)

    def test_non_local_6221_iv(self):
        tc = app.NONLOCAL_MFLI_TIME_CONSTANT_S
        self.assertEqual(app.NONLOCAL_IV6221_FIRST_SETTLE_TAUS * tc, 10.0)          # far jumps (was 1 s)
        self.assertEqual(app.NONLOCAL_IV6221_RANGE_SETTLE_TAUS["mfli"] * tc, 10.0)  # after a range step

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


if __name__ == "__main__":
    unittest.main()
