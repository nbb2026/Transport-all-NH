"""STFMR page: the Newmark stage's Motor row, Set motor 0° and the angle loop (user, 2026-10-17).

The user asked: "add a Start Angle, Stop Angle, Step Angle below mod. Hz in
ST-FMR module.  Also add the connection button for Motor in RT-E and ST-FMR
modules", then "also add Set 0° button for the motor in ST-FMR".  The angles
are the outermost loop (angle x modulation x power x carrier x current); the
motor's USB device opens on one page at a time; Start checks the stage (MST,
EO) before any folder, file or output and offers RT-E's CLR / EO=1 Yes/No;
an angle change zeroes the 4886, turns the stage with the RF on, sets the
modulation and the power back and switches the 4886 on again at Start A.

No Tk window and no instrument: the real sweep runs on the shared STFMR
harness (stand-in devices that record every call in one list, a Root that
runs callbacks at once but keeps each wait), and the real
NewmarkNSCA1Controller runs on the fake PerformaxCom.dll of
test_rte_angle_scan (the standalone script's simulated NSC-A1, on a virtual
clock).  No real DLL is ever loaded; every dialog is mocked, and creating a
Tk root fails the test."""
from concurrent.futures import Future, ThreadPoolExecutor
import ctypes
import itertools
import os
import re
import sys
import threading
import types
import unittest
from pathlib import Path
from unittest import mock

_HERE = Path(__file__).resolve().parent
for _path in (_HERE / "test", _HERE.parent):
    if str(_path) not in sys.path:
        sys.path.insert(0, str(_path))
from test_stfmr_realtime_recording import Variable, app, events, harness, read_csv
from test_stfmr_sr830_tc_fixed_current import History, NoWindows, TimedRoot, build_stfmr_page
from test_stfmr_mod_prime_range import width_in_zeros
from test_stfmr_power_sweep import PowerRecording
import test_rte_angle_scan as rte_stage
from test_rte_angle_scan import Clock, RecordingDLL, Sim, script

App = app.CombinedControlApp
LABEL = 52      # the status label's width, in widths of "0"
_guards = []


def setUpModule():
    def refuse(what):
        def refused(*_args, **_kwargs):
            raise AssertionError(f"a test reached {what}")
        return refused
    targets = [(ctypes, "WinDLL", refuse("ctypes.WinDLL (a real DLL)")),
               (os, "add_dll_directory", refuse("os.add_dll_directory")),
               (app, "newmark_load_performax", refuse("the program's real Newmark DLL loader")),
               (rte_stage.app, "newmark_load_performax", refuse("the program's real Newmark DLL loader"))]
    for target, name, replacement in targets:
        guard = mock.patch.object(target, name, replacement, create=True)
        guard.start()
        _guards.append(guard)


def tearDownModule():
    while _guards:
        _guards.pop().stop()


class Deferred:
    """The STFMR worker, run by the test: jobs wait in order until run()."""

    def __init__(self):
        self.jobs = []

    def submit(self, fn, *args):
        future = Future()
        self.jobs.append((future, fn, args))
        return future

    def run(self):
        while self.jobs:
            future, fn, args = self.jobs.pop(0)
            try:
                future.set_result(fn(*args))
            except BaseException as error:
                future.set_exception(error)

    def shutdown(self, **_kwargs):
        pass


class MotorBase(NoWindows):
    """The STFMR harness with recording devices and the real stage
    controller (owner_scope "STFMR") on the fake DLL."""

    def make(self, angles=("0", "90", "90"), mods=("317.3", "317.3"), powers=("10", "10", "1"),
             stop="5", end_current="0.5", meter="SR830", sim=None, connect=True, executor=None):
        h = harness(self.tmp.name, meter=meter, stop=stop, end_current=end_current)
        h.root = TimedRoot()
        h.stfmr_lockin_status_var = History()
        h.stfmr_lockin_settle_var.set("0.05")
        h.stfmr_lockin_sr830_time_constant_var = Variable("100 ms")
        h.stfmr_lockin_mod_start_var.set(mods[0])
        h.stfmr_lockin_mod_end_var.set(mods[1])
        h.stfmr_lockin_power_start_var.set(powers[0])
        h.stfmr_lockin_power_stop_var.set(powers[1])
        h.stfmr_lockin_power_step_var.set(powers[2])
        h.log = []
        h.stfmr_lockin_controllers = {key: PowerRecording(h, key, h.log) for key in ("kepco", "gauss", "smb", "830")}
        h.stfmr_lockin_mfli_controller = PowerRecording(h, "mfli", h.log)
        h.progress = []
        h._set_stfmr_progress = lambda _bar, done, total: h.progress.append((done, total))
        h._append_stfmr_lockin_live_sample = mock.Mock()
        h.logged = []
        h.thread_log = h.logged.append
        clock = Clock()
        guard = mock.patch.object(script, "_now", clock.now)
        guard.start()
        self.addCleanup(guard.stop)
        sim = Sim() if sim is None else sim
        dll = RecordingDLL(sim)
        motor_log = []
        motor = app.NewmarkNSCA1Controller(motor_log.append, owner_scope="STFMR",
                                           loader=lambda: (dll, "fake PerformaxCom.dll"))
        motor._now, motor._sleep = clock.now, clock.sleep
        real_move = motor.move_to_deg
        h.move_hooks = {}

        def move(target, stop_event, on_progress=None):
            h.log.append(("motor", "move", float(target)))
            hook = h.move_hooks.pop(float(target), None)
            if hook is not None:
                hook()
            return real_move(target, stop_event, on_progress)

        motor.move_to_deg = move
        motor.check_ready = mock.Mock(side_effect=motor.check_ready)
        h.stfmr_lockin_motor = motor
        h.stfmr_lockin_motor_status_var = Variable("Not connected")
        h.stfmr_lockin_motor_resource_var = Variable("auto")
        h.stfmr_lockin_motor_busy = False
        h.stfmr_lockin_motor_check_pending = False
        h.stfmr_lockin_motor_known_deg = None
        h.stfmr_lockin_angle_start_var = Variable(angles[0])
        h.stfmr_lockin_angle_stop_var = Variable(angles[1])
        h.stfmr_lockin_angle_step_var = Variable(angles[2])
        h.newmark_stage_ownership = app.NewmarkStageOwnership()
        h.stfmr_lockin_stage_target_var = Variable("0")
        h.stfmr_lockin_stage_stop_event = threading.Event()
        h.stfmr_lockin_stage_move_pending = False
        h.stfmr_lockin_stage_prompt_open = False
        # With the motor the sweep starts after the stage check, and its end
        # releases the recorder: kept here for the test.
        h.last_recorder = None

        def close_recording(outcome, **details):
            h.last_recorder = h.stfmr_lockin_recorder or h.last_recorder
            return App._close_stfmr_lockin_recording(h, outcome, **details)
        h._close_stfmr_lockin_recording = close_recording
        if executor is not None:
            h.stfmr_lockin_executor = executor
        if connect:
            h.newmark_stage_ownership.claim("STFMR")
            motor.connect("auto")
        return types.SimpleNamespace(h=h, motor=motor, sim=sim, dll=dll, clock=clock, motor_log=motor_log)

    def sweep(self, start_only=False, **options):
        """Start (and with start_only, step only until the sweep runs:
        with the motor it starts after the stage check), else run it to its
        end; run.recorder is the sweep's recorder."""
        run = self.make(**options)
        h = run.h
        h.start_stfmr_lockin_scan()
        for _ in range(100):
            if h.stfmr_lockin_running or not h.root.pending:
                break
            h.root.step()
        run.recorder = h.stfmr_lockin_recorder
        if not start_only:
            h.root.drain()
            run.recorder = run.recorder or h.last_recorder
        return run

    @staticmethod
    def window(log, center):
        """The calls from the 4886 zero before ``center`` to its 4886 output after."""
        index = log.index(center)
        first = max(i for i in range(index) if log[i][:2] == ("kepco", "zero"))
        last = min(i for i in range(index, len(log)) if log[i][:2] == ("kepco", "output"))
        return log[first:last + 1]


# ---- the angle list and the rules at Start ---------------------------------------
class AngleListTests(unittest.TestCase):
    angles = staticmethod(app.stfmr_angle_list)

    def test_defaults_and_the_rt_e_rule(self):
        self.assertEqual((app.STFMR_LOCKIN_DEFAULT_ANGLE_START_DEG, app.STFMR_LOCKIN_DEFAULT_ANGLE_STOP_DEG,
                          app.STFMR_LOCKIN_DEFAULT_ANGLE_STEP_DEG), (0.0, 0.0, 10.0))
        self.assertEqual(self.angles("0", "0", "10"), (0.0,))
        self.assertEqual(self.angles("0", "90", "10"), tuple(float(a) for a in range(0, 91, 10)))
        self.assertEqual(len(self.angles(0, 90, 10)), 10)
        self.assertEqual(self.angles("90", "0", "30"), (90.0, 60.0, 30.0, 0.0), "descending")
        self.assertEqual(self.angles("0", "90", "-30"), (0.0, 30.0, 60.0, 90.0), "the step's sign is ignored")
        self.assertEqual(self.angles("0", "100", "30"), (0.0, 30.0, 60.0, 90.0, 100.0), "ends on Angle stop")
        self.assertEqual(self.angles("0", "0.3", "0.1"), (0.0, 0.1, 0.2, 0.3), "no float dust")
        self.assertEqual(self.angles(" -360 ", "360", "360"), (-360.0, 0.0, 360.0))
        # The same nodes as the RT-E Angle scan's.
        for start, stop, step in (("0", "45", "7"), ("30", "-30", "12.5")):
            self.assertEqual(self.angles(start, stop, step),
                             tuple(round(v, 9) for v in app.ppms_scan_axis(start, stop, step)))

    def test_refusals(self):
        for start, stop, step in (("", "0", "10"), ("x", "0", "10"), ("0", "nan", "10"), ("0", "inf", "10"),
                                  ("0", "90", ""), (None, "0", "10")):
            with self.subTest(start=start, stop=stop, step=step):
                with self.assertRaisesRegex(ValueError, "^Angle start °, Angle stop ° and the angle Step ° must be "
                                                        "finite numbers$"):
                    self.angles(start, stop, step)
        for start, stop in (("0", "0"), ("0", "90")):
            with self.assertRaisesRegex(ValueError, r"^The angle Step ° must be greater than 0°$"):
                self.angles(start, stop, "0")
        for start, stop in (("0", "360.5"), ("-361", "0"), ("350", "370")):
            with self.subTest(start=start, stop=stop):
                with self.assertRaisesRegex(ValueError, r"within ±360° of the stage zero \(the sample wires\)"):
                    self.angles(start, stop, "5")
        with self.assertRaisesRegex(ValueError, "at most 10001 points"):
            self.angles("0", "360", "0.01")

    def test_the_confirmation_line_and_the_move_time(self):
        self.assertEqual(app.stfmr_angle_summary((), 10, False), "Angle: motor not used")
        self.assertEqual(app.stfmr_angle_summary(self.angles(0, 90, 10), 10, True),
                         "Angle: 0 → 90°, step 10°, 10 angles (the motor turns the SAMPLE HOLDER with its "
                         "wires; angle outermost; the 4886 at zero during every move)")
        # With the motor connected 0 to 0 turns the stage to 0°, by design,
        # and the line says so plainly (review, 2026-10-17).
        self.assertEqual(app.stfmr_angle_summary((0.0,), 10, True),
                         "Angle: 0° (one angle: the stage turns to 0° first, the motor turning the SAMPLE "
                         "HOLDER with its wires; the 4886 at zero during the move)")
        self.assertEqual(app.NEWMARK_SPEED_DEG_S, 5.0)
        self.assertEqual(app.stfmr_angle_move_seconds((0.0, 90.0), 0.0), 18.0)
        self.assertEqual(app.stfmr_angle_move_seconds((0.0, 90.0), 30.0), 24.0, "from where the stage is")


