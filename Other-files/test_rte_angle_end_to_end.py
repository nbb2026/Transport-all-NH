"""RT-E Angle scan, end to end (user, 2026-10-17: "now move the stage
controllers to the RT-E settings and make the angle scan function
workable"; asked what fails: "Not tried yet; make it ready").

The real RTEPanel, built on stand-in widgets, runs a whole Angle scan
through its own Start (requirements, the Yes/No, the folder and file name,
the stage check), its scan workers and its finish, on a real single-worker
executor named rte-control, as on the lab PC.  The instruments are fakes
that log every call with the thread it came from: the KEPCO 4886 and the
LS475 at their controllers' methods, the 6221 and the lock-ins at the
page's acquisition methods (their instrument paths are the PPMS page's,
shared with the Field and Current scans), and the Newmark NSC-A1 is the
program's real controller on test_rte_angle_scan's fake PerformaxCom.dll
(the standalone script's simulated NSC-A1, on a virtual clock).  The Tk
thread is the test's: the post_ui queue and root.after callbacks run when
the test pumps them.  No real DLL is ever loaded, no window opens and
every dialog is answered here."""
from concurrent.futures import ThreadPoolExecutor
from contextlib import ExitStack
import csv
import ctypes
import datetime
import json
import os
from pathlib import Path
import tempfile
import threading
import time
import types
import unittest
from unittest.mock import Mock, patch

import test_rte_panel as rte_tests
from test_ppms_motor_debug import app
from test_ppms_two_panels import ImmediateExecutor, Var
from test_rte_angle_scan import Clock, EndsShort, Recorder, RecordingDLL, Sim, script

_guards = []
# The end of every SHV scan (user, 2026-10-18): the 6221 off and read back,
# then the KEPCO's Zero / OFF.
ZEROED = [("6221", "off"), ("6221", "OUTP?"), ("kepco", "force_off")]


def setUpModule():
    def refuse(what):
        def refused(*_args, **_kwargs):
            raise AssertionError(f"a test reached {what}")
        return refused
    for target, name, replacement in ((ctypes, "WinDLL", refuse("ctypes.WinDLL (a real DLL)")),
                                      (os, "add_dll_directory", refuse("os.add_dll_directory")),
                                      (app, "newmark_load_performax", refuse("the real Newmark DLL loader")),
                                      (app.tk, "Tk", refuse("tkinter.Tk (a window)"))):
        guard = patch.object(target, name, replacement, create=True)
        guard.start()
        _guards.append(guard)


def tearDownModule():
    while _guards:
        _guards.pop().stop()


class Root:
    """root.after for the page: callbacks wait for the test's Tk loop; the
    idle poll's 1 s timer is kept but only run when a test asks."""

    def __init__(self):
        self.pending = []

    def after(self, delay, callback):
        self.pending.append((delay, callback))
        return f"after#{len(self.pending)}"

    def after_cancel(self, _token):
        pass


class Kepco:
    connected, label = True, "KEPCO 4886"

    def __init__(self, log):
        self.log = log
        self.output_owned, self.cached_current_a = False, 0.0

    def set_output(self, mode, target, limit):
        self.log.append(("kepco", "set_output", mode, target, limit, threading.get_ident()))
        self.output_owned, self.cached_current_a = True, target

    def set_scan_current(self, value):
        self.log.append(("kepco", "set_scan_current", value, threading.get_ident()))
        self.cached_current_a = value       # as the controller's (Angle+Field rows, 2026-10-18)

    def safe_output_off(self):
        self.log.append(("kepco", "off", threading.get_ident()))
        self.output_owned = False

    def force_output_off(self):
        self.log.append(("kepco", "force_off", threading.get_ident()))
        self.output_owned, self.cached_current_a = False, 0.0
        return {}

    def disconnect(self):
        pass


class Gauss:
    connected, label = True, "Lake Shore 475"

    def __init__(self, log, field=123.0):
        self.log, self.field = log, field

    def read_field(self):
        self.log.append(("gauss", "read", threading.get_ident()))
        return {"field": self.field, "unit": "G"}

    @staticmethod
    def value_in_gauss(reading):
        return float(reading["field"])

    def disconnect(self):
        pass


def kepco_chosen(panel):
    """SHV starts on the Lake Shore 643 since 2026-10-23 (user); these
    tests drive the KEPCO 4886 path, chosen as the combobox would."""
    panel.rte_magnet_choice_var.set(app.MAGNET_SUPPLY_KEPCO)
    panel._rte_magnet_choice_changed()
    return panel


def build_page(kepco=True):
    """The real RTEPanel constructor on stand-in widgets (as test_rte_angle_scan's)."""
    parent = Mock(closing=False)
    parent.root.after.return_value = "timer"
    Recorder.made = []
    with ExitStack() as stack:
        for name in ("Frame", "LabelFrame", "Label", "Entry", "Button", "Combobox", "Checkbutton", "Progressbar"):
            stack.enter_context(patch.object(app.ttk, name, Recorder))
        stack.enter_context(patch.object(app.tk, "Canvas", Recorder))
        for name in ("StringVar", "IntVar", "BooleanVar"):
            stack.enter_context(patch.object(app.tk, name, Var))
        stack.enter_context(patch.object(app, "ThreadPoolExecutor", ImmediateExecutor))
        stack.enter_context(patch.object(app.CombinedControlApp, "_create_connection_indicator",
                                         lambda _self, parent, key: Recorder(parent, indicator=key)))
        stack.enter_context(patch.object(app.CombinedControlApp, "_set_connection_indicator", Mock()))
        stack.enter_context(patch.object(app.CombinedControlApp, "_draw_nonlocal_series", Mock()))
        panel = app.RTEPanel(parent, rte_tests.Widget())
        return kepco_chosen(panel) if kepco else panel


