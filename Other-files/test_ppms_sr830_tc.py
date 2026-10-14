"""PPMS page, the user's two changes of 2026-10-14:

- the Zurich row's "Time constant s" reads "TC (s)", as on RT-E;
- one 'SR830 TC' dropdown for the page's three SR830s (1 ms to 10 s, the
  STFMR page's OFLT codes, default 1 s = OFLT 10, what Connect always set):
  Connect sets the choice and reads it back, a new choice is set on every
  connected SR830 on the PPMS worker, and it is locked while busy.  RT-E
  keeps OFLT 10 at Connect and has no such control.

The review of that change (2026-10-14) adds: the scan point's 180 s
autorange limit grows with the time constant; an idle poll never cuts short
the 5-time-constant wait a change sets; each scan reads OFLT? for its CSV
column; the scan-start confirmation notes a Settle s shorter than 5 long
time constants; and a mouse-wheel turn over the dropdown does nothing.
That note is superseded by the user's rule of the same day: every reading
waits at least 7 time constants, and the confirmation shows the wait.  For
that wait RT-E's scan start reads OFLT? too (a query, no write).

No Tk window and no instrument: the real pages are built on stand-in
widgets, and the SR830s are fakes that answer from the commands they were
sent.  Every message box is mocked, and creating a Tk root fails the test.
"""
import csv
import math
import tempfile
import threading
import time
import tkinter
import tkinter.messagebox
import types
import unittest
from concurrent.futures import Future
from contextlib import ExitStack
from pathlib import Path
from unittest import mock

from test_ppms_motor_debug import app
from test_ppms_two_panels import ImmediateExecutor, Var


class StandIn:
    """A widget that keeps its master, options and grid cell; any other
    widget call does nothing."""

    def __init__(self, master=None, *_args, **options):
        self.master, self.options = master, dict(options)
        self.grid_options, self.children = None, []
        self.kind = type(self).__name__
        if isinstance(master, StandIn):
            master.children.append(self)

    def grid(self, **options):
        self.grid_options = dict(options)
        return self

    def bind(self, sequence, function=None, add=None):
        self.__dict__.setdefault("bindings", {})[sequence] = function

    def grid_configure(self, **options):
        self.grid_options = {**(self.grid_options or {}), **options}

    def grid_info(self):
        return dict(self.grid_options or {})

    def grid_remove(self):
        self.grid_options = None

    def winfo_children(self):
        return list(self.children)

    def configure(self, *_style, **options):
        self.options.update(options)

    config = configure

    def cget(self, name):
        return self.options.get(name, "")

    def winfo_class(self):
        return "T" + self.kind

    def winfo_reqwidth(self):
        return 70

    def winfo_reqheight(self):
        return 16

    def __getattr__(self, name):
        if name.startswith("__"):
            raise AttributeError(name)
        return lambda *_args, **_options: None


class Kinds:
    """A stand-in ttk: one StandIn subclass per widget name, so isinstance
    checks in the program still work."""

    def __init__(self):
        self.made = []
        self.classes = {}

    def __getattr__(self, name):
        if name.startswith("__"):
            raise AttributeError(name)
        if name not in self.classes:
            made = self.made

            class Kind(StandIn):
                def __init__(self, *args, **options):
                    super().__init__(*args, **options)
                    made.append(self)

            Kind.__name__ = name
            self.classes[name] = Kind
        return self.classes[name]


def _parent():
    parent = app.CombinedControlApp.__new__(app.CombinedControlApp)
    parent.root = mock.Mock()
    parent.closing = False
    parent.ui_queue = mock.Mock()
    parent.post_ui = lambda callback: callback()
    parent.thread_log = mock.Mock()
    parent.ui_scale = parent.canvas_scale = 1.0
    return parent


def _patches(stack, ttk):
    font = types.SimpleNamespace(
        nametofont=lambda *_args, **_options: types.SimpleNamespace(measure=lambda text: 7 * len(text)),
        Font=lambda *_args, **_options: types.SimpleNamespace(measure=lambda text: 7 * len(text)))
    stack.enter_context(mock.patch.object(app, "ttk", ttk))
    stack.enter_context(mock.patch.object(app, "tkfont", font))
    stack.enter_context(mock.patch.object(app.tk, "Canvas", ttk.Canvas))
    for name in ("StringVar", "IntVar", "BooleanVar", "DoubleVar"):
        stack.enter_context(mock.patch.object(app.tk, name, Var))
    stack.enter_context(mock.patch.object(app, "ThreadPoolExecutor", ImmediateExecutor))
    stack.enter_context(mock.patch.object(app, "NonLocalACController", return_value=mock.Mock(mfli_connected=False)))
    stack.enter_context(mock.patch.object(
        app, "create_zurich_chooser",
        lambda master, *_args, **_options: types.SimpleNamespace(
            zurich_shortfall=0, grid=lambda **_o: None, configure=lambda **_o: None, master=master)))
    stack.enter_context(mock.patch.object(
        app.CombinedControlApp, "_create_connection_indicator", lambda self, master, _name: StandIn(master)))
    stack.enter_context(mock.patch.object(app.CombinedControlApp, "_set_connection_indicator", mock.Mock()))
    stack.enter_context(mock.patch.object(app.CombinedControlApp, "_redraw_ppms_transport_plot", lambda self: None))
    stack.enter_context(mock.patch.object(app.CombinedControlApp, "_draw_nonlocal_series", mock.Mock()))


def _build(cls):
    """The real panel constructor (page build and Zurich row) on stand-ins;
    afterwards its SR830 slots hold fake SR830s and Tk callbacks run at once."""
    ttk = Kinds()
    with ExitStack() as stack:
        _patches(stack, ttk)
        panel = cls(_parent(), StandIn())
    panel.post_ui = lambda callback: callback()
    panel._set_connection_indicator = mock.Mock()
    panel._redraw_ppms_transport_plot = lambda: None
    panel.thread_log = mock.Mock()
    for key in app.PPMS_SR830_KEYS:
        panel.ppms_instrument_controllers[key] = FakeController(app.PPMS_SR830_LABELS[key])
    return panel, ttk