class AnglePlanTests(MotorBase):
    def test_without_the_motor_zero_to_zero_runs_as_before(self):
        run = self.make(angles=("0", "0", "not read"), connect=False)
        plan = run.h._stfmr_lockin_angle_plan()
        self.assertEqual((plan["angles"], plan["used"], plan["text"]), ([], False, "Angle: motor not used"))
        self.assertEqual((plan["start"], plan["stop"], plan["step"]), (0.0, 0.0, None))
        # An older page without the boxes or the motor: as before too.
        bare = harness(self.tmp.name)
        self.assertEqual(bare._stfmr_lockin_angle_plan()["used"], False)

    def test_angles_without_the_motor_are_refused(self):
        run = self.make(angles=("0", "90", "10"), connect=False)
        with self.assertRaisesRegex(RuntimeError, "connect it on the Motor row of the STFMR instrument "
                                                  "connections, or set Angle start and Angle stop to 0"):
            run.h._stfmr_lockin_angle_plan()
        run.h.newmark_stage_ownership.claim("SHV")
        with self.assertRaisesRegex(RuntimeError, "the motor is connected on the SHV page: disconnect it there "
                                                  "and connect it on the Motor row here"):
            run.h._stfmr_lockin_angle_plan()
        run.h.stfmr_lockin_angle_start_var.set("abc")
        with self.assertRaisesRegex(ValueError, "must be finite numbers"):
            run.h._stfmr_lockin_angle_plan()

    def test_with_the_motor_connected_the_list_is_used_even_zero_to_zero(self):
        run = self.make(angles=("0", "0", "10"))
        plan = run.h._stfmr_lockin_angle_plan()
        self.assertEqual((plan["angles"], plan["used"], plan["step"]), ([0.0], True, 10.0))
        run.h.stfmr_lockin_angle_stop_var.set("90")
        self.assertEqual(len(run.h._stfmr_lockin_angle_plan()["angles"]), 10)
        run.h.stfmr_lockin_angle_stop_var.set("400")
        with self.assertRaisesRegex(ValueError, "within ±360°"):
            run.h._stfmr_lockin_angle_plan()
        self.assertEqual(run.sim.received, ["ID", "VER"], "only flags are read: nothing sent")

    def test_an_unconfirmed_stop_is_refused(self):
        run = self.make()
        run.motor.motion_may_run = True
        # Stop stage first, as RT-E's advice: a reconnect would clear the flag
        # without ever confirming the stop (review, 2026-10-17).
        with self.assertRaisesRegex(RuntimeError, r"^The Newmark NSC-A1's last stop was not confirmed, so it may "
                                                  r"still be turning: press Stop stage first \(check the stage\)$"):
            run.h._stfmr_lockin_angle_plan()
        run.h.start_stfmr_lockin_scan()
        self.error.assert_called_once()
        self.assertEqual(self.error.call_args.args[0], "STFMR scan")
        self.confirm.assert_not_called()
        self.assertEqual(run.h.log, [])
        self.assertEqual(run.sim.received, ["ID", "VER"])


