"""MS-fourState.py: settings, the order of every instrument action, and the
values read, against simulated instruments.

The 6221 and SR830 are simulated at the level of their commands, so the
program's real 6221 controller (pulse and sine) and SR830 helpers run.  The
fake PPMS ramps the field in simulated time and the fake sample takes the
state written by the last pulse.  Only WindowTests needs a display.
"""
import importlib.util
import math
import sys
import threading
import tkinter as tk
import unittest
from pathlib import Path
from unittest import mock

PROGRAM = Path(__file__).resolve().parents[1] / "MS-fourState.py"
spec = importlib.util.spec_from_file_location("ms_four_state", PROGRAM)
ms = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = ms
spec.loader.exec_module(ms)
core = ms.core

HALL_OHM = {"D": 0.060, "A": 0.022, "B": -0.025, "E": -0.055}


class Clock:
    def __init__(self):
        self.now = 1000.0

    def __call__(self):
        return self.now


class StopEvent:
    """Waits move the simulated clock; ``stop_when`` sets it at a condition."""

    def __init__(self, clock):
        self.clock = clock
        self.flag = False
        self.stop_when = None

    def is_set(self):
        if self.stop_when is not None and self.stop_when():
            self.flag = True
        return self.flag

    def set(self):
        self.flag = True

    def wait(self, seconds):
        self.clock.now += seconds
        return self.is_set()


class Sample:
    """Takes D/E at 0 deg (by the field sign) and A/B at 270 deg (by H x I)."""

    def __init__(self):
        self.state = "E"

    def pulse(self, current_a, field_oe, angle_deg):
        if abs(angle_deg) < 1:
            self.state = "D" if field_oe > 0 else "E"
        elif abs(angle_deg - 270) < 1:
            self.state = "A" if field_oe * current_a < 0 else "B"


class World:
    def __init__(self):
        self.clock = Clock()
        self.stop = StopEvent(self.clock)
        self.events = []
        self.sample = Sample()
        self.ppms = FakePPMS(self)
        self.motor = FakeMotor(self)
        self.source_device = Fake6221(self)
        self.lockin_device = FakeSR830(self)
        self.source = core.Keithley6221Controller(lambda message: None)
        self.source.instrument = self.source_device
        self.lockin = core.SafeVisaIdentityController(lambda message: None, "SR830", "SR830")
        self.lockin.instrument = self.lockin_device
        self.emitted = []

    def settings(self, sequence="D A B E", **changes):
        values = dict(ms.DEFAULTS)
        values.update(changes)
        states = {name: (str(a), str(h), str(i)) for name, a, h, i in ms.DEFAULT_STATES}
        return ms.build_settings(states, sequence, values)

    def run(self, settings=None):
        run = ms.FourStateRun(self.ppms, self.source, self.lockin, settings or self.settings(),
                              emit=lambda kind, payload: self.emitted.append((kind, payload)),
                              stop=self.stop, motor=self.motor, clock=self.clock)
        with mock.patch.object(ms.time, "sleep", lambda seconds: None):
            run.run()
        return [payload for kind, payload in self.emitted if kind == "point"]


class FakePPMS:
    backend = core.PPMS_QDINSTRUMENT_BACKEND

    def __init__(self, world):
        self.world = world
        self.field = 0.0
        self.target = 0.0
        self.rate = 100.0
        self.mode = 0
        self.changed_at = world.clock.now
        self.start = 0.0
        self.arrived_at = world.clock.now
        self.active = object()
        self.connected = True

    def _update(self):
        elapsed = self.world.clock.now - self.changed_at
        step = self.rate * elapsed
        if abs(self.target - self.start) <= step:
            if self.field != self.target:
                self.arrived_at = self.changed_at + abs(self.target - self.start) / self.rate
            self.field = self.target
        else:
            self.field = self.start + math.copysign(step, self.target - self.start)

    def set_field(self, target, rate, approach, mode):
        self._update()
        self.world.events.append(("field", target, mode))
        self.start, self.target, self.rate, self.mode = self.field, float(target), float(rate), mode
        self.changed_at = self.world.clock.now
        return self.read_environment()

    def read_environment(self):
        self._update()
        if self.field != self.target:
            status = "Charging"
        else:
            status = "StablePersistent" if self.mode == 0 else "StableDriven"
        return {"temperature_k": 300.0, "temperature_status": "Stable",
                "field_oe": self.field, "field_status": status}