def build_ppms_panel():
    return _build(app.PPMSPanel)


def build_rte_panel():
    return _build(app.RTEPanel)


class FakeSR830:
    """Answers the PPMS read-backs from the commands it was sent.

    keeps_time_constant: the OFLT code it reports whatever it was sent.
    answers: replies to whole commands such as "FREQ?"; hooks: a function
    run when that command is queried, before the reply."""

    def __init__(self, keeps_time_constant=None):
        self.writes, self.queries = [], []
        self.state = {"*ESR": 0, "FMOD": 0, "RSLP": 0, "HARM": 1, "ISRC": 0, "IGND": 0,
                      "ICPL": 0, "ILIN": 0, "SYNC": 0, "SENS": 13, "OFLT": 8, "OFSL": 0,
                      "RMOD": 0, "PHAS": 0}
        self.keeps_time_constant = keeps_time_constant
        self.answers, self.hooks = {"FREQ?": "317.3", "LIAS?": "0"}, {}

    def write(self, command):
        self.writes.append(command)
        name, _, value = command.partition(" ")
        if name in self.state and value and "," not in value:
            self.state[name] = float(value)

    def query(self, command):
        self.queries.append(command)
        hook = self.hooks.pop(command, None)
        if hook is not None:
            hook()
        if command in self.answers:
            return self.answers[command]
        name = command.rstrip("?")
        if name == "OFLT" and self.keeps_time_constant is not None:
            return str(self.keeps_time_constant)
        return f"{self.state[name]:g}"

    def clear(self):
        pass


class FakeController:
    """A PPMS VISA slot holding a FakeSR830 once connected."""

    def __init__(self, label, keeps_time_constant=None):
        self.label, self.lock = label, threading.RLock()
        self.instrument, self.resource_name = None, ""
        self.keeps_time_constant = keeps_time_constant
        self.disconnects = 0

    @property
    def connected(self):
        return self.instrument is not None

    def connect(self, resource):
        self.resource_name = resource
        self.instrument = FakeSR830(self.keeps_time_constant)
        return "Stanford_Research_Systems,SR830"

    def disconnect(self):
        self.disconnects += 1
        self.instrument = None


class DeferredExecutor:
    """Holds each job until run() is called, as the PPMS worker would."""

    def __init__(self):
        self.jobs = []

    def submit(self, fn, *args):
        future = Future()
        self.jobs.append((fn, args, future))
        return future

    def run(self):
        while self.jobs:
            fn, args, future = self.jobs.pop(0)
            try:
                future.set_result(fn(*args))
            except BaseException as error:
                future.set_exception(error)


def connect_setup(time_constant_code, harmonic=1):
    """Connect's SR830 writes, in the order used before 2026-10-14, with the
    chosen time constant; each is followed by a *ESR? check."""
    return ["*CLS", "FMOD 0", "RSLP 1", f"HARM {harmonic}", "ISRC 1", "IGND 0", "ICPL 0",
            "ILIN 0", "SYNC 0", "SENS 9", f"OFLT {time_constant_code}", "OFSL 1", "RMOD 1",
            "PHAS 0", "DDEF 1,1,0", "DDEF 2,1,0"]


class NoWindows(unittest.TestCase):
    def setUp(self):
        self.error = mock.Mock()
        unexpected = mock.Mock(side_effect=AssertionError("an unexpected dialog opened"))
        patches = [
            mock.patch("tkinter.Tk", side_effect=AssertionError("a test tried to open a Tk window")),
            mock.patch.object(tkinter.messagebox, "showerror", self.error),
            mock.patch.object(app.time, "sleep"),  # the 30 ms after each SR830 write
        ]
        for name in ("showwarning", "showinfo", "askyesno", "askokcancel", "askyesnocancel",
                     "askquestion", "askretrycancel"):
            patches.append(mock.patch.object(tkinter.messagebox, name, unexpected))
        for patcher in patches:
            patcher.start()
            self.addCleanup(patcher.stop)

    @staticmethod
    def texts(ttk, kind=None):
        return [w.options.get("text") for w in ttk.made
                if (kind is None or w.kind == kind) and "text" in w.options]


