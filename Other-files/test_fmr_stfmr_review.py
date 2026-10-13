"""FMR and STFMR page fixes from the review of 2026-10-13, one check each.

No Tk and no instrument: the real methods run on a bare app with stand-ins
(a fake clock and after() queue, synchronous futures, a fake 4886 card, a
fake SR830 and a fake 475 that follow what was written to them)."""
from concurrent.futures import Future
import threading
import types
import unittest
from unittest import mock

from test_nonlocal_keithley import Var, bare_app
from test_ppms_motor_debug import app


class Clock:
    def __init__(self):
        self.now = 1000.0

    def monotonic(self):
        return self.now


class Root:
    """root.after() as a queue; running a callback first moves the clock on."""

    def __init__(self, clock):
        self.clock, self.queue = clock, []

    def after(self, ms, callback):
        self.queue.append((ms, callback))
        return len(self.queue)

    def run(self, done, limit=2000):
        for _step in range(limit):
            if done() or not self.queue:
                return
            ms, callback = self.queue.pop(0)
            self.clock.now += ms / 1000.0
            callback()
        raise AssertionError("the loop did not end")


class Now:
    """An executor that runs the work at once (done callbacks run at once)."""

    def submit(self, function, *args):
        future = Future()
        try:
            future.set_result(function(*args))
        except BaseException as error:
            future.set_exception(error)
        return future


class Card4886:
    """A write-only BOP 4886: records writes, can stall on chosen commands."""

    def __init__(self, stall=()):
        self.writes, self.current, self.stall = [], 0.0, set(stall)

    def write_raw(self, payload):
        command = payload.decode("ascii")
        self.writes.append(command)
        if command in self.stall:
            raise TimeoutError("VI_ERROR_TMO")
        for part in command.split(";"):
            if part.startswith("CURR "):
                self.current = float(part[5:])


def kepco_vars(a):
    a.stfmr_kepco_mode_var = Var("Mode: --")
    a.stfmr_kepco_current_var = Var("I: -- A")
    a.stfmr_kepco_voltage_var = Var("V: -- V")
    a.stfmr_kepco_output_var = Var("OUTP: --")
    a.stfmr_status_var = Var()


def readout(a):
    return [a.stfmr_kepco_mode_var.get(), a.stfmr_kepco_current_var.get(),
            a.stfmr_kepco_voltage_var.get(), a.stfmr_kepco_output_var.get()]


def connected_4886(card):
    """The real controller as Connect leaves it: a session, nothing sent."""
    kepco = app.STFMRKepco4886Controller(lambda message: None)
    kepco.instrument = card
    return kepco