class EndToEnd(unittest.TestCase):
    def make(self, sim=None, span=("0", "20", "10"), loops="1", round_trip=True, meter="zurich",
             tc_zurich="0.001"):
        """The page, ready for an Angle scan: the safety tick, every
        instrument connected (the stage through its real Connect, on the
        worker), Angle chosen and the range set; ``meter``: "zurich" (SHV's
        default, the Zurich alone at 1+2 since 2026-10-18) or "sr830" (SR830-1
        alone at 1+2+3, three harmonic passes)."""
        panel = build_page()
        h = types.SimpleNamespace(panel=panel, log=[], logged=[], ui=[], in_drain=False,
                                  folder=tempfile.TemporaryDirectory())
        self.addCleanup(h.folder.cleanup)
        h.executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="rte-control")
        self.addCleanup(h.executor.shutdown, wait=True)
        h.worker = h.executor.submit(threading.get_ident).result(timeout=30)
        panel.ppms_executor = h.executor
        panel.root = h.root = Root()
        panel.post_ui = h.ui.append
        # The page's log lines, at once; the page's own thread_log still runs,
        # so a running scan's lines are also its run log's notes (2026-10-18).
        guard = patch.object(app.CombinedControlApp, "thread_log", lambda _self, message: h.logged.append(message))
        guard.start()
        self.addCleanup(guard.stop)
        # The plots: what each redraw would draw.
        h.draw = Mock()
        guard = patch.object(app.CombinedControlApp, "_draw_nonlocal_series", h.draw)
        guard.start()
        self.addCleanup(guard.stop)
        # Every dialog the scan can reach.
        h.askyesno = Mock(return_value=True)
        h.showerror = Mock()
        for target, name, value in ((app.messagebox, "askyesno", h.askyesno),
                                    (app.messagebox, "showerror", h.showerror),
                                    (app.filedialog, "askdirectory", Mock(return_value=h.folder.name)),
                                    (app.simpledialog, "askstring", Mock(return_value="angle"))):
            guard = patch.object(target, name, value)
            guard.start()
            self.addCleanup(guard.stop)
        # The instruments.
        panel.rte_controllers["kepco"] = h.kepco = Kepco(h.log)
        panel.rte_controllers["gauss"] = h.gauss = Gauss(h.log)
        h.clock = Clock()
        guard = patch.object(script, "_now", h.clock.now)
        guard.start()
        self.addCleanup(guard.stop)
        h.sim = Sim() if sim is None else sim
        h.dll = RecordingDLL(h.sim)
        stage = app.NewmarkNSCA1Controller(h.logged.append, owner_scope="SHV",
                                           loader=lambda: (h.dll, "fake PerformaxCom.dll"))
        stage._now, stage._sleep = h.clock.now, h.clock.sleep
        real_move = stage.move_to_deg
        h.move_hooks = {}

        def move(target, stop_event, on_progress=None):
            h.log.append(("stage", "move", float(target), threading.get_ident()))
            hook = h.move_hooks.pop(float(target), None)
            if hook is not None:
                hook()
            state = real_move(target, stop_event, on_progress)
            h.log.append(("stage", "at", state["angle_deg"], threading.get_ident()))
            return state

        stage.move_to_deg = move
        panel.rte_controllers["newmark"] = h.stage = stage
        source = types.SimpleNamespace(connected=True, label="Keithley 6221", resource_name="GPIB0::12::INSTR",
                                       disconnect=lambda: None)
        panel.ppms_instrument_controllers["6221"] = source
        panel.zurich_controller = types.SimpleNamespace(mfli_connected=meter == "zurich", lock=threading.Lock(),
                                                        disconnect_mfli=lambda: None)
        panel.zurich_timeconstant.set(tc_zurich)
        if meter == "zurich":
            # The Zurich alone, chosen here: SHV starts with SR830-1 and
            # SR830-2 measured since 2026-10-20 (user's figure).
            for key in app.PPMS_SR830_KEYS:
                panel.ppms_lockin_selection[key].set(False)
            panel.ppms_lockin_selection["zurich"].set(True)
        if meter == "sr830":
            panel.ppms_instrument_controllers["830_a"] = types.SimpleNamespace(
                connected=True, label="SR830-1", resource_name="GPIB0::8::INSTR", disconnect=lambda: None)
            for key in app.PPMS_SR830_KEYS:
                panel.ppms_lockin_selection[key].set(key == "830_a")
            panel.ppms_lockin_selection["zurich"].set(False)
            panel.ppms_transport_harmonic_a_var.set("1+2+3")
            panel.ppms_sr830_time_constant_codes = {"830_a": 0}     # 10 us: no real wait
            h.harmonic = {"830_a": 1}

            def write(key, command):
                h.log.append(("sr830", command, threading.get_ident()))
                if command.startswith("HARM "):
                    h.harmonic[key] = int(command.split()[1])

            panel._ppms_write_sr830_checked = write
        h.harmonic = h.__dict__.get("harmonic", {})

        def query(key, command, count):
            # The 6221's OUTP? after the end's off (user, 2026-10-18), and an
            # SR830's HARM? between harmonic passes.
            if command == "OUTP?":
                h.log.append(("6221", "OUTP?", threading.get_ident()))
                return (0,)
            assert command == "HARM?", command
            return (h.harmonic[key],)
        panel._ppms_query_numbers = query
        h.points = 0

        def sample(settings_lockins):
            h.points += 1
            # The Zurich's harmonics as the page has them (1+2 by default,
            # user's figure, 2026-10-18), one demodulator per order.
            orders = str(panel.zurich_harmonic.get())
            values = {"zurich_harmonic": orders}
            for demod in range(1, len(orders.split("+")) + 1):
                values.update({f"zurich_demod{demod}_x_v": demod * 1e-6 + h.points * 1e-9,
                               f"zurich_demod{demod}_y_v": -demod * 1e-6, f"zurich_demod{demod}_r_v": demod * 1.4e-6,
                               f"zurich_demod{demod}_theta_deg": -45.0, f"zurich_demod{demod}_harmonic": demod,
                               f"zurich_demod{demod}_input": 0})
            for key in settings_lockins:
                values.update({"sr830_8_x_v": 2e-6 + h.points * 1e-9, "sr830_8_y_v": 1e-6, "sr830_8_r_v": 2.2e-6,
                               "sr830_8_theta_deg": 26.0, "sr830_8_reference_valid": True})
            return values

        def configure(settings):
            h.log.append(("6221", "on", settings["current_rms_ma"], settings["frequency_hz"], threading.get_ident()))

        def first(frequency, lockins, duration):
            h.log.append(("lockins", "first", tuple(lockins), threading.get_ident()))
            return sample(lockins), 2

        def average(frequency, lockins, duration):
            h.log.append(("lockins", "read", tuple(lockins), threading.get_ident()))
            return sample(lockins)

        panel._configure_ppms_transport_scan = configure
        panel._acquire_ppms_first_point = first
        panel._average_ppms_transport_sample = average
        panel._stop_ppms_transport_source = lambda: h.log.append(("6221", "off", threading.get_ident()))
        panel._read_ppms_current_display = lambda: "ON"
        # The page as the user leaves it before Start.
        panel.ppms_control_safety_var.set(True)
        panel.rte_drive_target.set("1.5")
        panel.rte_voltage_limit.set("5")
        panel.ppms_field_scan_stable_var.set("0")
        panel.ppms_acquisition_time_var.set("0.01")
        panel.rte_connect = None
        panel._rte_connect("newmark")
        self.pump(h)
        self.assertTrue(stage.connected, h.showerror.call_args)
        panel.ppms_simple_mode.set("Angle")
        panel._rte_mode_changed()
        for variable, value in zip(panel.ppms_simple_range, span):
            variable.set(value)
        panel.ppms_field_scan_loop_var.set(loops)
        panel.rte_round_trip.set(round_trip)
        h.log.clear()
        return h

    def pump(self, h, until=None, limit=5000):
        """As the program's Tk thread, until nothing is left: wait for the
        worker, run the post_ui queue (drain_ui_queue), then what
        root.after(0, ...) scheduled (the Tk event loop); the idle poll's
        1 s timer stays queued."""
        for _ in range(limit):
            h.executor.submit(lambda: None).result(timeout=120)
            moved = False
            while h.ui:
                h.in_drain = True
                try:
                    h.ui.pop(0)()
                finally:
                    h.in_drain = False
                moved = True
            ready = [item for item in h.root.pending if item[0] < 1000]
            for item in ready:
                h.root.pending.remove(item)
                item[1]()
                moved = True
            if until is not None and until():
                return
            if not moved and not h.panel.ppms_field_scan_running:
                return
        raise AssertionError("the page did not settle")

    def start(self, h):
        h.panel.start_ppms_field_scan()
        self.pump(h)

    @staticmethod
    def rows(h):
        path = h.panel.ppms_live_csv_path
        with path.open(encoding="utf-8-sig", newline="") as handle:
            return list(csv.DictReader(handle))

    @staticmethod
    def names(h, *kinds):
        return [entry[:2] for entry in h.log if entry[0] in kinds]


