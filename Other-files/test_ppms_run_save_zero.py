"""PPMS page: one run folder and log per scan, and the 6221 zeroed at every
end (user, 2026-10-18), with the PPMS review findings of that day (P1-P9).

No Tk window and no instrument: the real PPMSPanel is built on stand-in
widgets (test_ppms_sr830_tc), its rack holds the fake 6221 and SR830s of
test_ppms_frequency_scan, every dialog is mocked, and a fake clock moves only
when the scan waits.  The real Start, worker, zeroing and finish run.
"""
import csv
import json
import math
import tempfile
import threading
import types
import unittest
from pathlib import Path
from unittest import mock

from test_nonlocal_keithley import Var
from test_ppms_frequency_scan import ClockEvent, Motor, Rig
from test_ppms_motor_debug import app
from test_ppms_sr830_tc import build_ppms_panel


class Page:
    """A built PPMS page on the fake rack: SR830-1 and SR830-3 measured, no
    Zurich, the rotator at 26°, 300 K and 1234 Oe read back."""

    def __init__(self, test, mode="Current", main_row=("0.5", "1", "0.5")):
        self.test = test
        self.rig = rig = Rig(test, mode, None, (0.5, 1.0), zurich=False)
        panel, _ttk = build_ppms_panel()
        del panel.thread_log            # the page's own: its log lines become notes
        self.lines = []
        panel.parent_app.append_log = self.lines.append
        for name in ("ppms_instrument_controllers", "ppms_sr830_range_states", "_ppms_autorange_sr830",
                     "_read_ppms_live", "_apply_ppms_environment_readback"):
            setattr(panel, name, getattr(rig.page, name))
        rig.page.ppms_environment_controller.backend = app.PPMS_QDINSTRUMENT_BACKEND
        rig.page.ppms_environment_controller.connected = True
        panel.ppms_environment_controller = rig.page.ppms_environment_controller
        panel.ppms_field_scan_stop_event = rig.stop
        panel._require_ppms_control_ready = mock.Mock()
        panel.ppms_lockin_selection["zurich"].set(False)
        panel.ppms_lockin_selection["830_b"].set(False)
        panel.ppms_motor_confirm_var.set(True)
        panel.ppms_motor_target_var.set("26")
        panel.ppms_temperature_target_var.set("300")
        panel.ppms_field_target_var.set("50000")    # not used: the field is not commanded
        panel.ppms_transport_frequency_var.set("317.3")
        panel.ppms_acquisition_time_var.set("1")
        panel.ppms_last_readback = dict(temperature_k=300.0, field_oe=1234.0, angle_deg=26.0)
        panel.ppms_simple_mode.set(mode)
        for var, value in zip(panel.ppms_simple_range, main_row):
            var.set(value)
        self.dialogs, self.later = [], []
        panel.root = types.SimpleNamespace(after=lambda _ms, show: (self.later.append(show), show()))
        self.panel, self.folder = panel, tempfile.TemporaryDirectory()
        test.addCleanup(self.folder.cleanup)

    def start(self, name="run1", answer=True, folder=True):
        def error(title, text, **_options):
            self.dialogs.append((title, text))
        with mock.patch.object(app.time, "monotonic", self.rig.clock), \
                mock.patch.object(app.time, "sleep", lambda _s: None), \
                mock.patch.object(app, "PPMSMotorDebug", Motor), \
                mock.patch.object(app.messagebox, "askyesno", return_value=answer) as ask, \
                mock.patch.object(app.messagebox, "showerror", side_effect=error), \
                mock.patch.object(app.filedialog, "askdirectory",
                                  return_value=self.folder.name if folder else "") as directory, \
                mock.patch.object(app.simpledialog, "askstring", return_value=name):
            self.panel.start_ppms_field_scan()
        self.ask, self.directory = ask, directory
        return self

    def events(self, path=None):
        path = path or self.panel.ppms_run_folder / f"{self.panel.ppms_run_folder.name}.log"
        return [json.loads(line) for line in Path(path).read_text(encoding="utf-8").splitlines()]

    def rows(self):
        path = self.panel.ppms_run_folder / f"{self.panel.ppms_run_folder.name}.csv"
        with path.open(encoding="utf-8-sig", newline="") as handle:
            return list(csv.DictReader(handle))

    def header(self):
        path = self.panel.ppms_run_folder / f"{self.panel.ppms_run_folder.name}.csv"
        with path.open(encoding="utf-8-sig", newline="") as handle:
            return next(csv.reader(handle))

    def sent_6221(self):
        return [entry for entry in self.rig.log if entry[0] in ("6221", "6221?")]


