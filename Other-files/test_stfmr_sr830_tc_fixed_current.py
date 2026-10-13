"""STFMR page, two changes the user approved on 2026-10-13, one check each:

- an 'SR830 TC' choice (default 1 s, as before), with every SR830 reading of
  a sweep waiting at least 5 time constants (12 dB/oct settling);
- fixed-current runs (Start A = Stop A): only the first two readings of each
  frequency are discarded, so such a run saves data.

No Tk window and no instrument: the real sweep runs on the shared STFMR
harness (stand-in devices, a Root that runs callbacks at once but keeps each
wait), and the real SR830 controller talks to a fake SR830.  Every message
box and file dialog is mocked, and creating a Tk root fails the test."""
import re
import sys
import tempfile
import tkinter
import tkinter.filedialog
import tkinter.messagebox
import tkinter.simpledialog
import types
import unittest
from pathlib import Path
from unittest import mock

# The shared STFMR harness lives in the test/ subfolder and imports the main
# program by module name from the folder above Other-files (as
# test_stfmr_dual does).
_HERE = Path(__file__).resolve().parent
for _path in (_HERE / "test", _HERE.parent):
    if str(_path) not in sys.path:
        sys.path.insert(0, str(_path))
from test_stfmr_realtime_recording import Root, Variable, app, events, harness, read_csv

App = app.CombinedControlApp


class TimedRoot(Root):
    """The harness's Root, also keeping the delay before each point read."""

    def __init__(self):
        super().__init__()
        self.reads = []

    def after(self, ms, callback):
        if getattr(callback, "__name__", "") == "_read_next_stfmr_lockin_point":
            self.reads.append(ms)
        return super().after(ms, callback)


class History(Variable):
    """A text variable that keeps everything it was set to."""

    def __init__(self, value=""):
        super().__init__(value)
        self.history = []

    def set(self, value):
        self.history.append(value)
        super().set(value)


class FakeSR830:
    """Answers configure()'s read-backs from the commands it was sent.

    keeps_time_constant: the OFLT code it reports whatever it was sent, as
    an SR830 does that replaces a too-short time constant."""

    def __init__(self, keeps_time_constant=None):
        self.writes, self.queries = [], []
        self.state = {"FMOD": 0, "RSLP": 0, "HARM": 1, "SENS": 13, "OFLT": 10}
        self.keeps_time_constant = keeps_time_constant

    def write(self, command):
        self.writes.append(command)
        name, _, value = command.partition(" ")
        if name in self.state and value:
            self.state[name] = int(value)

    def query(self, command):
        self.queries.append(command)
        name = command.rstrip("?")
        if name == "OFLT" and self.keeps_time_constant is not None:
            return str(self.keeps_time_constant)
        return str(self.state[name])


class StandIn:
    """A widget that keeps its options, grid cell and column settings."""

    def __init__(self, master=None, *_args, **options):
        self.master, self.options = master, dict(options)
        self.grid_options, self.column_options = None, {}
        self.kind = type(self).__name__

    def grid(self, **options):
        self.grid_options = options

    def columnconfigure(self, index, **options):
        self.column_options.setdefault(index, {}).update(options)

    def configure(self, **options):
        self.options.update(options)

    def cget(self, name):
        return self.options.get(name, "")

    def winfo_reqwidth(self):
        return 70

    def winfo_reqheight(self):
        return 16

    def create_oval(self, *_args, **_options):
        return 1

    def pack(self, *_args, **_options):
        pass

    rowconfigure = bind = lower = pack


