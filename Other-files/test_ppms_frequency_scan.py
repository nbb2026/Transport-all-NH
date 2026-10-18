"""PPMS page, Frequency and Frequency+Current scans (user, 2026-10-18: "add
frequency and frequency+current scan in the PPMS module").

The frequencies are the whole-number primes from Start to End Hz (no step), as
Non-local's frequency scan.  Frequency+Current runs the whole current sweep at
each frequency.  No Tk window and no instrument: fake SCPI instruments answer,
and a fake clock moves only when the scan waits.
"""
import csv
import math
import tempfile
import threading
import types
import unittest
from pathlib import Path
from unittest import mock

from test_nonlocal_keithley import Var
from test_ppms_motor_debug import app
from test_ppms_sr830_tc import build_ppms_panel


class Log(list):
    """The instruments' shared log; ``hook`` sees each entry as it is
    added (a Stop pressed at that step)."""

    def __init__(self):
        super().__init__()
        self.hook = None

    def append(self, entry):
        super().append(entry)
        if self.hook is not None:
            self.hook(entry)


class Clock:
    """time.monotonic for a test: it moves only when the scan waits."""

    def __init__(self):
        self.now = 1000.0

    def __call__(self):
        return self.now


class ClockEvent:
    """The scan's stop event.  Each wait is logged and moves the clock on;
    ``on_wait`` may set the event (a Stop pressed during that wait)."""

    def __init__(self, clock, log):
        self.clock, self.log, self.flag, self.on_wait = clock, log, False, None

    def is_set(self):
        return self.flag

    def set(self):
        self.flag = True

    def clear(self):
        self.flag = False

    def wait(self, seconds):
        self.log.append(("wait", round(float(seconds), 9)))
        if self.on_wait is not None:
            self.on_wait(float(seconds))
        self.clock.now += float(seconds)
        return self.flag


class Fake6221:
    """The 6221's sine as the PPMS page drives it: *RST and ABOR stop it,
    INIT starts it, and every command and query goes to the log."""

    def __init__(self, log):
        self.log = log
        self.output, self.frequency, self.amplitude = 1, 317.3, math.sqrt(2) * 1e-3
        self.compliance, self.range = 50.0, 0.1
        self.on_write = None

    def write(self, command):
        self.log.append(("6221", command))
        name, _, value = command.partition(" ")
        if command in ("*RST", "SOUR:WAVE:ABOR", "OUTP OFF"):
            self.output = 0
        if command == "*RST":
            self.frequency, self.amplitude = 1000.0, 0.0
        elif name == "SOUR:WAVE:FREQ":
            self.frequency = float(value)
        elif name == "SOUR:WAVE:AMPL":
            self.amplitude = float(value)
        elif name == "CURR:COMP":
            self.compliance = float(value)
        elif name == "SOUR:CURR:RANG":
            self.range = float(value)
        elif command == "SOUR:WAVE:INIT":
            self.output = 1
        if self.on_write is not None:
            self.on_write(command)

    def query(self, command):
        if command != "SYST:ERR?":
            self.log.append(("6221?", command))
        return {
            "SYST:ERR?": '0,"No error"', "OUTP:LTE?": "0", "OUTP?": str(self.output),
            "SOUR:WAVE:FREQ?": f"{self.frequency:.12g}", "SOUR:WAVE:AMPL?": f"{self.amplitude:.12e}",
            "CURR:COMP?": f"{self.compliance:g}", "SOUR:CURR:RANG?": f"{self.range:.12e}",
            "STAT:MEAS:COND?": "0",
        }[command]

    def safe_output_off(self):
        self.write("SOUR:WAVE:ABOR")
        self.write("OUTP OFF")


class FakeSR830:
    """An SR830 locked to the 6221's marker.  Its UNLK bit latches: the
    first LIAS? after the sine stopped or changed reads 8, as the real
    lock-in reports a lost reference.  ``never_locks_at`` makes it read a
    wrong reference frequency there."""

    def __init__(self, log, source, address, harmonic):
        self.log, self.source, self.address, self.harmonic = log, source, address, harmonic
        self.seen = (source.output, source.frequency)
        self.never_locks_at = None

    def reference(self):
        if not self.source.output:
            return 0.0
        if self.never_locks_at is not None and math.isclose(self.source.frequency, self.never_locks_at):
            return self.source.frequency * 1.5
        return self.source.frequency

    def write(self, command):
        self.log.append((f"sr830_{self.address}", command))
        name, _, value = command.partition(" ")
        if name == "HARM":
            self.harmonic = int(value)

    def query(self, command):
        if command != "*ESR?":
            self.log.append((f"sr830_{self.address}?", command))
        if command == "LIAS?":
            now = (self.source.output, self.source.frequency)
            changed, self.seen = now != self.seen or not self.source.output, now
            return "8" if changed else "0"
        frequency = self.reference()
        return {
            "*ESR?": "0", "HARM?": str(self.harmonic), "FMOD?": "0",
            "RSLP?": str(app.PPMS_SR830_REFERENCE_SLOPE), "OFLT?": "10",
            "FREQ?": f"{frequency:.12g}",
            "SNAP? 1,2,3,4,9": f"{1e-6 * self.address},0,{1e-6 * self.address},0,{frequency:.12g}",
        }[command]


class FakeZurich:
    """The Zurich on the PPMS page: its ExtRef PLL needs one read to follow
    a new frequency (``instant`` locks it at once, for a scan that does not
    wait for it).  ``never_locks`` keeps it unlocked.

    ``stale_reads`` is LabOne 18.12's case: no lock flag, and after a change
    that many reads still report the frequency it followed before, valid
    whenever that is within 0.5 % of the one expected."""

    def __init__(self, log, source):
        self.log, self.source = log, source
        self.lock = threading.RLock()
        self.mfli_connected, self.mfli_config = True, {"timeconstant_s": 1.0}
        self.following = None
        self.never_locks = self.instant = False
        self.stale_reads, self.held, self.pll_hz = 0, 0, source.frequency

    def configure_mfli(self, *args, **options):
        self.log.append(("zurich", "configure_mfli"))

    def configure_second_harmonic(self, *args, **options):
        self.log.append(("zurich", "configure_second_harmonic"))

    def configure_third_harmonic(self, *args, **options):
        self.log.append(("zurich", "configure_third_harmonic"))

    def disable_unused_ppms_demods(self, *args):
        self.log.append(("zurich", "disable_unused_ppms_demods"))

    def read_mfli(self, expected):
        self.log.append(("zurich?", "read_mfli", float(expected)))
        state = (self.source.output, self.source.frequency)
        if self.stale_reads:
            if state != self.following:
                self.following, self.held = state, self.stale_reads
            if self.held:
                self.held -= 1
            else:
                self.pll_hz = self.source.frequency if self.source.output else 0.0
            valid = math.isclose(self.pll_hz, expected, rel_tol=5e-3, abs_tol=0.1)
            return {"X": 2e-6, "Y": 0.0, "R": 2e-6, "phase_deg": 0.0, "reference_frequency_hz": self.pll_hz,
                    "reference_valid": valid, "extref_locked": None,
                    "status_text": "LabOne 18.12 judges LOCK by reference-frequency convergence",
                    "input_overload": False, "input_range_v": 0.01, "auto_range": ""}
        locked = (not self.never_locks and self.source.output and (self.following == state or self.instant)
                  and math.isclose(self.source.frequency, expected, rel_tol=5e-3, abs_tol=0.1))
        self.following = state
        return {"X": 2e-6, "Y": 0.0, "R": 2e-6, "phase_deg": 0.0,
                "reference_frequency_hz": self.source.frequency if self.source.output else 0.0,
                "reference_valid": bool(locked), "extref_locked": bool(locked),
                "status_text": "OK" if locked else "Trigger In 1 not locked",
                "input_overload": False, "input_range_v": 0.01, "auto_range": ""}

    def read_stfmr_mfli(self, expected, auto_range=False, initial=False, attempts=0):
        return self.read_mfli(expected)

    def read_mfli_four_demods(self):
        self.log.append(("zurich?", "four_demods"))
        sample = {"zurich_demod1_enabled": True}
        for index in range(1, 5):
            sample.update({f"zurich_demod{index}_x_v": 2e-6 / index, f"zurich_demod{index}_y_v": 0.0,
                           f"zurich_demod{index}_r_v": 2e-6 / index, f"zurich_demod{index}_theta_deg": 0.0,
                           f"zurich_demod{index}_harmonic": index, f"zurich_demod{index}_input": 0})
        return sample


