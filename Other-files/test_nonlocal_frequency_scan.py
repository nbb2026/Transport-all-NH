"""Frequency scan on 6221-IV and Gate-VI (user, 2026-10-10), as the STFMR
multi-frequency sweep: the whole scan runs at one frequency, then at the
next, with the 6221 off and set up again in between.  The frequencies are
the whole-number primes from f start to f stop, as the microwave test
program's modulation axis.

No Tk and no instrument: the 6221-IV and Gate-VI stand-ins record what the
scans ask of them."""
import csv
import tempfile
import threading
import time
import unittest
from pathlib import Path
from unittest import mock

from test_nonlocal_6221_iv import FakeSource, page
from test_nonlocal_gate_vi_harmonics import FakeAC, gate_page
from test_nonlocal_keithley import Var, bare_app
from test_ppms_motor_debug import app


class Event(threading.Event):
    """Records each wait; sleeps only the short ones (sample timing)."""

    def __init__(self):
        super().__init__()
        self.waits = []

    def wait(self, timeout=None):
        self.waits.append(timeout)
        if timeout and timeout < 0.05:
            time.sleep(timeout)
        return self.is_set()


def run_recorder(page):
    """A scan's run folder and log, as begin_scan_run makes them (user,
    2026-10-18): <tmp>/run/run.csv and run.log."""
    return app.ScanRunRecorder(Path(tempfile.mkdtemp()), page, "run", {})


def luminance(color):
    return sum(int(color[index:index + 2], 16) for index in (1, 3, 5))


class PrimeTests(unittest.TestCase):
    def test_the_primes_from_start_to_stop(self):
        self.assertEqual(app.nonlocal_prime_frequencies("7", "31"),
                         (7.0, 11.0, 13.0, 17.0, 19.0, 23.0, 29.0, 31.0))
        self.assertEqual(app.nonlocal_prime_frequencies(2, 2), (2.0,))
        self.assertEqual(app.nonlocal_prime_frequencies("97", "97.0"), (97.0,))
        self.assertEqual(len(app.nonlocal_prime_frequencies(2, 100000)), 9592)

    def test_what_is_refused(self):
        for start, stop, message in (
            ("7.5", "31", "whole numbers"), ("x", "31", "whole numbers"), ("31", "7", "f start ≤ f stop"),
            ("1", "7", "2 ≤ f start"), ("2", "100001", "100000"), ("24", "28", "No prime"),
        ):
            with self.assertRaisesRegex(ValueError, message):
                app.nonlocal_prime_frequencies(start, stop)

    def test_the_row_note(self):
        self.assertEqual(app.nonlocal_frequency_scan_text("7", "97"), "22 primes: 7–97 Hz")
        self.assertEqual(app.nonlocal_frequency_scan_text("17", "17"), "1 prime: 17 Hz")
        self.assertIn("No prime", app.nonlocal_frequency_scan_text("24", "28"))

    def test_the_defaults(self):
        self.assertEqual((app.NONLOCAL_FSCAN_START_HZ, app.NONLOCAL_FSCAN_STOP_HZ), (7, 97))
        self.assertEqual(app.NONLOCAL_FSCAN_SETTLE_PERIODS, 5.0)

    def test_shades_run_light_to_dark(self):
        color = "#1565c0"
        self.assertEqual(app.shade_color(color, 0.5), color)
        self.assertGreater(luminance(app.shade_color(color, 0.0)), luminance(color))
        self.assertLess(luminance(app.shade_color(color, 1.0)), luminance(color))


