"""Avtech AVR-E3-B pulse generator on CIDWM (user, 2026-10-13), against a
simulated generator that answers as Avtech's manuals describe.  No GPIB, no
window.

One pulse: trigger on hold, width and amplitude set and read back, output
on, "trigger:source immediate" once, then hold and the output off, read
back.  A pulse is never fired with a sign, amplitude or width the generator
did not take.
"""
import types
import unittest
from unittest import mock

from test_ppms_motor_debug import app


class FakeAvtech:
    """The generator at the SCPI level: its state, the commands it got and
    the pulses it fired."""

    def __init__(self, identity="AVTECH ELECTROSYSTEMS,AVR-E3-B-PN-W3,14486,5.07",
                 polarities=(1, -1), stuck_on=False, trip=False, fail=(), replies=None):
        self.identity = identity
        self.polarities = polarities
        self.stuck_on, self.trip = stuck_on, trip
        # fail: (command, which occurrence) writes that time out; replies: fixed query answers.
        self.fail, self.replies = set(fail), dict(replies or {})
        self.trigger, self.output, self.width, self.amplitude = "internal", 0, 1e-9, 0.1
        self.delay = 0.5
        self.errors, self.pulses, self.log = [], [], []
        self.tripped = 0
        self.clears = 0

    def clear(self):
        self.clears += 1
        self.log.append("<device clear>")

    def write(self, command):
        occurrence = sum(1 for entry in self.log if entry == command) + 1
        self.log.append(command)
        if (command, occurrence) in self.fail:
            raise TimeoutError(f"VI_ERROR_TMO on {command}")
        word, _, value = command.strip().lower().partition(" ")
        if word == "*cls":
            self.errors.clear()
        elif word == "pulse:delay":
            self.delay = float(value)
        elif word == "trigger:source":
            if value == "immediate" and self.output:
                self.pulses.append((self.amplitude, self.width))
                if self.trip:
                    self.tripped = 1
                    self.errors.append('1001,"Device-specific error; Overload condition detected!"')
            self.trigger = value
        elif word == "output":
            self.output = 1 if value in ("on", "1") else 0
        elif word == "pulse:width":
            self.width = float(value)
        elif word == "volt:ampl":
            requested = float(value)
            if (1 if requested > 0 else -1) in self.polarities:
                self.amplitude = requested
            else:
                self.errors.append('-222,"Data out of range"')

    def query(self, command):
        self.log.append(command)
        command = command.strip().lower()
        if command in self.replies:
            return self.replies[command]
        if command == "pulse:delay?":
            return f"{self.delay:.4e}"
        if command == "*idn?":
            return self.identity
        if command == "pulse:width?":
            return f"{self.width:.4e}"
        if command == "volt:ampl?":
            return f"{self.amplitude:.4e}"
        if command == "output?":
            return "1" if self.output or self.stuck_on else "0"
        if command == "syst:err?":
            return self.errors.pop(0) if self.errors else '0,"No error"'
        if command == "output:protection:tripped?":
            return str(self.tripped)
        raise AssertionError(f"unexpected query {command}")

    def close(self):
        pass


def controller(fake):
    c = app.AvtechPulserController(lambda _message: None)
    c.instrument = fake
    c.identity = fake.identity
    return c