def controller(label, instrument, resource):
    return types.SimpleNamespace(label=label, instrument=instrument, resource_name=resource,
                                 lock=threading.RLock(), connected=True,
                                 safe_output_off=getattr(instrument, "safe_output_off", lambda: None))


class Motor:
    """The PPMS rotator, at rest at the fixed angle: no move is needed."""

    def __init__(self, _active):
        self.on_read = None

    def read(self):
        return {"angle_deg": 26.0, "status": "AtTarget"}

    def move_and_verify(self, *args, **options):
        raise AssertionError("a frequency scan at the fixed angle needs no move")


class Rig:
    """A PPMS page (the real PPMSPanel methods) on fake instruments, the
    6221 already running from a Set I at 317.3 Hz."""

    def __init__(self, test, mode, frequencies, currents, zurich=True, preserve_on_stop=True):
        self.test = test
        self.log = Log()
        self.clock = Clock()
        page = app.PPMSPanel.__new__(app.PPMSPanel)
        page.parent_app = types.SimpleNamespace()   # nothing borrowed from a main page
        page.closing = False
        page.root = None
        page.ppms_field_scan_stop_event = self.stop = ClockEvent(self.clock, self.log)
        page.ppms_scan_preserve_on_stop = preserve_on_stop
        self.source = Fake6221(self.log)
        self.sr830 = {"830_a": FakeSR830(self.log, self.source, 8, 1), "830_c": FakeSR830(self.log, self.source, 10, 3)}
        page.ppms_instrument_controllers = {
            "6221": controller("Keithley 6221", self.source, "GPIB0::30::INSTR"),
            "830_a": controller("SR830-1", self.sr830["830_a"], "GPIB0::8::INSTR"),
            "830_c": controller("SR830-3", self.sr830["830_c"], "GPIB0::10::INSTR"),
        }
        page.ppms_sr830_range_states = {key: types.SimpleNamespace(sensitivity_index=17, clear_latch_pending=False)
                                        for key in self.sr830}
        page._ppms_autorange_sr830 = lambda *args, **options: 0.0
        self.zurich = FakeZurich(self.log, self.source)
        page.zurich_controller = self.zurich
        page.zurich_closed = False
        page.zurich_scan_enabled = zurich
        page.zurich_scan_configured = False
        page.zurich_scan_harmonic, page.zurich_scan_reference, page.zurich_scan_tau = "1", "Trigger In 1", 1.0
        page._display_zurich_sample = lambda sample: self.log.append(("display", "zurich"))
        remote = types.SimpleNamespace(backend="MultiPyVu", active=object())
        remote.set_temperature = lambda target, rate, approach: self.log.append(("ppms", "set_temperature", target))
        remote.set_field = lambda *args: test.fail("a frequency scan never commands the field")
        page.ppms_environment_controller = remote
        page._read_ppms_live = lambda: dict(field_oe=1234.0, field_status="Persistent",
                                            temperature_k=300.0, temperature_status="Stable")
        page._apply_ppms_environment_readback = lambda data: None
        page.ppms_last_readback = None
        page.post_ui = lambda callback: callback()
        page.thread_log = lambda text: self.log.append(("log", text))
        for name in ("ppms_field_scan_status_var", "ppms_transport_status_var", "ppms_field_scan_eta_var",
                     "ppms_current_live_var"):
            setattr(page, name, Var(""))
        page.ppms_field_scan_progress = mock.Mock()
        page._redraw_ppms_transport_plot = lambda: None
        page.ppms_scan_context = {}
        page.ppms_field_scan_rows = []
        page.ppms_field_scan_started_at = self.clock.now
        self.folder = tempfile.TemporaryDirectory()
        test.addCleanup(self.folder.cleanup)
        page.ppms_live_csv_path = Path(self.folder.name) / "scan.csv"
        page.ppms_live_csv_fields = None
        active = tuple(self.sr830)
        settings = app.validate_ppms_transport_scan_settings(
            currents[0], (frequencies or (317.3,))[0], 50.0, 1, 2, active, allow_no_lockins=zurich, harmonic_c=3)
        settings.update(nested={"angle": (26.0,), "temperature": (300.0,)}, currents=tuple(currents),
                        scan_mode=mode, acquisition_s=1.0, temperature_rate=10.0, temperature_approach=1,
                        slowdown=14, harmonic_pass=1)
        if frequencies is not None:
            settings["frequencies"] = tuple(frequencies)
        page.ppms_field_scan_total = len(currents) * len(frequencies or (1,))
        self.page, self.settings = page, settings

    def run(self):
        """The scan as Start runs it: its one harmonic pass through
        _ppms_scan_with_final_field, which zeroes the 6221 at every end
        (user, 2026-10-18)."""
        settings = dict(self.settings)
        settings["harmonic_passes"] = (dict(self.settings),)
        with mock.patch.object(app.time, "monotonic", self.clock), \
                mock.patch.object(app.time, "sleep", lambda _s: None), \
                mock.patch.object(app, "PPMSMotorDebug", Motor):
            self.page._ppms_scan_with_final_field([(1, 1, 1, 1234.0)], 100.0, 0.0, 1, settings)

    def rows(self):
        with self.page.ppms_live_csv_path.open(encoding="utf-8-sig", newline="") as handle:
            return list(csv.DictReader(handle))

    def header(self):
        with self.page.ppms_live_csv_path.open(encoding="utf-8-sig", newline="") as handle:
            return next(csv.reader(handle))

    def index(self, entry, start=0):
        return self.log.index(entry, start)

    def stop_at(self, entry, occurrence=1):
        """Press Stop (or "I off", with preserve_on_stop False) when the
        log gets ``entry`` for the ``occurrence``-th time."""
        seen = []

        def hook(added):
            if added == entry:
                seen.append(added)
                if len(seen) == occurrence:
                    self.stopped_at = len(self.log)
                    self.stop.set()
        self.log.hook = hook

    def assert_switched_off(self):
        """Switched off at the end, and read back as off (review, 2026-10-18),
        by the zeroing every ending has (user, 2026-10-18)."""
        sent = [entry for entry in self.log if entry[0] in ("6221", "6221?")]
        self.test.assertEqual(sent[-3:], [("6221", "SOUR:WAVE:ABOR"), ("6221", "OUTP OFF"), ("6221?", "OUTP?")])
        self.test.assertEqual(self.source.output, 0)
        self.test.assertEqual(self.page.ppms_transport_status_var.get(),
                              "Transport acquisition: 6221 output OFF (read back)")
        self.test.assertEqual(self.page.ppms_scan_outputs, {"6221": "zeroed"})
        self.test.assertIn(("log", "PPMS scan end: 6221 zeroed"), self.log)


