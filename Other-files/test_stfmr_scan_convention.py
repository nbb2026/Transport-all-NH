"""The STFMR lock-in sweep on the shared save and end convention, and the
audit's ST-FMR findings of 2026-10-18 (S1-S4, C1-4).

Save (user, 2026-10-18): Yes/No -> begin_scan_run (folder, name, the
STFMRScanRecorder files) -> the sweep; the log has run_prepared,
scan_started, point_saved, note, stop_requested, outputs_zeroed and
run_finished; Save CSV opens in the run folder.  End: the 4886 zeroed
(force_output_off) and the SMB100A RF off with OUTP? read back, on the
page's worker, at every end.

Window-free: the shared STFMR harness (stand-in instruments, a Root that runs
callbacks in order); every dialog is answered here."""
import sys
import threading
import types
import unittest
from pathlib import Path
from unittest import mock

_HERE = Path(__file__).resolve().parent
for _path in (_HERE / "test", _HERE.parent):
    if str(_path) not in sys.path:
        sys.path.insert(0, str(_path))
from test_stfmr_realtime_recording import Variable, app, events, folder_dialog, harness, read_csv
from test_stfmr_sr830_tc_fixed_current import NoWindows
from test_stfmr_mod_prime_range import width_in_zeros
from test_stfmr_motor_angle import LABEL, MotorBase
import test_fmr_stfmr_minors as minors


class Sweep(NoWindows):
    def start(self, **options):
        h = harness(self.tmp.name, **options)
        h.order = []
        self.confirm.side_effect = lambda *a, **k: h.order.append("yes/no") or True
        self.folder_dialog.side_effect = lambda *a, **k: h.order.append("folder") or folder_dialog(*a, **k)
        self.file_name.side_effect = lambda *a, **k: h.order.append("name") or "run A"
        h.submitted = []
        original = h.stfmr_lockin_executor.submit

        def submit(fn, *args):
            h.submitted.append(getattr(fn, "__name__", repr(fn)))
            return original(fn, *args)
        h.stfmr_lockin_executor.submit = submit
        h.start_stfmr_lockin_scan()
        return h, h.stfmr_lockin_recorder


