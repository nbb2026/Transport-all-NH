"""STFMR firmware autorange and retry lifecycle tests; no physical devices."""
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import Transport_all_NH_dev as app
from test_stfmr_realtime_recording import harness, events, read_csv


class ControllerTests(unittest.TestCase):
    def test_native_and_legacy_firmware_paths(self):
        for legacy in (False, True):
            c = app.NonLocalACController(lambda *_: None)
            c.mfli_device = 'dev4435'
            if legacy:
                c.mfli_bridge = Mock()
                c.mfli_bridge.request.return_value = {'autorange': {
                    'old_input_range_v': .03, 'input_range_v': .1}}
            else:
                c.mfli_daq = Mock()
                c.mfli_daq.getDouble.side_effect = [.03, .1]
                c.mfli_daq.getInt.return_value = 0
            action = c._run_mfli_autorange('test')
            self.assertIn('0.03->0.1', action)
            self.assertEqual(c.mfli_config['input_range_v'], .1)
            self.assertEqual(c.mfli_range_cooldown, 3)
            if legacy:
                c.mfli_bridge.request.assert_called_once_with('autorange')
            else:
                c.mfli_daq.setInt.assert_called_once_with('/dev4435/sigins/0/autorange', 1)

    def test_firmware_timeout_is_not_accepted(self):
        c = app.NonLocalACController(lambda *_: None)
        c.mfli_device = 'dev4435'
        c.mfli_daq = Mock()
        c.mfli_daq.getDouble.return_value = .03
        c.mfli_daq.getInt.return_value = 1
        with patch.object(app.time, 'monotonic', side_effect=[0, 9]):
            with self.assertRaisesRegex(RuntimeError, 'timed out'):
                c._run_mfli_autorange('test')

    def make(self, **updates):
        c = app.NonLocalACController(lambda *_: None)
        sample = dict(input_overload=False, input_utilization=.5,
                      input_extrema_valid=True)
        sample.update(updates)
        c.read_mfli = lambda _: dict(sample)
        c.mfli_config['input_range_v'] = .1
        c._run_mfli_autorange = Mock(return_value='RANGE -> 0.1 V')
        return c

    def test_disabled_never_ranges_even_overloaded(self):
        c = self.make(input_overload=True)
        c.read_stfmr_mfli(317.3, auto_range=False, initial=True)
        c._run_mfli_autorange.assert_not_called()

    def test_initial_frequency_range(self):
        c = self.make()
        sample = c.read_stfmr_mfli(317.3, auto_range=True, initial=True)
        self.assertTrue(sample['auto_range'])
        self.assertEqual(sample['input_range_after_v'], .1)

    def test_hardware_overload_triggers_despite_tiny_r_utilization(self):
        c = self.make(input_overload=True, input_utilization=.001)
        c.read_stfmr_mfli(317.3, auto_range=True)
        c._run_mfli_autorange.assert_called_once_with('STFMR input overload')

    def test_unknown_extrema_does_not_downrange_from_r_fallback(self):
        c = self.make(input_extrema_valid=False, input_utilization=.00001)
        for _ in range(10):
            c.read_stfmr_mfli(317.3, auto_range=True)
        c._run_mfli_autorange.assert_not_called()

    def test_high_and_three_low_readings(self):
        c = self.make(input_utilization=.9)
        c.read_stfmr_mfli(317.3, auto_range=True)
        c._run_mfli_autorange.assert_called_once_with('overload/high')
        c = self.make(input_utilization=.1)
        for _ in range(2):
            c.read_stfmr_mfli(317.3, auto_range=True)
        c._run_mfli_autorange.assert_not_called()
        c.read_stfmr_mfli(317.3, auto_range=True)
        c._run_mfli_autorange.assert_called_once_with('low')

    def test_bounded_retries_and_success_on_final_read(self):
        c = self.make(input_overload=True)
        with self.assertRaisesRegex(RuntimeError, '3 retries'):
            c.read_stfmr_mfli(317.3, auto_range=True, attempts=3)
        c._run_mfli_autorange.assert_not_called()
        c = self.make()
        c.read_stfmr_mfli(317.3, auto_range=True, attempts=3)
        c._run_mfli_autorange.assert_not_called()


class ScanTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        patch.object(app.messagebox, 'askyesno', return_value=True).start()
        self.errors = patch.object(app.messagebox, 'showerror').start()
        # Start's file-name step (2026-10-18), left blank: the timestamped default.
        patch.object(app.simpledialog, 'askstring', return_value='').start()
        self.addCleanup(patch.stopall)

    def start(self, enabled=True):
        h = harness(self.tmp.name)
        h.stfmr_lockin_mfli_auto_range_var.set(enabled)
        h.start_stfmr_lockin_scan()
        return h, h.stfmr_lockin_recorder

    def wait_rows(self, h, count=1):
        for _ in range(100):
            if len(h.stfmr_lockin_rows) >= count:
                return
            h.root.step()
        self.fail('no retained rows')

    def overload(self, h, once=False):
        device = h.stfmr_lockin_mfli_controller
        original = device.read_mfli
        count = [0]
        def read(expected):
            sample = original(expected)
            if count[0] == 0 or not once:
                sample.update(input_overload=True, device_status_flags=1, x_v=999.0)
            count[0] += 1
            return sample
        device.read_mfli = read

    def test_initial_each_frequency_keeps_two_discard_rule(self):
        h, rec = self.start()
        h.root.drain()
        changes = [e for e in events(rec) if e['event'] == 'mfli_autorange_retry']
        self.assertEqual(len(changes), 2)
        self.assertTrue(all(e['point_index'] == 1 for e in changes))
        self.assertTrue(all(e['settle_s'] >= 1.0 for e in changes))
        rows = read_csv(rec.csv_path)
        self.assertEqual(len(rows), 6)
        self.assertEqual([r['index'] for r in rows], ['3','4','5','8','9','10'])
        self.assertTrue(all(r['mfli_input_range_v'] == '0.1' for r in rows))
        self.assertTrue(all(r['mfli_auto_range_enabled'] == 'True' for r in rows))

    def test_retry_same_current_never_saves_transition(self):
        h, rec = self.start()
        self.wait_rows(h)
        self.overload(h, once=True)
        current_calls = len(h.stfmr_lockin_controllers['kepco'].calls)
        # Read plus GUI completion: autorange must not advance the current/index.
        index = h.stfmr_lockin_index
        h.root.step()
        h.root.step()
        self.assertEqual(h.stfmr_lockin_index, index)
        self.assertEqual(len(h.stfmr_lockin_controllers['kepco'].calls), current_calls)
        self.assertEqual(len(read_csv(rec.csv_path)), 1)
        h.root.drain()
        rows = read_csv(rec.csv_path)
        self.assertFalse(any(float(r['x_v']) == 999 for r in rows))
        self.assertTrue(rows[1]['mfli_auto_range_action'])

    def test_persistent_overload_stops_with_outputs_off(self):
        h, rec = self.start()
        self.wait_rows(h)
        self.overload(h)
        h.root.drain()
        self.assertFalse(h.stfmr_lockin_running)
        self.assertEqual(len(read_csv(rec.csv_path)), 1)
        self.assertEqual(events(rec)[-1]['outcome'], 'error')
        self.assertIn(('rf_off',), h.stfmr_lockin_controllers['smb'].calls)
        self.assertIn(('zero',), h.stfmr_lockin_controllers['kepco'].calls)

    def test_stop_during_settling_preserves_written_rows(self):
        h, rec = self.start()
        self.wait_rows(h)
        self.overload(h, once=True)
        h.root.step()
        h.root.step()
        h.stop_stfmr_lockin_scan()
        h.root.drain()
        self.assertEqual(len(read_csv(rec.csv_path)), 1)
        self.assertEqual(events(rec)[-1]['outcome'], 'stopped')
        self.assertIn(('rf_off',), h.stfmr_lockin_controllers['smb'].calls)

    def test_disabled_and_frozen_checkbox(self):
        h, rec = self.start(enabled=False)
        h.stfmr_lockin_mfli_auto_range_var.set(True)
        h.root.drain()
        self.assertFalse(any(e['event']=='mfli_autorange_retry' for e in events(rec)))
        self.assertTrue(all(r['mfli_auto_range_enabled']=='False' for r in read_csv(rec.csv_path)))


if __name__ == '__main__':
    unittest.main()
