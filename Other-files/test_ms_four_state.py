"""MS-fourState.py and its cycle and two-state modes: settings, the order of
every instrument action, the values read, and each stop, against simulated
instruments.

The 6221 and SR830 are simulated at the level of their commands, so the
program's own command sequences and the main program's 6221 controller and
SR830 helpers run.  The fake PPMS ramps the field in simulated time and can
report a stale status for a while after a command; the fake sample takes the
state written by the last pulse.  Only WindowTests needs a display.
"""
import importlib.util
import math
import queue
import sys
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
        if self.field != self.target:
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

    def read(self):
        return {"angle_deg": self.angle, "status": "AtTarget"}

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
            ("D", 0.0, 1500.0, 22.0), ("A", 270.0, -1500.0, 22.0), ("A'", 270.0, 1500.0, -22.0),
            ("B", 270.0, 1500.0, 22.0), ("B'", 270.0, -1500.0, -22.0), ("E", 0.0, -1500.0, 22.0)))
        settings = World().settings()
        self.assertEqual(settings.sequence, ("D", "A", "B", "E"))
        self.assertEqual(settings.pulse_width_s, 30e-6)
        self.assertEqual(settings.pulse_count, 1)
        self.assertEqual(settings.read_current_rms_a, 0.5e-3)
        self.assertEqual(settings.read_frequency_hz, core.PPMS_TRANSPORT_FREQUENCY_HZ)
        self.assertEqual(ms.DEFAULT_CYCLE, "D A B E B A")

    def test_sequence_text(self):
        names = tuple(name for name, *_ in ms.DEFAULT_STATES)
        self.assertEqual(ms.parse_sequence("d, a' B;e", names), ("D", "A'", "B", "E"))
        self.assertEqual(ms.parse_sequence("D-A-B-E-B-A", names), ("D", "A", "B", "E", "B", "A"))
        for text in ("", "D X", "C"):
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
        fields = [e for e in events if e[0] == "field"]
        self.assertTrue(all(mode == 1 for _, _, mode in fields), "every field command is Driven")
        order = [e[0] if e[0] != "field" else f"field {e[1]:+g}" for e in events
                 if e[0] in ("field", "pulse", "sine", "rotate")]
        self.assertEqual(order, [
            "field +1500", "pulse", "field +0", "sine",
            "rotate", "field -1500", "pulse", "field +0", "sine",
            "field +1500", "pulse", "field +0", "sine",
            "rotate", "field -1500", "pulse", "field +0", "sine"])

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

    def test_cycle_until_stop(self):
        world = World()
        world.stop.stop_when = lambda: len(world.points()) >= 8
        with self.assertRaises(core.PPMSScanCancelled):
            world.run(world.settings(ms.DEFAULT_CYCLE, until_stop=True))
        rows = world.points()
        self.assertEqual("".join(row["state"] for row in rows), "DABEBADA")
        for row in rows:
            self.assertAlmostEqual(row["x_v"], HALL_OHM[row["state"]] * 0.5e-3, places=12)
        self.assertEqual([e[1] for e in world.events if e[0] == "rotate"], [270.0, 0.0, 270.0, 0.0, 270.0])
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
        world.stop.stop_when = lambda: world.ppms.target == -1500.0
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
        world.stop.stop_when = lambda: world.ppms.target == -1500.0
        with self.assertRaises(core.PPMSScanCancelled):
            world.run()
        self.assertEqual([e[1] for e in world.events if e[0] == "field"][-2:], [-1500.0, 0.0])
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
        app.csv_file = mock.Mock()
        app.csv_writer = mock.Mock()
        app.csv_writer.writerow.side_effect = OSError("disk full (test)")
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
        self.assertEqual(app.plot.set_rows.call_count, 2, "the plot keeps every point")
        self.assertTrue(any("CSV write failed at step 2" in text for text in app.logged))

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
        self.assertFalse(app.until_stop_var.get())

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

    def test_cycle_and_two_state_windows(self):
        for mode, title in (("cycle", "MS-cycleState"), ("two", "MS-twoState")):
            with self.subTest(mode=mode):
                window = tk.Toplevel(self.root)
                window.withdraw()
                app = ms.FourStateApp(window, mode)
                self.addCleanup(window.destroy)
                self.assertEqual(window.title(), title)
                self.assertTrue(app.until_stop_var.get(), "runs until Stop by default")
                text = app.summary(app.current_settings())
                self.assertIn("until Stop", text)
                if mode == "cycle":
                    self.assertEqual(app.current_settings().sequence, ("D", "A", "B", "E", "B", "A"))
                    self.assertIn("4 large rotation(s) per pass", text)
                else:
                    self.assertEqual(app.current_settings().sequence, ("A", "B"))
                    app.pair_vars[1].set("A")
                    with self.assertRaises(ValueError):
                        app.current_settings()


if __name__ == "__main__":
    unittest.main()
