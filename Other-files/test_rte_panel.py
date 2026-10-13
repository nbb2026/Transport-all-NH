"""Offline RT-E regression tests. Never open a VISA or LabOne session."""
from contextlib import ExitStack
import csv
from pathlib import Path
import tempfile
import threading
import types
import time
import unittest
from unittest.mock import Mock, patch

from test_ppms_motor_debug import app
from test_ppms_two_panels import Var, ImmediateExecutor


class Widget:
    def __init__(self, *args, **kwargs):
        self.options = dict(kwargs)
        self.parent = args[0] if args else None
    def grid(self, **kwargs): pass
    def grid_remove(self): pass
    def rowconfigure(self, *args, **kwargs): pass
    def columnconfigure(self, *args, **kwargs): pass
    def bind(self, *args, **kwargs): pass
    def configure(self, **kwargs): self.options.update(kwargs)
    def grid_size(self): return (18, 9)


class RTE(unittest.TestCase):
    def harmonic_panel(self, choices=('1+2', '1+2+3', '3')):
        panel = self.make_worker()
        panel.zurich_scan_enabled = False
        for key, choice in zip(app.PPMS_SR830_KEYS, choices):
            setattr(panel, app.PPMS_SR830_HARMONIC_ATTRS[key], Var(choice))
        return panel

    def test_mixed_harmonic_passes_drop_finished_meters(self):
        panel = self.harmonic_panel()
        settings = self.settings()
        settings['active_lockins'] = app.PPMS_SR830_KEYS
        passes = panel._rte_harmonic_passes(settings)
        self.assertEqual([p['active_lockins'] for p in passes],
                         [app.PPMS_SR830_KEYS, ('830_a', '830_b'), ('830_b',)])
        self.assertEqual([[p[app.PPMS_SR830_HARMONIC_SETTING_KEYS[k]] for k in p['active_lockins']]
                          for p in passes], [[1, 1, 3], [2, 2], [3]])

    def test_finished_meter_csv_cells_are_empty(self):
        panel = self.harmonic_panel(('1', '1+2', '3'))
        panel.ppms_instrument_controllers['830_b'] = Mock(resource_name='GPIB0::9::INSTR')
        settings = dict(self.settings(), active_lockins=('830_a', '830_b'))
        def sample(freq, active, duration):
            return {f'sr830_{8 if k == "830_a" else 9}_x_v': 1e-6 for k in active}
        panel._acquire_ppms_first_point = lambda *args: (sample(*args), 2)
        panel._rte_set_harmonic = Mock()
        panel._rte_harmonic_scan_worker('Current', ((1, 1, .1),),
                                      panel._rte_harmonic_passes(settings), 5., 0., .001)
        first, second = panel.ppms_field_scan_rows
        self.assertEqual(set(first), set(second))
        self.assertEqual(second['sr830_8_x_v'], '')
        self.assertEqual(second['sr830_8_harmonic'], '')
        self.assertEqual(second['sr830_9_harmonic'], 2)

    def test_stop_between_harmonic_passes_sends_no_next_harmonic(self):
        panel = self.harmonic_panel()
        panel._rte_set_harmonic = Mock()
        panel._append_ppms_live_csv.side_effect = lambda row: panel.ppms_field_scan_stop_event.set()
        with self.assertRaises(app.PPMSScanCancelled):
            panel._rte_harmonic_scan_worker('Current', ((1, 1, .1),),
                                          panel._rte_harmonic_passes(self.settings()), 5., 0., .001)
        panel._rte_set_harmonic.assert_not_called()

    def test_field_passes_do_not_restart_source_or_kepco(self):
        panel = self.harmonic_panel()
        panel._rte_set_harmonic = Mock()
        panel._rte_harmonic_scan_worker('Field', ((1, 1, -1.), (1, 2, 1.)),
                                      panel._rte_harmonic_passes(self.settings()), 5., 0., .001)
        panel._configure_ppms_transport_scan.assert_called_once()
        panel.rte_controllers['kepco'].set_output.assert_called_once()
        self.assertEqual(panel.rte_controllers['kepco'].set_scan_current.call_count, 3)

    def test_multi_pass_keeps_source_running_and_labels_csv(self):
        for choice, count in [('1+2', 2), ('1+2+3', 3)]:
            panel = self.harmonic_panel((choice, '2', '3'))
            passes = panel._rte_harmonic_passes(self.settings())
            panel._rte_set_harmonic = Mock()
            plan = ((1, 1, .1), (1, 2, .2))
            panel._rte_harmonic_scan_worker('Current', plan, passes, 5., 0., .001)
            panel._configure_ppms_transport_scan.assert_called_once()
            self.assertEqual(panel._rte_update_current.call_count, count * 2 - 1)
            self.assertEqual([c.args for c in panel._rte_set_harmonic.call_args_list],
                             [('830_a', h) for h in range(2, count + 1)])
            self.assertEqual([r['sr830_8_harmonic'] for r in panel.ppms_field_scan_rows],
                             [h for h in range(1, count + 1) for _ in range(2)])
            self.assertEqual([r['harmonic_pass'] for r in panel.ppms_field_scan_rows],
                             [h for h in range(1, count + 1) for _ in range(2)])
            self.assertEqual(panel._acquire_ppms_first_point.call_count, count)
            # Never between passes; once, at the end (the next test).
            panel._stop_ppms_transport_source.assert_called_once()

    def test_a_completed_current_scan_ends_with_the_6221_off(self):
        panel = self.harmonic_panel(('1+2+3', '2', '3'))
        order = Mock()
        order.attach_mock(panel._average_ppms_transport_sample, 'acquire')
        order.attach_mock(panel._acquire_ppms_first_point, 'acquire_first')
        order.attach_mock(panel._stop_ppms_transport_source, 'off')
        order.attach_mock(panel._ppms_query_numbers, 'query')
        panel._rte_set_harmonic = Mock()
        panel._rte_harmonic_scan_worker('Current', ((1, 1, .1), (1, 2, .2)),
                                      panel._rte_harmonic_passes(self.settings()), 5., 0., .001)
        names = [c[0] for c in order.mock_calls]
        self.assertEqual(names[-2:], ['off', 'query'], "off after the last point, then read back")
        self.assertEqual(names.count('off'), 1, "never between harmonic passes")
        self.assertEqual(order.mock_calls[-1].args, ('6221', 'OUTP?', 1))
        panel.rte_controllers['kepco'].safe_output_off.assert_not_called()

    def test_a_6221_that_does_not_read_back_off_is_reported(self):
        panel = self.harmonic_panel(('1', '2', '3'))
        panel._ppms_query_numbers = Mock(return_value=(1.,))
        with self.assertRaisesRegex(RuntimeError, 'did not read back OFF'):
            panel._rte_harmonic_scan_worker('Current', ((1, 1, .1),),
                                          panel._rte_harmonic_passes(self.settings()), 5., 0., .001)

    def test_field_scans_and_stops_keep_their_outputs(self):
        panel = self.harmonic_panel(('1', '2', '3'))
        panel._rte_harmonic_scan_worker('Field', ((1, 1, -1.), (1, 2, 1.)),
                                      panel._rte_harmonic_passes(self.settings()), 5., 0., .001)
        panel._stop_ppms_transport_source.assert_not_called()
        panel.rte_controllers['kepco'].safe_output_off.assert_not_called()
        panel = self.harmonic_panel(('1', '2', '3'))
        panel._append_ppms_live_csv.side_effect = lambda row: panel.ppms_field_scan_stop_event.set()
        with self.assertRaises(app.PPMSScanCancelled):
            panel._rte_harmonic_scan_worker('Current', ((1, 1, .1), (1, 2, .2)),
                                          panel._rte_harmonic_passes(self.settings()), 5., 0., .001)
        panel._stop_ppms_transport_source.assert_not_called()

    def test_harmonic_failure_does_not_acquire_next_pass(self):
        panel = self.harmonic_panel()
        panel._rte_set_harmonic = Mock(side_effect=RuntimeError('bad harmonic'))
        with self.assertRaisesRegex(RuntimeError, 'bad harmonic'):
            panel._rte_harmonic_scan_worker('Current', ((1, 1, .1),),
                                          panel._rte_harmonic_passes(self.settings()), 5., 0., .001)
        self.assertEqual(len(panel.ppms_field_scan_rows), 1)
        panel._stop_ppms_transport_source.assert_called_once()
        panel.rte_controllers['kepco'].safe_output_off.assert_not_called()

    def test_harmonic_readback_and_cancellation(self):
        panel = self.harmonic_panel()
        panel._ppms_write_sr830_checked = Mock()
        panel._ppms_query_numbers = Mock(return_value=[2])
        panel._rte_set_harmonic('830_a', 2)
        panel._ppms_write_sr830_checked.assert_called_once_with('830_a', 'HARM 2')
        with self.assertRaises(RuntimeError):
            panel._rte_set_harmonic('830_a', 3)
        panel.ppms_field_scan_stop_event.set()
        panel._ppms_write_sr830_checked.reset_mock()
        with self.assertRaises(app.PPMSScanCancelled):
            panel._rte_set_harmonic('830_a', 1)
        panel._ppms_write_sr830_checked.assert_not_called()

    def test_future_harmonics_validated_before_start(self):
        panel = self.harmonic_panel(('1+2+3', '2', '3'))
        settings = self.settings()
        settings['frequency_hz'] = 40000
        with self.assertRaises(ValueError):
            panel._rte_harmonic_passes(settings)

    def test_current_defaults_and_round_trip_point_count(self):
        panel = self.make_worker()
        panel.ppms_simple_mode = Var('Current')
        panel.ppms_simple_range = [Var('') for _ in range(3)]
        panel.rte_scan_units = Var('')
        panel.rte_round_trip = Var(True)
        panel._redraw_ppms_transport_plot = Mock()
        panel._rte_mode_changed()
        values = [v.get() for v in panel.ppms_simple_range]
        self.assertEqual(values, ['0.01', '1', '0.01'])
        self.assertEqual(panel.rte_scan_units.get(), '6221 mA RMS')
        self.assertFalse(panel.rte_round_trip.get())
        forward = app.rte_scan_plan('Current', *values, round_trip=False)
        both = app.rte_scan_plan('Current', *values, round_trip=True)
        self.assertEqual(len(forward), 100)
        self.assertEqual(len(both), 199)
        self.assertAlmostEqual(both[0][2], .01)
        self.assertAlmostEqual(both[99][2], 1.)
        self.assertAlmostEqual(both[100][2], .99)
        self.assertAlmostEqual(both[-1][2], .01)
        panel.ppms_simple_mode.set('Field')
        panel._rte_mode_changed()
        self.assertTrue(panel.rte_round_trip.get())
        self.assertEqual([v.get() for v in panel.ppms_simple_range], ['-3', '3', '0.05'])
        panel.ppms_simple_mode.set('Current')
        panel._rte_mode_changed()
        self.assertFalse(panel.rte_round_trip.get())

    def make_worker(self):
        panel = app.RTEPanel.__new__(app.RTEPanel)
        panel.parent_app = Mock(closing=False)
        panel.closing = panel.rte_closed = False
        panel.ppms_field_scan_stop_event = threading.Event()
        panel.ppms_field_scan_rows = []
        panel.ppms_field_scan_started_at = time.monotonic()
        panel.ppms_field_scan_total = 2
        panel.ppms_live_csv_fields = None
        panel.ppms_instrument_controllers = {'830_a': Mock(resource_name='GPIB0::8::INSTR')}
        panel.rte_controllers = {'kepco': Mock(output_owned=True, cached_current_a=0.), 'gauss': Mock()}
        panel._configure_ppms_transport_scan = Mock()
        panel._rte_update_current = Mock()
        panel._stop_ppms_transport_source = Mock()
        panel._ppms_query_numbers = Mock(return_value=(0.,))  # 6221 OUTP? after the off command
        panel._rte_settle_field = Mock(return_value=-2.)
        panel._rte_read_field = Mock(return_value=-2.)
        sample = {'sr830_8_x_v': 1e-6, 'sr830_8_y_v': -1e-6, 'sr830_8_r_v': 1.414e-6,
                  'sr830_8_theta_deg': -45., 'sr830_8_reference_valid': True}
        panel._acquire_ppms_first_point = Mock(return_value=(dict(sample), 2))
        panel._average_ppms_transport_sample = Mock(return_value=dict(sample))
        panel.post_ui = Mock()
        panel._append_ppms_live_csv = Mock()
        panel._rte_wait = lambda seconds: (
            (_ for _ in ()).throw(app.PPMSScanCancelled('stop'))
            if panel.ppms_field_scan_stop_event.is_set() else None)
        return panel

    def settings(self):
        return app.validate_ppms_transport_scan_settings(1., 317.3, 50., 1, 2, ('830_a',))

    def test_plan_both_directions_uneven_endpoint_round_trip_and_loops(self):
        plan = app.rte_scan_plan('Field', 1, -1, .75, 2, True)
        self.assertEqual([p[2] for p in plan[:7]], [1., .25, -.5, -1., -.5, .25, 1.])
        self.assertEqual(len(plan), 14)
        self.assertEqual(plan[7], (2, 1, 1.))
        self.assertEqual(app.rte_scan_plan('Current', 1, 1, .1), ((1, 1, 1.),))

    def test_plan_rejects_invalid_ranges_and_removed_modes(self):
        # Angle is an RT-E mode again since 2026-10-13 (the Newmark stage);
        # only angles beyond +/-360 deg of the stage zero are refused.
        for args in [('Temperature', 1, 2, 1), ('Angle', 0, 370, 5),
                     ('Field', -11, 1, 1), ('Field', 0, float('nan'), 1),
                     ('Current', 1, 2, 0), ('Field', 0, 1, 1, 1.5)]:
            with self.subTest(args=args), self.assertRaises(ValueError):
                app.rte_scan_plan(*args)

    def test_current_scan_never_writes_kepco(self):
        panel = self.make_worker()
        panel._rte_scan_worker('Current', ((1, 1, .1), (1, 2, .2)), self.settings(), 5., 0., .001)
        panel.rte_controllers['kepco'].set_output.assert_not_called()
        panel.rte_controllers['kepco'].set_scan_current.assert_not_called()
        panel._rte_settle_field.assert_not_called()
        self.assertEqual([c.args[0]['current_rms_ma'] for c in panel._configure_ppms_transport_scan.call_args_list], [.1])
        self.assertEqual([c.args[0]['current_rms_ma'] for c in panel._rte_update_current.call_args_list], [.2])
        self.assertEqual([r['6221_current_rms_ma'] for r in panel.ppms_field_scan_rows], [.1, .2])
        panel._stop_ppms_transport_source.assert_not_called()

    def test_field_scan_configures_output_once_and_keeps_signed_field(self):
        panel = self.make_worker()
        panel._rte_scan_worker('Field', ((1, 1, -1.), (1, 2, 1.)), self.settings(), 5., 0., .001)
        panel.rte_controllers['kepco'].set_output.assert_called_once_with('CURR', -1., 5.)
        panel.rte_controllers['kepco'].set_scan_current.assert_called_once_with(1.)
        self.assertEqual(panel._configure_ppms_transport_scan.call_count, 1)
        self.assertEqual(panel.ppms_field_scan_rows[0]['measured_field_g'], -2.)
        self.assertEqual(panel.ppms_field_scan_rows[0]['sr830_8_harmonic'], 1)
        for row in panel.ppms_field_scan_rows:
            self.assertFalse(any('temperature' in k or 'angle' in k for k in row))
        panel.rte_controllers['kepco'].safe_output_off.assert_not_called()

    def test_live_current_update_writes_only_amplitude(self):
        panel = self.make_worker()
        settings = self.settings()
        settings['continuous_sweep_peak_a'] = .002
        peak = settings['peak_a']
        panel._ppms_query_numbers = Mock(side_effect=[[1], [peak], [1]])
        panel._ppms_write_6221_checked = Mock()
        app.RTEPanel._rte_update_current(panel, settings)
        panel._ppms_write_6221_checked.assert_called_once_with(f'SOUR:WAVE:AMPL {peak:.12e}')

    def test_live_current_update_rejects_off_output_and_bad_readback(self):
        for replies in ([[0]], [[1], [0.0]]):
            panel = self.make_worker()
            settings = self.settings()
            settings['continuous_sweep_peak_a'] = .002
            panel._ppms_query_numbers = Mock(side_effect=replies)
            panel._ppms_write_6221_checked = Mock()
            with self.assertRaises(RuntimeError):
                app.RTEPanel._rte_update_current(panel, settings)
            panel._configure_ppms_transport_scan.assert_not_called()

    def test_round_trip_updates_live_without_reinitialization(self):
        panel = self.make_worker()
        plan = app.rte_scan_plan('Current', .1, .2, .1, 2, True)
        panel._rte_scan_worker('Current', plan, self.settings(), 5., 0., .001)
        panel._configure_ppms_transport_scan.assert_called_once()
        self.assertEqual(panel._rte_update_current.call_count, len(plan) - 1)
        self.assertEqual(len(panel.ppms_field_scan_rows), len(plan))

    def test_stop_before_start_sends_no_commands(self):
        panel = self.make_worker()
        panel.ppms_field_scan_stop_event.set()
        with self.assertRaises(app.PPMSScanCancelled):
            panel._rte_scan_worker('Field', ((1, 1, 0.),), self.settings(), 5., 0., 1.)
        panel._configure_ppms_transport_scan.assert_not_called()
        panel.rte_controllers['kepco'].set_output.assert_not_called()
        panel._append_ppms_live_csv.assert_not_called()

    def test_stop_during_acquisition_discards_partial_point(self):
        panel = self.make_worker()
        panel._acquire_ppms_first_point.side_effect = app.PPMSScanCancelled('stop')
        with self.assertRaises(app.PPMSScanCancelled):
            panel._rte_scan_worker('Field', ((1, 1, 0.),), self.settings(), 5., 0., 1.)
        self.assertEqual(panel.ppms_field_scan_rows, [])
        panel._append_ppms_live_csv.assert_not_called()
        panel.rte_controllers['kepco'].safe_output_off.assert_not_called()

    def test_field_failure_turns_outputs_off_even_when_source_off_fails(self):
        panel = self.make_worker()
        panel._rte_settle_field.side_effect = RuntimeError('field timeout')
        panel._stop_ppms_transport_source.side_effect = RuntimeError('source off failed')
        with self.assertRaises(RuntimeError):
            panel._rte_scan_worker('Field', ((1, 1, 0.),), self.settings(), 5., 0., 1.)
        panel.rte_controllers['kepco'].safe_output_off.assert_called_once()
        self.assertFalse(panel.ppms_field_scan_rows)

    def test_lockin_error_preserves_magnet_but_stops_excitation(self):
        panel = self.make_worker()
        panel._acquire_ppms_first_point.side_effect = RuntimeError('reference lost')
        with self.assertRaises(RuntimeError):
            panel._rte_scan_worker('Field', ((1, 1, 0.),), self.settings(), 5., 0., 1.)
        panel._stop_ppms_transport_source.assert_called_once()
        panel.rte_controllers['kepco'].safe_output_off.assert_not_called()

    def test_csv_write_failure_does_not_publish_unsaved_point(self):
        panel = self.make_worker()
        panel._append_ppms_live_csv.side_effect = OSError('disk full')
        with self.assertRaises(OSError):
            panel._rte_scan_worker('Current', ((1, 1, 1.),), self.settings(), 5., 0., 1.)
        self.assertFalse(panel.ppms_field_scan_rows)

    def test_real_csv_append_retains_all_demod_columns(self):
        panel = self.make_worker()
        del panel._append_ppms_live_csv
        sample = panel._acquire_ppms_first_point.return_value[0]
        for i in range(1, 5):
            sample.update({f'zurich_demod{i}_enabled': i < 4,
                           f'zurich_demod{i}_x_v': i*1e-9 if i < 4 else '',
                           f'zurich_demod{i}_status': 'OK' if i < 4 else 'disabled'})
        with tempfile.TemporaryDirectory() as tmp:
            panel.ppms_live_csv_path = Path(tmp) / 'rte.csv'
            panel._rte_scan_worker('Current', ((1, 1, 1.),), self.settings(), 5., 0., 1.)
            with panel.ppms_live_csv_path.open(encoding='utf-8-sig', newline='') as f:
                rows = list(csv.DictReader(f))
            self.assertEqual(len(rows), 1)
            self.assertEqual(rows[0]['zurich_demod4_x_v'], '')
            self.assertEqual(rows[0]['zurich_demod4_status'], 'disabled')
            self.assertEqual(rows[0]['measured_field_g'], '-2.0')

    def test_constructor_builds_independent_panel_without_hardware(self):
        parent = Mock(closing=False)
        parent.root.after.return_value = 'timer'
        with ExitStack() as stack:
            for name in ('Frame', 'LabelFrame', 'Label', 'Entry', 'Button', 'Combobox', 'Checkbutton', 'Progressbar'):
                stack.enter_context(patch.object(app.ttk, name, Widget))
            stack.enter_context(patch.object(app.tk, 'Canvas', Widget))
            for name in ('StringVar', 'IntVar', 'BooleanVar'):
                stack.enter_context(patch.object(app.tk, name, Var))
            stack.enter_context(patch.object(app, 'ThreadPoolExecutor', ImmediateExecutor))
            stack.enter_context(patch.object(app.CombinedControlApp, '_create_connection_indicator', lambda *a, **k: Widget()))
            stack.enter_context(patch.object(app.CombinedControlApp, '_set_connection_indicator', Mock()))
            stack.enter_context(patch.object(app.CombinedControlApp, '_draw_nonlocal_series', Mock()))
            panel = app.RTEPanel(parent, Widget())
            self.assertEqual(panel.ppms_simple_mode.get(), 'Current')
            self.assertFalse(panel.rte_round_trip.get())
            self.assertEqual([v.get() for v in panel.ppms_simple_range], ['0.01', '1', '0.01'])
            measurement = panel.ppms_instruments_frame.parent
            rack = panel.rte_connection_widgets['kepco'][0].parent
            for key in ('6221', '2400'):
                self.assertIs(panel.ppms_device_resource_widgets[key].parent, rack)
                self.assertIs(panel.ppms_device_connect_buttons[key].parent, rack)
                self.assertIs(panel.ppms_device_disconnect_buttons[key].parent, rack)
            self.assertEqual(measurement.options['text'], 'Measurement instruments')
            for entry, _, _ in panel.rte_connection_widgets.values():
                self.assertIs(entry.parent.parent, measurement)
                self.assertNotIn('text', entry.parent.options)
            self.assertNotIn('text', panel.ppms_instruments_frame.options)
            self.assertEqual(panel.zurich_harmonic.get(), '1+2+3')
            self.assertEqual(panel.zurich_resource.get(), 'DEV4414 (Super)')
            self.assertEqual(panel.zurich_controller.mfli_server_candidates, (('192.168.68.250', 8004),))
            self.assertFalse(hasattr(app.NonLocalACController(lambda _: None), 'mfli_server_candidates'))
            self.assertEqual(len(panel.ppms_harmonic_canvases), 9)
            self.assertFalse(panel.ppms_environment_controller.connected)
            self.assertFalse(panel.rte_controllers['kepco'].connected)
            self.assertNotIn('ppms_motor_move_button', panel.__dict__)
            self.assertNotIn('ppms_temperature_scan_range', panel.__dict__)
            self.assertEqual(panel.rte_resources['gauss'].get(), app.STFMR_LOCKIN_DEFAULT_RESOURCES['gauss'])
            self.assertEqual(panel.ppms_lockin_choice_widgets[0][0].options['state'], 'readonly')
            panel.safe_close_ppms()
            panel.safe_close_ppms()  # idempotent

    def test_rte_worker_has_no_environment_or_motor_calls(self):
        import inspect
        source = inspect.getsource(app.RTEPanel)
        self.assertNotIn('.set_temperature(', source)
        self.assertNotIn('.set_field(', source)
        self.assertNotIn('PPMSMotorDebug(', source)

    def test_idle_status_is_read_only_and_does_not_query_ppms(self):
        panel = self.make_worker()
        panel._ppms_query_numbers = Mock(return_value=(317.3,))
        panel._ppms_read_sr830_status = Mock(return_value=0)
        panel._read_ppms_current_display = Mock(return_value='ON / 1 mA RMS')
        data = panel._rte_idle_read(317.3)
        self.assertEqual(data['field_display'], 'LS475 field: -2 G')
        self.assertTrue(data['references']['830_a'][1])
        panel.rte_controllers['kepco'].set_output.assert_not_called()
        panel._configure_ppms_transport_scan.assert_not_called()

    def test_rte_server_override_and_shared_default_are_isolated(self):
        with patch.object(app.socket, 'create_connection') as connect:
            connect.return_value.__enter__.return_value = Mock()
            servers, _ = app.NonLocalACController._mfli_reachable_servers(candidates=(('192.168.68.250', 8004),))
            self.assertEqual(servers, [('192.168.68.250', 8004)])
            connect.assert_called_once_with(('192.168.68.250', 8004), 1.2)
            connect.reset_mock()
            servers, _ = app.NonLocalACController._mfli_reachable_servers()
            self.assertEqual(servers, list(app.NONLOCAL_MFLI_SERVER_CANDIDATES))

    def test_rte_unreachable_message_uses_correct_address(self):
        controller = app.NonLocalACController(lambda _: None)
        controller.mfli_server_candidates = (('192.168.68.250', 8004),)
        with patch.object(app.socket, 'create_connection', side_effect=TimeoutError('timed out')):
            with self.assertRaises(RuntimeError) as caught:
                controller.connect_mfli('MF-DEV4414')
        message = str(caught.exception)
        self.assertIn('dev4414', message)
        self.assertIn('192.168.68.250', message)
        self.assertNotIn('192.168.69.77', message)
        self.assertNotIn('192.168.69.78', message)

    def test_field_read_failure_after_acquisition_does_not_save_point(self):
        panel = self.make_worker()
        panel._rte_read_field.side_effect = [-2., RuntimeError('LS475 unavailable')]
        with self.assertRaisesRegex(RuntimeError, 'LS475 unavailable'):
            panel._rte_scan_worker('Field', ((1, 1, 0.),), self.settings(), 5., 0., 1.)
        panel.rte_controllers['kepco'].safe_output_off.assert_called_once()
        panel._append_ppms_live_csv.assert_not_called()

    def test_harmonic_change_lets_autorange_restart_immediately(self):
        panel = self.make_worker()
        panel._ppms_write_sr830_checked = Mock()
        panel._ppms_query_numbers = Mock(return_value=(2,))
        state = types.SimpleNamespace(low_range_count=2, range_cooldown=3)
        panel.ppms_sr830_range_states = {'830_a': state}
        panel._rte_set_harmonic('830_a', 2)
        # The range held for the previous order says nothing about the new one.
        self.assertEqual((state.low_range_count, state.range_cooldown), (0, 0))


if __name__ == '__main__':
    unittest.main()