class PageTests(NoWindows):
    def test_the_zurich_row_says_tc_s_on_both_pages(self):
        for build in (build_ppms_panel, build_rte_panel):
            with self.subTest(page=build.__name__):
                panel, ttk = build()
                (name,) = [w for w in ttk.made if w.options.get("text") == "Zurich MFLI"]
                row = [w.options.get("text") for w in name.master.children if w.kind == "Label"]
                self.assertIn("TC (s)", row)
                self.assertNotIn("Time constant s", self.texts(ttk))
                # The entry beside it keeps the Zurich TC (its default is
                # not this change's business).
                self.assertIn(panel.zurich_timeconstant, [w.options.get("textvariable") for w in name.master.children])

    def test_one_sr830_tc_dropdown_defaults_to_1_s_with_the_stfmr_choices(self):
        panel, ttk = build_ppms_panel()
        combos = [w for w in ttk.made if w.kind == "Combobox"
                  and w.options.get("textvariable") is panel.ppms_sr830_time_constant_var]
        self.assertEqual(combos, [panel.ppms_sr830_time_constant_combo])
        combo = combos[0]
        self.assertEqual(combo.options["values"], tuple(app.STFMR_LOCKIN_SR830_TIME_CONSTANTS))
        self.assertEqual(combo.options["values"],
                         ("1 ms", "3 ms", "10 ms", "30 ms", "100 ms", "300 ms", "1 s", "3 s", "10 s"))
        self.assertEqual([app.SR830_TIME_CONSTANTS_S[code] for code in app.STFMR_LOCKIN_SR830_TIME_CONSTANTS.values()],
                         [1e-3, 3e-3, 10e-3, 30e-3, 100e-3, 300e-3, 1.0, 3.0, 10.0])
        self.assertEqual(panel.ppms_sr830_time_constant_var.get(), "1 s")
        self.assertEqual(app.STFMR_LOCKIN_SR830_TIME_CONSTANTS["1 s"], 10, "today's Connect value")
        self.assertEqual(panel._ppms_sr830_time_constant_code(), 10)
        self.assertEqual((combo.options["state"], combo.options["width"]), ("readonly", 6))
        self.assertLessEqual(max(map(len, app.STFMR_LOCKIN_SR830_TIME_CONSTANTS)), 6)
        self.assertEqual(self.texts(ttk).count("SR830 TC"), 1)

    def test_the_dropdown_sits_beside_macro_under_the_keithley_2400(self):
        """The rack: Keithleys left, SR830-1/2/3 right; Macro and the
        dropdown share the free cell under the 2400, left of SR830-3."""
        panel, ttk = build_ppms_panel()
        combo = panel.ppms_sr830_time_constant_combo
        row = combo.master
        rack = panel.ppms_instruments_frame
        self.assertIs(row.master, rack)
        self.assertIs(row, panel.ppms_macro_row)
        self.assertEqual(row.grid_options,
                         {"row": 2, "column": 0, "rowspan": 1, "columnspan": 1, "sticky": "w", "padx": 2})
        cells = {(w.grid_options["row"], w.grid_options["column"]): w for w in rack.children
                 if w.grid_options and w.grid_options.get("row", 9) < 3}
        self.assertIs(cells[(2, 0)], row, "nothing else in the cell under the 2400")
        self.assertIs(cells[(1, 0)], panel.ppms_device_resource_widgets["2400"].master)
        self.assertIs(cells[(2, 1)], panel.ppms_device_resource_widgets["830_c"].master)
        parts = sorted(row.children, key=lambda w: w.grid_options["column"])
        self.assertEqual([(w.kind, w.options.get("text")) for w in parts],
                         [("Button", "Macro"), ("Label", "SR830 TC"), ("Combobox", None)])
        self.assertIs(parts[-1], combo)
        self.assertEqual(parts[0].options["command"], panel.open_ppms_macro)
        # Three narrow parts, against the Keithley row's label, 18-character
        # address, Connect, Disconnect, lamp and 13-character status: the
        # cell, and the rack, keep their width (the CI GUI test measures it).
        self.assertEqual(len(row.children), 3)

    def test_rte_has_no_sr830_tc_control(self):
        panel, ttk = build_rte_panel()
        self.assertNotIn("SR830 TC", self.texts(ttk))
        for name in ("ppms_sr830_time_constant_var", "ppms_sr830_time_constant_combo"):
            self.assertNotIn(name, panel.__dict__)
        self.assertEqual(panel._ppms_sr830_time_constant_code(), 10)
        panel._ppms_apply_sr830_time_constant()  # nothing to apply
        self.assertFalse(panel.ppms_environment_busy)


class ConnectTests(NoWindows):
    def connect(self, panel, key="830_a", label=None, keeps=None):
        if label is not None:
            panel.ppms_sr830_time_constant_var.set(label)
        panel.ppms_instrument_controllers[key].keeps_time_constant = keeps
        panel.connect_ppms_device(key)
        return panel.ppms_instrument_controllers[key]

    def test_connect_sets_the_chosen_time_constant_and_reads_it_back(self):
        for label, code in app.STFMR_LOCKIN_SR830_TIME_CONSTANTS.items():
            with self.subTest(label=label):
                panel, _ttk = build_ppms_panel()
                controller = self.connect(panel, label=label)
                self.assertTrue(controller.connected)
                sr830 = controller.instrument
                self.assertEqual(sr830.writes, connect_setup(code), "the old order, the chosen code")
                self.assertIn("OFLT?", sr830.queries)
                self.assertEqual(sr830.state["OFSL"], 1, "12 dB/oct")
                self.assertEqual(panel.ppms_sr830_time_constant_codes["830_a"], code)
                self.assertEqual(panel.ppms_device_connection_vars["830_a"].get(), "Connected")
                self.error.assert_not_called()

    def test_the_default_connect_is_todays_oflt_10(self):
        panel, _ttk = build_ppms_panel()
        controller = self.connect(panel)
        self.assertEqual(controller.instrument.writes, connect_setup(10))

    def test_a_time_constant_the_sr830_does_not_keep_refuses_the_connect(self):
        panel, _ttk = build_ppms_panel()
        controller = self.connect(panel, label="100 ms", keeps=10)
        self.assertFalse(controller.connected, "disconnected, not claimed connected")
        self.assertEqual(controller.disconnects, 1)
        self.assertIn("OFLT=10", self.error.call_args.args[1])
        self.assertIn("expected 8", self.error.call_args.args[1])
        self.assertTrue(panel.ppms_device_connection_vars["830_a"].get().startswith("Failed"))
        self.assertNotIn("830_a", panel.ppms_sr830_time_constant_codes)
        self.assertEqual(panel.ppms_sr830_time_constant_combo.options["state"], "readonly",
                         "unlocked again after the failed Connect")

    def test_the_choice_is_locked_until_a_connect_ends(self):
        panel, _ttk = build_ppms_panel()
        panel.ppms_executor = DeferredExecutor()
        combo = panel.ppms_sr830_time_constant_combo
        panel.connect_ppms_device("830_b")
        self.assertEqual(combo.options["state"], "disabled")
        panel.ppms_sr830_time_constant_var.set("3 s")
        panel._ppms_apply_sr830_time_constant()      # cannot be picked; refused anyway
        self.assertEqual(panel.ppms_sr830_time_constant_var.get(), "1 s", "back to what Connect sends")
        self.assertEqual(len(panel.ppms_executor.jobs), 1, "only the Connect")
        panel.ppms_executor.run()
        self.assertEqual(combo.options["state"], "readonly")
        self.assertEqual(panel.ppms_instrument_controllers["830_b"].instrument.writes,
                         connect_setup(10, harmonic=2))

    def test_rte_connect_keeps_oflt_10(self):
        panel, _ttk = build_rte_panel()
        panel.ppms_resource_vars["830_a"].set("GPIB0::8::INSTR")
        panel.connect_ppms_device("830_a")
        controller = panel.ppms_instrument_controllers["830_a"]
        self.assertTrue(controller.connected)
        harmonic = panel._ppms_harmonic_value("830_a")
        self.assertEqual(controller.instrument.writes, connect_setup(10, harmonic=harmonic))
        self.error.assert_not_called()


