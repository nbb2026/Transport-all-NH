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
        """The SHV page (RT-E until 2026-10-18): the Zurich alone measures
        (2026-10-07), 1+2, at 1 mA RMS, 317.3 Hz, KEPCO current 8 A (the
        user's figures, 2026-10-18; it was 1+2+3, 0.1 mA RMS, 17.3 Hz, 0 A)."""
        panel = self.app.rte_panel
        self.assertEqual(self.settings(panel), {
            "830_a": ("1+2+3", False),
            "830_b": ("2", False),
            "830_c": ("3", False),
            "zurich": ("1+2", True),
        })
        self.assertEqual(self.app.notebook.tab(self.app.rte_panel_tab, "text"), "SHV")
        self.assertEqual(panel.ppms_transport_current_var.get(), "1")
        self.assertEqual(panel.ppms_transport_frequency_var.get(), "317.3")
        self.assertEqual(panel.ppms_transport_compliance_var.get(), "50")
        self.assertEqual((panel.rte_drive_target.get(), panel.rte_voltage_limit.get()), ("8", "5"))
        self.assertEqual(panel.zurich_expected_frequency, 317.3)
        # The PPMS page keeps its own excitation.
        self.assertEqual(self.app.ppms_panel.ppms_transport_frequency_var.get(), "317.3")
        # The start-up scan (user's figure, 2026-10-18): Angle, 0 to -360 deg
        # in 5 deg steps, round trip, Settle s 0.5, Acquire s 2, Loops 1.
        self.assertEqual(panel.ppms_simple_mode.get(), "Angle")
        self.assertEqual([v.get() for v in panel.ppms_simple_range], ["0", "-360", "5"])
        self.assertIs(panel.rte_round_trip.get(), True)
        self.assertEqual((panel.ppms_field_scan_stable_var.get(), panel.ppms_acquisition_time_var.get(),
                          int(panel.ppms_field_scan_loop_var.get())), ("0.5", "2", 1))
        self.assertEqual(panel.rte_scan_units.get(), "Stage °")
        self.assertEqual(panel.ppms_plot_axis, "angle")
        # The PPMS page keeps its own Acquire s.
        self.assertEqual(self.app.ppms_panel.ppms_acquisition_time_var.get(), "1")

    def test_rte_selection_gives_one_plot_row_per_harmonic(self):
        panel = self.app.rte_panel
        orders = panel._ppms2_measured_orders()
        # The Zurich at 1+2 by default (user's figure, 2026-10-18; was 1+2+3).
        self.assertEqual(sorted(orders), [1, 2])
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
        # 6221-IV starts at the current range, excitation and timing RT-E had
        # until 2026-10-18 (RT-E now starts on an Angle scan at 317.3 Hz).
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
            ("-10", "10", "1", "0.5", "0.001"))

    def test_non_local_frequencies_default_to_17_3_hz(self):
        """2026-10-09: every non-local AC frequency starts at 17.3 Hz (was
        13.7 on Gate-VI)."""
        a = self.app
        self.assertEqual((a.nonlocal_vi_frequency_var.get(), a.nonlocal_iv6221_frequency_var.get(),
                          a.nonlocal_frequency_var.get()), ("17.3", "17.3", "17.3"))

    def test_camera_display_defaults_follow_the_users_figure(self):
        """2026-10-12: CLim -2 ~ 2, 4 frames, 4×4 binning, 2×2 mean filter,
        exposure 100 ms, FPS 10; refresh 0.2 s, 100 points; edit steps
        1, 1, 1, 5, 5.  The camera worker starts from the same values."""
        a = self.app
        self.assertEqual((a.difference_clim_low_var.get(), a.difference_clim_high_var.get(),
                          a.difference_temporal_average_var.get(), a.difference_spatial_binning_var.get(),
                          a.difference_mean_filter_var.get(), a.camera_exposure_ms_var.get(),
                          a.camera_video_fps_var.get()), ("-2", "2", 4, 4, 2, "100", "10"))
        self.assertEqual((float(a.roi_live_interval_var.get()), int(float(a.roi_live_max_points_var.get()))),
                         (0.2, 100))
        self.assertEqual({key: var.get() for key, var in a.roi_edit_step_vars.items()},
                         {"A": "1", "W": "1", "H": "1", "X": "5", "Y": "5"})
        self.assertEqual((app.DIFFERENCE_TEMPORAL_AVERAGE_FRAMES, app.DIFFERENCE_SPATIAL_BINNING,
                          app.DIFFERENCE_MEAN_FILTER_SIZE, app.EXPOSURE_S), (4, 4, 2, 0.1))

    def test_cidwm_pulse_sign_scan_row(self):
        """2026-10-12: Pulses, Signs and Wait after pulse s, with its own
        start and stop buttons (disabled until the 6221 is connected)."""
        a = self.app
        self.assertEqual((a.cidwm_scan_current_var.get(), a.cidwm_scan_pulses_var.get(),
                          a.cidwm_scan_signs_var.get(), a.cidwm_scan_wait_var.get()),
                         ("2", "10", "+-", "1"))
        self.assertEqual(str(a.cidwm_scan_start_button.cget("state")), "disabled")
        self.assertEqual(str(a.cidwm_scan_stop_button.cget("state")), "disabled")
        self.assertEqual(str(a.cidwm_scan_start_button.cget("text")), "Choose folder and start scan")

    def test_cidwm_avtech_box_and_the_scan_source(self):
        """2026-10-13: the Avtech AVR-E3-B box (GPIB address 8, 10 V, 10 ns,
        everything off until connected and ticked) and the scan's pulse
        source, the 6221 by default."""
        a = self.app
        self.assertEqual((a.avtech_resource_var.get(), a.avtech_amplitude_var.get(), a.avtech_width_var.get(),
                          a.cidwm_scan_amplitude_var.get(), a.cidwm_scan_source_var.get()),
                         ("GPIB0::8::INSTR", "10", "10", "10", "6221"))
        self.assertEqual(str(a.avtech_connect_button.cget("state")), "normal")
        for button in (a.avtech_disconnect_button, a.avtech_apply_button, a.avtech_apply_negative_button,
                       a.avtech_off_button):
            self.assertEqual(str(button.cget("state")), "disabled", str(button.cget("text")))
        self.assertEqual([str(b.cget("text")) for b in a.cidwm_scan_source_buttons], ["6221", "Avtech"])
        self.assertFalse(a.avtech_safety_var.get())

    def _boxes(self):
        found, pending = [], list(self.app.root.winfo_children())
        while pending:
            widget = pending.pop()
            if widget.winfo_class() == "EnglishMessageBox":
                found.append(widget)
            pending.extend(widget.winfo_children())
        return found

    @staticmethod
    def _button(box, name):
        pending = [box]
        while pending:
            widget = pending.pop()
            if widget.winfo_class() == "TButton" and widget.winfo_name() == name:
                return widget
            pending.extend(widget.winfo_children())
        return None

    def _ask(self, function, title, message, steps):
        """Run a message box with ``steps`` (ms, callback) scheduled, and a
        last resort that answers every open box, so a test never waits."""
        root = self.app.root
        jobs = [root.after(ms, callback) for ms, callback in steps]

        def answer_all():
            for box in self._boxes():
                for name in ("no", "cancel", "ok"):
                    button = self._button(box, name)
                    if button is not None:
                        button.invoke()
                        break

        jobs.append(root.after(10000, answer_all))
        try:
            return function(title, message, parent=root)
        finally:
            for job in jobs:
                root.after_cancel(job)

    def test_message_boxes_and_file_dialogs_are_in_english(self):
        """2026-10-12: Yes/No, not the Chinese Windows' buttons: our own
        message boxes, and Tk's own file dialogs, their text in English."""
        root = self.app.root
        if str(root.tk.call("tk", "windowingsystem")) != "x11":
            for command, target in (
                ("::tk_messageBox", "::tk::MessageBox"),
                ("::tk_getOpenFile", "::tk::dialog::file:: open"),
                ("::tk_getSaveFile", "::tk::dialog::file:: save"),
                ("::tk_chooseDirectory", "::tk::dialog::file::chooseDir::"),
            ):
                alias = root.tk.splitlist(root.tk.call("interp", "alias", "", command))
                self.assertEqual(" ".join(map(str, alias)), target, command)
        self.assertEqual(str(root.tk.call("::msgcat::mclocale")), "en")
        seen = []

        def read_and_answer():
            box = self._boxes()[0]
            seen.extend(str(self._button(box, name).cget("text")) for name in ("yes", "no"))
            self._button(box, "no").invoke()

        self.assertFalse(self._ask(app.messagebox.askyesno, "English dialogs",
                                   "Is this box in English?", [(300, read_and_answer)]))
        self.assertEqual(seen, ["Yes", "No"])
        self.assertEqual(self._boxes(), [], "closed")

    def test_a_box_over_an_open_box_leaves_both_answers_right(self):
        """Review, 2026-10-12: Tk's own box shares one window and one answer,
        so a second box closed the first and gave it the second's answer."""
        log = []

        def open_inner():
            log.append(("inner", app.messagebox.showinfo("Inner", "Over the open box",
                                                         parent=self.app.root)))

        def press(title, name):
            box = next(box for box in self._boxes() if box.title() == title)
            self._button(box, name).invoke()

        answer = self._ask(app.messagebox.askyesno, "Outer", "Start?", [
            (200, open_inner),
            (700, lambda: log.append(("open", sorted(box.title() for box in self._boxes())))),
            (800, lambda: press("Inner", "ok")),
            (1300, lambda: press("Outer", "yes")),
        ])
        self.assertTrue(answer, "the outer box's own answer")
        self.assertEqual(log, [("open", ["Inner", "Outer"]), ("inner", "ok")])

    def test_a_box_while_a_tcl_window_holds_the_grab(self):
        """Review, 2026-10-12: a Combobox's open list or Tk's file dialog is
        not a tkinter widget, so grab_current() raised KeyError and the box
        stayed on top for good.  It answers now and gives the grab back."""
        root = self.app.root
        root.tk.eval("toplevel .tcl_only_grab; wm geometry .tcl_only_grab 120x60+0+0")
        try:
            root.update()
            root.tk.call("grab", "set", ".tcl_only_grab")
            answer = self._ask(app.messagebox.askyesno, "Grab", "Answer?", [
                (300, lambda: self._button(self._boxes()[0], "yes").invoke()),
            ])
            self.assertTrue(answer)
            self.assertEqual(self._boxes(), [], "no box left behind")
            self.assertEqual(str(root.tk.call("grab", "current", root._w)), ".tcl_only_grab",
                             "the grab given back")
        finally:
            root.tk.call("grab", "release", ".tcl_only_grab")
            root.tk.eval("destroy .tcl_only_grab")

    def test_moke_6221_has_no_continuous_output_and_the_pulse_defaults(self):
        """2026-10-12: no AC/DC continuous output on CIDWM (MOKE-6221); the pulse
        output at the user's figure (2 mA, 50 us, 1 pulse, 100 V)."""
        a = self.app
        titles = set()
        pending = [a.k6221_tab]
        while pending:
            widget = pending.pop()
            pending.extend(widget.winfo_children())
            if widget.winfo_class() == "TLabelframe":
                titles.add(str(widget.cget("text")))
        self.assertNotIn("AC/DC continuous output and live readback", titles)
        self.assertIn("DC unipolar pulse output (0 A ↔ ±I, 50% duty)", titles)
        # 2026-10-12, CIDWM: MOKE magnet's difference display and ROI editing
        # (one set of variables: one camera), no pulse current scan.
        self.assertIn("MOKE difference display", titles)
        # 2026-10-12: no ROI editing and no live ROI plot; one Save images button.
        self.assertFalse({"MOKE ROI editing", "ROI mean intensity vs time (live)"} & titles)
        self.assertIn("Images", titles)
        self.assertIn("Camera settings", titles)
        self.assertNotIn("Pulse current scan and ROI acquisition", titles)
        self.assertEqual((len(a.difference_capture_buttons), len(a.objective_100x_marker_buttons)), (2, 2))
        self.assertEqual(len(a.difference_mode_buttons), 6)
        texts = []
        pending = [a.k6221_tab]
        while pending:
            widget = pending.pop()
            pending.extend(widget.winfo_children())
            if widget.winfo_class() == "TButton":
                texts.append(str(widget.cget("text")))
        self.assertNotIn("Clear curve", texts, "the magnet scan's curve: MOKE magnet only")
        self.assertIn("Save images", texts)
        for gone in ("Save difference raw image", "Save raw 16-bit", "Save current display", "Apply"):
            self.assertNotIn(gone, texts)
        # 2026-10-12: +I and -I pulses, the size from "Pulse current |I| (mA)".
        self.assertTrue({"Apply +I pulse", "Apply −I pulse"} <= set(texts))
        self.assertNotIn("Apply pulse output", texts)

        self.assertFalse(hasattr(a, "apply_k6221_continuous_output"))
        self.assertEqual((a.k6221_current_var.get(), a.k6221_width_var.get(), a.k6221_count_var.get(),
                          a.k6221_compliance_var.get()), ("2", "50", "1", "100"))
        # The live readback stays, in the pulse box.
        self.assertEqual(a.k6221_readback_output_var.get(), "OUTP: OFF")
        self.assertIsNotNone(a.k6221_compliance_state_label)

    def test_frequency_scan_rows_on_6221_iv_and_gate_vi(self):
        """2026-10-10: a Frequency scan row on 6221-IV and Gate-VI, off by
        default, the primes from 7 to 97 Hz; Gate-VI's needs an AC read
        current, as its Frequency Hz."""
        a = self.app
        for prefix in ("nonlocal_iv6221", "nonlocal_vi"):
            self.assertFalse(getattr(a, f"{prefix}_fscan_var").get())
            self.assertEqual((getattr(a, f"{prefix}_fstart_var").get(),
                              getattr(a, f"{prefix}_fstop_var").get()), ("7", "97"))
            self.assertEqual(getattr(a, f"{prefix}_fscan_info_var").get(), "Off: Frequency Hz only")
        self.addCleanup(a.nonlocal_vi_fscan_var.set, False)
        a.nonlocal_vi_fscan_var.set(True)
        self.assertEqual(a.nonlocal_vi_fscan_info_var.get(), "22 primes: 7–97 Hz")
        # 6221-IV's row is held while a measurement runs, as its lock-in choices.
        held = {widget for widget, _state in a.nonlocal_iv6221_choice_widgets}
        self.assertTrue(set(a.nonlocal_iv6221_fscan_widgets) <= held)
        states = lambda: {str(widget.cget("state")) for widget in a.nonlocal_vi_fscan_widgets}
        self.assertEqual(states(), {"normal"})
        self.addCleanup(a.nonlocal_vi_output_mode_var.set, "AC")
        a.nonlocal_vi_output_mode_var.set("DC")
        self.assertEqual(states(), {"disabled"})

    def test_gate_vi_loops_default_to_2(self):
        """2026-10-11: Loops on the Gate-VI scan, beside the frequency scan row."""
        a = self.app
        self.assertEqual(a.nonlocal_vi_loops_var.get(), "2")
        # The scan box at the user's figure (2026-10-11).
        self.assertEqual(
            tuple(getattr(a, f"nonlocal_vi_{name}_var").get() for name in (
                "start_voltage", "stop_voltage", "step_voltage", "pulse_width", "post_pulse",
                "gate_resistance", "sample_hz", "settle", "measure")),
            ("100", "-100", "10", "0.1", "0.5", "100", "1", "5", "30"))
        self.assertEqual(len(self.widgets("TEntry", a.nonlocal_vi_loops_var)), 1)
        self.assertEqual(len(self.widgets("TLabel", text="Loops:")), 1)

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
        self.assertEqual(a.nonlocal_mfli_tc_var.get(), "1")   # 1 s (user, 2026-10-14; was 0.1)
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
        self.assertEqual(a.nonlocal_vi_mfli_harmonic_var.get(), "1+2", "the Zurich default (user, 2026-10-09)")
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

    def test_every_lock_in_time_constant_box_starts_at_1_s(self):
        """User, 2026-10-14: "all default lock-in time constant change to
        1s".  The PPMS and RT-E Zurich TC, the Non-local rack's Zurich TC and
        STFMR's MFLI TC s and SR830 TC all start at 1 s (three were 0.1 s and
        the SR830's 100 ms)."""
        a = self.app
        for name, panel in (("PPMS", a.ppms_panel), ("SHV", a.rte_panel)):
            self.assertEqual(panel.zurich_timeconstant.get(), "1", name)
        self.assertEqual(a.nonlocal_mfli_tc_var.get(), "1")
        self.assertEqual(a.stfmr_lockin_mfli_timeconstant_var.get(), "1")
        self.assertEqual(a.stfmr_lockin_sr830_time_constant_var.get(), "1 s")


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
                "MOKE light", "MOKE motor", "MOKE magnet", "CIDWM",
                "MOKE-24", "Non-local", "PPMS", "SHV", "FMR", "STFMR"])
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
        # The Field and Current Start line with the wait before each reading
        # and the shortest duration (2026-10-14), at its longest: 100000
        # points, Settle s 86400 s and Acquire s 86400 s (about 493 px in
        # Arial 15 px and 590 px in DejaVu Sans, under the 505 px / 598 px
        # of the Stopped line below).  The wait shows at most five digits
        # (ppms_settle_status_text): whole seconds from 100 s, 3 significant
        # digits below, so a sub-second wait is at most "0.0864" (497 px /
        # 595 px).  Only a wait under 10 ms with Acquire s of 10 h or more
        # could be wider; a wait of 100000 s or more (once 7 x a 30 ks SR830
        # time constant) cannot occur at the user's 1 x TC, as Settle s is
        # at most 86400 s (user, 2026-10-14).
        "SHV Current: 100000 points, settle 86400 s; at least 4800024 h 00 min 00 s",
        "SHV Current: 100000 points, settle 0.0864 s; at least 2400026 h 24 min 02 s",
        # The default Field scan at 1 x TC: 199 x (1 s + 1 s) + 1 s (user,
        # 2026-10-14; "settle 7 s; at least 26 min 33 s" at 7 x TC).
        "SHV Field: 199 points, settle 1 s; at least 6 min 39 s",
        # A wait that differs from the one announced at Start, while it runs
        # (a time constant changed since Connect), at its widest: about 412
        # px in Arial, 485 px in DejaVu Sans (the same digits as the
        # "0.0864 s: 7 x 0.0123 s" it replaces; at 1 x TC the wait is the TC).
        "Each reading now waits 0.0864 s: 1 x 0.0864 s TC of the Zurich",
        # Every ending zeroes the 6221 and the KEPCO (user, 2026-10-18; the
        # outputs were held, and a Current scan's 6221 alone set to 0, until
        # then), so the finish lines say how that went.
        "Stopping; completed CSV points kept; KEPCO and 6221 zeroed next",
        "Completed; " + app.rte_outputs_text({"6221": "zeroed", "kepco": "zeroed"}),
        "Completed; " + app.rte_outputs_text({"6221": "zero FAILED: x", "kepco": "not connected"}),
        "Completed; " + app.rte_outputs_text({"6221": "zero FAILED: x", "kepco": "zero FAILED: y"}),
        # The Angle scan's (2026-10-13), at their longest: six significant
        # digits ({:g}) for the angles and the KEPCO current, 100000 points.
        "SHV Angle -359.995° to 359.995°, KEPCO -9.87654 A held; 100000 points",
        "Stopped; " + app.rte_outputs_text({"6221": "zeroed", "kepco": "zeroed"}, check=False)
        + "; stage stopped at -359.999°",
        "Stopped; " + app.rte_outputs_text({"6221": "zero FAILED: x", "kepco": "zeroed"}, check=False)
        + "; stage stopped at -359.999°",
        "Stopped; " + app.rte_outputs_text(None, check=False) + "; stage stopped at -359.999°",
        "Completed; " + app.rte_outputs_text({"6221": "zeroed", "kepco": "zeroed"}, check=False)
        + "; stage at +360.000°",
        # ABORT stopped it, STOP did not (review, 2026-10-18).
        "Stopped; " + app.rte_outputs_text({"6221": "zeroed", "kepco": "zeroed"}, check=False)
        + "; stage stopped at -359.999° (ABORT)",
        # A failure that also left an output not confirmed at zero says so
        # (review, 2026-10-18), the error shortened to keep the line.
        app.rte_failed_status_text(RuntimeError("6221 hit compliance; scan stopped and output turned off"),
                                   {"6221": "zero FAILED: x", "kepco": "zeroed"}),
        app.rte_failed_status_text(RuntimeError("VI_ERROR_TMO (-1073807339): Timeout expired before "
                                                "operation completed"),
                                   {"6221": "zero FAILED: x", "kepco": "zero FAILED: y"}),
        app.rte_failed_status_text(RuntimeError("LS475 field drifted during acquisition; point discarded"), None),
        # An Angle Start that did not start (review, 2026-10-17): the longest
        # fixed reason, and the widest refusal the stage check or a fix can
        # give, cut to 44 characters (about 478 px in Arial 15 px).
        app.rte_angle_not_started_text("an OFF button was pressed"),
        app.rte_angle_not_started_text(RuntimeError(
            "The Newmark NSC-A1's last stop was not confirmed, so it may still be turning: press Stop stage "
            "first (check the stage)")),
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
            if notebook.tab(index, "text") == "SHV":
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


