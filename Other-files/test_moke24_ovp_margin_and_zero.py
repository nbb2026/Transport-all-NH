"""MOKE-24 (user, 2026-10-13), two approved changes.

1. OVP margin warning: the software limit equals the hardware OVP (200 V on
   a 2410, 160 V on a 2400) and the OVP levels are ±5 %, so a scan near the
   limit can be clipped.  The scan's Start confirmation now warns when the
   scan's largest |V| is above 95 % of the connected model's OVP, with the
   margin.  Nothing is refused: the user can still start.
2. Zero on an output that is on: it went through apply_manual_voltage(0.0),
   which on the high range changed the range and so sent :OUTP OFF and
   :OUTP ON at 0 V without the safety tick.  Now it writes :SOUR:VOLT 0
   (checked), reads it back, and keeps the range and the output; no :OUTP
   command.  A failed write or readback is reported, never shown as 0 V.

No Tk and no instrument: the real start, submit and finish methods and the
real 2410 controller run on stand-ins; every dialog is mocked, and a real Tk
root fails the test instead of opening a window."""
import tkinter.messagebox as tk_messagebox
import types
import unittest
from unittest import mock

from test_moke24_current_limit import page as moke24_page
from test_nonlocal_keithley import Var
from test_ppms_motor_debug import app

_no_tk = mock.patch.object(
    app.tk, "Tk", side_effect=AssertionError("a test reached a real Tk window"))


def setUpModule():
    # A dialog that is not mocked would create a Tk root: fail instead.
    _no_tk.start()


def tearDownModule():
    _no_tk.stop()


def mock_dialogs(test, askyesno=False):
    """Every messagebox and filedialog call returns at once; returns the
    mocks by name.  app.messagebox is tkinter.messagebox, patched here."""
    mocks = {}
    for name in ("showinfo", "showwarning", "showerror", "askquestion", "askokcancel",
                 "askyesnocancel", "askretrycancel"):
        mocks[name] = mock.Mock(return_value=None)
    mocks["askyesno"] = mock.Mock(return_value=askyesno)
    for name, dialog in mocks.items():
        for module in {id(tk_messagebox): tk_messagebox, id(app.messagebox): app.messagebox}.values():
            patcher = mock.patch.object(module, name, dialog)
            patcher.start()
            test.addCleanup(patcher.stop)
    for name in ("askopenfilename", "asksaveasfilename", "askdirectory"):
        patcher = mock.patch.object(app.filedialog, name, mock.Mock(return_value=""))
        patcher.start()
        test.addCleanup(patcher.stop)
    return mocks


class Keithley:
    """A 2410/2400 that records every write.  ``fail`` names a write or a
    query that times out; ``error`` is what SYST:ERR? answers after a write
    of ``error_after``; ``readback`` overrides :SOUR:VOLT?."""

    def __init__(self, voltage="0", source_range="20", ovp="200", fail=None,
                 error_after=None, readback=None):
        self.commands, self.queries = [], []
        self.voltage, self.range, self.ovp = voltage, source_range, ovp
        self.fail, self.error_after, self.readback = fail, error_after, readback
        self.last = None

    def write(self, command):
        if command == self.fail:
            raise TimeoutError(f"VI_ERROR_TMO on {command}")
        self.commands.append(command)
        self.last = command
        if command.startswith(":SOUR:VOLT "):
            self.voltage = command.split(" ", 1)[1]
        elif command.startswith(":SOUR:VOLT:RANG "):
            self.range = command.split(" ", 1)[1]

    def query(self, command):
        if command == self.fail:
            raise TimeoutError(f"VI_ERROR_TMO on {command}")
        self.queries.append(command)
        if command == "SYST:ERR?":
            if self.error_after is not None and self.last == self.error_after:
                return '-221,"Settings conflict"'
            return "0,No error"
        if command == ":SOUR:VOLT?":
            return self.voltage if self.readback is None else self.readback
        return {":SOUR:VOLT:PROT?": self.ovp, ":SOUR:VOLT:RANG?": self.range}[command]


# ---------------------------------------------------------------- OVP margin

