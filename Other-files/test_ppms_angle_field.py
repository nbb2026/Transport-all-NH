"""PPMS page, the Angle+Field scan (user, 2026-10-18: "does PPMS module have
angle+field scan? if not, add it").

The field is the outer loop: at each outer field the field is commanded as a
Field scan commands it (Driven, the rate box, its stable reading awaited),
then the whole angle sweep runs with the rotator as Angle+Temperature sweeps
it at each temperature, and the first angle after each field change takes the
first-point agreeing windows.  The temperature is held as in an Angle scan.

No Tk window and no instrument: the real PPMSPanel on stand-in widgets
(test_ppms_sr830_tc), the fake 6221 and SR830s of test_ppms_frequency_scan, a
fake magnet and rotator here, every dialog mocked, and a clock that moves only
when the scan waits.  The real Start, workers, zeroing and finish run.
"""
import csv
import inspect
import math
import tempfile
import threading
import types
import unittest
from pathlib import Path
from unittest import mock

from test_nonlocal_keithley import Var
from test_ppms_frequency_scan import PlotTests, Rig
from test_ppms_motor_debug import app
from test_ppms_run_save_zero import ZERO, Page
from test_ppms_sr830_tc import build_ppms_panel


class Magnet:
    """The PPMS magnet: a field command ramps for two readings ("Charging"),
    then reads the target, StableDriven (or StablePersistent after a
    Persistent command).  ``refuse`` makes a command to that field fail;
    ``on_set`` sees each command as it is sent."""

    def __init__(self, log, field=1234.0):
        self.log, self.field, self.target = log, field, field
        self.mode, self.pending, self.status = 0, 0, "StablePersistent"
        self.refuse, self.on_set = None, None

    def set_field(self, target, rate, approach, mode):
        self.log.append(("ppms", "set_field", float(target), int(mode)))
        if self.on_set is not None:
            self.on_set(float(target))
        if self.refuse is not None and math.isclose(target, self.refuse):
            raise RuntimeError("MultiVu busy")
        self.target, self.mode, self.pending = float(target), int(mode), 2

    def read(self):
        if self.pending:
            self.pending -= 1
            self.field += (self.target - self.field) / 2
            status = "Charging"
        else:
            self.field = self.target
            status = "StablePersistent" if self.mode == 0 else "StableDriven"
        return dict(field_oe=self.field, field_status=status, temperature_k=300.0, temperature_status="Stable")


def rotator(log, start=26.0):
    """The PPMS rotator (PPMSMotorDebug's stand-in): each MOVE is logged and
    arrives at once."""
    state = {"angle": float(start)}

    class Rotator:
        def __init__(self, _active):
            self.on_read = None

        def read(self):
            return {"angle_deg": state["angle"], "status": "AtTarget"}

        def move_and_verify(self, target, slowdown, cancelled=lambda: False, index_target=None):
            if cancelled():
                raise app.PPMSScanCancelled("Cancelled before moving")
            log.append(("motor", round(float(target), 6)))
            state["angle"] = float(target)
            return {"angle_deg": float(target), "status": "AtTarget"}
    Rotator.state = state
    return Rotator


class AngleFieldPage(Page):
    """The built PPMS page of test_ppms_run_save_zero, in Angle+Field: the
    angles 26 and 27° on the main row, the outer fields 100 and 200 Oe, the
    magnet and rotator fakes, the field read at 1234 Oe, Persistent."""

    def __init__(self, test, main_row=("26", "27", "1"), outer=("100", "200", "100")):
        super().__init__(test, "Angle+Field", main_row)
        panel, log = self.panel, self.rig.log
        self.magnet = Magnet(log)
        remote = panel.ppms_environment_controller
        remote.set_field = self.magnet.set_field
        remote.read_environment = self.magnet.read
        panel._read_ppms_live = self.magnet.read
        panel.ppms_field_rate_var.set("100")
        for var, value in zip(panel.ppms_outer_field_scan_range, outer):
            var.set(value)
        self.motor = rotator(log)

    def start(self, name="run1", answer=True, folder=True):
        def error(title, text, **_options):
            self.dialogs.append((title, text))
        with mock.patch.object(app.time, "monotonic", self.rig.clock), \
                mock.patch.object(app.time, "sleep", lambda _s: None), \
                mock.patch.object(app, "PPMSMotorDebug", self.motor), \
                mock.patch.object(app.messagebox, "askyesno", return_value=answer) as ask, \
                mock.patch.object(app.messagebox, "showerror", side_effect=error), \
                mock.patch.object(app.filedialog, "askdirectory",
                                  return_value=self.folder.name if folder else ""), \
                mock.patch.object(app.simpledialog, "askstring", return_value=name):
            self.panel.start_ppms_field_scan()
        self.ask = ask
        return self

    def field_commands(self):
        return [entry[2:] for entry in self.rig.log if entry[:2] == ("ppms", "set_field")]

    def names(self):
        return [e["event"] for e in self.events() if e["event"] != "note"]


