"""The window finishes drawing: no side-by-side pair flips forever.

2026-10-12: on the CIDWM page the difference display's mode row (Off / Plain
difference / Drift tracking beside Recapture BG / 100x marker) flipped
between side by side and stacked at every <Configure>, because the spare
width went to the Images box and the display's width followed its own row.
root.update() never returned and the program hung at start-up on the lab
PCs.  Each class builds the whole application as the lab PC shows it.
"""
import tkinter as tk
import tkinter.font
import unittest

from lab_screen import LAB_WIDE, build_lab_app, lab_window
from test_ppms_motor_debug import app

WIDTHS = (LAB_WIDE, 1101, 826, 1574, 2560)  # gui_checks.py's, and maximized at the lab
CI_SHOT_HEIGHT = 1300  # gui_checks.py's window height, the CI screenshots'


def descendants(widget):
    for child in widget.winfo_children():
        yield child
        yield from descendants(child)


def cidwm_mode_row(built):
    box = next(w for w in descendants(built.k6221_tab)
               if isinstance(w, tk.ttk.LabelFrame) and str(w.cget("text")) == "MOKE difference display")
    return next(w for w in box.winfo_children() if hasattr(w, "_pair_side_by_side"))


def select_page(built, text):
    notebook = next(w for w in built.__dict__.values() if isinstance(w, tk.ttk.Notebook))
    for index in range(notebook.index("end")):
        if notebook.tab(index, "text") == text:
            notebook.select(index)


def rte_stage_parts(built):
    """RT-E's settings box since 2026-10-17 (user: "now move the stage
    controllers to the RT-E settings"): (the params/lock-ins pair's frame,
    the params, the lock-ins, the stage block)."""
    panel = built.rte_panel
    block = panel.rte_stage_stop_button.master
    lockins = panel.ppms_settings_panel
    body = block.master
    params = next(w for w in body.winfo_children() if w is not block and w is not lockins.master)
    return body, params, lockins, block


class Built:
    @classmethod
    def build(cls):
        try:
            cls.root = tk.Tk()
        except tk.TclError as error:  # headless build server
            raise unittest.SkipTest(f"no display: {error}")
        cls.a = build_lab_app(app, cls.root)
        cls.height = lab_window(cls.a)[1]

    @classmethod
    def tearDownClass(cls):
        cls.a.closing = True
        cls.a.safe_close_hardware()
        cls.root.destroy()

    def show(self, width):
        self.root.geometry(f"{width}x{self.height}+0+0")
        self.root.update()