# ---- the sweep with the motor ------------------------------------------------------
class AngleSweepTests(MotorBase):
    def test_the_angle_is_the_outermost_loop(self):
        run = self.sweep(angles=("0", "90", "90"), mods=("307", "311"), powers=("10", "12", "2"), stop="6")
        self.error.assert_not_called()
        rows = read_csv(run.recorder.csv_path)
        order = []
        for row in rows:
            key = (float(row["stage_angle_deg"]), float(row["modulation_frequency_hz"]),
                   float(row["microwave_power_dbm"]), float(row["microwave_frequency_ghz"]))
            if not order or order[-1] != key:
                order.append(key)
        self.assertEqual(order, list(itertools.product((0.0, 90.0), (307.0, 311.0), (10.0, 12.0), (5.0, 6.0))))
        # The row index and the progress count the angles: 16 passes of 4
        # currents, the first two of each discarded.
        self.assertEqual([int(row["index"]) for row in rows], [p * 4 + k for p in range(16) for k in (3, 4)])
        self.assertEqual(run.h.progress[-1], (64, 64))
        self.assertEqual(len(rows), 32)
        # stage_angle_deg is the CSV's last column (older columns keep their
        # places) and holds the angle read back after the move.
        header = run.recorder.csv_path.read_text(encoding="utf-8-sig").splitlines()[0].split(",")
        # The magnet supply's two columns after it since 2026-10-21 (user).
        self.assertEqual(header[-3:], ["stage_angle_deg", "magnet_supply", "magnet_measured_current_a"])
        self.assertEqual(app.STFMRScanRecorder.FIELDNAMES[-3], "stage_angle_deg")
        self.assertEqual(list(run.h.stfmr_lockin_rows[0])[-3], "stage_angle_deg")
        history = events(run.recorder)
        settings = history[0]["settings"]
        self.assertEqual((settings["angles_deg"], settings["angle_start_deg"], settings["angle_stop_deg"],
                          settings["angle_step_deg"], settings["motor_used"]), ([0.0, 90.0], 0.0, 90.0, 90.0, True))
        self.assertEqual(settings["motor_identity"], "Ace-Series-SDE V231BL")
        self.assertEqual((settings["motor_steps_per_degree"], settings["motor_speed_deg_s"]), (10000, 5.0))
        self.assertEqual(settings["motor_angle_at_start_deg"], 0.0, "read by the stage check")
        moves = [e for e in history if e["event"] == "stage_move"]
        self.assertEqual([(e["target_deg"], e["angle_deg"], e["px"]) for e in moves],
                         [(0.0, 0.0, 0), (90.0, 90.0, 900000)])
        self.assertEqual(history[-1]["outcome"], "completed")
        self.assertEqual(history[-1]["stage"], "stage at +90.000°")
        self.assertEqual(run.h.stfmr_lockin_status_var.get(),
                         "Completed; 4886 and RF zeroed; 32 points saved; 2 frequencies, 2 modulation frequencies, "
                         "2 powers, 2 angles")
        self.assertEqual(run.h.stfmr_lockin_motor_status_var.get(), "+90.000°")
        self.assertIn("X900000", run.sim.received)
        # The confirmation names the angles, the stage and the move time.
        text = self.confirm.call_args.args[1]
        self.assertIn("Angle: 0 → 90°, step 90°, 2 angles (the motor turns the SAMPLE HOLDER with its wires; "
                      "angle outermost; the 4886 at zero during every move); the stage is at +0.000° now\n", text)
        self.assertIn(" + 18 s of stage moves at 5°/s", text)
        self.assertTrue(text.endswith("Start now, turn the stage and enable the 4886 and SMB100A outputs?"))

    def test_the_first_move_comes_before_anything_is_switched_on(self):
        run = self.sweep(angles=("30", "60", "30"))
        self.error.assert_not_called()
        # The 4886 zeroed first, as before every move (review, 2026-10-17).
        self.assertEqual(run.h.log[0], ("kepco", "zero"))
        self.assertEqual(run.h.log[1], ("motor", "move", 30.0))
        self.assertEqual(run.h.log[2][:2], ("smb", "configure"))
        first_output = run.h.log.index(next(e for e in run.h.log if e[:2] == ("kepco", "output")))
        self.assertGreater(first_output, 1)
        self.assertIn("X300000", run.sim.received)

    def test_the_angle_change_sequence(self):
        """4886 zero, the move, the modulation and the power set back, the
        first carrier, the SR830's reference wait, then the 4886 on at Start
        A; the RF is never switched off in between."""
        run = self.sweep(angles=("0", "90", "90"), mods=("307", "311"), powers=("10", "12", "2"), stop="6")
        self.error.assert_not_called()
        window = self.window(run.h.log, ("motor", "move", 90.0))
        self.assertEqual([entry[:2] for entry in window],
                         [("kepco", "zero"), ("motor", "move"), ("smb", "modulation"), ("smb", "power"),
                          ("smb", "frequency"), ("830", "wait_for_reference"), ("kepco", "output")])
        self.assertEqual(window[2][2], 307.0, "the first modulation frequency")
        self.assertEqual(window[3][2], 10.0, "the first power")
        self.assertEqual(window[4][2], 5.0, "the first carrier")
        self.assertEqual(window[6][2:4], ("CURR", -1.0), "Start A")
        self.assertEqual([e for e in run.h.log if e[:2] == ("smb", "rf_off")], [("smb", "rf_off")],
                         "the RF goes off once, at the end")
        # previous_frequency_hz: at the angle change the SR830 must be seen
        # to leave the LAST modulation frequency (311), not None.
        waits = [(call[1], call[3]) for call in run.h.stfmr_lockin_controllers["830"].calls
                 if call[0] == "wait_for_reference"]
        self.assertEqual(waits, [(307.0, None), (311.0, 307.0), (307.0, 311.0), (311.0, 307.0)])

    def test_one_modulation_and_one_power_are_left_alone_at_an_angle_change(self):
        run = self.sweep(angles=("0", "-45", "45"))
        self.error.assert_not_called()
        window = self.window(run.h.log, ("motor", "move", -45.0))
        self.assertEqual([entry[:2] for entry in window],
                         [("kepco", "zero"), ("motor", "move"), ("smb", "frequency"), ("kepco", "output")])
        rows = read_csv(run.recorder.csv_path)
        self.assertEqual([float(r["stage_angle_deg"]) for r in rows], [0.0, 0.0, -45.0, -45.0])
        statuses = run.h.stfmr_lockin_status_var.history
        self.assertIn("0° done; zeroing; stage to -45° at 5 GHz", statuses)

    def test_one_angle_with_the_motor_moves_once_and_records_it(self):
        run = self.sweep(angles=("0", "0", "10"))
        self.error.assert_not_called()
        self.assertEqual([e for e in run.h.log if e[0] == "motor"], [("motor", "move", 0.0)])
        rows = read_csv(run.recorder.csv_path)
        self.assertEqual({r["stage_angle_deg"] for r in rows}, {"0.0"})
        self.assertIn("Angle: 0° (one angle: the stage turns to 0° first, the motor turning the SAMPLE HOLDER "
                      "with its wires; the 4886 at zero during the move)", self.confirm.call_args.args[1])
        # One angle reads as before in the status lines and the legend.
        self.assertFalse([s for s in run.h.stfmr_lockin_status_var.history if "°" in s and "GHz/" in s])

    def test_a_stop_during_a_move_sends_stop_and_ends_the_sweep_stopped(self):
        run = self.make(angles=("0", "90", "90"))
        h = run.h
        h.move_hooks[90.0] = lambda: run.clock.hooks.append((run.clock.t + 3.0, h.stop_stfmr_lockin_scan))
        h.start_stfmr_lockin_scan()
        h.root.drain()
        recorder = h.last_recorder
        self.error.assert_not_called()
        self.assertFalse(h.stfmr_lockin_running)
        received = run.sim.received
        self.assertIn("STOP", received[received.index("X900000"):], "STOP sent from the worker")
        self.assertNotIn("ABORT", received)
        self.assertFalse(run.motor.motion_may_run, "the stop was confirmed")
        self.assertTrue(h.stfmr_lockin_stage_where.startswith("stage stopped at +"), h.stfmr_lockin_stage_where)
        where = h.stfmr_lockin_stage_where.split(" at ", 1)[1]
        self.assertTrue(0 < float(where.rstrip("°")) < 90, where)
        # The outputs first, then the stage, as SHV's (user, 2026-10-18).
        self.assertEqual(h.stfmr_lockin_status_var.get(), f"Stopped; 4886 and RF zeroed; stage at {where}; 2 saved")
        self.assertLessEqual(width_in_zeros(h.stfmr_lockin_status_var.get()), LABEL)
        self.assertEqual(h.stfmr_lockin_motor_status_var.get(), where)
        # Nothing was switched on after the move; the end zeroes and switches off.
        after = h.log[h.log.index(("motor", "move", 90.0)) + 1:]
        self.assertEqual([e[:2] for e in after], [("kepco", "zero"), ("smb", "rf_off")])
        history = events(recorder)
        self.assertEqual((history[-1]["outcome"], history[-1]["stage"]), ("stopped", f"stage stopped at {where}"))
        self.assertEqual(len(read_csv(recorder.csv_path)), 2, "the first angle's points are kept")

    def test_a_refusal_mid_sweep_ends_it_without_a_question(self):
        """A latched limit error that appears during the sweep: no Yes/No
        with the RF on; the sweep ends through the output-off path."""
        run = self.make(angles=("0", "90", "90"))
        h = run.h
        h.move_hooks[90.0] = lambda: setattr(run.sim, "latched", 1 << 6)
        h.start_stfmr_lockin_scan()
        h.root.drain()
        recorder = h.last_recorder
        self.confirm.assert_called_once()                      # the sweep's own confirmation only
        self.assertEqual(self.confirm.call_args.args[0], "STFMR scan")
        self.assertNotIn("CLR", run.sim.received)
        self.assertNotIn("X900000", run.sim.received)
        self.error.assert_called_once()
        message = self.error.call_args.args[1]
        self.assertIn("a limit error is latched", message)
        self.assertIn("Start a sweep with the motor on the STFMR page", message)
        self.assertTrue(message.endswith("Stage stopped at +0.000°"), message)
        after = h.log[h.log.index(("motor", "move", 90.0)) + 1:]
        self.assertEqual([e[:2] for e in after], [("kepco", "zero"), ("smb", "rf_off")])
        self.assertEqual(events(recorder)[-1]["outcome"], "error")
        self.assertFalse(h.stfmr_lockin_running)

    def test_a_failed_move_stops_the_stage_and_says_where(self):
        run = self.make(angles=("0", "90", "90"), sim=rte_stage.EndsShort())
        run.h.start_stfmr_lockin_scan()
        run.h.root.drain()
        self.error.assert_called_once()
        message = self.error.call_args.args[1]
        self.assertIn("not at the target", message)
        self.assertIn("Stage stopped at +89.999°", message)
        self.assertIn("STFMR sweep, after the stage failure: stage stopped at +89.999°", run.h.logged)

    def test_series_keys_legend_and_live_rows_name_the_angle(self):
        run = self.sweep(angles=("0", "90", "90"))
        rows = run.h.stfmr_lockin_rows
        series = App._stfmr_lockin_field_component_series(rows, "SR830", "8", "x_v", group_frequency=True)
        self.assertEqual(sorted(series), ["8_ang_0_freq_5_loop_1", "8_ang_90_freq_5_loop_1"])
        labels, colors = App._stfmr_lockin_frequency_series_styles(series, "x_v")
        self.assertEqual(labels, {"8_ang_0_freq_5_loop_1": "0°", "8_ang_90_freq_5_loop_1": "90°"})
        self.assertNotEqual(colors["8_ang_0_freq_5_loop_1"], colors["8_ang_90_freq_5_loop_1"])
        current = App._stfmr_lockin_current_field_series(rows, "SR830", "8")
        self.assertEqual(sorted(current), ["8_ang_0_freq_5_loop_1", "8_ang_90_freq_5_loop_1"])
        # Before the modulation and power parts, and named with them.
        rows2 = [dict(r, modulation_frequency_hz=m, microwave_power_dbm=p) for r in rows for m, p in ((307, 10), (311, 12))]
        keys = App._stfmr_lockin_field_component_series(rows2, "SR830", "8", "x_v", group_frequency=True)
        self.assertIn("8_ang_90_mod_311_pow_12_freq_5_loop_1", keys)
        labels, _ = App._stfmr_lockin_frequency_series_styles(keys, "x_v")
        self.assertEqual(labels["8_ang_90_mod_311_pow_12_freq_5_loop_1"], "311 Hz, 12 dBm, 90°")
        # The live plots get each point's angle.
        live = run.h._append_stfmr_lockin_live_sample.call_args_list
        self.assertEqual([c.kwargs["stage_angle_deg"] for c in live], [0.0, 0.0, 90.0, 90.0])
        # Without the motor, as before: no angle part.
        plain = [dict(r, stage_angle_deg="") for r in rows]
        self.assertEqual(sorted(App._stfmr_lockin_field_component_series(plain, "SR830", "8", "x_v",
                                                                         group_frequency=True)), ["8_freq_5_loop_1"])

    def test_status_lines_name_the_angle_and_fit_the_label(self):
        run = self.sweep(start_only=True, angles=("-180.5", "179.5", "360"), mods=("1009", "1013"),
                         powers=("-12.5", "-10.5", "2"), stop="6")
        run.h.stfmr_lockin_started -= 40000.0           # a long time left
        run.h.root.drain()
        self.error.assert_not_called()
        statuses = run.h.stfmr_lockin_status_var.history
        scanning = [s for s in statuses if re.search(r"; \d+/\d+; ", s)]
        self.assertTrue(scanning)
        self.assertTrue([s for s in scanning if s.startswith("6 GHz/1013 Hz/-10.5 dBm/179.5°; ")], scanning[-3:])
        # The pre-read lines and the carrier, angle and power changes; the
        # long "start pre-read done; the first two samples ..." line was
        # always cut at the label's end (it was so before the angles).
        reviewed = scanning + [s for s in statuses if ("pre-read" in s and "pre-read done" not in s)
                               or " done; zeroing" in s]
        self.assertIn("-180.5° done; zeroing; stage to 179.5° at 5 GHz", statuses)
        for status in reviewed:
            with self.subTest(status=status):
                self.assertLessEqual(width_in_zeros(status), LABEL)
        # The widest lines the pass label can make.
        h = run.h
        h.stfmr_lockin_expected_hz, h.stfmr_lockin_power_dbm = 1013.0, -12.5
        h.stfmr_lockin_angles_deg, h.stfmr_lockin_angle_index = [-359.5, 359.5], 0
        label = h._stfmr_lockin_pass_label(10.25)
        self.assertEqual(label, "10.25 GHz/1013 Hz/-12.5 dBm/-359.5°")
        for status in (f"{label}; 12345/48000; ~10.5 h", f"{label}: I=-2.925 A; pre-read",
                       "Stopped; 4886 and RF zeroed; stage at -359.999°; 12345 saved",
                       # A failed zero: the outputs and the stage still fit.
                       "Stopped; 4886 zero FAILED; RF zeroed; stage at -359.999°",
                       "Stopping; zeroing the 4886, SMB100A RF off next…",
                       "Ending; zeroing the 4886, SMB100A RF off…",
                       "Turning the stage to -359.5°; then the SMB100A and lock-in",
                       "Sweep not started: " + app.short_status_error(RuntimeError("x" * 80), 33)):
            with self.subTest(status=status):
                self.assertLessEqual(width_in_zeros(status), LABEL)
        h.stfmr_lockin_modulation_frequencies_hz, h.stfmr_lockin_powers_dbm = [1013.0], [-12.5]
        self.assertEqual(h._stfmr_lockin_pass_label(5.0), "5 GHz/-359.5°", "'/30°' as '/12 dBm'")


class WithoutTheMotorTests(MotorBase):
    def test_zero_to_zero_without_the_motor_is_the_sweep_of_before(self):
        before = self.make(connect=False)
        h = before.h
        # As an older page: no angle boxes and no motor at all.
        for name in ("stfmr_lockin_motor", "stfmr_lockin_angle_start_var", "stfmr_lockin_angle_stop_var",
                     "stfmr_lockin_angle_step_var", "newmark_stage_ownership"):
            delattr(h, name)
        h.start_stfmr_lockin_scan()
        h.root.drain()
        old_recorder = h.last_recorder
        self.confirm.reset_mock()
        run = self.sweep(angles=("0", "0", "10"), connect=False)
        self.error.assert_not_called()
        self.assertEqual(run.h.log, h.log, "the same instrument calls in the same order")
        self.assertEqual(run.h.stfmr_lockin_status_var.history, h.stfmr_lockin_status_var.history)
        old_rows, rows = read_csv(old_recorder.csv_path), read_csv(run.recorder.csv_path)
        skip = {"elapsed_s", "timestamp_iso"}
        self.assertEqual([{k: v for k, v in r.items() if k not in skip} for r in rows],
                         [{k: v for k, v in r.items() if k not in skip} for r in old_rows])
        self.assertEqual({r["stage_angle_deg"] for r in rows}, {""}, "the angle column blank")
        self.assertEqual(run.sim.received, [], "the stage is never asked anything")
        run.motor.check_ready.assert_not_called()
        settings = events(run.recorder)[0]["settings"]
        self.assertEqual((settings["angles_deg"], settings["motor_used"], settings["motor_identity"]), ([], False, ""))
        self.assertNotIn("stage", events(run.recorder)[-1])
        text = self.confirm.call_args.args[1]
        self.assertIn("\nAngle: motor not used\n", text)
        self.assertTrue(text.endswith("Start now and enable the 4886 and SMB100A outputs?"))
        self.assertNotIn("stage moves", text)


# ---- Start's stage check and the CLR / EO=1 question --------------------------------
LATCHED_QUESTION = ("A limit error is latched on the stage (MST 64: not moving, -limit ERROR (latched)). Clear it now "
                    "(CLR)?")


