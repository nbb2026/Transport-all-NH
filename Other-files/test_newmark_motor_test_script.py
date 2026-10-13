"""Newmark_motor_test.py, the standalone NSC-A1 rotation-stage test (user,
2026-10-13), on its simulated controller, on stand-ins and on a fake DLL.

No real DLL is ever loaded (also on the Windows CI, where ctypes.WinDLL
exists): ctypes.WinDLL, os.add_dll_directory and the script's own loader are
replaced for the whole module.  No serial or COM port, no window.  Time is a
virtual clock, so a 10 s move takes no real time."""
import ast
import ctypes
import importlib.util
import io
import os
import shutil
import tempfile
import types
import unittest
from contextlib import redirect_stdout
from pathlib import Path
from unittest import mock

SCRIPT = Path(__file__).resolve().parents[1] / "Newmark_motor_test.py"
spec = importlib.util.spec_from_file_location("newmark_motor_test", SCRIPT)
script = importlib.util.module_from_spec(spec)
spec.loader.exec_module(script)

REAL_LOADER = script.load_performax
_guards = []


def _no_real_dll(*_args, **_kwargs):
    raise AssertionError("a test tried to load a real DLL")


def setUpModule():
    for target, name in ((ctypes, "WinDLL"), (os, "add_dll_directory"), (script, "load_performax")):
        guard = mock.patch.object(target, name, _no_real_dll, create=True)
        guard.start()
        _guards.append(guard)


def tearDownModule():
    while _guards:
        _guards.pop().stop()


class Clock:
    """Virtual time; a sleep at or after each time in ``interrupts`` raises Ctrl+C once."""

    def __init__(self, interrupts=()):
        self.t = 1000.0
        self.interrupts = sorted(1000.0 + offset for offset in interrupts)

    def now(self):
        return self.t

    def sleep(self, seconds):
        if self.interrupts and self.t >= self.interrupts[0]:
            self.interrupts.pop(0)
            raise KeyboardInterrupt
        self.t += seconds


class Sim(script.SimulatedNSC):
    """The script's simulated controller; ``moves`` lists the motion commands it got."""

    @property
    def moves(self):
        return [c for c in self.received if c.startswith("X") or c.startswith("HL") or c.startswith("J")]

    def degrees(self):
        return self.px / 10000


class Stuck(Sim):
    """A move whose MST never says it ended, until STOP or ABORT."""

    def _finished(self, now):
        return self.motion["stopping"] and now >= self.motion["t1"]


class IgnoresStop(Stuck):
    def _stop(self, command, now):
        if command != "STOP":
            super()._stop(command, now)


class Runaway(Stuck):
    """Nothing stops it."""

    def _stop(self, command, now):
        pass


class LosesSteps(Sim):
    def _end_position(self, motion):
        return motion["to"] - 7


class Lagging(Sim):
    """The transaction of ``command`` (its n-th time) fails, but the controller did
    it and its reply comes late: every later reply is one behind until a flush."""

    def __init__(self, command, nth=1, **options):
        super().__init__(**options)
        self.fail_command, self.nth, self.seen, self.pending = command, nth, 0, None

    def send_recv(self, handle, data):
        command = bytes(data).split(b"\0", 1)[0].decode()
        reply = super().send_recv(handle, data)
        if reply is not None and command == self.fail_command:
            self.seen += 1
            if self.seen == self.nth:
                self.pending = reply
                return None
        if self.pending is not None and reply is not None:
            reply, self.pending = self.pending, reply
        return reply

    def flush(self, handle):
        self.pending = None
        return super().flush(handle)


class Garbled(Sim):
    """The n-th reply to ``command`` is ``text`` (a reply out of place)."""

    def __init__(self, command, text, nth=1, **options):
        super().__init__(**options)
        self.target, self.text, self.nth, self.seen = command, text, nth, 0

    def reply(self, command):
        answer = super().reply(command)
        if command == self.target:
            self.seen += 1
            if self.seen == self.nth:
                return self.text
        return answer


class NoFlushDll(script.SimulatedPerformax):
    fnPerformaxComFlush = None      # an older PerformaxCom.dll


class InterruptingDll(script.SimulatedPerformax):
    """Ctrl+C during the transaction of ``command``: after the controller got it, or before."""

    def __init__(self, controller, command, delivered=True):
        super().__init__(controller)
        self.command, self.delivered, self.fired = command, delivered, False

    def fnPerformaxComSendRecv(self, handle, write, write_size, read_size, read):
        if not self.fired and ctypes.string_at(write) == self.command.encode():
            self.fired = True
            if self.delivered:
                super().fnPerformaxComSendRecv(handle, write, write_size, read_size, read)
            raise KeyboardInterrupt
        return super().fnPerformaxComSendRecv(handle, write, write_size, read_size, read)