class SaveAndEndTests(Sweep):
    def test_a_completed_sweep_logs_the_convention_and_zeroes_on_the_worker(self):
        h, recorder = self.start()
        self.assertEqual(h.order, ["yes/no", "folder", "name"], "the shared order")
        self.assertIn("At the end (completed, stopped or failed) the 4886 is zeroed and the SMB100A RF switched off",
                      self.confirm.call_args.args[1])
        self.assertEqual(recorder.directory, Path(self.tmp.name) / "run A")
        h.root.drain()
        self.error.assert_not_called()
        history = events(recorder)
        names = [e["event"] for e in history]
        self.assertEqual(names[:2], ["run_prepared", "scan_started"])
        self.assertEqual(history[0]["settings"]["file_name"], "run A")
        self.assertEqual(len(read_csv(recorder.csv_path)), 6)
        self.assertEqual(names.count("point_saved"), 6, "one per saved point")
        self.assertEqual(names[-3:], ["outputs_zeroed", "note", "run_finished"])
        self.assertEqual({k: history[-3][k] for k in ("4886", "SMB100A RF")},
                         {"4886": "zeroed", "SMB100A RF": "zeroed"})
        end = history[-1]
        self.assertEqual((end["outcome"], end["saved_points"], end["points_saved"], end["outputs_off"]),
                         ("completed", 6, 6, True))
        self.assertEqual(end["status"], "Completed; 4886 and RF zeroed; 6 points saved; 2 frequencies")
        self.assertEqual(h.submitted[-1], "_stfmr_lockin_zero_outputs", "zeroed on the page's worker")
        self.assertFalse(h.stfmr_lockin_running)
        self.assertFalse(h.stfmr_lockin_ending)

    def test_the_sweep_counts_as_running_until_its_outputs_are_zeroed(self):
        h, recorder = self.start()
        while not h.stfmr_lockin_rows:
            h.root.step()
        h.stop_stfmr_lockin_scan()
        self.assertTrue(h.stfmr_lockin_running and h.stfmr_lockin_ending, "locked until the zero reports")
        self.assertEqual(h.stfmr_lockin_status_var.get(), "Ending; zeroing the 4886, SMB100A RF off…")
        h.stop_stfmr_lockin_scan()          # a second press while ending: nothing more
        h.root.drain()
        history = events(recorder)
        self.assertEqual([e.get("button") for e in history if e["event"] == "stop_requested"], ["Stop / zero"])
        self.assertEqual(history[-1]["outcome"], "stopped")
        self.assertEqual(h.stfmr_lockin_status_var.get(), "Stopped; 4886 and RF zeroed; 1 point saved")

    def test_the_kepco_zero_off_stops_the_sweep_and_says_so(self):
        h, recorder = self.start()
        while not h.stfmr_lockin_rows:
            h.root.step()
        h.stfmr_lockin_controllers["kepco"].lock = threading.Lock()
        h.zero_stfmr_lockin_kepco_output()
        h.root.drain()
        history = events(recorder)
        self.assertEqual([e.get("button") for e in history if e["event"] == "stop_requested"],
                         ["KEPCO Zero / OFF"])
        self.assertEqual(history[-1]["outcome"], "stopped")

    def test_a_failure_zeroes_both_and_its_dialog_says_how_they_ended(self):
        h, recorder = self.start()
        while not h.stfmr_lockin_rows:
            h.root.step()
        h.stfmr_lockin_controllers["gauss"].read_field = mock.Mock(side_effect=RuntimeError("LS475 gone"))
        h.root.drain()
        self.assertEqual(h.stfmr_lockin_status_var.get(), "Failed: LS475 gone")
        self.error.assert_called_once()
        self.assertEqual(self.error.call_args.args,
                         ("STFMR scan", "LS475 gone\n\nAt the end: 4886 zeroed; SMB100A RF zeroed"))
        end = events(recorder)[-1]
        self.assertEqual((end["outcome"], end["error"]), ("error", "LS475 gone"))

    def test_an_rf_off_not_read_back_is_checked_and_the_4886_still_zeroed(self):
        h, recorder = self.start()
        h.stfmr_lockin_controllers["smb"].rf_off_read_back = mock.Mock(
            side_effect=RuntimeError("the SMB100A RF did not read back OFF (OUTP? '1')"))
        h.root.drain()
        self.assertIn(("zero",), h.stfmr_lockin_controllers["kepco"].calls)
        status = h.stfmr_lockin_status_var.get()
        self.assertTrue(status.startswith("Completed; 4886 zeroed; RF zero FAILED: check it; "), status)
        self.assertLessEqual(width_in_zeros("Completed; 4886 zeroed; RF zero FAILED: check it"), LABEL)
        self.error.assert_called_once()
        self.assertEqual(self.error.call_args.args[0], "STFMR outputs")
        self.assertIn("CHECK IT: SMB100A RF not confirmed at zero.", self.error.call_args.args[1])
        end = events(recorder)[-1]
        self.assertEqual((end["outcome"], end["outputs_off"]), ("completed", False))

    def test_save_csv_opens_in_the_run_folder(self):
        h, recorder = self.start()
        h.root.drain()
        self.folder_dialog.side_effect = None
        self.folder_dialog.return_value = ""
        h.save_stfmr_lockin_results()
        self.assertEqual(self.folder_dialog.call_args.kwargs["initialdir"], str(recorder.directory))

    def test_closing_mid_sweep_zeroes_both_and_logs_it(self):
        h, recorder = self.start()
        while not h.stfmr_lockin_rows:
            h.root.step()
        h.closing = True
        h.safe_close_stfmr_lockin()
        history = events(recorder)
        self.assertEqual([e["event"] for e in history[-3:]], ["stop_requested", "outputs_zeroed", "run_finished"])
        self.assertEqual(history[-3]["button"], "program closing")
        self.assertEqual(history[-1]["outcome"], "closed")
        self.assertEqual(history[-1]["outputs"], {"4886": "zeroed", "SMB100A RF": "zeroed"})
        h.root.drain()
        self.assertEqual(len(events(recorder)), len(history), "nothing after the close")

    def test_a_disconnect_ending_during_the_dialogs_starts_nothing(self):
        # review, 2026-10-18 (S3): every instrument is checked again after
        # the folder and name dialogs, before any output.
        h = harness(self.tmp.name)
        h.stfmr_lockin_starting = False

        def folder(*_args):
            h.stfmr_lockin_controllers["smb"] = types.SimpleNamespace(connected=False, calls=[])
            return Path(self.tmp.name)
        h.select_csv_save_directory = folder
        h.start_stfmr_lockin_scan()
        self.assertFalse(h.stfmr_lockin_running)
        self.assertIsNone(h.stfmr_lockin_recorder)
        self.error.assert_called_once()
        self.assertEqual(self.error.call_args.args[:2], ("STFMR start failed",
                                                         "The SMB100A is no longer connected; the sweep was not "
                                                         "started"))
        self.assertFalse([c for c in h.stfmr_lockin_controllers["kepco"].calls if c[0] == "output"])
        (log,) = Path(self.tmp.name).glob("*/*.log")
        self.assertEqual(events(types.SimpleNamespace(log_path=log))[-1]["outcome"], "not_started")

    def test_the_end_lines_fit_the_status_label(self):
        for status in ("Completed; 4886 and RF zeroed; 12345 points saved",
                       "Stopped; 4886 and RF zeroed; 12345 points saved",
                       "Completed; zero FAILED: 4886 and RF: check them",
                       "Failed: " + app.short_status_error(RuntimeError("x" * 80), 44)):
            with self.subTest(status=status):
                self.assertLessEqual(width_in_zeros(status), LABEL)