class StartCheckTests(MotorBase):
    def test_the_check_runs_before_the_folder_the_files_and_any_output(self):
        executor = Deferred()
        run = self.make(executor=executor)
        h = run.h
        h.select_csv_save_directory = mock.Mock(return_value=Path(self.tmp.name))
        h.start_stfmr_lockin_scan()
        self.assertEqual(len(executor.jobs), 1, "the check, queued on the STFMR worker")
        self.assertTrue(h.stfmr_lockin_motor_check_pending)
        h.select_csv_save_directory.assert_not_called()
        self.confirm.assert_not_called()
        self.assertIsNone(h.stfmr_lockin_recorder)
        self.assertEqual(h.log, [])
        self.assertEqual(list(Path(self.tmp.name).iterdir()), [], "no files")
        self.assertEqual(h.stfmr_lockin_status_var.get(), "Checking the motor (MST, EO) before the sweep…")
        h.start_stfmr_lockin_scan()                    # Start is locked while it is pending
        self.assertEqual(len(executor.jobs), 1)
        executor.run()
        run.motor.check_ready.assert_called_once_with()
        self.assertEqual(run.sim.received[2:], ["MST", "EO", "MST", "PX"], "the check and where the stage is")
        while h.root.pending or executor.jobs:
            executor.run()
            if h.root.pending:
                h.root.step()
        h.select_csv_save_directory.assert_called_once()
        self.confirm.assert_called_once()
        self.assertFalse(h.stfmr_lockin_motor_check_pending)
        self.assertFalse(h.stfmr_lockin_running, "the sweep ran to its end")
        self.assertEqual(len(read_csv(next(Path(self.tmp.name).glob("*/*.csv")))), 4)

    def test_a_latched_limit_error_is_asked_and_cleared_on_yes(self):
        sim = Sim()
        sim.latched = 1 << 6
        run = self.sweep(sim=sim)
        self.error.assert_not_called()
        titles = [call.args[0] for call in self.confirm.call_args_list]
        self.assertEqual(titles, ["STFMR stage limit error", "STFMR scan"])
        self.assertEqual(self.confirm.call_args_list[0].args[1], LATCHED_QUESTION)
        self.assertEqual(self.confirm.call_args_list[0].args[1],
                         app.newmark_fix_prompt(app.NewmarkLimitErrorLatched(64), "SHV")[3], "SHV's wording")
        received = run.sim.received
        self.assertEqual(received.count("CLR"), 1)
        self.assertLess(received.index("CLR"), received.index("X900000"))
        self.assertIn("STFMR Newmark NSC-A1: limit error cleared (CLR; MST 64 -> 0, read back)", run.motor_log)
        self.assertIn("STFMR Newmark NSC-A1: Yes: CLR queued on the STFMR worker; it is read back before anything "
                      "moves", run.h.logged)
        self.assertEqual(len(read_csv(run.recorder.csv_path)), 4)

    def test_no_sends_nothing_and_starts_nothing(self):
        sim = Sim()
        sim.latched = 1 << 7
        self.confirm.return_value = False
        run = self.make(sim=sim)
        run.h.select_csv_save_directory = mock.Mock()
        run.h.start_stfmr_lockin_scan()
        run.h.root.drain()
        self.confirm.assert_called_once()
        self.assertNotIn("CLR", run.sim.received)
        self.assertEqual(run.h.stfmr_lockin_status_var.get(), "Sweep not started: CLR not sent (No)")
        run.h.select_csv_save_directory.assert_not_called()
        self.assertEqual(list(Path(self.tmp.name).iterdir()), [])
        self.assertEqual(run.h.log, [])
        self.assertFalse(run.h.stfmr_lockin_motor_check_pending)
        self.assertIn("STFMR Newmark NSC-A1: No: CLR not sent; nothing moved", run.h.logged)

    def test_the_driver_off_is_asked_and_switched_on_on_yes(self):
        sim = Sim()
        sim.eo = 0
        run = self.sweep(sim=sim)
        self.error.assert_not_called()
        self.assertEqual(self.confirm.call_args_list[0].args[0], "STFMR stage motor driver")
        self.assertIn("Switch it on now (EO=1) and go on?", self.confirm.call_args_list[0].args[1])
        self.assertIn("answer No and check it first", self.confirm.call_args_list[0].args[1])
        self.assertEqual(run.sim.received.count("EO=1"), 1)
        self.assertIn("STFMR Newmark NSC-A1: motor driver switched on (EO=1; EO 0 -> 1, read back)", run.motor_log)

    def test_both_problems_are_asked_in_turn_each_once(self):
        sim = Sim()
        sim.latched, sim.eo = 1 << 6, 0
        run = self.sweep(sim=sim)
        self.assertEqual([c.args[0] for c in self.confirm.call_args_list],
                         ["STFMR stage limit error", "STFMR stage motor driver", "STFMR scan"])
        self.assertEqual((run.sim.received.count("CLR"), run.sim.received.count("EO=1")), (1, 1))

    def test_a_limit_input_is_a_plain_refusal(self):
        sim = Sim()
        sim.inputs = 1 << 5
        run = self.sweep(sim=sim)
        self.confirm.assert_not_called()
        self.error.assert_called_once()
        self.assertEqual(self.error.call_args.args[0], "STFMR scan not started")
        self.assertIn("polarity problem", self.error.call_args.args[1])
        self.assertTrue(run.h.stfmr_lockin_status_var.get().startswith("Sweep not started: a limit input is on"))
        self.assertNotIn("CLR", run.sim.received)

    def test_no_fix_after_a_stop_or_a_close_behind_the_yes(self):
        for press in ("stop", "kepco zero", "close"):
            with self.subTest(press=press):
                self.confirm.reset_mock()
                self.error.reset_mock()
                sim = Sim()
                sim.latched = 1 << 6
                executor = Deferred()
                run = self.make(sim=sim, executor=executor)
                h = run.h
                h.start_stfmr_lockin_scan()
                executor.run()                      # the check: refused
                h.root.drain()                      # the question: Yes, the fix queued behind a read
                self.assertEqual(len(executor.jobs), 1)
                if press == "stop":
                    h.stop_stfmr_lockin_scan()
                    self.assertEqual(h.stfmr_lockin_status_var.get(), "Stop / zero pressed: the sweep will not start")
                elif press == "kepco zero":
                    h.stfmr_lockin_controllers["kepco"].lock = threading.Lock()
                    h.stfmr_lockin_controllers["kepco"].force_output_off = mock.Mock()
                    h._show_stfmr_lockin_kepco_state = mock.Mock()
                    h.zero_stfmr_lockin_kepco_output()
                else:
                    h.stfmr_lockin_closed = True
                executor.run()
                h.root.drain()
                self.assertNotIn("CLR", run.sim.received, "nothing sent after the Yes")
                self.confirm.assert_called_once()
                self.assertFalse(h.stfmr_lockin_running)
                self.assertFalse(h.stfmr_lockin_motor_check_pending)
                self.assertIsNone(h.stfmr_lockin_recorder)
                if press == "close":
                    self.assertIn("STFMR Newmark NSC-A1: CLR not sent: STFMR is closing after the Yes", h.logged)
                else:
                    self.assertIn("STFMR Newmark NSC-A1: CLR not sent: Stop / zero was pressed after the Yes",
                                  h.logged)
                    self.assertEqual(h.stfmr_lockin_status_var.get(), "Sweep not started: Stop / zero was pressed")

    def test_the_question_opens_outside_the_ui_drain(self):
        sim = Sim()
        sim.latched = 1 << 6
        run = self.make(sim=sim)
        h = run.h
        h.ui, h.in_drain = [], False
        h.post_ui = h.ui.append
        drained = []

        def answer(title, _question, **_options):
            drained.append((title, h.in_drain, h.stfmr_lockin_motor_check_pending))
            return False
        self.confirm.side_effect = answer
        h.start_stfmr_lockin_scan()
        for _ in range(20):
            h.in_drain = True
            while h.ui:
                h.ui.pop(0)()
            h.in_drain = False
            if not h.root.pending:
                break
            h.root.step()
        self.assertEqual(drained, [("STFMR stage limit error", False, True)],
                         "asked from the Tk event loop, with Start and the Motor row locked")

    def test_a_fix_not_read_back_or_back_right_after_is_an_error(self):
        # CLR whose read-back still shows the error: an error, nothing moves.
        class StaysLatched(Sim):
            def reply(self, command):
                text = super().reply(command)
                if command == "CLR":
                    self.latched = 1 << 6
                return text

        # CLR read back clean, then latched again at the check after it:
        # an error, never a second question.
        class Relatches(Sim):
            clean_reads = None

            def reply(self, command):
                if command == "MST" and self.clean_reads is not None:
                    if self.clean_reads == 0:
                        self.latched, self.clean_reads = 1 << 6, None
                    else:
                        self.clean_reads -= 1
                text = super().reply(command)
                if command == "CLR":
                    self.clean_reads = 1
                return text

        for sim, title, message in (
                (StaysLatched(), "STFMR scan not started", "still shows a limit error latched"),
                (Relatches(), "STFMR stage", "The limit error is latched again (MST 64: not moving, -limit ERROR "
                                             "(latched)) right after CLR was read back, so nothing moved: check the "
                                             "stage")):
            with self.subTest(sim=type(sim).__name__):
                self.confirm.reset_mock()
                self.error.reset_mock()
                sim.latched = 1 << 6
                run = self.make(sim=sim)
                run.h.start_stfmr_lockin_scan()
                run.h.root.drain()
                self.confirm.assert_called_once()
                self.error.assert_called_once()
                self.assertEqual(self.error.call_args.args[0], title)
                self.assertIn(message, self.error.call_args.args[1])
                self.assertEqual(run.sim.received.count("CLR"), 1, "never repeated")
                self.assertIsNone(run.h.stfmr_lockin_recorder)
                self.assertEqual(run.h.log, [])


