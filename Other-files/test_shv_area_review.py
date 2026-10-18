"""SHV page, Newmark driver and shared controllers: the audit of 2026-10-18.

H1: an unconfirmed stop is kept across a Disconnect and a Connect, on either
page, until a STOP sequence sees the stage stopped; a Disconnect with MST
unreadable after one sends STOP again and says CHECK THE STAGE.  H2: the
SHV stop handlers set their event and queue their OFF before the run log is
written.  H3: an ABORT that stopped the stage is said so, and the KEPCO,
LS475 and Newmark lines are a running scan's run-log notes.  C1-1: the
LabOne C / .NET adapters, without setDouble / getDouble, are skipped.
C1-3: the PNA gets OUTP OFF only once identified.  C1-5: the .NET DLL search
goes past a bad candidate and never leaks its DLL-directory handle.  C2-7:
the camera is "still releasing" while its thread holds it.  The SHV scans'
save and zero at every ending, and their dialogs out of drain_ui_queue.

No Tk window, no instrument, no real DLL: the stage is the program's
controller on test_rte_angle_scan's fake PerformaxCom.dll (the standalone
script's simulated NSC-A1, on a virtual clock), the pages are the stand-ins
of test_rte_panel, test_rte_angle_end_to_end and test_stfmr_motor_angle."""
import csv
import ctypes
import inspect
import os
from pathlib import Path
import re
import sys
import tempfile
import threading
import time
import types
import unittest
from unittest.mock import Mock, patch

import test_rte_angle_end_to_end as ete
import test_rte_angle_scan as rte_stage
import test_rte_panel as rte_tests
import test_stfmr_motor_angle as stfmr_motor
from test_ppms_motor_debug import app
from test_ppms_two_panels import Var
from test_rte_angle_scan import AfterRoot, script

_guards = []


def setUpModule():
    def refuse(what):
        def refused(*_args, **_kwargs):
            raise AssertionError(f"a test reached {what}")
        return refused
    for module in (app, stfmr_motor.app):
        for target, name, replacement in ((ctypes, "WinDLL", refuse("ctypes.WinDLL (a real DLL)")),
                                          (os, "add_dll_directory", refuse("os.add_dll_directory")),
                                          (module, "newmark_load_performax", refuse("the real Newmark DLL loader")),
                                          (module.tk, "Tk", refuse("tkinter.Tk (a window)"))):
            guard = patch.object(target, name, replacement, create=True)
            guard.start()
            _guards.append(guard)


def tearDownModule():
    while _guards:
        _guards.pop().stop()


# ---- simulated stages ----------------------------------------------------------
class StopIgnored(rte_stage.Stuck):
    """A move that does not end by itself; STOP does nothing, ABORT stops it at once."""

    def _stop(self, command, now):
        if command == "ABORT":
            super()._stop(command, now)


class LosesMST(rte_stage.Runaway):
    """STOP and ABORT do nothing (Runaway) while ``stuck``; ``lose`` MST
    transactions from now on get no reply (-1: every one).  Not stuck, it is
    the plain simulated stage again."""

    lose = 0
    stuck = True

    def _finished(self, now):
        return super()._finished(now) if self.stuck else rte_stage.Sim._finished(self, now)

    def _stop(self, command, now):
        if not self.stuck:
            rte_stage.Sim._stop(self, command, now)

    def send_recv(self, handle, data):
        command = bytes(data).split(b"\0", 1)[0].decode("ascii")
        if command == "MST" and self.lose:
            self.lose -= 1 if self.lose > 0 else 0
            self.received.append("MST (lost)")
            return None
        return super().send_recv(handle, data)


def turning(sim):
    sim._start(3000000, script._now())


