"""STFMR save-before-output and streaming regressions; no Tk/VISA hardware."""
import csv
import json
import sys
import tempfile
import threading
import unittest
from concurrent.futures import Future
from pathlib import Path
from unittest.mock import patch, Mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import Transport_all_NH_dev as app


class Variable:
    def __init__(self, value):
        self.value = value

    def get(self):
        return self.value

    def set(self, value):
        self.value = value


class Root:
    def __init__(self):
        self.pending = {}
        self.counter = 0

    def after(self, _ms, callback):
        self.counter += 1
        self.pending[self.counter] = callback
        return self.counter

    def after_cancel(self, token):
        self.pending.pop(token, None)

    def step(self):
        token = next(iter(self.pending))
        self.pending.pop(token)()

    def drain(self):
        for _ in range(500):
            if not self.pending:
                return
            self.step()
        raise AssertionError('scan did not finish')


class Executor:
    def __init__(self):
        self.fail = False

    def submit(self, operation):
        if self.fail:
            raise RuntimeError('executor unavailable')
        future = Future()
        try:
            future.set_result(operation())
        except BaseException as error:
            future.set_exception(error)
        return future

    def shutdown(self, **_kwargs):
        pass


class Device:
    connected = True
    mfli_connected = True

    def __init__(self, harness):
        self.harness = harness
        self.calls = []
        self.sample_count = 0
        self.zero_error = False
        self.retry_next = False
        self.mfli_config = {'input_range_v': .03}

    def configure(self, *args):
        # Header and complete settings must be readable BEFORE RF configuration.
        recorder = self.harness.stfmr_lockin_recorder
        assert recorder is not None
        assert recorder.csv_path.read_text(encoding='utf-8-sig').startswith('index,')
        assert 'run_prepared' in recorder.log_path.read_text()
        self.calls.append(('configure', args))

    def configure_mfli(self, *args, **kwargs):
        self.calls.append(('configure_mfli', args, kwargs))

    def wait_for_reference(self, *args, **kwargs):
        pass

    def set_frequency(self, value):
        self.calls.append(('frequency', value))

    def set_output(self, *args):
        self.calls.append(('output', args))

    def set_scan_current(self, value):
        self.calls.append(('current', value))

    def zero_output(self):
        self.calls.append(('zero',))
        if self.zero_error:
            raise RuntimeError('zero failed')

    def rf_off(self):
        self.calls.append(('rf_off',))

    def read_field(self):
        return {'field': 123.4, 'unit': 'G', 'unit_code': 1}

    def read_sample(self, _expected):
        self.sample_count += 1
        result = {
            'x_v': 3e-6, 'y_v': 4e-6, 'r_v': 5e-6, 'phase_deg': 53.13,
            'reference_frequency_hz': 317.3, 'reference_valid': True,
            'measurement_valid': True, 'sensitivity_index': 15,
            'sensitivity_full_scale_v': 50e-6,
        }
        if self.retry_next:
            self.retry_next = False
            result['auto_range'] = '15 -> 16'
        return result

    def read_mfli(self, expected):
        sample = self.read_sample(expected)
        sample.update(input_range_v=self.mfli_config['input_range_v'],
                      input_overload=False, input_utilization=.5,
                      input_extrema_valid=False)
        return sample

    def _run_mfli_autorange(self, reason):
        self.calls.append(('autorange', reason))
        self.mfli_config['input_range_v'] = .1
        return 'RANGE 0.03->0.1 V (' + reason + ')'

    def read_stfmr_mfli(self, expected, **kwargs):
        return app.NonLocalACController.read_stfmr_mfli(self, expected, **kwargs)

    def disconnect(self):
        self.calls.append(('disconnect',))

    def disconnect_mfli(self):
        self.disconnect()