class FakeMotor:
    def __init__(self, world):
        self.world = world
        self.angle = 0.0

    def read(self):
        return {"angle_deg": self.angle, "status": "AtTarget"}

    def move_and_verify(self, target, speed, cancelled=lambda: False, allow_large=False, timeout=90.0, **_):
        world = self.world
        world.events.append(("rotate", target, world.ppms.read_environment()["field_oe"],
                             world.source_device.output_on(), allow_large, timeout))
        world.clock.now += abs(target - self.angle) / 3.0
        self.angle = float(target)
        return {"angle_deg": self.angle, "status": "AtTarget"}


class Fake6221:
    def __init__(self, world):
        self.world = world
        self.p = {"CURR:COMP": 10.0, "SOUR:WAVE:FUNC": "SIN", "SOUR:WAVE:AMPL": 0.0, "SOUR:WAVE:OFFS": 0.0,
                  "SOUR:WAVE:FREQ": 1000.0, "SOUR:WAVE:DUR:CYCL": "INF", "SOUR:WAVE:PMAR:STAT": "OFF",
                  "SOUR:CURR": 0.0}
        self.output = False
        self.off_at = None
        self.condition = 0  # STAT:MEAS:COND? while the output is on
        self.keep_pulse_on = False

    def output_on(self):
        if self.off_at is not None and self.world.clock.now >= self.off_at:
            self.output, self.off_at = False, None
        return self.output

    def sine_rms(self):
        if self.output_on() and self.p["SOUR:WAVE:FUNC"] == "SIN" and self.p["SOUR:WAVE:DUR:CYCL"] == "INF":
            return float(self.p["SOUR:WAVE:AMPL"]) / math.sqrt(2.0)
        return 0.0

    def write(self, command):
        head, _, value = command.partition(" ")
        if head == "SOUR:WAVE:INIT":
            self.output = True
            if self.p["SOUR:WAVE:DUR:CYCL"] != "INF":
                cycles = int(float(self.p["SOUR:WAVE:DUR:CYCL"]))
                period = 1.0 / float(self.p["SOUR:WAVE:FREQ"])
                total = cycles * period
                current = 2.0 * float(self.p["SOUR:WAVE:OFFS"])
                world = self.world
                env = world.ppms.read_environment()
                # The 50 % square's high half is the pulse: width = period / 2.
                world.events.append(("pulse", current, env["field_oe"], env["field_status"], world.motor.angle,
                                     period / 2.0, cycles, float(self.p["CURR:COMP"]),
                                     world.clock.now - world.ppms.arrived_at))
                world.sample.pulse(current, env["field_oe"], world.motor.angle)
                if not self.keep_pulse_on:
                    self.off_at = world.clock.now + total
            else:
                self.world.events.append(("sine", self.sine_rms(), float(self.p["SOUR:WAVE:FREQ"]),
                                          self.p["SOUR:WAVE:PMAR:STAT"], self.world.ppms.read_environment()["field_oe"]))
        elif head == "OUTP":
            self.output = value == "ON"
            if not self.output:
                self.off_at = None
        elif head == "SOUR:WAVE:ABOR":
            self.output, self.off_at = False, None
        elif head == "CURR" and value == "0":
            self.p["SOUR:CURR"] = 0.0
        elif head in ("SOUR:WAVE:DUR:CYCL", "SOUR:WAVE:FUNC", "SOUR:WAVE:PMAR:STAT"):
            self.p[head] = value
        elif value:
            try:
                self.p[head] = float(value)
            except ValueError:
                self.p[head] = value

    def query(self, command):
        if command == "SYST:ERR?":
            return '0,"No error"'
        if command == "OUTP?":
            return "1" if self.output_on() else "0"
        if command == "STAT:MEAS:COND?":
            return str(self.condition if self.output_on() else 0)
        key = command[:-1]
        return str(self.p[key])

    def clear(self):
        pass