# ---- H1, H3: the driver ----------------------------------------------------------------
class UnconfirmedStopDriverTests(rte_stage.StageBase):
    def unconfirmed(self, sim, ownership=None):
        h = self.make(sim)
        if ownership is not None:
            h.stage.share_stop_record(ownership)
        h.stage.connect("auto")
        turning(h.sim)
        result = h.stage.stop()
        self.assertFalse(result["stopped"])
        self.assertTrue(h.stage.stop_finished, "the stop sequence ran to its end")
        return h

    def test_a_disconnect_with_mst_unreadable_after_an_unconfirmed_stop_stops_again_and_says_so(self):
        # Review, 2026-10-18: it closed silently and cleared motion_may_run,
        # since the stop sequence had "finished".
        h = self.unconfirmed(LosesMST())
        h.sim.lose = -1
        start = len(h.sim.received)
        with self.assertRaisesRegex(app.NewmarkStageError, r"^Newmark NSC-A1 closed, but the stage could not be "
                                                           r"confirmed stopped \(STOP, then ABORT\): CHECK THE STAGE$"):
            h.stage.disconnect()
        rest = h.sim.received[start:]
        self.assertEqual(rest[0], "MST (lost)")
        self.assertLess(rest.index("STOP"), rest.index("ABORT"), "STOP, then ABORT")
        self.assertEqual(h.dll.names()[-1], "fnPerformaxComClose")
        self.assertFalse(h.stage.connected)
        self.assertTrue(h.stage.motion_may_run, "kept across the close")
        self.assertIn("not confirmed stopped", h.stage.unconfirmed_stop)

    def test_a_stop_seen_at_the_disconnect_clears_it(self):
        h = self.unconfirmed(LosesMST())
        h.sim.motion = None         # it came to rest meanwhile
        h.sim.lose = 1              # the disconnect's first MST is lost
        h.stage.disconnect()
        self.assertIn("STOP", h.sim.received[-12:])
        self.assertFalse(h.stage.motion_may_run)
        self.assertEqual(h.stage.unconfirmed_stop, "")
        self.assertTrue(any("the STOP before closing confirmed the stage stopped; the earlier unconfirmed stop" in line
                            for line in h.log), h.log)
        self.assertTrue(h.log[-1].startswith("SHV Newmark NSC-A1: stopped before closing, at "), h.log[-1])

    def test_mst_unreadable_on_a_still_stage_closes_without_stop(self):
        h = self.make(LosesMST())
        h.stage.connect("auto")
        h.sim.lose = 1
        h.stage.disconnect()
        self.assertNotIn("STOP", h.sim.received)
        self.assertFalse(h.stage.connected)

    def test_it_is_kept_across_a_reconnect_until_stop_stage_sees_it_stopped(self):
        ownership = app.NewmarkStageOwnership()
        h = self.unconfirmed(LosesMST(), ownership)
        with self.assertRaises(app.NewmarkStageError):
            h.stage.disconnect()
        self.assertIn("SHV page", ownership.unconfirmed_stop)
        h.sim.motion, h.sim.stuck = None, False      # it came to rest meanwhile
        h.stage.connect("auto")
        self.assertTrue(h.stage.motion_may_run, "a new Connect no longer clears it")
        self.assertTrue(any("its last stop was not confirmed" in line and "press Stop stage before any move" in line
                            for line in h.log))
        sent = list(h.sim.received)
        for call in (lambda: h.stage.move_to_deg(5, threading.Event()), h.stage.check_ready):
            with self.assertRaisesRegex(app.NewmarkRefused, "last stop was not confirmed, so it may still be "
                                                            r"turning: press Stop stage first \(check the stage\); "
                                                            "nothing was sent"):
                call()
        self.assertEqual(h.sim.received, sent, "refused before anything was sent")
        # The other page's controller sees it as soon as it claims the device.
        other = app.NewmarkNSCA1Controller(lambda _text: None, owner_scope="STFMR")
        self.assertFalse(other.motion_may_run)
        ownership.release("SHV")
        ownership.claim("STFMR", other)
        self.assertTrue(other.motion_may_run)
        ownership.release("STFMR")
        result = h.stage.stop()
        self.assertTrue(result["stopped"])
        self.assertEqual(ownership.unconfirmed_stop, "")
        self.assertFalse(h.stage.motion_may_run)
        self.assertFalse(other.motion_may_run)
        self.assertTrue(any("Stop stage confirmed the stage stopped" in line for line in h.log))
        h.stage.move_to_deg(5, threading.Event())
        self.assertEqual(h.sim.px, 50000)

    def test_a_controllers_own_record_moves_to_the_programs_on_claim(self):
        h = self.unconfirmed(LosesMST())
        ownership = app.NewmarkStageOwnership()
        ownership.claim("SHV", h.stage)
        self.assertIs(h.stage.stop_record, ownership)
        self.assertTrue(ownership.unconfirmed_stop)


class AbortDriverTests(rte_stage.StageBase):
    def test_stop_stage_that_needed_abort_says_so(self):
        h = self.make(StopIgnored())
        h.stage.connect("auto")
        turning(h.sim)
        result = h.stage.stop()
        self.assertTrue(result["stopped"])
        self.assertTrue(result["aborted"])
        self.assertIn("ABORT sent: STOP did not stop it", result["notes"])
        self.assertLess(h.sim.received.index("STOP"), h.sim.received.index("ABORT"))
        self.assertEqual(h.stage.unconfirmed_stop, "")

    def test_a_stop_that_stop_alone_ends_has_no_abort_note(self):
        h = self.make()
        h.stage.connect("auto")
        turning(h.sim)
        result = h.stage.stop()
        self.assertEqual((result["stopped"], result["aborted"], result["notes"]), (True, False, []))

    def test_a_time_out_stopped_by_abort(self):
        h = self.make(StopIgnored())
        h.stage.connect("auto")
        with self.assertRaisesRegex(app.NewmarkMotionError, r"did not end within 7 s, stopped \(STOP, then ABORT\) at "
                                                            r".*\(ABORT sent: STOP did not stop it\)$"):
            h.stage.move_to_deg(1, threading.Event())

    def test_a_stop_during_a_move_stopped_by_abort(self):
        h = self.make(StopIgnored())
        h.stage.connect("auto")
        event = threading.Event()
        h.clock.hooks.append((h.clock.t + 2.0, event.set))
        with self.assertRaises(app.NewmarkStopRequested) as caught:
            h.stage.move_to_deg(90, event)
        self.assertTrue(caught.exception.stopped)
        self.assertTrue(h.stage.stop_aborted)
        self.assertIn("(ABORT sent: STOP did not stop it)", str(caught.exception))

    def test_a_disconnect_stopped_by_abort(self):
        h = self.make(StopIgnored())
        h.stage.connect("auto")
        turning(h.sim)
        h.stage.disconnect()
        self.assertTrue(h.log[-1].startswith("SHV Newmark NSC-A1: stopped before closing (STOP, then ABORT), at "),
                        h.log[-1])


