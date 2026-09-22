"""Offline regression tests for instrument safety logic (no hardware needed)."""

import math
import sys
import tempfile
import types
import unittest
from pathlib import Path

import numpy as np


PROJECT = Path(__file__).resolve().parents[1]
if str(PROJECT) not in sys.path:
    sys.path.insert(0, str(PROJECT))

import Magneto_Electro_Optical_Transport_V3 as app


class FakeVariable:
    def __init__(self, value):
        self.value = value

    def get(self):
        return self.value


class Fake2410:
    def __init__(self):
        self.commands = []
        self.voltage = 0.0
        self.voltage_range = 20.0
        self.ovp = 200.0
        self.output = 0

    def write(self, command):
        self.commands.append(command)
        upper = command.upper()
        if upper.startswith(":SOUR:VOLT:RANG "):
            self.voltage_range = float(command.rsplit(" ", 1)[1])
        elif upper.startswith(":SOUR:VOLT:PROT "):
            self.ovp = float(command.rsplit(" ", 1)[1])
        elif upper.startswith(":SOUR:VOLT "):
            self.voltage = float(command.rsplit(" ", 1)[1])
        elif upper == ":OUTP ON":
            self.output = 1
        elif upper == ":OUTP OFF":
            self.output = 0

    def query(self, command):
        upper = command.upper()
        if upper == "SYST:ERR?":
            return '0,"No error"'
        if upper == ":SOUR:VOLT:RANG?":
            return str(self.voltage_range)
        if upper == ":SOUR:VOLT:PROT?":
            return str(self.ovp)
        if upper == ":SOUR:VOLT?":
            return str(self.voltage)
        if upper == ":OUTP?":
            return str(self.output)
        raise AssertionError(f"unexpected query: {command}")


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
            self.output = 1
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
        self.reference_slope = 2
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


class FakeMultiPyVuClient:
    def get_temperature(self):
        return 7.25, "Stable"

    def get_field(self):
        return -1234.5, "Holding"


class InstrumentSafetyTests(unittest.TestCase):
    def setUp(self):
        app._INSTRUMENT_LEASES.clear()

    def tearDown(self):
        app._INSTRUMENT_LEASES.clear()

    def test_release_identity_and_self_test_entrypoint(self):
        self.assertEqual(app.APP_VERSION, "V3")
        self.assertEqual(app.APPLICATION_NAME, "Magneto-Electro-Optical Transport")
        self.assertTrue(
            app.MAIN_WINDOW_TITLE.endswith("-NH & Prof Chen's group")
        )
        self.assertTrue(
            app.CAMERA_WINDOW.endswith("-NH & Prof Chen's group")
        )
        self.assertEqual(app.PCO_DLL_SELF_TEST_SWITCH, "--pco-dll-self-test")

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
        self.assertEqual(host, "192.168.137.105")
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
        self.assertTrue(math.isclose(rate_k_s, 10.0 / 60.0))
        target, rate_k_s = app.validate_ppms_temperature_command(5.0, 1.0, 10.0)
        self.assertEqual(target, 5.0)
        self.assertTrue(math.isclose(rate_k_s, 1.0 / 60.0))
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
                "m": FakeVariable("连续"),
                "p": FakeVariable(5),
                "g": FakeVariable(0),
                "d": FakeVariable(5),
                "f": FakeVariable(0),
            }
            for channel in app.CHANNELS
        }
        harness.magnet_connection_var = FakeVariable("已连接")
        harness.magnet_status_var = FakeVariable("就绪")
        harness.magnet_switch_var = FakeVariable("面外")
        harness.magnet_switch_confirmed_label = "面外"
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
            "safe_close_nonlocal",
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
        self.assertEqual(source, ".NET 自动解析：QDInstrument")
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
        self.assertIsInstance(state["sample_monotonic"], float)


if __name__ == "__main__":
    unittest.main()
