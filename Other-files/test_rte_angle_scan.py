"""RT-E Angle scan with the Newmark NSC-A1 rotation stage (user, 2026-10-13).

The program's NewmarkNSCA1Controller runs on a fake PerformaxCom.dll: the
reviewed simulated controller of Newmark_motor_test.py (its
SimulatedPerformax answers the DLL's calls with the same ctypes buffers),
wrapped so that every call records the thread it came from.  No real DLL is
ever loaded (also on the Windows CI, where ctypes.WinDLL exists: it,
os.add_dll_directory and the program's loader are replaced for the whole
module), no serial or COM port is opened, and no window: tkinter.Tk and every
dialog the page can reach fail the test unless a test answers them itself.
Time is a virtual clock, so a 36 s turn takes no real time; past its limit a
sleep fails the test, so a lost time-out fails instead of hanging.
"""
from concurrent.futures import Future, ThreadPoolExecutor
from contextlib import ExitStack
import ctypes
import importlib.util
import inspect
import os
from pathlib import Path
import tempfile
import threading
import types
import unittest
from unittest.mock import Mock, patch

import test_rte_panel as rte_tests
from test_ppms_motor_debug import app
from test_ppms_two_panels import ImmediateExecutor, Var

SCRIPT = Path(__file__).resolve().parents[1] / "Newmark_motor_test.py"
_spec = importlib.util.spec_from_file_location("newmark_motor_test_for_rte", SCRIPT)
script = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(script)

REAL_LOADER = app.newmark_load_performax     # before the module guard replaces the name
_guards = []


def _refuse(what):
    def refuse(*_args, **_kwargs):
        raise AssertionError(f"a test reached {what}")
    return refuse


def setUpModule():
    targets = [(ctypes, "WinDLL", _refuse("ctypes.WinDLL (a real DLL)")),
               (os, "add_dll_directory", _refuse("os.add_dll_directory")),
               (app, "newmark_load_performax", _refuse("the program's real Newmark DLL loader")),
               (app.tk, "Tk", _refuse("tkinter.Tk (a window)"))]
    for name in ("showerror", "showinfo", "showwarning", "askyesno", "askokcancel", "askyesnocancel"):
        targets.append((app.messagebox, name, _refuse(f"messagebox.{name}")))
    for name in ("askdirectory", "asksaveasfilename", "askopenfilename"):
        targets.append((app.filedialog, name, _refuse(f"filedialog.{name}")))
    targets.append((app.simpledialog, "askstring", _refuse("simpledialog.askstring")))
    for target, name, replacement in targets:
        guard = patch.object(target, name, replacement, create=True)
        guard.start()
        _guards.append(guard)


def tearDownModule():
    while _guards:
        _guards.pop().stop()


# ---- stand-ins -------------------------------------------------------------------
class Clock:
    """Virtual time for the stage and its simulator.  ``hooks`` are (time, fn)
    run once by the sleep that reaches that time (on whichever thread sleeps)."""

    LIMIT = 5000.0

    def __init__(self):
        self.t = 1000.0
        self.hooks = []

    def now(self):
        return self.t

    def sleep(self, seconds):
        if self.t - 1000.0 > self.LIMIT:
            raise AssertionError("more than 5000 virtual seconds: a time-out that never fires?")
        self.t += seconds
        for hook in list(self.hooks):
            if self.t >= hook[0]:
                self.hooks.remove(hook)
                hook[1]()


class Sim(script.SimulatedNSC):
    """The script's simulated NSC-A1 (DRVMS 250: 10000 steps/deg)."""

    @property
    def moves(self):
        return [command for command in self.received if command.startswith(("X", "HL", "J"))]


class Stuck(Sim):
    """A move whose MST never says it ended, until STOP or ABORT."""

    def _finished(self, now):
        return self.motion["stopping"] and now >= self.motion["t1"]


class EndsShort(Sim):
    """Every move ends 7 steps short of its target (steps lost)."""

    def _end_position(self, motion):
        return motion["to"] if motion["stopping"] else motion["to"] - 7


class LateMovingBits(Sim):
    """After X it answers OK but reads "not moving" for 0.08 s before it starts."""

    def _start(self, target, now, homing=False, queue=()):
        self.motion = {"from": self.px, "to": self.px, "t0": now, "t1": now + 0.08, "homing": False,
                       "stopping": False, "idle": True,
                       "queue": [{"to": target, "duration": abs(target - self.px) / self.hspd + self.acc / 1000}]}


class LosesOne(Sim):
    """The USB transaction of ``command`` fails once (no reply)."""

    def __init__(self, command, **options):
        super().__init__(**options)
        self.lose, self.lost = command, False

    def send_recv(self, handle, data):
        command = bytes(data).split(b"\0", 1)[0].decode("ascii")
        if command == self.lose and not self.lost:
            self.lost = True
            self.received.append(command + " (lost)")
            return None
        return super().send_recv(handle, data)


class ReplyLost(Sim):
    """The controller runs ``command`` once, but its reply is lost (the USB
    transaction fails after it), as for an X whose move has started."""

    def __init__(self, command, **options):
        super().__init__(**options)
        self.lose, self.lost = command, False

    def send_recv(self, handle, data):
        reply = super().send_recv(handle, data)
        command = bytes(data).split(b"\0", 1)[0].decode("ascii")
        if command == self.lose and not self.lost and reply is not None:
            self.lost = True
            return None
        return reply


class Runaway(Stuck):
    """A move that never ends, and STOP and ABORT do nothing: the stage cannot
    be confirmed stopped."""

    def _stop(self, command, now):
        pass


class RunawayReplyLost(Runaway, ReplyLost):
    """X runs, its reply is lost, and STOP and ABORT do nothing."""


class StopLostInTheGap(LateMovingBits):
    """main's 15b88a1 simulator: a STOP in the gap after X, while MST still
    says "not moving", does nothing, and the move starts anyway.  ``on_x``
    runs right after each X is answered (a Stop pressed in that gap)."""

    def __init__(self, on_x=None, **options):
        super().__init__(**options)
        self.on_x = on_x

    def reply(self, command):
        text = super().reply(command)
        if command.startswith("X") and self.on_x is not None:
            self.on_x()
        return text

    def _stop(self, command, now):
        if not (command == "STOP" and self.motion is not None and self.motion.get("idle")):
            super()._stop(command, now)


class RecordingDLL:
    """PerformaxCom.dll as the script's simulator answers it; every call is
    recorded with the thread it came from."""

    def __init__(self, controller, missing=()):
        self.inner = script.SimulatedPerformax(controller)
        self.missing = set(missing)
        self.calls = []

    def __getattr__(self, name):
        if not name.startswith("fnPerformaxCom") or name in self.missing:
            raise AttributeError(name)
        function = getattr(self.inner, name)

        def call(*args):
            self.calls.append((name, threading.get_ident()))
            return function(*args)
        return call

    def names(self):
        return [name for name, _thread in self.calls]


class Recorder(rte_tests.Widget):
    """A stand-in widget that remembers every instance and where it was
    gridded (for the page build)."""

    made = []

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.grid_options = {}
        Recorder.made.append(self)

    def grid(self, **kwargs):
        self.grid_options = dict(kwargs)


class StageBase(unittest.TestCase):
    def make(self, sim=None, executor=None, missing=()):
        """An RT-E page stand-in (test_rte_panel's) with the real stage controller
        on the fake DLL.  ``ui`` holds what the page posts to the Tk thread."""
        clock = Clock()
        guard = patch.object(script, "_now", clock.now)
        guard.start()
        self.addCleanup(guard.stop)
        sim = Sim() if sim is None else sim
        dll = RecordingDLL(sim, missing)
        log = []
        stage = app.NewmarkNSCA1Controller(log.append, loader=lambda: (dll, "fake PerformaxCom.dll"))
        stage._now, stage._sleep = clock.now, clock.sleep
        panel = rte_tests.RTE.make_worker(self)
        panel.rte_controllers["newmark"] = stage
        kepco = panel.rte_controllers["kepco"]
        kepco.set_output.side_effect = lambda mode, target, limit: setattr(kepco, "cached_current_a", target)
        panel.rte_stage_stop_event = threading.Event()
        panel.rte_stage_where = ""
        panel.rte_stage_live = Var("Stage: not connected")
        panel.rte_stage_target = Var("0")
        panel.rte_resources = {"newmark": Var("auto")}
        # No rte_connections["newmark"]: the stage line is the stage's only
        # text since its controls moved to the scan box (user/CI, 2026-10-14),
        # so a stray use of it fails here with AttributeError.
        panel.rte_drive_live = Var("")
        panel.rte_field_live = Var("")
        panel.ppms_environment_busy = False
        panel.ppms_field_scan_running = False
        panel.ppms_control_safety_var = Var(True)
        panel._update_ppms_control_ui = Mock()
        panel.thread_log = Mock()
        panel.root = None
        ui = []
        panel.post_ui = ui.append
        panel.ppms_executor = ImmediateExecutor() if executor is None else executor
        return types.SimpleNamespace(panel=panel, stage=stage, sim=sim, dll=dll, clock=clock, ui=ui, log=log)

    @staticmethod
    def drain(h):
        while h.ui:
            h.ui.pop(0)()

    def settle(self, h, executor):
        """Wait until the one worker has run everything queued, then run the
        Tk-thread callbacks it posted, as the page's queue would."""
        executor.submit(lambda: None).result(timeout=30)
        self.drain(h)

    @staticmethod
    def settings():
        return app.validate_ppms_transport_scan_settings(1., 317.3, 50., 1, 2, ("830_a",))