class PrimeListTests(unittest.TestCase):
    def test_the_primes_from_start_to_end_with_no_step(self):
        values = app.ppms_prime_frequencies(7, 97)
        self.assertEqual(values, (7., 11., 13., 17., 19., 23., 29., 31., 37., 41., 43., 47., 53.,
                                  59., 61., 67., 71., 73., 79., 83., 89., 97.))
        self.assertEqual(values, app.nonlocal_prime_frequencies(7, 97), "Non-local's list")
        self.assertEqual(app.ppms_prime_frequencies("7", "199")[-1], 199.)
        self.assertEqual(len(app.ppms_prime_frequencies(7, 199)), 43)
        self.assertEqual(app.ppms_prime_frequencies(2, 2), (2.,))
        self.assertEqual(app.ppms_prime_frequencies(99990, 100000), (99991.,))
        self.assertEqual(len(app.ppms_prime_frequencies(2, 100000)), 9592)

    def test_the_start_checks_in_the_pages_words(self):
        for start, end, message in (
                (24, 28, "No prime from 24 to 28 Hz; widen the range"),
                (97, 7, "A frequency scan needs 2 ≤ Start ≤ End ≤ 100000 Hz (the 6221's sine)"),
                (1, 10, "needs 2 ≤ Start"),
                (2, 100001, "≤ 100000 Hz (the 6221's sine)"),
                (7.5, 11, "Frequency Start and End must be whole numbers of Hz"),
                ("", 11, "whole numbers of Hz"),
                ("nan", 11, "whole numbers of Hz")):
            with self.subTest(start=start, end=end):
                with self.assertRaisesRegex(ValueError, message.replace("(", r"\(").replace(")", r"\)")):
                    app.ppms_prime_frequencies(start, end)
                self.assertIn(message, app.ppms_frequency_scan_note(start, end))

    def test_the_count_text(self):
        self.assertEqual(app.ppms_frequency_count_text(app.ppms_prime_frequencies(7, 199)), "43 primes: 7–199 Hz")
        self.assertEqual(app.ppms_frequency_count_text((7.,)), "1 prime: 7 Hz")
        self.assertEqual(app.ppms_frequency_scan_note(7, 97), "22 primes: 7–97 Hz")
        self.assertEqual(app.ppms_frequency_scan_note("7", "7"), "1 prime: 7 Hz")


class ModeTableTests(unittest.TestCase):
    """Every mode-keyed table and branch knows both new modes."""

    def test_the_mode_list_and_its_tables(self):
        self.assertEqual(app.PPMS_SCAN_MODES[-2:], ("Frequency", "Frequency+Current"))
        self.assertEqual(app.PPMS_SCAN_MODES[:7], ("Temperature", "Field", "Angle", "Current", "Field+Temperature",
                                                   "Angle+Temperature", "Current+Temperature"))
        self.assertEqual(set(app.PPMS_SCAN_PLOT_AXES), set(app.PPMS_SCAN_MODES))
        self.assertEqual(app.PPMS_SCAN_PLOT_AXES["Frequency"], "frequency")
        self.assertEqual(app.PPMS_SCAN_PLOT_AXES["Frequency+Current"], "current")
        page = app.CombinedControlApp.__new__(app.CombinedControlApp)
        defaults = page._ppms_scan_mode_defaults()
        self.assertEqual(set(defaults), set(app.PPMS_SCAN_MODES))
        self.assertEqual(defaults["Frequency"], ("7", "97", "", "Hz (primes)"))
        self.assertEqual(defaults["Frequency+Current"], defaults["Current"])
        # The list fits its longest entry, which is still Current+Temperature.
        self.assertEqual(max(app.PPMS_SCAN_MODES, key=len), "Current+Temperature")
        self.assertLessEqual(max(len(mode) for mode in app.PPMS_SCAN_MODES), 19)

    def test_acquire_s_is_1_s_for_both(self):
        for mode in app.PPMS_FREQUENCY_SCAN_MODES:
            self.assertEqual(app.CombinedControlApp._ppms_acquisition_default(mode), "1")
        page = app.CombinedControlApp.__new__(app.CombinedControlApp)
        page.ppms_acquisition_time_var = Var("1")
        page._apply_ppms_mode_acquisition_default("Temperature")
        self.assertEqual(page.ppms_acquisition_time_var.get(), "5")
        page._apply_ppms_mode_acquisition_default("Frequency")
        self.assertEqual(page.ppms_acquisition_time_var.get(), "1")
        page._apply_ppms_mode_acquisition_default("Temperature")
        page._apply_ppms_mode_acquisition_default("Frequency+Current")
        self.assertEqual(page.ppms_acquisition_time_var.get(), "1")

    def test_the_step_box_is_greyed_in_frequency_and_the_current_box_in_frequency_current(self):
        """And "f Hz" in both, which neither reads (review, 2026-10-18)."""
        page = app.CombinedControlApp.__new__(app.CombinedControlApp)
        page.ppms_scan_target_widgets = {key: [mock.Mock()] for key in
                                         ("Temperature", "Field", "Angle", "Current", "Frequency")}
        page.ppms_scan_step_entry = mock.Mock()
        for mode, scanned, step in (("Frequency", {"Frequency"}, "disabled"),
                                    ("Frequency+Current", {"Current", "Frequency"}, None),
                                    ("Current", {"Current"}, None),
                                    ("Field", {"Field"}, None)):
            with self.subTest(mode=mode):
                page.ppms_scan_step_entry.reset_mock()
                page.ppms_simple_mode = Var(mode)
                page._update_ppms_scan_target_widgets(True)
                for parameter, widgets in page.ppms_scan_target_widgets.items():
                    widgets[0].configure.assert_called_with(state="disabled" if parameter in scanned else "normal")
                if step:
                    page.ppms_scan_step_entry.configure.assert_called_once_with(state=step)
                else:
                    page.ppms_scan_step_entry.configure.assert_not_called()

    def test_f_hz_is_a_scan_target_widget(self):
        """The real page build (on stand-in widgets) registers "f Hz" as the
        Frequency target, so both frequency scans lock it."""
        panel, _ttk = build_ppms_panel()
        widgets = panel.ppms_scan_target_widgets["Frequency"]
        self.assertEqual([w.options.get("textvariable") for w in widgets], [panel.ppms_transport_frequency_var])
        self.assertEqual(panel.ppms_frequency_range_frame.grid_options, None, "the outer row starts hidden")

    def test_both_redraws_know_every_axis(self):
        row = {"6221_frequency_hz": 7.0, "6221_current_rms_ma": 1.0, "measured_field_oe": 0.0,
               "temperature_k": 300.0, "measured_angle_deg": 26.0, "sr830_8_x_v": 1e-6, "sr830_8_y_v": 0.0,
               "sr830_8_r_v": 1e-6, "sr830_8_theta_deg": 0.0, "sr830_8_harmonic": 1}
        for mode, axis in app.PPMS_SCAN_PLOT_AXES.items():
            with self.subTest(mode=mode):
                base = PlotTests.base_page([row], mode)
                base._redraw_ppms_transport_plot()
                panel = PlotTests.panel_page([row], mode)
                panel._redraw_ppms_transport_plot()
                self.assertTrue(base.calls and panel.calls)