def harness(folder, meter='MFLI', stop='6', end_current='1'):
    h = object.__new__(app.CombinedControlApp)
    h.root = Root()
    h.closing = h.stfmr_lockin_closed = h.stfmr_lockin_running = False
    h.stfmr_lockin_starting = False
    h.stfmr_lockin_recorder = None
    h.stfmr_lockin_save_directory = Path(folder)
    values = {
        # The sweep's safety confirmation is the tick in the 4886 output panel.
        'manual_safety': True, 'meter': meter, 'frequency_start': '5',
        'frequency_stop': stop, 'frequency_step': '1', 'power': '10',
        'lfo': '317.3', 'delay': '0', 'limit': '10', 'loop': 1,
        'start': '-1', 'stop': end_current, 'step': '0.5',
        'sr830_reference': 'Sine', 'sr830_auto_range': True,
        'mfli_timeconstant': '0.1', 'mfli_auto_range': True, 'reference': 'Aux Input 1',
        'status': '', 'field': '', 'voltage': '', 'reference_status': '',
        'save_path': '',
    }
    for name, value in values.items():
        setattr(h, f'stfmr_lockin_{name}_var', Variable(value))
    h.stfmr_lockin_resource_vars = {
        key: Variable(value) for key, value in app.STFMR_LOCKIN_DEFAULT_RESOURCES.items()
    }
    h.stfmr_lockin_controllers = {key: Device(h) for key in ('kepco', 'gauss', 'smb', '830')}
    h.stfmr_lockin_mfli_controller = Device(h)
    h.stfmr_lockin_executor = Executor()
    h.stfmr_lockin_stop_event = threading.Event()
    h.stfmr_lockin_rows = []
    h.stfmr_lockin_live_rows = []
    h.stfmr_lockin_after_id = h.stfmr_lockin_live_after_id = None
    h.stfmr_lockin_progress = None
    h.select_csv_save_directory = lambda *_args: Path(folder)
    h._set_stfmr_progress = lambda *_args: None
    h._update_stfmr_lockin_ui = lambda: None
    h._redraw_stfmr_lockin_live_plots = lambda: None
    h._redraw_stfmr_lockin_plot = lambda: None
    h._set_stfmr_lockin_reference_state = lambda *_args: None
    h._append_stfmr_lockin_live_sample = lambda *_args, **_kwargs: None
    h.post_ui = lambda callback: h.root.after(0, callback)
    h.append_log = lambda _text: None
    h._export_stfmr_lockin_data_pngs = Mock(return_value=())
    return h


def read_csv(path):
    with path.open(encoding='utf-8-sig', newline='') as handle:
        return list(csv.DictReader(handle))


def events(recorder):
    return [json.loads(line) for line in recorder.log_path.read_text().splitlines()]


class StreamingTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.confirm = patch.object(app.messagebox, 'askyesno', return_value=True).start()
        self.error = patch.object(app.messagebox, 'showerror').start()
        self.addCleanup(patch.stopall)

    def start(self, **kwargs):
        h = harness(self.tmp.name, **kwargs)
        h.start_stfmr_lockin_scan()
        return h, h.stfmr_lockin_recorder

    def wait_rows(self, h, count=1):
        while len(h.stfmr_lockin_rows) < count:
            self.assertTrue(h.root.pending)
            h.root.step()

    def test_cancel_folder_preserves_old_data_and_never_enables_output(self):
        h = harness(self.tmp.name)
        h.stfmr_lockin_rows = [{'old': 1}]
        h.select_csv_save_directory = lambda *_args: None
        h.start_stfmr_lockin_scan()
        self.assertEqual(h.stfmr_lockin_rows, [{'old': 1}])
        self.assertFalse(h.stfmr_lockin_starting)
        self.confirm.assert_not_called()
        self.assertFalse(h.stfmr_lockin_controllers['smb'].calls)
        self.assertFalse(list(Path(self.tmp.name).iterdir()))

    def test_cancel_confirmation_creates_no_run(self):
        self.confirm.return_value = False
        h, recorder = self.start()
        self.assertIsNone(recorder)
        self.assertFalse(h.stfmr_lockin_controllers['kepco'].calls)
        self.assertFalse(list(Path(self.tmp.name).iterdir()))

    def test_invalid_directory_prevents_hardware_and_keeps_old_rows(self):
        h = harness(Path(self.tmp.name)/'missing')
        h.stfmr_lockin_rows = [{'old': 1}]
        h.start_stfmr_lockin_scan()
        self.error.assert_called_once()
        self.assertFalse(h.stfmr_lockin_controllers['smb'].calls)
        self.assertEqual(h.stfmr_lockin_rows, [{'old': 1}])

    def test_live_multifrequency_csv_matches_retained_gui_points(self):
        h, recorder = self.start()
        self.assertEqual(read_csv(recorder.csv_path), [])
        self.wait_rows(h)
        self.assertTrue(h.stfmr_lockin_running)
        self.assertEqual(len(read_csv(recorder.csv_path)), 1)
        h.root.drain()
        rows = read_csv(recorder.csv_path)
        self.assertEqual([int(r['index']) for r in rows], [3, 4, 5, 8, 9, 10])
        self.assertEqual([float(r['microwave_frequency_ghz']) for r in rows], [5]*3+[6]*3)
        self.assertEqual(len(rows), len(h.stfmr_lockin_rows))
        self.assertTrue(all(r['timestamp_iso'] for r in rows))
        self.assertEqual(set(rows[0]), set(app.STFMRScanRecorder.FIELDNAMES))
        history = events(recorder)
        self.assertEqual(history[0]['settings']['meter'], 'MFLI')
        self.assertEqual(history[0]['settings']['mfli_timeconstant_s'], 0.1)
        self.assertEqual(sum(r['event']=='startup_point_discarded' for r in history), 4)
        self.assertEqual(history[-1]['outcome'], 'completed')
        self.assertEqual(history[-1]['saved_points'], 6)
        self.assertTrue(history[-1]['outputs_off'])
        self.assertTrue(recorder.csv_handle.closed and recorder.log_handle.closed)
        self.error.assert_not_called()

    def test_sr830_single_frequency_and_autorange_retry(self):
        h, recorder = self.start(meter='SR830', stop='5')
        self.wait_rows(h)
        h.stfmr_lockin_controllers['830'].retry_next = True
        h.root.drain()
        self.assertEqual(len(read_csv(recorder.csv_path)), 3)
        self.assertTrue(any(e['event']=='sr830_autorange_retry' for e in events(recorder)))
        self.assertEqual(events(recorder)[0]['settings']['sr830_ref_in'], 'Sine')
        self.error.assert_not_called()

    def test_no_retained_points_still_leaves_header_and_log(self):
        h, recorder = self.start(stop='5', end_current='-0.5')
        h.root.drain()
        self.assertEqual(read_csv(recorder.csv_path), [])
        self.assertEqual(events(recorder)[-1]['saved_points'], 0)
        self.assertEqual(events(recorder)[-1]['outcome'], 'completed')

    def test_manual_stop_preserves_live_csv(self):
        h, recorder = self.start()
        self.wait_rows(h)
        h.stop_stfmr_lockin_scan()
        h.root.drain()
        self.assertEqual(len(read_csv(recorder.csv_path)), 1)
        self.assertEqual(events(recorder)[-1]['outcome'], 'stopped')
        self.assertIn(('rf_off',), h.stfmr_lockin_controllers['smb'].calls)
        h._export_stfmr_lockin_data_pngs.assert_not_called()

    def test_completion_saves_csv_without_png(self):
        h, recorder = self.start()
        def export(folder, stem):
            self.assertIn(('zero',), h.stfmr_lockin_controllers['kepco'].calls)
            self.assertIn(('rf_off',), h.stfmr_lockin_controllers['smb'].calls)
            self.assertEqual(folder, recorder.directory)
            self.assertEqual(stem, recorder.csv_path.stem)
            self.assertEqual(len(h.stfmr_lockin_rows), 6)
            self.assertFalse(recorder.closed)
            return tuple(folder / (stem + '_' + suffix + '.png')
                         for suffix in ('X', 'Y', 'theta', 'field_vs_current'))
        h._export_stfmr_lockin_data_pngs.side_effect = export
        h.root.drain()
        h._export_stfmr_lockin_data_pngs.assert_not_called()
        end = events(recorder)[-1]
        self.assertNotIn('auto_png_paths', end)
        self.assertTrue(end['outputs_off'])
        self.assertEqual(len(read_csv(recorder.csv_path)), 6)
        self.assertIn('CSV saved', h.stfmr_lockin_status_var.get())
        h._finish_stfmr_lockin_scan()
        h._export_stfmr_lockin_data_pngs.assert_not_called()

    def test_png_failure_does_not_lose_csv_or_claim_measurement_failure(self):
        h, recorder = self.start(stop='5')
        h._export_stfmr_lockin_data_pngs.side_effect = OSError('PNG disk full')
        with patch.object(app.messagebox, 'showwarning') as warning:
            h.root.drain()
        self.assertEqual(len(read_csv(recorder.csv_path)), 3)
        end = events(recorder)[-1]
        self.assertEqual(end['outcome'], 'completed')
        self.assertTrue(end['outputs_off'])
        self.assertNotIn('auto_png_error', end)
        h._export_stfmr_lockin_data_pngs.assert_not_called()
        self.assertTrue(recorder.closed)
        warning.assert_not_called()
        self.error.assert_not_called()

    def test_real_png_export_without_tk_canvas_or_camera(self):
        from PIL import Image
        h = object.__new__(app.CombinedControlApp)
        h.stfmr_lockin_selected_meter = 'MFLI'
        h.stfmr_lockin_rows = [dict(meter='MFLI', microwave_frequency_ghz=f,
            loop=loop, current_a=i, field_g=250+300*i, x_v=(i+2)*1e-6,
            y_v=(i-1)*1e-6, phase_deg=40+i)
            for f in (2, 3) for loop in (1, 2) for i in (-1, 0, 1)]
        paths = h._export_stfmr_lockin_data_pngs(Path(self.tmp.name), 'STFMR_test')
        self.assertEqual([p.name for p in paths], ['STFMR_test_' + s + '.png'
            for s in ('X', 'Y', 'theta', 'field_vs_current')])
        for path in paths:
            with Image.open(path) as image:
                self.assertEqual(image.size, (6300, 4500))
                self.assertAlmostEqual(image.info['dpi'][0], 900, places=1)
                self.assertNotEqual(image.convert('L').getextrema()[0], 255)
        with self.assertRaises(FileExistsError):
            h._export_stfmr_lockin_data_pngs(Path(self.tmp.name), 'STFMR_test')

    def test_manual_save_uses_data_export_without_imagegrab(self):
        h = harness(self.tmp.name)
        h.stfmr_lockin_rows = [{'index': 3, 'x_v': 1e-6}]
        h._export_stfmr_lockin_data_pngs.return_value = tuple(Path(self.tmp.name) / s
            for s in ('X.png', 'Y.png', 'theta.png', 'field_vs_current.png'))
        with patch.object(app, 'ImageGrab', None), patch.object(
                app.filedialog, 'askdirectory', return_value=self.tmp.name):
            h.save_stfmr_lockin_results()
        h._export_stfmr_lockin_data_pngs.assert_not_called()
        csv_path = next(Path(self.tmp.name).glob('*.csv'))
        self.assertEqual(list(Path(self.tmp.name).glob('*.png')), [])
        self.assertEqual(len(read_csv(csv_path)), 1)
        self.error.assert_not_called()

    def test_close_ignores_inflight_callback(self):
        h, recorder = self.start()
        self.wait_rows(h)
        h.root.step()  # start next read, leaving its GUI callback pending
        before = recorder.csv_path.read_bytes()
        h.closing = True
        h.safe_close_stfmr_lockin()
        calls = list(h.stfmr_lockin_controllers['kepco'].calls)
        h.root.drain()
        self.assertEqual(recorder.csv_path.read_bytes(), before)
        self.assertEqual(h.stfmr_lockin_controllers['kepco'].calls, calls)
        self.assertEqual(events(recorder)[-1]['outcome'], 'closed')
        self.assertTrue(recorder.csv_handle.closed)

    def test_csv_disk_failure_aborts_before_next_current(self):
        h, recorder = self.start()
        self.wait_rows(h)
        calls = len([x for x in h.stfmr_lockin_controllers['kepco'].calls if x[0]=='current'])
        with patch.object(recorder, 'append_point', side_effect=OSError('disk full')):
            h.root.drain()
        self.assertFalse(h.stfmr_lockin_running)
        self.assertEqual(len(read_csv(recorder.csv_path)), 1)
        self.assertEqual(len([x for x in h.stfmr_lockin_controllers['kepco'].calls if x[0]=='current']), calls)
        self.assertEqual(events(recorder)[-1]['outcome'], 'error')
        self.assertIn('disk full', events(recorder)[-1]['error'])
        self.assertIn(('zero',), h.stfmr_lockin_controllers['kepco'].calls)
        self.assertIn(('rf_off',), h.stfmr_lockin_controllers['smb'].calls)
        self.assertTrue(recorder.log_handle.closed)

    def test_log_failure_and_zero_failure_still_close_rf_and_files(self):
        h, recorder = self.start()
        h.stfmr_lockin_controllers['kepco'].zero_error = True
        with patch.object(recorder, 'event', side_effect=OSError('log unavailable')):
            h.root.drain()
        self.assertFalse(h.stfmr_lockin_running)
        self.assertIn(('rf_off',), h.stfmr_lockin_controllers['smb'].calls)
        self.assertTrue(recorder.log_handle.closed and recorder.csv_handle.closed)
        self.error.assert_called_once()

    def test_executor_submission_failure_closes_recording(self):
        h = harness(self.tmp.name)
        h.stfmr_lockin_executor.fail = True
        h.start_stfmr_lockin_scan()
        self.assertFalse(h.stfmr_lockin_running)
        self.assertIsNone(h.stfmr_lockin_recorder)
        path = next(Path(self.tmp.name).glob('*/*.log'))
        self.assertEqual(json.loads(path.read_text().splitlines()[-1])['outcome'], 'error')
        self.assertIn(('rf_off',), h.stfmr_lockin_controllers['smb'].calls)

    def test_recorder_fsync_on_each_row_and_unique_runs(self):
        first = app.STFMRScanRecorder(Path(self.tmp.name), {'meter': 'test'})
        second = app.STFMRScanRecorder(Path(self.tmp.name), {})
        self.assertNotEqual(first.directory, second.directory)
        with patch.object(app.os, 'fsync', wraps=app.os.fsync) as sync:
            first.append_point({'index': 3, 'field_g': 0.0})
            self.assertEqual(sync.call_count, 1)
        self.assertEqual(len(read_csv(first.csv_path)), 1)
        self.assertEqual(first.close('completed'), [])
        self.assertEqual(first.close('completed'), [])
        second.close('stopped')

    def test_reentrant_start_from_dialog_does_not_create_second_run(self):
        h = harness(self.tmp.name)
        def select(*_args):
            h.start_stfmr_lockin_scan()
            return Path(self.tmp.name)
        h.select_csv_save_directory = select
        h.start_stfmr_lockin_scan()
        h.root.drain()
        self.assertEqual(len(list(Path(self.tmp.name).iterdir())), 1)

    def test_multi_loop_and_frozen_timeconstant(self):
        h = harness(self.tmp.name, stop='5')
        h.stfmr_lockin_loop_var.set(2)
        h.start_stfmr_lockin_scan()
        recorder = h.stfmr_lockin_recorder
        h.stfmr_lockin_mfli_timeconstant_var.set('0.8')
        h.root.drain()
        rows = read_csv(recorder.csv_path)
        self.assertEqual([int(r['index']) for r in rows], list(range(3, 11)))
        self.assertEqual([int(r['loop']) for r in rows], [1]*3+[2]*5)
        self.assertTrue(all(float(r['mfli_timeconstant_s']) == 0.1 for r in rows))

    def test_preflight_flush_failure_never_enables_output(self):
        h = harness(self.tmp.name)
        with patch.object(app.STFMRScanRecorder, '_sync', side_effect=OSError('read only')):
            h.start_stfmr_lockin_scan()
        self.assertIsNone(h.stfmr_lockin_recorder)
        self.assertFalse(h.stfmr_lockin_controllers['smb'].calls)
        self.assertFalse(h.stfmr_lockin_running)
        self.error.assert_called_once()

    def test_shutdown_error_is_logged_without_claiming_outputs_off(self):
        h, recorder = self.start(stop='5')
        h.stfmr_lockin_controllers['kepco'].zero_error = True
        h.root.drain()
        end = events(recorder)[-1]
        self.assertEqual(end['outcome'], 'error')
        self.assertFalse(end['outputs_off'])
        self.assertTrue(end['shutdown_errors'])
        self.assertIn(('rf_off',), h.stfmr_lockin_controllers['smb'].calls)


if __name__ == '__main__':
    unittest.main()
