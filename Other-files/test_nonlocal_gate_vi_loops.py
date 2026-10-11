"""Gate-VI Loops (user, 2026-10-11): the pulse scan (start → stop → start)
run several times; in a frequency scan, at each frequency.  Each row of the
CSVs carries its loop, and the plots draw a line per loop.

No Tk and no instrument: the Gate-VI stand-ins record what the scan asks."""
import csv
import threading
import unittest
from unittest import mock

import test_nonlocal_frequency_scan as scan_tests
from test_nonlocal_keithley import Var, bare_app
from test_ppms_motor_debug import app


class LoopTests(unittest.TestCase):
    # GateVITests' page and run, without running its tests here.
    make = scan_tests.GateVITests.make
    run_scan = scan_tests.GateVITests.run_scan

    def page(self, loops, tick=False, **values):
        a = self.make(tick=tick, **values)
        a.nonlocal_vi_loops_var = Var(loops)
        return a

    def test_the_default_and_what_is_refused(self):
        self.assertEqual(app.NONLOCAL_VI_LOOPS, 2, "default 2 (user, 2026-10-11)")
        a = self.make(tick=False)  # no Loops box: one loop
        self.assertEqual(a._parse_nonlocal_vi_settings()["loops"], 1)
        self.assertEqual(self.page("3")._parse_nonlocal_vi_settings()["loops"], 3)
        for value in ("0", "1.5", "x", "10001"):
            with self.assertRaisesRegex(ValueError, "Loops must be an integer from 1 to 10000"):
                self.page(value)._parse_nonlocal_vi_settings()
        # 0 → 4 → 0 V in 0.01 V steps is 801 points; 13 loops are 10413.
        a = self.page("13")
        a.nonlocal_vi_step_voltage_var.set("0.01")
        with self.assertRaisesRegex(ValueError, "all loops and frequencies"):
            a._parse_nonlocal_vi_settings()

    def test_the_time_estimate_scales_with_the_loops(self):
        once = app.NONLOCAL_VI_RES_HOLD_S + 2 * app.NONLOCAL_VI_RES_SAMPLE_INTERVAL_S
        single = self.page("1")._parse_nonlocal_vi_settings()["estimated_s"]
        triple = self.page("3")._parse_nonlocal_vi_settings()["estimated_s"]
        self.assertAlmostEqual(triple - once, 3 * (single - once))

    def test_each_loop_is_a_whole_pulse_scan_with_its_loop_in_the_csvs(self):
        a = self.page("2")
        settings, summary, statuses = self.run_scan(a)
        voltages = [call.args[0] for call in a.nonlocal_vi_2410_controller.apply_voltage_pulse.call_args_list]
        self.assertEqual(voltages, [0.0, 4.0, 0.0, 0.0, 4.0, 0.0], "start → stop → start, twice")
        rows = sorted({(int(row["point_index"]), row["loop"], row["pulse_voltage_V"]) for row in summary})
        self.assertEqual(rows, [(1, "1", "0.0"), (2, "1", "4.0"), (3, "1", "0.0"),
                                (4, "2", "0.0"), (5, "2", "4.0"), (6, "2", "0.0")])
        self.assertIn("Loop 2/2: the pulse scan starts", statuses)
        self.assertEqual([row[5] for row in a.nonlocal_vi_resistance_rows], ["", 1, 1, 1, 2, 2, 2])
        a.nonlocal_vi_progress.configure.assert_called_with(value=6)

    def test_the_csv_headers_end_with_the_loop(self):
        self.assertEqual(app.CombinedControlApp._nonlocal_vi_raw_header()[-1], "loop")
        self.assertEqual(app.CombinedControlApp._nonlocal_vi_summary_header()[-1], "loop")

    def test_in_a_frequency_scan_the_loops_run_at_each_frequency(self):
        a = self.page("2", tick=True)
        _settings, summary, statuses = self.run_scan(a)
        order = [(row["6221_frequency_Hz"], row["loop"]) for row in summary]
        self.assertEqual(sorted(set(order), key=order.index),
                         [("11.0", "1"), ("11.0", "2"), ("13.0", "1"), ("13.0", "2")])
        self.assertIn("Loop 2/2 at 13 Hz: the pulse scan starts", statuses)
        self.assertEqual(a.nonlocal_vi_2410_controller.apply_voltage_pulse.call_count, 12)

    def test_one_loop_keeps_the_scan_as_before(self):
        a = self.page("1")
        _settings, summary, statuses = self.run_scan(a)
        self.assertEqual({row["loop"] for row in summary}, {"1"})
        self.assertFalse([text for text in statuses if str(text).startswith("Loop")])