def build_stfmr_page():
    """Run the real build_stfmr_lockin_ui without Tk: ttk, tk.Canvas,
    tk.Button and the fonts are stand-ins ("0" is 7 px wide)."""
    widgets = []

    def kind(name):
        def make(*args, **options):
            widget = type(name, (StandIn,), {})(*args, **options)
            widgets.append(widget)
            return widget
        return make

    fake_ttk = types.SimpleNamespace(**{name: kind(name) for name in (
        "Frame", "LabelFrame", "Label", "Entry", "Combobox", "Spinbox",
        "Checkbutton", "Button", "Progressbar")})
    fake_font = types.SimpleNamespace(
        nametofont=lambda _name: types.SimpleNamespace(measure=lambda text: 7 * len(text)))

    class PageOnly(App):
        def __getattr__(self, name):        # the page's Tk variables
            if name.startswith("stfmr_lockin_") and name.endswith("_var"):
                value = Variable("")
                setattr(self, name, value)
                return value
            raise AttributeError(name)

    a = object.__new__(PageOnly)
    a.root = a.stfmr_lockin_tab = StandIn()
    a.stfmr_lockin_resource_vars = {key: Variable("") for key in ("kepco", "gauss", "smb", "830", "mfli")}
    a.stfmr_lockin_connection_vars = dict(a.stfmr_lockin_resource_vars)
    a.stfmr_lockin_resource_widgets, a.stfmr_lockin_connect_buttons = {}, {}
    a.stfmr_lockin_disconnect_buttons = {}
    a._ui_px = lambda value, minimum=1, **_options: value
    a._arrange_pair_by_width = lambda *_args, **_options: None
    a._create_connection_indicator = lambda master, _name: StandIn(master)
    a._redraw_stfmr_lockin_live_plots = a._redraw_stfmr_lockin_plot = lambda: None

    def zurich_chooser(master, *_args, **_options):
        chooser = StandIn(master)
        chooser.zurich_shortfall = 0
        return chooser

    with mock.patch.object(app, "ttk", fake_ttk), mock.patch.object(app, "tkfont", fake_font), \
            mock.patch.object(app.tk, "Canvas", kind("Canvas")), \
            mock.patch.object(app.tk, "Button", kind("Button")), \
            mock.patch.object(app, "create_zurich_chooser", zurich_chooser):
        App.build_stfmr_lockin_ui(a)

    def in_row(master, row):
        return sorted((w for w in widgets if w.master is master and w.grid_options
                       and w.grid_options.get("row") == row),
                      key=lambda w: w.grid_options["column"])

    def cell(master, row, column):
        (widget,) = [w for w in in_row(master, row) if w.grid_options["column"] == column]
        return widget

    return types.SimpleNamespace(app=a, in_row=in_row, cell=cell)


def sr830_setup(time_constant_code):
    """configure()'s writes as of 5d91c14, with the time constant chosen."""
    return ["*CLS", "FMOD 0", "RSLP 0", "HARM 1", "ISRC 0", "IGND 0", "ICPL 0",
            "ILIN 0", "SYNC 0", "SENS 13", f"OFLT {time_constant_code}", "OFSL 1",
            "RMOD 1", "DDEF 1,1,0", "DDEF 2,1,0"]