class SinglePulseTests(unittest.TestCase):
    def test_one_pulse_in_avtechs_sequence_and_off_after(self):
        fake = FakeAvtech()
        result = controller(fake).single_pulse(20.0, 10e-9)
        self.assertEqual(fake.pulses, [(20.0, 1e-8)])
        log = fake.log
        on, fire = log.index("output on"), log.index("trigger:source immediate")
        self.assertEqual(log.count("trigger:source immediate"), 1)
        self.assertLess(log.index("trigger:source hold"), on, "triggering held before the output goes on")
        for step in ("*CLS", "pulse:delay 0", "pulse:delay?", "pulse:width 1e-08", "pulse:width?",
                     "volt:ampl 20", "volt:ampl?", "syst:err?"):
            self.assertLess(log.index(step), on, f"{step} before the output goes on")
        self.assertLess(on, fire)
        after = log[fire + 1:]
        self.assertLess(after.index("trigger:source hold"), after.index("output off"))
        self.assertEqual(after[after.index("output off") + 1], "output?", "off read back")
        self.assertEqual((fake.output, fake.trigger), (0, "hold"))
        self.assertEqual((result["amplitude_v"], result["width_s"], result["overload"]), (20.0, 1e-8, False))

    def test_a_sign_the_unit_cannot_give_fires_nothing(self):
        # The model says -P: refused before any command.
        fake = FakeAvtech(identity="AVTECH ELECTROSYSTEMS,AVR-E3-B-P-W3,14486,5.07", polarities=(1,))
        with self.assertRaisesRegex(RuntimeError, "positive pulses only.*no pulse fired"):
            controller(fake).single_pulse(-20.0, 10e-9)
        self.assertEqual((fake.pulses, fake.log), ([], []))
        # The model does not say, and the unit keeps its old amplitude:
        # the read-back stops it, and it ends off.
        fake = FakeAvtech(identity="AVTECH ELECTROSYSTEMS,AVR-E3-B-W3,14486,5.07", polarities=(1,))
        with self.assertRaisesRegex(app.AvtechPulseError, "no pulse fired: .*took \\+0.1 V for -20 V") as caught:
            controller(fake).single_pulse(-20.0, 10e-9)
        self.assertFalse(caught.exception.fired)
        self.assertEqual(fake.pulses, [])
        self.assertNotIn("trigger:source immediate", fake.log)
        self.assertNotIn("output on", fake.log)
        self.assertEqual((fake.output, fake.trigger), (0, "hold"))

    def test_an_output_that_stays_on_is_reported(self):
        fake = FakeAvtech(stuck_on=True)
        with self.assertRaisesRegex(app.AvtechPulseError,
                                    "pulse fired, then: its output did not read back off; check the generator") as caught:
            controller(fake).single_pulse(20.0, 10e-9)
        self.assertEqual(len(fake.pulses), 1)
        self.assertTrue(caught.exception.fired)
        self.assertEqual(caught.exception.amplitude_v, 20.0)

    def test_an_overload_is_returned(self):
        """It is also logged as error 1001 (programming manual 3.2.4): an
        overload, not a failure (review, 2026-10-13)."""
        fake = FakeAvtech(trip=True)
        self.assertTrue(controller(fake).single_pulse(20.0, 10e-9)["overload"])
        self.assertEqual(fake.output, 0)

    def test_a_hold_that_times_out_after_the_pulse_still_switches_the_output_off(self):
        """Review, 2026-10-13: a failed hold write had skipped "output off"."""
        # The hold right after the pulse times out, and again in the off path.
        fake = FakeAvtech(fail={("trigger:source hold", 2), ("trigger:source hold", 3), ("trigger:source hold", 4)})
        with self.assertRaisesRegex(app.AvtechPulseError, "pulse fired, then:") as caught:
            controller(fake).single_pulse(20.0, 10e-9)
        self.assertTrue(caught.exception.fired)
        self.assertEqual(fake.output, 0, "the output went off anyway")
        self.assertGreaterEqual(fake.clears, 1, "a device clear before trying again")
        fired = fake.log.index("trigger:source immediate")
        self.assertIn("output off", fake.log[fired:])

    def test_a_delay_left_on_the_front_panel_is_set_to_0_first(self):
        fake = FakeAvtech()
        controller(fake).single_pulse(20.0, 10e-9)
        self.assertEqual(fake.delay, 0.0)
        self.assertLess(fake.log.index("pulse:delay 0"), fake.log.index("output on"))

    def test_a_nan_read_back_fires_nothing(self):
        fake = FakeAvtech(replies={"volt:ampl?": "nan"})
        with self.assertRaisesRegex(app.AvtechPulseError, "no pulse fired"):
            controller(fake).single_pulse(20.0, 10e-9)
        self.assertEqual(fake.pulses, [])

    def test_stale_errors_from_an_earlier_session_do_not_refuse_the_pulse(self):
        fake = FakeAvtech()
        fake.errors.append('-102,"Syntax error; Unrecognized command"')
        controller(fake).single_pulse(20.0, 10e-9)
        self.assertEqual(len(fake.pulses), 1)

    def test_the_limits(self):
        for amplitude, width in ((0.0, 10e-9), (150.0, 10e-9), (20.0, 0.5e-9), (20.0, 10e-6)):
            with self.subTest(amplitude=amplitude, width=width):
                with self.assertRaises(ValueError):
                    app.AvtechPulserController.validate(amplitude, width)
        self.assertEqual(app.AvtechPulserController.validate(-100.0, 5e-6), (-100.0, 5e-6))

    def test_the_polarity_from_the_model(self):
        for identity, expected in (("AVTECH,AVR-E3-B-PN-W3,1,5.07", (1, -1)),
                                   ("AVTECH,AVR-E3-B-P-W3,1,5.07", (1,)),
                                   ("AVTECH,AVR-E3-B-N,1,5.07", (-1,)),
                                   ("AVTECH,AVR-E3-B-W3,1,5.07", None),
                                   # Only the model field (review, 2026-10-13).
                                   ("AVTECH,AVR-E3-B-W3,14486-N,5.07", None),
                                   ("AVTECH,AVR-E3-B-W3,14486,FW-P 5.07", None)):
            with self.subTest(identity=identity):
                c = app.AvtechPulserController(lambda _message: None)
                c.identity = identity
                self.assertEqual(c.polarities, expected)

    def test_the_defaults(self):
        self.assertEqual((app.AVTECH_DEFAULT_RESOURCE, app.AVTECH_DEFAULT_AMPLITUDE_V,
                          app.AVTECH_DEFAULT_WIDTH_NS), ("GPIB0::8::INSTR", 10.0, 10.0))


