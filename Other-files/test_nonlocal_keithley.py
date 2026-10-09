"""Non-local page: |R| vs time (moved from MOKE-24) and the I-V sweep.

Both run on the Gate-VI Keithley (NonLocalVI2410Controller).  No Tk and no
instrument: a stand-in canvas records the drawing, and a fake SourceMeter
answers the SCPI commands.
"""
import threading
from pathlib import Path
import unittest
from concurrent.futures import Future
from unittest import mock

from test_ppms_motor_debug import app


class Canvas:
    def __init__(self, width=1900, height=260):
        self.items = []
        self.size = (width, height)

    def delete(self, *args):
        self.items.clear()

    def winfo_width(self):
        return self.size[0]

    def winfo_height(self):
        return self.size[1]

    def create_line(self, *args, **kwargs):
        self.items.append(("line", len(args), kwargs.get("fill")))

    def create_text(self, *args, **kwargs):
        self.items.append(("text", 0, kwargs.get("text")))

    def create_rectangle(self, *args, **kwargs):
        self.items.append(("rectangle", 0, kwargs.get("fill")))

    def create_oval(self, *args, **kwargs):
        self.items.append(("oval", 0, None))

    def texts(self):
        return [text for kind, _, text in self.items if kind == "text"]


class Var:
    def __init__(self, value=""):
        self.value = value

    def get(self):
        return self.value

    def set(self, value):
        self.value = value


def bare_app():
    app_ = app.CombinedControlApp.__new__(app.CombinedControlApp)
    app_._canvas_is_visible = lambda canvas: True
    app_._draw_plot_grid = lambda *args: None
    app_.ui_font_family, app_.ui_font_size, app_.ui_small_font_size = "Segoe UI", 10, 9
    return app_


class FakeSourceMeter:
    """A 2410/2400 on the pulse page: a 1 GΩ sample in voltage mode, so the
    current is V / 1 GΩ (nA), clamped at the current limit."""

    def __init__(self, resistance_ohm=1e9):
        self.commands = []
        self.resistance_ohm = resistance_ohm
        self.voltage = 0.0
        self.current_source = 0.0
        self.limit = 1e-3
        self.ovp = 210.0
        self.range = 21.0
        self.output = 0
        self.mode = "VOLT"

    def write(self, command):
        self.commands.append(command)
        upper = command.upper()
        value = command.rsplit(" ", 1)[-1]
        if upper == "*RST":
            self.output, self.voltage, self.ovp, self.mode, self.range = 0, 0.0, 210.0, "VOLT", 21.0
        elif upper.startswith(":SOUR:FUNC "):
            self.mode = value.upper()
        elif upper.startswith(":SOUR:VOLT:PROT "):
            self.ovp = float(value)
        elif upper.startswith(":SOUR:VOLT:RANG "):
            # The range's full scale, as a 2400 reads it back (21, 210, 1100 V).
            self.range = float(value) * 1.05 if float(value) < 1000 else 1100.0
        elif upper.startswith(":SOUR:VOLT "):
            self.voltage = float(value)
        elif upper.startswith(":SOUR:CURR "):
            self.current_source = float(value)
        elif upper.startswith(":SENS:CURR:PROT "):
            self.limit = float(value)
        elif upper == ":OUTP ON":
            self.output = 1
        elif upper == ":OUTP OFF":
            self.output = 0

    def query(self, command):
        upper = command.upper()
        if upper == "SYST:ERR?":
            return '0,"No error"'
        if upper == ":OUTP?":
            return str(self.output)
        if upper == ":SOUR:VOLT:PROT?":
            return str(self.ovp)
        if upper == ":SOUR:VOLT:RANG?":
            return str(self.range)
        if upper == ":SENS:CURR:PROT?":
            return str(self.limit)
        if upper == ":READ?":
            if self.mode == "CURR":
                if self.resistance_ohm == float("inf"):  # open: at the 5 V limit, no current
                    return "5.000000e+00,0.000000e+00"
                return f"{self.current_source * self.resistance_ohm:.6e},{self.current_source:.6e}"
            current = self.voltage / self.resistance_ohm
            current = max(-self.limit, min(self.limit, current))
            return f"{self.voltage:.6e},{current:.6e}"
        raise AssertionError(f"unexpected query: {command}")

    def close(self):
        pass