class StyleTests(unittest.TestCase):
    def test_one_meter_names_every_frequency_up_to_six(self):
        series = {f"mfli_freq_{f:g}_loop_1": [(0, 1)] for f in (7, 11, 13)}
        labels, colors, markers = app.CombinedControlApp._nonlocal_frequency_styles(
            series, {"mfli": "#CC79A7"})
        self.assertEqual(labels, {"mfli_freq_7_loop_1": "7 Hz", "mfli_freq_11_loop_1": "11 Hz",
                                  "mfli_freq_13_loop_1": "13 Hz"})
        self.assertGreater(luminance(colors["mfli_freq_7_loop_1"]), luminance(colors["mfli_freq_13_loop_1"]),
                           "the lowest frequency light, the highest dark")
        self.assertEqual(markers, {}, "one meter: the usual marker")

    def test_many_frequencies_name_the_lowest_and_highest_and_several_meters_their_meter(self):
        frequencies = app.nonlocal_prime_frequencies(7, 31)
        series = {f"{key}_freq_{f:g}_loop_{loop}": [(0, 1)]
                  for key in ("8", "mfli") for f in frequencies for loop in (1, 2)}
        labels, _colors, markers = app.CombinedControlApp._nonlocal_frequency_styles(
            series, {"8": "#0072B2", "mfli": "#CC79A7"})
        self.assertEqual(sorted(label for label in labels.values() if label),
                         ["31 Hz SR830 #8", "31 Hz Zurich", "7 Hz SR830 #8", "7 Hz Zurich"])
        self.assertEqual(labels["8_freq_7_loop_2"], "", "a second loop is not named again")
        self.assertEqual({markers["8_freq_7_loop_1"], markers["mfli_freq_7_loop_1"]},
                         {"open_circle", "circle"})


