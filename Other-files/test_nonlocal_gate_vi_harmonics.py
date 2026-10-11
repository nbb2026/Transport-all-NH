"""Gate-VI harmonics (user, 2026-10-09): SR830 1+2 read one order after the
other at each point, the Zurich 1+2 or 1+2+3 at once.  Pulse gate 100 MΩ.
Its |R| measured as |R| vs time measures it: 15 s, the last 5 s averaged.

No Tk and no instrument: stand-in controllers record what the scan asks."""
import csv
import math
import tempfile
import types
import threading
import unittest
from pathlib import Path
from unittest import mock

from test_nonlocal_keithley import FakeSourceMeter, Var, bare_app, connected_controller
from test_ppms_motor_debug import app


def reading(r, **extra):
    return dict(X=r, Y=0.0, R=r, voltage=r, phase_deg=0.0, reference_frequency_hz=17.3,
                lia_status=0, status_text="OK", reference_valid=True, measurement_valid=True,
                output_on=True, measurement_condition=0, in_compliance=False,
                sensitivity_index=9, auto_range="", **extra)


class FakeAC:
    def __init__(self):
        self.calls = []
        self.harmonic = {}
        self.sensitivity_indices = {}
        self.source_connected = True
        self.connected_lockin_keys = ("8",)
        self.mfli_connected = True
        self.nanovoltmeter_connected = False

    def configure(self, current, frequency, compliance, lockin_configs, marker_line,
                  mfli_config=None, use_nanovoltmeter=False, sample_hz=1.0, output_mode="AC"):
        self.calls.append(("configure", {k: dict(v) for k, v in lockin_configs.items()}, dict(mfli_config)))
        self.harmonic = {key: config["harmonic"] for key, config in lockin_configs.items()}
        self.sensitivity_indices = {key: 9 for key in lockin_configs}
        return 17.3, {
            "8": {"meter_type": "SR830", "time_constant_s": 0.001, "harmonic": self.harmonic["8"],
                  "coupling": "AC", "channel": "R", "resource": "GPIB 8"},
            "mfli": {"meter_type": "MFLI", "resource": "dev4414", "harmonic": mfli_config["harmonic"],
                     "coupling": "AC", "channel": "R", "sample_rate_hz": 10.0},
        }

    def configure_second_harmonic(self, tau, rate):
        self.calls.append(("second", tau, rate))

    def configure_third_harmonic(self, tau, rate):
        self.calls.append(("third", tau, rate))

    def disable_unused_ppms_demods(self, mode):
        self.calls.append(("disable", mode))

    def output_off(self):
        pass

    def confirm_output_off(self, context):
        pass

    def set_current(self, current, frequency):
        return current

    def wait_references(self, frequency, stop, keys):
        return {}

    def set_lockin_harmonic(self, key, harmonic, sensitivity_index=None):
        self.calls.append(("harmonic", key, harmonic, sensitivity_index))
        self.harmonic[key] = harmonic
        self.sensitivity_indices[key] = 9 if sensitivity_index is None else sensitivity_index

    def read_samples(self, frequency, keys):
        samples = {}
        for key in keys:
            if key == "8":
                # Where autorange leaves each order: 1ω on 17, 2ω on 12.
                self.sensitivity_indices["8"] = 17 if self.harmonic["8"] == 1 else 12
                samples[key] = reading(1e-3 if self.harmonic["8"] == 1 else 2e-6)
            else:
                samples[key] = reading(2e-4, input_range_v=0.01)
        return samples

    def read_mfli_four_demods(self):
        fields = {}
        for index, (harmonic, adc, x) in enumerate(((1, 0, 2e-4), (2, 0, 3e-7), (3, 0, 4e-9), (1, 8, 1.0)), 1):
            prefix = f"zurich_demod{index}_"
            fields.update({prefix + "harmonic": harmonic, prefix + "input": adc,
                           prefix + "x_v": x, prefix + "y_v": 0.0})
        return fields