# Named to run after PanelDefaultTests' message boxes and before TCRackTests,
# as RTEStatusRowTests does: it maps and destroys a program copy too.
class ReviewFixWidgetTests(unittest.TestCase):
    """Widgets and texts changed by the review of the merged areas (review,
    2026-10-18): the MOKE magnet status says the other KEPCO was only sent
    0 A, on one line in the lab's window; a STFMR Start cancelled at its
    Yes/No or folder leaves the instrument rows and Apply output unlocked."""

    @classmethod
    def setUpClass(cls):
        try:
            cls.root = tk.Tk()
        except tk.TclError as error:  # headless build server
            raise unittest.SkipTest(f"no display: {error}")
        cls.app = build_lab_app(app, cls.root)  # as the lab PC shows it
        cls.root.geometry(f"{LAB_WIDE}x{lab_window(cls.app)[1]}+0+0")
        cls.root.update()

    @classmethod
    def tearDownClass(cls):
        cls.app.closing = True
        cls.app.safe_close_hardware()
        cls.root.destroy()

    def select(self, text):
        notebook = self.app.notebook
        for index in range(notebook.index("end")):
            if notebook.tab(index, "text") == text:
                notebook.select(index)
        self.root.update()

    @staticmethod
    def labels_of(widget, variable):
        found = []
        for child in widget.winfo_children():
            try:
                if str(child.cget("textvariable")) == str(variable):
                    found.append(child)
            except tk.TclError:
                pass
            found.extend(ReviewFixWidgetTests.labels_of(child, variable))
        return found

    def test_the_moke_status_fits_with_a_kepco_sent_not_read_back(self):
        self.select("MOKE magnet")
        variable = self.app.magnet_status_var
        [label] = self.labels_of(self.root, variable)
        labels = ("Out-of-plane KEPCO", "In-plane KEPCO")
        sent = app.ZERO_SENT_NOT_READ_BACK
        font = tkinter.font.Font(font=label.cget("font") or "TkDefaultFont")
        try:
            variable.set("Ready")
            self.root.update()
            one_line = label.winfo_height()
            for results, prefix in (({labels[0]: "zeroed", labels[1]: sent}, "Completed; "),
                                    ({labels[1]: "zeroed", labels[0]: sent}, "Stopped; "),
                                    ({labels[0]: "zero FAILED: x", labels[1]: sent}, "Completed; ")):
                message = prefix + app.scan_end_outputs_text(results, labels, together="both KEPCOs")
                with self.subTest(message=message):
                    self.assertIn("0 A sent, not read back", message)
                    variable.set(message)
                    self.root.update()
                    self.assertLessEqual(font.measure(message), label.winfo_width(), message)
                    self.assertEqual(label.winfo_height(), one_line, message)
        finally:
            variable.set("Ready")

    def test_a_cancelled_stfmr_start_unlocks_the_rows_and_apply(self):
        self.select("STFMR")
        a = self.app
        kepco = a.stfmr_lockin_controllers["kepco"]
        connect = a.stfmr_lockin_connect_buttons["gauss"]
        apply = a.stfmr_lockin_apply_output_button
        during = {}

        def cancelled_start():
            # As a Start's dialogs leave the page: locked while it starts,
            # then the Yes/No answered No.
            a._update_stfmr_lockin_ui()
            self.root.update()
            during.update(connect=str(connect.cget("state")), apply=str(apply.cget("state")))

        kepco.instrument = object()             # "connected", nothing sent
        try:
            a._run_stfmr_lockin_start_step(cancelled_start)
            self.root.update()
            self.assertEqual(during, {"connect": "disabled", "apply": "disabled"})
            self.assertEqual(str(connect.cget("state")), "normal")
            self.assertEqual(str(apply.cget("state")), "normal")
        finally:
            kepco.instrument = None
            a._update_stfmr_lockin_ui()


