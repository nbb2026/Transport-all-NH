"""Review of the merged areas (c3b4b24..1c51161): its fixes (review, 2026-10-18).

ST-FMR / FMR SF1-SF6, PPMS / SHV PS1-PS8, MOKE / CIDWM / Non-local MN1-MN4
and the nits.  No Tk window and no instrument: the real methods run on
stand-ins (the shared STFMR, PPMS and SHV harnesses of the other suites),
every dialog is mocked, and the real Newmark driver only ever sees the fake
PerformaxCom.dll of test_rte_angle_scan."""
from concurrent.futures import Future
import csv
import inspect
import json
import re
import tempfile
import threading
import types
import unittest
from pathlib import Path
from unittest import mock

import test_ppms_run_save_zero as ppms_run
import test_rte_panel as rte_tests
import test_stfmr_motor_angle as motor_tests
import test_stfmr_power_sweep as power_tests
from test_ppms_motor_debug import app
from test_ppms_two_panels import ImmediateExecutor, Var
from test_stfmr_realtime_recording import events, harness

App = app.CombinedControlApp


def setUpModule():
    # No real DLL: the guards of the Newmark stage's suite.
    motor_tests.setUpModule()


def tearDownModule():
    motor_tests.tearDownModule()


def read_events(path):
    return [json.loads(line) for line in Path(path).read_text(encoding="utf-8").splitlines()]


class Kepco:
    """Another page's 4886 controller, as the stage check sees it."""

    def __init__(self, on=False, connected=False):
        self.output_owned, self.connected = on, connected


# ---- SF1: the stage never turns with any page's 4886 on ---------------------------------------
class StageKepcoTests(motor_tests.MotorBase):
    def hand(self, target="15", **options):
        run = self.make(**options)
        run.h.stfmr_lockin_stage_target_var.set(target)
        return run

    def test_a_hand_move_needs_this_pages_4886_connected(self):
        run = self.hand()
        run.h.stfmr_lockin_controllers["kepco"].connected = False
        run.h.move_stfmr_lockin_stage()
        self.error.assert_called_once()
        self.assertEqual(self.error.call_args.args[1],
                         "Connect the 4886 on this page and zero it (Zero / OFF) first: the stage must not "
                         "turn with the magnet on")
        self.assertEqual(run.sim.moves, [], "the stage did not turn")

    def test_a_hand_move_is_refused_while_the_shv_or_fmr_4886_is_on(self):
        for page in ("SHV", "FMR"):
            with self.subTest(page=page):
                self.error.reset_mock()
                run = self.hand()
                h = run.h
                if page == "SHV":
                    h.rte_panel = types.SimpleNamespace(rte_controllers={"kepco": Kepco(on=True)})
                else:
                    h.stfmr_controllers = {"kepco": Kepco(on=True)}
                h.move_stfmr_lockin_stage()
                self.error.assert_called_once()
                self.assertEqual(self.error.call_args.args[1],
                                 f"The KEPCO 4886 output of the {page} page is on (or not confirmed off): zero it "
                                 "there (Zero / OFF) first: the stage must not turn with the magnet on")
                self.assertEqual(run.sim.moves, [])

    def test_the_clr_and_eo_checks_refuse_too(self):
        run = self.hand()
        run.h.stfmr_controllers = {"kepco": Kepco(on=True)}
        with self.assertRaisesRegex(RuntimeError, "of the FMR page is on"):
            run.h._stfmr_lockin_require_stage_fix_ready()
        run.h.stfmr_controllers = {"kepco": Kepco(on=False)}
        run.h.stfmr_lockin_controllers["kepco"].connected = False
        with self.assertRaisesRegex(RuntimeError, "^Connect the 4886 on this page"):
            run.h._stfmr_lockin_require_stage_fix_ready()

    def test_with_every_4886_off_the_move_runs(self):
        run = self.hand("15")
        run.h.stfmr_controllers = {"kepco": Kepco(on=False, connected=True)}
        run.h.rte_panel = types.SimpleNamespace(rte_controllers={"kepco": Kepco(on=False)})
        run.h.move_stfmr_lockin_stage()
        run.h.root.drain()
        self.error.assert_not_called()
        self.assertEqual(run.sim.moves, ["X150000"])

    def test_a_sweep_refuses_its_first_move_while_another_4886_is_on(self):
        run = self.make(angles=("30", "60", "30"))
        h = run.h
        h.stfmr_controllers = {"kepco": Kepco(on=True)}
        h.start_stfmr_lockin_scan()
        h.root.drain()
        self.error.assert_called_once()
        self.assertIn("of the FMR page is on", self.error.call_args.args[1])
        self.assertEqual(run.sim.moves, [], "the stage did not turn")
        self.assertFalse([e for e in h.log if e[:2] == ("kepco", "output")], "nothing switched on")

    def test_a_sweep_refuses_a_later_move_while_another_4886_is_on(self):
        run = self.make(angles=("30", "60", "30"))
        h = run.h
        shv = Kepco(on=False)
        h.rte_panel = types.SimpleNamespace(rte_controllers={"kepco": shv})

        def switch_on_elsewhere():
            shv.output_owned = True        # SHV's KEPCO switched on during the first angle
        h.move_hooks[30.0] = switch_on_elsewhere
        h.start_stfmr_lockin_scan()
        h.root.drain()
        self.assertEqual(run.sim.moves, ["X300000"], "the first move only")
        self.error.assert_called_once()
        self.assertIn("of the SHV page is on", self.error.call_args.args[1])

    def test_a_sweep_refuses_a_later_move_when_its_4886_is_gone(self):
        run = self.make(angles=("30", "60", "30"))
        h = run.h
        h.move_hooks[30.0] = lambda: setattr(h.stfmr_lockin_controllers["kepco"], "connected", False)
        h.start_stfmr_lockin_scan()
        h.root.drain()
        self.assertEqual(run.sim.moves, ["X300000"])
        self.assertIn("The KEPCO 4886 is not connected; the sweep stopped before the stage moved",
                      self.error.call_args.args[1])

    def test_the_lookup_ignores_this_pages_own_and_missing_pages(self):
        h = object.__new__(App)
        own = Kepco(on=True)
        h.stfmr_lockin_controllers = {"kepco": own}
        self.assertEqual(h._stfmr_lockin_other_4886_on(), [])
        h.stfmr_controllers = {"kepco": own}
        self.assertEqual(h._stfmr_lockin_other_4886_on(), [], "the same object is this page's")
        h.stfmr_controllers = {"kepco": Kepco(on=True)}
        h.rte_panel = types.SimpleNamespace(rte_controllers={"kepco": Kepco(on=True)})
        self.assertEqual(h._stfmr_lockin_other_4886_on(), ["FMR", "SHV"])


