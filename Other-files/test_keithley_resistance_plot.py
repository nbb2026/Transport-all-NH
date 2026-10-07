"""MOKE-24 |R| vs time plot: drawn without numpy and with a steady value.

On a PC where the camera libraries (pco, Pillow) do not import, the program
sets np to None.  The plot then stopped after the time axis: no |R| ticks, no
trace, no "Current |R|" label.  No Tk: a stand-in canvas records the drawing.
"""
import unittest
from unittest import mock

from test_ppms_motor_debug import app


class Canvas:
    def __init__(self):
        self.items = []

    def delete(self, *args):
        self.items.clear()

    def winfo_width(self):
        return 1900

    def winfo_height(self):
        return 260

    def create_line(self, *args, **kwargs):
        self.items.append(("line", len(args), kwargs.get("fill")))

    def create_text(self, *args, **kwargs):
        self.items.append(("text", 0, kwargs.get("text")))

    def create_rectangle(self, *args, **kwargs):
        self.items.append(("rectangle", 0, None))

    def create_oval(self, *args, **kwargs):
        self.items.append(("oval", 0, None))


class ResistancePlotTests(unittest.TestCase):
    def draw(self, rows):
        app_ = app.CombinedControlApp.__new__(app.CombinedControlApp)
        app_.keithley_resistance_canvas = Canvas()
        app_._canvas_is_visible = lambda canvas: True
        app_._draw_plot_grid = lambda *args: None
        app_.ui_font_family, app_.ui_small_font_size = "Segoe UI", 9
        app_.keithley_resistance_rows = rows
        with mock.patch.object(app, "np", None):
            app_._redraw_keithley_resistance_plot()
        return app_.keithley_resistance_canvas.items

    def test_a_steady_value_is_drawn_without_numpy(self):
        # As on the lab PC: 369.711 MΩ measured continuously at 1 nA.
        items = self.draw([(4.0 + 0.5 * i, 369.711e6) for i in range(32)])
        texts = [text for kind, _, text in items if kind == "text"]
        self.assertTrue(any(kind == "line" and count > 4 and fill == "#7a3db8"
                            for kind, count, fill in items), "the |R| trace is drawn")
        self.assertIn("Current |R| = 369.711 MΩ", texts)
        self.assertIn("|R| (MΩ)", texts)
        axis = [float(text) for text in texts if text and text.replace(".", "", 1).isdigit()]
        self.assertGreaterEqual(len([value for value in axis if 340 <= value <= 400]), 2,
                                f"|R| axis numbers around 369.7: {texts}")

    def test_a_changing_value_and_a_single_point(self):
        items = self.draw([(0.5 * i, 1e3 * (1 + 0.01 * i)) for i in range(10)])
        self.assertTrue(any(kind == "line" and count > 4 for kind, count, _ in items))
        items = self.draw([(1.0, 52.0)])
        self.assertTrue(any(kind == "oval" for kind, _, _ in items), "one point is drawn as a dot")



class Var:
    def __init__(self, value=""):
        self.value = value

    def get(self):
        return self.value

    def set(self, value):
        self.value = value


class StopTests(unittest.TestCase):
    def test_start_is_offered_again_after_stop_even_if_the_redraw_fails(self):
        import threading
        from concurrent.futures import Future
        app_ = app.CombinedControlApp.__new__(app.CombinedControlApp)
        app_.closing = False
        app_.keithley_resistance_busy = True
        app_.keithley_voltage_var, app_.keithley_status_var, app_.keithley_manual_voltage_var = Var(), Var(), Var()
        app_.keithley_data_lock = threading.Lock()
        app_._redraw_keithley_resistance_plot = mock.Mock(side_effect=AttributeError("'NoneType' object has no attribute 'asarray'"))
        app_._update_keithley_controls = mock.Mock()
        stopped = Future()
        stopped.set_exception(InterruptedError("Resistance measurement stopped"))
        with self.assertRaises(AttributeError):
            app_._finish_keithley_resistance(stopped)
        self.assertFalse(app_.keithley_resistance_busy)
        app_._update_keithley_controls.assert_called_once()
        self.assertIn("finished", app_.keithley_status_var.get())


if __name__ == "__main__":
    unittest.main()