# ---- H3: the SHV page's stage texts ------------------------------------------------------
class AbortPageTests(rte_stage.StageBase):
    def test_stop_stage_on_the_page(self):
        h = self.make(StopIgnored())
        h.stage.connect("auto")
        turning(h.sim)
        h.panel._rte_stage_stop()
        self.drain(h)
        self.assertRegex(h.panel.rte_stage_live.get(), r"^Stage: stopped at [+-]\d+\.\d{3}° \(ABORT\)$")
        logged = [call.args[0] for call in h.panel.thread_log.call_args_list]
        self.assertTrue(any(re.search(r"Stop stage: stopped \(STOP, then ABORT\) at ", line) for line in logged),
                        logged)

    def test_a_hand_move_stopped_by_abort(self):
        h = self.make(StopIgnored())
        h.stage.connect("auto")
        h.panel.rte_stage_target.set("90")
        h.clock.hooks.append((h.clock.t + 2.0, h.panel.rte_stage_stop_event.set))
        h.panel._rte_stage_move()
        self.drain(h)
        self.assertRegex(h.panel.rte_stage_live.get(), r"^Stage: stopped at \+\d+\.\d{3}° \(Stop, ABORT\)$")

    def test_a_scan_move_stopped_by_abort(self):
        h = self.make(StopIgnored())
        h.stage.connect("auto")
        h.clock.hooks.append((h.clock.t + 2.0, h.panel.ppms_field_scan_stop_event.set))
        with self.assertRaises(app.PPMSScanCancelled) as caught:
            h.panel._rte_scan_move_stage(90)
        self.assertRegex(h.panel.rte_stage_where, r"^stage stopped at \+\d+\.\d{3}° \(ABORT\)$")
        self.assertIn("(ABORT)", str(caught.exception))

    def test_the_stop_after_a_stage_failure_stopped_by_abort(self):
        h = self.make(StopIgnored())
        h.stage.connect("auto")
        turning(h.sim)
        h.panel._rte_log = Mock()
        h.panel._rte_stop_stage_after_failure(app.NewmarkStageError("PX: an empty reply"))
        self.assertRegex(h.panel.rte_stage_where, r"^stage stopped at [+-]\d+\.\d{3}° \(ABORT\)$")


# ---- H1 on the SHV page, end to end --------------------------------------------------------
class SHVReconnectTests(ete.EndToEnd):
    def test_an_unconfirmed_stop_holds_after_a_reconnect_until_stop_stage(self):
        h = self.make(sim=LosesMST(), span=("0", "10", "10"), round_trip=False)
        panel, stage = h.panel, h.stage
        turning(h.sim)
        panel._rte_stage_stop()
        self.pump(h)
        self.assertEqual(h.showerror.call_args.args[0], "SHV stage")
        self.assertIn("could not be confirmed stopped", h.showerror.call_args.args[1])
        panel._rte_disconnect("newmark")
        self.pump(h)
        self.assertFalse(stage.connected)
        self.assertEqual(panel.rte_stage_live.get(), "Stage: closed, STOP not confirmed")
        self.assertIn("CHECK THE STAGE", h.showerror.call_args.args[1])
        ownership = panel._newmark_stage_ownership()
        self.assertIsNone(ownership.owner)
        self.assertTrue(ownership.unconfirmed_stop)
        # Connected again: still not confirmed, and the stage line says so.
        h.sim.motion, h.sim.stuck = None, False     # it came to rest meanwhile
        h.showerror.reset_mock()
        panel._rte_connect("newmark")
        self.pump(h)
        self.assertTrue(stage.connected)
        self.assertTrue(stage.motion_may_run)
        self.assertEqual(panel.rte_stage_live.get(), "Stage: +0.000°; last stop NOT confirmed: press Stop stage")
        self.assertTrue(any("its last stop was not confirmed" in line for line in h.logged))
        # The idle read keeps saying it.
        data = h.executor.submit(panel._rte_idle_read, 317.3).result(timeout=30)    # on the worker
        self.assertEqual(data["stage_display"], "Stage: +0.000°; last stop NOT confirmed: press Stop stage")
        # Start (every mode) and Move stage are refused; nothing is sent to move.
        panel.start_ppms_field_scan()
        self.pump(h)
        self.assertEqual(h.showerror.call_args.args[0], "SHV scan parameters")
        self.assertIn("last stop was not confirmed", h.showerror.call_args.args[1])
        panel.rte_stage_target.set("5")
        panel._rte_stage_move()
        self.assertEqual(h.showerror.call_args.args[0], "SHV stage")
        self.assertIn("press Stop stage first", h.showerror.call_args.args[1])
        self.assertEqual(h.sim.moves, [], "nothing was sent to move")
        self.assertFalse(panel.ppms_field_scan_running)
        # Stop stage sees it stopped: cleared, and the scan runs.
        panel._rte_stage_stop()
        self.pump(h)
        self.assertEqual(panel.rte_stage_live.get(), "Stage: stopped at +0.000°")
        self.assertFalse(stage.motion_may_run)
        self.assertEqual(ownership.unconfirmed_stop, "")
        h.showerror.reset_mock()
        self.start(h)
        h.showerror.assert_not_called()
        self.assertEqual(panel.ppms_field_scan_status_var.get(), "Completed; KEPCO and 6221 zeroed; stage at +10.000°")