# ---- SF2: the FMR close sets no StringVar off the Tk thread ------------------------------------
class FMRCloseReadoutTests(unittest.TestCase):
    def page(self):
        a = object.__new__(App)
        kepco = mock.Mock(connected=True)
        kepco.force_output_off.return_value = dict(mode="CURR", current_a=0.0, voltage_v=0.0, output=False)
        a.stfmr_controllers = {"kepco": kepco, "vna": mock.Mock(connected=True)}
        a._apply_stfmr_kepco_readback = mock.Mock(side_effect=RuntimeError("main thread is not in main loop"))
        a._flag_stfmr_kepco_off_unconfirmed = mock.Mock()
        return a

    def test_closing_updates_no_readout(self):
        a = self.page()
        results = a._stfmr_fmr_zero_outputs(readout=False)
        self.assertEqual(results, {"4886": "zeroed", "PNA RF": "zeroed"})
        a._apply_stfmr_kepco_readback.assert_not_called()
        a.stfmr_controllers["kepco"].force_output_off.side_effect = RuntimeError("OUTP OFF timeout")
        results = a._stfmr_fmr_zero_outputs(readout=False)
        self.assertTrue(results["4886"].startswith("zero FAILED: OUTP OFF timeout"))
        a._flag_stfmr_kepco_off_unconfirmed.assert_not_called()

    def test_a_tk_error_in_the_readout_never_fails_a_sent_zero(self):
        a = self.page()
        results = a._stfmr_fmr_zero_outputs()
        a._apply_stfmr_kepco_readback.assert_called_once()
        self.assertEqual(results["4886"], "zeroed")

    def test_safe_close_passes_readout_false(self):
        source = inspect.getsource(App.safe_close_stfmr)
        # magnet=: the LS648's zero then runs on a thread of its own
        # (2026-10-21); the readout is never set either way.
        self.assertIn("self._stfmr_fmr_zero_outputs(readout=False, magnet=not deferred)", source)


# ---- SF3 and SF6: the SMB100A carrier read-back and the first read-backs -----------------------
class CarrierReadBackTests(power_tests.PowerSweepCase):
    def test_the_carrier_is_compared_with_the_value_sent(self):
        smb = app.STFMRSMB100AController(lambda _message: None)
        smb.instrument = power_tests.FakeSmb100a()
        # 9 digits: "FREQ 12.3456789GHz", 1.2 Hz from the value asked for.
        readback = smb.set_frequency(12.3456789012)
        self.assertIn("FREQ 12.3456789GHz", smb.instrument.writes)
        self.assertAlmostEqual(readback, 12.3456789e9, delta=1e-3)
        smb.configure(12.3456789012, 10.0, 317.3)
        self.assertAlmostEqual(smb.last_configure_readbacks["frequency_readback_hz"], 12.3456789e9, delta=1e-3)
        self.assertAlmostEqual(smb.last_configure_readbacks["modulation_readback_hz"], 317.3, delta=1e-9)

    def test_a_carrier_the_generator_did_not_take_still_raises(self):
        smb = app.STFMRSMB100AController(lambda _message: None)
        smb.instrument = power_tests.FakeSmb100a()
        smb.instrument.query = lambda command: "5000000000" if command == "FREQ?" else "0"
        with self.assertRaisesRegex(RuntimeError, "did not take the new carrier frequency"):
            smb.set_frequency(6.0)

    def test_the_lfo_rule_is_unchanged(self):
        smb = app.STFMRSMB100AController(lambda _message: None)
        smb.instrument = power_tests.FakeSmb100a()
        smb.instrument.query = lambda command: "317.4" if command == "LFO:FREQ?" else "0"
        with self.assertRaisesRegex(RuntimeError, "modulation frequency"):
            smb.set_modulation_frequency(317.3)

    def test_a_failed_configure_leaves_no_old_read_backs(self):
        smb = app.STFMRSMB100AController(lambda _message: None)
        smb.instrument = power_tests.FakeSmb100a()
        smb.configure(5.0, 10.0, 317.3)
        self.assertIn("frequency_readback_hz", smb.last_configure_readbacks)
        smb.instrument.query = lambda command: "x"
        with self.assertRaises(RuntimeError):
            smb.configure(6.0, 10.0, 317.3)
        self.assertEqual(smb.last_configure_readbacks, {})

    def test_the_first_frequency_ready_has_the_carrier_and_lfo_read_backs(self):
        smb = app.STFMRSMB100AController(lambda _message: None)
        smb.instrument = power_tests.FakeSmb100a()
        h, recorder = self.sweep(powers=("10", "10", "1"), stop="6", smb=smb)
        self.error.assert_not_called()
        ready = [e for e in events(recorder) if e["event"] == "frequency_ready"]
        self.assertEqual(len(ready), 2)
        self.assertAlmostEqual(ready[0]["frequency_readback_hz"], 5e9, delta=1e-3)
        self.assertAlmostEqual(ready[0]["modulation_readback_hz"], 117.3, delta=1e-9)
        self.assertAlmostEqual(ready[1]["frequency_readback_hz"], 6e9, delta=1e-3)


