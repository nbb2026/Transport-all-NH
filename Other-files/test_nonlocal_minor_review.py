"""Non-local minor fixes (audit/review, 2026-10-13), each run through the
changed code.

- Rack rows: "Connecting…" / "Disconnecting…" stay on the row until that
  Connect or Disconnect finishes; every controls update wrote "Not
  connected" / "Connected" over them at once.
- Frequency Hz is read only when it is used (an AC read current without the
  frequency scan): a 6221-IV or Gate-VI frequency scan, or a DC Gate-VI run,
  was refused for an empty entry.
- The Gate-VI start confirmation says "low-pass filter order 2", not
  "2th-order low-pass".
- The Gate-VI pulse gate above the 1 nA range: a measured |R| above the bound
  is kept and can pass a Pulse gate above 4.9 GΩ; the block message no longer
  calls the value "the most it can show".

No Tk and no instrument: stand-in controllers and widgets record what the
page asks of them, and every dialog is mocked."""
import csv
import math
import tempfile
import threading
import types
import unittest
from concurrent.futures import Future
from pathlib import Path
from unittest import mock

from test_nonlocal_6221_iv import FakeSource, page
from test_nonlocal_gate_vi_harmonics import FakeAC, gate_page, reading
from test_nonlocal_keithley import Var, bare_app
from test_nonlocal_pulse_gate_6221 import BOUND_OHM, measure
from test_ppms_motor_debug import app


class RecordingVar(Var):
    def __init__(self, value=""):
        super().__init__(value)
        self.history = []

    def set(self, value):
        super().set(value)
        self.history.append(value)


class QueuedExecutor:
    """Holds each job until the test runs it, as the page's single worker
    thread does while a slow connect or drain is still running."""

    def __init__(self):
        self.jobs = []

    def submit(self, function, *args):
        future = Future()
        self.jobs.append((future, function, args))
        return future

    def run_next(self):
        future, function, args = self.jobs.pop(0)
        try:
            future.set_result(function(*args))
        except BaseException as error:
            future.set_exception(error)


RACK_KEYS = ("2410", "6221", "8", "9", "mfli", "2182")


def rack_page():
    """The rack with the real controls update and summary refresh; widgets
    are mocks.  The Keithley 2400 is connected, the rest not."""
    a = bare_app()
    a.nonlocal_vi_running = a.nonlocal_vi_busy = False
    a.nonlocal_keithley_task = None
    a.closing = False
    a.root = None
    a.nonlocal_vi_device_connection_vars = {key: RecordingVar("Not connected") for key in RACK_KEYS}
    a.nonlocal_vi_device_connect_buttons = {key: mock.Mock() for key in RACK_KEYS}
    a.nonlocal_vi_device_disconnect_buttons = {key: mock.Mock() for key in RACK_KEYS}
    for name in ("nonlocal_vi_output_mode_combo", "nonlocal_vi_model_combo",
                 "nonlocal_vi_frequency_entry", "nonlocal_vi_start_button",
                 "nonlocal_vi_stop_button", "nonlocal_vi_off_button"):
        setattr(a, name, mock.Mock())
    for name in ("_refresh_nonlocal_vi_model_labels", "_update_nonlocal_keithley_controls",
                 "_set_nonlocal_vi_indicator", "_set_mfli_lock_state", "_redraw_nonlocal_vi_plots",
                 "_update_k6221_controls", "_update_keithley_controls"):
        setattr(a, name, mock.Mock())
    a._nonlocal_vi_device_conflict = lambda key: False
    a.nonlocal_vi_output_mode_var = Var("AC")
    a.nonlocal_vi_status_var, a.nonlocal_vi_connection_var = Var(), Var()
    a.nonlocal_vi_output_var, a.nonlocal_iv6221_source_var = Var(), Var()
    a.nonlocal_iv6221_zurich_range_var = Var()
    for name in ("nonlocal_vi_safety_var", "nonlocal_resistance_safety_var",
                 "nonlocal_iv_safety_var", "nonlocal_iv6221_safety_var"):
        setattr(a, name, Var(True))
    a.nonlocal_vi_measurement_enabled_vars = {key: Var(False) for key in ("8", "9", "mfli", "2182")}
    a.nonlocal_vi_lockin_resource_vars = {"8": Var("GPIB0::8::INSTR"), "9": Var("GPIB0::9::INSTR")}

    keithley = types.SimpleNamespace(connected=True, model="2400")

    def disconnect():
        keithley.connected = False

    keithley.disconnect = disconnect
    a.nonlocal_vi_2410_controller = keithley
    ac = types.SimpleNamespace(connected=False, source_connected=False, connected_lockin_keys=(),
                               mfli_connected=False, nanovoltmeter_connected=False,
                               fail_connect=False)

    def connect_lockin(key, resource):
        if ac.fail_connect:
            raise RuntimeError("VI_ERROR_TMO")
        ac.connected_lockin_keys = (key,)
        return "Stanford_Research_Systems,SR830"

    ac.connect_lockin = connect_lockin
    a.nonlocal_vi_ac_controller = ac
    a.nonlocal_vi_executor = QueuedExecutor()
    a.post_ui = lambda callback: callback()
    return a


