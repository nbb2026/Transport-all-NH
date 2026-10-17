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
import ast
from concurrent.futures import Future, ThreadPoolExecutor
from contextlib import ExitStack
import ctypes
import importlib.util
import inspect
import os
from pathlib import Path
import tempfile
import threading
import time
import tokenize
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


# The driver read and its reads: never sent since the microstep is fixed (user, 2026-10-17).
DRIVER_READS = ("RR", "R2", "DRVMS", "DRVRC", "DRVIC", "DRVIT")
SCALE = ("10000 steps/° (72:1 x 200 full steps x microstep 250 / 360; microstep 250 from QuickMotion, fixed in "
         "the program)")


class Sim(script.SimulatedNSC):
    """The script's simulated NSC-A1 (DRVMS 250, the lab PC's QuickMotion
    microstep: 10000 steps/deg; the program never asks it for the driver)."""

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


class AfterRoot:
    """The page's root for the stage harness: ``after(0, fn)`` queues ``fn``
    for StageBase.drain to run from the "Tk event loop", outside the post_ui
    queue (review, 2026-10-17); no window."""

    def __init__(self, afters):
        self.afters = afters
        self.delays = []

    def after(self, delay, callback):
        self.delays.append(delay)
        self.afters.append(callback)
        return f"after#{len(self.delays)}"

    def after_cancel(self, _timer):
        pass


class DeferredExecutor:
    """The RT-E worker, run by the test: jobs wait in order until ``run()``,
    as behind an idle-poll read on the program's one worker (review,
    2026-10-17).  The scan's own job is only recorded, as StartExecutor's."""

    SCAN = rte_tests.StartExecutor.SCAN

    def __init__(self):
        self.jobs = []
        self.submit = Mock(side_effect=self._submit)

    def _submit(self, fn, *args):
        future = Future()
        if getattr(fn, "__name__", "") != self.SCAN:
            self.jobs.append((future, fn, args))
        return future

    def run(self):
        """Run every job queued so far, and those they queue, in order."""
        while self.jobs:
            future, fn, args = self.jobs.pop(0)
            try:
                future.set_result(fn(*args))
            except BaseException as error:
                future.set_exception(error)

    def scans(self):
        return [call.args for call in self.submit.call_args_list
                if getattr(call.args[0], "__name__", "") == self.SCAN]

    def shutdown(self, **_kwargs):
        pass


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
        # The rack's Motor row says "Connected" / "Not connected" again since
        # its connection moved back to the rack (user, 2026-10-17); the
        # stage line in the scan box shows the reading.
        panel.rte_connections = {"newmark": Var("Not connected")}
        panel.rte_drive_live = Var("")
        panel.rte_field_live = Var("")
        panel.ppms_environment_busy = False
        panel.ppms_field_scan_running = False
        panel.ppms_control_safety_var = Var(True)
        panel._update_ppms_control_ui = Mock()
        panel.thread_log = Mock()
        afters = []
        panel.root = AfterRoot(afters)
        ui = []
        panel.post_ui = ui.append
        panel.ppms_executor = ImmediateExecutor() if executor is None else executor
        return types.SimpleNamespace(panel=panel, stage=stage, sim=sim, dll=dll, clock=clock, ui=ui, log=log,
                                     afters=afters, in_drain=False)

    @staticmethod
    def drain(h):
        """As the program's Tk thread: drain_ui_queue runs the post_ui
        callbacks (``in_drain`` meanwhile), then the Tk event loop runs what
        root.after(0, ...) scheduled, one at a time (review, 2026-10-17)."""
        while h.ui or h.afters:
            h.in_drain = True
            try:
                while h.ui:
                    h.ui.pop(0)()
            finally:
                h.in_drain = False
            if h.afters:
                h.afters.pop(0)()

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
    def test_connect_confirms_the_nsc_with_the_fixed_scale(self):
        h = self.make()
        self.assertEqual(h.stage.connect("auto"), "Ace-Series-SDE")
        self.assertTrue(h.stage.connected)
        names = h.dll.names()
        self.assertLess(names.index("fnPerformaxComSetTimeouts"), names.index("fnPerformaxComOpen"))
        self.assertEqual(names[names.index("fnPerformaxComOpen") + 1], "fnPerformaxComFlush")
        # ID, then VER, and nothing else: the driver is not read (no RR, no
        # 2 s wait), since the microstep is fixed (user, 2026-10-17).
        self.assertEqual(h.sim.received, ["ID", "VER"], "ID before anything else, then VER")
        self.assertLess(h.clock.t - 1000.0, 0.5, "no wait for a driver read")
        self.assertEqual(h.stage.steps_per_degree, 10000, "40 x microstep 250 (fixed)")
        self.assertEqual(h.stage.scale_text(), SCALE)
        self.assertIn(f"RT-E Newmark NSC-A1 connected (Ace-Series-SDE, V231BL): {SCALE}", h.log)
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
                        "SL=1", "ZH+", "T1", "@01ID", "X1.5", "HSPD=-5") + DRIVER_READS:
            with self.subTest(command=command), self.assertRaises(app.NewmarkCommandNotAllowed):
                check(command)
        for command in ("ID", "MST", "PX", "EO", "SL", "EDIO", "ABS", "STOP", "ABORT", "PX=0"):
            check(command)
        # The driver read is off the list since the microstep is fixed (user,
        # 2026-10-17), with or without a scale.
        for command in DRIVER_READS:
            with self.subTest(command=command):
                self.assertNotIn(command, app.NEWMARK_READ_ONLY | app.NEWMARK_ACTIONS)
                with self.assertRaises(app.NewmarkCommandNotAllowed):
                    check(command, 10000)
        self.assertFalse(hasattr(app, "NEWMARK_RR_WAIT_S"))
        self.assertFalse(hasattr(app.NewmarkNSCA1Controller, "_read_scale"))
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
        for command in ("STORE", "HL+", "X3600001") + DRIVER_READS:
            with self.assertRaises(app.NewmarkCommandNotAllowed):
                h.stage.query(command)
        self.assertEqual(h.sim.received, before, "refused before it was sent")

    def test_stepnloop_and_dio_control_on_still_connect_and_move(self):
        # The lab PC's NSC-A1 has DIO control on (EDIO=1), which stopped the
        # driver read (user, 2026-10-17).  With the fixed microstep, SL and EDIO
        # block nothing: it connects with its scale and moves.
        sim = Sim()
        sim.registers.update({"SL": 1, "EDIO": 1})
        h = self.make(sim)
        h.stage.connect("auto")
        self.assertTrue(h.stage.connected)
        self.assertEqual(h.stage.steps_per_degree, 10000)
        self.assertEqual(app.newmark_stage_text(h.stage.read_state()), "Stage: +0.000° (10000 steps/°)")
        state = h.stage.move_to_deg(5, threading.Event())
        self.assertEqual(h.sim.moves, ["X50000"])
        self.assertEqual((state["px"], state["angle_deg"]), (50000, 5.0))
        self.assertFalse([c for c in h.sim.received if c in DRIVER_READS + ("SL", "EDIO")], h.sim.received)

    def test_the_microstep_is_250_with_its_source(self):
        self.assertEqual(app.NEWMARK_MICROSTEP, 250)
        self.assertEqual(app.NEWMARK_MICROSTEP, script.MICROSTEP, "the program and the script agree")
        self.assertEqual(app.NEWMARK_STEPS_PER_DEGREE, 40 * app.NEWMARK_MICROSTEP)
        self.assertEqual(app.NEWMARK_STEPS_PER_DEGREE, 10000)
        self.assertEqual(app.NewmarkNSCA1Controller(lambda _text: None).steps_per_degree, 10000)
        program = Path(app.__file__).read_text(encoding="utf-8").splitlines()
        index = next(i for i, line in enumerate(program) if line.startswith("NEWMARK_MICROSTEP = "))
        comment = []
        while program[index - 1].startswith("#"):
            index -= 1
            comment.insert(0, program[index].lstrip("#").strip())
        comment = " ".join(comment)
        for text in ("user, 2026-10-17", "QuickMotion screenshot", "Microstepping 250",
                     "72:1 x 200 full steps / 360 = 40 full steps per degree", "10000 steps/deg",
                     "0.36 arcsec", "Run Current 2100 mA", "Idle Current 100 mA", "not used", "EDIO=1",
                     "If the microstep is changed in QuickMotion, change this constant"):
            self.assertIn(text, comment)

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
        # The latched limit error and the driver off are their own refusals,
        # which the RT-E page offers to fix; their texts say how, on the page,
        # and name no test script (user, 2026-10-17).  The STFMR page offers
        # the same at a Start with the motor, so they name it too (user,
        # 2026-10-17).
        refused, latched, off = app.NewmarkRefused, app.NewmarkLimitErrorLatched, app.NewmarkDriverOff
        cases = (("moving", lambda sim: sim._start(sim.px + 10 ** 6, script._now()), "is moving", refused),
                 ("latched", lambda sim: setattr(sim, "latched", 1 << 6),
                  r"limit error is latched \(MST 64: not moving, -limit ERROR \(latched\)\): the move was not sent\. "
                  r"To clear it, press Move stage or Start an Angle scan on the RT-E page, or Start a sweep with "
                  r"the motor on the STFMR page, and answer Yes \(CLR\)$",
                  latched),
                 ("input", lambda sim: setattr(sim, "inputs", 1 << 5), "polarity problem.*QuickMotion", refused),
                 # A limit input is checked before the latched error: CLR is
                 # never offered while the cause is still there.
                 ("input and latched", lambda sim: (setattr(sim, "inputs", 1 << 4), setattr(sim, "latched", 1 << 6)),
                  "limit input is on at rest.*polarity problem", refused),
                 ("EO=0", lambda sim: setattr(sim, "eo", 0),
                  r"^the motor driver is off \(EO=0\): the position counter would advance without motion, so the "
                  r"move was not sent\. To switch it on, press Move stage or Start an Angle scan on the RT-E page, "
                  r"or Start a sweep with the motor on the STFMR page, and answer Yes \(EO=1\)$", off))
        for name, arrange, message, kind in cases:
            with self.subTest(name):
                h = self.make()
                h.stage.connect("auto")
                arrange(h.sim)
                with self.assertRaisesRegex(app.NewmarkRefused, message) as caught:
                    h.stage.move_to_deg(5, threading.Event())
                self.assertIs(type(caught.exception), kind)
                self.assertNotIn("motor_test", str(caught.exception))
                self.assertEqual([m for m in h.sim.moves if m != "X1000000"], [])
                self.assertNotIn("X50000", h.sim.received)
                self.assertFalse({"CLR", "EO=1"} & set(h.sim.received), "nothing is fixed by itself")
                if kind is latched:
                    self.assertEqual(caught.exception.mst, 64)
                if kind is off:
                    self.assertEqual(caught.exception.eo, 0)

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
        # The session is reset; the scale is the fixed one (user, 2026-10-17).
        self.assertEqual((h.stage.identity, h.stage.steps_per_degree), ("", 10000))
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

        # Not in the program's folder: the normal search, and Python 3.8+'s
        # error 126, now named as such with where the DLLs go (user, 2026-10-17).
        with patch.object(app.ctypes, "WinDLL", not_found, create=True), \
                patch.object(app, "newmark_dll_folder", return_value=None):
            with self.assertRaisesRegex(app.NewmarkStageError, r"PerformaxCom\.dll was not found: it is not in "
                                        r"the program's folder"):
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