class Sr830RangeTests(Sweep):
    """C1-4 (user, 2026-10-18: "Keep my front-panel range")."""

    class Instrument:
        def __init__(self, sens="20"):
            self.writes, self.sens = [], sens

        def write(self, command):
            self.writes.append(command)
            if command.startswith("SENS "):
                self.sens = command.split()[1]

        def query(self, command):
            return {"FMOD?": "0", "RSLP?": "0", "HARM?": "1", "SENS?": self.sens, "OFLT?": "10"}[command]

    def controller(self, **options):
        controller = app.STFMRSR830Controller(lambda _text: None)
        controller.instrument = fake = self.Instrument(**options)
        return controller, fake

    def test_autorange_off_keeps_and_records_the_front_panel_range(self):
        controller, fake = self.controller(sens="20")
        result = controller.configure("Sine", False, 10)
        self.assertFalse([w for w in fake.writes if w.startswith("SENS")], "no SENS sent")
        self.assertEqual((result["sensitivity_index"], controller.sensitivity_index), (20, 20))

    def test_autorange_on_still_starts_from_sens_13(self):
        controller, fake = self.controller(sens="20")
        self.assertEqual(controller.configure("Sine", True, 10)["sensitivity_index"], 13)
        self.assertIn("SENS 13", fake.writes)

    def test_a_reply_that_is_no_range_is_refused(self):
        controller, _fake = self.controller(sens="30")
        with self.assertRaisesRegex(RuntimeError, "not a sensitivity index"):
            controller.configure("Sine", False, 10)

    def test_the_run_log_records_the_range_read(self):
        h = harness(self.tmp.name, meter="SR830", stop="5")
        h.stfmr_lockin_sr830_auto_range_var.set(False)
        sr830 = h.stfmr_lockin_controllers["830"]
        original = sr830.configure
        sr830.configure = lambda *args: original(*args) or {"sensitivity_index": 20}
        h.start_stfmr_lockin_scan()
        recorder = h.stfmr_lockin_recorder
        h.root.drain()
        history = events(recorder)
        settings = history[0]["settings"]
        self.assertEqual((settings["sr830_initial_sensitivity_index"], settings["sr830_front_panel_range_kept"]),
                         (None, True))
        ready = [e for e in history if e["event"] == "frequency_ready"]
        self.assertEqual(ready[0]["sr830_sensitivity_index"], 20)
        self.assertIn("Autorange: off (front-panel range kept)", self.confirm.call_args.args[1])