class AngleScanTests(EndToEnd):
    def test_a_whole_round_trip_angle_scan(self):
        h = self.make()
        self.start(h)
        panel = h.panel
        h.showerror.assert_not_called()
        self.assertFalse(panel.ppms_field_scan_running)
        # The Yes/No names the angles, the held field and the wires.
        question = h.askyesno.call_args.args[1]
        self.assertIn("Angle scan from 0° to 20° (step 10°, round trip): 5 angles x 1 harmonic pass(es) = 5 points",
                      question)
        self.assertIn("the KEPCO is set once to +1.5 A (voltage limit 5 V)", question)
        self.assertIn("SAMPLE HOLDER with its wires", question)
        # The order: the stage check, the 6221 on, the KEPCO set once and the
        # field settled on the LS475, then every angle: the move, the field
        # before, the lock-ins, the field after.
        sequence = [entry[:3] if entry[0] == "stage" else entry[:2] for entry in h.log]
        self.assertEqual(sequence[:2], [("6221", "on"), ("kepco", "set_output")])
        settle = sequence[2:2 + app.STFMR_FIELD_SETTLE_REQUIRED_SAMPLES]
        self.assertEqual(settle, [("gauss", "read")] * app.STFMR_FIELD_SETTLE_REQUIRED_SAMPLES)
        points = sequence[2 + app.STFMR_FIELD_SETTLE_REQUIRED_SAMPLES:-3]
        expected = []
        for index, angle in enumerate((0.0, 10.0, 20.0, 10.0, 0.0)):
            expected += [("stage", "move", angle), ("stage", "at", angle), ("gauss", "read"),
                         ("lockins", "first" if index == 0 else "read"), ("gauss", "read")]
        self.assertEqual(points, expected)
        self.assertEqual([e[2:5] for e in h.log if e[:2] == ("kepco", "set_output")], [("CURR", 1.5, 5.0)])
        # The end: the 6221 off and read back, then the KEPCO zeroed (user,
        # 2026-10-18: "when finish scan in SHV, automatically zero the Kepco
        # and 6221 pls"; both were held until then).
        self.assertEqual(sequence[-3:], ZEROED)
        self.assertEqual(self.names(h, "kepco", "6221"), [("6221", "on"), ("kepco", "set_output")] + ZEROED)
        # Every instrument call and every DLL call on the rte-control worker.
        self.assertEqual({entry[-1] for entry in h.log}, {h.worker})
        self.assertEqual({thread for _name, thread in h.dll.calls}, {h.worker})
        self.assertNotIn(threading.get_ident(), {thread for _name, thread in h.dll.calls})
        for command in ("X100000", "X200000"):
            self.assertIn(command, h.sim.received)
        # The live CSV: one row per angle, the angle read back, its steps.
        rows = self.rows(h)
        self.assertEqual([float(r["stage_target_deg"]) for r in rows], [0.0, 10.0, 20.0, 10.0, 0.0])
        self.assertEqual([float(r["stage_angle_deg"]) for r in rows], [0.0, 10.0, 20.0, 10.0, 0.0])
        self.assertEqual([int(r["stage_position_steps"]) for r in rows], [0, 100000, 200000, 100000, 0])
        self.assertEqual({r["stage_steps_per_degree"] for r in rows}, {"10000"})
        self.assertEqual({(r["module"], r["scan_mode"], r["loop"]) for r in rows}, {("SHV", "Angle", "1")})
        self.assertEqual([r["point"] for r in rows], ["1", "2", "3", "4", "5"])
        self.assertEqual({r["kepco_programmed_current_a"] for r in rows}, {"1.5"})
        self.assertEqual({r["measured_field_g"] for r in rows}, {"123.0"})
        self.assertEqual({r["kepco_voltage_limit_v"] for r in rows}, {"5.0"})
        self.assertEqual(rows[0]["first_point_windows"], "2")
        # The finish and the live lines.
        self.assertEqual(panel.ppms_field_scan_status_var.get(), "Completed; KEPCO and 6221 zeroed; stage at +0.000°")
        self.assertEqual((panel.rte_drive_live.get(), panel.ppms_current_live_var.get()),
                         ("KEPCO: zeroed and output OFF", "OFF / 0 mA"))
        self.assertEqual(panel.rte_stage_live.get(), "Stage: +0.000° (10000 steps/°)")
        self.assertEqual(panel.rte_field_live.get(), "LS475 field: +123 G")
        self.assertEqual(panel.ppms_field_scan_total, 5)
        # The plots: against the angle read back, every point.
        # The last plot drawn with data: with the Zurich at 1+2 (user's
        # figure, 2026-10-18) the hidden third-harmonic row is drawn empty.
        drawn = next(c for c in reversed(h.draw.call_args_list) if c.args[1])
        x_label, series = drawn.args[2], drawn.args[1]
        self.assertEqual(x_label, "Sample angle (°)")
        self.assertTrue(series)
        for name, points in series.items():
            with self.subTest(series=name):
                self.assertEqual([x for x, _y in points], [0.0, 10.0, 20.0, 10.0, 0.0])
        # Save CSV writes the same rows, the stage columns included.
        saved = Path(h.folder.name) / "saved.csv"
        with patch.object(app.filedialog, "asksaveasfilename", return_value=str(saved)):
            panel.save_ppms_field_scan_csv()
        with saved.open(encoding="utf-8-sig", newline="") as handle:
            copy = list(csv.DictReader(handle))
        self.assertEqual([r["stage_angle_deg"] for r in copy], [r["stage_angle_deg"] for r in rows])
        # The idle poll afterwards reads the stage again, on the worker.
        h.sim.received.clear()
        panel.ppms_poll_inflight = False
        panel._ppms_poll_timer()
        self.pump(h)
        self.assertEqual(h.sim.received, ["MST", "PX"])
        self.assertEqual(panel.rte_stage_live.get(), "Stage: +0.000° (10000 steps/°)")

    def test_loops_without_round_trip(self):
        h = self.make(span=("30", "0", "15"), loops="2", round_trip=False)
        self.start(h)
        h.showerror.assert_not_called()
        rows = self.rows(h)
        self.assertEqual([(r["loop"], r["point"], float(r["stage_angle_deg"])) for r in rows],
                         [("1", "1", 30.0), ("1", "2", 15.0), ("1", "3", 0.0),
                          ("2", "1", 30.0), ("2", "2", 15.0), ("2", "3", 0.0)])
        self.assertEqual([e[2] for e in h.log if e[:2] == ("stage", "move")], [30.0, 15.0, 0.0, 30.0, 15.0, 0.0])
        self.assertEqual(len([e for e in h.log if e[:2] == ("kepco", "set_output")]), 1)
        series = next(c.args[1] for c in reversed(h.draw.call_args_list) if c.args[1])
        self.assertEqual(sorted({name.rsplit(" ", 1)[-1] for name in series}), ["L1", "L2"], "one series per loop")

    def test_harmonic_passes_with_an_sr830(self):
        h = self.make(span=("0", "10", "10"), round_trip=False, meter="sr830")
        self.start(h)
        h.showerror.assert_not_called()
        rows = self.rows(h)
        self.assertEqual([(r["harmonic_pass"], r["sr830_8_harmonic"], float(r["stage_angle_deg"])) for r in rows],
                         [("1", "1", 0.0), ("1", "1", 10.0), ("2", "2", 0.0), ("2", "2", 10.0),
                          ("3", "3", 0.0), ("3", "3", 10.0)])
        # The field set once for every pass; the harmonic changed between
        # passes, the 6221 left on.
        self.assertEqual(len([e for e in h.log if e[:2] == ("kepco", "set_output")]), 1)
        self.assertEqual(len([e for e in h.log if e[:2] == ("6221", "on")]), 1)
        self.assertEqual([e[1] for e in h.log if e[0] == "sr830"], ["HARM 2", "HARM 3"])
        self.assertEqual(h.panel.ppms_field_scan_total, 6)
        self.assertEqual({entry[-1] for entry in h.log}, {h.worker})

    def test_the_lockins_at_start_up(self):
        # The user's figure (2026-10-20): SR830-1 at 1 and SR830-2 at 2
        # measured, SR830-3 at 3 and the Zurich at 1+2 not measured.
        panel = build_page()
        self.assertEqual({key: (str(panel._ppms_harmonic_var(key).get()), panel.ppms_lockin_selection[key].get())
                          for key in app.PPMS_SR830_KEYS},
                         {"830_a": ("1", True), "830_b": ("2", True), "830_c": ("3", False)})
        self.assertEqual((panel.zurich_harmonic.get(), panel.ppms_lockin_selection["zurich"].get()), ("1+2", False))

    def test_the_zurich_alone_runs(self):
        h = self.make(span=("0", "0", "5"), round_trip=False)
        panel = h.panel
        self.assertEqual(panel.ppms_lockin_selection["zurich"].get(), True)
        self.assertFalse(any(panel.ppms_lockin_selection[k].get() for k in app.PPMS_SR830_KEYS))
        self.start(h)
        h.showerror.assert_not_called()
        self.assertEqual([e[2] for e in h.log if e[0] == "lockins"], [()], "no SR830 read, the Zurich in the sample")
        row = self.rows(h)[0]
        self.assertEqual((row["zurich_harmonic"], row["zurich_demod2_harmonic"]), ("1+2", "2"))
        self.assertNotIn("zurich_demod3_harmonic", row, "1+2 by default (user's figure, 2026-10-18)")

    def test_the_mode_change_sets_the_angle_boxes(self):
        panel = build_page()
        # The page starts on the user's Angle+Field scan (2026-10-19).
        self.assertEqual(panel.ppms_simple_mode.get(), "Angle+Field")
        self.assertEqual(panel.rte_scan_note.get(), "Angle+Field: Stage °; outer: KEPCO A")
        with patch.object(app.CombinedControlApp, "_draw_nonlocal_series"):
            panel.ppms_simple_mode.set("Field")
            panel._rte_mode_changed()
        self.assertEqual(panel.rte_scan_note.get(), "Field: KEPCO A; readback: signed LS475 G")
        with patch.object(app.CombinedControlApp, "_draw_nonlocal_series") as draw:
            panel.ppms_simple_mode.set("Angle")
            panel._rte_mode_changed()
        # 0 to -360 deg in 5 deg steps (user's figures, 2026-10-18).
        self.assertEqual([v.get() for v in panel.ppms_simple_range], ["0", "-360", "5"])
        self.assertEqual(panel.rte_scan_units.get(), "Stage °")
        # The note says which box holds the field (user, 2026-10-17).
        self.assertEqual(panel.rte_scan_note.get(), "Angle: Stage °; field: KEPCO current A")
        # Round trip off for Angle (user, 2026-10-18: "default not click
        # Round trip"; it was on since 2026-10-13).
        self.assertIs(panel.rte_round_trip.get(), False, "round trip off by default")
        self.assertEqual(panel.ppms_plot_axis, "angle")
        self.assertEqual(draw.call_args.args[2], "Sample angle (°)", "an empty plot against the angle")
        for mode in ("Field", "Current"):
            with patch.object(app.CombinedControlApp, "_draw_nonlocal_series"):
                panel.ppms_simple_mode.set(mode)
                panel._rte_mode_changed()
            self.assertEqual(panel.rte_scan_note.get(), "Field: KEPCO A; readback: signed LS475 G")
        self.assertLessEqual(max(map(len, app.RTE_SCAN_NOTES.values())), 40)


class AngleScanStopTests(EndToEnd):
    def test_stop_during_a_move_sends_stop_and_says_where_the_stage_is(self):
        h = self.make(span=("0", "90", "90"), round_trip=False)
        panel = h.panel
        h.move_hooks[90.0] = lambda: h.clock.hooks.append((h.clock.t + 3.0, panel.stop_ppms_field_scan))
        self.start(h)
        h.showerror.assert_not_called()
        received = h.sim.received
        self.assertIn("STOP", received[received.index("X900000"):])
        self.assertNotIn("ABORT", received)
        self.assertFalse(h.stage.motion_may_run)
        status = panel.ppms_field_scan_status_var.get()
        self.assertTrue(status.startswith("Stopped; KEPCO and 6221 zeroed; stage stopped at +"), status)
        where = float(status.rsplit(" ", 1)[1].rstrip("°"))
        self.assertTrue(0 < where < 90, where)
        self.assertEqual([float(r["stage_angle_deg"]) for r in self.rows(h)], [0.0], "the first angle's row kept")
        self.assertEqual(self.names(h, "kepco", "6221"), [("6221", "on"), ("kepco", "set_output")] + ZEROED,
                         "a Stop zeroes both, once the stage has stopped")
        moves = [entry[:3] for entry in h.log]
        self.assertLess(moves.index(("stage", "move", 90.0)), moves.index(("6221", "off", h.worker)),
                        "the stopped move (STOP confirmed) before the zeroing; the stage is not moved again")
        self.assertEqual([e[2] for e in h.log if e[:2] == ("stage", "move")], [0.0, 90.0])
        self.assertFalse(panel.ppms_field_scan_running)
        self.assertIn(f"SHV Newmark NSC-A1 connected (Ace-Series-SDE, V231BL): {h.stage.scale_text()}", h.logged)

    def test_stop_during_a_settle(self):
        # 1 s settle a point (the Zurich's 1 s time constant): Stop from the
        # Tk thread while the worker waits.
        h = self.make(span=("0", "10", "10"), round_trip=False, tc_zurich="1")
        panel = h.panel
        panel.start_ppms_field_scan()
        self.pump(h, until=lambda: panel.ppms_field_scan_running)
        deadline = time.monotonic() + 30
        while not [e for e in h.log if e[:2] == ("stage", "at")] and time.monotonic() < deadline:
            time.sleep(0.02)
        time.sleep(0.2)                         # inside the first point's 1 s settle
        panel.stop_ppms_field_scan()
        self.pump(h)
        h.showerror.assert_not_called()
        self.assertEqual(panel.ppms_field_scan_status_var.get(),
                         "Stopped; KEPCO and 6221 zeroed; stage at +0.000°")
        self.assertEqual([e for e in h.log if e[0] == "lockins"], [], "the point was not read")
        self.assertEqual([e[2] for e in h.log if e[:2] == ("stage", "move")], [0.0], "no further move")

    def test_a_failed_move_stops_the_6221_then_zeroes_both_and_says_where(self):
        h = self.make(EndsShort(), span=("0", "10", "10"), round_trip=False)
        panel = h.panel
        self.start(h)
        h.showerror.assert_called_once()
        message = h.showerror.call_args.args[1]
        self.assertIn("not at the target", message)
        self.assertIn("Stage stopped at +9.999°", message)
        self.assertTrue(panel.ppms_field_scan_status_var.get().startswith("Failed: "))
        self.assertEqual(self.names(h, "kepco", "6221"),
                         [("6221", "on"), ("kepco", "set_output"), ("6221", "off")] + ZEROED,
                         "a stage failure: the 6221 off at once, then the end's zeroing of both")
        self.assertIn("At the end: 6221 zeroed; KEPCO zeroed", message)
        self.assertIn("SHV Angle scan, after the stage failure: stage stopped at +9.999°", h.logged)
        self.assertEqual([float(r["stage_angle_deg"]) for r in self.rows(h)], [0.0])

    def test_closing_during_a_scan_stops_it_and_closes_the_stage_on_the_worker(self):
        h = self.make(span=("0", "10", "10"), round_trip=False, tc_zurich="1")
        panel = h.panel
        panel.zurich_executor = Mock()
        panel.start_ppms_field_scan()
        self.pump(h, until=lambda: panel.ppms_field_scan_running)
        deadline = time.monotonic() + 30
        while not [e for e in h.log if e[:2] == ("stage", "at")] and time.monotonic() < deadline:
            time.sleep(0.02)
        panel.safe_close_ppms()
        h.executor.shutdown(wait=True)
        self.assertFalse(h.stage.connected, "the queued clean-up closed it")
        self.assertEqual(h.dll.names()[-1], "fnPerformaxComClose")
        self.assertEqual({thread for _name, thread in h.dll.calls}, {h.worker})
        self.assertEqual([e[2] for e in h.log if e[:2] == ("stage", "move")], [0.0], "no further move")
        self.assertIsNone(panel._newmark_stage_ownership().owner)