# ---- the Motor row and the device owner (user, 2026-10-17) -----------------------
class MotorRowOwnershipTests(StageBase):
    """User, 2026-10-17: "add the connection button for Motor in RT-E and
    ST-FMR modules".  The USB device opens on one page at a time: the RT-E
    page claims it before its Connect job is queued and releases it once the
    handle is closed; a Connect while the STFMR page has it is refused."""

    def test_connect_is_refused_while_the_stfmr_page_has_it(self):
        h = self.make()
        h.panel._newmark_stage_ownership().claim("STFMR")
        with patch.object(app.messagebox, "showerror") as showerror:
            h.panel._rte_connect("newmark")
            self.drain(h)
        showerror.assert_called_once_with("RT-E motor", "The motor is connected on the STFMR page: disconnect it "
                                          "there first.", parent=h.panel.root)
        self.assertEqual(h.dll.calls, [], "nothing queued or sent")
        self.assertFalse(h.stage.connected)
        self.assertEqual(h.panel.rte_connections["newmark"].get(), "Not connected")

    def test_connect_claims_and_disconnect_releases(self):
        h = self.make()
        ownership = h.panel._newmark_stage_ownership()
        with patch.object(app.messagebox, "showerror") as showerror:
            h.panel._rte_connect("newmark")
            self.drain(h)
            self.assertEqual(ownership.owner, "RT-E")
            self.assertEqual(h.panel.rte_connections["newmark"].get(), "Connected")
            self.assertEqual(h.panel.rte_stage_live.get(), "Stage: +0.000° (10000 steps/°)")
            h.panel._rte_disconnect("newmark")
            self.drain(h)
        showerror.assert_not_called()
        self.assertIsNone(ownership.owner)
        self.assertEqual(h.panel.rte_connections["newmark"].get(), "Not connected")
        self.assertEqual(h.panel.rte_stage_live.get(), "Stage: not connected")

    def test_a_failed_connect_releases(self):
        h = self.make(Sim(serials=()))
        with patch.object(app.messagebox, "showerror") as showerror:
            h.panel._rte_connect("newmark")
            self.drain(h)
        self.assertIn("No Performax USB device found", showerror.call_args.args[1])
        self.assertIsNone(h.panel._newmark_stage_ownership().owner)

    def test_a_disconnect_still_open_keeps_the_claim(self):
        h = self.make()
        h.panel._newmark_stage_ownership().claim("RT-E")
        with ThreadPoolExecutor(max_workers=1) as other:
            other.submit(h.stage.connect, "auto").result(timeout=30)
            with patch.object(app.messagebox, "showerror"):
                h.panel._rte_disconnect("newmark")      # refused: another thread holds the handles
                self.drain(h)
            self.assertTrue(h.stage.connected)
            self.assertEqual(h.panel._newmark_stage_ownership().owner, "RT-E")
            other.submit(h.stage.disconnect).result(timeout=30)

    def test_closing_releases(self):
        h = self.make()
        with patch.object(app.messagebox, "showerror"):
            h.panel._rte_connect("newmark")
            self.drain(h)
        h.panel.zurich_executor, h.panel.zurich_controller = Mock(), Mock()
        h.panel.ppms_instrument_controllers = {}
        h.panel.safe_close_ppms()
        self.assertFalse(h.stage.connected)
        self.assertIsNone(h.panel._newmark_stage_ownership().owner)


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
            # Settle s, right after the move: 8 s, longer than the 1 x 1 s
            # lock-in settle, so it is the wait (user, 2026-10-14).
            if seconds == 8.:
                while h.ui:                 # what the Tk thread shows by then
                    h.ui.pop(0)()
                    shown.append(panel.rte_stage_live.get())
                seen.append(panel.rte_stage_live.get())
        panel._rte_wait = wait
        # Start announced this wait, so the worker posts no "Each reading
        # now waits" status of its own, as in the program (user, 2026-10-14).
        panel.ppms_scan_settle_logged = 8.
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
        # The driver off or a latched limit error that appears once the scan
        # runs fails the scan through the stage-failure path: no question
        # mid-scan with the outputs on (every dialog fails this module's
        # tests), nothing fixed (user, 2026-10-17).
        for name, arrange, kind in (("EO=0", lambda sim: setattr(sim, "eo", 0), app.NewmarkDriverOff),
                                    ("latched", lambda sim: setattr(sim, "latched", 1 << 7),
                                     app.NewmarkLimitErrorLatched)):
            with self.subTest(name):
                sim = Sim()
                h = self.make(sim)
                h.stage.connect("auto")
                arrange(sim)        # the first move is refused
                with self.assertRaises(kind):
                    self.run_scan(h=h, plan=app.rte_scan_plan("Angle", 5, 10, 5, 1, False))
                panel = h.panel
                panel._stop_ppms_transport_source.assert_called_once()
                panel.rte_controllers["kepco"].safe_output_off.assert_not_called()
                self.assertIn("STOP", h.sim.received, "a stage failure sends STOP")
                self.assertFalse({"CLR", "EO=1"} & set(h.sim.received))
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
        # The first read's MST is lost (until 2026-10-17 it was RR's reply,
        # but the driver is no longer read).  The handle is open, so the lamp,
        # which follows it, says connected (user, 2026-10-13), and the stage
        # line that the read failed.
        h = self.make(LosesOne("MST"))
        with patch.object(app.messagebox, "showerror") as showerror:
            h.panel._rte_connect("newmark")
            self.drain(h)
        showerror.assert_not_called()
        self.assertTrue(h.stage.connected)
        self.assertTrue(h.panel.rte_stage_live.get().startswith("Stage read failed: MST: the USB transaction failed"),
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
              span=("0", "10", "5"), drive="1.5", limit="5", executor=None, may_run=False, owner=None):
        """The real RTEPanel.start_ppms_field_scan up to its submit (as in
        test_ppms_rte_review), with every dialog answered here.  ``owner``:
        the page that has the motor's USB device (user, 2026-10-17)."""
        panel = rte_tests.RTE.make_worker(self)
        if owner is not None:
            panel._newmark_stage_ownership().claim(owner)
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
                                                label="Newmark NSC-A1")
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
        # The stage check at an Angle Start runs at once; the scan is only
        # recorded (user, 2026-10-17).
        panel.ppms_executor = rte_tests.StartExecutor() if executor is None else executor
        panel.ppms_environment_busy = panel.ppms_field_scan_running = False
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
        for options, message in ((dict(stage=False), r'Connect the Newmark NSC-A1 \(the "Motor" row of the '
                                                     r'measurement instruments\) for an angle scan'),
                                 # The STFMR page has the device (user, 2026-10-17).
                                 (dict(stage=False, owner="STFMR"),
                                  "The motor is connected on the STFMR page: disconnect it there and connect it "
                                  "here"),
                                 (dict(kepco=False), "Connect KEPCO for an angle scan"),
                                 (dict(gauss=False), "Connect LS475"),
                                 (dict(scale=None), "Newmark NSC-A1 has no steps/° scale"),
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
                     # scan can take: 5 x (1 s + 1 s) + 1 s (user, 2026-10-14:
                     # 1 x TC; 7 x TC and 41 s before).
                     "Each angle waits 1 s (1 x 1 s, the time constant of SR830-1, the slowest lock-in "
                     "measured; Settle s is 0 s) before it is read; the scan takes at least 11 s."):
            self.assertIn(text, question)
        run.askdirectory.assert_not_called()
        run.panel.ppms_executor.submit.assert_not_called()
        run.panel.rte_controllers["newmark"].check_ready.assert_not_called()
        run = self.start(yes=True)
        # The stage check (MST, EO) on the worker first, then the scan (user,
        # 2026-10-17).
        run.panel.rte_controllers["newmark"].check_ready.assert_called_once_with()
        self.assertEqual(run.panel.ppms_executor.submit.call_count, 2)
        self.assertEqual(len(run.panel.ppms_executor.scans()), 1)
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
                # The wait before each reading (1 x 1 s) and the shortest
                # duration, 5 x (1 s + 1 s) + 1 s (user, 2026-10-14: 1 x TC;
                # 7 x TC and 41 s before).
                self.assertEqual(run.panel.ppms_field_scan_status_var.get(),
                                 f"RT-E {mode}: 5 points, settle 1 s; at least 11 s")

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
                run = self.start(executor=rte_tests.StartExecutor(future))
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

    def test_the_motor_row_is_in_the_rack_and_the_stage_block_in_the_settings(self):
        """User/CI, 2026-10-14: a rack row and a row under the settings for
        the stage took about 84 px of height in the 1101 px window, and every
        plot fell below its 55 px minimum; the stage's controls became one
        block in the RT-E scan box.  User, 2026-10-17: "add the connection
        button for Motor in RT-E and ST-FMR modules": the connection is the
        rack's fifth row, "Motor", laid out as the four above it (label,
        18-character address, Connect, Disconnect, lamp, 13-character
        status), accepting about one row of plot height.  User, 2026-10-17
        again: "now move the stage controllers to the RT-E settings": Move
        stage, Stop stage, Set stage 0° and the live line are a block of two
        rows in the RT-E settings box, with the KEPCO current an Angle scan
        holds; the scan box has only the scan rows, its status and notes."""
        panel = self.build_page()
        entry, connect, disconnect = panel.rte_connection_widgets["newmark"]
        rack = panel.rte_connection_widgets["kepco"][0].parent
        self.assertIs(entry.parent, rack)
        # Five rack rows: KEPCO, LS475, 6221, 2400, Motor.
        self.assertEqual({w.grid_options["row"] for w in Recorder.made
                          if w.parent is rack and "row" in w.grid_options}, {0, 1, 2, 3, 4})
        row = {w.grid_options["column"]: w for w in Recorder.made
               if w.parent is rack and w.grid_options.get("row") == 4}
        lamp = next(w for w in Recorder.made if w.options.get("indicator") == "rte_newmark")
        self.assertEqual(sorted(row), [0, 1, 2, 3, 4, 5])
        self.assertEqual((row[0].options["text"], row[0].options["width"]), ("Motor", 13))
        self.assertEqual((row[1], row[2], row[3], row[4]), (entry, connect, disconnect, lamp))
        self.assertIs(row[5].options["textvariable"], panel.rte_connections["newmark"])
        self.assertEqual(row[5].options["width"], 13)
        self.assertEqual(panel.rte_connections["newmark"].get(), "Not connected")
        self.assertEqual(panel.rte_resources["newmark"].get(), "auto")
        # The same widths as the KEPCO row above it, so the columns line up.
        kepco = {w.grid_options["column"]: w for w in Recorder.made
                 if w.parent is rack and w.grid_options.get("row") == 0}
        for column in (0, 1, 2, 3, 5):
            self.assertEqual(row[column].options.get("width"), kepco[column].options.get("width"), column)
        self.assertEqual((connect.options["text"], disconnect.options["text"]), ("Connect", "Disconnect"))
        self.assertNotIn("style", connect.options, "a plain button, as the other rows'")
        self.assertFalse([w for w in Recorder.made if w.options.get("text") == "Newmark stage"])
        self.assertFalse([w for w in Recorder.made if w.options.get("text") == "Stage"], "no Stage row left")
        # The block: two rows in the RT-E settings, a child of the box's
        # body (params / lock-ins), first under the KEPCO rows (its place in
        # the 2000 px window; arrange_stage_block moves it beside the
        # lock-ins in the 1101 px window).
        block = panel.rte_stage_stop_button.parent
        settings_box = next(w for w in Recorder.made if w.options.get("text") == "RT-E settings")
        self.assertIs(block.parent.parent, settings_box)
        self.assertEqual((block.grid_options["row"], block.grid_options["column"]), (1, 0))
        rows = panel.rte_start_button.parent
        scan = rows.parent
        self.assertEqual(scan.options.get("text"), "RT-E scan")
        self.assertFalse([w for w in Recorder.made if w.parent is scan and w is not rows
                          and "row" in w.grid_options and w.grid_options["row"] == 0], "only the scan rows on top")
        # Three scan rows (mode/Start/End/Step; units/Loops/Settle s/Acquire
        # s; Start scan/Stop/Save CSV as/Round trip).
        self.assertEqual({w.grid_options["row"] for w in Recorder.made
                          if w.parent is rows and "row" in w.grid_options}, {0, 1, 2})
        labels = {w.options["text"]: w for w in Recorder.made
                  if w.parent is block and "text" in w.options and "command" not in w.options}
        buttons = {w.options.get("text"): w for w in Recorder.made if "command" in w.options}
        move, zero, stop = buttons["Move stage"], buttons["Set stage 0°"], buttons["Stop stage"]
        self.assertIs(stop, panel.rte_stage_stop_button)
        target = next(w for w in Recorder.made if w.options.get("textvariable") is panel.rte_stage_target)
        live = next(w for w in Recorder.made if w.options.get("textvariable") is panel.rte_stage_live)
        layout = {(w.grid_options["row"], w.grid_options["column"]): w for w in Recorder.made if w.parent is block}
        self.assertEqual(layout, {
            (0, 0): labels["Stage °"], (0, 1): target, (0, 2): move, (0, 3): stop,
            (1, 0): zero, (1, 2): live})
        self.assertEqual(target.options["width"], 9)
        for button in (move, stop, zero):
            # No taller than the entries beside them.
            self.assertEqual(button.options["style"], "Inline.TButton", button.options["text"])
        # One live line, a fixed box, for the reading.
        self.assertEqual(live.options["width"], 32)
        self.assertEqual(panel.rte_stage_live.get(), "Stage: not connected")
        self.assertEqual([w for w in Recorder.made if w.parent is block and "textvariable" in w.options
                          and w.options["textvariable"] is not panel.rte_stage_target], [live])
        # The status/progress row, the acquisition state and the field note
        # stay full width under the scan rows.
        self.assertIs(panel.ppms_field_scan_progress.parent.parent, scan)
        note = next(w for w in Recorder.made if w.options.get("text") == "Field: KEPCO A; readback: signed LS475 G")
        self.assertIs(note.parent.parent, scan)
        self.assertEqual((rows.grid_options["row"], panel.ppms_field_scan_progress.parent.grid_options["row"],
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

    def test_the_stage_question_keeps_the_buttons_locked(self):
        """While the Yes/No for a CLR or an EO=1 is open (it opens from the
        Tk event loop after its job, with the rack idle), the page's
        buttons stay locked as while busy; Stop stage stays usable (review,
        2026-10-17)."""
        panel = self.build_page()
        self.assertIs(panel.rte_stage_prompt_open, False)
        entry, connect, disconnect = panel.rte_connection_widgets["newmark"]
        buttons = {w.options.get("text"): w for w in Recorder.made if "command" in w.options}
        move, zero, stop = buttons["Move stage"], buttons["Set stage 0°"], buttons["Stop stage"]
        target = next(w for w in Recorder.made if w.options.get("textvariable") is panel.rte_stage_target)
        states = lambda: tuple(w.options["state"] for w in (entry, connect, disconnect, target, move, stop, zero,
                                                            panel.rte_start_button))
        stage = panel.rte_controllers["newmark"]
        stage.handle = object()         # connected, as far as the page can tell
        try:
            panel.ppms_control_safety_var.set(True)
            panel.rte_stage_prompt_open = True
            panel._update_ppms_control_ui()
            self.assertEqual(states(), ("disabled",) * 5 + ("normal", "disabled", "disabled"))
            self.assertEqual(buttons["KEPCO Zero / OFF"].options.get("state", "normal"), "normal")
            self.assertEqual(buttons["6221 / 2400 OFF"].options.get("state", "normal"), "normal")
            panel.rte_stage_prompt_open = False
            panel._update_ppms_control_ui()
            self.assertEqual(states(), ("disabled", "disabled", "normal", "normal", "normal", "normal", "normal",
                                        "normal"))
        finally:
            stage.handle = None
        panel.safe_close_ppms()


# ---- a latched limit error or the driver off, fixed on the page (user, 2026-10-17) ----
# The user asked that the program not depend on Newmark_motor_test.py, whose
# "clear" and "enable" the old refusals named.  Move stage and an Angle scan's
# Start now ask Yes/No themselves; Yes sends CLR or EO=1 on the RT-E worker,
# reads it back, and then goes on; No sends nothing.
LATCHED = "RT-E stage limit error"
DRIVER = "RT-E stage motor driver"
SCAN = "RT-E Angle scan"
LATCHED_QUESTION = ("A limit error is latched on the stage (MST 64: not moving, -limit ERROR (latched)). Clear it now "
                    "(CLR)?")
# Yes goes on at once, so the warning about the angle comes in the question,
# before it (review, 2026-10-17).
DRIVER_QUESTION = ("The stage's motor driver is off (EO=0). Switch it on now (EO=1) and go on? The motor then holds "
                   "the stage with its current.\n\nIf the driver went off during a move, the angle shown may not be "
                   "the stage's: answer No and check it first (Set stage 0° if needed).")
TICK = "Tick the RT-E rack safety confirmation first"
ACTIONS = ("CLR", "EO=0", "EO=1", "STOP", "ABORT", "PX=0")


def actions(received):
    """The commands that change something (fixes, stops, the zero, moves), in order."""
    return [command for command in received if command in ACTIONS or command.startswith(("X", "HL", "J"))]


def latch(sim):
    sim.latched = 1 << 6


def driver_off(sim):
    sim.eo = 0


class Watched(Sim):
    """``watch(command)`` is called as each command arrives (from the thread that sent it)."""

    watch = None

    def reply(self, command):
        if self.watch is not None:
            self.watch(command)
        return super().reply(command)


class ClrDoesNotClear(Watched):
    """CLR is answered OK, but the limit error stays latched."""

    def reply(self, command):
        return "OK" if command == "CLR" else super().reply(command)


class EoStaysOff(Watched):
    """EO=1 is answered OK, but EO still reads 0."""

    def reply(self, command):
        return "OK" if command == "EO=1" else super().reply(command)


class Relatches(Watched):
    """CLR clears the limit error, but it is latched again at the next ABS,
    as if its cause were still there."""

    cleared = False

    def reply(self, command):
        if command == "ABS" and self.cleared:
            self.latched = 1 << 6
        text = super().reply(command)
        self.cleared = self.cleared or command == "CLR"
        return text


class OpenFails(Sim):
    """fnPerformaxComOpen fails (another program has the device)."""

    def open(self, index):
        return None


class Questions:
    """messagebox.askyesno, answered by title.  Each question is recorded with
    how many commands had been sent by then, whether the rack was busy, and
    ``snapshot()``.  ``before(title, h)`` runs first (e.g. to untick the safety)."""

    def __init__(self, h, answers, before=None, snapshot=None):
        self.h, self.answers, self.before, self.snapshot = h, dict(answers), before, snapshot
        self.asked = []

    def __call__(self, title, message, parent=None, **_options):
        # in_drain: asked from inside drain_ui_queue (it must not be);
        # prompt_open: the buttons locked while it is open (review, 2026-10-17).
        self.asked.append(types.SimpleNamespace(
            title=title, message=message, sent=len(self.h.sim.received), busy=self.h.panel.ppms_environment_busy,
            snapshot=self.snapshot() if self.snapshot else None, in_drain=self.h.in_drain,
            prompt_open=self.h.panel.__dict__.get("rte_stage_prompt_open")))
        if self.before is not None:
            self.before(title, self.h)
        return self.answers[title]

    def titles(self):
        return [question.title for question in self.asked]

    def after(self, title):
        """What was sent after the (first) question with this title."""
        return self.h.sim.received[next(q.sent for q in self.asked if q.title == title):]


NOT_CONFIRMED = ("The Newmark NSC-A1's last stop was not confirmed, so it may still be turning: press Stop stage first "
                 "(check the stage)")
BUSY = "RT-E is busy; wait or stop the scan first"
# What the checks before the question and after its Yes refuse (review,
# 2026-10-17): (name, change(h), the error dialog's text or None for none).
RECHECKS = (("an unconfirmed stop", lambda h: setattr(h.stage, "motion_may_run", True), NOT_CONFIRMED),
            ("a move running", lambda h: setattr(h.stage, "in_move", True), NOT_CONFIRMED),
            # The page's view: the handle gone, as after a Disconnect.
            ("the stage disconnected", lambda h: setattr(h.stage, "handle", None), "Connect the Newmark NSC-A1 first"),
            ("the rack busy", lambda h: setattr(h.panel, "ppms_environment_busy", True), BUSY),
            ("a scan running", lambda h: setattr(h.panel, "ppms_field_scan_running", True), BUSY),
            # A closing page shows no dialog.
            ("the program closing", lambda h: setattr(h.panel, "closing", True), None),
            ("the page closed", lambda h: setattr(h.panel, "rte_closed", True), None))
FIXES = ((latch, LATCHED, "CLR"), (driver_off, DRIVER, "EO=1"))
PRESSES = ("Stop stage", "KEPCO Zero / OFF", "6221 / 2400 OFF", "close")


def press(h, name):
    """A button that stays usable while a fix job waits on the worker (review, 2026-10-17)."""
    panel = h.panel
    if name == "Stop stage":
        panel._rte_stage_stop()
    elif name == "KEPCO Zero / OFF":
        panel._rte_zero()
    elif name == "6221 / 2400 OFF":
        # ppms_sources_off sets the scan's stop event first, then queues its
        # OFF (with the 6221 and 2400 controllers this harness has not).
        panel.ppms_field_scan_stop_event.set()
    else:
        panel.zurich_executor, panel.zurich_controller = Mock(), Mock()
        panel.safe_close_ppms()


def drain_ui_only(h):
    """The post_ui callbacks alone (one drain_ui_queue), not what they
    scheduled with root.after."""
    while h.ui:
        h.ui.pop(0)()


class StageFixDriverTests(StageBase):
    def test_clear_limit_error_sends_clr_then_reads_mst_back(self):
        h = self.make()
        h.stage.connect("auto")
        h.sim.latched = 1 << 7
        start = len(h.sim.received)
        self.assertEqual(h.stage.clear_limit_error(), 0)
        self.assertEqual(h.sim.received[start:], ["MST", "CLR", "MST"])
        self.assertEqual(h.sim.latched, 0)
        self.assertIn("RT-E Newmark NSC-A1: limit error cleared (CLR; MST 128 -> 0, read back)", h.log)
        # Nothing latched any more: MST read, CLR not sent.
        start = len(h.sim.received)
        h.stage.clear_limit_error()
        self.assertEqual(h.sim.received[start:], ["MST"])
        self.assertIn("RT-E Newmark NSC-A1: no limit error is latched now (MST 0); CLR not sent", h.log)

    def test_enable_driver_sends_eo1_then_reads_eo_back(self):
        h = self.make()
        h.stage.connect("auto")
        h.sim.eo = 0
        start = len(h.sim.received)
        self.assertEqual(h.stage.enable_driver(), 1)
        self.assertEqual(h.sim.received[start:], ["MST", "EO", "EO=1", "EO"])
        self.assertEqual(h.sim.eo, 1)
        self.assertIn("RT-E Newmark NSC-A1: motor driver switched on (EO=1; EO 0 -> 1, read back)", h.log)
        start = len(h.sim.received)
        h.stage.enable_driver()
        self.assertEqual(h.sim.received[start:], ["MST", "EO"], "already on: EO=1 not sent")
        self.assertEqual(h.sim.moves, [])

    def test_both_are_refused_while_the_stage_moves(self):
        for name, arrange, fix, command in (("CLR", latch, "clear_limit_error", "CLR"),
                                            ("EO=1", driver_off, "enable_driver", "EO=1")):
            with self.subTest(name):
                h = self.make()
                h.stage.connect("auto")
                arrange(h.sim)
                h.sim._start(h.sim.px + 10 ** 6, script._now())     # turning (as after a lost stop)
                with self.assertRaisesRegex(app.NewmarkRefused, rf"the stage is moving.*{command} was not sent"):
                    getattr(h.stage, fix)()
                self.assertNotIn(command, h.sim.received)

    def test_clr_is_refused_while_a_limit_input_is_on(self):
        # CLR cannot fix a limit input's cause (a polarity problem): MST is
        # read, nothing is sent, and the refusal is a plain one, never offered
        # again (review, 2026-10-17).
        for name, inputs, latched in (("with a latched error", 1 << 4, 1 << 6), ("alone", 1 << 5, 0)):
            with self.subTest(name):
                h = self.make()
                h.stage.connect("auto")
                h.sim.inputs, h.sim.latched = inputs, latched
                start = len(h.sim.received)
                with self.assertRaisesRegex(app.NewmarkRefused, r"^a limit input is on at rest \(MST \d+: .*\), but the "
                                            r"RM-5-110 has no limit switches: a polarity problem; fix the limit "
                                            r"polarity in Newmark's QuickMotion\. CLR was not sent$") as caught:
                    h.stage.clear_limit_error()
                self.assertIs(type(caught.exception), app.NewmarkRefused, "a plain refusal, not offered")
                self.assertEqual(h.sim.received[start:], ["MST"])
                self.assertEqual(h.sim.latched, latched)

    def test_not_confirmed_raises_and_is_never_repeated(self):
        for sim, arrange, fix, command, message in (
                (ClrDoesNotClear(), latch, "clear_limit_error", "CLR",
                 r"CLR was sent, but MST read back still shows a limit error latched \(MST 64: .*\); nothing moved"),
                (EoStaysOff(), driver_off, "enable_driver", "EO=1",
                 r"EO=1 was sent, but EO read back 0, not 1: the motor driver is not on; nothing moved")):
            with self.subTest(command):
                h = self.make(sim)
                h.stage.connect("auto")
                arrange(h.sim)
                with self.assertRaisesRegex(app.NewmarkStageError, message) as caught:
                    getattr(h.stage, fix)()
                self.assertNotIsInstance(caught.exception, app.NEWMARK_FIXABLE_REFUSALS, "an error, not a question")
                self.assertEqual(h.sim.received.count(command), 1)
                self.assertEqual(h.sim.moves, [])

    def test_check_ready_reads_mst_and_eo_only(self):
        h = self.make()
        h.stage.connect("auto")
        start = len(h.sim.received)
        self.assertEqual(h.stage.check_ready(), 0)
        self.assertEqual(h.sim.received[start:], ["MST", "EO"])
        latch(h.sim)
        with self.assertRaises(app.NewmarkLimitErrorLatched) as caught:
            h.stage.check_ready()
        self.assertEqual(caught.exception.mst, 64)
        h.sim.latched = 0
        driver_off(h.sim)
        with self.assertRaises(app.NewmarkDriverOff) as caught:
            h.stage.check_ready()
        self.assertEqual(caught.exception.eo, 0)
        self.assertEqual(actions(h.sim.received), [])

    def test_the_connect_error_names_no_test_script(self):
        h = self.make(OpenFails())
        with self.assertRaises(app.NewmarkStageError) as caught:
            h.stage.connect("auto")
        self.assertEqual(str(caught.exception), "Could not open Performax device 0 (is QuickMotion or another program "
                                                "using it?)")
        self.assertFalse(h.stage.connected)


class StageFixMoveTests(StageBase):
    """Move stage with a latched limit error or the driver off (user, 2026-10-17)."""

    def move(self, sim=None, answers=(), arrange=(), before=None, target="10", safety=True, prepare=None):
        h = self.make(Watched() if sim is None else sim)
        h.stage.connect("auto")
        for step in arrange:
            step(h.sim)
        if prepare is not None:
            prepare(h)
        h.panel.rte_stage_target.set(target)
        h.panel.ppms_control_safety_var = Var(safety)
        questions = Questions(h, answers, before)
        h.start = len(h.sim.received)
        with patch.object(app.messagebox, "askyesno", questions), \
                patch.object(app.messagebox, "showerror") as showerror:
            h.panel._rte_stage_move()
            self.drain(h)
        h.questions, h.showerror = questions, showerror
        return h

    def test_a_latched_limit_error_no_sends_nothing(self):
        h = self.move(answers={LATCHED: False}, arrange=[latch])
        self.assertEqual([(q.title, q.message) for q in h.questions.asked], [(LATCHED, LATCHED_QUESTION)])
        self.assertEqual(h.questions.after(LATCHED), [], "No: nothing sent")
        self.assertEqual(actions(h.sim.received), [])
        self.assertEqual(h.sim.latched, 64)
        h.showerror.assert_not_called()
        self.assertFalse(h.questions.asked[0].busy, "asked with the rack idle again")
        self.assertFalse(h.panel.ppms_environment_busy)
        h.panel.thread_log.assert_any_call("RT-E Newmark NSC-A1: No: CLR not sent; nothing moved")

    def test_a_latched_limit_error_yes_clears_reads_back_then_moves(self):
        busy = []

        def watch_busy(h):
            h.sim.watch = lambda command: busy.append(h.panel.ppms_environment_busy) if command == "CLR" else None
        h = self.move(answers={LATCHED: True}, arrange=[latch], prepare=watch_busy)
        after = h.questions.after(LATCHED)
        self.assertEqual(after[:5], ["MST", "CLR", "MST", "ABS", "MM"], "CLR, MST read back, then the move")
        self.assertEqual(actions(after), ["CLR", "X100000"], "exactly the one command, then the move")
        self.assertEqual(busy, [True], "the rack is busy while CLR runs")
        self.assertEqual(h.sim.px, 100000)
        self.assertEqual(h.panel.rte_stage_live.get(), "Stage: +10.000° (10000 steps/°)")
        h.showerror.assert_not_called()
        self.assertEqual(h.questions.titles(), [LATCHED])
        self.assertIn("RT-E Newmark NSC-A1: limit error cleared (CLR; MST 64 -> 0, read back)", h.log)
        self.assertFalse(h.panel.ppms_environment_busy)

    def test_the_driver_off_no_sends_nothing(self):
        h = self.move(answers={DRIVER: False}, arrange=[driver_off])
        self.assertEqual([(q.title, q.message) for q in h.questions.asked], [(DRIVER, DRIVER_QUESTION)])
        self.assertEqual(h.questions.after(DRIVER), [])
        self.assertEqual(actions(h.sim.received), [])
        self.assertEqual(h.sim.eo, 0)
        h.showerror.assert_not_called()
        h.panel.thread_log.assert_any_call("RT-E Newmark NSC-A1: No: EO=1 not sent; nothing moved")

    def test_the_driver_off_yes_switches_it_on_reads_back_then_moves(self):
        h = self.move(answers={DRIVER: True}, arrange=[driver_off])
        after = h.questions.after(DRIVER)
        self.assertEqual(after[:6], ["MST", "EO", "EO=1", "EO", "ABS", "MM"], "EO=1, EO read back, then the move")
        self.assertEqual(actions(after), ["EO=1", "X100000"])
        self.assertEqual((h.sim.eo, h.sim.px), (1, 100000))
        h.showerror.assert_not_called()
        self.assertIn("RT-E Newmark NSC-A1: motor driver switched on (EO=1; EO 0 -> 1, read back)", h.log)

    def test_both_are_asked_in_turn_each_once(self):
        h = self.move(answers={LATCHED: True, DRIVER: True}, arrange=[latch, driver_off])
        self.assertEqual(h.questions.titles(), [LATCHED, DRIVER])
        self.assertEqual(actions(h.questions.after(LATCHED)), ["CLR", "EO=1", "X100000"])
        h.showerror.assert_not_called()
        # No to the second: the CLR stays done, nothing more is sent.
        h = self.move(answers={LATCHED: True, DRIVER: False}, arrange=[latch, driver_off])
        self.assertEqual(actions(h.sim.received), ["CLR"])
        self.assertEqual(h.questions.after(DRIVER), [])
        self.assertEqual(h.sim.moves, [])

    def test_never_asked_or_sent_without_the_safety_tick(self):
        # Not ticked at Move stage: refused before anything is sent.
        h = self.move(answers={}, arrange=[latch], safety=False)
        self.assertEqual(h.questions.asked, [])
        self.assertIn("safety confirmation", h.showerror.call_args.args[1])
        self.assertEqual(h.sim.received[h.start:], [])
        # Unticked while the stage was read (the refusal comes back): no
        # question, nothing more sent.
        h = self.make()
        h.stage.connect("auto")
        latch(h.sim)
        h.panel.rte_stage_target.set("10")
        questions = Questions(h, {})
        with patch.object(app.messagebox, "askyesno", questions), \
                patch.object(app.messagebox, "showerror") as showerror:
            h.panel._rte_stage_move()           # the job has run; its result waits for the Tk thread
            sent = len(h.sim.received)
            h.panel.ppms_control_safety_var.set(False)
            self.drain(h)
        self.assertEqual(questions.asked, [])
        self.assertEqual(showerror.call_args.args, ("RT-E stage", TICK))
        self.assertEqual(h.sim.received[sent:], [])
        self.assertNotIn("CLR", h.sim.received)
        # Unticked while the question is open, then Yes: refused, nothing sent.
        untick = lambda _title, h: h.panel.ppms_control_safety_var.set(False)
        for answers, arrange in (({LATCHED: True}, [latch]), ({DRIVER: True}, [driver_off])):
            with self.subTest(answers=answers):
                h = self.move(answers=answers, arrange=arrange, before=untick)
                self.assertEqual(h.showerror.call_args.args, ("RT-E stage", TICK))
                self.assertEqual(h.questions.after(next(iter(answers))), [])
                self.assertEqual(actions(h.sim.received), [])

    def test_a_fix_that_is_not_read_back_is_an_error_and_nothing_moves(self):
        for sim, arrange, answers, command, message in (
                (ClrDoesNotClear(), latch, {LATCHED: True}, "CLR",
                 "CLR was sent, but MST read back still shows a limit error latched (MST 64: "),
                (EoStaysOff(), driver_off, {DRIVER: True}, "EO=1",
                 "EO=1 was sent, but EO read back 0, not 1: the motor driver is not on; nothing moved")):
            with self.subTest(command):
                h = self.move(sim, answers=answers, arrange=[arrange])
                h.showerror.assert_called_once()
                self.assertIn(message, h.showerror.call_args.args[1])
                self.assertEqual(actions(h.sim.received), [command], "sent once, never repeated, no move")
                self.assertEqual(len(h.questions.asked), 1)
                self.assertFalse(h.panel.ppms_environment_busy)

    def test_a_problem_back_right_after_its_fix_is_an_error_not_a_second_question(self):
        h = self.move(Relatches(), answers={LATCHED: True}, arrange=[latch])
        self.assertEqual(h.questions.titles(), [LATCHED])
        h.showerror.assert_called_once()
        self.assertEqual(h.showerror.call_args.args,
                         ("RT-E stage", "The limit error is latched again (MST 64: not moving, -limit ERROR "
                                        "(latched)) right after CLR was read back, so nothing moved: check the stage"))
        self.assertEqual(actions(h.sim.received), ["CLR"])

    def test_refused_while_the_stage_moves_when_the_fix_runs(self):
        def turning(_title, h):     # the stage starts turning while the question is open
            h.sim._start(h.sim.px + 10 ** 6, script._now())
        h = self.move(answers={LATCHED: True}, arrange=[latch], before=turning)
        h.showerror.assert_called_once()
        self.assertIn("the stage is moving", h.showerror.call_args.args[1])
        self.assertIn("CLR was not sent", h.showerror.call_args.args[1])
        self.assertNotIn("CLR", h.sim.received)

    def test_a_limit_input_stays_a_plain_refusal(self):
        for arrange in ([lambda sim: setattr(sim, "inputs", 1 << 4)],
                        [lambda sim: setattr(sim, "inputs", 1 << 5), latch]):
            with self.subTest(len(arrange)):
                h = self.move(answers={}, arrange=arrange)
                self.assertEqual(h.questions.asked, [])
                h.showerror.assert_called_once()
                title, message = h.showerror.call_args.args
                self.assertEqual(title, "RT-E")
                self.assertIn("polarity problem; fix the limit polarity in Newmark's QuickMotion", message)
                self.assertEqual(actions(h.sim.received), [])

    def test_stop_pressed_while_the_stage_was_read_asks_nothing(self):
        h = self.make()
        h.stage.connect("auto")
        latch(h.sim)
        h.panel.rte_stage_target.set("10")
        questions = Questions(h, {})
        with patch.object(app.messagebox, "askyesno", questions), \
                patch.object(app.messagebox, "showerror") as showerror:
            h.panel._rte_stage_move()
            h.panel.rte_stage_stop_event.set()     # Stop stage, before the refusal reached the Tk thread
            self.drain(h)
        self.assertEqual(questions.asked, [])
        showerror.assert_not_called()
        self.assertEqual(h.panel.rte_stage_live.get(), "Stage: not moved (Stop)")
        self.assertEqual(actions(h.sim.received), [])

    # ---- review, 2026-10-17 ------------------------------------------------------
    def start_move(self, arrange, answers=(), executor=None):
        """Move stage to 10° up to its first job; returns (h, questions, showerror patch)."""
        h = self.make(Watched(), executor=executor)
        h.stage.connect("auto")
        arrange(h.sim)
        h.panel.rte_stage_target.set("10")
        return h, Questions(h, answers)

    def test_off_pressed_while_the_stage_was_read_asks_nothing(self):
        # KEPCO Zero / OFF and 6221 / 2400 OFF set the scan's stop event,
        # which the move watches too.
        for arrange, _title, command in FIXES:
            with self.subTest(command):
                h, questions = self.start_move(arrange)
                with patch.object(app.messagebox, "askyesno", questions), \
                        patch.object(app.messagebox, "showerror") as showerror:
                    h.panel._rte_stage_move()
                    h.panel.ppms_field_scan_stop_event.set()    # before the refusal reached the Tk thread
                    self.drain(h)
                self.assertEqual(questions.asked, [])
                showerror.assert_not_called()
                self.assertEqual(h.panel.rte_stage_live.get(), "Stage: not moved (OFF)")
                h.panel.thread_log.assert_any_call(
                    f"RT-E Newmark NSC-A1: OFF was pressed meanwhile: {command} not offered; nothing sent")
                self.assertEqual(actions(h.sim.received), [])

    def test_the_question_opens_outside_the_drain_with_the_buttons_locked(self):
        # The refusal comes back in a post_ui callback (inside drain_ui_queue):
        # the question opens from the Tk event loop instead, through
        # root.after(0, ...), with the buttons locked from the refusal until
        # the answer (review, 2026-10-17).
        for arrange, title, command in FIXES:
            for yes in (False, True):
                with self.subTest(command=command, yes=yes):
                    h, questions = self.start_move(arrange, {title: yes})
                    locked = []
                    h.panel._update_ppms_control_ui = Mock(
                        side_effect=lambda: locked.append(h.panel.__dict__.get("rte_stage_prompt_open")))
                    with patch.object(app.messagebox, "askyesno", questions), \
                            patch.object(app.messagebox, "showerror") as showerror:
                        h.panel._rte_stage_move()
                        drain_ui_only(h)
                        self.assertEqual(questions.asked, [], "not asked inside the drain")
                        self.assertTrue(h.panel.rte_stage_prompt_open)
                        self.assertEqual((len(h.afters), h.panel.root.delays), (1, [0]))
                        self.assertIs(locked[-1], True, "the buttons were locked as it was scheduled")
                        self.drain(h)
                    question = questions.asked[0]
                    self.assertEqual(questions.titles(), [title])
                    self.assertFalse(question.in_drain)
                    self.assertTrue(question.prompt_open, "locked while it is open")
                    self.assertFalse(question.busy)
                    self.assertFalse(h.panel.rte_stage_prompt_open, "unlocked once answered")
                    self.assertIn(False, locked[locked.index(True):], "the buttons were updated after the answer")
                    showerror.assert_not_called()
                    self.assertEqual(actions(h.sim.received), [command, "X100000"] if yes else [])

    def test_no_question_while_the_window_is_destroyed(self):
        h, questions = self.start_move(latch)
        h.panel.root.after = Mock(side_effect=app.tk.TclError("can't invoke \"after\" command"))
        with patch.object(app.messagebox, "askyesno", questions), \
                patch.object(app.messagebox, "showerror") as showerror:
            h.panel._rte_stage_move()
            self.drain(h)
        self.assertEqual(questions.asked, [])
        showerror.assert_not_called()
        self.assertFalse(h.panel.rte_stage_prompt_open)
        self.assertEqual(actions(h.sim.received), [])

    def test_the_checks_before_the_question_refuse(self):
        # Something changed between the refusal and the question (both on the
        # Tk thread): no question, nothing sent; an error dialog unless the
        # page is closing.
        for arrange, _title, command in FIXES:
            for name, change, message in RECHECKS:
                with self.subTest(command=command, check=name):
                    h, questions = self.start_move(arrange)
                    with patch.object(app.messagebox, "askyesno", questions), \
                            patch.object(app.messagebox, "showerror") as showerror:
                        h.panel._rte_stage_move()
                        drain_ui_only(h)            # the refusal is back; the question is scheduled
                        sent = len(h.sim.received)
                        change(h)
                        self.drain(h)
                    self.assertEqual(questions.asked, [])
                    self.assertEqual(h.sim.received[sent:], [])
                    self.assertEqual(actions(h.sim.received), [])
                    self.assertFalse(h.panel.rte_stage_prompt_open)
                    if message is None:
                        showerror.assert_not_called()
                    else:
                        self.assertEqual(showerror.call_args.args, ("RT-E stage", message))
                        h.panel.thread_log.assert_any_call(f"RT-E Newmark NSC-A1: {command} not offered: {message}")

    def test_the_checks_after_the_yes_refuse(self):
        for arrange, title, command in FIXES:
            for name, change, message in RECHECKS:
                with self.subTest(command=command, check=name):
                    h = self.move(answers={title: True}, arrange=[arrange],
                                  before=lambda _title, h, change=change: change(h))
                    self.assertEqual(h.questions.titles(), [title])
                    self.assertEqual(h.questions.after(title), [], "nothing sent after the Yes")
                    self.assertEqual(actions(h.sim.received), [])
                    self.assertFalse(h.panel.rte_stage_prompt_open)
                    if message is None:
                        h.showerror.assert_not_called()
                        h.panel.thread_log.assert_any_call(f"RT-E Newmark NSC-A1: {command} not sent: RT-E is closing")
                    else:
                        self.assertEqual(h.showerror.call_args.args, ("RT-E stage", message))
                        h.panel.thread_log.assert_any_call(f"RT-E Newmark NSC-A1: {command} not sent: {message}")

    def test_a_limit_input_on_when_clr_runs_refuses_it(self):
        def input_on(_title, h):        # a limit input comes on while the question is open
            h.sim.inputs = 1 << 4
        h = self.move(answers={LATCHED: True}, arrange=[latch], before=input_on)
        h.showerror.assert_called_once()
        title, message = h.showerror.call_args.args
        self.assertEqual(title, "RT-E")
        self.assertRegex(message, r"^a limit input is on at rest \(MST 80: not moving, -limit input on, -limit ERROR "
                                  r"\(latched\)\), but .*polarity problem.*QuickMotion\. CLR was not sent$")
        self.assertEqual(h.questions.after(LATCHED), ["MST"], "MST read, CLR not sent, no move")
        self.assertEqual(actions(h.sim.received), [])
        self.assertEqual(h.questions.titles(), [LATCHED])

    def test_stop_off_or_close_after_the_yes_sends_no_fix(self):
        # After the Yes, the fix job can wait on the one worker behind a read
        # (the idle poll).  A Stop stage, KEPCO Zero / OFF, 6221 / 2400 OFF
        # or closing pressed meanwhile: no CLR or EO=1, no move; the move
        # reports the Stop (review, 2026-10-17).
        for arrange, title, command in FIXES:
            for name in PRESSES:
                with self.subTest(command=command, pressed=name):
                    executor = DeferredExecutor()
                    h, questions = self.start_move(arrange, {title: True}, executor=executor)
                    shown, show = [], h.panel.rte_stage_live.set
                    h.panel.rte_stage_live.set = lambda text: (shown.append(text), show(text))
                    with patch.object(app.messagebox, "askyesno", questions), \
                            patch.object(app.messagebox, "showerror") as showerror:
                        h.panel._rte_stage_move()
                        executor.run()
                        self.drain(h)               # the refusal, then the question: Yes
                        self.assertEqual(questions.titles(), [title])
                        self.assertEqual(len(executor.jobs), 1, "the fix job waits on the worker")
                        press(h, name)
                        executor.run()
                        self.drain(h)
                    self.assertNotIn(command, h.sim.received)
                    self.assertFalse({"CLR", "EO=1"} & set(h.sim.received))
                    self.assertEqual(h.sim.moves, [])
                    self.assertEqual(questions.titles(), [title], "not asked again")
                    showerror.assert_not_called()
                    if name == "close":
                        h.panel.thread_log.assert_any_call(
                            f"RT-E Newmark NSC-A1: {command} not sent: RT-E is closing after the Yes")
                        self.assertFalse(h.stage.connected, "closed by the clean-up queued after it")
                    else:
                        reason = "Stop stage was pressed" if name == "Stop stage" else "an OFF button was pressed"
                        h.panel.thread_log.assert_any_call(
                            f"RT-E Newmark NSC-A1: {command} not sent: {reason} after the Yes")
                        self.assertIn(f"Stage: not moved ({'Stop' if name == 'Stop stage' else 'OFF'})", shown)


class StageFixAngleStartTests(StageBase):
    """An Angle scan's Start with a latched limit error or the driver off: the
    stage check runs before any instrument is set up (user, 2026-10-17)."""

    def equip(self, h):
        """The real start_ppms_field_scan on the stage harness, its scan only recorded."""
        panel = h.panel
        panel._rte_capture_zurich = Mock()
        panel.zurich_scan_enabled = False
        panel.ppms_simple_mode = Var("Angle")
        panel.ppms_simple_range = [Var(v) for v in ("0", "10", "5")]
        panel.ppms_field_scan_loop_var = Var("1")
        panel.rte_round_trip = Var(True)
        panel.rte_controllers["gauss"].connected = True
        panel.rte_controllers["kepco"].connected = True
        panel.rte_drive_target, panel.rte_voltage_limit = Var("1.5"), Var("5")
        settings = app.validate_ppms_transport_scan_settings(.1, 17.3, 50., 1, 2, ("830_a",))
        panel._ppms_direct_transport_settings = Mock(return_value=settings)
        panel._rte_harmonic_passes = Mock(return_value=[settings])
        panel.ppms_field_scan_stable_var = Var("0")
        panel.ppms_acquisition_time_var = Var("1")
        panel.ppms_field_scan_progress = Mock()
        panel.ppms_field_scan_status_var = Var("")
        panel._redraw_ppms_transport_plot = Mock()
        panel._rte_show_point = Mock()

    def start(self, sim=None, answers=(), arrange=(), before=None, safety=True, executor=None, settle=None,
              prepare=None):
        executor = rte_tests.StartExecutor() if executor is None else executor
        h = self.make(Watched() if sim is None else sim, executor=executor)
        if isinstance(executor, ThreadPoolExecutor):
            executor.submit(h.stage.connect, "auto").result(timeout=30)
        else:
            h.stage.connect("auto")
        self.equip(h)
        for step in arrange:
            step(h.sim)
        if prepare is not None:
            prepare(h)
        h.panel.ppms_control_safety_var = Var(safety)
        folder = tempfile.TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        h.folder = folder.name
        kepco = h.panel.rte_controllers["kepco"]
        snapshot = lambda: dict(csv=os.listdir(folder.name), kepco=kepco.set_output.call_count,
                                source=h.panel._configure_ppms_transport_scan.call_count,
                                scans=len(executor.scans()) if hasattr(executor, "scans") else None)
        questions = Questions(h, {SCAN: True, **dict(answers)}, before, snapshot)
        h.start = len(h.sim.received)
        with patch.object(app.filedialog, "askdirectory", return_value=folder.name), \
                patch.object(app.simpledialog, "askstring", return_value=""), \
                patch.object(app.messagebox, "askyesno", questions), \
                patch.object(app.messagebox, "showerror") as showerror:
            h.panel.start_ppms_field_scan()
            if settle is None:
                self.drain(h)
            else:
                settle(h)
        h.questions, h.showerror, h.executor = questions, showerror, executor
        return h

    def assert_not_started(self, h):
        self.assertEqual(h.executor.scans(), [])
        self.assertFalse(h.panel.ppms_field_scan_running)
        self.assertEqual(os.listdir(h.folder), [], "no CSV made")
        h.panel.rte_controllers["kepco"].set_output.assert_not_called()

    def assert_started(self, h):
        scans = h.executor.scans()
        self.assertEqual(len(scans), 1)
        self.assertEqual(scans[0][1], "Angle")
        self.assertTrue(h.panel.ppms_field_scan_running)
        self.assertEqual(len(os.listdir(h.folder)), 1)

    def test_a_ready_stage_reads_mst_and_eo_then_starts(self):
        h = self.start()
        self.assertEqual(h.questions.titles(), [SCAN])
        self.assertEqual(h.sim.received[h.start:], ["MST", "EO"], "the check, and nothing else, before the scan")
        self.assertEqual(h.questions.asked[0].snapshot["csv"], [])
        self.assert_started(h)
        h.showerror.assert_not_called()

    def test_a_latched_limit_error_asked_before_any_instrument_is_set_up(self):
        h = self.start(answers={LATCHED: False}, arrange=[latch])
        self.assertEqual(h.questions.titles(), [SCAN, LATCHED], "the scan's Yes/No first, then the stage's")
        question = h.questions.asked[1]
        self.assertEqual(question.message, LATCHED_QUESTION)
        self.assertEqual(question.snapshot, dict(csv=[], kepco=0, source=0, scans=0), "no instrument set up yet")
        self.assertFalse(question.busy)
        self.assertEqual(h.sim.received[h.start:question.sent], ["MST"])
        # No: nothing sent, nothing started.
        self.assertEqual(h.questions.after(LATCHED), [])
        self.assert_not_started(h)
        h.showerror.assert_not_called()
        h.panel.thread_log.assert_any_call("RT-E Newmark NSC-A1: No: CLR not sent; nothing moved")
        # From the Tk event loop, not inside the drain; the status line says
        # why the scan did not start (review, 2026-10-17).
        self.assertFalse(question.in_drain)
        self.assertTrue(question.prompt_open)
        self.assertEqual(h.panel.ppms_field_scan_status_var.get(), "Angle scan not started: CLR not sent (No)")

    def test_a_latched_limit_error_yes_clears_then_the_scan_starts(self):
        h = self.start(answers={LATCHED: True}, arrange=[latch])
        self.assertEqual(h.questions.after(LATCHED), ["MST", "CLR", "MST", "MST", "EO"],
                         "CLR read back, then the check again; no move before the scan")
        self.assertEqual(actions(h.sim.received), ["CLR"])
        self.assert_started(h)
        h.showerror.assert_not_called()
        self.assertEqual(h.panel.ppms_field_scan_status_var.get(),
                         "RT-E Angle 0° to 10°, KEPCO +1.5 A held; 5 points")

    def test_the_driver_off_no_and_yes(self):
        h = self.start(answers={DRIVER: False}, arrange=[driver_off])
        self.assertEqual(h.questions.titles(), [SCAN, DRIVER])
        self.assertEqual(h.questions.asked[1].message, DRIVER_QUESTION)
        self.assertEqual(h.sim.received[h.start:h.questions.asked[1].sent], ["MST", "EO"])
        self.assertEqual(h.questions.asked[1].snapshot, dict(csv=[], kepco=0, source=0, scans=0))
        self.assertEqual(h.questions.after(DRIVER), [])
        self.assert_not_started(h)
        self.assertEqual(h.panel.ppms_field_scan_status_var.get(), "Angle scan not started: EO=1 not sent (No)")
        h = self.start(answers={DRIVER: True}, arrange=[driver_off])
        self.assertEqual(h.questions.after(DRIVER), ["MST", "EO", "EO=1", "EO", "MST", "EO"])
        self.assertEqual(actions(h.sim.received), ["EO=1"])
        self.assert_started(h)
        h.showerror.assert_not_called()

    def test_never_asked_or_sent_without_the_safety_tick(self):
        h = self.start(answers={}, arrange=[latch], safety=False)
        self.assertEqual(h.questions.asked, [])
        self.assertEqual(h.showerror.call_args.args, ("RT-E scan parameters", TICK))
        self.assertEqual(h.sim.received[h.start:], [], "nothing sent")
        self.assert_not_started(h)
        untick = lambda title, h: h.panel.ppms_control_safety_var.set(False) if title != SCAN else None
        for answers, arrange in (({LATCHED: True}, [latch]), ({DRIVER: True}, [driver_off])):
            with self.subTest(answers=answers):
                h = self.start(answers=answers, arrange=arrange, before=untick)
                self.assertEqual(h.showerror.call_args.args, ("RT-E stage", TICK))
                self.assertEqual(h.questions.after(next(iter(answers))), [])
                self.assertEqual(actions(h.sim.received), [])
                self.assert_not_started(h)
                self.assertEqual(h.panel.ppms_field_scan_status_var.get(), f"Angle scan not started: {TICK}")

    def test_a_clr_that_does_not_clear_starts_nothing(self):
        h = self.start(ClrDoesNotClear(), answers={LATCHED: True}, arrange=[latch])
        h.showerror.assert_called_once()
        self.assertIn("CLR was sent, but MST read back still shows a limit error latched",
                      h.showerror.call_args.args[1])
        self.assertEqual(actions(h.sim.received), ["CLR"])
        self.assert_not_started(h)
        # Cut to 44 characters for the status line; the dialog has it all.
        self.assertEqual(h.panel.ppms_field_scan_status_var.get(),
                         "Angle scan not started: CLR was sent, but MST read back still shows…")

    def test_a_limit_input_stays_a_plain_refusal(self):
        h = self.start(answers={}, arrange=[lambda sim: setattr(sim, "inputs", 1 << 5), latch])
        self.assertEqual(h.questions.titles(), [SCAN])
        h.showerror.assert_called_once()
        self.assertIn("polarity problem", h.showerror.call_args.args[1])
        self.assertEqual(actions(h.sim.received), [])
        self.assert_not_started(h)
        self.assertEqual(h.panel.ppms_field_scan_status_var.get(),
                         "Angle scan not started: a limit input is on at rest (MST 96: not mo…")

    def test_stop_stage_during_the_check_keeps_the_scan_from_starting(self):
        def stop_during_the_check(h):
            # Stop stage pressed while the check reads MST.
            h.sim.watch = lambda command: h.panel.rte_stage_stop_event.set() if command == "MST" else None
        h = self.start(prepare=stop_during_the_check)
        h.showerror.assert_not_called()
        self.assertEqual(h.questions.titles(), [SCAN])
        self.assert_not_started(h)
        self.assertEqual(h.panel.ppms_field_scan_status_var.get(), "Angle scan not started: Stop stage was pressed")
        h.panel.thread_log.assert_any_call("RT-E Angle scan not started: Stop stage was pressed during the stage check")
        self.assertEqual(actions(h.sim.received), [])

    # ---- review, 2026-10-17 ------------------------------------------------------
    @staticmethod
    def during_the_check(event_name):
        """prepare: KEPCO Zero / OFF (the scan's stop event) or Stop stage
        pressed while the check reads MST."""
        def prepare(h):
            event = getattr(h.panel, event_name)
            h.sim.watch = lambda command: event.set() if command == "MST" else None
        return prepare

    def test_off_during_the_check_keeps_the_scan_from_starting(self):
        h = self.start(prepare=self.during_the_check("ppms_field_scan_stop_event"))
        h.showerror.assert_not_called()
        self.assertEqual(h.questions.titles(), [SCAN])
        self.assert_not_started(h)
        self.assertEqual(h.panel.ppms_field_scan_status_var.get(), "Angle scan not started: an OFF button was pressed")
        h.panel.thread_log.assert_any_call("RT-E Angle scan not started: an OFF button was pressed during the stage "
                                           "check")
        self.assertEqual(actions(h.sim.received), [])

    def test_stop_or_off_during_a_check_that_ends_in_a_fixable_refusal(self):
        # The check's refusal comes back after the Stop or OFF: no question,
        # nothing sent, and the status line says the scan did not start.
        for arrange, _title, command in FIXES:
            for event, pressed, reason in (("rte_stage_stop_event", "Stop", "Stop stage was pressed"),
                                           ("ppms_field_scan_stop_event", "OFF", "an OFF button was pressed")):
                with self.subTest(command=command, pressed=pressed):
                    h = self.start(arrange=[arrange], prepare=self.during_the_check(event))
                    self.assertEqual(h.questions.titles(), [SCAN])
                    h.showerror.assert_not_called()
                    self.assert_not_started(h)
                    self.assertEqual(h.panel.ppms_field_scan_status_var.get(), f"Angle scan not started: {reason}")
                    self.assertEqual(h.panel.rte_stage_live.get(), f"Stage: not moved ({pressed})")
                    h.panel.thread_log.assert_any_call(f"RT-E Newmark NSC-A1: {pressed} was pressed meanwhile: "
                                                       f"{command} not offered; nothing sent")
                    self.assertEqual(actions(h.sim.received), [])

    def test_a_refusal_before_the_question_or_after_the_check_shows_on_the_status_line(self):
        def unconfirmed_meanwhile(h):
            drain_ui_only(h)            # the check's refusal is back; the question is scheduled
            h.stage.motion_may_run = True
            self.drain(h)
        h = self.start(arrange=[latch], settle=unconfirmed_meanwhile)
        self.assertEqual(h.questions.titles(), [SCAN])
        self.assertEqual(h.showerror.call_args.args, ("RT-E stage", NOT_CONFIRMED))
        self.assert_not_started(h)
        self.assertEqual(h.panel.ppms_field_scan_status_var.get(),
                         "Angle scan not started: The Newmark NSC-A1's last stop was not conf…")
        # A ready stage, the safety unticked during its check: `checked` refuses.
        def untick_during_the_check(h):
            h.sim.watch = lambda command: h.panel.ppms_control_safety_var.set(False) if command == "EO" else None
        h = self.start(prepare=untick_during_the_check)
        self.assertEqual(h.showerror.call_args.args, ("RT-E scan not started", TICK))
        self.assert_not_started(h)
        self.assertEqual(h.panel.ppms_field_scan_status_var.get(), f"Angle scan not started: {TICK}")

    def test_stop_off_or_close_after_the_yes_starts_nothing(self):
        # After the Yes, the fix job can wait on the one worker behind a read
        # (the idle poll): a Stop stage, KEPCO Zero / OFF, 6221 / 2400 OFF or
        # closing meanwhile sends no CLR or EO=1, and the scan does not start
        # (review, 2026-10-17).
        for arrange, title, command in FIXES:
            for name in PRESSES:
                with self.subTest(command=command, pressed=name):
                    executor = DeferredExecutor()

                    def settle(h, executor=executor, name=name):
                        executor.run()
                        self.drain(h)           # the check's refusal, then the stage's question: Yes
                        self.assertEqual(len(executor.jobs), 1, "the fix job waits on the worker")
                        press(h, name)
                        executor.run()
                        self.drain(h)
                    h = self.start(answers={title: True}, arrange=[arrange], executor=executor, settle=settle)
                    self.assertEqual(h.questions.titles(), [SCAN, title])
                    self.assertFalse({"CLR", "EO=1"} & set(h.sim.received))
                    self.assertEqual(h.sim.moves, [])
                    self.assert_not_started(h)
                    h.showerror.assert_not_called()
                    status = h.panel.ppms_field_scan_status_var.get()
                    if name == "close":
                        self.assertEqual(status, "", "a closing page shows nothing")
                        h.panel.thread_log.assert_any_call(
                            f"RT-E Newmark NSC-A1: {command} not sent: RT-E is closing after the Yes")
                    else:
                        reason = "Stop stage was pressed" if name == "Stop stage" else "an OFF button was pressed"
                        self.assertEqual(status, f"Angle scan not started: {reason}")
                        h.panel.thread_log.assert_any_call(
                            f"RT-E Newmark NSC-A1: {command} not sent: {reason} after the Yes")

    def test_field_and_current_starts_never_check_the_stage(self):
        # Only an Angle Start reads the stage; Field and Current start at
        # once, with no stage command and no stage question, whatever the
        # stage's state (review, 2026-10-17).
        for mode, span in (("Field", ("0", "1", "0.5")), ("Current", ("0.01", "0.03", "0.01"))):
            with self.subTest(mode):
                def prepare(h, mode=mode, span=span):
                    h.panel.ppms_simple_mode = Var(mode)
                    h.panel.ppms_simple_range = [Var(v) for v in span]
                    h.stage.check_ready = Mock(wraps=h.stage.check_ready)
                h = self.start(arrange=[latch, driver_off], prepare=prepare)
                h.stage.check_ready.assert_not_called()
                self.assertEqual(h.sim.received[h.start:], [], "no stage command")
                self.assertEqual(h.questions.asked, [])
                h.showerror.assert_not_called()
                scans = h.executor.scans()
                self.assertEqual([scan[1] for scan in scans], [mode])
                self.assertEqual(h.executor.submit.call_count, 1, "the scan alone")
                self.assertTrue(h.panel.ppms_field_scan_running)

    def test_angle_start_with_a_fix_runs_the_scan_on_the_worker(self):
        # A real single-worker executor, as the program's: the check, CLR,
        # EO=1 and the whole scan on it, none on the Tk (test) thread.
        executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="rte-control")
        self.addCleanup(executor.shutdown, wait=True)
        worker = executor.submit(threading.get_ident).result(30)

        def settle_all(h):
            for _ in range(20):
                self.settle(h, executor)
                if len(h.panel.ppms_field_scan_rows) == 5 and not h.panel.ppms_field_scan_running:
                    return
            self.fail("the scan did not end")
        h = self.start(answers={LATCHED: True, DRIVER: True}, arrange=[latch, driver_off],
                       executor=executor, settle=settle_all)
        h.showerror.assert_not_called()
        self.assertEqual(h.questions.titles(), [SCAN, LATCHED, DRIVER])
        self.assertEqual(actions(h.sim.received), ["CLR", "EO=1", "X50000", "X100000", "X50000", "X0"])
        self.assertEqual([row["stage_target_deg"] for row in h.panel.ppms_field_scan_rows], [0., 5., 10., 5., 0.])
        self.assertEqual({thread for _name, thread in h.dll.calls}, {worker})
        self.assertNotIn(threading.get_ident(), {thread for _name, thread in h.dll.calls})


class StageFixThreadTests(StageBase):
    """With a real single-worker executor, as the program's: every DLL call of
    the fixes and the move after them runs on the worker, and Stop stage
    still stops a move that follows a fix (user, 2026-10-17)."""

    def test_move_stage_fixes_and_stop_stage_on_the_worker(self):
        executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="rte-control")
        self.addCleanup(executor.shutdown, wait=True)
        h = self.make(Watched(), executor=executor)
        worker = executor.submit(lambda: (threading.get_ident(), threading.current_thread().name)).result(30)
        executor.submit(h.stage.connect, "auto").result(timeout=30)
        latch(h.sim)
        driver_off(h.sim)
        threads = []
        h.sim.watch = lambda command: threads.append((command, threading.get_ident())) \
            if command in ("CLR", "EO=1") else None
        reached, proceed, busy = threading.Event(), threading.Event(), []

        def hold():
            busy.append(h.panel.ppms_environment_busy)
            reached.set()
            proceed.wait(10)
        h.clock.hooks.append((h.clock.t + 1.0, hold))   # 1 s into the move after the fixes
        questions = Questions(h, {LATCHED: True, DRIVER: True})
        with patch.object(app.messagebox, "askyesno", questions), \
                patch.object(app.messagebox, "showerror") as showerror:
            h.panel.rte_stage_target.set("90")
            h.panel._rte_stage_move()
            # The Tk thread's turn: each refusal's question as it arrives (the
            # CLR one, then after CLR the EO=1 one), until the move runs.
            deadline = time.monotonic() + 20
            while not reached.wait(0.01):
                self.drain(h)
                self.assertLess(time.monotonic(), deadline, "the move after EO=1 did not start")
            self.assertEqual(busy, [True], "a move keeps the rack busy")
            calls = len(h.dll.calls)
            h.panel._rte_stage_stop()                   # the Tk thread only sets the event
            self.assertEqual(len(h.dll.calls), calls)
            proceed.set()
            self.settle(h, executor)
        showerror.assert_not_called()
        self.assertEqual(questions.titles(), [LATCHED, DRIVER])
        self.assertEqual([command for command, _thread in threads], ["CLR", "EO=1"])
        self.assertEqual({thread for _command, thread in threads}, {worker[0]})
        self.assertEqual(actions(h.sim.received), ["CLR", "EO=1", "X900000", "STOP"])
        self.assertTrue(h.panel.rte_stage_live.get().endswith("(Stop)"), h.panel.rte_stage_live.get())
        self.assertFalse(h.panel.ppms_environment_busy)
        self.assertEqual({thread for _name, thread in h.dll.calls}, {worker[0]})
        self.assertTrue(worker[1].startswith("rte-control"))