class CtypesDll:
    """PerformaxCom.dll as real ctypes function pointers with the manual's C
    prototypes (its own definitions, not the script's), over a simulated
    controller.  The script's declare_functions sets their argtypes, so every
    call goes through ctypes' argument conversion, pointers and buffers."""

    def __init__(self, controller):
        self.controller = controller
        self.errors, self.writes, self.sizes, self.product_calls = [], [], [], []
        dword, void_p, boolean = ctypes.c_uint32, ctypes.c_void_p, ctypes.c_int

        def guarded(function):
            def call(*args):
                try:
                    return function(*args)
                except BaseException as error:  # a ctypes callback must never raise
                    self.errors.append(repr(error))
                    return 0
            return call

        proto = ctypes.CFUNCTYPE
        self.fnPerformaxComGetNumDevices = proto(boolean, ctypes.POINTER(dword))(guarded(self._count))
        self.fnPerformaxComGetProductString = proto(boolean, dword, void_p, dword)(guarded(self._product))
        self.fnPerformaxComOpen = proto(boolean, dword, ctypes.POINTER(void_p))(guarded(self._open))
        self.fnPerformaxComClose = proto(boolean, void_p)(guarded(self._close))
        self.fnPerformaxComSetTimeouts = proto(boolean, dword, dword)(guarded(self._timeouts))
        self.fnPerformaxComSendRecv = proto(boolean, void_p, void_p, dword, dword, void_p)(guarded(self._send))
        self.fnPerformaxComFlush = proto(boolean, void_p)(guarded(self._flush))

    def _count(self, pointer):
        pointer[0] = self.controller.count()
        return 1

    def _product(self, index, address, option):
        self.product_calls.append((index, option))
        text = self.controller.product_string(index, option)
        if text is None:
            return 0
        data = text.encode() + b"\0"
        ctypes.memmove(address, data, len(data))
        return 1

    def _open(self, index, pointer):
        handle = self.controller.open(index)
        if handle is None:
            return 0
        pointer[0] = handle
        return 1

    def _close(self, handle):
        return int(self.controller.close(handle))

    def _timeouts(self, read_ms, write_ms):
        return int(self.controller.set_timeouts(read_ms, write_ms))

    def _send(self, handle, write, write_size, read_size, read):
        data = ctypes.string_at(write, write_size)
        self.writes.append(data)
        self.sizes.append((write_size, read_size))
        reply = self.controller.send_recv(handle, data)
        if reply is None:
            return 0
        # As a USB buffer may: the reply, a NUL, then leftovers.
        ctypes.memmove(read, (reply + b"\0leftover").ljust(read_size, b"\0")[:read_size], read_size)
        return 1

    def _flush(self, handle):
        return int(self.controller.flush(handle))


def run(argv, controller=None, dll=None, answer="yes", clock=None):
    controller = Sim() if controller is None else controller
    dll = script.SimulatedPerformax(controller) if dll is None else dll
    clock = Clock() if clock is None else clock
    answers = mock.Mock(side_effect=answer) if isinstance(answer, BaseException) else mock.Mock(return_value=answer)
    with mock.patch.object(script, "load_performax", return_value=(dll, "C:/lab/PerformaxCom.dll")), \
            mock.patch.object(script, "_now", clock.now), mock.patch.object(script, "_sleep", clock.sleep), \
            mock.patch("builtins.input", answers), redirect_stdout(io.StringIO()) as out:
        code = script.main(argv)
    return code, out.getvalue()


def after(sent, command):
    """The commands sent after the first ``command``."""
    return sent[sent.index(command) + 1:]


class UsageTests(unittest.TestCase):
    def test_usage_errors_are_exit_2_and_nothing_is_opened(self):
        for argv in (["move"], ["move", "--degrees", "0"], ["move", "--degrees", "361"],
                     ["move", "--degrees", "-400"], ["move", "--degrees", "nan"], ["move", "--degrees", "inf"],
                     ["move", "--degrees", "10", "--speed", "25"], ["move", "--degrees", "10", "--speed", "0"],
                     ["move", "--degrees", "10", "--speed", "-1"], ["--speed", "5"], ["--degrees", "5"],
                     ["home"], ["move", "--degrees", "5", "--direction", "+"], ["spin"], ["--serial", " "],
                     ["stop", "--speed", "3"]):
            with self.subTest(argv=argv), mock.patch.object(script, "load_performax") as loader, \
                    mock.patch.object(script, "SimulatedPerformax") as simulated, \
                    redirect_stdout(io.StringIO()), mock.patch("sys.stderr", io.StringIO()):
                with self.assertRaises(SystemExit) as caught:
                    script.main(argv + ["--dry-run"] if argv[0] == "stop" else argv)
                self.assertEqual(caught.exception.code, 2)
                loader.assert_not_called()
                simulated.assert_not_called()

    def test_negative_degrees_and_a_minus_direction_parse(self):
        args = script.parse_arguments(["move", "--degrees", "-10"])
        self.assertEqual((args.degrees, args.speed), (-10.0, 5.0))
        self.assertEqual(script.parse_arguments(["home", "--direction", "-"]).direction, "-")


class AllowlistTests(unittest.TestCase):
    FORBIDDEN = ("STORE", "RW", "J+", "J-", "DRVMS=250", "DRVRC=1000", "POL=0", "DN=SDE02", "DB=1", "RT=1",
                 "EOBOOT=1", "IERR=1", "HCA=0", "RZ=1", "EDEC=0", "TOC=0", "SL=0", "SLR=1", "L+", "L-", "Z+",
                 "ZH-", "H+", "T100", "SA0=1", "SR0=1", "GS0", "DO=1", "EDIO=0", "SSPD100", "EX=0", "R4",
                 "X1.5", "x100", "X", "X 100", "HSPD=-1", "X100\r", "@01ID", "ID ", "px", "X100;STORE",
                 "X\u0661\u0662", "PX=1234567890", "HL+", "HL-", "")

    def test_forbidden_commands_never_reach_the_controller(self):
        controller = Sim()
        nsc = script.NSC(script.SimulatedPerformax(controller), echo=lambda _t: None)
        nsc.open(0)
        for command in self.FORBIDDEN:
            with self.subTest(command=command):
                with self.assertRaises(script.CommandNotAllowed):
                    nsc.query(command)
        self.assertEqual(controller.received, [])

    def test_the_commands_the_script_needs_pass(self):
        for command in sorted(script.READ_ONLY | script.ACTIONS) + ["X-3600000", "X0", "PX=0", "HSPD=50000",
                                                                    "LSPD=5000", "ACC=300"]:
            with self.subTest(command=command):
                script.check_allowed(command)
        script.check_allowed("HL+", homing=True)
        script.check_allowed("HL-", homing=True)

    def test_a_command_of_64_bytes_or_more_is_refused(self):
        with self.assertRaises(script.CommandNotAllowed):
            script.command_buffer("A" * 64)
        buffer = script.command_buffer("B" * 63)
        self.assertEqual((len(buffer.raw), buffer.raw[63]), (64, 0))
        self.assertEqual(script.command_buffer("PX").raw, b"PX" + b"\0" * 62)

    def test_only_home_mode_may_send_hl(self):
        controller = Sim()
        code, _out = run(["move", "--degrees", "5", "--yes"], controller)
        self.assertEqual(code, 0)
        self.assertFalse(script.NSC(None).allow_homing)
        nsc = script.NSC(script.SimulatedPerformax(Sim()), echo=lambda _t: None)
        nsc.open(0)
        with self.assertRaises(script.CommandNotAllowed):
            nsc.start_motion("HL+")

    def test_every_mode_sends_only_allowed_commands(self):
        for argv in ([], ["move", "--degrees", "-20", "--yes"], ["stop"], ["zero"], ["clear"], ["enable", "--yes"],
                     ["disable"], ["home", "--direction", "+", "--yes"]):
            with self.subTest(argv=argv):
                controller = Sim()
                run(argv, controller)
                for command in controller.received:
                    script.check_allowed(command, homing=argv[:1] == ["home"])
                self.assertFalse([c for c in controller.received if c.startswith("?")])