# ---- SF4: Save CSV's file names --------------------------------------------------------------
class SaveCsvNameTests(unittest.TestCase):
    def test_a_saved_copy_never_matches_the_run_csv_pattern(self):
        with tempfile.TemporaryDirectory() as folder:
            h = harness(folder)
            h.root = mock.Mock()
            h.stfmr_lockin_rows = [{"index": 1, "current_a": 0.5}]
            h._export_stfmr_lockin_data_pngs = mock.Mock(return_value=())
            run = Path(folder) / "STFMR_field_scan_20261018_120000_000001"
            run.mkdir()
            (run / f"{run.name}.csv").write_text("index\n", encoding="utf-8")
            h.stfmr_lockin_run_directory = run
            with mock.patch.object(app.filedialog, "askdirectory", return_value=str(run)) as ask, \
                    mock.patch.object(app.messagebox, "showerror") as error:
                h.save_stfmr_lockin_results()
            error.assert_not_called()
            self.assertEqual(ask.call_args.kwargs["initialdir"], str(run))
            self.assertEqual(len(list(run.glob("STFMR_field_scan_*.csv"))), 1, "still one run CSV")
            [saved] = run.glob("STFMR_saved_*.csv")
            self.assertRegex(saved.name, r"^STFMR_saved_\d{8}_\d{6}_\d{6}\.csv$")
            h._export_stfmr_lockin_data_pngs.assert_called_once_with(run, saved.stem)
            with saved.open(encoding="utf-8-sig", newline="") as handle:
                self.assertEqual(list(csv.DictReader(handle)), [{"index": "1", "current_a": "0.5"}])


# ---- SF5: a cancelled Start unlocks the rows and Apply ---------------------------------------
class StartStepRefreshTests(unittest.TestCase):
    def test_every_step_refreshes_the_page(self):
        a = object.__new__(App)
        a.closing = a.stfmr_lockin_closed = a.stfmr_lockin_running = False
        seen = []
        a._update_stfmr_lockin_ui = mock.Mock(side_effect=lambda: seen.append(a.stfmr_lockin_starting))
        a._run_stfmr_lockin_start_step(lambda: a._update_stfmr_lockin_ui())   # cancelled at its Yes/No
        self.assertEqual(seen, [True, False], "locked during the step, refreshed after it")
        self.assertFalse(a._stfmr_lockin_rows_locked())
        self.assertFalse(a._stfmr_lockin_manual_output_held())

    def test_no_refresh_on_a_closing_page(self):
        a = object.__new__(App)
        a.closing, a.stfmr_lockin_closed, a.stfmr_lockin_running = True, False, False
        a._update_stfmr_lockin_ui = mock.Mock()
        a._run_stfmr_lockin_start_step(lambda: None)
        a._update_stfmr_lockin_ui.assert_not_called()


# ---- PS1, PS3: closing during a PPMS scan -----------------------------------------------------
class PPMSCloseTests(unittest.TestCase):
    def page(self):
        page = ppms_run.Page(self)
        sent = page.rig.log
        for key, controller in page.panel.ppms_instrument_controllers.items():
            def disconnect(key=key, controller=controller):
                sent.append(("disconnect", key))
                controller.instrument, controller.connected = None, False
            controller.disconnect = disconnect
        page.panel.ppms_environment_controller.disconnect = lambda: sent.append(("disconnect", "ppms"))
        page.panel.root.after_cancel = lambda _timer: None
        return page

    def test_the_6221_is_zeroed_and_logged_before_anything_disconnects(self):
        page = self.page()
        panel = page.panel
        during = []

        def on_wait(seconds):
            if panel.ppms_field_scan_rows and not panel.parent_app.closing:
                panel.parent_app.closing = True
                panel.safe_close_ppms()
                during.append(panel.ppms_instrument_controllers["6221"].connected)
        page.rig.stop.on_wait = on_wait
        page.start()
        self.assertEqual(during, [True], "the close left the instruments to the worker")
        log = page.rig.log
        zero = max(i for i, entry in enumerate(log) if entry == ("6221?", "OUTP?"))
        first_disconnect = log.index(next(e for e in log if e[0] == "disconnect"))
        self.assertLess(zero, first_disconnect, "zeroed and read back, then disconnected")
        self.assertIn(("disconnect", "6221"), log)
        self.assertIn(("disconnect", "ppms"), log)
        self.assertFalse(panel.ppms_instrument_controllers["6221"].connected)
        history = page.events(next(Path(page.folder.name).iterdir()) / "run1.log")
        zeroed = next(e for e in history if e["event"] == "outputs_zeroed")
        self.assertEqual(zeroed["6221"], "zeroed", "never 'not connected' for the source the scan drove")
        self.assertEqual(history[-1]["event"], "run_finished")
        self.assertEqual(history[-1]["outputs"], {"6221": "zeroed"})
        names = [e["event"] for e in history]
        self.assertLess(names.index("stop_requested"), names.index("outputs_zeroed"))

    def test_closing_while_idle_disconnects_at_once(self):
        page = self.page()
        page.panel.safe_close_ppms()
        self.assertIn(("disconnect", "6221"), page.rig.log)

    def test_the_handoff(self):
        handoff = app.ScanCloseHandoff()
        done = []
        self.assertFalse(handoff.defer(lambda: done.append("now")), "no scan: the close cleans up")
        handoff.begin()
        self.assertTrue(handoff.defer(lambda: done.append("after")))
        self.assertEqual(done, [])
        handoff.end()
        self.assertEqual(done, ["after"])
        handoff.end()
        self.assertEqual(done, ["after"], "once")


