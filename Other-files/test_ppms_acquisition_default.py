"""PPMS page, "Acquire s" (user, 2026-10-16): 5 s for a Temperature scan,
1 s for the other modes.  A scan-mode change puts the new mode's default in
the box unless the operator typed another value there.  No Tk window."""
import unittest

from test_nonlocal_keithley import Var
from test_ppms_motor_debug import app


def page(value="1"):
    panel = app.CombinedControlApp.__new__(app.CombinedControlApp)
    panel.ppms_acquisition_time_var = Var(value)
    return panel


class AcquisitionDefaultTests(unittest.TestCase):
    def test_the_defaults(self):
        self.assertEqual(app.PPMS_TEMPERATURE_ACQUISITION_S, 5.0)
        self.assertEqual(app.PPMS_ACQUISITION_S, 1.0)
        default = app.CombinedControlApp._ppms_acquisition_default
        self.assertEqual(default("Temperature"), "5")
        for mode in ("Field", "Angle", "Current", "Field+Temperature", "Angle+Temperature", "Current+Temperature"):
            with self.subTest(mode=mode):
                self.assertEqual(default(mode), "1")

    def test_switching_modes_follows_the_defaults(self):
        panel = page()
        panel._apply_ppms_mode_acquisition_default("Temperature")
        self.assertEqual(panel.ppms_acquisition_time_var.get(), "5")
        panel._apply_ppms_mode_acquisition_default("Field")
        self.assertEqual(panel.ppms_acquisition_time_var.get(), "1")
        panel._apply_ppms_mode_acquisition_default("Temperature")
        self.assertEqual(panel.ppms_acquisition_time_var.get(), "5")

    def test_a_typed_value_is_kept(self):
        panel = page()
        panel._apply_ppms_mode_acquisition_default("Temperature")
        panel.ppms_acquisition_time_var.set("3")          # typed in Temperature
        panel._apply_ppms_mode_acquisition_default("Field")
        self.assertEqual(panel.ppms_acquisition_time_var.get(), "3")
        panel._apply_ppms_mode_acquisition_default("Temperature")
        self.assertEqual(panel.ppms_acquisition_time_var.get(), "3")
        typed = page("2")                                  # typed in Field first
        typed.ppms_acquisition_default_shown = "1"
        typed._apply_ppms_mode_acquisition_default("Temperature")
        self.assertEqual(typed.ppms_acquisition_time_var.get(), "2")

    def test_the_mode_change_calls_it(self):
        with open(app.__file__, encoding="utf-8") as source:
            text = source.read()
        self.assertIn("self._apply_ppms_mode_acquisition_default(self.ppms_simple_mode.get())", text)
        self.assertIn('self.ppms_acquisition_time_var = tk.StringVar(value=f"{PPMS_ACQUISITION_S:g}")', text)


if __name__ == "__main__":
    unittest.main()