class DeviceSelectionTests(unittest.TestCase):
    def test_no_device(self):
        controller = Sim(serials=())
        code, out = run([], controller)
        self.assertEqual(code, 1)
        self.assertIn("No Performax USB device found", out)
        self.assertIn("24 V", out)
        self.assertNotIn(("open", 0), controller.calls)
        self.assertEqual(controller.received, [])

    def test_no_device_for_stop_is_exit_3(self):
        code, out = run(["stop"], Sim(serials=()))
        self.assertEqual(code, 3)

    def test_one_device_is_used_without_a_serial(self):
        controller = Sim(serials=("A1",))
        code, out = run([], controller)
        self.assertEqual(code, 0, out)
        self.assertIn(("open", 0), controller.calls)
        self.assertIn("the only one", out)

    def test_two_devices_need_a_serial(self):
        controller = Sim(serials=("A1", "B2"))
        code, out = run([], controller)
        self.assertEqual(code, 1)
        self.assertIn("--serial", out)
        self.assertIn("'A1'", out)
        self.assertIn("'B2'", out)
        self.assertFalse([call for call in controller.calls if call[0] == "open"])
        self.assertEqual(controller.received, [])

    def test_two_devices_with_a_serial(self):
        controller = Sim(serials=("A1", "B2"))
        code, out = run(["--serial", "B2"], controller)
        self.assertEqual(code, 0, out)
        self.assertIn(("open", 1), controller.calls)

    def test_an_unknown_serial(self):
        controller = Sim(serials=("A1",))
        code, out = run(["--serial", "ZZ"], controller)
        self.assertEqual(code, 1)
        self.assertEqual(controller.received, [])


class IdentityTests(unittest.TestCase):
    def test_another_device_gets_only_id_and_is_closed(self):
        for identity in ("Performax-4EX", "?ID"):
            with self.subTest(identity=identity):
                controller = Sim(identity=identity)
                code, out = run(["move", "--degrees", "10", "--yes"], controller)
                self.assertEqual(code, 1)
                self.assertEqual(controller.received, ["ID"])
                self.assertEqual(controller.calls[-1][0], "close")
                self.assertIn("Nothing else was sent", out)

    def test_id_comes_first_after_open_and_flush(self):
        controller = Sim()
        run([], controller)
        self.assertEqual(controller.received[:2], ["ID", "VER"])
        self.assertEqual([call[0] for call in controller.calls[:3]], ["timeouts", "open", "flush"])
        self.assertEqual(controller.calls[0], ("timeouts", 1000, 1000))

    def test_a_version_that_contains_sde_is_fine(self):
        controller = Sim()
        controller.version = "SDE-V231"
        code, out = run([], controller)
        self.assertEqual(code, 0, out)

    def test_ver_answering_like_id_means_out_of_step(self):
        controller = Garbled("VER", "Ace-Series-SDE")
        code, out = run(["move", "--degrees", "10", "--yes"], controller)
        self.assertEqual(code, 1)
        self.assertEqual(controller.received, ["ID", "VER"])
        self.assertIn("out of step", out)


class InfoTests(unittest.TestCase):
    def test_info_is_read_only_plus_rr(self):
        controller = Sim()
        code, out = run([], controller)
        self.assertEqual(code, 0, out)
        for command in controller.received:
            self.assertTrue(command in script.READ_ONLY or command == "RR", command)
        self.assertIn("10000 steps/deg (72:1 x 200 full steps x DRVMS 250 / 360)", out)
        self.assertEqual(controller.received.count("RR"), 1)
        self.assertEqual(after(controller.received, "RR")[0], "R2")

    def test_status_is_decoded_and_problems_are_named(self):
        controller = Sim()
        controller.inputs, controller.latched, controller.eo = 1 << 5, 1 << 6, 0
        code, out = run([], controller)
        self.assertEqual(code, 0, out)
        self.assertIn("+limit input on", out)
        self.assertIn("-limit ERROR (latched)", out)
        self.assertIn("polarity", out)
        self.assertIn("'clear'", out)
        self.assertIn("'enable'", out)

    def test_home_bit_alone_is_not_moving(self):
        controller = Sim()
        controller.home_px = 0
        code, out = run([], controller)
        self.assertIn("MST 8 = not moving, home switch on", out)

    def test_stepnloop_on_skips_rr(self):
        controller = Sim()
        controller.registers["SL"] = 1
        code, out = run([], controller)
        self.assertEqual(code, 0, out)
        self.assertNotIn("RR", controller.received)
        self.assertIn("driver parameters not read", out)

    def test_rt_1_prefix_is_removed(self):
        controller = Sim()
        controller.registers["RT"] = 1
        controller.px = -12345
        code, out = run([], controller)
        self.assertEqual(code, 0, out)
        self.assertIn("position: PX -12345 = -1.2345 deg", out)
        self.assertIn("RT=1", out)