# ---- the driver ----------------------------------------------------------------
class NewmarkDriverTests(StageBase):
    def test_connect_confirms_the_nsc_and_reads_the_scale(self):
        h = self.make()
        self.assertEqual(h.stage.connect("auto"), "Ace-Series-SDE")
        self.assertTrue(h.stage.connected)
        names = h.dll.names()
        self.assertLess(names.index("fnPerformaxComSetTimeouts"), names.index("fnPerformaxComOpen"))
        self.assertEqual(names[names.index("fnPerformaxComOpen") + 1], "fnPerformaxComFlush")
        self.assertEqual(h.sim.received[:2], ["ID", "VER"], "ID before anything else, then VER")
        self.assertEqual(h.sim.received[2:6], ["MST", "SL", "EDIO", "RR"])
        self.assertGreaterEqual(h.clock.t - 1000.0, app.NEWMARK_RR_WAIT_S, "no reads while RR runs")
        self.assertEqual(h.sim.received[6:8], ["R2", "DRVMS"])
        self.assertEqual(h.stage.steps_per_degree, 10000, "40 x DRVMS 250")
        self.assertEqual(h.stage.scale_text(), "10000 steps/° (72:1 x 200 x DRVMS 250 / 360)")
        self.assertEqual(h.stage.resource_name, "SIM-NSC-A1")
        self.assertEqual(h.sim.moves, [])
        self.assertEqual(app.newmark_stage_text(h.stage.read_state()), "Stage: +0.000° (10000 steps/°)")

    def test_device_choice(self):
        h = self.make(Sim(serials=()))
        with self.assertRaisesRegex(app.NewmarkStageError, "No Performax USB device found"):
            h.stage.connect("auto")
        self.assertFalse(h.stage.connected)
        h = self.make(Sim(serials=("A1", "B2")))
        with self.assertRaisesRegex(app.NewmarkStageError, "2 Performax devices.*'A1', 'B2'.*in place of auto"):
            h.stage.connect("auto")
        self.assertNotIn("fnPerformaxComOpen", h.dll.names())
        with self.assertRaisesRegex(app.NewmarkStageError, "0 Performax devices have the USB serial 'C3'"):
            h.stage.connect("C3")
        h.stage.connect(" B2 ")
        self.assertIn(("open", 1), h.sim.calls)
        self.assertEqual(h.stage.resource_name, "B2")

    def test_a_device_that_is_not_an_nsc_gets_only_id_and_is_closed(self):
        for identity in ("SDE01", "Model 2000", "?Unknown"):
            with self.subTest(identity=identity):
                h = self.make(Sim(identity=identity))
                with self.assertRaisesRegex(app.NewmarkStageError, "not an NSC-A1"):
                    h.stage.connect("auto")
                self.assertEqual(h.sim.received, ["ID"])
                self.assertEqual(h.dll.names()[-1], "fnPerformaxComClose")
                self.assertFalse(h.stage.connected)

    def test_the_allowlist(self):
        check = app.newmark_check_allowed
        # Homing is the test script's alone: the program never moves the zero.
        for command in ("STORE", "RW", "J+", "J-", "HL+", "HL-", "POL=0", "DRVMS=250", "DN=SDE02", "RT=1",
                        "SL=1", "ZH+", "T1", "@01ID", "X1.5", "HSPD=-5"):
            with self.subTest(command=command), self.assertRaises(app.NewmarkCommandNotAllowed):
                check(command)
        for command in ("ID", "MST", "PX", "EO", "DRVMS", "ABS", "STOP", "ABORT", "RR", "PX=0"):
            check(command)
        check("X3600000", 10000)    # +360 deg
        check("X-3600000", 10000)
        for command in ("X3600001", "HSPD=200001", "LSPD=9", "ACC=0", "ACC=5001"):
            with self.subTest(command=command), self.assertRaises(app.NewmarkCommandNotAllowed):
                check(command, 10000)
        with self.assertRaisesRegex(app.NewmarkCommandNotAllowed, "shorter than 64"):
            app.newmark_command_buffer("X" + "1" * 63)
        h = self.make()
        h.stage.connect("auto")
        before = list(h.sim.received)
        for command in ("STORE", "HL+", "X3600001"):
            with self.assertRaises(app.NewmarkCommandNotAllowed):
                h.stage.query(command)
        self.assertEqual(h.sim.received, before, "refused before it was sent")

    def test_a_scale_that_cannot_be_read_keeps_the_stage_connected_without_moves(self):
        sim = Sim()
        sim.registers["SL"] = 1
        h = self.make(sim)
        h.stage.connect("auto")
        self.assertTrue(h.stage.connected, "connected, so Stop works")
        self.assertIsNone(h.stage.steps_per_degree)
        self.assertIn("StepNLoop (SL=1)", h.stage.scale_error)
        self.assertNotIn("RR", h.sim.received)
        self.assertEqual(app.newmark_stage_text(h.stage.read_state()), "Stage: PX 0 (scale not read)")
        with self.assertRaisesRegex(app.NewmarkRefused, "StepNLoop"):
            h.stage.move_to_deg(5, threading.Event())
        self.assertEqual(h.sim.moves, [])
        sim.registers["SL"] = 0         # fixed in QuickMotion: the next move reads the scale
        h.stage.move_to_deg(5, threading.Event())
        self.assertEqual(h.sim.moves, ["X50000"])

    def test_absolute_moves_with_speeds_set_and_read_back(self):
        h = self.make()
        h.stage.connect("auto")
        start = len(h.sim.received)
        progress = []
        state = h.stage.move_to_deg(10, threading.Event(), on_progress=lambda px, deg: progress.append(deg))
        sent = h.sim.received[start:]
        self.assertEqual(sent[:10], ["ABS", "MM", "HSPD=50000", "LSPD=5000", "ACC=300", "HSPD", "LSPD", "ACC",
                                     "MST", "EO"], "5 deg/s, read back, then the before-move checks")
        self.assertEqual(sent[10:12], ["PX", "X100000"])
        self.assertEqual((state["px"], state["angle_deg"]), (100000, 10.0))
        self.assertEqual(h.sim.px, 100000)
        self.assertTrue(progress and progress[-1] <= 10.0 and len(progress) < 20, progress)
        # 350 -> 0 goes back -350 (absolute), never across 360.
        h.stage.move_to_deg(350, threading.Event())
        h.stage.move_to_deg(0, threading.Event())
        h.stage.move_to_deg(-360, threading.Event())
        self.assertEqual(h.sim.moves, ["X100000", "X3500000", "X0", "X-3600000"])
        # Already there: nothing is sent to move.
        h.stage.move_to_deg(-360, threading.Event())
        self.assertEqual(len(h.sim.moves), 4)

    def test_a_move_ends_after_idle_reads_with_px_at_the_target(self):
        h = self.make(LateMovingBits())
        h.stage.connect("auto")
        state = h.stage.move_to_deg(5, threading.Event())
        self.assertEqual(state["px"], 50000)
        self.assertFalse(h.stage.motion_may_run)

    def test_refusals_before_a_move(self):
        cases = (("moving", lambda sim: sim._start(sim.px + 10 ** 6, script._now()), "is moving"),
                 ("latched", lambda sim: setattr(sim, "latched", 1 << 6), "limit error is latched"),
                 ("input", lambda sim: setattr(sim, "inputs", 1 << 5), "polarity problem"),
                 ("EO=0", lambda sim: setattr(sim, "eo", 0), "driver is off"))
        for name, arrange, message in cases:
            with self.subTest(name):
                h = self.make()
                h.stage.connect("auto")
                arrange(h.sim)
                with self.assertRaisesRegex(app.NewmarkRefused, message):
                    h.stage.move_to_deg(5, threading.Event())
                self.assertEqual([m for m in h.sim.moves if m != "X1000000"], [])
                self.assertNotIn("X50000", h.sim.received)

    def test_the_wire_limit(self):
        h = self.make()
        h.stage.connect("auto")
        before = list(h.sim.received)
        for target in (360.001, -361, float("nan"), float("inf")):
            with self.subTest(target=target), self.assertRaisesRegex(app.NewmarkRefused, "±360°"):
                h.stage.move_to_deg(target, threading.Event())
        self.assertEqual(h.sim.received, before, "nothing sent")
        h.stage.move_to_deg(360, threading.Event())
        self.assertEqual(h.sim.px, 3600000)

    def test_the_stop_event_during_a_move_sends_stop_from_the_move_loop(self):
        h = self.make()
        h.stage.connect("auto")
        event = threading.Event()
        h.clock.hooks.append((h.clock.t + 2.0, event.set))
        with self.assertRaises(app.NewmarkStopRequested) as caught:
            h.stage.move_to_deg(90, event)
        self.assertTrue(caught.exception.stopped)
        self.assertIn("STOP", h.sim.received)
        self.assertNotIn("ABORT", h.sim.received)
        self.assertTrue(0 < caught.exception.px < 900000, caught.exception.px)
        self.assertEqual(h.sim.px, caught.exception.px, "where it stopped, read back")
        self.assertFalse(h.stage.in_move)
        # Set before the move: nothing is sent to move.
        with self.assertRaises(app.NewmarkStopRequested) as caught:
            h.stage.move_to_deg(0, event)
        self.assertTrue(caught.exception.before)
        self.assertEqual(h.sim.moves, ["X900000"])

    def test_a_stop_in_the_gap_after_x_is_confirmed_by_three_still_reads(self):
        # Stop just after X was answered, before the moving bits: the STOP does
        # nothing and the move then starts.  One "not moving" read would call it
        # stopped at 0 deg while it turns on to 30 deg unwatched (user, 2026-10-13).
        event = threading.Event()
        h = self.make(StopLostInTheGap(on_x=event.set))
        h.stage.connect("auto")
        with self.assertRaises(app.NewmarkStopRequested) as caught:
            h.stage.move_to_deg(30, event)
        self.assertTrue(caught.exception.stopped)
        self.assertIsNone(h.sim.motion, "watched until it really stopped")
        self.assertEqual(h.sim.px, 300000)
        self.assertEqual(caught.exception.px, 300000, "reported where it stopped, not at the start")
        self.assertIn("stopped (Stop) at +30.000°", str(caught.exception))
        self.assertFalse(h.stage.motion_may_run)
        self.assertNotIn("ABORT", h.sim.received)
        self.assertEqual(h.sim.received[-2 * app.NEWMARK_IDLE_READS:], ["MST", "PX"] * app.NEWMARK_IDLE_READS,
                         "still: three reads in a row of MST not moving, each with the same PX")

    def test_a_lost_reply_to_x_stops_the_move(self):
        h = self.make(ReplyLost("X900000"))
        h.stage.connect("auto")
        with self.assertRaisesRegex(app.NewmarkMotionError, r"X900000: the USB transaction failed \(it is not "
                                    r"repeated\); the move may have started: STOP sent; stopped at \+") as caught:
            h.stage.move_to_deg(90, threading.Event())
        error = caught.exception
        self.assertTrue(error.stop_ran, "the page must not stop it again")
        self.assertTrue(error.stopped)
        self.assertIsNone(h.sim.motion)
        self.assertTrue(0 < h.sim.px < 900000, h.sim.px)
        self.assertEqual(error.px, h.sim.px)
        self.assertEqual(h.sim.moves, ["X900000"], "never repeated")
        rest = h.sim.received[h.sim.received.index("X900000") + 1:]
        self.assertEqual(rest[:3], ["STOP", "ID", "VER"], "STOP first, then back in step")
        self.assertFalse(h.stage.motion_may_run)
        # STOP and ABORT do nothing: not confirmed, and the message says so.
        h = self.make(RunawayReplyLost("X900000"))
        h.stage.connect("auto")
        with self.assertRaisesRegex(app.NewmarkMotionError, "could not be confirmed stopped: CHECK THE STAGE") as caught:
            h.stage.move_to_deg(90, threading.Event())
        self.assertFalse(caught.exception.stopped)
        self.assertTrue(caught.exception.stop_ran)
        rest = h.sim.received[h.sim.received.index("X900000") + 1:]
        self.assertLess(rest.index("STOP"), rest.index("ABORT"))

    def test_a_time_out_stops_and_raises(self):
        h = self.make(Stuck())
        h.stage.connect("auto")
        with self.assertRaisesRegex(app.NewmarkMotionError, r"did not end within 7 s, stopped \(STOP\) at") as caught:
            h.stage.move_to_deg(1, threading.Event())    # 3 x (0.2 + 0.6) + 5 = 7.4 s
        self.assertTrue(caught.exception.stop_ran)
        self.assertTrue(caught.exception.stopped)
        self.assertEqual(h.sim.received.count("STOP"), 1)

    def test_px_off_the_target_is_a_failure(self):
        h = self.make(EndsShort())
        h.stage.connect("auto")
        with self.assertRaisesRegex(app.NewmarkMotionError, r"ended at \+4\.999° \(PX 49993\), not at the target "
                                                            r"\+5\.000° \(PX 50000, -7 steps\)") as caught:
            h.stage.move_to_deg(5, threading.Event())
        self.assertFalse(caught.exception.stop_ran, "it had ended: no STOP yet")
        self.assertNotIn("STOP", h.sim.received)

    def test_a_lost_reply_is_not_repeated_and_replies_are_resynced(self):
        h = self.make(LosesOne("EO"))
        h.stage.connect("auto")
        with self.assertRaisesRegex(app.NewmarkStageError, "EO: the USB transaction failed"):
            h.stage.move_to_deg(5, threading.Event())
        self.assertEqual(h.sim.moves, [])
        self.assertTrue(h.stage.need_resync)
        start = len(h.sim.received)
        h.stage.read_state()
        self.assertEqual(h.sim.received[start:], ["ID", "VER", "MST", "PX"], "flush, ID, VER, then the reads")
        self.assertEqual(h.sim.received.count("EO (lost)"), 1)
        self.assertEqual(h.sim.received.count("EO"), 0, "the lost command was not sent again")

    def test_set_zero(self):
        h = self.make()
        h.stage.connect("auto")
        h.stage.move_to_deg(12.5, threading.Event())
        self.assertEqual(h.stage.set_zero(), 125000)
        self.assertEqual(h.sim.received[-3:], ["PX", "PX=0", "PX"])
        self.assertEqual(h.sim.px, 0)
        h.sim._start(500000, script._now())
        with self.assertRaisesRegex(app.NewmarkRefused, "moving"):
            h.stage.set_zero()

    def test_disconnect_stops_a_moving_stage_first(self):
        h = self.make()
        h.stage.connect("auto")
        h.sim._start(3000000, script._now())    # turning, as after a lost stop
        h.stage.disconnect()
        self.assertIn("STOP", h.sim.received)
        names = h.dll.names()
        self.assertEqual(names[-1], "fnPerformaxComClose")
        self.assertFalse(h.stage.connected)
        self.assertIsNone(h.stage.steps_per_degree)
        h = self.make()
        h.stage.connect("auto")
        h.stage.disconnect()
        self.assertNotIn("STOP", h.sim.received, "idle: closed without STOP")
        self.assertEqual(h.dll.names()[-1], "fnPerformaxComClose")
        h.stage.disconnect()    # idempotent

    def test_stop_outside_a_move(self):
        h = self.make()
        h.stage.connect("auto")
        result = h.stage.stop()
        self.assertEqual((result["stopped"], result["px"], result["angle_deg"]), (True, 0, 0.0))
        self.assertIn("STOP", h.sim.received)

    def test_a_call_from_another_thread_never_reaches_the_dll(self):
        h = self.make()
        with ThreadPoolExecutor(max_workers=1, thread_name_prefix="rte-control") as worker:
            worker.submit(h.stage.connect, "auto").result(timeout=30)
            calls = len(h.dll.calls)
            for call in (h.stage.read_state, h.stage.stop, h.stage.disconnect, h.stage.set_zero,
                         lambda: h.stage.move_to_deg(5, threading.Event())):
                with self.assertRaisesRegex(app.NewmarkStageError, "another thread"):
                    call()
            self.assertEqual(len(h.dll.calls), calls)
            self.assertTrue(h.stage.connected)
            worker.submit(h.stage.disconnect).result(timeout=30)
        self.assertFalse(h.stage.connected)

    def test_an_old_dll_without_flush_still_works(self):
        h = self.make(missing=(app.NEWMARK_FLUSH,))
        h.stage.connect("auto")
        h.stage.move_to_deg(1, threading.Event())
        self.assertEqual(h.sim.px, 10000)
        self.assertEqual(sum("no fnPerformaxComFlush" in line for line in h.log), 1)


