"""MS-fourState.py in its Cycle and Sequence modes: settings, the order of
every instrument action, the values read, and each stop, against simulated
instruments.

The 6221 and SR830 are simulated at the level of their commands, so the
program's own command sequences and its 6221 controller and SR830 helpers
(copied from the main program) run.  The fake PPMS ramps the field in simulated time and can
report a stale status for a while after a command; the fake sample takes the
state written by the last pulse.  Only WindowTests needs a display.
"""
import importlib.util
import math
import queue
import sys
import time
import tkinter as tk
import types
import unittest
from tkinter import ttk
from pathlib import Path
from unittest import mock

PROGRAM = Path(__file__).resolve().parents[1] / "MS-fourState.py"
spec = importlib.util.spec_from_file_location("ms_four_state", PROGRAM)
ms = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = ms
spec.loader.exec_module(ms)
core = ms.core

HALL_OHM = {"D": 0.060, "A": 0.022, "B": -0.025, "E": -0.055}


class StandaloneTests(unittest.TestCase):
    """MS-fourState.py runs on its own (user, 2026-10-09)."""

    def test_it_does_not_load_the_main_program(self):
        source = PROGRAM.read_text(encoding="utf-8")
        self.assertNotIn("spec_from_file_location", source)
        self.assertIs(core, ms, "the copied instrument code is in the file itself")

    def test_the_copied_instrument_code_matches_the_main_program(self):
        # A fix to the main program's controllers must reach MS-fourState:
        # run Other-files/tools/ms_fourstate_copy_core.py, then this passes.
        tool_path = Path(__file__).resolve().parent / "tools" / "ms_fourstate_copy_core.py"
        tool_spec = importlib.util.spec_from_file_location("ms_fourstate_copy_core", tool_path)
        tool = importlib.util.module_from_spec(tool_spec)
        tool_spec.loader.exec_module(tool)
        expected = tool.copied_block(tool.CORE.read_text(encoding="utf-8"))
        source = PROGRAM.read_text(encoding="utf-8")
        actual = tool.BEGIN + source.split(tool.BEGIN, 1)[1].split(tool.END, 1)[0] + tool.END

        def without_digest(text):
            return "\n".join(line for line in text.splitlines() if "SHA-256" not in line)

        self.assertEqual(without_digest(actual), without_digest(expected),
                         "run Other-files/tools/ms_fourstate_copy_core.py")


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

    def clear(self):
        self.flag = False

    def wait(self, seconds):
        self.clock.now += seconds
        return self.is_set()


class Sample:
    """Takes D/E at 0 or 360 deg and B/A at 270 deg by the sign of H x I
    (every default state is written at +1500 Oe; the current picks)."""

    def __init__(self):
        self.state = "E"

    def pulse(self, current_a, field_oe, angle_deg):
        if abs(angle_deg) < 1 or abs(angle_deg - 360) < 1:
            self.state = "D" if field_oe * current_a > 0 else "E"
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
        self.last_run = None

    def settings(self, sequence="D A B E", until_stop=False, **changes):
        values = dict(ms.DEFAULTS)
        values.update(changes)
        states = {name: (str(a), str(h), str(i)) for name, a, h, i in ms.DEFAULT_STATES}
        return ms.build_settings(states, sequence, values, until_stop=until_stop)

    def advance(self, seconds):
        self.clock.now += seconds

    def points(self):
        return [payload for kind, payload in self.emitted if kind == "point"]

    def run(self, settings=None):
        self.last_run = ms.FourStateRun(
            self.ppms, self.source, self.lockin, settings or self.settings(),
            emit=lambda kind, payload: self.emitted.append((kind, payload)),
            stop=self.stop, motor=self.motor, clock=self.clock, sleep=self.advance)
        with mock.patch.object(ms.time, "sleep", lambda seconds: None):
            self.last_run.run()
        return self.points()


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
        self.lag_s = 0.0      # Status and field reported stale for this long after a command.
        self.stale = None
        self.stale_until = 0.0
        self.active = object()
        self.connected = True
        self.fault = None     # A field status such as MagnetFailure, reported from now on.

    def _update(self):
        elapsed = self.world.clock.now - self.changed_at
        step = self.rate * elapsed
        if abs(self.target - self.start) <= step:
            if self.field != self.target:
                self.arrived_at = self.changed_at + abs(self.target - self.start) / self.rate
            self.field = self.target
        else:
            self.field = self.start + math.copysign(step, self.target - self.start)

    def _reading(self):
        self._update()
        if self.fault:
            status = self.fault
        elif self.field != self.target:
            status = "Charging"
        else:
            status = "StablePersistent" if self.mode == 0 else "StableDriven"
        return {"temperature_k": 300.0, "temperature_status": "Stable",
                "field_oe": self.field, "field_status": status}

    def set_field(self, target, rate, approach, mode):
        before = self._reading()
        self.world.events.append(("field", target, mode))
        self.start, self.target, self.rate, self.mode = self.field, float(target), float(rate), mode
        self.changed_at = self.world.clock.now
        if self.lag_s:
            self.stale, self.stale_until = before, self.world.clock.now + self.lag_s
        return self.read_environment()

    def read_environment(self):
        if self.stale is not None and self.world.clock.now < self.stale_until:
            return dict(self.stale)
        return self._reading()


