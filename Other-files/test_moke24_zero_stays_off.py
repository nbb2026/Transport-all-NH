"""MOKE-24 (audit/review, 2026-10-13): the manual 'Zero' button needs no
safety tick, but it went through apply_manual_voltage(0.0), which sends
:OUTP ON when the output is off.  So Zero right after Connect (which clears
the tick), after a scan or after the red 'zero / output off' button
switched the output on at 0 V without the tick.  Now Zero on an output that
is off sends 0 V and :OUTP OFF again, never :OUTP ON, and the page shows
"0 V / OFF"; on an output that is on it writes 0 V as before.

No Tk and no instrument: the real controls, submit and finish methods and
the real 2410 controller, with a stand-in 2410 and every dialog mocked."""
import unittest
from unittest import mock

from test_moke24_current_limit import page as moke24_page
from test_ppms_motor_debug import app


class Keithley:
    """A 2410 that accepts every command; ``fail`` names one that times out."""

    def __init__(self, fail=None):
        self.commands, self.fail, self.voltage = [], fail, "0"

    def write(self, command):
        if command == self.fail:
            raise TimeoutError(f"VI_ERROR_TMO on {command}")
        self.commands.append(command)
        if command.startswith(":SOUR:VOLT "):
            self.voltage = command.split(" ", 1)[1]

    def query(self, command):
        return {"SYST:ERR?": "0,No error", ":SOUR:VOLT?": self.voltage}[command]


def page(output_on, instrument):
    """Connected as Connect leaves it: the voltage source set up on the 20 V
    range, the safety tick cleared; the output on or off."""
    a = moke24_page()
    controller = a.keithley_controller
    controller.instrument = instrument
    controller.source_mode, controller.source_voltage_range_v = "VOLT", 20.0
    controller.output_enabled = output_on
    a.keithley_voltage_var.set("Output: 0 V / OFF")
    return a


def press_zero(a):
    """What the Zero button calls."""
    with mock.patch.object(app.messagebox, "showwarning") as warning, \
            mock.patch.object(app.messagebox, "showerror") as error:
        a._submit_keithley_manual_voltage(0.0, "zero", require_safety=False)
    warning.assert_not_called()
    return error


class ZeroTests(unittest.TestCase):
    def test_zero_never_switches_an_off_output_on(self):
        instrument = Keithley()
        a = page(False, instrument)
        self.assertFalse(a.keithley_safety_var.get())
        press_zero(a)
        self.assertNotIn(":OUTP ON", instrument.commands)
        self.assertEqual(instrument.commands, [":SOUR:VOLT 0", ":OUTP OFF"])
        self.assertFalse(a.keithley_controller.output_enabled)
        self.assertEqual(a.keithley_voltage_var.get(), "Output: 0 V / OFF")
        self.assertIn("stays off", a.keithley_status_var.get())
        self.assertFalse(a.keithley_manual_busy)

    def test_zero_on_an_output_that_is_on_writes_0_v_as_before(self):
        instrument = Keithley()
        instrument.voltage = "5"
        a = page(True, instrument)
        press_zero(a)
        self.assertNotIn(":OUTP OFF", instrument.commands)
        self.assertIn(":SOUR:VOLT 0", instrument.commands)
        self.assertTrue(a.keithley_controller.output_enabled)
        self.assertEqual(a.keithley_voltage_var.get(), "Output: +0 V / ON")

    def test_a_failed_off_is_reported_not_shown_as_off(self):
        instrument = Keithley(fail=":OUTP OFF")
        a = page(False, instrument)
        a.keithley_voltage_var.set("Output: check")
        press_zero(a)
        self.assertNotIn(":OUTP ON", instrument.commands)
        self.assertIn("failed to turn output off", a.keithley_status_var.get())
        self.assertEqual(a.keithley_voltage_var.get(), "Output: check", "not set to OFF")
        self.assertFalse(a.keithley_manual_busy)

    def test_write_still_needs_the_tick(self):
        instrument = Keithley()
        a = page(False, instrument)
        a.keithley_manual_voltage_var.set("1")
        with mock.patch.object(app.messagebox, "showwarning") as warning:
            a.write_keithley_manual_voltage()
        self.assertIn("safety confirmation", warning.call_args.args[1])
        self.assertEqual(instrument.commands, [])


if __name__ == "__main__":
    unittest.main()