class FieldZeroOnceTests(unittest.TestCase):
    def page(self, closing=False):
        a = object.__new__(App)
        a.closing = closing
        a.ppms_field_scan_stop_event = threading.Event()
        a.ppms_field_scan_stop_event.set()
        a.ppms_active_scan_mode = "Field"
        a.ppms_environment_controller = mock.Mock(connected=True)
        a.post_ui = lambda _callback: None
        a.thread_log = mock.Mock()
        a._ppms_zero_outputs_after_scan = mock.Mock(return_value={"6221": "zeroed"})
        a._ppms_close_run = mock.Mock()
        return a

    def test_an_accepted_zero_is_sent_once(self):
        a = self.page()
        a.ppms_scan_field_zero_accepted = False
        self.assertTrue(a._ppms_zero_field_after_interrupted_scan(50.0, 1))
        self.assertTrue(a._ppms_zero_field_after_interrupted_scan(50.0, 1))
        a.ppms_environment_controller.set_field.assert_called_once_with(0.0, 100.0, 1, 1)
        self.assertEqual(a.ppms_scan_field_note, "field ramping to 0 Oe")

    def test_a_refused_zero_may_be_tried_again(self):
        a = self.page()
        a.ppms_scan_field_zero_accepted = False
        a.ppms_environment_controller.set_field.side_effect = [RuntimeError("busy"), None]
        self.assertFalse(a._ppms_zero_field_after_interrupted_scan(50.0, 1))
        self.assertEqual(a.ppms_scan_field_note, app.PPMS_FIELD_NOT_ZEROED_TEXT)
        self.assertTrue(a._ppms_zero_field_after_interrupted_scan(50.0, 1))
        self.assertEqual(a.ppms_scan_field_note, "field ramping to 0 Oe")

    def test_closing_zeroes_the_field_on_the_worker_before_the_log_closes(self):
        a = self.page(closing=True)
        order = mock.Mock()
        a.ppms_environment_controller.set_field = order.set_field
        a._ppms_close_run = order.close_run
        a._ppms_nested_scan_worker = mock.Mock()
        a._ppms_scan_with_final_field((), 50.0, 1.0, 1, {"harmonic_passes": [{}], "scan_mode": "Field"})
        self.assertEqual([name for name, _args, _kw in order.mock_calls], ["set_field", "close_run"])
        order.set_field.assert_called_once_with(0.0, 100.0, 1, 1)

    def test_closing_after_the_scans_own_zero_sends_no_second(self):
        a = self.page()
        a.ppms_field_scan_stop_event.clear()

        def nested(*_args):
            a.closing = True
            a.ppms_field_scan_stop_event.set()
            a._ppms_zero_field_after_interrupted_scan(50.0, 1)   # the field worker's clean-up
            raise app.PPMSScanCancelled("closing")
        a._ppms_nested_scan_worker = nested
        with self.assertRaises(app.PPMSScanCancelled):
            a._ppms_scan_with_final_field((), 50.0, 1.0, 1, {"harmonic_passes": [{}], "scan_mode": "Field"})
        a.ppms_environment_controller.set_field.assert_called_once()
        a._ppms_close_run.assert_called_once()

    def test_closing_a_temperature_scan_never_commands_the_field(self):
        a = self.page(closing=True)
        a.ppms_active_scan_mode = "Temperature"
        a._ppms_nested_scan_worker = mock.Mock()
        a._ppms_scan_with_final_field((), 50.0, 1.0, 1, {"harmonic_passes": [{}], "scan_mode": "Temperature"})
        a.ppms_environment_controller.set_field.assert_not_called()


# ---- PS2, PS5: the stop handlers' order and the I off dialog ---------------------------------
class Probe:
    closed = False

    def __init__(self, probe):
        self.probe, self.events = probe, []

    def event(self, name, **details):
        self.events.append((name, details.get("button"), self.probe()))
        return True