class ModeRegistrationTests(unittest.TestCase):
    """Every mode-keyed table and branch knows Angle+Field, as the frequency
    modes' test checks theirs."""

    def test_the_mode_list_and_its_tables(self):
        self.assertEqual(app.PPMS_SCAN_MODES, (
            "Temperature", "Field", "Angle", "Current", "Field+Temperature", "Angle+Temperature",
            "Current+Temperature", "Angle+Field", "Frequency", "Frequency+Current"))
        self.assertEqual(set(app.PPMS_SCAN_PLOT_AXES), set(app.PPMS_SCAN_MODES))
        self.assertEqual(app.PPMS_SCAN_PLOT_AXES["Angle+Field"], "angle")
        self.assertEqual(app.PPMS_FIELD_COMMANDING_MODES, ("Field", "Field+Temperature", "Angle+Field"))
        page = app.CombinedControlApp.__new__(app.CombinedControlApp)
        defaults = page._ppms_scan_mode_defaults()
        self.assertEqual(set(defaults), set(app.PPMS_SCAN_MODES))
        self.assertEqual(defaults["Angle+Field"], defaults["Angle"])
        self.assertEqual(defaults["Angle+Field"], ("0", "360", "5", "°"))
        self.assertEqual(app.CombinedControlApp._ppms_acquisition_default("Angle+Field"), "1")
        # The Mode list keeps its width: its longest entry is unchanged.
        self.assertEqual(max(app.PPMS_SCAN_MODES, key=len), "Current+Temperature")

    def test_the_angle_and_field_boxes_are_locked(self):
        page = app.CombinedControlApp.__new__(app.CombinedControlApp)
        page.ppms_scan_target_widgets = {key: [mock.Mock()] for key in
                                         ("Temperature", "Field", "Angle", "Current", "Frequency")}
        page.ppms_scan_step_entry = mock.Mock()
        page.ppms_simple_mode = Var("Angle+Field")
        page._update_ppms_scan_target_widgets(True)
        for parameter, widgets in page.ppms_scan_target_widgets.items():
            widgets[0].configure.assert_called_with(
                state="disabled" if parameter in ("Angle", "Field") else "normal")
        page.ppms_scan_step_entry.configure.assert_not_called()

    def test_every_field_branch_knows_the_mode(self):
        """The endings that follow the Field scan's rules read the one
        table: the worker's close, the finish, the nested and point workers
        and the page's close."""
        for method in (app.CombinedControlApp._ppms_scan_with_final_field,
                       app.CombinedControlApp._report_ppms_scan_finished,
                       app.CombinedControlApp._ppms_nested_scan_worker,
                       app.CombinedControlApp._ppms_field_scan_worker,
                       app.CombinedControlApp.safe_close_ppms,
                       app.CombinedControlApp.start_ppms_field_scan):
            with self.subTest(method=method.__name__):
                self.assertIn("PPMS_FIELD_COMMANDING_MODES", inspect.getsource(method))
        obj = app.CombinedControlApp.__new__(app.CombinedControlApp)
        obj.closing, obj.thread_log = False, mock.Mock()
        obj.root = types.SimpleNamespace(after=lambda _ms, show: show())
        obj.ppms_field_scan_status_var, obj.ppms_field_scan_eta_var = Var(""), Var("")
        obj.ppms_scan_outputs = {"6221": "zeroed"}
        obj.ppms_scan_field_note = ""
        obj._report_ppms_scan_finished("Angle+Field", True, None)
        self.assertEqual(obj.ppms_field_scan_status_var.get(), "Stopped; 6221 zeroed; field kept at its setpoint")

    def test_the_outer_field_row(self):
        """Built on stand-in widgets: "Outer field Oe:" with Start / End /
        Step at the page's field range defaults, hidden, and shown at row 4
        (where the outer temperature row sits) only in Angle+Field."""
        panel, _ttk = build_ppms_panel()
        frame = panel.ppms_outer_field_range_frame
        labels = [w.options.get("text") for w in frame.children if w.kind == "Label"]
        self.assertEqual(labels, ["Outer field Oe:", "Start", "End", "Step"])
        entries = [w for w in frame.children if w.kind == "Entry"]
        self.assertEqual([w.options["textvariable"] for w in entries], panel.ppms_outer_field_scan_range)
        self.assertTrue(all(w in panel.ppms_control_widgets for w in entries), "locked during a scan")
        self.assertEqual([v.get() for v in panel.ppms_outer_field_scan_range], list(panel.ppms_default_field_range))
        self.assertIsNone(frame.grid_options, "hidden at start")
        change_mode = panel.ppms_scan_mode_combo.bindings["<<ComboboxSelected>>"]
        temperature_frame = next(w for w in panel.ppms_scan_mode_combo.master.children
                                 if w.kind == "Frame" and any(c.options.get("text") == "Outer temperature K:"
                                                              for c in w.children))
        panel.ppms_simple_mode.set("Angle+Field")
        change_mode()
        self.assertEqual(frame.grid_options["row"], 4)
        self.assertEqual(frame.grid_options["sticky"], "w")
        self.assertIsNone(temperature_frame.grid_options)
        self.assertIsNone(panel.ppms_frequency_range_frame.grid_options)
        self.assertEqual([v.get() for v in panel.ppms_simple_range], ["0", "360", "5"])
        self.assertEqual(panel.ppms_simple_unit.get(), "°")
        self.assertEqual(panel.ppms_plot_axis, "angle")
        for mode in ("Angle+Temperature", "Field", "Frequency+Current"):
            with self.subTest(mode=mode):
                panel.ppms_simple_mode.set(mode)
                change_mode()
                self.assertIsNone(frame.grid_options)