def gate_page(sr830="1+2", zurich="1+2+3"):
    a = bare_app()
    for name, value in (
        ("output_mode", "AC"), ("start_voltage", "0"), ("stop_voltage", "4"), ("step_voltage", "4"),
        ("pulse_width", "0.1"), ("post_pulse", "0"), ("current", "1"), ("frequency", "17.3"),
        ("sample_hz", "100"), ("settle", "0"), ("measure", "0.05"), ("compliance", "10"),
        ("marker_line", "1"), ("gate_resistance", f"{app.NONLOCAL_VI_MIN_RESISTANCE_FOR_PULSE_OHM / 1e6:g}"),
        ("mfli_coupling", "AC"), ("mfli_reference_source", "Trigger In 1"), ("mfli_harmonic", zurich),
        ("mfli_channel", "R"), ("mfli_device", "DEV4414 (Super)"),
    ):
        setattr(a, f"nonlocal_vi_{name}_var", Var(value))
    a.nonlocal_mfli_tc_var = Var("0.001")
    a.nonlocal_vi_lockin_coupling_vars = {"8": Var("AC"), "9": Var("AC")}
    a.nonlocal_vi_lockin_harmonic_vars = {"8": Var(sr830), "9": Var("2")}
    a.nonlocal_vi_lockin_channel_vars = {"8": Var("R"), "9": Var("R")}
    a.nonlocal_vi_lockin_resource_vars = {"8": Var("GPIB0::8::INSTR"), "9": Var("GPIB0::9::INSTR")}
    a.nonlocal_vi_measurement_enabled_vars = {"8": Var(True), "9": Var(False)}
    a.nonlocal_vi_use_mfli_var, a.nonlocal_vi_use_2182_var = Var(True), Var(False)
    a.nonlocal_vi_ac_controller = FakeAC()
    a.nonlocal_vi_ac_controller.mfli_device = "dev4414"
    keithley = mock.Mock(model="2400", identity="KEITHLEY 2400", connected=True)
    keithley.measure_resistance.return_value = {
        "source_current_a": 1e-9, "voltage_v": 1.0, "current_a": 1e-9, "resistance_ohm": 1e9}
    a.nonlocal_vi_2410_controller = keithley
    return a


class ParseTests(unittest.TestCase):
    def test_the_pulse_gate_default_is_100_megohm(self):
        self.assertEqual(app.NONLOCAL_VI_MIN_RESISTANCE_FOR_PULSE_OHM, 100e6)

    def test_the_scan_defaults_follow_the_users_figure(self):
        # User's figure, 2026-10-11.
        self.assertEqual(
            (app.NONLOCAL_VI_START_PULSE_V, app.NONLOCAL_VI_STOP_PULSE_V, app.NONLOCAL_VI_STEP_PULSE_V,
             app.NONLOCAL_VI_PULSE_WIDTH_S, app.NONLOCAL_VI_POST_PULSE_SETTLE_S,
             app.NONLOCAL_VI_MIN_RESISTANCE_FOR_PULSE_OHM, app.NONLOCAL_VI_SAMPLE_HZ,
             app.NONLOCAL_VI_6221_SETTLE_S, app.NONLOCAL_VI_MEASURE_S, app.NONLOCAL_VI_LOOPS),
            (100.0, -100.0, 10.0, 0.1, 0.5, 100e6, 1.0, 5.0, 30.0, 2))

    def test_choices_become_orders_and_passes(self):
        settings = gate_page()._parse_nonlocal_vi_settings()
        self.assertEqual(settings["lockin_orders"], {"8": (1, 2), "mfli": (1, 2, 3)})
        self.assertEqual(settings["lockin_configs"]["8"]["harmonic"], 1, "1ω first")
        self.assertEqual(settings["mfli_config"]["harmonic"], 1, "Demod 1 reads 1ω")
        self.assertEqual(settings["mfli_config"]["external_reference_demodulator"], 3)
        self.assertEqual(settings["read_passes"], [("8", "mfli"), ("8",)])
        single = gate_page("2", "2")._parse_nonlocal_vi_settings()
        self.assertEqual(single["read_passes"], [("8", "mfli")])
        self.assertEqual(single["mfli_config"]["harmonic"], 2)
        self.assertIsNone(single["mfli_config"]["external_reference_demodulator"])
        # The second pass: max(Settle s, 10 τ of 1 s) + Measure s, per point.
        self.assertAlmostEqual(settings["estimated_s"] - single["estimated_s"],
                               len(settings["voltage_scan"]) * (10.0 + 0.05))

    def test_limits_and_messages(self):
        a = gate_page()
        a.nonlocal_vi_frequency_var.set("60000")   # 2 × 60 kHz > the SR830's 102 kHz
        with self.assertRaisesRegex(ValueError, "SR830 #8 demodulation frequency"):
            a._parse_nonlocal_vi_settings()
        a = gate_page(sr830="1+2+3")
        with self.assertRaisesRegex(ValueError, "SR830 #8 harmonic must be one of"):
            a._parse_nonlocal_vi_settings()
        a = gate_page()
        a.nonlocal_vi_ac_controller.connected_lockin_keys = ()
        with self.assertRaisesRegex(ValueError, "not connected"):
            a._parse_nonlocal_vi_settings()