class FmrFieldSettleTests(unittest.TestCase):
    """#15: a 'Wait s' above 15 s ended every scan at its first point."""

    def run_first_point(self, settle_s, field_of):
        a = bare_app()
        clock = Clock()
        a.root = Root(clock)
        a.stfmr_executor = Now()
        a.post_ui = lambda callback: callback()
        kepco = mock.Mock()
        kepco.set_output.return_value = {}
        reads = []

        def read_field():
            reads.append(clock.now)
            return {"field": field_of(len(reads)), "unit_code": 1, "unit": "G"}

        a.stfmr_controllers = {"kepco": kepco, "gauss": types.SimpleNamespace(read_field=read_field)}
        a._apply_stfmr_kepco_readback = lambda result: None
        a.stfmr_fmr_running = True
        a.stfmr_fmr_stop_event = threading.Event()
        a.stfmr_fmr_currents = [-1.0, 0.0, 1.0]
        a.stfmr_fmr_current_index = a.stfmr_fmr_frequency_index = a.stfmr_fmr_index = 0
        a.stfmr_fmr_frequencies = [5e9]
        a.stfmr_fmr_rows = []
        a.stfmr_fmr_voltage_limit_v = 5.0
        a.stfmr_fmr_settle_s = settle_s
        a.stfmr_fmr_field_settle_samples = []
        a.stfmr_fmr_status_var, a.stfmr_gauss_field_var = Var(), Var()
        a._measure_stfmr_fmr_point = mock.Mock()
        a._finish_stfmr_fmr_scan = mock.Mock()
        started = clock.now
        with mock.patch.object(app, "time", types.SimpleNamespace(monotonic=clock.monotonic)):
            a._start_next_stfmr_fmr_point()
            a.root.run(lambda: a._measure_stfmr_fmr_point.called or a._finish_stfmr_fmr_scan.called)
        kepco.set_output.assert_called_once_with("CURR", -1.0, 5.0)
        return a, clock.now - started

    def test_a_20_s_wait_measures_a_stable_field(self):
        a, elapsed = self.run_first_point(20.0, lambda n: -335.0)
        a._finish_stfmr_fmr_scan.assert_not_called()
        a._measure_stfmr_fmr_point.assert_called_once()
        self.assertGreaterEqual(elapsed, 20.0, "the whole requested wait first")
        self.assertLess(elapsed, 20.5)

    def test_a_short_wait_is_unchanged(self):
        a, elapsed = self.run_first_point(0.2, lambda n: -335.0)
        a._measure_stfmr_fmr_point.assert_called_once()
        self.assertAlmostEqual(elapsed, 1.0, delta=0.11, msg="1 s at the first point")

    def test_a_field_that_never_settles_still_times_out_after_the_wait(self):
        a, elapsed = self.run_first_point(20.0, lambda n: -335.0 + (50.0 if n % 2 else -50.0))
        a._measure_stfmr_fmr_point.assert_not_called()
        a._finish_stfmr_fmr_scan.assert_called_once()
        error = a._finish_stfmr_fmr_scan.call_args.kwargs["error"]
        self.assertIn("did not settle within 35 s", str(error))
        self.assertGreaterEqual(elapsed, 35.0)
        self.assertLess(elapsed, 35.5)

    def test_the_default_timeout_still_ends_an_unsettled_point(self):
        a, elapsed = self.run_first_point(0.2, lambda n: -335.0 + (50.0 if n % 2 else -50.0))
        a._finish_stfmr_fmr_scan.assert_called_once()
        self.assertIn("within 16 s", str(a._finish_stfmr_fmr_scan.call_args.kwargs["error"]))
        self.assertLess(elapsed, 16.5)