def launcher(test, main_row=("0", "10", "5"), outer=("0", "2000", "1000"), rate="100", confirm=True,
             readback=None):
    """The PPMS Start (no window) in Angle+Field: returns (page, Yes/No text,
    sequence, settings, estimate s), or the error text."""
    page = app.CombinedControlApp.__new__(app.CombinedControlApp)
    page.ppms_field_scan_running = page.ppms_environment_busy = False
    page.closing = False
    page._require_ppms_control_ready = mock.Mock()
    page.ppms_simple_mode = Var("Angle+Field")
    page.ppms_simple_range = [Var(value) for value in main_row]
    page.ppms_temperature_scan_range = [Var(value) for value in ("300", "290", "5")]
    page.ppms_frequency_scan_range = [Var(value) for value in ("7", "97")]
    page.ppms_outer_field_scan_range = [Var(value) for value in outer]
    boxes = dict(ppms_acquisition_time_var="1", ppms_field_target_var="50000", ppms_temperature_target_var="300",
                 ppms_motor_target_var="26", ppms_field_scan_stable_var="0", ppms_field_rate_var=rate,
                 ppms_field_approach_var="No overshoot", ppms_transport_current_var="1",
                 ppms_transport_frequency_var="317.3", ppms_transport_compliance_var="50",
                 ppms_motor_confirm_var=confirm, ppms_temperature_rate_var="10",
                 ppms_temperature_approach_var="No overshoot", ppms_motor_speed_var="14")
    for name, value in boxes.items():
        setattr(page, name, Var(value))
    for key, value in zip(app.PPMS_SR830_KEYS, ("1", "2", "3")):
        setattr(page, app.PPMS_SR830_HARMONIC_ATTRS[key], Var(value))
    page.ppms_environment_controller = types.SimpleNamespace(backend=app.PPMS_QDINSTRUMENT_BACKEND)
    page.ppms_instrument_controllers = {
        key: types.SimpleNamespace(connected=True, resource_name=f"GPIB0::{address}::INSTR")
        for key, address in (("6221", 30), ("830_a", 8), ("830_b", 9), ("830_c", 10))}
    page.ppms_last_readback = readback or dict(temperature_k=300.0, field_oe=1234.0, angle_deg=26.0)
    page.ppms_field_scan_stop_event = threading.Event()
    for name in ("_redraw_ppms_transport_plot", "_update_ppms_control_ui", "ppms_executor",
                 "ppms_field_scan_progress", "ppms_field_scan_eta_var", "ppms_transport_status_var"):
        setattr(page, name, mock.Mock())
    page.ppms_field_scan_status_var = Var("")
    page.root = None
    page.thread_log = mock.Mock()
    estimates = []
    format_duration = page._format_scan_duration
    page._format_scan_duration = lambda seconds: estimates.append(seconds) or format_duration(seconds)
    with tempfile.TemporaryDirectory() as folder, \
            mock.patch.object(app.simpledialog, "askstring", return_value=""), \
            mock.patch.object(app.messagebox, "askyesno", return_value=True) as ask, \
            mock.patch.object(app.filedialog, "askdirectory", return_value=folder), \
            mock.patch.object(app.messagebox, "showerror") as error:
        page.start_ppms_field_scan()
        if error.called:
            page.ppms_executor.submit.assert_not_called()
            return error.call_args.args[1]
        test.assertTrue(page.ppms_field_scan_running)
        # The run log as Start left it (run_prepared), read before the
        # folder goes.
        page.run_log_text = page.ppms_run_recorder.log_path.read_text(encoding="utf-8")
    args = page.ppms_executor.submit.call_args.args
    return page, ask.call_args.args[1], args[1], args[-1], estimates[0]