ZERO = [("6221", "SOUR:WAVE:ABOR"), ("6221", "OUTP OFF"), ("6221?", "OUTP?")]


class RunFolderAndZeroTests(unittest.TestCase):
    def test_a_completed_scan_saves_in_its_run_folder_and_zeroes_the_6221(self):
        page = Page(self).start()
        panel = page.panel
        self.assertFalse(panel.ppms_field_scan_running)
        self.assertEqual(panel.ppms_run_folder, Path(page.folder.name) / "run1")
        self.assertEqual(sorted(p.name for p in panel.ppms_run_folder.iterdir()), ["run1.csv", "run1.log"])
        rows = page.rows()
        self.assertEqual([float(r["6221_current_rms_ma"]) for r in rows], [0.5, 1.0])
        # The 6221 zeroed and read back last, after the last point.
        self.assertEqual(page.sent_6221()[-3:], ZERO)
        self.assertEqual(page.rig.source.output, 0)
        self.assertEqual(panel.ppms_field_scan_status_var.get(), "Completed; 6221 zeroed")
        self.assertEqual(panel.ppms_transport_status_var.get(), "Transport acquisition: 6221 output OFF (read back)")
        self.assertEqual(page.dialogs, [])
        self.assertTrue(panel.ppms_macro_scan_result)
        # The temperature was held, the field never commanded.
        self.assertFalse(any(entry[:2] == ("ppms", "set_field") for entry in page.rig.log))
        events = page.events()
        names = [e["event"] for e in events if e["event"] != "note"]
        self.assertEqual(names, ["run_prepared", "scan_started", "harmonic_pass", "point_saved", "point_saved",
                                 "outputs_zeroed", "run_finished"])
        prepared, finished = events[0], events[-1]
        self.assertEqual(prepared["settings"]["mode"], "Current")
        self.assertEqual(prepared["file_name"], "run1")
        self.assertFalse(prepared["settings"]["field"]["commanded"])
        self.assertEqual(finished["outcome"], "completed")
        self.assertEqual(finished["points_saved"], 2)
        self.assertEqual(finished["outputs"], {"6221": "zeroed"})
        self.assertEqual(finished["status"], "Completed; 6221 zeroed")
        saved = [e for e in events if e["event"] == "point_saved"]
        self.assertEqual([(e["x_column"], e["x"]) for e in saved], [("6221_current_rms_ma", 0.5),
                                                                     ("6221_current_rms_ma", 1.0)])
        self.assertEqual(next(e for e in events if e["event"] == "outputs_zeroed")["6221"], "zeroed")
        # The page's log lines while the scan ran are notes.
        notes = [e["text"] for e in events if e["event"] == "note"]
        self.assertIn("PPMS scan end: 6221 zeroed", notes)
        self.assertTrue(any(text.startswith("Scan temperature segment: 300 K") for text in notes))
        self.assertIsNone(panel.__dict__.get("ppms_run_recorder"), "closed and released")
        # The Yes/No says how every end leaves the outputs.
        question = page.ask.call_args.args[1]
        self.assertIn("At every end (completed, stopped or failed) the 6221 is switched off and read back as off.",
                      question)
        self.assertIn("the field is not commanded (the CSV's target_field_oe is the field read now, 1234 Oe)",
                      question)
        self.assertNotIn("kept", question)

    def test_the_columns_are_the_live_csvs_of_before(self):
        """The run folder's CSV has the columns, in order, that the page's own
        live CSV had (the worker on its old path)."""
        page = Page(self).start()
        old = Rig(self, "Current", None, (0.5, 1.0), zurich=False)
        old.run()
        self.assertEqual(page.header(), old.header())
        self.assertEqual([r["target_field_oe"] for r in page.rows()], ["1234.0", "1234.0"])

    def test_the_stop_button_ends_with_the_6221_zeroed(self):
        page = Page(self)
        rig = page.rig
        # Stop during the second current's settle (the wait of 1 s, the TC).

        def on_wait(seconds):
            if len(page.panel.ppms_field_scan_rows) == 1 and math.isclose(seconds, 1.0):
                page.panel.stop_ppms_field_scan()
        rig.stop.on_wait = on_wait
        page.start()
        panel = page.panel
        self.assertEqual(len(page.rows()), 1)
        self.assertEqual(page.sent_6221()[-3:], ZERO)
        self.assertEqual(rig.source.output, 0)
        self.assertEqual(panel.ppms_field_scan_status_var.get(), "Stopped; 6221 zeroed")
        self.assertEqual(page.dialogs, [])
        self.assertFalse(panel.ppms_macro_scan_result)
        events = page.events()
        names = [e["event"] for e in events if e["event"] != "note"]
        self.assertEqual(names[-4:], ["point_saved", "stop_requested", "outputs_zeroed", "run_finished"])
        self.assertEqual(events[-1]["outcome"], "stopped")
        self.assertTrue(events[-1]["stop_reason"].startswith("Stop"))
        self.assertEqual(next(e for e in events if e["event"] == "stop_requested")["button"], "Stop")

    def test_i_off_ends_with_the_6221_zeroed_and_logged(self):
        page = Page(self)

        def on_wait(seconds):
            if page.panel.ppms_field_scan_rows and not page.panel.ppms_field_scan_stop_event.is_set():
                page.panel.ppms_sources_off()
        page.rig.stop.on_wait = on_wait
        page.start()
        self.assertEqual(page.panel.ppms_field_scan_status_var.get(), "Stopped; 6221 zeroed")
        self.assertEqual(page.sent_6221()[-3:], ZERO)
        events = page.events()
        self.assertEqual(next(e for e in events if e["event"] == "stop_requested")["button"], "I off")
        self.assertTrue(events[-1]["stop_reason"].startswith("I off"))

    def test_a_failure_ends_with_the_6221_zeroed_and_its_dialog(self):
        page = Page(self)
        page.rig.sr830["830_a"].never_locks_at = 317.3
        page.start()
        panel = page.panel
        self.assertEqual(page.rig.source.output, 0)
        self.assertEqual(page.sent_6221()[-3:], ZERO)
        status = panel.ppms_field_scan_status_var.get()
        self.assertTrue(status.startswith("Failed: Connected SR830 failed to lock"), status)
        self.assertEqual(len(page.dialogs), 1)
        title, text = page.dialogs[0]
        self.assertEqual(title, "PPMS Current scan")
        self.assertTrue(text.endswith("\n\nAt the end: 6221 zeroed"), text)
        self.assertEqual(len(page.later), 1, "opened from the Tk event loop")
        events = page.events()
        self.assertEqual([e["event"] for e in events if e["event"] != "note"][-2:], ["outputs_zeroed", "run_finished"])
        self.assertEqual(events[-1]["outcome"], "error")
        self.assertIn("failed to lock", events[-1]["error"])
        self.assertEqual(events[-1]["points_saved"], 0)

    def test_a_6221_that_does_not_read_back_off_is_checked(self):
        page = Page(self)
        source = page.rig.source

        def stuck(command):
            if command == "OUTP OFF" and len(page.panel.ppms_field_scan_rows) == 2:
                source.output = 1
        source.on_write = stuck
        page.start()
        panel = page.panel
        self.assertEqual(panel.ppms_scan_outputs,
                         {"6221": "zero FAILED: the 6221 output did not read back OFF (OUTP? 1)"})
        self.assertEqual(panel.ppms_field_scan_status_var.get(), "Completed; 6221 zero FAILED: check it")
        self.assertEqual(page.dialogs[0][0], "PPMS outputs")
        self.assertIn("The scan completed, but the 6221 was not confirmed at zero: CHECK IT.", page.dialogs[0][1])
        self.assertFalse(panel.ppms_macro_scan_result, "a Macro does not go on")
        self.assertTrue(page.events()[-1]["outputs"]["6221"].startswith("zero FAILED"))

    def test_a_failed_log_write_ends_the_scan_through_its_failure_path(self):
        page = Page(self)
        log = page.rig.log

        def hook(entry):
            recorder = page.panel.__dict__.get("ppms_run_recorder")
            if entry == ("sr830_8?", "SNAP? 1,2,3,4,9") and recorder is not None and recorder.failure is None:
                recorder.failure = OSError("No space left on device")
        log.hook = hook
        page.start()
        status = page.panel.ppms_field_scan_status_var.get()
        # The status line ends at "; " (short_status_error); the dialog has it all.
        self.assertEqual(status, "Failed: The PPMS log could not be written (No space left on device);")
        self.assertTrue(page.dialogs[0][1].startswith(
            "The PPMS log could not be written (No space left on device); the scan stopped"))
        self.assertEqual(page.sent_6221()[-3:], ZERO)
        self.assertEqual(len(page.rows()), 1, "the point read before the check was saved")

    def test_a_cancelled_name_makes_nothing_and_touches_no_output(self):
        for kwargs in (dict(name=None), dict(folder=False), dict(answer=False)):
            with self.subTest(**kwargs):
                page = Page(self).start(**kwargs)
                self.assertEqual(list(Path(page.folder.name).iterdir()), [])
                self.assertFalse(page.panel.ppms_field_scan_running)
                self.assertEqual(page.sent_6221(), [])
                self.assertIsNone(page.panel.__dict__.get("ppms_run_recorder"))

    def test_save_csv_as_opens_in_the_run_folder(self):
        page = Page(self).start()
        with mock.patch.object(app.filedialog, "asksaveasfilename", return_value="") as save:
            page.panel.save_ppms_field_scan_csv()
        self.assertEqual(save.call_args.kwargs["initialdir"], str(page.panel.ppms_run_folder))

    def test_a_second_scan_gets_its_own_folder_and_the_dialog_opens_beside_the_last(self):
        page = Page(self).start()
        first = page.panel.ppms_run_folder
        page.start()
        self.assertEqual(page.panel.ppms_run_folder, first.parent / "run1_01")
        self.assertEqual(page.directory.call_args.kwargs["initialdir"], str(first.parent))
        self.assertTrue((first / "run1.csv").is_file(), "nothing overwritten")

    def test_the_start_reads_the_sr830_tc_choice_for_the_worker(self):
        page = Page(self)
        page.panel.ppms_sr830_time_constant_var.set("3 s")
        page.start()
        self.assertEqual(page.panel.ppms_scan_sr830_tc_code, app.STFMR_LOCKIN_SR830_TIME_CONSTANTS["3 s"])