class MoveTests(unittest.TestCase):
    def test_a_relative_move_as_an_absolute_target(self):
        controller = Sim()
        controller.px = 50000
        code, out = run(["move", "--degrees", "10"], controller)
        self.assertEqual(code, 0, out)
        self.assertEqual(controller.moves, ["X150000"])
        self.assertEqual(controller.px, 150000)
        sent = controller.received
        before_move = sent[:sent.index("X150000")]
        for command in ("ABS", "HSPD=50000", "LSPD=5000", "ACC=300", "HSPD", "LSPD", "ACC", "MM"):
            self.assertIn(command, before_move)
        self.assertLess(before_move.index("ACC=300"), before_move.index("HSPD", before_move.index("ACC=300")))
        self.assertEqual(before_move[-3:], ["MST", "EO", "PX"])  # checked again just before
        self.assertIn("+5.0000 deg -> +15.0000 deg", out)
        self.assertIn("the target exactly", out)
        self.assertIn("which way the stage turned for this + move", out)

    def test_negative_move_and_speed(self):
        controller = Sim()
        code, out = run(["move", "--degrees", "-2.5", "--speed", "2", "--yes"], controller)
        self.assertEqual(code, 0, out)
        self.assertEqual(controller.moves, ["X-25000"])
        self.assertIn("HSPD=20000", controller.received)
        self.assertIn("LSPD=2000", controller.received)
        self.assertIn("this - move", out)

    def test_no_means_nothing_but_reads(self):
        controller = Sim()
        code, out = run(["move", "--degrees", "10"], controller, answer="no")
        self.assertEqual(code, 1)
        self.assertIn("Not moved", out)
        self.assertTrue(all(c in script.READ_ONLY or c == "RR" for c in controller.received), controller.received)

    def test_cable_limit_and_absolute_way_back(self):
        controller = Sim()
        controller.px = 3000000         # +300 deg
        code, out = run(["move", "--degrees", "90", "--yes"], controller)
        self.assertEqual(code, 1)
        self.assertIn("beyond +/-360 deg", out)
        self.assertEqual(controller.moves, [])
        controller = Sim()
        controller.px = 3500000         # +350 deg: back to 0 the long way, never across 360
        code, out = run(["move", "--degrees", "-350", "--yes"], controller, clock=Clock())
        self.assertEqual(code, 0, out)
        self.assertEqual(controller.moves, ["X0"])
        controller = Sim()
        controller.px = -3600000
        code, _out = run(["move", "--degrees", "-0.01", "--yes"], controller)
        self.assertEqual((code, controller.moves), (1, []))

    def test_refusals_before_a_move(self):
        cases = (("moving", "it is moving"), ("latched", "limit error is latched"),
                 ("limit input", "polarity"), ("driver off", "EO=0"))
        for name, text in cases:
            with self.subTest(name):
                controller = Sim()
                if name == "moving":
                    controller.motion = {"from": 0, "to": 10 ** 7, "t0": 0.0, "t1": 10 ** 9, "homing": False,
                                         "stopping": False}
                elif name == "latched":
                    controller.latched = 1 << 7
                elif name == "limit input":
                    controller.inputs = 1 << 4
                else:
                    controller.eo = 0
                code, out = run(["move", "--degrees", "10", "--yes"], controller)
                self.assertEqual(code, 1, out)
                self.assertIn("Refused", out)
                self.assertIn(text, out)
                self.assertEqual(controller.moves, [])
                if name != "moving":
                    self.assertNotIn("HSPD=50000", controller.received)

    def test_a_read_back_px_that_differs(self):
        controller = LosesSteps()
        code, out = run(["move", "--degrees", "10", "--yes"], controller)
        self.assertEqual(code, 1)
        self.assertIn("not the target 100000 (-7 steps)", out)
        self.assertIn("the motor is stopped", out)

    def test_a_timeout_sends_stop(self):
        controller = Stuck()
        code, out = run(["move", "--degrees", "1", "--yes"], controller)
        self.assertEqual(code, 1, out)
        self.assertIn("did not end within", out)
        self.assertEqual(after(controller.received, "X10000").count("STOP"), 1)
        self.assertNotIn("ABORT", controller.received)
        self.assertIsNone(controller.motion)

    def test_stop_that_does_not_stop_then_abort(self):
        controller = IgnoresStop()
        code, out = run(["move", "--degrees", "1", "--yes"], controller)
        self.assertEqual(code, 1, out)
        moved = after(controller.received, "X10000")
        self.assertLess(moved.index("STOP"), moved.index("ABORT"))
        self.assertIsNone(controller.motion)

    def test_nothing_stops_it_is_exit_3(self):
        controller = Runaway()
        code, out = run(["move", "--degrees", "1", "--yes"], controller)
        self.assertEqual(code, 3)
        self.assertIn("CHECK THE STAGE", out)
        self.assertEqual(controller.calls[-1][0], "close")

    def test_a_refused_move_command_is_stopped_not_repeated(self):
        class RefusesX(Sim):
            def reply(self, command):
                return "?State Error" if command.startswith("X") else super().reply(command)
        controller = RefusesX()
        code, out = run(["move", "--degrees", "1", "--yes"], controller)
        self.assertEqual(code, 1, out)
        self.assertEqual(controller.moves, ["X10000"])
        self.assertIn("STOP", after(controller.received, "X10000"))

    def test_speed_read_backs(self):
        class AdjustsAcc(Sim):
            def reply(self, command):
                answer = super().reply(command)
                if command == "ACC=300":
                    self.acc = 120
                return answer
        controller = AdjustsAcc()
        code, out = run(["move", "--degrees", "1", "--yes"], controller)
        self.assertEqual(code, 0, out)
        self.assertIn("adjusted ACC to 120 ms", out)
        for bad in ("HSPD", "ACC"):
            with self.subTest(bad=bad):
                controller = Garbled(bad, "0" if bad == "ACC" else "49999")
                code, out = run(["move", "--degrees", "1", "--yes"], controller)
                self.assertEqual(code, 1, out)
                self.assertIn("read back", out)
                self.assertEqual(controller.moves, [])

    def test_a_position_changed_since_the_question(self):
        controller = Sim()

        def answer(_prompt):
            controller.px = 777
            return "yes"
        with mock.patch.object(script, "confirm", lambda _yes, _action: answer(None)):
            code, out = run(["move", "--degrees", "1"], controller)
        self.assertEqual(code, 1)
        self.assertIn("position changed", out)
        self.assertEqual(controller.moves, [])

    def test_less_than_one_step(self):
        code, out = run(["move", "--degrees", "0.00001", "--yes"], Sim())
        self.assertEqual(code, 1)
        self.assertIn("less than one step", out)


