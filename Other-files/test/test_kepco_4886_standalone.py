from __future__ import annotations

import importlib.util
import pathlib
import unittest


SOURCE = pathlib.Path(__file__).resolve().parents[1] / "KEPCO_4886_control.py"
if not SOURCE.exists():
    # The standalone KEPCO script is not part of this folder any more; the
    # 4886 logic under test lives in Transport_all_NH_dev.py and is covered
    # by test_instrument_safety_logic.py.
    raise unittest.SkipTest(f"standalone script not present: {SOURCE}")
SPEC = importlib.util.spec_from_file_location("kepco_4886_standalone", SOURCE)
module = importlib.util.module_from_spec(SPEC)
assert SPEC.loader is not None
SPEC.loader.exec_module(module)


class FakeInstrument:
    def __init__(self, replies=()):
        self.timeout = None
        self.write_termination = "unchanged"
        self.read_termination = "unchanged"
        self.send_end = False
        self.writes = []
        self.replies = list(replies)
        self.closed = False

    def write_raw(self, payload):
        self.writes.append(bytes(payload))
        return len(payload)

    def read_raw(self):
        return self.replies.pop(0)

    def close(self):
        self.closed = True


class FailAfterEnableInstrument(FakeInstrument):
    def write_raw(self, payload):
        raw = bytes(payload)
        self.writes.append(raw)
        if raw == b"CURR 1.000000":
            raise RuntimeError("simulated final setpoint failure")
        return len(raw)


class FakePNA:
    def __init__(self):
        self.timeout = None
        self.write_termination = None
        self.read_termination = None
        self.writes = []
        self.queries = []
        self.closed = False

    def write(self, command):
        self.writes.append(command)

    def query(self, command):
        self.queries.append(command)
        if command == "*IDN?":
            return "Keysight Technologies,N5242B,TEST,A.01\n"
        if command == "CALC1:PAR:CAT?":
            return '"ExistingS21,S21"\n'
        if command == "*OPC?":
            return "1\n"
        if command == "CALC1:DATA? FDATA":
            return "-12.345\n"
        raise AssertionError(command)

    def close(self):
        self.closed = True


class FakeManager:
    def __init__(self):
        self.resources = {
            "GPIB0::6::INSTR": FakeInstrument(),
            "GPIB0::12::INSTR": FakeInstrument((b"1\r\n", b"+1.234E+02\r\n")),
            "GPIB0::16::INSTR": FakePNA(),
        }
        self.close_calls = 0

    def open_resource(self, resource):
        return self.resources[resource]

    def close(self):
        self.close_calls += 1