class FieldScanEndTests(unittest.TestCase):
    """A field scan's own field rules are unchanged; the 6221 is zeroed too."""

    def harness(self, refuse_zero=False):
        import test_ppms_nested as nested
        case = nested.NestedTests()
        obj, motor, settings, events = case.harness()
        del obj._ppms_zero_field_after_interrupted_scan       # the real one
        remote = obj.ppms_environment_controller
        send = remote.set_field          # the harness's: it logs and moves the field

        def set_field(target, *args):
            if refuse_zero and target == 0.0:
                events.append(("field", target))
                raise RuntimeError("MultiVu refused")
            send(target, *args)
        remote.set_field = set_field
        settings.update(scan_mode="Field", nested=dict(angle=(26.,), temperature=(300.,)))

        def run():
            clock = iter(range(10000))
            with mock.patch.object(app, 'PPMSMotorDebug', motor), \
                    mock.patch.object(app.time, 'monotonic', lambda: next(clock)):
                obj._ppms_nested_scan_worker([(1, 1, 1, 100.)], 100., 0., 1, settings)
        case.run_worker = lambda *_args: run()
        return obj, motor, settings, events, case

    def test_a_field_failure_zeroes_the_field_once(self):
        obj, motor, settings, events, case = self.harness()

        def fail(*args):
            raise app.PPMSFieldScanError("PPMS timed out waiting for +100 Oe to settle")
        obj._ppms_field_scan_worker = fail
        with self.assertRaisesRegex(app.PPMSFieldScanError, "^PPMS timed out waiting for \\+100 Oe to settle$"):
            case.run_worker(obj, motor, settings)
        self.assertEqual(events.count(("field", 0.0)), 1)
        self.assertEqual(obj.ppms_scan_field_note, "field ramping to 0 Oe")

    def test_a_refused_field_zero_is_in_the_error_and_the_status(self):
        obj, motor, settings, events, case = self.harness(refuse_zero=True)
        # The real inner worker: its field command fails, and both workers
        # meet the failure.
        obj._ppms_field_scan_worker = types.MethodType(app.CombinedControlApp._ppms_field_scan_worker, obj)
        obj.ppms_field_scan_rows, obj.ppms_field_scan_total, obj.ppms_field_scan_started_at = [], 1, 0.
        obj.ppms_last_readback = None
        obj._configure_ppms_transport_scan = lambda config: None
        remote = obj.ppms_environment_controller
        refuse = remote.set_field

        def set_field(target, *args):
            if target != 0.0:
                raise RuntimeError("busy")
            refuse(target, *args)
        remote.set_field = set_field
        with self.assertRaises(app.PPMSFieldScanError) as caught:
            case.run_worker(obj, motor, settings)
        self.assertTrue(str(caught.exception).endswith("; field NOT zeroed: check MultiVu"), str(caught.exception))
        self.assertEqual(str(caught.exception).count("field NOT zeroed"), 1, "the zero is tried once")
        # The finish keeps it on the status line, which ends at "; ".
        obj.ppms_field_scan_status_var, obj.ppms_field_scan_eta_var = Var(""), Var("")
        obj.ppms_scan_outputs = {"6221": "zeroed"}
        dialogs = []
        obj.root = types.SimpleNamespace(after=lambda _ms, show: show())
        with mock.patch.object(app.messagebox, "showerror", side_effect=lambda *a, **k: dialogs.append(a)):
            obj._report_ppms_scan_finished("Field", False, caught.exception)
        self.assertTrue(obj.ppms_field_scan_status_var.get().startswith("Failed: Field set failed"))
        self.assertTrue(obj.ppms_field_scan_status_var.get().endswith("; field NOT zeroed: check MultiVu"))
        self.assertIn("field NOT zeroed: check MultiVu", dialogs[0][1])
        self.assertIn("At the end: 6221 zeroed", dialogs[0][1])

    def test_a_stopped_field_scan_says_what_its_field_did(self):
        obj = app.CombinedControlApp.__new__(app.CombinedControlApp)
        obj.closing, obj.thread_log = False, mock.Mock()
        obj.root = types.SimpleNamespace(after=lambda _ms, show: show())
        obj.ppms_field_scan_status_var, obj.ppms_field_scan_eta_var = Var(""), Var("")
        obj.ppms_scan_outputs = {"6221": "zeroed"}
        for note, expected in (("", "Stopped; 6221 zeroed; field kept at its setpoint"),
                               ("field ramping to 0 Oe", "Stopped; 6221 zeroed; field ramping to 0 Oe"),
                               (app.PPMS_FIELD_NOT_ZEROED_TEXT,
                                "Stopped; 6221 zeroed; field NOT zeroed: check MultiVu")):
            with self.subTest(note=note):
                obj.ppms_scan_field_note = note
                obj._report_ppms_scan_finished("Field", True, None)
                self.assertEqual(obj.ppms_field_scan_status_var.get(), expected)
        obj.ppms_scan_field_note = ""
        obj.ppms_zero_persist_completed = True
        obj._report_ppms_scan_finished("Field", False, None)
        self.assertEqual(obj.ppms_field_scan_status_var.get(),
                         "Completed; 6221 zeroed; field zeroed and confirmed Persistent")

    def test_the_6221_is_zeroed_before_a_queued_zero_and_persist(self):
        obj = app.CombinedControlApp.__new__(app.CombinedControlApp)
        obj.closing = False
        obj.ppms_field_scan_stop_event = threading.Event()
        obj.ppms_zero_persist_pending = True
        order = []
        obj._ppms_nested_scan_worker = lambda *args: order.append("scan")
        obj._ppms_zero_outputs_after_scan = lambda: order.append("6221 zeroed") or {"6221": "zeroed"}
        obj.post_ui = lambda f: None
        remote = obj.ppms_environment_controller = mock.Mock()
        remote.set_field.side_effect = lambda *args: order.append(("field", args[3]))
        remote.read_environment.side_effect = [{'field_oe': 0., 'field_status': 'StableDriven'},
                                               {'field_oe': 0., 'field_status': 'StablePersistent'}]
        obj._ppms_scan_with_final_field()
        self.assertEqual(order, ["scan", "6221 zeroed", ("field", 1), ("field", 0)])
        self.assertTrue(obj.ppms_zero_persist_completed)