class RackRowTests(unittest.TestCase):
    def setUp(self):
        patcher = mock.patch.object(app, "messagebox")
        self.messagebox = patcher.start()
        self.addCleanup(patcher.stop)

    def test_connecting_stays_until_the_connection_finishes(self):
        a = rack_page()
        row = a.nonlocal_vi_device_connection_vars["8"]
        a.connect_nonlocal_vi_device("8")
        # connect ran the controls update (and its summary refresh) itself.
        self.assertEqual(row.get(), "Connecting…")
        self.assertNotIn("Not connected", row.history, "never overwritten while connecting")
        a._update_nonlocal_vi_controls()   # as another page does meanwhile
        self.assertEqual(row.get(), "Connecting…")
        self.assertEqual(a.nonlocal_vi_device_connection_vars["9"].get(), "Not connected")
        self.assertEqual(a.nonlocal_vi_device_connection_vars["2410"].get(), "Connected")
        a.nonlocal_vi_executor.run_next()
        self.assertEqual(row.get(), "Connected")
        self.assertIsNone(a.nonlocal_vi_pending_row)
        self.assertFalse(a.nonlocal_vi_busy)
        self.messagebox.showerror.assert_not_called()

    def test_a_failed_connection_ends_not_connected(self):
        a = rack_page()
        a.nonlocal_vi_ac_controller.fail_connect = True
        a.connect_nonlocal_vi_device("8")
        self.assertEqual(a.nonlocal_vi_device_connection_vars["8"].get(), "Connecting…")
        a.nonlocal_vi_executor.run_next()
        self.assertEqual(a.nonlocal_vi_device_connection_vars["8"].get(), "Not connected")
        self.assertIsNone(a.nonlocal_vi_pending_row)
        self.messagebox.showerror.assert_called_once()

    def test_disconnecting_stays_until_the_drain_and_off_finish(self):
        a = rack_page()
        row = a.nonlocal_vi_device_connection_vars["2410"]
        a.disconnect_nonlocal_vi_device("2410")
        self.assertEqual(row.get(), "Disconnecting…")
        self.assertNotIn("Connected", row.history, "never overwritten while disconnecting")
        a._update_nonlocal_vi_controls()
        self.assertEqual(row.get(), "Disconnecting…")
        a.nonlocal_vi_executor.run_next()
        self.assertEqual(row.get(), "Not connected")
        self.assertEqual(a.nonlocal_vi_output_var.get(), "Keithley 2400: disconnected (OFF sent)")
        self.assertIsNone(a.nonlocal_vi_pending_row)
        self.messagebox.showerror.assert_not_called()


# ------------------------------------------- Frequency Hz only when used
def iv6221_page(frequency, tick, fstart="7", fstop="31"):
    a = page(FakeSource(), frequency=frequency)
    a.nonlocal_iv6221_fscan_var = Var(tick)
    a.nonlocal_iv6221_fstart_var, a.nonlocal_iv6221_fstop_var = Var(fstart), Var(fstop)
    return a


def without_start_time(settings):
    return {key: value for key, value in settings.items() if key != "started"}


class IV6221FrequencyTests(unittest.TestCase):
    def test_a_frequency_scan_ignores_an_empty_or_invalid_frequency_hz(self):
        expected = without_start_time(iv6221_page("17.3", True)._parse_nonlocal_iv6221_settings())
        self.assertEqual(expected["frequency_hz"], 7.0, "the first frequency scanned")
        for entry in ("", "abc", "nan"):
            with self.subTest(entry=entry):
                settings = iv6221_page(entry, True)._parse_nonlocal_iv6221_settings()
                self.assertEqual(without_start_time(settings), expected)

    def test_frequency_hz_is_still_checked_where_it_is_used(self):
        with self.assertRaisesRegex(ValueError, "6221-IV settings must be valid numbers"):
            iv6221_page("", False)._parse_nonlocal_iv6221_settings()
        with self.assertRaisesRegex(ValueError, "finite"):
            iv6221_page("inf", False)._parse_nonlocal_iv6221_settings()
        # Set 6221 always uses it.
        with self.assertRaisesRegex(ValueError, "6221-IV settings must be valid numbers"):
            iv6221_page("", True)._parse_nonlocal_iv6221_lockins()


