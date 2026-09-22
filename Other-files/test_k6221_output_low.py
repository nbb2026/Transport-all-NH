"""The 6221 must run with TRIAX -> OUTPUT LOW -> FLOATING.

Earth-grounding the output low ties the sample to mains ground and forms a
loop with the PPMS rack and the lock-in inputs. Both places that bring a 6221
up have to leave it floating, and say so plainly when the instrument refuses.
"""
import threading
import unittest

from test_ppms_motor_debug import app


class FakeK6221:
    """Answers the handful of queries the bring-up sequences make."""

    def __init__(self, low_to_earth=1, stuck=False, reply=None):
        self.low_to_earth = low_to_earth
        self.stuck = stuck
        self.reply = reply
        self.writes = []

    def write(self, command):
        self.writes.append(command)
        if command == app.K6221_OUTPUT_LOW_FLOATING and not self.stuck:
            self.low_to_earth = 0

    def query(self, command):
        if command == app.K6221_OUTPUT_LOW_QUERY:
            return self.reply if self.reply is not None else f"{self.low_to_earth}\n"
        if command == "*ESR?":  # standard event status register: one integer
            return "0\n"
        if command in (":SYST:ERR?", "SYST:ERR?"):
            return '0,"No error"\n'
        raise AssertionError(f"unexpected query {command!r}")


class ControllerTests(unittest.TestCase):
    """The MOKE-6221 page, which resets the instrument before configuring."""

    def controller(self, fake):
        made = app.Keithley6221Controller.__new__(app.Keithley6221Controller)
        made.instrument = fake
        made.logger = lambda *_: None
        made._query_error = lambda *_: None
        return made

    def test_grounded_output_low_is_set_to_floating(self):
        fake = FakeK6221(low_to_earth=1)
        self.controller(fake)._set_output_low_floating()
        self.assertIn(app.K6221_OUTPUT_LOW_FLOATING, fake.writes)
        self.assertEqual(fake.low_to_earth, 0)

    def test_already_floating_is_left_alone(self):
        fake = FakeK6221(low_to_earth=0)
        self.controller(fake)._set_output_low_floating()
        self.assertEqual(fake.writes, [], "no write when it already floats")

    def test_instrument_that_refuses_raises_with_the_panel_path(self):
        fake = FakeK6221(low_to_earth=1, stuck=True)
        with self.assertRaises(RuntimeError) as caught:
            self.controller(fake)._set_output_low_floating()
        self.assertIn("OUTPUT LOW", str(caught.exception))

    def test_on_off_replies_are_understood(self):
        for reply, expected in (("OFF\n", 0), ("ON\n", 1), ("0", 0), ("1", 1)):
            self.assertEqual(
                self.controller(FakeK6221(reply=reply))._read_output_low_to_earth(),
                expected, reply)

    def test_unknown_reply_is_reported(self):
        with self.assertRaises(RuntimeError):
            self.controller(FakeK6221(reply="MAYBE\n"))._read_output_low_to_earth()


class RackTests(unittest.TestCase):
    """The PPMS and RT-E rack, which does not reset the instrument."""

    def panel(self, fake):
        made = app.CombinedControlApp.__new__(app.CombinedControlApp)
        controller = type("C", (), {})()
        controller.instrument = fake
        controller.lock = threading.RLock()
        controller.label = "Keithley 6221"
        made.ppms_instrument_controllers = {"6221": controller}
        return made

    def test_grounded_output_low_is_set_to_floating(self):
        fake = FakeK6221(low_to_earth=1)
        self.panel(fake)._initialize_ppms_6221()
        self.assertIn(app.K6221_OUTPUT_LOW_FLOATING, fake.writes)
        self.assertEqual(fake.low_to_earth, 0)

    def test_already_floating_leaves_a_running_source_untouched(self):
        fake = FakeK6221(low_to_earth=0)
        self.panel(fake)._initialize_ppms_6221()
        self.assertEqual(fake.writes, [])

    def test_instrument_that_refuses_raises_with_the_panel_path(self):
        fake = FakeK6221(low_to_earth=1, stuck=True)
        with self.assertRaises(RuntimeError) as caught:
            self.panel(fake)._initialize_ppms_6221()
        self.assertIn("OUTPUT LOW", str(caught.exception))

    def test_connect_runs_the_6221_bring_up(self):
        """The rack connect path has to reach _initialize_ppms_6221."""
        panel = app.CombinedControlApp.__new__(app.CombinedControlApp)
        called = []
        controller = type("C", (), {"connect": lambda self, resource: "KEITHLEY,6221"})()
        panel.ppms_instrument_controllers = {"6221": controller}
        panel._initialize_ppms_6221 = lambda: called.append(True)
        panel._connect_and_initialize_ppms_device("6221", "GPIB0::12::INSTR")
        self.assertEqual(called, [True])


if __name__ == "__main__":
    unittest.main()
