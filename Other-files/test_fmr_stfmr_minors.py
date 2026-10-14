"""FMR and STFMR minor fixes from the audit of 2026-10-13, one check each.

No Tk and no instrument: the real methods run on a bare app with stand-ins
(widgets that remember their state, a fake 4886 card, fake VISA sessions,
futures completed by hand).  Every message box a test can reach is mocked."""
from concurrent.futures import Future
import threading
import types
import unittest
from unittest import mock

import test_fmr_stfmr_review as review
from test_nonlocal_keithley import Var, bare_app
from test_ppms_motor_debug import app


class Widget:
    """A button or entry: remembers the state it was last given."""

    def __init__(self, state="normal", zurich=False):
        self.state = state
        self.zurich_chooser = zurich

    def configure(self, **options):
        if "state" in options:
            self.state = options["state"]


class Later:
    """An executor whose futures complete only when the test says so."""

    def __init__(self):
        self.jobs = []

    def submit(self, function, *args):
        future = Future()
        self.jobs.append((future, function, args))
        return future

    def finish(self):
        future, function, args = self.jobs.pop(0)
        future.set_result(function(*args))


KEYS = ("kepco", "gauss", "smb", "830", "mfli")


def lockin_app(meter="MFLI", connected=("kepco", "gauss", "smb", "mfli")):
    """The STFMR page as Connect leaves it: the given rows connected."""
    a = bare_app()
    a.closing = a.stfmr_lockin_closed = a.stfmr_lockin_running = False
    a.stfmr_lockin_gauss_busy = False
    a.stfmr_lockin_rows = []
    a.stfmr_lockin_live_rows = [{"meter": meter, "x_v": 1e-6}]
    a.stfmr_lockin_meter_var = Var(meter)
    a.stfmr_lockin_controllers = {
        key: types.SimpleNamespace(connected=key in connected, connect=mock.Mock())
        for key in ("kepco", "gauss", "smb", "830")
    }
    a.stfmr_lockin_mfli_controller = types.SimpleNamespace(mfli_connected="mfli" in connected)
    a.stfmr_lockin_resource_vars = {key: Var(f"GPIB0::{n}::INSTR") for n, key in enumerate(KEYS)}
    a.stfmr_lockin_connection_vars = {
        key: Var("Connected" if key in connected else "Not connected") for key in KEYS
    }
    a.stfmr_lockin_connect_buttons = {
        key: Widget("disabled" if key in connected else "normal") for key in KEYS
    }
    a.stfmr_lockin_disconnect_buttons = {
        key: Widget("normal" if key in connected else "disabled") for key in KEYS
    }
    a.stfmr_lockin_resource_widgets = {
        key: Widget("disabled" if key in connected else
                    "readonly" if key == "mfli" else "normal", zurich=key == "mfli")
        for key in KEYS
    }
    for name in ("start", "stop", "save", "read_gauss"):
        setattr(a, f"stfmr_lockin_{name}_button", Widget())
    a.stfmr_lockin_meter_combo = Widget("readonly")
    a.instrument_connection_indicators = {}
    a.stfmr_lockin_reference_indicator = None
    a.stfmr_lockin_overload_indicator = None
    a.stfmr_lockin_voltage_var, a.stfmr_lockin_reference_status_var = Var(), Var()
    a.stfmr_lockin_overload_var, a.stfmr_lockin_field_var = Var(), Var()
    a._redraw_stfmr_lockin_live_plots = mock.Mock()
    a._redraw_stfmr_lockin_plot = mock.Mock()
    a.stfmr_lockin_executor = Later()
    a.post_ui = lambda callback: callback()
    return a


def row_states(a, key):
    return (a.stfmr_lockin_connect_buttons[key].state,
            a.stfmr_lockin_disconnect_buttons[key].state,
            a.stfmr_lockin_resource_widgets[key].state)