def fail_next_log_write(recorder, failures=1):
    """The run log's next ``failures`` writes raise, as a full disk would."""
    real = recorder._append
    left = [failures]

    def append(text):
        if left[0]:
            left[0] -= 1
            raise OSError(28, "No space left on device")
        return real(text)
    recorder._append = append


class RunLogBase(EndToEnd):
    """The page of the scans above, with which thread wrote each log event
    and Field and Current scans on it."""

    def make(self, *args, **kwargs):
        h = super().make(*args, **kwargs)
        # Which thread wrote each event, from the recorder's own event().
        h.writes = []
        real = app.SHVScanRecorder.event

        def event(recorder, name, **details):
            h.writes.append((name, threading.get_ident()))
            return real(recorder, name, **details)
        guard = patch.object(app.SHVScanRecorder, "event", event)
        guard.start()
        self.addCleanup(guard.stop)
        real_close = app.SHVScanRecorder.close

        def close(recorder, outcome, **details):
            h.writes.append(("close", threading.get_ident()))
            return real_close(recorder, outcome, **details)
        guard = patch.object(app.SHVScanRecorder, "close", close)
        guard.start()
        self.addCleanup(guard.stop)
        return h

    def make_mode(self, mode, span, **kwargs):
        """A Field or Current scan on the same page (the KEPCO, the LS475
        and the 6221 fakes); the stage stays connected and still."""
        h = self.make(**kwargs)
        panel = h.panel
        panel.ppms_simple_mode.set(mode)
        panel._rte_mode_changed()
        for variable, value in zip(panel.ppms_simple_range, span):
            variable.set(value)
        panel._update_ppms_transport_current = lambda settings: h.log.append(
            ("6221", "current", settings["current_rms_ma"], threading.get_ident()))
        h.log.clear()
        return h

    @staticmethod
    def events(h):
        folder = h.panel.rte_run_folder
        with (folder / f"{folder.name}.log").open(encoding="utf-8") as handle:
            return [json.loads(line) for line in handle]

    @staticmethod
    def without_notes(events):
        return [event["event"] for event in events if event["event"] != "note"]

    def assert_closed(self, h):
        self.assertIsNone(h.panel.rte_recorder, "the page let the recorder go")
        self.assertEqual(self.events(h)[-1]["event"], "run_finished")


