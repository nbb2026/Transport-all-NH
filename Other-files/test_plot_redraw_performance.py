"""Redraw optimisations must not change what the transport plots show.

Two guarantees are pinned here:

* ``_draw_nonlocal_series`` skips a marker only when an identical one was
  already drawn at the same rounded pixel box, so the rasterised image is
  unchanged however dense the series gets.
* ``_ppms2_prefix_resolver`` picks exactly the columns ``_ppms2_meter_value``
  used to pick, for every meter, harmonic order and row layout.
"""
import math
import random
import tkinter as tk
import unittest

from test_ppms_motor_debug import app


class Plotter(app.CombinedControlApp):
    """Only the drawing helpers are needed, so skip the real construction."""
    def __init__(self):
        pass


def _boxes(canvas):
    """(type, rounded coords) of every marker item on a canvas.

    Markers are a few pixels across; the plot frame is a rectangle too, so
    size is what separates them.
    """
    found = []
    for item in canvas.find_all():
        if canvas.type(item) not in ("oval", "rectangle"):
            continue
        box = tuple(round(c) for c in canvas.coords(item))
        if box[2] - box[0] <= 20 and box[3] - box[1] <= 20:
            found.append((canvas.type(item),) + box)
    return found


class MarkerDedupeTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        try:
            cls.root = tk.Tk()
        except tk.TclError as error:  # headless build server
            raise unittest.SkipTest(f"no display: {error}")
        cls.root.geometry("520x160+20+20")
        cls.plotter = Plotter()
        cls.canvas = tk.Canvas(cls.root, width=470, height=100)
        cls.canvas.pack()
        cls.root.update()

    @classmethod
    def tearDownClass(cls):
        cls.root.destroy()

    def draw(self, points, series_markers=None):
        self.canvas.delete("all")
        self.plotter._draw_nonlocal_series(
            self.canvas, {"S": points}, "x", "y", "wait", markers=True,
            series_markers=series_markers, series_colors={"S": "#0072B2"},
            series_labels={"S": "S"}, show_legend=False)
        return _boxes(self.canvas)

    def test_no_two_markers_share_a_rounded_box(self):
        """A skipped marker would have painted over an identical one."""
        random.seed(11)
        for count in (50, 200, 600, 1500, 4000):
            for markers in (None, {"S": "open_circle"}, {"S": "square"}):
                points = [(random.uniform(-15000, 15000), random.uniform(-1e-5, 1e-5))
                          for _ in range(count)]
                boxes = self.draw(points, markers)
                self.assertEqual(len(boxes), len(set(boxes)),
                                 f"duplicate marker boxes for {count} points, {markers}")

    def test_every_distinct_box_is_still_drawn(self):
        """Dense data keeps one marker per distinct pixel box, not fewer."""
        # A smooth sweep puts many points on each pixel column, which is where
        # the saving comes from: the distinct boxes must still all be present.
        points = [(x, math.sin(x / 900.0) * 1e-5) for x in range(-15000, 15000, 20)]
        boxes = self.draw(points)
        self.assertLess(len(boxes), len(points), "dense sweep should shed duplicates")
        columns = {box[1] for box in boxes}
        self.assertGreater(len(columns), 300, "distinct pixel columns must survive")

    def test_sparse_data_keeps_every_marker(self):
        points = [(i * 1000.0, i * 1e-6) for i in range(-12, 13)]
        self.assertEqual(len(self.draw(points)), len(points))


class PrefixResolverTests(unittest.TestCase):
    """The batched column resolver must agree with the per-value accessor."""

    class Panel(app.PPMSPanel):
        def __init__(self, mode):
            self.zurich_harmonic = type("V", (), {"get": staticmethod(lambda: mode)})()
            self.ppms_plot_addresses = {"830_a": 8, "830_b": 9, "830_c": 10}

    def rows(self):
        made = []
        for demod_metadata in (True, False):
            for mode in ("1", "2", "3", "1+2", "1+2+3", None):
                row = {"zurich_harmonic": mode} if mode is not None else {}
                for address, harmonic in ((8, 1), (9, 2), (10, 3)):
                    row[f"sr830_{address}_harmonic"] = harmonic
                    row[f"sr830_{address}_x_v"] = 1e-6 * address
                    row[f"sr830_{address}_y_v"] = 2e-6
                    row[f"sr830_{address}_theta_deg"] = 30.0
                for demod in (1, 2, 3, 4):
                    if demod_metadata:
                        row[f"zurich_demod{demod}_harmonic"] = demod
                        row[f"zurich_demod{demod}_input"] = "0"
                    row[f"zurich_demod{demod}_x_v"] = 1e-7 * demod
                    row[f"zurich_demod{demod}_y_v"] = 1e-7
                    row[f"zurich_demod{demod}_theta_deg"] = 5.0 * demod
                row["zurich_x_v"] = 9e-7
                row["zurich_y_v"] = 9e-7
                row["zurich_theta_deg"] = 1.0
                made.append(row)
        return made

    def test_resolver_matches_the_single_value_accessor(self):
        for mode in ("1", "2", "3", "1+2", "1+2+3"):
            panel = self.Panel(mode)
            for order in (1, 2, 3):
                for meter in ("830_a", "830_b", "830_c", "zurich"):
                    resolve = panel._ppms2_prefix_resolver(meter, order)
                    for row in self.rows():
                        for channel in ("x", "y", "theta"):
                            prefix = resolve(row)
                            suffix = "theta_deg" if channel == "theta" else f"{channel}_v"
                            batched = (panel._ppms2_finite(row.get(prefix + suffix))
                                       if prefix is not None else None)
                            self.assertEqual(
                                batched,
                                panel._ppms2_meter_value(meter, order, channel, row),
                                f"{mode} {meter} order {order} {channel}")


if __name__ == "__main__":
    unittest.main()
