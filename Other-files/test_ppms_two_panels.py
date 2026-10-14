"""PPMS2 isolation and Zurich lifecycle without Tk or physical devices."""
import threading
import textwrap
import types
import unittest
from concurrent.futures import Future
from unittest.mock import Mock, patch
from test_ppms_motor_debug import app


class Var:
    def __init__(self, value=None, **kwargs): self.value = value
    def get(self): return self.value
    def set(self, value): self.value = value


class ImmediateExecutor:
    def __init__(self, **kwargs): pass
    def submit(self, fn, *args):
        result = Future()
        try: result.set_result(fn(*args))
        except BaseException as error: result.set_exception(error)
        return result
    def shutdown(self, **kwargs): pass


class TwoPanelTests(unittest.TestCase):
    def test_zurich_selection_applies_without_reconnecting(self):
        for harmonic in ('1', '2', '3', '1+2', '1+2+3'):
            parent, panel, controller = self.make_panel()
            parent.closing = False
            controller.mfli_connected = True
            panel.zurich_harmonic.set(harmonic)
            panel._refresh_ppms2_plot_layout = Mock()
            panel._apply_ppms2_zurich_harmonic()
            controller.connect_mfli.assert_not_called()
            controller.disconnect_mfli.assert_not_called()
            self.assertEqual(controller.configure_mfli.call_args.args[1], 1 if '+' in harmonic else int(harmonic))
            controller.disable_unused_ppms_demods.assert_called_once_with(harmonic)
            self.assertEqual(controller.configure_second_harmonic.call_count, int('+' in harmonic))
            self.assertEqual(controller.configure_third_harmonic.call_count, int(harmonic == '1+2+3'))

    def test_live_sr830_harmonic_update_without_reconnect(self):
        for cls in (app.CombinedControlApp, app.PPMS2Panel):
            for key in ('830_a', '830_b'):
                panel = cls.__new__(cls)
                panel.ppms_environment_busy = panel.ppms_field_scan_running = panel.closing = False
                panel.ppms_transport_harmonic_a_var = Var(3)
                panel.ppms_transport_harmonic_b_var = Var(3)
                controller = Mock(connected=True, lock=threading.RLock())
                panel.ppms_instrument_controllers = {key: controller}
                panel.ppms_executor = ImmediateExecutor()
                panel.post_ui = lambda callback: callback()
                panel._update_ppms_control_ui = Mock()
                panel._redraw_ppms_transport_plot = Mock()
                panel.thread_log = Mock()
                panel._ppms_write_sr830_checked = Mock()
                panel._ppms_query_numbers = Mock(return_value=(3,))
                panel._ppms_apply_selected_harmonic(key)
                panel._ppms_write_sr830_checked.assert_called_once_with(key, 'HARM 3')
                panel._ppms_query_numbers.assert_called_once_with(key, 'HARM?', 1)
                controller.connect.assert_not_called()
                self.assertFalse(panel.ppms_environment_busy)
                panel._ppms_write_sr830_checked.reset_mock()
                controller.connected = False
                panel._ppms_apply_selected_harmonic(key)
                panel._ppms_write_sr830_checked.assert_not_called()

    def test_legacy_reference_controller_follows_demod_four(self):
        block = app._MFLI_BRIDGE_SOURCE.split('            if extref_demod == 3:', 1)[1].split('            configured_extref_demod = extref_demod', 1)[0]
        code = textwrap.dedent('            if extref_demod == 3:' + block)
        daq = Mock()
        daq.getInt.side_effect = lambda node: 1 if '/extrefs/0/' in node else 3
        env = dict(EXTREF=0, extref_demod=3, daq=daq, device='dev4414')
        exec(code, env)
        self.assertEqual(env['EXTREF'], 1)
        daq.setInt.assert_called_once_with('/dev4414/extrefs/0/enable', 0)
        env['extref_demod'] = 1
        daq.reset_mock()
        exec(code, env)
        self.assertEqual(env['EXTREF'], 0)
        daq.setInt.assert_called_once_with('/dev4414/extrefs/1/enable', 0)
        env['extref_demod'] = 3
        daq.getInt.side_effect = lambda node: 1
        with self.assertRaises(RuntimeError):
            exec(code, env)

    def test_all_modes_connection_mapping_and_cleanup(self):
        for mode in ('1', '2', '3', '1+2', '1+2+3'):
            with self.subTest(mode=mode):
                _, panel, controller = self.make_panel()
                panel.zurich_harmonic.set(mode)
                panel.connect_ppms2_zurich()
                self.assertEqual(controller.configure_mfli.call_args.args[1], 1 if '+' in mode else int(mode))
                self.assertEqual(controller.configure_second_harmonic.call_count, int('+' in mode))
                self.assertEqual(controller.configure_third_harmonic.call_count, int(mode == '1+2+3'))
                controller.disable_unused_ppms_demods.assert_called_once_with(mode)
                self.assertEqual(controller.configure_mfli.call_args.kwargs.get('external_reference_demodulator'), 3)

    def test_unused_demodulators_disabled_without_touching_reference(self):
        controller = app.NonLocalACController.__new__(app.NonLocalACController)
        controller.lock = threading.RLock()
        controller.mfli_bridge = None
        controller.mfli_device = 'dev4414'
        controller.mfli_daq = Mock()
        controller.mfli_daq.getInt.return_value = 0
        for mode, indices in (('1', (1,2)), ('2', (1,2)), ('3', (1,2)), ('1+2', (2,)), ('1+2+3', ())):
            controller.mfli_config = {'extref_demodulator': 3}
            controller.mfli_daq.reset_mock()
            controller.disable_unused_ppms_demods(mode)
            self.assertEqual([c.args[0] for c in controller.mfli_daq.setInt.call_args_list],
                             [f'/dev4414/demods/{i}/enable' for i in indices])

    def test_compact_five_row_axes_stay_inside_canvas(self):
        panel = app.CombinedControlApp.__new__(app.CombinedControlApp)
        panel._draw_plot_grid = Mock()
        for height in (80, 95, 119, 150):
            canvas = Mock(ppms_compact_plot=True)
            canvas.winfo_width.return_value = 600
            canvas.winfo_height.return_value = height
            panel._draw_nonlocal_series(canvas, {'test': [(0., 0.), (1., 1.)]},
                                        'Magnetic field (T)', 'X voltage', 'waiting', show_legend=False)
            rect = canvas.create_rectangle.call_args.args
            self.assertEqual(rect[3], height - 44)
            for call in canvas.create_text.call_args_list:
                self.assertGreaterEqual(call.args[1], 0)
                self.assertLess(call.args[1], height)
            title = [c for c in canvas.create_text.call_args_list if c.kwargs.get('text') == 'Magnetic field (T)']
            self.assertEqual(title[0].args[1], height - 10)

    def test_sr830_titles_with_and_without_zurich_use_recorded_orders(self):
        from itertools import product
        for select_a, select_b, select_z in product((False, True), repeat=3):
            _, panel, _ = self.make_panel()
            for key, selected in zip(('830_a', '830_b', 'zurich'), (select_a, select_b, select_z)):
                panel.ppms_lockin_selection[key].set(selected)
            panel.ppms_transport_canvas = Mock()
            panel.ppms_transport_canvases = {(k, c): Mock() for k in ('830_a', '830_b') for c in ('x', 'y', 'theta')}
            panel.ppms_transport_plot_frames = {k: Mock() for k in panel.ppms_transport_canvases}
            panel.ppms_plot_addresses = {'830_a': 8, '830_b': 9}
            panel.ppms_transport_harmonic_a_var.set(1)
            panel.ppms_transport_harmonic_b_var.set(1)
            panel.ppms_field_scan_rows = [dict(measured_field_oe=10, sr830_8_harmonic=2, sr830_9_harmonic=3,
                sr830_8_x_v=1, sr830_8_y_v=2, sr830_8_r_v=3, sr830_8_theta_deg=4,
                sr830_9_x_v=1, sr830_9_y_v=2, sr830_9_r_v=3, sr830_9_theta_deg=4)]
            panel._draw_nonlocal_series = Mock()
            panel._redraw_ppms_transport_plot()
            for (key, channel), frame in panel.ppms_transport_plot_frames.items():
                expected = '2nd harmonic' if key == '830_a' else '3rd harmonic'
                self.assertIn(expected, frame.configure.call_args.kwargs['text'])
            panel.ppms_field_scan_rows = []
            panel._redraw_ppms_transport_plot()
            for frame in panel.ppms_transport_plot_frames.values():
                self.assertIn('1st harmonic', frame.configure.call_args.kwargs['text'])

    def test_participation_click_order_and_reselection(self):
        from itertools import permutations
        for sequence in permutations(('830_a', '830_b', 'zurich')):
            _, panel, _ = self.make_panel()
            for var in panel.ppms_lockin_selection.values():
                var.set(False)
            self.assertEqual(panel._ppms2_ordered_plot_rows(), [])
            panel.zurich_harmonic.set('1+2+3')
            for key in sequence:
                panel.ppms_lockin_selection[key].set(True)
                panel._ppms2_ordered_plot_rows()
            expected = [(k, suffix) for k in sequence for suffix in (('', '_2', '_3') if k == 'zurich' else ('',))]
            self.assertEqual(panel._ppms2_ordered_plot_rows(), expected)
            first = sequence[0]
            panel.ppms_lockin_selection[first].set(False)
            panel._ppms2_ordered_plot_rows()
            panel.ppms_lockin_selection[first].set(True)
            panel._ppms2_ordered_plot_rows()
            self.assertEqual(panel.ppms_selection_order, list(sequence[1:]) + [first])
            panel.zurich_harmonic.set('1')
            self.assertEqual([k for k, suffix in panel._ppms2_ordered_plot_rows()], list(sequence[1:]) + [first])

    def test_temperature_shades_follow_values_not_acquisition_order(self):
        panel = app.CombinedControlApp.__new__(app.CombinedControlApp)
        panel.ppms_active_scan_mode = 'Field+Temperature'
        panel.ppms_temperature_scan_range = [Var('10'), Var('100'), Var('10')]
        for base in app.PPMS_HARMONIC_COLORS.values():
            colors = panel._ppms_temperature_plot_colors(base, {'low': 10, 'mid': 50, 'high': 100})
            brightness = lambda c: sum(int(c[i:i+2], 16) for i in (1,3,5))
            self.assertGreater(brightness(colors['low']), brightness(colors['mid']))
            self.assertGreater(brightness(colors['mid']), brightness(colors['high']))
            self.assertEqual(panel._ppms_temperature_plot_colors(base, {'low': 10})['low'], colors['low'])
            panel.ppms_temperature_scan_range = [Var('100'), Var('10'), Var('10')]
            self.assertEqual(panel._ppms_temperature_plot_colors(base, {'high': 100, 'mid': 50, 'low': 10}), colors)
        panel.ppms_active_scan_mode = 'Field'
        self.assertEqual(panel._ppms_temperature_plot_colors('#0072B2', {'a': 10, 'b': 100}), {})

    def test_temperature_rate_boundary_and_return_to_high_temperature(self):
        for cls in (app.CombinedControlApp, app.PPMS2Panel):
            panel = cls.__new__(cls)
            panel.ppms_temperature_target_var = Var('300')
            panel.ppms_temperature_rate_var = Var('10')
            for target, rate in (('9.9', '1'), ('10', '10'), ('2', '1'), ('300', '10')):
                panel.ppms_temperature_target_var.set(target)
                panel._ppms_adjust_low_temperature_rate()
                self.assertEqual(panel.ppms_temperature_rate_var.get(), rate)

    def test_finish_zero_then_persistent_only_after_success(self):
        panel = app.CombinedControlApp.__new__(app.CombinedControlApp)
        panel.closing = False
        panel.ppms_field_scan_stop_event = threading.Event()
        panel.ppms_zero_persist_pending = True
        panel._ppms_nested_scan_worker = Mock()
        panel.post_ui = lambda f: None
        panel.ppms_environment_controller = Mock()
        panel.ppms_environment_controller.read_environment.side_effect = [
            {'field_oe': 0., 'field_status': 'StableDriven'},
            {'field_oe': 0., 'field_status': 'StablePersistent'}]
        panel._ppms_scan_with_final_field()
        self.assertEqual([c.args for c in panel.ppms_environment_controller.set_field.call_args_list], [(0.,100.,0,1),(0.,100.,0,0)])
        self.assertTrue(panel.ppms_zero_persist_completed)
        for failure in (True, False):
            panel.ppms_zero_persist_pending = True
            panel.ppms_environment_controller.reset_mock()
            panel._ppms_nested_scan_worker.side_effect = RuntimeError('scan failed') if failure else None
            if failure:
                with self.assertRaises(RuntimeError): panel._ppms_scan_with_final_field()
            else:
                panel.ppms_field_scan_stop_event.set()
                panel._ppms_scan_with_final_field()
            panel.ppms_environment_controller.set_field.assert_not_called()
            self.assertFalse(panel.ppms_zero_persist_pending)

    def test_dual_harmonic_config_preserves_pll(self):
        controller = app.NonLocalACController.__new__(app.NonLocalACController)
        controller.lock = threading.RLock()
        controller.mfli_config = dict(extref_demodulator=1, harmonic=1)
        controller.mfli_bridge = None
        controller.mfli_device = 'dev4414'
        nodes = {}
        controller.mfli_daq = Mock()
        controller.mfli_daq.setInt.side_effect = lambda key, value: nodes.update({key: value})
        controller.mfli_daq.getInt.side_effect = lambda key: nodes[key]
        controller.configure_second_harmonic(.1)
        self.assertEqual(nodes['/dev4414/demods/2/harmonic'], 2)
        self.assertTrue(all('/demods/2/' in key for key in nodes))
        controller.mfli_bridge = Mock()
        controller.configure_second_harmonic(.1)
        self.assertEqual(controller.mfli_bridge.request.call_args.args[0], 'configure_second_harmonic')
        controller.mfli_config['extref_demodulator'] = 0
        with self.assertRaises(RuntimeError):
            controller.configure_second_harmonic(.1)

    def test_dual_harmonic_layout_and_csv_plot_mapping(self):
        parent, panel, controller = self.make_panel()
        panel.zurich_harmonic.set('1+2')
        for key in ('830_a', '830_b', '830_c'):
            panel.ppms_lockin_selection[key].set(False)
        panel.ppms_harmonic_plot_frames = {key: Mock() for key in panel.ppms_harmonic_plot_frames}
        with patch.object(panel, '_redraw_ppms_transport_plot'):
            panel._refresh_ppms2_plot_layout()
        frames = panel.ppms_harmonic_plot_frames
        for ch in ('x', 'y', 'theta'):
            self.assertEqual(frames[(1, ch)].grid.call_args.kwargs['row'], 0)
            self.assertEqual(frames[(2, ch)].grid.call_args.kwargs['row'], 1)
            frames[(3, ch)].grid_remove.assert_called()
            self.assertIn('Zurich', frames[(1, ch)].configure.call_args.kwargs['text'])
        panel.ppms_plot_axis = 'field'
        row = dict(measured_field_oe=10, zurich_harmonic='1+2')
        for demod in (1, 2):
            for suffix in ('x_v', 'y_v', 'theta_deg'):
                row[f'zurich_demod{demod}_{suffix}'] = demod
        panel.ppms_field_scan_rows = [row]
        panel._draw_nonlocal_series = Mock()
        with patch.object(app.CombinedControlApp, '_redraw_ppms_transport_plot'):
            panel._redraw_ppms_transport_plot()
        drawn = [c for c in panel._draw_nonlocal_series.call_args_list if c.args[1]]
        self.assertEqual(len(drawn), 6)
        self.assertEqual([next(iter(c.args[1].values()))[0][1] for c in drawn], [1, 1, 1, 2, 2, 2])
        # Zurich points are drawn as open circles so they stay distinguishable
        # from SR830 data sharing the same row.
        self.assertTrue(all(set(c.kwargs['series_markers'].values()) == {'open_circle'} for c in drawn))

    def test_triple_configuration_and_plot_rows(self):
        _, panel, controller = self.make_panel()
        panel.zurich_harmonic.set('1+2+3')
        for key in ('830_a', '830_b', '830_c'):
            panel.ppms_lockin_selection[key].set(False)
        panel.connect_ppms2_zurich()
        # At the TC box's 1 s default (user, 2026-10-14; it was 0.1 s).
        controller.configure_second_harmonic.assert_called_once_with(1.0)
        controller.configure_third_harmonic.assert_called_once_with(1.0)
        self.assertEqual(controller.configure_mfli.call_args.kwargs['external_reference_demodulator'], 3)
        panel.ppms_harmonic_plot_frames = {key: Mock() for key in panel.ppms_harmonic_plot_frames}
        with patch.object(panel, '_redraw_ppms_transport_plot'):
            panel._refresh_ppms2_plot_layout()
        frames = panel.ppms_harmonic_plot_frames
        for row, order in enumerate((1, 2, 3)):
            for ch in ('x', 'y', 'theta'):
                self.assertEqual(frames[(order, ch)].grid.call_args.kwargs['row'], row)
        point = dict(measured_field_oe=10, zurich_harmonic='1+2+3')
        for demod in (1, 2, 3):
            for suffix in ('x_v', 'y_v', 'theta_deg'):
                point[f'zurich_demod{demod}_{suffix}'] = demod
        panel.ppms_field_scan_rows = [point]
        panel._draw_nonlocal_series = Mock()
        with patch.object(app.CombinedControlApp, '_redraw_ppms_transport_plot'):
            panel._redraw_ppms_transport_plot()
        drawn = [c for c in panel._draw_nonlocal_series.call_args_list if c.args[1]]
        values = [next(iter(c.args[1].values()))[0][1] for c in drawn]
        self.assertEqual(values, [1, 1, 1, 2, 2, 2, 3, 3, 3])
        panel.zurich_harmonic.set('1+2')
        with patch.object(panel, '_redraw_ppms_transport_plot'):
            panel._refresh_ppms2_plot_layout()
        for ch in ('x', 'y', 'theta'):
            frames[(3, ch)].grid_remove.assert_called()

    def test_same_order_meters_share_one_row(self):
        _, panel, _ = self.make_panel()
        panel.zurich_harmonic.set('1')
        panel.ppms_transport_harmonic_a_var.set(1)
        panel.ppms_transport_harmonic_b_var.set(2)
        panel.ppms_lockin_selection['830_c'].set(False)
        panel.ppms_harmonic_plot_frames = {key: Mock() for key in panel.ppms_harmonic_plot_frames}
        with patch.object(panel, '_redraw_ppms_transport_plot'):
            panel._refresh_ppms2_plot_layout()
        frames = panel.ppms_harmonic_plot_frames
        title = frames[(1, 'x')].configure.call_args.kwargs['text']
        self.assertIn('SR830-1', title)
        self.assertIn('Zurich', title)
        self.assertNotIn('SR830-2', title)
        self.assertEqual(frames[(2, 'x')].grid.call_args.kwargs['row'], 1)
        for ch in ('x', 'y', 'theta'):
            frames[(3, ch)].grid_remove.assert_called()
        panel.ppms_plot_axis = 'field'
        panel.ppms_plot_addresses = {'830_a': 8, '830_b': 9}
        panel.ppms_field_scan_rows = [dict(measured_field_oe=10, zurich_harmonic='1', zurich_x_v=5, zurich_y_v=5, zurich_theta_deg=5,
            sr830_8_harmonic=1, sr830_8_x_v=1, sr830_8_y_v=1, sr830_8_theta_deg=1,
            sr830_9_harmonic=2, sr830_9_x_v=2, sr830_9_y_v=2, sr830_9_theta_deg=2)]
        panel._draw_nonlocal_series = Mock()
        with patch.object(app.CombinedControlApp, '_redraw_ppms_transport_plot'):
            panel._redraw_ppms_transport_plot()
        drawn = [c for c in panel._draw_nonlocal_series.call_args_list if c.args[1]]
        first_row = drawn[0]
        self.assertEqual(sorted(v[0][1] for v in first_row.args[1].values()), [1, 5])
        self.assertEqual(sorted(first_row.kwargs['series_colors'].values()),
                         sorted([app.PPMS_METER_COLORS['830_a'], app.PPMS_METER_COLORS['zurich']]))

    def test_third_harmonic_does_not_modify_reference(self):
        controller = app.NonLocalACController.__new__(app.NonLocalACController)
        controller.lock = threading.RLock()
        controller.mfli_config = dict(extref_demodulator=1, harmonic=1)
        controller.mfli_bridge = None
        controller.mfli_device = 'dev4414'
        nodes = {}
        controller.mfli_daq = Mock()
        controller.mfli_daq.setInt.side_effect = lambda key, value: nodes.update({key: value})
        controller.mfli_daq.getInt.side_effect = lambda key: nodes[key]
        controller.configure_third_harmonic(.1)
        self.assertEqual(nodes['/dev4414/demods/3/harmonic'], 3)
        self.assertTrue(all('/demods/3/' in key for key in nodes))
        controller.mfli_daq.getInt.return_value = 0
        controller.mfli_daq.getInt.side_effect = None
        with self.assertRaises(RuntimeError):
            controller.configure_third_harmonic(.1)
        controller.mfli_bridge = Mock()
        controller.configure_third_harmonic(.1)
        self.assertEqual(controller.mfli_bridge.request.call_args.args[0], 'configure_third_harmonic')

    def test_triple_demod_order_with_reference_on_four(self):
        controller = app.NonLocalACController.__new__(app.NonLocalACController)
        controller.lock = threading.RLock()
        controller.mfli_config = dict(extref_demodulator=3, harmonic=1)
        controller.mfli_bridge = None
        controller.mfli_device = 'dev4414'
        nodes = {}
        controller.mfli_daq = Mock()
        controller.mfli_daq.setInt.side_effect = lambda k, v: nodes.update({k: v})
        controller.mfli_daq.getInt.side_effect = lambda k: nodes[k]
        controller.configure_second_harmonic(.1)
        controller.configure_third_harmonic(.1)
        self.assertEqual(nodes['/dev4414/demods/1/harmonic'], 2)
        self.assertEqual(nodes['/dev4414/demods/2/harmonic'], 3)
        self.assertFalse(any('/demods/3/' in k for k in nodes))

    def test_plot_layout_follows_all_selection_combinations(self):
        from itertools import product
        channels = ('x', 'y', 'theta')
        meters = ('830_a', '830_b', '830_c', 'zurich')
        for flags in product((False, True), repeat=4):
            panel = app.PPMS2Panel.__new__(app.PPMS2Panel)
            panel.ppms_plot_frame = Mock()
            panel.ppms_instrument_controllers = {key: Mock() for key in ('830_a', '830_b', '830_c')}
            panel.ppms_transport_harmonic_a_var = Var(1)
            panel.ppms_transport_harmonic_b_var = Var(2)
            panel.ppms_transport_harmonic_c_var = Var(3)
            panel.zurich_harmonic = Var('1+2+3')
            panel.ppms_transport_plot_frames = {(key, ch): Mock() for key in ('830_a', '830_b', '830_c') for ch in channels}
            panel.zurich_plot_frames = {ch: Mock() for ch in channels}
            panel.ppms_harmonic_plot_frames = {(order, ch): Mock() for order in (1, 2, 3) for ch in channels}
            panel.ppms_lockin_selection = {key: Var(value) for key, value in zip(meters, flags)}
            panel._redraw_ppms_transport_plot = Mock()
            panel._refresh_ppms2_plot_layout()
            # Per-instrument frames are always hidden; one row per measured order.
            for frame in list(panel.ppms_transport_plot_frames.values()) + list(panel.zurich_plot_frames.values()):
                frame.grid_remove.assert_called()
            selected = dict(zip(meters, flags))
            row = 0
            for order, slot in ((1, '830_a'), (2, '830_b'), (3, '830_c')):
                visible = selected[slot] or selected['zurich']
                for col, ch in enumerate(channels):
                    frame = panel.ppms_harmonic_plot_frames[(order, ch)]
                    if visible:
                        frame.grid.assert_called_once_with(row=row, column=col, sticky='nsew')
                        title = frame.configure.call_args.kwargs['text']
                        self.assertEqual(app.PPMS_SR830_LABELS[slot] in title, selected[slot])
                        self.assertEqual('Zurich' in title, selected['zurich'])
                    else:
                        frame.grid_remove.assert_called_once()
                        frame.grid.assert_not_called()
                row += int(visible)

    def make_panel(self):
        parent = app.CombinedControlApp.__new__(app.CombinedControlApp)
        parent.root = Mock()
        parent.closing = False
        parent.ui_queue = Mock()
        parent.post_ui = lambda callback: callback()
        parent.thread_log = Mock()
        controller = Mock(lock=threading.RLock(), mfli_connected=False)
        def connect(device):
            controller.mfli_connected = True
        def disconnect():
            controller.mfli_connected = False
        def build(panel):
            panel.ppms_instruments_frame = Mock()
            panel.ppms_settings_panel = Mock()
            panel.ppms_plot_frame = Mock()
        controller.connect_mfli.side_effect = connect
        controller.disconnect_mfli.side_effect = disconnect
        controller.read_stfmr_mfli.side_effect = lambda frequency, **kwargs: controller.read_mfli(frequency)
        with patch.object(app.tk, 'StringVar', Var), patch.object(app.tk, 'IntVar', Var), \
             patch.object(app.tk, 'BooleanVar', Var), patch.object(app, 'ThreadPoolExecutor', ImmediateExecutor), \
             patch.object(app, 'NonLocalACController', return_value=controller), \
             patch.object(app.CombinedControlApp, 'build_ppms_ui', build), \
             patch.object(app.PPMS2Panel, '_redraw_ppms_transport_plot'), \
             patch.object(app.tk, 'Canvas'), patch.object(app.ttk, 'Frame'), patch.object(app.ttk, 'Checkbutton'), \
             patch.object(app.ttk, 'LabelFrame'), patch.object(app.ttk, 'Label'), \
             patch.object(app.ttk, 'Entry'), patch.object(app.ttk, 'Combobox'), patch.object(app.ttk, 'Button'):
            parent._initialize_ppms_state()
            panel = app.PPMS2Panel(parent, Mock())
        panel.post_ui = lambda callback: callback()
        return parent, panel, controller

    def test_state_is_independent_and_default_device(self):
        parent, panel, controller = self.make_panel()
        self.assertEqual(panel.ppms_host_var.get(), app.PPMS_DEFAULT_HOST)
        self.assertEqual(parent.ppms_host_var.get(), app.PPMS_DEFAULT_HOST)
        self.assertEqual(panel.zurich_resource.get(), 'DEV4414 (Super)')
        self.assertEqual(panel.ppms_resource_vars['830_b'].get(), 'GPIB0::9::INSTR')
        self.assertEqual(panel.zurich_reference.get(), 'Trigger In 1')
        self.assertIsNot(panel.ppms_environment_controller, parent.ppms_environment_controller)
        for key in parent.ppms_instrument_controllers:
            self.assertIsNot(panel.ppms_instrument_controllers[key], parent.ppms_instrument_controllers[key])
        panel.ppms_field_scan_stop_event.set()
        panel.ppms_field_scan_rows.append({'test': 1})
        self.assertFalse(parent.ppms_field_scan_stop_event.is_set())
        self.assertEqual(parent.ppms_field_scan_rows, [])
        parent.ppms_some_future_state = 'must not leak'
        with self.assertRaises(AttributeError):
            _ = panel.ppms_some_future_state

    def test_connect_configure_and_live_values(self):
        _, panel, controller = self.make_panel()
        panel.zurich_harmonic.set('2')
        panel.connect_ppms2_zurich()
        controller.connect_mfli.assert_called_once_with('dev4414')
        # The Super Zurich's own Data Server, not DEV4435's USB-link server.
        self.assertEqual(controller.mfli_server_candidates, (('192.168.68.250', 8004),))
        # At the TC box's 1 s default (user, 2026-10-14; it was 0.1 s).
        controller.configure_mfli.assert_called_once_with('AC', 2, 'R',
            reference_source='Trigger In 1', sample_rate_hz=app.NONLOCAL_SAMPLE_HZ,
            input_impedance='10 MΩ', input_scaling=1.0, timeconstant_s=1.0, external_reference_demodulator=3)
        controller.read_mfli.return_value = dict(X=1e-6, Y=-2e-6, R=2.236e-6,
            phase_deg=-63.435, reference_frequency_hz=317.3, reference_valid=True, status_text='OK')
        panel._poll_ppms2_zurich()
        self.assertIn('317.3000 Hz', panel.zurich_values.get())
        self.assertIn('-63.4350', panel.zurich_values.get())
        self.assertIn('LOCK', panel.zurich_status.get())
        controller.read_mfli.side_effect = RuntimeError('offline')
        panel._poll_ppms2_zurich()
        self.assertIn('stale values hidden', panel.zurich_values.get())
        panel.disconnect_ppms2_zurich()
        self.assertFalse(controller.mfli_connected)

    def test_a_background_read_never_locks_the_zurich_controls(self):
        """The 1 s read runs on its own flag: a control refresh during a read
        used to grey Connect, Disconnect, the harmonic list and Measure, and
        nothing re-enabled them when the read ended."""
        parent, panel, controller = self.make_panel()
        panel.connect_ppms2_zurich()
        self.assertTrue(controller.mfli_connected)
        pending = Future()
        panel.zurich_executor = Mock(submit=Mock(return_value=pending))
        panel._poll_ppms2_zurich()
        self.assertTrue(panel.zurich_reading)
        self.assertFalse(panel.zurich_busy)
        for name in ('zurich_select', 'zurich_connect_button', 'zurich_disconnect_button',
                     'zurich_reference_combo', 'zurich_harmonic_combo'):
            setattr(panel, name, Mock())
        panel._set_zurich_busy(panel.zurich_busy)  # What a control refresh does.
        panel.zurich_disconnect_button.configure.assert_called_with(state='normal')
        panel.zurich_select.configure.assert_called_with(state='normal')
        panel.zurich_harmonic_combo.configure.assert_called_with(state='readonly')
        # A change clicked during the read queues behind it instead of being dropped.
        panel.connect_ppms2_zurich(apply_only=True)
        self.assertEqual(panel.zurich_executor.submit.call_count, 2)
        pending.set_result(dict(X=1e-6, Y=0.0, R=1e-6, phase_deg=0.0, reference_frequency_hz=317.3,
                                reference_valid=False, status_text='6221 off'))
        self.assertFalse(panel.zurich_reading)

    def test_zurich_snapshot_retaken_only_after_a_range_change(self):
        """1+2+3 reads three demodulators; do not read them twice per point."""
        for actions, expected_reads in (('', 1), ('RANGE .01->.1 V', 2)):
            with self.subTest(actions=actions or 'no range change'):
                _, panel, controller = self.make_panel()
                panel.zurich_scan_enabled = True
                panel.zurich_scan_harmonic = '1+2+3'
                demods = {f'zurich_demod{i}_{k}': (i * 1e-6 if k != 'enabled' else 1)
                          for i in range(1, 5) for k in ('x_v', 'y_v', 'r_v', 'theta_deg', 'enabled')}
                controller.read_mfli_four_demods = Mock(return_value=demods)
                panel._read_ppms2_zurich_ranged = Mock(return_value=dict(
                    X=1e-6, Y=1e-6, R=2e-6, phase_deg=45., reference_frequency_hz=317.3,
                    reference_valid=True, status_text='OK', input_range_v=.1,
                    ppms_autorange_actions=actions))
                panel._display_zurich_sample = Mock()
                with patch.object(app.PPMSPanel.__bases__[0], '_average_ppms_transport_sample',
                                  return_value={}):
                    sample = panel._average_ppms_transport_sample(317.3, ('830_a',), 1.)
                self.assertEqual(controller.read_mfli_four_demods.call_count, expected_reads)
                self.assertEqual(sample['zurich_input_range_v'], .1)
                self.assertEqual(sample['zurich_autorange'], actions)

    def test_zurich_autorange_status_shows_the_input_range(self):
        _, panel, _ = self.make_panel()
        panel.zurich_range_var = Var('Autorange ON')
        panel._set_connection_indicator = Mock()
        panel._display_zurich_sample(dict(X=1e-6, Y=1e-6, R=2e-6, phase_deg=45.,
            reference_frequency_hz=317.3, reference_valid=True, status_text='OK', input_range_v=.001))
        self.assertEqual(panel.zurich_range_var.get(), 'Autorange 1 mV')

    def test_configuration_failure_disconnects_and_does_not_touch_ppms(self):
        parent, panel, controller = self.make_panel()
        controller.configure_mfli.side_effect = RuntimeError('bad readback')
        panel.connect_ppms2_zurich()
        controller.disconnect_mfli.assert_called_once()
        self.assertFalse(panel.zurich_busy)
        self.assertFalse(parent.ppms_field_scan_stop_event.is_set())
        self.assertFalse(panel.ppms_field_scan_stop_event.is_set())

    def test_four_demods_saved_independently_and_no_setting_writes(self):
        controller = app.NonLocalACController(lambda message: None)
        controller.mfli_device = 'dev4414'
        controller.mfli_daq = Mock()
        def sample(node):
            index = int(node.split('/')[3]) + 1
            return {'x': [index * 1e-6], 'y': [-index * 2e-6], 'frequency': [index * 317.3], 'timestamp': [100 + index]}
        controller.mfli_daq.getSample.side_effect = sample
        controller.mfli_daq.getInt.side_effect = lambda node: 1
        result = controller.read_mfli_four_demods()
        self.assertEqual(len(result), 40)
        for i in range(1, 5):
            self.assertAlmostEqual(result[f'zurich_demod{i}_x_v'], i * 1e-6)
            self.assertAlmostEqual(result[f'zurich_demod{i}_frequency_hz'], i * 317.3)
        controller.mfli_daq.setInt.assert_not_called()
        controller.mfli_daq.getInt.side_effect = lambda node: 0 if '/demods/2/enable' in node else 1
        controller.mfli_daq.getSample.reset_mock()
        disabled = controller.read_mfli_four_demods()
        self.assertEqual(disabled.keys(), result.keys())
        self.assertEqual(disabled['zurich_demod3_status'], 'disabled')
        self.assertEqual(disabled['zurich_demod3_x_v'], '')
        self.assertEqual(disabled['zurich_demod3_enabled'], 0)
        self.assertEqual(controller.mfli_daq.getSample.call_count, 3)
        self.assertEqual(disabled['zurich_demod4_x_v'], result['zurich_demod4_x_v'])
        controller.mfli_daq.getSample.side_effect = RuntimeError('communication failed')
        with self.assertRaisesRegex(RuntimeError, 'communication failed'):
            controller.read_mfli_four_demods()

    def test_legacy_bridge_skips_disabled_demod4_and_keeps_fixed_csv_columns(self):
        block = app._MFLI_BRIDGE_SOURCE.split('        elif command == "read_demods":', 1)[1].split('        elif command == "autorange":', 1)[0]
        daq, emit = Mock(), Mock()
        daq.getInt.side_effect = lambda node: 0 if '/demods/3/enable' in node else 1
        daq.getSample.return_value = dict(x=1e-6, y=2e-6, frequency=317.3, timestamp=42)
        exec(textwrap.dedent(block), dict(daq=daq, device='dev4414', emit=emit,
            scalar=lambda sample, key, required=True: sample.get(key)))
        channels = emit.call_args.args[0]['demods']
        self.assertEqual(len(channels), 4)
        self.assertEqual(channels[3], {'enabled': 0})
        self.assertEqual(daq.getSample.call_count, 3)
        controller = app.NonLocalACController(lambda _: None)
        controller.mfli_bridge = Mock()
        controller.mfli_bridge.request.return_value = {'demods': channels}
        row = controller.read_mfli_four_demods()
        self.assertEqual(row['zurich_demod4_status'], 'disabled')
        self.assertEqual(row['zurich_demod4_frequency_hz'], '')
        self.assertEqual(row['zurich_demod1_x_v'], 1e-6)

    def test_zurich_third_row_uses_saved_points_not_idle_titles(self):
        _, panel, controller = self.make_panel()
        panel.zurich_harmonic.set('2')
        for key in ('830_a', '830_b', '830_c'):
            panel.ppms_lockin_selection[key].set(False)
        panel.ppms_plot_axis = 'angle'
        panel.ppms_field_scan_rows = [dict(measured_angle_deg=30, temperature_index=1,
            zurich_x_v=1e-6, zurich_y_v=-2e-6, zurich_theta_deg=-63, zurich_r_v=2.2e-6)]
        panel._draw_nonlocal_series = Mock()
        with patch.object(app.CombinedControlApp, '_redraw_ppms_transport_plot'):
            panel._redraw_ppms_transport_plot()
        drawn = [c for c in panel._draw_nonlocal_series.call_args_list if c.args[1]]
        self.assertEqual(len(drawn), 3)
        self.assertEqual(list(drawn[0].args[1].values()), [[(30., 1e-6)]])
        panel.ppms_harmonic_plot_frames = {key: Mock() for key in panel.ppms_harmonic_plot_frames}
        panel._display_zurich_sample(dict(X=1, Y=2, R=3, phase_deg=4,
            reference_frequency_hz=317.3, reference_valid=True, status_text='OK'))
        for frame in panel.ppms_harmonic_plot_frames.values(): frame.configure.assert_not_called()

    def test_ppms_autorange_discards_transition_and_cancels_wait(self):
        _, panel, controller = self.make_panel()
        controller.mfli_config = {'timeconstant_s': .1}
        panel.ppms_zurich_autorange_pending = True
        controller.read_stfmr_mfli.side_effect = [
            {'auto_range': 'RANGE .01->.1 V', 'X': 999},
            {'auto_range': '', 'X': 1e-6, 'input_range_v': .1}]
        clock = iter(range(100))
        with patch.object(app.time, 'monotonic', lambda: next(clock)):
            result = panel._read_ppms2_zurich_ranged(317.3, scanning=True)
        self.assertEqual(result['X'], 1e-6)
        self.assertEqual([c.kwargs['initial'] for c in controller.read_stfmr_mfli.call_args_list], [True, False])
        self.assertIn('RANGE', result['ppms_autorange_actions'])
        self.assertEqual([c.kwargs['attempts'] for c in controller.read_stfmr_mfli.call_args_list], [0, 1])
        def range_then_cancel(*args, **kwargs):
            panel.ppms_field_scan_stop_event.set()
            return {'auto_range': 'RANGE'}
        controller.read_stfmr_mfli.side_effect = range_then_cancel
        with self.assertRaises(app.PPMSScanCancelled):
            panel._read_ppms2_zurich_ranged(317.3, scanning=True)

    def test_reused_autorange_overload_low_signal_and_retry_limit(self):
        controller = app.NonLocalACController(lambda _: None)
        controller.read_mfli = Mock(return_value={'input_overload': True,
            'input_extrema_valid': True, 'input_utilization': .95})
        controller._run_mfli_autorange = Mock(return_value='RANGE')
        controller.mfli_config = {'input_range_v': .1}
        result = controller.read_stfmr_mfli(317.3, auto_range=True)
        self.assertEqual(result['auto_range'], 'RANGE')
        with self.assertRaisesRegex(RuntimeError, '3 retries'):
            controller.read_stfmr_mfli(317.3, auto_range=True, attempts=3)
        controller._run_mfli_autorange.reset_mock()
        controller.mfli_range_cooldown = 0
        controller.mfli_low_range_count = 0
        controller.read_mfli.return_value = {'input_overload': False,
            'input_extrema_valid': True, 'input_utilization': .1}
        for _ in range(2):
            controller.read_stfmr_mfli(317.3, auto_range=True)
            controller._run_mfli_autorange.assert_not_called()
        controller.read_stfmr_mfli(317.3, auto_range=True)
        controller._run_mfli_autorange.assert_called_once_with('low')

    def test_optional_lockin_selection_all_combinations(self):
        parent, panel, controller = self.make_panel()
        for obj, keys in ((parent, ('830_a', '830_b')), (panel, ('830_a', '830_b', 'zurich'))):
            for mask in range(1 << len(keys)):
                obj.ppms_lockin_selection = {key: Var(bool(mask & (1 << i))) for i, key in enumerate(keys)}
                for key in ('830_a', '830_b'):
                    obj.ppms_instrument_controllers[key] = Mock(connected=obj.ppms_lockin_selection[key].get())
                controller.mfli_connected = bool(mask & 4)
                if mask == 0:
                    with self.assertRaisesRegex(ValueError, 'at least one'):
                        obj._ppms_selected_lockins()
                    continue
                active, use_zurich = obj._ppms_selected_lockins()
                self.assertEqual(active, tuple(k for k in ('830_a', '830_b') if obj.ppms_lockin_selection[k].get()))
                self.assertEqual(use_zurich, bool(mask & 4))
                result = app.validate_ppms_transport_scan_settings(1, 317.3, 50, 1, 2, active, allow_no_lockins=use_zurich)
                self.assertEqual(result['active_lockins'], active)
        panel.ppms_lockin_selection = {'830_a': Var(False), '830_b': Var(False), 'zurich': Var(True)}
        controller.mfli_connected = False
        with self.assertRaisesRegex(ValueError, 'Zurich'):
            panel._ppms_selected_lockins()


if __name__ == '__main__': unittest.main()