class FixedFieldFeedbackTests(unittest.TestCase):
    """#16: the feedback stopped at about 6.02 A (12 steps of 0.5 A)."""

    def feedback(self, target_g, gauss_per_amp=300.0):
        a = bare_app()
        a.root = Root(Clock())
        a.stfmr_executor = Now()
        a.post_ui = lambda callback: callback()
        card = Card4886()
        kepco = connected_4886(card)
        kepco.set_output("CURR", 0.0, 5.0)      # as the sweep starts it
        gauss = types.SimpleNamespace(
            read_field=lambda: {"field": gauss_per_amp * card.current, "unit_code": 1, "unit": "G"})
        a.stfmr_controllers = {"kepco": kepco, "gauss": gauss}
        kepco_vars(a)
        a.stfmr_gauss_field_var, a.stfmr_frequency_status_var = Var(), Var()
        a.stfmr_frequency_running = True
        a.stfmr_frequency_stop_event = threading.Event()
        a.stfmr_frequency_settle_s = 0.3
        a.stfmr_frequency_feedback_points = []
        a.stfmr_frequency_feedback_iteration = 0
        a.stfmr_frequency_feedback_current = 0.0
        a.stfmr_frequency_target_field_g = target_g
        a.stfmr_frequency_tolerance_g = 10.0
        a._measure_next_stfmr_frequency_point = mock.Mock()
        a._finish_stfmr_frequency_scan = mock.Mock()
        a._schedule_stfmr_frequency_feedback_read()
        a.root.run(lambda: a._measure_next_stfmr_frequency_point.called
                   or a._finish_stfmr_frequency_scan.called)
        currents = [float(w.split()[1]) for w in card.writes if w.startswith("CURR ") and ";" not in w]
        return a, card, currents

    def test_fields_that_need_7_to_9_a_are_reached(self):
        for target_g, amps in ((2100.0, 7.0), (2700.0, 9.0), (-2700.0, -9.0)):
            with self.subTest(target_g=target_g):
                a, card, currents = self.feedback(target_g)
                a._finish_stfmr_frequency_scan.assert_not_called()
                a._measure_next_stfmr_frequency_point.assert_called_once()
                self.assertAlmostEqual(card.current, amps, delta=10.0 / 300.0)
                self.assertLessEqual(max(abs(c) for c in currents), 10.0)
                self.assertLessEqual(max(abs(b - c) for b, c in zip(currents, currents[1:])), 0.5 + 1e-9,
                                     "each step still at most 0.5 A")

    def test_a_field_beyond_10_a_ends_with_the_limit_named(self):
        a, card, currents = self.feedback(3600.0)          # needs 12 A
        a._measure_next_stfmr_frequency_point.assert_not_called()
        a._finish_stfmr_frequency_scan.assert_called_once()
        error = str(a._finish_stfmr_frequency_scan.call_args.kwargs["error"])
        self.assertIn("needs more than the 4886's ±10 A", error)
        self.assertEqual(max(abs(c) for c in currents), 10.0, "never above 10 A")

    def test_the_corrections_reach_full_scale_with_margin(self):
        reach = app.MAGNET_FIELD_ZERO_PROBE_A + app.STFMR_FIELD_FEEDBACK_MAX_ITERATIONS * app.MAGNET_FIELD_ZERO_MAX_STEP_A
        self.assertGreater(reach - app.STFMR_KEPCO_MAX_CURRENT_A, 5.0)
        self.assertEqual(app.MAGNET_FIELD_ZERO_MAX_ITERATIONS, 12, "MOKE zeroing unchanged")

    def test_the_start_dialog_estimates_with_the_new_limit(self):
        a = bare_app()
        a.root = None
        a.stfmr_fmr_running = a.stfmr_frequency_running = False
        a._stfmr_frequency_inputs = lambda: (("S21",), [1e9, 2e9], -10.0, 2700.0, 10.0, 5.0, 0.5)
        with mock.patch.object(app.messagebox, "askyesno", return_value=False) as ask:
            a.start_stfmr_frequency_scan()
        expected = (2 + app.STFMR_FIELD_FEEDBACK_MAX_ITERATIONS) * 0.5 + 2 * 0.25
        self.assertIn(f"Estimated at most about: {expected:.1f} s", ask.call_args.args[1])
        self.assertFalse(a.stfmr_frequency_running, "declined: nothing started")


class FakeSR830:
    """Front panel at a chosen SENS; the reading saturates above full scale."""

    def __init__(self, sens, signal_v):
        self.sens, self.signal, self.writes = sens, signal_v, []

    def full_scale(self):
        return app.NONLOCAL_SR830_FULL_SCALE_V[self.sens]

    def write(self, command):
        self.writes.append(command)
        if command.startswith("SENS "):
            self.sens = int(command.split()[1])

    def query(self, command):
        if command.startswith("SNAP?"):
            r = min(self.signal, 1.09 * self.full_scale())
            return f"{r},0,{r},0,1000.0"
        if command == "LIAS?":
            return "1" if self.signal > self.full_scale() else "0"
        if command == "SENS?":
            return str(self.sens)
        raise AssertionError(command)


def sr830_on(sens, signal_v):
    controller = app.STFMRSR830Controller(lambda message: None)   # as after Connect
    controller.instrument = FakeSR830(sens, signal_v)
    return controller


