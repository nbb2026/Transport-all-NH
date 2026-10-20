"""The Avtech's Pulse count (user, 2026-10-20: "add pulse count function in
avtech", "like 6221"), and the safety tick and status line removed ("delete
the two rows at bottom"; the tick: "Remove completely").

N pulses are N repetitions of the reviewed single-pulse sequence, each with
the output read back off, AVTECH_PULSE_GAP_S apart; a failure, an overload
or Avtech output off stops the train with the output off.  Against the
simulated generator of test_avtech_pulser.  No GPIB, no window.
"""
import concurrent.futures
import inspect
import threading
import unittest
from unittest import mock

from test_avtech_pulser import FakeAvtech, controller
from test_nonlocal_keithley import Var, bare_app
from test_ppms_motor_debug import app


def sequences(log):
    """The log split at each "*CLS" (one per single-pulse sequence)."""
    starts = [i for i, entry in enumerate(log) if entry == "*CLS"]
    return [log[a:b] for a, b in zip(starts, starts[1:] + [len(log)])]


class CountTests(unittest.TestCase):
    def test_whole_numbers_1_to_10000(self):
        validate = app.AvtechPulserController.validate_count
        self.assertEqual((app.AVTECH_DEFAULT_PULSE_COUNT, app.AVTECH_MAX_PULSE_COUNT), (1, 10_000))
        for text, value in (("1", 1), (" 7 ", 7), ("10000", 10_000), (3, 3)):
            self.assertEqual(validate(text), value)
        for bad in ("0", "10001", "1.5", "10.0", "1e3", "-1", "+2", "", "abc", "1_000", 0, 2.0, True, None):
            with self.subTest(bad=bad), self.assertRaisesRegex(ValueError, "whole number from 1 to 10000"):
                validate(bad)

    def test_the_gap_is_within_the_duty_cycle(self):
        self.assertEqual(app.AVTECH_PULSE_GAP_S, 1.0)
        self.assertLessEqual(app.AVTECH_MAX_WIDTH_S / app.AVTECH_PULSE_GAP_S, 0.05)

    def test_a_bad_count_sends_nothing(self):
        fake = FakeAvtech()
        with self.assertRaises(ValueError):
            controller(fake).pulse_train(20.0, 10e-9, "0", gap_s=0)
        self.assertEqual(fake.log, [])