class ZeroPersistButtonTests(unittest.TestCase):
    """P5: queued only while a scan runs; a second click cancels; Start
    clears it."""

    def test_the_button(self):
        panel, _ttk = build_ppms_panel()
        button = panel.ppms_zero_persist_button
        self.assertEqual(button.options["state"], "disabled", "idle: not usable")
        panel._queue_ppms_zero_persist()
        self.assertFalse(getattr(panel, "ppms_zero_persist_pending", False), "nothing queued while idle")
        panel.ppms_field_scan_running = True
        panel._update_ppms_control_ui()
        self.assertEqual(button.options["state"], "normal")
        panel._queue_ppms_zero_persist()
        self.assertTrue(panel.ppms_zero_persist_pending)
        self.assertEqual(button.options["text"], "Queued: zero + persist")
        panel._queue_ppms_zero_persist()
        self.assertFalse(panel.ppms_zero_persist_pending, "a second click cancels")
        self.assertEqual(button.options["text"], "Zero field + persist after scan")
        self.assertEqual(panel.ppms_field_scan_status_var.get(), "Zero field + persist after this scan: cancelled")
        panel.ppms_field_scan_running = False
        panel._update_ppms_control_ui()
        self.assertEqual(button.options["state"], "disabled")

    def test_start_clears_one_queued_before(self):
        page = Page(self)
        page.panel.ppms_zero_persist_pending = True     # left from before (the old idle click)
        page.start()
        self.assertFalse(page.panel.ppms_zero_persist_pending)
        self.assertFalse(any(entry[:2] == ("ppms", "set_field") for entry in page.rig.log),
                         "no zero + persist after this scan")