class OtherModeTests(unittest.TestCase):
    def moving(self, controller):
        controller.motion = {"from": 0, "to": 900000, "t0": 1000.0, "t1": 1100.0, "homing": False,
                             "stopping": False}
        return controller

    def test_stop_while_moving(self):
        controller = self.moving(Sim())
        code, out = run(["stop"], controller)
        self.assertEqual(code, 0, out)
        self.assertEqual(controller.received[2], "STOP")    # right after ID and VER
        self.assertIsNone(controller.motion)
        self.assertIn("Stopped (MST read back)", out)
        self.assertIn(" deg)", out)

    def test_stop_at_rest(self):
        code, out = run(["stop"], Sim())
        self.assertEqual(code, 0, out)

    def test_stop_mode_falls_back_to_abort_and_unknown(self):
        controller = self.moving(IgnoresStop())
        code, out = run(["stop"], controller)
        self.assertEqual(code, 0, out)
        self.assertIn("ABORT", controller.received)
        code, out = run(["stop"], self.moving(Runaway()))
        self.assertEqual(code, 3)
        self.assertIn("MOTION STATE UNKNOWN", out)

    def test_zero(self):
        controller = Sim()
        controller.px = 123450
        code, out = run(["zero"], controller)
        self.assertEqual(code, 0, out)
        self.assertEqual(controller.px, 0)
        self.assertIn("PX 123450 (+12.3450 deg) -> 0", out)
        self.assertIn("lost when the controller is powered off", out)
        code, out = run(["zero"], self.moving(Sim()))
        self.assertEqual(code, 1)
        self.assertIn("Refused", out)

    def test_clear(self):
        controller = Sim()
        controller.latched = 1 << 6
        code, out = run(["clear"], controller)
        self.assertEqual((code, controller.latched), (0, 0))
        self.assertIn("Limit error cleared", out)
        controller = Sim()
        controller.inputs = 1 << 5
        code, out = run(["clear"], controller)
        self.assertEqual(code, 0)
        self.assertIn("polarity", out)

        class KeepsError(Sim):
            def reply(self, command):
                answer = super().reply(command)
                self.latched = 1 << 7
                return answer
        code, out = run(["clear"], KeepsError())
        self.assertEqual(code, 1)
        self.assertIn("still latched", out)

    def test_enable_asks(self):
        controller = Sim()
        controller.eo = 0
        code, out = run(["enable"], controller, answer="no")
        self.assertEqual((code, controller.eo), (1, 0))
        self.assertNotIn("EO=1", controller.received)
        code, out = run(["enable"], controller, answer="yes")
        self.assertEqual((code, controller.eo), (0, 1))
        controller.eo = 0
        with mock.patch("builtins.input", side_effect=AssertionError("asked")):
            code, _out = run(["enable", "--yes"], controller)
        self.assertEqual((code, controller.eo), (0, 1))
        controller = Sim()
        code, out = run(["enable"], controller)
        self.assertEqual(code, 0)
        self.assertNotIn("EO=1", controller.received)

    def test_disable(self):
        controller = Sim()
        code, out = run(["disable"], controller)
        self.assertEqual((code, controller.eo), (0, 0))
        controller = self.moving(Sim())
        code, out = run(["disable"], controller)
        self.assertEqual((code, controller.eo), (1, 1))
        self.assertNotIn("EO=0", controller.received)


class HomeTests(unittest.TestCase):
    def test_home_plus_and_minus(self):
        for direction in ("+", "-"):
            with self.subTest(direction=direction):
                controller = Sim()
                controller.px = 100000
                code, out = run(["home", "--direction", direction], controller)
                self.assertEqual(code, 0, out)
                self.assertEqual(controller.moves, [f"HL{direction}"])
                self.assertEqual(controller.px, 0)
                self.assertIn("full turn", out)
                self.assertIn("CABLES", out)
                self.assertIn("home switch input on", out)
                self.assertIn("HSPD=50000", controller.received[:controller.received.index(f"HL{direction}")])

    def test_home_not_confirmed(self):
        controller = Sim()
        code, out = run(["home", "--direction", "+"], controller, answer="")
        self.assertEqual(code, 1)
        self.assertEqual(controller.moves, [])

    def test_home_timeout_sends_stop(self):
        controller = Stuck()
        code, out = run(["home", "--direction", "-", "--yes"], controller)
        self.assertEqual(code, 1, out)
        self.assertIn("the home search did not end within", out)
        self.assertIn("STOP", after(controller.received, "HL-"))