class Sr830IdleReadTests(unittest.TestCase):
    """#17: the idle live read stepped the SR830 range from an assumed SENS 13."""

    def test_a_read_only_sample_never_changes_the_range(self):
        controller = sr830_on(26, 0.5)           # front panel at 1 V
        for _poll in range(5):
            sample = controller.read_sample(1000.0, auto_range=False)
        self.assertEqual([w for w in controller.instrument.writes if w.startswith("SENS")], [])
        self.assertEqual(controller.instrument.sens, 26)
        self.assertEqual(sample["r_v"], 0.5)
        self.assertEqual(sample["auto_range"], "")

    def test_the_scan_read_still_autoranges(self):
        controller = sr830_on(13, 0.5)           # as configure() leaves it
        controller.sensitivity_index, controller.auto_range_enabled = 13, True
        sample = controller.read_sample(1000.0)
        self.assertEqual(controller.instrument.writes, ["SENS 14"])
        self.assertIn("SENS 13->14", sample["auto_range"])

    def live_app(self, meter, controller):
        a = bare_app()
        a.closing = a.stfmr_lockin_closed = a.stfmr_lockin_running = False
        a.stfmr_lockin_live_future = None
        a.stfmr_lockin_meter_var = Var(meter)
        a.stfmr_lockin_gauss_continuous_var = Var(False)
        # The idle check expects Mod. start (2026-10-13).
        a.stfmr_lockin_mod_start_var = Var("1000")
        a.stfmr_lockin_controllers = {"830": controller, "gauss": types.SimpleNamespace(connected=False)}
        a.stfmr_lockin_mfli_controller = types.SimpleNamespace(
            mfli_connected=True,
            read_mfli=lambda hz: {"X": 1e-6, "Y": 0.0, "R": 1e-6, "phase_deg": 0.0,
                                  "reference_frequency_hz": 1000.0, "reference_valid": True,
                                  "input_overload": False})
        a.stfmr_lockin_executor = Now()
        a.post_ui = lambda callback: callback()
        a.root = Root(Clock())
        a.stfmr_lockin_reference_indicator = None
        a.stfmr_lockin_voltage_var, a.stfmr_lockin_reference_status_var = Var(), Var()
        a.stfmr_lockin_status_var = Var()
        return a

    def test_the_idle_live_timer_reads_without_range_writes(self):
        for meter in ("SR830", "MFLI + SR830"):
            with self.subTest(meter=meter):
                controller = sr830_on(26, 0.5)
                a = self.live_app(meter, controller)
                for _poll in range(5):
                    a._stfmr_lockin_live_timer()
                    a.root.queue.clear()
                self.assertEqual(controller.instrument.writes, [], "no SENS or other write")
                self.assertEqual(a.stfmr_lockin_status_var.get(), "", "no live read failure")
                self.assertIn("0.5", a.stfmr_lockin_voltage_var.get())


