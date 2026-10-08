"""Non-local page: the fixes from the whole-module review (2026-10-08).

No Tk and no instrument: stand-in controllers and variables record what the
page asks of them.
"""
import queue
import threading
import time
import unittest
from concurrent.futures import Future
from unittest import mock

from test_nonlocal_keithley import FakeSourceMeter, Var, bare_app, connected_controller
from test_ppms_motor_debug import app


class Executor:
    """Runs each job at once, in order, as the page's single worker would."""

    def __init__(self):
        self.jobs = []

    def submit(self, function, *args):
        self.jobs.append(function)
        future = Future()
        try:
            future.set_result(function(*args))
        except BaseException as error:
            future.set_exception(error)
        return future


def page():
    a = bare_app()
    a.root, a.closing, a.nonlocal_vi_closed = None, True, False
    a.nonlocal_vi_running = a.nonlocal_vi_busy = False
    a.nonlocal_keithley_task = None
    a.nonlocal_vi_executor = Executor()
    a.post_ui = lambda callback: callback()
    a.nonlocal_vi_stop_event = threading.Event()
    a.nonlocal_vi_interlock = app.NonLocalVIInterlock()
    a.nonlocal_vi_status_var, a.nonlocal_vi_output_var = Var(), Var()
    a.nonlocal_iv6221_source_var = Var("6221 ON: 0.1 mA RMS / 17.3 Hz")
    a._update_nonlocal_vi_controls = mock.Mock()
    return a