class CtrlCTests(unittest.TestCase):
    """Ctrl+C anywhere: STOP (+ wait, PX) if a move may be running; else nothing moved."""

    def test_at_the_question(self):
        controller = Sim()
        code, out = run(["move", "--degrees", "10"], controller, answer=KeyboardInterrupt())
        self.assertEqual(code, 1)
        self.assertIn("had not started a move", out)
        self.assertEqual(controller.moves, [])
        self.assertNotIn("STOP", controller.received)

    def test_during_the_driver_read(self):
        controller = Sim()
        code, out = run(["move", "--degrees", "10", "--yes"], controller, clock=Clock(interrupts=(0,)))
        self.assertEqual(code, 1)
        self.assertEqual(controller.moves, [])
        self.assertEqual(controller.received[-1], "RR")

    def test_during_the_move(self):
        controller = Sim()
        code, out = run(["move", "--degrees", "30", "--yes"], controller, clock=Clock(interrupts=(4.0,)))
        self.assertEqual(code, 1, out)
        self.assertIn("STOP", after(controller.received, "X300000"))
        self.assertTrue(0 < controller.px < 300000, controller.px)
        self.assertIsNone(controller.motion)
        # The handler itself waited for the stop and read PX (not only close()).
        self.assertIn(f"Stopped (Ctrl+C); the motor is stopped (MST read back) at {controller.px / 10000:+.4f} deg "
                      f"(PX {controller.px}).", out)
        self.assertLess(out.index("Stopped (Ctrl+C)"), out.index("> (close)"))

    def test_during_the_move_command_after_it_arrived(self):
        controller = Sim()
        dll = InterruptingDll(controller, "X100000", delivered=True)
        code, out = run(["move", "--degrees", "10", "--yes"], controller, dll=dll)
        self.assertEqual(code, 1, out)
        rest = after(controller.received, "X100000")
        self.assertEqual(rest[:4], ["STOP", "ID", "VER", "MST"])    # STOP first, then back in step
        self.assertEqual(controller.moves, ["X100000"])             # never repeated
        self.assertIsNone(controller.motion)

    def test_during_the_move_command_before_it_arrived(self):
        controller = Sim()
        dll = InterruptingDll(controller, "X100000", delivered=False)
        code, out = run(["move", "--degrees", "10", "--yes"], controller, dll=dll)
        self.assertEqual(code, 1, out)
        self.assertEqual(controller.moves, [])
        self.assertIn("STOP", controller.received)

    def test_a_second_ctrl_c_while_stopping_aborts(self):
        controller = Sim()
        clock = Clock(interrupts=(4.0, 4.0))
        code, out = run(["move", "--degrees", "30", "--yes"], controller, clock=clock)
        self.assertEqual(code, 1, out)
        rest = after(controller.received, "X300000")
        self.assertLess(rest.index("STOP"), rest.index("ABORT"))
        self.assertIsNone(controller.motion)

    def test_before_the_identity(self):
        controller = Sim()
        dll = InterruptingDll(controller, "ID", delivered=False)
        code, out = run(["move", "--degrees", "10", "--yes"], controller, dll=dll)
        self.assertEqual(code, 1)
        self.assertEqual(controller.received, [])
        self.assertIn("nothing else was sent", out)

    def test_during_info(self):
        controller = Sim()
        dll = InterruptingDll(controller, "PX", delivered=True)
        code, out = run([], controller, dll=dll)
        self.assertEqual(code, 1)
        self.assertNotIn("STOP", controller.received)

    def test_during_the_home_search(self):
        controller = Sim()
        code, out = run(["home", "--direction", "+", "--yes"], controller, clock=Clock(interrupts=(3.5,)))
        self.assertEqual(code, 1, out)
        self.assertIn("STOP", after(controller.received, "HL+"))
        self.assertIsNone(controller.motion)

    def test_an_unexpected_error_mid_move_still_stops_before_close(self):
        controller = Sim()

        def broken(nsc, *_args):
            raise TypeError("a bug")
        with mock.patch.object(script.NSC, "wait_motion", broken):
            with self.assertRaises(TypeError):
                run(["move", "--degrees", "30", "--yes"], controller)
        self.assertIn("STOP", after(controller.received, "X300000"))
        self.assertIsNone(controller.motion)
        self.assertEqual(controller.calls[-1][0], "close")

    def test_a_stop_cut_short_is_repeated_on_close(self):
        controller = Sim()
        nsc = script.NSC(script.SimulatedPerformax(controller), echo=lambda _t: None)
        clock = Clock()
        with mock.patch.object(script, "_now", clock.now), mock.patch.object(script, "_sleep", clock.sleep):
            nsc.open(0)
            nsc.identify()
            controller.reply("X300000")
            nsc.motion_may_run = True
            with mock.patch.object(nsc, "_wait_until_still", side_effect=KeyboardInterrupt):
                with self.assertRaises(KeyboardInterrupt):
                    nsc.stop_motion()
            nsc.close()
        self.assertEqual(controller.received.count("STOP"), 2)
        self.assertIsNone(controller.motion)
        self.assertEqual(controller.calls[-1][0], "close")

    def test_during_stop_mode(self):
        controller = OtherModeTests.moving(None, Sim())
        dll = InterruptingDll(controller, "STOP", delivered=True)
        code, out = run(["stop"], controller, dll=dll)
        self.assertEqual(code, 0, out)
        self.assertIsNone(controller.motion)


