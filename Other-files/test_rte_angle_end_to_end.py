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

    def safe_output_off(self):
        self.log.append(("kepco", "off", threading.get_ident()))
        self.output_owned = False

    def force_output_off(self):
        self.log.append(("kepco", "force_off", threading.get_ident()))

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


def build_page():
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
        return app.RTEPanel(parent, rte_tests.Widget())


class EndToEnd(unittest.TestCase):
    def make(self, sim=None, span=("0", "20", "10"), loops="1", round_trip=True, meter="zurich",
             tc_zurich="0.001"):
        """The page, ready for an Angle scan: the safety tick, every
        instrument connected (the stage through its real Connect, on the
        worker), Angle chosen and the range set; ``meter``: "zurich" (RT-E's
        default, the Zurich alone at 1+2+3) or "sr830" (SR830-1 alone at
        1+2+3, three harmonic passes)."""
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
        panel.thread_log = h.logged.append
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
        stage = app.NewmarkNSCA1Controller(h.logged.append, owner_scope="RT-E",
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
        if meter == "sr830":
            panel.ppms_instrument_controllers["830_a"] = types.SimpleNamespace(
                connected=True, label="SR830-1", resource_name="GPIB0::8::INSTR", disconnect=lambda: None)
            panel.ppms_lockin_selection["830_a"].set(True)
            panel.ppms_lockin_selection["zurich"].set(False)
            panel.ppms_transport_harmonic_a_var.set("1+2+3")
            panel.ppms_sr830_time_constant_codes = {"830_a": 0}     # 10 us: no real wait
            h.harmonic = {"830_a": 1}

            def write(key, command):
                h.log.append(("sr830", command, threading.get_ident()))
                if command.startswith("HARM "):
                    h.harmonic[key] = int(command.split()[1])

            def query(key, command, count):
                assert command == "HARM?", command
                return (h.harmonic[key],)
            panel._ppms_write_sr830_checked = write
            panel._ppms_query_numbers = query
        h.points = 0

        def sample(settings_lockins):
            h.points += 1
            values = {"zurich_harmonic": "1+2+3"}
            for demod in (1, 2, 3):
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
        points = sequence[2 + app.STFMR_FIELD_SETTLE_REQUIRED_SAMPLES:]
        expected = []
        for index, angle in enumerate((0.0, 10.0, 20.0, 10.0, 0.0)):
            expected += [("stage", "move", angle), ("stage", "at", angle), ("gauss", "read"),
                         ("lockins", "first" if index == 0 else "read"), ("gauss", "read")]
        self.assertEqual(points, expected)
        self.assertEqual([e[2:5] for e in h.log if e[:2] == ("kepco", "set_output")], [("CURR", 1.5, 5.0)])
        self.assertEqual(self.names(h, "kepco", "6221"), [("6221", "on"), ("kepco", "set_output")],
                         "the KEPCO and the 6221 are held after the scan, as on a Field scan")
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
        self.assertEqual({(r["module"], r["scan_mode"], r["loop"]) for r in rows}, {("RT-E", "Angle", "1")})
        self.assertEqual([r["point"] for r in rows], ["1", "2", "3", "4", "5"])
        self.assertEqual({r["kepco_programmed_current_a"] for r in rows}, {"1.5"})
        self.assertEqual({r["measured_field_g"] for r in rows}, {"123.0"})
        self.assertEqual({r["kepco_voltage_limit_v"] for r in rows}, {"5.0"})
        self.assertEqual(rows[0]["first_point_windows"], "2")
        # The finish and the live lines.
        self.assertEqual(panel.ppms_field_scan_status_var.get(), "Completed; outputs held; stage at +0.000°")
        self.assertEqual(panel.rte_stage_live.get(), "Stage: +0.000° (10000 steps/°)")
        self.assertEqual(panel.rte_field_live.get(), "LS475 field: +123 G")
        self.assertEqual(panel.ppms_field_scan_total, 5)
        # The plots: against the angle read back, every point.
        x_label, series = h.draw.call_args.args[2], h.draw.call_args.args[1]
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
        series = h.draw.call_args.args[1]
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

    def test_the_zurich_alone_is_ready_by_default(self):
        h = self.make(span=("0", "0", "5"), round_trip=False)
        panel = h.panel
        self.assertEqual(panel.ppms_lockin_selection["zurich"].get(), True)
        self.assertFalse(any(panel.ppms_lockin_selection[k].get() for k in app.PPMS_SR830_KEYS))
        self.start(h)
        h.showerror.assert_not_called()
        self.assertEqual([e[2] for e in h.log if e[0] == "lockins"], [()], "no SR830 read, the Zurich in the sample")
        self.assertEqual(self.rows(h)[0]["zurich_demod3_harmonic"], "3")

    def test_the_mode_change_sets_the_angle_boxes(self):
        panel = build_page()
        # The page starts on the user's Angle scan (2026-10-18).
        self.assertEqual(panel.ppms_simple_mode.get(), "Angle")
        self.assertEqual(panel.rte_scan_note.get(), "Angle: Stage °; field: KEPCO current A")
        with patch.object(app.CombinedControlApp, "_draw_nonlocal_series"):
            panel.ppms_simple_mode.set("Field")
            panel._rte_mode_changed()
        self.assertEqual(panel.rte_scan_note.get(), "Field: KEPCO A; readback: signed LS475 G")
        with patch.object(app.CombinedControlApp, "_draw_nonlocal_series") as draw:
            panel.ppms_simple_mode.set("Angle")
            panel._rte_mode_changed()
        # 0 to -360 deg in 10 deg steps (user's figure, 2026-10-18).
        self.assertEqual([v.get() for v in panel.ppms_simple_range], ["0", "-360", "10"])
        self.assertEqual(panel.rte_scan_units.get(), "Stage °")
        # The note says which box holds the field (user, 2026-10-17).
        self.assertEqual(panel.rte_scan_note.get(), "Angle: Stage °; field: KEPCO current A")
        self.assertIs(panel.rte_round_trip.get(), True, "back toward the start: the wires unwind")
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
        self.assertTrue(status.startswith("Stopped; outputs held unless OFF was requested; stage stopped at +"), status)
        where = float(status.rsplit(" ", 1)[1].rstrip("°"))
        self.assertTrue(0 < where < 90, where)
        self.assertEqual([float(r["stage_angle_deg"]) for r in self.rows(h)], [0.0], "the first angle's row kept")
        self.assertEqual(self.names(h, "kepco", "6221"), [("6221", "on"), ("kepco", "set_output")],
                         "outputs held: a Stop switches nothing off")
        self.assertFalse(panel.ppms_field_scan_running)
        self.assertIn(f"RT-E Newmark NSC-A1 connected (Ace-Series-SDE, V231BL): {h.stage.scale_text()}", h.logged)

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
                         "Stopped; outputs held unless OFF was requested; stage at +0.000°")
        self.assertEqual([e for e in h.log if e[0] == "lockins"], [], "the point was not read")
        self.assertEqual([e[2] for e in h.log if e[:2] == ("stage", "move")], [0.0], "no further move")

    def test_a_failed_move_stops_the_6221_holds_the_field_and_says_where(self):
        h = self.make(EndsShort(), span=("0", "10", "10"), round_trip=False)
        panel = h.panel
        self.start(h)
        h.showerror.assert_called_once()
        message = h.showerror.call_args.args[1]
        self.assertIn("not at the target", message)
        self.assertIn("Stage stopped at +9.999°", message)
        self.assertTrue(panel.ppms_field_scan_status_var.get().startswith("Failed: "))
        self.assertEqual(self.names(h, "kepco", "6221"), [("6221", "on"), ("kepco", "set_output"), ("6221", "off")],
                         "a stage failure: the 6221 off, the KEPCO's field held")
        self.assertIn("RT-E Angle scan, after the stage failure: stage stopped at +9.999°", h.logged)
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


if __name__ == "__main__":
    unittest.main()
