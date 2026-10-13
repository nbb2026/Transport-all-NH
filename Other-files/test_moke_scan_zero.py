"""Isolated worker regression: no GUI, drivers, or physical instruments."""
import ast
import __future__
import math
from pathlib import Path
import threading
import time
import types
import unittest

SOURCE = Path(__file__).resolve().parents[1] / 'Transport_all_NH_dev.py'
tree = ast.parse(SOURCE.read_text(encoding="utf-8"))
names = {'_magnet_scan_worker', '_magnet_scan_zero_readback'}
methods = [n for c in tree.body if isinstance(c, ast.ClassDef)
           for n in c.body if isinstance(n, ast.FunctionDef) and n.name in names]
assert len(methods) == len(names)
# The worker now waits for an ROI sample newer than the point's current
# (review, 2026-10-13); with no camera window it does not wait.  Its wait
# uses the module helper camera_average_block_s (audit, 2026-10-13).
methods += [n for n in tree.body
            if isinstance(n, ast.FunctionDef) and n.name == 'camera_average_block_s']
assert len(methods) == len(names) + 1
namespace = {'Path': Path, 'time': time, 'math': math,
             'MAGNET_SCAN_FRAME_TIMEOUT_S': 10.0}
exec(compile(ast.Module(body=methods, type_ignores=[]), str(SOURCE), 'exec',
             flags=__future__.annotations.compiler_flag), namespace)


class Var:
    def set(self, value): self.value = value


class ZeroTests(unittest.TestCase):
    def make_app(self, fail_zero=False, stopped=False, fail_field=False):
        app = types.SimpleNamespace()
        app.magnet_scan_stop_event = threading.Event()
        if stopped: app.magnet_scan_stop_event.set()
        app.magnet_scan_data_lock = threading.Lock()
        app.magnet_scan_current_samples = []
        app.magnet_scan_rows = []
        app.magnet_scan_start_time = time.time()
        app.magnet_scan_collecting = False
        app.magnet_status_var = Var()
        app.magnet_target_vars = {'ip': Var()}
        app.magnet_current_vars = {'ip': Var()}
        app.magnet_last_currents = {}
        app.calls = []
        app.ui = []
        app.post_ui = app.ui.append
        def field():
            if fail_field: raise RuntimeError('field error')
            return 12.
        app._require_gauss_meter = lambda key: types.SimpleNamespace(query_field=field)
        # The scan's field read has the reconnect recovery (audit,
        # 2026-10-13); here a failure that recovery cannot fix.
        app._query_gauss_with_recovery = lambda key: field()
        app._require_magnet_bop = lambda key: types.SimpleNamespace(
            set_current=lambda value: app.calls.append(('fallback', key, value)))
        def write(key, value):
            app.calls.append(('verified', key, value))
            if fail_zero and value == 0: raise RuntimeError('zero readback failed')
            return value
        app._write_and_verify_magnet_current = write
        app._append_magnet_scan_point = lambda key, row: None
        app._magnet_scan_zero_readback = types.MethodType(namespace['_magnet_scan_zero_readback'], app)
        return app

    def run_worker(self, app):
        namespace['_magnet_scan_worker'](app, [(1,1,1,-1.)], 0., 0., 'ip', None)

    def test_complete_zero_after_last_point_without_extra_data(self):
        app = self.make_app()
        self.run_worker(app)
        self.assertEqual(app.calls, [('verified','ip',-1.),('verified','ip',0.)])
        self.assertEqual(len(app.magnet_scan_rows), 1)
        self.assertEqual(app.magnet_scan_rows[0][4], -1.)
        for callback in app.ui: callback()
        self.assertEqual(app.magnet_target_vars['ip'].value, '0.000')
        self.assertEqual(app.magnet_last_currents['ip'], 0.)
        self.assertFalse(app.magnet_scan_collecting)

    def test_zero_failure_propagates_and_retries_safe_write(self):
        app = self.make_app(fail_zero=True)
        with self.assertRaisesRegex(RuntimeError, 'zero readback failed'): self.run_worker(app)
        self.assertEqual(app.calls[-1], ('fallback','ip',0.))
        self.assertEqual(len(app.magnet_scan_rows), 1)
        self.assertFalse(app.magnet_scan_collecting)

    def test_stop_preserves_existing_zero(self):
        app = self.make_app(stopped=True)
        self.run_worker(app)
        self.assertEqual(app.calls, [('fallback','ip',0.)])
        self.assertEqual(app.magnet_scan_rows, [])

    def test_measurement_error_preserves_existing_zero(self):
        app = self.make_app(fail_field=True)
        with self.assertRaisesRegex(RuntimeError, 'field error'): self.run_worker(app)
        self.assertEqual(app.calls[-1], ('fallback','ip',0.))
        self.assertEqual(app.magnet_scan_rows, [])


if __name__ == '__main__': unittest.main()
