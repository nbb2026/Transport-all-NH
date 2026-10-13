"""PPMS / RT-E review fixes (2026-10-13), one check each.

No Tk and no instrument: stand-ins record what the code asks of them."""
import threading
import types
import unittest
from unittest.mock import MagicMock, Mock, patch

import test_rte_panel as rte_tests
from test_ppms_motor_debug import app
from test_ppms_two_panels import Var


class RTECurrentScanStartsAtItsLargestCurrent(unittest.TestCase):
    """The real RT-E worker with the real 6221 configure and live updates."""

    def run_scan(self, plan):
        panel = rte_tests.RTE.make_worker(self)
        # Use the program's own configure (PPMSPanel -> CombinedControlApp)
        # and live-amplitude update instead of the helper's mocks.
        del panel._configure_ppms_transport_scan, panel._rte_update_current
        panel.zurich_scan_enabled = False
        settings = app.validate_ppms_transport_scan_settings(.1, 317.3, 50., 1, 2, ('830_a',))
        source = Mock(connected=True, label='6221', lock=MagicMock())
        panel.ppms_instrument_controllers = {
            '6221': source,
            '830_a': Mock(connected=True, label='SR830 #8', resource_name='GPIB0::8::INSTR')}
        panel._initialize_ppms_6221 = Mock()
        panel._ppms_write_sr830_checked = Mock()
        writes, state = [], {'OUTP': 0.}

        def write(command):
            writes.append(command)
            name, _, value = command.partition(' ')
            if name in ('SOUR:CURR:RANG', 'SOUR:WAVE:AMPL'):
                state[name] = float(value)
            elif name == 'SOUR:WAVE:INIT':
                state['OUTP'] = 1.
            elif command == 'OUTP OFF':
                state['OUTP'] = 0.

        def query(key, command, count):
            replies = {
                'SOUR:CURR:RANG?': state.get('SOUR:CURR:RANG'), 'CURR:COMP?': settings['compliance_v'],
                'SOUR:WAVE:FREQ?': settings['frequency_hz'], 'SOUR:WAVE:AMPL?': state.get('SOUR:WAVE:AMPL'),
                'OUTP?': state['OUTP'], 'HARM?': settings['harmonic_a'], 'FMOD?': 0,
                'RSLP?': app.PPMS_SR830_REFERENCE_SLOPE, 'FREQ?': settings['frequency_hz'], 'LIAS?': 0}
            return (replies[command],)

        panel._ppms_write_6221_checked = write
        panel._ppms_query_numbers = query
        panel._rte_scan_worker('Current', plan, settings, 5., 0., .001)
        return panel, writes

    def check(self, plan):
        panel, writes = self.run_scan(plan)
        def peak(current_ma):
            return app.validate_ppms_transport_scan_settings(
                current_ma, 317.3, 50., 1, 2, ('830_a',))['peak_a']
        largest = peak(max(p[2] for p in plan))
        self.assertEqual([w for w in writes if w.startswith('SOUR:CURR:RANG')],
                         [f'SOUR:CURR:RANG {largest:.12e}'])
        self.assertEqual([w for w in writes if w.startswith('SOUR:WAVE:AMPL')],
                         [f'SOUR:WAVE:AMPL {peak(p[2]):.12e}' for p in plan])
        self.assertEqual(len(panel.ppms_field_scan_rows), len(plan))
        panel._stop_ppms_transport_source.assert_not_called()

    def test_descending_scan_passes_the_range_check(self):
        # Start 0.1 -> End 0.01 mA: the first point is the largest current.
        self.check(app.rte_scan_plan('Current', .1, .01, .01, 1, False))

    def test_single_point_at_the_default_current(self):
        plan = app.rte_scan_plan('Current', .1, .1, .01)
        self.assertEqual(plan, ((1, 1, .1),))
        self.check(plan)

    def test_other_single_currents_and_an_ascending_scan(self):
        for start in (.3, .7, 1.1, 2.9, 3.3, 7.7, 10., 70.7):
            with self.subTest(start=start):
                self.check(app.rte_scan_plan('Current', start, start, .01))
        self.check(app.rte_scan_plan('Current', .01, .1, .01, 1, False))