class RunLogTests(RunLogBase):
    """The SHV run folder and its log (user, 2026-10-18: "like ST-FMR, pls
    also write a log of SHV and save results in a folder"), on the real
    page, worker and stage controller of the scans above."""

    def test_a_whole_angle_scan_is_logged_in_order(self):
        h = self.make()
        before = dict(h.stage.last_state)
        self.start(h)
        panel = h.panel
        h.showerror.assert_not_called()
        # The run folder <folder>/<name>/, its two files and nothing else.
        folder = Path(h.folder.name) / "angle"
        self.assertEqual(panel.rte_run_folder, folder)
        self.assertEqual(sorted(p.name for p in Path(h.folder.name).iterdir()), ["angle"])
        self.assertEqual(sorted(p.name for p in folder.iterdir()), ["angle.csv", "angle.log"])
        self.assertEqual(panel.ppms_live_csv_path, folder / "angle.csv")
        self.assertEqual(len(self.rows(h)), 5, "the live CSV as before, in the folder")
        events = self.events(h)
        expected = ["run_prepared", "scan_started", "harmonic_pass", "field_programmed", "field_settled"]
        for _angle in range(5):
            expected += ["stage_move", "point_saved"]
        self.assertEqual(self.without_notes(events), expected + ["outputs_zeroed", "run_finished"])
        for event in events:
            datetime.datetime.fromisoformat(event["timestamp"])
        # run_prepared: the settings and the instruments at Start.
        prepared = events[0]
        self.assertEqual((prepared["program"], prepared["app_version"]), ("Transport_all.py", app.APP_VERSION))
        self.assertTrue(prepared["python_version"])
        self.assertEqual((prepared["file_name"], prepared["mode"]), ("angle", "Angle"))
        self.assertEqual((prepared["run_folder"], prepared["csv_path"], prepared["log_path"]),
                         (str(folder), str(folder / "angle.csv"), str(folder / "angle.log")))
        plan = prepared["plan"]
        self.assertEqual((plan["start"], plan["end"], plan["step"], plan["loops"], plan["round_trip"]),
                         ("0", "20", "10", 1, True))
        self.assertEqual((plan["points"], plan["harmonic_passes"], plan["total_points"], plan["first_target"],
                          plan["farthest_target"]), (5, 1, 5, 0.0, 20.0))
        self.assertEqual((prepared["timing"]["settle_s"], prepared["timing"]["acquire_s"]), (0.0, 0.01))
        self.assertEqual(prepared["timing"]["effective_settle_s"], 0.001, "1 x the Zurich's 1 ms TC")
        self.assertEqual(prepared["harmonic_passes"], [{"harmonic_pass": 1, "harmonics": {}, "zurich_harmonics": "1+2"}])
        self.assertEqual(list(prepared["lockins"]), ["Zurich"])
        self.assertEqual((prepared["lockins"]["Zurich"]["harmonics"], prepared["lockins"]["Zurich"]["time_constant_s"]),
                         ("1+2", 0.001))
        self.assertEqual(prepared["source_6221"]["current_rms_ma"], float(panel.ppms_transport_current_var.get()))
        self.assertEqual(prepared["kepco"], {"current_a": 1.5, "voltage_limit_v": 5.0, "held": True})
        self.assertEqual(prepared["resources"]["newmark"]["identity"], "Ace-Series-SDE")
        self.assertTrue(prepared["resources"]["newmark"]["connected"])
        self.assertTrue(prepared["resources"]["kepco"]["connected"])
        self.assertTrue(prepared["resources"]["zurich"]["connected"])
        self.assertEqual((prepared["motor"]["identity"], prepared["motor"]["version"],
                          prepared["motor"]["steps_per_degree"]), ("Ace-Series-SDE", "V231BL", 10000))
        # Where the stage was: its last reading on the worker (at Connect)
        # and the stage check's MST; nothing was read for the log.
        at_start = prepared["stage_at_start"]
        self.assertEqual((at_start["angle_deg"], at_start["px"], at_start["read_at"]),
                         (before["angle_deg"], before["px"], before["read_at"]))
        self.assertEqual(at_start["stage_check_mst"], 0)
        self.assertIs(prepared["safety_confirmed"], True)
        # The run, in order.
        by_name = {}
        for event in events:
            by_name.setdefault(event["event"], []).append(event)
        self.assertEqual(by_name["scan_started"][0]["points"], 5)
        self.assertEqual(by_name["field_programmed"][0]["current_a"], 1.5)
        self.assertEqual(by_name["field_settled"][0]["field_g"], 123.0)
        self.assertEqual([(e["target_deg"], e["angle_deg"], e["px"]) for e in by_name["stage_move"]],
                         [(0.0, 0.0, 0), (10.0, 10.0, 100000), (20.0, 20.0, 200000), (10.0, 10.0, 100000),
                          (0.0, 0.0, 0)])
        saved = by_name["point_saved"]
        self.assertEqual([(e["index"], e["loop"], e["point"], e["x"]) for e in saved],
                         [(1, 1, 1, 0.0), (2, 1, 2, 10.0), (3, 1, 3, 20.0), (4, 1, 4, 10.0), (5, 1, 5, 0.0)])
        self.assertEqual({(e["x_column"], e["field_before_g"], e["field_after_g"], e["field_drift_g"]) for e in saved},
                         {("stage_angle_deg", 123.0, 123.0, 0.0)})
        finished = events[-1]
        self.assertEqual((finished["outcome"], finished["error"], finished["points_saved"], finished["status"],
                          finished["stage"]),
                         ("completed", "", 5, "Completed; KEPCO and 6221 zeroed; stage at +0.000°", "stage at +0.000°"))
        self.assertEqual(finished["outputs"], {"6221": "zeroed", "kepco": "zeroed"})
        (zeroed,) = [e for e in events if e["event"] == "outputs_zeroed"]
        self.assertEqual((zeroed["6221"], zeroed["kepco"]), ("zeroed", "zeroed"))
        # Every SHV log line of the run is a note, the Start line naming the folder.
        notes = [event["text"] for event in events if event["event"] == "note"]
        self.assertTrue(notes[0].startswith(f"SHV Angle scan; run folder: {folder} (angle.csv and angle.log)"), notes)
        self.assertTrue(set(notes) <= set(h.logged))
        self.assertIn(f"SHV run folder {folder}: completed; angle.csv and angle.log closed", h.logged)
        self.assert_closed(h)
        # Both threads wrote: the Tk thread at Start and at the finish, the
        # worker during the scan; every DLL call stayed on the worker.
        threads = {}
        for name, thread in h.writes:
            threads.setdefault(name, set()).add(thread)
        tk_thread = threading.get_ident()
        self.assertEqual(threads["run_prepared"], {tk_thread})
        self.assertEqual(threads["close"], {tk_thread}, "run_finished and the close at the finish")
        for name in ("scan_started", "harmonic_pass", "field_programmed", "field_settled", "stage_move",
                     "point_saved"):
            self.assertEqual(threads[name], {h.worker}, name)
        self.assertEqual({thread for _name, thread in h.dll.calls}, {h.worker})
        # Save CSV as opens in the run folder.
        with patch.object(app.filedialog, "asksaveasfilename", return_value="") as ask:
            panel.save_ppms_field_scan_csv()
        self.assertEqual(ask.call_args.kwargs["initialdir"], str(folder))

    def test_a_name_in_use_gets_a_suffix_and_nothing_is_overwritten(self):
        h = self.make(span=("0", "10", "10"), round_trip=False)
        self.start(h)
        first = Path(h.folder.name) / "angle"
        kept = {p.name: p.read_bytes() for p in first.iterdir()}
        # A file of the same name, as another run might have left.
        (Path(h.folder.name) / "angle_01").write_text("not a folder", encoding="utf-8")
        for expected in ("angle_02", "angle_03"):
            self.start(h)
            h.showerror.assert_not_called()
            folder = Path(h.folder.name) / expected
            self.assertEqual(h.panel.rte_run_folder, folder)
            self.assertEqual(sorted(p.name for p in folder.iterdir()), [f"{expected}.csv", f"{expected}.log"])
            self.assertEqual(len(self.rows(h)), 2)
        self.assertEqual({p.name: p.read_bytes() for p in first.iterdir()}, kept, "the first run untouched")
        self.assertEqual((Path(h.folder.name) / "angle_01").read_text(encoding="utf-8"), "not a folder")
        # A blank name: SHV_<date>_<time>.
        with patch.object(app.simpledialog, "askstring", return_value=""):
            self.start(h)
        self.assertRegex(h.panel.rte_run_folder.name, r"^SHV_\d{8}_\d{6}(_\d\d)?$")

    def test_a_folder_that_cannot_be_made_starts_nothing(self):
        h = self.make()
        missing = Path(h.folder.name) / "missing"
        with patch.object(app.filedialog, "askdirectory", return_value=str(missing)):
            self.start(h)
        h.showerror.assert_called_once()
        self.assertEqual(h.showerror.call_args.args[0], "SHV run folder")
        self.assertIn("the scan was not started", h.showerror.call_args.args[1])
        self.assertEqual([e for e in h.log if e[0] in ("6221", "kepco", "lockins")], [], "nothing switched on")
        self.assertEqual([e for e in h.log if e[:2] == ("stage", "move")], [])
        self.assertFalse(h.panel.ppms_field_scan_running)
        self.assertFalse(missing.exists())
        self.assertIsNone(h.panel.__dict__.get("rte_recorder"))

    def test_a_first_log_entry_that_cannot_be_written_starts_nothing(self):
        h = self.make()
        real = app.scan_log_line

        def line(name, details):
            if name == "run_prepared":
                raise OSError(28, "No space left on device")
            return real(name, details)
        with patch.object(app, "scan_log_line", line):
            self.start(h)
        self.assertEqual(h.showerror.call_args.args[0], "SHV run folder")
        self.assertIn("Could not write the run log", h.showerror.call_args.args[1])
        self.assertEqual([e for e in h.log if e[0] in ("6221", "kepco", "lockins", "stage")], [])
        folder = Path(h.folder.name) / "angle"
        with (folder / "angle.log").open(encoding="utf-8") as handle:
            (closing,) = [json.loads(text) for text in handle]
        self.assertEqual((closing["event"], closing["outcome"]), ("run_finished", "not_started"))
        self.assertIsNone(h.panel.__dict__.get("rte_recorder"))

    def test_a_field_scan_is_logged_in_order(self):
        h = self.make_mode("Field", ("0", "1", "1"))
        self.start(h)
        h.showerror.assert_not_called()
        events = self.events(h)
        expected = ["run_prepared", "scan_started", "harmonic_pass"]
        for _point in range(3):
            expected += ["field_programmed", "field_settled", "point_saved"]
        self.assertEqual(self.without_notes(events), expected + ["outputs_zeroed", "run_finished"])
        self.assertEqual([e["current_a"] for e in events if e["event"] == "field_programmed"], [0.0, 1.0, 0.0])
        self.assertEqual({(e["x_column"], e["x"]) for e in events if e["event"] == "point_saved"},
                         {("measured_field_g", 123.0)})
        self.assertEqual(events[0]["kepco"], {"current_a": "the plan", "voltage_limit_v": 5.0})
        self.assertNotIn("stage_at_start", events[0])
        self.assertEqual((events[-1]["outcome"], events[-1]["points_saved"], events[-1]["status"]),
                         ("completed", 3, "Completed; KEPCO and 6221 zeroed"))
        self.assertEqual(self.names(h, "kepco", "6221")[-3:], ZEROED)
        self.assertNotIn("stage", events[-1])
        self.assert_closed(h)

    def test_a_current_scan_is_logged_in_order(self):
        h = self.make_mode("Current", ("0.5", "1.5", "0.5"))
        self.start(h)
        h.showerror.assert_not_called()
        events = self.events(h)
        self.assertEqual(self.without_notes(events),
                         ["run_prepared", "scan_started", "harmonic_pass", "point_saved", "point_saved",
                          "point_saved", "outputs_zeroed", "run_finished"])
        self.assertEqual([(e["target"], e["x_column"], e["x"]) for e in events if e["event"] == "point_saved"],
                         [(0.5, "6221_current_rms_ma", 0.5), (1.0, "6221_current_rms_ma", 1.0),
                          (1.5, "6221_current_rms_ma", 1.5)])
        self.assertEqual(events[0]["kepco"], {"used": False})
        self.assertEqual((events[-1]["outcome"], events[-1]["status"]),
                         ("completed", "Completed; KEPCO and 6221 zeroed"))
        # A Current scan never drives the KEPCO; it is zeroed at the end all
        # the same (user, 2026-10-18), after the 6221.
        self.assertEqual([e[:2] for e in h.log if e[0] == "kepco"], [("kepco", "force_off")])
        self.assertEqual(self.names(h, "kepco", "6221")[-3:], ZEROED)
        self.assert_closed(h)

    def test_a_stop_during_a_move_is_logged(self):
        h = self.make(span=("0", "90", "90"), round_trip=False)
        panel = h.panel
        h.move_hooks[90.0] = lambda: h.clock.hooks.append((h.clock.t + 3.0, panel.stop_ppms_field_scan))
        self.start(h)
        h.showerror.assert_not_called()
        events = self.events(h)
        self.assertEqual(self.without_notes(events),
                         ["run_prepared", "scan_started", "harmonic_pass", "field_programmed", "field_settled",
                          "stage_move", "point_saved", "stop_requested", "outputs_zeroed", "run_finished"])
        (stop,) = [e for e in events if e["event"] == "stop_requested"]
        self.assertEqual(stop["button"], "Stop")
        finished = events[-1]
        self.assertEqual((finished["outcome"], finished["error"], finished["points_saved"]), ("stopped", "", 1))
        self.assertTrue(finished["stage"].startswith("stage stopped at +"), finished)
        self.assertEqual(finished["status"], panel.ppms_field_scan_status_var.get())
        self.assertIn("SHV stopped at a stage move", finished["stop_reason"])
        self.assert_closed(h)

    def test_a_stage_failure_is_logged(self):
        h = self.make(EndsShort(), span=("0", "10", "10"), round_trip=False)
        self.start(h)
        events = self.events(h)
        self.assertEqual(self.without_notes(events),
                         ["run_prepared", "scan_started", "harmonic_pass", "field_programmed", "field_settled",
                          "stage_move", "point_saved", "stage_failure", "outputs_zeroed", "run_finished"])
        (failure,) = [e for e in events if e["event"] == "stage_failure"]
        self.assertIn("not at the target", failure["error"])
        self.assertEqual(failure["stage"], "stage stopped at +9.999°")
        finished = events[-1]
        self.assertEqual((finished["outcome"], finished["points_saved"], finished["stage"]),
                         ("error", 1, "stage stopped at +9.999°"))
        self.assertIn("not at the target", finished["error"])
        self.assertTrue(finished["status"].startswith("Failed: "))
        self.assertIn("SHV Angle scan, after the stage failure: stage stopped at +9.999°",
                      [e["text"] for e in events if e["event"] == "note"])
        self.assert_closed(h)

    def test_a_log_write_failure_ends_the_scan_safely(self):
        h = self.make(span=("0", "20", "10"), round_trip=False)
        panel = h.panel
        # The write after the move to 20 deg (its stage_move) fails once.
        h.move_hooks[20.0] = lambda: fail_next_log_write(panel.rte_recorder)
        self.start(h)
        h.showerror.assert_called_once()
        self.assertEqual(h.showerror.call_args.args[0], "SHV scan")
        self.assertIn("The SHV log could not be written", h.showerror.call_args.args[1])
        self.assertTrue(panel.ppms_field_scan_status_var.get().startswith("Failed: The SHV log could not be written"))
        # The normal failure path, as for a failed live-CSV row: the 6221 off,
        # the KEPCO's field held, the stage at rest where it was (no STOP).
        self.assertEqual(self.names(h, "kepco", "6221"),
                         [("6221", "on"), ("kepco", "set_output"), ("6221", "off")] + ZEROED)
        self.assertNotIn("STOP", h.sim.received)
        self.assertFalse(h.stage.motion_may_run)
        self.assertEqual([float(r["stage_angle_deg"]) for r in self.rows(h)], [0.0, 10.0], "no row after the failure")
        events = self.events(h)
        self.assertEqual(self.without_notes(events),
                         ["run_prepared", "scan_started", "harmonic_pass", "field_programmed", "field_settled",
                          "stage_move", "point_saved", "stage_move", "point_saved", "outputs_zeroed",
                          "run_finished"])
        self.assertEqual((events[-1]["outcome"], events[-1]["points_saved"], events[-1]["stage"]),
                         ("error", 2, "stage at +20.000°"))
        self.assertIn("No space left on device", events[-1]["error"])
        self.assert_closed(h)

    def test_a_stop_button_from_the_tk_thread_is_logged(self):
        h = self.make(span=("0", "10", "10"), round_trip=False, tc_zurich="1")
        panel = h.panel
        panel.start_ppms_field_scan()
        self.pump(h, until=lambda: panel.ppms_field_scan_running)
        deadline = time.monotonic() + 30
        while not [e for e in h.log if e[:2] == ("stage", "at")] and time.monotonic() < deadline:
            time.sleep(0.02)
        panel._rte_stage_stop()
        self.pump(h)
        events = self.events(h)
        (stop,) = [e for e in events if e["event"] == "stop_requested"]
        self.assertEqual(stop["button"], "Stop stage")
        self.assertIn(("stop_requested", threading.get_ident()), h.writes, "written from the Tk thread")
        self.assertEqual(events[-1]["outcome"], "stopped")
        self.assert_closed(h)

    def test_closing_the_program_mid_scan_closes_the_log(self):
        h = self.make(span=("0", "10", "10"), round_trip=False, tc_zurich="1")
        panel = h.panel
        panel.zurich_executor = Mock()
        panel.start_ppms_field_scan()
        self.pump(h, until=lambda: panel.ppms_field_scan_running)
        deadline = time.monotonic() + 30
        while not [e for e in h.log if e[:2] == ("stage", "at")] and time.monotonic() < deadline:
            time.sleep(0.02)
        panel.safe_close_ppms()
        h.executor.shutdown(wait=True)
        events = self.events(h)
        # The stop events are set before stop_requested is written (review,
        # 2026-10-18), so the worker's outputs_zeroed may come first.
        last = self.without_notes(events)[-3:]
        self.assertEqual((sorted(last[:2]), last[2]), (["outputs_zeroed", "stop_requested"], "run_finished"))
        (stop,) = [e for e in events if e["event"] == "stop_requested"]
        self.assertEqual(stop["button"], "the program closing")
        # The close leaves neither output on: the scan's end zeroed both on
        # the worker, before the clean-up job closed the stage.
        self.assertEqual(self.names(h, "kepco", "6221")[-3:], ZEROED)
        finished = events[-1]
        self.assertEqual((finished["outcome"], finished["status"], finished["points_saved"]),
                         ("stopped", "the program closed", 0))
        # Closed by the clean-up job on the worker, after the scan, before the stage.
        self.assertIn(("close", h.worker), h.writes)
        self.assertIn(("stop_requested", threading.get_ident()), h.writes)
        self.assertIsNone(panel.rte_recorder)
        self.assertEqual({thread for _name, thread in h.dll.calls}, {h.worker})

    def test_a_scan_the_worker_cannot_take_closes_its_log(self):
        h = self.make_mode("Field", ("0", "1", "1"))
        panel = h.panel
        refusing = Mock()
        refusing.submit.side_effect = RuntimeError("cannot schedule new futures after shutdown")
        panel.ppms_executor = refusing
        panel.start_ppms_field_scan()
        self.pump(h)        # the dialog opens from the Tk event loop (review, 2026-10-18)
        self.assertEqual(h.showerror.call_args.args[0], "SHV scan not started")
        self.assertFalse(panel.ppms_field_scan_running)
        events = self.events(h)
        self.assertEqual(self.without_notes(events), ["run_prepared", "run_finished"])
        self.assertEqual((events[-1]["outcome"], events[-1]["points_saved"]), ("not_started", 0))
        self.assertIsNone(panel.rte_recorder)