# ---- H1 across the pages: ST-FMR -----------------------------------------------------------
class CrossPageTests(stfmr_motor.MotorBase):
    def test_an_unconfirmed_stop_on_shv_holds_on_stfmr_until_its_stop_stage(self):
        run = self.make(connect=False)
        h = run.h
        ownership = h.newmark_stage_ownership
        shv_sim = LosesMST()
        shv_dll = rte_stage.RecordingDLL(shv_sim)
        shv_log = []
        shv = stfmr_motor.app.NewmarkNSCA1Controller(shv_log.append, owner_scope="SHV",
                                                     loader=lambda: (shv_dll, "fake PerformaxCom.dll"))
        shv._now, shv._sleep = run.clock.now, run.clock.sleep
        ownership.claim("SHV", shv)
        shv.connect("auto")
        turning(shv_sim)
        self.assertFalse(shv.stop()["stopped"])
        with self.assertRaises(stfmr_motor.app.NewmarkStageError):
            shv.disconnect()
        ownership.release("SHV")
        # The STFMR page's own Connect: the flag is the program's.
        h.connect_stfmr_lockin_motor()
        h.root.drain()
        self.assertTrue(run.motor.connected)
        self.assertTrue(run.motor.motion_may_run)
        self.assertEqual(h.stfmr_lockin_motor_status_var.get(), "CHECK STAGE")
        with self.assertRaisesRegex(RuntimeError, "last stop was not confirmed, so it may still be turning: press "
                                                  "Stop stage first"):
            h._stfmr_lockin_angle_plan()
        h.move_stfmr_lockin_stage()
        self.assertIn("press Stop stage first", self.error.call_args.args[1])
        self.assertEqual(run.sim.moves, [])
        # Its Stop stage sees the stage stopped: cleared for both pages.
        h.stop_stfmr_lockin_stage()
        h.root.drain()
        self.assertFalse(run.motor.motion_may_run)
        self.assertEqual(ownership.unconfirmed_stop, "")
        self.assertFalse(shv.motion_may_run)
        self.assertEqual(h._stfmr_lockin_angle_plan()["used"], True)


# ---- H2: the SHV stop handlers, event and OFF first ------------------------------------------
class ProbeRecorder:
    """A running scan's recorder that notes, at each event, what the probe says."""

    closed = False

    def __init__(self, probe):
        self.probe, self.events = probe, []

    def event(self, name, **details):
        self.events.append((name, details.get("button"), self.probe()))
        return True

    def check(self):
        pass


class StopOrderTests(unittest.TestCase):
    def panel(self):
        panel = rte_tests.RTE.make_worker(self)
        panel.ppms_executor = Mock()
        return panel

    def test_stop(self):
        panel = self.panel()
        panel.ppms_field_scan_running = True
        panel.ppms_field_scan_status_var = Var("")
        panel.ppms_field_scan_stop_button = Mock()
        panel.rte_recorder = ProbeRecorder(panel.ppms_field_scan_stop_event.is_set)
        panel.stop_ppms_field_scan()
        self.assertEqual(panel.rte_recorder.events, [("stop_requested", "Stop", True)])

    def test_kepco_zero_off(self):
        panel = self.panel()
        panel.rte_recorder = ProbeRecorder(lambda: (panel.ppms_field_scan_stop_event.is_set(),
                                                    panel.ppms_executor.submit.called))
        panel._rte_zero()
        self.assertEqual(panel.rte_recorder.events, [("stop_requested", "KEPCO Zero / OFF", (True, True))])
        self.assertIs(panel.ppms_executor.submit.call_args.args[0], panel.rte_controllers["kepco"].force_output_off)

    def test_6221_2400_off(self):
        panel = self.panel()
        panel.ppms_connection_var = Var("")
        panel.rte_recorder = ProbeRecorder(lambda: (panel.ppms_field_scan_stop_event.is_set(),
                                                    panel.ppms_executor.submit.called))
        panel.ppms_sources_off()
        self.assertEqual(panel.rte_recorder.events, [("stop_requested", "6221 / 2400 OFF", (True, True))])

    def test_stop_stage(self):
        panel = self.panel()
        panel.rte_stage_stop_event = threading.Event()
        panel.rte_controllers["newmark"] = Mock(connected=True, in_move=False)
        panel.ppms_field_scan_running = False
        panel.rte_stage_live = Var("")
        panel.rte_recorder = ProbeRecorder(lambda: (panel.rte_stage_stop_event.is_set(),
                                                    panel.ppms_executor.submit.called))
        panel._rte_stage_stop()
        self.assertEqual(panel.rte_recorder.events, [("stop_requested", "Stop stage", (True, True))])
        self.assertIs(panel.ppms_executor.submit.call_args.args[0], panel.rte_controllers["newmark"].stop)

    def test_the_program_closing(self):
        panel = self.panel()
        panel.rte_stage_stop_event = threading.Event()
        panel.root = Mock()
        panel.zurich_executor = Mock()
        panel.rte_recorder = ProbeRecorder(lambda: (panel.ppms_field_scan_stop_event.is_set(),
                                                    panel.rte_stage_stop_event.is_set(),
                                                    panel.ppms_executor.submit.called))
        panel.safe_close_ppms()
        # Written before the clean-up job is queued, which closes the log once
        # the scan has ended.
        self.assertEqual(panel.rte_recorder.events, [("stop_requested", "the program closing", (True, True, False))])
        panel.ppms_executor.submit.assert_called_once()


