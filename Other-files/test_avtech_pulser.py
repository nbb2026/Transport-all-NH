"""Avtech AVR-E3-B pulse generator on CIDWM (user, 2026-10-13), against a
simulated generator that answers as Avtech's manuals describe.  No GPIB, no
window.

One pulse: trigger on hold, width and amplitude set and read back, output
on, "trigger:source immediate" once, then hold and the output off, read
back.  A pulse is never fired with a sign, amplitude or width the generator
did not take.
"""
import unittest
from unittest import mock

from test_ppms_motor_debug import app


class FakeAvtech:
    """The generator at the SCPI level: its state, the commands it got and
    the pulses it fired."""

    def __init__(self, identity="AVTECH ELECTROSYSTEMS,AVR-E3-B-PN-W3,14486,5.07",
                 polarities=(1, -1), stuck_on=False, trip=False):
        self.identity = identity
        self.polarities = polarities
        self.stuck_on, self.trip = stuck_on, trip
        self.trigger, self.output, self.width, self.amplitude = "internal", 0, 1e-9, 0.1
        self.errors, self.pulses, self.log = [], [], []
        self.tripped = 0

    def write(self, command):
        self.log.append(command)
        word, _, value = command.strip().lower().partition(" ")
        if word == "trigger:source":
            if value == "immediate" and self.output:
                self.pulses.append((self.amplitude, self.width))
                self.tripped = int(self.trip)
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
        for step in ("pulse:width 1e-08", "pulse:width?", "volt:ampl 20", "volt:ampl?", "syst:err?"):
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
        with self.assertRaisesRegex(RuntimeError, "took \\+0.1 V for -20 V.*no pulse fired"):
            controller(fake).single_pulse(-20.0, 10e-9)
        self.assertEqual(fake.pulses, [])
        self.assertNotIn("trigger:source immediate", fake.log)
        self.assertNotIn("output on", fake.log)
        self.assertEqual((fake.output, fake.trigger), (0, "hold"))

    def test_an_output_that_stays_on_is_reported(self):
        fake = FakeAvtech(stuck_on=True)
        with self.assertRaisesRegex(RuntimeError, "fired the pulse, but its output did not read back off"):
            controller(fake).single_pulse(20.0, 10e-9)
        self.assertEqual(len(fake.pulses), 1)

    def test_an_overload_is_returned(self):
        self.assertTrue(controller(FakeAvtech(trip=True)).single_pulse(20.0, 10e-9)["overload"])

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
                                   ("AVTECH,AVR-E3-B-W3,1,5.07", None)):
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
        self.assertEqual(fake.log, ["*IDN?", "trigger:source hold", "output off"])
        self.assertEqual(fake.pulses, [])
        c.instrument = None
        app.release_instrument_lease("GPIB0::8::INSTR", c)

    def test_another_instrument_gets_nothing_but_the_identity_query(self):
        fake = FakeAvtech(identity="KEITHLEY INSTRUMENTS,MODEL 6221,1,A01")
        with self.assertRaisesRegex(RuntimeError, "is not Avtech"):
            self.connect(fake)
        self.assertEqual(fake.log, ["*IDN?"])


if __name__ == "__main__":
    unittest.main()