class StopAndOffTests(unittest.TestCase):
    def setUp(self):
        self.a = a = page()
        a.nonlocal_vi_ac_controller = mock.Mock(source_connected=True, any_connected=True)
        a.nonlocal_vi_2410_controller = mock.Mock(connected=True, model="2400")

    def test_the_red_stop_switches_off_even_while_an_instrument_connects(self):
        a = self.a
        a.nonlocal_vi_busy = True   # e.g. Connect on the Zurich, after Set 6221
        a.stop_nonlocal_vi()
        self.assertEqual(len(a.nonlocal_vi_executor.jobs), 1, "the shutdown is queued")
        a.nonlocal_vi_ac_controller.output_off.assert_called_once()
        a.nonlocal_vi_ac_controller.confirm_output_off.assert_called_once()
        a.nonlocal_vi_2410_controller.confirm_output_off.assert_called_once()
        self.assertEqual(a.nonlocal_vi_output_var.get(), "Keithley 2400 and 6221 outputs: OFF")
        self.assertEqual(a.nonlocal_iv6221_source_var.get(), "6221: OFF")
        self.assertIn("(read back)", a.nonlocal_vi_status_var.get())
        self.assertFalse(a.nonlocal_vi_busy)

    def test_off_is_claimed_only_for_an_output_read_back(self):
        a = self.a
        a.nonlocal_vi_ac_controller.source_connected = False   # e.g. just disconnected
        a.nonlocal_iv6221_source_var.set("6221: check the output")
        a.stop_nonlocal_vi()
        # A failed disconnect left "check the output": the warning stays.
        self.assertEqual(a.nonlocal_vi_output_var.get(), "Keithley 2400 output: OFF; 6221: check it")
        self.assertEqual(a.nonlocal_iv6221_source_var.get(), "6221: check the output",
                         "nothing was read from the 6221")
        self.assertIn("6221: check it", a.nonlocal_vi_status_var.get())
        # Disconnected without error: simply not connected.
        a.nonlocal_iv6221_source_var.set("6221: disconnected (OFF sent)")
        a.nonlocal_vi_output_var.set("6221: disconnected (OFF sent)")
        a.stop_nonlocal_vi()
        self.assertEqual(a.nonlocal_vi_output_var.get(), "Keithley 2400 output: OFF")
        self.assertIn("the other is not connected", a.nonlocal_vi_status_var.get())

    def test_two_presses_keep_the_page_busy_until_both_ran(self):
        a = self.a
        a.nonlocal_vi_executor.submit = lambda function, *args: Future()   # queued, not run yet
        a.stop_nonlocal_vi()
        a.stop_nonlocal_vi()
        a._finish_nonlocal_vi_off(None, ("6221", "keithley"))
        self.assertTrue(a.nonlocal_vi_busy, "the second OFF is still queued")
        a._finish_nonlocal_vi_off(None, ("6221", "keithley"))
        self.assertFalse(a.nonlocal_vi_busy)

    def test_a_connect_finishing_first_keeps_the_page_busy_until_the_off_ran(self):
        a = self.a
        a.nonlocal_vi_off_pending = True
        a.nonlocal_vi_busy = True
        for name in ("_set_mfli_lock_state", "_refresh_nonlocal_vi_connection_summary",
                     "_redraw_nonlocal_vi_plots", "_update_k6221_controls", "_update_keithley_controls"):
            setattr(a, name, mock.Mock())
        a.nonlocal_vi_measurement_enabled_vars = {}
        for name in ("nonlocal_resistance_safety_var", "nonlocal_iv_safety_var",
                     "nonlocal_iv6221_safety_var", "nonlocal_vi_safety_var"):
            setattr(a, name, Var(True))
        future = Future()
        future.set_result("KEITHLEY 2182A")
        a._finish_nonlocal_vi_device_connect("2182", "Keithley 2182A", future)
        self.assertTrue(a.nonlocal_vi_busy)
        a._finish_nonlocal_vi_off(None, ("6221", "keithley"))
        self.assertFalse(a.nonlocal_vi_busy)

    def test_off_is_not_claimed_when_switching_off_failed(self):
        a = self.a
        a.nonlocal_vi_ac_controller.confirm_output_off.side_effect = RuntimeError("6221 output is still ON")
        a.stop_nonlocal_vi()
        self.assertEqual(a.nonlocal_vi_output_var.get(), "Keithley 2400 and 6221 outputs: check them")
        self.assertEqual(a.nonlocal_iv6221_source_var.get(), "6221: check the output")
        self.assertIn("6221 output is still ON", a.nonlocal_vi_status_var.get())

    def test_disconnecting_the_keithley_says_nothing_about_the_6221(self):
        a = self.a
        for name in ("nonlocal_resistance_safety_var", "nonlocal_iv_safety_var",
                     "nonlocal_iv6221_safety_var", "nonlocal_vi_safety_var"):
            setattr(a, name, Var(True))
        a.nonlocal_vi_measurement_enabled_vars = {}
        for name in ("_refresh_nonlocal_vi_connection_summary", "_redraw_nonlocal_vi_plots",
                     "_update_k6221_controls", "_update_keithley_controls"):
            setattr(a, name, mock.Mock())
        a._finish_nonlocal_vi_device_disconnect("2410", "Keithley 2400", None)
        self.assertEqual(a.nonlocal_vi_output_var.get(), "Keithley 2400: disconnected (OFF sent)")
        self.assertEqual(a.nonlocal_iv6221_source_var.get(), "6221 ON: 0.1 mA RMS / 17.3 Hz",
                         "the 6221 may still be on")
        a._finish_nonlocal_vi_device_disconnect("6221", "Keithley 6221", RuntimeError("timeout"))
        self.assertEqual(a.nonlocal_vi_output_var.get(), "6221 output: check it")
        self.assertEqual(a.nonlocal_iv6221_source_var.get(), "6221: check the output")


class GateVIFinishTests(unittest.TestCase):
    def finish(self, error, shutdown_error):
        a = page()
        a.nonlocal_vi_2410_controller = mock.Mock(model="2400")
        a.nonlocal_vi_eta_var = Var()
        a.nonlocal_vi_run_directory = None
        a.nonlocal_vi_shutdown_error = shutdown_error
        a._set_mfli_lock_state = mock.Mock()
        for name in ("_redraw_nonlocal_vi_plots", "_update_k6221_controls", "_update_keithley_controls"):
            setattr(a, name, mock.Mock())
        a._finish_nonlocal_vi_scan(error)
        return a

    def test_a_scan_error_and_a_failed_shutdown_are_both_reported(self):
        a = self.finish(RuntimeError("SR830 timeout"), RuntimeError("6221 output is still ON"))
        status = a.nonlocal_vi_status_var.get()
        self.assertIn("VI failed: SR830 timeout", status)
        self.assertIn("switching the outputs off failed, check them: 6221 output is still ON", status)
        self.assertEqual(a.nonlocal_vi_output_var.get(), "Keithley 2400 and 6221 outputs: check them")

    def test_a_clean_end_says_off_read_back(self):
        a = self.finish(RuntimeError("SR830 timeout"), None)
        self.assertIn("all outputs off (read back)", a.nonlocal_vi_status_var.get())
        self.assertEqual(a.nonlocal_vi_output_var.get(), "Keithley 2400 and 6221 outputs: OFF")