class StaleReplyTests(unittest.TestCase):
    def test_a_failed_transaction_is_followed_by_flush_id_ver(self):
        controller = Lagging("HSPD")
        controller.lspd = 4321
        code, out = run([], controller)
        self.assertEqual(code, 1, out)              # one read failed
        rest = after(controller.received, "HSPD")
        self.assertEqual(rest[:3], ["ID", "VER", "LSPD"])
        self.assertEqual(controller.calls.count(("flush",)), 3)     # after Open, after the failure, in the resync
        self.assertIn("LSPD 4321 pps", out)         # its own reply, not HSPD's late one
        self.assertIn("HSPD could not be read", out)

    def test_without_flush_a_stale_reply_is_never_used(self):
        # An older DLL without fnPerformaxComFlush: replies stay one behind.  ID
        # and VER then never give "SDE" followed by a different reply, so every
        # later read fails instead of showing the reply of the read before it.
        controller = Lagging("HSPD")
        controller.lspd, controller.acc = 4321, 222
        code, out = run([], controller, dll=NoFlushDll(controller))
        self.assertEqual(code, 1, out)
        self.assertIn("out of step", out)
        self.assertIn("still one behind", out)
        summary = out[out.index("Summary"):]
        self.assertIn("HSPD None pps, LSPD None pps, ACC None ms", summary)
        self.assertNotIn("RR", controller.received)

    def test_without_flush_a_failure_before_the_move_moves_nothing(self):
        controller = Lagging("PX")
        code, out = run(["move", "--degrees", "10", "--yes"], controller, dll=NoFlushDll(controller))
        self.assertEqual(code, 1, out)
        self.assertEqual(controller.moves, [])
        self.assertIn("Failed: PX: the USB transaction failed (it is not repeated)", out)

    def test_a_reply_out_of_place_is_not_used(self):
        controller = Garbled("MST", "OK")
        code, out = run([], controller)
        self.assertEqual(code, 1)
        self.assertIn("not a number", out)
        rest = after(controller.received, "MST")
        self.assertEqual(rest[:3], ["ID", "VER", "EO"])

    def test_a_failed_move_command_is_not_repeated(self):
        controller = Lagging("X100000")
        code, out = run(["move", "--degrees", "10", "--yes"], controller)
        self.assertEqual(code, 1, out)
        self.assertEqual(controller.moves, ["X100000"])
        self.assertIn("STOP", after(controller.received, "X100000"))
        self.assertIsNone(controller.motion)

    def test_rr_silence_answers_after_the_wait(self):
        controller = Sim()
        controller.silent_until = -1
        code, out = run(["move", "--degrees", "1", "--yes"], controller)
        self.assertEqual(code, 0, out)
        controller = Sim()
        with mock.patch.object(script, "RR_WAIT_S", 1.0):
            code, out = run(["move", "--degrees", "1", "--yes"], controller)
        self.assertEqual(code, 1)
        self.assertEqual(controller.moves, [])


class CtypesPathTests(unittest.TestCase):
    """The ctypes calls themselves: argtypes, pointers, 64-byte buffers, a 256-byte product string."""

    def run_through_ctypes(self, argv, controller):
        fake = CtypesDll(controller)
        script.declare_functions(fake)
        code, out = run(argv, controller, dll=fake)
        self.assertEqual(fake.errors, [])
        return fake, code, out

    def test_declared_like_the_manual(self):
        fake = script.declare_functions(CtypesDll(Sim()))
        send = fake.fnPerformaxComSendRecv
        self.assertEqual(send.argtypes, [ctypes.c_void_p, ctypes.c_void_p, script.DWORD, script.DWORD,
                                         ctypes.c_void_p])
        self.assertIs(send.restype, ctypes.c_int)
        self.assertEqual(ctypes.sizeof(script.DWORD), 4)
        self.assertEqual(fake.fnPerformaxComOpen.argtypes[1]._type_, ctypes.c_void_p)

    def test_info_and_move_through_ctypes(self):
        controller = Sim(serials=("12345",))
        controller.registers["RT"] = 1
        fake, code, out = self.run_through_ctypes(["move", "--degrees", "-10", "--yes"], controller)
        self.assertEqual(code, 0, out)
        self.assertEqual(controller.px, -100000)
        self.assertEqual(set(fake.sizes), {(64, 64)})
        for data in fake.writes:
            command, _nul, rest = data.partition(b"\0")
            self.assertEqual(len(data), 64)
            self.assertTrue(command and command == command.upper())
            self.assertEqual(rest.strip(b"\0"), b"")
        self.assertEqual(fake.writes[0], b"ID" + b"\0" * 62)
        self.assertEqual(sorted(fake.product_calls), [(0, 0), (0, 1)])
        self.assertIn("serial '12345'", out)
        handle = 0x5EED0
        self.assertEqual(controller.calls[0], ("timeouts", 1000, 1000))
        self.assertEqual(controller.calls[1:3], [("open", 0), ("flush",)])
        self.assertEqual(controller.calls[-1], ("close", handle))
        self.assertIn("X-100000", controller.received)

    def test_info_through_ctypes_with_two_devices(self):
        controller = Sim(serials=("A1", "B2"))
        fake, code, out = self.run_through_ctypes(["--serial", "B2"], controller)
        self.assertEqual(code, 0, out)
        self.assertIn(("open", 1), controller.calls)
        self.assertEqual(controller.calls[-1], ("close", 0x5EED1))

    def test_reply_cleaning(self):
        self.assertEqual(script.clean_reply(b"1000\r\n\0junk"), "1000")
        self.assertEqual(script.clean_reply(b"#01OK\r\0"), "OK")
        self.assertEqual(script.clean_reply(b"#01-25\r"), "-25")
        self.assertEqual(script.clean_reply(b"#SDE01Ace-Series-SDE"), "Ace-Series-SDE")
        self.assertEqual(script.clean_reply(b"?Moving\r"), "?Moving")
        self.assertEqual(script.clean_reply(b"\0" * 64), "")