def launcher(test, mode, main_row, outer=("7", "97"), harmonics=("1", "2", "3"), frequency_box="317.3",
             current_box="1"):
    """The PPMS Start (no window): returns (page, Yes/No text, settings, estimate s), or the error text."""
    page = app.CombinedControlApp.__new__(app.CombinedControlApp)
    page.ppms_field_scan_running = page.ppms_environment_busy = False
    page.closing = False
    page._require_ppms_control_ready = mock.Mock()
    page.ppms_simple_mode = Var(mode)
    page.ppms_simple_range = [Var(value) for value in main_row]
    page.ppms_temperature_scan_range = [Var(value) for value in ("300", "290", "5")]
    page.ppms_frequency_scan_range = [Var(value) for value in outer]
    boxes = dict(ppms_acquisition_time_var="1", ppms_field_target_var="1234", ppms_temperature_target_var="300",
                 ppms_motor_target_var="26", ppms_field_scan_stable_var="0", ppms_field_rate_var="100",
                 ppms_field_approach_var="No overshoot", ppms_transport_current_var=current_box,
                 ppms_transport_frequency_var=frequency_box, ppms_transport_compliance_var="50",
                 ppms_motor_confirm_var=True, ppms_temperature_rate_var="10",
                 ppms_temperature_approach_var="No overshoot", ppms_motor_speed_var="14")
    for name, value in boxes.items():
        setattr(page, name, Var(value))
    for key, value in zip(app.PPMS_SR830_KEYS, harmonics):
        setattr(page, app.PPMS_SR830_HARMONIC_ATTRS[key], Var(value))
    page.ppms_environment_controller = types.SimpleNamespace(backend=app.PPMS_QDINSTRUMENT_BACKEND)
    page.ppms_instrument_controllers = {
        key: types.SimpleNamespace(connected=True, resource_name=f"GPIB0::{address}::INSTR")
        for key, address in (("6221", 30), ("830_a", 8), ("830_b", 9), ("830_c", 10))}
    page.ppms_last_readback = dict(temperature_k=300.0, field_oe=1234.0, angle_deg=26.0)
    page.ppms_field_scan_stop_event = threading.Event()
    for name in ("_redraw_ppms_transport_plot", "_update_ppms_control_ui", "ppms_executor",
                 "ppms_field_scan_progress", "ppms_field_scan_eta_var", "ppms_transport_status_var"):
        setattr(page, name, mock.Mock())
    page.ppms_field_scan_status_var = Var("")
    page.root = None
    page.thread_log = mock.Mock()
    estimates = []
    format_duration = page._format_scan_duration
    page._format_scan_duration = lambda seconds: estimates.append(seconds) or format_duration(seconds)
    with tempfile.TemporaryDirectory() as folder, \
            mock.patch.object(app.simpledialog, "askstring", return_value=""), \
            mock.patch.object(app.messagebox, "askyesno", return_value=True) as ask, \
            mock.patch.object(app.filedialog, "askdirectory", return_value=folder), \
            mock.patch.object(app.messagebox, "showerror") as error:
        page.start_ppms_field_scan()
        if error.called:
            page.ppms_executor.submit.assert_not_called()
            return error.call_args.args[1]
    test.assertTrue(page.ppms_field_scan_running)
    args = page.ppms_executor.submit.call_args.args
    return page, ask.call_args.args[1], args[-1], estimates[0]


