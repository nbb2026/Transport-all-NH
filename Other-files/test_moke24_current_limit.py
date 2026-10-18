"""MOKE-24 (review, 2026-10-13): Connect is the only reader of the page's
'Current limit (A)'.  The field could still be edited while connected, so an
edit looked applied while scans, manual writes, the 21 mA refusal and the CSV
kept the limit set at Connect.  Now the field is locked from Connect until
Disconnect, as the Model list is, and the refusal says how to change it.

No Tk and no instrument: the real controls update, connect and disconnect
finishes and the real 2410 controller, with stand-in widgets."""
import threading
import types
import unittest
from unittest import mock

from test_k6221_failed_off_interlock import Now
from test_nonlocal_keithley import Var, bare_app
from test_ppms_motor_debug import app


def page():
    a = bare_app()
    a.keithley_controller = app.Keithley2410Controller(lambda _message: None)
    a.nonlocal_vi_2410_controller = mock.Mock(connected=False)
    a.nonlocal_vi_running = a.nonlocal_vi_busy = False
    a.keithley_connecting = a.keithley_scan_running = a.keithley_manual_busy = False
    a.magnet_scan_running = a.k6221_scan_running = False
    a.keithley_compliance_var = Var("0.05")
    a.keithley_resource_var, a.keithley_model_var = Var("GPIB0::24::INSTR"), Var("2410")
    for name in ("keithley_status_var", "keithley_connection_var", "keithley_hazard_var",
                 "keithley_voltage_var", "keithley_manual_voltage_var"):
        setattr(a, name, Var())
    a.keithley_safety_var = Var(False)
    a.keithley_data_lock = threading.Lock()
    a.keithley_stop_event = threading.Event()
    a.keithley_executor = Now()
    a.post_ui = lambda callback: callback()
    # A finish's dialog opens from root.after(0, ...) (M5, review,
    # 2026-10-18); here at once.
    a.closing, a.root = True, types.SimpleNamespace(after=lambda _delay, callback: callback())
    a.instrument_connection_indicators = {}
    a.active_file_exports = {}
    a.keithley_step_rows = []
    a.keithley_roi, a.camera_window = None, None
    a.keithley_roi_edit_buttons = a.keithley_manual_buttons = ()
    for name in ("keithley_compliance_entry", "keithley_model_combo", "keithley_connect_button",
                 "keithley_disconnect_button", "keithley_start_button", "keithley_manual_zero_button",
                 "keithley_stop_button", "keithley_off_button", "keithley_save_button",
                 "keithley_save_images_check"):
        setattr(a, name, mock.Mock())
    for name in ("_update_magnet_scan_controls", "_update_moke_roi_controls", "_update_nonlocal_vi_controls"):
        setattr(a, name, mock.Mock())
    return a


def entry_state(a):
    return a.keithley_compliance_entry.configure.call_args.kwargs["state"]


def fake_connect(controller):
    """What Keithley2410Controller.connect keeps: the instrument and the
    limit it was given (the limit sent as :SENS:CURR:PROT)."""
    def connect(resource, compliance_a, model):
        controller.instrument = types.SimpleNamespace()
        controller.compliance_a = compliance_a
        return "KEITHLEY INSTRUMENTS INC.,MODEL 2410,1,C30"
    return connect


class LockTests(unittest.TestCase):
    def test_the_field_is_locked_from_connect_to_disconnect(self):
        a = page()
        a._update_keithley_controls()
        self.assertEqual(entry_state(a), "normal", "editable before Connect")
        connect = fake_connect(a.keithley_controller)

        def connecting(*args):
            # While the connect runs, the field is already locked.
            self.assertTrue(a.keithley_connecting)
            self.assertEqual(entry_state(a), "disabled")
            return connect(*args)

        with mock.patch.object(a.keithley_controller, "connect", side_effect=connecting):
            a.connect_keithley()
        self.assertEqual(a.keithley_controller.compliance_a, 0.05)
        self.assertEqual(entry_state(a), "disabled", "a connected limit cannot be edited")

        # Disconnect: the field is free again, and the next Connect uses it.
        a.keithley_controller.instrument = None
        a._finish_keithley_disconnect(None)
        self.assertEqual(entry_state(a), "normal")
        a.keithley_compliance_var.set("0.01")
        with mock.patch.object(a.keithley_controller, "connect", side_effect=connect):
            a.connect_keithley()
        self.assertEqual(a.keithley_controller.compliance_a, 0.01)
        self.assertEqual(entry_state(a), "disabled")

    def test_a_scan_or_a_manual_write_locks_it_too(self):
        for flag in ("keithley_scan_running", "keithley_manual_busy", "keithley_connecting"):
            with self.subTest(flag):
                a = page()
                setattr(a, flag, True)
                a._update_keithley_controls()
                self.assertEqual(entry_state(a), "disabled")

    def test_a_failed_connect_leaves_it_editable(self):
        a = page()
        with mock.patch.object(a.keithley_controller, "connect", side_effect=RuntimeError("timeout")), \
                mock.patch.object(app.messagebox, "showerror"):
            a.connect_keithley()
        self.assertFalse(a.keithley_controller.connected)
        self.assertEqual(entry_state(a), "normal")


class MessageTests(unittest.TestCase):
    """The 2410 refuses more than 21 mA above 20 V; the refusal says how to
    lower the limit, since the field no longer accepts an edit."""

    def controller(self):
        controller = app.Keithley2410Controller(lambda _message: None)
        controller.compliance_a = 0.05
        return controller

    def test_a_scan_above_20_v(self):
        with self.assertRaises(ValueError) as caught:
            self.controller().enable_output(25.0)
        text = str(caught.exception)
        self.assertIn("at most 21 mA; current setting 0.05 A", text)
        self.assertIn("Disconnect, then Connect with a lower Current limit", text)

    def test_a_manual_write_above_20_v(self):
        with self.assertRaises(ValueError) as caught:
            self.controller().apply_manual_voltage(25.0)
        text = str(caught.exception)
        self.assertIn("current setting 0.05 A", text)
        self.assertIn("Disconnect, then Connect with a lower Current limit", text)


if __name__ == "__main__":
    unittest.main()
