"""Offline regression tests for instrument safety logic (no hardware needed)."""

import math
import sys
import tempfile
import types
import unittest
from unittest import mock
from pathlib import Path

import numpy as np


PROJECT = Path(__file__).resolve().parents[1]
if str(PROJECT) not in sys.path:
    sys.path.insert(0, str(PROJECT))

import Transport_all_NH_dev as app


class FakeVariable:
    def __init__(self, value):
        self.value = value

    def get(self):
        return self.value

    def set(self, value):
        self.value = value


class FakeProgressbar:
    def __init__(self):
        self.options = {}

    def configure(self, **options):
        self.options.update(options)


class FakeSharedVisaInstrument:
    def __init__(self, identity):
        self.identity = identity
        self.closed = False
        self.commands = []
        self.timeout = None
        self.write_termination = "PYVISA_DEFAULT_WRITE"
        self.read_termination = "PYVISA_DEFAULT_READ"
        self.send_end = False
        self.query_delay = 0.0

    def _require_open(self):
        if self.closed:
            raise RuntimeError("VI_ERROR_INV_OBJECT")

    def query(self, command):
        self._require_open()
        if command == "*IDN?":
            return self.identity
        return "0"

    def write(self, command):
        self._require_open()
        self.commands.append(command)

    def close(self):
        self.closed = True


class FakeSharedVisaManager:
    def __init__(self, identities):
        self.identities = dict(identities)
        self._session = 1
        self.resources = []
        self.close_calls = 0
        self.list_resources_calls = 0

    @property
    def session(self):
        if self._session is None:
            raise RuntimeError("invalid ResourceManager session")
        return self._session

    def open_resource(self, resource):
        self.session
        instrument = FakeSharedVisaInstrument(self.identities[resource])
        self.resources.append(instrument)
        return instrument

    def list_resources(self):
        self.session
        self.list_resources_calls += 1
        return tuple(self.identities)

    def close(self):
        self.close_calls += 1
        for instrument in self.resources:
            instrument.close()
        self._session = None


class FakePyVisaModule:
    def __init__(self, manager):
        self.manager = manager

    def ResourceManager(self):
        return self.manager


class Fake2410:
    # :SOUR:VOLT:PROT levels from the 2400-series manual: a value between two
    # levels selects the lower one, and above the highest it means none.
    OVP_LEVELS = (20.0, 40.0, 100.0, 200.0, 300.0, 400.0, 500.0)
    OVP_NONE = 1100.0
    IDENTITY = "KEITHLEY INSTRUMENTS INC.,MODEL 2410,1234567,C34"

    def __init__(self):
        self.commands = []
        self.voltage = 0.0
        self.voltage_range = 20.0
        self.ovp = 200.0
        self.output = 0
        self.errors = []

    def _ovp_level(self, value):
        value = abs(value)
        if value > self.OVP_LEVELS[-1]:
            return self.OVP_NONE
        return max([level for level in self.OVP_LEVELS if level <= value] or [self.OVP_LEVELS[0]])

    def write(self, command):
        self.commands.append(command)
        upper = command.upper()
        if upper.startswith(":SOUR:VOLT:RANG "):
            self.voltage_range = float(command.rsplit(" ", 1)[1])
        elif upper.startswith(":SOUR:VOLT:PROT "):
            self.ovp = self._ovp_level(float(command.rsplit(" ", 1)[1]))
        elif upper.startswith(":SOUR:VOLT "):
            self.voltage = float(command.rsplit(" ", 1)[1])
        elif upper == ":OUTP ON":
            self.output = 1
        elif upper == ":OUTP OFF":
            self.output = 0
        elif upper == "*RST":
            # As the instrument: output off, source at 0, OVP none.
            self.output, self.voltage, self.ovp = 0, 0.0, self.OVP_NONE

    def query(self, command):
        upper = command.upper()
        if upper == "SYST:ERR?":
            if self.errors:
                return self.errors.pop(0)
            return '0,"No error"'
        if upper == ":SOUR:VOLT:RANG?":
            return str(self.voltage_range)
        if upper == ":SOUR:VOLT:PROT?":
            return str(self.ovp)
        if upper == ":SOUR:VOLT?":
            return str(self.voltage)
        if upper == ":OUTP?":
            return str(self.output)
        if upper == "*IDN?":
            return self.IDENTITY
        raise AssertionError(f"unexpected query: {command}")

    def close(self):
        pass


class Fake2400(Fake2410):
    """A 2400: its highest source range is 200 V, so the 2410's 1000 V range
    is refused with a SCPI error, as the instrument does.  It has no 200 V
    OVP level: asked for 200 V it selects none, read back as 210 V."""

    OVP_LEVELS = (20.0, 40.0, 60.0, 80.0, 100.0, 120.0, 160.0)
    OVP_NONE = 210.0
    IDENTITY = "KEITHLEY INSTRUMENTS INC.,MODEL 2400,4105497,C30   Mar 17 2006 09:29:29/A02  /K/J"

    def write(self, command):
        upper = command.upper()
        if upper.startswith(":SOUR:VOLT:RANG ") and float(command.rsplit(" ", 1)[1]) > 210.0:
            self.commands.append(command)
            self.errors.append('-222,"Parameter data out of range"')
            return
        super().write(command)



class Fake6221:
    def __init__(self):
        self.commands = []
        self.current = 0.0
        self.compliance = 10.0
        self.auto_range = 0
        self.output = 0
        self.wave_frequency = 13.7
        self.wave_amplitude = 0.0
        self.wave_offset = 0.0
        self.wave_cycles = 1
        self.marker_enabled = 0
        self.marker_line = 4
        self.marker_phase = 0.0
        self.measurement_condition = 0
        self.auto_complete_waveform = False
        self.reported_wave_cycles = None

    def write(self, command):
        self.commands.append(command)
        upper = command.upper()
        if upper.startswith("CURR:COMP "):
            self.compliance = float(command.rsplit(" ", 1)[1])
        elif upper.startswith("SOUR:CURR:RANG:AUTO "):
            self.auto_range = int(upper.endswith("ON"))
        elif upper.startswith("SOUR:CURR "):
            self.current = float(command.rsplit(" ", 1)[1])
        elif upper.startswith("SOUR:WAVE:FREQ "):
            self.wave_frequency = float(command.rsplit(" ", 1)[1])
        elif upper.startswith("SOUR:WAVE:AMPL "):
            self.wave_amplitude = float(command.rsplit(" ", 1)[1])
        elif upper.startswith("SOUR:WAVE:OFFS "):
            self.wave_offset = float(command.rsplit(" ", 1)[1])
        elif upper.startswith("SOUR:WAVE:DUR:CYCL "):
            value = command.rsplit(" ", 1)[1]
            self.wave_cycles = value if value.upper() == "INF" else int(value)
        elif upper.startswith("SOUR:WAVE:PMAR:OLIN "):
            self.marker_line = int(command.rsplit(" ", 1)[1])
        elif upper.startswith("SOUR:WAVE:PMAR:STAT "):
            self.marker_enabled = int(upper.endswith("ON"))
        elif upper.startswith("SOUR:WAVE:PMAR "):
            self.marker_phase = float(command.rsplit(" ", 1)[1])
        elif upper == "SOUR:WAVE:INIT":
            self.output = 0 if self.auto_complete_waveform else 1
        elif upper == "SOUR:WAVE:ABOR":
            self.output = 0
        elif upper == "OUTP ON":
            self.output = 1
        elif upper == "OUTP OFF":
            self.output = 0

    def query(self, command):
        upper = command.upper()
        if upper == "SYST:ERR?":
            return '0,"No error"'
        if upper == "CURR:COMP?":
            return str(self.compliance)
        if upper == "SOUR:CURR:RANG:AUTO?":
            return str(self.auto_range)
        if upper == "SOUR:CURR?":
            return str(self.current)
        if upper == "SOUR:WAVE:FREQ?":
            return str(self.wave_frequency)
        if upper == "SOUR:WAVE:AMPL?":
            return str(self.wave_amplitude)
        if upper == "SOUR:WAVE:OFFS?":
            return str(self.wave_offset)
        if upper == "SOUR:WAVE:DUR:CYCL?":
            if self.reported_wave_cycles is not None:
                return str(self.reported_wave_cycles)
            return str(self.wave_cycles)
        if upper == "SOUR:WAVE:PMAR:STAT?":
            return str(self.marker_enabled)
        if upper == "SOUR:WAVE:PMAR:OLIN?":
            return str(self.marker_line)
        if upper == "SOUR:WAVE:PMAR?":
            return str(self.marker_phase)
        if upper == "OUTP?":
            return str(self.output)
        if upper == "STAT:MEAS:COND?":
            return str(self.measurement_condition)
        raise AssertionError(f"unexpected query: {command}")


class FakeSR830:
    def __init__(self, r_value):
        self.commands = []
        self.harmonic = 1
        self.reference_mode = 0
        self.reference_slope = 1  # PPMS default: TTL rising edge
        self.frequency = 13.7
        self.r_value = float(r_value)
        self.status = 0

    def write(self, command):
        self.commands.append(command)
        upper = command.upper()
        if upper.startswith("HARM "):
            self.harmonic = int(command.rsplit(" ", 1)[1])
        elif upper.startswith("FMOD "):
            self.reference_mode = int(command.rsplit(" ", 1)[1])
        elif upper.startswith("RSLP "):
            self.reference_slope = int(command.rsplit(" ", 1)[1])
        elif upper.startswith("SENS "):
            self.sensitivity = int(command.rsplit(" ", 1)[1])

    def query(self, command):
        upper = command.upper()
        if upper == "*ESR?":
            return "0"
        if upper == "HARM?":
            return str(self.harmonic)
        if upper == "FMOD?":
            return str(self.reference_mode)
        if upper == "RSLP?":
            return str(self.reference_slope)
        if upper == "FREQ?":
            return str(self.frequency)
        if upper == "LIAS?":
            return str(self.status)
        if upper == "SNAP? 1,2,3,4,9":
            return f"1e-6,2e-6,{self.r_value},3.5,{self.frequency}"
        if upper == "SENS?":
            # Sensitivity code queried by the PPMS SR830 autorange step.
            return str(getattr(self, "sensitivity", 24))
        if upper == "OFLT?":
            # Time-constant code (9 = 100 ms) used to size the settle wait.
            return str(getattr(self, "time_constant_code", 9))
        raise AssertionError(f"unexpected query: {command}")

    def clear(self):
        pass


class Fake2182:
    def write(self, _command):
        pass

    def query(self, command):
        if command.upper() == "SYST:ERR?":
            return '0,"No error"'
        raise AssertionError(f"unexpected query: {command}")


class FakeKepco4886:
    def __init__(self):
        self.commands = []
        self.mode = "CURR"
        self.current = 0.0
        self.voltage = 0.0
        self.output = 0

    def write(self, command):
        self.commands.append(command)
        upper = command.upper()
        if upper.startswith("FUNC:MODE "):
            self.mode = upper.rsplit(" ", 1)[1]
        elif upper.startswith("CURR "):
            self.current = float(command.rsplit(" ", 1)[1])
        elif upper.startswith("VOLT "):
            self.voltage = float(command.rsplit(" ", 1)[1])
        elif upper == "OUTP ON":
            self.output = 1
        elif upper == "OUTP OFF":
            self.output = 0

    def query(self, command):
        upper = command.upper()
        if upper == "FUNC:MODE?":
            return self.mode
        if upper == "MEAS:CURR?" or upper == "CURR?":
            return str(self.current)
        if upper == "MEAS:VOLT?" or upper == "VOLT?":
            return str(self.voltage)
        if upper == "OUTP?":
            return str(self.output)
        if upper == "SYST:ERR?":
            return '0,"No error"'
        raise AssertionError(f"unexpected query: {command}")


