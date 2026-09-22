"""Offline Macro parsing/execution and full-session He regressions."""
import threading
import unittest
from unittest.mock import Mock, patch
from test_ppms_motor_debug import app

class MacroTests(unittest.TestCase):
    def test_sequence_and_line_numbers(self):
        commands = app.parse_ppms_macro('REM example\nTMP TEMP 10 10 0\nFLD FIELD 0 100 0 0\nWAI WAITFOR 1 1 1 0 0 0\nSCAN')
        self.assertEqual(commands[0], (2, 'TEMP', (10, 10, 0)))
        self.assertEqual(commands[2], (4, 'WAITFOR', (1, 10, 0, 0)))
        self.assertEqual(commands[3], (5, 'SCAN', ()))
    def test_reject_entire_script_before_execution(self):
        for line in ('VSMMH 1 2', 'FIELD nan 100', 'TEMP 5 10', 'WAIT -1', 'WAI WAITFOR 1 0 0 1 0 0', 'SCAN 1', '__import__("os")'):
            with self.subTest(line=line), self.assertRaisesRegex(ValueError, 'Line 2'):
                app.parse_ppms_macro('WAIT 0\n'+line)
    def test_missing_wait_target(self):
        with self.assertRaisesRegex(ValueError, 'target'):
            app.parse_ppms_macro('WAI WAITFOR 1 1 0 0 0 0')
    def test_stop_prevents_device_command(self):
        panel = app.CombinedControlApp.__new__(app.CombinedControlApp)
        panel.ppms_macro_stop = threading.Event()
        panel.ppms_macro_stop.set()
        panel.closing = False
        panel.ppms_environment_controller = Mock()
        with self.assertRaises(app.PPMSScanCancelled):
            panel._ppms_macro_operation('FIELD', (0, 100, 0, 0))
        panel.ppms_environment_controller.set_field.assert_not_called()
    def test_persistent_wait_checks_mode(self):
        panel = app.CombinedControlApp.__new__(app.CombinedControlApp)
        panel.ppms_macro_stop = Mock(is_set=lambda: False)
        panel.closing = False
        panel.post_ui = Mock()
        panel.ppms_environment_controller = Mock()
        panel.ppms_environment_controller.read_environment.side_effect = [
            dict(field_oe=0, field_status='Holding'),
            dict(field_oe=0, field_status='StablePersistent')]
        panel._ppms_macro_operation('WAITFOR', (0, None, 0, 0))
        self.assertEqual(panel.ppms_environment_controller.read_environment.call_count, 2)
    def test_he_ten_second_gate(self):
        panel = app.CombinedControlApp.__new__(app.CombinedControlApp)
        panel._append_ppms_helium_csv = Mock()
        panel.ppms_helium_change_var = Mock()
        for second in (0, 1, 9, 10, 11, 20, 4000):
            with patch.object(app.time, 'monotonic', return_value=second):
                panel._update_ppms_helium_change(dict(helium_percent=80, helium_fresh=True))
        self.assertEqual([t for t,v in panel.ppms_helium_history], [0, 10, 20, 4000])