def connected_controller(meter):
    controller = app.NonLocalVI2410Controller(lambda _message: None)
    controller.instrument = meter
    controller.resource_name = "GPIB0::24::INSTR"
    controller.identity = "KEITHLEY INSTRUMENTS INC.,MODEL 2400,1,C30"
    controller.source_mode = "VOLT"
    return controller


# ------------------------------------------------------------- |R| vs time
class ResistancePlotTests(unittest.TestCase):
    def draw(self, rows):
        app_ = bare_app()
        app_.nonlocal_resistance_canvas = Canvas()
        app_.nonlocal_resistance_rows = rows
        with mock.patch.object(app, "np", None):
            app_._redraw_nonlocal_resistance_plot()
        return app_.nonlocal_resistance_canvas.items

    def test_a_steady_value_is_drawn_without_numpy(self):
        # As on the lab PC: 369.711 MΩ measured continuously at 1 nA.
        items = self.draw([(4.0 + 0.5 * i, 369.711e6) for i in range(32)])
        texts = [text for kind, _, text in items if kind == "text"]
        self.assertTrue(any(kind == "line" and count > 4 and fill == "#7a3db8"
                            for kind, count, fill in items), "the |R| trace is drawn")
        self.assertIn("Current |R| = 369.711 MΩ", texts)
        self.assertIn("|R| (MΩ)", texts)
        axis = [float(text) for text in texts if text and text.replace(".", "", 1).isdigit()]
        self.assertGreaterEqual(len([value for value in axis if 340 <= value <= 400]), 2,
                                f"|R| axis numbers around 369.7: {texts}")

    def test_a_changing_value_and_a_single_point(self):
        items = self.draw([(0.5 * i, 1e3 * (1 + 0.01 * i)) for i in range(10)])
        self.assertTrue(any(kind == "line" and count > 4 for kind, count, _ in items))
        items = self.draw([(1.0, 52.0)])
        self.assertTrue(any(kind == "oval" for kind, _, _ in items), "one point is drawn as a dot")


class FinishTests(unittest.TestCase):
    def finished(self, kind, error, *, output_off=True, redraw_error=None):
        app_ = bare_app()
        app_.closing = True  # no dialogs
        app_.nonlocal_keithley_task = kind
        app_.nonlocal_keithley_output_off = output_off
        app_.nonlocal_vi_2410_controller = app.NonLocalVI2410Controller(lambda _m: None)
        app_.nonlocal_vi_output_var, app_.nonlocal_vi_status_var = Var(), Var()
        app_.nonlocal_iv_eta_var = Var()
        app_.nonlocal_iv_rows, app_.nonlocal_iv_total_points = [(1, 0, 0, 0, 0, False)], 49
        redraw = mock.Mock(side_effect=redraw_error)
        app_._redraw_nonlocal_resistance_plot = app_._redraw_nonlocal_iv_plot = redraw
        app_._update_nonlocal_vi_controls = mock.Mock()
        future = Future()
        future.set_exception(error)
        finish = (app_._finish_nonlocal_resistance if kind == "resistance"
                  else app_._finish_nonlocal_iv_sweep)
        if redraw_error is None:
            finish(future)
        else:
            with self.assertRaises(type(redraw_error)):
                finish(future)
        return app_

    def test_start_is_offered_again_after_stop_even_if_the_redraw_fails(self):
        app_ = self.finished("resistance", InterruptedError("stopped"),
                             redraw_error=AttributeError("'NoneType' object has no attribute 'asarray'"))
        self.assertIsNone(app_.nonlocal_keithley_task)
        app_._update_nonlocal_vi_controls.assert_called_once()
        self.assertIn("finished", app_.nonlocal_vi_status_var.get())
        self.assertEqual(app_.nonlocal_vi_output_var.get(), "Keithley 2410 and 6221 outputs: OFF")

    def test_a_stopped_or_failed_sweep_says_so(self):
        app_ = self.finished("iv", InterruptedError("stopped"))
        self.assertEqual(app_.nonlocal_vi_status_var.get(),
                         "2400-IV sweep stopped after 1/49 points; voltage zeroed and output off."
                         " No CSV was written.")
        self.assertEqual(app_.nonlocal_iv_eta_var.get(), "Remaining: --")
        app_ = self.finished("iv", RuntimeError("bus timeout; more"), output_off=False)
        self.assertIn("2400-IV sweep stopped by an error after 1/49 points: bus timeout", app_.nonlocal_vi_status_var.get())
        self.assertEqual(app_.nonlocal_vi_output_var.get(), "Keithley 2410 output: check it",
                         "an output that may still be on is not called off")