class ChangeTests(NoWindows):
    def connected_panel(self, keys=("830_a", "830_c")):
        panel, ttk = build_ppms_panel()
        for key in keys:
            panel.connect_ppms_device(key)
            panel.ppms_instrument_controllers[key].instrument.writes.clear()
            panel.ppms_instrument_controllers[key].instrument.queries.clear()
        return panel

    def choose(self, panel, label):
        panel.ppms_sr830_time_constant_var.set(label)
        panel._ppms_apply_sr830_time_constant()

    def test_a_new_choice_is_set_on_every_connected_sr830_on_the_worker(self):
        panel = self.connected_panel()
        panel.ppms_executor = DeferredExecutor()
        state = types.SimpleNamespace(low_range_count=2, range_cooldown=3, low_range_peak=1e-4,
                                      sensitivity_index=12, clear_latch_pending=False)
        panel.ppms_sr830_range_states = {"830_a": state}
        panel.ppms_idle_range_ready_at = {"830_a": time.monotonic() + 50.0}  # 5 x 10 s, stale
        controllers = panel.ppms_instrument_controllers
        self.choose(panel, "100 ms")
        # Nothing is sent from the Tk thread; the choice is locked meanwhile.
        self.assertEqual(controllers["830_a"].instrument.writes, [])
        self.assertTrue(panel.ppms_environment_busy)
        self.assertEqual(panel.ppms_sr830_time_constant_combo.options["state"], "disabled")
        self.assertEqual(len(panel.ppms_executor.jobs), 1)
        before = time.monotonic()
        panel.ppms_executor.run()
        for key in ("830_a", "830_c"):
            sr830 = controllers[key].instrument
            self.assertEqual(sr830.writes, ["OFLT 8"], key)
            self.assertEqual(sr830.queries, ["*ESR?", "OFLT?"], key)
            self.assertEqual(panel.ppms_sr830_time_constant_codes[key], 8)
        self.assertFalse(controllers["830_b"].connected, "an unconnected SR830 gets it at Connect")
        self.assertFalse(panel.ppms_environment_busy)
        self.assertEqual(panel.ppms_sr830_time_constant_combo.options["state"], "readonly")
        self.assertEqual(panel.ppms_sr830_time_constant_applied, "100 ms")
        self.assertIn("OFLT 8", panel.thread_log.call_args.args[0])
        # Ranging restarts: the counts made with the old time constant are
        # dropped, and idle autorange waits 5 new time constants (0.5 s here).
        self.assertEqual((state.low_range_count, state.range_cooldown, state.low_range_peak), (0, 0, 0.0))
        self.assertEqual(state.sensitivity_index, 12, "the range itself is kept")
        ready = panel.ppms_idle_range_ready_at["830_a"]
        self.assertGreaterEqual(ready, before + 0.5)
        self.assertLess(ready, time.monotonic() + 0.6)
        # A later Connect sends the new choice.
        panel.connect_ppms_device("830_b")
        panel.ppms_executor.run()
        self.assertEqual(controllers["830_b"].instrument.writes, connect_setup(8, harmonic=2))
        self.error.assert_not_called()

    def test_with_no_sr830_connected_the_choice_waits_for_connect(self):
        panel, _ttk = build_ppms_panel()
        panel.ppms_executor = DeferredExecutor()
        self.choose(panel, "3 s")
        self.assertEqual(panel.ppms_executor.jobs, [])
        self.assertFalse(panel.ppms_environment_busy)
        self.assertEqual(panel.ppms_sr830_time_constant_applied, "3 s")
        panel.ppms_executor.submit = ImmediateExecutor().submit
        panel.connect_ppms_device("830_c")
        self.assertEqual(panel.ppms_instrument_controllers["830_c"].instrument.writes,
                         connect_setup(11, harmonic=3))

    def test_a_change_is_refused_and_locked_while_busy(self):
        for busy in ("ppms_environment_busy", "ppms_field_scan_running", "ppms_sr830_connecting"):
            with self.subTest(busy=busy):
                panel = self.connected_panel(("830_a",))
                panel.ppms_executor = DeferredExecutor()
                setattr(panel, busy, {"830_b"} if busy == "ppms_sr830_connecting" else True)
                panel._update_ppms_control_ui()
                self.assertEqual(panel.ppms_sr830_time_constant_combo.options["state"], "disabled")
                self.choose(panel, "10 s")
                self.assertEqual(panel.ppms_executor.jobs, [])
                self.assertEqual(panel.ppms_instrument_controllers["830_a"].instrument.writes, [])
                self.assertEqual(panel.ppms_sr830_time_constant_var.get(), "1 s", "shows what the SR830s have")
                setattr(panel, busy, set() if busy == "ppms_sr830_connecting" else False)
                panel._update_ppms_control_ui()
                self.assertEqual(panel.ppms_sr830_time_constant_combo.options["state"], "readonly")

    def test_a_readback_mismatch_is_reported_and_the_others_still_change(self):
        panel = self.connected_panel()
        panel.ppms_instrument_controllers["830_a"].instrument.keeps_time_constant = 10
        self.choose(panel, "3 s")
        message = self.error.call_args.args[1]
        self.assertIn("SR830-1", message)
        self.assertIn("requested OFLT 11, read 10", message)
        self.assertNotIn("830_a", panel.ppms_sr830_time_constant_codes, "unknown, not recorded")
        self.assertEqual(panel.ppms_sr830_time_constant_codes["830_c"], 11)
        self.assertEqual(panel.ppms_instrument_controllers["830_c"].instrument.writes, ["OFLT 11"])
        self.assertTrue(panel.ppms_instrument_controllers["830_a"].connected, "a change never disconnects")
        self.assertFalse(panel.ppms_environment_busy)
        self.assertEqual(panel.ppms_sr830_time_constant_combo.options["state"], "readonly")

    def test_the_change_runs_through_the_harmonic_changes_checked_write(self):
        """OFLT goes through _ppms_write_sr830_checked (write, *ESR?) and is
        read back with OFLT?, as HARM is."""
        panel = self.connected_panel(("830_b",))
        panel._ppms_write_sr830_checked = mock.Mock(wraps=panel._ppms_write_sr830_checked)
        self.choose(panel, "30 ms")
        panel._ppms_write_sr830_checked.assert_called_once_with("830_b", "OFLT 7")