class ScanBoxFrequencyModeTests(unittest.TestCase):
    """The PPMS scan box's Frequency and Frequency+Current modes (user,
    2026-10-18): both in the Mode list, which keeps its width; Frequency's
    Start / End in Hz with the Step box empty; Frequency+Current's "Outer
    frequency Hz:" row where the outer temperature row sits, with its prime
    count; and the box no wider in either mode, in the 1101 px window and at
    2000 px."""

    @classmethod
    def setUpClass(cls):
        try:
            cls.root = tk.Tk()
        except tk.TclError as error:  # headless build server
            raise unittest.SkipTest(f"no display: {error}")
        cls.app = build_lab_app(app, cls.root)  # as the lab PC shows it
        notebook = next(w for w in cls.app.__dict__.values()
                        if isinstance(w, tk.ttk.Notebook))
        for index in range(notebook.index("end")):
            if notebook.tab(index, "text") == "PPMS":
                notebook.select(index)
        cls.panel = cls.app.ppms_panel
        cls.scan = cls.panel.ppms_field_scan_start_button.master.master
        cls.root.geometry(f"{LAB_WIDE}x{lab_window(cls.app)[1]}+0+0")
        cls.root.update()

    @classmethod
    def tearDownClass(cls):
        cls.app.closing = True
        cls.app.safe_close_hardware()
        cls.root.destroy()

    def choose(self, mode, width=None):
        self.panel.ppms_simple_mode.set(mode)
        self.panel.ppms_scan_mode_combo.event_generate("<<ComboboxSelected>>")
        if width is not None:
            self.root.geometry(f"{width}x{lab_window(self.app)[1]}+0+0")
        for _ in range(3):
            self.root.update()

    def tearDown(self):
        self.choose("Field")

    def unit_label(self):
        name = str(self.panel.ppms_simple_unit)
        return next(w for w in self.scan.winfo_children()
                    if w.winfo_class() == "TLabel" and str(w.cget("textvariable")) == name)

    def test_both_modes_are_in_the_list_and_fit_it(self):
        combo = self.panel.ppms_scan_mode_combo
        self.assertEqual(tuple(str(v) for v in combo.cget("values")), app.PPMS_SCAN_MODES)
        self.assertEqual(int(combo.cget("width")), 19)
        font = tkinter.font.nametofont("TkTextFont")
        room = combo.winfo_width() - (combo.winfo_reqwidth() - int(combo.cget("width")) * font.measure("0"))
        for mode in app.PPMS_SCAN_MODES:
            self.assertLessEqual(font.measure(mode), room, mode)

    def test_frequency_has_start_and_end_in_hz_and_no_step(self):
        self.choose("Frequency")
        self.assertEqual([v.get() for v in self.panel.ppms_simple_range], ["7", "97", ""])
        self.assertEqual(self.panel.ppms_simple_unit.get(), "Hz (primes)")
        label = self.unit_label()
        font = tkinter.font.nametofont(str(label.cget("font")) or "TkDefaultFont")
        self.assertLessEqual(font.measure("Hz (primes)"), label.winfo_width(), "the unit shows in full")
        self.assertEqual(str(self.panel.ppms_scan_step_entry.cget("state")), "disabled")
        self.assertFalse(self.panel.ppms_frequency_range_frame.winfo_ismapped())
        self.assertEqual(self.panel.ppms_plot_axis, "frequency")
        self.assertEqual(self.panel.ppms_acquisition_time_var.get(), "1")

    def test_frequency_current_has_the_outer_frequency_row(self):
        self.choose("Frequency+Current")
        self.assertEqual([v.get() for v in self.panel.ppms_simple_range], ["0.01", "1", "0.01"])
        self.assertEqual(self.panel.ppms_simple_unit.get(), "mA RMS")
        frame = self.panel.ppms_frequency_range_frame
        self.assertTrue(frame.winfo_ismapped())
        self.assertEqual(int(frame.grid_info()["row"]), 4, "where the outer temperature row sits")
        texts = [str(w.cget("text")) for w in frame.winfo_children() if w.winfo_class() == "TLabel"]
        self.assertEqual(texts[:3], ["Outer frequency Hz:", "Start", "End"])
        self.assertEqual([v.get() for v in self.panel.ppms_frequency_scan_range], ["7", "97"])
        self.assertEqual(self.panel.ppms_frequency_scan_note_var.get(), "22 primes: 7–97 Hz")
        # Shown in full, or cut to the row's room with "…", never widening it.
        shown = texts[3]
        self.assertTrue(shown == "22 primes: 7–97 Hz"
                        or (shown.endswith("…") and "22 primes: 7–97 Hz".startswith(shown[:-1].rstrip())), shown)
        self.assertEqual(self.panel.ppms_plot_axis, "current")
        try:
            self.panel.ppms_frequency_scan_range[0].set("24")
            self.panel.ppms_frequency_scan_range[1].set("28")
            self.root.update()
            self.assertEqual(self.panel.ppms_frequency_scan_note_var.get(), "No prime from 24 to 28 Hz; widen the range")
        finally:
            self.panel.ppms_frequency_scan_range[0].set("7")
            self.panel.ppms_frequency_scan_range[1].set("97")
        self.choose("Current+Temperature")
        self.assertFalse(frame.winfo_ismapped())

    def test_the_box_is_no_wider_and_nothing_sticks_out(self):
        """At 2000 px nothing in the scan box sticks out of it, in any mode.
        The 1101 px window already cuts the box's right-hand cells in every
        mode (gui_checks notes it); there the new modes cut no more than
        Field and Current+Temperature do."""
        tab = self.panel.ppms_tab
        sizes, overs = {}, {}
        for width in (1101, LAB_WIDE):
            for mode in ("Field", "Current+Temperature", "Frequency", "Frequency+Current"):
                with self.subTest(width=width, mode=mode):
                    self.choose(mode, width)
                    sizes[(width, mode)] = (self.scan.winfo_reqwidth(), self.scan.winfo_reqheight())
                    right = tab.winfo_rootx() + tab.winfo_width()
                    box_right = self.scan.winfo_rootx() + self.scan.winfo_width()
                    self.assertLessEqual(box_right, right + 1, "the scan box stays on the page")
                    over = 0
                    pending = list(self.scan.winfo_children())
                    while pending:
                        widget = pending.pop()
                        pending.extend(widget.winfo_children())
                        if widget.winfo_ismapped():
                            over = max(over, widget.winfo_rootx() + widget.winfo_width() - box_right)
                    overs[(width, mode)] = over
                    if width == LAB_WIDE:
                        self.assertLessEqual(over, 1, "something sticks out of the scan box at 2000 px")
            with self.subTest(width=width):
                self.assertLessEqual(sizes[(width, "Frequency")][0], sizes[(width, "Field")][0])
                self.assertLessEqual(sizes[(width, "Frequency+Current")][0], sizes[(width, "Current+Temperature")][0])
                self.assertLessEqual(abs(sizes[(width, "Frequency+Current")][1]
                                         - sizes[(width, "Current+Temperature")][1]), 2,
                                     "the outer row costs the plots no more height than the temperature row")
                self.assertLessEqual(overs[(width, "Frequency")], overs[(width, "Field")], overs)
                self.assertLessEqual(overs[(width, "Frequency+Current")], overs[(width, "Current+Temperature")], overs)