class PPMSExitKeepsFieldOutsideFieldScans(unittest.TestCase):
    def stand_in(self, mode, owner=None):
        a = owner if owner is not None else types.SimpleNamespace()
        a.ppms_closed = False
        a.ppms_field_scan_running = True
        a.ppms_field_scan_stop_event = threading.Event()
        a.ppms_poll_after_id = None
        a.ppms_environment_controller = Mock(connected=True)
        a.ppms_field_rate_var = Var('100')
        a.ppms_field_approach_var = Var('No overshoot')
        a._ppms_field_approach_code = app.CombinedControlApp._ppms_field_approach_code
        a.ppms_instrument_controllers = {}
        a.ppms_executor = Mock()
        a.append_log = Mock()
        if mode is not None:
            a.ppms_active_scan_mode = mode
        return a

    def test_only_field_scans_zero_the_field_on_exit(self):
        for mode in ('Temperature', 'Angle', 'Current', 'Angle+Temperature',
                     'Current+Temperature', 'Field', 'Field+Temperature'):
            with self.subTest(mode=mode):
                a = self.stand_in(mode)
                app.CombinedControlApp.safe_close_ppms(a)
                remote = a.ppms_environment_controller
                if mode in ('Field', 'Field+Temperature'):
                    remote.set_field.assert_called_once_with(0.0, 100.0, 1, 1)
                else:
                    remote.set_field.assert_not_called()
                remote.disconnect.assert_called_once()
                self.assertTrue(a.ppms_field_scan_stop_event.is_set())

    def test_ppms_page_close_path(self):
        # PPMSPanel.safe_close_ppms -> the base body on the panel itself; its
        # __getattr__ refuses ppms* names, so a missing mode reads as "".
        for mode in ('Temperature', None, 'Field'):
            with self.subTest(mode=mode):
                panel = app.PPMSPanel.__new__(app.PPMSPanel)
                panel.parent_app = Mock()
                panel.zurich_closed = False
                panel.zurich_after_id = None
                panel.zurich_executor = Mock()
                panel.zurich_controller = Mock()
                self.stand_in(mode, panel)
                panel.safe_close_ppms()
                remote = panel.ppms_environment_controller
                if mode == 'Field':
                    remote.set_field.assert_called_once_with(0.0, 100.0, 1, 1)
                else:
                    remote.set_field.assert_not_called()
                remote.disconnect.assert_called_once()


