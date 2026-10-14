"""PPMS page, the user's two changes of 2026-10-14:

- the Zurich row's "Time constant s" reads "TC (s)", as on RT-E;
- one 'SR830 TC' dropdown for the page's three SR830s (1 ms to 10 s, the
  STFMR page's OFLT codes, default 1 s = OFLT 10, what Connect always set):
  Connect sets the choice and reads it back, a new choice is set on every
  connected SR830 on the PPMS worker, and it is locked while busy.  RT-E
  keeps OFLT 10 at Connect and has no such control.

No Tk window and no instrument: the real pages are built on stand-in
widgets, and the SR830s are fakes that answer from the commands they were
sent.  Every message box is mocked, and creating a Tk root fails the test.
"""
import csv
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

    keeps_time_constant: the OFLT code it reports whatever it was sent."""

    def __init__(self, keeps_time_constant=None):
        self.writes, self.queries = [], []
        self.state = {"*ESR": 0, "FMOD": 0, "RSLP": 0, "HARM": 1, "ISRC": 0, "IGND": 0,
                      "ICPL": 0, "ILIN": 0, "SYNC": 0, "SENS": 13, "OFLT": 8, "OFSL": 0,
                      "RMOD": 0, "PHAS": 0}
        self.keeps_time_constant = keeps_time_constant

    def write(self, command):
        self.writes.append(command)
        name, _, value = command.partition(" ")
        if name in self.state and value and "," not in value:
            self.state[name] = float(value)

    def query(self, command):
        self.queries.append(command)
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
        """One point of the real PPMS scan worker, with SR830-1 and SR830-3."""
        obj = app.CombinedControlApp.__new__(app.CombinedControlApp)
        obj.closing = False
        obj.ppms_field_scan_stop_event = threading.Event()
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
        header, values = self.run_point({"830_a": 8, "830_c": 10})
        self.assertEqual(header[-4:], ["sr830_8_harmonic", "sr830_10_harmonic",
                                       "sr830_8_time_constant_s", "sr830_10_time_constant_s"])
        self.assertEqual(values[-2:], ["0.1", "1.0"])

    def test_a_time_constant_that_did_not_read_back_is_left_empty(self):
        header, values = self.run_point({"830_a": 12})
        self.assertEqual(header[-2:], ["sr830_8_time_constant_s", "sr830_10_time_constant_s"])
        self.assertEqual(values[-2:], ["10.0", ""])


if __name__ == "__main__":
    unittest.main()
