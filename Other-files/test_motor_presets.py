"""MOKE motor quick positions: 7250 µm and 6400 µm (user, 2026-10-12).

Each fills the absolute position and runs Go to absolute position, so it has
the same range check, safety tick and confirmation; both stay disabled until
the stage is homed.  No window is opened: the methods run on stand-ins.
"""
import types
import unittest
from unittest import mock

from test_ppms_motor_debug import app

MotorApp, motor_self_test = app.create_motor_subsystem()


class PresetTests(unittest.TestCase):
    def test_counts(self):
        motor_self_test()  # includes 6400 µm -> 655360 and 7250 µm -> 742400 counts

    def test_each_preset_goes_through_go_to_absolute(self):
        for method, value in ((MotorApp.goto_preset_6400, "6400"), (MotorApp.goto_preset_7250, "7250")):
            with self.subTest(value=value):
                calls = []
                stand_in = types.SimpleNamespace(
                    absolute_var=types.SimpleNamespace(set=lambda v: calls.append(("set", v))),
                    goto_absolute=lambda: calls.append("goto"),
                )
                method(stand_in)
                self.assertEqual(calls, [("set", value), "goto"])

    def controls(self, homed):
        names = ("goto_button", "preset_7250_button", "preset_6400_button", "up_button",
                 "down_button", "home_button")
        stand_in = types.SimpleNamespace(
            controller=types.SimpleNamespace(connected=True),
            connection_indicator_canvas=mock.Mock(), connection_indicator_item=1,
            command_busy=False, init_off_busy=False, initialized=True, homed=homed,
            **{name: mock.Mock() for name in names + (
                "connect_button", "disconnect_button", "port_combo", "read_button",
                "init_on_button", "init_off_button")},
        )
        stand_in.action_widgets = [getattr(stand_in, name) for name in names]
        MotorApp._update_controls(stand_in)
        return {name: getattr(stand_in, name).configure.call_args.kwargs["state"] for name in names}

    def test_presets_wait_for_home(self):
        before = self.controls(homed=False)
        self.assertEqual((before["preset_6400_button"], before["preset_7250_button"]), ("disabled", "disabled"))
        self.assertEqual(before["home_button"], "normal")
        after = self.controls(homed=True)
        self.assertEqual((after["preset_6400_button"], after["preset_7250_button"]), ("normal", "normal"))


if __name__ == "__main__":
    unittest.main()
