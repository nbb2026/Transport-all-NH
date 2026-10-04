"""Set H and Zero H on the PPMS page end Persistent, and the status line says
when the PPMS reports Persistent at the setpoint.

Offline, without Tk: the field commands go to a mock and readings are fed in
by hand, each with the time it was taken.
"""
import inspect
import threading
import types
import unittest
from concurrent.futures import Future
from unittest.mock import Mock, patch

from test_ppms_motor_debug import app

GOING = "Going to +5000 Oe, then Persistent…"
CONFIRMED = "Field +5000 Oe reached; Persistent confirmed"


class Var:
    def __init__(self, value=None): self.value = value
    def get(self): return self.value
    def set(self, value): self.value = value


class ImmediateExecutor:
    def submit(self, fn, *args):
        result = Future()
        try: result.set_result(fn(*args))
        except BaseException as error: result.set_exception(error)
        return result


class Clock:
    def __init__(self): self.now = 1000.
    def __call__(self): return self.now


def reading(field, status):
    return dict(field_oe=float(field), field_status=status, temperature_k=300., temperature_status="Stable")


class PersistentSetpointTests(unittest.TestCase):
    def make_panel(self, cls=app.CombinedControlApp):
        panel = cls.__new__(cls)
        if cls is not app.CombinedControlApp:
            panel.parent_app = types.SimpleNamespace()
        panel.closing = False
        panel.root = None
        panel.ppms_environment_busy = False
        panel.ppms_field_scan_running = False
        panel.ppms_field_scan_stop_event = threading.Event()
        panel.ppms_executor = ImmediateExecutor()
        panel.post_ui = lambda callback: callback()
        panel._require_ppms_control_ready = lambda: None
        panel._set_ppms_environment_ui = lambda connected: None
        panel._draw_ppms_helium_history = lambda: None
        panel._check_ppms_helium_warning = lambda result: None
        for name in ("ppms_connection_var", "ppms_temperature_var", "ppms_temperature_status_var",
                     "ppms_field_var", "ppms_field_status_var"):
            setattr(panel, name, Var(""))
        panel.ppms_field_target_var = Var("5000")
        panel.ppms_field_rate_var = Var("100")
        panel.ppms_field_approach_var = Var("No overshoot")
        panel.ppms_field_mode_var = Var("Persistent")
        panel.ppms_environment_controller = Mock()
        self.clock = Clock()
        patcher = patch.object(app.time, "monotonic", self.clock)
        patcher.start()
        self.addCleanup(patcher.stop)
        return panel

    def press(self, panel, button, before):
        """Press a field button; the command's own readback shows ``before``."""
        panel.ppms_environment_controller.set_field.return_value = reading(*before)
        button()
        self.sent = self.clock.now

    def feed(self, panel, field, status, after=None, taken=None):
        """A live reading arriving ``after`` s after the command, taken at ``taken``."""
        if after is not None:
            self.clock.now = self.sent + after
        data = dict(reading(field, status), read_at=self.clock.now if taken is None else taken)
        panel._apply_ppms_environment_readback(data)
        return panel.ppms_connection_var.get()

    def test_set_h_and_zero_h_send_the_persistent_end_mode(self):
        for cls in (app.CombinedControlApp, app.PPMSPanel):
            with self.subTest(cls=cls.__name__):
                panel = self.make_panel(cls)
                self.press(panel, panel.set_ppms_field, (0, "StablePersistent"))
                panel.ppms_environment_controller.set_field.assert_called_once_with(5000., 100., 1, 0)
                self.assertEqual(panel.ppms_connection_var.get(), GOING)
                panel.ppms_environment_controller.reset_mock()
                self.press(panel, panel.zero_ppms_field, (5000, "StablePersistent"))
                panel.ppms_environment_controller.set_field.assert_called_once_with(0., 100., 1, 0)
                self.assertEqual(panel.ppms_field_target_var.get(), "0")
                self.assertEqual(panel.ppms_connection_var.get(), "Going to +0 Oe, then Persistent…")

    def test_status_follows_the_field_until_persistent_at_the_setpoint(self):
        panel = self.make_panel()
        self.press(panel, panel.set_ppms_field, (0, "StablePersistent"))
        for after, field, status in ((1, 0, "WarmingSwitch"), (20, 2000, "Charging"),
                                     (50, 5000, "Charging"), (60, 5000, "CoolingSwitch")):
            self.assertEqual(self.feed(panel, field, status, after), GOING, status)
        self.assertEqual(panel.ppms_field_status_var.get(), "Status: CoolingSwitch")
        self.assertEqual(self.feed(panel, 5000.4, "StablePersistent", 90), CONFIRMED)
        self.assertEqual(panel.ppms_field_status_var.get(), "Status: StablePersistent")
        self.assertIsNone(panel.ppms_persistent_watch)
        panel.ppms_connection_var.set("Temperature command")
        self.assertEqual(self.feed(panel, 5000, "StablePersistent", 200), "Temperature command",
                         "said once, then the line is free for other commands")

    def test_zero_h_confirms_persistent_within_the_field_tolerance(self):
        panel = self.make_panel()
        self.press(panel, panel.zero_ppms_field, (5000, "StablePersistent"))
        self.feed(panel, 2500, "Discharging", 25)
        self.assertEqual(self.feed(panel, -0.2181174, "StablePersistent", 80),
                         "Field +0 Oe reached; Persistent confirmed")

    def test_readings_taken_before_the_command_never_count(self):
        # Another target: a late reading of the old Persistent field must not
        # be reported as "Persistent away from the setpoint".
        panel = self.make_panel()
        self.press(panel, panel.set_ppms_field, (0, "WarmingSwitch"))
        self.assertEqual(self.feed(panel, 0, "StablePersistent", 0.2, taken=self.sent - 0.3), GOING)
        self.assertIsNotNone(panel.ppms_persistent_watch)
        # The same field again: a late reading from before the switch cycle
        # must not confirm it.
        panel = self.make_panel()
        self.press(panel, panel.set_ppms_field, (5000, "WarmingSwitch"))
        self.assertEqual(self.feed(panel, 5000, "StablePersistent", 15, taken=self.sent - 0.3), GOING)
        self.assertEqual(self.feed(panel, 5000, "CoolingSwitch", 20), GOING)
        self.assertEqual(self.feed(panel, 5000, "StablePersistent", 40), CONFIRMED)
        # Readings without a time (other commands' readbacks) say nothing either.
        panel = self.make_panel()
        self.press(panel, panel.set_ppms_field, (0, "WarmingSwitch"))
        self.clock.now = self.sent + 100
        panel._apply_ppms_environment_readback(reading(5000, "StablePersistent"))
        self.assertEqual(panel.ppms_connection_var.get(), GOING)

    def test_already_persistent_at_the_setpoint_is_confirmed_after_a_few_seconds(self):
        panel = self.make_panel()
        self.press(panel, panel.set_ppms_field, (5000, "StablePersistent"))
        self.assertEqual(self.feed(panel, 5000, "StablePersistent", 1), GOING)
        self.assertEqual(self.feed(panel, 5000, "StablePersistent", app.PPMS_PERSISTENT_CONFIRM_S - 0.5), GOING)
        self.assertEqual(self.feed(panel, 5000, "StablePersistent", app.PPMS_PERSISTENT_CONFIRM_S), CONFIRMED)

    def test_persistent_away_from_the_setpoint_is_reported_not_confirmed(self):
        panel = self.make_panel()
        self.press(panel, panel.set_ppms_field, (0, "StablePersistent"))
        # Not acted on yet: Persistent at the old field is only waited on.
        self.assertEqual(self.feed(panel, 0, "StablePersistent", 30), GOING)
        self.feed(panel, 1000, "Charging", 40)
        text = self.feed(panel, 4997, "StablePersistent", 90)
        self.assertEqual(text, "Persistent at +4997 Oe, not +5000 Oe; check MultiVu, not resent")
        self.assertIsNone(panel.ppms_persistent_watch)
        panel.ppms_environment_controller.set_field.assert_called_once()  # Nothing resent.

    def test_gives_up_after_the_ramp_time_plus_the_margin(self):
        panel = self.make_panel()
        self.press(panel, panel.set_ppms_field, (0, "StablePersistent"))
        limit = 5000 / 100 + app.PPMS_PERSISTENT_MARGIN_S
        self.assertEqual(self.feed(panel, 5000, "StableDriven", limit - 1), GOING)
        self.assertEqual(self.feed(panel, 5000, "StableDriven", limit),
                         "Not Persistent at +5000 Oe in time (StableDriven); check MultiVu, not resent")
        self.assertIsNone(panel.ppms_persistent_watch)
        panel.ppms_environment_controller.set_field.assert_called_once()

    def test_a_field_scan_or_macro_takes_the_field_over(self):
        for mode, follows in (("Field", False), ("Field+Temperature", False),
                              ("Temperature", True), ("Angle", True)):
            with self.subTest(mode=mode):
                panel = self.make_panel()
                self.press(panel, panel.set_ppms_field, (0, "StablePersistent"))
                panel.ppms_field_scan_running = True
                panel.ppms_active_scan_mode = mode
                self.feed(panel, 2000, "Charging", 20)
                text = self.feed(panel, 5000, "StablePersistent", 90)
                self.assertEqual(text == CONFIRMED, follows, text)
        panel = self.make_panel()
        self.press(panel, panel.set_ppms_field, (0, "StablePersistent"))
        panel.ppms_macro_running = True
        self.assertEqual(self.feed(panel, 2000, "Charging", 20), GOING)
        self.assertIsNone(panel.ppms_persistent_watch)

    def test_a_failed_command_starts_no_follow_up(self):
        panel = self.make_panel()
        panel.ppms_environment_controller.set_field.side_effect = RuntimeError("refused (test)")
        with patch.object(app, "messagebox") as box:
            panel.set_ppms_field()
        box.showerror.assert_called_once()
        self.assertTrue(panel.ppms_connection_var.get().startswith("Command failed"))
        self.assertIsNone(panel.__dict__.get("ppms_persistent_watch"))

    def test_a_new_setpoint_replaces_the_follow_up(self):
        panel = self.make_panel()
        self.press(panel, panel.set_ppms_field, (0, "StablePersistent"))
        panel.ppms_field_mode_var.set("Driven")
        panel.ppms_field_target_var.set("3000")
        self.press(panel, panel.set_ppms_field, (100, "Charging"))
        panel.ppms_environment_controller.set_field.assert_called_with(3000., 100., 1, 1)
        self.assertIsNone(panel.ppms_persistent_watch)
        self.assertEqual(panel.ppms_connection_var.get(), "Command completed; live readback in progress")
        self.assertEqual(self.feed(panel, 5000, "StablePersistent", 90),
                         "Command completed; live readback in progress")

    def test_disconnect_ends_the_follow_up(self):
        panel = self.make_panel()
        self.press(panel, panel.set_ppms_field, (0, "StablePersistent"))
        panel.ppms_control_safety_var = Var(True)
        panel.ppms_environment_controller.disconnect = Mock()
        panel.disconnect_ppms_environment()
        self.assertIsNone(panel.ppms_persistent_watch)

    def test_live_readings_carry_the_time_they_were_taken(self):
        panel = self.make_panel()
        panel.ppms_environment_controller = types.SimpleNamespace(
            backend="fake", read_environment=lambda: reading(0, "StablePersistent"))
        panel.ppms_instrument_controllers = {}
        panel._read_ppms_current_display = lambda: "OFF / 0 mA"
        panel._read_ppms_cryogen_display = lambda: {}
        panel._ppms_sr830_keys = lambda: ()
        self.clock.now = 1234.5
        self.assertEqual(panel._read_ppms_live()["read_at"], 1234.5)

    def test_field_scans_and_their_clean_up_still_send_driven(self):
        for name in ("_ppms_nested_scan_worker", "_ppms_field_scan_worker",
                     "_ppms_zero_field_after_interrupted_scan"):
            source = inspect.getsource(getattr(app.CombinedControlApp, name))
            self.assertNotIn("ppms_field_mode_var", source, name)
            self.assertNotIn("_send_ppms_field_setpoint", source, name)


if __name__ == "__main__":
    unittest.main()