# ----------------------------------------------------------------- I-V sweep
class SweepValuesTests(unittest.TestCase):
    def test_the_figure_sweep(self):
        voltages = app.build_iv_sweep_voltages(-6, 6, 0.25)
        self.assertEqual(len(voltages), 49)
        self.assertEqual((voltages[0], voltages[1], voltages[-1]), (-6.0, -5.75, 6.0))
        self.assertEqual(voltages, sorted(voltages), "one way, Start to End")

    def test_the_end_is_kept_and_limits_apply(self):
        self.assertEqual(app.build_iv_sweep_voltages(1, -1, 0.3), [1.0, 0.7, 0.4, 0.1, -0.2, -0.5, -0.8, -1.0])
        self.assertEqual(app.build_iv_sweep_voltages(2, 2, 0.1), [2.0])
        with self.assertRaisesRegex(ValueError, "within ±20 V"):
            app.build_iv_sweep_voltages(-25, 6, 1)
        self.assertEqual(len(app.build_iv_sweep_voltages(-1000, 1000, 2.5, 1000)), 801)

    def test_the_sweep_settings_follow_the_model(self):
        a = bare_app()
        a.nonlocal_iv_start_var, a.nonlocal_iv_end_var = Var("-50"), Var("50")
        a.nonlocal_iv_step_var, a.nonlocal_iv_settle_var = Var("2.5"), Var("0.5")
        a.nonlocal_iv_limit_var = Var("0.001")
        a.nonlocal_vi_2410_controller = mock.Mock(model="2400", resource_name="GPIB0::24::INSTR", identity="")
        settings = a._parse_nonlocal_iv_settings()
        self.assertEqual((settings["max_abs_v"], settings["source"]["range_v"], settings["source"]["ovp_v"]),
                         (50.0, 200.0, 60.0))
        self.assertEqual(len(settings["voltages"]), 41)
        a.nonlocal_iv_start_var.set("-250")
        with self.assertRaisesRegex(ValueError, "within ±200 V"):
            a._parse_nonlocal_iv_settings()
        a.nonlocal_vi_2410_controller.model = "2410"
        a.nonlocal_iv_start_var.set("-1000")
        self.assertEqual(a._parse_nonlocal_iv_settings()["source"]["ovp_v"], 1100.0)
        a.nonlocal_iv_limit_var.set("0.05")
        with self.assertRaisesRegex(ValueError, "current limit is at most 0.021 A"):
            a._parse_nonlocal_iv_settings()
        with self.assertRaisesRegex(ValueError, "increase the step"):
            app.build_iv_sweep_voltages(-10, 10, 0.001)

    def test_the_defaults(self):
        # User, 2026-10-09: -50 → 50 V in 5 V steps, 0.5 s settle (21 points).
        self.assertEqual((app.NONLOCAL_IV_SWEEP_START_V, app.NONLOCAL_IV_SWEEP_END_V,
                          app.NONLOCAL_IV_SWEEP_STEP_V, app.NONLOCAL_IV_SWEEP_SETTLE_S), (-50.0, 50.0, 5.0, 0.5))
        self.assertEqual(len(app.build_iv_sweep_voltages(-50, 50, 5, 200)), 21)

    def test_current_units(self):
        self.assertEqual(app.format_current(-1.833e-8), "-18.33 nA")
        self.assertEqual(app.format_current(4e-13), "0.4 pA")
        self.assertEqual(app.format_current(2.5e-4), "250 µA")
        self.assertEqual(app.format_current(1e-3), "1 mA")