class ConnectDuringSweepTests(unittest.TestCase):
    """minor #24: connecting the other lock-in during a sweep switched the
    Meter selector and blanked the live X/Y/theta plots."""

    def test_connect_does_nothing_while_a_sweep_runs(self):
        a = lockin_app()
        a.stfmr_lockin_running = True
        with mock.patch.object(app.messagebox, "showerror") as error:
            a.connect_stfmr_lockin_device("830")
        error.assert_not_called()
        self.assertEqual(a.stfmr_lockin_executor.jobs, [], "nothing sent to the SR830")
        self.assertEqual(a.stfmr_lockin_meter_var.get(), "MFLI")
        self.assertEqual(a.stfmr_lockin_connection_vars["830"].get(), "Not connected")
        self.assertEqual(len(a.stfmr_lockin_live_rows), 1, "live rows kept")

    def test_a_connect_finishing_during_a_sweep_keeps_the_meter(self):
        a = lockin_app()
        a.connect_stfmr_lockin_device("830")       # pressed just before Start
        self.assertEqual(row_states(a, "830"), ("disabled", "disabled", "disabled"))
        a.stfmr_lockin_running = True              # Start
        a._update_stfmr_lockin_ui()
        self.assertEqual(row_states(a, "830"), ("disabled", "disabled", "disabled"),
                         "a row still connecting keeps its disabled buttons")
        a.stfmr_lockin_controllers["830"].connected = True
        a.stfmr_lockin_executor.finish()
        a.stfmr_lockin_controllers["830"].connect.assert_called_once_with("GPIB0::3::INSTR")
        self.assertEqual(a.stfmr_lockin_connection_vars["830"].get(), "Connected")
        self.assertEqual(a.stfmr_lockin_meter_var.get(), "MFLI", "the sweep's meter")
        self.assertEqual(len(a.stfmr_lockin_live_rows), 1, "live rows kept")
        a._redraw_stfmr_lockin_live_plots.assert_not_called()

    def test_connecting_while_idle_still_selects_that_lock_in(self):
        a = lockin_app()
        a.connect_stfmr_lockin_device("830")
        a.stfmr_lockin_controllers["830"].connected = True
        a.stfmr_lockin_executor.finish()
        self.assertEqual(a.stfmr_lockin_meter_var.get(), "SR830")
        self.assertEqual(a.stfmr_lockin_live_rows, [])
        self.assertEqual(row_states(a, "830"), ("disabled", "normal", "disabled"))

    def test_the_rows_are_locked_for_the_sweep_and_restored_after(self):
        a = lockin_app()
        a.stfmr_lockin_running = True
        a._update_stfmr_lockin_ui()
        for key in KEYS:
            self.assertEqual(row_states(a, key), ("disabled",) * 3, key)
        a.stfmr_lockin_running = False             # the sweep ends
        a._update_stfmr_lockin_ui()
        for key in ("kepco", "gauss", "smb", "mfli"):
            self.assertEqual(row_states(a, key), ("disabled", "normal", "disabled"), key)
        self.assertEqual(row_states(a, "830"), ("normal", "disabled", "normal"))
        a.stfmr_lockin_connection_vars["mfli"].set("Not connected")
        a.stfmr_lockin_mfli_controller.mfli_connected = False
        a._update_stfmr_lockin_ui()
        self.assertEqual(row_states(a, "mfli"), ("normal", "disabled", "readonly"),
                         "the Zurich list is choosable again, not typable")

    def test_a_disconnect_in_progress_keeps_its_buttons_disabled(self):
        """Review, 2026-10-13: a row refresh while the SR830 disconnect was
        queued (a Read, a Meter change) enabled its Disconnect again."""
        a = lockin_app(connected=("kepco", "gauss", "smb", "830"), meter="SR830")
        a.stfmr_lockin_controllers["830"].disconnect = mock.Mock()
        a.disconnect_stfmr_lockin_device("830")
        self.assertEqual(a.stfmr_lockin_connection_vars["830"].get(), "Disconnecting…")
        a._update_stfmr_lockin_ui()
        self.assertEqual(row_states(a, "830")[:2], ("disabled", "disabled"))
        a.stfmr_lockin_controllers["830"].connected = False
        with mock.patch.object(app.messagebox, "showerror") as error:
            a.stfmr_lockin_executor.finish()
        error.assert_not_called()
        a.stfmr_lockin_controllers["830"].disconnect.assert_called_once_with()
        self.assertEqual(a.stfmr_lockin_connection_vars["830"].get(), "Not connected")
        a._update_stfmr_lockin_ui()
        self.assertEqual(row_states(a, "830"), ("normal", "disabled", "normal"))


class FakeSession:
    """A VISA session that answers *IDN? as the given instrument."""

    def __init__(self, identity):
        self.identity, self.writes = identity, []

    def query(self, command):
        assert command == "*IDN?", command
        return self.identity

    def write(self, command):
        self.writes.append(command)

    def close(self):
        pass