class NewmarkLoaderTests(unittest.TestCase):
    def test_load_errors_are_explained(self):
        explain = app.newmark_explain_load_error
        self.assertIn("not a 64-bit DLL", explain("C:/lab/PerformaxCom.dll",
                                                  OSError("[WinError 193] %1 is not a valid Win32 application")))
        for error in (OSError("[WinError 126] The specified module could not be found"),
                      # What Python 3.8+ raises for it on Windows: no winerror, no
                      # "[WinError 126]" (user, 2026-10-13).
                      FileNotFoundError("Could not find module 'C:\\lab\\run1930\\PerformaxCom.dll' (or one of its "
                                        "dependencies). Try using the full path with constructor syntax.")):
            with self.subTest(error=error):
                text = explain("C:\\lab\\run1930\\PerformaxCom.dll", error)
                self.assertIn("it, or a DLL it needs, was not found (Windows error 126)", text)
                self.assertIn("PerformaxCom.dll needs SiUSBXp.dll, and Python does not search PATH", text)
                self.assertIn("both 64-bit DLLs together in the program's folder", text)
        self.assertNotIn("error 126", explain("PerformaxCom.dll", PermissionError("[WinError 5] Access is denied")))
        # A path holding "193" is not WinError 193.
        self.assertNotIn("64-bit", explain("C:/193/PerformaxCom.dll", OSError("[WinError 5] Access is denied")))

    def test_the_real_loader_only_on_windows_and_64_bit(self):
        with patch.object(app.ctypes, "WinDLL", None, create=True):
            with self.assertRaisesRegex(app.NewmarkStageError, "Windows DLL"):
                REAL_LOADER()
        loaded = []

        class FakeWinDLL:
            def __init__(self, path):
                loaded.append(path)
                if path.endswith("PerformaxCom.dll"):
                    raise OSError("[WinError 193] %1 is not a valid Win32 application")

        with tempfile.TemporaryDirectory() as folder, \
                patch.object(app.ctypes, "WinDLL", FakeWinDLL, create=True), \
                patch.object(app.os, "add_dll_directory", Mock(return_value="cookie"), create=True) as add, \
                patch.object(app, "newmark_dll_folder", return_value=Path(folder)):
            for name in ("PerformaxCom.dll", "SiUSBXp.dll"):
                (Path(folder) / name).write_bytes(b"")
            with self.assertRaisesRegex(app.NewmarkStageError, "WinError 193.*not a 64-bit DLL"):
                REAL_LOADER()
            add.assert_called_once_with(folder)
            self.assertEqual([Path(p).name for p in loaded], ["SiUSBXp.dll", "PerformaxCom.dll"],
                             "SiUSBXp.dll first, from the same folder")
            with patch.object(app.ctypes, "sizeof", lambda _type: 4):
                with self.assertRaisesRegex(app.NewmarkStageError, "32-bit Python"):
                    REAL_LOADER()

        def not_found(path):
            raise FileNotFoundError(f"Could not find module '{path}' (or one of its dependencies). Try using the "
                                    "full path with constructor syntax.")

        # Not in the program's folder: the normal search, and Python 3.8+'s error 126.
        with patch.object(app.ctypes, "WinDLL", not_found, create=True), \
                patch.object(app, "newmark_dll_folder", return_value=None):
            with self.assertRaisesRegex(app.NewmarkStageError, r"Could not load PerformaxCom\.dll: it, or a DLL it "
                                        r"needs, was not found \(Windows error 126\)"):
                REAL_LOADER()