class Standalone4886Tests(unittest.TestCase):
    def test_kepco_open_is_zero_transaction_and_apply_matches_trace(self):
        manager = FakeManager()
        controller = module.Kepco4886WriteOnly()
        controller.connect("GPIB0::6::INSTR", manager=manager)
        instrument = manager.resources["GPIB0::6::INSTR"]
        self.assertEqual(instrument.writes, [])
        self.assertIsNone(instrument.write_termination)
        self.assertIsNone(instrument.read_termination)
        self.assertTrue(instrument.send_end)

        commands = controller.apply("CURR", -1.0, 10.0)
        self.assertEqual(commands, [
            "FUNC:MODE CURR",
            "CURR 0;VOLT 10",
            "VOLT:RANG:AUTO 1;CURR:RANG:AUTO 1",
            "OUTP ON",
            "CURR -1.000000",
        ])
        self.assertEqual(instrument.writes, [item.encode("ascii") for item in commands])
        controller.disconnect()
        self.assertTrue(instrument.closed)
        self.assertEqual(manager.close_calls, 0)

    def test_lakeshore_475_reads_unit_and_field_without_touching_kepco(self):
        manager = FakeManager()
        monitor = module.LakeShore475Monitor()
        monitor.connect("GPIB0::12::INSTR", manager)
        field, unit = monitor.read_field()
        gauss = manager.resources["GPIB0::12::INSTR"]
        kepco = manager.resources["GPIB0::6::INSTR"]
        self.assertEqual(gauss.writes, [b"UNIT?", b"RDGFIELD?"])
        self.assertEqual(field, 123.4)
        self.assertEqual(unit, "G")
        self.assertEqual(kepco.writes, [])

    def test_failure_after_output_on_attempts_zero_and_off(self):
        manager = FakeManager()
        manager.resources["GPIB0::6::INSTR"] = FailAfterEnableInstrument()
        controller = module.Kepco4886WriteOnly()
        controller.connect("GPIB0::6::INSTR", manager=manager)
        with self.assertRaisesRegex(RuntimeError, "final setpoint failure"):
            controller.apply("CURR", 1.0, 5.0)
        writes = manager.resources["GPIB0::6::INSTR"].writes
        self.assertEqual(writes[-2:], [b"CURR 0.000000", b"OUTP OFF"])
        self.assertFalse(controller.output_owned)

    def test_current_scan_points_are_inclusive_in_both_directions(self):
        self.assertEqual(module.build_scan_points(-0.1, 0.1, 0.1), [-0.1, 0.0, 0.1])
        self.assertEqual(module.build_scan_points(0.1, -0.1, 0.1), [0.1, 0.0, -0.1])

    def test_pna_cw_setup_and_selected_s_parameter_read(self):
        manager = FakeManager()
        pna = module.KeysightPNAFMR()
        identity = pna.connect("GPIB0::16::INSTR", manager)
        self.assertIn("N5242", identity)
        pna.configure_cw(5e9, ("S11", "S21"))
        values = pna.trigger_and_read(("S11", "S21"))
        instrument = manager.resources["GPIB0::16::INSTR"]
        self.assertIn("SENS1:FREQ:CW 5000000000", instrument.writes)
        self.assertIn("SENS1:SWE:TYPE CW", instrument.writes)
        self.assertIn("CALC1:PAR:DEF:EXT 'TRANSPORT_FMR_S11','S11'", instrument.writes)
        self.assertIn("CALC1:PAR:DEF:EXT 'TRANSPORT_FMR_S21','S21'", instrument.writes)
        self.assertIn("DISP:WIND1:TRAC1:FEED 'TRANSPORT_FMR_S11'", instrument.writes)
        self.assertIn("DISP:WIND1:TRAC2:FEED 'TRANSPORT_FMR_S21'", instrument.writes)
        self.assertIn("TRIG:SEQ:SOUR IMM", instrument.writes)
        self.assertIn("TRIG:SEQ:SCOP CHAN", instrument.writes)
        self.assertIn("SENS1:SWE:TRIG:MODE CHAN", instrument.writes)
        self.assertEqual(instrument.writes.count("SENS1:SWE:MODE SING"), 1)
        self.assertEqual(values, {"S11": -12.345, "S21": -12.345})

    def test_pna_all_four_parameters_are_displayed_before_one_channel_sweep(self):
        manager = FakeManager()
        pna = module.KeysightPNAFMR()
        pna.connect("GPIB0::16::INSTR", manager)
        pna.configure_cw(10e9, module.S_PARAMETERS)
        values = pna.trigger_and_read(module.S_PARAMETERS)
        instrument = manager.resources["GPIB0::16::INSTR"]
        for trace_number, parameter in enumerate(module.S_PARAMETERS, start=1):
            self.assertIn(
                f"DISP:WIND1:TRAC{trace_number}:FEED 'TRANSPORT_FMR_{parameter}'",
                instrument.writes,
            )
        self.assertEqual(instrument.writes.count("SENS1:SWE:MODE SING"), 1)
        self.assertEqual(set(values), set(module.S_PARAMETERS))

    def test_pna_rejects_unknown_or_empty_measurement_selection(self):
        pna = module.KeysightPNAFMR()
        with self.assertRaisesRegex(ValueError, "at least one"):
            pna.ensure_measurements(())
        with self.assertRaisesRegex(ValueError, "S33"):
            pna.ensure_measurements(("S33",))


if __name__ == "__main__":
    unittest.main()