class ScanEndZeroTests(RunLogBase):
    """User, 2026-10-18: "when finish scan in SHV, automatically zero the
    Kepco and 6221 pls": each ending zeroes the 6221 (off, read back), then
    the KEPCO (its Zero / OFF), on the worker, each step tried whatever the
    other did; a zero that fails is said on the status line, in a dialog
    and in the log."""

    def test_a_kepco_zero_that_fails_is_reported(self):
        h = self.make(span=("0", "10", "10"), round_trip=False)

        def refused():
            h.log.append(("kepco", "force_off", threading.get_ident()))
            raise RuntimeError("KEPCO 4886 zero/OFF not confirmed: 'OUTP OFF' VI_ERROR_TMO")
        h.kepco.force_output_off = refused
        self.start(h)
        panel = h.panel
        self.assertEqual(self.names(h, "kepco", "6221")[-3:], ZEROED)
        self.assertEqual(panel.ppms_field_scan_status_var.get(), "Completed; 6221 zeroed; KEPCO zero FAILED; stage at +10.000°")
        h.showerror.assert_called_once()
        title, message = h.showerror.call_args.args
        self.assertEqual(title, "SHV outputs")
        self.assertIn("CHECK IT", message)
        self.assertIn("KEPCO zero FAILED: KEPCO 4886 zero/OFF not confirmed: 'OUTP OFF' VI_ERROR_TMO", message)
        self.assertNotEqual(panel.rte_drive_live.get(), "KEPCO: zeroed and output OFF")
        finished = self.events(h)[-1]
        self.assertEqual(finished["outputs"]["kepco"],
                         "zero FAILED: KEPCO 4886 zero/OFF not confirmed: 'OUTP OFF' VI_ERROR_TMO")
        self.assertEqual(finished["outcome"], "completed")
        self.assertIn("SHV scan end: 6221 zeroed; KEPCO zero FAILED: KEPCO 4886 zero/OFF not confirmed: "
                      "'OUTP OFF' VI_ERROR_TMO", h.logged)
        self.assertEqual({entry[-1] for entry in h.log}, {h.worker})

    def test_a_6221_off_that_fails_still_zeroes_the_kepco(self):
        h = self.make_mode("Field", ("0", "1", "1"))

        def refused():
            h.log.append(("6221", "off", threading.get_ident()))
            raise RuntimeError("6221 output OFF not confirmed: VI_ERROR_TMO")
        h.panel._stop_ppms_transport_source = refused
        self.start(h)
        panel = h.panel
        self.assertEqual(self.names(h, "kepco", "6221")[-2:], [("6221", "off"), ("kepco", "force_off")],
                         "the KEPCO after the failed 6221 off (no read-back of a refused off)")
        self.assertEqual(panel.ppms_field_scan_status_var.get(), "Completed; 6221 zero FAILED; KEPCO zeroed: check it")
        self.assertEqual(h.showerror.call_args.args[0], "SHV outputs")
        self.assertEqual(self.events(h)[-1]["outputs"],
                         {"6221": "zero FAILED: 6221 output OFF not confirmed: VI_ERROR_TMO", "kepco": "zeroed"})
        self.assertEqual({entry[-1] for entry in h.log}, {h.worker})

    def test_a_failed_scan_zeroes_both_and_its_dialog_says_so(self):
        h = self.make_mode("Field", ("0", "1", "1"))
        h.gauss.read_field = Mock(side_effect=RuntimeError("LS475 read failed"))
        self.start(h)
        # The field failure's own clean-up (the 6221 off, the KEPCO's
        # safe off), then the end's zeroing of both.
        self.assertEqual(self.names(h, "kepco", "6221"),
                         [("6221", "on"), ("kepco", "set_output"), ("6221", "off"), ("kepco", "off")] + ZEROED)
        status = h.panel.ppms_field_scan_status_var.get()
        self.assertEqual(status, "Failed: LS475 read failed", "a failure keeps its text")
        title, message = h.showerror.call_args.args
        self.assertEqual(title, "SHV scan")
        self.assertTrue(message.endswith("At the end: 6221 zeroed; KEPCO zeroed"), message)
        self.assertEqual(self.events(h)[-1]["outputs"], {"6221": "zeroed", "kepco": "zeroed"})

    def test_the_finish_status_lines(self):
        zeroed = {"6221": "zeroed", "kepco": "zeroed"}
        self.assertEqual(app.rte_outputs_text(zeroed), "KEPCO and 6221 zeroed")
        self.assertEqual(app.rte_outputs_text({"6221": "zeroed", "kepco": "not connected"}),
                         "6221 zeroed; KEPCO not connected")
        self.assertEqual(app.rte_outputs_text({"6221": "zeroed", "kepco": "zero FAILED: x"}),
                         "6221 zeroed; KEPCO zero FAILED: check it")
        self.assertEqual(app.rte_outputs_text({"6221": "zeroed", "kepco": "zero FAILED: x"}, check=False),
                         "6221 zeroed; KEPCO zero FAILED")
        self.assertEqual(app.rte_outputs_text({"6221": "zero FAILED: x", "kepco": "zeroed"}),
                         "6221 zero FAILED; KEPCO zeroed: check it")
        self.assertEqual(app.rte_outputs_text({"6221": "zero FAILED: x", "kepco": "zero FAILED: y"}),
                         "zero FAILED: 6221 and KEPCO: check them")
        self.assertEqual(app.rte_outputs_text({"6221": "zero FAILED: x", "kepco": "zero FAILED: y"}, check=False),
                         "zero FAILED: 6221 and KEPCO")
        self.assertEqual(app.rte_outputs_text(None), "KEPCO and 6221 not confirmed zeroed: check them")