class FakeMotor:
    def __init__(self, world):
        self.world = world
        self.angle = 0.0
        self.status = "AtTarget"

    def read(self):
        return {"angle_deg": self.angle, "status": self.status}

    def move_and_verify(self, target, speed, cancelled=lambda: False, allow_large=False, timeout=90.0, **_):
        world = self.world
        world.events.append(("rotate", target, world.ppms._reading()["field_oe"],
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
        self.condition = 0              # STAT:MEAS:COND? while the output is on
        self.keep_pulse_on = False
        self.ignore_off = False         # OUTP OFF / ABOR do nothing (a stuck output)
        self.compliance_on_pulse = False
        self.event_register = 0
        self.event_query_supported = True
        self.event_queries = 0
        self.errors = []
        self.messages = []

    def output_on(self):
        if self.off_at is not None and self.world.clock.now >= self.off_at:
            self.output, self.off_at = False, None
        return self.output

    def sine_rms(self):
        if self.output_on() and self.p["SOUR:WAVE:FUNC"] == "SIN" and self.p["SOUR:WAVE:DUR:CYCL"] == "INF":
            return float(self.p["SOUR:WAVE:AMPL"]) / math.sqrt(2.0)
        return 0.0

    def write(self, message):
        self.messages.append(message)
        for part in message.split(";:"):
            self._command(part)

    def _command(self, command):
        head, _, value = command.partition(" ")
        if head == "SOUR:WAVE:INIT":
            self.output = True
            if self.p["SOUR:WAVE:DUR:CYCL"] != "INF":
                cycles = int(float(self.p["SOUR:WAVE:DUR:CYCL"]))
                period = 1.0 / float(self.p["SOUR:WAVE:FREQ"])
                current = 2.0 * float(self.p["SOUR:WAVE:OFFS"])
                world = self.world
                env = world.ppms._reading()
                # The 50 % square's high half is the pulse: width = period / 2.
                world.events.append(("pulse", current, env["field_oe"], env["field_status"], world.motor.angle,
                                     period / 2.0, cycles, float(self.p["CURR:COMP"]),
                                     world.clock.now - world.ppms.arrived_at))
                world.sample.pulse(current, env["field_oe"], world.motor.angle)
                if self.compliance_on_pulse:
                    self.event_register |= 8
                if not self.keep_pulse_on:
                    self.off_at = world.clock.now + cycles * period
            else:
                self.world.events.append(("sine", self.sine_rms(), float(self.p["SOUR:WAVE:FREQ"]),
                                          self.p["SOUR:WAVE:PMAR:STAT"], self.world.ppms._reading()["field_oe"]))
        elif head == "OUTP":
            if value == "ON":
                self.output = True
            elif not self.ignore_off:
                self.output, self.off_at = False, None
        elif head == "SOUR:WAVE:ABOR":
            if not self.ignore_off:
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
            return self.errors.pop(0) if self.errors else '0,"No error"'
        if command == "OUTP?":
            return "1" if self.output_on() else "0"
        if command == "STAT:MEAS:COND?":
            return str(self.condition if self.output_on() else 0)
        if command == "STAT:MEAS:EVEN?":
            self.event_queries += 1
            if not self.event_query_supported:
                self.errors.append('-113,"Undefined header"')
                raise TimeoutError("VI_ERROR_TMO (test)")
            value, self.event_register = self.event_register, 0
            return str(value)
        return str(self.p[command[:-1]])

    def clear(self):
        pass


class FakeSR830:
    def __init__(self, world):
        self.world = world
        self.p = {"HARM": 2, "FMOD": 1, "RSLP": 0, "ILIN": 3, "SYNC": 1, "SENS": 20, "OFLT": 8,
                  "ISRC": 1, "IGND": 0, "ICPL": 0, "OFSL": 1, "RMOD": 1, "PHAS": 0}
        self.frequency_error = 0.0
        self.overload_left = 0
        self.overload_below = None   # Overload at sensitivities below this code (pickup).
        self.snaps_until_unlock = None
        self.esr = 0
        self.reject = set()          # Commands answered with a command error in *ESR?.
        self.sens_writes = []

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
        self.world.events.append(("sr830", command))
        head, _, value = command.partition(" ")
        if head == "*CLS":
            self.esr = 0
            return
        if command in self.reject:
            self.esr |= 32
            return
        if head == "SENS":
            self.sens_writes.append(int(value))
        try:
            self.p[head] = int(value)
        except ValueError:
            self.p[head] = value

    def query(self, command):
        if command == "*ESR?":
            value, self.esr = self.esr, 0
            return str(value)
        if command == "FREQ?":
            frequency = float(self.world.source_device.p["SOUR:WAVE:FREQ"]) if self.locked() else 0.0
            return f"{frequency + self.frequency_error:.6f}"
        x, y = self.signal()
        if command == "LIAS?":
            status = 0 if self.locked() else 8
            scale = ms.core.NONLOCAL_SR830_FULL_SCALE_V[self.p["SENS"]]
            if (self.overload_left or math.hypot(x, y) > scale
                    or (self.overload_below is not None and self.p["SENS"] < self.overload_below)):
                self.overload_left = max(0, self.overload_left - 1)
                status |= 1
            return str(status)
        if command == "SNAP? 1,2,3,4,9":
            if self.snaps_until_unlock is not None:
                self.snaps_until_unlock -= 1
            self.world.events.append(("snap", self.world.ppms._reading()["field_oe"],
                                      self.world.source_device.sine_rms()))
            frequency = float(self.query("FREQ?"))
            return f"{x:.9e},{y:.9e},{math.hypot(x, y):.9e},{math.degrees(math.atan2(y, x)):.6f},{frequency:.6f}"
        if command == "OUTP? 3":
            return f"{math.hypot(x, y):.9e}"
        if command.startswith("OEXP? "):
            return "0.00,0"
        return str(self.p[command[:-1]])

    def clear(self):
        pass


class SettingsTests(unittest.TestCase):
    def test_defaults_are_the_figure_table(self):
        self.assertEqual(ms.DEFAULT_STATES, (
            ("D", 360.0, 1500.0, 22.0), ("A", 270.0, 1500.0, -22.0),
            ("B", 270.0, 1500.0, 22.0), ("E", 360.0, -1500.0, 22.0)),
            "the user's figure of 2026-10-09: E at -1500 Oe, +22 mA")
        self.assertEqual(ms.DEFAULTS["read_time_s"], "15", "the figure's read time")
        settings = World().settings()
        self.assertEqual(settings.sequence, ("D", "A", "B", "E"))
        self.assertEqual(settings.pulse_width_s, 30e-6)
        self.assertEqual(settings.pulse_count, 1)
        self.assertEqual(settings.read_current_rms_a, 0.5e-3)
        self.assertEqual(settings.read_frequency_hz, core.PPMS_TRANSPORT_FREQUENCY_HZ)
        self.assertEqual(ms.DEFAULT_CYCLE, "D A B E B A")

    def test_sequence_text(self):
        names = tuple(name for name, *_ in ms.DEFAULT_STATES)
        self.assertEqual(ms.parse_sequence("d, a B;e", names), ("D", "A", "B", "E"))
        self.assertEqual(ms.parse_sequence("D-A-B-E-B-A", names), ("D", "A", "B", "E", "B", "A"))
        for text in ("", "D X", "C", "A' B'"):
            with self.subTest(text=text), self.assertRaises(ValueError):
                ms.parse_sequence(text, names)

    def test_out_of_range_values_are_refused_before_anything_is_sent(self):
        world = World()
        for change in ({"pulse_width_us": "2"}, {"field_rate": "150"}, {"read_current_ma": "0"},
                       {"read_current_ma": "6"}, {"decel": "15"}, {"pulse_count": "0"},
                       {"compliance_v": "200"}, {"read_time_s": "0.1"}, {"repeats": "0"},
                       {"approach": "Oscillate"}):
            with self.subTest(change=change), self.assertRaises(ValueError):
                world.settings(**change)
        states = {name: (str(a), str(h), str(i)) for name, a, h, i in ms.DEFAULT_STATES}
        for name, bad in (("D", ("400", "1500", "22")), ("A", ("270", "95000", "22")),
                          ("B", ("270", "1500", "200"))):
            with self.subTest(state=name), self.assertRaises(ValueError):
                ms.build_settings(dict(states, **{name: bad}), "D A B E", dict(ms.DEFAULTS))

    def test_until_stop_and_counted_runs(self):
        world = World()
        endless = world.settings(ms.DEFAULT_CYCLE, until_stop=True)
        self.assertIsNone(endless.total_steps)
        steps = endless.iter_steps()
        self.assertEqual([next(steps) for _ in range(8)],
                         [(1, "D"), (1, "A"), (1, "B"), (1, "E"), (1, "B"), (1, "A"), (2, "D"), (2, "A")])
        counted = world.settings("A B", repeats="3")
        self.assertEqual(counted.total_steps, 6)
        self.assertEqual(len(list(counted.iter_steps())), 6)
        # D-A-B-E-B-A turns D->A, B->E, E->B and A->D: three inside, one back to D.
        self.assertEqual(endless.large_rotations(), (3, True))
        self.assertEqual(world.settings("A B").large_rotations(), (0, False))

    def test_the_time_axis_unit_follows_the_run_length(self):
        self.assertEqual(ms.time_unit(0.0), ("s", 1.0))
        self.assertEqual(ms.time_unit(599.0), ("s", 1.0))
        self.assertEqual(ms.time_unit(600.0), ("min", 60.0))
        self.assertEqual(ms.time_unit(3 * 3600.0), ("h", 3600.0))
        self.assertEqual(ms.thin(list(range(10)), 4), [0, 2, 4, 6, 8])
        self.assertEqual(ms.thin([1, 2], 4), [1, 2])

    def test_boxes_flow_side_by_side(self):
        self.assertEqual(ms.FourStateApp.flow_lines([800, 470, 520, 300, 420, 290], 1700, 6),
                         [[0, 1], [2, 3, 4, 5]])
        self.assertEqual(ms.FourStateApp.flow_lines([800, 470, 520], 1000, 6), [[0], [1, 2]])
        self.assertEqual(ms.FourStateApp.flow_lines([1200], 1000, 6), [[0]], "too wide: a line of its own")

    def test_tick_marks_end_for_equal_values(self):
        for low, high in ((1.0, 1.0 + 1e-15), (2.5e-5, 2.5e-5), (-1e-300, 1e-300)):
            with self.subTest(low=low, high=high):
                ticks = ms.nice_ticks(low, high, 4)
                self.assertLessEqual(len(ticks), 51)


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
                self.assertIs(row["pulse_in_compliance"], False)
        self.assertFalse(world.source_device.output_on(), "6221 off at the end")
        self.assertEqual(world.ppms.read_environment()["field_oe"], 0.0)
        self.assertEqual(world.last_run.shutdown_problems, [])

    def test_order_of_actions(self):
        world = World()
        world.run()
        events = world.events
        rotations = [e for e in events if e[0] == "rotate"]
        # The simulated rotator starts at 0 deg: D at 360 is one full turn
        # first, then 90 deg to A and B at 270, and 90 deg back for E.
        self.assertEqual([e[1] for e in rotations], [360.0, 270.0, 360.0], "D at 360, A and B at 270, E at 360")
        for _, _angle, field, output, large, timeout in rotations:
            self.assertLessEqual(abs(field), 2.0, "rotates only at zero field")
            self.assertFalse(output, "rotates with the 6221 off")
            self.assertTrue(large)
            self.assertEqual(timeout, 900.0)
        pulses = [e for e in events if e[0] == "pulse"]
        self.assertEqual([(round(e[1] * 1e3, 6), e[2], e[4]) for e in pulses],
                         [(22.0, 1500.0, 360.0), (-22.0, 1500.0, 270.0), (22.0, 1500.0, 270.0), (22.0, -1500.0, 360.0)])
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
        fields = [e for e in events if e[0] == "field"]
        self.assertTrue(all(mode == 1 for _, _, mode in fields), "every field command is Driven")
        order = [e[0] if e[0] != "field" else f"field {e[1]:+g}" for e in events
                 if e[0] in ("field", "pulse", "sine", "rotate")]
        self.assertEqual(order, [
            "rotate", "field +1500", "pulse", "field +0", "sine",  # 0 -> D at 360 deg first
            "rotate", "field +1500", "pulse", "field +0", "sine",
            "field +1500", "pulse", "field +0", "sine",
            "rotate", "field -1500", "pulse", "field +0", "sine"])  # E at -1500 Oe

    def test_pulse_starts_as_on_the_moke_6221_page(self):
        world = World()
        world.run(world.settings("D"))
        messages = world.source_device.messages
        start = messages.index("OUTP ON")
        self.assertEqual(messages[start - 1:start + 2], ["SOUR:WAVE:ARM", "OUTP ON", "SOUR:WAVE:INIT"])
        self.assertEqual(messages[:start - 1][-1], "SOUR:WAVE:DUR:CYCL 1", "all settings sent and read before ARM")

    def test_lockin_is_set_to_the_ppms_page_reference_and_recorded(self):
        world = World()
        world.run()
        commands = [e[1] for e in world.events if e[0] == "sr830"]
        self.assertEqual(commands[:6], ["*CLS", "HARM 1", "FMOD 0", f"RSLP {core.PPMS_SR830_REFERENCE_SLOPE}",
                                        "ILIN 0", "SYNC 0"])
        recorded = [payload for kind, payload in world.emitted if kind == "lockin"][0]
        self.assertEqual(set(ms.SR830_RECORDED) | {"tau_s", "offset_X_percent", "expand_X",
                                                   "offset_Y_percent", "expand_Y"}, set(recorded))
        self.assertEqual(recorded["tau_s"], 0.1)
        self.assertEqual((recorded["offset_X_percent"], recorded["expand_X"]), (0.0, 0))

    def test_a_latched_error_left_by_a_disconnect_does_not_stop_the_start(self):
        world = World()
        world.lockin_device.esr = 32  # SYST:LOC on disconnect is not an SR830 command.
        rows = world.run(world.settings("D"))
        self.assertEqual(len(rows), 1)

    def test_connection_applies_the_ppms_page_baseline(self):
        world = World()
        world.lockin_device.esr = 32
        ms.initialize_lockin(world.lockin)
        for command, value in ms.SR830_BASELINE:
            self.assertEqual(world.lockin_device.p[command], value, command)
        self.assertEqual(world.lockin_device.p["DDEF"], "2,1,0")

    def test_a_failed_baseline_leaves_the_lockin_disconnected(self):
        world = World()
        world.lockin_device.reject.add("ISRC 1")
        lockin = mock.Mock()
        lockin.lock = world.lockin.lock
        lockin.instrument = world.lockin_device
        lockin.connect.return_value = "Stanford_Research_Systems,SR830"
        with self.assertRaises(RuntimeError) as caught:
            ms.connect_lockin(lockin, "GPIB0::8::INSTR")
        self.assertIn("ISRC 1", str(caught.exception))
        lockin.disconnect.assert_called_once()

    def test_a_long_time_constant_is_refused(self):
        world = World()
        world.lockin_device.p["OFLT"] = 13  # 30 s
        with self.assertRaises(RuntimeError) as caught:
            world.run(world.settings("D"))
        self.assertIn("time constant", str(caught.exception))
        self.assertEqual([e for e in world.events if e[0] in ("field", "pulse", "rotate")], [],
                         "refused before anything moves")

    def test_already_zero_field_is_left_alone_before_rotating(self):
        world = World()
        world.run(world.settings("A"))
        first = next(e for e in world.events if e[0] not in ("sr830",))
        self.assertEqual(first[0], "rotate", "Persistent at 0 Oe: nothing sent before the rotation")

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

    def test_a_and_b_written_the_other_way_in_their_rows(self):
        # With field and current both reversed in the A and B rows, the same
        # states are written (the A' and B' of the figure).
        world = World()
        values = dict(ms.DEFAULTS)
        states = {name: (str(a), str(h), str(i)) for name, a, h, i in ms.DEFAULT_STATES}
        states["A"], states["B"] = ("270", "-1500", "22"), ("270", "-1500", "-22")
        rows = world.run(ms.build_settings(states, "A B", values))
        pulses = [e for e in world.events if e[0] == "pulse"]
        self.assertEqual([(round(e[1] * 1e3, 6), e[2]) for e in pulses], [(22.0, -1500.0), (-22.0, -1500.0)])
        for row, state in zip(rows, ("A", "B")):
            self.assertAlmostEqual(row["x_v"], HALL_OHM[state] * 0.5e-3, places=12)

    def test_repeats(self):
        world = World()
        rows = world.run(world.settings("D E", repeats="3"))
        self.assertEqual([(row["cycle"], row["state"]) for row in rows],
                         [(1, "D"), (1, "E"), (2, "D"), (2, "E"), (3, "D"), (3, "E")])

    def test_cycle_until_stop(self):
        world = World()
        world.stop.stop_when = lambda: len(world.points()) >= 8
        with self.assertRaises(core.PPMSScanCancelled):
            world.run(world.settings(ms.DEFAULT_CYCLE, until_stop=True))
        rows = world.points()
        self.assertEqual("".join(row["state"] for row in rows), "DABEBADA")
        for row in rows:
            self.assertAlmostEqual(row["x_v"], HALL_OHM[row["state"]] * 0.5e-3, places=12)
        # From 0 deg: one full turn to D at 360, then 90 deg each way.
        self.assertEqual([e[1] for e in world.events if e[0] == "rotate"],
                         [360.0, 270.0, 360.0, 270.0, 360.0, 270.0])
        self.assertFalse(world.source_device.output_on())

    def test_two_state_switching_until_stop(self):
        world = World()
        world.stop.stop_when = lambda: len(world.points()) >= 6
        with self.assertRaises(core.PPMSScanCancelled):
            world.run(world.settings("A B", until_stop=True))
        self.assertEqual([row["state"] for row in world.points()], ["A", "B"] * 3)
        self.assertEqual(len([e for e in world.events if e[0] == "rotate"]), 1, "A and B share 270 deg")

    def test_stop_turns_the_6221_off_and_returns_the_field_to_zero(self):
        world = World()
        world.stop.stop_when = lambda: sum(e[0] == "field" and e[1] == 1500.0 for e in world.events) >= 2  # A's write
        with self.assertRaises(core.PPMSScanCancelled):
            world.run()
        self.assertFalse(world.source_device.output_on())
        self.assertEqual(world.events[-1][:2], ("field", 0.0), "0 Oe sent on stop")
        self.assertEqual(len([e for e in world.events if e[0] == "pulse"]), 1, "no pulse after the stop")
        self.assertEqual(world.ppms.read_environment()["field_oe"], 0.0, "zero confirmed before the run ends")

    def test_a_stop_right_after_a_field_command_still_returns_to_zero(self):
        # The PPMS still reports the old field for a while: shutdown must
        # follow what was commanded, not the stale reading.
        world = World()
        world.ppms.lag_s = 2.0
        world.stop.stop_when = lambda: sum(e[0] == "field" and e[1] == 1500.0 for e in world.events) >= 2  # A's write
        with self.assertRaises(core.PPMSScanCancelled):
            world.run()
        self.assertEqual([e[1] for e in world.events if e[0] == "field"][-2:], [1500.0, 0.0])
        reading = world.ppms.read_environment()
        self.assertEqual((reading["field_oe"], reading["field_status"]), (0.0, "StableDriven"))
        self.assertEqual(world.last_run.shutdown_problems, [])
        # Confirmed from readings taken after the stale ones, held 3 s.
        self.assertGreaterEqual(world.clock.now - world.ppms.changed_at, world.ppms.lag_s + 3.0)

    def test_compliance_during_the_read_stops_the_run(self):
        world = World()
        world.source_device.condition = 8
        with self.assertRaises(RuntimeError) as caught:
            world.run()
        self.assertIn("compliance", str(caught.exception))
        self.assertFalse(world.source_device.output_on())
        self.assertEqual(world.ppms.read_environment()["field_oe"], 0.0)

    def test_compliance_during_a_pulse_is_read_from_the_latch(self):
        world = World()
        world.source_device.compliance_on_pulse = True
        rows = world.run(world.settings("D"))
        self.assertIs(rows[0]["pulse_in_compliance"], True)
        self.assertTrue(any("compliance" in str(p) for kind, p in world.emitted if kind == "log"))

    def test_an_unanswered_latch_query_is_asked_once_and_leaves_no_error(self):
        world = World()
        world.source_device.event_query_supported = False
        rows = world.run(world.settings("D E"))
        self.assertEqual([row["pulse_in_compliance"] for row in rows], ["unknown", "unknown"])
        self.assertEqual(world.source_device.event_queries, 1)
        self.assertEqual(world.source_device.errors, [], "error queue emptied")

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

    def test_a_pulse_that_does_not_end_is_turned_off_and_stops_the_run(self):
        world = World()
        world.source_device.keep_pulse_on = True
        with self.assertRaises(RuntimeError) as caught:
            world.run()
        self.assertIn("still on", str(caught.exception))
        self.assertFalse(world.source_device.output_on())
        self.assertEqual(world.ppms.read_environment()["field_oe"], 0.0, "write field removed and confirmed")

    def test_an_output_that_will_not_turn_off_is_reported(self):
        world = World()
        rows = []

        def stuck_after_first_point(kind, payload):
            world.emitted.append((kind, payload))
            if kind == "point":
                rows.append(payload)
                world.source_device.ignore_off = True
                world.source_device.output = True

        run = ms.FourStateRun(world.ppms, world.source, world.lockin, world.settings("D E"),
                              emit=stuck_after_first_point, stop=world.stop, motor=world.motor,
                              clock=world.clock, sleep=world.advance)
        with mock.patch.object(ms.time, "sleep", lambda seconds: None), self.assertRaises(RuntimeError) as caught:
            run.run()
        self.assertIn("did not read back off", str(caught.exception))
        self.assertTrue(any("6221 not confirmed off" in problem for problem in run.shutdown_problems))
        self.assertEqual(len(rows), 1, "nothing more is written with the output stuck on")

    def test_autorange_finds_a_range_that_holds_the_signal(self):
        world = World()
        rows = world.run(world.settings("D"))
        code = rows[0]["sensitivity_code"]
        scales = core.NONLOCAL_SR830_FULL_SCALE_V
        magnitude = math.hypot(rows[0]["x_v"], rows[0]["y_v"])
        self.assertLess(magnitude, scales[code])
        self.assertGreaterEqual(magnitude, ms.AUTORANGE_LOW_FRACTION * scales[code])

    def test_autorange_does_not_alternate_on_pickup(self):
        # Overload from pickup on sensitive ranges, with a signal below 10 %
        # of those ranges: up until the overload clears, never back down.
        world = World()
        world.lockin_device.p["SENS"] = 9
        world.lockin_device.overload_below = 16
        world.run(world.settings("A", read_current_ma="0.01"))
        self.assertEqual(world.lockin_device.sens_writes, list(range(10, 17)))

    def test_an_overload_during_the_read_ranges_up_and_reads_again(self):
        world = World()
        world.lockin_device.overload_left = 2
        rows = world.run(world.settings("D"))
        self.assertAlmostEqual(rows[0]["x_v"], HALL_OHM["D"] * 0.5e-3, places=12)


class ManualTests(unittest.TestCase):
    """The manual settings (Move, Set H, Zero H, Set I, I off, Pulse, Read),
    as on the PPMS page, against the simulated instruments."""

    def manual(self, world, current_ma=None, **changes):
        values = dict(ms.DEFAULTS)
        values.update(changes)
        settings = ms.manual_settings(values, current_ma)
        return ms.FourStateRun(world.ppms, world.source, world.lockin, settings,
                               emit=lambda kind, payload: world.emitted.append((kind, payload)),
                               stop=world.stop, motor=world.motor, clock=world.clock, sleep=world.advance)

    def field_to(self, world, target, mode=1):
        distance = abs(target - world.ppms._reading()["field_oe"])
        world.ppms.set_field(target, 100.0, 1, mode)
        world.advance(distance / 100.0 + 5.0)
        world.events.clear()

    def test_move_turns_the_rotator_at_zero_field_with_the_6221_off(self):
        world = World()
        run = self.manual(world)
        run.manual_current()  # The read sine is on before the move.
        asked = []
        angle = run.manual_move(90.0, lambda before, target: asked.append((before, target)) or True)
        self.assertEqual(angle, 90.0)
        self.assertEqual(asked, [(0.0, 90.0)], "a turn over 30 deg is confirmed first")
        rotate = [e for e in world.events if e[0] == "rotate"]
        self.assertEqual(rotate, [("rotate", 90.0, 0.0, False, True, 900.0)], "6221 off, 0 Oe, large move")

    def test_a_small_turn_needs_no_confirmation(self):
        world = World()
        self.manual(world).manual_move(10.0, lambda before, target: self.fail("not asked"))
        self.assertEqual(world.motor.angle, 10.0)

    def test_move_is_refused_away_from_zero_field(self):
        world = World()
        self.field_to(world, 1500.0)
        with self.assertRaisesRegex(RuntimeError, "only at zero field"):
            self.manual(world).manual_move(90.0, lambda before, target: True)
        self.assertEqual([e for e in world.events if e[0] in ("rotate", "field")], [], "nothing sent")

    def test_a_large_turn_declined_sends_nothing(self):
        world = World()
        result, outcome, problems = ms.run_manual(
            self.manual(world), lambda run: run.manual_move(270.0, lambda before, target: False))
        self.assertEqual(outcome[0], "declined")
        self.assertEqual(problems, [])
        self.assertEqual([e for e in world.events if e[0] in ("rotate", "field")], [])

    def test_set_h_ends_persistent_as_on_the_ppms_page(self):
        world = World()
        start = world.clock.now
        data = self.manual(world).manual_field(1500.0)
        self.assertEqual([e for e in world.events if e[0] == "field"], [("field", 1500.0, 0)], "Persistent end mode")
        self.assertEqual((data["field_oe"], data["field_status"]), (1500.0, "StablePersistent"))
        self.assertGreaterEqual(world.clock.now - start, 1500.0 / float(ms.DEFAULTS["field_rate"]))

    def test_a_stale_persistent_reading_is_not_taken_for_arrival(self):
        # The PPMS still reports Persistent at the old field for a while.
        world = World()
        world.ppms.lag_s = 5.0
        data = self.manual(world).manual_field(1500.0)
        self.assertEqual(data["field_oe"], 1500.0)
        # Zero H while already Persistent at 0 Oe: confirmed only after the
        # PPMS page's confirmation time, as the reading may predate the command.
        world = World()
        world.ppms.lag_s = 30.0
        start = world.clock.now
        self.manual(world).manual_field(0.0)
        self.assertGreaterEqual(world.clock.now - start, core.PPMS_PERSISTENT_CONFIRM_S)

    def test_stop_during_set_h_returns_to_zero_driven(self):
        world = World()
        world.stop.stop_when = lambda: world.ppms.target == 1500.0 and world.ppms._reading()["field_oe"] > 500
        with mock.patch.object(ms.time, "sleep", lambda seconds: None):
            result, outcome, problems = ms.run_manual(self.manual(world), lambda run: run.manual_field(1500.0))
        self.assertEqual(outcome[0], "stopped")
        self.assertEqual([e for e in world.events if e[0] == "field"], [("field", 1500.0, 0), ("field", 0.0, 1)])
        self.assertEqual(world.ppms.read_environment()["field_oe"], 0.0)
        self.assertEqual(problems, [])
        self.assertFalse(world.source_device.output_on())

    def test_set_i_starts_the_read_sine_and_writes_nothing_to_the_sr830(self):
        world = World()
        result = self.manual(world, current_ma="0.8").manual_current()
        sine = [e for e in world.events if e[0] == "sine"]
        self.assertEqual(len(sine), 1)
        self.assertAlmostEqual(sine[0][1], 0.8e-3)
        self.assertEqual(sine[0][3], "ON", "phase marker on, the SR830 reference")
        self.assertTrue(world.source_device.output_on(), "left on, as the PPMS page's Set I")
        self.assertIn("not locked", result["lock"], "the SR830 is still set to its internal reference")
        self.assertEqual([e for e in world.events if e[0] == "sr830"], [], "nothing written to the SR830")
        world.lockin_device.p["FMOD"] = 0
        self.assertIn("SR830 locked", self.manual(world, current_ma="0.8").manual_current()["lock"])

    def test_an_error_turns_the_6221_off_and_leaves_the_field(self):
        world = World()
        self.field_to(world, 1500.0, mode=0)
        run = self.manual(world)
        run.manual_current()
        world.source_device.errors.append('-222,"Parameter data out of range"')
        result, outcome, problems = ms.run_manual(run, lambda run: run.manual_pulse(22.0))
        self.assertEqual(outcome[0], "failed")
        self.assertFalse(world.source_device.output_on())
        self.assertEqual([e for e in world.events if e[0] == "field"], [], "the field is left")
        self.assertEqual(world.ppms.read_environment()["field_oe"], 1500.0)

    def test_pulse_uses_the_write_settings_and_ends_off(self):
        world = World()
        result = self.manual(world, pulse_width_us="50", pulse_count="3").manual_pulse(-22.0)
        pulse = [e for e in world.events if e[0] == "pulse"]
        self.assertEqual(len(pulse), 1)
        self.assertAlmostEqual(pulse[0][1], -0.022)
        self.assertAlmostEqual(pulse[0][5], 50e-6)
        self.assertEqual(pulse[0][6], 3)
        self.assertFalse(world.source_device.output_on())
        self.assertIs(result["in_compliance"], False)
        with self.assertRaisesRegex(ValueError, "nothing to send"):
            self.manual(world).manual_pulse(0.0)

    def test_read_shows_angle_field_and_output(self):
        world = World()
        world.motor.angle = 270.0
        reading = self.manual(world).manual_read()
        self.assertEqual((reading["angle_deg"], reading["field_oe"], reading["source_on"]), (270.0, 0.0, False))
        text = ms.FourStateApp.format_reading(reading)
        self.assertIn("φ 270.00° (AtTarget)", text)
        self.assertIn("H +0.0 Oe StablePersistent", text)
        self.assertIn("6221 OFF", text)

    def test_nothing_is_sent_while_the_rotator_moves(self):
        # After a stopped Move the rotator can still be turning: Set H, Set I
        # and Pulse are refused until it reads AtTarget, as on the PPMS page.
        world = World()
        world.motor.status = "Moving"
        for action in (lambda run: run.manual_field(1500.0), lambda run: run.manual_current(),
                       lambda run: run.manual_pulse(22.0), lambda run: run.manual_move(10.0, lambda a, b: True)):
            with self.assertRaisesRegex(RuntimeError, "rotator is Moving"):
                action(self.manual(world))
        self.assertEqual([e for e in world.events if e[0] in ("field", "pulse", "sine", "rotate")], [])
        world.motor.status = "AtIndexSwitch"
        self.manual(world).manual_field(1500.0)

    def test_a_refused_move_leaves_the_6221_as_it_was(self):
        world = World()
        run = self.manual(world)
        run.manual_current()
        self.field_to(world, 1500.0, mode=0)
        with self.assertRaisesRegex(RuntimeError, "only at zero field"):
            run.manual_move(90.0, lambda before, target: True)
        self.assertTrue(world.source_device.output_on(), "checked before anything was turned off")
        with self.assertRaises(ms.ManualDeclined):
            self.field_to(world, 0.0, mode=0)
            run.manual_move(270.0, lambda before, target: False)
        self.assertTrue(world.source_device.output_on(), "a declined move turns nothing off")

    def test_the_field_is_read_again_after_the_question(self):
        world = World()

        def answer_while_the_field_rises(before, target):
            world.ppms.set_field(1500.0, 100.0, 1, 1)  # e.g. from MultiVu while the question was open
            world.advance(30.0)
            return True

        with self.assertRaisesRegex(RuntimeError, "only at zero field"):
            self.manual(world).manual_move(270.0, answer_while_the_field_rises)
        self.assertEqual([e for e in world.events if e[0] == "rotate"], [])

    def test_a_magnet_fault_ends_set_h_at_once(self):
        world = World()
        world.ppms.fault = "MagnetFailure"
        start = world.clock.now
        with self.assertRaisesRegex(RuntimeError, "MagnetFailure"):
            self.manual(world).manual_field(1500.0)
        self.assertLess(world.clock.now - start, 5.0)

    def test_set_h_shows_the_reading_while_it_waits(self):
        world = World()
        self.manual(world).manual_field(1500.0)
        shown = [payload for kind, payload in world.emitted if kind == "manual-reading"]
        self.assertGreater(len(shown), 3)
        self.assertLess(shown[0]["field_oe"], shown[-1]["field_oe"])

    def test_stop_reports_only_the_instruments_connected(self):
        world = World()
        world.source.instrument = None  # Set H with only the PPMS connected
        world.stop.stop_when = lambda: world.ppms._reading()["field_oe"] > 500
        with mock.patch.object(ms.time, "sleep", lambda seconds: None):
            _result, outcome, problems = ms.run_manual(self.manual(world), lambda run: run.manual_field(1500.0))
        self.assertEqual((outcome[0], problems), ("stopped", []))
        self.assertEqual(world.ppms.read_environment()["field_oe"], 0.0)
        world = World()
        world.ppms.connected = False  # Set I with only the 6221 connected
        world.stop.stop_when = lambda: world.source_device.output_on()
        _result, outcome, problems = ms.run_manual(self.manual(world), lambda run: run.manual_current())
        self.assertEqual((outcome[0], problems), ("stopped", []))
        self.assertFalse(world.source_device.output_on())

    def test_manual_rate_and_compliance_are_checked(self):
        for changes, message in (({"field_rate": "150"}, "rate"), ({"field_rate": "0"}, "rate"),
                                 ({"compliance_v": "200"}, "ompliance"), ({"compliance_v": "-5"}, "ompliance")):
            with self.subTest(changes=changes), self.assertRaisesRegex(ValueError, message):
                ms.manual_settings(dict(ms.DEFAULTS, **changes))

    def test_the_manual_pulse_has_its_own_width(self):
        world = World()
        app = ms.FourStateApp.__new__(ms.FourStateApp)
        app.busy, app.motor_pending = False, False
        app.title, app.root = "MS-fourState", None
        app.ppms, app.source = world.ppms, world.source
        app.safety_var = mock.Mock(get=lambda: True)
        app.values = {key: mock.Mock(get=lambda value=value: value) for key, value in ms.DEFAULTS.items()}
        app.manual_vars = {key: mock.Mock(get=lambda value=value: value)
                           for key, value in dict(ms.MANUAL_DEFAULTS, pulse_width_us="50").items()}
        sent = []
        app._run_manual = lambda label, settings, operation, describe, **_: sent.append(settings)
        with mock.patch.object(ms.messagebox, "askyesno", return_value=True) as asked:
            app.manual_pulse()
        self.assertAlmostEqual(sent[0].pulse_width_s, 50e-6, msg="the manual width")
        self.assertEqual(ms.DEFAULTS["pulse_width_us"], "30", "the runs' width is untouched")
        self.assertIn("50 µs", asked.call_args[0][1])
        app.manual_vars["pulse_width_us"] = mock.Mock(get=lambda: "-5")
        with mock.patch.object(ms.messagebox, "showerror") as refused:
            app.manual_pulse()
        refused.assert_called_once()
        self.assertEqual(len(sent), 1, "a bad width sends nothing")

    def test_manual_values_are_checked(self):
        with self.assertRaisesRegex(ValueError, "Read current"):
            ms.manual_settings(dict(ms.DEFAULTS), "70")
        self.assertEqual(ms.manual_settings(dict(ms.DEFAULTS), "0.8").read_current_rms_a, 0.8e-3)
        self.assertEqual(ms.manual_settings(ms.DEFAULTS).sequence, ())


class TimelineTests(unittest.TestCase):
    """The run's timeline: H, I, pulses and X at their real times."""

    def test_each_period_is_recorded_at_its_time(self):
        world = World()
        world.run(world.settings("D A"))
        traces = [payload for kind, payload in world.emitted if kind == "trace"]
        times = [trace[0] for trace in traces]
        self.assertEqual(times, sorted(times), "in time order, from the run's start")
        self.assertGreaterEqual(times[0], 0.0)
        of = lambda quantity: [trace for trace in traces if trace[1] == quantity]
        self.assertEqual([(value, state) for _, _, value, state in of("pulse")], [(22.0, "D"), (-22.0, "A")])
        self.assertEqual([state for _, _, _, state in of("read")], ["D", "A"])
        for at, _, _, state in of("pulse"):
            field = max((t for t in of("H") if t[0] <= at), key=lambda t: t[0])[2]
            self.assertEqual(field, 1500.0, f"{state} pulsed in the write field")
        levels = [value for _, _, value, _ in of("I")]
        self.assertIn(0.5, levels, "the read current, in mA RMS")
        self.assertEqual(levels[-1], 0.0, "and off at the end")
        level = 0.0
        for _, quantity, value, state in traces:  # In the order they happened.
            if quantity == "I":
                level = value
            elif quantity == "X":
                self.assertEqual(level, 0.5, "X is read only while the read current is on")
                self.assertAlmostEqual(value, HALL_OHM[state] * 0.5e-3, places=12)

    def test_manual_actions_make_no_timeline(self):
        world = World()
        run = ms.FourStateRun(world.ppms, world.source, world.lockin, ms.manual_settings(ms.DEFAULTS),
                              emit=lambda kind, payload: world.emitted.append((kind, payload)),
                              stop=world.stop, motor=world.motor, clock=world.clock, sleep=world.advance)
        run.manual_pulse(22.0)
        self.assertEqual([kind for kind, _ in world.emitted if kind == "trace"], [])


class ConsoleTests(unittest.TestCase):
    """Every command to the instruments, and every reply, on the console."""

    def test_a_visa_instrument_echoes_what_is_sent_and_read(self):
        lines = []
        device = mock.Mock()
        device.query.return_value = "1\n"
        echoed = ms.EchoedResource(device, "6221", lines.append)
        echoed.write("OUTP OFF")
        self.assertEqual(echoed.query("OUTP?"), "1\n", "the reply itself is unchanged")
        echoed.timeout = 5000
        echoed.close()
        self.assertEqual(lines, ["6221 > OUTP OFF", "6221 > OUTP?", "6221 < 1"])
        self.assertEqual(device.timeout, 5000, "settings reach the instrument")
        device.close.assert_called_once()
        device.query.side_effect = TimeoutError("VI_ERROR_TMO")
        with self.assertRaises(TimeoutError):
            echoed.query("SNAP? 1,2")
        self.assertEqual(lines[-1], "6221 ! VI_ERROR_TMO")

    def test_every_instrument_the_shared_manager_opens_is_echoed(self):
        manager = mock.Mock()
        manager.open_resource.return_value = mock.Mock(query=mock.Mock(return_value="SR830,s/n,ver"))
        original = core.acquire_shared_visa_manager
        self.addCleanup(setattr, core, "acquire_shared_visa_manager", original)
        core.acquire_shared_visa_manager = lambda: manager
        lines = []
        ms.echo_visa_commands(lines.append, {"GPIB0::8::INSTR": "SR830"}.get)
        acquired = core.acquire_shared_visa_manager()
        self.assertIs(acquired, manager, "the same manager, so its reference count is kept")
        instrument = acquired.open_resource("GPIB0::8::INSTR")
        instrument.query("*IDN?")
        self.assertEqual(lines, ["SR830 > *IDN?", "SR830 < SR830,s/n,ver"])

    def test_the_ppms_commands_and_readings_are_echoed(self):
        world = World()
        lines = []
        ppms = ms.EchoedPPMS(world.ppms, lines.append)
        ppms.set_field(1500.0, 100.0, 1, 0)
        self.assertEqual(lines[0], "PPMS > SetField +1500 Oe at 100 Oe/s, No overshoot, Persistent")
        self.assertTrue(lines[1].startswith("PPMS < T 300.000 K Stable | H +0.0 Oe"))
        self.assertTrue(ppms.connected, "everything else is the controller's own")
        self.assertEqual(world.events[-1], ("field", 1500.0, 0))

    def test_the_rotator_lines_go_to_the_console(self):
        app = ms.FourStateApp.__new__(ms.FourStateApp)
        posted = []
        app.post = lambda kind, payload=None: posted.append((kind, payload))
        app.log_from_thread("PPMS motor SEND ONCE: MOVE 270.000000 0 14")
        app.log_from_thread("PPMS motor GetPPMSItem position=270.0")
        app.log_from_thread("Keithley 6221 connected")
        self.assertEqual(posted, [("command", "Rotator > SEND ONCE: MOVE 270.000000 0 14"),
                                  ("command", "Rotator < GetPPMSItem position=270.0"),
                                  ("log", "Keithley 6221 connected")])


class StatusBoxTests(unittest.TestCase):
    """The PPMS status box: read only, polled while idle, fed by runs."""

    def test_the_status_read_covers_ppms_and_6221(self):
        world = World()
        world.motor.angle = 270.0
        status = ms.read_instrument_status(world.ppms, world.source, motor=world.motor)
        self.assertEqual((status["temperature_k"], status["field_oe"], status["field_status"]),
                         (300.0, 0.0, "StablePersistent"))
        self.assertEqual((status["angle_deg"], status["angle_status"]), (270.0, "AtTarget"))
        self.assertIs(status["source_on"], False)
        self.assertEqual([e for e in world.events if e[0] in ("field", "rotate", "sr830")], [], "read only")
        world.ppms.connected = False
        world.source.instrument = None
        self.assertEqual(set(ms.read_instrument_status(world.ppms, world.source)), {"time"})

    def test_a_run_feeds_the_box_at_most_once_a_second(self):
        world = World()
        world.run(world.settings("D A"))
        shown = [(kind, payload) for kind, payload in world.emitted if kind == "ppms-reading"]
        fields = [p for _, p in shown if "field_oe" in p]
        angles = [p["angle_deg"] for _, p in shown if "angle_deg" in p]
        self.assertGreater(len(fields), 5)
        self.assertEqual(angles, [360.0, 270.0], "each rotation is shown")
        reads = world.clock.now - 1000.0
        self.assertLessEqual(len(fields), reads / ms.STATUS_EMIT_S + 5)

    def test_the_box_shows_what_a_reading_holds(self):
        app = ms.FourStateApp.__new__(ms.FourStateApp)
        app.status_box_vars = {key: mock.Mock() for key in (
            "temperature", "temperature_status", "field", "field_status", "angle", "angle_status",
            "pressure", "helium", "source", "time")}
        app._show_status({"temperature_k": 300.0012, "temperature_status": "Stable", "field_oe": -1500.04,
                          "field_status": "StableDriven", "angle_deg": 270.0, "angle_status": "AtTarget",
                          "pressure_display": "4.98 Torr", "helium_display": "72.10%", "source_on": True,
                          "time": "09:52:13"})
        shown = {key: var.set.call_args[0][0] for key, var in app.status_box_vars.items()}
        self.assertEqual(shown, {"temperature": "300.001 K", "temperature_status": "Stable",
                                 "field": "-1500.0 Oe", "field_status": "StableDriven",
                                 "angle": "270.00°", "angle_status": "AtTarget", "pressure": "4.98 Torr",
                                 "helium": "72.10%", "source": "ON", "time": "09:52:13"})
        for var in app.status_box_vars.values():
            var.reset_mock()
        app._show_status({"field_oe": 10.0, "field_status": "Charging"})
        self.assertFalse(app.status_box_vars["angle"].set.called, "what a reading lacks stays")

    def test_polling_only_while_idle(self):
        world = World()
        app = ms.FourStateApp.__new__(ms.FourStateApp)
        app.root = FakeRoot()
        app.ppms, app.source = world.ppms, world.source
        app.status_polling, app.last_cryogen = False, None
        started = []
        app._status_worker = lambda cryogen: started.append(cryogen)
        with mock.patch.object(ms.threading, "Thread",
                               lambda target, args, daemon: types.SimpleNamespace(start=lambda: target(*args))):
            app.busy = True
            app._poll_status()
            self.assertEqual(started, [], "nothing while a run or an action is going")
            app.busy = False
            app._poll_status()
            self.assertEqual(started, [True], "pressure and helium on the first poll")
            app.status_polling = False
            app._poll_status()
            self.assertEqual(started, [True, False], "then every CRYOGEN_POLL_S only")
        self.assertEqual(len(app.root.scheduled), 3, "always rescheduled")


class FakeRoot:
    def __init__(self):
        self.scheduled = []

    def after(self, delay, callback):
        self.scheduled.append(callback)


class DisplayTests(unittest.TestCase):
    """GUI event handling, without a window."""

    def app(self):
        app = ms.FourStateApp.__new__(ms.FourStateApp)
        app.root = FakeRoot()
        app.events = queue.Queue()
        app.logged = []
        app._append_log = lambda text, show=True: app.logged.append(text)
        return app

    def test_one_failing_handler_does_not_stop_the_event_pump(self):
        app = self.app()
        handled = []

        def handle(kind, payload):
            if kind == "point":
                raise OSError("disk full (test)")
            handled.append(kind)

        app._handle = handle
        app.post("point", {})
        app.post("call", None)
        app._drain()
        self.assertEqual(handled, ["call"], "the next event is still handled")
        self.assertEqual(len(app.root.scheduled), 1, "the pump is rescheduled")
        self.assertIn("disk full", app.logged[0])

    def gui_app(self):
        app = self.app()
        app.title = "MS-fourState"
        app.rows = []
        app.run = None
        app.plot = mock.Mock()
        app.progress = mock.Mock()
        app.last_var = mock.Mock()
        # A live CSV that cannot be written (its folder is gone).
        app.run_paths = ms.run_file_paths(Path("/nonexistent/folder"), "run")
        app.csv_failed = False
        return app

    def test_a_failed_csv_row_is_shown_once_and_the_run_goes_on(self):
        app = self.gui_app()
        row = dict(step=1, state="D", x_v=1e-6, y_v=0.0, theta_deg=0.0, rxy_ohm=0.002,
                   read_field_oe=0.0, temperature_k=300.0)
        with mock.patch.object(ms.messagebox, "showwarning") as warning:
            app._add_point(row)
            app._add_point(dict(row, step=2))
        warning.assert_called_once()
        self.assertEqual(len(app.rows), 2)
        self.assertTrue(any("CSV write failed at step 2" in text for text in app.logged))

    def manual_app(self, world):
        app = self.app()
        app.title = "MS-fourState"
        app.ppms, app.source, app.lockin = world.ppms, world.source, world.lockin
        app.stop_event = world.stop
        app.busy, app.worker = False, None
        app.motor_pending, app.field_unconfirmed = False, None
        app.status_var, app.manual_var = mock.Mock(), mock.Mock()
        app.safety_var = mock.Mock(get=lambda: True)
        app._set_busy = lambda busy, running=False: None
        app._manual_log = lambda text: None
        app.data_directory = Path("/nonexistent")
        return app

    def finish(self, app, label, outcome, problems=(), reading=None, kind="", target=None):
        with mock.patch.object(ms.messagebox, "showerror") as error:
            app._manual_finished(label, lambda result: "done", None, outcome, list(problems),
                                 reading or {}, kind, target)
        return error

    def test_a_stopped_move_blocks_until_a_reading_at_rest(self):
        world = World()
        app = self.manual_app(world)
        self.finish(app, "Moving", ("stopped", core.PPMSScanCancelled("Stopped")),
                    reading={"angle_status": "Moving", "angle_deg": 120.0}, kind="move")
        self.assertTrue(app.motor_pending)
        for check in (dict(ppms=True, moves=True), dict(source=True, moves=True)):
            with self.assertRaisesRegex(ValueError, "may still be turning"):
                app._manual_check(**check)
        app._manual_check(source=True, safety=False)  # I off still works
        self.finish(app, "Reading", None, reading={"angle_status": "AtTarget", "angle_deg": 270.0})
        self.assertFalse(app.motor_pending)

    def test_a_failed_set_h_blocks_move_until_zero_h(self):
        world = World()
        app = self.manual_app(world)
        app.manual_vars = {"angle": mock.Mock(get=lambda: "90")}
        app.values = {key: mock.Mock(get=lambda value=value: value) for key, value in ms.DEFAULTS.items()}
        error = self.finish(app, "Field to +1500 Oe", ("failed", RuntimeError("read failed")), kind="field", target=1500.0)
        self.assertEqual(app.field_unconfirmed, 1500.0)
        self.assertIn("may still be going to +1500 Oe", error.call_args[0][1])
        with mock.patch.object(ms.messagebox, "showerror") as refused:
            app.manual_move()
        self.assertIn("not confirmed", refused.call_args[0][1])
        self.assertIsNone(app.worker)
        self.finish(app, "Field to +0 Oe", None, kind="field", target=0.0)
        self.assertIsNone(app.field_unconfirmed)

    def test_a_failure_with_the_6221_not_connected_does_not_claim_it_was_turned_off(self):
        world = World()
        world.source.instrument = None
        app = self.manual_app(world)
        error = self.finish(app, "Field to +1500 Oe", ("failed", RuntimeError("x")), kind="field", target=1500.0)
        self.assertNotIn("6221 has been turned off", error.call_args[0][1])

    def test_a_stop_as_the_action_ends_still_turns_off(self):
        world = World()
        world.ppms.connected = False         # Set I with the 6221 alone (no rotator to read)
        world.lockin_device.p["FMOD"] = 0    # The SR830 locks at once: no real-time wait
        app = self.manual_app(world)
        finished = []
        app.post = lambda kind, payload=None: payload() if kind == "call" else None
        app._manual_finished = lambda *args: finished.append(args)
        settings = ms.manual_settings(ms.DEFAULTS)

        def set_i_then_stop(run):
            run.manual_current()
            world.stop.set()  # Stop clicked just as Set I returns

        with mock.patch.object(ms.time, "sleep", lambda seconds: None):
            app._run_manual("Set I", settings, set_i_then_stop, lambda result: "done")
            app.worker.join(10)
        self.assertEqual(finished[0][3][0], "stopped")
        self.assertFalse(world.source_device.output_on())

    def connection_app(self, world):
        app = self.app()
        app.title = "MS-fourState"
        app.ppms, app.source, app.lockin = world.ppms, world.source, world.lockin
        app.busy = False
        app.status_var = mock.Mock()
        app.status_vars = {key: mock.Mock() for key in ms.CONNECTIONS}
        app.connection_failed = {key: False for key in ms.CONNECTIONS}
        app.resource_names = {}
        app.values = {key: mock.Mock(get=lambda value=value: value) for key, value in ms.DEFAULTS.items()}
        app.source_resource_var = mock.Mock(get=lambda: "GPIB0::30::INSTR")
        app.updates = []
        app._update_connection_controls = lambda: app.updates.append(dict(app.connection_failed))
        app._set_busy = lambda busy, running=False: setattr(app, "busy", busy)
        app.post = lambda kind, payload=None: payload() if kind == "call" else None
        return app

    def wait_idle(self, app):
        for _ in range(200):
            if not app.busy:
                return
            time.sleep(0.01)
        self.fail("the connection worker did not finish")

    def test_connect_and_disconnect_one_instrument_only(self):
        world = World()
        world.source.instrument = None
        app = self.connection_app(world)
        world.source.connect = mock.Mock(return_value="KEITHLEY INSTRUMENTS INC.,MODEL 6221")
        world.lockin.connect = mock.Mock(side_effect=AssertionError("not asked"))
        world.ppms.connect = mock.Mock(side_effect=AssertionError("not asked"))
        app.connect_device("6221")
        self.wait_idle(app)
        world.source.connect.assert_called_once_with("GPIB0::30::INSTR", 15.0)
        app.status_vars["6221"].set.assert_called_with("Connected: KEITHLEY INSTRUMENTS INC.,MODEL 6221")
        world.ppms.disconnect = mock.Mock()
        world.lockin.disconnect = mock.Mock()
        world.source.disconnect = mock.Mock()
        app.disconnect_device("6221")
        self.wait_idle(app)
        world.source.disconnect.assert_called_once()
        world.ppms.disconnect.assert_not_called()
        world.lockin.disconnect.assert_not_called()
        app.status_vars["6221"].set.assert_called_with("Not connected")

    def test_a_failed_connect_shows_its_reason_and_a_red_lamp(self):
        world = World()
        app = self.connection_app(world)
        app.lockin_resource_var = mock.Mock(get=lambda: "GPIB0::8::INSTR")
        world.lockin.instrument = None
        with mock.patch.object(ms, "connect_lockin", side_effect=RuntimeError("VI_ERROR_TMO")):
            app.connect_device("sr830")
            self.wait_idle(app)
        self.assertIn("Failed: ", app.status_vars["sr830"].set.call_args[0][0])
        self.assertTrue(app.connection_failed["sr830"])
        self.assertTrue(app.updates[-1]["sr830"], "the lamp turns red")

    def test_close_waits_for_a_connection_in_progress(self):
        app = self.app()
        app.title = "MS-fourState"
        app.worker = None
        app.busy = True
        app.root.destroy = mock.Mock()
        with mock.patch.object(ms.messagebox, "showwarning") as warning:
            app.close()
        warning.assert_called_once()
        app.root.destroy.assert_not_called()


class PPMSAddressTests(unittest.TestCase):
    def test_the_ppms_address_starts_at_192_168_137_137(self):
        """User, 2026-10-09; the main program's addresses stay in the list."""
        self.assertEqual(ms.PPMS_HOST, "192.168.137.137")
        self.assertEqual(ms.PPMS_HOSTS, ("192.168.137.137", *core.PPMS_HOST_CHOICES))


class PlotHiddenStartTests(unittest.TestCase):
    def test_the_first_5_s_of_each_read_are_not_drawn(self):
        """User, 2026-10-09: the settling start of each read is hidden."""
        periods = [(100.0, 120.0), (200.0, 220.0)]
        traces = [(100.0 + 2.0 + k, "X", -280e-6 + k * 1e-6, "A") for k in range(12)]
        traces += [(200.0 + 4.0 + k, "X", -275e-6, "E") for k in range(10)]
        shown = ms.StatePlot.shown_samples(traces, periods)
        self.assertEqual([at for at, _, state in shown if state == "A"], [107.0 + k for k in range(7)],
                         "from 5 s after the read's first sample (102 s) on")
        self.assertEqual([at for at, _, state in shown if state == "E"], [209.0 + k for k in range(5)])
        self.assertAlmostEqual(shown[0][1], -275.0, places=6, msg="in µV")
        self.assertEqual(ms.StatePlot.HIDDEN_READ_START_S, 5.0)


class FillScreenTests(unittest.TestCase):
    """The window opens maximised, filling the screen (user, 2026-10-09)."""

    def test_zoomed_where_tk_offers_it_else_the_screen_size(self):
        root = mock.Mock()
        self.assertEqual(ms.fill_screen(root), "zoomed")
        root.state.assert_called_once_with("zoomed")
        root = mock.Mock()
        root.state.side_effect = tk.TclError("bad argument \"zoomed\"")
        self.assertEqual(ms.fill_screen(root), "-zoomed")
        root.attributes.assert_called_once_with("-zoomed", True)
        root = mock.Mock()
        root.state.side_effect = root.attributes.side_effect = tk.TclError("no")
        root.winfo_screenwidth.return_value, root.winfo_screenheight.return_value = 2560, 1440
        self.assertEqual(ms.fill_screen(root), "geometry")
        root.geometry.assert_called_once_with("2560x1440+0+0")


class SaveTests(unittest.TestCase):
    """Saving as the main program's PPMS page (user, 2026-10-09)."""

    def app(self, folder):
        app = ms.FourStateApp.__new__(ms.FourStateApp)
        app.root, app.title = None, "MS-fourState"
        app.mode_var = mock.Mock(get=lambda: "Cycle")
        app.last_paths = None
        self.folder = folder
        return app

    def setUp(self):
        import tempfile
        self.directory = Path(tempfile.mkdtemp())

    def choose(self, app, names):
        answers = iter(names)
        with mock.patch.object(ms.filedialog, "askdirectory", return_value=str(self.directory)), \
                mock.patch.object(ms.simpledialog, "askstring", lambda *a, **k: next(answers)), \
                mock.patch.object(ms.messagebox, "showerror") as error:
            return app._choose_run_files(), error

    def test_a_folder_then_a_file_name_as_on_the_ppms_page(self):
        app = self.app(self.directory)
        paths, _error = self.choose(app, [""])
        self.assertEqual(paths["csv"].parent, self.directory)
        self.assertRegex(paths["csv"].name, r"^MS-fourState_Cycle_\d{8}_\d{6}\.csv$", "the default name")
        with paths["csv"].open(encoding="utf-8-sig") as handle:
            self.assertEqual(handle.readline().strip().split(","), list(ms.CSV_COLUMNS))
        stem = paths["csv"].stem
        self.assertEqual({key: path.name for key, path in paths.items()},
                         {"csv": f"{stem}.csv", "settings": f"{stem}_settings.json",
                          "trace": f"{stem}_trace.csv", "log": f"{stem}_log.txt",
                          "plot": f"{stem}_plot.eps"})

    def test_a_typed_name_is_checked_and_never_overwrites(self):
        app = self.app(self.directory)
        first, _ = self.choose(app, ["sample1 run.csv"])
        self.assertEqual(first["csv"].name, "sample1 run.csv")
        second, error = self.choose(app, ["bad/name", "CON", "sample1 run"])
        self.assertEqual(error.call_count, 2, "a path and a reserved name are refused")
        self.assertEqual(second["csv"].name, "sample1 run_01.csv", "an existing name gets a number")
        cancelled, _ = self.choose(app, [None])
        self.assertIsNone(cancelled)

    def test_each_row_is_appended_and_synced_and_save_csv_as_writes_them_all(self):
        app = self.app(self.directory)
        paths, _ = self.choose(app, ["live"])
        row = {column: 1 for column in ms.CSV_COLUMNS}
        ms.append_live_csv(paths["csv"], row)
        ms.append_live_csv(paths["csv"], dict(row, step=2))
        with paths["csv"].open(encoding="utf-8-sig") as handle:
            self.assertEqual(len(handle.read().splitlines()), 3)
        app.rows = [row, dict(row, step=2)]
        app.last_paths = paths
        app.status_var = mock.Mock()
        target = self.directory / "again.csv"
        with mock.patch.object(ms.filedialog, "asksaveasfilename", return_value=str(target)):
            app.save_csv_as()
        with target.open(encoding="utf-8-sig") as handle:
            self.assertEqual(len(handle.read().splitlines()), 3)


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
        cls.app.close()  # Cancels its timers, then destroys the window.

    def test_defaults_on_screen(self):
        app = self.app
        self.assertEqual(app.mode, "cycle", "Cycle is the default mode")
        self.assertEqual(app.cycle_var.get(), "D A B E B A")
        self.assertEqual(app.sequence_var.get(), "D A B E")
        self.assertEqual(app.host_var.get(), "192.168.137.137")
        self.assertEqual(app.values["repeats"].get(), "1")
        self.assertEqual(app.values["pulse_width_us"].get(), "30")
        self.assertEqual(app.values["read_current_ma"].get(), "0.5")
        self.assertEqual([variable.get() for variable in app.state_vars["A"]], ["270", "1500", "-22"])
        self.assertFalse(app.safety_var.get(), "the safety tick starts cleared")
        self.assertFalse(app.until_stop_var.get())
        self.assertEqual(app.mode_var.get(), "Cycle")

    def test_start_needs_the_instruments(self):
        with mock.patch.object(ms.messagebox, "showerror") as error:
            self.app.start()
        error.assert_called_once()
        self.assertIsNone(self.app.worker)

    def test_confirmation_lists_every_setting(self):
        text = self.app.summary(self.app.current_settings())
        for part in ("D", "φ 270°", "H +1500 Oe", "pulse -22 mA", "30 µs × 1", "0.5 mA RMS", "large rotation"):
            self.assertIn(part, text)

    def test_plot_draws_the_timeline(self):
        plot = self.app.plot
        self.addCleanup(plot.clear)
        plot.clear()
        for trace in ((10.0, "H", 1500.0, "A"), (30.0, "pulse", -22.0, "A"), (31.0, "I", 0.0, None),
                      (40.0, "H", 0.0, "A"), (50.0, "I", 0.5, "A"), (60.0, "X", 11e-6, "A"),
                      (61.0, "read", 11e-6, "A"), (62.0, "I", 0.0, None)):
            plot.add(trace)
        plot.redraw()
        self.root.update()
        texts = [self.app.canvas.itemcget(item, "text") for item in self.app.canvas.find_all()
                 if self.app.canvas.type(item) == "text"]
        for label in ("H (T)", "I (mA)", "SR830 X (µV)", "A", "Time since start (s)", "-20"):
            self.assertIn(label, texts)  # -20: the I axis reaches down to the -22 mA pulse.

    def test_each_instrument_connects_on_its_own(self):
        app = self.app
        self.assertEqual(set(app.connection_widgets), {"ppms", "6221", "sr830"})
        for key, (address, connect, disconnect, lamp) in app.connection_widgets.items():
            with self.subTest(key=key):
                self.assertEqual((connect.cget("text"), str(connect.cget("state"))), ("Connect", "normal"))
                self.assertEqual((disconnect.cget("text"), str(disconnect.cget("state"))), ("Disconnect", "disabled"))
                self.assertEqual(lamp.itemcget("lamp", "fill"), ms.LAMP_COLORS["off"])
        app.port_var.set("not a port")
        self.addCleanup(app.port_var.set, str(core.PPMS_QDINSTRUMENT_DEFAULT_PORT))
        with mock.patch.object(ms.messagebox, "showerror") as error:
            app.connection_widgets["ppms"][1].invoke()
        error.assert_called_once()
        self.assertFalse(app.busy)

    def test_the_console_shows_commands_and_counts_repeats(self):
        app = self.app
        self.addCleanup(app._clear_console)
        app._clear_console()
        app._append_log("Connecting Keithley 6221…")
        for line in ("6221 > OUTP?", "6221 < 0", "6221 < 0", "6221 < 0", "SR830 ! VI_ERROR_TMO"):
            app._append_command(line)
        text = app.log.get("1.0", "end-1c")
        self.assertIn("6221 > OUTP?", text)
        self.assertEqual(text.count("6221 < 0"), 1, "a repeat updates its line")
        self.assertIn("6221 < 0  ×3", text)
        self.assertIn("SR830 ! VI_ERROR_TMO", text)
        last = app.log.index("end-2l")
        self.assertIn("error", app.log.tag_names(f"{last}+10c"))
        app.show_commands_var.set(False)
        self.addCleanup(app.show_commands_var.set, True)
        app._append_command("6221 > *IDN?")
        self.assertNotIn("*IDN?", app.log.get("1.0", "end-1c"), "hidden with the tick off")

    def test_the_plot_has_the_room(self):
        # The window opens maximised; un-maximised it is WINDOW_SIZE where the
        # screen allows.  The plot takes what the controls leave.
        self.assertEqual(ms.WINDOW_SIZE, (1720, 1240))
        self.assertEqual(int(self.app.log.cget("width")), 46)

    def test_the_mode_list_switches_the_run_row(self):
        app = self.app
        self.addCleanup(lambda: (app.mode_var.set("Cycle"), app._apply_mode(),
                                 app.values["repeats"].set("1"), app.until_stop_var.set(False)))
        self.root.update()
        shown = lambda widget: widget.winfo_manager() == "grid"
        self.assertEqual([shown(w) for w in (app.cycle_entry, app.count_entry, app.until_stop_box,
                                             app.sequence_entry)], [True, True, True, False])
        app.values["repeats"].set("3")
        settings = app.current_settings()
        self.assertEqual((settings.sequence, settings.repeats), (("D", "A", "B", "E", "B", "A"), 3))
        app.until_stop_var.set(True)
        self.assertIsNone(app.current_settings().repeats, "a cycle can still run until Stop")
        self.assertIn("Run this cycle until Stop?", app.summary(app.current_settings()))
        app.mode_var.set("Sequence")
        app._apply_mode()
        self.root.update()
        self.assertEqual([shown(w) for w in (app.cycle_entry, app.count_entry, app.until_stop_box,
                                             app.sequence_entry)], [False, False, False, True])
        self.assertTrue(shown(app.sequence_count_entry), "a sequence has its own Cycles box")
        self.assertTrue(shown(app.count_label))
        self.assertEqual(app.sequence_label.cget("text"), "Sequence")
        app.sequence_var.set("A B A B A B")
        settings = app.current_settings()
        self.assertEqual((settings.sequence, settings.repeats), (("A", "B", "A", "B", "A", "B"), 1),
                         "by default a sequence runs once, whatever the cycle settings")
        self.assertIn("Run this once?", app.summary(settings))
        self.addCleanup(app.sequence_repeats_var.set, "1")
        app.sequence_repeats_var.set("4")
        settings = app.current_settings()
        self.assertEqual(settings.repeats, 4)
        self.assertIn("Run this sequence 4 times?", app.summary(settings))
        app.sequence_var.set("D A B E")
        self.assertEqual(app.cycle_var.get(), "D A B E B A", "each mode keeps its own states")

    def test_manual_settings_as_on_the_ppms_page(self):
        app = self.app
        self.assertEqual(set(app.manual_buttons), {"Move", "Read", "Set H", "Zero H", "Set I", "I off", "Pulse"})
        self.assertEqual({key: variable.get() for key, variable in app.manual_vars.items()},
                         {"angle": "360", "field": "0", "current_ma": "0.5", "pulse_ma": "22",
                          "pulse_width_us": "30"})
        # Decel, field rate and frequency are the Write and Read boxes' fields.
        entries = [w for w in app.manual_buttons["Move"].master.winfo_children() if isinstance(w, ttk.Entry)]
        shared = {str(entry.cget("textvariable")) for entry in entries}
        for key in ("decel", "field_rate", "read_frequency_hz"):
            self.assertIn(str(app.values[key]), shared)
        with mock.patch.object(ms.messagebox, "showerror") as error:
            app.manual_buttons["Set H"].invoke()
            app.manual_buttons["I off"].invoke()
        self.assertEqual(error.call_count, 2, "each needs its instrument connected")
        self.assertIsNone(app.worker)
        self.addCleanup(app._set_busy, False)
        app._set_busy(True, running=True)
        self.assertEqual({str(b.cget("state")) for b in app.manual_buttons.values()}, {"disabled"})
        self.assertEqual(str(app.stop_button.cget("state")), "normal", "Stop works during a manual action")

    def test_a_window_opened_in_sequence_mode(self):
        window = tk.Toplevel(self.root)
        window.withdraw()
        app = ms.FourStateApp(window, "sequence")
        self.addCleanup(app.close)
        self.assertEqual(window.title(), "MS-fourState")
        self.assertEqual(app.mode, "sequence")
        self.assertEqual(app.current_settings().sequence, ("D", "A", "B", "E"))


if __name__ == "__main__":
    unittest.main()