class WarmUpFromBaseTemperature(unittest.TestCase):
    def set_t(self, target, rate, readback_k):
        a = types.SimpleNamespace(
            ppms_temperature_target_var=Var(target), ppms_temperature_rate_var=Var(rate),
            ppms_last_readback=None if readback_k is None else {'temperature_k': readback_k},
            ppms_temperature_approach_var=Var('Fast settle'),
            _ppms_temperature_approach_code=app.CombinedControlApp._ppms_temperature_approach_code,
            ppms_environment_controller=Mock(), _run_ppms_command=Mock(), root=None)
        with patch.object(app.messagebox, 'showerror') as showerror:
            app.CombinedControlApp.set_ppms_temperature(a)
        showerror.assert_not_called()
        a._run_ppms_command.assert_called_once()
        a._run_ppms_command.call_args.args[1]()   # the queued PPMS operation
        return a

    def test_set_t_from_5_k_lowers_the_filled_in_rate(self):
        a = self.set_t('300', '10', 5.0)
        a.ppms_environment_controller.set_temperature.assert_called_once_with(300.0, 1.0, 0)
        self.assertEqual(a.ppms_temperature_rate_var.get(), '1')

    def test_set_t_above_9_k_or_a_slower_rate_is_kept(self):
        for rate, readback, sent in (('10', 20.0, 10.0), ('10', None, 10.0), ('0.5', 5.0, 0.5)):
            with self.subTest(rate=rate, readback=readback):
                a = self.set_t('300', rate, readback)
                a.ppms_environment_controller.set_temperature.assert_called_once_with(300.0, sent, 0)
                self.assertEqual(a.ppms_temperature_rate_var.get(), rate)

    def macro_panel(self, live_k):
        panel = app.CombinedControlApp.__new__(app.CombinedControlApp)
        panel.ppms_macro_stop = threading.Event()
        panel.closing = False
        panel.ppms_environment_controller = Mock()
        panel.ppms_environment_controller.read_environment.return_value = {'temperature_k': live_k}
        panel.thread_log = Mock()
        return panel

    def test_macro_temp_without_rate_picks_it_from_the_live_temperature(self):
        commands = app.parse_ppms_macro('TEMP 5\nWAI WAITFOR 60 1 0 0 0 0\nSCAN\nTEMP 300')
        self.assertEqual(commands[-1], (4, 'TEMP', (300.0, None, 0)))
        for target, live, rate in ((300., 5., 1.), (300., 9., 1.), (300., 20., 10.),
                                   (5., 300., 1.), (300., float('nan'), 1.)):
            with self.subTest(target=target, live=live):
                panel = self.macro_panel(live)
                panel._ppms_macro_operation('TEMP', (target, None, 0))
                panel.ppms_environment_controller.set_temperature.assert_called_once_with(target, rate, 0)
                # The run log names the rate the line used.
                self.assertIn(f'without a rate: {rate:g} K/min', panel.thread_log.call_args.args[0])

    def test_macro_explicit_rates(self):
        # An explicit rate is kept; after a TEMP at or below 9 K with no SCAN
        # in between, a fast warm-up is refused at Validate, before anything
        # is sent.
        self.assertEqual(app.parse_ppms_macro('TMP TEMP 10 10 0')[0], (1, 'TEMP', (10, 10, 0)))
        for text in ('TEMP 5\nWAI WAITFOR 60 1 0 0 0 0\nTEMP 300 10',
                     'TEMP 5\nFIELD 0 100\nWAIT 10\nTEMP 300 10'):
            with self.subTest(text=text), self.assertRaisesRegex(ValueError, r'Line \d: .*1 K/min'):
                app.parse_ppms_macro(text)
        self.assertEqual(app.parse_ppms_macro('TEMP 5\nWAI WAITFOR 60 1 0 0 0 0\nTEMP 300 1')[-1],
                         (3, 'TEMP', (300., 1., 0)))
        # A SCAN in between may be a Temperature scan that warmed the sample
        # (e.g. 5 -> 300 K), so Validate no longer assumes 5 K: the line
        # parses (as at c3271af) and the run checks the live temperature.
        commands = app.parse_ppms_macro('TMP TEMP 5 1 0\nWAI WAITFOR 60 1 0 0 0 0\nSCAN\nTMP TEMP 250 10 0')
        self.assertEqual(commands[-1], (4, 'TEMP', (250., 10., 0)))
        # WAITFOR still waits for the last TEMP target.
        self.assertEqual(app.parse_ppms_macro('TEMP 5\nSCAN\nWAI WAITFOR 60 1 0 0 0 0')[-1],
                         (3, 'WAITFOR', (60., 5., None, None)))
        panel = self.macro_panel(300.)
        panel._ppms_macro_operation('TEMP', commands[-1][2])
        panel.ppms_environment_controller.set_temperature.assert_called_once_with(250., 10., 0)
        # The same line after a SCAN that left the sample at base temperature
        # is refused at run time, with nothing sent.
        panel = self.macro_panel(5.)
        with self.assertRaisesRegex(ValueError, '1 K/min'):
            panel._ppms_macro_operation('TEMP', commands[-1][2])
        panel.ppms_environment_controller.set_temperature.assert_not_called()
        # Starting at base temperature with no TEMP before it: still refused
        # at run time, with nothing sent (fails safe, as before).
        panel = self.macro_panel(5.)
        with self.assertRaisesRegex(ValueError, '1 K/min'):
            panel._ppms_macro_operation('TEMP', app.parse_ppms_macro('TEMP 300 10')[0][2])
        panel.ppms_environment_controller.set_temperature.assert_not_called()
        panel.thread_log.assert_not_called()


if __name__ == '__main__':
    unittest.main()