class PPMSStopOrderTests(unittest.TestCase):
    def page(self):
        a = object.__new__(App)
        a.closing = False
        a.ppms_field_scan_running = True
        a.ppms_field_scan_stop_event = threading.Event()
        a.ppms_field_scan_status_var = Var("")
        a.ppms_field_scan_stop_button = mock.Mock()
        a.ppms_connection_var = mock.Mock()
        a.ppms_executor = mock.Mock()
        a.ppms_executor.submit.return_value = Future()
        a.post_ui = lambda callback: callback()
        return a

    def test_stop(self):
        a = self.page()
        a.ppms_run_recorder = Probe(a.ppms_field_scan_stop_event.is_set)
        a.stop_ppms_field_scan()
        self.assertEqual(a.ppms_run_recorder.events, [("stop_requested", "Stop", True)])

    def test_i_off(self):
        a = self.page()
        a.ppms_run_recorder = Probe(lambda: (a.ppms_field_scan_stop_event.is_set(), a.ppms_executor.submit.called))
        a.ppms_sources_off()
        self.assertEqual(a.ppms_run_recorder.events, [("stop_requested", "I off", (True, True))])

    def test_the_i_off_error_dialog_opens_from_the_event_loop(self):
        a = self.page()
        later = []
        a.root = types.SimpleNamespace(after=lambda _ms, show: later.append(show))
        future = Future()
        a.ppms_executor.submit.return_value = future
        a.ppms_sources_off()
        with mock.patch.object(app.messagebox, "showerror") as shown:
            future.set_exception(RuntimeError("6221: output is still ON (OUTP?=1)"))
            shown.assert_not_called()
            self.assertEqual(len(later), 1)
            later.pop()()
        shown.assert_called_once_with("PPMS source off", "6221: output is still ON (OUTP?=1)", parent=a.root)


# ---- PS4: a failure says CHECK IT when an output was not confirmed at zero ----------------------
class FailureCheckItTests(unittest.TestCase):
    def ppms(self, results):
        a = object.__new__(App)
        later = []
        a.closing, a.root = False, types.SimpleNamespace(after=lambda _ms, show: later.append(show))
        a.thread_log = mock.Mock()
        a.ppms_field_scan_status_var, a.ppms_field_scan_eta_var = Var(""), Var("")
        a.ppms_scan_outputs = results
        with mock.patch.object(app.messagebox, "showerror") as shown:
            a._report_ppms_scan_finished("Current", False, OSError("CSV write failed"))
            while later:
                later.pop(0)()
        return a.ppms_field_scan_status_var.get(), shown

    def test_ppms(self):
        status, shown = self.ppms({"6221": "zero FAILED: OUTP? 1"})
        self.assertEqual(status, "Failed: CSV write failed; 6221 zero FAILED")
        shown.assert_called_once()
        self.assertTrue(shown.call_args.args[1].endswith("CHECK IT: the 6221 was not confirmed at zero."))
        status, _shown = self.ppms(None)
        self.assertEqual(status, "Failed: CSV write failed; 6221 not confirmed zeroed")
        status, shown = self.ppms({"6221": "zeroed"})
        self.assertEqual(status, "Failed: CSV write failed")
        self.assertNotIn("CHECK IT", shown.call_args.args[1])
        long = OSError("x" * 150)
        a = object.__new__(App)
        a.closing, a.root = True, None
        a.thread_log = mock.Mock()
        a.ppms_field_scan_status_var, a.ppms_field_scan_eta_var = Var(""), Var("")
        a.ppms_scan_outputs = {"6221": "zero FAILED: x"}
        a._report_ppms_scan_finished("Current", False, long)
        self.assertEqual(len(a.ppms_field_scan_status_var.get()), len("Failed: ") + 100)

    def test_shv_status(self):
        error = RuntimeError("6221 hit compliance; scan stopped and output turned off")
        # As before when every output is confirmed at zero.
        self.assertEqual(app.rte_failed_status_text(error, {"6221": "zeroed", "kepco": "zeroed"}),
                         "Failed: " + app.short_status_error(error, 100))
        self.assertEqual(app.rte_failed_status_text(error, {"6221": "zeroed", "kepco": "not connected"}),
                         "Failed: 6221 hit compliance;")
        self.assertEqual(app.rte_failed_status_text(error, {"6221": "zero FAILED: x", "kepco": "zeroed"}),
                         "Failed: 6221 hit compliance; 6221 zero FAILED")
        both = app.rte_failed_status_text(error, {"6221": "zero FAILED: x", "kepco": "zero FAILED: y"})
        self.assertEqual(both, "Failed: 6221 hit compliance; 6221 and KEPCO zero FAILED")
        self.assertEqual(app.rte_failed_status_text(error, None), "Failed: 6221 hit compliance; outputs not "
                                                                  "confirmed zeroed")
        long = RuntimeError("VI_ERROR_TMO (-1073807339): Timeout expired before operation completed")
        for results in ({"6221": "zero FAILED: x", "kepco": "zero FAILED: y"}, None,
                        {"6221": "zeroed", "kepco": "zero FAILED: y"}):
            text = app.rte_failed_status_text(long, results)
            self.assertLessEqual(len(text), app.RTE_FAILED_STATUS_CHARS, text)
            self.assertTrue(text.endswith(("zero FAILED", "confirmed zeroed")), text)


