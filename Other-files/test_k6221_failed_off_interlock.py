"""CIDWM (review, 2026-10-13): when switching the 6221 off fails after Apply
±I pulse, at the end of a 6221 pulse sign scan or while disconnecting, the
6221's output is unknown, so the Avtech does not pulse until an off
succeeds.  Before, only the red button's off set that interlock, and Apply
±I and Disconnect even said "0 mA / OFF".  The dialog keeps why the pulse
failed (a command that timed out, say) as well as the failed off.

No Tk and no instrument: the real 6221 controller, worker and finish
methods with a stand-in 6221 whose OUTP OFF can time out."""
import concurrent.futures
import tempfile
import threading
import unittest
from pathlib import Path
from unittest import mock

from test_cidwm_pulse_polarity import Instrument
from test_nonlocal_keithley import Var, bare_app
from test_ppms_motor_debug import app


class Stuck6221(Instrument):
    """A 6221 whose OUTP OFF times out once ``fail_off`` is set; ``fail_on``
    names another command that times out; ``wrong`` gives a frequency
    readback that does not match.  OUTP? reads what the OUTP commands that
    went through left (since 2026-10-18 a scan's end reads it back, and the
    readback decides); a 6221 whose OUTP OFF times out was left on."""

    def __init__(self, fail_off=True, wrong=False, fail_on=None):
        super().__init__()
        self.fail_off, self.wrong, self.fail_on = fail_off, wrong, fail_on
        self.output = "1" if fail_off else "0"

    def write(self, command):
        if (self.fail_off and command == "OUTP OFF") or command == self.fail_on:
            raise TimeoutError(f"VI_ERROR_TMO on {command}")
        super().write(command)
        if command in ("OUTP ON", "OUTP OFF"):
            self.output = "1" if command == "OUTP ON" else "0"

    def query(self, command):
        if self.wrong and command == "SOUR:WAVE:FREQ?":
            return "1"
        if command == "OUTP?":
            return self.output
        return super().query(command)

    def close(self):
        pass


class Now:
    """An executor that runs the job at once."""

    def submit(self, function, *args):
        future = concurrent.futures.Future()
        try:
            future.set_result(function(*args))
        except BaseException as error:
            future.set_exception(error)
        return future


def page(instrument):
    a = bare_app()
    a.k6221_controller = app.Keithley6221Controller(lambda _message: None)
    a.k6221_controller.instrument = instrument
    a.k6221_safety_var = Var(True)
    a.k6221_current_var, a.k6221_width_var = Var("2"), Var("50")
    a.k6221_compliance_var, a.k6221_count_var = Var("100"), Var("1")
    a.k6221_stop_event = threading.Event()
    a.k6221_output_var, a.k6221_status_var, a.k6221_connection_var = Var(), Var(), Var()
    a.avtech_output_var = Var()
    a.k6221_busy, a.k6221_scan_running, a.k6221_output_unknown = False, False, False
    a.k6221_executor = Now()
    a.post_ui = lambda callback: callback()
    a.closing, a.root = True, None
    for name in ("_update_k6221_controls", "_update_nonlocal_vi_controls", "_update_keithley_controls",
                 "_update_magnet_scan_controls", "_reset_k6221_readback", "_publish_k6221_readback",
                 "thread_log"):
        setattr(a, name, mock.Mock())
    # The Avtech, ready to pulse.
    a.avtech_controller = mock.Mock(connected=True)
    # No safety tick and no status line since 2026-10-20 (user): the
    # Pulse count box and the train's stop event instead.
    a.avtech_count_var = Var("1")
    a.avtech_stop_event = threading.Event()
    a.avtech_busy, a.avtech_output_unknown = False, False
    a.avtech_executor = mock.Mock()
    a._parse_avtech_pulse = lambda: (1.0, 10e-9)
    return a


def avtech_pulsed(a):
    """Press Apply +V pulse; True when the Avtech pulse was submitted."""
    with mock.patch.object(app.messagebox, "showwarning") as warning:
        app.CombinedControlApp.apply_avtech_pulse(a, 1)
    if a.avtech_executor.submit.called:
        return True
    warning.assert_called_once()
    text = warning.call_args.args[1]
    assert "output state is unknown" in text and "reconnect" in text, text
    return False