class LoopPlotTests(unittest.TestCase):
    def redraw(self, summaries, resistances):
        a = bare_app()
        a.nonlocal_vi_data_lock = threading.Lock()
        a.nonlocal_vi_time_rows = []
        a.nonlocal_vi_summary_rows = summaries
        a.nonlocal_vi_resistance_rows = resistances
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
        return {call.args[0]: call for call in a._draw_nonlocal_series.call_args_list}

    def rows(self, loops, frequencies=(17.3,)):
        summaries = [
            {"key": "mfli", "order": 1, "loop": loop, "pulse_voltage_v": voltage, "mean": 1e-6,
             "mean_x": 1e-6 * loop, "mean_y": 0.0, "frequency_hz": frequency, "set_frequency_hz": frequency}
            for frequency in frequencies for loop in loops for voltage in (0.0, 4.0)
        ]
        resistances = [(0.0, 4e8, 0.1, "initial_gate", frequencies[0], "")] + [
            (voltage, 1e9 * loop, 2.0, "post_point", frequency, loop)
            for frequency in frequencies for loop in loops for voltage in (0.0, 4.0)
        ]
        return summaries, resistances

    def test_a_line_per_loop(self):
        calls = self.redraw(*self.rows((1, 2)))
        self.assertEqual(calls["resistance"].args[1], {"8_loop_1": [(0.0, 1e9), (4.0, 1e9)],
                                                       "8_loop_2": [(0.0, 2e9), (4.0, 2e9)]})
        self.assertEqual(calls[("summary", 1)].args[1], {"mfli_loop_1": [(0.0, 1e-6), (4.0, 1e-6)],
                                                         "mfli_loop_2": [(0.0, 2e-6), (4.0, 2e-6)]})
        self.assertEqual(calls[("summary", 1)].kwargs["series_labels"],
                         {"mfli_loop_1": "Zurich", "mfli_loop_2": "Zurich"})
        self.assertFalse(calls[("summary", 1)].kwargs["show_legend"], "one meter: no legend")

    def test_one_loop_as_before(self):
        calls = self.redraw(*self.rows((1,)))
        self.assertEqual(calls["resistance"].args[1], {"8": [(0.0, 1e9), (4.0, 1e9)]})
        self.assertEqual(calls[("summary", 1)].args[1], {"mfli": [(0.0, 1e-6), (4.0, 1e-6)]})

    def test_loops_in_a_frequency_scan(self):
        calls = self.redraw(*self.rows((1, 2), (11.0, 13.0)))
        self.assertEqual(set(calls[("summary", 1)].args[1]),
                         {"mfli_freq_11_loop_1", "mfli_freq_11_loop_2",
                          "mfli_freq_13_loop_1", "mfli_freq_13_loop_2"})
        labels = calls[("summary", 1)].kwargs["series_labels"]
        self.assertEqual({key: value for key, value in labels.items() if value},
                         {"mfli_freq_11_loop_1": "11 Hz", "mfli_freq_13_loop_1": "13 Hz"})
        self.assertEqual(set(calls["resistance"].args[1]),
                         {"8_freq_11_loop_1", "8_freq_11_loop_2", "8_freq_13_loop_1", "8_freq_13_loop_2"})


if __name__ == "__main__":
    unittest.main()