# ---- PS6: the SHV run log is closed after the stage's line ------------------------------------
class SHVCloseOrderTests(unittest.TestCase):
    def test_the_stage_and_instrument_lines_are_in_the_log_before_it_closes(self):
        panel = rte_tests.RTE.make_worker(self)
        panel.rte_stage_stop_event = threading.Event()
        panel.root = mock.Mock()
        panel.zurich_executor = mock.Mock()
        panel.zurich_controller = mock.Mock()
        panel.ppms_executor = ImmediateExecutor()
        panel.ppms_instrument_controllers = {}
        panel.rte_stage_where = "stage at +0.000°"
        stage = mock.Mock(connected=False)
        stage.disconnect.side_effect = lambda: panel.thread_log("SHV Newmark NSC-A1: stopped (STOP); closed")
        kepco = mock.Mock()
        kepco.disconnect.side_effect = lambda: panel.thread_log("SHV KEPCO 4886 VISA session closed")
        panel.rte_controllers = {"newmark": stage, "kepco": kepco}
        future = Future()
        future.set_exception(app.PPMSScanCancelled("closing"))
        panel.rte_scan_future = future
        with tempfile.TemporaryDirectory() as folder:
            panel.rte_recorder = app.SHVScanRecorder(Path(folder), "close")
            path = panel.rte_recorder.log_path
            panel.safe_close_ppms()
            history = read_events(path)
        names = [e.get("text", e["event"]) for e in history]
        self.assertEqual(names[-3:], ["SHV Newmark NSC-A1: stopped (STOP); closed",
                                      "SHV KEPCO 4886 VISA session closed", "run_finished"])
        self.assertEqual(history[-1]["outcome"], "stopped")
        self.assertIsNone(panel.__dict__.get("rte_recorder"))


# ---- PS7: an I off during Start's dialogs keeps the scan from starting -------------------------
class PPMSStartStopEventTests(unittest.TestCase):
    def test_an_old_stop_does_not_stop_the_new_scan(self):
        page = ppms_run.Page(self)
        page.panel.ppms_field_scan_stop_event.set()        # a Stop of the scan before
        page.start()
        self.assertEqual(page.panel.ppms_field_scan_status_var.get(), "Completed; 6221 zeroed")

    def test_i_off_during_the_yes_no_starts_nothing(self):
        page = ppms_run.Page(self)
        panel = page.panel
        with mock.patch.object(app.messagebox, "showerror"), mock.patch.object(app.messagebox, "askyesno", side_effect=lambda *_a, **_k: (panel.ppms_sources_off(),
                                                                                         True)[1]), \
                mock.patch.object(app.filedialog, "askdirectory") as folder:
            panel.start_ppms_field_scan()
        folder.assert_not_called()
        self.assertFalse(panel.ppms_field_scan_running)
        self.assertEqual(panel.ppms_field_scan_status_var.get(), "Current scan not started: I off was pressed")
        self.assertEqual(list(Path(page.folder.name).iterdir()), [])

    def test_i_off_during_the_folder_dialog_closes_the_run_not_started(self):
        page = ppms_run.Page(self)
        panel = page.panel

        def name(*_args, **_options):
            panel.ppms_sources_off()
            return "run1"
        with mock.patch.object(app.messagebox, "askyesno", return_value=True), \
                mock.patch.object(app.messagebox, "showerror"), \
                mock.patch.object(app.filedialog, "askdirectory", return_value=page.folder.name), \
                mock.patch.object(app.simpledialog, "askstring", side_effect=name):
            panel.start_ppms_field_scan()
        self.assertFalse(panel.ppms_field_scan_running)
        self.assertEqual(panel.ppms_field_scan_status_var.get(), "Current scan not started: I off was pressed")
        history = read_events(Path(page.folder.name) / "run1" / "run1.log")
        self.assertEqual([e["event"] for e in history], ["run_prepared", "run_finished"])
        self.assertEqual(history[-1]["outcome"], "not_started")
        self.assertFalse([entry for entry in page.rig.log if entry == ("6221", "OUTP ON")])


# ---- PS8: LIAS? asked once ------------------------------------------------------------------
class Instrument:
    def __init__(self, *replies):
        self.replies, self.queries, self.clears = list(replies), [], 0

    def query(self, command):
        self.queries.append(command)
        return self.replies.pop(0)

    def clear(self):
        self.clears += 1


class ClearOnReadTests(unittest.TestCase):
    def page(self, instrument):
        a = object.__new__(App)
        a.thread_log = mock.Mock()
        a.ppms_instrument_controllers = {"830_a": types.SimpleNamespace(
            label="SR830-1", lock=threading.RLock(), instrument=instrument, connected=True)}
        return a

    def test_a_misaligned_lias_is_not_asked_again_and_counts_as_overloaded(self):
        instrument = Instrument("4.2e-06,1")
        a = self.page(instrument)
        with mock.patch.object(app.time, "sleep"):
            with self.assertRaisesRegex(RuntimeError, "not asked again"):
                a._ppms_query_numbers("830_a", "LIAS?", 1)
        self.assertEqual((instrument.queries, instrument.clears), (["LIAS?"], 1), "asked once, buffer cleared")
        for reply in ("4.2e-06,1", "4.2e-06", "300"):
            with self.subTest(reply=reply):
                instrument = Instrument(reply)
                a = self.page(instrument)
                with mock.patch.object(app.time, "sleep"):
                    status = a._ppms_query_lias("830_a")
                self.assertEqual(status, app.NonLocalACController.SR830_LIAS_UNREADABLE)
                self.assertTrue(status & 7 and status & 8, "overloaded and unlocked")
                self.assertEqual(instrument.queries, ["LIAS?"])
                self.assertIn("not asked again", a.thread_log.call_args.args[0])

    def test_esr_once_and_other_queries_still_retried(self):
        instrument = Instrument("x")
        a = self.page(instrument)
        with mock.patch.object(app.time, "sleep"), self.assertRaises(RuntimeError):
            a._ppms_query_numbers("830_a", "*ESR?", 1)
        self.assertEqual(instrument.queries, ["*ESR?"])
        instrument = Instrument("x", "y", "317.3")
        a = self.page(instrument)
        with mock.patch.object(app.time, "sleep"):
            self.assertEqual(a._ppms_query_numbers("830_a", "FREQ?", 1), (317.3,))
        self.assertEqual(len(instrument.queries), 3)

    def test_the_status_read_uses_one_lias_per_read(self):
        instrument = Instrument("0", "3")
        a = self.page(instrument)
        a.ppms_sr830_range_states = {"830_a": types.SimpleNamespace(clear_latch_pending=True)}
        self.assertEqual(a._ppms_read_sr830_status("830_a"), 3)
        self.assertEqual(instrument.queries, ["LIAS?", "LIAS?"], "the discarding read, then the status")


