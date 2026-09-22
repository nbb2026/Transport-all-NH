"""Offline FMR save-dialog and signed-axis regression checks."""
import csv
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch
from test_ppms_motor_debug import app


class FMRLiveSaveTests(unittest.TestCase):
    def setUp(self):
        self.obj = app.CombinedControlApp.__new__(app.CombinedControlApp)
        self.obj.root = Mock()

    def test_live_csv_signed_values_and_collision(self):
        with tempfile.TemporaryDirectory() as folder:
            existing = Path(folder) / 'sample.csv'
            existing.write_text('original')
            with patch.object(app.filedialog, 'askdirectory', return_value=folder), \
                 patch.object(app.simpledialog, 'askstring', return_value='sample.csv'):
                self.assertTrue(self.obj._prepare_stfmr_fmr_live_csv())
            self.assertEqual(self.obj.stfmr_fmr_live_csv_path.name, 'sample_01.csv')
            for value in (-335.0, 335.0):
                self.obj._append_stfmr_fmr_live_csv({'measured_field_g': value})
            with self.obj.stfmr_fmr_live_csv_path.open(encoding='utf-8-sig') as stream:
                rows = list(csv.DictReader(stream))
            self.assertEqual([float(row['measured_field_g']) for row in rows], [-335, 335])
            self.assertEqual(existing.read_text(), 'original')

    def test_cancel_prevents_hardware_start(self):
        obj = self.obj
        obj.stfmr_fmr_running = obj.stfmr_frequency_running = False
        obj._stfmr_fmr_inputs = Mock(return_value=(('S21',), [3e9], 0, [-1, 1], 5, .2))
        obj._configure_stfmr_fmr_frequency = Mock()
        with patch.object(app.messagebox, 'askyesno', return_value=True), \
             patch.object(app.filedialog, 'askdirectory', return_value=''):
            obj.start_stfmr_fmr_scan()
        obj._configure_stfmr_fmr_frequency.assert_not_called()
        self.assertFalse(obj.stfmr_fmr_running)

    def test_default_and_cancel_name(self):
        with tempfile.TemporaryDirectory() as folder:
            with patch.object(app.filedialog, 'askdirectory', return_value=folder), \
                 patch.object(app.simpledialog, 'askstring', return_value=None):
                self.assertFalse(self.obj._prepare_stfmr_fmr_live_csv())
            self.assertEqual(list(Path(folder).iterdir()), [])
            with patch.object(app.filedialog, 'askdirectory', return_value=folder), \
                 patch.object(app.simpledialog, 'askstring', return_value=''):
                self.assertTrue(self.obj._prepare_stfmr_fmr_live_csv())
            self.assertTrue(self.obj.stfmr_fmr_live_csv_path.name.startswith('FMR_field_'))

    def test_signed_plot(self):
        obj = self.obj
        canvas = obj.stfmr_fmr_canvas = Mock()
        canvas.winfo_width.return_value = 900
        canvas.winfo_height.return_value = 400
        obj.stfmr_fmr_selected = ('S21',)
        obj.stfmr_fmr_rows = [dict(measured_field_g=h, pna_frequency_hz=3e9, S21=1)
                              for h in (-335, 335)]
        obj._redraw_stfmr_fmr_plot()
        markers = canvas.create_oval.call_args_list
        self.assertLess(markers[0].args[0], markers[1].args[0])
        labels = [str(call.kwargs.get('text', '')) for call in canvas.create_text.call_args_list]
        self.assertTrue(any(label.startswith('-') for label in labels))
        self.assertIn('Measured magnetic field (G)', labels)


if __name__ == '__main__':
    unittest.main()