def scan_page(model, start, end, step="5"):
    """Connected, ROI chosen, difference mode on, the safety tick set: Start
    goes as far as its confirmation."""
    a = moke24_page()
    controller = a.keithley_controller
    controller.set_model(model)
    controller.instrument = Keithley()
    a.keithley_safety_var = Var(True)
    a.keithley_roi = (0, 0, 10, 10)
    a.camera_window = types.SimpleNamespace(worker=types.SimpleNamespace(background_enabled=True))
    a.keithley_start_var, a.keithley_end_var = Var(start), Var(end)
    a.keithley_step_var, a.keithley_settle_var, a.keithley_collect_var = Var(step), Var("1"), Var("1")
    a.keithley_save_images_var = Var(False)
    a.keithley_image_directory = None
    return a


class OvpMarginTests(unittest.TestCase):
    def confirm_text(self, model, start, end, step="5"):
        """Press Start, answer No; the confirmation's text."""
        dialogs = mock_dialogs(self, askyesno=False)
        a = scan_page(model, start, end, step)
        a.start_keithley_scan()
        dialogs["showwarning"].assert_not_called()
        dialogs["showerror"].assert_not_called()
        dialogs["askyesno"].assert_called_once()
        self.assertEqual(a.keithley_controller.instrument.commands, [], "No sends nothing")
        self.assertFalse(a.keithley_scan_running)
        return dialogs["askyesno"].call_args.args[1]

    def test_2410_above_95_percent_warns_with_the_margin(self):
        text = self.confirm_text("2410", "0", "195")
        self.assertIn("Near the hardware OVP", text)
        self.assertIn("|V| = 195 V", text)
        self.assertIn("margin of only 5 V (2.5 %)", text)
        self.assertIn("2410's 200 V OVP", text)
        self.assertIn("may act from 190 V", text)
        self.assertIn("may clip", text)
        self.assertIn("High-voltage scan", text, "the high-voltage warning stays")
        self.assertTrue(text.endswith("Start?"))

    def test_2410_at_the_limit_shows_a_zero_margin(self):
        text = self.confirm_text("2410", "0", "200")
        self.assertIn("margin of only 0 V (0 %)", text)

    def test_negative_scans_count_by_magnitude(self):
        text = self.confirm_text("2410", "0", "-195")
        self.assertIn("|V| = 195 V", text)

    def test_2400_uses_its_own_160_v_ovp(self):
        text = self.confirm_text("2400", "0", "156", step="4")
        self.assertIn("margin of only 4 V (2.5 %)", text)
        self.assertIn("2400's 160 V OVP", text)
        self.assertIn("may act from 152 V", text)

    def test_the_warning_starts_above_95_percent_of_the_ovp(self):
        # At 95 % (190 V on a 2410, 152 V on a 2400) or below: no warning;
        # the next volt up: the warning.
        for model, end, warned in (("2410", "1", False), ("2410", "100", False),
                                   ("2410", "190", False), ("2410", "191", True),
                                   ("2400", "152", False), ("2400", "153", True)):
            with self.subTest(model=model, end=end):
                text = self.confirm_text(model, "0", end, step="1")
                if warned:
                    self.assertIn("Near the hardware OVP", text)
                else:
                    self.assertNotIn("OVP", text)

    def test_the_user_can_still_start(self):
        # Yes goes on past the confirmation: the next check (image saving
        # ticked without a folder) is reached, so nothing is refused for
        # the margin and nothing is sent before that check.
        dialogs = mock_dialogs(self, askyesno=True)
        a = scan_page("2410", "0", "200")
        a.keithley_save_images_var = Var(True)
        a.start_keithley_scan()
        self.assertIn("Near the hardware OVP", dialogs["askyesno"].call_args.args[1])
        dialogs["showwarning"].assert_called_once()
        self.assertIn("save folder", dialogs["showwarning"].call_args.args[1])
        self.assertEqual(a.keithley_controller.instrument.commands, [])
        # The limit and OVP are unchanged.
        self.assertEqual(a.keithley_controller.max_abs_voltage_v, 200.0)
        self.assertEqual(a.keithley_controller.source_ovp_v, 200.0)