class StartTests(unittest.TestCase):
    def test_frequency_mode(self):
        page, question, settings, _estimate = launcher(self, "Frequency", ("7", "199", ""))
        self.assertEqual(settings["frequencies"], app.ppms_prime_frequencies(7, 199))
        self.assertEqual(settings["frequency_hz"], 7.0, "the first prime, not the f Hz box")
        self.assertEqual(settings["currents"], (1.0,), "the I mA RMS box, as a Current scan's fixed value")
        self.assertEqual(settings["compliance_v"], 50.0)
        self.assertEqual(settings["nested"], {"angle": (26.0,), "temperature": (300.0,)})
        self.assertEqual(page.ppms_field_scan_total, 43)
        self.assertEqual(page.ppms_plot_axis, "frequency")
        self.assertIn("Scan mode: Frequency\nFrequencies: 43 primes: 7–199 Hz (Step not used)\n", question)
        self.assertIn("Measurement points: 43\n", question)
        self.assertIn("6221 is switched off and set up again", question)
        self.assertIn("reference lock is awaited", question)
        self.assertIn("at least 5 periods of the frequency", question)
        self.assertIn("\nHeld as in a Current scan: 300 K (T K) and 26° (Angle °); the field is not commanded\n",
                      question)
        self.assertTrue(page.ppms_field_scan_status_var.get().startswith(
            "Frequency scan: 43 primes: 7–199 Hz; 43 points, settle 1 s; estimated about "))
        # The run folder, named PPMS_<date>_<time> when the name is blank
        # (user, 2026-10-18).
        self.assertRegex(page.ppms_run_folder.name, r"^PPMS_\d{8}_\d{6}$")
        self.assertEqual(page.ppms_run_recorder.csv_path.parent, page.ppms_run_folder)
        # The worker gets the passes built here, with the frequencies in them.
        self.assertEqual(len(settings["harmonic_passes"]), 1)
        self.assertEqual(settings["harmonic_passes"][0]["frequencies"], settings["frequencies"])

    def test_the_step_and_the_f_hz_box_are_not_read(self):
        for step, box in (("abc", "317.3"), ("", ""), ("0", "not a number")):
            with self.subTest(step=step, box=box):
                _page, _question, settings, _estimate = launcher(self, "Frequency", ("7", "13", step),
                                                                 frequency_box=box)
                self.assertEqual(settings["frequencies"], (7.0, 11.0, 13.0))

    def test_frequency_current_mode(self):
        page, question, settings, _estimate = launcher(self, "Frequency+Current", ("0.01", "0.03", "0.01"),
                                                       outer=("7", "13"))
        self.assertEqual(settings["frequencies"], (7.0, 11.0, 13.0))
        self.assertEqual(settings["currents"], (0.01, 0.02, 0.03))
        self.assertEqual(settings["current_rms_ma"], 0.01)
        self.assertEqual(settings["frequency_hz"], 7.0)
        self.assertEqual(page.ppms_field_scan_total, 9)
        self.assertEqual(page.ppms_plot_axis, "current")
        self.assertIn("Frequencies: 3 primes: 7–13 Hz, the whole current sweep at each\n", question)
        self.assertIn("the other currents change only the 6221 amplitude", question)
        self.assertTrue(page.ppms_field_scan_status_var.get().startswith(
            "Frequency+Current scan: 3 primes: 7–13 Hz; 9 points"))

    def test_refusals(self):
        for mode, main_row, outer, harmonics, expected in (
                ("Frequency", ("24", "28", ""), ("7", "97"), ("1", "2", "3"), "No prime from 24 to 28 Hz"),
                ("Frequency", ("", "28", ""), ("7", "97"), ("1", "2", "3"), "Please fill in frequency start (Hz)"),
                ("Frequency", ("7", "100003", ""), ("7", "97"), ("1", "2", "3"), "≤ 100000 Hz (the 6221's sine)"),
                ("Frequency", ("97", "7", ""), ("7", "97"), ("1", "2", "3"), "2 ≤ Start ≤ End"),
                ("Frequency+Current", ("0.01", "0.03", "0.01"), ("24", "28"), ("1", "2", "3"), "No prime from 24 to 28"),
                ("Frequency+Current", ("0.01", "0.03", "0.01"), ("7", ""), ("1", "2", "3"),
                 "Please fill in outer frequency end (Hz)"),
                # SR830-3 on its 3rd harmonic: 3 x 34019 Hz passes 102 kHz.
                ("Frequency", ("33000", "35000", ""), ("7", "97"), ("1", "2", "3"),
                 "At 34019 Hz: SR830 slot C demodulation frequency must not exceed 102 kHz"),
                # SR830-1 at 1+2: its second pass, on harmonic 2, passes 102 kHz above 51 kHz.
                ("Frequency+Current", ("0.01", "0.02", "0.01"), ("50000", "52000"), ("1+2", "1", "1"),
                 "At 51001 Hz: SR830 slot A demodulation frequency must not exceed 102 kHz"),
                # The current is checked as a Current scan's is.
                ("Frequency+Current", ("0.01", "100", "10"), ("7", "13"), ("1", "2", "3"),
                 "sine-wave peak must not exceed")):
            with self.subTest(mode=mode, main_row=main_row, outer=outer):
                message = launcher(self, mode, main_row, outer=outer, harmonics=harmonics)
                self.assertIsInstance(message, str)
                self.assertIn(expected, message)

    def test_the_largest_frequencies_pass_where_every_harmonic_fits(self):
        _page, _question, settings, _estimate = launcher(self, "Frequency", ("33000", "34000", ""))
        self.assertEqual(settings["frequencies"][-1], 33997.0)   # 3 x 33997 = 101991 Hz

    def test_the_estimate_counts_every_point_and_each_frequencys_set_up(self):
        """Frequency, 7–97 Hz (22 primes), 1 s settle (1 x the 1 s SR830 TC)
        and Acquire 1 s: 22 x (1 + 1) s of points, 6 s per point, 12 s per
        set-up, and one more window at each frequency (5 periods are all
        under 1 s here)."""
        _page, _question, _settings, estimate = launcher(self, "Frequency", ("7", "97", ""))
        self.assertAlmostEqual(estimate, 22 * 2 + 6 * 22 + 12 * 22 + 22 * 1)
        # 2 and 3 Hz: their 5 periods (2.5 s, 1.67 s) are longer than the 1 s settle.
        _page, _question, _settings, estimate = launcher(self, "Frequency", ("2", "3", ""))
        self.assertAlmostEqual(estimate, 2 * 2 + 6 * 2 + 12 * 2 + 2 * 1 + 1.5 + (5 / 3 - 1))
        # Frequency+Current: 3 primes x 3 currents.
        _page, _question, _settings, estimate = launcher(self, "Frequency+Current", ("0.01", "0.03", "0.01"),
                                                         outer=("7", "13"))
        # One 12 s set-up per frequency, not per current (review, 2026-10-18).
        self.assertAlmostEqual(estimate, 9 * 2 + 6 * 9 + 12 * 3 + 3 * 1)

    def test_set_up_allowance_per_pass(self):
        passes = [(1.0, 1.0, "SR830-1"), (1.0, 1.0, "SR830-1")]
        self.assertAlmostEqual(app.CombinedControlApp._ppms_frequency_set_up_s((2.0, 7.0), passes, 1.0),
                               2 * (1 + 1.5 + 1 + 0) + 2 * app.PPMS_SET_UP_ALLOWANCE_S)
        self.assertEqual(app.PPMS_SET_UP_ALLOWANCE_S, 12.0, "the allowance every set-up had")

    def test_the_worker_uses_the_passes_built_at_start(self):
        page = app.CombinedControlApp.__new__(app.CombinedControlApp)
        page.closing = False
        page.ppms_field_scan_stop_event = threading.Event()
        page._ppms_harmonic_passes = mock.Mock(side_effect=AssertionError("Tk variables read on the worker"))
        ran = []
        page._ppms_nested_scan_worker = lambda *args: ran.append(args[-1]["harmonic_pass"])
        passes = ({"harmonic_pass": 1}, {"harmonic_pass": 2})
        page._ppms_scan_with_final_field([], 100.0, 0.0, 1, {"harmonic_passes": passes})
        self.assertEqual(ran, [1, 2])