class KeithleyTaskTests(unittest.TestCase):
    def setUp(self):
        patcher = mock.patch.object(app.time, "sleep")
        patcher.start()
        self.addCleanup(patcher.stop)
        self.a = a = page()
        self.meter = FakeSourceMeter()
        a.nonlocal_vi_2410_controller = connected_controller(self.meter)
        a.nonlocal_vi_ac_controller = mock.Mock(source_connected=False)
        a.nonlocal_keithley_stop_event = threading.Event()
        a.nonlocal_keithley_output_off = False
        a.nonlocal_keithley_untouched = True

    def test_a_failed_restore_keeps_the_measurement_error(self):
        controller = self.a.nonlocal_vi_2410_controller
        controller.restore_voltage_mode = mock.Mock(side_effect=RuntimeError("VI_ERROR_TMO on SYST:ERR?"))

        def body(_controller):
            raise RuntimeError("READ? timed out")

        with self.assertRaisesRegex(
                RuntimeError, "READ\\? timed out; then switching back to the pulse setup failed: VI_ERROR_TMO"):
            self.a._run_on_nonlocal_keithley(app.NonLocalVIInterlock.VOLTAGE, body)

    def test_a_stop_before_the_start_touches_nothing(self):
        a = self.a
        a.nonlocal_keithley_stop_event.set()
        with self.assertRaises(InterruptedError):
            a._run_on_nonlocal_keithley(app.NonLocalVIInterlock.VOLTAGE, lambda c: None)
        self.assertTrue(a.nonlocal_keithley_untouched)
        self.assertEqual(self.meter.commands, [])

    def test_the_current_limit_is_read_back(self):
        controller = self.a.nonlocal_vi_2410_controller
        self.assertAlmostEqual(controller.start_iv_sweep(1e-6), 1e-6)
        self.meter.query = (lambda original: lambda command: (
            "2e-6" if command.upper() == ":SENS:CURR:PROT?" else original(command)))(self.meter.query)
        with self.assertRaisesRegex(RuntimeError, "current limit reads 2e-06 A, not the 1e-06 A"):
            controller.start_iv_sweep(1e-6)


class PulseTests(unittest.TestCase):
    def test_the_level_is_checked_after_the_pulse_not_inside_it(self):
        meter = FakeSourceMeter()
        controller = connected_controller(meter)
        events = []
        original_query, original_write = meter.query, meter.write
        meter.query = lambda command: (events.append(("query", command)), original_query(command))[1]
        meter.write = lambda command: (events.append(("write", command)), original_write(command))[1]
        controller._wait = lambda duration, stop: events.append(("wait", duration))
        with mock.patch.object(app.time, "sleep", lambda seconds: None):
            controller.apply_voltage_pulse(5.0, 0.1, threading.Event())
        level_at = events.index(("write", ":SOUR:VOLT 5"))
        self.assertEqual(events[level_at + 1], ("wait", 0.1),
                         "the full width, with no error check between the level and the wait")
        self.assertEqual(meter.commands[-2:], [":SOUR:VOLT 0", ":OUTP OFF"])
        self.assertIn(("query", "SYST:ERR?"), events[level_at + 2:], "the level is checked after the pulse")

    def test_a_rejected_level_fails_after_the_output_is_off(self):
        meter = FakeSourceMeter()
        controller = connected_controller(meter)
        original_query = meter.query
        state = {"pulsed": False}

        def query(command):
            if command == "SYST:ERR?" and state["pulsed"] and meter.output == 0:
                return '-222,"Data out of range"'
            return original_query(command)

        meter.query = query
        controller._wait = lambda duration, stop: state.update(pulsed=True)
        with mock.patch.object(app.time, "sleep", lambda seconds: None), \
                self.assertRaisesRegex(RuntimeError, "Data out of range"):
            controller.apply_voltage_pulse(5.0, 0.1, threading.Event())
        self.assertEqual(meter.output, 0)

    def test_a_stop_before_the_pulse_never_switches_the_output_on(self):
        meter = FakeSourceMeter()
        controller = connected_controller(meter)
        stop = threading.Event()
        stop.set()
        with mock.patch.object(app.time, "sleep", lambda seconds: None):
            with self.assertRaises(app.NonLocalVIStopped):
                controller.apply_voltage_pulse(5.0, 0.1, stop)
            with self.assertRaises(app.NonLocalVIStopped):
                controller.measure_resistance(stop)
        self.assertNotIn(":OUTP ON", meter.commands)