def gate_vi_page(frequency, tick=False, mode="AC"):
    a = gate_page("1", "2")
    a.nonlocal_vi_frequency_var.set(frequency)
    a.nonlocal_vi_fscan_var = Var(tick)
    a.nonlocal_vi_fstart_var, a.nonlocal_vi_fstop_var = Var("7"), Var("31")
    if mode == "DC":
        # DC needs the 2182A alone, as the page asks.
        a.nonlocal_vi_ac_controller = DCFakeAC()
        a.nonlocal_vi_output_mode_var.set("DC")
        a.nonlocal_vi_measurement_enabled_vars["8"].set(False)
        a.nonlocal_vi_use_mfli_var.set(False)
        a.nonlocal_vi_use_2182_var.set(True)
    return a


class DCFakeAC(FakeAC):
    """The 6221 in DC with only the 2182A: configure returns no frequency,
    as the real one does in DC."""

    def __init__(self):
        super().__init__()
        self.connected_lockin_keys = ()
        self.mfli_connected = False
        self.nanovoltmeter_connected = True

    def configure(self, current, frequency, compliance, lockin_configs, marker_line,
                  mfli_config=None, use_nanovoltmeter=False, sample_hz=1.0, output_mode="AC"):
        self.calls.append(("configure", frequency, output_mode, use_nanovoltmeter))
        return math.nan, {"2182": {"meter_type": "Keithley 2182A", "resource": "GPIB0::7::INSTR",
                                   "channel": "voltage"}}

    def read_samples(self, frequency, keys):
        return {key: reading(2e-6) for key in keys}


class GateVIFrequencyTests(unittest.TestCase):
    def test_a_frequency_scan_ignores_an_empty_frequency_hz(self):
        expected = gate_vi_page("17.3", tick=True)._parse_nonlocal_vi_settings()
        self.assertEqual(expected["frequency_hz"], 7.0)
        for entry in ("", "abc"):
            with self.subTest(entry=entry):
                self.assertEqual(gate_vi_page(entry, tick=True)._parse_nonlocal_vi_settings(), expected)

    def test_a_dc_run_does_not_read_frequency_hz(self):
        for entry in ("", "abc", "17.3"):
            with self.subTest(entry=entry):
                settings = gate_vi_page(entry, mode="DC")._parse_nonlocal_vi_settings()
                self.assertEqual(settings["output_mode"], "DC")
                self.assertTrue(math.isnan(settings["frequency_hz"]), "DC has no frequency")
                self.assertFalse(settings["frequency_scan"])
                self.assertEqual(len(settings["frequencies"]), 1)
                self.assertEqual(settings["measurement_keys"], ("2182",))

    def test_an_ac_run_without_the_scan_still_checks_it(self):
        with self.assertRaisesRegex(ValueError, "must be valid numbers"):
            gate_vi_page("")._parse_nonlocal_vi_settings()
        with self.assertRaisesRegex(ValueError, "must be finite values"):
            gate_vi_page("nan")._parse_nonlocal_vi_settings()
        self.assertEqual(gate_vi_page("17.3")._parse_nonlocal_vi_settings()["frequency_hz"], 17.3)

    def test_a_dc_scan_with_an_empty_frequency_hz_runs_and_plots_one_curve(self):
        a = gate_vi_page("", mode="DC")
        settings = a._parse_nonlocal_vi_settings()
        settings["sample_interval_s"] = 0.01
        a.nonlocal_vi_stop_event = threading.Event()
        a.nonlocal_vi_interlock = app.NonLocalVIInterlock()
        a.nonlocal_vi_data_lock = threading.Lock()
        a.nonlocal_vi_time_rows, a.nonlocal_vi_summary_rows = [], []
        a.nonlocal_vi_pulse_rows, a.nonlocal_vi_current_rows, a.nonlocal_vi_resistance_rows = [], [], []
        a.post_ui = lambda callback: callback()
        a._redraw_nonlocal_vi_plots = mock.Mock()
        a._publish_reference_sync = mock.Mock()
        a._publish_nonlocal_vi_samples = mock.Mock()
        a.nonlocal_vi_progress = mock.Mock()
        a.nonlocal_vi_eta_var, a.nonlocal_vi_status_var, a.nonlocal_vi_output_var = Var(), Var(), Var()
        directory = Path(tempfile.mkdtemp())
        a._nonlocal_vi_scan_worker(settings, directory)
        self.assertEqual([c[2] for c in a.nonlocal_vi_ac_controller.calls if c[0] == "configure"], ["DC"])
        self.assertEqual(a.nonlocal_vi_2410_controller.apply_voltage_pulse.call_count,
                         len(settings["voltage_scan"]))
        with (directory / "VI_summary.csv").open(encoding="utf-8-sig") as file:
            summary = list(csv.DictReader(file))
        self.assertEqual(len(summary), len(settings["voltage_scan"]), "one 2182A row per pulse")
        # The plots split curves by frequency: a DC run is one curve, as before.
        after = [row for row in a.nonlocal_vi_resistance_rows if row[3] != "initial_gate"]
        self.assertEqual(len(after), len(settings["voltage_scan"]))
        self.assertEqual(len({row[4] for row in after}), 1)
        self.assertEqual(len({row["set_frequency_hz"] for row in a.nonlocal_vi_summary_rows}), 1)
        log = (directory / "VI_parameters.log").read_text(encoding="utf-8")
        self.assertIn("frequency_hz=nan", log, "no frequency in DC")


