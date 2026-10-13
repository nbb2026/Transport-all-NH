"""LED, MOKE motor and camera-row minor fixes (audit, 2026-10-13).

1. LED: an empty or non-numeric Current or p/g/d/f box (or the "Continuous
   current, all 4" box) raised TclError outside the integer check, so Apply,
   Apply all, the presets and Set continuous current did nothing and showed
   nothing.  Now they show "Input error" and send nothing.
2. LED: a single channel's Apply in Pulse mode asks the same confirmation as
   Apply all.
3. MOKE motor: the Step refusal no longer says 0 is allowed.
4. MOKE motor: repeated Up/Down steps cannot pass the ±100000 µm software
   range that Go to absolute position enforces.
5. Camera settings rows: averaging and exposure changes also show on the
   CIDWM status line (the rows are on that page too).

No Tk and no hardware: the real methods run on stand-ins, every dialog is
mocked, and no command is sent (the LED and motor workers are never run on a
port)."""
import tkinter.messagebox as tk_messagebox
import types
import unittest
from unittest import mock

from test_camera_settings_rows import camera, click, no_dialogs, page
from test_nonlocal_keithley import Var, bare_app
from test_ppms_motor_debug import app

MotorApp, motor_self_test = app.create_motor_subsystem()

_no_tk = mock.patch.object(
    app.tk, "Tk", side_effect=AssertionError("a test reached a real Tk window"))


def setUpModule():
    # A dialog that is not mocked would create a Tk root: fail instead.
    _no_tk.start()


def tearDownModule():
    _no_tk.stop()


def no_motor_dialogs(test):
    """The motor subsystem imports tkinter.messagebox itself, so patching
    app.messagebox does not reach it: mock every dialog of that module."""
    for name in ("showinfo", "showwarning", "showerror", "askquestion", "askokcancel",
                 "askyesno", "askyesnocancel", "askretrycancel"):
        patcher = mock.patch.object(tk_messagebox, name, mock.Mock())
        patcher.start()
        test.addCleanup(patcher.stop)


class BoxIntVar:
    """A tk.IntVar without Tk: the real IntVar.get runs on the text of its
    box through a stand-in interpreter that refuses what Tcl refuses."""

    get = app.tk.IntVar.get

    def __init__(self, text="0"):
        self.text = text
        self._tk = self
        self._name = "box"

    def set(self, value):
        self.text = value

    def globalgetvar(self, _name):
        return self.text

    @staticmethod
    def getint(value):
        try:
            return int(value)
        except (TypeError, ValueError):
            raise app.tk.TclError(f'expected integer but got "{value}"') from None

    @staticmethod
    def getdouble(value):
        try:
            return float(value)
        except (TypeError, ValueError):
            raise app.tk.TclError(f'expected floating-point number but got "{value}"') from None


def led_page():
    a = bare_app()
    a.channel_vars = {
        channel: {"i": BoxIntVar("100"), "m": Var("Continuous"), "p": BoxIntVar("5"),
                  "g": BoxIntVar("0"), "d": BoxIntVar("5"), "f": BoxIntVar("0")}
        for channel in app.CHANNELS
    }
    a.global_current_var = BoxIntVar("200")
    a.auto_readback_var = Var(False)
    a.run_task = mock.Mock()
    return a


class LedInputTests(unittest.TestCase):
    def setUp(self):
        no_dialogs(self)

    def test_the_stand_in_box_raises_like_tk(self):
        for text in ("", "abc", " "):
            with self.subTest(text=text), self.assertRaises(app.tk.TclError):
                BoxIntVar(text).get()
        self.assertEqual(BoxIntVar("7").get(), 7)

    def test_a_bad_box_shows_input_error_and_sends_nothing(self):
        buttons = (
            ("Apply (channel 2)", lambda a: a.apply_channel(2), ("i", "p", "g", "d", "f")),
            ("Apply all", lambda a: a.apply_all(), ("i", "p", "g", "d", "f")),
            # The presets write every Current box, so only p/g/d/f can be bad.
            ("All 4 currents preset", lambda a: a.apply_preset(500), ("p", "g", "d", "f")),
            ("All 4 continuous on", lambda a: a.all_continuous(), ("i", "p", "g", "d", "f")),
        )
        for label, press, names in buttons:
            for name in names:
                for text in ("", "abc"):
                    with self.subTest(button=label, box=name, text=text):
                        a = led_page()
                        a.channel_vars[2][name].set(text)
                        with mock.patch.object(app.messagebox, "showerror") as error:
                            press(a)    # raised TclError before the fix
                        error.assert_called_once()
                        self.assertEqual(error.call_args.args[0], "Input error")
                        self.assertIn(f"Channel 2 {'current' if name == 'i' else name}",
                                      error.call_args.args[1])
                        a.run_task.assert_not_called()

    def test_set_continuous_current_with_a_bad_box(self):
        for text in ("", "abc"):
            with self.subTest(text=text):
                a = led_page()
                a.global_current_var.set(text)
                with mock.patch.object(app.messagebox, "showerror") as error:
                    a.apply_global_continuous_current()
                error.assert_called_once()
                self.assertIn("Continuous current for all 4", error.call_args.args[1])
                a.run_task.assert_not_called()
                # The channel boxes are left as they were.
                self.assertEqual(a.channel_vars[1]["i"].get(), 100)

    def test_valid_boxes_still_apply(self):
        a = led_page()
        a.global_current_var.set("300")
        a.apply_global_continuous_current()
        a.run_task.assert_called_once()
        label, work = a.run_task.call_args.args[:2]
        self.assertEqual(label, "Setting all four channels…")
        a.controller = mock.Mock()
        work()
        sent = a.controller.apply_all.call_args.args[0]
        self.assertEqual({channel: (s.current, s.mode) for channel, s in sent.items()},
                         {channel: (300, 1) for channel in app.CHANNELS})