class Smb100aReadBackTests(unittest.TestCase):
    """S4 (review, 2026-10-18): the carrier and the LFO are read back."""

    class Instrument:
        def __init__(self):
            self.sequence, self.state = [], {"FREQ?": "5e9", "LFO:FREQ?": "317", ":POW?": "10", "OUTP?": "0"}
            self.ignore = set()

        def write(self, command):
            self.sequence.append(command)
            if any(command.startswith(prefix) for prefix in self.ignore):
                return
            if command.startswith("FREQ "):
                self.state["FREQ?"] = f"{float(command[5:-3]) * 1e9:.12g}"
            elif command.startswith("LFO:FREQ "):
                self.state["LFO:FREQ?"] = command.split()[1]
            elif command.startswith(":POW "):
                self.state[":POW?"] = command.split()[1]
            elif command == "OUTPUT OFF":
                self.state["OUTP?"] = "0"

        def query(self, command):
            self.sequence.append(command)
            return self.state[command]

    def smb(self):
        controller = app.STFMRSMB100AController(lambda _text: None)
        controller.instrument = self.Instrument()
        return controller

    def test_a_carrier_the_generator_kept_is_refused(self):
        smb = self.smb()
        self.assertEqual(smb.set_frequency(6.25), 6.25e9)
        self.assertEqual(smb.instrument.sequence[-2:], ["FREQ 6.25GHz", "FREQ?"])
        smb.instrument.ignore = {"FREQ "}
        with self.assertRaisesRegex(RuntimeError, r"did not take the new carrier frequency: sent 'FREQ 7GHz', "
                                                  r"FREQ\? read back '6250000000'"):
            smb.set_frequency(7.0)

    def test_an_lfo_the_generator_kept_is_refused_and_rounding_is_taken(self):
        smb = self.smb()
        self.assertEqual(smb.set_modulation_frequency(311), 311.0)
        smb.instrument.ignore = {"LFO:FREQ "}
        smb.instrument.state["LFO:FREQ?"] = "117.4"
        self.assertEqual(smb.set_modulation_frequency(117.35), 117.4, "the generator's 0.1 Hz rounding")
        with self.assertRaisesRegex(RuntimeError, "did not take the new modulation frequency"):
            smb.set_modulation_frequency(313)

    def test_configure_refuses_before_anything_is_switched_on(self):
        for prefix, what in (("LFO:FREQ ", "modulation frequency"), ("FREQ ", "carrier frequency")):
            with self.subTest(prefix=prefix):
                smb = self.smb()
                smb.instrument.ignore = {prefix}
                smb.instrument.state.update({"FREQ?": "1", "LFO:FREQ?": "1"})
                with self.assertRaisesRegex(RuntimeError, f"did not take the new {what}"):
                    smb.configure(5.0, 10.0, 317.3)
                self.assertNotIn("OUTPUT ON", smb.instrument.sequence)
                self.assertNotIn("LFO ON", smb.instrument.sequence)

    def test_the_rf_off_is_read_back(self):
        smb = self.smb()
        smb.rf_off_read_back()
        self.assertEqual(smb.instrument.sequence, ["AM:STAT OFF", "LFO OFF", "OUTPUT OFF", "OUTP?"])
        smb.instrument.ignore = {"OUTPUT OFF"}
        smb.instrument.state["OUTP?"] = "1"
        with self.assertRaisesRegex(RuntimeError, r"did not read back OFF \(OUTP\? '1'\)"):
            smb.rf_off_read_back()


class HandMoveWith4886OnTests(MotorBase):
    """S1 (user, 2026-10-18: "Refuse: zero it first")."""

    def test_a_hand_move_is_refused_while_the_4886_is_on(self):
        run = self.make()
        h = run.h
        h.stfmr_lockin_stage_target_var.set("15")
        h.stfmr_lockin_controllers["kepco"].output_owned = True
        h.move_stfmr_lockin_stage()
        self.error.assert_called_once()
        self.assertIn("Zero the 4886 first (Zero / OFF)", self.error.call_args.args[1])
        h.root.drain()
        self.assertEqual(run.sim.moves, [], "nothing moved")
        h.stfmr_lockin_controllers["kepco"].output_owned = False
        h.move_stfmr_lockin_stage()
        h.root.drain()
        self.assertEqual(run.sim.moves, ["X150000"])

    def test_its_clr_or_eo_fix_is_refused_too(self):
        run = self.make()
        run.h.stfmr_lockin_controllers["kepco"].output_owned = True
        with self.assertRaisesRegex(RuntimeError, "Zero the 4886 first"):
            run.h._stfmr_lockin_require_stage_fix_ready()