class NoWindows(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.confirm = mock.Mock(return_value=True)
        self.error = mock.Mock()
        unexpected = mock.Mock(side_effect=AssertionError("an unexpected dialog opened"))
        patches = [
            mock.patch("tkinter.Tk", side_effect=AssertionError("a test tried to open a Tk window")),
            mock.patch.object(tkinter.messagebox, "askyesno", self.confirm),
            mock.patch.object(tkinter.messagebox, "showerror", self.error),
        ]
        for name in ("showwarning", "showinfo", "askokcancel", "askyesnocancel",
                     "askquestion", "askretrycancel"):
            patches.append(mock.patch.object(tkinter.messagebox, name, unexpected))
        for name in ("askdirectory", "asksaveasfilename", "askopenfilename", "askopenfilenames"):
            patches.append(mock.patch.object(tkinter.filedialog, name, unexpected))
        for name in ("askstring", "askfloat", "askinteger"):
            patches.append(mock.patch.object(tkinter.simpledialog, name, unexpected))
        for patcher in patches:
            patcher.start()
            self.addCleanup(patcher.stop)

    def run_sweep(self, meter="SR830", delay="0.5", tc="1 s", start=None, loop=None,
                  start_only=False, **kwargs):
        h = harness(self.tmp.name, meter=meter, **kwargs)
        h.root = TimedRoot()
        h.stfmr_lockin_status_var = History()
        h.stfmr_lockin_delay_var.set(delay)
        h.stfmr_lockin_sr830_time_constant_var = Variable(tc)
        if start is not None:
            h.stfmr_lockin_start_var.set(start)
        if loop is not None:
            h.stfmr_lockin_loop_var.set(loop)
        h.start_stfmr_lockin_scan()
        recorder = h.stfmr_lockin_recorder      # the sweep's end releases it
        if not start_only:
            h.root.drain()
        return h, recorder

    def message(self):
        self.confirm.assert_called_once()
        return self.confirm.call_args.args[1]


class Sr830TimeConstantTests(NoWindows):
    """STFMR set the SR830 to a fixed 1 s (OFLT 10, 12 dB/oct) while the
    default Wait/pt is 0.5 s, so each SR830 point was a lagged reading."""

    def test_the_choices_are_the_sr830_oflt_codes_and_1_s_stays_the_default(self):
        choices = app.STFMR_LOCKIN_SR830_TIME_CONSTANTS
        self.assertEqual(list(choices), ["1 ms", "3 ms", "10 ms", "30 ms", "100 ms",
                                         "300 ms", "1 s", "3 s", "10 s"])
        for label, code in choices.items():
            number, unit = label.split()
            seconds = float(number) * (1e-3 if unit == "ms" else 1.0)
            self.assertAlmostEqual(app.SR830_TIME_CONSTANTS_S[code], seconds, delta=seconds * 1e-9, msg=label)
        self.assertEqual(app.STFMR_LOCKIN_SR830_DEFAULT_TIME_CONSTANT, "1 s")
        self.assertEqual(choices["1 s"], 10)
        self.assertEqual(app.STFMR_LOCKIN_SR830_TIME_CONSTANT_CODE, 10)
        self.assertEqual(app.STFMR_LOCKIN_SR830_SETTLE_TAUS, 5.0)

    def test_configure_sends_the_chosen_code_in_the_same_place_and_reads_it_back(self):
        for code in (10, 4, 8, 12):
            with self.subTest(code=code):
                fake = FakeSR830()
                controller = app.STFMRSR830Controller(lambda _message: None)
                controller.instrument = fake
                if code == 10:
                    result = controller.configure()      # the default, unchanged set-up
                else:
                    result = controller.configure("Sine", True, code)
                self.assertEqual(fake.writes, sr830_setup(code))
                self.assertEqual(fake.queries, ["FMOD?", "RSLP?", "HARM?", "SENS?", "OFLT?"])
                self.assertEqual(result["time_constant_index"], code)

    def test_a_time_constant_the_sr830_does_not_keep_stops_the_set_up(self):
        fake = FakeSR830(keeps_time_constant=6)
        controller = app.STFMRSR830Controller(lambda _message: None)
        controller.instrument = fake
        with self.assertRaisesRegex(RuntimeError, "time constant readback mismatch: requested OFLT 4, readback 6"):
            controller.configure("Sine", True, 4)
        for bad in (3, 13, "10", None):
            with self.subTest(code=bad):
                fake = FakeSR830()
                controller.instrument = fake
                with self.assertRaisesRegex(ValueError, "not an 'SR830 TC' choice"):
                    controller.configure("Sine", True, bad)
                self.assertEqual(fake.writes, [], "nothing sent")

    def test_an_sr830_sweep_waits_five_time_constants_at_each_point(self):
        h, recorder = self.run_sweep(stop="5")           # 1 frequency, 5 currents
        text = self.message()
        self.assertIn("SR830 TC: 1 s (12 dB/oct); each point waits 5 s (Wait/pt 0.5 s, at least 5 × TC)", text)
        self.assertIn("Estimated at least 20.0 s.", text)  # 4 waits of 5 s
        # The first read follows the start pre-read at once, as before; it is
        # discarded.  Every later point waits 5 s, not the 0.5 s Wait/pt.
        self.assertEqual(h.root.reads, [1, 5000, 5000, 5000, 5000])
        self.assertIn(("configure", ("Sine", True, 10)), h.stfmr_lockin_controllers["830"].calls)
        rows = read_csv(recorder.csv_path)
        self.assertEqual(len(rows), 3)
        self.assertEqual({row["sr830_time_constant_s"] for row in rows}, {"1.0"})
        settings = events(recorder)[0]["settings"]
        self.assertEqual((settings["sr830_time_constant_index"], settings["sr830_time_constant_s"],
                          settings["effective_point_wait_s"], settings["point_delay_s"]), (10, 1.0, 5.0, 0.5))
        self.assertEqual(events(recorder)[-1]["outcome"], "completed")
        self.error.assert_not_called()

    def test_the_wait_is_the_longer_of_wait_per_point_and_five_time_constants(self):
        for tc, delay, code, wait_ms in (("100 ms", "0.5", 8, 500), ("300 ms", "0.5", 9, 1500),
                                         ("10 ms", "0", 6, 50), ("3 s", "20", 11, 20000)):
            with self.subTest(tc=tc, delay=delay):
                self.confirm.reset_mock()
                h, recorder = self.run_sweep(stop="5", tc=tc, delay=delay)
                self.assertEqual(h.root.reads, [1] + [wait_ms] * 4)
                self.assertIn(("configure", ("Sine", True, code)), h.stfmr_lockin_controllers["830"].calls)
                self.assertIn(f"each point waits {wait_ms / 1000:g} s", self.message())
                self.assertEqual(events(recorder)[0]["settings"]["effective_point_wait_s"], wait_ms / 1000)

    def test_the_re_read_after_a_sensitivity_change_waits_too(self):
        h, recorder = self.run_sweep(stop="5", start_only=True)
        while not h.stfmr_lockin_rows:
            h.root.step()
        h.stfmr_lockin_controllers["830"].retry_next = True   # the next read changes SENS
        h.root.drain()
        self.assertTrue(any(e["event"] == "sr830_autorange_retry" for e in events(recorder)))
        self.assertEqual(h.root.reads, [1] + [5000] * 5, "the re-read waited 0.5 s")
        self.assertEqual(len(read_csv(recorder.csv_path)), 3)

    def test_dual_meter_points_and_both_re_reads_wait_too(self):
        h, recorder = self.run_sweep(meter="MFLI + SR830", stop="5", start_only=True)
        self.assertIn("SR830 TC: 1 s (12 dB/oct); each point waits 5 s", self.message())
        while not h.stfmr_lockin_rows:
            h.root.step()
        h.stfmr_lockin_controllers["830"].retry_next = True   # an SR830 range change
        mfli_read = h.stfmr_lockin_mfli_controller.read_stfmr_mfli
        changes = iter([False, True])                          # then an MFLI one

        def read_stfmr_mfli(*args, **kwargs):
            sample = mfli_read(*args, **kwargs)
            if next(changes, False):
                sample = dict(sample, auto_range="RANGE 0.03->0.1 V (high)")
            return sample

        h.stfmr_lockin_mfli_controller.read_stfmr_mfli = read_stfmr_mfli
        h.root.drain()
        names = [e["event"] for e in events(recorder)]
        self.assertEqual(names.count("sr830_autorange_retry"), 1)
        # The stand-in MFLI's first-point range check re-reads point 1 once,
        # then the one above.
        self.assertEqual(names.count("mfli_autorange_retry"), 2)
        # Before: 0.5 s per point, max(0.5, 10 x 0.1 s) = 1 s for each re-read.
        self.assertEqual(h.root.reads, [1] + [5000] * 7)
        self.assertEqual({e["settle_s"] for e in events(recorder) if e["event"] == "mfli_autorange_retry"},
                         {5.0})
        rows = read_csv(recorder.csv_path)
        self.assertEqual([row["meter"] for row in rows], ["MFLI", "SR830"] * 3)
        self.assertEqual([row["sr830_time_constant_s"] for row in rows], ["nan", "1.0"] * 3)

    def test_the_mfli_alone_keeps_wait_per_point_and_records_no_sr830_tc(self):
        # The SR830 TC is not read when the SR830 is not used, as the MFLI TC
        # is not read for the SR830 alone.
        h, recorder = self.run_sweep(meter="MFLI", tc="not a choice")
        text = self.message()
        self.assertNotIn("SR830 TC", text)
        self.assertIn("Estimated at least 4.0 s.", text)    # 2 x 4 waits of 0.5 s
        # 1 s: the stand-in MFLI's first-point range check re-reads point 1
        # after max(Wait/pt, 10 x the 0.1 s MFLI TC), as before.
        self.assertEqual(h.root.reads, [1, 1000, 500, 500, 500, 500] * 2)
        rows = read_csv(recorder.csv_path)
        self.assertEqual(len(rows), 6)
        self.assertEqual({row["sr830_time_constant_s"] for row in rows}, {"nan"})
        self.assertEqual(events(recorder)[0]["settings"]["effective_point_wait_s"], 0.5)
        self.error.assert_not_called()

    def test_a_choice_not_in_the_list_is_refused_before_anything_starts(self):
        h, recorder = self.run_sweep(tc="2 s")
        self.error.assert_called_once()
        self.assertEqual(self.error.call_args.args[1],
                         "SR830 TC must be one of: 1 ms, 3 ms, 10 ms, 30 ms, 100 ms, 300 ms, 1 s, 3 s, 10 s")
        self.confirm.assert_not_called()
        self.assertIsNone(recorder)
        self.assertFalse(h.stfmr_lockin_running)
        for key in ("kepco", "smb", "830"):
            self.assertEqual(h.stfmr_lockin_controllers[key].calls, [], key)
        self.assertEqual(list(Path(self.tmp.name).iterdir()), [], "no files")

    def test_the_choice_is_locked_while_a_sweep_runs(self):
        class Widget:
            def __init__(self, state):
                self.state = state

            def configure(self, **options):
                self.state = options.get("state", self.state)

        a = object.__new__(App)
        a.stfmr_lockin_running = False
        a.stfmr_lockin_gauss_busy = False
        a.stfmr_lockin_rows = []
        a.stfmr_lockin_meter_var = Variable("SR830")
        a.stfmr_lockin_controllers = {key: types.SimpleNamespace(connected=True)
                                      for key in ("kepco", "gauss", "smb", "830")}
        a.stfmr_lockin_mfli_controller = types.SimpleNamespace(mfli_connected=False)
        a.stfmr_lockin_connect_buttons, a.stfmr_lockin_disconnect_buttons = {}, {}
        a.stfmr_lockin_connection_vars, a.stfmr_lockin_resource_widgets = {}, {}
        for name in ("start", "stop", "save", "read_gauss"):
            setattr(a, f"stfmr_lockin_{name}_button", Widget("normal"))
        a.stfmr_lockin_meter_combo = Widget("readonly")
        a.stfmr_lockin_sr830_time_constant_combo = combo = Widget("readonly")
        for running, state in ((True, "disabled"), (False, "readonly")):
            a.stfmr_lockin_running = running
            App._update_stfmr_lockin_ui(a)
            self.assertEqual(combo.state, state, f"running={running}")
            self.assertEqual(a.stfmr_lockin_meter_combo.state, state, "locked as the meter is")

    def test_the_choice_sits_under_loop_in_the_parameter_table(self):
        """The real page build, on stand-in widgets that keep their options
        and grid cells (the CI GUI tests check the drawn page)."""
        page = build_stfmr_page()
        choice = page.app.stfmr_lockin_sr830_time_constant_combo
        table = choice.master
        self.assertIs(table, page.app.stfmr_lockin_meter_combo.master, "in the parameter table")
        self.assertEqual(choice.kind, "Combobox")
        self.assertEqual((choice.grid_options["row"], choice.grid_options["column"],
                          choice.grid_options["sticky"]), (3, 7, "ew"))
        self.assertEqual((choice.options["state"], choice.options["width"]), ("readonly", 6))
        self.assertEqual(choice.options["values"], tuple(app.STFMR_LOCKIN_SR830_TIME_CONSTANTS))
        self.assertIs(choice.options["textvariable"], page.app.stfmr_lockin_sr830_time_constant_var)
        self.assertEqual(page.cell(table, 3, 6).options["text"], "SR830 TC:")
        loop = page.cell(table, 2, 7)                # the cell above: Loop, also 6 wide
        self.assertEqual((loop.kind, loop.options["width"]), ("Spinbox", 6))
        self.assertEqual(page.cell(table, 2, 6).options["text"], "Loop:")
        self.assertEqual(page.cell(table, 3, 2).options["text"], "MFLI TC s:", "the MFLI TC row")
        self.assertEqual([w.grid_options["column"] for w in page.in_row(table, 3)],
                         [0, 1, 2, 3, 4, 6, 7], "nothing past the columns rows 0 and 1 use")
        # The column still gives way, but not below the dropdown (70 px here;
        # the six-character floor is 50 px).
        self.assertEqual(table.column_options[7], {"weight": 1, "minsize": 70})
        self.assertEqual(table.column_options[5], {"weight": 1, "minsize": 50})
        self.assertLessEqual(max(map(len, app.STFMR_LOCKIN_SR830_TIME_CONSTANTS)), 6)


class FixedCurrentTests(NoWindows):
    """Start refused a loop of under 3 currents (0e82358) because the first
    two points of every loop were discarded (ca5ee54): a fixed-current,
    multi-frequency run was impossible."""

    discard = staticmethod(App._stfmr_should_discard_startup_point)

    def test_only_reads_after_a_current_jump_are_discarded(self):
        self.assertEqual([self.discard(i, 1) for i in range(6)],
                         [True, True, False, False, False, False], "fixed current")
        for per_loop in (2, 3, 5):        # sweeps: unchanged, two per loop
            for index in range(4 * per_loop):
                self.assertEqual(self.discard(index, per_loop), index % per_loop < 2, (index, per_loop))
        self.assertEqual([self.discard(i) for i in range(4)], [True, True, False, False])

    def test_the_saved_count_follows_the_rule(self):
        for per_loop in range(1, 7):
            for loops in range(1, 6):
                expected = sum(not self.discard(i, per_loop) for i in range(per_loop * loops))
                self.assertEqual(App._stfmr_saved_points_per_frequency(per_loop, loops), expected,
                                 (per_loop, loops))

    def test_a_fixed_current_run_saves_all_but_the_first_two_readings_of_each_frequency(self):
        h, recorder = self.run_sweep(meter="MFLI", start="1", end_current="1", loop=4)
        self.assertIn("4 readings per frequency at a fixed 1 A, first two readings of each "
                      "frequency discarded, about 4 points saved", self.message())
        self.assertIn("Estimated at least 3.0 s.", self.message())     # 6 waits of 0.5 s
        rows = read_csv(recorder.csv_path)
        self.assertEqual([int(r["index"]) for r in rows], [3, 4, 7, 8])
        self.assertEqual([int(r["loop"]) for r in rows], [3, 4, 3, 4])
        self.assertEqual([float(r["microwave_frequency_ghz"]) for r in rows], [5, 5, 6, 6])
        self.assertEqual({float(r["current_a"]) for r in rows}, {1.0})
        history = events(recorder)
        self.assertEqual(history[0]["settings"]["expected_saved_points"], 4)
        self.assertEqual(sum(e["event"] == "startup_point_discarded" for e in history), 4)
        self.assertEqual([e["saved_points"] for e in history if e["event"] == "frequency_complete"], [2, 2])
        self.assertEqual((history[-1]["outcome"], history[-1]["saved_points"], history[-1]["outputs_off"]),
                         ("completed", 4, True))
        # The 4886 is switched on at each frequency's start, set to the same
        # current for each reading, and zeroed at each frequency's end.
        output = ("output", ("CURR", 1.0, 10.0))
        self.assertEqual(h.stfmr_lockin_controllers["kepco"].calls,
                         [output] + [("current", 1.0)] * 3 + [("zero",)]
                         + [output] + [("current", 1.0)] * 3 + [("zero",)])
        self.assertIn(("rf_off",), h.stfmr_lockin_controllers["smb"].calls)
        discarded = [text for text in h.stfmr_lockin_status_var.history if "discarded start-transition" in text]
        self.assertEqual(len(discarded), 4)
        for first, second in (discarded[0:2], discarded[2:4]):
            self.assertRegex(first, r"point 1/2: I=\+1 A, .*; taking the second reading$")
            self.assertRegex(second, r"point 2/2: I=\+1 A, .*; saving from the third reading$")
        self.error.assert_not_called()

    def test_a_dual_meter_fixed_current_run_saves_pairs(self):
        h, recorder = self.run_sweep(meter="MFLI + SR830", stop="5", start="-2", end_current="-2", loop=3)
        self.assertIn("about 2 points saved", self.message())            # one field point, two rows
        rows = read_csv(recorder.csv_path)
        self.assertEqual([(r["index"], r["meter"]) for r in rows], [("3", "MFLI"), ("3", "SR830")])
        # The MFLI's first-point range check re-reads point 1 once.
        self.assertEqual(h.root.reads, [1, 5000, 5000, 5000])
        self.assertEqual(events(recorder)[-1]["saved_points"], 2)

    def test_a_sweep_still_discards_two_points_per_loop_and_counts_them_per_loop(self):
        h, recorder = self.run_sweep(meter="MFLI", stop="5", loop=2)       # 5 currents, 2 loops
        rows = read_csv(recorder.csv_path)
        self.assertEqual([int(r["index"]) for r in rows], [3, 4, 5, 8, 9, 10])
        discarded = [text for text in h.stfmr_lockin_status_var.history if "discarded start-transition" in text]
        # Loop 2's were shown as points 6/2 and 7/2.
        self.assertEqual([re.search(r"point (\d+)/2", text).group(1) for text in discarded], ["1", "2", "1", "2"])
        self.assertTrue(discarded[0].endswith("moving to the second current point"))
        self.assertTrue(discarded[1].endswith("saving from the third current point"))
        self.assertIn("first two points of each loop discarded, about 6 points saved", self.message())

    def test_only_runs_that_would_save_nothing_are_refused_before_any_output(self):
        for start, end, loop, text in (("-1", "-0.5", 3, "gives 2 current points per loop"),
                                       ("1", "1", 2, "Loop 2 would save nothing: set Loop to at least 3"),
                                       ("1", "1", 1, "Loop 1 would save nothing: set Loop to at least 3")):
            with self.subTest(start=start, end=end, loop=loop):
                self.error.reset_mock()
                h, recorder = self.run_sweep(meter="MFLI", start=start, end_current=end, loop=loop)
                self.error.assert_called_once()
                self.assertIn(text, self.error.call_args.args[1])
                self.confirm.assert_not_called()
                self.assertIsNone(recorder)
                self.assertFalse(h.stfmr_lockin_running)
                for key in ("kepco", "smb", "830"):
                    self.assertEqual(h.stfmr_lockin_controllers[key].calls, [], key)
                self.assertEqual(list(Path(self.tmp.name).iterdir()), [], "no files")

    def test_the_legend_names_each_frequency_on_its_first_saved_loop(self):
        # A fixed-current run saves loops 3 and on, and only loop 1 was named.
        series = {f"{key}_freq_{f}_loop_{loop}": [(0.0, 0.0)]
                  for key in ("mfli", "8") for f in (5, 6) for loop in (3, 4)}
        labels, _colors = App._stfmr_lockin_frequency_series_styles(series, "x_v")
        self.assertEqual(labels["mfli_freq_5_loop_3"], "5 GHz MFLI")
        self.assertEqual(labels["8_freq_6_loop_3"], "6 GHz SR830")
        self.assertEqual([labels[k] for k in series if k.endswith("_loop_4")], [""] * 4)
        sweep = {"mfli_freq_5_loop_1": [], "mfli_freq_5_loop_2": []}
        labels, _colors = App._stfmr_lockin_frequency_series_styles(sweep, "x_v")
        self.assertEqual(labels, {"mfli_freq_5_loop_1": "5 GHz", "mfli_freq_5_loop_2": ""})


if __name__ == "__main__":
    unittest.main()