# ---- every DLL call on the RT-E worker -------------------------------------------
class StageThreadAffinityTests(StageBase):
    def test_every_dll_call_runs_on_the_rte_worker(self):
        executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="rte-control")
        self.addCleanup(executor.shutdown, wait=True)
        h = self.make(executor=executor)
        panel = h.panel
        panel._ppms_sr830_keys = lambda: ()     # the idle read: no SR830 to read here
        panel.ppms_transport_frequency_var = Var("17.3")
        panel.ppms_poll_inflight = False
        panel.ppms_current_live_var = Var("")
        panel.ppms_xy_vars = {}
        panel._read_ppms_current_display = Mock(return_value="Not connected")
        panel.root = Mock()
        panel.zurich_executor, panel.zurich_controller = Mock(), Mock()
        # What _rte_show_point updates for each scan point.
        panel.ppms_plot_addresses, panel.ppms_field_scan_total = {}, 5
        panel.ppms_field_scan_progress, panel.ppms_field_scan_status_var = Mock(), Var("")
        panel._redraw_ppms_transport_plot = Mock()
        worker = executor.submit(lambda: (threading.get_ident(), threading.current_thread().name)).result(30)
        with patch.object(app.messagebox, "showerror") as showerror, \
                patch.object(app.messagebox, "askyesno", return_value=True):
            panel._rte_connect("newmark")
            self.settle(h, executor)
            self.assertTrue(h.stage.connected)
            self.assertEqual(panel.rte_stage_live.get(), "Stage: +0.000° (10000 steps/°)")
            panel._ppms_poll_timer()                # the idle read: MST and PX
            self.settle(h, executor)
            panel.rte_stage_target.set("15")
            panel._rte_stage_move()
            self.settle(h, executor)
            self.assertEqual(panel.rte_stage_live.get(), "Stage: +15.000° (10000 steps/°)")
            panel._rte_stage_set_zero()
            self.settle(h, executor)
            panel._rte_stage_stop()                 # idle: STOP queued on the worker
            self.settle(h, executor)
            plan = app.rte_scan_plan("Angle", 0, 10, 5, 1, True)
            panel.rte_stage_stop_event.clear()      # as Start does
            executor.submit(panel._rte_harmonic_scan_worker, "Angle", plan, [self.settings()],
                            5., 0., .001, 1.5).result(timeout=30)
            self.settle(h, executor)
            self.assertEqual(len(panel.ppms_field_scan_rows), 5)
            self.assertEqual(panel.rte_stage_live.get(), "Stage: +0.000° (10000 steps/°)")
            panel._rte_disconnect("newmark")
            self.settle(h, executor)
            self.assertEqual(panel.rte_stage_live.get(), "Stage: not connected")
            panel._rte_connect("newmark")
            self.settle(h, executor)
            panel.safe_close_ppms()                 # the queued clean-up closes the stage
            executor.shutdown(wait=True)
        showerror.assert_not_called()
        self.assertFalse(h.stage.connected)
        names = h.dll.names()
        for name in ("fnPerformaxComOpen", "fnPerformaxComSendRecv", "fnPerformaxComClose"):
            self.assertIn(name, names)
        self.assertEqual(names[-1], "fnPerformaxComClose")
        threads = {thread for _name, thread in h.dll.calls}
        self.assertNotIn(threading.get_ident(), threads, "a DLL call ran on the Tk (test) thread")
        self.assertEqual(threads, {worker[0]})
        self.assertTrue(worker[1].startswith("rte-control"))
        for command in ("X150000", "PX=0", "STOP", "X50000", "X100000", "X0"):
            self.assertIn(command, h.sim.received)


# ---- the scan plan ---------------------------------------------------------------
class AnglePlanTests(unittest.TestCase):
    def test_angle_nodes_within_the_wire_limit(self):
        plan = app.rte_scan_plan("Angle", 0, 360, 5, 1, False)
        self.assertEqual(len(plan), 73)
        self.assertEqual((plan[0][2], plan[-1][2]), (0., 360.))
        trip = app.rte_scan_plan("Angle", 0, 360, 5, 2, True)
        self.assertEqual(len(trip), 2 * 145)
        self.assertEqual([p[2] for p in trip[143:146]], [5., 0., 0.])
        self.assertEqual(trip[145], (2, 1, 0.))
        self.assertEqual(len(app.rte_scan_plan("Angle", -360, 360, 10)), 145)
        self.assertEqual(app.rte_scan_plan("Angle", 90, 90, 5), ((1, 1, 90.),))
        for args in (("Angle", 0, 360.5, 5), ("Angle", -361, 0, 1), ("Angle", 0, 400, 100)):
            with self.subTest(args=args), self.assertRaisesRegex(ValueError, r"within \+/-360° of the stage zero"):
                app.rte_scan_plan(*args)
        for args in (("Angle", 0, 10, 0), ("Angle", 0, 10, 5, 0), ("Temperature", 0, 1, 1)):
            with self.subTest(args=args), self.assertRaises(ValueError):
                app.rte_scan_plan(*args)

    def test_field_and_current_plans_unchanged(self):
        self.assertEqual(app.rte_scan_plan("Field", -1, 1, 1), ((1, 1, -1.), (1, 2, 0.), (1, 3, 1.),
                                                                (1, 4, 0.), (1, 5, -1.)))
        self.assertEqual(len(app.rte_scan_plan("Current", .01, 1, .01, 1, False)), 100)
        with self.assertRaisesRegex(ValueError, "10 A"):
            app.rte_scan_plan("Field", 0, 11, 1)


