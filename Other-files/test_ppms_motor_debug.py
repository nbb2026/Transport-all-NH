"""Offline only: fake .NET methods; never load a vendor DLL or open a device."""
import importlib.util
import math
from pathlib import Path
import sys
import threading
import types
import unittest
from unittest.mock import patch

source = Path(__file__).resolve().parents[1] / "Transport_all.py"
spec = importlib.util.spec_from_file_location("transport_motor_test", source)
app = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = app
spec.loader.exec_module(app)


class Type:
    def __init__(self, name, enum=False, ref=False):
        self.FullName, self.IsEnum, self.IsByRef = name, enum, ref
    def GetElementType(self):
        return Type(self.FullName, self.IsEnum)


class Method:
    def __init__(self, name, types_, invoke):
        self.Name, self.types, self.invoke = name, types_, invoke
        self.ReturnType = Type("System.Int32")
    def GetParameters(self):
        return [types.SimpleNamespace(ParameterType=t) for t in self.types]
    def Invoke(self, instrument, arguments):
        return self.invoke(arguments)
    def __str__(self):
        return self.Name


class Array:
    @classmethod
    def __class_getitem__(cls, _type):
        return lambda n: [None] * n


class MotorTests(unittest.TestCase):
    def setUp(self):
        self.writes = []
        self.logs = []
        self.angle, self.status, self.result = 25.004, "Stopped", 0
        self.modes = ["Move", "Index", "Define"]
        self.get = Method("GetPPMSItem", [Type("System.Int32"), Type("System.Double", ref=True), Type("System.Boolean")], self.read_fake)
        self.set = Method("SendPPMSCommand", [Type("System.String"), Type("System.String", ref=True), Type("System.String", ref=True), Type("System.Int32"), Type("System.Double")], self.write_fake)
        self.methods = [self.get, self.set]
        instrument = types.SimpleNamespace(GetType=lambda: types.SimpleNamespace(GetMethods=lambda: self.methods))
        self.controller = types.SimpleNamespace(instrument=instrument, lock=threading.RLock(), logger=self.logs.append)
        self.motor = app.PPMSMotorDebug(self.controller)
        self.system = types.SimpleNamespace(
            Array=Array, Object=object, Double=float, Int32=int, Boolean=bool,
            Activator=types.SimpleNamespace(CreateInstance=lambda t: 0),
            Enum=types.SimpleNamespace(GetNames=lambda t: self.modes, Parse=lambda t, n: n),
        )
        self.patch = patch.dict(sys.modules, System=self.system)
        self.patch.start()
        self.addCleanup(self.patch.stop)

    def read_fake(self, arguments):
        self.assertIn(arguments[0], (0, 3))
        self.assertIs(arguments[2], True)
        arguments[1] = self.angle if arguments[0] == 3 else {"Stopped": 4096, "Moving": 20480}.get(self.status, 0)
        return 0

    def write_fake(self, arguments):
        self.writes.append(list(arguments))
        return self.result

    def test_read_has_no_writes(self):
        self.assertEqual(self.motor.read()["angle_deg"], 25.004)
        self.assertEqual("AtTarget", self.motor.read()["status"])
        self.assertEqual(self.writes, [])

    def test_index_during_move_does_not_resend(self):
        readings = [dict(angle_deg=25.3, status='AtIndexSwitch'),
                    dict(angle_deg=25.4, status='Moving'),
                    dict(angle_deg=25.5, status='AtTarget'),
                    dict(angle_deg=25.5, status='AtTarget')]
        with patch.object(self.motor, 'move', return_value={'target_deg': 25.5}) as move, \
                patch.object(self.motor, 'read', side_effect=readings):
            result = self.motor.move_and_verify(25.5, 14, interval=0)
        self.assertEqual(result['status'], 'AtTarget')
        move.assert_called_once_with(25.5, 14)

    def test_persistent_index_at_correct_angle_is_accepted_without_retry(self):
        with patch.object(self.motor, 'move', return_value={'target_deg': 25.5}) as move, \
                patch.object(self.motor, 'read', return_value=dict(angle_deg=25.5, status='AtIndexSwitch')):
            result = self.motor.move_and_verify(25.5, 14, interval=0)
        self.assertEqual(result['status'], 'AtIndexSwitch')
        self.assertEqual(move.call_count, 1)

    def test_index_at_wrong_angle_still_times_out(self):
        with patch.object(self.motor, 'move', return_value={'target_deg': 25.5}) as move, \
                patch.object(self.motor, 'read', return_value=dict(angle_deg=25., status='AtIndexSwitch')):
            with self.assertRaisesRegex(RuntimeError, 'timed out'):
                self.motor.move_and_verify(25.5, 14, timeout=.001, interval=0)
        self.assertEqual(move.call_count, 1)

    def test_next_move_is_allowed_from_index(self):
        with patch.object(self.motor, 'read', return_value=dict(angle_deg=345., status='AtIndexSwitch')):
            self.motor.move(350., 14)
        self.assertEqual(self.writes[0][0], 'MOVE 350.000000 0 14')

    def test_repeated_index_sends_no_extra_actual_command(self):
        readings = [dict(angle_deg=25., status='AtTarget'),
                    dict(angle_deg=25.2, status='AtIndexSwitch'),
                    dict(angle_deg=25.2, status='AtIndexSwitch'),
                    dict(angle_deg=25.3, status='AtIndexSwitch'),
                    dict(angle_deg=25.5, status='AtTarget'),
                    dict(angle_deg=25.5, status='AtTarget')]
        with patch.object(self.motor, 'read', side_effect=readings):
            self.motor.move_and_verify(25.5, 14, interval=0)
        self.assertEqual(len(self.writes), 1)

    def test_index_does_not_jump_to_final_scan_angle(self):
        readings = [dict(angle_deg=344., status='AtTarget'),
                    dict(angle_deg=344.5, status='AtIndexSwitch'),
                    dict(angle_deg=344.5, status='AtIndexSwitch'),
                    dict(angle_deg=344.9, status='AtTarget'),
                    dict(angle_deg=344.9, status='AtTarget'),
                    dict(angle_deg=350., status='AtTarget'),
                    dict(angle_deg=350., status='AtTarget')]
        with patch.object(self.motor, 'read', side_effect=readings) as read:
            result = self.motor.move_and_verify(344.9, 14, interval=0, index_target=350.)
        self.assertEqual([w[0] for w in self.writes],
                         ['MOVE 344.900000 0 14'])
        self.assertEqual(result['angle_deg'], 344.9)
        self.assertEqual(read.call_count, 5)

    def test_moving_readbacks_publish_measured_angle_and_status(self):
        updates = []
        self.motor.on_read = updates.append
        for angle, status in ((26.1, 'Moving'), (27.2, 'Moving'), (28., 'Stopped')):
            self.angle, self.status = angle, status
            self.motor.read()
        self.assertEqual(updates, [
            {'angle_deg': 26.1, 'status': 'Moving'},
            {'angle_deg': 27.2, 'status': 'Moving'},
            {'angle_deg': 28., 'status': 'AtTarget'},
        ])
        self.assertEqual(self.writes, [])

    def test_single_small_move(self):
        self.motor.move(25.5, 14)
        self.assertEqual(self.writes, [["MOVE 25.500000 0 14", "", "", 0, 0.]])

    def test_bounds_and_nan(self):
        for target, speed in [(-350, 14), (-371, 14), (371, 14), (math.nan, 14), (25.5, -1), (25.5, 15), (25.5, .1), (25.5, math.inf)]:
            with self.assertRaises(ValueError):
                self.motor.validate_move(target, speed, self.angle)
        self.assertFalse(self.writes)

    def test_single_move_370_degree_limit(self):
        self.assertEqual(self.motor.validate_move(-20, 14, -5), (-20., 14))
        self.assertEqual(self.motor.validate_move(-360, 14, -339), (-360., 14))
        self.assertEqual(self.motor.validate_move(360, 14, 339), (360., 14))
        for current in (-370.1, 370.1):
            self.assertEqual(self.motor.validate_move(0, 14, current, allow_large=True), (0., 14))
        self.assertEqual(self.motor.validate_move(0, 14, -5), (0., 14))
        self.assertEqual(self.motor.validate_move(360, 14, 350), (360., 14))
        for target in (-270.001,):
            with self.assertRaises(ValueError):
                self.motor.validate_move(target, 14, 100)
        for target in (-270, -269.999, 370):
            self.assertEqual(self.motor.validate_move(target, 14, 100), (target, 14))
        self.assertEqual(self.motor.validate_move(0, 14, 350.003), (0., 14))
        self.assertEqual(self.motor.validate_move(370, 14, 0), (370., 14))
        with self.assertRaises(ValueError):
            self.motor.validate_move(370, 14, -0.001)
        self.assertFalse(self.writes)

    def test_expanded_angle_bounds_and_readback(self):
        for target, current in ((-370, -350), (370, 350), (355, 360.004),
                                (-355, -360.004), (370, 370), (-370, -370),
                                (370, 370.004), (-370, -370.004)):
            self.assertEqual(self.motor.validate_move(target, 14, current), (target, 14))
        for target in (-370.001, 370.001):
            with self.assertRaises(ValueError):
                self.motor.validate_move(target, 14, 0, allow_large=True)
        self.assertFalse(self.writes)

    def test_moving_or_unknown_state_rejected(self):
        for self.status in ("Moving", "Unknown", "0"):
            with self.assertRaises(RuntimeError):
                self.motor.move(25.5, 14)
        self.assertFalse(self.writes)

    def test_bad_signature_does_not_write(self):
        self.set.types[1] = Type("System.String")
        with self.assertRaises(RuntimeError):
            self.motor.move(25.5, 14)
        self.assertFalse(self.writes)

    def test_nonzero_return_never_retries(self):
        self.result = 2
        with self.assertRaises(RuntimeError):
            self.motor.move(25.5, 14)
        self.assertEqual(len(self.writes), 1)

    def test_missing_or_ambiguous_api_fails_closed(self):
        for methods in ([self.get], [self.get, self.set, self.set]):
            self.methods = methods
            with self.assertRaises(RuntimeError):
                self.motor.move(25.5, 14)
        self.assertFalse(self.writes)

    def test_info_readonly(self):
        self.assertIn("SendPPMSCommand", self.motor.describe())
        self.assertFalse(self.writes)

    def test_boolean_success(self):
        self.get.ReturnType = Type("System.Boolean")
        old = self.get.invoke
        self.get.invoke = lambda args: (old(args), True)[1]
        self.assertAlmostEqual(self.motor.read()["angle_deg"], self.angle)
        self.assertFalse(self.writes)

    def test_read_error_blocks_move(self):
        self.get.invoke = lambda args: 1
        with self.assertRaises(RuntimeError):
            self.motor.read()
        self.assertFalse(self.writes)

    def test_nonempty_reply_no_retry(self):
        def invoke(args):
            self.writes.append(list(args))
            args[2] = "command rejected"
            return 0
        self.set.invoke = invoke
        with self.assertRaisesRegex(RuntimeError, "timed out"):
            self.motor.move_and_verify(25.5, 14, timeout=.001, interval=0)
        self.assertEqual(len(self.writes), 1)

    def test_garbled_reply_waits_for_verified_position(self):
        def invoke(args):
            self.writes.append(list(args))
            args[2] = "\x04\x12garbled"
            self.angle = 25.51
            return 1
        self.set.invoke = invoke
        result = self.motor.move_and_verify(25.5, 14, interval=0)
        self.assertTrue(result["ambiguous"])
        self.assertEqual(result["status"], "AtTarget")
        self.assertEqual(result["raw_error"], "\x04\x12garbled")
        self.assertEqual(len(self.writes), 1)

    def test_timeout_no_retry(self):
        def invoke(args):
            self.writes.append(list(args))
            raise TimeoutError("unknown outcome")
        self.set.invoke = invoke
        with self.assertRaises(TimeoutError):
            self.motor.move(25.5, 14)
        self.assertTrue(self.motor.movement_attempted)
        self.assertEqual(len(self.writes), 1)

    def test_invalid_status_blocks_write(self):
        old = self.get.invoke
        def invoke(args):
            if args[0] == 0:
                args[1] = 4096.5
                return 0
            return old(args)
        self.get.invoke = invoke
        with self.assertRaises(RuntimeError):
            self.motor.move(25.5, 14)
        self.assertFalse(self.writes)

    def test_return_one_requires_verified_target(self):
        self.result = 1
        old = self.set.invoke
        def invoke(args):
            result = old(args)
            self.angle = 25.51
            return result
        self.set.invoke = invoke
        result = self.motor.move_and_verify(25.5, 14, interval=0)
        self.assertTrue(result["ambiguous"])
        self.assertEqual(len(self.writes), 1)

    def test_return_one_wrong_target_times_out_no_retry(self):
        self.result = 1
        with self.assertRaisesRegex(RuntimeError, "timed out"):
            self.motor.move_and_verify(25.5, 14, timeout=.001, interval=0)
        self.assertEqual(len(self.writes), 1)

    def test_cancel_verification_not_stop_claim(self):
        with self.assertRaisesRegex(RuntimeError, "does not mean the motor"):
            self.motor.move_and_verify(25.5, 14, cancelled=lambda: True)
        self.assertEqual(len(self.writes), 0)

    def test_two_consecutive_readbacks_required(self):
        original = self.motor.read
        sequence = iter([
            {"angle_deg": 25.5, "status": "AtTarget"},
            {"angle_deg": 25.5, "status": "Moving"},
            {"angle_deg": 25.5, "status": "AtTarget"},
            {"angle_deg": 25.5, "status": "AtTarget"},
        ])
        count = []
        def read():
            if not self.writes:
                return original()
            count.append(1)
            return next(sequence)
        self.motor.read = read
        self.motor.move_and_verify(25.5, 14, interval=0)
        self.assertEqual(len(count), 4)


if __name__ == "__main__":
    unittest.main()