class LedPulseConfirmTests(unittest.TestCase):
    def setUp(self):
        no_dialogs(self)

    def test_single_channel_pulse_asks_first(self):
        for answer in (False, True):
            with self.subTest(answer=answer):
                a = led_page()
                a.channel_vars[3]["m"].set("Pulse")
                with mock.patch.object(app.messagebox, "askyesno", return_value=answer) as ask:
                    a.apply_channel(3)
                ask.assert_called_once()
                self.assertEqual(ask.call_args.args[0], "Confirm pulse mode")
                self.assertEqual(a.run_task.called, answer)
                if answer:
                    a.controller = mock.Mock()
                    a.run_task.call_args.args[1]()
                    channel, settings = a.controller.apply_channel.call_args.args
                    self.assertEqual((channel, settings.mode), (3, 2))

    def test_continuous_and_off_are_not_asked(self):
        for mode in ("Continuous", "Off"):
            with self.subTest(mode=mode):
                a = led_page()
                a.channel_vars[1]["m"].set(mode)
                with mock.patch.object(app.messagebox, "askyesno") as ask:
                    a.apply_channel(1)
                ask.assert_not_called()
                a.run_task.assert_called_once()


def motor(step="2000", counts=0):
    return types.SimpleNamespace(
        root=None, step_var=Var(step), command_counts=counts, pending_target=None,
        safety_var=Var(True), bottom_status_var=Var(""), controller=mock.Mock(),
        _submit=mock.Mock(),
    )


def press(stand_in, direction):
    """Up (-1) or Down (+1); a submitted step is taken as written, as
    _finish_task does."""
    stand_in._submit.reset_mock()
    MotorApp.step_move(stand_in, direction)
    if stand_in._submit.called:
        stand_in._submit.call_args.args[1]()     # the command, on the mock controller
        stand_in.command_counts = stand_in.pending_target[0]
        stand_in.pending_target = None
        return True
    return False


class MotorStepTests(unittest.TestCase):
    def setUp(self):
        no_dialogs(self)
        no_motor_dialogs(self)

    def test_counts(self):
        motor_self_test()

    def test_a_zero_step_is_refused_with_a_true_message(self):
        for step in ("0.000", "0", "-5", "20001"):
            with self.subTest(step=step):
                stand_in = motor(step)
                with mock.patch.object(tk_messagebox, "showwarning") as warning:
                    self.assertFalse(press(stand_in, 1))
                text = warning.call_args.args[1]
                self.assertIn("greater than 0", text)
                self.assertIn("at most 20000 µm", text)
                self.assertNotIn("0–", text)

    def test_down_steps_stop_at_the_software_range(self):
        # The audit's case: the 7250 µm preset, then Down with Step 20000.
        stand_in = motor("20000", 742_400)
        limit = 100_000 * 102.4
        with mock.patch.object(tk_messagebox, "showwarning") as warning:
            for _ in range(18):
                self.assertTrue(press(stand_in, 1))
            warning.assert_not_called()
            before = stand_in.command_counts
            self.assertLessEqual(before, limit)
            self.assertFalse(press(stand_in, 1))     # the 19th passed before the fix
        warning.assert_called_once()
        self.assertIn("±100000 µm", warning.call_args.args[1])
        self.assertEqual(stand_in.command_counts, before)
        self.assertIsNone(stand_in.pending_target)
        self.assertEqual(stand_in.controller.write_target_counts.call_count, 18)

    def test_up_steps_reach_the_limit_exactly_but_not_past_it(self):
        # 20 Up steps of 20000 µm (512000 counts each) land on -10240000
        # counts, which is -100000 µm on the absolute scale: allowed, as Go
        # to absolute position allows it.
        stand_in = motor("20000", 0)
        with mock.patch.object(tk_messagebox, "showwarning") as warning:
            for _ in range(20):
                self.assertTrue(press(stand_in, -1))
            self.assertEqual(stand_in.command_counts, -10_240_000)
            warning.assert_not_called()
            self.assertFalse(press(stand_in, -1))
        warning.assert_called_once()
        self.assertEqual(stand_in.command_counts, -10_240_000)

    def test_a_step_in_range_writes_its_target(self):
        stand_in = motor("2000", 742_400)
        self.assertTrue(press(stand_in, 1))
        stand_in.controller.write_target_counts.assert_called_once_with(
            742_400 + 51_200, "Down 2000 µm")


class CameraRowStatusTests(unittest.TestCase):
    """The rows on the CIDWM page reported only to the MOKE magnet page."""

    def setUp(self):
        no_dialogs(self)
        self.a = page()
        self.a.attach_camera(camera())

    def test_averaging_shows_on_both_pages(self):
        click(self.a, "difference_mean_filter_var", 4)
        self.assertIn("4×4 Mean filter", self.a.k6221_status_var.get())
        self.assertEqual(self.a.k6221_status_var.get(), self.a.magnet_status_var.get())

    def test_exposure_shows_on_both_pages(self):
        self.a.camera_exposure_ms_var.set("50")
        self.a.apply_camera_exposure()
        self.assertIn("Updating camera exposure to 50 ms", self.a.k6221_status_var.get())
        self.assertEqual(self.a.k6221_status_var.get(), self.a.magnet_status_var.get())

    def test_no_camera_shows_on_both_pages(self):
        a = page()
        a.apply_difference_display_average()
        self.assertEqual(a.k6221_status_var.get(), "The camera has not started yet")
        self.assertEqual(a.magnet_status_var.get(), "The camera has not started yet")


if __name__ == "__main__":
    unittest.main()