class EveryPageSettlesTests(Built, unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.build()

    def test_no_pair_needs_the_loop_guard(self):
        notebook = next(w for w in self.a.__dict__.values() if isinstance(w, tk.ttk.Notebook))
        sub_pages = self.a.nonlocal_measurements  # 2400-IV, 6221-IV, Gate-VI, |R|
        for width in WIDTHS:
            for index in range(notebook.index("end")):
                notebook.select(index)
                page = notebook.tab(index, "text")
                for sub_index in range(sub_pages.index("end") if page == "Non-local" else 1):
                    sub_page = ""
                    if page == "Non-local":
                        sub_pages.select(sub_index)
                        sub_page = sub_pages.tab(sub_index, "text")
                    self.show(width)
                    held = [str(w) for w in descendants(self.root)
                            if getattr(w, "_pair_flip_guard", {}).get("held")]
                    with self.subTest(width=width, page=page, sub_page=sub_page):
                        self.assertEqual(held, [], "a pair flipped at every <Configure>")

    def test_cidwm_difference_display_takes_the_spare_width(self):
        # Since 2026-10-20 (user: "use two columns for these two part") the
        # difference display is the right-hand box of a pair with the pulse
        # sign scan, which keeps its natural width (column 0, weight 0); the
        # display takes the spare width (column 1, weight 1), so its width
        # never follows its own mode row.  It was the left-hand box beside
        # the Images box (weights 1, 0) until then.
        select_page(self.a, "CIDWM")
        self.show(LAB_WIDE)
        row = cidwm_mode_row(self.a)
        display = row.master
        pair = display.master
        self.assertEqual((int(pair.columnconfigure(0)["weight"]), int(pair.columnconfigure(1)["weight"])),
                         (0, 1))
        self.assertTrue(pair._pair_side_by_side, "the scan and the display side by side at 2000 px")
        self.assertEqual(int(display.grid_info()["column"]), 1)
        self.assertTrue(row._pair_side_by_side, "the mode row fits beside its buttons at 2000 px")


class RTEStageBlockTests(Built, unittest.TestCase):
    """User/CI, 2026-10-14: the Newmark stage's controls were a block in the
    RT-E scan box; as a rack row and a row under the settings they had taken
    about 84 px of height in the 1101 px window the program opens with, and
    every plot fell below its 55 px minimum ("Increase the window height").
    User, 2026-10-17: the connection is the rack's Motor row, and the block
    (Stage °, Move stage, Stop stage, Set stage 0°, the live line) is in the
    RT-E settings box: beside the lock-in choices in the 1101 px window (no
    height), beside or under the KEPCO rows at 2000 px (where the scan box,
    two rows shorter without the block, sets the height or nearly)."""

    @classmethod
    def setUpClass(cls):
        cls.build()
        notebook = next(w for w in cls.a.__dict__.values() if isinstance(w, tk.ttk.Notebook))
        for index in range(notebook.index("end")):
            if notebook.tab(index, "text") == "SHV":
                notebook.select(index)
        # Three plot rows, the worst case for height: SHV starts with
        # SR830-1 at 1 and SR830-2 at 2 measured and the Zurich not (user's
        # figure, 2026-10-20), and every plot must still keep its 55 px when
        # the user also measures the Zurich at 1+2+3.  As its Measure tick
        # and its harmonic box do (their command and <<ComboboxSelected>>);
        # the Zurich is not connected, so nothing is sent.
        cls.three_plot_rows(cls.a.rte_panel)
        cls.root.update()

    @staticmethod
    def three_plot_rows(panel):
        panel.ppms_lockin_selection["zurich"].set(True)
        panel.zurich_harmonic.set("1+2+3")
        panel._apply_ppms2_zurich_harmonic()

    def layouts(self):
        """(settings beside the scan box, params beside the lock-ins, where the block is)."""
        body, _params, _lockins, block = rte_stage_parts(self.a)
        controls = body.master.master
        return (controls._pair_side_by_side, body._pair_side_by_side, block._rte_stage_place)

    def numbers(self, label):
        body, params, lockins, block = rte_stage_parts(self.a)
        settings, scan = body.master, self.a.rte_panel.rte_start_button.master.master
        canvases = self.a.rte_panel.ppms_harmonic_canvases
        print(f"SHV {label} {self.root.winfo_width()}x{self.root.winfo_height()} px: layouts {self.layouts()}; "
              f"settings {settings.winfo_width()}x{settings.winfo_height()}, scan box "
              f"{scan.winfo_width()}x{scan.winfo_height()}, params {params.winfo_reqwidth()}x"
              f"{params.winfo_reqheight()}, lock-ins {lockins.winfo_reqwidth()}x{lockins.winfo_reqheight()}, "
              f"stage block {block.winfo_reqwidth()}x{block.winfo_reqheight()}, outer row "
              f"{self.a.rte_panel.rte_outer_place} ({self.a.rte_panel.ppms_simple_mode.get()}); plots "
              f"{sorted({c.winfo_height() for c in canvases.values()})} px tall")

    def test_the_block_is_in_the_settings_whatever_the_history(self):
        expected = {1101: (False, False, "beside lock-ins")}
        fresh = {}
        for history in ((1101,), (LAB_WIDE,), (LAB_WIDE, 1101), (1101, LAB_WIDE),
                        (2560, 1101), (1101, 2560, LAB_WIDE), (826, LAB_WIDE, 1101)):
            for width in history:
                self.show(width)
            final = self.layouts()
            fresh.setdefault(history[-1], final)
            with self.subTest(history=history):
                self.assertEqual(final, fresh[history[-1]], "the same whatever the resize history")
                if history[-1] in expected:
                    self.assertEqual(final, expected[history[-1]])
                else:
                    self.assertEqual(final[:2], (True, True), "side by side at 2000 px")
                    self.assertIn(final[2], ("beside lock-ins", "under params"))
                body = rte_stage_parts(self.a)[0]
                self.assertFalse(getattr(body, "_pair_flip_guard", {}).get("held"))
        self.assertEqual(rte_stage_parts(self.a)[3].master.master.cget("text"), "SHV settings")

    def test_the_block_costs_no_height_and_fits_at_1101_px(self):
        self.root.geometry(f"1101x{CI_SHOT_HEIGHT}+0+0")
        self.root.update()
        self.numbers("1101 px")
        body, _params, lockins, block = rte_stage_parts(self.a)
        self.assertEqual(block.grid_size()[1], 2, "two rows")
        self.assertLessEqual(block.winfo_reqheight(), lockins.winfo_reqheight(),
                             "no taller than the lock-in rows beside it")
        self.assertGreaterEqual(block.winfo_rooty(), lockins.winfo_rooty())
        self.assertLessEqual(block.winfo_rooty() + block.winfo_height(), lockins.winfo_rooty() + lockins.winfo_height())
        self.assertGreater(block.winfo_rootx(), lockins.winfo_rootx() + lockins.winfo_width())
        settings = body.master
        self.assertLessEqual(block.winfo_rootx() + block.winfo_width(), settings.winfo_rootx() + settings.winfo_width())
        # The scan box keeps only the scan rows, its status and notes.
        rows = self.a.rte_panel.rte_start_button.master
        self.assertEqual(rows.master.cget("text"), "SHV scan")

    def test_at_2000_px_the_block_is_beside_or_under_the_kepco_rows(self):
        self.show(LAB_WIDE)
        self.numbers("2000 px")
        body, params, lockins, block = rte_stage_parts(self.a)
        place = block._rte_stage_place
        if place == "under params":
            self.assertGreaterEqual(block.winfo_rooty(), params.winfo_rooty() + params.winfo_height())
            self.assertLess(block.winfo_rootx() + block.winfo_width(), lockins.winfo_rootx() + 1)
        else:
            self.assertEqual(place, "beside lock-ins")
            self.assertGreater(block.winfo_rootx(), lockins.winfo_rootx() + lockins.winfo_width())
        scan = self.a.rte_panel.rte_start_button.master.master
        self.assertGreaterEqual(scan.winfo_width(), scan.winfo_reqwidth(), "the scan box keeps its width")

    def test_a_reading_stays_on_one_line(self):
        self.show(1101)
        panel = self.a.rte_panel
        block = rte_stage_parts(self.a)[3]
        live = next(w for w in block.winfo_children() if w.winfo_class() == "TLabel"
                    and str(w.cget("textvariable")) == str(panel.rte_stage_live))
        try:
            panel.rte_stage_live.set("Stage: not connected")
            self.root.update()
            one_line = live.winfo_height()
            panel.rte_stage_live.set("Stage: -359.999° (10000 steps/°)")
            self.root.update()
            self.assertEqual(live.winfo_height(), one_line)
        finally:
            panel.rte_stage_live.set("Stage: not connected")

    def test_a_long_message_wraps_inside_the_stage_line(self):
        # User/CI, 2026-10-14: a failure or an unconfirmed STOP is longer than
        # the stage line's fixed box.  It wraps rather than being cut (a
        # warning is read whole), so the block keeps its width and no pair
        # changes; beside the four lock-in rows it costs no height.  The
        # plots' 55 px minimum is checked on Windows only: the README's
        # estimate is from the lab PC's fonts (Microsoft YaHei UI).
        self.root.geometry(f"1101x{CI_SHOT_HEIGHT}+0+0")
        self.root.update()
        panel = self.a.rte_panel
        block = rte_stage_parts(self.a)[3]
        live = next(w for w in block.winfo_children() if w.winfo_class() == "TLabel"
                    and str(w.cget("textvariable")) == str(panel.rte_stage_live))
        # The longest text a read failure shows: the error cut to 60 characters.
        failure = "Stage read failed: " + app.short_status_error(app.NewmarkStageError(
            "Newmark NSC-A1: a call from another thread was refused before it reached the DLL "
            "(its USB handles belong to the thread that connected)"), 60)
        on_windows = self.root.tk.call("tk", "windowingsystem") == "win32"
        try:
            panel.rte_stage_live.set("Stage: not connected")
            self.root.update()
            one_line, width, layouts = live.winfo_height(), block.winfo_reqwidth(), self.layouts()
            heights = {key: canvas.winfo_height() for key, canvas in panel.ppms_harmonic_canvases.items()}
            for message in ("Stage: STOP not confirmed: CHECK THE STAGE",
                            "Stage: -359.999° (10000 steps/°), moving, limit input on", failure,
                            # A stop not confirmed before a reconnect (review, 2026-10-18).
                            "Stage: -359.999°; last stop NOT confirmed: press Stop stage"):
                panel.rte_stage_live.set(message)
                self.root.update()
                with self.subTest(message=message):
                    self.assertEqual(block.winfo_reqwidth(), width, "the message wraps: the block keeps its width")
                    self.assertEqual(self.layouts(), layouts)
                    self.assertEqual({key: canvas.winfo_height() for key, canvas
                                      in panel.ppms_harmonic_canvases.items()}, heights, "no height")
                    if on_windows:
                        for key, canvas in panel.ppms_harmonic_canvases.items():
                            self.assertGreaterEqual(canvas.winfo_height(), 55, f"{key}: Increase the window height")
            # 79 characters in a box 32 digits wide cannot fit on one line.
            self.assertGreater(live.winfo_height(), one_line, "the failure wraps, it is not cut")
        finally:
            panel.rte_stage_live.set("Stage: not connected")
            self.root.update()

    def test_set_stage_zero_is_red_and_as_large_as_before(self):
        # User, 2026-10-18: "make the set angle zero button in SHV module also
        # red": a tk.Button in the program's red, filling a frame of exactly
        # the Inline ttk button's size, so the block keeps its layout; grey
        # while locked.
        panel = self.a.rte_panel
        zero = panel.rte_stage_zero_button
        self.assertEqual(zero.winfo_class(), "Button")
        self.assertEqual(str(zero.cget("text")), "Set stage 0°")
        holder = zero.master
        reference = tk.ttk.Button(holder.master, text="Set stage 0°", width=11, style="Inline.TButton")
        try:
            size = (reference.winfo_reqwidth(), reference.winfo_reqheight())
        finally:
            reference.destroy()
        self.assertEqual((holder.winfo_reqwidth(), holder.winfo_reqheight()), size)
        self.assertIs(holder.master, panel.rte_stage_stop_button.master, "in the stage block, where it was")
        self.assertEqual(holder.grid_info()["row"], 1)
        try:
            panel.ppms_environment_busy = False
            panel._update_ppms_control_ui()
            self.root.update()
            self.assertEqual(str(zero.cget("state")), "normal")
            self.assertEqual((str(zero.cget("bg")).lower(), str(zero.cget("fg")), str(zero.cget("activebackground"))),
                             ("#c62828", "white", "#8e0000"))
            panel.ppms_environment_busy = True
            panel._update_ppms_control_ui()
            self.root.update()
            self.assertEqual(str(zero.cget("state")), "disabled", "locked while busy, as before")
            self.assertNotEqual(str(zero.cget("bg")).lower(), "#c62828", "a locked button looks locked")
        finally:
            panel.ppms_environment_busy = False
            panel._update_ppms_control_ui()
            self.root.update()

    def test_every_plot_is_drawn_in_the_1101_px_window(self):
        self.root.geometry(f"1101x{CI_SHOT_HEIGHT}+0+0")
        self.root.update()
        canvases = self.a.rte_panel.ppms_harmonic_canvases
        self.assertEqual(len(canvases), 9, "the Zurich at 1+2+3 (set above): three rows of X, Y, theta")
        self.assertEqual(sorted(self.a.rte_panel._ppms2_measured_orders()), [1, 2, 3])
        # The layout numbers, for the record (user, 2026-10-17).
        print(f"SHV plots at {self.root.winfo_width()}x{self.root.winfo_height()} px: "
              f"{sorted({c.winfo_height() for c in canvases.values()})} px tall")
        for key, canvas in canvases.items():
            with self.subTest(plot=key):
                # _draw_nonlocal_series's compact minimum.
                self.assertGreaterEqual(canvas.winfo_height(), 55, "Increase the window height")
        for width in (LAB_WIDE,):
            # The lab's widened window at the lab screen's height.
            self.root.geometry(f"{width}x{self.height}+0+0")
            self.root.update()
            print(f"SHV plots at {self.root.winfo_width()}x{self.root.winfo_height()} px: "
                  f"{sorted({c.winfo_height() for c in canvases.values()})} px tall")
            for key, canvas in canvases.items():
                with self.subTest(width=width, plot=key):
                    self.assertGreaterEqual(canvas.winfo_height(), 55, "Increase the window height")

    def test_at_the_default_1_plus_2_two_rows_are_drawn_and_the_third_is_hidden(self):
        """The SHV default, SR830-1 at 1 and SR830-2 at 2 measured and the
        Zurich at 1+2 not (user's figures, 2026-10-18 and 2026-10-20): the
        first- and second-harmonic rows keep their 55 px in the 1101 px
        window and at 2000 px, and the third row is hidden."""
        panel = self.a.rte_panel
        canvases = panel.ppms_harmonic_canvases
        try:
            panel.ppms_lockin_selection["zurich"].set(False)
            panel.zurich_harmonic.set(app.RTE_ZURICH_DEFAULT_HARMONICS)
            panel._apply_ppms2_zurich_harmonic()
            self.assertEqual(app.RTE_ZURICH_DEFAULT_HARMONICS, "1+2")
            self.assertEqual({order: sorted(meters) for order, meters in panel._ppms2_measured_orders().items()},
                             {1: ["830_a"], 2: ["830_b"]})
            for width, height in ((1101, CI_SHOT_HEIGHT), (LAB_WIDE, self.height)):
                self.root.geometry(f"{width}x{height}+0+0")
                self.root.update()
                print(f"SHV plots at 1+2, {self.root.winfo_width()}x{self.root.winfo_height()} px: "
                      f"{sorted({c.winfo_height() for k, c in canvases.items() if k[0] != 3})} px tall")
                for key, canvas in canvases.items():
                    with self.subTest(width=width, plot=key):
                        if key[0] == 3:
                            self.assertFalse(canvas.winfo_ismapped(), "the third-harmonic row is hidden")
                        else:
                            self.assertTrue(canvas.winfo_ismapped())
                            self.assertGreaterEqual(canvas.winfo_height(), 55, "Increase the window height")
        finally:
            self.three_plot_rows(panel)
            self.root.update()

    def test_the_outer_row_costs_no_height_in_the_1101_px_window(self):
        """The outer row of Angle+Field (the start-up scan since 2026-10-19,
        user) and Angle+Current: beside the main row in the 1101 px window,
        so the scan box is as tall as an Angle scan's (CI, 2026-10-30: under
        the main rows it took 36 px from the plots on Windows, 43 px each with
        three plot rows); under it at 2000 px, its Start / End / Step under
        the main row's."""
        panel = self.a.rte_panel
        rows = panel.rte_start_button.master
        scan = rows.master
        outer = [widget for widget, _options in panel.rte_outer_widgets]
        font = tkinter.font.nametofont("TkDefaultFont")
        self.assertEqual(panel.ppms_simple_mode.get(), "Angle+Field", "the start-up scan")

        def choose(mode, width, height):
            panel.ppms_simple_mode.set(mode)
            panel._rte_mode_changed()
            self.root.geometry(f"{width}x{height}+0+0")
            for _ in range(3):
                self.root.update()

        def main_entries():
            return sorted((w for w in rows.grid_slaves(row=0) if w.winfo_class() == "TEntry" and w not in outer),
                          key=lambda w: w.winfo_rootx())

        try:
            heights = {}
            for mode in ("Angle", "Angle+Field", "Angle+Current"):
                choose(mode, 1101, CI_SHOT_HEIGHT)
                heights[mode] = scan.winfo_height()
                if mode == "Angle":
                    self.assertFalse(any(w.winfo_ismapped() for w in outer))
                    continue
                # The layout numbers, for the record.
                print(f"SHV {mode} at 1101 px: outer row {panel.rte_outer_place}, {panel.rte_outer_numbers}, "
                      f"columns {panel.rte_scan_rows_columns}, outer widgets "
                      f"{[w.winfo_reqwidth() for w in outer]} px, label {panel.rte_outer_label.get()!r}")
                with self.subTest(mode=mode, width=1101):
                    self.assertEqual(panel.rte_outer_place, "beside")
                    self.assertTrue(all(w.winfo_ismapped() for w in outer))
                    self.assertEqual({int(w.grid_info()["row"]) for w in outer}, {0})
                    entries = main_entries()
                    self.assertEqual(len(entries), 3, "Start, End and Step")
                    self.assertGreater(outer[0].winfo_rootx(), entries[-1].winfo_rootx() + entries[-1].winfo_width())
                    xs = [w.winfo_rootx() for w in outer]
                    self.assertEqual(xs, sorted(xs), "the label, then Start / End / Step")
                    right = scan.winfo_rootx() + scan.winfo_width()
                    for widget in outer:
                        self.assertLessEqual(widget.winfo_rootx() + widget.winfo_width(), right, "inside the box")
                    self.assertLessEqual(font.measure(panel.rte_outer_label.get()), outer[0].winfo_width(),
                                         "the label shows in full")
            print(f"SHV scan box at 1101x{CI_SHOT_HEIGHT} px: {heights} px tall")
            self.assertEqual(heights["Angle+Field"], heights["Angle"], "no height in the 1101 px window")
            self.assertEqual(heights["Angle+Current"], heights["Angle"])
            for mode in ("Angle+Field", "Angle+Current"):
                choose(mode, LAB_WIDE, self.height)
                with self.subTest(mode=mode, width=LAB_WIDE):
                    self.assertEqual(panel.rte_outer_place, "under")
                    self.assertEqual({int(w.grid_info()["row"]) for w in outer}, {3})
                    for mine, theirs in zip(panel.rte_outer_entries, main_entries()):
                        self.assertTrue(mine.winfo_ismapped())
                        self.assertEqual((mine.winfo_rootx(), mine.winfo_width()),
                                         (theirs.winfo_rootx(), theirs.winfo_width()))
                    self.assertGreater(outer[0].winfo_rooty(), rows.grid_slaves(row=2, column=0)[0].winfo_rooty())
        finally:
            choose("Angle+Field", LAB_WIDE, self.height)

    def test_the_motor_row_lines_up_with_the_rack_rows(self):
        """User, 2026-10-17: the stage's connection is the rack's fifth row,
        "Motor", laid out as the four above it, in the 1101 px and 2000 px
        windows; its lamp follows the stage.  The first row is the magnet
        supply's, the LS643's since the page starts on it (user, 2026-10-23)
        or the KEPCO's once chosen, each in the same columns."""
        panel = self.a.rte_panel
        entry, connect, disconnect = panel.rte_connection_widgets["newmark"]
        self.assertEqual(panel.rte_magnet_choice_var.get(), app.RTE_MAGNET_CHOICES[1], "the LS643 at start-up")
        try:
            for choice in (app.RTE_MAGNET_CHOICES[1], app.MAGNET_SUPPLY_KEPCO):
                panel.rte_magnet_choice_var.set(choice)
                panel._rte_magnet_choice_changed()
                shown = panel.rte_supply_widgets["lakeshore" if choice != app.MAGNET_SUPPLY_KEPCO else "kepco"]
                hidden = panel.rte_supply_widgets["kepco" if choice != app.MAGNET_SUPPLY_KEPCO else "lakeshore"]
                gauss = panel.rte_connection_widgets["gauss"]
                for width in (1101, LAB_WIDE):
                    with self.subTest(choice=choice, width=width):
                        self.show(width)
                        self.assertIs(entry.master, shown[0].master, "in the rack")
                        self.assertEqual(int(entry.grid_info()["row"]), 4)
                        self.assertFalse(any(w.winfo_ismapped() for w in hidden), "the other supply's row")
                        for theirs in (shown[:3], gauss):
                            for mine, other in zip((entry, connect, disconnect), theirs):
                                self.assertTrue(mine.winfo_ismapped() and other.winfo_ismapped())
                                self.assertEqual((mine.winfo_rootx(), mine.winfo_width()),
                                                 (other.winfo_rootx(), other.winfo_width()))
                        status = next(w for w in entry.master.grid_slaves(row=4, column=5))
                        self.assertEqual(str(status.cget("textvariable")), str(panel.rte_connections["newmark"]))
                        self.assertEqual((status.winfo_rootx(), status.winfo_width()),
                                         (shown[4].winfo_rootx(), shown[4].winfo_width()))
                        (label,) = entry.master.grid_slaves(row=4, column=0)
                        self.assertEqual(str(label.cget("text")), "Motor")
                        # The supply's choice in the label column, no wider.
                        combo = panel.rte_magnet_choice_combo
                        self.assertLessEqual(combo.winfo_rootx() + combo.winfo_width(), entry.winfo_rootx())
        finally:
            panel.rte_magnet_choice_var.set(app.RTE_MAGNET_CHOICES[1])
            panel._rte_magnet_choice_changed()
            self.root.update()
        self.assertEqual(panel.rte_magnet_choice_var.get(), app.RTE_MAGNET_CHOICES[1])
        self.assertEqual(panel.rte_resources["newmark"].get(), "auto")
        self.assertEqual(panel.rte_connections["newmark"].get(), "Not connected")


class GuardTests(Built, unittest.TestCase):
    """A pair whose width follows its own layout settles: the looping row is
    held stacked.

    Until 2026-10-20 this rebuilt the 2026-10-12 shape on the CIDWM page (the
    spare width given to the Images box beside the difference display).
    That pair is gone: the difference display is beside the pulse sign scan
    and takes the spare width itself (user, 2026-10-20), and the Images box
    is alone on its row.  So the guard is held here by a pair made with the
    program's own _arrange_pair_by_width whose box is one pixel too narrow
    for side by side whenever it is side by side, and wide enough whenever
    it is stacked: the shape of the 2026-10-12 hang, which never settles
    without the guard."""

    @classmethod
    def setUpClass(cls):
        cls.build()

    def test_the_looping_row_is_held_stacked(self):
        self.show(LAB_WIDE)
        box = tk.Frame(self.root)
        self.addCleanup(box.destroy)
        left = tk.ttk.Frame(box, width=200, height=20)
        right = tk.ttk.Frame(box, width=200, height=20)
        left.grid(row=0, column=0)
        right.grid(row=0, column=1)
        widths = []

        def follow_own_layout(_event=None):
            # After the pair's own <Configure> handler: a width that makes
            # the other layout right, as a box sized by its own rows.
            wide = getattr(box, "_pair_side_by_side", None)
            if wide is None:
                return
            need = int(box._pair_wide_need)
            width = need - 1 if wide else need + 20
            widths.append(width)
            box.place_configure(width=width)

        self.a._arrange_pair_by_width(box, left, right, gap=0)
        box.bind("<Configure>", follow_own_layout, add="+")
        box.place(x=0, y=0, width=10, height=60)
        self.root.update()  # returns: without the guard it never would
        row = box
        self.assertTrue(row._pair_flip_guard["held"])
        self.assertIs(row._pair_side_by_side, False)
        self.assertEqual(row._pair_flip_guard["flips"], app.PAIR_FLIP_LIMIT + 1, "no flip after the hold")
        self.assertGreater(len(widths), app.PAIR_FLIP_LIMIT, "it did flip until it was held")
        # Held stacked although it is now wide enough, until the window is resized.
        self.assertGreaterEqual(box.winfo_width(), box._pair_wide_need)


if __name__ == "__main__":
    unittest.main()