class StartTests(unittest.TestCase):
    def test_the_outer_plan(self):
        page, question, sequence, settings, _estimate = launcher(self)
        self.assertEqual(sequence, ((1, 1, 3, 0.0), (1, 2, 3, 1000.0), (1, 3, 3, 2000.0)),
                         "one entry per outer field, one way")
        self.assertEqual(settings["outer_fields"], (0.0, 1000.0, 2000.0))
        self.assertEqual(settings["nested"], {"angle": (0.0, 5.0, 10.0), "temperature": (300.0,)})
        self.assertEqual(settings["scan_mode"], "Angle+Field")
        self.assertEqual(settings["currents"], (1.0,))
        self.assertEqual(page.ppms_field_scan_total, 9)
        self.assertEqual(page.ppms_plot_axis, "angle")
        self.assertEqual(page.ppms_active_scan_mode, "Angle+Field")
        self.assertTrue(page.ppms_field_scan_status_var.get().startswith(
            "Angle+Field scan: 3 fields x 3 angles; 9 points, settle 1 s; estimated about "))
        # The Yes/No: the outer steps and their ramps, the angles per field
        # and the rotator's moves, what is held, how every end leaves things.
        self.assertIn("Scan mode: Angle+Field\nOuter fields: 3 steps, 0 to 2000 Oe (Step 1000 Oe), "
                      "the whole angle sweep at each\n", question)
        self.assertIn("At each field: set Driven at 100 Oe/s (about 10 s per step; about 13 s to the first field "
                      "from 1234 Oe; 33 s of ramps in all), its stable reading awaited, then the angles; the first "
                      "angle after each field change takes agreeing windows\n", question)
        self.assertIn("Angles per field: 3, 0 to 10° (Step 5°); rotator: 12 moves of at most 0.9° per sweep and "
                      "12 back to 0° before each later field, 89 moves in all (planned at 1 s each)\n", question)
        self.assertIn("Held as in an Angle scan: 300 K (T K)\n", question)
        self.assertIn("Measurement points: 9\n", question)
        self.assertIn("the field stays at its setpoint after Stop, and is set to 0 Oe after I off, a field failure "
                      "or closing the program", question)
        self.assertIn("At every end (completed, stopped or failed) the 6221 is switched off and read back as off.",
                      question)
        # The run log's settings: the field is commanded, the outer fields listed.
        log = page.run_log_text
        self.assertIn('"commanded": true', log)
        self.assertIn('"sequence_oe": [0.0, 1000.0, 2000.0]', log)
        self.assertIn('"outer_fields": [0.0, 1000.0, 2000.0]', log)

    def test_the_estimate(self):
        """3 fields x 3 angles at 1 s settle (1 x the 1 s TC) and Acquire
        1 s: 9 x 2 s of points, 6 s per point, 12 s per angle block (as
        Angle+Temperature counts per temperature), the ramps at 100 Oe/s
        from 1234 Oe (12.34 + 10 + 10 s), and 89 rotator moves at 1 s:
        26 -> 0° (29), 0 -> 5 -> 10° (6 + 6) at each of the 3 fields, and
        10 -> 0° (12) before the 2 later fields."""
        _page, _question, _sequence, _settings, estimate = launcher(self)
        self.assertAlmostEqual(estimate, 9 * 2 + 6 * 9 + 12 * 9 + 32.34 + 89 * app.PPMS_ROTATOR_MOVE_ALLOWANCE_S)
        self.assertEqual(app.PPMS_ROTATOR_MOVE_ALLOWANCE_S, 1.0)

    def test_the_limits(self):
        limit = f"{app.PPMS_FIELD_LIMIT_OE:g}"
        for kwargs, expected in (
                (dict(outer=("0", "100000", "50000")), f"Outer field 100000 Oe: PPMS target field must be within ±{limit} Oe"),
                (dict(outer=("-90000", "0", "90000")), "Outer field -90000 Oe"),
                (dict(outer=("0", "", "100")), "Please fill in outer field end (Oe)"),
                (dict(outer=("0", "1000", "0")), "the step must be greater than 0"),
                (dict(outer=("0", "abc", "100")), "outer field end (Oe) must be a number"),
                (dict(rate="150"), "field ramp rate must be greater than 0 and below 150 Oe/s"),
                (dict(main_row=("0", "400", "10")), "Angle range is limited to −370 to 370°"),
                (dict(confirm=False), "Angle scans need QDInstrument and the motor-specific safety confirmation")):
            with self.subTest(**kwargs):
                message = launcher(self, **kwargs)
                self.assertIsInstance(message, str)
                self.assertIn(expected, message)

    def test_a_single_field_and_the_field_box_not_read(self):
        page, question, sequence, settings, _estimate = launcher(self, outer=("500", "500", "100"))
        self.assertEqual(sequence, ((1, 1, 1, 500.0),))
        self.assertIn("Outer fields: 1 steps, 500 to 500 Oe, the whole angle sweep at each\n", question)
        self.assertNotIn("50000", question, "the H Oe box is not used")