class ApplyTests(unittest.TestCase):
    def test_a_failed_off_after_the_train_keeps_the_avtech_waiting(self):
        a = page(Stuck6221())
        a.apply_k6221_output(1)
        self.assertTrue(a.k6221_output_unknown)
        self.assertEqual(a.k6221_output_var.get(), "Output: check the 6221", "not 0 mA / OFF")
        self.assertIn("failed to turn output off", a.k6221_status_var.get())
        self.assertFalse(avtech_pulsed(a))

    def test_a_failed_command_then_a_failed_off_keeps_both_errors(self):
        a = page(Stuck6221(fail_on="SOUR:WAVE:INIT"))
        a.apply_k6221_output(1)
        self.assertTrue(a.k6221_output_unknown)
        status = a.k6221_status_var.get()
        self.assertIn("VI_ERROR_TMO on SOUR:WAVE:INIT", status, "why the pulse failed")
        self.assertIn("then 6221 failed to turn output off", status)
        self.assertEqual(a.k6221_output_var.get(), "Output: check the 6221")
        self.assertFalse(avtech_pulsed(a))

    def test_a_readback_mismatch_then_a_failed_off_keeps_the_avtech_waiting(self):
        a = page(Stuck6221(wrong=True))
        a.apply_k6221_output(1)
        self.assertTrue(a.k6221_output_unknown)
        self.assertIn("failed to turn output off", a.k6221_status_var.get())
        self.assertFalse(avtech_pulsed(a))

    def test_an_off_that_works_frees_the_avtech(self):
        for failure in ({}, {"wrong": True}, {"fail_on": "SOUR:WAVE:INIT"}):
            with self.subTest(**failure):
                a = page(Stuck6221(fail_off=False, **failure))
                a.k6221_output_unknown = True   # an earlier off had failed
                a.apply_k6221_output(1)
                self.assertFalse(a.k6221_output_unknown)
                self.assertTrue(a.k6221_output_var.get().endswith("OFF"), a.k6221_output_var.get())
                self.assertTrue(avtech_pulsed(a))

    def test_zero_switched_off_frees_the_avtech(self):
        a = page(Stuck6221(fail_off=False))
        a.k6221_current_var = Var("0")
        a.apply_k6221_output(1)
        self.assertFalse(a.k6221_output_unknown)
        self.assertEqual(a.k6221_output_var.get(), "Output: 0 mA / OFF")

    def test_zero_with_a_failed_off_keeps_the_avtech_waiting(self):
        a = page(Stuck6221())
        a.k6221_current_var = Var("0")
        a.apply_k6221_output(1)
        self.assertTrue(a.k6221_output_unknown)
        self.assertEqual(a.k6221_output_var.get(), "Output: check the 6221")
        self.assertFalse(avtech_pulsed(a))


class ScanTests(unittest.TestCase):
    def finish(self, instrument):
        a = page(instrument)
        a.cidwm_scan_source = "6221"
        a.cidwm_scan_status_var = Var()
        a.k6221_scan_running = True
        a.k6221_stop_event.set()   # stopped before the first pulse; the off at the end still runs
        # The run folder through the shared recorder (user, 2026-10-18).
        recorder = app.ScanRunRecorder(Path(self.enterContext(tempfile.TemporaryDirectory())), "CIDWM",
                                       "run", {})
        a.cidwm_scan_recorder = recorder
        future = a.k6221_executor.submit(a._cidwm_pulse_scan_worker, {"signs": (1,)}, recorder)
        a._finish_cidwm_pulse_scan(future)
        self.assertTrue(recorder.closed, "run_finished written by the finish")
        return a

    def test_a_failed_off_at_the_end_keeps_the_avtech_waiting(self):
        a = self.finish(Stuck6221())
        self.assertTrue(a.k6221_output_unknown)
        self.assertEqual(a.k6221_output_var.get(), "Output: check the 6221")
        self.assertFalse(avtech_pulsed(a))

    def test_an_off_that_works_frees_the_avtech(self):
        a = self.finish(Stuck6221(fail_off=False))
        self.assertFalse(a.k6221_output_unknown)
        self.assertTrue(avtech_pulsed(a))


class DisconnectTests(unittest.TestCase):
    def disconnect(self, instrument):
        a = page(instrument)
        a.k6221_safety_var = mock.Mock()
        with mock.patch.object(app, "release_shared_visa_manager"), \
                mock.patch.object(app, "release_instrument_lease"):
            a.disconnect_k6221()
        self.assertFalse(a.k6221_controller.connected)
        return a

    def test_a_failed_off_keeps_the_avtech_waiting(self):
        a = self.disconnect(Stuck6221())
        self.assertTrue(a.k6221_output_unknown)
        self.assertEqual(a.k6221_output_var.get(), "Output: check the 6221")
        self.assertIn("Disconnect warning", a.k6221_status_var.get())
        self.assertFalse(avtech_pulsed(a))

    def test_an_off_that_works_frees_the_avtech(self):
        a = self.disconnect(Stuck6221(fail_off=False))
        self.assertFalse(a.k6221_output_unknown)
        self.assertEqual(a.k6221_output_var.get(), "Output: 0 mA / OFF")
        self.assertTrue(avtech_pulsed(a))



class ScanWidthComplianceTests(unittest.TestCase):
    """Review, 2026-10-13: the scan's width and compliance parse checked the
    compliance against the controller's last read-back width, so a width
    stored just under 5 us refused a valid box width."""

    def page(self, width_us, compliance_v):
        a = bare_app()
        a.k6221_controller = app.Keithley6221Controller(lambda _message: None)
        a.k6221_width_var, a.k6221_compliance_var = Var(width_us), Var(compliance_v)
        return a

    def test_the_box_width_is_checked_not_the_last_read_back(self):
        a = self.page("10", "20")
        a.k6221_controller.pulse_width_s = 4.99995e-6
        self.assertEqual(a._parse_k6221_width_compliance(), (10e-6, 20.0))

    def test_bad_boxes_still_refused(self):
        for width, compliance, message in (("10", "x", "compliance must be a number"),
                                           ("x", "20", "pulse width must be a number"),
                                           ("1", "20", "pulse width")):
            with self.subTest(width=width, compliance=compliance):
                with self.assertRaises(ValueError) as raised:
                    self.page(width, compliance)._parse_k6221_width_compliance()
                self.assertIn(message, str(raised.exception))


if __name__ == "__main__":
    unittest.main()