class ConnectTests(unittest.TestCase):
    def connect(self, fake):
        manager = mock.Mock()
        manager.open_resource.return_value = fake
        c = app.AvtechPulserController(lambda _message: None)
        with mock.patch.object(app, "pyvisa", object()), \
                mock.patch.object(app, "acquire_shared_visa_manager", return_value=manager), \
                mock.patch.object(app, "release_shared_visa_manager"):
            identity = c.connect("GPIB0::8::INSTR")
        return c, identity

    def test_connecting_leaves_the_trigger_on_hold_and_the_output_off(self):
        fake = FakeAvtech()
        c, identity = self.connect(fake)
        self.assertIn("AVR-E3-B", identity)
        self.assertEqual(fake.log, ["*IDN?", "trigger:source hold", "output off", "*CLS"])
        self.assertEqual(fake.pulses, [])
        with mock.patch.object(app, "release_shared_visa_manager"):
            c.disconnect()
        self.assertNotIn("SYST:LOC", fake.log, "not an Avtech command (review, 2026-10-13)")
        self.assertEqual(fake.log[-3:], ["trigger:source hold", "output off", "output?"])
        self.assertFalse(c.connected)

    def test_another_instrument_gets_nothing_but_the_identity_query(self):
        fake = FakeAvtech(identity="KEITHLEY INSTRUMENTS,MODEL 6221,1,A01")
        with self.assertRaisesRegex(RuntimeError, "is not Avtech"):
            self.connect(fake)
        self.assertEqual(fake.log, ["*IDN?"])


class SourceExclusionTests(unittest.TestCase):
    """Review, 2026-10-13: the 6221 and the Avtech never pulse together."""

    def stand_in(self, **state):
        values = dict(k6221_busy=False, avtech_busy=False,
                      avtech_controller=mock.Mock(connected=True), k6221_controller=mock.Mock(connected=True))
        values.update(state)
        return types.SimpleNamespace(**values)

    def test_a_scan_waits_for_the_other_source(self):
        ready = app.CombinedControlApp._cidwm_source_ready
        for state, source, expected in (({}, "avtech", True), ({"k6221_busy": True}, "avtech", False),
                                        ({"avtech_busy": True}, "6221", False), ({}, "6221", True)):
            with self.subTest(state=state, source=source):
                self.assertIs(ready(self.stand_in(**state), source), expected)

    def test_a_6221_disconnect_leaves_an_avtech_scan_running(self):
        a = mock.Mock()
        a.__dict__.update(k6221_scan_running=True, cidwm_scan_source="avtech", k6221_busy=True, closing=True)
        app.CombinedControlApp._finish_k6221_disconnect(a, None)
        self.assertTrue(a.k6221_scan_running)


if __name__ == "__main__":
    unittest.main()
