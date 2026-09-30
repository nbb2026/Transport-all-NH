"""Start-up defaults and status layout of the PPMS and RT-E pages.

Both are decided in the panel constructors, which the stub-based suites never
run, so they are checked here against a fully built application.
"""
import tkinter as tk
import tkinter.font
import unittest

from lab_screen import LAB_WIDE, build_lab_app, lab_window
from test_ppms_motor_debug import app


class PanelDefaultTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        try:
            cls.root = tk.Tk()
        except tk.TclError as error:  # headless build server
            raise unittest.SkipTest(f"no display: {error}")
        cls.root.withdraw()
        cls.app = app.CombinedControlApp(cls.root)

    @classmethod
    def tearDownClass(cls):
        cls.app.closing = True
        cls.app.safe_close_hardware()
        cls.root.destroy()

    def settings(self, panel):
        """(harmonic, measured) per meter, as the settings row shows them."""
        state = {key: (str(panel._ppms_harmonic_var(key).get()),
                       bool(panel.ppms_lockin_selection[key].get()))
                 for key in app.PPMS_SR830_KEYS}
        state["zurich"] = (str(panel.zurich_harmonic.get()),
                           bool(panel.ppms_lockin_selection["zurich"].get()))
        return state

    def test_rte_measures_sr830_1_across_all_three_harmonics(self):
        self.assertEqual(self.settings(self.app.rte_panel), {
            "830_a": ("1+2+3", True),
            "830_b": ("2", False),
            "830_c": ("3", False),
            "zurich": ("1+2+3", False),
        })

    def test_rte_selection_gives_one_plot_row_per_harmonic(self):
        panel = self.app.rte_panel
        orders = panel._ppms2_measured_orders()
        self.assertEqual(sorted(orders), [1, 2, 3])
        self.assertTrue(all(meters == ["830_a"] for meters in orders.values()),
                        f"only SR830-1 is measured by default: {orders}")

    def test_rte_scan_runs_one_pass_per_harmonic(self):
        passes = self.app.rte_panel._rte_harmonic_passes(dict(
            active_lockins=("830_a",), current_rms_ma=1.0, frequency_hz=133.7,
            compliance_v=10.0, harmonic_a=1, harmonic_b=2, harmonic_c=3,
            allow_no_lockins=False))
        self.assertEqual([entry["harmonic_a"] for entry in passes], [1, 2, 3])

    def test_ppms_keeps_one_harmonic_per_meter(self):
        self.assertEqual(self.settings(self.app.ppms_panel), {
            "830_a": ("1", True),
            "830_b": ("2", True),
            "830_c": ("3", True),
            "zurich": ("1+2+3", True),
        })


class NotebookTabTests(unittest.TestCase):
    """The tab strip names each page; pages sharing a rack share a prefix."""

    def test_tab_titles(self):
        try:
            root = tk.Tk()
        except tk.TclError as error:  # headless build server
            raise unittest.SkipTest(f"no display: {error}")
        try:
            built = app.CombinedControlApp(root)
            notebook = next(w for w in built.__dict__.values()
                            if isinstance(w, tk.ttk.Notebook))
            titles = [notebook.tab(index, "text")
                      for index in range(notebook.index("end"))]
            self.assertEqual(titles, [
                "MOKE light", "MOKE motor", "MOKE magnet", "MOKE-2410",
                "MOKE-6221", "Gate-VI", "PPMS", "RT-E", "FMR", "STFMR"])
        finally:
            built.closing = True
            built.safe_close_hardware()
            root.destroy()


class RTEStatusRowTests(unittest.TestCase):
    """The RT-E scan status stays on one line in the widened lab window.

    These are the messages RT-E itself shows.  Failures are cut at the first
    "; " by short_status_error, and the full text goes to the error dialog and
    the log.  (The long "Stopping the Current scan; ..." text is the PPMS
    page's; RT-E overrides the stop.)
    """

    MESSAGES = (
        "Failed: " + app.short_status_error(
            RuntimeError("6221 hit compliance; scan stopped and output turned off"), 100),
        "Failed: " + app.short_status_error(
            RuntimeError("LS475 field drifted during acquisition; point discarded"), 100),
        "RT-E Current: 3 harmonic pass(es), 300 points",
        "Stopping; completed CSV points and outputs kept",
    )

    @classmethod
    def setUpClass(cls):
        try:
            cls.root = tk.Tk()
        except tk.TclError as error:  # headless build server
            raise unittest.SkipTest(f"no display: {error}")
        cls.app = build_lab_app(app, cls.root)  # as the lab PC shows it
        cls.root.geometry(f"{LAB_WIDE}x{lab_window(cls.app)[1]}+0+0")
        notebook = next(w for w in cls.app.__dict__.values()
                        if isinstance(w, tk.ttk.Notebook))
        for index in range(notebook.index("end")):
            if notebook.tab(index, "text") == "RT-E":
                notebook.select(index)
        cls.root.update()

    @classmethod
    def tearDownClass(cls):
        cls.app.closing = True
        cls.app.safe_close_hardware()
        cls.root.destroy()

    def status_label(self):
        variable = str(self.app.rte_panel.ppms_field_scan_status_var)
        found = []

        def walk(widget):
            for child in widget.winfo_children():
                if (isinstance(child, tk.ttk.Label)
                        and str(child.cget("textvariable")) == variable):
                    found.append(child)
                walk(child)

        walk(self.root)
        self.assertEqual(len(found), 1, "expected exactly one status label")
        return found[0]

    def test_longest_status_fits_on_one_line(self):
        label = self.status_label()
        for message in self.MESSAGES:
            self.app.rte_panel.ppms_field_scan_status_var.set(message)
            self.root.update()
            font = tkinter.font.Font(font=label.cget("font") or "TkDefaultFont")
            self.assertLessEqual(font.measure(message), label.winfo_width(),
                                 f"status too narrow for: {message}")
            self.assertLess(label.winfo_height(), 26,
                            f"status wrapped onto a second line: {message}")

    def test_status_never_wraps(self):
        self.assertEqual(int(self.status_label().cget("wraplength")), 0)

    def test_status_shares_its_row_with_the_progress_bar(self):
        """Giving the status its own row would cost the plots a row of height."""
        progress = self.app.rte_panel.ppms_field_scan_progress
        self.assertEqual(progress.master, self.status_label().master)
        self.assertLess(abs(progress.winfo_rooty() - self.status_label().winfo_rooty()), 6)


if __name__ == "__main__":
    unittest.main()