class IV6221Tests(unittest.TestCase):
    def make(self, fstart="11", fstop="13", tick=True, **values):
        self.source = FakeSource()
        a = page(self.source, **values)
        a.nonlocal_iv6221_fscan_var = Var(tick)
        a.nonlocal_iv6221_fstart_var, a.nonlocal_iv6221_fstop_var = Var(fstart), Var(fstop)
        a.nonlocal_vi_2410_controller = mock.Mock(connected=False)
        a.nonlocal_vi_interlock = app.NonLocalVIInterlock()
        a.nonlocal_keithley_stop_event = Event()
        a.nonlocal_keithley_output_off = False
        self.posted = []
        a.post_ui = self.posted.append
        a._publish_reference_sync = mock.Mock()
        a._show_nonlocal_mfli_lock = mock.Mock()
        a.nonlocal_iv6221_source_var, a.nonlocal_iv6221_point_var = Var(), Var()
        self.sources = []
        a.nonlocal_iv6221_source_var.set = self.sources.append
        # The LOCKIN_SETTLE_TAUS point settle (user, 2026-10-14; 1 x TC) is
        # off here, so the 5-period wait shows.
        for name, value in (("NONLOCAL_IV6221_FIRST_SETTLE_TAUS", 0.0),
                            ("LOCKIN_SETTLE_TAUS", 0.0),
                            ("NONLOCAL_IV6221_REFERENCE_WAIT_S", 0.0)):
            patcher = mock.patch.object(app, name, value)
            patcher.start()
            self.addCleanup(patcher.stop)
        return a

    def test_the_tick_takes_the_primes_and_without_it_frequency_hz(self):
        settings = self.make("7", "31")._parse_nonlocal_iv6221_settings()
        self.assertTrue(settings["frequency_scan"])
        self.assertEqual(settings["frequencies"], app.nonlocal_prime_frequencies(7, 31))
        settings = self.make(tick=False)._parse_nonlocal_iv6221_settings()
        self.assertFalse(settings["frequency_scan"])
        self.assertEqual(settings["frequencies"], (17.3,))

    def test_the_unused_frequency_hz_is_not_checked_and_the_log_names_the_first_scanned(self):
        # Review, 2026-10-10: 2 × 60 kHz is above the SR830's 102 kHz, but
        # Frequency Hz is not used in a frequency scan.
        settings = self.make("7", "31", frequency="60000")._parse_nonlocal_iv6221_settings()
        self.assertEqual(settings["frequency_hz"], 7.0)
        with self.assertRaisesRegex(ValueError, "SR830 #8 demodulation frequency exceeds 102 kHz"):
            self.make(tick=False, frequency="60000")._parse_nonlocal_iv6221_settings()

    def test_limits_at_the_highest_frequency(self):
        # SR830 #8 at its 2nd harmonic: 2 × 99991 Hz is above its 102 kHz.
        with self.assertRaisesRegex(ValueError, "SR830 #8 demodulation frequency exceeds 102 kHz at 99991 Hz"):
            self.make("99991", "99991")._parse_nonlocal_iv6221_settings()
        # 100 currents × 1229 primes.
        with self.assertRaisesRegex(ValueError, "100000 points including harmonic passes and frequencies"):
            self.make("2", "10000")._parse_nonlocal_iv6221_settings()
        with self.assertRaisesRegex(ValueError, "No prime"):
            self.make("24", "28")._parse_nonlocal_iv6221_settings()

    def test_the_whole_scan_runs_at_each_frequency_with_the_6221_set_up_again(self):
        a = self.make(start="0.1", end="0.2", step="0.1")
        settings = a._parse_nonlocal_iv6221_settings()
        settings.update({"settle_s": 0.0, "acquire_s": 1e-9})
        recorder = run_recorder("NL_6221IV")
        path = recorder.csv_path
        a._nonlocal_iv6221_worker(settings, recorder)
        calls = self.source.calls
        self.assertEqual([c[2] for c in calls if c[0] == "configure"], [11.0, 13.0])
        second = [c[0] for c in calls].index("configure", 1)
        self.assertEqual(calls[second - 2:second], [("output_off",), ("confirm_output_off",)],
                         "off, read back off, before the next frequency is set up")
        self.assertEqual([c[:2] for c in calls if c[0] in ("set_current", "update_amplitude")],
                         [("set_current", 0.1), ("update_amplitude", 0.2)] * 2,
                         "the sine started again at each frequency")
        self.assertEqual(sum(c[0] == "wait_references" for c in calls), 2, "the lock at each frequency")
        self.assertEqual(calls[-2:], [("output_off",), ("confirm_output_off",)])
        # At least 5 periods of each new frequency before its first point.
        waits = a.nonlocal_keithley_stop_event.waits
        self.assertIn(5 / 11, waits)
        self.assertIn(5 / 13, waits)
        with path.open(encoding="utf-8-sig") as file:
            rows = list(csv.DictReader(file))
        self.assertEqual([(row["point"], row["frequency_Hz"], row["set_current_rms_mA"]) for row in rows],
                         [("1", "11", "0.1"), ("2", "11", "0.2"), ("3", "13", "0.1"), ("4", "13", "0.2")])
        appended = []
        a._append_nonlocal_iv6221_point = appended.append
        for callback in self.posted:
            callback()
        self.assertEqual([(row["set_frequency_hz"], row["frequency_index"], row["frequency_count"])
                          for row in appended], [(11.0, 1, 2), (11.0, 1, 2), (13.0, 2, 2), (13.0, 2, 2)])
        self.assertEqual(self.sources, ["6221 ON: 0.1 mA RMS / 11 Hz", "6221 ON: 0.2 mA RMS / 11 Hz",
                                        "6221: OFF; setting up 13 Hz",
                                        "6221 ON: 0.1 mA RMS / 13 Hz", "6221 ON: 0.2 mA RMS / 13 Hz"])

    def test_without_the_tick_one_scan_and_no_extra_wait(self):
        a = self.make(tick=False, start="0.1", end="0.2", step="0.1")
        settings = a._parse_nonlocal_iv6221_settings()
        settings.update({"settle_s": 0.0, "acquire_s": 1e-9})
        a._nonlocal_iv6221_worker(settings, run_recorder("NL_6221IV"))
        self.assertEqual([c[2] for c in self.source.calls if c[0] == "configure"], [17.3])
        self.assertNotIn(5 / 17.3, a.nonlocal_keithley_stop_event.waits)

    def test_a_stop_between_frequencies_leaves_the_6221_off(self):
        a = self.make(start="0.1", end="0.1", step="0.1")
        settings = a._parse_nonlocal_iv6221_settings()
        settings.update({"settle_s": 0.0, "acquire_s": 1e-9})
        configure = self.source.configure

        def stop_after_first(*args, **options):
            result = configure(*args, **options)
            if args[1] == 11.0:
                original = self.source.read_samples

                def read_then_stop(*read_args):
                    a.nonlocal_keithley_stop_event.set()
                    return original(*read_args)
                self.source.read_samples = read_then_stop
            return result

        self.source.configure = stop_after_first
        with self.assertRaises(InterruptedError):
            a._nonlocal_iv6221_worker(settings, run_recorder("NL_6221IV"))
        self.assertEqual([c[2] for c in self.source.calls if c[0] == "configure"], [11.0])
        self.assertEqual(self.source.calls[-2:], [("output_off",), ("confirm_output_off",)])
        self.assertFalse(self.source.output_on)

    def test_the_plot_has_a_curve_per_frequency(self):
        a = bare_app()
        a.nonlocal_iv6221_canvases = {(1, "X"): "canvas"}
        a._draw_nonlocal_series = mock.Mock()
        a.nonlocal_iv6221_rows = [
            {"pass": 1, "loop": 1, "current_rms_ma": current, "frequency_hz": frequency,
             "set_frequency_hz": frequency, "series": {("mfli", 1): {"X": current * 1e-6}}}
            for frequency in (11.0, 13.0) for current in (0.1, 0.2)
        ]
        a._redraw_nonlocal_iv6221_plots()
        call = a._draw_nonlocal_series.call_args
        self.assertEqual(call.args[1], {"mfli_freq_11_loop_1": [(0.1, 1e-7), (0.2, 2e-7)],
                                        "mfli_freq_13_loop_1": [(0.1, 1e-7), (0.2, 2e-7)]})
        self.assertEqual(call.kwargs["series_labels"],
                         {"mfli_freq_11_loop_1": "11 Hz", "mfli_freq_13_loop_1": "13 Hz"})
        self.assertTrue(call.kwargs["show_legend"])
        # One frequency: as before, a curve per meter and loop.
        for row in a.nonlocal_iv6221_rows:
            row["frequency_hz"] = row["set_frequency_hz"] = 17.3
        a._redraw_nonlocal_iv6221_plots()
        self.assertEqual(list(a._draw_nonlocal_series.call_args.args[1]), ["mfli_loop_1"])


