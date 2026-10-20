"""The difference CLim boxes follow the Auto limits (user, 2026-10-20: "the
input should also be changed simutaneously with the auto numbers")."""
import types
import unittest
from unittest import mock

from test_ppms_motor_debug import app


class Var:
    def __init__(self, value=""):
        self.value, self.traces = value, []

    def get(self):
        return self.value

    def set(self, value):
        self.value = value
        for callback in self.traces:
            callback()

    def __str__(self):
        return f"PY_VAR{id(self)}"


def page(focused=None):
    a = types.SimpleNamespace()
    a.difference_clim_low_var, a.difference_clim_high_var = Var("-2"), Var("2")
    a.root = types.SimpleNamespace(focus_get=lambda: focused)
    a.camera_window = types.SimpleNamespace(worker=types.SimpleNamespace(background_mode="tracking"))
    a.scheduled = []
    a._cancel_difference_clim_live_update = lambda: None
    a.difference_clim_after_id = None
    a.root.after = lambda ms, callback: a.scheduled.append(callback)
    for name in ("_show_auto_clim_in_boxes", "_schedule_difference_clim_live_update"):
        setattr(a, name, types.MethodType(getattr(app.CombinedControlApp, name), a))
    for variable in (a.difference_clim_low_var, a.difference_clim_high_var):
        variable.traces.append(a._schedule_difference_clim_live_update)
    return a


class ClimBoxesTests(unittest.TestCase):
    def test_the_boxes_show_the_auto_limits_without_going_manual(self):
        a = page()
        a._show_auto_clim_in_boxes((-219.7123, 217.4987))
        self.assertEqual((a.difference_clim_low_var.get(), a.difference_clim_high_var.get()), ("-219.7", "217.5"))
        self.assertEqual(a.scheduled, [], "no switch to Manual")

    def test_a_box_being_typed_in_is_left_alone(self):
        a = page()
        a.root.focus_get = lambda: types.SimpleNamespace(cget=lambda _name: str(a.difference_clim_low_var))
        a._show_auto_clim_in_boxes((-50.0, 60.0))
        self.assertEqual((a.difference_clim_low_var.get(), a.difference_clim_high_var.get()), ("-2", "60"))

    def test_typing_still_switches_to_manual(self):
        a = page()
        a.difference_clim_low_var.set("-5")
        self.assertEqual(len(a.scheduled), 1)


if __name__ == "__main__":
    unittest.main()
