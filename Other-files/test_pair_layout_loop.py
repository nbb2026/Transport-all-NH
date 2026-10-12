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


def descendants(widget):
    for child in widget.winfo_children():
        yield child
        yield from descendants(child)


def cidwm_mode_row(built):
    box = next(w for w in descendants(built.k6221_tab)
               if isinstance(w, tk.ttk.LabelFrame) and str(w.cget("text")) == "MOKE difference display")
    return next(w for w in box.winfo_children() if hasattr(w, "_pair_side_by_side"))


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