# ---- the Motor row, the device owner and Set motor 0° --------------------------------
class OwnershipTests(MotorBase):
    def test_one_page_at_a_time(self):
        owner = app.NewmarkStageOwnership()
        self.assertIsNone(owner.owner)
        owner.claim("SHV")
        owner.claim("SHV")
        with self.assertRaisesRegex(RuntimeError, r"^The motor is connected on the SHV page: disconnect it there "
                                                  r"first\.$"):
            owner.claim("STFMR")
        owner.release("STFMR")
        self.assertEqual(owner.owner, "SHV", "only the owner releases")
        owner.release("SHV")
        owner.claim("STFMR")
        with self.assertRaisesRegex(RuntimeError, "connected on the STFMR page"):
            owner.claim("SHV")

    def test_the_main_app_and_the_rt_e_page_share_one(self):
        main = object.__new__(App)
        main.newmark_stage_ownership = shared = app.NewmarkStageOwnership()
        self.assertIs(main._newmark_stage_ownership(), shared)
        page = app.RTEPanel.__new__(app.RTEPanel)
        page.parent_app = main
        self.assertIs(page._newmark_stage_ownership(), shared)
        stand_in = object.__new__(App)
        made = stand_in._newmark_stage_ownership()
        self.assertIsInstance(made, app.NewmarkStageOwnership)
        self.assertIs(stand_in._newmark_stage_ownership(), made)
        source = Path(app.__file__).read_text(encoding="utf-8")
        init = source[source.index("        self.newmark_stage_ownership = NewmarkStageOwnership()"):]
        self.assertLess(init.index("NewmarkStageOwnership()"), init.index("self.build_ui()"),
                        "made before build_ui builds the SHV page")

    def test_connect_is_refused_while_the_rt_e_page_has_it(self):
        run = self.make(connect=False)
        run.h.newmark_stage_ownership.claim("SHV")
        run.h.connect_stfmr_lockin_motor()
        self.error.assert_called_once_with("STFMR motor", "The motor is connected on the SHV page: disconnect "
                                           "it there first.", parent=run.h.root)
        self.assertEqual(run.dll.calls, [], "nothing queued or sent")
        self.assertFalse(run.motor.connected)
        self.assertEqual(run.h.newmark_stage_ownership.owner, "SHV")

    def test_connect_claims_disconnect_and_a_failed_connect_release(self):
        run = self.make(connect=False)
        h = run.h
        h.connect_stfmr_lockin_motor()
        h.root.drain()
        self.error.assert_not_called()
        self.assertTrue(run.motor.connected)
        self.assertEqual(h.newmark_stage_ownership.owner, "STFMR")
        self.assertEqual(h.stfmr_lockin_motor_status_var.get(), "+0.000°")
        self.assertIn("STFMR Newmark NSC-A1 connected (Ace-Series-SDE, V231BL): " + rte_stage.SCALE, run.motor_log)
        h.disconnect_stfmr_lockin_motor()
        h.root.drain()
        self.assertFalse(run.motor.connected)
        self.assertIsNone(h.newmark_stage_ownership.owner)
        self.assertEqual(h.stfmr_lockin_motor_status_var.get(), "Not connected")
        self.assertEqual(run.motor_log[-1], "STFMR Newmark NSC-A1 disconnected")
        failed = self.make(connect=False, sim=Sim(serials=()))
        failed.h.connect_stfmr_lockin_motor()
        failed.h.root.drain()
        self.assertIn("No Performax USB device found", self.error.call_args.args[1])
        self.assertIsNone(failed.h.newmark_stage_ownership.owner, "released after a failed connect")
        self.assertEqual(failed.h.stfmr_lockin_motor_status_var.get(), "Not connected")

    def test_a_disconnect_without_a_confirmed_stop_releases_and_warns(self):
        run = self.make(sim=rte_stage.Runaway())
        run.sim._start(3000000, script._now())         # turning, and STOP and ABORT do nothing
        run.h.disconnect_stfmr_lockin_motor()
        run.h.root.drain()
        self.assertFalse(run.motor.connected)
        self.assertIsNone(run.h.newmark_stage_ownership.owner)
        self.assertEqual(run.h.stfmr_lockin_motor_status_var.get(), "CHECK STAGE")
        self.assertIn("CHECK THE STAGE", self.error.call_args.args[1])

    def test_the_row_is_locked_while_a_sweep_runs_or_starts(self):
        run = self.make(connect=False)
        h = run.h
        for flag in ("stfmr_lockin_running", "stfmr_lockin_starting", "stfmr_lockin_motor_check_pending",
                     "stfmr_lockin_motor_busy", "stfmr_lockin_closed"):
            with self.subTest(flag=flag):
                setattr(h, flag, True)
                h.connect_stfmr_lockin_motor()
                setattr(h, flag, False)
                self.assertEqual(run.dll.calls, [])
        self.assertIsNone(h.newmark_stage_ownership.owner)

    def test_the_log_prefix_follows_owner_scope(self):
        for scope in ("STFMR", "SHV"):
            with self.subTest(scope=scope):
                run = self.make(connect=False)
                log = []
                motor = app.NewmarkNSCA1Controller(log.append, owner_scope=scope,
                                                   loader=lambda run=run: (run.dll, "fake PerformaxCom.dll"))
                motor._now, motor._sleep = run.clock.now, run.clock.sleep
                motor.connect("auto")
                run.sim.latched = 1 << 6
                motor.clear_limit_error()
                motor.move_to_deg(10, threading.Event())
                motor.set_zero()
                motor.disconnect()
                self.assertTrue(log)
                self.assertEqual([line for line in log if not line.startswith(f"{scope} Newmark NSC-A1")], [])
                self.assertIn(f"{scope} Newmark NSC-A1 disconnected", log)
        self.assertEqual(app.NewmarkNSCA1Controller(print).owner_scope, "SHV", "the default is unchanged")


class SetZeroTests(MotorBase):
    def test_refused_without_the_motor_and_while_a_sweep_runs_or_starts(self):
        run = self.make(connect=False)
        h = run.h
        h.set_stfmr_lockin_motor_zero()
        self.error.assert_called_once_with("STFMR stage", "Connect the Newmark NSC-A1 first (the Motor row)",
                                           parent=h.root)
        self.confirm.assert_not_called()
        run = self.make()
        h = run.h
        for flag in ("stfmr_lockin_running", "stfmr_lockin_starting", "stfmr_lockin_motor_check_pending",
                     "stfmr_lockin_motor_busy"):
            with self.subTest(flag=flag):
                setattr(h, flag, True)
                h.set_stfmr_lockin_motor_zero()
                setattr(h, flag, False)
        self.confirm.assert_not_called()
        self.assertNotIn("PX=0", run.sim.received)

    def test_no_sends_nothing(self):
        run = self.make()
        self.confirm.return_value = False
        run.h.set_stfmr_lockin_motor_zero()
        run.h.root.drain()
        self.confirm.assert_called_once()
        self.assertEqual(self.confirm.call_args.args, ("STFMR stage zero", app.newmark_set_zero_question()))
        self.assertEqual(run.sim.received, ["ID", "VER"])

    def test_yes_sets_the_zero_on_the_worker_and_shows_the_angle(self):
        executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="stfmr-lockin")
        self.addCleanup(executor.shutdown, wait=True)
        run = self.make(connect=False, executor=executor)
        h = run.h
        h.ui = []
        h.post_ui = h.ui.append

        def settle():
            executor.submit(lambda: None).result(timeout=30)
            while h.ui:
                h.ui.pop(0)()
        h.connect_stfmr_lockin_motor()
        settle()
        executor.submit(run.motor.move_to_deg, 15, threading.Event()).result(timeout=30)
        h.set_stfmr_lockin_motor_zero()
        self.assertEqual(h.stfmr_lockin_motor_status_var.get(), "Setting 0°…")
        self.assertTrue(h.stfmr_lockin_motor_busy, "the row is locked meanwhile")
        settle()
        self.error.assert_not_called()
        self.assertIn("PX=0", run.sim.received)
        self.assertEqual(h.stfmr_lockin_motor_status_var.get(), "+0.000°")
        self.assertFalse(h.stfmr_lockin_motor_busy)
        self.assertEqual(h.stfmr_lockin_motor_known_deg, 0.0)
        self.assertIn("STFMR Newmark NSC-A1: zero set here (was +15.000°, PX 150000)", run.motor_log)
        h.disconnect_stfmr_lockin_motor()
        settle()
        worker = executor.submit(threading.get_ident).result(timeout=30)
        self.assertEqual({thread for _name, thread in run.dll.calls}, {worker}, "every DLL call on the worker")
        # RT-E asks the same question.
        source = Path(app.__file__).read_text(encoding="utf-8")
        self.assertIn("messagebox.askyesno('SHV stage zero', newmark_set_zero_question(), parent=self.root)",
                      source)

    def test_a_refusal_shows_an_error(self):
        run = self.make()
        run.sim._start(3000000, script._now())         # the stage turns (by hand, say)
        run.h.set_stfmr_lockin_motor_zero()
        run.h.root.drain()
        self.assertIn("the stage is moving", self.error.call_args.args[1])
        self.assertNotIn("PX=0", run.sim.received)