class CsvTests(NoWindows):
    def run_point(self, codes):
        """One point of the real PPMS scan worker, with SR830-1 and SR830-3.
        The stop event moves a clock instead of waiting: the point waits
        7 time constants of the slower SR830 first (user, 2026-10-14)."""
        obj = app.CombinedControlApp.__new__(app.CombinedControlApp)
        obj.closing = False
        obj.ppms_field_scan_stop_event = self.event = ClockEvent(Clock())
        obj.ppms_scan_context = {}
        obj.ppms_last_readback = None
        obj.ppms_field_scan_rows = []
        obj.ppms_field_scan_total = 1
        obj.ppms_field_scan_started_at = time.monotonic()
        obj.ppms_environment_controller = types.SimpleNamespace(backend="MultiPyVu")
        obj.ppms_instrument_controllers = {
            "830_a": types.SimpleNamespace(resource_name="GPIB0::8::INSTR"),
            "830_c": types.SimpleNamespace(resource_name="GPIB0::10::INSTR")}
        obj.ppms_sr830_time_constant_codes = codes
        obj._read_ppms_live = lambda: dict(field_oe=0.0, field_status="Persistent",
                                           temperature_k=300.0, temperature_status="Stable")
        obj._configure_ppms_transport_scan = lambda settings: None
        sample = {}
        for address in (8, 10):
            sample.update({f"sr830_{address}_x_v": 1e-6, f"sr830_{address}_frequency_hz": 317.3,
                           f"sr830_{address}_reference_valid": True, f"sr830_{address}_sensitivity_index": 9})
        obj._acquire_ppms_first_point = lambda *args: (dict(sample), 1)
        obj._average_ppms_transport_sample = lambda *args: dict(sample)
        obj.post_ui = lambda callback: None
        obj.thread_log = lambda text: None
        settings = dict(scan_mode="Temperature", active_lockins=("830_a", "830_c"),
                        harmonic_a=1, harmonic_b=2, harmonic_c=3, acquisition_s=1.0,
                        frequency_hz=317.3, current_rms_ma=1.0, compliance_v=50.0)
        with tempfile.TemporaryDirectory() as folder:
            obj.ppms_live_csv_path = Path(folder) / "scan.csv"
            obj.ppms_live_csv_fields = None
            app.CombinedControlApp._ppms_field_scan_worker(obj, [(1, 1, 1, 300.0)], 100.0, 0.0, 1, settings)
            with obj.ppms_live_csv_path.open(encoding="utf-8-sig", newline="") as handle:
                reader = csv.reader(handle)
                return next(reader), next(reader)

    def test_each_sr830s_time_constant_is_a_new_last_column(self):
        """Then the point's wait and the slowest time constant, the newest
        columns, last (user, 2026-10-14)."""
        header, values = self.run_point({"830_a": 8, "830_c": 10})
        self.assertEqual(header[-6:], ["sr830_8_harmonic", "sr830_10_harmonic",
                                       "sr830_8_time_constant_s", "sr830_10_time_constant_s",
                                       "effective_settle_s", "slowest_lockin_tc_s"])
        self.assertEqual(values[-4:], ["0.1", "1.0", "7.0", "1.0"])
        self.assertEqual(self.event.waits, [7.0], "7 x 1 s (SR830-3), not Settle s 0")

    def test_a_time_constant_that_did_not_read_back_is_left_empty(self):
        header, values = self.run_point({"830_a": 12})
        self.assertEqual(header[-4:-2], ["sr830_8_time_constant_s", "sr830_10_time_constant_s"])
        # SR830-3's time constant is unknown: the wait counts it with the
        # value Connect sets (1 s), and SR830-1's 10 s is the longer one.
        self.assertEqual(values[-4:], ["10.0", "", "70.0", "10.0"])
        self.assertEqual(self.event.waits, [70.0])



class Clock:
    """time.monotonic for a test: it moves only when the scan waits."""

    def __init__(self):
        self.now = 1000.0

    def __call__(self):
        return self.now


class ClockEvent:
    """The scan's stop event: never set; each wait moves the clock on."""

    def __init__(self, clock):
        self.clock, self.waits = clock, []

    def is_set(self):
        return False

    def wait(self, seconds):
        self.waits.append(seconds)
        self.clock.now += seconds
        return False


class RangingSR830:
    """An SR830 in a scan with time-constant code ``code``.  Its 0.6 mV
    signal overloads every range below ``needs`` (SENS 17 is 1 mV)."""

    def __init__(self, code, needs=17, sens=9):
        self.code, self.needs, self.sens = code, needs, sens
        self.writes = []

    def write(self, command):
        self.writes.append(command)
        name, _, value = command.partition(" ")
        if name == "SENS":
            self.sens = int(value)

    def query(self, command):
        replies = {"SNAP? 1,2,3,4,9": "6e-4,0,6e-4,0,317.3",
                   "LIAS?": "1" if self.sens < self.needs else "0",
                   "SENS?": str(self.sens), "OFLT?": str(self.code)}
        return replies[command]

    def clear(self):
        pass