class FakeLakeShore475:
    def __init__(self, field=123.45, unit=1):
        self.field = field
        self.unit = unit

    def query(self, command):
        if command.upper() == "RDGFIELD?":
            return str(self.field)
        if command.upper() == "UNIT?":
            return str(self.unit)
        raise AssertionError(f"unexpected query: {command}")


class FakeMultiPyVuClient:
    def get_temperature(self):
        return 7.25, "Stable"

    def get_field(self):
        return -1234.5, "Holding"


class InstrumentSafetyTests(unittest.TestCase):
    def setUp(self):
        app._INSTRUMENT_LEASES.clear()
        app._SHARED_VISA_MANAGER = None
        app._SHARED_VISA_MANAGER_USERS = 0

    def tearDown(self):
        app._INSTRUMENT_LEASES.clear()
        app._SHARED_VISA_MANAGER = None
        app._SHARED_VISA_MANAGER_USERS = 0

    def test_shared_visa_manager_survives_one_controller_disconnect(self):
        original_pyvisa = app.pyvisa
        manager = FakeSharedVisaManager({
            "GPIB0::6::INSTR": "KEPCO,4886,TEST,1",
            "GPIB0::12::INSTR": "LSCI,MODEL475,TEST,1",
        })
        app.pyvisa = FakePyVisaModule(manager)
        kepco = app.SafeVisaIdentityController(
            lambda _message: None, "KEPCO", "4886", owner_scope="STFMR"
        )
        gauss = app.SafeVisaIdentityController(
            lambda _message: None, "Gauss", "MODEL475", owner_scope="STFMR"
        )
        try:
            kepco.connect("GPIB0::6::INSTR")
            gauss.connect("GPIB0::12::INSTR")
            self.assertIs(kepco.resource_manager, gauss.resource_manager)
            self.assertEqual(app._SHARED_VISA_MANAGER_USERS, 2)

            kepco.disconnect()
            self.assertEqual(manager.close_calls, 0)
            self.assertEqual(app._SHARED_VISA_MANAGER_USERS, 1)
            self.assertIn("MODEL475", gauss.instrument.query("*IDN?"))

            gauss.disconnect()
            self.assertEqual(manager.close_calls, 1)
            self.assertEqual(app._SHARED_VISA_MANAGER_USERS, 0)
            self.assertIsNone(app._SHARED_VISA_MANAGER)
        finally:
            try:
                kepco.disconnect()
            except BaseException:
                pass
            try:
                gauss.disconnect()
            except BaseException:
                pass
            app.pyvisa = original_pyvisa

    def test_failed_visa_identity_check_does_not_close_other_session(self):
        original_pyvisa = app.pyvisa
        manager = FakeSharedVisaManager({
            "GPIB0::6::INSTR": "KEPCO,4886,TEST,1",
            "GPIB0::16::INSTR": "UNEXPECTED,DEVICE,TEST,1",
        })
        app.pyvisa = FakePyVisaModule(manager)
        kepco = app.SafeVisaIdentityController(
            lambda _message: None, "KEPCO", "4886", owner_scope="STFMR"
        )
        vna = app.SafeVisaIdentityController(
            lambda _message: None, "N5227A", "N5227A", owner_scope="STFMR"
        )
        try:
            kepco.connect("GPIB0::6::INSTR")
            with self.assertRaises(RuntimeError):
                vna.connect("GPIB0::16::INSTR")
            self.assertEqual(manager.close_calls, 0)
            self.assertEqual(app._SHARED_VISA_MANAGER_USERS, 1)
            self.assertIn("4886", kepco.instrument.query("*IDN?"))
            kepco.disconnect()
            self.assertEqual(manager.close_calls, 1)
        finally:
            try:
                kepco.disconnect()
            except BaseException:
                pass
            try:
                vna.disconnect()
            except BaseException:
                pass
            app.pyvisa = original_pyvisa

    def test_2410_scan_range_and_hardware_ovp(self):
        controller = app.Keithley2410Controller(lambda _message: None)
        fake = Fake2410()
        controller.instrument = fake
        controller.enable_output(200.0)
        self.assertEqual(fake.output, 1)
        self.assertEqual(fake.voltage_range, 1000.0)
        self.assertEqual(fake.ovp, 200.0)
        self.assertTrue(math.isclose(controller.set_voltage(200.0), 200.0))
        with self.assertRaises(ValueError):
            controller.set_voltage(200.0001)

    def test_manual_range_change_is_output_off_first(self):
        controller = app.Keithley2410Controller(lambda _message: None)
        fake = Fake2410()
        controller.instrument = fake
        controller.enable_output(200.0)
        fake.commands.clear()
        controller.apply_manual_voltage(1.0)
        off_index = fake.commands.index(":OUTP OFF")
        range_index = fake.commands.index(":SOUR:VOLT:RANG 20")
        on_index = fake.commands.index(":OUTP ON")
        self.assertLess(off_index, range_index)
        self.assertLess(range_index, on_index)

    def test_stfmr_kepco_current_mode_matches_labview_trace(self):
        controller = app.STFMRKepco4886Controller(lambda _message: None)
        fake = FakeKepco4886()
        controller.instrument = fake
        result = controller.set_output("CURR", -2.5, 8.0)
        self.assertEqual(fake.commands, [
            "FUNC:MODE CURR",
            "CURR 0;VOLT 8",
            "VOLT:RANG:AUTO 1;CURR:RANG:AUTO 1",
            "OUTP ON",
            "CURR -2.500000",
        ])
        self.assertEqual(result["mode"], "CURR")
        self.assertTrue(result["output"])
        self.assertEqual(result["current_a"], -2.5)
        self.assertEqual(result["voltage_v"], 8.0)
        fake.commands.clear()
        scan_result = controller.set_scan_current(1.25)
        self.assertEqual(fake.commands, ["CURR 1.250000"])
        self.assertEqual(scan_result["current_a"], 1.25)
        with self.assertRaises(ValueError):
            controller.set_output("CURR", 10.0001, 8.0)

    def test_stfmr_kepco_voltage_mode_and_zero(self):
        controller = app.STFMRKepco4886Controller(lambda _message: None)
        fake = FakeKepco4886()
        controller.instrument = fake
        result = controller.set_output("VOLT", 12.0, 3.0)
        self.assertEqual(fake.commands, [
            "FUNC:MODE VOLT",
            "VOLT 0;CURR 3",
            "VOLT:RANG:AUTO 1;CURR:RANG:AUTO 1",
            "OUTP ON",
            "VOLT 12.000000",
        ])
        self.assertEqual(result["mode"], "VOLT")
        zeroed = controller.zero_output()
        self.assertFalse(zeroed["output"])
        self.assertEqual(zeroed["current_a"], 0.0)
        self.assertEqual(zeroed["voltage_v"], 0.0)
        self.assertEqual(fake.commands[-2:], ["VOLT 0.000000", "OUTP OFF"])

    def test_stfmr_kepco_connect_preserves_native_gpib_eoi_profile(self):
        original_pyvisa = app.pyvisa
        manager = FakeSharedVisaManager({
            "GPIB0::6::INSTR": "KEPCO,BIT 4886 20-10,TEST,1.0",
        })
        app.pyvisa = FakePyVisaModule(manager)
        controller = app.STFMRKepco4886Controller(lambda _message: None)
        controller.connect_settle_seconds = 0.0
        try:
            identity = controller.connect("GPIB0::6::INSTR")
            self.assertIn("4886 20-10", identity)
            instrument = manager.resources[-1]
            self.assertIsNone(instrument.write_termination)
            self.assertIsNone(instrument.read_termination)
            self.assertTrue(instrument.send_end)
            self.assertEqual(instrument.query_delay, 0.0)
            self.assertEqual(instrument.commands, [])
            self.assertEqual(manager.list_resources_calls, 0)
        finally:
            try:
                controller.disconnect()
            except BaseException:
                pass
            app.pyvisa = original_pyvisa

    def test_stfmr_kepco_connect_never_queries_or_writes(self):
        class TimeoutInstrument(FakeSharedVisaInstrument):
            def query(self, command):
                self._require_open()
                raise RuntimeError("VI_ERROR_TMO")

        class TimeoutManager(FakeSharedVisaManager):
            def open_resource(self, resource):
                self.session
                instrument = TimeoutInstrument(self.identities[resource])
                self.resources.append(instrument)
                return instrument

        original_pyvisa = app.pyvisa
        manager = TimeoutManager({
            "GPIB0::6::INSTR": "KEPCO,BIT 4886 20-10,TEST,1.0",
        })
        app.pyvisa = FakePyVisaModule(manager)
        controller = app.STFMRKepco4886Controller(lambda _message: None)
        controller.connect_settle_seconds = 0.0
        try:
            identity = controller.connect("GPIB0::6::INSTR")
            instrument = manager.resources[-1]
            self.assertIn("not queried", identity)
            self.assertEqual(instrument.commands, [])
            self.assertFalse(instrument.closed)
            controller.disconnect()
            self.assertTrue(instrument.closed)
        finally:
            try:
                controller.disconnect()
            except BaseException:
                pass
            app.pyvisa = original_pyvisa

    def test_stfmr_gaussmeter_preserves_units(self):
        controller = app.STFMRGauss475Controller(lambda _message: None)
        controller.instrument = FakeLakeShore475(field=-456.75, unit=3)
        result = controller.read_field()
        self.assertEqual(result["field"], -456.75)
        self.assertEqual(result["unit"], "Oe")

    def test_stfmr_lockin_defaults_match_supplied_labview_setup(self):
        self.assertEqual(app.STFMR_LOCKIN_DEFAULT_RESOURCES, {
            "kepco": "GPIB0::6::INSTR",
            "gauss": "GPIB0::15::INSTR",
            "smb": "GPIB0::28::INSTR",
            "830": "GPIB0::9::INSTR",
            "mfli": "dev4435",
        })
        self.assertEqual(app.STFMR_LOCKIN_DEFAULT_FREQUENCY_GHZ, 4.0)
        self.assertEqual(app.STFMR_LOCKIN_DEFAULT_POWER_DBM, 6.0)
        self.assertEqual(app.STFMR_LOCKIN_DEFAULT_LFO_HZ, 317.3)
        self.assertEqual(app.STFMR_LOCKIN_MFLI_TIME_CONSTANT_S, 0.1)
        self.assertEqual(
            app.STFMR_LOCKIN_SR830_DEFAULT_REFERENCE_INPUT, "Sine"
        )
        self.assertEqual(app.STFMR_LOCKIN_SR830_REFERENCE_INPUTS, {
            "Sine": 0,
            "TTL Rising": 1,
            "TTL Falling": 2,
        })

    def test_stfmr_lockin_xy_component_normalizes_sr830_and_mfli_samples(self):
        helper = app.CombinedControlApp._stfmr_lockin_sample_component
        self.assertAlmostEqual(helper({"x_v": 1.25e-6}, "X"), 1.25e-6)
        self.assertAlmostEqual(helper({"y_v": -2.5e-6}, "Y"), -2.5e-6)
        self.assertAlmostEqual(helper({"X": 3.0e-3}, "X"), 3.0e-3)
        self.assertAlmostEqual(helper({"Y": -4.0e-3}, "Y"), -4.0e-3)
        with self.assertRaisesRegex(RuntimeError, "valid X"):
            helper({"X": float("nan")}, "X")

    def test_stfmr_sweep_refuses_to_start_without_the_safety_tick(self):
        """The page's one confirmation, in the 4886 output panel, gates the sweep."""
        harness = object.__new__(app.CombinedControlApp)
        harness._stfmr_lockin_connected = lambda _key: True
        harness.stfmr_lockin_meter_var = FakeVariable("MFLI")
        harness.stfmr_lockin_manual_safety_var = FakeVariable(False)
        with self.assertRaisesRegex(RuntimeError, "safety confirmation"):
            harness._stfmr_lockin_inputs()

    def test_stfmr_lockin_scan_is_one_way_per_loop(self):
        harness = object.__new__(app.CombinedControlApp)
        harness._stfmr_lockin_connected = lambda _key: True
        harness.stfmr_lockin_manual_safety_var = FakeVariable(True)
        harness.stfmr_lockin_meter_var = FakeVariable("MFLI")
        harness.stfmr_lockin_frequency_start_var = FakeVariable("5")
        harness.stfmr_lockin_frequency_stop_var = FakeVariable("7")
        harness.stfmr_lockin_frequency_step_var = FakeVariable("1")
        harness.stfmr_lockin_power_var = FakeVariable("10")
        harness.stfmr_lockin_lfo_var = FakeVariable("317.3")
        harness.stfmr_lockin_delay_var = FakeVariable("0.5")
        harness.stfmr_lockin_limit_var = FakeVariable("10")
        harness.stfmr_lockin_loop_var = FakeVariable(2)
        harness.stfmr_lockin_start_var = FakeVariable("-1")
        harness.stfmr_lockin_stop_var = FakeVariable("1")
        harness.stfmr_lockin_step_var = FakeVariable("1")
        points, frequencies, *_rest = harness._stfmr_lockin_inputs()
        self.assertEqual(points, [-1.0, 0.0, 1.0, -1.0, 0.0, 1.0])
        self.assertEqual(frequencies, [5.0, 6.0, 7.0])
        self.assertEqual(harness.stfmr_lockin_points_per_loop, 3)

    def test_stfmr_lockin_field_plot_does_not_join_loops(self):
        rows = [
            {"meter": "MFLI", "loop": 1, "field_g": -10, "voltage_v": 1e-3},
            {"meter": "MFLI", "loop": 1, "field_g": 10, "voltage_v": 2e-3},
            {"meter": "MFLI", "loop": 2, "field_g": -10, "voltage_v": 3e-3},
            {"meter": "MFLI", "loop": 2, "field_g": 10, "voltage_v": 4e-3},
        ]
        series = app.CombinedControlApp._stfmr_lockin_field_component_series(
            rows, "MFLI", "mfli", "voltage_v"
        )
        self.assertEqual(
            series,
            {
                "mfli_loop_1": [(-10.0, 1e-3), (10.0, 2e-3)],
                "mfli_loop_2": [(-10.0, 3e-3), (10.0, 4e-3)],
            },
        )

    def test_stfmr_lockin_field_plot_groups_frequency_and_loop(self):
        rows = [
            {
                "meter": "MFLI", "loop": 1,
                "microwave_frequency_ghz": 5.0,
                "field_g": -10, "voltage_v": 1e-3,
            },
            {
                "meter": "MFLI", "loop": 1,
                "microwave_frequency_ghz": 6.0,
                "field_g": -9, "voltage_v": 2e-3,
            },
            {
                "meter": "MFLI", "loop": 2,
                "microwave_frequency_ghz": 5.0,
                "field_g": 10, "voltage_v": 3e-3,
            },
        ]
        series = app.CombinedControlApp._stfmr_lockin_field_component_series(
            rows,
            "MFLI",
            "mfli",
            "voltage_v",
            group_frequency=True,
        )
        self.assertEqual(
            series,
            {
                "mfli_freq_5_loop_1": [(-10.0, 1e-3)],
                "mfli_freq_6_loop_1": [(-9.0, 2e-3)],
                "mfli_freq_5_loop_2": [(10.0, 3e-3)],
            },
        )
        labels, colors = (
            app.CombinedControlApp._stfmr_lockin_frequency_series_styles(
                series, "voltage_v"
            )
        )
        self.assertEqual(labels["mfli_freq_5_loop_1"], "5 GHz")
        self.assertEqual(labels["mfli_freq_5_loop_2"], "")
        self.assertEqual(labels["mfli_freq_6_loop_1"], "6 GHz")
        self.assertEqual(
            colors["mfli_freq_5_loop_1"], colors["mfli_freq_5_loop_2"]
        )
        self.assertNotEqual(
            colors["mfli_freq_5_loop_1"], colors["mfli_freq_6_loop_1"]
        )

    def test_stfmr_lockin_frequency_colors_darkens_with_frequency(self):
        series = {
            f"mfli_freq_{frequency}_loop_1": [(0.0, 0.0)]
            for frequency in (3.0, 4.0, 5.0, 6.0, 7.0, 8.0)
        }

        def brightness(color):
            red, green, blue = (
                int(color[index:index + 2], 16) for index in (1, 3, 5)
            )
            return 0.2126 * red + 0.7152 * green + 0.0722 * blue

        for component in ("x_v", "y_v", "phase_deg", "voltage_v"):
            _labels, colors = (
                app.CombinedControlApp._stfmr_lockin_frequency_series_styles(
                    series, component
                )
            )
            ordered_brightness = [
                brightness(colors[f"mfli_freq_{frequency}_loop_1"])
                for frequency in (3.0, 4.0, 5.0, 6.0, 7.0, 8.0)
            ]
            self.assertTrue(all(
                earlier > later
                for earlier, later in zip(
                    ordered_brightness, ordered_brightness[1:]
                )
            ))

    def test_stfmr_lockin_export_saves_all_four_plot_canvases(self):
        harness = object.__new__(app.CombinedControlApp)
        harness.stfmr_lockin_x_canvas = object()
        harness.stfmr_lockin_y_canvas = object()
        harness.stfmr_lockin_theta_canvas = object()
        harness.stfmr_lockin_canvas = object()
        captured = []
        harness.save_plot_canvas_snapshot = (
            lambda canvas, path: captured.append((canvas, Path(path).name))
        )

        saved = harness._save_stfmr_lockin_plot_snapshots(
            Path("results"), "20260828_220000_000000"
        )

        self.assertEqual(
            [path.name for path in saved],
            [
                "STFMR_field_scan_20260828_220000_000000_X.png",
                "STFMR_field_scan_20260828_220000_000000_Y.png",
                "STFMR_field_scan_20260828_220000_000000_theta.png",
                "STFMR_field_scan_20260828_220000_000000_field_vs_current.png",
            ],
        )
        self.assertEqual(
            [canvas for canvas, _name in captured],
            [
                harness.stfmr_lockin_x_canvas,
                harness.stfmr_lockin_y_canvas,
                harness.stfmr_lockin_theta_canvas,
                harness.stfmr_lockin_canvas,
            ],
        )

    def test_stfmr_lockin_plots_use_compact_markers(self):
        harness = object.__new__(app.CombinedControlApp)
        captured = {}
        harness._draw_nonlocal_series = (
            lambda *_args, **kwargs: captured.update(kwargs)
        )

        harness._draw_stfmr_lockin_frequency_component(
            object(), [], "MFLI", "mfli", "x_v", "MFLI X", "等待数据"
        )

        self.assertTrue(captured["markers"])
        self.assertEqual(captured["marker_radius"], 1.5)

    def test_stfmr_lower_right_plot_is_4886_current_vs_measured_field(self):
        harness = object.__new__(app.CombinedControlApp)
        harness.stfmr_lockin_canvas = object()
        harness.stfmr_lockin_rows = [{
            "meter": "MFLI", "microwave_frequency_ghz": 3.0,
            "loop": 1, "field_g": 125.0, "current_a": 0.5,
        }]
        harness.stfmr_lockin_selected_meter = "MFLI"
        captured = {}
        harness._draw_nonlocal_series = (
            lambda *args, **kwargs: captured.update(args=args, kwargs=kwargs)
        )

        harness._redraw_stfmr_lockin_plot()

        self.assertEqual(
            captured["args"][1],
            {"mfli_freq_3_loop_1": [(0.5, 125.0)]},
        )
        self.assertEqual(captured["args"][2], "4886 set current (A)")
        self.assertEqual(captured["args"][3], "LS475 measured field (G)")
        self.assertNotIn("auto_voltage_unit", captured["kwargs"])

    def test_mfli_accepts_discrete_timeconstant_readback_near_request(self):
        class FakeBridge:
            def request(self, command, **kwargs):
                self.command = command
                self.kwargs = kwargs
                return {
                    "actual_ac": 1,
                    "actual_imp50": 0,
                    "actual_scaling": 1.0,
                    "actual_harmonic": 1,
                    "actual_extref_harmonic": 1,
                    "extref_enabled": 1,
                    "extref_adcselect": kwargs["reference_adcselect"],
                    "timeconstant_s": 0.100016,
                    "filter_order": app.NONLOCAL_LOCKIN_FILTER_ORDER,
                    "sample_rate_hz": kwargs["sample_rate_hz"],
                    "input_range_v": 0.03,
                }

        controller = app.NonLocalACController(lambda _message: None)
        bridge = FakeBridge()
        controller.mfli_bridge = bridge
        controller.mfli_device = "dev4435"
        controller.mfli_props = {"options": "MD", "has_demod_2": True}
        result = controller.configure_mfli(
            "AC",
            1,
            "R",
            reference_source="Aux Input 1",
            sample_rate_hz=1.0,
            input_impedance="10 MΩ",
            input_scaling=1.0,
            timeconstant_s=0.1,
        )
        self.assertEqual(bridge.command, "configure")
        self.assertAlmostEqual(bridge.kwargs["timeconstant_s"], 0.1)
        self.assertAlmostEqual(result["timeconstant_s"], 0.100016)

    def test_mfli_readback_uses_independent_xy_for_phase_and_lock_state(self):
        class FakeBridge:
            def request(self, command, **_kwargs):
                self.command = command
                return {
                    "sample": {
                        "x": -650.0e-6,
                        "y": -750.0e-6,
                        "frequency": 244.78,
                        "extref_locked": 0,
                        "input_range_v": 0.03,
                        "input_max_normalized": 0.1,
                        "input_min_normalized": -0.1,
                    }
                }

        controller = app.NonLocalACController(lambda _message: None)
        bridge = FakeBridge()
        controller.mfli_bridge = bridge
        controller.mfli_device = "dev4435"
        controller.mfli_config = {"harmonic": 1}
        sample = controller.read_mfli(317.3)

        self.assertEqual(bridge.command, "read")
        self.assertAlmostEqual(sample["X"], -650.0e-6)
        self.assertAlmostEqual(sample["Y"], -750.0e-6)
        self.assertAlmostEqual(sample["phase_deg"], -130.91438322002512)
        self.assertFalse(sample["reference_valid"])

    def test_stfmr_reference_indicator_uses_green_red_and_neutral_states(self):
        class FakeCanvas:
            def itemconfigure(self, item, **kwargs):
                self.item = item
                self.options = kwargs

        harness = object.__new__(app.CombinedControlApp)
        canvas = FakeCanvas()
        harness.stfmr_lockin_reference_indicator = (canvas, 7)

        harness._set_stfmr_lockin_reference_state(True)
        self.assertEqual(canvas.item, 7)
        self.assertEqual(canvas.options["fill"], "#20b15a")
        harness._set_stfmr_lockin_reference_state(False)
        self.assertEqual(canvas.options["fill"], "#e53935")
        harness._set_stfmr_lockin_reference_state(None)
        self.assertEqual(canvas.options["fill"], "#9aa0a6")

    def test_stfmr_smb100a_setup_matches_supplied_trace(self):
        controller = app.STFMRSMB100AController(lambda _message: None)
        fake = FakeSharedVisaInstrument("Rohde&Schwarz,SMB100A,TEST,1.0")
        controller.instrument = fake
        controller.configure(5.0, 10.0, 317.3)
        self.assertEqual(fake.commands, [
            ":AM:DEPT:LIN 1.0E+2",
            "LFO:VOLT 0.50 V",
            "LFO:FREQ 317.3 Hz",
            ":POW 10",
            "FREQ 5GHz",
            "LFO ON",
            "OUTPUT ON",
            "AM:STAT ON",
        ])
        controller.set_frequency(6.0)
        self.assertEqual(fake.commands[-1], "FREQ 6GHz")
        controller.rf_off()
        self.assertEqual(
            fake.commands[-3:], ["AM:STAT OFF", "LFO OFF", "OUTPUT OFF"]
        )

    def test_stfmr_sr830_setup_and_sample_readback(self):
        class FakeSR830(FakeSharedVisaInstrument):
            def __init__(self, identity):
                super().__init__(identity)
                self.sensitivity = app.STFMR_LOCKIN_SR830_SENSITIVITY_CODE

            def write(self, command):
                super().write(command)
                if command.startswith("SENS "):
                    self.sensitivity = int(command.split()[1])

            def query(self, command):
                if command == "SNAP? 1,2,3,4,9":
                    return "1e-6,-2e-6,2.2360679e-6,-63.4349,317.3"
                if command == "LIAS?":
                    return "0"
                if command == "FMOD?":
                    return "0"
                if command == "RSLP?":
                    return "0"
                if command == "HARM?":
                    return "1"
                if command == "SENS?":
                    return str(self.sensitivity)
                if command == "FREQ?":
                    return "317.3"
                return super().query(command)

        controller = app.STFMRSR830Controller(lambda _message: None)
        fake = FakeSR830("Stanford_Research_Systems,SR830,TEST,1.0")
        controller.instrument = fake
        controller.configure()
        lock = controller.wait_for_reference(317.3)
        sample = controller.read_sample(317.3)
        self.assertIn("FMOD 0", fake.commands)
        self.assertIn("RSLP 0", fake.commands)
        self.assertIn(
            f"SENS {app.STFMR_LOCKIN_SR830_SENSITIVITY_CODE}", fake.commands
        )
        self.assertIn(
            f"OFLT {app.STFMR_LOCKIN_SR830_TIME_CONSTANT_CODE}", fake.commands
        )
        self.assertEqual(lock["reference_input"], "Sine")
        self.assertAlmostEqual(sample["r_v"], 2.2360679e-6)
        self.assertTrue(sample["reference_valid"])
        self.assertTrue(sample["measurement_valid"])

    def test_stfmr_sr830_ttl_ref_and_auto_range_are_verified(self):
        class FakeSR830(FakeSharedVisaInstrument):
            def __init__(self):
                super().__init__("Stanford_Research_Systems,SR830,TEST,1.0")
                self.sensitivity = app.STFMR_LOCKIN_SR830_SENSITIVITY_CODE
                self.reference_slope = 0

            def write(self, command):
                super().write(command)
                if command.startswith("SENS "):
                    self.sensitivity = int(command.split()[1])
                if command.startswith("RSLP "):
                    self.reference_slope = int(command.split()[1])

            def query(self, command):
                responses = {
                    "FMOD?": "0",
                    "HARM?": "1",
                    "LIAS?": "0",
                    "FREQ?": "317.3",
                    "SNAP? 1,2,3,4,9": "30e-6,40e-6,60e-6,53.13,317.3",
                }
                if command == "RSLP?":
                    return str(self.reference_slope)
                if command == "SENS?":
                    return str(self.sensitivity)
                if command in responses:
                    return responses[command]
                return super().query(command)

        controller = app.STFMRSR830Controller(lambda _message: None)
        fake = FakeSR830()
        controller.instrument = fake
        configured = controller.configure("TTL Falling", auto_range=True)
        first = controller.read_sample(317.3)
        second = controller.read_sample(317.3)

        self.assertEqual(configured["reference_slope"], 2)
        self.assertIn("RSLP 2", fake.commands)
        self.assertEqual(first["sensitivity_index"], 13)
        self.assertEqual(first["auto_range"], "SENS 13->14 (overload/high)")
        self.assertFalse(first["measurement_valid"])
        self.assertEqual(second["sensitivity_index"], 14)
        self.assertEqual(second["auto_range"], "")
        self.assertTrue(second["measurement_valid"])

    def test_stfmr_normal_completion_turns_off_smb100a(self):
        class FakeKepco:
            def __init__(self):
                self.zero_calls = 0

            def zero_output(self):
                self.zero_calls += 1

        class FakeSmb:
            def __init__(self):
                self.off_calls = 0

            def rf_off(self):
                self.off_calls += 1

        harness = object.__new__(app.CombinedControlApp)
        kepco = FakeKepco()
        smb = FakeSmb()
        harness.stfmr_lockin_running = True
        harness.stfmr_lockin_controllers = {"kepco": kepco, "smb": smb}
        harness.stfmr_lockin_rows = [{"field_g": 0.0}]
        harness.stfmr_lockin_frequencies_ghz = [5.0]
        harness.stfmr_lockin_status_var = FakeVariable("")
        harness.closing = False
        harness._update_stfmr_lockin_ui = lambda: None

        harness._finish_stfmr_lockin_scan()

        self.assertEqual(kepco.zero_calls, 1)
        self.assertEqual(smb.off_calls, 1)
        self.assertIn("SMB100A outputs OFF", harness.stfmr_lockin_status_var.get())

    def test_stfmr_pna_displays_and_triggers_all_selected_traces_once(self):
        class FakePNA:
            def __init__(self):
                self.commands = []
                self.data_index = 0

            def write(self, command):
                self.commands.append(command)

            def query(self, command):
                self.commands.append(command)
                if command == "CALC1:PAR:CAT?":
                    return ""
                if command in {"SOUR1:POW1?", "SOUR1:POW2?"}:
                    return "-10"
                if command == "*OPC?":
                    return "1"
                if command == "CALC1:DATA? FDATA":
                    self.data_index += 1
                    return str(-10.0 - self.data_index)
                raise AssertionError(f"unexpected query: {command}")

        controller = app.STFMRPNAFMRController(lambda _message: None)
        fake = FakePNA()
        controller.instrument = fake
        selected = ("S11", "S12", "S21", "S22")
        actual_powers = controller.configure_cw(5.0e9, selected, -10.0)
        values = controller.trigger_and_read(selected)
        self.assertEqual(actual_powers, {1: -10.0, 2: -10.0})
        self.assertEqual(set(values), set(selected))
        self.assertEqual(fake.commands.count("SENS1:SWE:MODE SING"), 1)
        for index, parameter in enumerate(selected, start=1):
            name = f"TRANSPORT_FMR_{parameter}"
            self.assertIn(
                f"DISP:WIND1:TRAC{index}:FEED '{name}'", fake.commands
            )
        self.assertIn("TRIG:SEQ:SCOP CHAN", fake.commands)
        self.assertIn("SOUR1:POW1 -10", fake.commands)
        self.assertIn("SOUR1:POW2 -10", fake.commands)

    def test_stfmr_pna_power_limit_and_source_port_mapping(self):
        controller = app.STFMRPNAFMRController(lambda _message: None)
        self.assertEqual(controller.source_ports_for(("S11", "S21")), (1,))
        self.assertEqual(controller.source_ports_for(("S12", "S22")), (2,))
        self.assertEqual(controller.source_ports_for(("S11", "S22")), (1, 2))
        with self.assertRaises(ValueError):
            controller.configure_cw(5.0e9, ("S21",), 0.01)

    def test_stfmr_fmr_scan_points_are_inclusive_and_bounded(self):
        points = app.CombinedControlApp._build_stfmr_one_way_points(
            -1.0, 1.0, 0.3
        )
        self.assertEqual(points[0], -1.0)
        self.assertEqual(points[-1], 1.0)
        self.assertTrue(all(-1.0 <= item <= 1.0 for item in points))
        with self.assertRaises(ValueError):
            app.CombinedControlApp._build_stfmr_one_way_points(
                -1.0, 1.0, 0.0
            )
        with self.assertRaises(ValueError):
            app.CombinedControlApp._build_stfmr_one_way_points(
                -11.0, 1.0, 0.1
            )

    def test_stfmr_frequency_points_are_inclusive_in_both_directions(self):
        upward = app.CombinedControlApp._build_stfmr_frequency_points(
            5.0, 10.0, 1.0
        )
        self.assertEqual(
            upward,
            [5e9, 6e9, 7e9, 8e9, 9e9, 10e9],
        )
        downward = app.CombinedControlApp._build_stfmr_frequency_points(
            10.0, 5.0, 2.0
        )
        self.assertEqual(downward, [10e9, 8e9, 6e9, 5e9])
        with self.assertRaises(ValueError):
            app.CombinedControlApp._build_stfmr_frequency_points(
                5.0, 10.0, 0.0
            )

    def test_stfmr_field_settle_rejects_transition_and_accepts_quiet_tail(self):
        checker = app.CombinedControlApp._stfmr_field_samples_stable
        self.assertFalse(checker([0.0, 120.0, 260.0]))
        self.assertFalse(checker([400.0, 410.0]))
        self.assertTrue(checker([399.2, 400.1, 399.8]))
        self.assertTrue(checker([1998.0, 2000.0, 2001.0]))

    def test_stfmr_discards_first_two_startup_points(self):
        checker = app.CombinedControlApp._stfmr_should_discard_startup_point
        self.assertTrue(checker(0))
        self.assertTrue(checker(1))
        self.assertFalse(checker(2))
        self.assertFalse(checker(120))

    def test_stfmr_progress_clamps_to_valid_scan_range(self):
        progress = FakeProgressbar()
        app.CombinedControlApp._set_stfmr_progress(progress, 3, 10)
        self.assertEqual(progress.options, {"maximum": 10, "value": 3})
        app.CombinedControlApp._set_stfmr_progress(progress, 12, 10)
        self.assertEqual(progress.options, {"maximum": 10, "value": 10})
        app.CombinedControlApp._set_stfmr_progress(None, 1, 0)

    def test_2400_uses_its_200_v_range_and_allows_105_ma(self):
        controller = app.Keithley2410Controller(lambda _message: None, model="2400")
        fake = Fake2400()
        controller.instrument = fake
        controller.compliance_a = 0.1  # Above the 2410's 21 mA, within the 2400's 105 mA.
        controller.enable_output(160.0)
        self.assertEqual(fake.voltage_range, 200.0)
        self.assertEqual(fake.ovp, 160.0, "a real OVP level on a 2400")
        self.assertEqual(controller.source_ovp_readback_v, 160.0)
        self.assertTrue(math.isclose(controller.set_voltage(160.0), 160.0))
        with self.assertRaisesRegex(ValueError, "2400 software limit ±160 V"):
            controller.set_voltage(160.0001)
        controller.output_enabled = False
        with self.assertRaisesRegex(ValueError, "±160 V"):
            controller.enable_output(200.0)
        with self.assertRaisesRegex(ValueError, "±160 V"):
            controller.apply_manual_voltage(-170.0)
        controller.compliance_a = 0.106
        with self.assertRaisesRegex(ValueError, "105 mA"):
            controller.apply_manual_voltage(30.0)
        controller.compliance_a = 1e-3
        controller.apply_manual_voltage(1.0)
        self.assertEqual(fake.voltage_range, 20.0, "20 V range at or below 20 V, as on the 2410")

    def test_2410_keeps_200_v_and_its_200_v_ovp(self):
        controller = app.Keithley2410Controller(lambda _message: None)
        fake = Fake2410()
        controller.instrument = fake
        controller.enable_output(200.0)
        self.assertEqual((fake.voltage_range, fake.ovp), (1000.0, 200.0))
        self.assertEqual(controller.source_ovp_readback_v, 200.0)
        self.assertTrue(math.isclose(controller.set_voltage(-200.0), -200.0))

    def test_an_ovp_the_model_lacks_is_refused_before_output(self):
        # Asking a 2400 for 200 V OVP selects none (210 V): the readback
        # check must catch that rather than run without a hardware limit.
        controller = app.Keithley2410Controller(lambda _message: None, model="2400")
        fake = Fake2400()
        controller.instrument = fake
        with mock.patch.dict(app.KEITHLEY_MODELS, {"2400": dict(app.KEITHLEY_MODELS["2400"], source_ovp_v=200.0)}):
            with self.assertRaisesRegex(RuntimeError, "over-voltage protection readback is too high: 210 V"):
                controller.enable_output(20.0)
        self.assertEqual(fake.output, 0, "output never turned on")

    def test_a_failed_resistance_start_cannot_skip_the_ovp(self):
        # *RST sets the OVP to none.  If the resistance start then fails, the
        # next manual voltage must set and check the OVP before OUTP ON.
        for model, fake_class, voltage, ovp in (("2400", Fake2400, 150.0, 160.0),
                                                ("2410", Fake2410, 190.0, 200.0)):
            with self.subTest(model=model):
                controller = app.Keithley2410Controller(lambda _message: None, model=model)
                fake = fake_class()
                controller.instrument = fake
                controller.apply_manual_voltage(voltage)
                controller.output_off()
                fake.errors.append('-113,"Undefined header"')
                with self.assertRaisesRegex(RuntimeError, "SCPI error"):
                    controller.initialize_resistance(1e-9)
                self.assertEqual((fake.output, fake.ovp), (0, fake.OVP_NONE))
                del fake.commands[:]
                controller.apply_manual_voltage(voltage)
                commands = [command.upper() for command in fake.commands]
                self.assertIn(f":SOUR:VOLT:PROT {ovp:g}", commands)
                self.assertLess(commands.index(f":SOUR:VOLT:PROT {ovp:g}"), commands.index(":OUTP ON"))
                self.assertEqual((fake.output, fake.ovp), (1, ovp))

    def test_an_ovp_below_the_level_asked_for_is_refused(self):
        controller = app.Keithley2410Controller(lambda _message: None, model="2400")
        fake = Fake2400()
        controller.instrument = fake
        with mock.patch.dict(app.KEITHLEY_MODELS, {"2400": dict(app.KEITHLEY_MODELS["2400"], source_ovp_v=110.0)}):
            with self.assertRaisesRegex(RuntimeError, "readback is 100 V, not the 110 V asked for"):
                controller.enable_output(20.0)
        self.assertEqual(fake.output, 0)
        with self.assertRaisesRegex(RuntimeError, "not in voltage-source mode"):
            controller.set_voltage(1.0)  # a part-done setup is not trusted

    def test_scan_sequence_follows_the_model_limit(self):
        self.assertEqual(app.build_voltage_sequence(0.0, 160.0, 80.0, 160.0)[:3], [0.0, 80.0, 160.0])
        with self.assertRaisesRegex(ValueError, "±160 V"):
            app.build_voltage_sequence(0.0, 200.0, 100.0, 160.0)
        app.build_voltage_sequence(0.0, 200.0, 100.0)  # the 2410's ±200 V by default

    def test_a_2400_driven_as_a_2410_fails_safe(self):
        controller = app.Keithley2410Controller(lambda _message: None)  # 2410 selected
        fake = Fake2400()
        controller.instrument = fake
        # Its OVP reads back as none (210 V), refused before the range is set.
        with self.assertRaisesRegex(RuntimeError, "over-voltage protection readback is too high: 210 V"):
            controller.enable_output(200.0)
        self.assertEqual(fake.output, 0, "output never turned on")
        with mock.patch.object(Fake2400, "_ovp_level", lambda self, value: abs(value)):
            # Even with an OVP readback of 200 V, the 1000 V range is refused.
            with self.assertRaisesRegex(RuntimeError, "SCPI error"):
                controller.enable_output(200.0)
        self.assertEqual(fake.output, 0, "output never turned on")

    def test_identity_must_name_the_selected_model(self):
        cases = (
            ("KEITHLEY INSTRUMENTS INC.,MODEL 2410,1234567,C34", "2410", True),
            ("KEITHLEY INSTRUMENTS INC.,MODEL 2400,4105497,C30", "2400", True),
            ("KEITHLEY INSTRUMENTS INC.,MODEL 2400-C,4105497,C30", "2400", True),
            ("KEITHLEY INSTRUMENTS INC.,MODEL 2410,1234567,C34", "2400", False),
            ("KEITHLEY INSTRUMENTS INC.,MODEL 2400,4105497,C30", "2410", False),
            ("KEITHLEY INSTRUMENTS INC.,MODEL 2410,2400123,C34", "2400", False),
            ("KEITHLEY INSTRUMENTS INC.,MODEL 2410-C,1234567,C34", "2410", True),
            ("KEITHLEY INSTRUMENTS INC.,MODEL 2400,1241056,C30", "2410", False),
            ("KEITHLEY INSTRUMENTS INC.,MODEL 2400-LV,4105497,C30", "2400", False),
            ("KEITHLEY INSTRUMENTS INC.,MODEL 2401,4105497,C30", "2400", False),
        )
        for identity, model, expected in cases:
            with self.subTest(identity=identity, model=model):
                self.assertIs(app.Keithley2410Controller.identity_matches(identity, model), expected)

    def test_model_is_fixed_while_connected(self):
        controller = app.Keithley2410Controller(lambda _message: None)
        with self.assertRaises(ValueError):
            controller.set_model("2700")
        controller.instrument = Fake2410()
        with self.assertRaises(RuntimeError):
            controller.set_model("2400")
        self.assertEqual(controller.model, "2410")

    def test_connect_checks_the_selected_model(self):
        manager = types.SimpleNamespace(open_resource=lambda resource: Fake2400())
        originals = (app.pyvisa, app.acquire_shared_visa_manager, app.release_shared_visa_manager)
        app.pyvisa = types.SimpleNamespace()
        app.acquire_shared_visa_manager = lambda: manager
        app.release_shared_visa_manager = lambda _manager: None
        try:
            controller = app.Keithley2410Controller(lambda _message: None)
            with self.assertRaisesRegex(RuntimeError, "not a Keithley 2410"):
                controller.connect("GPIB0::24::INSTR", 1e-3)
            self.assertFalse(controller.connected)
            self.assertEqual(controller.model, "2410")
            with self.assertRaisesRegex(RuntimeError, "not a Keithley 2400"):
                manager.open_resource = lambda resource: Fake2410()
                controller.connect("GPIB0::24::INSTR", 1e-3, "2400")
            self.assertEqual(controller.model, "2410", "a failed connect keeps the previous model")
            manager.open_resource = lambda resource: Fake2400()
            identity = controller.connect("GPIB0::24::INSTR", 1e-3, "2400")
            self.assertIn("MODEL 2400", identity)
            self.assertEqual(controller.model, "2400")
            with self.assertRaisesRegex(RuntimeError, "Disconnect before changing"):
                controller.connect("GPIB0::24::INSTR", 1e-3, "2410")
            self.assertTrue(controller.connected)
            self.assertEqual(controller.model, "2400")
            controller.disconnect()
        finally:
            app.pyvisa, app.acquire_shared_visa_manager, app.release_shared_visa_manager = originals

    def test_2410_high_voltage_rejects_impossible_current_compliance(self):
        controller = app.Keithley2410Controller(lambda _message: None)
        controller.instrument = Fake2410()
        controller.compliance_a = 0.1
        with self.assertRaises(ValueError):
            controller.enable_output(200.0)

    def test_duplicate_resource_is_rejected_until_release(self):
        first = object()
        second = object()
        app.acquire_instrument_lease("GPIB0::24::INSTR", first, "first")
        with self.assertRaises(RuntimeError):
            app.acquire_instrument_lease("gpib0::24::instr", second, "second")
        # The same controller may own several roles (source/meter); reusing
        # one address inside that controller must also be rejected.
        with self.assertRaises(RuntimeError):
            app.acquire_instrument_lease("GPIB0::24::INSTR", first, "other role")
        app.release_instrument_lease("GPIB0::24::INSTR", first)
        app.acquire_instrument_lease("gpib0::24::instr", second, "second")

    def test_ppms_default_endpoint_and_readback(self):
        host, port = app.PPMSMultiVuReadbackController.validate_endpoint(
            app.PPMS_DEFAULT_HOST, app.PPMS_DEFAULT_PORT
        )
        self.assertEqual(host, "192.168.137.5")
        self.assertEqual(port, 11000)
        self.assertEqual(app.PPMS_DEFAULT_BACKEND, app.PPMS_QDINSTRUMENT_BACKEND)
        self.assertEqual(app.PPMS_MULTIPYVU_DEFAULT_PORT, 5000)
        controller = app.PPMSMultiVuReadbackController(lambda _message: None)
        controller.client = FakeMultiPyVuClient()
        result = controller.read_environment()
        self.assertEqual(result["temperature_k"], 7.25)
        self.assertEqual(result["field_oe"], -1234.5)
        self.assertEqual(result["temperature_status"], "Stable")
        self.assertEqual(result["field_status"], "Holding")

    def test_ppms1_temperature_and_field_guardrails(self):
        target, rate_k_s = app.validate_ppms_temperature_command(
            300.0, 10.0, 20.0
        )
        self.assertEqual(target, 300.0)
        self.assertTrue(math.isclose(rate_k_s, 10.0))
        target, rate_k_s = app.validate_ppms_temperature_command(5.0, 1.0, 10.0)
        self.assertEqual(target, 5.0)
        self.assertTrue(math.isclose(rate_k_s, 1.0))
        with self.assertRaises(ValueError):
            app.validate_ppms_temperature_command(5.0, 1.01, 10.0)
        with self.assertRaises(ValueError):
            app.validate_ppms_temperature_command(380.01, 1.0, 300.0)
        self.assertEqual(
            app.validate_ppms_field_command(89900.0, 149.999),
            (89900.0, 149.999),
        )
        with self.assertRaises(ValueError):
            app.validate_ppms_field_command(89900.1, 100.0)
        with self.assertRaises(ValueError):
            app.validate_ppms_field_command(0.0, 150.0)

    def test_ppms1_field_scan_returns_to_start_each_loop(self):
        sequence = app.build_ppms_field_scan_sequence(-100.0, 100.0, 100.0, 2)
        targets = [row[3] for row in sequence]
        self.assertEqual(
            targets,
            [-100.0, 0.0, 100.0, 0.0, -100.0] * 2,
        )
        self.assertTrue(all(abs(value) <= 89900.0 for value in targets))

    def test_round_trip_sequence_builder_is_consistent(self):
        expected = [0.0, 0.6, 1.0, 0.6, 0.0]
        self.assertEqual(app.build_voltage_sequence(0.0, 1.0, 0.6), expected)
        self.assertEqual(
            app.build_voltage_sequence(1.0, 0.0, 0.6),
            [1.0, 0.4, 0.0, 0.4, 1.0],
        )
        self.assertEqual(
            app.build_6221_current_sequence(0.1, 0.1, 0.01),
            [0.1],
        )
        current_rows = app.build_current_scan_sequence(0.0, 1.0, 0.6, 2)
        self.assertEqual([row[3] for row in current_rows], expected * 2)

    def test_camera_orientation_and_corner_labels_follow_ccw_rotation(self):
        # The application intentionally disables the complete optional camera
        # stack when PCO/Pillow is absent.  Supply the independently available
        # NumPy module here so this pixel-orientation unit test remains
        # hardware- and PCO-SDK-independent.
        original_np = app.np
        app.np = np
        try:
            source = np.arange(6, dtype=np.uint16).reshape(2, 3)
            rotated = app.orient_camera_image(source)
            expected = np.array(
                [[2, 5], [1, 4], [0, 3]],
                dtype=np.uint16,
            )
            self.assertTrue(np.array_equal(rotated, expected))
            self.assertEqual(rotated.shape, (3, 2))
            self.assertFalse(np.shares_memory(rotated, source))
        finally:
            app.np = original_np
        self.assertEqual(
            app.CAMERA_CORNER_LABELS,
            {"nw": "4", "sw": "1", "se": "2", "ne": "3"},
        )

    def test_moke_hardware_snapshot_uses_cached_state_without_queries(self):
        harness = object.__new__(app.CombinedControlApp)
        harness.connection_var = FakeVariable("已连接：COM5")
        harness.operation_var = FakeVariable("就绪")
        harness.controller = types.SimpleNamespace(
            is_connected=True,
            port="COM5",
        )
        harness.port_var = FakeVariable("COM5")
        harness.global_current_var = FakeVariable(100)
        harness.auto_readback_var = FakeVariable(True)
        harness.raw_edit_var = FakeVariable(False)
        harness.channel_vars = {
            channel: {
                "i": FakeVariable(channel * 10),
                "m": FakeVariable("Continuous"),
                "p": FakeVariable(5),
                "g": FakeVariable(0),
                "d": FakeVariable(5),
                "f": FakeVariable(0),
            }
            for channel in app.CHANNELS
        }
        harness.magnet_connection_var = FakeVariable("已连接")
        harness.magnet_status_var = FakeVariable("就绪")
        harness.magnet_switch_var = FakeVariable("Out-of-plane")
        harness.magnet_switch_confirmed_label = "Out-of-plane"
        harness.magnet_safety_var = FakeVariable(True)
        harness.magnet_auto_poll_var = FakeVariable(False)
        harness.magnet_poll_interval_var = FakeVariable(0.5)
        harness.magnet_poll_inflight = False
        harness.magnet_poll_failures = 0
        harness.magnet_busy = False
        harness.magnet_scan_running = True
        harness.magnet_scan_active_key = "OUT"
        harness.magnet_port_vars = {
            key: FakeVariable(port)
            for key, port in app.MAGNET_DEFAULT_PORTS.items()
        }
        harness.magnet_target_vars = {
            "OUT": FakeVariable("1.000"),
            "IN": FakeVariable("0.000"),
        }
        harness.magnet_manual_step_vars = {
            "OUT": FakeVariable("0.1"),
            "IN": FakeVariable("0.1"),
        }
        harness.magnet_current_vars = {
            "OUT": FakeVariable("+1.000 A"),
            "IN": FakeVariable("+0.000 A"),
        }
        harness.magnet_field_vars = {
            "OUT": FakeVariable("+250.00 GS"),
            "IN": FakeVariable("-- GS（非当前档）"),
        }
        harness.magnet_last_currents = {"OUT": 1.0, "IN": 0.0}
        harness.magnet_channels = {}
        for key in ("OUT", "IN"):
            bop = types.SimpleNamespace(
                connected=True,
                port=app.MAGNET_DEFAULT_PORTS[f"{key}_BOP"],
                output_armed=(key == "OUT"),
            )
            gauss = types.SimpleNamespace(
                connected=True,
                port=app.MAGNET_DEFAULT_PORTS[f"{key}_GAUSS"],
            )
            harness.magnet_channels[key] = types.SimpleNamespace(
                bop=bop,
                gauss=gauss,
            )

        snapshot = harness.moke_hardware_parameter_snapshot("scan_start")
        prefix = "hardware.scan_start"
        self.assertTrue(snapshot[f"{prefix}.illumination.connected"])
        self.assertEqual(
            snapshot[f"{prefix}.illumination.channel_4.gui_current"], 40
        )
        self.assertEqual(
            snapshot[f"{prefix}.illumination.channel_1.gui_mode_code"], 1
        )
        self.assertTrue(snapshot[f"{prefix}.magnet.scan_running"])
        self.assertEqual(snapshot[f"{prefix}.magnet.scan_active_channel_key"], "OUT")
        self.assertTrue(snapshot[f"{prefix}.magnet.OUT.BOP_output_armed"])
        self.assertEqual(
            snapshot[f"{prefix}.magnet.OUT.cached_verified_current_A"], 1.0
        )

    def test_magnet_gauss_zero_closed_loop_finds_compensation_current(self):
        class FakeBop:
            connected = True
            output_armed = True

            def __init__(self):
                self.current = 0.0

            def query_current(self):
                return self.current

        class NeverStop:
            def wait(self, _seconds):
                return False

            def is_set(self):
                return False

        harness = object.__new__(app.CombinedControlApp)
        bop = FakeBop()
        harness.magnet_channels = {
            "OUT": types.SimpleNamespace(
                bop=bop,
                gauss=types.SimpleNamespace(connected=True),
            )
        }
        harness.magnet_field_zero_stop_event = NeverStop()
        harness.magnet_thread_log = lambda _message: None
        harness._write_and_verify_magnet_current = (
            lambda _key, value: setattr(bop, "current", float(value))
            or bop.current
        )
        # Simulated remanent offset: B = 200 G/A * I + 20 G.
        harness._query_gauss_with_recovery = (
            lambda _key: 200.0 * bop.current + 20.0
        )

        result = harness._zero_magnet_field_worker("OUT")

        self.assertFalse(result["cancelled"])
        self.assertTrue(math.isclose(result["current"], -0.1, abs_tol=0.001))
        self.assertLessEqual(
            abs(result["field"]), app.MAGNET_FIELD_ZERO_TOLERANCE_GS
        )

    def test_magnet_gauss_zero_rejects_unresponsive_meter_and_zeros_current(self):
        class FakeBop:
            connected = True
            output_armed = True

            def __init__(self):
                self.current = 0.25

            def query_current(self):
                return self.current

        class NeverStop:
            def wait(self, _seconds):
                return False

            def is_set(self):
                return False

        harness = object.__new__(app.CombinedControlApp)
        bop = FakeBop()
        harness.magnet_channels = {
            "OUT": types.SimpleNamespace(
                bop=bop,
                gauss=types.SimpleNamespace(connected=True),
            )
        }
        harness.magnet_field_zero_stop_event = NeverStop()
        harness.magnet_thread_log = lambda _message: None
        harness._write_and_verify_magnet_current = (
            lambda _key, value: setattr(bop, "current", float(value))
            or bop.current
        )
        harness._query_gauss_with_recovery = lambda _key: 50.0

        with self.assertRaises(app.MagnetProtocolError):
            harness._zero_magnet_field_worker("OUT")

        self.assertEqual(bop.current, 0.0)

    def test_round_trip_sequence_rejects_tiny_step_before_allocation(self):
        with self.assertRaises(ValueError):
            app.build_round_trip_values(
                0.0,
                1.0,
                1e-20,
                decimals=12,
            )

    def test_safe_close_hardware_continues_after_module_failure(self):
        harness = object.__new__(app.CombinedControlApp)
        attempted = []
        warnings = []
        method_names = (
            "safe_close_camera",
            "safe_close_led",
            "safe_close_stage",
            "safe_close_magnets",
            "safe_close_keithley",
            "safe_close_nonlocal_vi",
            "safe_close_k6221",
            "safe_close_ppms",
            "safe_close_motor",
        )

        for method_name in method_names:
            def close_step(name=method_name):
                attempted.append(name)
                if name == "safe_close_stage":
                    raise RuntimeError("simulated stage shutdown failure")

            setattr(harness, method_name, close_step)
        harness.append_log = warnings.append

        app.CombinedControlApp.safe_close_hardware(harness)

        self.assertEqual(attempted, list(method_names))
        self.assertTrue(any("simulated stage" in text for text in warnings))

    def test_ui_queue_drain_is_time_sliced(self):
        class FakeRoot:
            def __init__(self):
                self.after_calls = []

            @staticmethod
            def winfo_exists():
                return True

            def after(self, delay_ms, callback):
                self.after_calls.append((delay_ms, callback))

        harness = object.__new__(app.CombinedControlApp)
        harness.root = FakeRoot()
        harness.ui_queue = app.queue.Queue()
        harness.closing = False
        completed = []
        callback_count = app.UI_QUEUE_MAX_CALLBACKS_PER_TICK + 5
        for index in range(callback_count):
            harness.ui_queue.put(lambda value=index: completed.append(value))

        app.CombinedControlApp.drain_ui_queue(harness)

        self.assertGreater(len(completed), 0)
        self.assertLessEqual(
            len(completed), app.UI_QUEUE_MAX_CALLBACKS_PER_TICK
        )
        self.assertFalse(harness.ui_queue.empty())
        self.assertEqual(
            harness.root.after_calls[-1][0], app.UI_QUEUE_ACTIVE_DELAY_MS
        )

    def test_ppms1_transport_allows_one_sr830_at_address_10(self):
        settings = app.validate_ppms_transport_scan_settings(
            0.001, 13.7, 50.0, 1, 20000, ("830_a",)
        )
        self.assertTrue(math.isclose(
            settings["peak_a"], math.sqrt(2.0) * 1e-6
        ))
        self.assertEqual(settings["active_lockins"], ("830_a",))
        with self.assertRaises(ValueError):
            app.validate_ppms_transport_scan_settings(80.0, 13.7, 50.0, 1, 1)
        with self.assertRaises(ValueError):
            app.validate_ppms_transport_scan_settings(
                0.001, 13.7, 50.0, 1, 1, ()
            )
        self.assertEqual(
            app.CombinedControlApp._ppms_sr830_address("GPIB0::10::INSTR"),
            10,
        )
        with self.assertRaises(ValueError):
            app.CombinedControlApp._ppms_sr830_address("GPIB0::11::INSTR")

        harness = object.__new__(app.CombinedControlApp)
        source = app.SafeVisaIdentityController(
            lambda _message: None,
            "Keithley 6221",
            "6221",
            ("SOUR:WAVE:ABOR", "CURR 0", "OUTP OFF"),
        )
        lockin_a = app.SafeVisaIdentityController(
            lambda _message: None, "SR830 槽 A", "SR830"
        )
        lockin_b = app.SafeVisaIdentityController(
            lambda _message: None, "SR830 槽 B", "SR830"
        )
        source.instrument = Fake6221()
        lockin_a.instrument = FakeSR830(10e-6)
        lockin_a.resource_name = "GPIB0::10::INSTR"
        harness.ppms_instrument_controllers = {
            "6221": source,
            "830_a": lockin_a,
            "830_b": lockin_b,
        }
        harness.ppms_field_scan_stop_event = app.threading.Event()
        harness.post_ui = lambda callback: callback()
        harness.ppms_transport_status_var = types.SimpleNamespace(
            set=lambda _value: None
        )
        harness.thread_log = lambda _message: None
        # The SR830 autorange step refreshes the reference indicator; the
        # bare harness has no widgets, so an empty registry makes it a no-op.
        harness.instrument_connection_indicators = {}
        harness.ppms_sr830_status_vars = {}

        harness._configure_ppms_transport_scan(settings)
        self.assertEqual(source.instrument.output, 1)
        self.assertEqual(lockin_a.instrument.harmonic, 1)
        self.assertIsNone(lockin_b.instrument)
        sample = harness._read_ppms_transport_sample(13.7, ("830_a",))
        self.assertTrue(math.isclose(sample["sr830_10_r_v"], 10e-6))
        self.assertTrue(sample["sr830_10_reference_valid"])
        self.assertNotIn("sr830_8_r_v", sample)
        self.assertNotIn("sr830_9_r_v", sample)
        harness._stop_ppms_transport_source()
        self.assertEqual(source.instrument.output, 0)

    def test_qdinstrument_pythonnet_readback_shapes(self):
        value, status = app.PPMSQDInstrumentReadbackController._normalize_readback(
            (0, 7.25, "Stable"), "温度"
        )
        self.assertEqual(value, 7.25)
        self.assertEqual(status, "Stable")
        value, status = app.PPMSQDInstrumentReadbackController._normalize_readback(
            (-1234.5, "Holding"), "磁场"
        )
        self.assertEqual(value, -1234.5)
        self.assertEqual(status, "Holding")
        with self.assertRaises(RuntimeError):
            app.PPMSQDInstrumentReadbackController._normalize_readback(
                (1, 7.25, "Error"), "温度"
            )

    def test_qdinstrument_explicit_dll_discovery(self):
        with tempfile.TemporaryDirectory() as directory:
            dll = Path(directory) / "QDInstrument.dll"
            dll.write_bytes(b"offline-test-placeholder")
            matches = (
                app.PPMSQDInstrumentReadbackController.discover_dll_paths(
                    str(dll)
                )
            )
            self.assertTrue(matches)
            self.assertEqual(matches[0], dll.resolve())

    def test_qdinstrument_loader_can_use_dotnet_assembly_name(self):
        class FakeClr:
            def __init__(self):
                self.references = []

            def AddReference(self, reference):
                self.references.append(reference)

        controller = app.PPMSQDInstrumentReadbackController
        original_discover = controller.__dict__["discover_dll_paths"]
        original_import = controller.__dict__["_import_qdinstrument_api"]
        fake_clr = FakeClr()
        try:
            controller.discover_dll_paths = classmethod(
                lambda cls, *args, **kwargs: ()
            )
            controller._import_qdinstrument_api = staticmethod(
                lambda: ("QDInstrumentBase", "QDInstrumentFactory")
            )
            base, factory, source = controller._load_qdinstrument_api(fake_clr)
        finally:
            controller.discover_dll_paths = original_discover
            controller._import_qdinstrument_api = original_import
        self.assertEqual(base, "QDInstrumentBase")
        self.assertEqual(factory, "QDInstrumentFactory")
        self.assertEqual(source, ".NET auto-resolved: QDInstrument")
        self.assertEqual(fake_clr.references, ["QDInstrument"])

    def test_qdinstrument_ref_readback_uses_reflected_argument_types(self):
        class FakeValueType:
            IsByRef = False
            IsEnum = False
            IsValueType = True
            FullName = "System.Double"

        class FakeRefType:
            IsByRef = True
            FullName = "System.Double&"

            @staticmethod
            def GetElementType():
                return FakeValueType

        class FakeParameter:
            ParameterType = FakeRefType

        class FakeMethod:
            Name = "GetTemperature"

            @staticmethod
            def GetParameters():
                return [FakeParameter(), FakeParameter()]

            @staticmethod
            def Invoke(_instrument, arguments):
                arguments[0] = 7.25
                arguments[1] = "Stable"
                return 0

        class FakeRuntimeType:
            @staticmethod
            def GetMethods():
                return [FakeMethod()]

        class FakeInstrument:
            @staticmethod
            def GetType():
                return FakeRuntimeType()

        class FakeArray:
            @classmethod
            def __class_getitem__(cls, _item):
                return lambda count: [None] * count

        fake_system = types.ModuleType("System")
        fake_system.Array = FakeArray
        fake_system.Object = object
        fake_system.Enum = types.SimpleNamespace(
            ToObject=lambda _value_type, value: value
        )
        fake_system.Activator = types.SimpleNamespace(
            CreateInstance=lambda _value_type: 0.0
        )
        original_system = sys.modules.get("System")
        sys.modules["System"] = fake_system
        try:
            raw = app.PPMSQDInstrumentReadbackController._invoke_ref_readback(
                FakeInstrument(), "GetTemperature"
            )
        finally:
            if original_system is None:
                sys.modules.pop("System", None)
            else:
                sys.modules["System"] = original_system
        self.assertEqual(raw, (0, 7.25, "Stable"))
        self.assertEqual(
            app.PPMSQDInstrumentReadbackController._normalize_readback(
                raw, "温度"
            ),
            (7.25, "Stable"),
        )

    def test_qdinstrument_setfield_uses_reflected_value_types(self):
        captured = []

        class FakeValueType:
            IsByRef = False
            IsEnum = False
            IsValueType = True
            FullName = "System.Double"

            def __call__(self, value):
                return float(value)

        class FakeEnumType:
            IsByRef = False
            IsEnum = True
            IsValueType = True
            FullName = "QD.FieldMode"

        class FakeParameter:
            def __init__(self, value_type):
                self.ParameterType = value_type

        class FakeMethod:
            Name = "SetField"

            @staticmethod
            def GetParameters():
                return [
                    FakeParameter(FakeValueType()),
                    FakeParameter(FakeValueType()),
                    FakeParameter(FakeEnumType()),
                    FakeParameter(FakeEnumType()),
                ]

            @staticmethod
            def Invoke(_instrument, arguments):
                captured.extend(arguments)
                return 0

        class FakeRuntimeType:
            @staticmethod
            def GetMethods():
                return [FakeMethod()]

        class FakeInstrument:
            @staticmethod
            def GetType():
                return FakeRuntimeType()

        class FakeArray:
            @classmethod
            def __class_getitem__(cls, _item):
                return lambda count: [None] * count

        fake_system = types.ModuleType("System")
        fake_system.Array = FakeArray
        fake_system.Object = object
        fake_system.Enum = types.SimpleNamespace(
            ToObject=lambda _value_type, value: int(value)
        )
        fake_system.Convert = types.SimpleNamespace(
            ChangeType=lambda value, _value_type, _culture: float(value)
        )
        fake_system.Globalization = types.SimpleNamespace(
            CultureInfo=types.SimpleNamespace(InvariantCulture=object())
        )
        original_system = sys.modules.get("System")
        sys.modules["System"] = fake_system
        try:
            result = app.PPMSQDInstrumentReadbackController._invoke_typed_command(
                FakeInstrument(), "SetField", (1000.0, 100.0, 1, 1)
            )
            FakeMethod.ReturnType = types.SimpleNamespace(FullName="System.Boolean")
            FakeMethod.Invoke = staticmethod(lambda _instrument, _arguments: True)
            self.assertIs(
                app.PPMSQDInstrumentReadbackController._invoke_typed_command(
                    FakeInstrument(), "SetField", (1000.0, 100.0, 1, 1)
                ), True,
            )
            FakeMethod.Invoke = staticmethod(lambda _instrument, _arguments: False)
            with self.assertRaisesRegex(RuntimeError, "Boolean False"):
                app.PPMSQDInstrumentReadbackController._invoke_typed_command(
                    FakeInstrument(), "SetField", (1000.0, 100.0, 1, 1)
                )
            FakeMethod.ReturnType = types.SimpleNamespace(FullName="System.Int32")
            FakeMethod.Invoke = staticmethod(lambda _instrument, _arguments: 1)
            with self.assertRaisesRegex(RuntimeError, "may have taken effect"):
                app.PPMSQDInstrumentReadbackController._invoke_typed_command(
                    FakeInstrument(), "SetField", (1000.0, 100.0, 1, 1)
                )
        finally:
            if original_system is None:
                sys.modules.pop("System", None)
            else:
                sys.modules["System"] = original_system
        self.assertEqual(result, 0)
        self.assertEqual(captured, [1000.0, 100.0, 1, 1])

    def test_legacy_labone_bridge_source_still_compiles(self):
        # The embedded bridge is launched by an older Python/LabOne runtime;
        # compile it separately because normal py_compile only sees a string.
        compile(app._MFLI_BRIDGE_SOURCE, "<mfli_bridge>", "exec")
        self.assertIn(
            'requested_imp50 = int(request.get("input_imp50", 0))',
            app._MFLI_BRIDGE_SOURCE,
        )
        self.assertIn(
            "daq.setInt(imp50_node, requested_imp50)",
            app._MFLI_BRIDGE_SOURCE,
        )
        self.assertIn(
            "daq.setDouble(scaling_node, requested_scaling)",
            app._MFLI_BRIDGE_SOURCE,
        )
        self.assertIn('"actual_scaling"', app._MFLI_BRIDGE_SOURCE)

    def test_nice_linear_ticks_keep_rolling_axes_readable(self):
        axis_min, axis_max, ticks, step = app.nice_linear_ticks(
            3.38,
            23.4,
            expand_limits=False,
        )
        self.assertEqual((axis_min, axis_max), (3.38, 23.4))
        self.assertEqual(step, 5.0)
        self.assertEqual(ticks, [5.0, 10.0, 15.0, 20.0])

    def test_nice_linear_ticks_expand_small_readback_axis(self):
        axis_min, axis_max, ticks, step = app.nice_linear_ticks(
            0.0,
            0.00108,
            include_zero=True,
        )
        self.assertEqual(axis_min, 0.0)
        self.assertTrue(axis_max >= 0.00108)
        self.assertEqual(step, 0.00025)
        self.assertEqual(ticks[:3], [0.0, 0.00025, 0.0005])

    def test_stfmr_field_magnitude_axis_starts_at_zero(self):
        axis_min, axis_max, ticks, step = app.nice_magnitude_ticks(
            [-780.0, -300.0, 0.0, 240.0]
        )
        self.assertEqual(axis_min, 0.0)
        self.assertEqual(ticks[0], 0.0)
        self.assertGreaterEqual(axis_max, 780.0)
        self.assertGreater(step, 0.0)

    def test_6221_dc_mode_uses_fixed_current_not_waveform(self):
        controller = app.NonLocalACController(lambda _message: None)
        controller.source = Fake6221()
        controller.nanovoltmeter = Fake2182()
        frequency, readbacks = controller.configure(
            0.0,
            13.7,
            50.0,
            {},
            use_nanovoltmeter=True,
            output_mode="DC",
        )
        self.assertTrue(math.isnan(frequency))
        self.assertEqual(readbacks["2182"]["measurement_mode"],
                         "DC voltage (not phase-sensitive)")
        self.assertTrue(math.isclose(controller.set_current(-0.1, frequency),
                                     -0.1))
        self.assertEqual(controller.source.output, 1)
        self.assertNotIn("SOUR:WAVE:INIT", controller.source.commands)

    def test_6221_ac_mode_converts_gui_rms_to_remote_peak(self):
        controller = app.NonLocalACController(lambda _message: None)
        controller.source = Fake6221()
        controller.nanovoltmeter = Fake2182()
        frequency, _readbacks = controller.configure(
            0.1,
            13.7,
            50.0,
            {},
            use_nanovoltmeter=True,
            output_mode="AC",
        )
        expected_peak_a = math.sqrt(2.0) * 0.1e-3
        self.assertTrue(math.isclose(frequency, 13.7))
        self.assertTrue(math.isclose(controller.source.wave_amplitude,
                                     expected_peak_a))

    def test_6221_standalone_continuous_ac_readback_and_compliance(self):
        controller = app.Keithley6221Controller(lambda _message: None)
        fake = Fake6221()
        controller.instrument = fake
        state = controller.set_continuous_output(
            "AC", 10e-6, 13.7, 50.0
        )
        self.assertEqual(state["mode"], "AC")
        self.assertTrue(state["output_on"])
        self.assertFalse(state["in_compliance"])
        self.assertTrue(math.isclose(state["current_a"], 10e-6))
        self.assertTrue(math.isclose(
            fake.wave_amplitude, math.sqrt(2.0) * 10e-6
        ))
        self.assertIn("SOUR:WAVE:DUR:CYCL INF", fake.commands)

        fake.measurement_condition = 8
        state = controller.read_output_state()
        self.assertTrue(state["in_compliance"])
        self.assertEqual(state["measurement_condition"], 8)

    def test_6221_standalone_continuous_dc_uses_signed_fixed_current(self):
        controller = app.Keithley6221Controller(lambda _message: None)
        fake = Fake6221()
        controller.instrument = fake
        state = controller.set_continuous_output(
            "DC", -25e-6, 0.0, 20.0
        )
        self.assertEqual(state["mode"], "DC")
        self.assertTrue(state["output_on"])
        self.assertTrue(math.isclose(state["current_a"], -25e-6))
        self.assertNotIn("SOUR:WAVE:INIT", fake.commands)

    def test_6221_pulse_returns_verified_readback_state(self):
        controller = app.Keithley6221Controller(lambda _message: None)
        fake = Fake6221()
        fake.measurement_condition = 8
        controller.instrument = fake
        state = controller.set_dc_pulse(1e-3, 10e-6, 50.0, 5)
        self.assertEqual(state["mode"], "PULSE")
        self.assertTrue(state["output_on"])
        self.assertTrue(state["in_compliance"])
        self.assertEqual(state["measurement_condition"], 8)
        self.assertTrue(math.isclose(state["current_a"], 1e-3))
        self.assertTrue(math.isclose(state["pulse_width_s"], 10e-6))
        self.assertEqual(state["pulse_count"], 5)
        self.assertFalse(state["completed_before_readback"])
        self.assertIn("SOUR:WAVE:EXTR OFF", fake.commands)
        self.assertIsInstance(state["sample_monotonic"], float)

    def test_6221_fast_finite_pulse_may_complete_before_output_query(self):
        controller = app.Keithley6221Controller(lambda _message: None)
        fake = Fake6221()
        fake.auto_complete_waveform = True
        controller.instrument = fake

        state = controller.set_dc_pulse(1e-3, 10e-6, 50.0, 1)

        self.assertEqual(state["mode"], "PULSE")
        self.assertEqual(state["pulse_count"], 1)
        self.assertFalse(state["output_on"])
        self.assertTrue(state["completed_before_readback"])
        self.assertFalse(controller.output_enabled)
        self.assertEqual(controller.output_mode, "OFF")
        self.assertTrue(math.isclose(state["current_a"], 1e-3))

    def test_6221_pulse_cycle_mismatch_is_still_rejected(self):
        controller = app.Keithley6221Controller(lambda _message: None)
        fake = Fake6221()
        fake.reported_wave_cycles = 2
        controller.instrument = fake

        with self.assertRaisesRegex(RuntimeError, "pulse count readback mismatch"):
            controller.set_dc_pulse(1e-3, 10e-6, 50.0, 1)
        self.assertFalse(controller.output_enabled)

    def test_6221_roi_scan_uses_one_pulse_interval_and_raw16_snapshot(self):
        class FakePulseController:
            def __init__(self):
                self.calls = []
                self.off_count = 0

            def set_dc_pulse(self, current_a, width_s, compliance_v, count):
                self.calls.append((current_a, width_s, compliance_v, count))
                return {
                    "current_a": current_a,
                    "pulse_width_s": width_s,
                    "pulse_count": count,
                    "total_time_s": 0.0,
                }

            def output_off(self):
                self.off_count += 1

        harness = object.__new__(app.CombinedControlApp)
        harness.k6221_controller = FakePulseController()
        harness.k6221_data_lock = app.threading.Lock()
        harness.k6221_stop_event = app.threading.Event()
        harness.k6221_scan_start_time = app.time.time()
        harness.k6221_raw_rows = []
        harness.k6221_step_rows = []
        harness.k6221_current_ma = 0.0
        harness.k6221_pulse_active = False
        harness.k6221_output_var = FakeVariable("")
        harness.k6221_status_var = FakeVariable("")
        raw = np.arange(12, dtype=np.uint16).reshape(3, 4)
        harness.latest_k6221_moke_snapshot = lambda: (raw.copy(), 12.5)
        harness._wait_k6221_pulse_with_readback = lambda _state: False
        harness._publish_k6221_off_state = lambda: None
        harness._redraw_k6221_plot = lambda: None
        harness.post_ui = lambda callback: callback()
        saved = []
        original_save = app.save_raw_u16_tiff
        app.save_raw_u16_tiff = (
            lambda image, path: saved.append((image.copy(), Path(path)))
        )
        try:
            with tempfile.TemporaryDirectory() as directory:
                harness._k6221_scan_worker(
                    [0.5], 10e-6, 50.0, 0.0, Path(directory)
                )
        finally:
            app.save_raw_u16_tiff = original_save

        self.assertEqual(
            harness.k6221_controller.calls,
            [(0.5e-3, 10e-6, 50.0, 1)],
        )
        self.assertEqual(len(harness.k6221_step_rows), 1)
        self.assertEqual(
            harness.k6221_step_rows[0],
            (0.5, 12.5, 0.0, 1, 10.0, 0.0),
        )
        self.assertEqual(harness.k6221_raw_rows[-1][3], "after_pulse")
        self.assertEqual(len(saved), 1)
        self.assertEqual(saved[0][0].dtype, np.uint16)
        self.assertTrue(np.array_equal(saved[0][0], raw))
        self.assertIn("MOKE_raw16", saved[0][1].name)

    def test_6221_fixed_pulse_train_records_roi_by_pulse_number(self):
        class FakePulseController:
            def __init__(self):
                self.calls = []

            def set_dc_pulse(self, current_a, width_s, compliance_v, count):
                self.calls.append((current_a, width_s, compliance_v, count))
                return {"current_a": current_a}

            def output_off(self):
                pass

        harness = object.__new__(app.CombinedControlApp)
        harness.k6221_controller = FakePulseController()
        harness.k6221_data_lock = app.threading.Lock()
        harness.k6221_stop_event = app.threading.Event()
        harness.k6221_scan_start_time = app.time.time()
        harness.k6221_train_raw_rows = []
        harness.k6221_train_step_rows = []
        harness.k6221_current_ma = 0.0
        harness.k6221_output_var = FakeVariable("")
        harness.k6221_status_var = FakeVariable("")
        harness._wait_k6221_pulse_with_readback = lambda _state: False
        harness._publish_k6221_off_state = lambda: None
        harness._redraw_k6221_plot = lambda: None
        harness.post_ui = lambda callback: callback()
        raw = np.arange(12, dtype=np.uint16).reshape(3, 4)
        harness._collect_k6221_post_pulse_roi = (
            lambda _interval: (raw.copy(), 7.5, 0.25, 4, False)
        )

        harness._k6221_pulse_train_worker(
            2.0, 3, 20e-6, 50.0, 0.5, None
        )

        self.assertEqual(
            harness.k6221_controller.calls,
            [(2e-3, 20e-6, 50.0, 1)] * 3,
        )
        self.assertEqual(
            [row[0] for row in harness.k6221_train_step_rows],
            [1, 2, 3],
        )
        self.assertEqual(
            harness.k6221_train_step_rows[-1],
            (3, 7.5, 0.25, 4, 2.0, 20.0, 0.5),
        )

    def test_6221_post_pulse_window_averages_camera_roi_samples(self):
        harness = object.__new__(app.CombinedControlApp)
        harness.k6221_data_lock = app.threading.Lock()
        harness.k6221_scan_start_time = app.time.time() - 2.0
        harness.k6221_scan_kind = "pulse_train"
        harness.k6221_active_pulse_index = 4
        harness.k6221_train_raw_rows = []

        class SamplingEvent:
            def wait(self, _interval):
                elapsed = app.time.time() - harness.k6221_scan_start_time
                with harness.k6221_data_lock:
                    for value in (1.0, 2.0, 3.0):
                        harness.k6221_train_raw_rows.append(
                            (elapsed, 4, 1.0, value, "interval")
                        )
                return False

        harness.k6221_stop_event = SamplingEvent()
        raw = np.arange(6, dtype=np.uint16).reshape(2, 3)
        harness.latest_k6221_moke_snapshot = lambda: (raw.copy(), 99.0)

        snapshot, mean_value, std_value, count, used_fallback = (
            harness._collect_k6221_post_pulse_roi(0.25)
        )

        self.assertTrue(np.array_equal(snapshot, raw))
        self.assertEqual(mean_value, 2.0)
        self.assertTrue(math.isclose(std_value, math.sqrt(2.0 / 3.0)))
        self.assertEqual(count, 3)
        self.assertFalse(used_fallback)


if __name__ == "__main__":
    unittest.main()