class HandMoveTests(MotorBase):
    """Move stage and Stop stage on the STFMR page (user, 2026-10-17: "also
    add Move stage button for the motor in ST-FMR"), as RT-E's."""

    def hand(self, target="15", **options):
        run = self.make(**options)
        h = run.h
        h.stfmr_lockin_stage_target_var.set(target)
        h.submitted = []
        executor = h.stfmr_lockin_executor
        original = executor.submit

        def submit(fn, *args):
            h.submitted.append(fn)
            return original(fn, *args)
        executor.submit = submit
        return run

    def test_the_target_limits(self):
        for target in ("360.5", "-361", "abc", "nan", "inf", ""):
            with self.subTest(target=target):
                self.error.reset_mock()
                run = self.hand(target)
                run.h.move_stfmr_lockin_stage()
                self.error.assert_called_once_with(
                    "STFMR stage", "The stage angle must be within ±360° of the stage zero (the sample wires)",
                    parent=run.h.root)
                self.assertEqual(run.h.submitted, [])
                self.assertEqual(run.sim.moves, [])
        for target, command in (("360", "X3600000"), ("-360", "X-3600000")):
            with self.subTest(target=target):
                run = self.hand(target)
                run.h.move_stfmr_lockin_stage()
                run.h.root.drain()
                self.assertEqual(run.sim.moves, [command])

    def test_refusals(self):
        run = self.hand(connect=False)
        run.h.move_stfmr_lockin_stage()
        self.assertEqual(self.error.call_args.args[1], "Connect the Newmark NSC-A1 first (the Motor row)")
        run = self.hand()
        run.h.stfmr_lockin_manual_safety_var.set(False)
        run.h.move_stfmr_lockin_stage()
        self.assertIn("safety confirmation", self.error.call_args.args[1])
        run.h.stfmr_lockin_manual_safety_var.set(True)
        run.motor.motion_may_run = True
        run.h.move_stfmr_lockin_stage()
        self.assertIn("last stop was not confirmed", self.error.call_args.args[1])
        run.motor.motion_may_run = False
        self.error.reset_mock()
        for flag in ("stfmr_lockin_running", "stfmr_lockin_starting", "stfmr_lockin_motor_check_pending",
                     "stfmr_lockin_motor_busy", "stfmr_lockin_stage_prompt_open", "stfmr_lockin_closed"):
            with self.subTest(flag=flag):
                setattr(run.h, flag, True)
                run.h.move_stfmr_lockin_stage()
                setattr(run.h, flag, False)
        self.error.assert_not_called()
        self.assertEqual(run.h.submitted, [])
        self.assertEqual(run.sim.moves, [])

    def test_the_move_runs_on_the_worker_with_its_progress_in_the_motor_row(self):
        executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="stfmr-lockin")
        self.addCleanup(executor.shutdown, wait=True)
        run = self.make(connect=False, executor=executor)
        h = run.h
        h.ui = []
        h.post_ui = h.ui.append
        statuses = []
        status = h.stfmr_lockin_motor_status_var
        original_set = status.set
        status.set = lambda value: (statuses.append(value), original_set(value))

        def settle():
            executor.submit(lambda: None).result(timeout=30)
            while h.ui:
                h.ui.pop(0)()
        h.connect_stfmr_lockin_motor()
        settle()
        h.stfmr_lockin_stage_target_var.set("12.5")
        h.move_stfmr_lockin_stage()
        self.assertTrue(h.stfmr_lockin_motor_busy and h.stfmr_lockin_stage_move_pending)
        settle()
        self.error.assert_not_called()
        self.assertEqual(run.sim.moves, ["X125000"])
        self.assertEqual(status.get(), "+12.500°", "the angle read back")
        progress = [s for s in statuses if re.fullmatch(r"\+\d+\.\d{3}°", s)]
        self.assertGreater(len(progress), 3, "a few times a second while it turns")
        self.assertFalse(h.stfmr_lockin_motor_busy or h.stfmr_lockin_stage_move_pending)
        worker = executor.submit(threading.get_ident).result(timeout=30)
        self.assertEqual({thread for _name, thread in run.dll.calls}, {worker})

    def test_stop_during_a_move_sets_the_event_and_queues_nothing(self):
        run = self.hand("90")
        h = run.h
        h.move_hooks[90.0] = lambda: run.clock.hooks.append((run.clock.t + 3.0, h.stop_stfmr_lockin_stage))
        h.move_stfmr_lockin_stage()
        h.root.drain()
        self.error.assert_not_called()
        self.assertEqual(len(h.submitted), 1, "only the move: no STOP job behind it")
        self.assertTrue(h.stfmr_lockin_stage_stop_event.is_set())
        received = run.sim.received
        self.assertIn("STOP", received[received.index("X900000"):], "STOP from the worker's move")
        self.assertFalse(run.motor.motion_may_run)
        angle = h.stfmr_lockin_motor_status_var.get()
        self.assertTrue(0 < float(angle.rstrip("°")) < 90, angle)
        self.assertTrue([line for line in h.logged if line.startswith("STFMR Newmark NSC-A1: Move stage stopped: "
                                                                     "stopped at +")])

    def test_stop_before_a_queued_move_starts_sends_nothing(self):
        executor = Deferred()
        run = self.hand("30", executor=executor)
        h = run.h
        h.stfmr_lockin_motor_known_deg = 0.0        # as Connect's first read left it
        h.move_stfmr_lockin_stage()
        h.stop_stfmr_lockin_stage()
        self.assertEqual(len(executor.jobs), 1, "never a STOP queued behind the move")
        self.assertEqual(h.stfmr_lockin_motor_status_var.get(), "Stopping…")
        executor.run()
        h.root.drain()
        self.assertEqual(run.sim.moves, [], "the move saw the Stop before it was sent")
        self.assertEqual(h.stfmr_lockin_motor_status_var.get(), "+0.000°")
        self.assertIn("STFMR Newmark NSC-A1: Move stage stopped: not moved", h.logged)

    def test_stop_with_nothing_moving_queues_stop(self):
        run = self.hand()
        h = run.h
        h.stop_stfmr_lockin_stage()
        h.root.drain()
        self.assertEqual([fn.__name__ for fn in h.submitted], ["stop_job"])
        self.assertIn("STOP", run.sim.received)
        self.assertEqual(h.stfmr_lockin_motor_status_var.get(), "+0.000°")
        self.assertIn("STFMR Newmark NSC-A1: Stop stage: stopped at +0.000°", h.logged)
        self.error.assert_not_called()
        # Not connected here: only the event.
        idle = self.hand(connect=False)
        idle.h.stop_stfmr_lockin_stage()
        self.assertEqual(idle.h.submitted, [])
        self.assertTrue(idle.h.stfmr_lockin_stage_stop_event.is_set())

    def test_stop_stage_during_a_sweep_stops_the_move_and_the_sweep(self):
        run = self.make(angles=("0", "90", "90"))
        h = run.h
        h.move_hooks[90.0] = lambda: run.clock.hooks.append((run.clock.t + 3.0, h.stop_stfmr_lockin_stage))
        h.start_stfmr_lockin_scan()
        h.root.drain()
        self.error.assert_not_called()
        self.assertFalse(h.stfmr_lockin_running)
        self.assertIn("STOP", run.sim.received[run.sim.received.index("X900000"):])
        self.assertTrue(h.stfmr_lockin_status_var.get().startswith("Stopped; 4886 and RF zeroed; stage at +"))
        self.assertTrue(h.stfmr_lockin_status_var.get().endswith("; 2 saved"))
        after = h.log[h.log.index(("motor", "move", 90.0)) + 1:]
        self.assertEqual([e[:2] for e in after], [("kepco", "zero"), ("smb", "rf_off")])
        self.assertEqual(events(h.last_recorder)[-1]["outcome"], "stopped")

    def test_a_latched_limit_error_is_asked_and_the_move_goes_on_after_yes(self):
        sim = Sim()
        sim.latched = 1 << 6
        run = self.hand("20", sim=sim)
        h = run.h
        h.move_stfmr_lockin_stage()
        h.root.drain()
        self.error.assert_not_called()
        self.assertEqual(self.confirm.call_args.args, ("STFMR stage limit error", LATCHED_QUESTION))
        self.assertEqual(run.sim.received.count("CLR"), 1)
        self.assertLess(run.sim.received.index("CLR"), run.sim.received.index("X200000"))
        self.assertEqual(h.stfmr_lockin_motor_status_var.get(), "+20.000°")
        self.assertFalse(h.stfmr_lockin_stage_prompt_open or h.stfmr_lockin_motor_busy)

    def test_no_sends_nothing_and_does_not_move(self):
        sim = Sim()
        sim.eo = 0
        run = self.hand("20", sim=sim)
        self.confirm.return_value = False
        run.h.move_stfmr_lockin_stage()
        run.h.root.drain()
        self.assertEqual(self.confirm.call_args.args[0], "STFMR stage motor driver")
        self.assertNotIn("EO=1", run.sim.received)
        self.assertEqual(run.sim.moves, [])
        self.assertIn("STFMR Newmark NSC-A1: No: EO=1 not sent; nothing moved", run.h.logged)

    def test_no_fix_after_a_stop_or_a_close_behind_the_yes(self):
        for press in ("stop", "close"):
            with self.subTest(press=press):
                sim = Sim()
                sim.latched = 1 << 6
                executor = Deferred()
                run = self.hand("20", sim=sim, executor=executor)
                h = run.h
                h.move_stfmr_lockin_stage()
                executor.run()                  # refused: latched
                h.root.drain()                  # Yes: CLR and the move queued
                self.assertEqual(len(executor.jobs), 1)
                if press == "stop":
                    h.stop_stfmr_lockin_stage()
                    self.assertEqual(len(executor.jobs), 1, "no STOP queued behind the fix and move")
                else:
                    h.stfmr_lockin_closed = True
                executor.run()
                h.root.drain()
                self.assertNotIn("CLR", run.sim.received)
                self.assertEqual(run.sim.moves, [])
                reason = "Stop stage was pressed" if press == "stop" else "STFMR is closing"
                self.assertIn(f"STFMR Newmark NSC-A1: CLR not sent: {reason} after the Yes", h.logged)

    def test_the_question_opens_outside_the_drain_with_the_buttons_locked(self):
        sim = Sim()
        sim.latched = 1 << 6
        run = self.hand("20", sim=sim)
        h = run.h
        h.ui, h.in_drain = [], False
        h.post_ui = h.ui.append
        seen = []

        def answer(title, _question, **_options):
            seen.append((title, h.in_drain, h.stfmr_lockin_stage_prompt_open, h._stfmr_lockin_motor_locked(),
                         h._stfmr_lockin_stage_job_pending()))
            return False
        self.confirm.side_effect = answer
        h.move_stfmr_lockin_stage()
        for _ in range(20):
            h.in_drain = True
            while h.ui:
                h.ui.pop(0)()
            h.in_drain = False
            if not h.root.pending:
                break
            h.root.step()
        self.assertEqual(seen, [("STFMR stage limit error", False, True, True, True)])
        self.assertFalse(h.stfmr_lockin_stage_prompt_open, "unlocked after the answer")

    def test_a_sweep_cannot_start_during_a_hand_move(self):
        executor = Deferred()
        run = self.hand("30", executor=executor)
        h = run.h
        h.move_stfmr_lockin_stage()
        h.start_stfmr_lockin_scan()
        self.assertEqual(len(executor.jobs), 1, "no stage check queued: Start waited")
        self.confirm.assert_not_called()
        self.assertFalse(h.stfmr_lockin_motor_check_pending)
        executor.run()
        h.root.drain()
        h.start_stfmr_lockin_scan()                 # afterwards it starts
        self.assertTrue(h.stfmr_lockin_motor_check_pending or h.stfmr_lockin_running or executor.jobs)

    def test_page_close_stops_a_hand_move(self):
        # During the move: the close sets the sweep's stop event first, which
        # the hand move watches too, so the worker sends STOP.
        run = self.hand("90")
        h = run.h
        h.move_hooks[90.0] = lambda: run.clock.hooks.append((run.clock.t + 3.0, h.stfmr_lockin_stop_event.set))
        h.move_stfmr_lockin_stage()
        self.assertIn("STOP", run.sim.received[run.sim.received.index("X900000"):])
        self.assertFalse(run.motor.motion_may_run)
        # Queued: the close's disconnect runs after it, and the move sends
        # nothing.
        executor = Deferred()
        run = self.hand("90", executor=executor)
        h = run.h
        h.stfmr_lockin_live_future = h.stfmr_lockin_future = None
        h.move_stfmr_lockin_stage()
        h.safe_close_stfmr_lockin()
        self.assertEqual(len(executor.jobs), 2, "the move, then the disconnect")
        executor.run()
        self.assertEqual(run.sim.moves, [])
        self.assertFalse(run.motor.connected)
        self.assertIsNone(h.newmark_stage_ownership.owner)