# ---- H3: the controllers' lines are the run log's notes ----------------------------------------
class ControllerLogTests(ete.RunLogBase):
    def test_the_rack_controllers_log_through_the_page(self):
        panel = ete.build_page()
        for key in ("kepco", "gauss", "newmark"):
            with self.subTest(key=key):
                self.assertEqual(panel.rte_controllers[key].logger, panel.thread_log)
                self.assertNotEqual(panel.rte_controllers[key].logger, panel.parent_app.thread_log)
        with tempfile.TemporaryDirectory() as folder:
            panel.rte_recorder = app.SHVScanRecorder(Path(folder), "notes")
            panel.rte_controllers["kepco"].logger("SHV KEPCO 4886 connected: KEPCO,BIT 4886")
            path = panel.rte_recorder.log_path
            panel.rte_recorder.close("not_started")
            self.assertIn('"event": "note", "text": "SHV KEPCO 4886 connected: KEPCO,BIT 4886"',
                          path.read_text(encoding="utf-8"))

    def test_an_abort_in_a_scan_is_in_its_log(self):
        h = self.make(sim=StopIgnored(), span=("0", "10", "10"), round_trip=False)
        panel = h.panel
        h.stage.logger = panel.thread_log       # as RTEPanel makes it
        h.move_hooks[10.0] = lambda: h.clock.hooks.append((h.clock.t + 3.0, panel.stop_ppms_field_scan))
        self.start(h)
        events = self.events(h)
        notes = [event["text"] for event in events if event["event"] == "note"]
        self.assertIn("SHV Newmark NSC-A1: not stopped after STOP; ABORT", notes)
        self.assertRegex(events[-1]["stage"], r"^stage stopped at \+\d+\.\d{3}° \(ABORT\)$")
        self.assertRegex(panel.ppms_field_scan_status_var.get(),
                         r"^Stopped; KEPCO and 6221 zeroed; stage stopped at \+\d+\.\d{3}° \(ABORT\)$")


# ---- the SHV scans' save and zero, and their dialogs out of drain_ui_queue --------------------------
class SHVSaveZeroTests(ete.RunLogBase):
    def outside_drain(self, h):
        """Every dialog: never inside drain_ui_queue (the README's 2026-10-14 rule)."""
        h.showerror.side_effect = lambda *_args, **_kwargs: self.assertFalse(h.in_drain, "opened inside "
                                                                                             "drain_ui_queue")

    def test_field_and_current_stopped_from_the_tk_thread(self):
        for mode, span, x_column in (("Field", ("0", "4", "1"), "measured_field_g"),
                                     ("Current", ("0.01", "0.05", "0.01"), "6221_current_rms_ma")):
            with self.subTest(mode=mode):
                h = self.make_mode(mode, span, tc_zurich="1")
                panel = h.panel
                panel.start_ppms_field_scan()       # submitted at once: no pump, which waits for the worker
                self.assertTrue(panel.ppms_field_scan_running)
                deadline = time.monotonic() + 30
                while not panel.ppms_field_scan_rows and time.monotonic() < deadline:
                    time.sleep(0.02)
                panel.stop_ppms_field_scan()
                self.pump(h)
                self.assertTrue(panel.ppms_field_scan_status_var.get().startswith("Stopped; KEPCO and 6221 zeroed"),
                                panel.ppms_field_scan_status_var.get())
                h.showerror.assert_not_called()
                self.assertEqual(self.names(h, "kepco", "6221")[-3:], ete.ZEROED, "the end zeroes both, in order")
                events = self.events(h)
                names = self.without_notes(events)
                self.assertEqual(names[:3], ["run_prepared", "scan_started", "harmonic_pass"])
                self.assertEqual((sorted(names[-3:-1]), names[-1]), (["outputs_zeroed", "stop_requested"],
                                                                     "run_finished"))
                saved = names.count("point_saved")
                rows = self.rows(h)
                self.assertEqual((len(rows), saved, events[-1]["points_saved"]), (saved, saved, saved))
                self.assertEqual((events[-1]["outcome"], events[-1]["outputs"]),
                                 ("stopped", {"6221": "zeroed", "kepco": "zeroed"}))
                (point,) = [event for event in events if event["event"] == "point_saved"][:1]
                self.assertEqual(point["x_column"], x_column)
                # Save CSV as opens in the run folder and writes the same rows.
                target = Path(h.folder.name) / f"{mode}_copy.csv"
                with patch.object(app.filedialog, "asksaveasfilename", return_value=str(target)) as ask:
                    panel.save_ppms_field_scan_csv()
                self.assertEqual(ask.call_args.kwargs["initialdir"], str(panel.rte_run_folder))
                with target.open(encoding="utf-8-sig", newline="") as handle:
                    self.assertEqual(list(csv.DictReader(handle)), rows)

    def test_a_failed_zero_dialog_opens_outside_the_drain(self):
        h = self.make(span=("0", "10", "10"), round_trip=False)
        self.outside_drain(h)

        def refused():
            h.log.append(("kepco", "force_off", threading.get_ident()))
            raise RuntimeError("KEPCO 4886 zero/OFF not confirmed")
        h.kepco.force_output_off = refused
        self.start(h)
        h.showerror.assert_called_once()
        self.assertEqual(h.showerror.call_args.args[0], "SHV outputs")
        self.assertFalse(h.panel.ppms_field_scan_running)

    def test_a_failed_scan_dialog_opens_outside_the_drain(self):
        h = self.make_mode("Field", ("0", "1", "1"))
        self.outside_drain(h)
        h.gauss.read_field = Mock(side_effect=RuntimeError("LS475 read failed"))
        self.start(h)
        h.showerror.assert_called_once()
        self.assertEqual(h.showerror.call_args.args[0], "SHV scan")
        self.assertTrue(h.showerror.call_args.args[1].endswith("At the end: 6221 zeroed; KEPCO zeroed"))

    def test_a_refused_angle_start_dialog_opens_outside_the_drain(self):
        sim = rte_stage.Sim()
        sim.inputs = 1 << 4          # a limit input on at rest: a plain refusal
        h = self.make(sim=sim)
        self.outside_drain(h)
        self.start(h)
        h.showerror.assert_called_once()
        self.assertEqual(h.showerror.call_args.args[0], "SHV")
        self.assertIn("a limit input is on at rest", h.showerror.call_args.args[1])
        self.assertTrue(h.panel.ppms_field_scan_status_var.get().startswith("Angle scan not started:"))