class ProgramIndependenceTests(unittest.TestCase):
    """The program needs no Newmark test script (user, 2026-10-17): it never
    imports it, and no text it shows or raises names it.  A comment may name
    it once, as the separate, optional test tool the driver came from."""

    def test_no_import_and_no_string_names_the_test_script(self):
        path = Path(app.__file__)
        source = path.read_text(encoding="utf-8")
        tree = ast.parse(source)
        for node in ast.walk(tree):
            names = ([alias.name for alias in node.names] if isinstance(node, ast.Import) else
                     [node.module or ""] if isinstance(node, ast.ImportFrom) else [])
            for name in names:
                self.assertNotIn("motor_test", name.lower(), f"line {node.lineno}")
        # Every string constant: messages, f-string parts, docstrings.
        strings = [node for node in ast.walk(tree) if isinstance(node, ast.Constant) and isinstance(node.value, str)]
        self.assertGreater(len(strings), 1000)
        named = [(node.lineno, node.value) for node in strings if "motor_test" in node.value.lower()]
        self.assertEqual(named, [])
        with open(path, "rb") as handle:
            comments = [token for token in tokenize.tokenize(handle.readline) if token.type == tokenize.COMMENT]
        mentions = [token for token in comments if "motor_test" in token.string.lower()]
        self.assertEqual(len(mentions), 1, [token.string for token in mentions])
        lines = source.splitlines()
        row = mentions[0].start[0] - 1
        block = []
        for index in range(row - 5, row + 6):
            if lines[index].lstrip().startswith("#"):
                block.append(lines[index].lstrip("# ").strip())
        block = " ".join(block)
        for text in ("needs no other script or module", "separate, optional test tool",
                     "never imports or calls it", "user, 2026-10-17"):
            self.assertIn(text, block)


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