class SequenceTests(unittest.TestCase):
    """The per-frequency hardware sequence, on the real worker methods."""

    def test_frequency_current_order_at_each_frequency(self):
        rig = Rig(self, "Frequency+Current", (2.0, 3.0), (0.5, 1.0))
        rig.run()
        log = rig.log
        peak = math.sqrt(2) * 1e-3
        for frequency, previous_end in ((2.0, 0), (3.0, None)):
            with self.subTest(frequency=frequency):
                start = rig.index(("6221", f"SOUR:WAVE:FREQ {frequency:.12g}"))
                # 1. The 6221 off and read back off (Non-local's order), then set up again.
                off = max(i for i in range(start) if log[i] == ("6221", "OUTP OFF")
                          and log[i + 1] == ("6221?", "OUTP?"))
                reset = rig.index(("6221", "*RST"), off)
                self.assertLess(reset, start)
                self.assertEqual(log[off - 1], ("6221", "SOUR:WAVE:ABOR"))
                # The one fixed range of the whole sweep, then the sine re-armed.
                self.assertLess(rig.index(("6221", f"SOUR:CURR:RANG {peak:.12e}"), reset), start)
                self.assertLess(start, rig.index(("6221", "SOUR:WAVE:RANG FIX"), reset))
                arm = rig.index(("6221", "SOUR:WAVE:ARM"), start)
                init = rig.index(("6221", "SOUR:WAVE:INIT"), arm)
                # 2. The SR830 lock at the new frequency: the latched UNLK
                # makes it poll again after 0.2 s.
                lock_wait = rig.index(("wait", 0.2), init)
                self.assertLess(rig.index(("sr830_8?", "LIAS?"), init), lock_wait)
                # 3. The Zurich follows (one unlocked read, 0.2 s, then locked).
                zurich = rig.index(("zurich?", "read_mfli", frequency), lock_wait)
                zurich_again = rig.index(("zurich?", "read_mfli", frequency), zurich + 1)
                # 4. The settle: 1 x the 1 s TC, or 5 periods if longer.
                settle = rig.index(("wait", round(max(1.0, 5 / frequency), 9)), zurich_again)
                # 5. Then the first point's windows read.
                first_read = rig.index(("sr830_8?", "SNAP? 1,2,3,4,9"), settle)
                self.assertTrue(all(entry[0] != "6221" for entry in log[init + 1:first_read]),
                                "nothing is sent to the 6221 between the re-arm and the first reading")
        # Each set-up is the page's own: the Zurich itself is configured once.
        self.assertEqual(log.count(("zurich", "configure_mfli")), 1)
        rows = rig.rows()
        self.assertEqual([(float(r["6221_frequency_hz"]), float(r["6221_current_rms_ma"])) for r in rows],
                         [(2.0, 0.5), (2.0, 1.0), (3.0, 0.5), (3.0, 1.0)])
        # Each frequency's first current gets the agreeing windows; the next one does not.
        self.assertEqual([r["first_point_windows"] for r in rows], ["2", "1", "2", "1"])
        self.assertEqual([float(r["effective_settle_s"]) for r in rows], [2.5, 1.0, 5 / 3, 1.0])
        # The reference each lock-in read, beside the set frequency.
        self.assertEqual([float(r["sr830_8_frequency_hz"]) for r in rows], [2.0, 2.0, 3.0, 3.0])
        self.assertEqual([float(r["zurich_reference_hz"]) for r in rows], [2.0, 2.0, 3.0, 3.0])
        # The second current changes only the amplitude: no reset or re-arm.
        second = rig.index(("6221", f"SOUR:WAVE:AMPL {math.sqrt(2) * 1e-3:.12e}"),
                           rig.index(("6221", "SOUR:WAVE:FREQ 2")) + 1)
        third_freq = rig.index(("6221", "SOUR:WAVE:FREQ 3"))
        between = log[second:third_freq]
        for command in ("SOUR:WAVE:ARM", "SOUR:WAVE:INIT"):
            self.assertNotIn(("6221", command), between, "the amplitude update does not re-arm")
        # Until the next frequency's set-up resets it.
        self.assertLess(rig.index(("6221", "*RST"), second), third_freq)
        # A completed scan zeroes the 6221 too (user, 2026-10-18; it kept
        # the last output), after the last point.
        rig.assert_switched_off()
        self.assertEqual(rig.source.frequency, 3.0)
        self.assertFalse(any(entry[:2] == ("ppms", "set_field") for entry in log))

    def test_frequency_mode_sets_each_frequency_up_and_reads_once(self):
        rig = Rig(self, "Frequency", (2.0, 3.0, 5.0), (1.0,))
        rig.run()
        rows = rig.rows()
        self.assertEqual([float(r["6221_frequency_hz"]) for r in rows], [2.0, 3.0, 5.0])
        self.assertEqual([float(r["6221_current_rms_ma"]) for r in rows], [1.0] * 3)
        self.assertEqual([r["first_point_windows"] for r in rows], ["2"] * 3)
        self.assertEqual([float(r["effective_settle_s"]) for r in rows], [2.5, 5 / 3, 1.0])
        self.assertEqual(rig.log.count(("6221", "SOUR:WAVE:INIT")), 3)
        self.assertEqual(rig.log.count(("6221", "SOUR:WAVE:RANG BEST")), 3, "the fixed current's best range")
        self.assertFalse(any(entry[1].startswith("SOUR:CURR:RANG") for entry in rig.log if entry[0] == "6221"))
        # The temperature is held at the T K box, as a Current scan holds it.
        self.assertEqual([entry for entry in rig.log if entry[:2] == ("ppms", "set_temperature")],
                         [("ppms", "set_temperature", 300.0)])
        # The log says when the 5 periods are the longer wait (review, 2026-10-18).
        notes = [entry[1] for entry in rig.log if entry[0] == "log" and "periods of" in entry[1]]
        self.assertEqual(notes, ["PPMS scan: this frequency's first reading waits 2.5 s: 5 periods of 2 Hz",
                                 "PPMS scan: this frequency's first reading waits 1.67 s: 5 periods of 3 Hz"])

    def test_the_columns_are_a_current_scans(self):
        """Existing columns keep their places; no column is added."""
        frequency = Rig(self, "Frequency", (2.0,), (1.0,))
        frequency.run()
        current = Rig(self, "Current", None, (1.0,))
        current.zurich.instant = True
        current.run()
        self.assertEqual(frequency.header(), current.header())
        self.assertIn("6221_frequency_hz", frequency.header())

    def test_the_sr830_lock_may_take_20_s_at_each_frequency(self):
        rig = Rig(self, "Frequency", (2.0, 3.0), (1.0,))
        rig.sr830["830_a"].never_locks_at = 3.0
        with self.assertRaisesRegex(RuntimeError, "failed to lock the 6221 external reference within 20 s"):
            rig.run()
        self.assertEqual(len(rig.rows()), 1)
        # A failure ends as a Current scan's: the 6221 switched off.
        rig.assert_switched_off()
        self.assertEqual(rig.source.output, 0)

    def test_a_zurich_that_does_not_lock_fails_the_scan_and_switches_the_6221_off(self):
        rig = Rig(self, "Frequency", (2.0, 3.0), (1.0,))
        rig.source.on_write = lambda command: setattr(rig.zurich, "never_locks", command == "SOUR:WAVE:FREQ 3"
                                                      or rig.zurich.never_locks)
        with self.assertRaisesRegex(RuntimeError, "Zurich failed to lock the 6221 external reference at 3 Hz within 20 s"):
            rig.run()
        self.assertEqual(len(rig.rows()), 1)
        rig.assert_switched_off()

    def test_a_6221_that_stays_on_is_not_set_up_again(self):
        rig = Rig(self, "Frequency", (2.0, 3.0), (1.0,))
        source = rig.source

        def stuck(command):
            if command == "OUTP OFF" and source.frequency == 2.0 and len(rig.rows_seen) == 1:
                source.output = 1
        rig.rows_seen = []
        append = rig.page._append_ppms_live_csv
        rig.page._append_ppms_live_csv = lambda row: (append(row), rig.rows_seen.append(row))
        source.on_write = stuck
        with self.assertRaisesRegex(RuntimeError, r"6221 output still ON \(OUTP\?=1\) before setting up 3 Hz"):
            rig.run()
        self.assertNotIn(("6221", "SOUR:WAVE:FREQ 3"), rig.log)
        # The end's off is read back too, and the line says it is still ON
        # instead of OFF (review, 2026-10-18); so does the end's zeroing
        # (user, 2026-10-18), for the finish to say CHECK IT.
        self.assertIn(("log", "PPMS: the 6221 still reads ON (OUTP?=1) after the scan's output off"), rig.log)
        self.assertEqual(rig.page.ppms_scan_outputs,
                         {"6221": "zero FAILED: the 6221 output did not read back OFF (OUTP? 1)"})
        self.assertEqual(rig.page.ppms_transport_status_var.get(),
                         "Transport acquisition: 6221 zero FAILED: the 6221 output did not read back OFF (OUTP? 1); "
                         "switch it off")

    def test_the_6221_frequency_is_read_back_to_0_01_hz(self):
        """997 Hz not applied reads back the reset 1000 Hz, within the
        0.5 % a lock-in reading may differ by: refused before the re-arm
        (review, 2026-10-18)."""
        rig = Rig(self, "Frequency", (997.0,), (1.0,))
        rig.source.on_write = lambda command: (setattr(rig.source, "frequency", 1000.0)
                                               if command == "SOUR:WAVE:FREQ 997" else None)
        with self.assertRaisesRegex(RuntimeError, "6221 frequency readback mismatch: requested 997 Hz, read 1000 Hz"):
            rig.run()
        self.assertNotIn(("6221", "SOUR:WAVE:ARM"), rig.log)
        rig.assert_switched_off()
        # Any other set-up keeps the 0.5 % (the SHV page's included).
        other = Rig(self, "Current", None, (1.0,), zurich=False)
        other.source.on_write = lambda command: (setattr(other.source, "frequency", 1000.0)
                                                 if command == "SOUR:WAVE:FREQ 997" else None)
        settings = dict(other.settings, frequency_hz=997.0)
        with mock.patch.object(app.time, "monotonic", other.clock), mock.patch.object(app.time, "sleep", lambda _s: None):
            app.CombinedControlApp._configure_ppms_transport_scan(other.page, settings)
        self.assertEqual((other.source.output, other.source.frequency), (1, 1000.0))

    def test_a_zurich_reading_held_from_the_previous_prime_does_not_count(self):
        """LabOne 18.12 (no lock flag): after 1009 Hz, 1013 Hz is within
        0.5 %, so a reading still at 1009 Hz is valid.  The wait counts only
        readings nearer 1013 Hz, three in a row (review, 2026-10-18)."""
        rig = Rig(self, "Frequency", (1009.0, 1013.0), (1.0,))
        rig.zurich.stale_reads = 2
        rig.run()
        init = rig.index(("6221", "SOUR:WAVE:INIT"), rig.index(("6221", "SOUR:WAVE:FREQ 1013")))
        first_read = rig.index(("sr830_8?", "SNAP? 1,2,3,4,9"), init)
        waits = [entry for entry in rig.log[init:first_read] if entry == ("zurich?", "read_mfli", 1013.0)]
        self.assertEqual(len(waits), 2 + app.PPMS_ZURICH_LOCK_READS, "two held readings, then three at 1013 Hz")
        self.assertEqual(app.PPMS_ZURICH_LOCK_READS, 3)
        rows = rig.rows()
        self.assertEqual([float(r["zurich_reference_hz"]) for r in rows], [1009.0, 1013.0])
        # The 6221's frequency before each change is read before its off.
        before = rig.index(("6221", "SOUR:WAVE:ABOR"), rig.index(("6221?", "SOUR:WAVE:FREQ?"), init - 60))
        self.assertLess(before, init)

    def test_a_stop_while_the_frequency_changes_ends_with_the_6221_zeroed(self):
        """Stop between the per-frequency off and the re-arm, or in the lock
        wait after it: the 6221 is zeroed and read back either way, and the
        finish says so (user, 2026-10-18; the line said "6221 off (stopped
        while changing frequency)" or "all settings and outputs kept")."""
        for entry in (("6221", "SOUR:WAVE:FREQ 3"), ("6221", "SOUR:WAVE:INIT")):
            with self.subTest(entry=entry):
                rig = Rig(self, "Frequency", (2.0, 3.0), (1.0,))
                rig.page.root = types.SimpleNamespace(after=lambda _ms, show: show())
                rig.stop_at(entry, occurrence=1 if entry[1].startswith("SOUR:WAVE:FREQ") else 2)
                with self.assertRaises(app.PPMSScanCancelled) as caught:
                    rig.run()
                self.assertEqual(len(rig.rows()), 1)
                rig.assert_switched_off()
                with mock.patch.object(app.messagebox, "showerror") as dialog:
                    rig.page._report_ppms_scan_finished("Frequency", True, caught.exception)
                dialog.assert_not_called()
                self.assertEqual(rig.page.ppms_field_scan_status_var.get(), "Stopped; 6221 zeroed")

    def test_frequency_current_endings_at_every_step(self):
        """The Stop button and "I off" both end with the 6221 switched off
        and read back (user, 2026-10-18; the Stop button kept what was on):
        at the change's off, in the set-up, the SR830 lock, the Zurich wait,
        the amplitude update and the settle, as a Current scan ends."""
        peak = f"SOUR:WAVE:AMPL {math.sqrt(2) * 1e-3:.12e}"
        steps = (
            ("the set-up after the off", ("6221", "*RST"), 2, 0, 2),
            ("the SR830 lock", ("6221", "SOUR:WAVE:INIT"), 2, 1, 2),
            ("the Zurich wait", ("zurich?", "read_mfli", 3.0), 1, 1, 2),
            ("the amplitude update", ("6221", peak), 1, 1, 1),
            ("the settle", ("wait", round(5 / 3, 9)), 1, 1, 2),
        )
        for name, entry, occurrence, output, rows in steps:
            for preserve in (True, False):
                with self.subTest(step=name, stop_button=preserve):
                    rig = Rig(self, "Frequency+Current", (2.0, 3.0), (0.5, 1.0), preserve_on_stop=preserve)
                    rig.stop_at(entry, occurrence)
                    try:
                        rig.run()
                    except app.PPMSScanCancelled:
                        pass
                    self.assertEqual(len(rig.rows()), rows)
                    rig.assert_switched_off()
                    if preserve:
                        # The Stop button: the step under way ends, and only
                        # the end's zeroing switches the 6221 off (output
                        # ``output`` at the stop).
                        self.assertEqual(rig.log[rig.stopped_at:].count(("6221", "OUTP OFF")), 1)

    def test_a_lock_in_range_failure_ends_with_the_6221_zeroed(self):
        rig = Rig(self, "Frequency", (2.0, 3.0), (1.0,))
        acquire = rig.page._acquire_ppms_first_point
        calls = []

        def first_point(*args):
            calls.append(args)
            if len(calls) == 2:
                raise app.PPMSLockinRangeError("SR830 autorange did not settle")
            return acquire(*args)
        rig.page._acquire_ppms_first_point = first_point
        with self.assertRaises(app.PPMSLockinRangeError):
            rig.run()
        # Its own clean-up still keeps the output; the end's zeroing does
        # not (user, 2026-10-18: it kept the output).
        self.assertEqual(rig.source.frequency, 3.0)
        rig.assert_switched_off()
        self.assertEqual(len(rig.rows()), 1)

    def test_every_harmonic_pass_runs_the_whole_frequency_scan(self):
        """Through _ppms_scan_with_final_field with the passes Start built:
        SR830-1 at 1+2, so pass 2 sets HARM 2 and SR830-3 drops out."""
        rig = Rig(self, "Frequency", (2.0, 3.0), (1.0,))
        first = dict(rig.settings, harmonic_pass=1)
        second = dict(rig.settings, harmonic_pass=2, harmonic_a=2, active_lockins=("830_a",))
        settings = dict(rig.settings, harmonic_passes=(first, second))
        with mock.patch.object(app.time, "monotonic", rig.clock), \
                mock.patch.object(app.time, "sleep", lambda _s: None), \
                mock.patch.object(app, "PPMSMotorDebug", Motor):
            rig.page._ppms_scan_with_final_field([(1, 1, 1, 1234.0)], 100.0, 0.0, 1, settings)
        rows = rig.rows()
        self.assertEqual([(r["harmonic_pass"], float(r["6221_frequency_hz"])) for r in rows],
                         [("1", 2.0), ("1", 3.0), ("2", 2.0), ("2", 3.0)])
        self.assertEqual(rig.log.count(("6221", "SOUR:WAVE:INIT")), 4)
        self.assertIn(("sr830_8", "HARM 2"), rig.log)
        self.assertEqual([r["sr830_10_harmonic"] for r in rows], ["3", "3", "", ""])

    def test_stop_mid_scan_zeroes_only_the_6221(self):
        """The Stop button during the second frequency's settle: that point
        is not saved, and only the end's zeroing of the 6221 is sent after
        it (user, 2026-10-18; nothing was switched off before)."""
        rig = Rig(self, "Frequency", (2.0, 3.0, 5.0), (1.0,))
        rig.stop.on_wait = lambda seconds: rig.stop.set() if math.isclose(seconds, 5 / 3) else None
        rig.run()
        self.assertEqual(len(rig.rows()), 1)
        stopped = rig.index(("wait", round(5 / 3, 9)))
        self.assertEqual([entry for entry in rig.log[stopped:] if entry[0] in ("6221", "6221?")],
                         [("6221", "SOUR:WAVE:ABOR"), ("6221", "OUTP OFF"), ("6221?", "OUTP?")])
        rig.assert_switched_off()
        self.assertEqual(rig.source.frequency, 3.0)
        self.assertNotIn(("6221", "SOUR:WAVE:FREQ 5"), rig.log)

    def test_i_off_mid_scan_switches_the_6221_off_as_a_current_scan(self):
        rig = Rig(self, "Frequency", (2.0, 3.0, 5.0), (1.0,), preserve_on_stop=False)
        rig.stop.on_wait = lambda seconds: rig.stop.set() if math.isclose(seconds, 5 / 3) else None
        rig.run()
        self.assertEqual(len(rig.rows()), 1)
        rig.assert_switched_off()

    def test_the_current_scans_ending_is_the_same(self):
        """A Current scan, as the frequency modes mirror it: completed or
        failed, the 6221 ends zeroed (user, 2026-10-18; completed kept it)."""
        rig = Rig(self, "Current", None, (0.5, 1.0))
        rig.zurich.instant = True
        rig.run()
        rig.assert_switched_off()
        rig = Rig(self, "Current", None, (0.5, 1.0))
        rig.sr830["830_a"].never_locks_at = 317.3
        with self.assertRaisesRegex(RuntimeError, "within 8 s"):
            rig.run()
        rig.assert_switched_off()