# ---- the Angle scan worker, end to end --------------------------------------------
class AngleScanWorkerTests(StageBase):
    def run_scan(self, sim=None, plan=None, passes=None, h=None):
        h = h or self.make(sim)
        if not h.stage.connected:
            h.stage.connect("auto")
        plan = plan or app.rte_scan_plan("Angle", 0, 10, 5, 1, True)
        h.panel._rte_harmonic_scan_worker("Angle", plan, passes or [self.settings()], 5., 0., .001, 1.5)
        return h

    def test_rows_columns_and_the_field_set_once(self):
        h = self.run_scan()
        panel, kepco = h.panel, h.panel.rte_controllers["kepco"]
        kepco.set_output.assert_called_once_with("CURR", 1.5, 5.)
        kepco.set_scan_current.assert_not_called()
        kepco.safe_output_off.assert_not_called()
        panel._rte_settle_field.assert_called_once()
        panel._configure_ppms_transport_scan.assert_called_once()
        panel._acquire_ppms_first_point.assert_called_once()
        self.assertEqual(panel._average_ppms_transport_sample.call_count, 4)
        self.assertEqual(h.sim.moves, ["X50000", "X100000", "X50000", "X0"], "0 is already there: no move")
        rows = panel.ppms_field_scan_rows
        self.assertEqual([r["stage_target_deg"] for r in rows], [0., 5., 10., 5., 0.])
        self.assertEqual([r["stage_angle_deg"] for r in rows], [0., 5., 10., 5., 0.])
        self.assertEqual([r["stage_position_steps"] for r in rows], [0, 50000, 100000, 50000, 0])
        self.assertTrue(all(r["stage_steps_per_degree"] == 10000 for r in rows))
        self.assertTrue(all(r["scan_mode"] == "Angle" for r in rows))
        self.assertTrue(all(r["kepco_programmed_current_a"] == 1.5 for r in rows))
        self.assertTrue(all(r["kepco_voltage_limit_v"] == 5. for r in rows))
        self.assertTrue(all(r["measured_field_g"] == -2. for r in rows))
        keys = list(rows[0])
        self.assertEqual(keys[keys.index("measured_field_g") + 1:keys.index("stable_s")],
                         ["stage_target_deg", "stage_angle_deg", "stage_position_steps", "stage_steps_per_degree"])
        panel._stop_ppms_transport_source.assert_not_called()
        self.assertEqual(panel.rte_stage_where, "stage at +0.000°")
        self.assertTrue(h.ui, "points and stage angles are posted to the Tk thread")

    def test_the_stage_line_shows_where_each_move_ended_before_settle_and_acquisition(self):
        # The last progress update ("…, moving") can come during the
        # deceleration; the end of the move is posted at once, not only when
        # the point is saved (review, 2026-10-14).
        h = self.make()
        h.stage.connect("auto")
        panel = h.panel
        panel._rte_show_point = Mock()      # the saved point's own update is tested elsewhere
        seen, shown = [], []

        def wait(seconds):
            # Settle s, right after the move: 8 s, longer than the 7 x 1 s
            # lock-in settle, so it is the wait (user, 2026-10-14).
            if seconds == 8.:
                while h.ui:                 # what the Tk thread shows by then
                    h.ui.pop(0)()
                    shown.append(panel.rte_stage_live.get())
                seen.append(panel.rte_stage_live.get())
        panel._rte_wait = wait
        panel._rte_harmonic_scan_worker("Angle", app.rte_scan_plan("Angle", 0, 10, 5, 1, False), [self.settings()],
                                        5., 8., .001, 1.5)
        self.assertEqual(seen, ["Stage: +0.000° (10000 steps/°)", "Stage: +5.000° (10000 steps/°)",
                                "Stage: +10.000° (10000 steps/°)"])
        self.assertTrue([text for text in shown if text.endswith(", moving")], "the moves' progress came first")

    def test_harmonic_passes_repeat_the_angles_without_resetting_the_field(self):
        h = self.make()
        panel = h.panel
        panel.zurich_scan_enabled = False
        setattr(panel, app.PPMS_SR830_HARMONIC_ATTRS["830_a"], Var("1+2"))
        panel._rte_set_harmonic = Mock()
        settings = self.settings()
        passes = panel._rte_harmonic_passes(settings)
        self.assertEqual(len(passes), 2)
        self.run_scan(passes=passes, h=h)
        rows = panel.ppms_field_scan_rows
        self.assertEqual([r["harmonic_pass"] for r in rows], [1] * 5 + [2] * 5)
        self.assertEqual([r["stage_target_deg"] for r in rows], [0., 5., 10., 5., 0.] * 2)
        panel.rte_controllers["kepco"].set_output.assert_called_once()
        panel._rte_settle_field.assert_called_once()
        panel._configure_ppms_transport_scan.assert_called_once()
        self.assertEqual(panel._acquire_ppms_first_point.call_count, 2)

    def test_stop_during_a_move_sends_stop_and_ends_as_stopped(self):
        h = self.make()
        panel = h.panel

        def first_point(*_args):
            # Stop is pressed 0.6 s into the next move (0 -> 5 deg, 1.3 s).
            h.clock.hooks.append((h.clock.t + 0.6, panel.ppms_field_scan_stop_event.set))
            return panel._acquire_ppms_first_point.return_value
        panel._acquire_ppms_first_point.side_effect = first_point
        with self.assertRaisesRegex(app.PPMSScanCancelled, r"stage stopped at \+"):
            self.run_scan(h=h)
        self.assertIn("STOP", h.sim.received)
        self.assertEqual(len(panel.ppms_field_scan_rows), 1)
        self.assertTrue(panel.rte_stage_where.startswith("stage stopped at +"), panel.rte_stage_where)
        self.assertTrue(0 < h.sim.px < 50000)
        panel._stop_ppms_transport_source.assert_not_called()
        panel.rte_controllers["kepco"].safe_output_off.assert_not_called()

    def test_stop_stage_during_acquisition_ends_the_scan_before_the_next_move(self):
        h = self.make()
        panel = h.panel

        def first_point(*_args):
            panel.rte_stage_stop_event.set()        # Stop stage while the first point is acquired
            return panel._acquire_ppms_first_point.return_value
        panel._acquire_ppms_first_point.side_effect = first_point
        with self.assertRaisesRegex(app.PPMSScanCancelled, r"stage at \+0\.000°"):
            self.run_scan(h=h)
        self.assertEqual(h.sim.moves, [])
        panel._stop_ppms_transport_source.assert_not_called()

    def test_a_stage_error_stops_the_6221_and_keeps_the_field(self):
        sim = Sim()
        h = self.make(sim)
        h.stage.connect("auto")
        sim.eo = 0      # the driver switched off: the first move is refused
        with self.assertRaisesRegex(app.NewmarkRefused, "driver is off"):
            self.run_scan(h=h, plan=app.rte_scan_plan("Angle", 5, 10, 5, 1, False))
        panel = h.panel
        panel._stop_ppms_transport_source.assert_called_once()
        panel.rte_controllers["kepco"].safe_output_off.assert_not_called()
        self.assertIn("STOP", h.sim.received, "a stage failure sends STOP")
        self.assertEqual(h.sim.moves, [])
        self.assertEqual(panel.ppms_field_scan_rows, [])

    def test_px_off_the_target_stops_the_6221_sends_stop_and_keeps_the_field(self):
        h = self.make(EndsShort())
        with self.assertRaisesRegex(app.NewmarkMotionError, "not at the target"):
            self.run_scan(h=h)
        panel = h.panel
        self.assertEqual(len(panel.ppms_field_scan_rows), 1, "the point at 0 deg (no move) was saved")
        self.assertEqual(h.sim.received.count("STOP"), 1)
        panel._stop_ppms_transport_source.assert_called_once()
        panel.rte_controllers["kepco"].safe_output_off.assert_not_called()
        self.assertTrue(panel.rte_stage_where.startswith("stage stopped at +4.999"), panel.rte_stage_where)

    def test_a_stage_time_out_is_stopped_once(self):
        h = self.make(Stuck())
        with self.assertRaisesRegex(app.NewmarkMotionError, "did not end within"):
            self.run_scan(h=h)
        self.assertEqual(h.sim.received.count("STOP"), 1, "the driver's STOP; the page does not repeat it")
        h.panel._stop_ppms_transport_source.assert_called_once()
        h.panel.rte_controllers["kepco"].safe_output_off.assert_not_called()

    def test_a_stop_in_the_gap_after_x_ends_the_scan_where_the_stage_really_stopped(self):
        sim = StopLostInTheGap()
        h = self.make(sim)
        sim.on_x = h.panel.ppms_field_scan_stop_event.set      # the scan's Stop, just after X
        h.stage.connect("auto")
        with self.assertRaisesRegex(app.PPMSScanCancelled, r"stage stopped at \+5\.000°"):
            self.run_scan(h=h)
        self.assertIsNone(sim.motion, "no move left running unwatched")
        self.assertEqual(sim.px, 50000)
        self.assertEqual(h.panel.rte_stage_where, "stage stopped at +5.000°")
        self.assertEqual(sim.moves, ["X50000"])

    def test_a_lost_reply_to_x_is_stopped_once_and_logged(self):
        h = self.make(ReplyLost("X50000"))
        with self.assertRaisesRegex(app.NewmarkMotionError, "X50000: the USB transaction failed"):
            self.run_scan(h=h)
        panel = h.panel
        self.assertEqual(h.sim.received.count("STOP"), 1, "the driver's STOP; the page does not repeat it")
        self.assertIsNone(h.sim.motion)
        self.assertTrue(panel.rte_stage_where.startswith("stage stopped at +"), panel.rte_stage_where)
        panel.thread_log.assert_any_call(f"RT-E Angle scan, after the stage failure: {panel.rte_stage_where}")
        panel._stop_ppms_transport_source.assert_called_once()
        panel.rte_controllers["kepco"].safe_output_off.assert_not_called()

    def test_a_stop_that_is_not_confirmed_is_logged(self):
        h = self.make(RunawayReplyLost("X50000"))
        with self.assertRaisesRegex(app.NewmarkMotionError, "CHECK THE STAGE"):
            self.run_scan(h=h)
        panel = h.panel
        self.assertEqual(panel.rte_stage_where, "stage NOT confirmed stopped: CHECK THE STAGE")
        panel.thread_log.assert_any_call("RT-E Angle scan, after the stage failure: stage NOT confirmed stopped: "
                                         "CHECK THE STAGE")
        panel._stop_ppms_transport_source.assert_called_once()
        # The page's own STOP (a failure before X, so the driver's did not run).
        h = self.make(Runaway())
        h.stage.connect("auto")
        h.sim._start(3000000, script._now())        # turning, as after a lost stop
        h.panel._rte_stop_stage_after_failure(app.NewmarkRefused("the stage is moving"))
        self.assertEqual(h.panel.rte_stage_where, "stage NOT confirmed stopped: CHECK THE STAGE")
        self.assertIn("ABORT", h.sim.received)
        h.panel.thread_log.assert_any_call("RT-E Angle scan, after the stage failure: stage NOT confirmed stopped: "
                                           "CHECK THE STAGE")

    def test_a_field_drift_switches_the_kepco_off(self):
        h = self.make()
        h.panel._rte_read_field.side_effect = [-2., -2., -2., 30.]
        with self.assertRaisesRegex(app.PPMSFieldScanError, "drifted"):
            self.run_scan(h=h)
        h.panel.rte_controllers["kepco"].safe_output_off.assert_called_once()
        h.panel._stop_ppms_transport_source.assert_called_once()
        self.assertEqual(len(h.panel.ppms_field_scan_rows), 1)
        self.assertNotIn("STOP", h.sim.received, "the stage was not moving")

    def test_a_field_that_does_not_settle_switches_the_kepco_off_before_any_move(self):
        h = self.make()
        h.panel._rte_settle_field.side_effect = app.PPMSFieldScanError("LS475 field did not settle")
        with self.assertRaisesRegex(app.PPMSFieldScanError, "did not settle"):
            self.run_scan(h=h)
        self.assertEqual(h.sim.moves, [])
        h.panel.rte_controllers["kepco"].safe_output_off.assert_called_once()
        h.panel._stop_ppms_transport_source.assert_called_once()

    def test_a_field_read_failure_switches_the_kepco_off(self):
        h = self.make()
        h.panel._rte_read_field.side_effect = [-2., RuntimeError("LS475 unavailable")]
        with self.assertRaisesRegex(RuntimeError, "LS475 unavailable"):
            self.run_scan(h=h)
        h.panel.rte_controllers["kepco"].safe_output_off.assert_called_once()

    def test_field_and_current_rows_have_no_stage_columns(self):
        for mode, plan in (("Field", ((1, 1, -1.), (1, 2, 1.))), ("Current", ((1, 1, .1), (1, 2, .2)))):
            with self.subTest(mode=mode):
                panel = rte_tests.RTE.make_worker(self)
                panel._rte_scan_worker(mode, plan, self.settings(), 5., 0., .001)
                for row in panel.ppms_field_scan_rows:
                    self.assertFalse([key for key in row if key.startswith("stage_")])
                self.assertEqual(panel.ppms_field_scan_rows[0]["kepco_voltage_limit_v"],
                                 5. if mode == "Field" else "")