# ---- MN1: the MOKE magnet's other KEPCO, sent and not read back --------------------------------
class SentNotReadBackTests(unittest.TestCase):
    labels = ("Out-of-plane KEPCO", "In-plane KEPCO")

    def test_zero_scan_outputs_keeps_the_state(self):
        results = app.zero_scan_outputs((("A", lambda: None), ("B", lambda: app.ZERO_SENT_NOT_READ_BACK),
                                         ("C", lambda: "not connected"), ("D", lambda: "anything else")))
        self.assertEqual(results, {"A": "zeroed", "B": "0 A sent, not read back", "C": "not connected",
                                   "D": "zeroed"})

    def test_the_texts_neither_zeroed_nor_failed(self):
        text = app.scan_end_outputs_text
        sent = app.ZERO_SENT_NOT_READ_BACK
        self.assertEqual(text({"Out-of-plane KEPCO": "zeroed", "In-plane KEPCO": sent}, self.labels,
                              together="both KEPCOs"),
                         "Out-of-plane KEPCO zeroed; In-plane KEPCO 0 A sent, not read back")
        self.assertEqual(text({"Out-of-plane KEPCO": "zero FAILED: x", "In-plane KEPCO": sent}, self.labels,
                              together="both KEPCOs"),
                         "Out-of-plane KEPCO zero FAILED; In-plane KEPCO 0 A sent, not read back: check it")
        self.assertEqual(text({"Out-of-plane KEPCO": "zeroed", "In-plane KEPCO": "zeroed"}, self.labels,
                              together="both KEPCOs"), "both KEPCOs zeroed")

    def test_no_check_it_dialog_for_a_zero_sent_by_design(self):
        a = object.__new__(App)
        a._close_scan_recorder = mock.Mock()
        a._dialog_after_drain = mock.Mock()
        status = a._finish_scan_run("magnet_scan_recorder", page="MOKE", title="MOKE current scan", error=None,
                                    stopped=False, labels=self.labels, together="both KEPCOs",
                                    results={"Out-of-plane KEPCO": "zeroed",
                                             "In-plane KEPCO": app.ZERO_SENT_NOT_READ_BACK})
        self.assertEqual(status, "Completed; Out-of-plane KEPCO zeroed; In-plane KEPCO 0 A sent, not read back")
        a._dialog_after_drain.assert_not_called()


# ---- MN2: closing mid Non-local scan ----------------------------------------------------------
class NonlocalCloseTests(unittest.TestCase):
    def page(self):
        a = object.__new__(App)
        a.closing = False
        a.nonlocal_vi_closed = False
        a.nonlocal_vi_stop_event = threading.Event()
        a.nonlocal_keithley_stop_event = threading.Event()
        a.nonlocal_vi_executor = mock.Mock()
        self.order = order = mock.Mock()
        a._nonlocal_stop_requested = order.stop_requested
        a._disconnect_nonlocal_vi_worker = order.disconnect
        a._nonlocal_zero_outputs = mock.Mock(side_effect=lambda _recorder: (
            order.zero(), {"6221": "zeroed", "Keithley 2400": "zeroed"})[1])
        a._nonlocal_close_run = order.close_run
        return a

    def test_the_scan_zeroes_and_closes_its_log_before_the_disconnect(self):
        a = self.page()
        recorder = mock.Mock()

        def body():
            a.closing = True
            a.safe_close_nonlocal_vi()          # the program closing, mid scan
            self.order.body_after_close()
            raise app.NonLocalVIStopped("closing")
        with self.assertRaises(app.NonLocalVIStopped):
            a._nonlocal_scan_job(recorder, "gate", body)
        self.assertEqual([name for name, _args, _kw in self.order.mock_calls],
                         ["stop_requested", "body_after_close", "zero", "close_run", "disconnect"])
        self.assertEqual(self.order.close_run.call_args.kwargs["outputs"],
                         {"6221": "zeroed", "Keithley 2400": "zeroed"})
        a.nonlocal_vi_executor.shutdown.assert_called_once_with(wait=False, cancel_futures=True)

    def test_closing_with_no_scan_disconnects_at_once(self):
        a = self.page()
        a.safe_close_nonlocal_vi()
        self.assertEqual([name for name, _args, _kw in self.order.mock_calls], ["stop_requested", "disconnect"])