class Fake6221:
    """The 6221 as a scan reads it: output on, 1 mA RMS, no compliance."""

    def __init__(self, compliance_v=10.0, frequency_hz=317.3, peak_a=math.sqrt(2) * 1e-3):
        self.writes = []
        self.replies = {"OUTP?": "1", "STAT:MEAS:COND?": "0", "SOUR:WAVE:AMPL?": f"{peak_a:.12e}",
                        "CURR:COMP?": f"{compliance_v:g}", "SOUR:WAVE:FREQ?": f"{frequency_hz:g}"}

    def write(self, command):
        self.writes.append(command)

    def query(self, command):
        return self.replies[command]

    def clear(self):
        pass


def slot(label, instrument, resource=""):
    return types.SimpleNamespace(label=label, instrument=instrument, resource_name=resource,
                                 lock=threading.RLock(), connected=True,
                                 safe_output_off=lambda: None)


class RangeLimitTests(NoWindows):
    """A scan point's autorange limit was a fixed 180 s, set when every
    range step waited 5 x 1 s; it now grows with the step wait."""

    def averaging(self, settles):
        """The real averaging loop on scripted readings (each a range step
        with that wait, then settled ones) and a clock moved by the waits."""
        obj = app.CombinedControlApp.__new__(app.CombinedControlApp)
        obj.closing = False
        clock = Clock()
        obj.ppms_field_scan_stop_event = ClockEvent(clock)
        settled = {"sr830_8_x_v": 1e-6, "sr830_8_y_v": 0.0, "sr830_8_frequency_hz": 317.3,
                   "sr830_8_reference_valid": True, "sr830_8_overload": False}
        readings = iter([{"range_settle_s": s} for s in settles])
        obj._read_ppms_transport_sample = lambda *_args: next(readings, settled)
        return obj, clock

    def run_steps(self, settle, steps):
        obj, clock = self.averaging([settle] * steps)
        with mock.patch.object(app.time, "monotonic", clock):
            obj._average_ppms_transport_sample(317.3, ("830_a",), 1.0)
        return obj

    def test_at_1_s_the_limit_is_still_180_s(self):
        """5 s per step: 36 steps fit, the 37th would end past 180 s."""
        obj = self.run_steps(5.0, 36)
        self.assertEqual(obj.ppms_field_scan_stop_event.waits.count(5.0), 36)
        with self.assertRaisesRegex(app.PPMSLockinRangeError, r"within 180 s; current point not saved"):
            self.run_steps(5.0, 37)

    def test_shorter_time_constants_keep_180_s(self):
        """100 ms: 0.5 s per step, so 360 fit in 180 s, and no more."""
        self.run_steps(0.5, 360)
        with self.assertRaisesRegex(app.PPMSLockinRangeError, r"within 180 s"):
            self.run_steps(0.5, 361)

    def test_a_long_time_constant_keeps_as_many_steps(self):
        """10 s: 50 s per step.  180 s held three; now 36 fit in 1800 s."""
        for settle, limit in ((15.0, "540"), (50.0, "1800")):
            with self.subTest(settle=settle):
                obj = self.run_steps(settle, 36)
                self.assertEqual(obj.ppms_field_scan_stop_event.waits.count(settle), 36)
                with self.assertRaisesRegex(app.PPMSLockinRangeError, rf"within {limit} s"):
                    self.run_steps(settle, 37)

    def test_the_first_point_after_connect_at_10_s_completes(self):
        """The review's case: Connect leaves SENS 9 (2 uV); at 10 s
        (OFLT 12) a 0.6 mV signal overloads up to SENS 17, eight steps of
        50 s each.  The point used to fail at the fourth step; the whole
        chain (6221 checks, SNAP?, LIAS?, SENS, OFLT?) now completes it."""
        obj = app.CombinedControlApp.__new__(app.CombinedControlApp)
        obj.closing = False
        clock = Clock()
        obj.ppms_field_scan_stop_event = ClockEvent(clock)
        obj.post_ui = lambda callback: None
        sr830 = RangingSR830(code=12)
        obj.ppms_instrument_controllers = {
            "6221": slot("Keithley 6221", Fake6221()),
            "830_a": slot("SR830-1", sr830, "GPIB0::8::INSTR")}
        started = clock.now
        with mock.patch.object(app.time, "monotonic", clock):
            sample = obj._average_ppms_transport_sample(317.3, ("830_a",), 1.0)
        self.assertEqual(sr830.writes, [f"SENS {code}" for code in range(10, 18)])
        self.assertEqual(obj.ppms_field_scan_stop_event.waits.count(50.0), 8, "5 x 10 s after each step")
        self.assertGreater(clock.now - started, 400.0, "well past the old 180 s")
        self.assertEqual(sample["sr830_8_sensitivity_index"], 17)
        self.assertAlmostEqual(sample["sr830_8_x_v"], 6e-4)
        self.assertGreater(sample["sample_count"], 0)