class PlotTests(unittest.TestCase):
    @staticmethod
    def base_page(rows, mode):
        page = app.CombinedControlApp.__new__(app.CombinedControlApp)
        page.calls = []
        page._draw_nonlocal_series = lambda *args, **options: page.calls.append((args, options))
        page.ppms_transport_canvas = object()
        page.ppms_transport_canvases = {("830_a", channel): object() for channel in ("x", "y", "theta")}
        page.ppms_transport_plot_frames = {}
        page.ppms_plot_addresses = {"830_a": 8}
        page.ppms_instrument_controllers = {"830_a": types.SimpleNamespace(connected=True,
                                                                           resource_name="GPIB0::8::INSTR")}
        page.ppms_transport_harmonic_a_var = Var("1")
        page.ppms_field_scan_rows = rows
        page.ppms_active_scan_mode = mode
        page.ppms_plot_axis = app.PPMS_SCAN_PLOT_AXES[mode]
        return page

    @staticmethod
    def panel_page(rows, mode):
        page = app.PPMSPanel.__new__(app.PPMSPanel)
        page.parent_app = types.SimpleNamespace()
        page.calls = []
        page._draw_nonlocal_series = lambda *args, **options: page.calls.append((args, options))
        page.ppms_transport_canvas = None
        page.ppms_harmonic_canvases = {(1, channel): object() for channel in ("x", "y", "theta")}
        page.ppms_lockin_selection = {"830_a": Var(True), "zurich": Var(False)}
        page.ppms_instrument_controllers = {"830_a": types.SimpleNamespace(connected=True,
                                                                           resource_name="GPIB0::8::INSTR")}
        page.ppms_plot_addresses = {"830_a": 8}
        page.ppms_transport_harmonic_a_var = Var("1")
        page.ppms_field_scan_rows = rows
        page.ppms_active_scan_mode = mode
        page.ppms_plot_axis = app.PPMS_SCAN_PLOT_AXES[mode]
        return page

    @staticmethod
    def rows(frequencies, currents):
        return [{"6221_frequency_hz": f, "6221_current_rms_ma": i, "loop": 1, "temperature_index": 1,
                 "target_temperature_k": 300.0, "sr830_8_x_v": f * i * 1e-6, "sr830_8_y_v": 0.0,
                 "sr830_8_r_v": f * i * 1e-6, "sr830_8_theta_deg": 0.0, "sr830_8_harmonic": 1}
                for f in frequencies for i in currents]

    def test_frequency_mode_plots_against_frequency(self):
        rows = self.rows((7.0, 11.0, 13.0), (1.0,))
        for page in (self.panel_page(rows, "Frequency"), self.base_page(rows, "Frequency")):
            with self.subTest(page=type(page).__name__):
                page._redraw_ppms_transport_plot()
                args, _options = page.calls[0]
                series, x_label = args[1], args[2]
                self.assertEqual(x_label, "Frequency (Hz)")
                self.assertEqual(len(series), 1, "one curve per meter")
                self.assertEqual([x for x, _y in next(iter(series.values()))], [7.0, 11.0, 13.0])

    def test_frequency_current_plots_one_curve_per_frequency(self):
        rows = self.rows((7.0, 11.0, 13.0), (0.5, 1.0))
        page = self.panel_page(rows, "Frequency+Current")
        page._redraw_ppms_transport_plot()
        args, options = page.calls[0]
        series, x_label = args[1], args[2]
        self.assertEqual(x_label, "Applied AC current (mA RMS)")
        self.assertEqual(sorted(series), ["SR830-1 11 Hz L1", "SR830-1 13 Hz L1", "SR830-1 7 Hz L1"])
        self.assertEqual(series["SR830-1 7 Hz L1"], [(0.5, 3.5e-6), (1.0, 7e-6)])
        self.assertEqual(options["series_labels"]["SR830-1 7 Hz L1"], "SR830-1 7 Hz L1")
        colors = options["series_colors"]
        base = app.PPMS_METER_COLORS["830_a"]
        self.assertEqual(colors["SR830-1 7 Hz L1"], app.shade_color(base, 0.0))   # lightest, lowest
        self.assertEqual(colors["SR830-1 11 Hz L1"].lower(), base.lower())
        self.assertEqual(colors["SR830-1 13 Hz L1"], app.shade_color(base, 1.0))  # darkest, highest
        base_page = self.base_page(rows, "Frequency+Current")
        base_page._redraw_ppms_transport_plot()
        args, options = base_page.calls[0]
        self.assertEqual(sorted(args[1]), ["GPIB8 X | 11 Hz L1", "GPIB8 X | 13 Hz L1", "GPIB8 X | 7 Hz L1"])
        self.assertEqual(len(set(options["series_colors"].values())), 3)