# ---- the red Set stage 0° (user, 2026-10-18), offline: its colours by state ---------------------
class RedButtonTests(unittest.TestCase):
    class Button:
        def __init__(self):
            self.options = {"state": "normal"}

        def cget(self, name):
            return self.options[name]

        def configure(self, **options):
            self.options.update(options)

    def test_red_while_it_can_be_pressed_grey_while_locked(self):
        panel = rte_tests.RTE.make_worker(self)
        button = self.Button()
        panel.rte_red_buttons = [button]
        panel._rte_paint_red_buttons()
        self.assertEqual({k: button.options[k] for k in ("bg", "fg", "activebackground", "activeforeground")},
                         {"bg": "#c62828", "fg": "white", "activebackground": "#8e0000", "activeforeground": "white"})
        button.configure(state="disabled")
        panel._rte_paint_red_buttons()
        self.assertNotEqual(button.options["bg"], "#c62828", "a locked button does not look pressable")
        self.assertEqual(button.options["state"], "disabled")
        button.configure(state="normal")
        panel._rte_paint_red_buttons()
        self.assertEqual(button.options["bg"], "#c62828")

    def test_the_stand_in_page_keeps_its_ttk_button(self):
        panel = ete.build_page()
        self.assertEqual(panel.rte_stage_zero_button.options.get("text"), "Set stage 0°")
        self.assertNotIn("rte_red_buttons", panel.__dict__)
        self.assertIn((panel.rte_stage_zero_button, "normal"), panel.rte_edit_widgets)


# ---- C1-1: the MFLI adapters --------------------------------------------------------------
class MFLIAdapterTests(unittest.TestCase):
    SERVERS = ([("127.0.0.1", 8004)], ["127.0.0.1:8004 open"])

    def test_the_native_adapters_lack_the_double_calls(self):
        for adapter in (app.LabOneCAPIAdapter, app.LabOneDotNetAdapter):
            with self.subTest(adapter=adapter.__name__):
                self.assertEqual([name for name in app.NONLOCAL_MFLI_DAQ_METHODS
                                  if not callable(getattr(adapter, name, None))], ["setDouble", "getDouble"])

    def test_the_list_holds_every_call_on_the_session(self):
        called = set(re.findall(r"daq\.([A-Za-z]+)\(", inspect.getsource(app.NonLocalACController)))
        # listNodes: the Core path's own daq, before it is the session.
        self.assertEqual(called - {"listNodes"}, set(app.NONLOCAL_MFLI_DAQ_METHODS))

    def connect(self, c_api=None):
        log = []
        controller = app.NonLocalACController(log.append)
        daq = Mock()
        daq.listNodes.return_value = ["/dev4414/demods"]
        daq.getString.return_value = "18.12"
        core = types.SimpleNamespace(ziDAQServer=Mock(return_value=daq))

        def refuse(*_args, **_kwargs):
            raise AssertionError("an adapter without setDouble / getDouble was loaded")
        guards = [patch.object(app.sys, "platform", "win32"),
                  patch.object(app.NonLocalACController, "_mfli_reachable_servers", Mock(return_value=self.SERVERS)),
                  patch.object(app, "acquire_instrument_lease"), patch.object(app, "release_instrument_lease"),
                  patch.object(app.LabOneDotNetAdapter, "__init__", refuse), patch.object(app, "zi_core", core)]
        guards.append(patch.object(app.LabOneCAPIAdapter, "__init__", refuse) if c_api is None
                      else patch.object(app, "LabOneCAPIAdapter", c_api))
        for guard in guards:
            guard.start()
        try:
            identity = controller.connect_mfli("MF-DEV4414")
        finally:
            for guard in reversed(guards):
                guard.stop()
        return controller, identity, log

    def test_on_windows_they_are_skipped_and_core_connects(self):
        controller, identity, log = self.connect()
        self.assertEqual(identity, "Zurich Instruments MFLI · dev4414 · LabOne 18.12")
        self.assertEqual(controller.mfli_props["connection"], "current-python-core")
        for label in ("LabOne C API", "LabOne .NET"):
            self.assertIn(f"Zurich MFLI {label}: skipped, it has no setDouble, getDouble (the MFLI set-up and reads "
                          "need them)", log)

    def test_an_adapter_with_every_call_is_still_used(self):
        class Complete:
            label = ""
            api_dll = "ziAPI-win64.dll"

            def __init__(self, host, port, level):
                pass

            def getSample(self, path):
                return {"x": 0.0, "y": 0.0, "frequency": 317.3, "phase": 0.0}

            def getString(self, path):
                return "18.12"
        for name in app.NONLOCAL_MFLI_DAQ_METHODS:
            if not hasattr(Complete, name):
                setattr(Complete, name, lambda self, *args: None)
        controller, identity, log = self.connect(Complete)
        self.assertEqual(controller.mfli_props["connection"], "current-python-labone-c-api")
        self.assertFalse([line for line in log if "LabOne C API: skipped" in line])