class IdlePollTests(NoWindows):
    """An 'SR830 TC' change makes idle autorange wait 5 new time constants;
    a poll running beside it on another PPMS worker must not cut that short."""

    class HandOverLock:
        """An RLock whose next acquisition first runs ``pending``: another
        worker held the lock, and this one waited for it to finish."""

        def __init__(self):
            self._lock, self.pending = threading.RLock(), None

        def __enter__(self):
            pending, self.pending = self.pending, None
            if pending is not None:
                pending()
            self._lock.acquire()
            return self

        def __exit__(self, *_exc):
            self._lock.release()

    def polling_panel(self):
        panel, _ttk = build_ppms_panel()
        panel.connect_ppms_device("830_a")
        controller = panel.ppms_instrument_controllers["830_a"]
        controller.lock = self.HandOverLock()
        sr830 = controller.instrument
        sr830.answers["OUTP? 3"] = "1e-07"   # far below 10 % of the range: a low reading
        sr830.writes.clear()
        sr830.queries.clear()
        panel.ppms_environment_controller = types.SimpleNamespace(backend="fake", read_environment=lambda: {})
        panel._read_ppms_current_display = lambda: "ON / sine setpoint 1 mA RMS / 317.3 Hz"
        panel._read_ppms_cryogen_display = lambda: {}
        panel.ppms_idle_autorange_enabled, panel.ppms_6221_zero_output = True, False
        panel.ppms_field_scan_running = False
        state = types.SimpleNamespace(low_range_count=2, range_cooldown=0, low_range_peak=0.0,
                                      sensitivity_index=13, clear_latch_pending=False)
        panel.ppms_sr830_range_states = {"830_a": state}
        panel.ppms_idle_range_ready_at = {}
        return panel, controller, sr830, state

    def change_to_10_s(self, panel):
        return lambda: panel._ppms_set_sr830_time_constant("830_a", 12)

    def test_a_change_the_poll_waited_for_is_not_overwritten(self):
        """The poll read LIAS?, then waited for the SR830 lock while the
        change held it.  It used to range anyway and write "ready now"."""
        panel, controller, sr830, state = self.polling_panel()
        sr830.hooks["LIAS?"] = lambda: setattr(controller.lock, "pending", self.change_to_10_s(panel))
        before = time.monotonic()
        result = panel._read_ppms_live()
        self.assertEqual(sr830.writes, ["OFLT 12"], "the change only, no range step")
        self.assertNotIn("OUTP? 3", sr830.queries, "the poll saw the wait and did not range")
        self.assertGreaterEqual(panel.ppms_idle_range_ready_at["830_a"], before + 50.0, "5 x 10 s")
        self.assertEqual(state.low_range_count, 0, "counting restarts after the wait")
        self.assertIn("LOCK", result["lockin_reference"]["830_a"])

    def test_a_ready_time_is_never_moved_earlier(self):
        """Even a change that lands inside the poll's range step (it cannot,
        under the lock) keeps its 50 s."""
        panel, _controller, sr830, _state = self.polling_panel()
        sr830.hooks["OUTP? 3"] = self.change_to_10_s(panel)
        before = time.monotonic()
        panel._read_ppms_live()
        self.assertIn("OUTP? 3", sr830.queries)
        self.assertGreaterEqual(panel.ppms_idle_range_ready_at["830_a"], before + 50.0)

    def test_without_a_change_the_poll_still_counts_low_readings(self):
        panel, _controller, sr830, state = self.polling_panel()
        panel._read_ppms_live()
        self.assertIn("OUTP? 3", sr830.queries)
        self.assertEqual(sr830.writes, ["SENS 8"], "the third low reading in a row ranges down from 9")
        self.assertGreater(panel.ppms_idle_range_ready_at["830_a"], time.monotonic(), "and waits to settle")


class ScanStartTimeConstantTests(NoWindows):
    """Each scan start reads every active SR830's OFLT? for the CSV column,
    so a front-panel change since Connect is recorded as it is."""

    def configure(self, panel, front_panel_code=None):
        sr830 = panel.ppms_instrument_controllers["830_a"].instrument
        if front_panel_code is not None:
            sr830.state["OFLT"] = front_panel_code
        sr830.writes.clear()
        sr830.queries.clear()
        panel.thread_log.reset_mock()
        source = Fake6221()
        panel.ppms_instrument_controllers["6221"] = slot("Keithley 6221", source, "GPIB0::12::INSTR")
        panel._ppms_write_6221_checked = source.write
        panel._initialize_ppms_6221 = lambda: None
        panel._configure_ppms_transport_scan(dict(
            active_lockins=("830_a",), current_rms_ma=1.0, frequency_hz=317.3, compliance_v=10.0,
            harmonic_a=1, harmonic_b=2, harmonic_c=3))
        return sr830

    def connected(self, build=build_ppms_panel):
        panel, _ttk = build()
        panel.ppms_resource_vars["830_a"].set("GPIB0::8::INSTR")
        panel.connect_ppms_device("830_a")
        return panel

    def test_the_scan_reads_oflt_once_and_writes_nothing_new(self):
        panel = self.connected()
        sr830 = self.configure(panel)
        self.assertEqual(sr830.writes, ["ILIN 0", "SYNC 0"], "the scan start's writes, unchanged")
        self.assertEqual(sr830.queries.count("OFLT?"), 1)
        self.assertLess(sr830.queries.index("RSLP?"), sr830.queries.index("OFLT?"))
        self.assertLess(sr830.queries.index("OFLT?"), sr830.queries.index("FREQ?"), "before the 6221 starts")
        self.assertEqual(panel.ppms_sr830_time_constant_codes["830_a"], 10)
        panel.thread_log.assert_not_called()

    def test_a_front_panel_change_is_recorded_and_logged(self):
        panel = self.connected()
        self.configure(panel, front_panel_code=11)
        self.assertEqual(panel.ppms_sr830_time_constant_codes["830_a"], 11, "3 s, as the SR830 reads")
        message = panel.thread_log.call_args.args[0]
        self.assertIn("SR830-1", message)
        self.assertIn("3 s (OFLT 11), not the 1 s last set", message)

    def test_a_failed_read_leaves_the_column_unknown_and_stops_the_start(self):
        panel = self.connected()
        panel.ppms_instrument_controllers["830_a"].instrument.answers["OFLT?"] = "25"
        with self.assertRaisesRegex(RuntimeError, "time-constant readback invalid: OFLT=25"):
            self.configure(panel)
        self.assertNotIn("830_a", panel.ppms_sr830_time_constant_codes)

    def test_rte_reads_it_too(self):
        """RT-E has no such choice and no per-SR830 column, but each reading
        waits 7 of these time constants (user, 2026-10-14), so it reads
        OFLT? at the scan start too: one query, the same writes."""
        panel = self.connected(build_rte_panel)
        sr830 = self.configure(panel)
        self.assertEqual(sr830.writes, ["ILIN 0", "SYNC 0"], "the scan start's writes, unchanged")
        self.assertEqual(sr830.queries.count("OFLT?"), 1)
        self.assertLess(sr830.queries.index("RSLP?"), sr830.queries.index("OFLT?"))
        self.assertLess(sr830.queries.index("OFLT?"), sr830.queries.index("FREQ?"), "before the 6221 starts")
        self.assertEqual(panel.ppms_sr830_time_constant_codes["830_a"], 10, "what Connect set")
        panel.thread_log.assert_not_called()

    def test_an_rte_front_panel_change_is_seen_logged_and_waited_for(self):
        panel = self.connected(build_rte_panel)
        self.configure(panel, front_panel_code=12)
        self.assertEqual(panel.ppms_sr830_time_constant_codes["830_a"], 12, "10 s, as the SR830 reads")
        message = panel.thread_log.call_args.args[0]
        self.assertTrue(message.startswith("RT-E SR830-1: the time constant reads 10 s (OFLT 12), "
                                           "not the 1 s last set"), message)
        self.assertEqual(panel._ppms_lockin_settle(0.5, ("830_a",), False), (70.0, 10.0, "SR830-1"))


