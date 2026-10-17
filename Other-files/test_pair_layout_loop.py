"""The window finishes drawing: no side-by-side pair flips forever.

2026-10-12: on the CIDWM page the difference display's mode row (Off / Plain
difference / Drift tracking beside Recapture BG / 100x marker) flipped
between side by side and stacked at every <Configure>, because the spare
width went to the Images box and the display's width followed its own row.
root.update() never returned and the program hung at start-up on the lab
PCs.  Each class builds the whole application as the lab PC shows it.
"""
import tkinter as tk
import unittest
from unittest import mock

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
        notebook = next(w for w in self.a.__dict__.values() if isinstance(w, tk.ttk.Notebook))
        for index in range(notebook.index("end")):
            if notebook.tab(index, "text") == "CIDWM":
                notebook.select(index)
        self.show(LAB_WIDE)
        row = cidwm_mode_row(self.a)
        moke = row.master.master
        self.assertEqual((int(moke.columnconfigure(0)["weight"]), int(moke.columnconfigure(1)["weight"])),
                         (1, 0))
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
        # The Zurich at 1+2+3, three plot rows, the worst case for height:
        # SHV starts at 1+2 since the user's figure (2026-10-18), and every
        # plot must still keep its 55 px when the user picks 1+2+3.  As the
        # harmonic box does (its <<ComboboxSelected>>); the Zurich is not
        # connected, so nothing is sent.
        cls.a.rte_panel.zurich_harmonic.set("1+2+3")
        cls.a.rte_panel._apply_ppms2_zurich_harmonic()
        cls.root.update()

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
              f"stage block {block.winfo_reqwidth()}x{block.winfo_reqheight()}; plots "
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
                            "Stage: -359.999° (10000 steps/°), moving, limit input on", failure):
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
        """The SHV default, the Zurich at 1+2 (user's figure, 2026-10-18):
        the first- and second-harmonic rows keep their 55 px in the 1101 px
        window and at 2000 px, and the third row is hidden."""
        panel = self.a.rte_panel
        canvases = panel.ppms_harmonic_canvases
        try:
            panel.zurich_harmonic.set(app.RTE_ZURICH_DEFAULT_HARMONICS)
            panel._apply_ppms2_zurich_harmonic()
            self.assertEqual(app.RTE_ZURICH_DEFAULT_HARMONICS, "1+2")
            self.assertEqual(sorted(panel._ppms2_measured_orders()), [1, 2])
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
            panel.zurich_harmonic.set("1+2+3")
            panel._apply_ppms2_zurich_harmonic()
            self.root.update()

    def test_the_motor_row_lines_up_with_the_rack_rows(self):
        """User, 2026-10-17: the stage's connection is the rack's fifth row,
        "Motor", laid out as the four above it, in the 1101 px and 2000 px
        windows; its lamp follows the stage."""
        panel = self.a.rte_panel
        entry, connect, disconnect = panel.rte_connection_widgets["newmark"]
        kepco = panel.rte_connection_widgets["kepco"]
        for width in (1101, LAB_WIDE):
            with self.subTest(width=width):
                self.show(width)
                self.assertIs(entry.master, kepco[0].master, "in the rack")
                self.assertEqual(int(entry.grid_info()["row"]), 4)
                for mine, theirs in zip((entry, connect, disconnect), kepco):
                    self.assertTrue(mine.winfo_ismapped())
                    self.assertEqual((mine.winfo_rootx(), mine.winfo_width()),
                                     (theirs.winfo_rootx(), theirs.winfo_width()))
                status = next(w for w in entry.master.grid_slaves(row=4, column=5))
                kepco_status = next(w for w in entry.master.grid_slaves(row=0, column=5))
                self.assertEqual(str(status.cget("textvariable")), str(panel.rte_connections["newmark"]))
                self.assertEqual((status.winfo_rootx(), status.winfo_width()),
                                 (kepco_status.winfo_rootx(), kepco_status.winfo_width()))
                (label,) = entry.master.grid_slaves(row=4, column=0)
                self.assertEqual(str(label.cget("text")), "Motor")
        self.assertEqual(panel.rte_resources["newmark"].get(), "auto")
        self.assertEqual(panel.rte_connections["newmark"].get(), "Not connected")


class GuardTests(Built, unittest.TestCase):
    """The layout that hung (spare width on the Images side) now settles:
    the looping row is held stacked."""

    @classmethod
    def setUpClass(cls):
        original = app.CombinedControlApp._arrange_pair_by_width

        def as_on_2026_10_12(self, container, left, right, **options):
            if "text" in right.keys() and str(right.cget("text")) == "Images":
                container.columnconfigure(0, weight=0)
                container.columnconfigure(1, weight=1)
            return original(self, container, left, right, **options)

        with mock.patch.object(app.CombinedControlApp, "_arrange_pair_by_width", as_on_2026_10_12):
            cls.build()

    def test_the_looping_row_is_held_stacked(self):
        self.show(LAB_WIDE)  # returns: it hung here before
        row = cidwm_mode_row(self.a)
        self.assertTrue(row._pair_flip_guard["held"])
        self.assertIs(row._pair_side_by_side, False)
        self.assertEqual(row._pair_flip_guard["flips"], app.PAIR_FLIP_LIMIT + 1, "no flip after the hold")


if __name__ == "__main__":
    unittest.main()
