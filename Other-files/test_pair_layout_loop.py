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


def rte_stage_pair(built):
    """RT-E's scan-box pair: (its frame, the scan rows, the stage block)."""
    rows = built.rte_panel.rte_start_button.master
    return rows.master, rows, built.rte_panel.rte_stage_stop_button.master


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
    """User/CI, 2026-10-14: the Newmark stage's controls are a block in the
    RT-E scan box.  As a rack row and a row under the settings they took
    about 84 px of height in the 1101 px window the program opens with, and
    every plot fell below its 55 px minimum ("Increase the window height")."""

    @classmethod
    def setUpClass(cls):
        cls.build()
        notebook = next(w for w in cls.a.__dict__.values() if isinstance(w, tk.ttk.Notebook))
        for index in range(notebook.index("end")):
            if notebook.tab(index, "text") == "RT-E":
                notebook.select(index)

    def layouts(self):
        """(settings beside the scan box, stage block beside the scan rows)."""
        pair = rte_stage_pair(self.a)[0]
        controls = pair.master.master
        return controls._pair_side_by_side, pair._pair_side_by_side

    def test_beside_the_scan_rows_at_1101_px_and_under_them_at_2000_px_whatever_the_history(self):
        # The settings/scan pair decides by the scan box's stacked width, so
        # the pair inside the scan box cannot change its decision.
        expected = {1101: (False, True), LAB_WIDE: (True, False)}
        for history in ((1101,), (LAB_WIDE,), (LAB_WIDE, 1101), (1101, LAB_WIDE),
                        (2560, 1101), (1101, 2560, LAB_WIDE), (826, LAB_WIDE, 1101)):
            for width in history:
                self.show(width)
            with self.subTest(history=history):
                self.assertEqual(self.layouts(), expected[history[-1]])
                pair = rte_stage_pair(self.a)[0]
                self.assertFalse(getattr(pair, "_pair_flip_guard", {}).get("held"))

    def test_the_block_costs_no_height_and_fits_at_1101_px(self):
        self.show(1101)
        pair, rows, block = rte_stage_pair(self.a)
        self.assertLessEqual(block.winfo_reqheight(), rows.winfo_reqheight(),
                             "the block is no taller than the three scan rows")
        scan = pair.master
        self.assertLessEqual(block.winfo_rootx() + block.winfo_width(), scan.winfo_rootx() + scan.winfo_width())

    def test_a_reading_stays_on_one_line(self):
        self.show(1101)
        panel = self.a.rte_panel
        block = rte_stage_pair(self.a)[2]
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

    def test_every_plot_is_drawn_in_the_1101_px_window(self):
        self.root.geometry(f"1101x{CI_SHOT_HEIGHT}+0+0")
        self.root.update()
        canvases = self.a.rte_panel.ppms_harmonic_canvases
        self.assertEqual(len(canvases), 9, "the Zurich at 1+2+3: three rows of X, Y, theta")
        for key, canvas in canvases.items():
            with self.subTest(plot=key):
                # _draw_nonlocal_series's compact minimum.
                self.assertGreaterEqual(canvas.winfo_height(), 55, "Increase the window height")


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