class FakeSR830:
    def __init__(self, world):
        self.world = world
        self.p = {"HARM": 2, "FMOD": 1, "RSLP": 0, "ILIN": 3, "SYNC": 1, "SENS": 20, "OFLT": 8}
        self.frequency_error = 0.0
        self.overload_left = 0
        self.snaps_until_unlock = None

    def locked(self):
        if self.snaps_until_unlock is not None and self.snaps_until_unlock <= 0:
            return False
        source = self.world.source_device
        return (source.sine_rms() > 0 and source.p["SOUR:WAVE:PMAR:STAT"] == "ON"
                and self.p["FMOD"] == 0)

    def signal(self):
        if not self.locked() or self.p["HARM"] != 1:
            return 0.0, 0.0
        x = HALL_OHM[self.world.sample.state] * self.world.source_device.sine_rms()
        return x, 0.1 * x

    def write(self, command):
        head, _, value = command.partition(" ")
        self.p[head] = int(value)
        self.world.events.append(("sr830", command))

    def query(self, command):
        if command == "*ESR?":
            return "0"
        if command == "FREQ?":
            frequency = float(self.world.source_device.p["SOUR:WAVE:FREQ"]) if self.locked() else 0.0
            return f"{frequency + self.frequency_error:.6f}"
        x, y = self.signal()
        if command == "LIAS?":
            status = 0 if self.locked() else 8
            if self.overload_left or math.hypot(x, y) > ms.core.NONLOCAL_SR830_FULL_SCALE_V[self.p["SENS"]]:
                self.overload_left = max(0, self.overload_left - 1)
                status |= 1
            return str(status)
        if command == "SNAP? 1,2,3,4,9":
            if self.snaps_until_unlock is not None:
                self.snaps_until_unlock -= 1
            self.world.events.append(("snap", self.world.ppms.read_environment()["field_oe"],
                                      self.world.source_device.sine_rms()))
            frequency = float(self.query("FREQ?"))
            return f"{x:.9e},{y:.9e},{math.hypot(x, y):.9e},{math.degrees(math.atan2(y, x)):.6f},{frequency:.6f}"
        if command == "OUTP? 3":
            return f"{math.hypot(x, y):.9e}"
        return str(self.p[command[:-1]])

    def clear(self):
        pass


class SettingsTests(unittest.TestCase):
    def test_defaults_are_the_figure_table(self):
        self.assertEqual(ms.DEFAULT_STATES, (
            ("D", 0.0, 1500.0, 22.0), ("A", 270.0, -1500.0, 22.0), ("A'", 270.0, 1500.0, -22.0),
            ("B", 270.0, 1500.0, 22.0), ("B'", 270.0, -1500.0, -22.0), ("E", 0.0, -1500.0, 22.0)))
        settings = World().settings()
        self.assertEqual(settings.sequence, ("D", "A", "B", "E"))
        self.assertEqual(settings.pulse_width_s, 30e-6)
        self.assertEqual(settings.pulse_count, 1)
        self.assertEqual(settings.read_current_rms_a, 0.5e-3)
        self.assertEqual(settings.read_frequency_hz, core.PPMS_TRANSPORT_FREQUENCY_HZ)

    def test_sequence_text(self):
        names = tuple(name for name, *_ in ms.DEFAULT_STATES)
        self.assertEqual(ms.parse_sequence("d, a' B;e", names), ("D", "A'", "B", "E"))
        for text in ("", "D X", "C"):
            with self.subTest(text=text), self.assertRaises(ValueError):
                ms.parse_sequence(text, names)

    def test_out_of_range_values_are_refused_before_anything_is_sent(self):
        world = World()
        for change in ({"pulse_width_us": "2"}, {"field_rate": "150"}, {"read_current_ma": "0"},
                       {"decel": "15"}, {"pulse_count": "0"}, {"compliance_v": "200"},
                       {"read_time_s": "0.1"}, {"repeats": "0"}, {"approach": "Oscillate"}):
            with self.subTest(change=change), self.assertRaises(ValueError):
                world.settings(**change)
        states = {name: (str(a), str(h), str(i)) for name, a, h, i in ms.DEFAULT_STATES}
        for name, bad in (("D", ("400", "1500", "22")), ("A", ("270", "95000", "22")),
                          ("B", ("270", "1500", "200"))):
            with self.subTest(state=name), self.assertRaises(ValueError):
                ms.build_settings(dict(states, **{name: bad}), "D A B E", dict(ms.DEFAULTS))