class SweepPlotTests(unittest.TestCase):
    def draw(self, rows, sample="1µm Pt-Pt", size=(900, 600)):
        app_ = bare_app()
        app_.nonlocal_iv_canvas = Canvas(*size)
        app_.nonlocal_iv_rows = rows
        app_.nonlocal_iv_settings = {"start_v": -6.0, "end_v": 6.0, "sample": sample}
        with mock.patch.object(app, "np", None):
            app_._redraw_nonlocal_iv_plot()
        return app_.nonlocal_iv_canvas

    def test_drawn_like_the_figure(self):
        # Noise of about ±20 nA on an open Pt-Pt gap, as in the figure.
        rows = [(i + 1, v, v, (-1) ** i * 2e-8, i * 0.6, False)
                for i, v in enumerate(app.build_iv_sweep_voltages(-6, 6, 0.25))]
        canvas = self.draw(rows)
        texts = canvas.texts()
        for label in ("Voltage (V)", "Current (nA)", "1µm Pt-Pt", "-6", "-4", "0", "4", "6"):
            self.assertIn(label, texts)
        squares = [fill for kind, _, fill in canvas.items if kind == "rectangle" and fill == "#111111"]
        self.assertEqual(len(squares), 49, "one black square per point")
        self.assertTrue(any(kind == "line" and count == 98 for kind, count, _ in canvas.items),
                        "a line joins the 49 points")
        self.assertNotIn("red: at the current limit", texts)

    def test_points_at_the_limit_are_red_and_empty_plot_says_what_comes(self):
        canvas = self.draw([(1, -1.0, -1.0, -1e-3, 0.5, True), (2, 0.0, 0.0, 0.0, 1.0, False)])
        self.assertIn("Current (mA)", canvas.texts())
        self.assertIn("red: at the current limit", canvas.texts())
        self.assertIn("#c62828", [fill for kind, _, fill in canvas.items if kind == "rectangle"])
        canvas = self.draw([], sample="")
        self.assertIn("The I–V curve is drawn point by point once a sweep starts", canvas.texts())