class WorkerTests(unittest.TestCase):
    def run_scan(self, sr830="1+2", zurich="1+2+3"):
        a = gate_page(sr830, zurich)
        settings = a._parse_nonlocal_vi_settings()
        settings["sample_interval_s"] = 0.01
        a.nonlocal_vi_stop_event = threading.Event()
        a.nonlocal_vi_interlock = app.NonLocalVIInterlock()
        a.nonlocal_vi_data_lock = threading.Lock()
        a.nonlocal_vi_time_rows, a.nonlocal_vi_summary_rows = [], []
        a.nonlocal_vi_pulse_rows, a.nonlocal_vi_current_rows, a.nonlocal_vi_resistance_rows = [], [], []
        a.post_ui = lambda callback: callback()
        a._redraw_nonlocal_vi_plots = mock.Mock()
        a._publish_reference_sync = mock.Mock()
        a.nonlocal_vi_progress = mock.Mock()
        a.nonlocal_vi_eta_var, a.nonlocal_vi_status_var, a.nonlocal_vi_output_var = Var(), Var(), Var()
        directory = Path(tempfile.mkdtemp())
        with mock.patch.object(app, "NONLOCAL_VI_HARMONIC_SETTLE_TAUS", 10.0):
            a._nonlocal_vi_scan_worker(settings, directory)
        with (directory / "VI_summary.csv").open(encoding="utf-8-sig") as file:
            summary = list(csv.DictReader(file))
        with (directory / "VI_raw.csv").open(encoding="utf-8-sig") as file:
            raw = list(csv.DictReader(file))
        return a, summary, raw

    def test_sr830_1_plus_2_and_zurich_1_plus_2_plus_3(self):
        a, summary, raw = self.run_scan()
        calls = a.nonlocal_vi_ac_controller.calls
        self.assertEqual([c for c in calls if c[0] in ("second", "third", "disable")],
                         [("second", 0.001, 10.0), ("third", 0.001, 10.0), ("disable", "1+2+3")])
        # Per point: 2ω, then back to 1ω; each order starts on its last range.
        # Three points (0 → 4 → 0 V).
        self.assertEqual([c[1:] for c in calls if c[0] == "harmonic"],
                         [("8", 2, None), ("8", 1, 17)] + [("8", 2, 12), ("8", 1, 17)] * 2)
        points = {row["point_index"] for row in summary}
        self.assertEqual(len(points), 3)
        found = sorted((row["point_index"], row["meter_key"], row["harmonic"]) for row in summary)
        self.assertEqual(found, sorted((p, key, order) for p in points for key, order in
                                       (("8", "1"), ("8", "2"), ("mfli", "1"), ("mfli", "2"), ("mfli", "3"))))
        means = {(row["meter_key"], row["harmonic"]): float(row["mean_voltage_V"]) for row in summary}
        self.assertEqual(means[("8", "1")], 1e-3)
        self.assertEqual(means[("8", "2")], 2e-6, "the 2ω pass read the 2ω signal")
        self.assertEqual(means[("mfli", "2")], 3e-7, "from Demod 2")
        self.assertEqual(means[("mfli", "3")], 4e-9, "from Demod 3")
        self.assertEqual({(row["meter_key"], row["harmonic"]) for row in raw}, set(means))
        orders = {(row[1], row[7]) for row in a.nonlocal_vi_time_rows}
        self.assertEqual(orders, set((key, int(order)) for key, order in means))

    def test_single_orders_read_once(self):
        a, summary, _raw = self.run_scan("1", "2")
        calls = a.nonlocal_vi_ac_controller.calls
        self.assertFalse([c for c in calls if c[0] in ("harmonic", "second", "third", "disable")])
        self.assertEqual(sorted({(row["meter_key"], row["harmonic"]) for row in summary}),
                         [("8", "1"), ("mfli", "2")])



class Canvas:
    """Records the text a plot draws; sized as a short Gate-VI row."""
    ppms_compact_plot, ppms_hide_x_labels = True, False

    def __init__(self, height):
        self.height, self.texts = height, []

    def winfo_width(self):
        return 600

    def winfo_height(self):
        return self.height

    def create_text(self, x, y, **options):
        self.texts.append((y, options.get("text")))

    def __getattr__(self, name):
        return lambda *args, **options: None


class TickLabelTests(unittest.TestCase):
    def y_labels(self, height):
        a = bare_app()
        canvas = Canvas(height)
        # 3.9 to 4.6 nV: eight ticks of 0.1, as the 3rd harmonic row in CI.
        a._draw_nonlocal_series(canvas, {"mfli": [(0.0, 3.9e-9), (1.0, 4.6e-9)]},
                                "Elapsed time (s)", "X", "waiting", auto_voltage_unit=True)
        return sorted(y for y, text in canvas.texts if text and text[0].isdigit() and y < height - 30)

    def test_labels_stay_16_px_apart_on_a_short_plot(self):
        short = self.y_labels(110)
        self.assertGreater(len(short), 1)
        self.assertGreaterEqual(min(b - a for a, b in zip(short, short[1:])), 16.0)
        self.assertGreater(len(self.y_labels(400)), len(short), "a tall plot labels every tick")