# Named to sort last: unittest runs a module's classes in name order, and this
# one maps and destroys a whole program copy.  Before PanelDefaultTests'
# message boxes that is the order that crashed Tk's focus_force on the Linux
# CI (2026-10-14), so nothing in this module runs after it (user, 2026-10-14).
class TCRackTests(unittest.TestCase):
    """The PPMS rack (user, 2026-10-14): the Zurich row says "TC (s)", as on
    RT-E, and one 'SR830 TC' dropdown for the three SR830s sits beside Macro,
    under the Keithley 2400 and left of SR830-3, in the window the program
    opens with and in the 2000 px one, without widening the rack."""

    @classmethod
    def setUpClass(cls):
        try:
            cls.root = tk.Tk()
        except tk.TclError as error:  # headless build server
            raise unittest.SkipTest(f"no display: {error}")
        cls.app = build_lab_app(app, cls.root)  # as the lab PC shows it
        notebook = next(w for w in cls.app.__dict__.values()
                        if isinstance(w, tk.ttk.Notebook))
        for index in range(notebook.index("end")):
            if notebook.tab(index, "text") == "PPMS":
                notebook.select(index)
        cls.root.update()

    @classmethod
    def tearDownClass(cls):
        cls.app.closing = True
        cls.app.safe_close_hardware()
        cls.root.destroy()

    def show(self, width):
        self.root.geometry(f"{width}x{lab_window(self.app)[1]}+0+0")
        self.root.update()

    def test_the_zurich_row_says_tc_s(self):
        texts = []
        pending = [self.app.ppms_panel.ppms_tab]
        while pending:
            widget = pending.pop()
            pending.extend(widget.winfo_children())
            try:
                texts.append(str(widget.cget("text")))
            except Exception:
                pass
        self.assertIn("TC (s)", texts)
        self.assertNotIn("Time constant s", texts)
        self.assertEqual(texts.count("SR830 TC"), 1)

    def test_the_sr830_tc_dropdown_sits_beside_macro_in_both_windows(self):
        panel = self.app.ppms_panel
        combo = panel.ppms_sr830_time_constant_combo
        row = combo.master
        macro = next(w for w in row.winfo_children() if isinstance(w, tk.ttk.Button))
        label = next(w for w in row.winfo_children() if isinstance(w, tk.ttk.Label))
        self.assertEqual((str(macro.cget("text")), str(label.cget("text"))), ("Macro", "SR830 TC"))
        self.assertEqual(panel.ppms_sr830_time_constant_var.get(), "1 s")
        self.assertEqual(tuple(str(v) for v in combo.cget("values")),
                         tuple(app.STFMR_LOCKIN_SR830_TIME_CONSTANTS))
        self.assertEqual(str(combo.cget("state")), "readonly")
        keithley = panel.ppms_device_resource_widgets["2400"].master
        sr830_3 = panel.ppms_device_resource_widgets["830_c"].master
        # Narrower than the Keithley row above it, so the column keeps its width.
        self.assertLess(row.winfo_reqwidth(), keithley.winfo_reqwidth())
        for width in (lab_window(self.app)[0], LAB_WIDE):
            with self.subTest(width=width):
                self.show(width)
                self.assertTrue(combo.winfo_ismapped())
                self.assertLess(macro.winfo_rootx() + macro.winfo_width(), label.winfo_rootx())
                self.assertLessEqual(combo.winfo_rootx() + combo.winfo_width(), sr830_3.winfo_rootx())
                self.assertGreater(combo.winfo_rooty(), keithley.winfo_rooty(), "under the 2400")
                self.assertLess(abs(combo.winfo_rooty() - sr830_3.winfo_rooty()), 10, "SR830-3's row")

    def test_the_sr830_tc_box_stops_the_mouse_wheel_before_its_class(self):
        """Tk's combobox class steps on a wheel turn and reports a selection,
        which would rewrite OFLT on every connected SR830.  The box's own
        wheel bindings (they return "break", test_ppms_sr830_tc) run before
        the class's.  No wheel event is generated here: Tk may route one
        through the focus window, and focus handling after a destroyed
        program copy is what crashed the Linux CI (user, 2026-10-14)."""
        combo = self.app.ppms_panel.ppms_sr830_time_constant_combo
        tags = combo.bindtags()
        self.assertLess(tags.index(str(combo)), tags.index("TCombobox"))
        for sequence in ("<MouseWheel>", "<Button-4>", "<Button-5>"):
            self.assertIn('== "break"} break', combo.bind(sequence), sequence)


if __name__ == "__main__":
    unittest.main()