class SweepControllerTests(unittest.TestCase):
    def setUp(self):
        patcher = mock.patch.object(app.time, "sleep")
        patcher.start()
        self.addCleanup(patcher.stop)
        self.meter = FakeSourceMeter()
        self.controller = connected_controller(self.meter)

    def test_start_sets_the_limit_and_checks_the_ovp(self):
        self.controller.start_iv_sweep(1e-6)
        commands = self.meter.commands
        self.assertEqual(commands[0], "*RST", "from a known state")
        for command in (":SOUR:VOLT:PROT 20", ":SOUR:VOLT:RANG 20", ":SENS:CURR:PROT 1e-06",
                        ":SENS:CURR:NPLC 1", ":OUTP ON"):
            self.assertIn(command, commands)
        self.assertLess(commands.index(":SOUR:VOLT 0"), commands.index(":OUTP ON"), "on at 0 V")
        self.assertEqual(self.meter.output, 1)
        self.controller.set_iv_voltage(-6)
        self.assertEqual(self.controller.read_iv_point(), (-6.0, -6e-9))
        with self.assertRaisesRegex(ValueError, "within ±20 V"):
            self.controller.set_iv_voltage(20.5)

    def test_a_wrong_ovp_or_limit_stops_before_the_output_is_on(self):
        with self.assertRaisesRegex(ValueError, "at most 0.1 A"):
            self.controller.start_iv_sweep(0.5)
        meter = FakeSourceMeter()
        meter.write = (lambda original: lambda command: original(
            ":SOUR:VOLT:PROT 40" if command.startswith(":SOUR:VOLT:PROT") else command))(meter.write)
        controller = connected_controller(meter)
        with self.assertRaisesRegex(RuntimeError, "reads OVP 40 V"):
            controller.start_iv_sweep(1e-3)
        self.assertEqual(meter.output, 0)

    def start(self, model, limit_a, max_abs_v):
        meter = FakeSourceMeter()
        controller = connected_controller(meter)
        controller.model = model
        controller.start_iv_sweep(limit_a, max_abs_v)
        return meter, controller

    def test_above_20_v_the_high_range_and_the_ovp_level_above_the_sweep(self):
        # User, 2026-10-09: ±200 V on a 2400, ±1000 V on a 2410.
        for model, max_abs_v, source_range, ovp in (
            # Above ±20 V the OVP level is at least 5 % above the voltages
            # (the levels are ±5 %): 160 V on a 2400 takes "none" (210 V).
            ("2400", 20, 21.0, 20.0), ("2400", 40, 210.0, 60.0), ("2400", 50, 210.0, 60.0),
            ("2400", 150, 210.0, 160.0), ("2400", 160, 210.0, 210.0), ("2400", 200, 210.0, 210.0),
            ("2410", 150, 1100.0, 200.0), ("2410", 1000, 1100.0, 1100.0),
        ):
            with self.subTest(model=model, max_abs_v=max_abs_v):
                meter, controller = self.start(model, 1e-3, max_abs_v)
                self.assertEqual((meter.range, meter.ovp), (source_range, ovp))
                self.assertLess(meter.commands.index(f":SOUR:VOLT:PROT {ovp:g}" if ovp != 20 else ":SOUR:VOLT:PROT 20"),
                                meter.commands.index(":OUTP ON"), "set before the output goes on")
                controller.set_iv_voltage(-max_abs_v)
                with self.assertRaisesRegex(ValueError, f"within ±{max_abs_v:g} V"):
                    controller.set_iv_voltage(max_abs_v + 0.5)
                controller.restore_voltage_mode()
                self.assertEqual((meter.output, meter.range, meter.ovp), (0, 21.0, 20.0), "back to the pulse setup")
                self.assertEqual(controller.iv_sweep_max_abs_v, 20.0)

    def test_the_model_limits_and_the_high_range_current_limit(self):
        for model, too_high in (("2400", 200.5), ("2410", 1000.5)):
            with self.assertRaisesRegex(ValueError, "must be within"):
                self.start(model, 1e-3, too_high)
        with self.assertRaisesRegex(ValueError, "at most 0.021 A on the Keithley 2410's 1000 V range"):
            self.start("2410", 0.05, 100)
        self.start("2410", 0.05, 20)   # 20 V range: up to 0.1 A
        self.start("2400", 0.1, 200)

    def test_an_ovp_below_the_sweep_stops_before_the_output_is_on(self):
        meter = FakeSourceMeter()
        meter.write = (lambda original: lambda command: original(
            ":SOUR:VOLT:PROT 160" if command.startswith(":SOUR:VOLT:PROT 210") else command))(meter.write)
        controller = connected_controller(meter)
        controller.model = "2400"
        with self.assertRaisesRegex(RuntimeError, "reads 160 V, not the 210 V"):
            controller.start_iv_sweep(1e-3, 200)
        self.assertEqual(meter.output, 0)

    def test_set_leaves_the_output_on_at_the_voltage(self):
        # User, 2026-10-09: a 2400 voltage left on, from 2400-IV settings.
        meter = FakeSourceMeter()
        controller = connected_controller(meter)
        controller.model = "2400"
        self.assertEqual(controller.set_dc_voltage(50, 1e-3), (50.0, 5e-8))
        self.assertEqual((meter.output, meter.voltage, meter.range, meter.ovp), (1, 50.0, 210.0, 60.0))
        self.assertLess(meter.commands.index(":SOUR:VOLT:PROT 60"), meter.commands.index(":OUTP ON"))
        self.assertLess(meter.commands.index(":OUTP ON"), meter.commands.index(":SOUR:VOLT 50"),
                        "on at 0 V, then the voltage")
        with self.assertRaisesRegex(ValueError, "within ±200 V"):
            controller.set_dc_voltage(250, 1e-3)
        controller.model = "2410"
        with self.assertRaisesRegex(ValueError, "at most 0.021 A"):
            controller.set_dc_voltage(500, 0.05)

    def test_after_high_voltage_the_output_drains_at_0_v_before_it_goes_off(self):
        # Review, 2026-10-09: a charged sample is drained, not left at 1000 V.
        meter = FakeSourceMeter()
        controller = connected_controller(meter)
        controller.model = "2410"
        controller.set_dc_voltage(-1000, 1e-3)
        with mock.patch.object(app.time, "sleep", lambda s: meter.commands.append(f"sleep {s:g}")):
            meter.commands.clear()
            controller.safe_shutdown(reset=False)
            self.assertEqual(meter.commands, [":SOUR:VOLT 0", "sleep 1", ":OUTP OFF"])
            # The next setup (*RST switches off at once) drains first too.
            controller.set_dc_voltage(500, 1e-3)
            meter.commands.clear()
            controller.setup_voltage_mode(1e-3)
            self.assertEqual(meter.commands[:4], [":SOUR:VOLT 0", "sleep 1", ":OUTP OFF", "*RST"])
            self.assertFalse(controller.high_voltage_active)
            # Up to ±20 V nothing waits.
            controller.set_dc_voltage(5, 1e-3)
            meter.commands.clear()
            controller.safe_shutdown(reset=False)
            self.assertEqual(meter.commands, [":SOUR:VOLT 0", ":OUTP OFF"])

    def test_a_failed_set_leaves_the_output_off(self):
        meter = FakeSourceMeter()
        meter.query = (lambda original: lambda command: (
            "2e-3" if command.upper() == ":SENS:CURR:PROT?" else original(command)))(meter.query)
        controller = connected_controller(meter)
        controller.model = "2400"
        with self.assertRaisesRegex(RuntimeError, "current limit reads"):
            controller.set_dc_voltage(50, 1e-3)
        self.assertEqual(meter.output, 0)

    def test_each_pulse_takes_its_own_range_and_ovp(self):
        meter = FakeSourceMeter()
        controller = connected_controller(meter)
        controller.model = "2400"
        controller.setup_voltage_mode(1e-3)
        controller.apply_voltage_pulse(100, 0.0, threading.Event())
        commands = meter.commands
        self.assertLess(commands.index(":SOUR:VOLT:PROT 120"), commands.index(":OUTP ON"), "105 V → 120 V")
        self.assertLess(commands.index(":SOUR:VOLT:RANG 200"), commands.index(":OUTP ON"))
        self.assertIn(":SOUR:VOLT 100", commands)
        self.assertEqual(meter.output, 0, "off after the pulse")
        controller.apply_voltage_pulse(-5, 0.0, threading.Event())
        self.assertEqual((meter.range, meter.ovp, meter.output), (21.0, 20.0, 0), "back to the 20 V range")
        with self.assertRaisesRegex(ValueError, "±200 V"):
            controller.apply_voltage_pulse(201, 0.0, threading.Event())
        controller.model = "2410"
        controller.apply_voltage_pulse(-1000, 0.0, threading.Event())
        self.assertEqual((meter.range, meter.ovp, meter.output), (1100.0, 1100.0, 0))

    def test_restore_leaves_the_output_off_in_the_pulse_setup(self):
        self.controller.start_iv_sweep(1e-3)
        self.controller.set_iv_voltage(5)
        self.meter.commands.clear()
        self.controller.restore_voltage_mode()
        self.assertEqual(self.meter.commands[:2], [":SOUR:VOLT 0", ":OUTP OFF"])
        self.assertIn(":SENS:CURR:PROT 0.001", self.meter.commands)
        self.assertEqual((self.meter.output, self.meter.voltage), (0, 0.0))
        self.assertEqual(self.controller.source_mode, "VOLT")
        with self.assertRaisesRegex(RuntimeError, "not set up for the 2400-IV sweep"):
            self.controller.set_iv_voltage(1)