# ---- Move / Stop / Set 0 by hand ---------------------------------------------------
class ManualStageControlTests(StageBase):
    def test_move_refusals(self):
        cases = (("no safety tick", dict(safety=False), "safety confirmation"),
                 ("not connected", dict(connect=False), "Connect the Newmark NSC-A1 first"),
                 ("beyond the wire limit", dict(target="370"), "±360°"),
                 ("not a number", dict(target="ten"), "could not convert"),
                 ("busy", dict(busy=True), "busy"))
        for name, options, message in cases:
            with self.subTest(name):
                h = self.make()
                if options.get("connect", True):
                    h.stage.connect("auto")
                h.panel.ppms_control_safety_var = Var(options.get("safety", True))
                h.panel.ppms_environment_busy = options.get("busy", False)
                h.panel.rte_stage_target.set(options.get("target", "10"))
                h.panel.ppms_executor = Mock()
                with patch.object(app.messagebox, "showerror") as showerror:
                    h.panel._rte_stage_move()
                showerror.assert_called_once()
                self.assertIn(message, showerror.call_args.args[1])
                h.panel.ppms_executor.submit.assert_not_called()
                self.assertEqual(h.sim.moves, [])

    def test_move_by_hand(self):
        h = self.make()
        h.stage.connect("auto")
        h.panel.rte_stage_target.set("-45")
        h.panel.rte_stage_stop_event.set()      # left over from an earlier Stop: cleared by Move
        h.panel._rte_stage_move()
        self.drain(h)
        self.assertEqual(h.sim.moves, ["X-450000"])
        self.assertEqual(h.panel.rte_stage_live.get(), "Stage: -45.000° (10000 steps/°)")
        self.assertFalse(h.panel.ppms_environment_busy)

    def test_a_lost_reply_to_x_by_hand_stops_the_move(self):
        # X ran but its reply was lost: STOP, as in a scan (user, 2026-10-13).
        h = self.make(ReplyLost("X900000"))
        h.stage.connect("auto")
        h.panel.rte_stage_target.set("90")
        with patch.object(app.messagebox, "showerror") as showerror:
            h.panel._rte_stage_move()
            self.drain(h)
        showerror.assert_called_once()
        self.assertIn("X900000: the USB transaction failed", showerror.call_args.args[1])
        self.assertIn("the move may have started: STOP sent; stopped at +", showerror.call_args.args[1])
        self.assertIsNone(h.sim.motion, "not left turning")
        self.assertEqual(h.sim.received.count("STOP"), 1)
        self.assertEqual(h.sim.moves, ["X900000"])
        self.assertFalse(h.stage.motion_may_run)
        self.assertFalse(h.panel.ppms_environment_busy)

    def test_off_buttons_during_a_move_by_hand_stop_it_and_run_next(self):
        # KEPCO Zero / OFF and 6221 / 2400 OFF queue their OFF behind the move on
        # the one worker: the move sees the scan's stop event and stops, so the OFF
        # runs within a poll and a stop, not after a 72 s turn (user, 2026-10-13).
        for button in ("KEPCO Zero / OFF", "6221 / 2400 OFF"):
            with self.subTest(button=button):
                executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="rte-control")
                self.addCleanup(executor.shutdown, wait=True)
                h = self.make(executor=executor)
                panel = h.panel
                executor.submit(h.stage.connect, "auto").result(timeout=30)
                seen = []

                def off_runs(*_args, h=h, seen=seen):
                    seen.append((h.clock.t, list(h.sim.received), h.sim.motion, h.sim.px))

                if button.startswith("KEPCO"):
                    panel.rte_controllers["kepco"].force_output_off.side_effect = off_runs
                    press = panel._rte_zero
                else:
                    class Lock:
                        def __enter__(self_, off_runs=off_runs):
                            off_runs()
                            return self_

                        def __exit__(self_, *_exc):
                            return False
                    panel.ppms_instrument_controllers = {key: Mock(instrument=None, lock=Lock())
                                                         for key in ("6221", "2400")}
                    panel.ppms_connection_var, panel.ppms_current_live_var = Var(""), Var("")
                    press = panel.ppms_sources_off
                reached, proceed, pressed_at = threading.Event(), threading.Event(), []

                def hold(h=h, reached=reached, proceed=proceed, pressed_at=pressed_at):
                    pressed_at.append(h.clock.t)
                    reached.set()
                    proceed.wait(10)
                h.clock.hooks.append((h.clock.t + 1.0, hold))   # 1 s into a 72 s turn
                panel.rte_stage_target.set("360")
                panel._rte_stage_move()
                self.assertTrue(reached.wait(10))
                press()                 # the Tk thread: sets the scan's stop event, queues OFF
                proceed.set()
                self.settle(h, executor)
                self.assertTrue(seen, "the OFF ran")
                off_time, received, motion, px = seen[0]
                self.assertIn("STOP", received[received.index("X3600000"):], "STOP sent before the OFF")
                self.assertIsNone(motion, "the stage had stopped when the OFF ran")
                self.assertLess(off_time - pressed_at[0], 1.0, "the OFF ran within a poll and a stop")
                self.assertTrue(0 < px < 100000, px)
                self.assertNotIn("ABORT", h.sim.received)
                self.assertTrue(panel.rte_stage_live.get().startswith("Stage: stopped at +"),
                                panel.rte_stage_live.get())
                self.assertTrue(panel.rte_stage_live.get().endswith("(OFF)"), panel.rte_stage_live.get())
                self.assertFalse(panel.ppms_environment_busy)

    def test_connect_with_a_failed_first_read_still_says_connected(self):
        # RR's reply is lost after it ran: the scale is not read, and the first
        # read falls in RR's 2 s blackout.  The handle is open, so the lamp,
        # which follows it, says connected (user, 2026-10-13), and the stage
        # line that the read failed.
        h = self.make(ReplyLost("RR"))
        with patch.object(app.messagebox, "showerror") as showerror:
            h.panel._rte_connect("newmark")
            self.drain(h)
        showerror.assert_not_called()
        self.assertTrue(h.stage.connected)
        self.assertTrue(h.panel.rte_stage_live.get().startswith("Stage read failed: ID: the USB transaction failed"),
                        h.panel.rte_stage_live.get())
        self.assertTrue(any("the first read after Connect failed" in str(call.args[0])
                            for call in h.panel.thread_log.call_args_list))

    def test_a_disconnect_that_closed_without_a_confirmed_stop_says_not_connected(self):
        h = self.make(Runaway())
        h.stage.connect("auto")
        h.panel.rte_stage_live.set("Stage: +0.000° (10000 steps/°)")
        h.sim._start(3000000, script._now())        # turning, and STOP and ABORT do nothing
        with patch.object(app.messagebox, "showerror") as showerror:
            h.panel._rte_disconnect("newmark")
            self.drain(h)
        self.assertFalse(h.stage.connected)
        self.assertEqual(h.panel.rte_stage_live.get(), "Stage: closed, STOP not confirmed")
        showerror.assert_called_once()
        self.assertIn("could not be confirmed stopped", showerror.call_args.args[1])
        self.assertIn("CHECK THE STAGE", showerror.call_args.args[1])
        self.assertLess(h.sim.received.index("STOP"), h.sim.received.index("ABORT"))
        # Refused while still open (a call from another thread): the stage
        # line keeps the reading, and the lamp stays on (from the handle).
        h = self.make()
        with ThreadPoolExecutor(max_workers=1) as other:
            other.submit(h.stage.connect, "auto").result(timeout=30)
            h.panel.rte_stage_live.set("Stage: +0.000° (10000 steps/°)")
            with patch.object(app.messagebox, "showerror") as showerror:
                h.panel._rte_disconnect("newmark")
                self.drain(h)
            self.assertIn("another thread", showerror.call_args.args[1])
            self.assertTrue(h.stage.connected)
            self.assertEqual(h.panel.rte_stage_live.get(), "Stage: +0.000° (10000 steps/°)")
            other.submit(h.stage.disconnect).result(timeout=30)

    def test_set_zero_asks_first(self):
        h = self.make()
        h.stage.connect("auto")
        h.stage.move_to_deg(30, threading.Event())
        with patch.object(app.messagebox, "askyesno", return_value=False) as askyesno:
            h.panel._rte_stage_set_zero()
        question = askyesno.call_args.args[1]
        self.assertIn("±360° limit for the sample wires", question)
        self.assertIn("moves with it", question)
        self.assertIn("powered off", question)
        self.assertNotIn("PX=0", h.sim.received)
        with patch.object(app.messagebox, "askyesno", return_value=True):
            h.panel._rte_stage_set_zero()
        self.drain(h)
        self.assertIn("PX=0", h.sim.received)
        self.assertEqual(h.sim.px, 0)
        self.assertEqual(h.panel.rte_stage_live.get(), "Stage: +0.000° (10000 steps/°)")
        # Busy: nothing; not connected: a message, no question.
        h.panel.ppms_environment_busy = True
        h.panel._rte_stage_set_zero()
        h.panel.ppms_environment_busy = False
        h.stage.disconnect()
        with patch.object(app.messagebox, "showerror") as showerror:
            h.panel._rte_stage_set_zero()
        self.assertIn("Connect the Newmark NSC-A1 first", showerror.call_args.args[1])

    def test_stop_stage_while_a_move_runs_sends_stop_from_the_worker(self):
        executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="rte-control")
        self.addCleanup(executor.shutdown, wait=True)
        h = self.make(executor=executor)
        executor.submit(h.stage.connect, "auto").result(timeout=30)
        reached, proceed = threading.Event(), threading.Event()

        def hold():
            reached.set()
            proceed.wait(10)
        h.clock.hooks.append((h.clock.t + 1.0, hold))   # 1 s into a 18 s turn
        h.panel.rte_stage_target.set("90")
        h.panel._rte_stage_move()
        self.assertTrue(reached.wait(10))
        self.assertTrue(h.panel.ppms_environment_busy, "a move keeps the rack busy")
        self.assertTrue(h.stage.in_move)
        calls = len(h.dll.calls)
        h.panel._rte_stage_stop()           # the Tk thread only sets the event
        self.assertEqual(len(h.dll.calls), calls)
        self.assertEqual(h.panel.rte_stage_live.get(), "Stage: stopping…")
        proceed.set()
        self.settle(h, executor)
        self.assertEqual(h.sim.received.count("STOP"), 1)
        self.assertTrue(h.panel.rte_stage_live.get().startswith("Stage: stopped at +"), h.panel.rte_stage_live.get())
        self.assertTrue(h.panel.rte_stage_live.get().endswith("(Stop)"))
        self.assertFalse(h.panel.ppms_environment_busy)
        self.assertNotIn(threading.get_ident(), {thread for _name, thread in h.dll.calls})

    def test_stop_stage_when_idle_queues_stop(self):
        h = self.make()
        h.stage.connect("auto")
        h.panel._rte_stage_stop()
        self.drain(h)
        self.assertIn("STOP", h.sim.received)
        self.assertEqual(h.panel.rte_stage_live.get(), "Stage: stopped at +0.000°")
        h.panel.thread_log.assert_any_call("RT-E Newmark NSC-A1: Stop stage: stopped at +0.000°")
        # Not connected: only the event.
        h = self.make()
        h.panel.ppms_executor = Mock()
        h.panel._rte_stage_stop()
        h.panel.ppms_executor.submit.assert_not_called()
        self.assertTrue(h.panel.rte_stage_stop_event.is_set())

    def test_stop_stage_when_idle_that_cannot_confirm_the_stop_logs_it_and_shows_a_dialog(self):
        # The rack is not busy for this stop, so the idle poll behind it
        # replaces the live line at once: the warning must also go to the log
        # and a dialog (review, 2026-10-14).
        h = self.make(Runaway())
        h.stage.connect("auto")
        h.sim._start(3000000, script._now())        # left turning by an earlier unconfirmed stop
        with patch.object(app.messagebox, "showerror") as showerror:
            h.panel._rte_stage_stop()
            self.drain(h)
        self.assertLess(h.sim.received.index("STOP"), h.sim.received.index("ABORT"))
        self.assertTrue(h.stage.motion_may_run, "still not confirmed stopped")
        self.assertEqual(h.panel.rte_stage_live.get(), "Stage: STOP not confirmed: CHECK THE STAGE")
        showerror.assert_called_once()
        title, message = showerror.call_args.args
        self.assertEqual(title, "RT-E stage")
        self.assertIn("STOP (then ABORT) sent, but the stage could not be confirmed stopped: CHECK THE STAGE",
                      message)
        self.assertIn("24 V off", message)
        h.panel.thread_log.assert_any_call(f"RT-E Newmark NSC-A1: {message}")
        # STOP not sent at all (here refused as a call from another thread):
        # the same log line and dialog.
        h = self.make()
        h.stage.connect("auto")
        h.stage._thread_id = object()
        with patch.object(app.messagebox, "showerror") as showerror:
            h.panel._rte_stage_stop()
            self.drain(h)
        self.assertNotIn("STOP", h.sim.received)
        self.assertTrue(h.panel.rte_stage_live.get().startswith("Stage STOP failed: "), h.panel.rte_stage_live.get())
        showerror.assert_called_once()
        message = showerror.call_args.args[1]
        self.assertIn("STOP was not sent", message)
        self.assertIn("another thread", message)
        self.assertIn("CHECK THE STAGE", message)
        h.panel.thread_log.assert_any_call(f"RT-E Newmark NSC-A1: {message}")
        h.stage._thread_id = None

    def test_stop_stage_during_a_scan_between_moves_only_sets_the_event(self):
        h = self.make()
        h.stage.connect("auto")
        h.panel.ppms_field_scan_running = True
        h.panel.ppms_executor = Mock()
        h.panel._rte_stage_stop()
        h.panel.ppms_executor.submit.assert_not_called()     # never a STOP queued behind the scan
        self.assertTrue(h.panel.rte_stage_stop_event.is_set())
        self.assertEqual(h.panel.rte_stage_live.get(), "Stage: Stop pressed; no further move")