class TrainTests(unittest.TestCase):
    def test_n_pulses_are_n_sequences_each_read_back_off(self):
        fake = FakeAvtech()
        result = controller(fake).pulse_train(20.0, 10e-9, 3, gap_s=0, stop_event=threading.Event())
        self.assertEqual(fake.pulses, [(20.0, 1e-8)] * 3)
        parts = sequences(fake.log)
        self.assertEqual(len(parts), 3)
        for part in parts:
            # Each the whole single-pulse sequence: settings read back, one
            # trigger, then hold, off and the off read back.
            for step in ("trigger:source hold", "pulse:delay 0", "pulse:width?", "volt:ampl?", "output on"):
                self.assertIn(step, part)
            self.assertEqual(part.count("trigger:source immediate"), 1)
            after = part[part.index("trigger:source immediate") + 1:]
            off = after.index("output off")
            self.assertEqual(after[off + 1], "output?", "off read back after every pulse")
        self.assertEqual((fake.output, fake.trigger), (0, "hold"))
        self.assertEqual((result["pulses"], result["count"], result["stopped"], result["overload"]),
                         (3, 3, False, False))

    def test_the_gap_is_waited_between_pulses_only(self):
        fake, stop = FakeAvtech(), mock.Mock()
        stop.is_set.return_value = False
        stop.wait.return_value = False
        controller(fake).pulse_train(20.0, 10e-9, 3, gap_s=1.0, stop_event=stop)
        self.assertEqual(stop.wait.call_args_list, [mock.call(1.0)] * 2)

    def test_a_failure_mid_train_stops_it_with_the_output_off(self):
        fake = FakeAvtech(fail_queries={("volt:ampl?", 2)})
        with self.assertRaises(app.AvtechPulseError) as caught:
            controller(fake).pulse_train(20.0, 10e-9, 5, gap_s=0)
        self.assertEqual((caught.exception.pulse_index, caught.exception.pulses_done), (2, 1))
        self.assertEqual(len(fake.pulses), 1)
        self.assertEqual(len(sequences(fake.log)), 2, "nothing after the failed pulse")
        self.assertEqual((fake.output, fake.trigger), (0, "hold"))
        last_off = len(fake.log) - 1 - fake.log[::-1].index("output off")
        self.assertEqual(fake.log[last_off + 1], "output?", "the off read back")

    def test_an_output_that_stays_on_stops_the_train(self):
        fake = FakeAvtech(stuck_after_pulse=True)
        with self.assertRaisesRegex(app.AvtechPulseError, "check the generator") as caught:
            controller(fake).pulse_train(20.0, 10e-9, 5, gap_s=0)
        self.assertEqual(caught.exception.pulse_index, 1)
        self.assertEqual(len(fake.pulses), 1)

    def test_an_overload_mid_train_stops_it(self):
        class TripsOnSecond(FakeAvtech):
            def write(self, command):
                super().write(command)
                if command == "trigger:source immediate" and len(self.pulses) == 2:
                    self.tripped = 1
                    self.errors.append('1001,"Device-specific error; Overload condition detected!"')

        fake = TripsOnSecond()
        result = controller(fake).pulse_train(20.0, 10e-9, 5, gap_s=0)
        self.assertEqual(len(fake.pulses), 2)
        self.assertEqual((result["pulses"], result["overload"], result["stopped"]), (2, True, False))
        self.assertEqual(fake.output, 0)

    def test_output_off_mid_train_stops_it_and_reads_the_off_back(self):
        fake, stop = FakeAvtech(), threading.Event()

        def progress(index, _total):
            if index == 2:
                stop.set()  # pressed during pulse 2

        result = controller(fake).pulse_train(20.0, 10e-9, 5, gap_s=0, stop_event=stop, progress=progress)
        self.assertEqual(len(fake.pulses), 2, "the pulse in progress ends, no more")
        self.assertEqual((result["pulses"], result["stopped"]), (2, True))
        self.assertEqual(fake.log[-4:], ["<device clear>", "trigger:source hold", "output off", "output?"],
                         "the stop's own off, read back")
        self.assertEqual(fake.output, 0)

    def test_a_failed_off_after_a_stop_leaves_the_output_unknown(self):
        fake, stop = FakeAvtech(), threading.Event()
        stop.set()
        with mock.patch.object(app.AvtechPulserController, "switch_off",
                               side_effect=RuntimeError("Avtech: output? failed; check the generator")):
            with self.assertRaisesRegex(app.AvtechPulseError, "stopped after 0/3 pulses") as caught:
                controller(fake).pulse_train(20.0, 10e-9, 3, gap_s=0, stop_event=stop)
        self.assertTrue(caught.exception.output_unknown)
        self.assertEqual(fake.pulses, [])


class OutputLabelTests(unittest.TestCase):
    text = staticmethod(app.CombinedControlApp._avtech_output_text)

    def test_the_texts(self):
        ok = {"amplitude_v": 40.0, "width_s": 200e-9, "overload": False}
        self.assertEqual(self.text(dict(ok, pulses=1), count=1), "Output: OFF; last +40 V 200 ns")
        self.assertEqual(self.text(dict(ok, pulses=10), count=10), "Output: OFF; last +40 V 200 ns (10/10)")
        self.assertEqual(self.text(dict(ok, pulses=3, stopped=True), count=10), "Output: OFF; stopped at 3/10")
        self.assertEqual(self.text(dict(ok, pulses=1, overload=True), count=1), "Output: OFF; OVERLOAD")
        self.assertEqual(self.text(dict(ok, pulses=4, overload=True), count=9), "Output: OFF; OVERLOAD at 4/9")
        failed = app.AvtechPulseError("no pulse fired: timeout")
        failed.pulse_index = 3
        self.assertEqual(self.text(error=failed, count=10), "Output: OFF; pulse failed at 3/10")
        self.assertEqual(self.text(error=app.AvtechPulseError("x; check the generator"), count=1),
                         "Output: check the Avtech")


def page(fake):
    a = bare_app()
    a.avtech_controller = controller(fake)
    a.avtech_controller.lock = threading.RLock()
    a.avtech_amplitude_var, a.avtech_width_var, a.avtech_count_var = Var("20"), Var("10"), Var("3")
    a.avtech_output_var = Var("Output: OFF")
    a.avtech_stop_event = threading.Event()
    a.avtech_busy = a.k6221_busy = a.k6221_scan_running = False
    a.avtech_output_unknown = a.k6221_output_unknown = a.avtech_train_running = False
    a.closing, a.root = True, None
    a.ui = []
    a.post_ui = a.ui.append
    a._update_k6221_controls = mock.Mock()
    a.logged = []
    a.append_log = a.logged.append
    return a