# ---- C1-3: the PNA connect ------------------------------------------------------------------------
class PNAConnectTests(unittest.TestCase):
    class Instrument:
        def __init__(self, identity, fails=None):
            self.identity, self.fails, self.sent, self.closed = identity, fails, [], False

        def query(self, command):
            self.sent.append(command)
            return self.identity

        def write(self, command):
            self.sent.append(command)
            if command == self.fails:
                raise RuntimeError("VI_ERROR_TMO")

        def close(self):
            self.closed = True

    def connect(self, instrument, resource):
        manager = Mock()
        manager.open_resource.return_value = instrument
        controller = app.STFMRPNAFMRController(lambda _text: None)
        with patch.object(app, "pyvisa", Mock()), \
                patch.object(app, "acquire_shared_visa_manager", return_value=manager), \
                patch.object(app, "release_shared_visa_manager"):
            with self.assertRaises(RuntimeError):
                controller.connect(resource)
        self.assertFalse(controller.connected)
        self.assertTrue(instrument.closed)

    def test_an_unidentified_device_gets_nothing_but_idn(self):
        instrument = self.Instrument("KEITHLEY INSTRUMENTS INC.,MODEL 2400,1234,C30")
        self.connect(instrument, "GPIB0::31::INSTR")
        self.assertEqual(instrument.sent, ["*IDN?"])

    def test_an_identified_pna_still_gets_outp_off_on_a_failure(self):
        instrument = self.Instrument("Agilent Technologies,N5242A,MY1234,A.09.90", fails="FORM:DATA ASC,0")
        self.connect(instrument, "GPIB0::32::INSTR")
        self.assertEqual(instrument.sent, ["*IDN?", "OUTP OFF", "FORM:DATA ASC,0", "OUTP OFF"])


# ---- C1-5: the LabOne .NET DLL search and its handle ----------------------------------------------
class DotNetSearchTests(unittest.TestCase):
    def test_a_bad_candidate_does_not_end_the_search(self):
        with tempfile.TemporaryDirectory() as bad, tempfile.TemporaryDirectory() as root:
            (Path(bad) / "ziDotNET-win64.dll").write_bytes(b"")     # its Core DLL is missing
            good = Path(root) / "Zurich Instruments/LabOne/API/DotNet"
            good.mkdir(parents=True)
            for name in ("ziDotNET-win64.dll", "ziDotNETCore-win64.dll"):
                (good / name).write_bytes(b"")
            with patch.dict(os.environ, {"MOKE_LABONE_DOTNET_API": bad, "ProgramW6432": root}), \
                    patch.object(app.sys, "maxsize", 2 ** 63 - 1):
                self.assertEqual(app.LabOneDotNetAdapter._find_dotnet_api(), (good, good / "ziDotNET-win64.dll"))
                with patch.dict(os.environ, {"ProgramW6432": bad}):
                    with self.assertRaisesRegex(RuntimeError, r"ziDotNETCore-win64\.dll is missing from the same "
                                                              r"directory\. Checked: "):
                        app.LabOneDotNetAdapter._find_dotnet_api()

    def test_a_failed_create_instance_closes_the_dll_directory(self):
        folder = tempfile.mkdtemp()
        self.addCleanup(lambda: sys.path.remove(folder) if folder in sys.path else None)
        self.addCleanup(os.rmdir, folder)
        handle = Mock()
        assembly = Mock()
        clr = types.ModuleType("clr")
        clr.AddReference = Mock(return_value=assembly)
        system = types.ModuleType("System")
        system.Activator = Mock()
        system.Activator.CreateInstance.side_effect = RuntimeError("no CLR host")
        system.Enum = Mock()
        with patch.object(app.sys, "platform", "win32"), patch.dict(sys.modules, {"clr": clr, "System": system}), \
                patch.object(app.LabOneDotNetAdapter, "_find_dotnet_api",
                             return_value=(Path(folder), Path(folder) / "ziDotNET-win64.dll")), \
                patch.object(app.os, "add_dll_directory", Mock(return_value=handle), create=True):
            with self.assertRaisesRegex(RuntimeError, "Failed to create the LabOne .NET API object.*no CLR host"):
                app.LabOneDotNetAdapter("127.0.0.1", 8004, 5)
        handle.close.assert_called_once()