# ---------------------------------------- the Gate-VI start confirmation
class StartConfirmationTests(unittest.TestCase):
    def test_the_zurich_filter_order_reads_correctly(self):
        a = gate_vi_page("17.3")
        a.nonlocal_keithley_task = None
        a.root = None
        a.nonlocal_vi_safety_var = Var(True)
        a.nonlocal_vi_output_directory = Path(tempfile.mkdtemp())
        a.nonlocal_vi_status_var = Var()
        with mock.patch.object(app, "filedialog") as filedialog, \
                mock.patch.object(app, "messagebox") as messagebox:
            filedialog.askdirectory.return_value = str(a.nonlocal_vi_output_directory)
            messagebox.askyesno.return_value = False   # declined: nothing starts
            a.start_nonlocal_vi_scan()
        messagebox.showwarning.assert_not_called()
        messagebox.askyesno.assert_called_once()
        text = messagebox.askyesno.call_args.args[1]
        self.assertIn(f"low-pass filter order {app.NONLOCAL_LOCKIN_FILTER_ORDER}", text)
        self.assertNotIn("th-order", text)
        self.assertEqual(list(a.nonlocal_vi_output_directory.iterdir()), [], "the run folder removed")


# ------------------------------------------ the pulse gate above 1 nA range
class GateAboveRangeTests(unittest.TestCase):
    def setUp(self):
        # Without the 7-time-constant read settle (user, 2026-10-14): Gate-VI
        # counts 1 s for its SR830s, which would wait 7 s per point here.
        patcher = mock.patch.object(app, "LOCKIN_SETTLE_TAUS", 0.0)
        patcher.start()
        self.addCleanup(patcher.stop)

    def run_scan(self, gate_megohm, result):
        a = gate_page("1", "2")
        a.nonlocal_vi_gate_resistance_var.set(gate_megohm)
        a.nonlocal_vi_2410_controller.measure_resistance.return_value = result
        settings = a._parse_nonlocal_vi_settings()
        settings["sample_interval_s"] = 0.01
        a.nonlocal_vi_stop_event = threading.Event()
        a.nonlocal_vi_interlock = app.NonLocalVIInterlock()
        a.nonlocal_vi_data_lock = threading.Lock()
        a.nonlocal_vi_time_rows, a.nonlocal_vi_summary_rows = [], []
        a.nonlocal_vi_pulse_rows, a.nonlocal_vi_current_rows, a.nonlocal_vi_resistance_rows = [], [], []
        a.post_ui = lambda callback: callback()
        a._redraw_nonlocal_vi_plots = mock.Mock()
        a._publish_reference_sync = mock.Mock()
        a.nonlocal_vi_progress = mock.Mock()
        a.nonlocal_vi_eta_var, a.nonlocal_vi_status_var, a.nonlocal_vi_output_var = Var(), Var(), Var()
        a._nonlocal_vi_scan_worker(settings, Path(tempfile.mkdtemp()))
        return a, settings

    def test_a_measured_value_above_the_bound_can_pass_a_gate_above_it(self):
        # 2e10 Ω read at the 5 V limit (0.25 nA): kept, over range, and above
        # a 5000 MΩ gate, so the pulses are given (the comment and README
        # said such a gate could never pass).
        result, _meter, _sources = measure(2e10)
        self.assertTrue(result["over_range"])
        self.assertGreater(5e9, BOUND_OHM)
        self.assertGreater(result["resistance_ohm"], 5e9)
        a, settings = self.run_scan("5000", result)
        self.assertEqual(a.nonlocal_vi_2410_controller.apply_voltage_pulse.call_count,
                         len(settings["voltage_scan"]))


if __name__ == "__main__":
    unittest.main()