class Clock:
    """Simulated time: each wait moves it on, nothing sleeps."""

    def __init__(self):
        self.now = 1000.0

    def monotonic(self):
        return self.now

    def sleep(self, seconds):
        self.now += seconds

    def is_set(self):
        return False

    def wait(self, seconds):
        self.now += seconds
        return False


class ChargingSample(FakeSourceMeter):
    """A sample that charges up after each current change, as on |R| vs time
    (user, 2026-10-09): V = I·R·(1 − e^(−t/τ))."""

    def __init__(self, clock, resistance_ohm=939e6, tau_s=1.0):
        super().__init__(resistance_ohm)
        self.clock, self.tau_s, self.changed_at = clock, tau_s, clock.now

    def write(self, command):
        super().write(command)
        if command.upper().startswith(":SOUR:CURR "):
            self.changed_at = self.clock.now

    def query(self, command):
        if command.upper() == ":READ?" and self.mode == "CURR":
            charge = 1 - math.exp(-(self.clock.now - self.changed_at) / self.tau_s)
            return f"{self.current_source * self.resistance_ohm * charge:.6e},{self.current_source:.6e}"
        return super().query(command)


class ResistanceTests(unittest.TestCase):
    def measure(self, meter, clock):
        controller = connected_controller(meter)
        sources = []
        with mock.patch.object(app, "time", types.SimpleNamespace(monotonic=clock.monotonic, sleep=clock.sleep)):
            started = clock.now
            result = controller.measure_resistance(clock, status_callback=sources.append)
        return result, clock.now - started, sources

    def test_the_reading_settles_as_on_r_vs_time(self):
        clock = Clock()
        meter = ChargingSample(clock)
        result, took, sources = self.measure(meter, clock)
        self.assertEqual(sources, [1e-9], "1 nA, as |R| vs time chose for this sample")
        self.assertAlmostEqual(result["resistance_ohm"], 939e6, delta=939e6 * 0.02,
                               msg="within 2 % of the settled value")
        self.assertGreaterEqual(took, app.NONLOCAL_VI_RES_HOLD_S)
        self.assertEqual((app.NONLOCAL_VI_RES_HOLD_S, app.NONLOCAL_VI_RES_AVERAGE_S), (15.0, 5.0))
        self.assertLess(took, app.NONLOCAL_VI_RES_HOLD_S + 1.0, "about 15 s")
        self.assertGreaterEqual(result["sample_count"], 20, "the last 5 s averaged (a reading every 0.2 s)")
        self.assertAlmostEqual(result["resistance_ohm"], 939e6, delta=939e6 * 0.001,
                               msg="settled to 0.1 % after 10–15 s")
        self.assertEqual(meter.output, 0, "switched off afterwards")
        self.assertEqual(meter.mode, "VOLT", "back in the pulse set-up")

    def test_a_current_change_starts_the_hold_again(self):
        clock = Clock()
        meter = ChargingSample(clock, resistance_ohm=1e6, tau_s=0.01)
        result, took, sources = self.measure(meter, clock)
        # 1 MΩ: 1 nA reads 1 mV, below 0.05 V, so the current steps up to
        # 100 nA (0.1 V), as |R| vs time does, then holds 5 s there.
        self.assertEqual(sources, [1e-9, 3e-9, 1e-8, 3e-8, 1e-7])
        self.assertEqual(result["source_current_a"], 1e-7)
        self.assertAlmostEqual(result["resistance_ohm"], 1e6, delta=1e3)
        self.assertGreaterEqual(took, app.NONLOCAL_VI_RES_HOLD_S + 4 * app.NONLOCAL_VI_RES_SAMPLE_INTERVAL_S)

    def test_a_current_that_keeps_changing_fails_after_30_s(self):
        clock = Clock()
        meter = ChargingSample(clock)
        meter.query = lambda command, original=meter.query: (
            ("1.0e-02" if meter.current_source == 1e-9 else "3.0e+00") + f",{meter.current_source:.6e}"
            if command.upper() == ":READ?" and meter.mode == "CURR" else original(command))
        with self.assertRaisesRegex(RuntimeError, "did not settle within 30 s"):
            self.measure(meter, clock)
        self.assertEqual(meter.output, 0, "switched off after the failure")


if __name__ == "__main__":
    unittest.main()