# ---- MN3: the Non-local dialogs from the Tk event loop ---------------------------------------
class NonlocalDialogTests(unittest.TestCase):
    FINISHES = ("_finish_nonlocal_vi_device_connect", "_finish_nonlocal_vi_device_disconnect",
                "_finish_nonlocal_vi_connect", "_finish_nonlocal_vi_disconnect", "_finish_nonlocal_vi_off",
                "_finish_nonlocal_keithley_set", "_finish_nonlocal_keithley_pulse", "_finish_nonlocal_iv6221_set")

    def test_no_finish_opens_a_dialog_itself(self):
        for name in self.FINISHES:
            with self.subTest(name=name):
                source = inspect.getsource(getattr(App, name))
                self.assertNotRegex(source, r"messagebox\.\w+\(")
                self.assertIn("self._nonlocal_dialog_later(", source)

    def test_the_off_finish_opens_its_error_later(self):
        a = object.__new__(App)
        later = []
        a.closing, a.root = False, types.SimpleNamespace(after=lambda _ms, show: later.append(show))
        a.nonlocal_vi_off_pending = 1
        a.nonlocal_vi_2410_controller = types.SimpleNamespace(model="2400")
        a.nonlocal_vi_output_var, a.nonlocal_iv6221_source_var, a.nonlocal_vi_status_var = Var(""), Var(""), Var("")
        a._update_nonlocal_vi_controls = mock.Mock()
        with mock.patch.object(app.messagebox, "showerror") as shown:
            a._finish_nonlocal_vi_off(RuntimeError("6221 OUTP? 1"))
            shown.assert_not_called()
            later.pop()()
        shown.assert_called_once_with("Non-local VI", "6221 OUTP? 1", parent=a.root)


# ---- MN4: the MOKE-24 and CIDWM controllers' lines are notes ----------------------------------
class ControllerNoteTests(unittest.TestCase):
    def test_the_lines_are_notes_of_a_running_scan(self):
        a = object.__new__(App)
        a.thread_log = mock.Mock()
        for log, attribute in ((a._keithley_controller_log, "keithley_scan_recorder"),
                               (a._cidwm_controller_log, "cidwm_scan_recorder")):
            with self.subTest(attribute=attribute):
                recorder = mock.Mock()
                setattr(a, attribute, recorder)
                log("Keithley line")
                a.thread_log.assert_called_with("Keithley line")
                recorder.event.assert_called_once_with("note", text="Keithley line")
                setattr(a, attribute, None)
                log("idle line")                  # no scan: the program log only
                a.thread_log.assert_called_with("idle line")

    def test_the_controllers_are_made_with_them(self):
        source = inspect.getsource(App.__init__)
        self.assertIn("Keithley2410Controller(self._keithley_controller_log)", source)
        self.assertIn("Keithley6221Controller(self._cidwm_controller_log)", source)
        self.assertIn("AvtechPulserController(self._cidwm_controller_log)", source)


# ---- Nits: the re-checks after the dialogs; Gate-VI's summary header ----------------------------
class NitTests(unittest.TestCase):
    def test_the_magnet_and_cidwm_starts_check_the_other_scans_after_their_dialogs(self):
        magnet = inspect.getsource(App.start_magnet_scan)
        after = magnet[magnet.index("recorder = begin_scan_run("):]
        self.assertIn("or self.keithley_scan_running", after)
        self.assertIn("or self.k6221_scan_running", after)
        cidwm = inspect.getsource(App.start_cidwm_pulse_scan)
        after = cidwm[cidwm.index("recorder = begin_scan_run("):]
        self.assertIn("or self.magnet_scan_running or self.keithley_scan_running", after)

    def test_write_header_before_any_row(self):
        with tempfile.TemporaryDirectory() as folder:
            recorder = app.ScanRunRecorder(Path(folder), "NL_GateVI", "gate", {})
            recorder.write_header(("point_index", "pulse_voltage_V"))
            recorder.write_header(("other",))          # once there, kept
            recorder.append_row({"point_index": 1, "pulse_voltage_V": 0.5})
            with self.assertRaisesRegex(ValueError, "no column for x"):
                recorder.append_row({"x": 1})
            recorder.close("completed")
            with recorder.csv_path.open(encoding="utf-8-sig", newline="") as handle:
                self.assertEqual(list(csv.reader(handle)), [["point_index", "pulse_voltage_V"], ["1", "0.5"]])
            with self.assertRaisesRegex(RuntimeError, "closed"):
                recorder.write_header(("a",))

    def test_a_gate_vi_stopped_before_its_first_point_has_the_summary_header(self):
        with tempfile.TemporaryDirectory() as folder:
            recorder = app.ScanRunRecorder(Path(folder), "NL_GateVI", "gate", {})
            a = object.__new__(App)
            a._nonlocal_keithley_off_first = mock.Mock(side_effect=app.NonLocalVIStopped("stopped"))
            a.nonlocal_vi_interlock = mock.Mock()
            with self.assertRaises(app.NonLocalVIStopped):
                a._nonlocal_vi_scan_worker({"frequency_hz": 317.3, "voltage_scan": (0.0,)}, recorder)
            with recorder.csv_path.open(encoding="utf-8-sig", newline="") as handle:
                self.assertEqual(list(csv.reader(handle)), [list(App._nonlocal_vi_summary_header())])
            recorder.close("stopped")


if __name__ == "__main__":
    unittest.main()