class StartSettleTests(NoWindows):
    """The scan-start confirmation used to note a Settle s shorter than 5
    time constants of a 3 s or 10 s choice, and the scan itself was not
    changed.  Since the user's rule of 2026-10-14 every reading waits at
    least 7 time constants of the slowest lock-in instead, and the
    confirmation shows that wait (more in test_lockin_settle_rte_ppms.py)."""

    def settle(self, label, settle_s, lockins=("830_a",), build=build_ppms_panel):
        panel, _ttk = build()
        if label is not None:
            panel.ppms_sr830_time_constant_var.set(label)
        return panel._ppms_lockin_settle(settle_s, lockins, False)

    def test_every_choice_waits_7_time_constants_or_settle_s(self):
        for label, code in app.STFMR_LOCKIN_SR830_TIME_CONSTANTS.items():
            with self.subTest(label=label):
                tau = app.SR830_TIME_CONSTANTS_S[code]
                settle, slowest, meter = self.settle(label, 0.5)
                self.assertEqual((slowest, meter), (tau, "SR830-1"))
                self.assertAlmostEqual(settle, max(0.5, 7.0 * tau))
        self.assertEqual(self.settle(None, 0.5)[0], 7.0, "the 1 s default: 7 s, not 0.5 s")
        self.assertEqual(self.settle("10 s", 100.0)[0], 100.0, "a longer Settle s is kept")
        self.assertEqual(self.settle(None, 0.5, build=build_rte_panel)[0], 7.0, "RT-E: OFLT 10 = 1 s")
        self.assertEqual(self.settle("10 s", 0.5, lockins=())[0], 0.5, "no lock-in: Settle s alone")

    def test_the_wait_is_in_the_scan_time_estimate(self):
        obj = app.CombinedControlApp.__new__(app.CombinedControlApp)
        obj.ppms_field_scan_running = obj.ppms_environment_busy = False
        obj._require_ppms_control_ready = mock.Mock()
        obj.ppms_simple_mode = Var("Field")
        obj.ppms_simple_range = [Var(v) for v in (100, 300, 100)]
        values = dict(ppms_acquisition_time_var=1, ppms_field_target_var=100, ppms_temperature_target_var=300,
                      ppms_motor_target_var=26, ppms_field_scan_stable_var=0.5, ppms_field_rate_var=100,
                      ppms_field_approach_var="No overshoot", ppms_transport_current_var=1,
                      ppms_transport_frequency_var=317.3, ppms_transport_compliance_var=10,
                      ppms_transport_harmonic_a_var=1, ppms_temperature_rate_var=1,
                      ppms_temperature_approach_var="No overshoot", ppms_motor_speed_var=14,
                      ppms_motor_confirm_var=True, ppms_sr830_time_constant_var="10 s")
        for name, value in values.items():
            setattr(obj, name, Var(value))
        obj.ppms_environment_controller = types.SimpleNamespace(backend=app.PPMS_QDINSTRUMENT_BACKEND)
        obj.ppms_instrument_controllers = {
            "6221": types.SimpleNamespace(connected=True, resource_name="GPIB0::12::INSTR"),
            "830_a": types.SimpleNamespace(connected=True, resource_name="GPIB0::8::INSTR")}
        obj.ppms_last_readback = dict(temperature_k=300, field_oe=100)
        obj.root = None
        ask = mock.Mock(return_value=False)   # the user answers No
        with mock.patch.object(app.messagebox, "askyesno", ask):
            obj.start_ppms_field_scan()
        self.error.assert_not_called()
        message = ask.call_args.args[1]
        self.assertIn("Measurement points: 5\nWait before each reading: 70 s (7 x 10 s, the time constant of "
                      "SR830-1, the slowest lock-in measured; Settle s is 0.5 s)\n", message)
        # 4 s of field ramp, 5 x (70 s + 1 s), and the 6 s and 12 s allowances.
        self.assertIn("Estimated duration: about 6 min 41 s", message)
        self.assertNotIn("lags", message, "the old note is gone: the scan waits instead")
        self.assertTrue(message.endswith("\nContinue to choose the save folder and start the scan?"))
        self.assertEqual(ask.call_args.kwargs["icon"], "question")
        self.assertFalse(obj.ppms_field_scan_running, "No: nothing starts")


class WheelTests(NoWindows):
    def test_a_mouse_wheel_turn_over_the_dropdown_does_nothing(self):
        """Tk's combobox steps on a wheel turn and reports a selection; the
        dropdown stops that, so only a value picked from the list applies."""
        panel, _ttk = build_ppms_panel()
        combo = panel.ppms_sr830_time_constant_combo
        for sequence in ("<MouseWheel>", "<Button-4>", "<Button-5>"):
            with self.subTest(sequence=sequence):
                self.assertEqual(combo.bindings[sequence](types.SimpleNamespace(delta=-120)), "break")
        self.assertIn("<<ComboboxSelected>>", combo.bindings, "a pick still applies")


if __name__ == "__main__":
    unittest.main()
