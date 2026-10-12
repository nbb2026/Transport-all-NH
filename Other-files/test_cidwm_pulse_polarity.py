"""CIDWM pulse polarity (user, 2026-10-12): Apply +I pulse and Apply −I
pulse use |Pulse current| with the button's sign.  A −I train is the 6221
square wave between 0 A and −|I| (amplitude |I|/2, offset −|I|/2).

No Tk and no instrument: a stand-in 6221 records the commands."""
import threading
import unittest
from unittest import mock

from test_nonlocal_keithley import Var, bare_app
from test_ppms_motor_debug import app


class Instrument:
    """A 6221 that echoes what it was set to."""

    def __init__(self):
        self.commands = []
        self.values = {}

    def write(self, command):
        self.commands.append(command)
        key, _, value = command.partition(" ")
        self.values[key] = value

    def query(self, command):
        replies = {
            "SYST:ERR?": "0,No error", "SOUR:WAVE:AMPL?": self.values.get("SOUR:WAVE:AMPL", "0"),
            "SOUR:WAVE:OFFS?": self.values.get("SOUR:WAVE:OFFS", "0"),
            "SOUR:WAVE:FREQ?": self.values.get("SOUR:WAVE:FREQ", "0"),
            "CURR:COMP?": self.values.get("CURR:COMP", "0"),
            "SOUR:WAVE:DUR:CYCL?": self.values.get("SOUR:WAVE:DUR:CYCL", "0"),
            "OUTP?": "0", "STAT:MEAS:COND?": "0",
        }
        return replies[command]


def page(current="2"):
    a = bare_app()
    a.k6221_controller = app.Keithley6221Controller(lambda _message: None)
    a.k6221_controller.instrument = Instrument()
    a.k6221_safety_var = Var(True)
    a.k6221_current_var, a.k6221_width_var = Var(current), Var("50")
    a.k6221_compliance_var, a.k6221_count_var = Var("100"), Var("1")
    a.k6221_stop_event = threading.Event()
    a.k6221_output_var, a.k6221_status_var = Var(), Var()
    a._update_k6221_controls = mock.Mock()
    a.k6221_executor = mock.Mock()
    return a


class PolarityTests(unittest.TestCase):
    def submitted_current(self, polarity, current="2"):
        a = page(current)
        a.apply_k6221_output(polarity)
        worker, current_a, width_s, compliance_v, count = a.k6221_executor.submit.call_args.args
        self.assertEqual((width_s, compliance_v, count), (50e-6, 100.0, 1))
        return a, current_a

    def test_the_buttons_set_the_sign_of_the_size(self):
        for polarity, current, expected in ((1, "2", 2e-3), (-1, "2", -2e-3),
                                            (1, "-2", 2e-3), (-1, "-2", -2e-3)):
            with self.subTest(polarity=polarity, current=current):
                a, current_a = self.submitted_current(polarity, current)
                self.assertAlmostEqual(current_a, expected)
        a, _current = self.submitted_current(-1)
        self.assertEqual(a.k6221_output_var.get(), "Running: 0 ↔ -2 mA; 50 us × 1")

    def test_a_negative_train_is_the_square_wave_between_0_a_and_minus_i(self):
        controller = page().k6221_controller
        result = controller.set_dc_pulse(-2e-3, 50e-6, 100.0, 1)
        values = controller.instrument.values
        self.assertAlmostEqual(float(values["SOUR:WAVE:AMPL"]), 1e-3)
        self.assertAlmostEqual(float(values["SOUR:WAVE:OFFS"]), -1e-3, msg="levels 0 A and -2 mA")
        self.assertAlmostEqual(float(values["SOUR:WAVE:FREQ"]), 1e4, msg="50 us per half")
        self.assertEqual(values["SOUR:WAVE:DUR:CYCL"], "1")
        self.assertAlmostEqual(result["current_a"], -2e-3)

    def test_zero_stays_off(self):
        a, current_a = self.submitted_current(-1, "0")
        self.assertEqual(str(current_a), "0.0", "no -0 shown")
        self.assertEqual(a.k6221_output_var.get(), "Running: 0 ↔ +0 mA; 50 us × 1")


if __name__ == "__main__":
    unittest.main()