class LeaseLabelTests(unittest.TestCase):
    """minor #25: a connection conflict called the FMR tab's 4886, 475 and
    PNA "STFMR", the other tab."""

    def connect(self, controller, address, identity=""):
        manager = mock.Mock()
        manager.open_resource.return_value = FakeSession(identity)
        with mock.patch.object(app, "pyvisa", object()), \
                mock.patch.object(app, "acquire_shared_visa_manager", return_value=manager), \
                mock.patch.object(app, "release_shared_visa_manager"):
            controller.connect(address)
        self.addCleanup(app.release_instrument_lease, address, controller)

    def conflict(self, holder, address, identity=""):
        self.connect(holder, address, identity)
        other = app.STFMRKepco4886Controller(lambda message: None)    # the STFMR tab's
        with mock.patch.object(app, "pyvisa", object()):
            with self.assertRaises(RuntimeError) as raised:
                other.connect(address)
        return str(raised.exception)

    def test_the_fmr_4886_is_named_fmr(self):
        message = self.conflict(
            app.STFMRKepco4886Controller(lambda message: None, owner_scope="FMR"), "GPIB0::21::INSTR")
        self.assertIn('already connected by "FMR / KEPCO BOP 4886 20-10"', message)
        self.assertIn('then connect from "STFMR / KEPCO BOP 4886 20-10"', message)

    def test_the_fmr_475_is_named_fmr(self):
        gauss = app.STFMRGauss475Controller(lambda message: None)
        gauss.owner_scope = "FMR"                  # as the FMR tab sets it
        message = self.conflict(gauss, "GPIB0::22::INSTR", "LSCI,MODEL475,0,1.0")
        self.assertIn('already connected by "FMR / Lake Shore 475 gaussmeter"', message)

    def test_the_pna_is_named_fmr(self):
        message = self.conflict(app.STFMRPNAFMRController(lambda message: None),
                                "GPIB0::23::INSTR", "Keysight Technologies,N5227B,MY1,A.1")
        self.assertIn('already connected by "FMR / Keysight PNA"', message)

    def test_the_stfmr_tab_keeps_its_name(self):
        message = self.conflict(app.STFMRKepco4886Controller(lambda message: None), "GPIB0::24::INSTR")
        self.assertIn('already connected by "STFMR / KEPCO BOP 4886 20-10"', message)

    def test_each_tab_gives_its_controllers_its_own_name(self):
        # The controllers are made in the Tk window build, which these tests
        # never run.
        with open(app.__file__, encoding="utf-8") as source:
            text = source.read()
        self.assertIn('"kepco": STFMRKepco4886Controller(self.thread_log, owner_scope="FMR")', text)
        self.assertIn('self.stfmr_controllers["gauss"].owner_scope = "FMR"', text)
        self.assertIn("self.rte_controllers['kepco'].owner_scope = 'RT-E'", text)


def inputs_app(start="-1", stop="1", step="0.5", loop="1"):
    a = bare_app()
    a._stfmr_lockin_connected = lambda _key: True
    a.stfmr_lockin_manual_safety_var = Var(True)
    a.stfmr_lockin_meter_var = Var("MFLI")
    for name, value in (("frequency_start", "5"), ("frequency_stop", "10"),
                        ("frequency_step", "1"),
                        # P start / P stop / P step replace Power dBm
                        # (user, 2026-10-14).
                        ("power_start", "6"), ("power_stop", "6"), ("power_step", "1"),
                        ("mod_start", "317.3"), ("mod_end", "317.3"),
                        # Settle s and Acquire s replace Wait/pt (user, 2026-10-14).
                        ("settle", "1"), ("acquire", "2"), ("limit", "10"), ("loop", loop),
                        ("start", start), ("stop", stop), ("step", step)):
        setattr(a, f"stfmr_lockin_{name}_var", Var(value))
    return a