class ReviewFixTests(MotorBase):
    """The adversarial review of 2026-10-17 ("ship after fixes")."""

    class Card:
        """A write-only 4886 card that logs each command in the sweep's log."""

        def __init__(self, log):
            self.log = log

        def write_raw(self, payload):
            self.log.append(("kepco-write", payload.decode("ascii")))

        def clear(self):
            pass

        def close(self):
            pass

    def test_1_the_4886_is_zeroed_before_the_first_move(self):
        """The user applies 2 A with the manual panel, then starts angles
        0 to 90: the 4886 (the real controller) is zeroed and switched off
        before the stage turns, as before every move mid-sweep."""
        run = self.make(angles=("30", "60", "30"))
        h = run.h
        kepco = app.STFMRKepco4886Controller(lambda _text: None)
        kepco.instrument = self.Card(h.log)
        h.stfmr_lockin_controllers["kepco"] = kepco
        kepco.set_output("CURR", 2.0, 5.0)                 # the manual panel: 2 A, output on
        self.assertTrue(kepco.output_owned)
        manual = len(h.log)
        h.start_stfmr_lockin_scan()
        h.root.drain()
        self.error.assert_not_called()
        sweep = h.log[manual:]
        self.assertEqual(sweep[:3], [("kepco-write", "CURR 0.000000"), ("kepco-write", "OUTP OFF"),
                                     ("motor", "move", 30.0)], "zero and OFF, then the move")
        before_second = sweep[:sweep.index(("motor", "move", 60.0))]
        self.assertEqual(before_second[-2:], [("kepco-write", "CURR 0.000000"), ("kepco-write", "OUTP OFF")])
        self.assertIn("STFMR sweep: 4886 zeroed before the first stage move", h.logged)
        moves = [e for e in events(h.last_recorder) if e["event"] == "stage_move"]
        self.assertEqual([e["kepco_zeroed_before"] for e in moves], [True, True])

    def test_1_a_failed_zero_ends_the_sweep_before_anything_is_switched_on(self):
        run = self.make(angles=("30", "60", "30"))
        h = run.h
        h.stfmr_lockin_controllers["kepco"].zero_error = True
        h.start_stfmr_lockin_scan()
        h.root.drain()
        self.error.assert_called_once()
        self.assertIn("zero failed", self.error.call_args.args[1])
        self.assertEqual(run.sim.moves, [], "the stage did not turn")
        self.assertFalse([e for e in h.log if e[0] in ("smb", "830", "motor") and e[1] != "rf_off"])
        self.assertFalse([e for e in h.log if e[:2] == ("kepco", "output")])
        self.assertFalse(h.stfmr_lockin_running)

    def test_2_an_unconfirmed_stop_says_stop_stage_everywhere(self):
        run = self.make()
        run.motor.motion_may_run = True
        expected = "press Stop stage first (check the stage)"
        with self.assertRaises(RuntimeError) as plan:
            run.h._stfmr_lockin_angle_plan()
        with self.assertRaises(RuntimeError) as fix:
            run.h._stfmr_lockin_require_motor_fix_ready()
        with self.assertRaises(RuntimeError) as hand_fix:
            run.h._stfmr_lockin_require_stage_fix_ready()
        run.h.move_stfmr_lockin_stage()
        for message in (str(plan.exception), str(fix.exception), str(hand_fix.exception),
                        self.error.call_args.args[1]):
            with self.subTest(message=message):
                self.assertIn(expected, message)
                self.assertNotIn("Disconnect", message)
        source = Path(app.__file__).read_text(encoding="utf-8")
        for advice in ("Disconnect and Connect it again", "press Disconnect on the Motor row",
                       "The page has no Stop stage button"):
            self.assertNotIn(advice, source)

    def test_3_stop_stage_during_a_queued_disconnect_queues_nothing(self):
        executor = Deferred()
        run = self.make(executor=executor)
        h = run.h
        h.disconnect_stfmr_lockin_motor()
        self.assertTrue(h.stfmr_lockin_motor_disconnect_pending)
        h.stop_stfmr_lockin_stage()
        self.assertEqual(len(executor.jobs), 1, "no STOP behind the Disconnect")
        self.assertIn("STFMR Newmark NSC-A1: Stop stage during Disconnect: the Disconnect sends STOP first if the "
                      "stage turns", h.logged)
        executor.run()
        h.root.drain()
        self.error.assert_not_called()
        self.assertFalse(run.motor.connected)
        self.assertEqual(h.stfmr_lockin_motor_status_var.get(), "Not connected")
        self.assertFalse(h.stfmr_lockin_motor_disconnect_pending)
        self.assertNotIn("STOP", run.sim.received, "the stage was still, so nothing to stop")

    def test_4_an_idle_stop_locks_start_move_and_set_zero_until_it_ends(self):
        executor = Deferred()
        run = self.make(executor=executor)
        h = run.h
        h.stop_stfmr_lockin_stage()
        self.assertEqual(len(executor.jobs), 1)
        self.assertTrue(h.stfmr_lockin_stage_stop_pending)
        self.assertTrue(h._stfmr_lockin_motor_locked() and h._stfmr_lockin_stage_job_pending())
        h.move_stfmr_lockin_stage()
        h.set_stfmr_lockin_motor_zero()
        h.start_stfmr_lockin_scan()
        h.stop_stfmr_lockin_stage()                     # usable, but never a second STOP behind the first
        self.assertEqual(len(executor.jobs), 1, "nothing else queued")
        self.error.assert_not_called()
        self.confirm.assert_not_called()
        executor.run()
        h.root.drain()
        self.assertFalse(h.stfmr_lockin_stage_stop_pending)
        self.assertEqual(run.sim.received.count("STOP"), 1)
        self.assertEqual(h.stfmr_lockin_motor_status_var.get(), "+0.000°")
        h.stfmr_lockin_stage_target_var.set("5")
        h.move_stfmr_lockin_stage()                     # unlocked again
        executor.run()
        h.root.drain()
        self.assertEqual(run.sim.moves, ["X50000"])

    def test_5_zero_off_stops_a_hand_move(self):
        def kepco_zero_ready(h):
            kepco = h.stfmr_lockin_controllers["kepco"]
            kepco.lock = threading.Lock()
            kepco.force_output_off = mock.Mock()
            h._show_stfmr_lockin_kepco_state = mock.Mock()
            return kepco
        # During the move: STOP from the worker.
        run = self.make()
        h = run.h
        kepco = kepco_zero_ready(h)
        h.stfmr_lockin_stage_target_var.set("90")
        h.move_hooks[90.0] = lambda: run.clock.hooks.append((run.clock.t + 3.0, h.zero_stfmr_lockin_kepco_output))
        h.move_stfmr_lockin_stage()
        h.root.drain()
        kepco.force_output_off.assert_called_once_with()
        self.assertTrue(h.stfmr_lockin_stop_event.is_set(), "the hand move's stop event")
        self.assertIn("STOP", run.sim.received[run.sim.received.index("X900000"):])
        self.assertFalse(run.motor.motion_may_run)
        # Queued: nothing is sent.
        executor = Deferred()
        run = self.make(executor=executor)
        h = run.h
        kepco_zero_ready(h)
        h.stfmr_lockin_stage_target_var.set("30")
        h.move_stfmr_lockin_stage()
        h.zero_stfmr_lockin_kepco_output()
        executor.run()
        h.root.drain()
        self.assertEqual(run.sim.moves, [])
        self.assertIn("STFMR Newmark NSC-A1: Move stage stopped: not moved", h.logged)

    def test_6_queued_jobs_do_nothing_once_closing_and_the_disconnect_runs(self):
        executor = Deferred()
        run = self.make(executor=executor)
        h = run.h
        sr830 = h.stfmr_lockin_controllers["830"]
        sr830.connect = mock.Mock()
        h.stfmr_lockin_connect_buttons = {"830": mock.Mock()}
        h.stfmr_lockin_disconnect_buttons = {"830": mock.Mock()}
        h.stfmr_lockin_resource_widgets = {"830": mock.Mock()}
        h.stfmr_lockin_connection_vars = {"830": Variable("Not connected")}
        h.connect_stfmr_lockin_device("830")                # an instrument Connect, queued
        h.set_stfmr_lockin_motor_zero()                     # answered Yes, queued
        h.root.drain()
        jobs = len(executor.jobs)
        self.assertEqual(jobs, 2)
        h.stfmr_lockin_live_future = h.stfmr_lockin_future = None
        h.safe_close_stfmr_lockin()
        self.assertEqual(len(executor.jobs), jobs + 1, "the motor's Disconnect, queued last")
        executor.run()
        sr830.connect.assert_not_called()
        self.assertNotIn("PX=0", run.sim.received)
        self.assertFalse(run.motor.connected, "the Disconnect still ran on the worker")
        self.assertIsNone(h.newmark_stage_ownership.owner)
        # A queued hand move, Start's check and a fix after the close: nothing.
        for queue in ("move", "check"):
            with self.subTest(queue=queue):
                executor = Deferred()
                run = self.make(executor=executor)
                h = run.h
                if queue == "move":
                    h.stfmr_lockin_stage_target_var.set("30")
                    h.move_stfmr_lockin_stage()
                else:
                    h.start_stfmr_lockin_scan()
                h.stfmr_lockin_live_future = h.stfmr_lockin_future = None
                h.safe_close_stfmr_lockin()
                executor.run()
                self.assertEqual(run.sim.moves, [])
                run.motor.check_ready.assert_not_called()
                self.assertFalse(run.motor.connected)

    def test_7_begin_uses_what_start_read(self):
        executor = Deferred()
        run = self.make(executor=executor)
        h = run.h
        h.start_stfmr_lockin_scan()
        self.assertTrue(h.stfmr_lockin_motor_check_pending)
        # Typed while the check runs (the boxes are locked then; a stand-in
        # page has no widgets to lock): none of it is used.
        h.stfmr_lockin_step_var.set("0.25")
        h.stfmr_lockin_reference_var.set("Trigger In 1")
        h.stfmr_lockin_resource_vars["kepco"].set("GPIB0::99::INSTR")
        h.stfmr_lockin_manual_safety_var.set(True)
        while executor.jobs or h.root.pending:
            executor.run()
            if h.root.pending:
                h.root.step()
        self.error.assert_not_called()
        settings = events(h.last_recorder)[0]["settings"]
        self.assertEqual(settings["current_step_a"], 0.5)
        self.assertEqual(settings["mfli_reference_source"], "Aux Input 1")
        self.assertEqual(settings["resources"]["kepco"], app.STFMR_LOCKIN_DEFAULT_RESOURCES["kepco"])
        self.assertEqual(h.stfmr_lockin_reference_source, "Aux Input 1")
        source = Path(app.__file__).read_text(encoding="utf-8")
        begin = source[source.index("        def begin() -> None:"):source.index("        self._stfmr_lockin_check_motor(begin)")]
        self.assertNotRegex(begin, r"\w+_vars?(\[[^\]]+\])?\.get\(\)|\bvar\.get\(\)", "begin() reads no box")