# ------------------------------------------------------------ Zero while ON

def zero_page(instrument, voltage=100.0, source_range=1000.0, output_on=True):
    """Connected with a manual voltage on: the output on at ``voltage`` on
    the ``source_range`` range, the safety tick cleared."""
    a = moke24_page()
    controller = a.keithley_controller
    controller.instrument = instrument
    controller.source_mode, controller.source_voltage_range_v = "VOLT", source_range
    controller.source_ovp_readback_v = 200.0
    controller.output_enabled = output_on
    controller.last_set_voltage = voltage
    a.keithley_current_voltage = voltage
    a.keithley_manual_voltage_var.set(f"{voltage:.9g}")
    a.keithley_voltage_var.set(f"Output: {voltage:+.6g} V / ON")
    return a


def press_zero(test, a):
    """What the Zero button calls (no safety tick needed)."""
    dialogs = mock_dialogs(test)
    a._submit_keithley_manual_voltage(0.0, "zero", require_safety=False)
    dialogs["showwarning"].assert_not_called()
    return dialogs


class ZeroWhileOnTests(unittest.TestCase):
    def assert_nothing_shown_as_zero(self, a, voltage=100.0):
        self.assertEqual(a.keithley_voltage_var.get(), f"Output: {voltage:+.6g} V / ON")
        self.assertEqual(a.keithley_manual_voltage_var.get(), f"{voltage:.9g}")
        self.assertEqual(a.keithley_current_voltage, voltage)
        self.assertEqual(a.keithley_controller.last_set_voltage, voltage)
        self.assertIn("Manual voltage failed", a.keithley_status_var.get())
        self.assertFalse(a.keithley_manual_busy)

    def test_high_range_zero_sets_0_v_only(self):
        instrument = Keithley(voltage="100", source_range="1000")
        a = zero_page(instrument)
        self.assertFalse(a.keithley_safety_var.get())
        press_zero(self, a)
        self.assertEqual(instrument.commands, [":SOUR:VOLT 0"], "no range change, no :OUTP")
        self.assertEqual(instrument.queries, ["SYST:ERR?", ":SOUR:VOLT?"])
        controller = a.keithley_controller
        self.assertTrue(controller.output_enabled)
        self.assertEqual(controller.source_voltage_range_v, 1000.0)
        self.assertEqual(controller.source_mode, "VOLT")
        self.assertEqual(controller.last_set_voltage, 0.0)
        self.assertEqual(a.keithley_current_voltage, 0.0, "Up/Down step from 0 V")
        self.assertEqual(a.keithley_manual_voltage_var.get(), "0")
        self.assertEqual(a.keithley_voltage_var.get(), "Output: +0 V / ON")
        self.assertIn("stays on", a.keithley_status_var.get())
        self.assertFalse(a.keithley_manual_busy)

    def test_low_range_zero_sets_0_v_only_and_write_reports_as_before(self):
        instrument = Keithley(voltage="5", source_range="20")
        a = zero_page(instrument, voltage=5.0, source_range=20.0)
        press_zero(self, a)
        self.assertEqual(instrument.commands, [":SOUR:VOLT 0"])
        self.assertEqual(a.keithley_controller.source_voltage_range_v, 20.0)
        self.assertEqual(a.keithley_voltage_var.get(), "Output: +0 V / ON")
        self.assertEqual(
            a.keithley_status_var.get(),
            "Zero: +0 V read back; the output stays on, range unchanged")
        # A manual Write (tick set) keeps its own status text.
        a.keithley_safety_var = Var(True)
        a.keithley_manual_voltage_var.set("1")
        dialogs = mock_dialogs(self)
        a.write_keithley_manual_voltage()
        dialogs["showwarning"].assert_not_called()
        self.assertEqual(instrument.commands, [":SOUR:VOLT 0", ":SOUR:VOLT 1"])
        self.assertEqual(a.keithley_status_var.get(), "Manual voltage set to +1 V")

    def assert_retry_sets_0_v_only(self, a, instrument):
        """Zero pressed again once the instrument answers: still 0 V only,
        on the range and output the failure left as they were."""
        instrument.commands.clear()
        press_zero(self, a)
        self.assertEqual(instrument.commands, [":SOUR:VOLT 0"])
        self.assertEqual(a.keithley_controller.source_voltage_range_v, 1000.0)
        self.assertEqual(a.keithley_voltage_var.get(), "Output: +0 V / ON")

    def test_a_failed_write_is_not_shown_as_zero(self):
        instrument = Keithley(voltage="100", source_range="1000", fail=":SOUR:VOLT 0")
        a = zero_page(instrument)
        a.closing = False  # the page is open: the error is shown as a dialog too
        dialogs = press_zero(self, a)
        dialogs["showerror"].assert_called_once()
        self.assertIn("VI_ERROR_TMO", dialogs["showerror"].call_args.args[1])
        self.assertEqual(instrument.commands, [])
        self.assertTrue(a.keithley_controller.output_enabled, "the output state is not guessed")
        self.assert_nothing_shown_as_zero(a)
        self.assertIn("VI_ERROR_TMO", a.keithley_status_var.get())
        instrument.fail = None
        self.assert_retry_sets_0_v_only(a, instrument)

    def test_a_scpi_error_after_the_write_is_not_shown_as_zero(self):
        instrument = Keithley(voltage="100", source_range="1000", error_after=":SOUR:VOLT 0")
        a = zero_page(instrument)
        press_zero(self, a)
        self.assertEqual(instrument.commands, [":SOUR:VOLT 0"])
        self.assertNotIn(":SOUR:VOLT?", instrument.queries)
        self.assert_nothing_shown_as_zero(a)
        self.assertIn("SCPI error", a.keithley_status_var.get())
        instrument.error_after = None
        self.assert_retry_sets_0_v_only(a, instrument)

    def test_a_failed_readback_is_not_shown_as_zero(self):
        instrument = Keithley(voltage="100", source_range="1000", fail=":SOUR:VOLT?")
        a = zero_page(instrument)
        press_zero(self, a)
        self.assertEqual(instrument.commands, [":SOUR:VOLT 0"])
        self.assert_nothing_shown_as_zero(a)

    def test_a_wrong_or_invalid_readback_is_not_shown_as_zero(self):
        for readback in ("5", "nan", "garbage"):
            with self.subTest(readback=readback):
                instrument = Keithley(voltage="100", source_range="1000", readback=readback)
                a = zero_page(instrument)
                press_zero(self, a)
                self.assertEqual(instrument.commands, [":SOUR:VOLT 0"])
                self.assert_nothing_shown_as_zero(a)

    def test_not_a_voltage_source_sends_nothing(self):
        instrument = Keithley()
        a = zero_page(instrument)
        a.keithley_controller.source_mode = "CURR"
        press_zero(self, a)
        self.assertEqual(instrument.commands, [])
        self.assertIn("not in voltage-source mode", a.keithley_status_var.get())
        self.assert_nothing_shown_as_zero(a)

    def test_after_the_red_off_button_zero_keeps_the_output_off(self):
        # Zero while on keeps the output on; after the red "zero / output
        # off" button, Zero is as in commit 2441d7b: 0 V and :OUTP OFF,
        # never :OUTP ON.
        instrument = Keithley(voltage="100", source_range="1000")
        a = zero_page(instrument)
        press_zero(self, a)
        self.assertEqual(instrument.commands, [":SOUR:VOLT 0"])
        self.assertTrue(a.keithley_controller.output_enabled)
        mock_dialogs(self)
        a.stop_keithley_scan()
        self.assertEqual(instrument.commands, [":SOUR:VOLT 0"] * 2 + [":OUTP OFF"])
        self.assertFalse(a.keithley_controller.output_enabled)
        instrument.commands.clear()
        press_zero(self, a)
        self.assertEqual(instrument.commands, [":SOUR:VOLT 0", ":OUTP OFF"])
        self.assertFalse(a.keithley_controller.output_enabled)
        self.assertEqual(a.keithley_voltage_var.get(), "Output: 0 V / OFF")
        self.assertIn("stays off", a.keithley_status_var.get())


if __name__ == "__main__":
    unittest.main()