class StepFakeAC(FakeAC):
    """The Gate-VI stand-in, reading back the frequency it was set up at."""

    def configure(self, current, frequency, *args, **options):
        result = super().configure(current, frequency, *args, **options)
        self.calls[-1] = ("configure", frequency)
        return frequency, result[1]

    def output_off(self):
        self.calls.append(("output_off",))

    def confirm_output_off(self, context):
        self.calls.append(("confirm_output_off", context))


class GateVITests(unittest.TestCase):
    def setUp(self):
        # The LOCKIN_SETTLE_TAUS read settle (user, 2026-10-14; 1 x TC) is
        # off here, so the 5-period pre-run shows; test_nonlocal_lockin_settle
        # has it.
        patcher = mock.patch.object(app, "LOCKIN_SETTLE_TAUS", 0.0)
        patcher.start()
        self.addCleanup(patcher.stop)

    def make(self, start="11", stop="13", tick=True, sr830="1", zurich="2"):
        a = gate_page(sr830, zurich)
        a.nonlocal_vi_ac_controller = StepFakeAC()
        a.nonlocal_vi_ac_controller.mfli_device = "dev4414"
        a.nonlocal_vi_fscan_var = Var(tick)
        a.nonlocal_vi_fstart_var, a.nonlocal_vi_fstop_var = Var(start), Var(stop)
        return a

    def test_the_tick_takes_the_primes_but_not_with_a_dc_read_current(self):
        settings = self.make("7", "31")._parse_nonlocal_vi_settings()
        self.assertTrue(settings["frequency_scan"])
        self.assertEqual(settings["frequencies"], app.nonlocal_prime_frequencies(7, 31))
        a = self.make(tick=False)
        settings = a._parse_nonlocal_vi_settings()
        self.assertEqual((settings["frequency_scan"], settings["frequencies"]), (False, (17.3,)))
        a = self.make()
        a.nonlocal_vi_output_mode_var.set("DC")
        a.nonlocal_vi_measurement_enabled_vars["8"].set(False)
        a.nonlocal_vi_use_mfli_var.set(False)
        a.nonlocal_vi_use_2182_var.set(True)
        a.nonlocal_vi_ac_controller.nanovoltmeter_connected = True
        settings = a._parse_nonlocal_vi_settings()
        self.assertFalse(settings["frequency_scan"], "a DC read current has no frequency to scan")
        self.assertEqual(len(settings["frequencies"]), 1)

    def test_the_unused_frequency_hz_is_not_checked(self):
        a = self.make("7", "31", sr830="1+2")
        a.nonlocal_vi_frequency_var.set("60000")
        self.assertEqual(a._parse_nonlocal_vi_settings()["frequency_hz"], 7.0)
        a.nonlocal_vi_fscan_var.set(False)
        with self.assertRaisesRegex(ValueError, "SR830 #8 demodulation frequency exceeds 102 kHz$"):
            a._parse_nonlocal_vi_settings()

    def test_each_order_starts_afresh_at_a_new_frequency(self):
        a = self.make(sr830="1+2")
        self.run_scan(a)
        switches = [c[1:] for c in a.nonlocal_vi_ac_controller.calls if c[0] in ("harmonic", "configure")]
        # Per point 2ω then back to 1ω; the 2nd order's first switch at the
        # next frequency starts at the configure range, not the last one.
        second = switches.index((13.0,))
        self.assertEqual(switches[1], ("8", 2, None))
        self.assertEqual(switches[second + 1], ("8", 2, None))
        self.assertEqual(switches[second + 3], ("8", 2, 12), "then on its own last range again")

    def test_limits_and_the_time_estimate(self):
        with self.assertRaisesRegex(ValueError, "SR830 #8 demodulation frequency exceeds 102 kHz at 99991 Hz"):
            self.make("99991", "99991", sr830="1+2")._parse_nonlocal_vi_settings()
        with self.assertRaisesRegex(ValueError, "all loops and frequencies"):
            a = self.make("2", "100000")
            a.nonlocal_vi_step_voltage_var.set("0.01")
            a._parse_nonlocal_vi_settings()
        # Pre-run 10 s, above 5 periods: twice the single pulse scan.
        single, double = self.make(tick=False), self.make("11", "13")
        for a in (single, double):
            a.nonlocal_vi_settle_var.set("10")
        once = app.NONLOCAL_VI_RES_HOLD_S + 2 * app.NONLOCAL_VI_RES_SAMPLE_INTERVAL_S
        self.assertAlmostEqual(double._parse_nonlocal_vi_settings()["estimated_s"] - once,
                               2 * (single._parse_nonlocal_vi_settings()["estimated_s"] - once))

    def run_scan(self, a):
        settings = a._parse_nonlocal_vi_settings()
        settings["sample_interval_s"] = 0.01
        a.nonlocal_vi_stop_event = Event()
        a.nonlocal_vi_interlock = app.NonLocalVIInterlock()
        a.nonlocal_vi_data_lock = threading.Lock()
        a.nonlocal_vi_time_rows, a.nonlocal_vi_summary_rows = [], []
        a.nonlocal_vi_pulse_rows, a.nonlocal_vi_current_rows, a.nonlocal_vi_resistance_rows = [], [], []
        a.post_ui = lambda callback: callback()
        a._redraw_nonlocal_vi_plots = mock.Mock()
        a._publish_nonlocal_vi_samples = mock.Mock()
        a._publish_reference_sync = mock.Mock()
        a.nonlocal_vi_progress = mock.Mock()
        a.nonlocal_vi_eta_var, a.nonlocal_vi_status_var, a.nonlocal_vi_output_var = Var(), Var(), Var()
        statuses = []
        a.nonlocal_vi_status_var.set = statuses.append
        recorder = run_recorder("NL_GateVI")
        a._nonlocal_vi_scan_worker(settings, recorder)
        with recorder.csv_path.open(encoding="utf-8-sig") as file:
            summary = list(csv.DictReader(file))
        return settings, summary, statuses

    def test_the_whole_pulse_scan_runs_at_each_frequency(self):
        a = self.make()
        settings, summary, statuses = self.run_scan(a)
        calls = a.nonlocal_vi_ac_controller.calls
        self.assertEqual([c for c in calls if c[0] == "configure"], [("configure", 11.0), ("configure", 13.0)])
        second = calls.index(("configure", 13.0))
        self.assertEqual(calls[second - 2:second],
                         [("output_off",), ("confirm_output_off", "before the next frequency")])
        self.assertEqual(calls[second + 1:second + 3],
                         [("output_off",), ("confirm_output_off", "after setting up the next frequency")])
        # 0 → 4 → 0 V: three pulses at 11 Hz, then three at 13 Hz.
        self.assertEqual(a.nonlocal_vi_2410_controller.apply_voltage_pulse.call_count, 6)
        points = sorted({(int(row["point_index"]), row["pulse_voltage_V"], row["6221_frequency_Hz"])
                         for row in summary})
        self.assertEqual(points, [(1, "0.0", "11.0"), (2, "4.0", "11.0"), (3, "0.0", "11.0"),
                                  (4, "0.0", "13.0"), (5, "4.0", "13.0"), (6, "0.0", "13.0")])
        self.assertIn("Frequency 2/2: 13 Hz; the pulse scan starts again", statuses)
        self.assertEqual(
            [(row[0], row[3], row[4]) for row in a.nonlocal_vi_resistance_rows[1:]],
            [(0.0, "post_point", 11.0), (4.0, "post_point", 11.0), (0.0, "post_point", 11.0),
             (0.0, "post_point", 13.0), (4.0, "post_point", 13.0), (0.0, "post_point", 13.0)])
        self.assertEqual({row["set_frequency_hz"] for row in a.nonlocal_vi_summary_rows}, {11.0, 13.0})
        # The 6221 pre-run: at least 5 periods (the page's pre-run is 0 s).
        # It counts from the switch-on and the lock check comes first, so
        # the wait after it is 5 periods less that (here instant) check
        # (review, 2026-10-14).
        waits = a.nonlocal_vi_stop_event.waits
        for period_s in (5 / 11, 5 / 13):
            self.assertEqual(sum(1 for w in waits if w and abs(w - period_s) < 0.01), 3)
        a.nonlocal_vi_progress.configure.assert_called_with(value=6)

    def test_without_the_tick_the_scan_is_as_before(self):
        a = self.make(tick=False)
        _settings, summary, statuses = self.run_scan(a)
        self.assertEqual([c for c in a.nonlocal_vi_ac_controller.calls if c[0] == "configure"],
                         [("configure", 17.3)])
        self.assertEqual(len({row["point_index"] for row in summary}), 3)
        self.assertFalse([text for text in statuses if str(text).startswith("Frequency")])
        self.assertNotIn(5 / 17.3, a.nonlocal_vi_stop_event.waits)