class CurrentTemperatureTests(unittest.TestCase):
    """P1: each temperature block's ramp runs at its first current, and that
    first point settles."""

    def test_the_6221_returns_to_the_first_current_before_each_ramp(self):
        import test_ppms_nested as nested
        case = nested.NestedTests()
        obj, motor, settings, events = case.harness()
        settings.update(scan_mode='Current+Temperature', nested=dict(angle=(26.,), temperature=(300., 295.)),
                        currents=(1., 2., 3.))
        remote = obj.ppms_environment_controller
        set_temperature = remote.set_temperature
        remote.set_temperature = lambda target, *args: (events.append(('temperature', target)),
                                                         set_temperature(target, *args))
        points = []
        obj._ppms_field_scan_worker = lambda seq, rate, stable, approach, config: points.append(
            (config['current_rms_ma'], obj.__dict__.pop('ppms_source_fresh', False)))
        case.run_worker(obj, motor, settings)
        sources = [e for e in events if e[0] in ('configure', 'amplitude', 'temperature')]
        self.assertEqual(sources, [('temperature', 300.), ('configure', 1.), ('amplitude', 2.), ('amplitude', 3.),
                                   ('amplitude', 1.), ('temperature', 295.), ('amplitude', 2.), ('amplitude', 3.)])
        self.assertEqual(points, [(1., True), (2., False), (3., False), (1., True), (2., False), (3., False)])