class Source:
    """The Gate-VI 6221: its output is switched off before the Keithley sources."""

    def __init__(self, log):
        self.log = log
        self.source_connected = True

    def output_off(self):
        self.log.append("6221 off")

    def confirm_output_off(self, context):
        self.log.append("6221 confirmed off")


class WorkerTests(unittest.TestCase):
    def setUp(self):
        patcher = mock.patch.object(app.time, "sleep")
        patcher.start()
        self.addCleanup(patcher.stop)
        self.meter = FakeSourceMeter()
        self.app = bare_app()
        self.log = []
        self.app.nonlocal_vi_2410_controller = connected_controller(self.meter)
        self.app.nonlocal_vi_ac_controller = Source(self.log)
        self.app.nonlocal_vi_interlock = app.NonLocalVIInterlock()
        self.app.nonlocal_keithley_stop_event = threading.Event()
        self.app.nonlocal_keithley_output_off = False
        self.posted = []
        self.app.post_ui = self.posted.append
        self.app.nonlocal_vi_status_var = Var()
        self.app.nonlocal_iv6221_source_var = Var()

    def settings(self, voltages=(-1.0, 0.0, 1.0)):
        return {"voltages": list(voltages), "current_limit_a": 1e-3, "settle_s": 0.0}

    def csv_path(self):
        import tempfile
        return Path(tempfile.mkdtemp()) / "2400-IV_test.csv"

    def test_a_sweep_reads_every_point_then_switches_off(self):
        path = self.csv_path()
        self.app._nonlocal_iv_worker(self.settings(), path)
        # Each point went to the live CSV as it was read.
        lines = path.read_text(encoding="utf-8-sig").splitlines()
        self.assertEqual(lines[0], "point,set_voltage_V,source_voltage_V,current_A,elapsed_s,at_current_limit")
        self.assertEqual([line.split(",")[:4] for line in lines[1:]],
                         [["1", "-1", "-1", "-1e-09"], ["2", "0", "0", "0"], ["3", "1", "1", "1e-09"]])
        self.assertEqual(self.log, ["6221 off", "6221 confirmed off"])
        rows = []
        self.app._append_nonlocal_iv_point = rows.append
        for callback in self.posted:
            callback()
        self.assertEqual([(row[0], row[1], row[3]) for row in rows],
                         [(1, -1.0, -1e-9), (2, 0.0, 0.0), (3, 1.0, 1e-9)])
        self.assertEqual((self.meter.output, self.meter.voltage), (0, 0.0))
        self.assertTrue(self.app.nonlocal_keithley_output_off)
        self.assertEqual(self.app.nonlocal_vi_interlock.phase, "IDLE")

    def test_stop_and_errors_still_switch_off(self):
        self.app.nonlocal_keithley_stop_event.set()
        with self.assertRaises(InterruptedError):
            self.app._nonlocal_iv_worker(self.settings(), self.csv_path())
        self.assertEqual(self.log, [], "a Stop before the start switches nothing")
        self.assertNotIn(":OUTP ON", self.meter.commands)
        self.assertEqual((self.meter.output, self.meter.voltage), (0, 0.0))
        self.assertEqual(self.app.nonlocal_vi_interlock.phase, "IDLE")
        self.app.nonlocal_keithley_stop_event.clear()
        self.meter.query = (lambda original: lambda command: (_ for _ in ()).throw(
            RuntimeError("VI_ERROR_TMO")) if command == ":READ?" else original(command))(self.meter.query)
        path = self.csv_path()
        with self.assertRaisesRegex(RuntimeError, "VI_ERROR_TMO"):
            self.app._nonlocal_iv_worker(self.settings(), path)
        self.assertEqual(len(path.read_text(encoding="utf-8-sig").splitlines()), 1,
                         "the header was written before the failed point")
        self.assertEqual((self.meter.output, self.meter.voltage), (0, 0.0))
        self.assertEqual(self.app.nonlocal_vi_interlock.phase, "IDLE")

    def test_resistance_autoranges_and_stops_cleanly(self):
        # 1 MΩ: 1 nA gives 1 mV, below the 50 mV target, so the test
        # current climbs to 100 nA (0.1 V).
        self.meter.resistance_ohm = 1e6
        stop = self.app.nonlocal_keithley_stop_event
        samples = []

        def post(callback):
            self.posted.append(callback)
            if len(self.posted) > 12:
                stop.set()

        self.app.post_ui = post
        self.app._append_nonlocal_resistance_sample = lambda *values: samples.append(values)
        with self.assertRaises(InterruptedError):
            with mock.patch.object(stop, "wait", side_effect=lambda timeout=None: stop.is_set()):
                self.app._nonlocal_resistance_worker()
        for callback in self.posted:
            callback()
        self.assertTrue(samples, "samples were published")
        self.assertAlmostEqual(samples[-1][3], 1e6, delta=1)
        self.assertEqual(samples[-1][4], 1e-7)
        self.assertEqual(self.meter.output, 0)
        self.assertEqual(self.meter.mode, "VOLT", "back in the pulse setup")
        self.assertEqual(self.app.nonlocal_vi_interlock.phase, "IDLE")