class ApplyOutputLockTests(unittest.TestCase):
    """S1: Apply output is locked while a stage job is pending."""

    def page(self):
        a = minors.lockin_app(connected=("kepco", "gauss", "smb", "mfli"))
        for name in ("apply_output", "read_output", "zero_output"):
            setattr(a, f"stfmr_lockin_{name}_button", minors.Widget())
        a.stfmr_lockin_controllers["kepco"].set_output = mock.Mock()
        a.stfmr_lockin_manual_safety_var = Variable(True)
        a._show_stfmr_lockin_kepco_state = lambda: None
        a.root = None
        return a

    def test_locked_and_refused_while_a_stage_job_or_a_start_is_pending(self):
        for flag in ("stfmr_lockin_motor_busy", "stfmr_lockin_stage_prompt_open", "stfmr_lockin_stage_stop_pending",
                     "stfmr_lockin_motor_check_pending", "stfmr_lockin_starting"):
            with self.subTest(flag=flag):
                a = self.page()
                setattr(a, flag, True)
                a._update_stfmr_lockin_apply_button()
                self.assertEqual(a.stfmr_lockin_apply_output_button.state, "disabled")
                with mock.patch.object(app.messagebox, "showwarning") as warning:
                    a.apply_stfmr_lockin_kepco_output()
                warning.assert_called_once()
                a.stfmr_lockin_controllers["kepco"].set_output.assert_not_called()
                setattr(a, flag, False)
                a._update_stfmr_lockin_apply_button()
                self.assertEqual(a.stfmr_lockin_apply_output_button.state, "normal")


class StartLockTests(unittest.TestCase):
    """S2 and S3 (review, 2026-10-18)."""

    def test_a_connect_ending_during_the_start_keeps_the_meter(self):
        for flag in ("stfmr_lockin_starting", "stfmr_lockin_motor_check_pending"):
            with self.subTest(flag=flag):
                a = minors.lockin_app(meter="MFLI", connected=("kepco", "gauss", "smb", "mfli"))
                a.stfmr_lockin_controllers["830"].connect = mock.Mock()
                a.connect_stfmr_lockin_device("830")        # pressed before the Start
                setattr(a, flag, True)
                a.stfmr_lockin_controllers["830"].connected = True
                a.stfmr_lockin_executor.finish()
                self.assertEqual(a.stfmr_lockin_meter_var.get(), "MFLI", "the meter the Start read")

    def test_the_rows_are_locked_from_the_start_and_connect_disconnect_refused(self):
        for flag in ("stfmr_lockin_starting", "stfmr_lockin_motor_check_pending"):
            with self.subTest(flag=flag):
                a = minors.lockin_app()
                setattr(a, flag, True)
                a._update_stfmr_lockin_ui()
                for key in minors.KEYS:
                    self.assertEqual(minors.row_states(a, key), ("disabled",) * 3, key)
                a.connect_stfmr_lockin_device("830")
                a.disconnect_stfmr_lockin_device("smb")
                self.assertEqual(a.stfmr_lockin_executor.jobs, [], "nothing queued")

    def test_the_instruments_are_checked_again(self):
        a = minors.lockin_app(meter="MFLI")
        a._stfmr_lockin_require_instruments("MFLI")
        a.stfmr_lockin_connection_vars["smb"].set("Disconnecting…")
        with self.assertRaisesRegex(RuntimeError, "^The SMB100A is no longer connected"):
            a._stfmr_lockin_require_instruments("MFLI")
        a.stfmr_lockin_connection_vars["smb"].set("Connected")
        a.stfmr_lockin_controllers["kepco"].connected = False
        with self.assertRaisesRegex(RuntimeError, "^The KEPCO 4886 is no longer connected"):
            a._stfmr_lockin_require_instruments("MFLI")


if __name__ == "__main__":
    unittest.main()
