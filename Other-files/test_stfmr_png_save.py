"""STFMR Save CSV also writes the PNG figures (user, 2026-10-13).

The STFMR page's "Save CSV" wrote the CSV only, and the 900 DPI data
exporter (_export_stfmr_lockin_data_pngs) was never called.  Now Save CSV
writes the CSV, then the four figures next to it with that exporter; a PNG
problem keeps the CSV and is reported on its own.  A sweep's end still writes
the CSV only (test/test_stfmr_realtime_recording.py checks that).

No Tk and no instrument: the real save_stfmr_lockin_results (and, in the
first test, the real exporter) runs on a bare app with a stand-in root and
status line.  The folder dialog and every message box are mocked, and a real
Tk root fails the test instead of opening a window."""
import csv
import tempfile
import tkinter.filedialog as tk_filedialog
import tkinter.messagebox as tk_messagebox
import types
import unittest
from pathlib import Path
from unittest import mock

from test_nonlocal_keithley import Var, bare_app
from test_ppms_motor_debug import app

SUFFIXES = ("X", "Y", "theta", "field_vs_current")

# The status label is a ttk.Label of width=52 (in widths of "0"), and text
# past that is cut off.  "Saved: STFMR_field_scan_<stamp>.csv" alone takes
# about 47 of them, so what the save did must come within the first 40
# characters or the operator never sees it (reviewer, 2026-10-13).
VISIBLE = 40

_no_tk = mock.patch.object(
    app.tk, "Tk", side_effect=AssertionError("a test reached a real Tk window"))


def setUpModule():
    # A dialog that is not mocked would create a Tk root: fail instead.
    _no_tk.start()


def tearDownModule():
    _no_tk.stop()


def dual_rows():
    """Saved rows of a short MFLI + SR830 run: two frequencies, one loop."""
    rows = []
    for index, (frequency, current) in enumerate(
            (f, i) for f in (5.0, 6.0) for i in (-1.0, 0.0, 1.0)):
        for meter, sign in (("MFLI", 1.0), ("SR830", -1.0)):
            rows.append({
                "index": index, "meter": meter,
                "microwave_frequency_ghz": frequency, "loop": 1,
                "current_a": current, "field_g": 250.0 + 300.0 * current,
                "x_v": sign * (current + 2.0) * 1e-6,
                "y_v": (current - 1.0) * 1e-6, "phase_deg": 40.0 + current,
            })
    return rows


class SaveCsvWritesPngTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.folder = Path(self.tmp.name)
        # Every dialog the save can reach, on the tkinter modules themselves
        # (the program's messagebox and filedialog are these modules).
        self.ask_folder = mock.patch.object(
            tk_filedialog, "askdirectory", return_value=str(self.folder)).start()
        self.error = mock.patch.object(tk_messagebox, "showerror").start()
        self.warning = mock.patch.object(tk_messagebox, "showwarning").start()
        self.info = mock.patch.object(tk_messagebox, "showinfo").start()
        self.addCleanup(mock.patch.stopall)
        h = bare_app()
        h.root = types.SimpleNamespace(update_idletasks=mock.Mock())
        h.stfmr_lockin_rows = dual_rows()
        h.stfmr_lockin_selected_meter = "MFLI + SR830"
        h.stfmr_lockin_save_directory = self.folder
        h.stfmr_lockin_status_var = Var("Sweep complete; CSV saved")
        self.h = h

    def csv_files(self):
        return list(self.folder.glob("*.csv"))

    def test_save_writes_the_csv_then_the_four_pngs(self):
        from PIL import Image
        h = self.h
        real_export = h._export_stfmr_lockin_data_pngs
        order = []

        def export(folder, stem):
            # The CSV is complete before any figure is drawn, and the status
            # line says so (redrawn before the Tk thread is held).
            [csv_path] = self.csv_files()
            self.assertEqual(csv_path.stem, stem)
            with csv_path.open(encoding="utf-8-sig", newline="") as handle:
                self.assertEqual(len(list(csv.DictReader(handle))), 12)
            status = h.stfmr_lockin_status_var.get()
            self.assertEqual(status, f"Saving PNG… (CSV saved: {csv_path.name})")
            self.assertIn("Saving PNG…", status[:VISIBLE])
            h.root.update_idletasks.assert_called_once_with()
            order.append("png")
            return real_export(folder, stem)

        h._export_stfmr_lockin_data_pngs = export
        h.save_stfmr_lockin_results()
        self.assertEqual(order, ["png"])
        self.assertIn("PNG", self.ask_folder.call_args.kwargs["title"])
        [csv_path] = self.csv_files()
        pngs = sorted(p.name for p in self.folder.glob("*.png"))
        self.assertEqual(pngs, sorted(f"{csv_path.stem}_{s}.png" for s in SUFFIXES))
        for name in pngs:
            with Image.open(self.folder / name) as image:
                self.assertAlmostEqual(image.info["dpi"][0], 900, places=1)
                # Something was drawn: not a blank white page.
                self.assertLess(image.convert("L").getextrema()[0], 255)
        status = h.stfmr_lockin_status_var.get()
        self.assertEqual(status, f"CSV + 4 PNG saved: {csv_path.name}")
        self.assertIn("CSV + 4 PNG saved", status[:VISIBLE])
        self.error.assert_not_called()
        self.warning.assert_not_called()

    def test_png_failure_keeps_the_csv_and_is_reported_on_its_own(self):
        h = self.h
        # A two-line error: the one-line status bar gets it on one line.
        h._export_stfmr_lockin_data_pngs = mock.Mock(
            side_effect=OSError("PNG disk full\nwhile drawing the X figure"))
        h.save_stfmr_lockin_results()
        [csv_path] = self.csv_files()
        with csv_path.open(encoding="utf-8-sig", newline="") as handle:
            saved = list(csv.DictReader(handle))
        self.assertEqual(len(saved), 12)
        self.assertEqual([row["meter"] for row in saved[:2]], ["MFLI", "SR830"])
        h._export_stfmr_lockin_data_pngs.assert_called_once_with(
            self.folder, csv_path.stem)
        # The CSV is not reported as failed; the PNG problem is named.
        self.error.assert_not_called()
        self.warning.assert_called_once()
        title, text = self.warning.call_args.args
        self.assertIn("PNG", title)
        self.assertIn(str(csv_path), text)
        self.assertIn("PNG disk full\nwhile drawing the X figure", text)
        status = h.stfmr_lockin_status_var.get()
        self.assertEqual(
            status, "CSV saved; PNG not saved: PNG disk full while drawing the X figure")
        self.assertIn("PNG not saved: PNG disk full", status[:VISIBLE])

    def test_csv_failure_draws_no_png(self):
        # Guard of the CSV-first rule (passes before the change too): a CSV
        # that cannot be written is an error and no figure is attempted.
        h = self.h
        missing = self.folder / "missing"
        self.ask_folder.return_value = str(missing)
        h._export_stfmr_lockin_data_pngs = mock.Mock()
        h.save_stfmr_lockin_results()
        h._export_stfmr_lockin_data_pngs.assert_not_called()
        self.error.assert_called_once()
        self.warning.assert_not_called()
        self.assertFalse(missing.exists())
        self.assertEqual(h.stfmr_lockin_status_var.get(), "Sweep complete; CSV saved")


if __name__ == "__main__":
    unittest.main()
