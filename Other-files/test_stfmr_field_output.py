"""STFMR manual KEPCO field output, and the narrowed address fields.

The panel mirrors the FMR page's "KEPCO 4886 BOP 20-10 field output". It is
exercised here inside the fully built application, with the real KEPCO
controller writing to a simulated BIT 4886, so the safety interlocks are
tested end to end rather than against a stub.
"""
import tkinter as tk
import tkinter.font
import unittest
from unittest import mock

from test_ppms_motor_debug import app


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
        cls.root.geometry("1900x1000+0+0")
        cls.app = app.CombinedControlApp(cls.root)
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

    def test_output_panel_is_left_of_the_connections(self):
        frames = self.page_frames(self.a.stfmr_lockin_tab)
        panel = frames["KEPCO 4886 BOP 20-10 field output"]
        connections = frames["STFMR instrument connections"]
        self.assertIs(panel.master, connections.master, "they share one top row")
        self.assertEqual(int(panel.master.grid_info()["row"]), 0)
        self.assertEqual(int(frames["STFMR field-sweep parameters"].grid_info()["row"]), 1)
        outer = panel.master.master
        self.assertEqual(int(outer.rowconfigure(4)["weight"]), 1, "plots keep the spare height")
        self.assertEqual(max(int(w.grid_info()["row"]) for w in outer.winfo_children()), 4)

    def test_connections_are_one_compact_column(self):
        rows = {key: int(entry.master.grid_info()["row"])
                for key, entry in self.a.stfmr_lockin_resource_widgets.items()}
        self.assertEqual(rows, {"kepco": 0, "gauss": 1, "smb": 2, "830": 3, "mfli": 4})
        self.assertEqual({int(e.master.grid_info()["column"])
                          for e in self.a.stfmr_lockin_resource_widgets.values()}, {0})

    def arranged(self, width, tab_name, widgets):
        self.root.geometry(f"{width}x900+0+40")
        notebook = next(w for w in self.a.__dict__.values() if isinstance(w, tk.ttk.Notebook))
        for index in range(notebook.index("end")):
            if notebook.tab(index, "text") == tab_name:
                notebook.select(index)
        for _ in range(3):
            self.root.update()
        return getattr(widgets["kepco"].master.master.master, "_pair_side_by_side", None)

    def test_top_row_stacks_when_the_window_is_narrow(self):
        """The default split-screen window is 826 px on a 1920 display."""
        try:
            for tab_name, widgets in (("STFMR", self.a.stfmr_lockin_resource_widgets),
                                      ("FMR", self.a.stfmr_device_resource_widgets)):
                with self.subTest(page=tab_name):
                    self.assertFalse(self.arranged(820, tab_name, widgets), "narrow: stacked")
                    self.assertTrue(self.arranged(1420, tab_name, widgets), "wide: side by side")
        finally:
            self.arranged(1900, "STFMR", self.a.stfmr_lockin_resource_widgets)

    def test_nothing_is_clipped_in_a_narrow_window(self):
        try:
            for tab_name, tab, widgets in (
                    ("STFMR", self.a.stfmr_lockin_tab, self.a.stfmr_lockin_resource_widgets),
                    ("FMR", self.a.stfmr_tab, self.a.stfmr_device_resource_widgets)):
                self.arranged(820, tab_name, widgets)
                right = tab.winfo_rootx() + tab.winfo_width()
                over = [w for w in descendants(tab)
                        if w.winfo_ismapped() and w.winfo_rootx() + w.winfo_width() > right + 1]
                self.assertEqual(over, [], f"{tab_name}: widgets past the right edge")
        finally:
            self.arranged(1900, "STFMR", self.a.stfmr_lockin_resource_widgets)

    def test_address_fields_are_narrow_and_status_text_fits(self):
        font = tkinter.font.nametofont("TkDefaultFont")
        for page, widgets, statuses in (
            ("STFMR", self.a.stfmr_lockin_resource_widgets, self.a.stfmr_lockin_connection_vars),
            ("FMR", self.a.stfmr_device_resource_widgets, self.a.stfmr_device_connection_vars),
        ):
            for key, entry in widgets.items():
                with self.subTest(page=page, key=key):
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