class ShortLoopTests(unittest.TestCase):
    """minor #26: one or two currents per loop switched the 4886 and the RF on
    and saved nothing (the first two points of each loop are discarded).

    User, 2026-10-13: a fixed current (Start A = Stop A) is allowed again; its
    loops have no current jump, so only the first two readings of each
    frequency are discarded, and only a run that would save nothing is
    refused."""

    def test_two_currents_per_loop_are_refused(self):
        for loop in ("1", "3"):
            with self.subTest(loop=loop):
                a = inputs_app("-1", "-0.5", loop=loop)
                with self.assertRaisesRegex(ValueError, "gives 2 current points.*at least 3 current points"):
                    a._stfmr_lockin_inputs()

    def test_a_fixed_current_needs_three_readings(self):
        for loop in ("1", "2"):
            with self.subTest(loop=loop):
                a = inputs_app("2", "2", loop=loop)
                with self.assertRaisesRegex(ValueError, f"fixed current.*Loop {loop} would save nothing.*at least 3"):
                    a._stfmr_lockin_inputs()
        a = inputs_app("2", "2", loop="3")
        points, *_rest = a._stfmr_lockin_inputs()
        self.assertEqual(points, [2.0] * 3)
        self.assertEqual(a.stfmr_lockin_points_per_loop, 1)

    def test_three_currents_per_loop_still_start(self):
        a = inputs_app("-1", "0", loop="2")
        points, frequencies, *_rest = a._stfmr_lockin_inputs()
        self.assertEqual(points, [-1.0, -0.5, 0.0] * 2)
        self.assertEqual(a.stfmr_lockin_points_per_loop, 3)
        self.assertFalse(app.CombinedControlApp._stfmr_should_discard_startup_point(2, 3),
                         "each loop keeps its third point")

    def test_start_refuses_before_any_dialog_file_or_output(self):
        a = inputs_app("-1", "-0.5", loop="3")
        a.closing = a.stfmr_lockin_closed = a.stfmr_lockin_running = False
        a.root = None
        a.select_csv_save_directory = mock.Mock()
        a._configure_stfmr_lockin_frequency = mock.Mock()
        with mock.patch.object(app.messagebox, "showerror") as error, \
                mock.patch.object(app.messagebox, "askyesno") as ask:
            a.start_stfmr_lockin_scan()
        error.assert_called_once()
        self.assertIn("at least 3 current points", error.call_args.args[1])
        ask.assert_not_called()
        a.select_csv_save_directory.assert_not_called()
        a._configure_stfmr_lockin_frequency.assert_not_called()
        self.assertFalse(a.stfmr_lockin_running)


class LoopTextTests(unittest.TestCase):
    """minor #28: the Loop box was an IntVar: cleared, it gave a Tcl error;
    2.5 quietly became 2."""

    def loops(self, text):
        a = inputs_app(loop=text)
        points, *_rest = a._stfmr_lockin_inputs()
        return len(points) // a.stfmr_lockin_points_per_loop

    def test_whole_numbers_are_read(self):
        for text, expected in (("1", 1), ("3", 3), (" 2 ", 2), ("2.0", 2), (4, 4)):
            with self.subTest(text=text):
                self.assertEqual(self.loops(text), expected)

    def test_anything_else_is_refused_with_a_plain_message(self):
        for text in ("", "2.5", "abc", "0", "-1", "nan", "inf"):
            with self.subTest(text=text):
                with self.assertRaisesRegex(ValueError, "^Loop count must be a whole number of at least 1$"):
                    self.loops(text)

    def test_start_shows_the_plain_message(self):
        a = inputs_app(loop="")
        a.closing = a.stfmr_lockin_closed = a.stfmr_lockin_running = False
        a.root = None
        a.select_csv_save_directory = mock.Mock()
        with mock.patch.object(app.messagebox, "showerror") as error, \
                mock.patch.object(app.messagebox, "askyesno") as ask:
            a.start_stfmr_lockin_scan()
        self.assertEqual(error.call_args.args[1], "Loop count must be a whole number of at least 1")
        ask.assert_not_called()
        a.select_csv_save_directory.assert_not_called()

    def test_the_box_holds_text(self):
        # Created in the Tk window build, which these tests never run.
        with open(app.__file__, encoding="utf-8") as source:
            text = source.read()
        self.assertIn('self.stfmr_lockin_loop_var = tk.StringVar(value="1")', text)
        self.assertNotIn("stfmr_lockin_loop_var = tk.IntVar", text)


def fmr_app(card):
    a = bare_app()
    a.closing = False
    a.root = None
    a.stfmr_fmr_running = a.stfmr_frequency_running = False
    a.stfmr_controllers = {"kepco": review.connected_4886(card)}
    review.kepco_vars(a)
    a.stfmr_safety_var = Var(True)
    a.stfmr_output_mode_var, a.stfmr_output_target_var, a.stfmr_output_limit_var = (
        Var("Current"), Var("2"), Var("5"))
    a.stfmr_fmr_rf_off_var = Var(False)
    a._set_stfmr_scan_busy = lambda busy: None
    return a


