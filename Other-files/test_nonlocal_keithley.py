"""Non-local page: |R| vs time (moved from MOKE-24) and the I-V sweep.

Both run on the Gate-VI Keithley (NonLocalVI2410Controller).  No Tk and no
instrument: a stand-in canvas records the drawing, and a fake SourceMeter
answers the SCPI commands.
"""
import threading
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
        self.output = 0
        self.mode = "VOLT"

    def write(self, command):
        self.commands.append(command)
        upper = command.upper()
        value = command.rsplit(" ", 1)[-1]
        if upper == "*RST":
            self.output, self.voltage, self.ovp, self.mode = 0, 0.0, 210.0, "VOLT"
        elif upper.startswith(":SOUR:FUNC "):
            self.mode = value.upper()
        elif upper.startswith(":SOUR:VOLT:PROT "):
            self.ovp = float(value)
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
        if upper == ":READ?":
            if self.mode == "CURR":
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
                         "I–V sweep stopped after 1/49 points; voltage zeroed and output off")
        self.assertEqual(app_.nonlocal_iv_eta_var.get(), "Remaining: --")
        app_ = self.finished("iv", RuntimeError("bus timeout; more"), output_off=False)
        self.assertIn("stopped by an error after 1/49 points: bus timeout", app_.nonlocal_vi_status_var.get())
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
        with self.assertRaisesRegex(ValueError, "increase the step"):
            app.build_iv_sweep_voltages(-10, 10, 0.001)

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
        with self.assertRaisesRegex(RuntimeError, "reads 40 V, not the 20 V"):
            controller.start_iv_sweep(1e-3)
        self.assertEqual(meter.output, 0)

    def test_restore_leaves_the_output_off_in_the_pulse_setup(self):
        self.controller.start_iv_sweep(1e-3)
        self.controller.set_iv_voltage(5)
        self.meter.commands.clear()
        self.controller.restore_voltage_mode()
        self.assertEqual(self.meter.commands[:2], [":SOUR:VOLT 0", ":OUTP OFF"])
        self.assertIn(":SENS:CURR:PROT 0.001", self.meter.commands)
        self.assertEqual((self.meter.output, self.meter.voltage), (0, 0.0))
        self.assertEqual(self.controller.source_mode, "VOLT")
        with self.assertRaisesRegex(RuntimeError, "not set up for the I–V sweep"):
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

    def settings(self, voltages=(-1.0, 0.0, 1.0)):
        return {"voltages": list(voltages), "current_limit_a": 1e-3, "settle_s": 0.0}

    def test_a_sweep_reads_every_point_then_switches_off(self):
        self.app._nonlocal_iv_worker(self.settings())
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
            self.app._nonlocal_iv_worker(self.settings())
        self.assertEqual((self.meter.output, self.meter.voltage), (0, 0.0))
        self.assertEqual(self.app.nonlocal_vi_interlock.phase, "IDLE")
        self.app.nonlocal_keithley_stop_event.clear()
        self.meter.query = (lambda original: lambda command: (_ for _ in ()).throw(
            RuntimeError("VI_ERROR_TMO")) if command == ":READ?" else original(command))(self.meter.query)
        with self.assertRaisesRegex(RuntimeError, "VI_ERROR_TMO"):
            self.app._nonlocal_iv_worker(self.settings())
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


if __name__ == "__main__":
    unittest.main()