class FmrKepcoReadoutTests(unittest.TestCase):
    """#18: the FMR readout showed 'OUTP: OFF' for an output it knew nothing
    about, and kept it after a failed Zero / OFF."""

    def fmr_app(self, card):
        a = bare_app()
        a.closing = False
        a.root = None
        a.stfmr_fmr_running = a.stfmr_frequency_running = False
        a.stfmr_controllers = {"kepco": connected_4886(card)}
        kepco_vars(a)
        a.stfmr_safety_var = Var(True)
        a.stfmr_output_mode_var, a.stfmr_output_target_var, a.stfmr_output_limit_var = (
            Var("Current"), Var("2"), Var("5"))
        return a

    def test_the_label_starts_unknown(self):
        # Created in the Tk window build, which these tests never run.
        with open(app.__file__, encoding="utf-8") as source:
            text = source.read()
        self.assertIn('self.stfmr_kepco_output_var = tk.StringVar(value="OUTP: --")', text)
        self.assertNotIn('stfmr_kepco_output_var = tk.StringVar(value="OUTP: OFF")', text)

    def test_connect_shows_unknown_and_sends_nothing(self):
        card = Card4886()
        a = self.fmr_app(card)
        a.read_stfmr_kepco_once()           # what Connect and "Show sent values" call
        self.assertEqual(readout(a), ["Mode: --", "I: -- A", "V: -- V", "OUTP: --"])
        self.assertEqual(card.writes, [])
        self.assertIn("unknown", a.stfmr_status_var.get())

    def test_a_failed_zero_is_not_shown_as_off(self):
        card = Card4886(stall={"OUTP OFF"})
        a = self.fmr_app(card)
        a.stfmr_kepco_output_var.set("OUTP: OFF")
        with mock.patch.object(app.messagebox, "showerror") as error:
            a.zero_stfmr_kepco_output()
        error.assert_called_once()
        self.assertEqual(card.writes[-1], "OUTP OFF")
        self.assertEqual(a.stfmr_kepco_output_var.get(), "OUTP: NOT CONFIRMED OFF")
        a.read_stfmr_kepco_once()           # "Show sent values" agrees
        self.assertEqual(a.stfmr_kepco_output_var.get(), "OUTP: NOT CONFIRMED OFF")

    def test_a_failed_apply_whose_clean_up_also_fails_is_flagged(self):
        card = Card4886(stall={"OUTP ON", "OUTP OFF"})
        a = self.fmr_app(card)
        with mock.patch.object(app.messagebox, "showerror"):
            a.apply_stfmr_kepco_output()
        self.assertIn("OUTP OFF", card.writes, "clean-up was attempted")
        self.assertEqual(a.stfmr_kepco_output_var.get(), "OUTP: NOT CONFIRMED OFF")

    def test_a_refused_target_leaves_an_output_that_is_on_shown_on(self):
        card = Card4886()
        a = self.fmr_app(card)
        a.apply_stfmr_kepco_output()
        self.assertEqual(a.stfmr_kepco_output_var.get(), "OUTP: ON")
        sent = list(card.writes)
        a.stfmr_output_target_var.set("12")       # beyond ±10 A: refused, nothing sent
        with mock.patch.object(app.messagebox, "showerror"):
            a.apply_stfmr_kepco_output()
        self.assertEqual(card.writes, sent)
        self.assertEqual(a.stfmr_kepco_output_var.get(), "OUTP: ON")

    def test_a_good_zero_still_shows_off(self):
        card = Card4886()
        a = self.fmr_app(card)
        a.apply_stfmr_kepco_output()
        a.zero_stfmr_kepco_output()
        self.assertEqual(readout(a)[3], "OUTP: OFF")

    def test_a_scan_end_whose_zero_fails_does_not_report_off(self):
        for kind in ("fmr", "frequency"):
            with self.subTest(kind=kind):
                card = Card4886()
                a = self.fmr_app(card)
                a.apply_stfmr_kepco_output()      # the scan's output is on
                card.stall = {"OUTP OFF"}
                setattr(a, f"stfmr_{kind}_running", True)
                setattr(a, f"stfmr_{kind}_after_id", None)
                setattr(a, f"stfmr_{kind}_rows", [])
                setattr(a, f"stfmr_{kind}_status_var", Var())
                setattr(a, f"stfmr_{kind}_save_button", mock.Mock())
                a.stfmr_fmr_rf_off_var = Var(False)
                a._set_stfmr_scan_busy = lambda busy: None
                finish = getattr(a, f"_finish_stfmr_{kind}_scan")
                with mock.patch.object(app.messagebox, "showwarning") as warning:
                    finish(stopped=True)
                warning.assert_called_once()
                self.assertEqual(a.stfmr_kepco_output_var.get(), "OUTP: NOT CONFIRMED OFF")
                status = getattr(a, f"stfmr_{kind}_status_var").get()
                self.assertIn("NOT CONFIRMED", status)
                self.assertNotIn("zeroed/OFF", status)


if __name__ == "__main__":
    unittest.main()