class SequenceTests(unittest.TestCase):
    """The real Start and workers on the fake rack: per field, the field
    command, its stable reading, then the angle sweep with its moves and
    reads; the field outer."""

    def test_the_call_order_per_field(self):
        page = AngleFieldPage(self).start()
        log = page.rig.log
        self.assertEqual(page.dialogs, [])
        self.assertEqual(page.panel.ppms_field_scan_status_var.get(), "Completed; 6221 zeroed")
        # Only the outer fields are commanded, Driven, and never 0 Oe.
        self.assertEqual(page.field_commands(), [(100.0, 1), (200.0, 1)])
        reads = [i for i, entry in enumerate(log) if entry == ("sr830_8?", "SNAP? 1,2,3,4,9")]
        first, second = (log.index(("ppms", "set_field", f, 1)) for f in (100.0, 200.0))
        configure = log.index(("6221", "SOUR:WAVE:INIT"))
        self.assertLess(configure, first, "the 6221 set up once, as in Angle+Temperature")
        # Field 1: command, its stable reading (the waits of the field
        # settle), the move to 27° (the rotator starts at 26°), reads.
        settle_waits = [entry for entry in log[first:reads[0]] if entry[0] == "wait"]
        self.assertGreaterEqual(len(settle_waits), 2, "the stable reading awaited for 1 s")
        moves_1 = [entry for entry in log[first:second] if entry[0] == "motor"]
        self.assertEqual(moves_1, [("motor", 26.9), ("motor", 27.0)])
        self.assertTrue(any(first < i < log.index(("motor", 26.9)) for i in reads),
                        "26° read before the move to 27°")
        # Field 2 only after field 1's whole sweep; then back to 26° first.
        self.assertTrue(all(i < second for i in reads[:len(reads) // 2]))
        moves_2 = [entry for entry in log[second:] if entry[0] == "motor"]
        self.assertEqual(moves_2, [("motor", 26.1), ("motor", 26.0), ("motor", 26.9), ("motor", 27.0)])
        self.assertLess(second, log.index(("motor", 26.1), second))
        self.assertTrue(all(entry[0] != "6221" or entry[1] != "SOUR:WAVE:INIT" for entry in log[first:]),
                        "the source keeps running between fields and angles")
        # The temperature held at the T K box, as in an Angle scan.
        self.assertEqual({entry[2] for entry in log if entry[:2] == ("ppms", "set_temperature")}, {300.0})
        self.assertEqual(page.sent_6221()[-3:], ZERO)

    def test_the_rows_and_the_first_point_windows_after_each_field_change(self):
        page = AngleFieldPage(self).start()
        rows = page.rows()
        self.assertEqual([(float(r["target_field_oe"]), float(r["target_angle_deg"])) for r in rows],
                         [(100.0, 26.0), (100.0, 27.0), (200.0, 26.0), (200.0, 27.0)])
        self.assertEqual([float(r["measured_field_oe"]) for r in rows], [100.0, 100.0, 200.0, 200.0])
        self.assertEqual([float(r["measured_angle_deg"]) for r in rows], [26.0, 27.0, 26.0, 27.0])
        self.assertEqual([r["first_point_windows"] for r in rows], ["2", "1", "2", "1"],
                         "the first angle after each field change takes the agreeing windows")
        self.assertEqual([r["angle_index"] for r in rows], ["1", "2", "1", "2"])
        self.assertEqual([r["outer_field_index"] for r in rows], ["1", "1", "2", "2"])
        self.assertEqual([r["temperature_k"] for r in rows], ["300.0"] * 4)
        header = page.header()
        self.assertEqual(header[-1], "outer_field_index", "the new column goes last")
        self.assertEqual(header[-3:-1], ["effective_settle_s", "slowest_lockin_tc_s"])

    def test_the_columns_are_an_angle_scans_with_the_index_last(self):
        """The other modes are unchanged: an Angle scan's columns, in order,
        then outer_field_index."""
        angle = AngleFieldPage(self)
        angle.panel.ppms_simple_mode.set("Angle")
        angle.start()
        self.assertEqual(angle.field_commands(), [], "an Angle scan never commands the field")
        self.assertNotIn("outer_field_index", angle.header())
        both = AngleFieldPage(self).start()
        self.assertEqual(both.header(), angle.header() + ["outer_field_index"])

    def test_the_files_and_log_events(self):
        page = AngleFieldPage(self).start()
        panel = page.panel
        self.assertEqual(sorted(p.name for p in panel.ppms_run_folder.iterdir()), ["run1.csv", "run1.log"])
        self.assertEqual(page.names(), ["run_prepared", "scan_started", "harmonic_pass",
                                        "field_step", "point_saved", "point_saved",
                                        "field_step", "point_saved", "point_saved",
                                        "outputs_zeroed", "run_finished"])
        events = page.events()
        steps = [e for e in events if e["event"] == "field_step"]
        self.assertEqual([(e["index"], e["count"], e["target_field_oe"], e["rate_oe_s"], e["mode"]) for e in steps],
                         [(1, 2, 100.0, 100.0, "Driven"), (2, 2, 200.0, 100.0, "Driven")])
        saved = [e for e in events if e["event"] == "point_saved"]
        self.assertEqual([(e["x_column"], e["x"], e["target_field_oe"]) for e in saved],
                         [("measured_angle_deg", 26.0, 100.0), ("measured_angle_deg", 27.0, 100.0),
                          ("measured_angle_deg", 26.0, 200.0), ("measured_angle_deg", 27.0, 200.0)])
        self.assertEqual(events[0]["settings"]["mode"], "Angle+Field")
        self.assertTrue(events[0]["settings"]["field"]["commanded"])
        self.assertEqual(events[-1]["outcome"], "completed")
        self.assertEqual(events[-1]["status"], "Completed; 6221 zeroed")
        self.assertTrue(panel.ppms_macro_scan_result)
        with mock.patch.object(app.filedialog, "asksaveasfilename", return_value="") as save:
            panel.save_ppms_field_scan_csv()
        self.assertEqual(save.call_args.kwargs["initialdir"], str(panel.ppms_run_folder))

    def test_every_harmonic_pass_repeats_the_whole_scan(self):
        """SR830-1 at 1+2: the second pass sets the fields again from the
        first, with its own field_step events."""
        page = AngleFieldPage(self)
        page.panel.ppms_transport_harmonic_a_var.set("1+2")
        page.start()
        self.assertEqual(page.field_commands(), [(100.0, 1), (200.0, 1), (100.0, 1), (200.0, 1)])
        self.assertEqual([r["harmonic_pass"] for r in page.rows()], ["1"] * 4 + ["2"] * 4)
        self.assertEqual(page.names().count("field_step"), 4)


class EndingTests(unittest.TestCase):
    def test_stop_mid_sweep_keeps_the_field(self):
        page = AngleFieldPage(self)

        def on_wait(seconds):
            if len(page.panel.ppms_field_scan_rows) == 1 and math.isclose(seconds, 1.0):
                page.panel.stop_ppms_field_scan()
        page.rig.stop.on_wait = on_wait
        page.start()
        panel = page.panel
        self.assertEqual(len(page.rows()), 1)
        self.assertEqual(page.field_commands(), [(100.0, 1)], "no 0 Oe after Stop")
        self.assertEqual(page.sent_6221()[-3:], ZERO)
        self.assertEqual(page.rig.source.output, 0)
        self.assertEqual(panel.ppms_field_scan_status_var.get(), "Stopped; 6221 zeroed; field kept at its setpoint")
        self.assertEqual(page.dialogs, [])
        self.assertEqual(page.names()[-4:], ["point_saved", "stop_requested", "outputs_zeroed", "run_finished"])
        self.assertEqual(page.events()[-1]["outcome"], "stopped")

    def test_stop_during_a_field_ramp_keeps_the_field(self):
        page = AngleFieldPage(self)
        page.magnet.on_set = lambda target: page.panel.stop_ppms_field_scan() if target == 200.0 else None
        page.start()
        panel = page.panel
        self.assertEqual(len(page.rows()), 2, "field 1's sweep")
        self.assertEqual(page.field_commands(), [(100.0, 1), (200.0, 1)], "the field keeps its setpoint")
        self.assertFalse(any(entry[0] == "motor" for entry in page.rig.log[page.rig.log.index(
            ("ppms", "set_field", 200.0, 1)):]), "no angle sweep at the field that was stopped")
        self.assertEqual(page.sent_6221()[-3:], ZERO)
        self.assertEqual(panel.ppms_field_scan_status_var.get(), "Stopped; 6221 zeroed; field kept at its setpoint")
        self.assertEqual(page.dialogs, [])
        finished = page.events()[-1]
        self.assertEqual(finished["outcome"], "stopped")
        self.assertEqual(finished["stop_reason"], "Stop: Scan stopped while the field settled")

    def test_i_off_zeroes_the_field_once_and_the_6221(self):
        for when in ("mid-sweep", "field ramp"):
            with self.subTest(when=when):
                page = AngleFieldPage(self)
                if when == "mid-sweep":
                    def on_wait(seconds):
                        if page.panel.ppms_field_scan_rows and not page.panel.ppms_field_scan_stop_event.is_set():
                            page.panel.ppms_sources_off()
                    page.rig.stop.on_wait = on_wait
                else:
                    page.magnet.on_set = (lambda target: page.panel.ppms_sources_off()
                                          if target == 200.0 else None)
                page.start()
                commands = page.field_commands()
                self.assertEqual(commands.count((0.0, 1)), 1, commands)
                self.assertEqual(commands[-1], (0.0, 1))
                self.assertEqual(page.sent_6221()[-3:], ZERO)
                self.assertEqual(page.rig.source.output, 0)
                self.assertEqual(page.panel.ppms_field_scan_status_var.get(),
                                 "Stopped; 6221 zeroed; field ramping to 0 Oe")
                events = page.events()
                self.assertEqual(next(e for e in events if e["event"] == "stop_requested")["button"], "I off")
                self.assertEqual(events[-1]["outcome"], "stopped")

    def test_a_field_failure_zeroes_the_field_once_and_the_6221(self):
        page = AngleFieldPage(self)
        page.magnet.refuse = 200.0
        page.start()
        panel = page.panel
        self.assertEqual(page.field_commands(), [(100.0, 1), (200.0, 1), (0.0, 1)])
        self.assertEqual(len(page.rows()), 2)
        self.assertEqual(page.sent_6221()[-3:], ZERO)
        status = panel.ppms_field_scan_status_var.get()
        self.assertEqual(status, "Failed: Field set failed: MultiVu busy")
        self.assertEqual(len(page.dialogs), 1)
        title, text = page.dialogs[0]
        self.assertEqual(title, "PPMS Angle+Field scan")
        self.assertTrue(text.endswith("At the end: 6221 zeroed; field ramping to 0 Oe"), text)
        events = page.events()
        self.assertEqual(events[-1]["outcome"], "error")
        self.assertEqual(page.names()[-2:], ["outputs_zeroed", "run_finished"])

    def test_a_refused_zero_says_check_multivu(self):
        page = AngleFieldPage(self)
        commands = []

        def refuse(target):
            commands.append(target)
            page.magnet.refuse = target if target in (200.0, 0.0) else None
        page.magnet.on_set = refuse
        page.start()
        self.assertEqual(page.field_commands(), [(100.0, 1), (200.0, 1), (0.0, 1)], "0 Oe tried once")
        status = page.panel.ppms_field_scan_status_var.get()
        self.assertTrue(status.endswith("; field NOT zeroed: check MultiVu"), status)

    def test_a_field_that_drifts_during_the_sweep_is_a_field_failure(self):
        page = AngleFieldPage(self)
        read = page.magnet.read

        def drifting():
            data = read()
            if page.panel.ppms_field_scan_rows:
                data["field_oe"] = 150.0       # off 100 Oe after the first point
            return data
        page.panel._read_ppms_live = drifting
        page.start()
        self.assertEqual(len(page.rows()), 1)
        self.assertEqual(page.field_commands(), [(100.0, 1), (0.0, 1)], "zeroed once")
        self.assertTrue(page.panel.ppms_field_scan_status_var.get().startswith("Failed: PPMS timed out waiting for +100 Oe"))
        self.assertEqual(page.sent_6221()[-3:], ZERO)

    def test_closing_zeroes_the_field_once(self):
        page = AngleFieldPage(self)
        panel = page.panel

        def on_wait(seconds):
            if panel.ppms_field_scan_rows and not panel.closing:
                panel.parent_app.closing = True
                panel.ppms_field_scan_stop_event.set()
        page.rig.stop.on_wait = on_wait
        panel.parent_app.closing = False
        page.start()
        self.assertEqual(page.field_commands().count((0.0, 1)), 1)
        self.assertEqual(page.rig.source.output, 0)
        events = page.events(next(Path(page.folder.name).iterdir()) / "run1.log")
        self.assertEqual(events[-1]["stop_reason"], "the program closing")

    def test_zero_and_persist_after_the_scan(self):
        page = AngleFieldPage(self)
        queued = []

        def on_wait(_seconds):
            if page.panel.ppms_field_scan_rows and not queued:
                queued.append(True)
                page.panel._queue_ppms_zero_persist()
        page.rig.stop.on_wait = on_wait
        page.start()
        self.assertEqual(page.field_commands(), [(100.0, 1), (200.0, 1), (0.0, 1), (0.0, 0)])
        log = page.rig.log
        self.assertLess(log.index(("6221?", "OUTP?"), log.index(("ppms", "set_field", 200.0, 1))),
                        log.index(("ppms", "set_field", 0.0, 1)), "the 6221 zeroed first")
        self.assertEqual(page.panel.ppms_field_scan_status_var.get(),
                         "Completed; 6221 zeroed; field zeroed and confirmed Persistent")


class PlotTests_(unittest.TestCase):
    """Against the angle, one curve per outer field, shaded by it."""

    @staticmethod
    def rows():
        return [{"measured_angle_deg": a, "target_field_oe": f, "outer_field_index": i, "loop": 1,
                 "angle_index": 1 + int(a > 0), "temperature_index": 1, "target_temperature_k": 300.0,
                 "sr830_8_x_v": (f + a) * 1e-9, "sr830_8_y_v": 0.0, "sr830_8_r_v": (f + a) * 1e-9,
                 "sr830_8_theta_deg": 0.0, "sr830_8_harmonic": 1}
                for i, f in enumerate((-500.0, 0.0, 500.0), 1) for a in (0.0, 5.0)]

    def test_the_panel_plot(self):
        page = PlotTests.panel_page(self.rows(), "Angle+Field")
        page._redraw_ppms_transport_plot()
        args, options = page.calls[0]
        series, x_label = args[1], args[2]
        self.assertEqual(x_label, "Measured angle (°)")
        names = ["SR830-1 H1 (-500 Oe) L1", "SR830-1 H2 (0 Oe) L1", "SR830-1 H3 (500 Oe) L1"]
        self.assertEqual(sorted(series), sorted(names))
        self.assertEqual([x for x, _y in series[names[0]]], [0.0, 5.0])
        for (_x, y), expected in zip(series[names[0]], (-5e-7, -4.95e-7)):
            self.assertAlmostEqual(y, expected, delta=1e-15)
        base = app.PPMS_METER_COLORS["830_a"]
        colors = options["series_colors"]
        self.assertEqual(colors[names[0]], app.shade_color(base, 0.0))   # lightest, lowest field
        self.assertEqual(colors[names[2]], app.shade_color(base, 1.0))   # darkest, highest

    def test_the_base_plot(self):
        page = PlotTests.base_page(self.rows(), "Angle+Field")
        page._redraw_ppms_transport_plot()
        args, options = page.calls[0]
        self.assertEqual(sorted(args[1]), ["GPIB8 X | H1 (-500 Oe) L1", "GPIB8 X | H2 (0 Oe) L1",
                                           "GPIB8 X | H3 (500 Oe) L1"])
        self.assertEqual(len(set(options["series_colors"].values())), 3)

    def test_angle_temperature_still_groups_by_temperature(self):
        rows = [dict(row, target_temperature_k=t, temperature_index=ti) for row in self.rows()[:2]
                for ti, t in ((1, 300.0), (2, 290.0))]
        page = PlotTests.panel_page(rows, "Angle+Temperature")
        page._redraw_ppms_transport_plot()
        self.assertTrue(all(" K) L1" in name for name in page.calls[0][0][1]))


if __name__ == "__main__":
    unittest.main()