class ZurichControllerTests(unittest.TestCase):
    def controller(self):
        controller = app.NonLocalACController(lambda _m: None)
        return controller

    def test_a_connected_zurich_the_scan_did_not_set_up_is_not_waited_for(self):
        controller = self.controller()
        controller.mfli_config = {"harmonic": 1}
        controller._wait_mfli_reference = mock.Mock(return_value={"valid": True})
        with mock.patch.object(type(controller), "mfli_connected", new_callable=mock.PropertyMock,
                               return_value=True):
            self.assertNotIn("mfli", controller.wait_references(17.3, threading.Event(), ()))
            controller.mfli_in_scan = True
            self.assertIn("mfli", controller.wait_references(17.3, threading.Event(), ()))

    def test_a_stop_during_the_reference_wait_is_a_stop_not_a_key_error(self):
        controller = self.controller()
        controller.mfli_config = {"reference_source": "Trigger In 1"}
        stop = threading.Event()
        stop.set()
        result = controller._wait_mfli_reference(17.3, stop)
        self.assertFalse(result["valid"])
        self.assertEqual(result["status"], 8)

    def test_unused_demodulators_follow_the_reference_routing(self):
        controller = self.controller()
        controller.mfli_bridge = mock.Mock()
        controller.mfli_config = {"extref_demodulator": 3}
        for mode, indices in (("1", [1, 2]), ("1+2", [2]), ("1+2+3", None)):
            controller.mfli_bridge.reset_mock()
            controller.disable_unused_ppms_demods(mode)
            if indices is None:
                controller.mfli_bridge.request.assert_not_called()
            else:
                controller.mfli_bridge.request.assert_called_once_with("disable_unused_demods", indices=indices)
        controller.mfli_config = {"extref_demodulator": 1}
        controller.mfli_bridge.reset_mock()
        controller.disable_unused_ppms_demods("1+2")
        controller.mfli_bridge.request.assert_called_once_with("disable_unused_demods", indices=[3])


class SR830Tests(unittest.TestCase):
    class Lockin:
        def __init__(self, esr=("0",)):
            self.commands, self.esr, self.lias = [], list(esr), []

        def write(self, command):
            self.commands.append(command)

        def query(self, command):
            if command == "*ESR?":
                return self.esr.pop(0) if self.esr else "0"
            if command == "SENS?":
                return "10"
            if command == "LIAS?":
                self.lias.append(command)
                return "1" if len(self.lias) == 1 else "0"
            if command.startswith("SNAP?"):
                return "1e-6,0,1e-6,0,17.3"
            raise AssertionError(command)

        def clear(self):
            self.commands.append("<clear>")

    def controller(self, lockin):
        controller = app.NonLocalACController(lambda _m: None)
        controller.lockins = {"8": lockin}
        controller.sensitivity_indices = {"8": 9}
        controller.low_range_counts = {"8": 0}
        controller.range_cooldowns = {"8": 0}
        return controller

    def test_an_unreadable_status_sends_the_setting_again(self):
        lockin = self.Lockin(esr=("garbled", "0"))
        controller = self.controller(lockin)
        with mock.patch.object(app.time, "sleep", lambda s: None):
            controller._write_sr830("8", "HARM 2")
        self.assertEqual(lockin.commands, ["HARM 2", "<clear>", "HARM 2"])
        lockin = self.Lockin(esr=("garbled", "garbled"))
        controller = self.controller(lockin)
        with mock.patch.object(app.time, "sleep", lambda s: None), \
                self.assertRaisesRegex(RuntimeError, "status after 'HARM 2' is unreadable"):
            controller._write_sr830("8", "HARM 2")

    def test_the_overload_latched_by_a_range_change_is_read_once_and_dropped(self):
        lockin = self.Lockin()
        controller = self.controller(lockin)
        controller.lias_clear_pending["8"] = True
        with mock.patch.object(app.time, "sleep", lambda s: None):
            sample = controller._read_sample_one("8", 17.3, True, 0)
        self.assertEqual(len(lockin.lias), 2, "one discarded LIAS? read")
        self.assertEqual(sample["lia_status"], 0, "the latched overload is not counted")