class RunTests(unittest.TestCase):
    def test_each_state_is_written_then_read_at_zero_field(self):
        world = World()
        rows = world.run()
        self.assertEqual([row["state"] for row in rows], ["D", "A", "B", "E"])
        for row in rows:
            with self.subTest(state=row["state"]):
                expected = HALL_OHM[row["state"]] * 0.5e-3
                self.assertAlmostEqual(row["x_v"], expected, places=12)
                self.assertAlmostEqual(row["rxy_ohm"], HALL_OHM[row["state"]], places=9)
                self.assertAlmostEqual(row["theta_deg"], math.degrees(math.atan2(0.1, 1.0)) if expected > 0
                                       else math.degrees(math.atan2(-0.1, -1.0)), places=4)
                self.assertEqual(row["read_field_oe"], 0.0)
                self.assertGreaterEqual(row["samples"], 3)
        self.assertFalse(world.source_device.output_on(), "6221 off at the end")
        self.assertEqual(world.ppms.read_environment()["field_oe"], 0.0)

    def test_order_of_actions(self):
        world = World()
        world.run()
        events = world.events
        rotations = [e for e in events if e[0] == "rotate"]
        self.assertEqual([e[1] for e in rotations], [270.0, 0.0], "D at 0 deg, A and B at 270, E back at 0")
        for _, _angle, field, output, large, timeout in rotations:
            self.assertLessEqual(abs(field), 2.0, "rotates only at zero field")
            self.assertFalse(output, "rotates with the 6221 off")
            self.assertTrue(large)
            self.assertEqual(timeout, 900.0)
        pulses = [e for e in events if e[0] == "pulse"]
        self.assertEqual([(round(e[1] * 1e3, 6), e[2], e[4]) for e in pulses],
                         [(22.0, 1500.0, 0.0), (22.0, -1500.0, 270.0), (22.0, 1500.0, 270.0), (22.0, -1500.0, 0.0)])
        for pulse in pulses:
            self.assertEqual(pulse[3], "StableDriven", "pulse only once the write field has settled")
            self.assertAlmostEqual(pulse[5], 30e-6, places=12, msg="30 us wide")
            self.assertEqual(pulse[6], 1, "one pulse")
            self.assertEqual(pulse[7], 15.0, "compliance")
            self.assertGreaterEqual(pulse[8], 2.0, "the field has held for the settle time")
        sines = [e for e in events if e[0] == "sine"]
        self.assertEqual(len(sines), 4)
        for _, rms, frequency, marker, field in sines:
            self.assertAlmostEqual(rms, 0.5e-3, places=12)
            self.assertEqual(frequency, core.PPMS_TRANSPORT_FREQUENCY_HZ)
            self.assertEqual(marker, "ON", "the marker is the SR830's external reference")
            self.assertEqual(field, 0.0, "read current only at zero field")
        for snap in (e for e in events if e[0] == "snap"):
            self.assertEqual(snap[1], 0.0)
        # Every field command is Driven, and each pulse lies between its
        # write field and the return to zero.
        fields = [e for e in events if e[0] == "field"]
        self.assertTrue(all(mode == 1 for _, _, mode in fields))
        order = [e[0] if e[0] != "field" else f"field {e[1]:+g}" for e in events
                 if e[0] in ("field", "pulse", "sine", "rotate")]
        self.assertEqual(order, [
            "field +1500", "pulse", "field +0", "sine",
            "rotate", "field -1500", "pulse", "field +0", "sine",
            "field +1500", "pulse", "field +0", "sine",
            "rotate", "field -1500", "pulse", "field +0", "sine"])

    def test_lockin_is_set_to_the_ppms_page_reference(self):
        world = World()
        world.run()
        commands = [e[1] for e in world.events if e[0] == "sr830"]
        self.assertEqual(commands[:5], ["ILIN 0", "SYNC 0", "HARM 1", "FMOD 0",
                                        f"RSLP {core.PPMS_SR830_REFERENCE_SLOPE}"])

    def test_already_zero_field_is_left_alone_before_rotating(self):
        world = World()
        world.run(world.settings("A"))
        first = next(e for e in world.events if e[0] != "sr830")
        self.assertEqual(first[0], "rotate", "Persistent at 0 Oe: nothing sent before the rotation")

    def test_primed_states_reverse_field_and_current(self):
        world = World()
        rows = world.run(world.settings("A' B'"))
        pulses = [e for e in world.events if e[0] == "pulse"]
        self.assertEqual([(round(e[1] * 1e3, 6), e[2]) for e in pulses], [(-22.0, 1500.0), (-22.0, -1500.0)])
        for row, state in zip(rows, ("A", "B")):
            self.assertAlmostEqual(row["x_v"], HALL_OHM[state] * 0.5e-3, places=12)

    def test_repeats(self):
        world = World()
        rows = world.run(world.settings("D E", repeats="3"))
        self.assertEqual([(row["cycle"], row["state"]) for row in rows],
                         [(1, "D"), (1, "E"), (2, "D"), (2, "E"), (3, "D"), (3, "E")])

    def test_stop_turns_the_6221_off_and_returns_the_field_to_zero(self):
        world = World()
        world.stop.stop_when = lambda: world.ppms.target == -1500.0
        with self.assertRaises(core.PPMSScanCancelled):
            world.run()
        self.assertFalse(world.source_device.output_on())
        self.assertEqual(world.events[-1][:2], ("field", 0.0), "0 Oe sent on stop")
        self.assertEqual(len([e for e in world.events if e[0] == "pulse"]), 1, "no pulse after the stop")

    def test_compliance_during_the_read_stops_the_run(self):
        world = World()
        world.source_device.condition = 8
        with self.assertRaises(RuntimeError) as caught:
            world.run()
        self.assertIn("compliance", str(caught.exception))
        self.assertFalse(world.source_device.output_on())
        self.assertEqual(world.ppms.read_environment()["field_oe"], 0.0)

    def test_an_unlocked_reference_stops_the_run(self):
        world = World()
        world.lockin_device.frequency_error = 5.0
        with self.assertRaises(RuntimeError) as caught:
            world.run()
        self.assertIn("did not lock", str(caught.exception))
        self.assertFalse(world.source_device.output_on())

    def test_a_reference_lost_during_the_read_stops_the_run(self):
        world = World()
        world.lockin_device.snaps_until_unlock = 2
        with self.assertRaises(RuntimeError) as caught:
            world.run(world.settings("D"))
        self.assertIn("lost the 6221 reference", str(caught.exception))
        self.assertFalse(world.source_device.output_on())

    def test_a_field_left_on_is_zeroed_before_the_first_rotation(self):
        world = World()
        world.ppms.set_field(500.0, 100.0, 1, 1)
        world.clock.now += 60
        world.events.clear()
        world.run(world.settings("A"))
        moves = [e for e in world.events if e[0] in ("field", "rotate")]
        self.assertEqual(moves[0], ("field", 0.0, 1))
        self.assertEqual(moves[1][0], "rotate")
        self.assertLessEqual(abs(moves[1][2]), 2.0)

    def test_a_pulse_that_does_not_end_is_turned_off_and_stops_the_run(self):
        world = World()
        world.source_device.keep_pulse_on = True
        with self.assertRaises(RuntimeError) as caught:
            world.run()
        self.assertIn("still on", str(caught.exception))
        self.assertFalse(world.source_device.output_on())
        self.assertEqual(world.events[-1], ("field", 0.0, 1), "write field removed: 0 Oe Driven sent")

    def test_autorange_finds_a_range_that_holds_the_signal(self):
        world = World()
        rows = world.run(world.settings("D"))
        code = rows[0]["sensitivity_code"]
        scales = core.NONLOCAL_SR830_FULL_SCALE_V
        magnitude = math.hypot(rows[0]["x_v"], rows[0]["y_v"])
        self.assertLess(magnitude, scales[code])
        self.assertGreaterEqual(magnitude, ms.AUTORANGE_LOW_FRACTION * scales[code])

    def test_an_overload_during_the_read_ranges_up_and_reads_again(self):
        world = World()
        world.lockin_device.overload_left = 2
        rows = world.run(world.settings("D"))
        self.assertAlmostEqual(rows[0]["x_v"], HALL_OHM["D"] * 0.5e-3, places=12)


class WindowTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        try:
            cls.root = tk.Tk()
        except tk.TclError as error:  # headless build server
            raise unittest.SkipTest(f"no display: {error}")
        cls.root.withdraw()
        cls.app = ms.FourStateApp(cls.root)
        cls.root.update()

    @classmethod
    def tearDownClass(cls):
        cls.root.destroy()

    def test_defaults_on_screen(self):
        app = self.app
        self.assertEqual(app.sequence_var.get(), "D A B E")
        self.assertEqual(app.values["pulse_width_us"].get(), "30")
        self.assertEqual(app.values["read_current_ma"].get(), "0.5")
        self.assertEqual([variable.get() for variable in app.state_vars["A"]], ["270", "-1500", "22"])
        self.assertFalse(app.safety_var.get(), "the safety tick starts cleared")

    def test_start_needs_the_instruments(self):
        with mock.patch.object(ms.messagebox, "showerror") as error:
            self.app.start()
        error.assert_called_once()
        self.assertIsNone(self.app.worker)

    def test_confirmation_lists_every_setting(self):
        text = self.app.summary(self.app.current_settings())
        for part in ("D", "φ 270°", "H -1500 Oe", "pulse +22 mA", "30 µs × 1", "0.5 mA RMS", "large rotation"):
            self.assertIn(part, text)

    def test_plot_draws_three_panels(self):
        row = dict(step=1, state="A", write_field_set_oe=-1500.0, pulse_current_ma=22.0, x_v=11e-6)
        self.app.plot.set_rows([row, dict(row, step=2, state="B", write_field_set_oe=1500.0, x_v=-12e-6)])
        self.root.update()
        texts = [self.app.canvas.itemcget(item, "text") for item in self.app.canvas.find_all()
                 if self.app.canvas.type(item) == "text"]
        for label in ("H write (T)", "I pulse (mA)", "SR830 X (µV)", "A", "B", "Step"):
            self.assertIn(label, texts)


if __name__ == "__main__":
    unittest.main()