# ---- C2-7: the camera still releasing ---------------------------------------------------------------
class FakeCamera:
    def __init__(self, wait):
        self.wait_for_new_image = wait
        self.calls = []
        self.exposure_time, self.configuration = None, None

    def default_configuration(self):
        pass

    def record(self, *_args):
        self.calls.append("record")

    def stop(self):
        self.calls.append("stop")

    def close(self):
        self.calls.append("close")

    def image(self, *_args):
        raise AssertionError("no frame in this test")


class CameraReleaseTests(unittest.TestCase):
    def worker(self, camera, join_s):
        stack = [patch.object(app, "pco", types.SimpleNamespace(Camera=lambda: camera), create=True),
                 patch.object(app, "CAMERA_WAIT_SLICE_S", 0.05), patch.object(app, "CAMERA_STOP_JOIN_S", join_s)]
        for guard in stack:
            guard.start()
            self.addCleanup(guard.stop)
        worker = app.CameraAcquisitionWorker(lambda _text: None)
        worker.start()
        return worker

    def test_a_wait_pco_can_bound_lets_the_thread_end_at_once(self):
        slices = []

        def wait(delay=True, timeout=None):
            slices.append(timeout)
            time.sleep(timeout)
            raise TimeoutError(f"Timeout ({timeout} s) reached")
        camera = FakeCamera(wait)
        worker = self.worker(camera, 5.0)
        time.sleep(0.2)
        started = time.monotonic()
        self.assertTrue(worker.stop(), "ended")
        self.assertLess(time.monotonic() - started, 1.0)
        self.assertFalse(worker.releasing)
        self.assertEqual(set(slices), {0.05})
        self.assertEqual(camera.calls[-2:], ["stop", "close"])
        self.assertTrue(worker.errors.empty(), "a timed-out slice is no camera error")

    def test_a_wait_pco_cannot_bound_is_still_releasing(self):
        exposed = threading.Event()

        def wait(delay=True):
            exposed.wait(10)
            raise RuntimeError("the recorder was stopped")
        camera = FakeCamera(wait)
        worker = self.worker(camera, 0.1)
        self.assertFalse(worker.stop(), "the thread still holds the camera")
        self.assertTrue(worker.releasing)
        self.assertNotIn("close", camera.calls)
        exposed.set()
        worker.thread.join(5)
        self.assertFalse(worker.releasing)
        self.assertEqual(camera.calls[-1], "close")

    def app(self, releasing):
        a = app.CombinedControlApp.__new__(app.CombinedControlApp)
        a.closing = False
        self.afters = []
        a.root = AfterRoot(self.afters)
        a.log = []
        a.append_log = a.log.append
        a.magnet_scan_running = a.keithley_scan_running = a.k6221_scan_running = False
        a.camera_video_stopping = False
        a.camera_connect_button = None
        a.camera_window_status_var, a.difference_status_var = Var(""), Var("")
        for name in ("_set_connection_indicator", "_set_camera_dependent_controls", "clear_camera_roi",
                     "_update_camera_launcher_controls", "attach_camera"):
            setattr(a, name, Mock())
        self.worker_state = types.SimpleNamespace(releasing=releasing)
        a.camera_window = types.SimpleNamespace(video_recorder=None, close=Mock(), worker=self.worker_state)
        return a

    def test_disconnect_says_still_releasing_and_connect_waits(self):
        a = self.app(releasing=True)
        a.disconnect_camera()
        self.assertEqual(a.camera_window_status_var.get(), "Camera still releasing (waiting for its last frame)…")
        self.assertIsNone(a.camera_window)
        with patch.object(app.messagebox, "showwarning") as warning, \
                patch.object(app, "TkCameraWindow", Mock(side_effect=AssertionError("a camera was opened"))):
            a.connect_camera()
        warning.assert_called_once()
        self.assertEqual(warning.call_args.args[0], "Camera connection")
        self.assertIn("still being released", warning.call_args.args[1])
        # Its thread lets go: "not connected", and Connect goes ahead.
        self.afters.pop(0)()
        self.assertEqual(a.camera_window_status_var.get(), "Camera still releasing (waiting for its last frame)…")
        self.worker_state.releasing = False
        self.afters.pop(0)()
        self.assertEqual(a.camera_window_status_var.get(), "Camera not connected")
        self.assertIn("Camera released by its acquisition thread", a.log)
        made = Mock()
        with patch.object(app, "TkCameraWindow", Mock(return_value=made)):
            a.connect_camera()
        a.attach_camera.assert_called_once_with(made)

    def test_a_camera_that_let_go_in_time_is_not_connected_at_once(self):
        a = self.app(releasing=False)
        a.disconnect_camera()
        self.assertEqual(a.camera_window_status_var.get(), "Camera not connected")
        self.assertEqual(self.afters, [])


if __name__ == "__main__":
    unittest.main()