# ---- Start (Angle), status lines, defaults, plots ---------------------------------
class AngleStartTests(unittest.TestCase):
    def start(self, mode="Angle", yes=True, stage=True, kepco=True, gauss=True, scale=10000,
              span=("0", "10", "5"), drive="1.5", limit="5", executor=None, may_run=False):
        """The real RTEPanel.start_ppms_field_scan up to its submit (as in
        test_ppms_rte_review), with every dialog answered here."""
        panel = rte_tests.RTE.make_worker(self)
        panel._require_ppms_control_ready = Mock()
        panel._rte_capture_zurich = Mock()
        panel.zurich_scan_enabled = False
        panel.ppms_simple_mode = Var(mode)
        panel.ppms_simple_range = [Var(v) for v in span]
        panel.ppms_field_scan_loop_var = Var("1")
        panel.rte_round_trip = Var(True)
        panel.rte_controllers["gauss"].connected = gauss
        panel.rte_controllers["kepco"].connected = kepco
        panel.rte_controllers["newmark"] = Mock(connected=stage, steps_per_degree=scale, motion_may_run=may_run,
                                                scale_error="SL=1", label="Newmark NSC-A1")
        panel.rte_stage_stop_event = threading.Event()
        panel.rte_stage_stop_event.set()
        panel.rte_drive_target, panel.rte_voltage_limit = Var(drive), Var(limit)
        settings = app.validate_ppms_transport_scan_settings(.1, 17.3, 50., 1, 2, ("830_a",))
        panel._ppms_direct_transport_settings = Mock(return_value=settings)
        panel._rte_harmonic_passes = Mock(return_value=[settings])
        panel.ppms_field_scan_stable_var = Var("0")
        panel.ppms_acquisition_time_var = Var("1")
        panel.ppms_field_scan_progress = Mock()
        panel.ppms_field_scan_status_var = Var("")
        panel._update_ppms_control_ui = Mock()
        panel._redraw_ppms_transport_plot = Mock()
        panel.thread_log = Mock()
        panel.ppms_executor = Mock() if executor is None else executor
        panel.post_ui = lambda callback: callback()
        panel.root = None
        with tempfile.TemporaryDirectory() as folder, \
                patch.object(app.filedialog, "askdirectory", return_value=folder) as askdirectory, \
                patch.object(app.simpledialog, "askstring", return_value=""), \
                patch.object(app.messagebox, "showerror") as showerror, \
                patch.object(app.messagebox, "askyesno", return_value=yes) as askyesno:
            panel.start_ppms_field_scan()
        return types.SimpleNamespace(panel=panel, showerror=showerror, askyesno=askyesno,
                                     askdirectory=askdirectory)

    def test_angle_needs_the_stage_the_kepco_and_the_ls475(self):
        for options, message in ((dict(stage=False), "Connect the Newmark NSC-A1"),
                                 (dict(kepco=False), "Connect KEPCO for an angle scan"),
                                 (dict(gauss=False), "Connect LS475"),
                                 (dict(scale=None), r"stage scale was not read \(SL=1\)"),
                                 (dict(span=("0", "365", "5")), "within"),
                                 (dict(drive="12"), "10 A"),
                                 (dict(limit="25"), "20 V")):
            with self.subTest(options=options):
                run = self.start(**options)
                run.showerror.assert_called_once()
                self.assertEqual(run.showerror.call_args.args[0], "RT-E scan parameters")
                self.assertRegex(run.showerror.call_args.args[1], message)
                run.askyesno.assert_not_called()
                run.panel.ppms_executor.submit.assert_not_called()

    def test_the_confirmation_and_the_submitted_scan(self):
        run = self.start(yes=False)
        question = run.askyesno.call_args.args[1]
        for text in ("from 0° to 10° (step 5°, round trip)", "5 angles x 1 harmonic pass(es) = 5 points",
                     "set once to +1.5 A (voltage limit 5 V)", "held for the whole scan",
                     "SAMPLE HOLDER with its wires attached", "within ±360°",
                     # The wait before each reading, and the shortest the
                     # scan can take: 5 x (7 s + 1 s) + 1 s (user, 2026-10-14).
                     "Each angle waits 7 s (7 x 1 s, the time constant of SR830-1, the slowest lock-in "
                     "measured; Settle s is 0 s) before it is read; the scan takes at least 41 s."):
            self.assertIn(text, question)
        run.askdirectory.assert_not_called()
        run.panel.ppms_executor.submit.assert_not_called()
        run = self.start(yes=True)
        args = run.panel.ppms_executor.submit.call_args.args
        self.assertEqual(args[1], "Angle")
        self.assertEqual([p[2] for p in args[2]], [0., 5., 10., 5., 0.])
        self.assertEqual((args[4], args[7]), (5., 1.5), "voltage limit, then the held KEPCO current")
        self.assertEqual(run.panel.ppms_plot_axis, "angle")
        self.assertFalse(run.panel.rte_stage_stop_event.is_set(), "an old Stop stage does not stop the new scan")
        # Without "stage turns sample and wires" (the Yes/No says it): the full
        # line did not fit the status label on the CI (user, 2026-10-13).
        self.assertEqual(run.panel.ppms_field_scan_status_var.get(),
                         "RT-E Angle 0° to 10°, KEPCO +1.5 A held; 5 points")

    def test_field_and_current_starts_unchanged(self):
        for mode in ("Field", "Current"):
            with self.subTest(mode=mode):
                run = self.start(mode=mode, span=("0.01", "0.03", "0.01"), stage=False)
                run.showerror.assert_not_called()
                run.askyesno.assert_not_called()
                self.assertEqual(len(run.panel.ppms_executor.submit.call_args.args), 7)
                # The wait before each reading (7 x 1 s) and the shortest
                # duration, 5 x (7 s + 1 s) + 1 s (user, 2026-10-14).
                self.assertEqual(run.panel.ppms_field_scan_status_var.get(),
                                 f"RT-E {mode}: 5 points, settle 7 s; at least 41 s")

    def test_no_scan_starts_while_the_stage_may_still_be_turning(self):
        # An unconfirmed stop: no scan watches the stage, and Stop stage queues
        # no STOP behind a running scan, so every mode is refused (review,
        # 2026-10-14).
        for mode, span in (("Field", ("0.01", "0.03", "0.01")), ("Current", ("0.01", "0.03", "0.01")),
                           ("Angle", ("0", "10", "5"))):
            with self.subTest(mode=mode):
                run = self.start(mode=mode, span=span, may_run=True)
                run.showerror.assert_called_once()
                self.assertEqual(run.showerror.call_args.args[0], "RT-E scan parameters")
                self.assertIn("last stop was not confirmed", run.showerror.call_args.args[1])
                self.assertIn("press Stop stage first", run.showerror.call_args.args[1])
                run.askyesno.assert_not_called()
                run.askdirectory.assert_not_called()
                run.panel.ppms_executor.submit.assert_not_called()
                self.assertFalse(run.panel.__dict__.get("ppms_field_scan_running", False))
        # A stage that is not connected is not asked.
        run = self.start(mode="Field", span=("0.01", "0.03", "0.01"), stage=False, may_run=True)
        run.showerror.assert_not_called()
        run.panel.ppms_executor.submit.assert_called_once()

    def test_completed_and_stopped_status_lines_say_where_the_stage_is(self):
        for outcome, where, expected in (
                (None, "stage at +10.000°", "Completed; outputs held; stage at +10.000°"),
                (app.PPMSScanCancelled("stop"), "stage stopped at +2.500°",
                 "Stopped; outputs held unless OFF was requested; stage stopped at +2.500°"),
                (RuntimeError("Zurich reference unlocked"), "stage at +5.000°",
                 "Failed: Zurich reference unlocked"),
                (app.NewmarkStageError("X50000: the USB transaction failed (it is not repeated)"),
                 "stage NOT confirmed stopped: CHECK THE STAGE",
                 "Failed: X50000: the USB transaction failed (it is not repeated)")):
            with self.subTest(expected=expected):
                future = Future()
                run = self.start(executor=Mock(**{"submit.return_value": future}))
                self.assertEqual(run.panel.rte_stage_where, "stage not moved")
                run.panel.rte_stage_where = where       # as the worker leaves it
                with patch.object(app.messagebox, "showerror") as showerror:
                    if outcome is None:
                        future.set_result(None)
                    else:
                        future.set_exception(outcome)
                self.assertEqual(run.panel.ppms_field_scan_status_var.get(), expected)
                self.assertFalse(run.panel.ppms_field_scan_running)
                failed = outcome is not None and not isinstance(outcome, app.PPMSScanCancelled)
                self.assertEqual(showerror.call_count, int(failed))
                if failed:
                    # The status line keeps only the failure; where the stage is
                    # goes to the dialog and the log (user, 2026-10-13).
                    self.assertEqual(showerror.call_args.args[1],
                                     f"{outcome}\n\n{where[:1].upper()}{where[1:]}")
                    run.panel.thread_log.assert_any_call(f"RT-E scan {expected}; {where}")