class ApplyTests(unittest.TestCase):
    def run_train(self, a):
        with concurrent.futures.ThreadPoolExecutor(max_workers=1) as executor:
            a.avtech_executor = executor
            with mock.patch.object(app, "AVTECH_PULSE_GAP_S", 0.0), \
                    mock.patch.object(app.messagebox, "showwarning") as warning:
                app.CombinedControlApp.apply_avtech_pulse(a, -1)
        for callback in list(a.ui):  # the Tk thread's drain
            callback()
        return warning

    def test_apply_fires_count_pulses_off_the_tk_thread_without_a_tick(self):
        threads = []

        class Recording(FakeAvtech):
            def write(self, command):
                threads.append(threading.current_thread())
                super().write(command)

            def query(self, command):
                threads.append(threading.current_thread())
                return super().query(command)

        fake = Recording()
        a = page(fake)
        self.assertFalse(hasattr(a, "avtech_safety_var"))
        warning = self.run_train(a)
        warning.assert_not_called()
        self.assertEqual(fake.pulses, [(-20.0, 1e-8)] * 3)
        self.assertTrue(threads)
        self.assertNotIn(threading.main_thread(), threads, "every instrument call on the worker")
        self.assertFalse(a.avtech_busy or a.avtech_train_running or a.avtech_output_unknown)
        self.assertEqual(a.avtech_output_var.get(), "Output: OFF; last -20 V 10 ns (3/3)")
        self.assertIn("3 pulses of -20 V, 10 ns, 0 s apart", a.logged[0])  # the gap patched to 0 here
        self.assertIn("3/3 pulses -20 V, 10 ns (read back); output off", a.logged[-1])

    def test_progress_is_shown(self):
        a = page(FakeAvtech())
        seen = []
        a.avtech_output_var.set = seen.append
        self.run_train(a)
        for n in (1, 2, 3):
            self.assertIn(f"Output: pulse {n}/3", seen)

    def test_a_bad_count_is_refused_before_anything_is_sent(self):
        for bad in ("0", "10001", "2.5", "x"):
            with self.subTest(bad=bad):
                fake = FakeAvtech()
                a = page(fake)
                a.avtech_count_var = Var(bad)
                a.avtech_executor = mock.Mock()
                with mock.patch.object(app.messagebox, "showwarning") as warning:
                    app.CombinedControlApp.apply_avtech_pulse(a, 1)
                warning.assert_called_once()
                a.avtech_executor.submit.assert_not_called()
                self.assertEqual(fake.log, [])

    def test_an_unknown_avtech_output_refuses_the_pulse(self):
        a = page(FakeAvtech())
        a.avtech_output_unknown = True
        a.avtech_executor = mock.Mock()
        with mock.patch.object(app.messagebox, "showwarning") as warning:
            app.CombinedControlApp.apply_avtech_pulse(a, 1)
        self.assertIn("Avtech's output state is unknown", warning.call_args.args[1])
        a.avtech_executor.submit.assert_not_called()

    def test_a_failure_mid_train_sets_the_label_and_logs(self):
        fake = FakeAvtech(stuck_after_pulse=True)
        a = page(fake)
        self.run_train(a)
        self.assertEqual(len(fake.pulses), 1)
        self.assertTrue(a.avtech_output_unknown, "holds the 6221 until an off reads back")
        self.assertEqual(a.avtech_output_var.get(), "Output: check the Avtech")
        self.assertIn("pulse 1/3", a.logged[-1])

    def test_output_off_during_a_train_sets_the_stop_and_sends_nothing_itself(self):
        a = page(FakeAvtech())
        a.avtech_busy = a.avtech_train_running = True
        a.avtech_executor = mock.Mock()
        app.CombinedControlApp.avtech_output_off(a)
        self.assertTrue(a.avtech_stop_event.is_set())
        a.avtech_executor.submit.assert_not_called()


class NoTickTests(unittest.TestCase):
    def test_no_avtech_tick_anywhere(self):
        source = inspect.getsource(app)
        self.assertNotIn("avtech_safety_var", source)
        self.assertNotIn("avtech_status_var", source)
        self.assertNotIn("Tick the Avtech", source)

    def test_the_scan_fires_one_pulse_per_point(self):
        """The CIDWM pulse sign scan ignores the Pulse count box."""
        source = inspect.getsource(app.CombinedControlApp._cidwm_pulse_scan_pulses)
        self.assertIn("single_pulse(", source)
        self.assertNotIn("pulse_train", source)
        self.assertNotIn("avtech_count_var", source)

    def test_the_scan_start_asks_no_tick_for_the_avtech(self):
        source = inspect.getsource(app.CombinedControlApp.start_cidwm_pulse_scan)
        self.assertIn('if source == "6221" and not self.k6221_safety_var.get()', source)


if __name__ == "__main__":
    unittest.main()