class SixTwoTwoOneOffTests(WorkerTests):
    """|R| and 2400-IV switch the 6221 off first (review of 2026-10-08)."""

    def test_the_6221_label_says_off_only_once_read_back_off(self):
        self.app._run_on_nonlocal_keithley(app.NonLocalVIInterlock.RESISTANCE, lambda controller: None)
        for callback in self.posted:
            callback()
        self.assertEqual(self.app.nonlocal_iv6221_source_var.get(), "6221: OFF")
        self.assertFalse(self.app.__dict__.get("nonlocal_6221_off_unconfirmed"))

    def test_a_6221_that_will_not_switch_off_is_named(self):
        def still_on(context):
            raise RuntimeError(f"6221 output is still ON {context}")

        self.app.nonlocal_vi_ac_controller.confirm_output_off = still_on
        with self.assertRaisesRegex(RuntimeError, "still ON"):
            self.app._run_on_nonlocal_keithley(app.NonLocalVIInterlock.RESISTANCE, lambda controller: None)
        for callback in self.posted:
            callback()
        self.assertEqual(self.app.nonlocal_iv6221_source_var.get(), "6221: check the output")
        self.assertEqual(self.app._nonlocal_output_warning(), "; check the 6221 output")
        self.assertEqual(self.app._nonlocal_output_text(), "6221 output: check it")
        self.assertEqual(self.meter.output, 0, "the Keithley was never switched on")


