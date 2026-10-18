"""Isolated worker regression: no GUI, drivers, or physical instruments.

Since 2026-10-18 (user: every scan page zeroes its sources at every end, as
SHV) every ending of a magnet scan, completed, stopped or failed, writes the
verified 0 A to the scanned path and sends 0 A to the other path's KEPCO, on
the worker after the scan's own clean-up.  Before, a completed scan verified
its 0 A, and a Stop or a failure wrote a plain 0 A to the scanned path only;
a failed zero replaced the scan's own error.  These tests changed with it."""
import threading
import types
import unittest
from unittest import mock

import numpy as np

from test_magnet_zero_and_frames import scan_app
from test_ppms_motor_debug import app


class ZeroTests(unittest.TestCase):
    def setUp(self):
        patcher = mock.patch.object(app, "np", np)
        patcher.start()
        self.addCleanup(patcher.stop)

    def make_app(self, fail_zero=False, stopped=False, fail_field=False):
        a = scan_app(None)
        a.camera_window = None
        a.magnet_scan_stop_event = threading.Event()
        if stopped:
            a.magnet_scan_stop_event.set()
        a.calls = []

        def field():
            if fail_field:
                raise RuntimeError('field error')
            return 12.0

        a._query_gauss_with_recovery = lambda key: field()
        a._require_gauss_meter = lambda key: types.SimpleNamespace(query_field=field)

        def write(key, value):
            a.calls.append(('verified', key, value))
            if fail_zero and value == 0:
                raise RuntimeError('zero readback failed')
            return value

        a._write_and_verify_magnet_current = write
        a._append_magnet_scan_point = lambda key, row: None
        return a

    def run_worker(self, a):
        a._magnet_scan_worker([(1, 1, 1, -1.0)], 0.0, 0.0, 'OUT', None)

    def run_ui(self, a):
        while a.ui:
            a.ui.pop(0)()

    def test_complete_zero_after_last_point_without_extra_data(self):
        a = self.make_app()
        self.run_worker(a)
        self.assertEqual(a.calls, [('verified', 'OUT', -1.0), ('verified', 'OUT', 0.0)])
        self.assertEqual(a.zero_writes, [('IN', 0.0)], "the other path's KEPCO sent 0 A")
        self.assertEqual(len(a.magnet_scan_rows), 1)
        self.assertEqual(a.magnet_scan_rows[0][4], -1.0)
        self.run_ui(a)
        self.assertEqual(a.magnet_target_vars['OUT'].get(), '0.000')
        self.assertEqual(a.magnet_last_currents['OUT'], 0.0)
        self.assertFalse(a.magnet_scan_collecting)
        # Sent, not read back (review, 2026-10-18; it was "zeroed").
        self.assertEqual(a.magnet_scan_outputs, {'Out-of-plane KEPCO': 'zeroed',
                                                 'In-plane KEPCO': '0 A sent, not read back'})

    def test_a_failed_zero_is_reported_not_raised_and_retries_a_plain_write(self):
        a = self.make_app(fail_zero=True)
        self.run_worker(a)          # the scan completed; its end says how the KEPCOs ended
        self.assertEqual(a.zero_writes, [('OUT', 0.0), ('IN', 0.0)], "one more plain 0 A, then the other")
        self.assertEqual(a.magnet_scan_outputs['Out-of-plane KEPCO'], 'zero FAILED: zero readback failed')
        self.assertEqual(a.magnet_scan_outputs['In-plane KEPCO'], '0 A sent, not read back')
        self.run_ui(a)
        self.assertEqual(a.magnet_current_vars['OUT'].get(), '0 A not confirmed')
        self.assertIsNone(a.magnet_last_currents['OUT'])
        self.assertEqual(len(a.magnet_scan_rows), 1)

    def test_stop_zeroes_with_a_readback(self):
        a = self.make_app(stopped=True)
        self.run_worker(a)
        self.assertEqual(a.calls, [('verified', 'OUT', 0.0)])
        self.assertEqual(a.zero_writes, [('IN', 0.0)])
        self.assertEqual(a.magnet_scan_rows, [])

    def test_a_measurement_error_is_kept_and_both_are_zeroed(self):
        a = self.make_app(fail_field=True)
        with self.assertRaisesRegex(RuntimeError, 'field error'):
            self.run_worker(a)
        self.assertEqual(a.calls[-1], ('verified', 'OUT', 0.0))
        self.assertEqual(a.zero_writes, [('IN', 0.0)])
        self.assertEqual(a.magnet_scan_rows, [])

    def test_a_closed_scanned_port_is_a_failure_and_the_other_is_still_zeroed(self):
        a = self.make_app()
        a.magnet_channels['OUT'].bop.connected = False
        self.run_worker(a)
        self.assertIn('its current is unknown', a.magnet_scan_outputs['Out-of-plane KEPCO'])
        self.assertEqual(a.zero_writes, [('IN', 0.0)])
        a.magnet_channels['IN'].bop = None
        self.run_worker(a)
        self.assertEqual(a.magnet_scan_outputs['In-plane KEPCO'], 'not connected')


if __name__ == '__main__':
    unittest.main()