class AutorangeTests(unittest.TestCase):
    """P2 (shared with SHV): a range that overloaded is a floor until the
    next pass, and only a point's first reading steps a range down."""

    def sr830(self, index=13):
        obj = app.CombinedControlApp.__new__(app.CombinedControlApp)
        state = {"SENS": index}
        instrument = mock.Mock()
        instrument.write.side_effect = lambda command: state.update(SENS=int(command.split()[1]))
        instrument.query.side_effect = lambda command: str(state["SENS"])
        obj.ppms_instrument_controllers = {"830_a": types.SimpleNamespace(
            instrument=instrument, lock=threading.RLock(), label="SR830-1")}
        obj._ppms_query_numbers = lambda key, command, n: ((state["SENS"],) if command == "SENS?" else (10,))
        obj._post_sr830_range_label = mock.Mock()
        return obj, state

    def test_no_flip_back_to_a_range_that_overloaded(self):
        obj, state = self.sr830()
        small = 0.01 * app.NONLOCAL_SR830_FULL_SCALE_V[13]
        self.assertEqual(obj._ppms_autorange_sr830("830_a", 0., 4), 5.)    # overload: 13 -> 14
        self.assertEqual(state["SENS"], 14)
        for _ in range(20):
            obj._ppms_autorange_sr830("830_a", small, 0)
        self.assertEqual(state["SENS"], 14, "never back down to the range that overloaded")
        obj._ppms_clear_sr830_range_floor()
        for _ in range(3):
            obj._ppms_autorange_sr830("830_a", small, 0)
        self.assertEqual(state["SENS"], 13, "after the floor is cleared it steps down again")

    def test_later_readings_of_a_point_do_not_step_down_or_count(self):
        obj, state = self.sr830()
        small = 0.01 * app.NONLOCAL_SR830_FULL_SCALE_V[13]
        for _ in range(10):
            obj._ppms_autorange_sr830("830_a", small, 0, allow_down=False)
        self.assertEqual(state["SENS"], 13)
        self.assertEqual(obj.ppms_sr830_range_states["830_a"].low_range_count, 0)
        # An overload still ranges up on a later reading.
        obj._ppms_autorange_sr830("830_a", 0., 4, allow_down=False)
        self.assertEqual(state["SENS"], 14)

    def test_only_the_first_reading_of_a_point_may_step_down(self):
        obj = app.CombinedControlApp.__new__(app.CombinedControlApp)
        obj.closing = False
        obj.ppms_field_scan_stop_event = mock.Mock(is_set=mock.Mock(return_value=False),
                                                   wait=mock.Mock(return_value=False))
        flags = []
        good = dict(sr830_8_x_v=1., sr830_8_y_v=0., sr830_8_frequency_hz=317.3, sr830_8_reference_valid=True)

        def read(frequency, lockins, allow_down=True):
            flags.append(allow_down)
            return dict(good, range_settle_s=5. if len(flags) == 2 else 0.)
        obj._read_ppms_transport_sample = read
        with mock.patch.object(app.time, "monotonic", side_effect=[0., 0., .5, 5., 5., 5.5, 6.5, 6.5]):
            obj._average_ppms_transport_sample(317.3, ("830_a",), 1.)
        self.assertEqual(flags[0], True)
        self.assertFalse(any(flags[1:]), "not after a range change's restart either")

    def test_the_floor_ends_with_the_pass(self):
        obj, state = self.sr830()
        obj._ppms_autorange_sr830("830_a", 0., 4)
        self.assertEqual(obj.ppms_sr830_range_states["830_a"].overload_floor, 14)
        obj.post_ui = lambda callback: None
        obj._average_ppms_transport_sample = lambda f, l, d: dict(sr830_8_x_v=1., sr830_8_y_v=0.)
        obj._acquire_ppms_first_point(317.3, ("830_a",), 1.)
        self.assertIsNone(obj.ppms_sr830_range_states["830_a"].overload_floor)