class AnglePageTests(unittest.TestCase):
    def test_mode_change_defaults(self):
        panel = rte_tests.RTE.make_worker(self)
        panel.ppms_simple_mode = Var("Angle")
        panel.ppms_simple_range = [Var("") for _ in range(3)]
        panel.rte_scan_units = Var("")
        panel.rte_round_trip = Var(False)
        panel._redraw_ppms_transport_plot = Mock()
        panel._rte_mode_changed()
        self.assertEqual([v.get() for v in panel.ppms_simple_range], ["0", "360", "5"])
        self.assertTrue(panel.rte_round_trip.get(), "round trip: back toward the start, unwinding the wires")
        self.assertEqual(panel.rte_scan_units.get(), "Stage °")
        self.assertEqual(panel.ppms_plot_axis, "angle")
        for mode, values, units, axis, trip in (("Current", ["0.01", "1", "0.01"], "6221 mA RMS", "current", False),
                                                ("Field", ["-3", "3", "0.05"], "KEPCO A", "field", True)):
            panel.ppms_simple_mode.set(mode)
            panel._rte_mode_changed()
            self.assertEqual(([v.get() for v in panel.ppms_simple_range], panel.rte_scan_units.get(),
                              panel.ppms_plot_axis, panel.rte_round_trip.get()), (values, units, axis, trip))

    def test_angle_plots_against_the_stage_angle(self):
        panel = rte_tests.RTE.make_worker(self)
        canvas = object()
        panel.ppms_harmonic_canvases = {(1, "x"): canvas}
        panel._ppms2_measured_orders = Mock(return_value={1: ["830_a"]})
        panel._ppms2_prefix_resolver = Mock(return_value=lambda row: "sr830_8_")
        panel._draw_nonlocal_series = Mock()
        panel.ppms_field_scan_rows = [
            {"loop": 1, "stage_angle_deg": 0., "measured_field_g": 100., "6221_current_rms_ma": .1, "sr830_8_x_v": 1.},
            {"loop": 1, "stage_angle_deg": 5., "measured_field_g": 101., "6221_current_rms_ma": .1, "sr830_8_x_v": 2.}]
        for axis, label, xs in (("angle", "Sample angle (°)", [0., 5.]),
                                ("field", "Measured magnetic field (G)", [100., 101.]),
                                ("current", "Applied AC current (mA RMS)", [.1, .1])):
            with self.subTest(axis=axis):
                panel.ppms_plot_axis = axis
                panel._draw_nonlocal_series.reset_mock()
                panel._redraw_ppms_transport_plot()
                drawn, series, x_label = panel._draw_nonlocal_series.call_args.args[:3]
                self.assertIs(drawn, canvas)
                self.assertEqual(x_label, label)
                self.assertEqual([x for x, _y in series["SR830-1 P1 L1"]], xs)

    def test_the_ppms_page_has_no_angle_stage(self):
        source = inspect.getsource(app.PPMSPanel)
        self.assertNotIn("newmark", source.lower())
        self.assertNotIn("rte_stage", source)
        for name in ("start_ppms_field_scan", "_redraw_ppms_transport_plot", "safe_close_ppms"):
            self.assertIsNot(getattr(app.PPMSPanel, name), getattr(app.RTEPanel, name))

    def build_page(self):
        """The real RTEPanel constructor on stand-in widgets (as test_rte_panel's)."""
        parent = Mock(closing=False)
        parent.root.after.return_value = "timer"
        Recorder.made = []
        with ExitStack() as stack:
            for name in ("Frame", "LabelFrame", "Label", "Entry", "Button", "Combobox", "Checkbutton", "Progressbar"):
                stack.enter_context(patch.object(app.ttk, name, Recorder))
            stack.enter_context(patch.object(app.tk, "Canvas", Recorder))
            for name in ("StringVar", "IntVar", "BooleanVar"):
                stack.enter_context(patch.object(app.tk, name, Var))
            stack.enter_context(patch.object(app, "ThreadPoolExecutor", ImmediateExecutor))
            stack.enter_context(patch.object(app.CombinedControlApp, "_create_connection_indicator",
                                             lambda _self, parent, key: Recorder(parent, indicator=key)))
            stack.enter_context(patch.object(app.CombinedControlApp, "_set_connection_indicator", Mock()))
            stack.enter_context(patch.object(app.CombinedControlApp, "_draw_nonlocal_series", Mock()))
            return app.RTEPanel(parent, rte_tests.Widget())

    def test_the_stage_controls_are_a_block_in_the_scan_box(self):
        """User/CI, 2026-10-14: a rack row and a row under the settings for
        the stage took about 84 px of height in the 1101 px window, and every
        plot fell below its 55 px minimum.  The stage's controls are now one
        block of three rows in the RT-E scan box, paired with the scan rows
        (beside them when they fit, under them otherwise); the rack and the
        settings have none of them."""
        panel = self.build_page()
        entry, connect, disconnect = panel.rte_connection_widgets["newmark"]
        block = entry.parent
        rows = panel.rte_start_button.parent
        self.assertIs(block.parent, rows.parent, "the block and the scan rows are one pair")
        scan = rows.parent.parent
        self.assertEqual(scan.options.get("text"), "RT-E scan")
        # The rack keeps its four rows (KEPCO, LS475, 6221, 2400), no stage row.
        rack = panel.rte_connection_widgets["kepco"][0].parent
        self.assertEqual({w.grid_options["row"] for w in Recorder.made
                          if w.parent is rack and "row" in w.grid_options}, {0, 1, 2, 3})
        self.assertFalse([w for w in Recorder.made if w.options.get("text") == "Newmark stage"])
        # Three rows level with the scan rows' three (mode/Start/End/Step;
        # units/Loops/Settle s/Acquire s; Start scan/Stop/Save CSV as/Round trip).
        self.assertEqual({w.grid_options["row"] for w in Recorder.made
                          if w.parent is rows and "row" in w.grid_options}, {0, 1, 2})
        labels = {w.options["text"]: w for w in Recorder.made
                  if w.parent is block and "text" in w.options and "command" not in w.options}
        buttons = {w.options.get("text"): w for w in Recorder.made if "command" in w.options}
        move, zero, stop = buttons["Move stage"], buttons["Set stage 0°"], buttons["Stop stage"]
        self.assertIs(stop, panel.rte_stage_stop_button)
        target = next(w for w in Recorder.made if w.options.get("textvariable") is panel.rte_stage_target)
        live = next(w for w in Recorder.made if w.options.get("textvariable") is panel.rte_stage_live)
        lamp = next(w for w in Recorder.made if w.options.get("indicator") == "rte_newmark")
        layout = {(w.grid_options["row"], w.grid_options["column"]): w for w in Recorder.made if w.parent is block}
        self.assertEqual(layout, {
            (0, 0): labels["Stage"], (0, 1): entry, (0, 2): connect, (0, 3): disconnect, (0, 4): lamp,
            (1, 0): labels["Stage °"], (1, 1): target, (1, 2): move, (1, 3): stop,
            (2, 0): zero, (2, 2): live})
        self.assertEqual(panel.rte_resources["newmark"].get(), "auto")
        self.assertEqual((entry.options["width"], target.options["width"]), (9, 9))
        self.assertEqual((connect.options["text"], disconnect.options["text"]), ("Connect", "Disconnect"))
        for button in (connect, disconnect, move, stop, zero):
            # No taller than the entries beside them: the block costs no height.
            self.assertEqual(button.options["style"], "Inline.TButton", button.options["text"])
        # One live line, a fixed box, for the connection and the reading; the
        # stage has no separate "Connected" label (the lamp shows it).
        self.assertEqual(live.options["width"], 32)
        self.assertEqual(panel.rte_stage_live.get(), "Stage: not connected")
        self.assertNotIn("newmark", panel.rte_connections)
        self.assertEqual([w for w in Recorder.made if w.parent is block and "textvariable" in w.options
                          and w.options["textvariable"] not in (panel.rte_resources["newmark"], panel.rte_stage_target)],
                         [live])
        # The status/progress row, the acquisition state and the field note
        # stay full width under the pair.
        self.assertIs(panel.ppms_field_scan_progress.parent.parent, scan)
        note = next(w for w in Recorder.made if w.options.get("text") == "Field: KEPCO A; readback: signed LS475 G")
        self.assertIs(note.parent.parent, scan)
        self.assertEqual((rows.parent.grid_options["row"], panel.ppms_field_scan_progress.parent.grid_options["row"],
                          note.parent.grid_options["row"]), (0, 1, 2))
        self.assertIsInstance(panel.rte_controllers["newmark"], app.NewmarkNSCA1Controller)
        self.assertEqual(panel.rte_controllers["newmark"].owner_scope, "RT-E")
        self.assertIsNone(panel.rte_controllers["newmark"].dll, "no DLL is loaded before Connect")
        combo = next(w for w in Recorder.made if w.options.get("textvariable") is panel.ppms_simple_mode)
        self.assertEqual(combo.options["values"], ("Field", "Current", "Angle"))
        # The button states are as they were in the rack and settings rows.
        states = lambda: tuple(w.options["state"] for w in (entry, connect, disconnect, target, move, stop, zero))
        self.assertEqual(states(), ("normal", "normal", "disabled", "normal", "normal", "disabled", "normal"),
                         "no stage connected")
        stage = panel.rte_controllers["newmark"]
        stage.handle = object()         # connected, as far as the page can tell
        try:
            panel.ppms_environment_busy = True
            panel._update_ppms_control_ui()
            self.assertEqual(states(), ("disabled",) * 5 + ("normal", "disabled"), "Stop stage works while busy")
            panel.ppms_environment_busy = False
            panel._update_ppms_control_ui()
            self.assertEqual(states(), ("disabled", "disabled", "normal", "normal", "normal", "normal", "normal"))
        finally:
            stage.handle = None
        panel.safe_close_ppms()
        self.assertTrue(panel.rte_stage_stop_event.is_set(), "closing stops a move by hand")


class CloseTests(StageBase):
    def test_close_stops_a_turning_stage_first_on_the_worker_then_closes(self):
        executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="rte-control")
        self.addCleanup(executor.shutdown, wait=True)
        h = self.make(executor=executor)
        panel = h.panel
        panel.zurich_executor, panel.zurich_controller = Mock(), Mock()
        executor.submit(h.stage.connect, "auto").result(timeout=30)
        h.sim._start(3000000, script._now())        # turning (an unconfirmed stop)
        order = []
        kepco, source = panel.rte_controllers["kepco"], panel.ppms_instrument_controllers["830_a"]
        kepco.disconnect.side_effect = lambda: order.append("kepco")
        source.disconnect.side_effect = lambda: order.append("830_a")
        disconnect = h.stage.disconnect
        h.stage.disconnect = lambda: (order.append("stage"), disconnect())
        panel.safe_close_ppms()
        executor.shutdown(wait=True)
        self.assertEqual(order[0], "stage")
        self.assertEqual(set(order), {"stage", "kepco", "830_a"})
        self.assertIn("STOP", h.sim.received)
        self.assertEqual(h.dll.names()[-1], "fnPerformaxComClose")
        self.assertNotIn(threading.get_ident(), {thread for _name, thread in h.dll.calls})
        self.assertFalse(h.stage.connected)
        kepco.safe_output_off.assert_not_called()


if __name__ == "__main__":
    unittest.main()
