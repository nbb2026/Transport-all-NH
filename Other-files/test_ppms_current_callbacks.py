"""Offline tests of the actual PPMS completion and shutdown callbacks."""
from concurrent.futures import Future
import threading
import unittest
from unittest.mock import Mock, patch
from test_ppms_motor_debug import app
from test_ppms_motor_debug import Array
import types


class CurrentCallbacks(unittest.TestCase):
    def test_field_tolerance_boundaries_still_require_stable_status(self):
        ready = app.CombinedControlApp._ppms_field_is_ready
        for target, tolerance in ((0., 2.), (1000., 2.), (60000., 2.), (-60000., 2.)):
            for sign in (-1, 1):
                with self.subTest(target=target, sign=sign):
                    self.assertTrue(ready({'field_oe': target + sign*tolerance,
                                           'field_status': 'StableDriven'}, target))
                    self.assertFalse(ready({'field_oe': target + sign*(tolerance + .1),
                                            'field_status': 'StableDriven'}, target))
                    self.assertFalse(ready({'field_oe': target,
                                            'field_status': 'Charging'}, target))

    def test_both_sr830s_support_all_harmonic_pairs_without_reinitialization(self):
        for ha in (1, 2, 3):
            for hb in (1, 2, 3):
                obj = self.make_app()
                obj.ppms_transport_status_var = Mock()
                obj.ppms_instrument_controllers = {
                    key: Mock(connected=True, lock=threading.RLock(), resource_name=f'GPIB0::{address}::INSTR')
                    for key, address in (('6221', 30), ('830_a', 8), ('830_b', 10))}
                settings = app.validate_ppms_transport_scan_settings(1., 317.3, 50., ha, hb, ('830_a', '830_b'))
                source_writes, lockin_writes = [], []
                harmonics = {'830_a': 1, '830_b': 2}
                obj._ppms_write_6221_checked = source_writes.append
                def write(key, command):
                    lockin_writes.append((key, command))
                    if command in ('ILIN 0', 'SYNC 0'):
                        return
                    self.assertTrue(command.startswith('HARM '))
                    harmonics[key] = int(command.split()[1])
                obj._ppms_write_sr830_checked = write
                def query(key, command, count):
                    if command == 'HARM?':
                        return (harmonics[key],)
                    # OFLT?: every scan start reads each SR830's time
                    # constant for the LOCKIN_SETTLE_TAUS x TC wait (1 x TC,
                    # user, 2026-10-14); this
                    # stand-in, without an 'SR830 TC' box, used to skip it.
                    return ({'SOUR:WAVE:AMPL?': settings['peak_a'], 'CURR:COMP?': 50.,
                             'SOUR:WAVE:FREQ?': 317.3, 'FMOD?': 0, 'RSLP?': 1,
                             'OUTP?': 1, 'FREQ?': 317.3, 'LIAS?': 0, 'OUTP:LTE?': 0,
                             'OFLT?': 10}[command],)
                obj._ppms_query_numbers = query
                obj._configure_ppms_transport_scan(settings)
                self.assertEqual(harmonics, {'830_a': ha, '830_b': hb})
                self.assertEqual(len(lockin_writes), 4 + int(ha != 1) + int(hb != 2))
                lockin_writes.clear()
                obj._configure_ppms_transport_scan(settings)
                self.assertEqual(lockin_writes, [
                    ('830_a', 'ILIN 0'), ('830_a', 'SYNC 0'),
                    ('830_b', 'ILIN 0'), ('830_b', 'SYNC 0')])
                obj._ppms_query_numbers = lambda k, c, n: (0,) if c == 'HARM?' else query(k, c, n)
                source_writes.clear()
                with self.assertRaisesRegex(RuntimeError, 'configuration readback mismatch'):
                    obj._configure_ppms_transport_scan(settings)
                self.assertNotIn('SOUR:WAVE:INIT', source_writes)

    def test_current_sweep_writes_each_actual_peak_and_rejects_stale_readback(self):
        obj = self.make_app()
        obj.ppms_transport_status_var = Mock()
        obj.ppms_instrument_controllers = {
            key: Mock(connected=True, lock=threading.RLock(), resource_name='GPIB0::8::INSTR')
            for key in ('6221', '830_a')}
        settings = app.validate_ppms_transport_scan_settings(1., 317.3, 50., 1, 2, ('830_a',))
        writes = []
        obj._ppms_write_6221_checked = lambda command: writes.append(command)
        def query(key, command, count):
            if command == 'SOUR:WAVE:AMPL?':
                return (float(next(c.split()[-1] for c in reversed(writes) if c.startswith('SOUR:WAVE:AMPL '))),)
            return ({'CURR:COMP?': 50., 'SOUR:WAVE:FREQ?': 317.3,
                     'HARM?': 1, 'FMOD?': 0, 'RSLP?': 1, 'OUTP?': 1,
                     'FREQ?': 317.3, 'LIAS?': 0, '*ESR?': 0, 'OUTP:LTE?': 0,
                     'OFLT?': 10}[command],)   # OFLT?: read at every scan start (user, 2026-10-14)
        obj._ppms_query_numbers = query
        for current in (1., 2., 5.):
            # Deliberately retain the old 1 mA peak in the input dictionary.
            obj._configure_ppms_transport_scan(dict(settings, current_rms_ma=current))
            self.assertAlmostEqual(query('6221', 'SOUR:WAVE:AMPL?', 1)[0], current*1e-3*2**.5)
        obj._ppms_query_numbers = lambda k, c, n: (settings['peak_a'],) if c == 'SOUR:WAVE:AMPL?' else query(k, c, n)
        writes.clear()
        with self.assertRaisesRegex(RuntimeError, 'amplitude readback mismatch'):
            obj._configure_ppms_transport_scan(dict(settings, current_rms_ma=5.))
        self.assertNotIn('SOUR:WAVE:INIT', writes)

    def test_scanned_target_controls_disabled_by_mode(self):
        obj = self.make_app()
        obj.ppms_scan_target_widgets = {key: [Mock(), Mock()] for key in ('Temperature', 'Field', 'Angle', 'Current')}
        obj.ppms_simple_mode = Mock()
        for mode in ('Temperature', 'Field', 'Angle', 'Current', 'Field+Temperature', 'Angle+Temperature'):
            obj.ppms_simple_mode.get.return_value = mode
            obj._update_ppms_scan_target_widgets(True)
            scanned = {'Temperature', 'Field'} if mode == 'Field+Temperature' else {'Angle', 'Temperature'} if mode == 'Angle+Temperature' else {mode}
            for parameter, widgets in obj.ppms_scan_target_widgets.items():
                for widget in widgets:
                    widget.configure.assert_called_with(state='disabled' if parameter in scanned else 'normal')
        obj._update_ppms_scan_target_widgets(False)
        for widgets in obj.ppms_scan_target_widgets.values():
            for widget in widgets:
                widget.configure.assert_called_with(state='disabled')

    def test_manual_large_angle_requires_yes_and_small_angle_has_no_dialog(self):
        obj = self.make_app()
        probe = Mock()
        probe.read.return_value = {'angle_deg': 0., 'status': 'AtTarget'}
        with patch.object(app.messagebox, 'askyesno', return_value=False) as ask:
            with self.assertRaises(app.PPMSScanCancelled):
                obj._ppms_confirmed_manual_move(probe, 60., 14)
            probe.move_and_verify.assert_not_called()
            ask.assert_called_once()
        with patch.object(app.messagebox, 'askyesno', return_value=True):
            obj._ppms_confirmed_manual_move(probe, 60., 14)
            self.assertTrue(probe.move_and_verify.call_args.kwargs['allow_large'])
        with patch.object(app.messagebox, 'askyesno') as ask:
            obj._ppms_confirmed_manual_move(probe, 30., 14)
            ask.assert_not_called()

    def test_no_range_change_without_overload_for_either_lockin(self):
        obj = self.make_app()
        obj.ppms_instrument_controllers = {
            key: Mock(lock=threading.RLock()) for key in ('830_a', '830_b')}
        obj._ppms_query_numbers = Mock(return_value=(13,))
        obj._set_connection_indicator = Mock()
        scale = app.NONLOCAL_SR830_FULL_SCALE_V[13]
        for key in ('830_a', '830_b'):
            # Without an overload the range never widens; at/above 10 % it never narrows either.
            for magnitude in (.10 * scale, .5 * scale, .98 * scale, 1.):
                for _ in range(6):
                    self.assertEqual(obj._ppms_autorange_sr830(key, magnitude, 0), 0.)
            obj.ppms_instrument_controllers[key].instrument.write.assert_not_called()

    def test_low_signal_narrows_one_code_after_three_settled_readings(self):
        obj = self.make_app()
        controller = Mock(lock=threading.RLock())
        controller.instrument.query.return_value = '12'
        obj.ppms_instrument_controllers = {'830_a': controller}
        obj._ppms_query_numbers = Mock(side_effect=[(13,), (13,), (13,), (10,), (12,), (12,), (12,), (12,)])
        obj._set_connection_indicator = Mock()
        small = .09 * app.NONLOCAL_SR830_FULL_SCALE_V[13]
        self.assertEqual(obj._ppms_autorange_sr830('830_a', small, 0), 0.)
        self.assertEqual(obj._ppms_autorange_sr830('830_a', small, 0), 0.)
        controller.instrument.write.assert_not_called()
        self.assertEqual(obj._ppms_autorange_sr830('830_a', small, 0), 5.)   # third reading: one code down
        controller.instrument.write.assert_called_once_with('SENS 12')
        # Cool-down: three more small readings do not step again.
        for _ in range(3):
            self.assertEqual(obj._ppms_autorange_sr830('830_a', small, 0), 0.)
        controller.instrument.write.assert_called_once()

    def test_down_range_skipped_when_unlocked_disabled_or_in_scan_window(self):
        obj = self.make_app()
        controller = Mock(lock=threading.RLock())
        obj.ppms_instrument_controllers = {'830_a': controller}
        obj._ppms_query_numbers = Mock(return_value=(13,))
        obj._set_connection_indicator = Mock()
        small = 1e-9
        for _ in range(5):
            obj._ppms_autorange_sr830('830_a', small, 8)                    # reference unlocked
            obj._ppms_autorange_sr830('830_a', small, 0, enabled=False)     # excitation off
            obj._ppms_autorange_sr830('830_a', small, 0, allow_down=False)  # inside a scan point
        controller.instrument.write.assert_not_called()

    def test_reading_above_ten_percent_resets_the_count(self):
        obj = self.make_app()
        controller = Mock(lock=threading.RLock())
        obj.ppms_instrument_controllers = {'830_a': controller}
        obj._ppms_query_numbers = Mock(return_value=(13,))
        obj._set_connection_indicator = Mock()
        scale = app.NONLOCAL_SR830_FULL_SCALE_V[13]
        for magnitude in (.05 * scale, .05 * scale, .3 * scale, .05 * scale, .05 * scale):
            self.assertEqual(obj._ppms_autorange_sr830('830_a', magnitude, 0), 0.)
        controller.instrument.write.assert_not_called()

    def test_overload_steps_up_one_code_per_settle(self):
        for key in ('830_a', '830_b'):
            with self.subTest(key=key):
                obj = self.make_app()
                controller = Mock(lock=threading.RLock())
                controller.instrument.query.side_effect = ['14', '15']
                obj.ppms_instrument_controllers = {key: controller}
                obj._ppms_query_numbers = Mock(side_effect=[
                    (13,), (10,),   # SENS?, OFLT?: overloaded -> one code up
                    (14,), (10,),   # still overloaded -> one more
                    (15,)])         # clear: no change
                obj._set_connection_indicator = Mock()
                self.assertEqual(obj._ppms_autorange_sr830(key, 0., 4), 5.)
                self.assertEqual(obj._ppms_autorange_sr830(key, 0., 4), 5.)
                self.assertEqual(obj._ppms_autorange_sr830(key, 1e-5, 0), 0.)
                self.assertEqual([c.args[0] for c in controller.instrument.write.call_args_list], ['SENS 14', 'SENS 15'])

    def test_connection_initializes_sr830_and_checks_settings(self):
        obj = self.make_app()
        controller = Mock(lock=threading.RLock())
        controller.connect.return_value = 'SR830'
        obj.ppms_instrument_controllers = {'830_b': controller}
        obj._ppms_write_sr830_checked = Mock()
        values = {'FMOD': 0, 'RSLP': 1, 'HARM': 2, 'ISRC': 1, 'IGND': 0,
                  'ICPL': 0, 'ILIN': 0, 'SYNC': 0, 'SENS': 9, 'OFLT': 10, 'OFSL': 1, 'RMOD': 1, 'PHAS': 0}
        obj._ppms_query_numbers = Mock(side_effect=lambda key, query, n: (values[query[:-1]],))
        self.assertEqual(obj._connect_and_initialize_ppms_device('830_b', 'GPIB0::10::INSTR', 2), 'SR830')
        obj._ppms_write_sr830_checked.assert_any_call('830_b', 'HARM 2')
        obj._ppms_write_sr830_checked.assert_any_call('830_b', 'ISRC 1')
        obj._ppms_write_sr830_checked.assert_any_call('830_b', 'ILIN 0')
        obj._ppms_write_sr830_checked.assert_any_call('830_b', 'SYNC 0')
        self.assertEqual(obj._ppms_query_numbers.call_count, 13)
        controller.disconnect.assert_not_called()

    def test_initialization_failure_disconnects_instead_of_claiming_connected(self):
        obj = self.make_app()
        controller = Mock()
        obj.ppms_instrument_controllers = {'830_a': controller}
        obj._initialize_ppms_sr830 = Mock(side_effect=RuntimeError('readback mismatch'))
        with self.assertRaisesRegex(RuntimeError, 'readback mismatch'):
            obj._connect_and_initialize_ppms_device('830_a', 'GPIB0::8::INSTR', 1)
        controller.disconnect.assert_called_once()

    def test_indicator_overload_red_then_recovers(self):
        obj = self.make_app()
        canvas = Mock()
        obj.instrument_connection_indicators = {"ppms_ref_830_a": (canvas, 1)}
        for connected, fault, color in ((True, True, "#e53935"),
                                        (True, False, "#20b15a"),
                                        (False, False, "#9aa0a6")):
            obj._set_connection_indicator("ppms_ref_830_a", connected, fault=fault)
            self.assertEqual(canvas.itemconfigure.call_args.kwargs["fill"], color)

    def test_scan_sampling_autoranges_in_both_directions(self):
        """A harmonic pass after a larger order must be able to narrow the range."""
        obj = self.make_app()
        obj.ppms_instrument_controllers = {'830_a': types.SimpleNamespace(resource_name='GPIB0::8::INSTR')}
        numbers = {'OUTP?': (1,), 'STAT:MEAS:COND?': (0,), 'SOUR:WAVE:AMPL?': (1e-3,),
                   'SNAP? 1,2,3,4,9': (1e-9, 0., 1e-9, 0., 317.3)}
        obj._ppms_query_numbers = Mock(side_effect=lambda key, command, n: numbers[command])
        obj._ppms_read_sr830_status = Mock(return_value=0)
        obj._ppms_autorange_sr830 = Mock(return_value=0.)
        obj.ppms_sr830_range_states = {'830_a': types.SimpleNamespace(sensitivity_index=9)}
        obj._read_ppms_transport_sample(317.3, ('830_a',))
        self.assertTrue(obj._ppms_autorange_sr830.call_args.kwargs['allow_down'])  # default: both directions
        self.assertTrue(obj._ppms_autorange_sr830.call_args.kwargs['enabled'])
        # A point's later readings only range up (review, 2026-10-18).
        obj._read_ppms_transport_sample(317.3, ('830_a',), False)
        self.assertFalse(obj._ppms_autorange_sr830.call_args.kwargs['allow_down'])

    def test_autorange_status_reports_the_selected_full_scale(self):
        obj = self.make_app()
        controller = Mock(lock=threading.RLock())
        controller.instrument.query.return_value = '14'
        obj.ppms_instrument_controllers = {'830_a': controller}
        obj._ppms_query_numbers = Mock(side_effect=[(13,), (10,)])
        obj._set_connection_indicator = Mock()
        obj.ppms_sr830_range_vars = {'830_a': Mock()}
        obj._ppms_autorange_sr830('830_a', 0., 4)          # overload: 50 uV -> 100 uV
        self.assertEqual(obj.ppms_sr830_range_vars['830_a'].set.call_args.args[0], 'Autorange 100 uV')
        self.assertEqual(
            [app.CombinedControlApp._format_sr830_full_scale(i) for i in (0, 13, 20, 26, None)],
            ['2 nV', '50 uV', '10 mV', '1 V', '--'])

    def test_autorange_uses_verified_sens_and_time_constant(self):
        obj = self.make_app()
        instrument = Mock()
        instrument.query.return_value = "14"
        obj.ppms_instrument_controllers = {"830_a": types.SimpleNamespace(instrument=instrument, lock=threading.RLock(), label="SR830-1")}
        obj._ppms_query_numbers = Mock(side_effect=[(13,), (10,)])
        obj._set_connection_indicator = Mock()
        obj.ppms_sr830_status_vars = {"830_a": Mock()}
        self.assertEqual(obj._ppms_autorange_sr830("830_a", 1., 1), 5.)
        instrument.write.assert_called_once_with("SENS 14")
        instrument.query.assert_called_once_with("SENS?")
        self.assertIn("OVERLOAD", obj.ppms_sr830_status_vars["830_a"].set.call_args.args[0])
        obj._set_connection_indicator.assert_called_with("ppms_ref_830_a", False, fault=True)

    def test_overload_at_maximum_range_raises(self):
        obj = self.make_app()
        instrument = Mock()
        obj.ppms_instrument_controllers = {"830_a": types.SimpleNamespace(instrument=instrument, lock=threading.RLock(), label="SR830-1")}
        obj._ppms_query_numbers = Mock(side_effect=[(26,)])
        obj._set_connection_indicator = Mock()
        obj.ppms_sr830_status_vars = {"830_a": Mock()}
        with self.assertRaisesRegex(app.PPMSLockinRangeError, "maximum sensitivity"):
            obj._ppms_autorange_sr830("830_a", 1., 4)
        instrument.write.assert_not_called()

    def test_overload_ranges_up_even_without_excitation(self):
        obj = self.make_app()
        instrument = Mock()
        instrument.query.return_value = "14"
        obj.ppms_instrument_controllers = {"830_a": types.SimpleNamespace(instrument=instrument, lock=threading.RLock(), label="SR830-1")}
        obj._ppms_query_numbers = Mock(side_effect=[(13,), (10,)])
        obj._set_connection_indicator = Mock()
        obj.ppms_sr830_status_vars = {"830_a": Mock()}
        self.assertEqual(obj._ppms_autorange_sr830("830_a", 20e-6, 1, enabled=False), 5.)
        instrument.write.assert_called_once_with("SENS 14")

    def test_idle_poll_ranges_up_on_overload_without_set_current(self):
        obj = app.CombinedControlApp.__new__(app.CombinedControlApp)
        obj.ppms_environment_controller = types.SimpleNamespace(backend="fake", read_environment=lambda: {})
        # Idle ranging runs under the SR830's lock (user, 2026-10-14).
        obj.ppms_instrument_controllers = {'830_a': types.SimpleNamespace(connected=True, lock=threading.RLock())}
        obj._read_ppms_current_display = lambda: "OFF / 0 mA"
        obj._read_ppms_cryogen_display = lambda: {}
        obj._ppms_query_numbers = lambda k, c, n: (317.3 if c == 'FREQ?' else 1,)
        calls = []
        obj._ppms_autorange_sr830 = lambda *args: (calls.append(args) or 5.)
        with patch.object(app.time, 'monotonic', return_value=100.):
            result = obj._read_ppms_live()
        self.assertEqual(len(calls), 1)
        self.assertIn('OVERLOAD', result['lockin_reference']['830_a'])
        self.assertIn('autorange', result['lockin_reference']['830_a'])

    def test_overloaded_samples_are_excluded_from_the_mean(self):
        obj = self.make_app()
        obj.ppms_field_scan_stop_event = Mock(is_set=Mock(return_value=False), wait=Mock(return_value=False))
        obj.closing = False
        good = dict(sr830_8_x_v=1., sr830_8_y_v=0., sr830_8_frequency_hz=317.3, sr830_8_reference_valid=True, sr830_8_overload=False)
        bad = dict(good, sr830_8_x_v=100., sr830_8_overload=True)
        obj._read_ppms_transport_sample = Mock(side_effect=[bad, good, bad, good])
        with patch.object(app.time, 'monotonic', side_effect=[0., .2, .4, .6, 1.2, 1.2]):
            result = obj._average_ppms_transport_sample(317.3, ("830_a",), 1.)
        self.assertEqual(result["sr830_8_x_v"], 1.)
        self.assertEqual(result["sample_count"], 2)
        self.assertEqual(result["overloaded_sample_count"], 2)

    def test_range_switch_discards_old_samples(self):
        obj = self.make_app()
        obj.ppms_field_scan_stop_event = Mock(is_set=Mock(return_value=False), wait=Mock(return_value=False))
        obj._read_ppms_transport_sample = Mock(side_effect=[
            {"range_settle_s": 5., "sr830_8_x_v": 999., "sr830_8_y_v": 999.},
            {"range_settle_s": 0., "sr830_8_x_v": 2., "sr830_8_y_v": 4.}])
        with patch.object(app.time, "monotonic", side_effect=[0., 0., 5., 7., 7.]):
            result = obj._average_ppms_transport_sample(317.3, ("830_a",), 1.)
        self.assertEqual(result["sample_count"], 1)
        self.assertEqual(result["sr830_8_x_v"], 2.)
        obj.ppms_field_scan_stop_event.wait.assert_called_once_with(5.)

    def test_window_averages_two_lockins_and_records_count(self):
        obj = self.make_app()
        obj.ppms_field_scan_stop_event = Mock(is_set=Mock(return_value=False), wait=Mock(return_value=False))
        samples = []
        for value in (1., 3.):
            sample = {"6221_output_on": True}
            for address in (8, 10):
                sample.update({f"sr830_{address}_x_v": value, f"sr830_{address}_y_v": value*2,
                               f"sr830_{address}_frequency_hz": 317.3, f"sr830_{address}_reference_valid": True})
            samples.append(sample)
        obj._read_ppms_transport_sample = Mock(side_effect=samples)
        with patch.object(app.time, "monotonic", side_effect=(0., .4, 1.2, 1.2)):
            result = obj._average_ppms_transport_sample(317.3, ("830_a", "830_b"), 1.)
        self.assertEqual(result["sample_count"], 2)
        for address in (8, 10):
            self.assertEqual(result[f"sr830_{address}_x_v"], 2.)
            self.assertEqual(result[f"sr830_{address}_y_v"], 4.)
        self.assertEqual(result["acquisition_elapsed_s"], 1.2)

    def test_window_cancel_or_bad_reference_does_not_return_partial_point(self):
        obj = self.make_app()
        obj.ppms_field_scan_stop_event.set()
        obj._read_ppms_transport_sample = Mock()
        with self.assertRaises(app.PPMSScanCancelled):
            obj._average_ppms_transport_sample(317.3, ("830_a",), 1.)
        obj._read_ppms_transport_sample.assert_not_called()
        obj.ppms_field_scan_stop_event.clear()
        obj._read_ppms_transport_sample.return_value = {"sr830_8_reference_valid": False}
        with self.assertRaisesRegex(RuntimeError, "reference unlocked"):
            obj._average_ppms_transport_sample(317.3, ("830_a",), 1.)

    def test_pressure_and_helium_queries_are_read_only(self):
        obj = self.make_app()
        obj.ppms_environment_controller = types.SimpleNamespace(backend=app.PPMS_QDINSTRUMENT_BACKEND, active=object())
        reader = Mock()
        reader.controller.lock = threading.RLock()
        reader._read_item.return_value = 9.94
        sent = []
        def query(instrument, args):
            sent.append(args[0])
            args[1] = "84.4,0"
            return 0
        reader._method.return_value.Invoke.side_effect = query
        system = types.SimpleNamespace(Array=Array, Object=object, Int32=int, Double=float)
        with patch.object(app, "PPMSMotorDebug", return_value=reader), patch.dict('sys.modules', System=system):
            result = obj._read_ppms_cryogen_display()
        reader._read_item.assert_called_once_with(19)
        self.assertEqual(sent, ["LEVEL?"])
        self.assertEqual(result["pressure_display"], "9.94 Torr")
        self.assertEqual(result["helium_display"], "84.40% (stale)")

    def test_scan_stop_zeroes_only_field_at_fixed_rate(self):
        for scan_rate in (1., 50., 149.):
            obj = self.make_app()
            obj.ppms_field_scan_running = True
            obj.ppms_active_scan_mode = "Field+Temperature"
            obj.ppms_field_scan_status_var = Mock()
            obj.ppms_field_scan_stop_button = Mock()
            obj.ppms_environment_controller = Mock()
            obj.stop_ppms_field_scan()
            self.assertTrue(obj.ppms_field_scan_stop_event.is_set())
            obj._ppms_zero_field_after_interrupted_scan(scan_rate, 1)
            obj.ppms_environment_controller.set_field.assert_called_once_with(0., 100., 1, 1)
            obj.ppms_environment_controller.set_temperature.assert_not_called()
            self.assertEqual(len(obj.ppms_environment_controller.mock_calls), 1)

    def make_app(self):
        obj = app.CombinedControlApp.__new__(app.CombinedControlApp)
        obj.root, obj.closing = None, False
        obj.ppms_environment_busy = False
        obj.ppms_connection_var = Mock()
        obj._require_ppms_control_ready = Mock()
        obj._set_ppms_environment_ui = Mock()
        obj._apply_ppms_environment_readback = Mock()
        obj.ppms_field_scan_stop_event = threading.Event()
        obj.post_ui = lambda callback: callback()
        obj.ppms_executor = Mock()
        self.future = Future()
        obj.ppms_executor.submit.return_value = self.future
        return obj

    def test_none_success_does_not_apply_environment(self):
        obj = self.make_app()
        obj._run_ppms_command("setting", Mock(), success_text="ON confirmed")
        self.future.set_result(None)
        obj._apply_ppms_environment_readback.assert_not_called()
        obj.ppms_connection_var.set.assert_called_with("ON confirmed")
        self.assertFalse(obj.ppms_environment_busy)

    def test_environment_success_still_applies_readback(self):
        obj = self.make_app()
        obj._run_ppms_command("setting", Mock())
        result = {"temperature_k": 300}
        self.future.set_result(result)
        obj._apply_ppms_environment_readback.assert_called_once_with(result)
        self.assertFalse(obj.ppms_environment_busy)

    def test_uncertain_environment_status_is_not_overwritten(self):
        obj = self.make_app()
        obj._run_ppms_command("setting", Mock())
        result = {"command_verification": "Temperature reply uncertain, confirming by readback"}
        with patch.object(app.messagebox, "showerror") as popup:
            self.future.set_result(result)
            popup.assert_not_called()
        obj.ppms_connection_var.set.assert_called_with(result["command_verification"])

    def test_late_on_callback_does_not_overwrite_off(self):
        obj = self.make_app()
        obj._run_ppms_command("setting", Mock(), success_text="ON confirmed")
        obj.ppms_field_scan_stop_event.set()
        obj.ppms_connection_var.set("OFF confirmed")
        self.future.set_result(None)
        obj.ppms_connection_var.set.assert_called_with("OFF confirmed")

    def test_worker_and_readback_failures_restore_ui(self):
        for readback_failure in (False, True):
            obj = self.make_app()
            if readback_failure:
                obj._apply_ppms_environment_readback.side_effect = ValueError("bad payload")
            with patch.object(app.messagebox, "showerror") as popup:
                obj._run_ppms_command("setting", Mock())
                if readback_failure:
                    self.future.set_result({})
                else:
                    self.future.set_exception(RuntimeError("device timeout"))
                popup.assert_called_once()
            self.assertFalse(obj.ppms_environment_busy)
            self.assertIn("Command failed", obj.ppms_connection_var.set.call_args.args[0])
            self.assertEqual(obj._set_ppms_environment_ui.call_count, 2)

    def test_submit_failure_restores_ui(self):
        obj = self.make_app()
        obj.ppms_executor.submit.side_effect = RuntimeError("closed executor")
        with patch.object(app.messagebox, "showerror"):
            obj._run_ppms_command("setting", Mock())
        self.assertFalse(obj.ppms_environment_busy)

    def shutdown(self, state):
        obj = self.make_app()
        obj.ppms_instrument_controllers = {}
        for key in ("6221", "2400"):
            controller = Mock()
            controller.lock = threading.RLock()
            controller.instrument = Mock() if key == "6221" else None
            obj.ppms_instrument_controllers[key] = controller
        obj._ppms_query_numbers = Mock(return_value=(state,))
        obj.ppms_sources_off()
        self.assertTrue(obj.ppms_field_scan_stop_event.is_set())
        worker = obj.ppms_executor.submit.call_args.args[0]
        with patch.object(app.messagebox, "showerror") as popup:
            try:
                result = worker()
            except Exception as error:
                self.future.set_exception(error)
            else:
                self.future.set_result(result)
            self.assertEqual(popup.call_count, int(state != 0))
        obj.ppms_instrument_controllers["6221"].safe_output_off.assert_called_once()
        return obj

    def test_shutdown_confirms_only_connected_source(self):
        obj = self.shutdown(0)
        obj.ppms_connection_var.set.assert_called_with("6221 output OFF confirmed by readback")

    def test_shutdown_on_readback_is_not_reported_as_success(self):
        obj = self.shutdown(1)
        self.assertIn("not confirmed", obj.ppms_connection_var.set.call_args.args[0])

    def test_cancel_prevents_further_start_command(self):
        obj = self.make_app()
        source = Mock()
        source.lock = threading.RLock()
        obj.ppms_instrument_controllers = {"6221": source}
        obj.ppms_field_scan_stop_event.set()
        with self.assertRaises(app.PPMSScanCancelled):
            obj._ppms_write_6221_checked("SOUR:WAVE:INIT")
        source.instrument.write.assert_not_called()

    def test_zero_current_skips_sr830_range_writes(self):
        obj = self.make_app()
        controller = Mock(lock=threading.RLock())
        controller.instrument.query.return_value = '11'
        obj.ppms_instrument_controllers = {'830_a': controller}
        obj._ppms_query_numbers = Mock(return_value=(10,))
        obj._set_connection_indicator = Mock()
        # Without an overload nothing is written while the excitation is off ...
        self.assertEqual(obj._ppms_autorange_sr830('830_a', 0., 0, enabled=False), 0.)
        controller.instrument.write.assert_not_called()
        # ... but an overload still ranges up one code.
        self.assertEqual(obj._ppms_autorange_sr830('830_a', 0., 7, enabled=False), 5.)
        controller.instrument.write.assert_called_once_with('SENS 11')

    def test_live_current_readback_states(self):
        obj = self.make_app()
        source = Mock()
        source.lock = threading.RLock()
        obj.ppms_instrument_controllers = {"6221": source}
        source.instrument = None
        self.assertEqual(obj._read_ppms_current_display(), "Not connected")
        source.instrument = Mock()
        obj._ppms_query_numbers = Mock(return_value=(0,))
        self.assertEqual(obj._read_ppms_current_display(), "OFF / 0 mA")
        self.assertTrue(obj.ppms_6221_zero_output)
        obj._ppms_query_numbers.assert_called_once_with("6221", "OUTP?", 1)
        obj._ppms_query_numbers.side_effect = [(1,), (0.001414213562373095,), (317.3,), (8,)]
        text = obj._read_ppms_current_display()
        self.assertIn("ON / sine setpoint 1 mA RMS / 317.3 Hz", text)
        self.assertFalse(obj.ppms_6221_zero_output)
        obj._ppms_query_numbers.side_effect = [(1,), (0.,), (317.3,), (0,)]
        obj._read_ppms_current_display()
        self.assertTrue(obj.ppms_6221_zero_output)
        self.assertIn("Compliance", text)
        self.assertTrue(text.startswith("⚠ Compliance hit"))
        obj._ppms_query_numbers.side_effect = [(1,), (0.001414213562373095,), (317.3,), (0,)]
        self.assertNotIn("Compliance hit", obj._read_ppms_current_display())
        obj.ppms_last_current_trip = True
        obj._ppms_query_numbers.side_effect = [(0,)]
        self.assertEqual(obj._read_ppms_current_display(), "OFF / 0 mA; compliance hit in the last scan")
        obj._ppms_query_numbers.side_effect = TimeoutError("timeout")
        self.assertTrue(obj._read_ppms_current_display().startswith("Unknown"))


if __name__ == "__main__":
    unittest.main()