class SHVUnchangedTests(unittest.TestCase):
    """The SHV page (RTEPanel) inherits this code; it must not change."""

    def test_the_shv_page_keeps_its_own_modes_and_methods(self):
        import inspect
        source = inspect.getsource(app.RTEPanel)
        self.assertIn("values=('Field', 'Current', 'Angle')", source)
        for name in ("Frequency", "PPMS_SCAN_MODES", "_ppms_frequency_scan_block", "_ppms_set_up_frequency",
                     "_ppms_wait_zurich_reference", "reference_timeout_s", "frequency_settle_s"):
            self.assertNotIn(name, source.replace("RTE_TRANSPORT_FREQUENCY_HZ", "").replace("Frequency Hz", ""))
        for name in ("build_ppms_ui", "start_ppms_field_scan", "_redraw_ppms_transport_plot",
                     "_update_ppms_control_ui", "_rte_mode_changed"):
            self.assertIn(name, app.RTEPanel.__dict__, f"SHV has its own {name}")

    def test_a_set_up_without_a_frequency_scan_keeps_the_8_s_lock(self):
        self.assertEqual(app.PPMS_TRANSPORT_REFERENCE_TIMEOUT_S, 8.0)
        rig = Rig(self, "Current", None, (1.0,), zurich=False)
        rig.sr830["830_a"].never_locks_at = 317.3
        page = rig.page
        with mock.patch.object(app.time, "monotonic", rig.clock), mock.patch.object(app.time, "sleep", lambda _s: None):
            with self.assertRaisesRegex(RuntimeError, "within 8 s"):
                app.CombinedControlApp._configure_ppms_transport_scan(page, dict(rig.settings))


if __name__ == "__main__":
    unittest.main()