def finish(a, kind, **options):
    setattr(a, f"stfmr_{kind}_running", True)
    setattr(a, f"stfmr_{kind}_after_id", None)
    setattr(a, f"stfmr_{kind}_rows", [])
    setattr(a, f"stfmr_{kind}_status_var", Var())
    setattr(a, f"stfmr_{kind}_save_button", mock.Mock())
    with mock.patch.object(app.messagebox, "showerror") as error, \
            mock.patch.object(app.messagebox, "showwarning") as warning:
        getattr(a, f"_finish_stfmr_{kind}_scan")(**options)
    warning.assert_not_called()
    return getattr(a, f"stfmr_{kind}_status_var").get(), error


class FmrScanEndBeforeAnyWriteTests(unittest.TestCase):
    """review: an FMR scan or frequency sweep that ended before any 4886
    write showed a made-up 'Current, 0 A, OUTP: OFF' and '4886 zeroed/OFF'."""

    def test_stopped_or_failed_before_the_first_write(self):
        for kind in ("fmr", "frequency"):
            for options in ({"stopped": True}, {"error": RuntimeError("PNA did not answer")}):
                with self.subTest(kind=kind, options=options):
                    card = review.Card4886()
                    a = fmr_app(card)
                    status, error = finish(a, kind, **options)
                    self.assertEqual(card.writes, [], "nothing sent")
                    self.assertEqual(review.readout(a), ["Mode: --", "I: -- A", "V: -- V", "OUTP: --"])
                    self.assertNotIn("zeroed/OFF", status)
                    if "stopped" in options:
                        self.assertIn("4886 not used", status)
                    else:
                        error.assert_called_once()

    def test_after_a_write_the_scan_end_still_reports_off(self):
        for kind in ("fmr", "frequency"):
            with self.subTest(kind=kind):
                card = review.Card4886()
                a = fmr_app(card)
                a.apply_stfmr_kepco_output()      # the scan's output is on
                status, _error = finish(a, kind, stopped=True)
                self.assertEqual(card.writes[-2:], ["CURR 0.000000", "OUTP OFF"])
                self.assertEqual(review.readout(a), ["Mode: Current", "I: +0 A", "V: +0 V", "OUTP: OFF"])
                self.assertIn("4886 zeroed/OFF", status)


class FmrDisconnectReadoutTests(unittest.TestCase):
    """review: Disconnect showed 'OUTP: OFF' for a 4886 sent nothing."""

    def disconnect(self, a):
        a.stfmr_device_disconnect_buttons = {"kepco": mock.Mock()}
        a.stfmr_device_connection_vars = {"kepco": Var("Connected")}
        a._set_stfmr_device_ui = mock.Mock()
        with mock.patch.object(app.messagebox, "showerror") as error, \
                mock.patch.object(app, "release_shared_visa_manager"):
            a.disconnect_stfmr_device("kepco")
        a._set_stfmr_device_ui.assert_called_once_with("kepco", False)
        self.assertFalse(a.stfmr_controllers["kepco"].connected)
        return error

    def test_nothing_sent_shows_unknown(self):
        card = review.Card4886()
        a = fmr_app(card)
        error = self.disconnect(a)
        error.assert_not_called()
        self.assertEqual(card.writes, [])
        self.assertEqual(review.readout(a), ["Mode: --", "I: -- A", "V: -- V", "OUTP: --"])

    def test_an_output_switched_off_at_disconnect_shows_off(self):
        card = review.Card4886()
        a = fmr_app(card)
        a.apply_stfmr_kepco_output()
        self.disconnect(a)
        self.assertEqual(card.writes[-2:], ["CURR 0.000000", "OUTP OFF"])
        self.assertEqual(a.stfmr_kepco_output_var.get(), "OUTP: OFF")

    def test_a_failed_off_at_disconnect_is_flagged(self):
        card = review.Card4886()
        a = fmr_app(card)
        a.apply_stfmr_kepco_output()
        card.stall = {"OUTP OFF"}
        error = self.disconnect(a)
        error.assert_called_once()
        self.assertEqual(a.stfmr_kepco_output_var.get(), "OUTP: NOT CONFIRMED OFF")


if __name__ == "__main__":
    unittest.main()