class SettleWithoutTkTests(unittest.TestCase):
    """P6: the worker's wait reads the 'SR830 TC' choice Start read."""

    def test_the_worker_never_reads_the_choice(self):
        obj = app.CombinedControlApp.__new__(app.CombinedControlApp)
        obj.closing = False
        obj.thread_log, obj.post_ui = mock.Mock(), lambda callback: None
        obj.ppms_sr830_time_constant_var = mock.Mock(get=mock.Mock(side_effect=AssertionError("Tk on the worker")))
        obj.ppms_scan_sr830_tc_code = app.STFMR_LOCKIN_SR830_TIME_CONSTANTS["3 s"]
        self.assertEqual(obj._ppms_point_settle(0.5, ("830_a",)), (3.0, 3.0))


class SetIZurichTests(unittest.TestCase):
    """P7: the PPMS page's Set I never configures the Zurich from an older
    Start's copied settings."""

    def test_set_i_sets_the_6221_alone(self):
        panel, _ttk = build_ppms_panel()
        panel.zurich_scan_enabled, panel.zurich_scan_configured = True, False   # a Start that returned early
        panel.zurich_scan_harmonic, panel.zurich_scan_reference, panel.zurich_scan_tau = "1", "Trigger In 1", 0.1
        panel._require_ppms_control_ready = mock.Mock()
        panel._ppms_direct_transport_settings = mock.Mock(return_value=dict(current_rms_ma=1.0, frequency_hz=317.3))
        commands = []
        panel._run_ppms_command = lambda label, operation, **options: commands.append(operation)
        panel._ppms_set_current_direct()
        configure = mock.Mock()
        with mock.patch.object(app.CombinedControlApp, "_configure_ppms_transport_scan", configure):
            commands[0]()
        configure.assert_called_once()
        panel.zurich_controller.configure_mfli.assert_not_called()