class GateVIPlotTests(unittest.TestCase):
    def test_a_curve_per_frequency_for_resistance_and_the_harmonic(self):
        a = bare_app()
        a.nonlocal_vi_data_lock = threading.Lock()
        a.nonlocal_vi_time_rows = []
        a.nonlocal_vi_summary_rows = [
            {"key": "mfli", "order": 1, "pulse_voltage_v": voltage, "mean": 1e-6, "mean_x": 1e-6 * frequency,
             "mean_y": 0.0, "frequency_hz": frequency, "set_frequency_hz": frequency}
            for frequency in (11.0, 13.0) for voltage in (0.0, 4.0)
        ]
        a.nonlocal_vi_resistance_rows = [(0.0, 4e8, 0.1, "initial_gate")] + [
            (voltage, 1e9, 2.0, "post_point", frequency)
            for frequency in (11.0, 13.0) for voltage in (0.0, 4.0)
        ]
        a.nonlocal_vi_ac_controller = mock.Mock(connected_lockin_keys=(), mfli_connected=True,
                                                nanovoltmeter_connected=False)
        a.nonlocal_vi_2410_controller = mock.Mock(model="2400")
        a._selected_nonlocal_vi_lockin_keys = lambda: ()
        a.nonlocal_vi_use_mfli_var, a.nonlocal_vi_use_2182_var = Var(True), Var(False)
        a.nonlocal_vi_lockin_harmonic_vars = {"8": Var("1"), "9": Var("1")}
        a.nonlocal_vi_mfli_harmonic_var = Var("1")
        keys = ["resistance"] + [(order, channel) for order in (1, 2, 3) for channel in ("x", "y", "theta")]
        keys += [("summary", order) for order in (1, 2, 3)]
        a.nonlocal_vi_plot_canvases = {key: key for key in keys}
        a.nonlocal_vi_plot_frames = {key: mock.Mock() for key in keys}
        a._layout_nonlocal_vi_plots = mock.Mock()
        a._draw_nonlocal_series = mock.Mock()
        a._redraw_nonlocal_vi_plots()
        calls = {call.args[0]: call for call in a._draw_nonlocal_series.call_args_list}
        self.assertEqual(calls["resistance"].args[1], {"8_freq_11_loop_1": [(0.0, 1e9), (4.0, 1e9)],
                                                       "8_freq_13_loop_1": [(0.0, 1e9), (4.0, 1e9)]},
                         "the check before the first pulse is still left out")
        self.assertNotIn("color_override", calls["resistance"].kwargs)
        self.assertEqual(calls[("summary", 1)].args[1], {"mfli_freq_11_loop_1": [(0.0, 1.1e-5), (4.0, 1.1e-5)],
                                                         "mfli_freq_13_loop_1": [(0.0, 1.3e-5), (4.0, 1.3e-5)]})
        self.assertEqual(calls[("summary", 1)].kwargs["series_labels"],
                         {"mfli_freq_11_loop_1": "11 Hz", "mfli_freq_13_loop_1": "13 Hz"})


if __name__ == "__main__":
    unittest.main()
