"""Low helium warnings are panel-local and do not control instruments."""
import unittest
from unittest.mock import Mock, patch
from test_ppms_motor_debug import app


class HeliumWarningTests(unittest.TestCase):
    def panel(self, cls):
        panel = cls.__new__(cls)
        panel.root = Mock()
        panel.closing = False
        panel.ppms_helium_live_var = Mock()
        panel.ppms_helium_warning_active = False
        panel._append_ppms_helium_csv = Mock()
        return panel

    def test_history_csv_persists_both_panels(self):
        import tempfile
        import csv
        from pathlib import Path
        with tempfile.TemporaryDirectory() as folder:
            with patch.object(app, '__file__', str(Path(folder) / 'Transport.py')):
                # Two sessions (here: two panel instances) append to the same
                # continuous file; elapsed_s restarts per session.
                for cls in (app.CombinedControlApp, app.PPMS2Panel):
                    panel = self.panel(cls)
                    panel.thread_log = Mock()
                    cls._append_ppms_helium_csv(panel, 10., 75.)
                    cls._append_ppms_helium_csv(panel, 20., 74.9)
                    self.assertEqual(panel.ppms_helium_csv_path, Path(folder).resolve() / app.PPMS_HELIUM_HISTORY_FILENAME)
                with panel.ppms_helium_csv_path.open(encoding='utf-8-sig') as handle:
                    rows = list(csv.DictReader(handle))
                self.assertEqual(len(rows), 4)
                self.assertEqual([float(r['elapsed_s']) for r in rows], [0., 10., 0., 10.])
                self.assertEqual(float(rows[3]['helium_percent']), 74.9)
                self.assertEqual(len(list(Path(folder).glob('*.csv'))), 1)
                # The header is written once only.
                self.assertEqual(panel.ppms_helium_csv_path.read_text(encoding='utf-8-sig').count('timestamp_local'), 1)

    def test_hourly_change_and_missing_data(self):
        for cls in (app.CombinedControlApp, app.PPMS2Panel):
            panel = self.panel(cls)
            panel.ppms_helium_change_var = Mock()
            for seconds in range(0, 3901, 60):
                with patch.object(app.time, 'monotonic', return_value=seconds):
                    panel._update_ppms_helium_change(dict(helium_percent=80-seconds/3600*2, helium_fresh=True))
            self.assertEqual(panel.ppms_helium_change_var.set.call_args.args[0], '-2.17 points (65.0 min recorded)')
            count = len(panel.ppms_helium_history)
            panel._update_ppms_helium_change(dict(helium_percent=50, helium_fresh=False))
            self.assertEqual(len(panel.ppms_helium_history), count)
            with patch.object(app.time, 'monotonic', return_value=5000):
                panel._update_ppms_helium_change(dict(helium_percent=90, helium_fresh=True))
            self.assertEqual(len(panel.ppms_helium_history), count + 1)
            self.assertEqual(panel.ppms_helium_history[0], (0, 80.))
            self.assertIn('83.3 min', panel.ppms_helium_change_var.set.call_args.args[0])

    def test_threshold_repeat_and_rearm_both_panels(self):
        for cls in (app.CombinedControlApp, app.PPMS2Panel):
            panel = self.panel(cls)
            with patch.object(app.messagebox, 'showwarning') as warning:
                for value in (80, 70):
                    panel._check_ppms_helium_warning(dict(helium_percent=value, helium_fresh=True))
                warning.assert_not_called()
                for value in (69.99, 69, 0):
                    panel._check_ppms_helium_warning(dict(helium_percent=value, helium_fresh=True))
                self.assertEqual(warning.call_count, 1)
                panel._check_ppms_helium_warning(dict(helium_percent=70, helium_fresh=True))
                panel._check_ppms_helium_warning(dict(helium_percent=69, helium_fresh=True))
                self.assertEqual(warning.call_count, 2)

    def test_invalid_and_stale_values(self):
        panel = self.panel(app.CombinedControlApp)
        with patch.object(app.messagebox, 'showwarning') as warning:
            for value in (None, 'unknown', float('nan'), float('inf'), -1, 101):
                panel._check_ppms_helium_warning(dict(helium_percent=value))
            warning.assert_not_called()
            panel._check_ppms_helium_warning(dict(helium_percent=60, helium_fresh=False))
            self.assertIn('stale', warning.call_args.args[1])
            panel._check_ppms_helium_warning(dict(helium_percent=80, helium_fresh=False))
            panel._check_ppms_helium_warning(dict(helium_percent=60, helium_fresh=True))
            self.assertEqual(warning.call_count, 1)

    def test_panels_independent(self):
        with patch.object(app.messagebox, 'showwarning') as warning:
            for cls in (app.CombinedControlApp, app.PPMS2Panel):
                self.panel(cls)._check_ppms_helium_warning(dict(helium_percent=69, helium_fresh=True))
            self.assertEqual(warning.call_count, 2)
            self.assertIn('PPMS', warning.call_args_list[0].args[0])
            self.assertIn('PPMS', warning.call_args_list[1].args[0])


if __name__ == '__main__':
    unittest.main()
