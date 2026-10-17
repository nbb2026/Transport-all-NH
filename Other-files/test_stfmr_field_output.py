"""STFMR manual KEPCO field output, and the narrowed address fields.

The panel mirrors the FMR page's "KEPCO 4886 BOP 20-10 field output". It is
exercised here inside the fully built application, with the real KEPCO
controller writing to a simulated BIT 4886, so the safety interlocks are
tested end to end rather than against a stub.
"""
import time
import tkinter as tk
import tkinter.font
import unittest
from unittest import mock

from lab_screen import LAB_WIDE, build_lab_app, lab_window
from test_ppms_motor_debug import app

CI_SHOT_HEIGHT = 1300  # gui_checks.py's window height, the CI screenshots'


class FakeVisaTimeout(Exception):
    def __init__(self):
        super().__init__("VI_ERROR_TMO (-1073807339): Timeout expired before operation completed.")
        self.error_code = app.VI_ERROR_TMO_CODE


class FakeBit4886:
    """Write-only card: records each command; can stall on chosen ones."""

    def __init__(self, stall=()):
        self.stall = set(stall)
        self.writes = []
        self.attempts = []
        self.clears = 0

    def write_raw(self, payload):
        command = payload.decode("ascii")
        self.attempts.append(command)
        if command in self.stall:
            raise FakeVisaTimeout()
        self.writes.append(command)

    def clear(self):
        self.clears += 1

    def close(self):
        pass


def descendants(widget):
    for child in widget.winfo_children():
        yield child
        yield from descendants(child)


class FieldOutputTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        try:
            cls.root = tk.Tk()
        except tk.TclError as error:  # headless build server
            raise unittest.SkipTest(f"no display: {error}")
        # As the lab PC shows it (2560x1440 at 96 DPI), whatever runs the tests.
        cls.app = build_lab_app(app, cls.root)
        cls.LAB_DEFAULT, cls.LAB_HEIGHT = lab_window(cls.app)
        cls.NARROWEST = int(cls.root.minsize()[0])
        cls.root.geometry(f"{LAB_WIDE}x{cls.LAB_HEIGHT}+0+0")
        notebook = next(w for w in cls.app.__dict__.values() if isinstance(w, tk.ttk.Notebook))
        for index in range(notebook.index("end")):
            if notebook.tab(index, "text") == "STFMR":
                notebook.select(index)
        cls.root.update()

    @classmethod
    def tearDownClass(cls):
        cls.app.closing = True
        cls.app.safe_close_hardware()
        cls.root.destroy()

    def setUp(self):
        self.a = self.app
        self.kepco = self.a.stfmr_lockin_controllers["kepco"]
        self.warnings, self.errors = [], []
        patches = (
            mock.patch.object(app.messagebox, "showwarning", lambda *a, **k: self.warnings.append(a)),
            mock.patch.object(app.messagebox, "showerror", lambda *a, **k: self.errors.append(a)),
        )
        for patcher in patches:
            patcher.start()
            self.addCleanup(patcher.stop)
        self.addCleanup(self.reset)

    def reset(self):
        self.kepco.instrument = None
        self.kepco.output_owned = False
        self.kepco.active_mode = ""
        self.kepco.last_command = ""
        self.a.stfmr_lockin_after_id = None
        self.a.stfmr_lockin_running = False
        self.a.stfmr_lockin_stop_event.clear()
        self.a.stfmr_lockin_manual_safety_var.set(False)
        self.a.stfmr_lockin_manual_mode_var.set("Current")
        self.a._on_stfmr_lockin_manual_mode_changed()
        self.a.stfmr_lockin_manual_target_var.set("0")
        self.a.stfmr_lockin_manual_limit_var.set("5")
        self.a._set_stfmr_lockin_device_ui("kepco", False)

    def connect(self, card=None):
        card = card or FakeBit4886()
        self.kepco.instrument = card
        self.kepco.last_command = ""   # a fresh session has sent nothing
        self.a._set_stfmr_lockin_device_ui("kepco", True)
        return card

    def states(self):
        return tuple(str(b.cget("state")) for b in (
            self.a.stfmr_lockin_apply_output_button,
            self.a.stfmr_lockin_read_output_button,
            self.a.stfmr_lockin_zero_output_button))

    # ---------------------------------------------------------------- layout
    def page_frames(self, tab):
        return {w.cget("text"): w for w in descendants(tab) if isinstance(w, tk.ttk.LabelFrame)}

    def test_connections_are_left_of_the_output_panel(self):
        for tab_name, tab, title in (("STFMR", self.a.stfmr_lockin_tab, "STFMR instrument connections"),
                                     ("FMR", self.a.stfmr_tab, "FMR instrument connections")):
            with self.subTest(page=tab_name):
                self.arranged(LAB_WIDE, tab_name, self.a.stfmr_lockin_resource_widgets
                              if tab_name == "STFMR" else self.a.stfmr_device_resource_widgets)
                frames = self.page_frames(tab)
                panel = frames["KEPCO 4886 BOP 20-10 field output"]
                connections = frames[title]
                self.assertIs(panel.master, connections.master, "they share one top row")
                self.assertEqual(int(panel.master.grid_info()["row"]), 0)
                self.assertEqual((int(connections.grid_info()["column"]), int(panel.grid_info()["column"])), (0, 1))
                self.assertLess(connections.winfo_rootx(), panel.winfo_rootx())
                self.assertEqual(int(panel.master.columnconfigure(1)["weight"]), 1,
                                 "the panel takes the spare width; the list stays compact")
        self.arranged(LAB_WIDE, "STFMR", self.a.stfmr_lockin_resource_widgets)
        frames = self.page_frames(self.a.stfmr_lockin_tab)
        self.assertEqual(int(frames["STFMR field-sweep parameters"].grid_info()["row"]), 1)
        outer = frames["KEPCO 4886 BOP 20-10 field output"].master.master
        self.assertEqual(int(outer.rowconfigure(4)["weight"]), 1, "plots keep the spare height")
        self.assertEqual(max(int(w.grid_info()["row"]) for w in outer.winfo_children()), 4)

    def test_connections_are_one_compact_column(self):
        rows = {key: int(entry.master.grid_info()["row"])
                for key, entry in self.a.stfmr_lockin_resource_widgets.items()}
        self.assertEqual(rows, {"kepco": 0, "gauss": 1, "smb": 2, "830": 3, "mfli": 4})
        self.assertEqual({int(e.master.grid_info()["column"])
                          for e in self.a.stfmr_lockin_resource_widgets.values()}, {0})
        # The Motor row, the sixth (user, 2026-10-17).
        motor = self.a.stfmr_lockin_motor_widgets[0].master
        self.assertEqual((int(motor.grid_info()["row"]), int(motor.grid_info()["column"])), (5, 0))
        self.assertIs(motor.master, self.a.stfmr_lockin_resource_widgets["kepco"].master.master)

    def test_the_motor_row_lines_up_with_the_other_rows(self):
        """User, 2026-10-17: "add the connection button for Motor in RT-E and
        ST-FMR modules": a row laid out as the five above, its label, its
        18-character "auto" field, Connect, Disconnect, lamp and 13-character
        status in the same columns, in the 1101 px and 2000 px windows."""
        entry, connect, disconnect = self.a.stfmr_lockin_motor_widgets
        kepco = self.a.stfmr_lockin_resource_widgets["kepco"]
        try:
            for width in (self.LAB_DEFAULT, LAB_WIDE):
                with self.subTest(width=width):
                    self.arranged(width, "STFMR", self.a.stfmr_lockin_resource_widgets)
                    for mine, theirs in ((entry, kepco), (connect, self.a.stfmr_lockin_connect_buttons["kepco"]),
                                         (disconnect, self.a.stfmr_lockin_disconnect_buttons["kepco"])):
                        self.assertEqual((mine.winfo_rootx(), mine.winfo_width()),
                                         (theirs.winfo_rootx(), theirs.winfo_width()))
                    labels = {}
                    for frame, key in ((entry.master, "motor"), (kepco.master, "kepco")):
                        labels[key] = [w for w in frame.winfo_children() if w.winfo_class() == "TLabel"]
                    self.assertEqual([str(w.cget("text")) for w in labels["motor"]][0], "Motor:")
                    self.assertEqual([(w.winfo_rootx(), w.winfo_width()) for w in labels["motor"]],
                                     [(w.winfo_rootx(), w.winfo_width()) for w in labels["kepco"]])
                    self.assertTrue(entry.winfo_ismapped() and connect.winfo_ismapped())
            self.assertEqual(self.a.stfmr_lockin_motor_resource_var.get(), "auto")
            self.assertEqual(int(entry.cget("width")), 18)
        finally:
            self.arranged(LAB_WIDE, "STFMR", self.a.stfmr_lockin_resource_widgets)

    def test_the_stage_controls_share_the_read_ls475_line(self):
        """User, 2026-10-17: Set motor 0° and Move stage on the STFMR page.
        Stage °, Move stage, Stop stage and Set motor 0° are kept together,
        every text whole, inside the page: on the Read LS475 line in the 1101
        px window (the actions under the table), on a line of their own under
        it at 2000 px (the actions beside the table, which stay side by side:
        on the Read LS475 line the actions were 912 px wide on Windows and
        stacked).  Whatever the resize history."""
        a = self.a
        group = [a.stfmr_lockin_stage_target_entry, a.stfmr_lockin_stage_move_button,
                 a.stfmr_lockin_stage_stop_button, a.stfmr_lockin_motor_zero_button]
        read = a.stfmr_lockin_read_gauss_button
        line = read.master.master
        font = tkinter.font.nametofont("TkDefaultFont")
        table = a.stfmr_lockin_meter_combo.master
        expected = {self.LAB_DEFAULT: (False, True), LAB_WIDE: (True, False)}
        try:
            for history in ((self.LAB_DEFAULT,), (LAB_WIDE,), (LAB_WIDE, self.LAB_DEFAULT),
                            (self.LAB_DEFAULT, LAB_WIDE), (826, LAB_WIDE, self.LAB_DEFAULT)):
                for width in history:
                    self.arranged(width, "STFMR", a.stfmr_lockin_resource_widgets)
                width = history[-1]
                with self.subTest(history=history):
                    self.assertEqual((getattr(table.master, "_pair_side_by_side", None),
                                      getattr(line, "_stage_controls_beside", None)), expected[width])
                    tab = a.stfmr_lockin_tab
                    right = tab.winfo_rootx() + tab.winfo_width()
                    beside = expected[width][1]
                    for widget in group:
                        self.assertTrue(widget.winfo_ismapped())
                        self.assertIs(widget.master.master, line)
                        middle = widget.winfo_rooty() + widget.winfo_height() / 2
                        if beside:
                            self.assertLessEqual(abs(middle - read.winfo_rooty() - read.winfo_height() / 2), 3,
                                                 "on the Read LS475 line")
                            self.assertGreater(widget.winfo_rootx(), read.winfo_rootx())
                        else:
                            self.assertGreaterEqual(widget.winfo_rooty(), read.winfo_rooty() + read.winfo_height(),
                                                    "under the Read LS475 line")
                        self.assertLessEqual(widget.winfo_rootx() + widget.winfo_width(), right + 1)
                        if widget.winfo_class() == "TButton":
                            self.assertLessEqual(font.measure(str(widget.cget("text"))), widget.winfo_width() - 10)
                    xs = [w.winfo_rootx() for w in group]
                    self.assertEqual(xs, sorted(xs), "Stage °, Move stage, Stop stage, Set motor 0°")
                    actions = line.master
                    print(f"STFMR {self.root.winfo_width()} px: table {table.winfo_width()} px "
                          f"(asks {table.winfo_reqwidth()}, {table.winfo_reqheight()} px tall), actions "
                          f"{actions.winfo_width()} px (asks {actions.winfo_reqwidth()}, "
                          f"{actions.winfo_reqheight()} px tall), stage controls beside {beside}")
        finally:
            self.arranged(LAB_WIDE, "STFMR", a.stfmr_lockin_resource_widgets)

    def test_the_stage_controls_follow_the_motor_and_the_sweep(self):
        a = self.a
        motor = a.stfmr_lockin_motor
        hand = (a.stfmr_lockin_stage_target_entry, a.stfmr_lockin_stage_move_button, a.stfmr_lockin_motor_zero_button)
        states = lambda: ([str(w.cget("state")) for w in hand], str(a.stfmr_lockin_stage_stop_button.cget("state")))
        try:
            a._update_stfmr_lockin_ui()
            self.assertEqual(states(), (["disabled"] * 3, "disabled"), "not connected")
            motor.handle = object()             # connected, as far as the page can tell
            a._update_stfmr_lockin_ui()
            self.assertEqual(states(), (["normal"] * 3, "normal"))
            a.stfmr_lockin_running = True
            a._update_stfmr_lockin_ui()
            self.assertEqual(states(), (["disabled"] * 3, "normal"), "Stop stage stays usable during a sweep")
            a.stfmr_lockin_running = False
            a.stfmr_lockin_motor_busy = True
            a._update_stfmr_lockin_ui()
            self.assertEqual(states(), (["disabled"] * 3, "normal"), "and during a hand move")
        finally:
            motor.handle = None
            a.stfmr_lockin_running = a.stfmr_lockin_motor_busy = False
            a._update_stfmr_lockin_ui()

    def arranged(self, width, tab_name, widgets):
        """Show a page at a window width; the width is checked, not assumed."""
        self.root.geometry(f"{width}x{self.LAB_HEIGHT}+0+0")
        notebook = next(w for w in self.a.__dict__.values() if isinstance(w, tk.ttk.Notebook))
        for index in range(notebook.index("end")):
            if notebook.tab(index, "text") == tab_name:
                notebook.select(index)
        for _ in range(3):
            self.root.update()
        self.assertEqual(self.root.winfo_width(), width, "the window did not take this width")
        return getattr(widgets["kepco"].master.master.master, "_pair_side_by_side", None)

    def test_top_row_stacks_when_the_window_is_narrow(self):
        """Stacked in the 1101 px window the program opens, side by side at 2000 px."""
        try:
            for tab_name, widgets in (("STFMR", self.a.stfmr_lockin_resource_widgets),
                                      ("FMR", self.a.stfmr_device_resource_widgets)):
                with self.subTest(page=tab_name):
                    self.assertFalse(self.arranged(self.LAB_DEFAULT, tab_name, widgets), "narrow: stacked")
                    connections = widgets["kepco"].master.master
                    self.assertEqual(int(connections.grid_info()["row"]), 0, "stacked: connections first")
                    self.assertTrue(self.arranged(LAB_WIDE, tab_name, widgets), "wide: side by side")
        finally:
            self.arranged(LAB_WIDE, "STFMR", self.a.stfmr_lockin_resource_widgets)

    def test_nothing_is_clipped_in_a_narrow_window(self):
        try:
            for tab_name, tab, widgets in (
                    ("STFMR", self.a.stfmr_lockin_tab, self.a.stfmr_lockin_resource_widgets),
                    ("FMR", self.a.stfmr_tab, self.a.stfmr_device_resource_widgets)):
                self.arranged(self.LAB_DEFAULT, tab_name, widgets)
                right = tab.winfo_rootx() + tab.winfo_width()
                over = [w for w in descendants(tab)
                        if w.winfo_ismapped() and w.winfo_rootx() + w.winfo_width() > right + 1]
                self.assertEqual(over, [], f"{tab_name}: widgets past the right edge")
        finally:
            self.arranged(LAB_WIDE, "STFMR", self.a.stfmr_lockin_resource_widgets)

    def test_zero_off_is_whole_in_the_1101_px_window(self):
        """User, 2026-10-15: the red Zero / OFF button at the end of the 4886
        output row was cut at the right edge on both pages.  Grid squeezes a
        button that does not fit inside its box, so it never crossed the
        page's edge and the test above did not see it."""
        try:
            for tab_name, button, widgets in (
                    ("STFMR", self.a.stfmr_lockin_zero_output_button, self.a.stfmr_lockin_resource_widgets),
                    ("FMR", self.a.stfmr_zero_output_button, self.a.stfmr_device_resource_widgets)):
                with self.subTest(page=tab_name):
                    self.arranged(self.LAB_DEFAULT, tab_name, widgets)
                    box = button.master
                    self.assertTrue(button.winfo_ismapped())
                    self.assertGreaterEqual(button.winfo_width(), button.winfo_reqwidth(), "the button is squeezed")
                    self.assertLessEqual(button.winfo_x() + button.winfo_width(), box.winfo_width() - 2,
                                         "the button runs past its box's right border")
        finally:
            self.arranged(LAB_WIDE, "STFMR", self.a.stfmr_lockin_resource_widgets)

    def test_parameter_table_is_aligned(self):
        """Every field in a column starts at the same x and has the same width."""
        self.arranged(LAB_WIDE, "STFMR", self.a.stfmr_lockin_resource_widgets)
        table = self.a.stfmr_lockin_meter_combo.master
        compared = 0
        for column in range(1, 12, 2):
            fields = [w for w in table.grid_slaves(column=column)
                      if w.winfo_class() in ("TEntry", "TCombobox", "TSpinbox")]
            if len(fields) < 2:
                continue
            compared += 1
            with self.subTest(column=column):
                self.assertEqual(len({(w.winfo_x(), w.winfo_width()) for w in fields}), 1,
                                 [(w.winfo_class(), w.winfo_x(), w.winfo_width()) for w in fields])
        # One table, so fields share columns down the rows; separate
        # per-field groups (the old layout) would leave nothing to compare.
        # Four columns hold two or more fields since the table is in
        # measurement order (2026-10-14): Comp. V alone ends row 2 (row 1
        # before the power row of 2026-10-14).
        self.assertGreaterEqual(compared, 4, "fields are not in one shared table")
        for column in range(0, 12, 2):
            for label in table.grid_slaves(column=column):
                if label.winfo_class() == "TLabel":
                    self.assertEqual(label.grid_info()["sticky"], "e", "labels sit against their field")

    def test_sweep_controls_sit_right_of_the_parameters(self):
        self.arranged(LAB_WIDE, "STFMR", self.a.stfmr_lockin_resource_widgets)
        table = self.a.stfmr_lockin_meter_combo.master
        column = self.a.stfmr_lockin_start_button.master.master
        self.assertIs(column.master, table.master, "both inside the field-sweep parameters box")
        self.assertTrue(getattr(table.master, "_pair_side_by_side", False))
        self.assertGreater(column.winfo_rootx(), table.winfo_rootx() + table.winfo_width() - 1)
        for button in (self.a.stfmr_lockin_start_button, self.a.stfmr_lockin_stop_button,
                       self.a.stfmr_lockin_save_button, self.a.stfmr_lockin_read_gauss_button):
            self.assertIs(button.master.master, column)

    def test_the_duplicate_safety_line_is_gone(self):
        boxes = [w.cget("text") for w in descendants(self.a.stfmr_lockin_tab)
                 if w.winfo_class() == "TCheckbutton" and "safety" in str(w.cget("text"))]
        self.assertEqual(len(boxes), 1, boxes)
        self.assertIn("microwave power", boxes[0], "the remaining tick covers what the removed line did")

    def test_sweep_refuses_without_the_panel_safety_tick(self):
        self.a.stfmr_lockin_manual_safety_var.set(False)
        original = self.a._stfmr_lockin_connected
        self.a._stfmr_lockin_connected = lambda _key: True
        try:
            with self.assertRaisesRegex(RuntimeError, "safety confirmation"):
                self.a._stfmr_lockin_inputs()
        finally:
            self.a._stfmr_lockin_connected = original

    def test_fmr_scan_has_two_columns(self):
        self.arranged(LAB_WIDE, "FMR", self.a.stfmr_device_resource_widgets)
        frames = self.page_frames(self.a.stfmr_tab)
        sweep = frames["FMR scan: sweep 4886 current, x-axis is the measured 475 field (signed)"]
        frequency = frames["Fixed-field frequency sweep"]
        self.assertIs(sweep.master, frequency.master)
        self.assertEqual((int(sweep.grid_info()["column"]), int(frequency.grid_info()["column"])), (0, 1))
        self.assertIs(self.a.stfmr_frequency_start_button.master.master, frequency)
        self.assertIs(self.a.stfmr_fmr_start_button.master.master, sweep)

    def test_fmr_buttons_and_labels_show_their_whole_text(self):
        self.arranged(LAB_WIDE, "FMR", self.a.stfmr_device_resource_widgets)
        font = tkinter.font.nametofont("TkDefaultFont")
        cut = []
        for widget in descendants(self.a.stfmr_tab):
            if widget.winfo_class() in ("TButton", "TLabel") and widget.winfo_ismapped():
                text = str(widget.cget("text"))
                if text and not str(widget.cget("textvariable")):
                    slack = 10 if widget.winfo_class() == "TButton" else 0
                    if font.measure(text) > widget.winfo_width() - slack:
                        cut.append(text)
        self.assertEqual(cut, [], "Connect, Disconnect and the sweep buttons were cut")

    def label_showing(self, variable):
        return next(w for w in descendants(self.root)
                    if w.winfo_class() == "TLabel" and str(w.cget("textvariable")) == str(variable))

    def test_live_readouts_fit_their_longest_text_without_resizing_the_column(self):
        """Dual MFLI + SR830 lines and SR830 autorange messages were cut."""
        self.arranged(LAB_WIDE, "STFMR", self.a.stfmr_lockin_resource_widgets)
        column = self.a.stfmr_lockin_start_button.master.master
        width_before = column.winfo_reqwidth()
        font = tkinter.font.nametofont("TkDefaultFont")
        dual = ("MFLI X=+1.2346e-05 Y=-1.2346e-05 V θ=+123.456°\n"
                "SR830 X=+1.2346e-05 Y=-1.2346e-05 V θ=+123.456°")
        autorange = "Ref: 1234.5 Hz; autorange SENS 20->21 (overload/high)"
        try:
            self.a.stfmr_lockin_voltage_var.set(dual)
            self.a.stfmr_lockin_reference_status_var.set(autorange)
            self.root.update()
            voltage = self.label_showing(self.a.stfmr_lockin_voltage_var)
            reference = self.label_showing(self.a.stfmr_lockin_reference_status_var)
            self.assertGreaterEqual(voltage.winfo_width(), max(map(font.measure, dual.splitlines())))
            self.assertGreaterEqual(reference.winfo_width(), font.measure(autorange))
            self.assertEqual(column.winfo_reqwidth(), width_before, "readings must not resize the column")
        finally:
            self.a.stfmr_lockin_voltage_var.set("-- V")
            self.a.stfmr_lockin_reference_status_var.set("Ref: --")

    def test_status_readouts_and_lamps_are_aligned(self):
        """Texts share a left edge, boxes a right edge, lamps one column.

        Before, the texts started at 0, 18 and 22 px, the boxes ended at 464,
        414 and 126 px, and the two dots were 2 px apart and 4 px off level.
        """
        self.arranged(LAB_WIDE, "STFMR", self.a.stfmr_lockin_resource_widgets)
        field, voltage, reference, overload = (self.label_showing(v) for v in (
            self.a.stfmr_lockin_field_var, self.a.stfmr_lockin_voltage_var,
            self.a.stfmr_lockin_reference_status_var, self.a.stfmr_lockin_overload_var))
        single_line = field.winfo_height()
        try:
            for voltage_text, reference_text in (
                    ("V: +1.79187e-06 V", "Ref: 317.3 Hz WARNING"),
                    ("MFLI X=+1.2346e-05 Y=-1.2346e-05 V θ=+123.456°\n"
                     "SR830 X=+1.2346e-05 Y=-1.2346e-05 V θ=+123.456°",
                     "MFLI: 317.3 Hz LOCK\nSR830: 317.3 Hz WARNING")):
                self.a.stfmr_lockin_voltage_var.set(voltage_text)
                self.a.stfmr_lockin_reference_status_var.set(reference_text)
                self.root.update()
                self.assertEqual({w.winfo_rootx() for w in (field, reference, overload)},
                                 {field.winfo_rootx()}, "texts must start at one x")
                self.assertEqual({w.winfo_rootx() + w.winfo_width() for w in (voltage, reference, overload)},
                                 {voltage.winfo_rootx() + voltage.winfo_width()},
                                 "boxes must end at one x")
                dots = []
                for (canvas, item), text in ((self.a.stfmr_lockin_reference_indicator, reference),
                                             (self.a.stfmr_lockin_overload_indicator, overload)):
                    x0, y0, x1, y1 = canvas.coords(item)
                    dots.append(canvas.winfo_rootx() + (x0 + x1) / 2)
                    self.assertLess(canvas.winfo_rootx() + x1, text.winfo_rootx(), "dot left of its text")
                    # Level with the first line of text, even when there are two.
                    self.assertLessEqual(
                        abs(canvas.winfo_rooty() + (y0 + y1) / 2 - (text.winfo_rooty() + single_line / 2)), 1)
                self.assertEqual(dots[0], dots[1], "both dots in one column")
        finally:
            self.a.stfmr_lockin_voltage_var.set("-- V")
            self.a.stfmr_lockin_reference_status_var.set("Ref: --")

    def test_kepco_readouts_line_up_with_the_controls_above(self):
        """Mode, I, V and OUTP each fill the control group above them."""
        pages = (
            ("FMR", self.a.stfmr_tab, self.a.stfmr_device_resource_widgets,
             (self.a.stfmr_kepco_mode_var, self.a.stfmr_kepco_current_var,
              self.a.stfmr_kepco_voltage_var, self.a.stfmr_kepco_output_var)),
            ("STFMR", self.a.stfmr_lockin_tab, self.a.stfmr_lockin_resource_widgets,
             (self.a.stfmr_lockin_kepco_mode_var, self.a.stfmr_lockin_kepco_current_var,
              self.a.stfmr_lockin_kepco_voltage_var, self.a.stfmr_lockin_kepco_output_var)),
        )
        try:
            for page, tab, widgets, variables in pages:
                with self.subTest(page=page):
                    self.arranged(LAB_WIDE, page, widgets)
                    panel = self.page_frames(tab)["KEPCO 4886 BOP 20-10 field output"]
                    for index, variable in enumerate(variables):
                        readout = self.label_showing(variable)
                        self.assertIs(readout.master, panel)
                        first = panel.grid_slaves(row=1, column=2 * index)[0]
                        second = panel.grid_slaves(row=1, column=2 * index + 1)[0]
                        self.assertLessEqual(abs(readout.winfo_rootx() - first.winfo_rootx()), 1,
                                             f"{variable.get()} starts under {first}")
                        self.assertLessEqual(
                            abs(readout.winfo_rootx() + readout.winfo_width()
                                - second.winfo_rootx() - second.winfo_width()), 1,
                            f"{variable.get()} ends under {second}")
        finally:
            self.arranged(LAB_WIDE, "STFMR", self.a.stfmr_lockin_resource_widgets)

    def test_fmr_gaussmeter_status_sits_beside_its_controls(self):
        """It sat alone at the far right, and readings shifted the buttons."""
        self.arranged(LAB_WIDE, "FMR", self.a.stfmr_device_resource_widgets)
        status = self.label_showing(self.a.stfmr_gauss_status_var)
        check = next(w for w in status.master.winfo_children() if w.winfo_class() == "TCheckbutton")
        gap = status.winfo_rootx() - check.winfo_rootx() - check.winfo_width()
        self.assertTrue(0 <= gap <= 30, f"status is {gap} px from the checkbox")
        field = self.label_showing(self.a.stfmr_gauss_field_var)
        read = self.a.stfmr_read_gauss_button
        before = read.winfo_rootx()
        font = tkinter.font.Font(font=field.cget("font") or "TkDefaultFont")
        try:
            for text in ("-1234.5678 Oe", "+272.03412 G"):
                self.a.stfmr_gauss_field_var.set(text)
                self.root.update()
                self.assertEqual(read.winfo_rootx(), before, "a reading must not shift the buttons")
                self.assertLessEqual(font.measure(text), field.winfo_width(), f"{text} is cut")
        finally:
            self.a.stfmr_gauss_field_var.set("-- G")
            self.arranged(LAB_WIDE, "STFMR", self.a.stfmr_lockin_resource_widgets)

    def test_side_by_side_only_when_the_table_gets_its_full_width(self):
        """The decision must allow for the parameter box's padding and border."""
        table = self.a.stfmr_lockin_meter_combo.master
        decided = set()
        try:
            for width in range(self.NARROWEST, LAB_WIDE + 1, 40):
                self.arranged(width, "STFMR", self.a.stfmr_lockin_resource_widgets)
                side = getattr(table.master, "_pair_side_by_side", None)
                self.assertIsNotNone(side, f"no layout decision at {width} px")
                decided.add(side)
                if side:
                    self.assertGreaterEqual(table.winfo_width(), table.winfo_reqwidth(),
                                            f"side by side at {width} px but the table is squeezed")
        finally:
            self.arranged(LAB_WIDE, "STFMR", self.a.stfmr_lockin_resource_widgets)
        self.assertEqual(decided, {True, False}, "both layouts should occur across this range")

    def test_narrow_window_keeps_dropdowns_whole_and_fields_readable(self):
        font = tkinter.font.nametofont("TkTextFont")
        try:
            self.arranged(self.NARROWEST, "STFMR", self.a.stfmr_lockin_resource_widgets)
            combo = self.a.stfmr_lockin_meter_combo
            self.assertGreaterEqual(combo.winfo_width(), combo.winfo_reqwidth(), "MFLI + SR830 was cut")
            # The SR830 TC dropdown (2026-10-13) shares a field column, which
            # gives way only down to the dropdown's own width.
            choice = self.a.stfmr_lockin_sr830_time_constant_combo
            self.assertGreaterEqual(choice.winfo_width(), choice.winfo_reqwidth(), "the SR830 TC was cut")
            table = combo.master
            for column in (5, 7, 9, 11):
                for field in table.grid_slaves(column=column):
                    if field.winfo_class() in ("TEntry", "TSpinbox"):
                        self.assertGreaterEqual(field.winfo_width(), font.measure("0" * 6))
        finally:
            self.arranged(LAB_WIDE, "STFMR", self.a.stfmr_lockin_resource_widgets)

    def test_sr830_time_constant_sits_beside_its_ref_in_and_shows_every_choice(self):
        """User, 2026-10-14: the SR830 row reads Meter, SR830 REF IN, SR830
        TC, SR830 autorange (since 2026-10-13 the TC sat under Loop).  The
        dropdown fills its column, as wide as f step, P step, Step A and
        Settle s above it, and "300 ms" is not cut.  The SR830 row is row 4
        since the power row (user, 2026-10-14), and row 5 since the angle row
        (user, 2026-10-17), which puts Angle step ° above it too."""
        font = tkinter.font.nametofont("TkTextFont")
        choice = self.a.stfmr_lockin_sr830_time_constant_combo
        table = self.a.stfmr_lockin_meter_combo.master
        above = [table.grid_slaves(row=row, column=5)[0] for row in (0, 1, 2, 3, 4)]
        try:
            for width in (self.LAB_DEFAULT, LAB_WIDE):
                with self.subTest(width=width):
                    self.arranged(width, "STFMR", self.a.stfmr_lockin_resource_widgets)
                    self.assertTrue(choice.winfo_ismapped())
                    self.assertEqual((int(choice.grid_info()["row"]), int(choice.grid_info()["column"])), (5, 5))
                    (label,) = table.grid_slaves(row=5, column=4)
                    self.assertEqual(str(label.cget("text")), "SR830 TC:")
                    self.assertIs(above[1], self.a.stfmr_lockin_power_step_entry)
                    self.assertIs(above[3], self.a.stfmr_lockin_settle_entry)
                    self.assertIs(above[4], self.a.stfmr_lockin_angle_step_entry)
                    for field in above:
                        self.assertEqual((choice.winfo_x(), choice.winfo_width()),
                                         (field.winfo_x(), field.winfo_width()))
                    # The text area: ttk sizes it as width x the width of "0";
                    # the rest of the requested width is arrow, border, padding.
                    room = choice.winfo_width() - (
                        choice.winfo_reqwidth() - int(choice.cget("width")) * font.measure("0"))
                    for value in choice.cget("values"):
                        self.assertLessEqual(font.measure(str(value)), room, f"{value} is cut")
        finally:
            self.arranged(LAB_WIDE, "STFMR", self.a.stfmr_lockin_resource_widgets)

    def test_the_table_is_in_measurement_order_and_fits(self):
        """User, 2026-10-14: "re-arrange the GUI buttons and windows in the
        ST-FMR sweep param module and make them in order".  One row per
        stage, at most five pairs (a sixth made the table too wide for the
        1101 px window the program opens with), every box and label whole in
        that window and in the 2000 px one.  Settle s (1) and Acquire s (2)
        replace Wait/pt.  P start / P stop / P step (12, 12, 1) replace
        Power dBm in their own row under the f row, so the table has six
        rows (user, 2026-10-14)."""
        field_font = tkinter.font.nametofont("TkTextFont")
        label_font = tkinter.font.nametofont("TkDefaultFont")
        table = self.a.stfmr_lockin_meter_combo.master
        rows = {
            0: ("f start GHz:", "f stop GHz:", "f step GHz:"),
            1: ("P start dBm:", "P stop dBm:", "P step dB:"),     # dB: review, 2026-10-14
            2: ("Start A:", "Stop A:", "Step A:", "Loop:", "Comp. V:"),
            3: ("Mod. start Hz:", "Mod. end Hz:", "Settle s:", "Acquire s:"),
            # The stage angles, below the Mod. row (user, 2026-10-17).
            4: ("Angle start °:", "Angle stop °:", "Angle step °:"),
            5: ("Meter:", "SR830 REF IN:", "SR830 TC:"),
            6: ("MFLI reference:", "MFLI TC s:"),
        }
        ticks = {5: (6, "SR830 autorange"), 6: (4, "MFLI autorange")}
        self.assertEqual((self.a.stfmr_lockin_settle_var.get(), self.a.stfmr_lockin_acquire_var.get()),
                         ("1", "2"))
        self.assertEqual((self.a.stfmr_lockin_mod_start_var.get(), self.a.stfmr_lockin_mod_end_var.get()),
                         ("317.3", "317.3"))   # user, 2026-10-17
        self.assertEqual((self.a.stfmr_lockin_power_start_var.get(), self.a.stfmr_lockin_power_stop_var.get(),
                          self.a.stfmr_lockin_power_step_var.get()), ("12", "12", "3"))   # user, 2026-10-17
        self.assertEqual((self.a.stfmr_lockin_start_var.get(), self.a.stfmr_lockin_stop_var.get(),
                          self.a.stfmr_lockin_step_var.get()), ("-6", "6", "0.02"))
        self.assertEqual((self.a.stfmr_lockin_frequency_start_var.get(), self.a.stfmr_lockin_frequency_stop_var.get(),
                          self.a.stfmr_lockin_frequency_step_var.get()), ("3", "9", "1"))
        self.assertEqual((self.a.stfmr_lockin_angle_start_var.get(), self.a.stfmr_lockin_angle_stop_var.get(),
                          self.a.stfmr_lockin_angle_step_var.get()), ("0", "0", "10"))   # user, 2026-10-17
        for column, entry in ((1, self.a.stfmr_lockin_angle_start_entry), (3, self.a.stfmr_lockin_angle_stop_entry),
                              (5, self.a.stfmr_lockin_angle_step_entry)):
            self.assertIs(table.grid_slaves(row=4, column=column)[0], entry)
        self.assertIs(table.grid_slaves(row=1, column=1)[0], self.a.stfmr_lockin_power_start_entry)
        self.assertIs(table.grid_slaves(row=1, column=3)[0], self.a.stfmr_lockin_power_stop_entry)
        self.assertIs(table.grid_slaves(row=1, column=5)[0], self.a.stfmr_lockin_power_step_entry)
        self.assertIs(table.grid_slaves(row=3, column=5)[0], self.a.stfmr_lockin_settle_entry)
        self.assertIs(table.grid_slaves(row=3, column=7)[0], self.a.stfmr_lockin_acquire_entry)
        # Kept so a sweep can lock it (review, 2026-10-14).
        self.assertIs(table.grid_slaves(row=6, column=3)[0], self.a.stfmr_lockin_mfli_timeconstant_entry)
        self.assertEqual(table.grid_slaves(row=7), [], "seven rows")
        # And at the lab's 2000 px window, the plots' heights for the record.
        self.arranged(LAB_WIDE, "STFMR", self.a.stfmr_lockin_resource_widgets)
        print(f"STFMR plots at {self.root.winfo_width()}x{self.root.winfo_height()} px: "
              f"{sorted({c.winfo_height() for c in (self.a.stfmr_lockin_x_canvas, self.a.stfmr_lockin_y_canvas, self.a.stfmr_lockin_theta_canvas, self.a.stfmr_lockin_canvas)})} px tall")
        try:
            for width in (self.LAB_DEFAULT, LAB_WIDE):
                self.arranged(width, "STFMR", self.a.stfmr_lockin_resource_widgets)
                tab = self.a.stfmr_lockin_tab
                right = tab.winfo_rootx() + tab.winfo_width()
                print(f"STFMR table at {self.root.winfo_width()} px: {table.winfo_width()} px wide "
                      f"(asks {table.winfo_reqwidth()}), its right edge {table.winfo_rootx() + table.winfo_width()}"
                      f" px, the page's {right} px")
                for row, labels in rows.items():
                    with self.subTest(width=width, row=row):
                        cells = []
                        for pair, text in enumerate(labels):
                            (label,) = table.grid_slaves(row=row, column=2 * pair)
                            (field,) = table.grid_slaves(row=row, column=2 * pair + 1)
                            self.assertEqual(str(label.cget("text")), text)
                            self.assertGreaterEqual(label.winfo_width(), label_font.measure(text), f"{text} is cut")
                            if field.winfo_class() in ("TEntry", "TSpinbox"):
                                self.assertGreaterEqual(field.winfo_width(), field_font.measure("0" * 6))
                            cells += [label, field]
                        if row in ticks:
                            column, text = ticks[row]
                            (tick,) = table.grid_slaves(row=row, column=column)
                            self.assertEqual(str(tick.cget("text")), text)
                            cells.append(tick)
                        for widget in cells:
                            self.assertTrue(widget.winfo_ismapped())
                            self.assertLessEqual(widget.winfo_rootx() + widget.winfo_width(), right + 1,
                                                 "past the right edge")
                            # The table frame hides whatever runs past its
                            # own edge, 17 px inside the tab's (review,
                            # 2026-10-13).
                            self.assertLessEqual(widget.winfo_rootx() + widget.winfo_width(),
                                                 table.winfo_rootx() + table.winfo_width() + 1,
                                                 "cut by the parameter table's edge")
                        self.assertEqual(table.grid_slaves(row=row, column=10), [], "no sixth pair")
                        # Left to right in the order listed.
                        xs = [w.winfo_rootx() for w in cells]
                        self.assertEqual(xs, sorted(xs))
        finally:
            self.arranged(LAB_WIDE, "STFMR", self.a.stfmr_lockin_resource_widgets)

    def test_the_actions_follow_the_table_in_order_in_the_default_window(self):
        """In the 1101 px window: the table, then Start, Stop / zero and Save
        CSV in one row, then Read LS475 with Continuous field read, then the
        readouts (user, 2026-10-14)."""
        table = self.a.stfmr_lockin_meter_combo.master
        column = self.a.stfmr_lockin_start_button.master.master
        try:
            self.arranged(self.LAB_DEFAULT, "STFMR", self.a.stfmr_lockin_resource_widgets)
            self.assertFalse(getattr(table.master, "_pair_side_by_side", True), "stacked at 1101 px")
            self.assertGreaterEqual(column.winfo_rooty(), table.winfo_rooty() + table.winfo_height())
            start, stop, save = (self.a.stfmr_lockin_start_button, self.a.stfmr_lockin_stop_button,
                                 self.a.stfmr_lockin_save_button)
            self.assertEqual(len({b.winfo_rooty() for b in (start, stop, save)}), 1, "one row")
            self.assertLess(start.winfo_rootx(), stop.winfo_rootx())
            self.assertLess(stop.winfo_rootx(), save.winfo_rootx())
            read = self.a.stfmr_lockin_read_gauss_button
            continuous = next(w for w in read.master.winfo_children() if w.winfo_class() == "TCheckbutton")
            self.assertIn("Continuous field read", str(continuous.cget("text")))
            # One row: the tick's middle is level with the button.
            middle = continuous.winfo_rooty() + continuous.winfo_height() / 2
            self.assertTrue(read.winfo_rooty() <= middle <= read.winfo_rooty() + read.winfo_height())
            self.assertLess(read.winfo_rootx(), continuous.winfo_rootx())
            self.assertGreaterEqual(read.winfo_rooty(), start.winfo_rooty() + start.winfo_height())
            field = self.label_showing(self.a.stfmr_lockin_field_var)
            self.assertGreaterEqual(field.winfo_rooty(), read.winfo_rooty() + read.winfo_height())
        finally:
            self.arranged(LAB_WIDE, "STFMR", self.a.stfmr_lockin_resource_widgets)

    def test_the_plots_are_drawn_inside_their_canvases_in_the_default_window(self):
        """The fifth table row (2026-10-14) takes about 40 px from the
        plots, and the sixth, the power row (user, 2026-10-14), as much
        again (about 19 px from each plot, as they are two rows of two).
        Drawn against a 120 px minimum, the plots' x-axis titles and tick
        labels were cut in any window under about 1326 px tall, such as the
        CI screenshots' 1101 x 1300 px, where each canvas is about 105 px.
        They are now drawn compact, at their real height, as the PPMS and
        RT-E plots are (review, 2026-10-14).  In the 1101 px window, at the
        lab screen's full height and at 1300 px, every text sits inside its
        canvas, the x-axis title 10 px above its bottom, and each canvas
        keeps at least the compact minimum of 55 px (at 1300 px checked on
        Windows only, where the README's estimate comes from: other fonts
        make the table taller)."""
        a = self.a
        canvases = {"X": a.stfmr_lockin_x_canvas, "Y": a.stfmr_lockin_y_canvas,
                    "theta": a.stfmr_lockin_theta_canvas, "field vs current": a.stfmr_lockin_canvas}
        titles = {"X": "Magnetic field (G)", "Y": "Magnetic field (G)", "theta": "Magnetic field (G)",
                  "field vs current": "4886 set current (A)"}
        # One carrier at 12 dBm, MFLI, five currents through 0 A: a legend
        # entry ("4 GHz") and a "Field at 0 A" value to place as well.
        rows = [{"meter": "MFLI", "loop": 1, "microwave_frequency_ghz": 4.0, "microwave_power_dbm": 12.0,
                 "modulation_frequency_hz": 117.3, "field_g": 10.0 + 300.0 * current, "current_a": current,
                 "x_v": 1e-6 * current, "y_v": 2e-6 - 1e-6 * current, "phase_deg": 10.0 + current}
                for current in (-1.0, -0.5, 0.0, 0.5, 1.0)]
        saved = (a.stfmr_lockin_live_rows, a.stfmr_lockin_rows, a.stfmr_lockin_meter_var.get(),
                 a.stfmr_lockin_selected_meter)
        on_windows = self.root.tk.call("tk", "windowingsystem") == "win32"
        try:
            a.stfmr_lockin_meter_var.set("MFLI")
            a.stfmr_lockin_selected_meter = "MFLI"
            a.stfmr_lockin_live_rows, a.stfmr_lockin_rows = list(rows), list(rows)
            for height in (self.LAB_HEIGHT, CI_SHOT_HEIGHT):
                self.arranged(self.LAB_DEFAULT, "STFMR", a.stfmr_lockin_resource_widgets)
                self.root.geometry(f"{self.LAB_DEFAULT}x{height}+0+0")
                for _ in range(3):
                    self.root.update()
                a._redraw_stfmr_lockin_live_plots()
                a._redraw_stfmr_lockin_plot()
                # The layout numbers, for the record (user, 2026-10-17: the
                # Motor row and the angle row cost the plots height).
                print(f"STFMR plots at {self.root.winfo_width()}x{self.root.winfo_height()} px: "
                      f"{sorted({c.winfo_height() for c in canvases.values()})} px tall")
                for name, canvas in canvases.items():
                    with self.subTest(height=height, plot=name):
                        self.assertTrue(canvas.winfo_ismapped())
                        self.assertIs(canvas.ppms_compact_plot, True)
                        canvas_height = canvas.winfo_height()
                        if height == self.LAB_HEIGHT or on_windows:
                            self.assertGreaterEqual(canvas_height, 55, "Increase the window height")
                        texts = [i for i in canvas.find_all() if canvas.type(i) == "text"]
                        self.assertTrue(texts)
                        for item in texts:
                            y = canvas.coords(item)[1]
                            self.assertTrue(0 <= y < canvas_height, f"{canvas.itemcget(item, 'text')!r} at {y}")
                        if canvas_height < 55:
                            continue
                        (title,) = [i for i in texts if canvas.itemcget(i, "text") == titles[name]]
                        self.assertEqual(canvas.coords(title)[1], canvas_height - 10)
                        # Every line of horizontal text (the x-axis title,
                        # the tick labels, the legend, "Field at 0 A") lies
                        # inside the canvas, give or take a pixel or two of
                        # line spacing.  The rotated y-axis title is centred
                        # on the plot and only its anchor is checked above:
                        # "LS475 measured field (G)" is longer than a short
                        # plot is tall, in either drawing.
                        for item in texts:
                            if float(canvas.itemcget(item, "angle") or 0.0):
                                continue
                            top, bottom = canvas.bbox(item)[1::2]
                            self.assertGreaterEqual(top, -2, canvas.itemcget(item, "text"))
                            self.assertLessEqual(bottom, canvas_height + 2, canvas.itemcget(item, "text"))
                        self.assertIn("4 GHz", [canvas.itemcget(i, "text") for i in texts])
        finally:
            (a.stfmr_lockin_live_rows, a.stfmr_lockin_rows, meter, a.stfmr_lockin_selected_meter) = saved
            a.stfmr_lockin_meter_var.set(meter)
            self.arranged(LAB_WIDE, "STFMR", a.stfmr_lockin_resource_widgets)
            a._redraw_stfmr_lockin_live_plots()
            a._redraw_stfmr_lockin_plot()

    def test_fmr_plots_fill_the_page_down_to_the_status_line(self):
        self.arranged(LAB_WIDE, "FMR", self.a.stfmr_device_resource_widgets)
        tab = self.a.stfmr_tab
        status = self.label_showing(self.a.stfmr_status_var)
        blank = (tab.winfo_rooty() + tab.winfo_height()) - (status.winfo_rooty() + status.winfo_height())
        self.assertLess(blank, 30, f"{blank} px left empty below the status line")
        canvas = self.a.stfmr_fmr_canvas
        self.assertGreater(canvas.winfo_height(), canvas.winfo_reqheight(), "plots should take the spare height")

    # ------------------------------------------------------------ CIDWM
    def test_cidwm_sections_pair_up_and_the_plot_takes_the_height(self):
        """2026-10-12: MOKE-6221 is CIDWM: the pulse output beside the
        connection, the pulse sign scan, then MOKE magnet's difference
        display beside Save images, and the display rows."""
        self.arranged(LAB_WIDE, "CIDWM", self.a.stfmr_lockin_resource_widgets)
        frames = self.page_frames(self.a.k6221_tab)
        connection = frames["Keithley 6221 / VISA"]
        pulse = frames["DC unipolar pulse output (0 A ↔ ±I, 50% duty)"]
        difference = frames["MOKE difference display"]
        images = frames["Images"]
        for gone in ("AC/DC continuous output and live readback",
                     "Pulse current scan and ROI acquisition", "ROI editing", "MOKE ROI editing",
                     "ROI mean intensity vs time (live)",
                     "Pulse current vs ROI mean intensity (current round-trip scan)"):
            self.assertNotIn(gone, frames)
        self.assertIs(connection.master, pulse.master)
        self.assertIs(difference.master, images.master)
        self.assertLess(connection.winfo_rootx(), pulse.winfo_rootx(), "connection on the left")
        self.assertLess(difference.winfo_rootx(), images.winfo_rootx(), "difference display on the left")
        outer = connection.master.master
        self.assertIn("Pulse sign scan (display, raw + processed TIFF after each pulse)", frames)
        self.assertIn("Camera settings", frames)
        # 2026-10-13: the Avtech box between the 6221 rows and the scan.
        avtech = frames["Avtech AVR-E3-B pulse generator (GPIB; 50 Ω load required)"]
        self.assertIs(avtech.master, outer)
        self.assertEqual(int(avtech.grid_info()["row"]), 1)
        self.assertLess(pulse.winfo_rooty(), avtech.winfo_rooty())
        self.assertLess(avtech.winfo_rooty(),
                        frames["Pulse sign scan (display, raw + processed TIFF after each pulse)"].winfo_rooty())
        self.assertEqual(int(outer.rowconfigure(5)["weight"]), 1, "spare height below the controls")

    def test_cidwm_red_stop_button_is_never_clipped(self):
        """At narrow widths it used to extend 62 px past the window edge."""
        try:
            for width in (self.LAB_DEFAULT, 1500, LAB_WIDE):  # the lab's real widths
                self.arranged(width, "CIDWM", self.a.stfmr_lockin_resource_widgets)
                tab = self.a.k6221_tab
                right = tab.winfo_rootx() + tab.winfo_width()
                over = [w for w in descendants(tab)
                        if w.winfo_ismapped() and w.winfo_rootx() + w.winfo_width() > right + 1]
                self.assertEqual(over, [], f"widgets past the edge at {width} px")
                stop = self.a.k6221_off_button
                self.assertTrue(stop.winfo_ismapped())
                self.assertLessEqual(stop.winfo_rootx() + stop.winfo_width(), right)
        finally:
            self.arranged(LAB_WIDE, "STFMR", self.a.stfmr_lockin_resource_widgets)

    # ------------------------------------------------------------ field at 0 A
    def test_field_at_zero_current_is_interpolated_or_taken_exactly(self):
        f = app.CombinedControlApp._fields_at_zero_current
        sweep = [(i / 100.0, 272.0 + 278.0 * i / 100.0) for i in range(-200, 201)]
        self.assertEqual(f(sweep), [272.0])
        skipping = [(-0.15, 230.0), (0.05, 286.0)]
        self.assertAlmostEqual(f(skipping)[0], 272.0)
        trip = [(-1, 0.0), (1, 540.0), (-1, -20.0)]
        self.assertEqual(len(f(trip)), 2, "a round trip crosses 0 A twice")
        self.assertEqual(f([(0.5, 400.0), (2.0, 830.0)]), [], "never reaches 0 A")

    def test_field_at_zero_current_label_sits_above_the_plot(self):
        canvas = self.a.stfmr_lockin_canvas
        sweep = [(i / 20.0, 272.0 + 278.0 * i / 20.0) for i in range(-40, 41)]
        self.a._draw_nonlocal_series(canvas, {"4 GHz": sweep}, "4886 set current (A)",
                                     "LS475 measured field (G)", "wait", markers=True,
                                     marker_radius=1.5, show_legend=True,
                                     series_labels={"4 GHz": "4 GHz"})
        self.a._label_field_at_zero_current(canvas, {"4 GHz": sweep})
        labels = [i for i in canvas.find_all()
                  if canvas.type(i) == "text" and "Field at 0 A" in canvas.itemcget(i, "text")]
        self.assertEqual(len(labels), 1)
        self.assertEqual(canvas.itemcget(labels[0], "text"), "Field at 0 A: +272.0 G")
        box = canvas.bbox(labels[0])
        frame_top = min(canvas.bbox(i)[1] for i in canvas.find_all() if canvas.type(i) == "rectangle")
        self.assertLessEqual(box[3], frame_top, "the label must stay clear of the data")
        self.assertEqual([i for i in canvas.find_overlapping(*box) if i != labels[0]], [])

    def test_disagreeing_sweeps_show_the_range(self):
        canvas = self.a.stfmr_lockin_canvas
        canvas.delete("all")
        series = {"up": [(-1, 0.0), (1, 540.0)], "down": [(1, 530.0), (-1, -20.0)]}
        self.a._label_field_at_zero_current(canvas, series)
        text = [canvas.itemcget(i, "text") for i in canvas.find_all() if canvas.type(i) == "text"]
        self.assertEqual(text, ["Field at 0 A: +255.0 to +270.0 G"])

    def test_tab_visits_connections_before_the_output_panel(self):
        for widgets in (self.a.stfmr_lockin_resource_widgets, self.a.stfmr_device_resource_widgets):
            connections = widgets["kepco"].master.master
            top = connections.master
            order = top.winfo_children()
            panel = next(w for w in order if w is not connections)
            self.assertLess(order.index(connections), order.index(panel))

    def test_zero_field_label_never_lands_on_other_text_in_a_narrow_plot(self):
        """Narrow plot, three disagreeing frequencies: no free spot on the canvas."""
        frame = tk.ttk.LabelFrame(self.root, text="Measured field vs 4886 current (field sweep only)")
        canvas = tk.Canvas(frame, width=387, height=250)
        canvas.pack()
        frame.place(x=0, y=0)
        self.root.update()
        try:
            series = {f"{f} GHz": [(-1, 272.0 + 3 * k - 278.0), (1, 272.0 + 3 * k + 278.0)]
                      for k, f in enumerate((4, 5, 6))}
            self.a._draw_nonlocal_series(canvas, series, "4886 set current (A)",
                                         "LS475 measured field (G)", "wait", markers=True,
                                         show_legend=True, series_labels={k: k for k in series})
            self.a._label_field_at_zero_current(canvas, series)
            on_canvas = [i for i in canvas.find_all() if canvas.type(i) == "text"
                         and "0 A)" in canvas.itemcget(i, "text") or canvas.type(i) == "text"
                         and "Field at 0 A" in canvas.itemcget(i, "text")]
            for item in on_canvas:
                hits = [i for i in canvas.find_overlapping(*canvas.bbox(item)) if i != item]
                self.assertEqual(hits, [], "a label on the canvas overlaps other drawing")
            shown = [canvas.itemcget(i, "text") for i in on_canvas] + [str(frame.cget("text"))]
            self.assertTrue(any("+272.0 to +278.0 G" in text for text in shown),
                            f"the value must still be shown somewhere: {shown}")
            # A later sweep without a 0 A crossing restores the plain title.
            canvas.delete("all")
            self.a._label_field_at_zero_current(canvas, {"4 GHz": [(0.5, 400.0), (2.0, 830.0)]})
            self.assertEqual(str(frame.cget("text")), "Measured field vs 4886 current (field sweep only)")
        finally:
            frame.destroy()

    def test_address_fields_are_narrow_and_status_text_fits(self):
        font = tkinter.font.nametofont("TkDefaultFont")
        for page, widgets, statuses in (
            ("STFMR", self.a.stfmr_lockin_resource_widgets, self.a.stfmr_lockin_connection_vars),
            ("FMR", self.a.stfmr_device_resource_widgets, self.a.stfmr_device_connection_vars),
        ):
            for key, entry in widgets.items():
                with self.subTest(page=page, key=key):
                    if entry.winfo_class() == "TCombobox":
                        # The Zurich chooser: as wide on screen as an address field.
                        self.root.update()
                        self.assertEqual(entry.winfo_width(), widgets["kepco"].winfo_width())
                    else:
                        self.assertEqual(int(entry.cget("width")), 18)
                    self.assertNotIn("e", entry.grid_info()["sticky"], "field must not stretch")
                    # The field may only give way when the row is too narrow;
                    # the tail column takes practically all spare width.
                    self.assertGreaterEqual(int(entry.master.columnconfigure(6)["weight"]),
                                            1000 * int(entry.master.columnconfigure(1)["weight"]))
                    status = next(w for w in entry.master.winfo_children()
                                  if isinstance(w, tk.ttk.Label)
                                  and str(w.cget("textvariable")) == str(statuses[key]))
                    self.assertGreaterEqual(int(status.cget("width")), len("Not connected"))

    # ------------------------------------------------------------ two Zuriches
    def test_mfli_row_offers_both_zurich_lockins_with_the_super_one_marked(self):
        self.arranged(LAB_WIDE, "STFMR", self.a.stfmr_lockin_resource_widgets)
        chooser = self.a.stfmr_lockin_resource_widgets["mfli"]
        self.assertEqual(chooser.winfo_class(), "TCombobox")
        self.assertEqual(str(chooser.cget("state")), "readonly", "chosen, never typed")
        self.assertEqual(tuple(chooser.cget("values")), ("DEV4435 (Normal)", "DEV4414 (Super)"))
        self.assertEqual(chooser.get(), "DEV4435 (Normal)", "the default lock-in is unchanged")
        font = tkinter.font.Font(font=chooser.cget("font") or "TkTextFont")
        # The text area: ttk sizes it as width x the width of "0"; the rest of
        # the requested width is arrow, border and padding.
        room = chooser.winfo_width() - (
            chooser.winfo_reqwidth() - int(chooser.cget("width")) * font.measure("0"))
        for label in chooser.cget("values"):
            self.assertLessEqual(font.measure(label), room, f"{label} is cut")
        # Same column as every address field and Connect button above it.
        widgets = self.a.stfmr_lockin_resource_widgets
        self.assertEqual({(w.winfo_rootx(), w.winfo_width()) for w in widgets.values()},
                         {(widgets["kepco"].winfo_rootx(), widgets["kepco"].winfo_width())})
        self.assertEqual({b.winfo_rootx() for b in self.a.stfmr_lockin_connect_buttons.values()},
                         {self.a.stfmr_lockin_connect_buttons["kepco"].winfo_rootx()})

    def test_each_zurich_choice_tries_the_data_server_that_can_reach_it(self):
        """The bug: choosing DEV4414 still probed only DEV4435's servers."""
        probed = []

        def refuse(address, timeout=None):
            probed.append(tuple(address))
            raise OSError("refused (test)")

        controller = self.a.stfmr_lockin_mfli_controller
        self.addCleanup(self.a.stfmr_lockin_resource_vars["mfli"].set, "DEV4435 (Normal)")
        self.addCleanup(setattr, controller, "mfli_server_candidates", None)
        with mock.patch.object(app.socket, "create_connection", refuse):
            for label, servers in (
                    ("DEV4414 (Super)", [("192.168.68.250", 8004)]),
                    ("DEV4435 (Normal)", [("127.0.0.1", 8004), ("192.168.69.78", 8004)])):
                with self.subTest(choice=label):
                    probed.clear()
                    self.errors.clear()
                    self.a.stfmr_lockin_resource_vars["mfli"].set(label)
                    self.a.connect_stfmr_lockin_device("mfli")
                    for _ in range(500):
                        self.root.update()
                        if self.errors:
                            break
                        time.sleep(0.01)
                    self.assertEqual(probed, servers)
                    self.assertEqual(len(self.errors), 1)
                    message = self.errors[0][1]
                    self.assertIn("192.168.68.250", message, "the Super Zurich's address is named")
                    self.assertIn("Super Zurich", message)
                    self.assertEqual(str(self.a.stfmr_lockin_resource_widgets["mfli"].cget("state")),
                                     "readonly", "after a failure the list is choosable again, not typable")
                    self.assertFalse(controller.mfli_connected)

    def test_bare_device_names_find_their_own_servers(self):
        for text, expected in (("dev4414", ("dev4414", (("192.168.68.250", 8004),))),
                               ("MF-DEV4414", ("dev4414", (("192.168.68.250", 8004),))),
                               ("DEV4414 (Super)", ("dev4414", (("192.168.68.250", 8004),))),
                               ("dev4435", ("dev4435", None)),
                               ("dev9999", ("dev9999", None))):
            with self.subTest(text=text):
                self.assertEqual(app.zurich_mfli_choice(text), expected)
        self.assertEqual(app.zurich_mfli_label("dev4414"), "DEV4414 (Super)")
        self.assertEqual(app.zurich_mfli_label("dev9999"), "dev9999")

    def test_rendered_address_field_is_much_narrower_than_before(self):
        self.root.update()
        entry = self.a.stfmr_lockin_resource_widgets["kepco"]
        self.assertLess(entry.winfo_width(), 260, "was stretched to about 750 px")

    # ------------------------------------------------------------ interlocks
    def test_buttons_disabled_until_the_kepco_is_connected(self):
        self.assertEqual(self.states(), ("disabled",) * 3)

    def test_sweep_disables_setpoints_but_not_zero(self):
        self.connect()
        self.assertEqual(self.states(), ("normal",) * 3)
        self.a.stfmr_lockin_running = True
        self.a._update_stfmr_lockin_ui()
        self.assertEqual(self.states(), ("disabled", "disabled", "normal"))

    def test_apply_refuses_without_the_safety_tick(self):
        card = self.connect()
        self.a.stfmr_lockin_manual_target_var.set("1.5")
        self.a.apply_stfmr_lockin_kepco_output()
        self.assertEqual(card.writes, [])
        self.assertEqual(len(self.warnings), 1)

    def test_apply_is_refused_while_a_sweep_runs(self):
        card = self.connect()
        self.a.stfmr_lockin_manual_safety_var.set(True)
        self.a.stfmr_lockin_running = True
        self.a.apply_stfmr_lockin_kepco_output()
        self.assertEqual(card.writes, [])
        self.assertEqual(len(self.warnings), 1)

    def test_bad_numbers_send_nothing(self):
        card = self.connect()
        self.a.stfmr_lockin_manual_safety_var.set(True)
        for target, limit in (("abc", "5"), ("1", ""), ("nan", "5"), ("11", "5"), ("1", "25")):
            with self.subTest(target=target, limit=limit):
                self.a.stfmr_lockin_manual_target_var.set(target)
                self.a.stfmr_lockin_manual_limit_var.set(limit)
                self.a.apply_stfmr_lockin_kepco_output()
                self.assertEqual(card.writes, [])

    # ------------------------------------------------------------- behaviour
    def test_apply_sends_the_trace_sequence_with_the_entered_values(self):
        card = self.connect()
        self.a.stfmr_lockin_manual_safety_var.set(True)
        self.a.stfmr_lockin_manual_target_var.set("1.5")
        self.a.stfmr_lockin_manual_limit_var.set("5")
        self.a.apply_stfmr_lockin_kepco_output()
        self.assertEqual(card.writes, ["FUNC:MODE CURR", "CURR 0;VOLT 5",
                                       "VOLT:RANG:AUTO 1;CURR:RANG:AUTO 1", "OUTP ON", "CURR 1.500000"])
        self.assertEqual(self.a.stfmr_lockin_kepco_current_var.get(), "I: +1.5 A")
        self.assertEqual(self.a.stfmr_lockin_kepco_output_var.get(), "OUTP: ON")
        self.assertEqual(card.clears, 0)

    def test_voltage_mode_relabels_and_sends_voltage_commands(self):
        card = self.connect()
        self.a.stfmr_lockin_manual_safety_var.set(True)
        self.a.stfmr_lockin_manual_mode_var.set("Voltage")
        self.a._on_stfmr_lockin_manual_mode_changed()
        self.assertEqual(self.a.stfmr_lockin_manual_target_label_var.get(), "Target voltage V:")
        self.assertEqual(self.a.stfmr_lockin_manual_limit_label_var.get(), "Current limit A:")
        self.a.stfmr_lockin_manual_target_var.set("2")
        self.a.stfmr_lockin_manual_limit_var.set("1")
        self.a.apply_stfmr_lockin_kepco_output()
        self.assertEqual(card.writes[0], "FUNC:MODE VOLT")
        self.assertEqual(card.writes[-1], "VOLT 2.000000")
        self.assertEqual(self.a.stfmr_lockin_kepco_mode_var.get(), "Mode: Voltage")

    def test_zero_turns_the_output_off(self):
        card = self.connect()
        self.a.stfmr_lockin_manual_safety_var.set(True)
        self.a.stfmr_lockin_manual_target_var.set("1.5")
        self.a.apply_stfmr_lockin_kepco_output()
        self.a.zero_stfmr_lockin_kepco_output()
        self.assertEqual(card.writes[-2:], ["CURR 0.000000", "OUTP OFF"])
        self.assertEqual(self.a.stfmr_lockin_kepco_output_var.get(), "OUTP: OFF")

    def test_zero_during_a_sweep_stops_it_and_zeroes_at_once(self):
        card = self.connect()
        self.a.stfmr_lockin_manual_safety_var.set(True)
        self.a.stfmr_lockin_manual_target_var.set("2")
        self.a.apply_stfmr_lockin_kepco_output()
        # A sweep with a worker transfer in flight (no pending timer).
        self.a.stfmr_lockin_running = True
        self.a.stfmr_lockin_after_id = None
        self.a.zero_stfmr_lockin_kepco_output()
        self.assertTrue(self.a.stfmr_lockin_stop_event.is_set(), "the sweep must be told to stop")
        self.assertEqual(card.writes[-2:], ["CURR 0.000000", "OUTP OFF"])
        self.assertFalse(self.kepco.output_owned)

    def test_stalled_card_is_named_and_the_output_switched_off(self):
        card = self.connect(FakeBit4886(stall={"CURR 1.500000"}))
        self.a.stfmr_lockin_manual_safety_var.set(True)
        self.a.stfmr_lockin_manual_target_var.set("1.5")
        self.a.apply_stfmr_lockin_kepco_output()
        self.assertEqual(len(self.errors), 1)
        message = str(self.errors[0][1])
        self.assertIn("KEPCO 4886", message)
        self.assertIn("applying the manual output", message)
        self.assertEqual(card.writes[-2:], ["CURR 0.000000", "OUTP OFF"])
        self.assertEqual(self.a.stfmr_lockin_kepco_output_var.get(), "OUTP: OFF")
        self.assertEqual(card.clears, 0, "the card is never device-cleared")

    def test_zero_acts_even_on_an_output_this_page_did_not_switch_on(self):
        """Front panel, LabVIEW, another page or a crashed session left it on."""
        card = self.connect()
        self.assertFalse(self.kepco.output_owned)
        self.a.zero_stfmr_lockin_kepco_output()
        self.assertEqual(card.writes, ["CURR 0.000000", "VOLT 0.000000", "OUTP OFF"],
                         "mode unknown: zero both setpoints, then switch off")
        self.assertEqual(self.a.stfmr_lockin_kepco_output_var.get(), "OUTP: OFF")

    def test_fmr_zero_also_acts_regardless_of_ownership(self):
        fmr = self.a.stfmr_controllers["kepco"]
        card = FakeBit4886()
        fmr.instrument, fmr.last_command, fmr.output_owned, fmr.active_mode = card, "", False, ""
        try:
            self.a.zero_stfmr_kepco_output()
            self.assertEqual(card.writes[-1], "OUTP OFF")
        finally:
            fmr.instrument = None

    def test_zero_never_blocks_the_window_while_the_worker_holds_the_supply(self):
        import threading, time
        card = self.connect()
        self.a.stfmr_lockin_manual_safety_var.set(True)
        self.a.stfmr_lockin_manual_target_var.set("2")
        self.a.apply_stfmr_lockin_kepco_output()
        writes_before = len(card.writes)
        held, release = threading.Event(), threading.Event()

        def worker():
            with self.kepco.lock:      # the sweep worker mid set_output
                held.set()
                release.wait(5)

        thread = threading.Thread(target=worker); thread.start(); held.wait(2)
        try:
            self.a.stfmr_lockin_running = True
            self.a.stfmr_lockin_after_id = None
            started = time.monotonic()
            self.a.zero_stfmr_lockin_kepco_output()
            self.assertLess(time.monotonic() - started, 0.5, "the Tk thread must not wait")
            self.assertEqual(len(card.writes), writes_before, "nothing sent while busy")
            self.assertTrue(self.a.stfmr_lockin_stop_event.is_set())
            self.assertIn("will be zeroed", self.a.stfmr_lockin_status_var.get())
        finally:
            release.set(); thread.join(2)
        deadline = time.monotonic() + 2       # the deferred retry fires once the lock is free
        while time.monotonic() < deadline and card.writes[-1:] != ["OUTP OFF"]:
            self.root.update(); time.sleep(0.02)
        self.assertEqual(card.writes[-2:], ["CURR 0.000000", "OUTP OFF"])

    def test_deferred_zero_is_not_sent_twice_after_the_sweep_ended(self):
        card = self.connect()
        self.a.stfmr_lockin_running = False
        self.a.zero_stfmr_lockin_kepco_output(deferred=True)
        self.assertEqual(card.writes, [], "the sweep's own zero/OFF already ran")

    def test_stall_on_outp_on_still_switches_the_output_off(self):
        card = self.connect(FakeBit4886(stall={"OUTP ON"}))
        self.a.stfmr_lockin_manual_safety_var.set(True)
        self.a.stfmr_lockin_manual_target_var.set("1")
        self.a.apply_stfmr_lockin_kepco_output()
        self.assertEqual(card.writes[-2:], ["CURR 0.000000", "OUTP OFF"])
        self.assertEqual(card.attempts.count("OUTP ON"), 1, "never resent")

    def test_display_is_unknown_until_something_is_sent(self):
        self.connect()
        self.assertEqual(self.a.stfmr_lockin_kepco_output_var.get(), "OUTP: --")
        self.assertEqual(self.a.stfmr_lockin_kepco_current_var.get(), "I: -- A")

    def test_failed_disconnect_is_not_shown_as_off(self):
        card = self.connect()
        self.a.stfmr_lockin_manual_safety_var.set(True)
        self.a.stfmr_lockin_manual_target_var.set("1")
        self.a.apply_stfmr_lockin_kepco_output()
        card.stall = {"CURR 0.000000", "OUTP OFF"}
        self.a.disconnect_stfmr_lockin_device("kepco")
        self.assertFalse(self.kepco.connected)
        self.assertEqual(self.a.stfmr_lockin_kepco_output_var.get(), "OUTP: NOT CONFIRMED OFF")

    def test_display_shows_ascii_colons(self):
        self.connect()
        for var in (self.a.stfmr_lockin_kepco_mode_var, self.a.stfmr_lockin_kepco_current_var,
                    self.a.stfmr_lockin_kepco_voltage_var, self.a.stfmr_lockin_kepco_output_var):
            self.assertNotIn("：", var.get())


if __name__ == "__main__":
    unittest.main()