class PageWidgetTests(unittest.TestCase):
    """The real page build on stand-in widgets (the CI GUI tests check the
    drawn page in the 1101 px and 2000 px windows)."""

    def setUp(self):
        guard = mock.patch("tkinter.Tk", side_effect=AssertionError("a test tried to open a Tk window"))
        guard.start()
        self.addCleanup(guard.stop)

    def test_the_motor_row_and_the_angle_row(self):
        page = build_stfmr_page()
        a = page.app
        entry, connect, disconnect = a.stfmr_lockin_motor_widgets
        frame = entry.master
        self.assertEqual(frame.grid_options["row"], 5, "the sixth connection row")
        kepco = a.stfmr_lockin_resource_widgets["kepco"].master
        self.assertIs(frame.master, kepco.master, "in the STFMR instrument connections box")
        row = page.in_row(frame, 0)
        # Laid out as the LS475 row: the KEPCO row's label is the magnet
        # supply choice since 2026-10-21 (user), a Combobox.
        self.assertEqual(page.in_row(kepco, 0)[0].kind, "Combobox")
        kepco_row = page.in_row(a.stfmr_lockin_resource_widgets["gauss"].master, 0)
        # The lamp (column 4) is the page's own stand-in, not a recorded widget.
        self.assertEqual([w.grid_options["column"] for w in row], [0, 1, 2, 3, 5])
        self.assertEqual([w.kind for w in row], [w.kind for w in kepco_row])
        self.assertEqual(row[0].options["text"], "Motor:")
        for mine, theirs in zip(row, kepco_row):
            for name in ("width", "anchor"):
                self.assertEqual(mine.options.get(name), theirs.options.get(name), (mine.kind, name))
            self.assertEqual(mine.grid_options, theirs.grid_options, mine.kind)
        self.assertIs(entry.options["textvariable"], a.stfmr_lockin_motor_resource_var)
        self.assertEqual((connect.options["text"], disconnect.options["text"], disconnect.options["state"]),
                         ("Connect", "Disconnect", "disabled"))
        self.assertIs(row[4].options["textvariable"], a.stfmr_lockin_motor_status_var)
        self.assertEqual(frame.column_options, kepco.column_options)
        self.assertEqual(frame.column_options, a.stfmr_lockin_resource_widgets["gauss"].master.column_options)
        # The angle row, right below the Mod. row: three pairs.
        table = a.stfmr_lockin_meter_combo.master
        cells = page.in_row(table, 4)
        self.assertEqual([w.options.get("text", "") for w in cells[0::2]],
                         ["Angle start °:", "Angle stop °:", "Step °:"])
        self.assertEqual([w.grid_options["column"] for w in cells], [0, 1, 2, 3, 4, 5])
        for field, variable, kept in zip(cells[1:6:2], (a.stfmr_lockin_angle_start_var, a.stfmr_lockin_angle_stop_var,
                                                       a.stfmr_lockin_angle_step_var),
                                         (a.stfmr_lockin_angle_start_entry, a.stfmr_lockin_angle_stop_entry,
                                          a.stfmr_lockin_angle_step_entry)):
            self.assertEqual((field.kind, field.options["width"], field.grid_options["sticky"]), ("Entry", 8, "ew"))
            self.assertIs(field.options["textvariable"], variable)
            self.assertIs(field, kept, "kept, so a sweep can lock it")
        # The stage by hand, kept together on the Read LS475 line of the
        # sweep actions: Stage °, Move stage, Stop stage, Set motor 0°.
        group = a.stfmr_lockin_stage_move_button.master
        self.assertIs(group.master, a.stfmr_lockin_read_gauss_button.master.master, "the Read LS475 line")
        for widget in (a.stfmr_lockin_stage_target_entry, a.stfmr_lockin_stage_stop_button,
                       a.stfmr_lockin_motor_zero_button):
            self.assertIs(widget.master, group)
        self.assertIs(a.stfmr_lockin_stage_target_entry.options["textvariable"], a.stfmr_lockin_stage_target_var)
        for widget, text in ((a.stfmr_lockin_stage_move_button, "Move stage"),
                             (a.stfmr_lockin_stage_stop_button, "Stop stage"),
                             (a.stfmr_lockin_motor_zero_button, "Set motor 0°")):
            self.assertEqual((widget.kind, widget.options["text"], widget.options["state"]),
                             ("Button", text, "disabled"))
        self.assertEqual(a.stfmr_lockin_stage_target_entry.options["state"], "disabled")
        self.assertFalse([w for w in page.in_row(table, 4) if w.kind == "Button"], "no button in the table")
        self.assertEqual([w.options.get("text", "") for w in page.in_row(table, 3)[0::2]],
                         ["Mod. start Hz:", "Mod. end Hz:", "Settle s:", "Acquire s:"])
        # The labels widen no column: "Angle start °:" and "Angle stop °:"
        # are narrower than "MFLI reference:" and "SR830 REF IN:", and "Step
        # °:" than "f step GHz:" ("Angle step °:" was 8 to 12 px wider, and
        # on Windows CI the table no longer fitted the 1101 px window;
        # 2026-10-17).
        for column, label in ((0, "Angle start °:"), (2, "Angle stop °:"), (4, "Step °:")):
            others = [page.cell(table, r, column).options["text"] for r in (0, 1, 2, 3, 5, 6)]
            self.assertLessEqual(width_in_zeros(label), max(width_in_zeros(t) for t in others) + 0.2,
                                 (label, others))

    def test_the_locks(self):
        class Widget:
            def __init__(self, state="normal"):
                self.state = state

            def configure(self, **options):
                self.state = options.get("state", self.state)

        a = object.__new__(App)
        a.closing = a.stfmr_lockin_closed = False
        a.stfmr_lockin_running = a.stfmr_lockin_starting = False
        a.stfmr_lockin_motor_busy = a.stfmr_lockin_motor_check_pending = False
        a.stfmr_lockin_stage_prompt_open = a.stfmr_lockin_stage_stop_pending = False
        a.stfmr_lockin_motor_disconnect_pending = False
        a.stfmr_lockin_gauss_busy = False
        a.stfmr_lockin_rows = []
        a.stfmr_lockin_meter_var = Variable("SR830")
        a.stfmr_lockin_controllers = {key: types.SimpleNamespace(connected=True)
                                      for key in ("kepco", "gauss", "smb", "830")}
        a.stfmr_lockin_mfli_controller = types.SimpleNamespace(mfli_connected=False)
        a.stfmr_lockin_connect_buttons, a.stfmr_lockin_disconnect_buttons = {}, {}
        a.stfmr_lockin_connection_vars, a.stfmr_lockin_resource_widgets = {}, {}
        for name in ("start", "stop", "save", "read_gauss"):
            setattr(a, f"stfmr_lockin_{name}_button", Widget())
        angles = [Widget() for _ in range(3)]
        (a.stfmr_lockin_angle_start_entry, a.stfmr_lockin_angle_stop_entry, a.stfmr_lockin_angle_step_entry) = angles
        a.stfmr_lockin_motor_widgets = row = (Widget(), Widget(), Widget())
        hand = [Widget() for _ in range(3)]
        (a.stfmr_lockin_stage_target_entry, a.stfmr_lockin_stage_move_button,
         a.stfmr_lockin_motor_zero_button) = hand
        a.stfmr_lockin_stage_stop_button = stage_stop = Widget()
        a.stfmr_lockin_motor = motor = types.SimpleNamespace(connected=False)
        a.instrument_connection_indicators = {}

        def states():
            App._update_stfmr_lockin_ui(a)
            return dict(row=[w.state for w in row], hand=[w.state for w in hand], stage_stop=stage_stop.state,
                        angles=[w.state for w in angles], start=a.stfmr_lockin_start_button.state,
                        stop=a.stfmr_lockin_stop_button.state)

        self.assertEqual(states(), dict(row=["normal", "normal", "disabled"], hand=["disabled"] * 3,
                                        stage_stop="disabled", angles=["normal"] * 3, start="normal",
                                        stop="disabled"), "not connected")
        motor.connected = True
        self.assertEqual(states(), dict(row=["disabled", "disabled", "normal"], hand=["normal"] * 3,
                                        stage_stop="normal", angles=["normal"] * 3, start="normal",
                                        stop="disabled"), "connected, idle")
        for flag in ("stfmr_lockin_running", "stfmr_lockin_starting", "stfmr_lockin_motor_busy",
                     "stfmr_lockin_motor_check_pending", "stfmr_lockin_stage_prompt_open",
                     "stfmr_lockin_stage_stop_pending"):         # an idle STOP (review, 2026-10-17)
            with self.subTest(flag=flag):
                setattr(a, flag, True)
                result = states()
                setattr(a, flag, False)
                self.assertEqual((result["row"], result["hand"]), (["disabled"] * 3, ["disabled"] * 3))
                self.assertEqual(result["stage_stop"], "normal", "Stop stage stays usable")
                if flag != "stfmr_lockin_starting":
                    self.assertEqual(result["start"], "disabled", "no sweep starts meanwhile")
                if flag in ("stfmr_lockin_running", "stfmr_lockin_motor_check_pending"):
                    self.assertEqual(result["angles"], ["disabled"] * 3)
                    self.assertEqual(result["stop"], "normal", "Stop / zero stays usable")
        # A Disconnect queued or running: Stop stage is locked too, as the
        # Disconnect sends STOP itself (review, 2026-10-17).
        a.stfmr_lockin_motor_busy = a.stfmr_lockin_motor_disconnect_pending = True
        self.assertEqual(states()["stage_stop"], "disabled")
        a.stfmr_lockin_motor_busy = a.stfmr_lockin_motor_disconnect_pending = False

    def test_every_input_is_locked_during_the_stage_check(self):
        """Review, 2026-10-17: during Start's stage check only the angle boxes
        were locked; every other input was already read, so an edit then
        showed on the page but was not used."""
        page = build_stfmr_page()
        a = page.app
        a.closing = a.stfmr_lockin_closed = False
        a.stfmr_lockin_running = a.stfmr_lockin_starting = False
        a.stfmr_lockin_motor_busy = a.stfmr_lockin_stage_prompt_open = False
        a.stfmr_lockin_gauss_busy = False
        a.stfmr_lockin_rows = []
        a.stfmr_lockin_meter_var = Variable("SR830")
        a.stfmr_lockin_controllers = {key: types.SimpleNamespace(connected=True, output_owned=False)
                                      for key in ("kepco", "gauss", "smb", "830")}
        a.stfmr_lockin_mfli_controller = types.SimpleNamespace(mfli_connected=False)
        a.stfmr_lockin_motor = types.SimpleNamespace(connected=False)
        a.instrument_connection_indicators = {}
        inputs = a.stfmr_lockin_parameter_inputs
        self.assertGreaterEqual(len(inputs), 25, "every box and tick of the table")
        for pending in (True, False):
            a.stfmr_lockin_motor_check_pending = pending
            App._update_stfmr_lockin_ui(a)
            with self.subTest(pending=pending):
                states = {w.options.get("state") for w, _idle in inputs}
                if pending:
                    self.assertEqual(states, {"disabled"})
                else:
                    self.assertEqual([w.options.get("state") for w, _idle in inputs], [idle for _w, idle in inputs])
                    self.assertIn("readonly", states, "the dropdowns back to readonly")


class CloseTests(MotorBase):
    def test_closing_disconnects_the_stage_on_the_worker(self):
        executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="stfmr-lockin")
        run = self.make(connect=False, executor=executor)
        h = run.h
        h.ui = []
        h.post_ui = h.ui.append
        h.connect_stfmr_lockin_motor()
        executor.submit(lambda: None).result(timeout=30)
        while h.ui:
            h.ui.pop(0)()
        self.assertTrue(run.motor.connected)
        worker = executor.submit(threading.get_ident).result(timeout=30)
        h.stfmr_lockin_live_future = h.stfmr_lockin_future = None
        h.safe_close_stfmr_lockin()
        executor.shutdown(wait=True)
        self.assertFalse(run.motor.connected)
        self.assertIsNone(h.newmark_stage_ownership.owner)
        self.assertEqual(run.dll.names()[-1], "fnPerformaxComClose")
        self.assertEqual({thread for _name, thread in run.dll.calls}, {worker})
        self.assertIn("STFMR Newmark NSC-A1 disconnected", run.motor_log)

    def test_closing_without_the_stage_is_as_before(self):
        run = self.make(connect=False)
        h = run.h
        h.stfmr_lockin_executor = mock.Mock()
        h.stfmr_lockin_live_future = h.stfmr_lockin_future = None
        h.safe_close_stfmr_lockin()
        h.stfmr_lockin_executor.submit.assert_not_called()
        h.stfmr_lockin_executor.shutdown.assert_called_once_with(wait=False, cancel_futures=True)


class ThreadTests(MotorBase):
    def test_a_whole_motor_sweep_calls_the_dll_from_the_worker_only(self):
        executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="stfmr-lockin")
        self.addCleanup(executor.shutdown, wait=True)
        run = self.make(connect=False, executor=executor, angles=("0", "20", "10"))
        h = run.h
        h.ui = []
        h.post_ui = h.ui.append

        def pump():
            for _ in range(20000):
                executor.submit(lambda: None).result(timeout=30)
                moved = False
                while h.ui:
                    h.ui.pop(0)()
                    moved = True
                if h.root.pending:
                    h.root.step()
                    moved = True
                if not moved:
                    return
            raise AssertionError("the sweep did not end")
        h.connect_stfmr_lockin_motor()
        pump()
        h.start_stfmr_lockin_scan()
        pump()
        self.error.assert_not_called()
        self.assertFalse(h.stfmr_lockin_running)
        rows = read_csv(next(Path(self.tmp.name).glob("*/*.csv")))
        self.assertEqual([float(r["stage_angle_deg"]) for r in rows], [0.0, 0.0, 10.0, 10.0, 20.0, 20.0])
        worker = executor.submit(threading.get_ident).result(timeout=30)
        threads = {thread for _name, thread in run.dll.calls}
        self.assertNotIn(threading.get_ident(), threads, "a DLL call ran on the Tk (test) thread")
        self.assertEqual(threads, {worker})
        for command in ("X100000", "X200000"):
            self.assertIn(command, run.sim.received)


if __name__ == "__main__":
    unittest.main()
