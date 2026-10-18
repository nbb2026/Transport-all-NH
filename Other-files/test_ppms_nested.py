"""Offline range and nested-worker acceptance; no hardware or Tk window."""
import threading
import types
import unittest
from unittest.mock import patch
from test_ppms_motor_debug import app


class NestedTests(unittest.TestCase):
    def test_boundary_rate_rejection_retries_without_stopping(self):
        obj, motor, settings, events = self.harness()
        settings.update(scan_mode='Temperature', temperature_rate=10.,
                        nested=dict(angle=(26.,), temperature=(20., 30.)))
        original = obj.ppms_environment_controller.set_temperature
        calls = []
        def set_temperature(target, rate, approach):
            calls.append((target, rate))
            if len(calls) == 1:
                raise ValueError('PPMS1 temperature ramp rate must not exceed 1 K/min in the 5–10 K range')
            return original(target, rate, approach)
        obj.ppms_environment_controller.set_temperature = set_temperature
        self.run_worker(obj, motor, settings)
        self.assertEqual(calls, [(20., 10.), (20., 1.), (20., 10.), (30., 10.)])
        self.assertEqual(len([e for e in events if e[0] == 'plane']), 2)
        self.assertFalse(any(e[0] in ('zero', 'off') for e in events))

    def test_scan_temperature_segments(self):
        for current, target, expected in (
            (20, 5, [(9.1, 8.), (5., 1.)]),
            (5, 20, [(20., 1.)]),
            (9, 6, [(6., 1.)]),
            (10, 5, [(9.1, 8.), (5., 1.)]),
            (9, 9, [(9., 1.)]),
            (9.01, 10, [(10., 8.)]),
            (20, 10, [(10., 8.)]),
            (5, 10, [(10., 1.)]),
        ):
            self.assertEqual(app.ppms_scan_temperature_segments(current, target, 8), expected)
        # A typed rate under 1 K/min is kept below 9 K too: min(rate, 1),
        # never raised to 1 (review, 2026-10-18).
        for current, target, expected in (
            (20, 5, [(9.1, .5), (5., .5)]),
            (5, 20, [(20., .5)]),
            (20, 10, [(10., .5)]),
        ):
            self.assertEqual(app.ppms_scan_temperature_segments(current, target, .5), expected)
        with self.assertRaises(ValueError):
            app.ppms_scan_temperature_segments(20, 5, 11)

    def test_nested_scan_sends_boundary_before_low_temperature(self):
        obj, motor, settings, events = self.harness()
        settings.update(scan_mode='Temperature', temperature_rate=8.,
                        nested=dict(angle=(26.,), temperature=(5., 20.)))
        calls = []
        original = obj.ppms_environment_controller.set_temperature
        def set_temperature(target, rate, approach):
            calls.append((target, rate))
            return original(target, rate, approach)
        obj.ppms_environment_controller.set_temperature = set_temperature
        self.run_worker(obj, motor, settings)
        # Above 9 K the warm-up returns to the typed 8 K/min, not to a fixed
        # 10 K/min (review, 2026-10-18).
        self.assertEqual(calls, [(9.1, 8.), (5., 1.), (20., 1.), (20., 8.)])

    def test_field_axis_units_threshold_and_raw_values_unchanged(self):
        obj = app.CombinedControlApp.__new__(app.CombinedControlApp)
        obj.ppms_transport_canvas = object()
        obj.ppms_transport_canvases = {(k, c): object() for k in ("830_a", "830_b")
                                      for c in ("x", "y", "theta", "r")}
        obj.ppms_plot_addresses = {"830_a": 8, "830_b": 10}
        obj.ppms_plot_axis = "field"
        for field, unit, expected in ((999, "Oe", 999), (1000, "T", .1),
                                      (-1000, "T", -.1), (-30000, "T", -3), (10, "Oe", 10)):
            row = {"measured_field_oe": field}
            for address in (8, 10):
                row.update({f"sr830_{address}_{suffix}": 1 for suffix in ("x_v", "y_v", "theta_deg", "r_v")})
            obj.ppms_field_scan_rows = [row]
            calls = []
            obj._draw_nonlocal_series = lambda *args, **kwargs: calls.append(args)
            obj._redraw_ppms_transport_plot()
            self.assertEqual(len(calls), 8)
            for call in calls:
                self.assertEqual(call[2], f"Measured magnetic field ({unit})")
                self.assertAlmostEqual(next(iter(call[1].values()))[0][0], expected)
            self.assertEqual(row["measured_field_oe"], field)

    def test_eight_plot_channels_are_separate(self):
        obj = app.CombinedControlApp.__new__(app.CombinedControlApp)
        obj.ppms_transport_canvas = object()
        obj.ppms_transport_canvases = {(k, c): object() for k in ("830_a", "830_b") for c in ("x", "y", "theta", "r")}
        obj.ppms_plot_addresses = {"830_a": 9, "830_b": 8}
        obj.ppms_plot_axis = "current"
        obj.ppms_field_scan_rows = [dict(sr830_8_r_v=0, sr830_9_r_v=0, sr830_8_harmonic=3, sr830_9_harmonic=2,
            sr830_8_theta_deg=45, sr830_9_theta_deg=-30,
            sr830_8_x_v=1, sr830_8_y_v=2, sr830_9_x_v=3, sr830_9_y_v=4,
            **{"6221_current_rms_ma": .01})]
        calls = []
        styles = []
        obj._draw_nonlocal_series = lambda *args, **kwargs: (calls.append(args), styles.append(kwargs))
        obj._redraw_ppms_transport_plot()
        self.assertEqual(len(calls), 8)
        self.assertEqual([style["color_override"] for style in styles], [app.PPMS_HARMONIC_COLORS["2"]]*4 + [app.PPMS_HARMONIC_COLORS["3"]]*4)
        self.assertEqual([s["auto_voltage_unit"] for s in styles], [True, True, False, True]*2)
        self.assertTrue(all(style["show_legend"] is False for style in styles))
        for call, expected in zip(calls, (3, 4, -30, 0, 1, 2, 45, 0)):
            self.assertEqual(len(call[1]), 1)
            self.assertEqual(next(iter(call[1].values())), [(.01, expected)])

    def test_plot_axes_and_grouping(self):
        obj = app.CombinedControlApp.__new__(app.CombinedControlApp)
        obj.ppms_transport_canvas = object()
        obj.ppms_field_scan_rows = [dict(sr830_8_r_v=1, sr830_8_x_v=2,
            sr830_8_y_v=3, measured_field_oe=10, temperature_k=300+i,
            measured_angle_deg=20+i, angle_index=1, temperature_index=i+1)
            for i in range(2)]
        captured = []
        obj._draw_nonlocal_series = lambda *args, **kwargs: captured.append(args)
        for axis, expected, count in (("field", 10, 4), ("temperature", 300, 2), ("angle", 20, 2)):
            obj.ppms_plot_axis = axis
            obj._redraw_ppms_transport_plot()
            series = captured[-1][1]
            self.assertEqual(len(series), count)
            self.assertEqual(next(iter(series.values()))[0][0], expected)

    def test_idle_reads_reference_not_xy(self):
        obj = app.CombinedControlApp.__new__(app.CombinedControlApp)
        obj.ppms_environment_controller = types.SimpleNamespace(backend="fake", read_environment=lambda: {})
        obj.ppms_instrument_controllers = {k: types.SimpleNamespace(connected=True) for k in ("830_a", "830_b")}
        obj._read_ppms_current_display = lambda: "OFF / 0 mA"
        obj._ppms_autorange_sr830 = lambda *args: 0.
        commands = []
        def query(key, command, count):
            commands.append(command)
            return (13.7,) if command == "FREQ?" else (8 if key == "830_b" else 0,)
        obj._ppms_query_numbers = query
        result = obj._read_ppms_live()
        self.assertEqual(result["current_display"], "OFF / 0 mA")
        self.assertIn("LOCK", result["lockin_reference"]["830_a"])
        self.assertIn("WARNING", result["lockin_reference"]["830_b"])
        self.assertTrue(all(c in ("FREQ?", "LIAS?", "OUTP? 3") for c in commands))

    def test_angle_temperature_plot_does_not_join_temperature_groups(self):
        obj = app.CombinedControlApp.__new__(app.CombinedControlApp)
        obj.ppms_transport_canvas = object()
        obj.ppms_plot_axis = 'angle'
        obj.ppms_active_scan_mode = 'Angle+Temperature'
        obj.ppms_plot_addresses = {'830_a': 8}
        obj.ppms_field_scan_rows = [dict(sr830_8_r_v=2, sr830_8_x_v=1, sr830_8_y_v=2,
            measured_angle_deg=angle, temperature_index=ti, target_temperature_k=temp)
            for ti, temp in ((1, 300), (2, 290)) for angle in (-5, 0)]
        calls = []
        obj._draw_nonlocal_series = lambda *args, **kwargs: calls.append(args)
        obj._redraw_ppms_transport_plot()
        series = calls[-1][1]
        self.assertEqual(len(series), 4)  # Two temperatures, X/Y each.
        self.assertTrue(all(len(points) == 2 for points in series.values()))
        self.assertTrue(any('300 K' in name for name in series))
        self.assertTrue(any('290 K' in name for name in series))

    def test_idle_autorange_requires_successful_current_setting(self):
        obj = app.CombinedControlApp.__new__(app.CombinedControlApp)
        obj.ppms_environment_controller = types.SimpleNamespace(backend="fake", read_environment=lambda: {})
        # Idle ranging runs under the SR830's lock (user, 2026-10-14).
        obj.ppms_instrument_controllers = {k: types.SimpleNamespace(connected=True, lock=threading.RLock())
                                           for k in ("830_a", "830_b")}
        obj._read_ppms_current_display = lambda: "ON / 1 mA"
        obj._read_ppms_cryogen_display = lambda: {}
        calls = []
        obj._ppms_autorange_sr830 = lambda *args: (calls.append(args) or 5.)
        # Without a successful PPMS current setting, a clean lock-in is never re-ranged ...
        obj._ppms_query_numbers = lambda k, c, n: (317.3 if c == 'FREQ?' else 0,)
        with patch.object(app.time, 'monotonic', return_value=100.):
            obj._read_ppms_live()
        self.assertEqual(len(calls), 0)
        # ... but an overload report always ranges up, whatever set the current.
        obj._ppms_query_numbers = lambda k, c, n: (317.3 if c == 'FREQ?' else 6,)
        with patch.object(app.time, 'monotonic', return_value=100.):
            result = obj._read_ppms_live()
        self.assertEqual(len(calls), 2)
        self.assertIn('OVERLOAD', result['lockin_reference']['830_a'])
        self.assertIn('autorange', result['lockin_reference']['830_b'])
        # The idle poll never ranges during a scan: the scan worker owns the lock-ins then.
        obj.ppms_field_scan_running = True
        with patch.object(app.time, 'monotonic', return_value=200.):
            obj._read_ppms_live()
        self.assertEqual(len(calls), 2)

    def test_range_direction_endpoint(self):
        self.assertEqual(app.ppms_scan_axis(0, 2.5, 1), (0., 1., 2., 2.5))
        self.assertEqual(app.ppms_scan_axis(2, 0, 1), (2., 1., 0.))
        self.assertEqual(app.ppms_scan_axis(2, 2, 1), (2.,))
        for args in ((0, 1, 0), (0, float('nan'), 1), (0, 1e9, 1)):
            with self.assertRaises(ValueError):
                app.ppms_scan_axis(*args)

    def harness(self):
        obj = app.CombinedControlApp.__new__(app.CombinedControlApp)
        obj.closing = False
        obj.ppms_field_scan_stop_event = threading.Event()
        obj.ppms_scan_context = {}
        obj.ppms_angle_live_var = types.SimpleNamespace(set=lambda value: None)
        state = dict(angle=26., field=0., temp=300.)
        events = []
        remote = types.SimpleNamespace(active=state)
        remote.set_field = lambda target, *args: (state.update(field=target), events.append(('field', target)))
        remote.set_temperature = lambda target, *args: state.update(temp=target)
        obj.ppms_environment_controller = remote
        obj._read_ppms_live = lambda: dict(field_oe=state['field'], temperature_k=state['temp'], field_status='StableDriven', temperature_status='Stable')
        obj._apply_ppms_environment_readback = lambda data: None
        obj.post_ui = lambda callback: callback()
        obj.thread_log = lambda text: None
        # read_back: the PPMS scans read OUTP? back after the off (2026-10-18).
        obj._stop_ppms_transport_source = lambda **_options: events.append(('off',))
        obj._configure_ppms_transport_scan = lambda config: events.append(('configure', config['current_rms_ma']))
        obj._update_ppms_transport_current = lambda config: events.append(('amplitude', config['current_rms_ma']))
        obj._ppms_zero_field_after_interrupted_scan = lambda *args: events.append(('zero',))
        obj._ppms_field_scan_worker = lambda *args: events.append(('plane', state['angle'], state['temp']))
        class Motor:
            def __init__(self, _): pass
            def read(self): return {'angle_deg': state['angle'], 'status': 'AtTarget'}
            def move_and_verify(self, target, slowdown, cancelled, index_target=None):
                if cancelled(): raise RuntimeError('cancelled')
                self_read = state['angle']
                assert abs(target-self_read) <= 1
                state['angle'] = target
                events.append(('move', target))
        settings = dict(nested=dict(angle=(26., 27.), temperature=(300., 301.)), slowdown=14,
                        temperature_rate=1., temperature_approach=1, current_rms_ma=.001)
        return obj, Motor, settings, events

    def run_worker(self, obj, motor, settings):
        clock = iter(range(10000))
        with patch.object(app, 'PPMSMotorDebug', motor), patch.object(app.time, 'monotonic', lambda: next(clock)):
            obj._ppms_nested_scan_worker([(1,1,1,0.)], 100., 0., 1, settings)

    def test_current_scan_applies_each_current(self):
        obj, motor, settings, events = self.harness()
        settings.update(nested=dict(angle=(26.,), temperature=(300.,)), currents=(.001, .002, .003), scan_mode="Current")
        applied = []
        obj._ppms_field_scan_worker = lambda seq, rate, stable, approach, config: applied.append(config["current_rms_ma"])
        self.run_worker(obj, motor, settings)
        self.assertEqual(applied, [.001, .002, .003])
        self.assertEqual([e for e in events if e[0] == 'configure'], [('configure', .001)])
        self.assertEqual([e for e in events if e[0] == 'amplitude'], [('amplitude', .002), ('amplitude', .003)])
        self.assertNotIn(('off',), events)

    def test_temperature_outer_current_order_and_amplitudes(self):
        obj, motor, settings, events = self.harness()
        settings.update(scan_mode='Current+Temperature', nested=dict(angle=(26.,), temperature=(300., 295.)),
                        currents=(1., 2., 3.))
        applied = []
        def sample(seq, rate, stable, approach, config):
            context = obj.ppms_scan_context
            current = config['current_rms_ma']
            self.assertAlmostEqual(config['peak_a'], current * 1e-3 * 2**.5)
            applied.append((context['temperature_index'], context['target_temperature_k'], current))
        obj._ppms_field_scan_worker = sample
        self.run_worker(obj, motor, settings)
        self.assertEqual(applied, [(ti, temp, current) for ti, temp in ((1, 300.), (2, 295.))
                                   for current in (1., 2., 3.)])
        self.assertNotIn(('off',), events)
        self.assertNotIn(('zero',), events)
        self.assertEqual(len([e for e in events if e[0] == 'configure']), 1)
        self.assertEqual(len([e for e in events if e[0] == 'amplitude']), 5)

    def test_angle_scan_configures_once_and_keeps_source_between_points(self):
        obj, motor, settings, events = self.harness()
        settings['scan_mode'] = 'Angle'
        obj._configure_ppms_transport_scan = lambda config: events.append(('configure',))
        def point(seq, rate, stable, approach, config):
            self.assertTrue(config['keep_source_between_angles'])
            self.assertNotIn(('off',), events)
            events.append(('point',))
        obj._ppms_field_scan_worker = point
        self.run_worker(obj, motor, settings)
        self.assertEqual(events.count(('configure',)), 1)
        self.assertEqual(events.count(('point',)), 4)
        self.assertEqual(events.count(('off',)), 0)  # Keep final bias after completion.

    def test_all_scan_modes_accept_near_temperature(self):
        for mode in ('Temperature', 'Field', 'Angle', 'Current', 'Field+Temperature', 'Angle+Temperature'):
            with self.subTest(mode=mode):
                obj, motor, settings, events = self.harness()
                settings['scan_mode'] = mode
                obj._configure_ppms_transport_scan = lambda config: None
                read = obj._read_ppms_live
                obj._read_ppms_live = lambda: dict(read(), temperature_status='Near')
                self.run_worker(obj, motor, settings)
                self.assertEqual(len([e for e in events if e[0] == 'plane']), 4)

    def test_non_field_modes_skip_field_readiness(self):
        for mode in ('Temperature', 'Angle', 'Current', 'Angle+Temperature', 'Current+Temperature'):
            with self.subTest(mode=mode):
                obj, motor, settings, events = self.harness()
                settings['scan_mode'] = mode
                obj._configure_ppms_transport_scan = lambda config: None
                def forbidden(*args):
                    raise AssertionError('non-field scan must not check field readiness')
                obj._ppms_field_is_ready = forbidden
                obj.ppms_environment_controller.set_field = forbidden
                read = obj._read_ppms_live
                obj._read_ppms_live = lambda: dict(read(), field_oe=999., field_status='Charging')
                self.run_worker(obj, motor, settings)
                self.assertEqual(len([e for e in events if e[0] == 'plane']), 4)

    def test_inner_non_field_worker_never_sets_magnet(self):
        for cls in (app.CombinedControlApp, app.PPMS2Panel):
            for mode in ('Temperature', 'Angle', 'Current', 'Angle+Temperature', 'Current+Temperature'):
                obj, motor, settings, events = self.harness()
                settings['scan_mode'] = mode
                obj.ppms_last_readback = None
                obj._configure_ppms_transport_scan = lambda config: None
                def forbidden(*args):
                    raise AssertionError('must preserve Persistent magnet state')
                obj.ppms_environment_controller.set_field = forbidden
                def read_and_stop():
                    obj.ppms_field_scan_stop_event.set()
                    return dict(field_oe=60000., field_status='Persistent')
                obj._read_ppms_live = read_and_stop
                obj.ppms_scan_preserve_on_stop = True
                cls._ppms_field_scan_worker(obj, [(1, 1, 1, 0.)], 100., 0., 1, settings)
                self.assertFalse(any(e[0] in ('field', 'zero') for e in events))

    def test_temperature_outer_angle_order_and_context(self):
        obj, motor, settings, events = self.harness()
        settings['scan_mode'] = 'Angle+Temperature'
        obj._configure_ppms_transport_scan = lambda config: events.append(('configure',))
        remote = obj.ppms_environment_controller
        set_temperature = remote.set_temperature
        remote.set_temperature = lambda target, *args: (set_temperature(target, *args), events.append(('temperature', target)))
        contexts = []
        point = obj._ppms_field_scan_worker
        def sample(*args):
            self.assertTrue(args[-1]['keep_source_between_angles'])
            contexts.append(dict(obj.ppms_scan_context))
            point(*args)
        obj._ppms_field_scan_worker = sample
        self.run_worker(obj, motor, settings)
        self.assertEqual([e for e in events if e[0] in ('temperature', 'plane')], [
            ('temperature', 300.), ('plane', 26., 300.), ('plane', 27., 300.),
            ('temperature', 301.), ('plane', 26., 301.), ('plane', 27., 301.)])
        self.assertEqual([(c['temperature_index'], c['angle_index']) for c in contexts],
                         [(1, 1), (1, 2), (2, 1), (2, 2)])
        self.assertEqual(events.count(('configure',)), 1)
        self.assertEqual(events.count(('off',)), 0)

    def test_lockin_range_failure_preserves_source_through_both_workers(self):
        obj, motor, settings, events = self.harness()
        obj.ppms_last_readback = None
        def fail(config):
            raise app.PPMSLockinRangeError('OVERLOAD')
        obj._configure_ppms_transport_scan = fail
        obj._ppms_field_scan_worker = types.MethodType(app.CombinedControlApp._ppms_field_scan_worker, obj)
        with self.assertRaises(app.PPMSLockinRangeError):
            self.run_worker(obj, motor, settings)
        self.assertNotIn(('off',), events)
        self.assertNotIn(('zero',), events)

    def test_motor_timeout_does_not_zero_field(self):
        obj, motor, settings, events = self.harness()
        def timeout(*args, **kwargs):
            raise RuntimeError('AtIndexSwitch timeout')
        with patch.object(motor, 'move_and_verify', timeout):
            with self.assertRaises(app.PPMSMotorScanError):
                self.run_worker(obj, motor, settings)
        self.assertNotIn(('zero',), events)
        self.assertIn(('off',), events)

    def test_recovery_at_final_angle_skips_remaining_intermediate_moves(self):
        obj, motor, settings, events = self.harness()
        settings['nested'] = dict(angle=(350.,), temperature=(300.,))
        calls = []
        def recover(self, target, slowdown, cancelled, index_target=None):
            calls.append((target, index_target))
            return dict(angle_deg=index_target, status='AtTarget')
        with patch.object(motor, 'move_and_verify', recover):
            self.run_worker(obj, motor, settings)
        self.assertEqual(len(calls), 1)
        self.assertEqual(calls[0][1], 350.)

    def test_inner_motor_error_does_not_zero_field_in_either_worker(self):
        obj, motor, settings, events = self.harness()
        obj.ppms_last_readback = None
        def fail(config):
            raise app.PPMSMotorScanError('angle readback failed')
        obj._configure_ppms_transport_scan = fail
        obj._ppms_field_scan_worker = types.MethodType(app.CombinedControlApp._ppms_field_scan_worker, obj)
        with self.assertRaises(app.PPMSMotorScanError):
            self.run_worker(obj, motor, settings)
        self.assertNotIn(('zero',), events)
        self.assertIn(('off',), events)

    def test_temperature_wait_does_not_close_current(self):
        obj, motor, settings, events = self.harness()
        settings.update(nested=dict(angle=(26.,), temperature=(300.,)), scan_mode="Field")
        read = obj._read_ppms_live
        def check_wait():
            self.assertNotIn(('off',), events)
            return read()
        obj._read_ppms_live = check_wait
        self.run_worker(obj, motor, settings)
        self.assertNotIn(('off',), events)

    def test_angle_outer_temperature_middle(self):
        obj, motor, settings, events = self.harness()
        self.run_worker(obj, motor, settings)
        self.assertEqual([e for e in events if e[0]=='plane'], [('plane',26.,300.),('plane',26.,301.),('plane',27.,300.),('plane',27.,301.)])
        self.assertNotIn(('off',), events)

    def test_temperature_outer_repeats_full_field_sequence(self):
        obj, motor, settings, events = self.harness()
        settings.update(nested=dict(angle=(26.,), temperature=(300., 295., 290.)),
                        currents=(.001,), scan_mode="Field+Temperature")
        sequence = ((1, 1, 3, -100.), (1, 2, 3, 0.), (1, 3, 3, 100.))
        captured = []
        def capture(seq, rate, stable, approach, config):
            context = dict(obj.ppms_scan_context)
            for point in seq:
                captured.append((context['temperature_index'], context['target_temperature_k'], point[3]))
        obj._ppms_field_scan_worker = capture
        clock = iter(range(10000))
        with patch.object(app, 'PPMSMotorDebug', motor), patch.object(app.time, 'monotonic', lambda: next(clock)):
            obj._ppms_nested_scan_worker(sequence, 100., 0., 1, settings)
        self.assertEqual(captured, [(i, t, b) for i, t in enumerate((300,295,290), 1) for b in (-100,0,100)])
        self.assertNotIn(('off',), events)

    def test_stop_prevents_next_plane_and_cleans(self):
        obj, motor, settings, events = self.harness()
        obj._ppms_field_scan_worker = lambda *args: obj.ppms_field_scan_stop_event.set()
        self.run_worker(obj, motor, settings)
        # An angle/temperature scan keeps the magnet's setpoint and mode
        # ("I off" or Stop; user, 2026-10-09), and the source goes off.
        self.assertNotIn(('zero',), events)
        self.assertEqual(events[-1], ('off',))

    def test_a_stopped_field_scan_still_zeroes_the_field(self):
        obj, motor, settings, events = self.harness()
        settings.update(nested=dict(angle=(26.,), temperature=(300.,)), scan_mode="Field")
        obj._ppms_field_scan_worker = lambda *args: obj.ppms_field_scan_stop_event.set()
        self.run_worker(obj, motor, settings)
        self.assertIn(('zero',), events)
        self.assertEqual(events[-1], ('off',))

    def test_only_the_first_angle_gets_the_settling_windows(self):
        # User, 2026-10-09: the source keeps running between angles, so only
        # the first point after it was started settles.
        obj, motor, settings, events = self.harness()
        settings.update(scan_mode="Angle")
        fresh = []
        obj._ppms_field_scan_worker = lambda *args: fresh.append(obj.__dict__.pop("ppms_source_fresh", False))
        self.run_worker(obj, motor, settings)
        self.assertEqual(fresh[0], True)
        self.assertFalse(any(fresh[1:]), fresh)

    def test_the_zurich_channels_have_a_noise_floor(self):
        settled = app.CombinedControlApp._ppms_first_point_settled
        # A weak 3rd harmonic (nV) wandering by 100 % between windows, on a
        # 10 mV input range: within 0.2 % of the range, so settled.
        previous = {"zurich_demod3_x_v": 2e-9, "zurich_demod3_y_v": 1e-9, "zurich_input_range_v": 0.01}
        current = {"zurich_demod3_x_v": 4e-9, "zurich_demod3_y_v": -1e-9, "zurich_input_range_v": 0.01}
        self.assertTrue(settled(previous, current))
        # Without a range (none read) it is still judged on 2 % of R.
        self.assertFalse(settled({k: v for k, v in previous.items() if "range" not in k},
                                 {k: v for k, v in current.items() if "range" not in k}))

    def test_non_field_failure_keeps_field_but_closes_source(self):
        obj, motor, settings, events = self.harness()
        def fail(*args): raise RuntimeError('instrument failure')
        obj._ppms_field_scan_worker = fail
        with self.assertRaisesRegex(RuntimeError, 'instrument failure'):
            self.run_worker(obj, motor, settings)
        self.assertNotIn(('zero',), events)
        self.assertEqual(events[-1], ('off',))

    def test_user_stop_preserves_output_and_field(self):
        obj, motor, settings, events = self.harness()
        obj.ppms_scan_preserve_on_stop = True
        def stop(*args):
            events.clear()  # Only inspect commands after Stop was requested.
            obj.ppms_field_scan_stop_event.set()
        obj._ppms_field_scan_worker = stop
        self.run_worker(obj, motor, settings)
        self.assertEqual(events, [])

    def test_failure_after_user_stop_still_cleans(self):
        obj, motor, settings, events = self.harness()
        obj.ppms_scan_preserve_on_stop = True
        def fail(*args):
            obj.ppms_field_scan_stop_event.set()
            raise RuntimeError('device fault')
        obj._ppms_field_scan_worker = fail
        with self.assertRaisesRegex(RuntimeError, 'device fault'):
            self.run_worker(obj, motor, settings)
        self.assertNotIn(('zero',), events)
        self.assertEqual(events[-1], ('off',))

    def test_explicit_field_failure_still_cleans_field(self):
        obj, motor, settings, events = self.harness()
        def fail(*args):
            raise app.PPMSFieldScanError('field command failed')
        obj._ppms_field_scan_worker = fail
        with self.assertRaises(app.PPMSFieldScanError):
            self.run_worker(obj, motor, settings)
        self.assertIn(('zero',), events)

    def test_sr830_configuration_error_preserves_field_in_both_workers(self):
        for mode in ('Angle', 'Current', 'Field', 'Angle+Temperature'):
            obj, motor, settings, events = self.harness()
            settings['scan_mode'] = mode
            obj.ppms_last_readback = None
            def fail(config):
                raise RuntimeError('SR830 configuration readback mismatch')
            obj._configure_ppms_transport_scan = fail
            obj._ppms_field_scan_worker = types.MethodType(app.CombinedControlApp._ppms_field_scan_worker, obj)
            with self.assertRaisesRegex(RuntimeError, 'SR830'):
                self.run_worker(obj, motor, settings)
            self.assertNotIn(('zero',), events)

    def test_voltage_units(self):
        self.assertIn('µV', app.CombinedControlApp._ppms_voltage_text(1e-6))
        self.assertIn('mV', app.CombinedControlApp._ppms_voltage_text(-.01))

    def test_first_point_repeats_windows_until_two_agree(self):
        obj = app.CombinedControlApp.__new__(app.CombinedControlApp)
        obj.post_ui = lambda callback: None
        obj.thread_log = lambda message: None
        def sample(x):
            return dict(sr830_8_x_v=x, sr830_8_y_v=0., sr830_8_sensitivity_index=17)
        obj._average_ppms_transport_sample = lambda f, l, d: sample(next(values))
        values = iter([1.6e-3, 2.4e-3, 2.62e-3, 2.63e-3, 2.63e-3])
        result, windows = obj._acquire_ppms_first_point(317.3, ("830_a",), 1.)
        self.assertEqual(windows, 4)                      # 2.62 -> 2.63 mV agrees within 2 %
        self.assertEqual(result["sr830_8_x_v"], 2.63e-3)  # the last window is kept
        # A channel sitting at zero settles on the full-scale floor, not on 2 % of ~0.
        values = iter([1e-9, 2e-9, 1e-9])
        result, windows = obj._acquire_ppms_first_point(317.3, ("830_a",), 1.)
        self.assertEqual(windows, 2)
        # Never settling keeps the last window after the cap.
        values = iter([i * 1e-3 for i in range(1, 20)])
        result, windows = obj._acquire_ppms_first_point(317.3, ("830_a",), 1.)
        self.assertEqual(windows, app.PPMS_FIRST_POINT_MAX_WINDOWS)
        self.assertEqual(result["sr830_8_x_v"], app.PPMS_FIRST_POINT_MAX_WINDOWS * 1e-3)


if __name__ == '__main__': unittest.main()