class OpenCircuitTests(unittest.TestCase):
    def test_an_open_circuit_point_is_skipped_not_fatal(self):
        with mock.patch.object(app.time, "sleep"):
            meter = FakeSourceMeter(resistance_ohm=float("inf"))
            controller = connected_controller(meter)
            controller.start_resistance(1e-9)
            voltage, current, resistance = controller.read_resistance()
        self.assertEqual((voltage, current), (5.0, 0.0))
        self.assertNotEqual(resistance, resistance, "NaN: the worker skips this point")


class StartQuestionTests(unittest.TestCase):
    def setUp(self):
        self.app = a = bare_app()
        a.root = None
        a.nonlocal_keithley_task = None
        a._nonlocal_keithley_free = lambda what: True
        a.nonlocal_iv_safety_var = Var(True)
        a.active_file_exports = set()
        a.nonlocal_vi_2410_controller = app.NonLocalVI2410Controller(lambda _m: None)
        a.nonlocal_iv_start_var, a.nonlocal_iv_end_var = Var("-6"), Var("6")
        a.nonlocal_iv_step_var, a.nonlocal_iv_settle_var = Var("0.25"), Var("0.5")
        a.nonlocal_iv_limit_var = Var("1e-6")
        a._begin_nonlocal_keithley_task = mock.Mock()

    def ask(self, answer, folder=""):
        with mock.patch.object(app.messagebox, "askyesno", return_value=answer) as question, \
                mock.patch.object(app.filedialog, "askdirectory", return_value=folder):
            self.app.start_nonlocal_iv_sweep()
        return question.call_args[0][1] if question.called else None

    def test_no_answer_or_no_folder_starts_nothing(self):
        self.app.nonlocal_iv_rows = [(1,)] * 49
        self.app.nonlocal_iv_directory = Path(".")
        text = self.ask(False)
        self.assertIn("-6 V → 6 V in 0.25 V steps, 49 points, 0.5 s settle", text)
        self.assertIn("Current limit: 1e-06 A", text)
        self.assertNotIn("Sample", text, "no Sample box (user, 2026-10-09)")
        self.assertIn("run folder, with a live CSV and the parameter log", text)
        self.ask(True, folder="")
        self.app._begin_nonlocal_keithley_task.assert_not_called()
        self.assertEqual(len(self.app.nonlocal_iv_rows), 49, "the last sweep stays on the plot")

    def test_yes_and_a_folder_makes_a_run_folder_with_the_log_and_a_live_csv(self):
        import tempfile
        a = self.app
        a.nonlocal_iv_rows = [(1,)] * 49
        a.nonlocal_iv_directory = Path(".")
        a.nonlocal_iv_progress = mock.Mock()
        a.nonlocal_iv_eta_var, a.nonlocal_iv_point_var = Var(), Var()
        a.nonlocal_vi_status_var, a.nonlocal_vi_output_var = Var(), Var()
        a._redraw_nonlocal_iv_plot = lambda: None
        folder = tempfile.mkdtemp()
        self.ask(True, folder=folder)
        a._begin_nonlocal_keithley_task.assert_called_once()
        self.assertEqual(a.nonlocal_iv_rows, [])
        self.assertEqual(a.nonlocal_iv_settings["current_limit_a"], 1e-6)
        csv_path = a.nonlocal_iv_csv_path
        # One folder per run, as on the STFMR and Gate-VI pages.
        self.assertEqual(csv_path.parent.parent, Path(folder))
        self.assertRegex(csv_path.parent.name, r"^2400-IV_\d{8}_\d{6}", "no sample name (no Sample box)")
        self.assertEqual(csv_path.name, csv_path.parent.name + ".csv")
        log = csv_path.with_name(csv_path.stem + "_parameters.log").read_text(encoding="utf-8")
        self.assertIn("sweep.current_limit_A=1e-06", log)
        self.assertIn(f"output.csv={csv_path.name}", log)


if __name__ == "__main__":
    unittest.main()