class LoaderTests(unittest.TestCase):
    """The real loader with WinDLL and add_dll_directory replaced (nothing is loaded)."""

    class FakeLib:
        def __init__(self, missing=()):
            for name, _argtypes in script.SIGNATURES:
                if name not in missing:
                    setattr(self, name, types.SimpleNamespace())

    def load(self, dll_dir=None, script_dir=None, error=None, missing=()):
        loaded, added = [], []

        def win_dll(path):
            loaded.append(path)
            if error is not None:
                raise error
            return self.FakeLib(missing)
        with mock.patch.object(ctypes, "WinDLL", win_dll, create=True), \
                mock.patch.object(os, "add_dll_directory", lambda folder: added.append(folder) or folder,
                                  create=True), \
                mock.patch.object(script, "SCRIPT_DIR", script_dir or tempfile.gettempdir() + "/no-such-folder"), \
                mock.patch.object(script, "_DLL_DIRECTORIES", []):
            result = REAL_LOADER(dll_dir)
        return result, loaded, added

    def folder_with(self, *names):
        folder = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, folder, True)
        for name in names:
            Path(folder, name).write_bytes(b"")
        return folder

    def test_not_on_windows(self):
        with mock.patch.object(ctypes, "WinDLL", None, create=True):
            with self.assertRaises(script.PerformaxError) as caught:
                REAL_LOADER(None)
        self.assertIn("Windows", str(caught.exception))

    def test_explicit_folder_first_with_siusbxp(self):
        folder = self.folder_with("PerformaxCom.dll", "SiUSBXp.dll")
        (lib, path), loaded, added = self.load(dll_dir=folder)
        self.assertEqual(added, [os.path.abspath(folder)])
        self.assertEqual(loaded, [os.path.join(os.path.abspath(folder), "SiUSBXp.dll"),
                                  os.path.join(os.path.abspath(folder), "PerformaxCom.dll")])
        self.assertEqual(path, loaded[-1])
        self.assertEqual(lib.fnPerformaxComSendRecv.argtypes[2], script.DWORD)
        self.assertIs(lib.fnPerformaxComFlush.restype, ctypes.c_int)

    def test_script_folder_then_normal_search(self):
        folder = self.folder_with("PerformaxCom.dll")
        (_lib, path), loaded, added = self.load(script_dir=folder)
        self.assertEqual((added, path), ([folder], os.path.join(folder, "PerformaxCom.dll")))
        (_lib, path), loaded, added = self.load()
        self.assertEqual((added, loaded, path), ([], ["PerformaxCom.dll"], "PerformaxCom.dll"))

    def test_explicit_folder_without_the_dll(self):
        folder = self.folder_with("SiUSBXp.dll")
        with self.assertRaises(script.PerformaxError) as caught:
            self.load(dll_dir=folder)
        self.assertIn("not in --dll-dir", str(caught.exception))

    def test_32_64_bit_mismatch_is_explained(self):
        error = OSError("[WinError 193] %1 is not a valid Win32 application")
        error.winerror = 193
        with self.assertRaises(script.PerformaxError) as caught:
            self.load(error=error)
        self.assertIn("64-bit", str(caught.exception))
        self.assertIn("WinError 193", str(caught.exception))

    def test_missing_functions(self):
        (lib, _path), _loaded, _added = self.load(missing=("fnPerformaxComFlush",))
        self.assertFalse(hasattr(lib, "fnPerformaxComFlush"))
        with self.assertRaises(script.PerformaxError):
            self.load(missing=("fnPerformaxComSendRecv",))

    def test_a_load_failure_sends_nothing(self):
        with mock.patch.object(script, "load_performax", side_effect=script.PerformaxError("no DLL")), \
                redirect_stdout(io.StringIO()) as out:
            code = script.main([])
        self.assertEqual(code, 1)
        self.assertIn("Nothing was sent", out.getvalue())


class DryRunTests(unittest.TestCase):
    def dry(self, argv):
        clock = Clock()
        with mock.patch.object(script, "_now", clock.now), mock.patch.object(script, "_sleep", clock.sleep), \
                mock.patch("builtins.input", return_value="yes"), redirect_stdout(io.StringIO()) as out:
            code = script.main(argv + ["--dry-run"])
        return code, out.getvalue()

    def test_every_mode_dry(self):
        for argv in ([], ["move", "--degrees", "10"], ["move", "--degrees", "-10", "--speed", "20"], ["stop"],
                     ["zero"], ["clear"], ["enable"], ["disable"], ["home", "--direction", "+"]):
            with self.subTest(argv=argv):
                code, out = self.dry(argv)      # load_performax is the module guard: it would fail
                self.assertEqual(code, 0, out)
                self.assertIn("dry run", out)
                self.assertIn("> ID\n< Ace-Series-SDE", out)


class StandaloneTests(unittest.TestCase):
    def test_imports_only_the_standard_library(self):
        tree = ast.parse(SCRIPT.read_text(encoding="utf-8"))
        names = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                names.update(alias.name.split(".")[0] for alias in node.names)
            elif isinstance(node, ast.ImportFrom):
                names.add(node.module.split(".")[0])
        self.assertLessEqual(names, {"argparse", "ctypes", "math", "os", "re", "sys", "time"})

    def test_docstring_has_usage_safety_and_exit_codes(self):
        for text in ("Usage", "SAFETY", "Exit status", "--dry-run", "PerformaxCom.dll", "SiUSBXp.dll"):
            self.assertIn(text, script.__doc__)


if __name__ == "__main__":
    unittest.main()