class FieldReadAtStartTests(unittest.TestCase):
    """P4: modes that never command the field record the field read at Start
    and count no field ramp; P8: field ramps per harmonic pass."""

    def test_target_field_is_the_field_read_at_start(self):
        page = Page(self).start()
        self.assertEqual([float(r["target_field_oe"]) for r in page.rows()], [1234.0, 1234.0])
        settings = page.events()[0]["settings"]
        self.assertEqual(settings["field"]["target_field_oe"], 1234.0)

    def test_no_reading_refuses(self):
        page = Page(self)
        page.panel.ppms_last_readback = None
        page.start()
        self.assertEqual(page.dialogs[0][0], "PPMS scan parameters")
        self.assertIn("No PPMS field reading yet", page.dialogs[0][1])
        self.assertFalse(page.panel.ppms_field_scan_running)

    def test_the_estimate_has_no_phantom_field_ramp(self):
        estimates = []
        for box in ("50000", "1234"):
            page = Page(self)
            page.panel.ppms_field_target_var.set(box)
            page.start(answer=False)
            estimates.append(page.ask.call_args.args[1].split("Estimated duration: ")[1].split("\n")[0])
        self.assertEqual(estimates[0], estimates[1], "the H Oe box is not a ramp")


class MacroScanTests(unittest.TestCase):
    """The next Macro line runs from the Tk event loop, so a SCAN line's
    dialogs never open inside drain_ui_queue (README rule, 2026-10-14)."""

    def test_the_next_line_after_a_finished_command_waits_for_the_event_loop(self):
        panel = app.CombinedControlApp.__new__(app.CombinedControlApp)
        panel.closing = False
        panel.ppms_macro_stop = threading.Event()
        panel.ppms_macro_commands = iter(app.parse_ppms_macro("WAIT 0\nSCAN"))
        panel.ppms_macro_status, panel.ppms_macro_editor = mock.Mock(), mock.Mock()
        panel.thread_log = mock.Mock()
        panel.ppms_environment_controller = mock.Mock()
        from test_ppms_two_panels import ImmediateExecutor
        panel.ppms_executor = ImmediateExecutor()
        panel._set_ppms_environment_ui = mock.Mock()
        panel._ppms_macro_finish = mock.Mock()
        panel.ppms_field_scan_running = False
        queued = []
        panel.root = types.SimpleNamespace(after=lambda _ms, callback: queued.append(callback))
        panel.post_ui = lambda callback: callback()
        panel.start_ppms_field_scan = mock.Mock()
        panel._ppms_macro_next()
        panel.start_ppms_field_scan.assert_not_called()
        self.assertEqual(len(queued), 1)
        queued.pop()()
        panel.start_ppms_field_scan.assert_called_once()


class ProgramCloseTests(unittest.TestCase):
    def test_the_worker_closes_the_run_log_when_the_program_closes(self):
        page = Page(self)
        panel = page.panel

        def on_wait(seconds):
            if panel.ppms_field_scan_rows and not panel.closing:
                panel.parent_app.closing = True
                recorder = panel.__dict__.get("ppms_run_recorder")
                recorder.event("stop_requested", button="the program closing")
                panel.ppms_field_scan_stop_event.set()
        page.rig.stop.on_wait = on_wait
        page.panel.parent_app.closing = False
        page.start()
        events = page.events(next(Path(page.folder.name).iterdir()) / "run1.log")
        self.assertEqual(events[-1]["event"], "run_finished")
        self.assertEqual(events[-1]["outcome"], "stopped")
        self.assertEqual(events[-1]["stop_reason"], "the program closing")
        self.assertEqual(events[-1]["outputs"], {"6221": "zeroed"})
        self.assertEqual(page.rig.source.output, 0)


if __name__ == "__main__":
    unittest.main()