class BridgeTests(unittest.TestCase):
    def test_a_late_reply_is_not_taken_for_the_next_request(self):
        bridge = app.LegacyMFLIBridge.__new__(app.LegacyMFLIBridge)
        bridge.responses = queue.Queue()
        bridge.diagnostics = []
        bridge.process = None
        bridge.responses.put({"ok": True, "id": 4, "autorange": {}})   # late, timed out earlier
        bridge.responses.put({"ok": True, "id": 5, "sample": {"x": 1}})
        reply = bridge._receive(1.0, 5)
        self.assertEqual(reply["sample"], {"x": 1})
        self.assertTrue(any("request 4" in line for line in bridge.diagnostics))


class ZurichRowTests(unittest.TestCase):
    def test_the_ref_follows_another_zurich_only_while_none_is_connected(self):
        a = bare_app()
        a.nonlocal_vi_ac_controller = mock.Mock(mfli_connected=True)
        a.nonlocal_vi_mfli_reference_source_var = Var("Trigger In 1")
        a.nonlocal_mfli_device_seen = "dev4414"
        a.nonlocal_vi_mfli_device_var = Var("DEV4435 (Normal)")
        a._on_nonlocal_mfli_device_changed()
        self.assertEqual(a.nonlocal_vi_mfli_reference_source_var.get(), "Trigger In 1",
                         "the connected DEV4414 keeps its Ref.")
        a.nonlocal_vi_ac_controller.mfli_connected = False
        a.nonlocal_vi_mfli_reference_source_var.set("Aux Input 2")   # set by hand
        a.nonlocal_vi_mfli_device_var.set("DEV4435 (Normal)")
        a._on_nonlocal_mfli_device_changed()
        self.assertEqual(a.nonlocal_vi_mfli_reference_source_var.get(), "Aux Input 2",
                         "choosing the same Zurich again keeps a Ref. set by hand")

    def test_the_zurich_controls_are_held_while_a_measurement_runs(self):
        a = page()
        a.closing = False
        a.nonlocal_vi_ac_controller = mock.Mock(mfli_connected=True)
        widgets = {name: mock.Mock() for name in ("chooser", "ref", "tc", "harmonic", "measure")}
        a.nonlocal_vi_mfli_chooser = widgets["chooser"]
        a.nonlocal_mfli_reference_combo = widgets["ref"]
        a.nonlocal_mfli_tc_entry = widgets["tc"]
        a.nonlocal_iv6221_choice_widgets = [(widgets["harmonic"], "readonly"), (widgets["measure"], "normal")]
        a._update_nonlocal_mfli_controls()
        states = {name: w.configure.call_args[1]["state"] for name, w in widgets.items()}
        self.assertEqual(states, {"chooser": "disabled", "ref": "readonly", "tc": "normal",
                                  "harmonic": "readonly", "measure": "normal"})
        a.nonlocal_keithley_task = "iv6221"
        a._update_nonlocal_mfli_controls()
        self.assertEqual({w.configure.call_args[1]["state"] for w in widgets.values()}, {"disabled"})


if __name__ == "__main__":
    unittest.main()
