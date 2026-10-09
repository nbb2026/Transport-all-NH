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

    def test_rte_defaults_are_the_users_figure(self):
        """2026-10-07: the Zurich alone measures, 1+2+3, at 0.1 mA RMS, 17.3 Hz."""
        panel = self.app.rte_panel
        self.assertEqual(self.settings(panel), {
            "830_a": ("1+2+3", False),
            "830_b": ("2", False),
            "830_c": ("3", False),
            "zurich": ("1+2+3", True),
        })
        self.assertEqual(panel.ppms_transport_current_var.get(), "0.1")
        self.assertEqual(panel.ppms_transport_frequency_var.get(), "17.3")
        self.assertEqual(panel.ppms_transport_compliance_var.get(), "50")
        self.assertEqual((panel.rte_drive_target.get(), panel.rte_voltage_limit.get()), ("0", "5"))
        self.assertEqual(panel.zurich_expected_frequency, 17.3)
        # The PPMS page keeps its own excitation.
        self.assertEqual(self.app.ppms_panel.ppms_transport_frequency_var.get(), "317.3")

    def test_rte_selection_gives_one_plot_row_per_harmonic(self):
        panel = self.app.rte_panel
        orders = panel._ppms2_measured_orders()
        self.assertEqual(sorted(orders), [1, 2, 3])
        self.assertTrue(all(meters == ["zurich"] for meters in orders.values()),
                        f"only the Zurich is measured by default: {orders}")

    def test_rte_scan_runs_one_pass_per_harmonic(self):
        passes = self.app.rte_panel._rte_harmonic_passes(dict(
            active_lockins=("830_a",), current_rms_ma=1.0, frequency_hz=133.7,
            compliance_v=10.0, harmonic_a=1, harmonic_b=2, harmonic_c=3,
            allow_no_lockins=False))
        self.assertEqual([entry["harmonic_a"] for entry in passes], [1, 2, 3])

    def test_ppms_set_h_and_zero_h_end_persistent(self):
        panel = self.app.ppms_panel
        self.assertEqual(panel.ppms_field_mode_var.get(), "Persistent")
        self.addCleanup(panel.ppms_field_target_var.set, panel.ppms_field_target_var.get())
        panel.ppms_field_target_var.set("1000")
        self.assertEqual(panel._parse_ppms_field_parameters()[3], 0, "Set H")
        self.assertEqual(panel._parse_ppms_field_parameters(0.0)[3], 0, "Zero H")

    def test_moke_24_page_chooses_a_2410_or_a_2400(self):
        moke = self.app
        self.addCleanup(lambda: (moke.keithley_model_var.set("2410"), moke._update_keithley_controls()))
        self.assertEqual(moke.keithley_model_var.get(), "2410", "the 2410 stays the default")
        self.assertEqual(tuple(moke.keithley_model_combo.cget("values")), ("2410", "2400"))
        self.assertEqual(str(moke.keithley_model_combo.cget("state")), "readonly")
        self.assertEqual(moke.keithley_off_button.cget("text"), "2410 zero / output off")
        self.assertIn("±200 V (hardware OVP 200 V)", moke.keithley_hazard_var.get())
        moke.keithley_model_var.set("2400")
        moke._update_keithley_controls()
        self.assertEqual(moke.keithley_off_button.cget("text"), "2400 zero / output off")
        self.assertIn("2400 scans are software-limited to ±160 V (hardware OVP 160 V)",
                      moke.keithley_hazard_var.get())
        # While a connect is under way the model and Connect stay locked.
        self.addCleanup(setattr, moke, "keithley_connecting", False)
        moke.keithley_connecting = True
        moke._update_keithley_controls()
        self.assertEqual(str(moke.keithley_model_combo.cget("state")), "disabled")
        self.assertEqual(str(moke.keithley_connect_button.cget("state")), "disabled")
        # Even once the controller holds the instrument (OVP still being
        # set), nothing that sources is offered until the connect finishes.
        self.addCleanup(setattr, moke.keithley_controller, "instrument", None)
        self.addCleanup(moke.keithley_safety_var.set, False)
        moke.keithley_controller.instrument = object()
        moke.keithley_safety_var.set(True)
        moke._update_keithley_controls()
        for button in (moke.keithley_start_button,
                       moke.keithley_disconnect_button, *moke.keithley_manual_buttons):
            self.assertEqual(str(button.cget("state")), "disabled", button.cget("text"))
        moke.keithley_controller.instrument = None
        moke.keithley_connecting = False
        moke._update_keithley_controls()
        self.assertEqual(str(moke.keithley_model_combo.cget("state")), "readonly")

    def test_resistance_moved_from_moke_24_to_non_local(self):
        """2026-10-08: MOKE-24 keeps the MOKE voltage scan; |R| vs time, the
        2400-IV sweep and 6221-IV (the RT-E Current scan) are on the
        Non-local page with Gate-VI."""
        a = self.app
        self.assertFalse(hasattr(a, "keithley_resistance_button"))
        self.assertNotIn("resistance", a.keithley_hazard_var.get())
        tabs = a.nonlocal_measurements
        self.assertEqual([tabs.tab(i, "text") for i in range(tabs.index("end"))],
                         ["|R| vs time", "2400-IV", "6221-IV", "Gate-VI scan"])
        # 6221-IV starts at RT-E's current range, excitation and timing.
        self.assertEqual(
            (a.nonlocal_iv6221_start_var.get(), a.nonlocal_iv6221_end_var.get(),
             a.nonlocal_iv6221_step_var.get(), a.nonlocal_iv6221_loops_var.get(),
             a.nonlocal_iv6221_current_var.get(),
             a.nonlocal_iv6221_frequency_var.get(), a.nonlocal_iv6221_compliance_var.get(),
             a.nonlocal_iv6221_settle_var.get(), a.nonlocal_iv6221_acquire_var.get()),
            ("0.01", "1", "0.01", "1", "0.1", "17.3", "50", "0.5", "1"))
        self.assertFalse(a.nonlocal_iv6221_round_trip_var.get())
        self.assertEqual(
            (a.nonlocal_iv_start_var.get(), a.nonlocal_iv_end_var.get(), a.nonlocal_iv_step_var.get(),
             a.nonlocal_iv_settle_var.get(), a.nonlocal_iv_limit_var.get()),
            ("-6", "6", "0.25", "0.5", "0.001"))

    def widgets(self, cls, variable=None, text=None):
        found, pending = [], [self.app.nonlocal_vi_tab]
        while pending:
            widget = pending.pop()
            pending.extend(widget.winfo_children())
            if widget.winfo_class() != cls:
                continue
            if variable is not None and str(widget.cget("textvariable")) != str(variable):
                continue
            if text is not None and str(widget.cget("text")) != text:
                continue
            found.append(widget)
        return found

    def test_6221_iv_settings_as_on_rte_and_waits_for_the_6221(self):
        """2026-10-08: RT-E's settings and start-up values on 6221-IV."""
        a = self.app
        self.addCleanup(a.nonlocal_iv6221_safety_var.set, False)
        a.nonlocal_iv6221_safety_var.set(True)
        a._update_nonlocal_vi_controls()
        for button in (a.nonlocal_iv6221_start_button, a.nonlocal_iv6221_set_button):
            self.assertEqual(str(button.cget("state")), "disabled", "the 6221 is not connected")
        # RT-E's harmonics and Measure ticks: the Zurich alone, 1+2+3.
        self.assertEqual({k: v.get() for k, v in a.nonlocal_iv6221_harmonic_vars.items()},
                         {"8": "1+2+3", "9": "2", "mfli": "1+2+3"})
        self.assertEqual({k: v.get() for k, v in a.nonlocal_iv6221_measure_vars.items()},
                         {"8": False, "9": False, "mfli": True})
        for key in ("8", "9", "mfli"):
            combos = self.widgets("TCombobox", a.nonlocal_iv6221_harmonic_vars[key])
            self.assertEqual(len(combos), 1, key)
            self.assertEqual(tuple(combos[0].cget("values")), tuple(app.PPMS_ZURICH_MODE_ORDERS))
        # One X / Y / θ row per measured order: 1st, 2nd and 3rd.
        shown = [order for order in app.PPMS_HARMONIC_ORDERS
                 if a.nonlocal_iv6221_plot_frames[(order, "X")].winfo_manager()]
        self.assertEqual(shown, [1, 2, 3])
        # The meters in each title, as on RT-E.
        self.assertEqual(a.nonlocal_iv6221_plot_frames[(2, "theta")].cget("text"),
                         "2nd harmonic θ — Zurich")
        a.nonlocal_iv6221_harmonic_vars["mfli"].set("2")
        self.addCleanup(a._refresh_nonlocal_iv6221_plot_layout)
        self.addCleanup(a.nonlocal_iv6221_harmonic_vars["mfli"].set, "1+2+3")
        a._refresh_nonlocal_iv6221_plot_layout()
        shown = [order for order in app.PPMS_HARMONIC_ORDERS
                 if a.nonlocal_iv6221_plot_frames[(order, "X")].winfo_manager()]
        self.assertEqual(shown, [2])

    def test_settings_and_scan_boxes_and_the_zurich_row(self):
        """2026-10-08: each Non-local scan has a settings box and a scan box,
        as RT-E; the rack's Zurich row has Ref. and TC (s)."""
        a = self.app
        for name in ("2400-IV", "6221-IV", "Gate-VI"):
            for part in ("settings", "scan"):
                self.assertEqual(len(self.widgets("TLabelframe", text=f"{name} {part}")), 1,
                                 f"{name} {part}")
        # The Zurich's reference input and TC are on the rack only.
        self.assertEqual(len(self.widgets("TCombobox", a.nonlocal_vi_mfli_reference_source_var)), 1)
        self.assertEqual(len(self.widgets("TEntry", a.nonlocal_mfli_tc_var)), 1)
        self.assertEqual(a.nonlocal_mfli_tc_var.get(), "0.1")
        # RT-E's rack Zurich (user, 2026-10-08): DEV4414 on Trigger In 1; the
        # Ref. follows the chosen Zurich's wiring.
        self.assertEqual((a.nonlocal_vi_mfli_device_var.get(), a.nonlocal_vi_mfli_reference_source_var.get()),
                         ("DEV4414 (Super)", "Trigger In 1"))
        self.addCleanup(a.nonlocal_vi_mfli_device_var.set, "DEV4414 (Super)")
        a.nonlocal_vi_mfli_device_var.set("DEV4435 (Normal)")
        self.assertEqual(a.nonlocal_vi_mfli_reference_source_var.get(), "Aux Input 1")
        a.nonlocal_vi_mfli_device_var.set("DEV4414 (Super)")
        self.assertEqual(a.nonlocal_vi_mfli_reference_source_var.get(), "Trigger In 1")
        self.assertEqual(a.nonlocal_iv6221_zurich_range_var.get(), "Autorange ON")
        # The Gate-VI read settings keep their own harmonic choices: an SR830
        # up to 1+2, the Zurich up to 1+2+3 (user, 2026-10-09).
        self.assertEqual(len(self.widgets("TCombobox", a.nonlocal_vi_lockin_harmonic_vars["8"])), 1)
        sr830 = self.widgets("TCombobox", a.nonlocal_vi_lockin_harmonic_vars["8"])[0]
        self.assertEqual(tuple(str(v) for v in sr830.cget("values")), ("1", "2", "3", "1+2"))
        zurich = self.widgets("TCombobox", a.nonlocal_vi_mfli_harmonic_var)[0]
        self.assertEqual(tuple(str(v) for v in zurich.cget("values")), ("1", "2", "3", "1+2", "1+2+3"))
        self.assertEqual(a.nonlocal_vi_gate_resistance_var.get(), "100")
        # Laid out as "6221-IV settings" (user, 2026-10-09): a Measure tick
        # per meter, the 2182A's too, and the safety tick on top.
        measure = [w for w in self.widgets("TCheckbutton") if str(w.cget("text")).startswith("Measure")]
        variables = {str(w.cget("variable")) for w in measure}
        for variable in (a.nonlocal_vi_measurement_enabled_vars["8"], a.nonlocal_vi_measurement_enabled_vars["9"],
                         a.nonlocal_vi_use_mfli_var, a.nonlocal_vi_use_2182_var):
            self.assertIn(str(variable), variables)
        self.assertEqual(len(self.widgets(
            "TCheckbutton", text="Safety confirmed: voltage pulses, read current, wiring, sample")), 1)
        self.assertEqual(len(self.widgets("TCombobox", a.nonlocal_vi_output_mode_var)), 1)
        # 2400-IV settings (user, 2026-10-09): no Sample box; Set, OFF and
        # Apply pulse for the Keithley on its own.
        for text in ("Set 2400", "2400 / 6221 OFF", "Apply pulse"):
            self.assertEqual(len(self.widgets("TButton", text=text)), 1, text)
        self.assertEqual(self.widgets("TLabel", text="Sample:"), [])

    def test_non_local_measurements_take_turns_on_the_keithley(self):
        a = self.app
        controller = a.nonlocal_vi_2410_controller
        buttons = (a.nonlocal_resistance_button, a.nonlocal_iv_start_button)

        def states():
            a._update_nonlocal_vi_controls()
            return [str(button.cget("state")) for button in buttons]

        self.addCleanup(lambda: (a.nonlocal_resistance_safety_var.set(False),
                                 a.nonlocal_iv_safety_var.set(False), states(),
                                 a._update_keithley_controls()))
        a.nonlocal_resistance_safety_var.set(True)
        a.nonlocal_iv_safety_var.set(True)
        self.assertEqual(states(), ["disabled", "disabled"], "not connected")
        self.addCleanup(setattr, controller, "instrument", None)
        controller.instrument = object()
        self.assertEqual(states(), ["normal", "normal"])
        self.addCleanup(setattr, a, "nonlocal_keithley_task", None)
        a.nonlocal_keithley_task = "iv"
        self.assertEqual(states(), ["disabled", "disabled"])
        self.assertEqual(str(a.nonlocal_iv_stop_button.cget("state")), "normal")
        self.assertEqual(str(a.nonlocal_vi_start_button.cget("state")), "disabled")
        self.assertEqual(str(a.nonlocal_vi_device_disconnect_buttons["2410"].cget("state")), "disabled")
        self.assertEqual(str(a.nonlocal_vi_model_combo.cget("state")), "disabled")
        a.nonlocal_keithley_task = None
        self.addCleanup(setattr, a, "nonlocal_vi_running", False)
        a.nonlocal_vi_running = True
        self.assertEqual(states(), ["disabled", "disabled"], "a pulse scan holds the Keithley")
        a.nonlocal_vi_running = False
        a._update_keithley_controls()
        self.assertEqual(str(a.keithley_connect_button.cget("state")), "disabled",
                         "MOKE-24 waits while the Non-local page holds the Keithley")

    def test_gate_vi_chooses_a_2410_or_a_2400(self):
        vi = self.app
        controller = vi.nonlocal_vi_2410_controller

        def choose(model):
            vi.nonlocal_vi_2410_model_var.set(model)
            vi._on_nonlocal_vi_model_selected()

        self.addCleanup(choose, "2400")
        # The Non-local page starts with a 2400 (user, 2026-10-08); MOKE-24
        # keeps the 2410.
        self.assertEqual(vi.nonlocal_vi_2410_model_var.get(), "2400", "the 2400 is the default here")
        self.assertEqual(controller.model, "2400")
        self.assertEqual(vi.keithley_model_var.get(), "2410", "MOKE-24 keeps the 2410")
        self.assertEqual(tuple(vi.nonlocal_vi_model_combo.cget("values")), ("2410", "2400"))
        self.assertEqual(str(vi.nonlocal_vi_model_combo.cget("state")), "readonly")
        self.assertEqual(vi.nonlocal_vi_keithley_label_var.get(), "Keithley 2400:")
        self.assertEqual(vi.nonlocal_vi_off_button.cget("text"), "Stop 2400 / 6221 now")
        self.assertIn("during 2400 pulses", vi.nonlocal_vi_interlock_var.get())
        self.assertEqual(vi.nonlocal_vi_plot_frames["resistance"].cget("text"), "Keithley 2400 resistance")
        self.assertNotIn("pulse", vi.nonlocal_vi_plot_frames, "no command-voltage plot (user, 2026-10-09)")
        choose("2410")
        self.assertEqual(controller.model, "2410")
        self.assertEqual(vi.nonlocal_vi_plot_frames["resistance"].cget("text"), "Keithley 2410 resistance")
        self.assertEqual(vi.nonlocal_vi_keithley_label_var.get(), "Keithley 2410:")
        self.assertEqual(vi.nonlocal_vi_off_button.cget("text"), "Stop 2410 / 6221 now")
        self.assertIn("during 2410 pulses", vi.nonlocal_vi_interlock_var.get())
        # Locked while a connect or a scan is under way.
        self.addCleanup(setattr, vi, "nonlocal_vi_busy", False)
        vi.nonlocal_vi_busy = True
        vi._update_nonlocal_vi_controls()
        self.assertEqual(str(vi.nonlocal_vi_model_combo.cget("state")), "disabled")
        vi.nonlocal_vi_busy = False
        vi._update_nonlocal_vi_controls()

    def test_gate_vi_labels_use_ascii_colons(self):
        texts = []
        pending = [self.app.nonlocal_vi_tab]
        while pending:
            widget = pending.pop()
            pending.extend(widget.winfo_children())
            try:
                texts.append(str(widget.cget("text")))
            except Exception:
                pass
        self.assertFalse([text for text in texts if "：" in text])
        self.assertIn("Keithley 6221:", texts)

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
                "MOKE light", "MOKE motor", "MOKE magnet", "MOKE-24",
                "MOKE-6221", "Non-local", "PPMS", "RT-E", "FMR", "STFMR"])
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
        "Completed; 6221 current set to 0 (output OFF, read back)",
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
        return self.app.rte_panel.rte_status_label

    def test_longest_status_fits_on_one_line(self):
        label = self.status_label()
        variable = self.app.rte_panel.ppms_field_scan_status_var
        variable.set("Ready")
        self.root.update()
        one_line = label.winfo_height()
        font = tkinter.font.Font(font=label.cget("font") or "TkDefaultFont")
        for message in self.MESSAGES:
            variable.set(message)
            self.root.update()
            self.assertEqual(str(label.cget("text")), message, "shown in full")
            self.assertLessEqual(font.measure(message), label.winfo_width(),
                                 f"status too narrow for: {message}")
            self.assertEqual(label.winfo_height(), one_line,
                             f"status wrapped onto a second line: {message}")

    def test_a_long_failure_is_cut_with_an_ellipsis(self):
        """The status shows what fits and ends in "…"; the variable keeps it all."""
        label = self.status_label()
        variable = self.app.rte_panel.ppms_field_scan_status_var
        message = "Failed: " + "VI_ERROR_TMO (-1073807339): Timeout expired before operation completed " * 3
        try:
            variable.set("Ready")
            self.root.update()
            one_line = label.winfo_height()
            variable.set(message)
            self.root.update()
            shown = str(label.cget("text"))
            font = tkinter.font.Font(font=label.cget("font") or "TkDefaultFont")
            self.assertTrue(shown.endswith("\u2026"), shown)
            self.assertTrue(message.startswith(shown[:-1].rstrip()), shown)
            self.assertLessEqual(font.measure(shown), label.winfo_width())
            self.assertEqual(variable.get(), message, "the full text is kept")
            self.assertEqual(label.winfo_height(), one_line)
        finally:
            variable.set("Ready")

    def test_status_never_wraps(self):
        self.assertEqual(int(self.status_label().cget("wraplength")), 0)

    def test_status_shares_its_row_with_the_progress_bar(self):
        """Giving the status its own row would cost the plots a row of height."""
        progress = self.app.rte_panel.ppms_field_scan_progress
        self.assertEqual(progress.master, self.status_label().master)
        self.assertLess(abs(progress.winfo_rooty() - self.status_label().winfo_rooty()), 6)


if __name__ == "__main__":
    unittest.main()