class OuterScanTests(RunLogBase):
    """Angle+Field and Angle+Current, end to end (user, 2026-10-18: "add the
    angle + field scan mode, field is the outside loop"; "also angle+current
    mode, the current is outside loop"), on the real page, worker and stage
    controller of the scans above."""

    def make_outer(self, mode, outer, span=("0", "10", "10"), **kwargs):
        h = self.make(span=span, round_trip=False, **kwargs)
        panel = h.panel
        with patch.object(app.CombinedControlApp, "_draw_nonlocal_series"):
            panel.ppms_simple_mode.set(mode)
            panel._rte_mode_changed()
        self.assertIs(panel.rte_round_trip.get(), False, "round trip off by default")
        for variable, value in zip(panel.ppms_simple_range, span):
            variable.set(value)
        for variable, value in zip(panel.rte_outer_ranges[mode], outer):
            variable.set(value)
        h.configured, h.updated = [], []

        def configure(settings):
            h.configured.append(dict(settings))
            h.log.append(("6221", "on", settings["current_rms_ma"], settings["frequency_hz"], threading.get_ident()))

        def update(settings):
            h.updated.append(dict(settings))
            h.log.append(("6221", "current", settings["current_rms_ma"], threading.get_ident()))
        panel._configure_ppms_transport_scan = configure
        panel._update_ppms_transport_current = update
        h.log.clear()
        return h

    @staticmethod
    def sequence(h):
        """The calls in order, without their threads: a move's angle, a
        KEPCO step's and a 6221 change's current, the 6221 on's current, the
        mode of set_output."""
        calls = []
        for entry in h.log:
            if entry[0] in ("gauss", "lockins") or entry[1] in ("off", "OUTP?", "force_off"):
                calls.append(entry[:2])
            elif entry[1] in ("on", "set_output"):
                calls.append(entry[:3])
            else:
                calls.append(entry[:-1])
        return calls

    @staticmethod
    def point(angle, first=False):
        return [("stage", "move", angle), ("stage", "at", angle), ("gauss", "read"),
                ("lockins", "first" if first else "read"), ("gauss", "read")]

    def test_angle_field_steps_the_field_outside_the_angle_sweep(self):
        h = self.make_outer("Angle+Field", ("0", "2", "1"))
        self.start(h)
        panel = h.panel
        h.showerror.assert_not_called()
        title, question = h.askyesno.call_args.args[:2]
        self.assertEqual(title, "SHV Angle+Field scan")
        self.assertIn("3 field steps x 2 angles x 1 harmonic pass(es) = 6 points.", question)
        self.assertIn("6 stage moves (2 of them back to 0°), 50° of turning in all", question)
        # Per field: the KEPCO (set_output for the first, set_scan_current
        # after), the LS475 settle, then the sweep from the first angle.
        settle = [("gauss", "read")] * app.STFMR_FIELD_SETTLE_REQUIRED_SAMPLES
        expected = [("6221", "on", panel_current(panel)), ("kepco", "set_output", "CURR")] + settle
        expected += self.point(0.0, first=True) + self.point(10.0)
        for value in (1.0, 2.0):
            expected += [("kepco", "set_scan_current", value)] + settle + self.point(0.0) + self.point(10.0)
        self.assertEqual(self.sequence(h), expected + ZEROED)
        self.assertEqual([e[2:5] for e in h.log if e[:2] == ("kepco", "set_output")], [("CURR", 0.0, 5.0)])
        self.assertEqual({entry[-1] for entry in h.log}, {h.worker})
        self.assertEqual({thread for _name, thread in h.dll.calls}, {h.worker})
        # The rows: Angle's columns, the outer step's number last.
        rows = self.rows(h)
        self.assertEqual([(float(r["kepco_programmed_current_a"]), float(r["stage_angle_deg"]), r["outer_field_index"])
                          for r in rows],
                         [(0., 0., "1"), (0., 10., "1"), (1., 0., "2"), (1., 10., "2"), (2., 0., "3"), (2., 10., "3")])
        # The magnet supply's two columns after them since 2026-10-21 (user).
        self.assertEqual(list(rows[0])[-4:], ["field_drift_g", "outer_field_index", "magnet_supply",
                                              "magnet_measured_current_a"])
        self.assertEqual({(r["scan_mode"], r["kepco_voltage_limit_v"]) for r in rows}, {("Angle+Field", "5.0")})
        self.assertEqual([r["point"] for r in rows], ["1", "2"] * 3)
        self.assertEqual([r["first_point_windows"] for r in rows], ["2", "1", "1", "1", "1", "1"])
        self.assertEqual(panel.ppms_field_scan_status_var.get(), "Completed; KEPCO and 6221 zeroed; stage at +10.000°")
        # The log: an event per field step, the settle with its number.
        events = self.events(h)
        expected_events = ["run_prepared", "scan_started", "harmonic_pass"]
        for _field in range(3):
            expected_events += ["field_programmed", "field_settled", "stage_move", "point_saved", "stage_move",
                                "point_saved"]
        self.assertEqual(self.without_notes(events), expected_events + ["outputs_zeroed", "run_finished"])
        self.assertEqual([(e["step_index"], e["steps"], e["current_a"]) for e in events
                          if e["event"] == "field_programmed"], [(1, 3, 0.0), (2, 3, 1.0), (3, 3, 2.0)])
        self.assertEqual([e["step_index"] for e in events if e["event"] == "field_settled"], [1, 2, 3])
        self.assertEqual([e["step_index"] for e in events if e["event"] == "point_saved"], [1, 1, 2, 2, 3, 3])
        prepared = events[0]
        self.assertEqual(prepared["mode"], "Angle+Field")
        self.assertEqual((prepared["outer"]["values"], prepared["outer"]["unit"], prepared["outer"]["steps"]),
                         ([0.0, 1.0, 2.0], "A (KEPCO)", 3))
        self.assertEqual(prepared["kepco"], {"current_a": "the outer loop", "voltage_limit_v": 5.0})
        self.assertEqual((prepared["plan"]["points"], prepared["plan"]["total_points"]), (2, 6))
        self.assertEqual(events[0]["stage_at_start"]["stage_check_mst"], 0, "the stage check at Start")
        self.assertEqual(events[-1]["outputs"], {"6221": "zeroed", "kepco": "zeroed"})
        self.assert_closed(h)
        # The plot: against the angle, a curve per field, named by its current.
        drawn = next(c for c in reversed(h.draw.call_args_list) if c.args[1])
        self.assertEqual(drawn.args[2], "Sample angle (°)")
        self.assertEqual({name.split(" ")[1] for name in drawn.args[1]}, {"+0", "+1", "+2"})
        for name, points in drawn.args[1].items():
            self.assertEqual([x for x, _y in points], [0.0, 10.0], name)

    def test_angle_field_harmonic_passes_run_the_whole_scan_each(self):
        h = self.make_outer("Angle+Field", ("0", "1", "1"), meter="sr830")
        self.start(h)
        h.showerror.assert_not_called()
        rows = self.rows(h)
        self.assertEqual([(r["harmonic_pass"], r["outer_field_index"], float(r["stage_angle_deg"])) for r in rows],
                         [(p, f, a) for p in "123" for f in "12" for a in (0.0, 10.0)])
        # The output switched on once; each later pass steps back to the first field.
        self.assertEqual([e[1:3] for e in h.log if e[0] == "kepco" and e[1] in ("set_output", "set_scan_current")],
                         [("set_output", "CURR"), ("set_scan_current", 1.0), ("set_scan_current", 0.0),
                          ("set_scan_current", 1.0), ("set_scan_current", 0.0), ("set_scan_current", 1.0)])
        self.assertEqual(len([e for e in h.log if e[:2] == ("6221", "on")]), 1)
        self.assertEqual([e[1] for e in h.log if e[0] == "sr830"], ["HARM 2", "HARM 3"])
        self.assertEqual(h.panel.ppms_field_scan_total, 12)
        self.assertEqual({entry[-1] for entry in h.log}, {h.worker})

    def test_stop_while_a_field_settles(self):
        h = self.make_outer("Angle+Field", ("0", "2", "1"))
        panel, real_read = h.panel, h.gauss.read_field
        stepped, stopped = [], []
        real_step = h.kepco.set_scan_current
        h.kepco.set_scan_current = lambda value: (stepped.append(value), real_step(value))

        def read():
            reading = real_read()
            if stepped and not stopped:
                stopped.append(True)
                panel.stop_ppms_field_scan()      # at the first read of field 2's settle
            return reading
        h.gauss.read_field = read
        self.start(h)
        h.showerror.assert_not_called()
        self.assertEqual(panel.ppms_field_scan_status_var.get(), "Stopped; KEPCO and 6221 zeroed; stage at +10.000°")
        self.assertEqual(stepped, [1.0], "no further field")
        moves = [e[2] for e in h.log if e[:2] == ("stage", "move")]
        self.assertEqual(moves, [0.0, 10.0], "no move after the Stop: the stage stays at 10°")
        self.assertEqual(len(self.rows(h)), 2)
        self.assertEqual(self.names(h, "kepco", "6221")[-3:], ZEROED)
        events = self.without_notes(self.events(h))
        self.assertEqual(events.count("field_programmed"), 2)
        self.assertEqual(events.count("field_settled"), 1, "field 2 never settled")
        self.assertEqual(events[-3:], ["stop_requested", "outputs_zeroed", "run_finished"])
        self.assertEqual(self.events(h)[-1]["outcome"], "stopped")
        self.assert_closed(h)

    def test_stop_during_the_return_to_the_first_angle(self):
        h = self.make_outer("Angle+Field", ("0", "1", "1"), span=("0", "90", "90"))
        panel = h.panel
        real_step = h.kepco.set_scan_current

        def step(value):
            real_step(value)
            # The next move is the return to 0°: Stop 3 s into it.
            h.move_hooks[0.0] = lambda: h.clock.hooks.append((h.clock.t + 3.0, panel.stop_ppms_field_scan))
        h.kepco.set_scan_current = step
        self.start(h)
        h.showerror.assert_not_called()
        self.assertIn("STOP", h.sim.received[h.sim.received.index("X900000"):])
        status = panel.ppms_field_scan_status_var.get()
        self.assertTrue(status.startswith("Stopped; KEPCO and 6221 zeroed; stage stopped at +"), status)
        where = float(status.rsplit(" ", 1)[1].rstrip("°"))
        self.assertTrue(0 < where < 90, where)
        self.assertEqual([e[2] for e in h.log if e[:2] == ("stage", "move")], [0.0, 90.0, 0.0])
        self.assertEqual(len(self.rows(h)), 2, "field 2 saved nothing")
        self.assertEqual(self.names(h, "kepco", "6221")[-3:], ZEROED)
        self.assertFalse(h.stage.motion_may_run)

    def test_a_stage_failure_in_the_first_sweep(self):
        h = self.make_outer("Angle+Field", ("0", "1", "1"), sim=EndsShort())
        self.start(h)
        panel = h.panel
        h.showerror.assert_called_once()
        message = h.showerror.call_args.args[1]
        self.assertIn("not at the target", message)
        self.assertIn("Stage stopped at +9.999°", message)
        self.assertIn("At the end: 6221 zeroed; KEPCO zeroed", message)
        self.assertTrue(panel.ppms_field_scan_status_var.get().startswith("Failed: "))
        self.assertEqual(self.names(h, "kepco", "6221"),
                         [("6221", "on"), ("kepco", "set_output"), ("6221", "off")] + ZEROED,
                         "the 6221 off at once, no next field, then both zeroed; the stage not moved again")
        self.assertEqual([e[2] for e in h.log if e[:2] == ("stage", "move")], [0.0, 10.0])
        events = self.without_notes(self.events(h))
        self.assertEqual(events[-3:], ["stage_failure", "outputs_zeroed", "run_finished"])
        self.assert_closed(h)

    def test_a_field_that_cannot_be_set_switches_the_kepco_off_and_zeroes_both(self):
        h = self.make_outer("Angle+Field", ("0", "1", "1"))
        h.kepco.set_scan_current = Mock(side_effect=RuntimeError("KEPCO 4886 'CURR 1.000000' VI_ERROR_TMO"))
        self.start(h)
        self.assertEqual(self.names(h, "kepco", "6221"),
                         [("6221", "on"), ("kepco", "set_output"), ("6221", "off"), ("kepco", "off")] + ZEROED)
        self.assertEqual([e[2] for e in h.log if e[:2] == ("stage", "move")], [0.0, 10.0], "no return move")
        self.assertTrue(h.panel.ppms_field_scan_status_var.get().startswith("Failed: KEPCO 4886"))
        self.assertEqual(self.events(h)[-1]["outcome"], "error")

    def test_angle_current_steps_the_6221_outside_the_angle_sweep(self):
        h = self.make_outer("Angle+Current", ("0.5", "1.5", "0.5"))
        self.start(h)
        panel = h.panel
        h.showerror.assert_not_called()
        title, question = h.askyesno.call_args.args[:2]
        self.assertEqual(title, "SHV Angle+Current scan")
        self.assertIn("the KEPCO is set once to +1.5 A (voltage limit 5 V)", question)
        settle = [("gauss", "read")] * app.STFMR_FIELD_SETTLE_REQUIRED_SAMPLES
        # The 6221 on at the first current, the field set once and settled,
        # then per current its amplitude (the first point read with the
        # agreeing windows) and the sweep from the first angle.
        expected = [("6221", "on", 0.5), ("kepco", "set_output", "CURR")] + settle
        expected += self.point(0.0, first=True) + self.point(10.0)
        for value in (1.0, 1.5):
            expected += [("6221", "current", value)] + self.point(0.0, first=True) + self.point(10.0)
        self.assertEqual(self.sequence(h), expected + ZEROED)
        self.assertEqual([e[2:5] for e in h.log if e[:2] == ("kepco", "set_output")], [("CURR", 1.5, 5.0)])
        # The 6221's range is fixed for the largest current, as a Current scan's.
        peak = 2 ** .5 * 1.5 * 1e-3
        self.assertEqual({round(s["continuous_sweep_peak_a"], 12) for s in h.configured + h.updated},
                         {round(peak, 12)})
        self.assertEqual({entry[-1] for entry in h.log}, {h.worker})
        rows = self.rows(h)
        self.assertEqual([(float(r["6221_current_rms_ma"]), float(r["stage_angle_deg"]), r["outer_current_index"],
                           r["first_point_windows"]) for r in rows],
                         [(.5, 0., "1", "2"), (.5, 10., "1", "1"), (1., 0., "2", "2"), (1., 10., "2", "1"),
                          (1.5, 0., "3", "2"), (1.5, 10., "3", "1")])
        # The magnet supply's two columns after them since 2026-10-21 (user).
        self.assertEqual(list(rows[0])[-4:], ["field_drift_g", "outer_current_index", "magnet_supply",
                                              "magnet_measured_current_a"])
        self.assertEqual({r["kepco_programmed_current_a"] for r in rows}, {"1.5"})
        self.assertEqual(panel.ppms_field_scan_status_var.get(), "Completed; KEPCO and 6221 zeroed; stage at +10.000°")
        events = self.events(h)
        expected_events = ["run_prepared", "scan_started", "harmonic_pass", "current_programmed", "field_programmed",
                           "field_settled", "stage_move", "point_saved", "stage_move", "point_saved"]
        for _current in range(2):
            expected_events += ["current_programmed", "stage_move", "point_saved", "stage_move", "point_saved"]
        self.assertEqual(self.without_notes(events), expected_events + ["outputs_zeroed", "run_finished"])
        self.assertEqual([(e["step_index"], e["current_rms_ma"]) for e in events if e["event"] == "current_programmed"],
                         [(1, .5), (2, 1.), (3, 1.5)])
        prepared = events[0]
        self.assertEqual((prepared["outer"]["values"], prepared["outer"]["unit"]), ([.5, 1., 1.5], "mA RMS (6221)"))
        self.assertEqual(prepared["kepco"], {"current_a": 1.5, "voltage_limit_v": 5.0, "held": True})
        self.assert_closed(h)
        drawn = next(c for c in reversed(h.draw.call_args_list) if c.args[1])
        self.assertEqual({" ".join(name.split(" ")[1:3]) for name in drawn.args[1]}, {"0.5 mA", "1 mA", "1.5 mA"})

    def test_angle_current_stop_mid_move_and_a_6221_step_failure(self):
        h = self.make_outer("Angle+Current", ("0.5", "1", "0.5"), span=("0", "90", "90"))
        panel = h.panel
        h.move_hooks[90.0] = lambda: h.clock.hooks.append((h.clock.t + 3.0, panel.stop_ppms_field_scan))
        self.start(h)
        h.showerror.assert_not_called()
        self.assertTrue(panel.ppms_field_scan_status_var.get().startswith(
            "Stopped; KEPCO and 6221 zeroed; stage stopped at +"))
        self.assertNotIn(("6221", "current"), self.names(h, "6221"), "the next current never set")
        self.assertEqual(self.names(h, "kepco", "6221")[-3:], ZEROED)
        # A 6221 step that fails: the scan fails, both are zeroed, the stage stays.
        h = self.make_outer("Angle+Current", ("0.5", "1", "0.5"))
        h.panel._update_ppms_transport_current = Mock(side_effect=RuntimeError("6221 live amplitude readback mismatch"))
        self.start(h)
        self.assertTrue(h.panel.ppms_field_scan_status_var.get().startswith("Failed: 6221 live amplitude"))
        self.assertEqual(self.names(h, "kepco", "6221"),
                         [("6221", "on"), ("kepco", "set_output"), ("6221", "off")] + ZEROED)
        self.assertEqual([e[2] for e in h.log if e[:2] == ("stage", "move")], [0.0, 10.0])
        self.assertEqual(self.events(h)[-1]["outputs"], {"6221": "zeroed", "kepco": "zeroed"})

    def test_the_other_modes_are_unchanged_by_the_outer_row(self):
        # An Angle scan after an Angle+Field one: no outer column, one held field.
        h = self.make_outer("Angle+Field", ("0", "1", "1"))
        self.start(h)
        panel = h.panel
        with patch.object(app.CombinedControlApp, "_draw_nonlocal_series"):
            panel.ppms_simple_mode.set("Angle")
            panel._rte_mode_changed()
        for variable, value in zip(panel.ppms_simple_range, ("0", "10", "10")):
            variable.set(value)
        h.log.clear()
        self.start(h)
        rows = self.rows(h)
        self.assertEqual(list(rows[0])[-3:], ["field_drift_g", "magnet_supply", "magnet_measured_current_a"])
        self.assertEqual({r["scan_mode"] for r in rows}, {"Angle"})
        self.assertEqual([e[1] for e in h.log if e[0] == "kepco"], ["set_output", "force_off"])
        drawn = next(c for c in reversed(h.draw.call_args_list) if c.args[1])
        self.assertEqual(sorted(drawn.args[1]), ["Zurich P1 L1"], "one curve, named as before")
        self.assertNotIn("series_labels", drawn.kwargs)


def panel_current(panel):
    """The 6221 current the page's box holds (the Field and Angle scans' excitation)."""
    return float(panel.ppms_transport_current_var.get())


if __name__ == "__main__":
    unittest.main()
