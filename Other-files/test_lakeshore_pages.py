"""The Lake Shore 643 / 648 on the SHV, ST-FMR and FMR pages (user,
2026-10-21: "Add LS643 connection & control for SHV and STFMR. Add LS648
connection & control for FMR"; each page chooses its magnet supply, the KEPCO
4886 by default).

The real controller on test_lakeshore_magnet_supply's FakeLS64x (its ramp on
a virtual clock) inside each page's own window-free harness: SHV's
test_rte_angle_end_to_end (the real RTEPanel on stand-in widgets, a real
rte-control worker), the ST-FMR sweep harness and the FMR scans' bare page,
both with a real one-thread worker here.  Every supply command records its
thread.  No VISA session, no window."""
from concurrent.futures import ThreadPoolExecutor, wait
import re
import sys
import threading
import unittest
from pathlib import Path
from unittest import mock

_HERE = Path(__file__).resolve().parent
for _path in (_HERE / "test", _HERE.parent):
    if str(_path) not in sys.path:
        sys.path.insert(0, str(_path))

import test_rte_angle_end_to_end as ete
from test_lakeshore_magnet_supply import connected_supply
from test_ppms_motor_debug import app
import test_fmr_scan_run as fmr_run
import test_fmr_stfmr_review as review
from test_stfmr_realtime_recording import Variable, events as stfmr_events, harness, read_csv
from test_stfmr_realtime_recording import app as stfmr_app      # the ST-FMR harness's own module
from test_stfmr_sr830_tc_fixed_current import NoWindows

setUpModule, tearDownModule = ete.setUpModule, ete.tearDownModule


class WorkerExecutor:
    """A real one-thread worker that finishes each job before submit()
    returns, so a harness's Tk loop stays in order and every job runs off
    the test's (the Tk) thread."""

    def __init__(self, case, name):
        self.pool = ThreadPoolExecutor(max_workers=1, thread_name_prefix=name)
        case.addCleanup(self.pool.shutdown, wait=True)
        self.ident = self.pool.submit(threading.get_ident).result(timeout=30)
        self.fail = False

    def submit(self, fn, *args):
        if self.fail:
            raise RuntimeError("cannot schedule new futures after shutdown")
        future = self.pool.submit(fn, *args)
        wait([future], timeout=120)
        return future

    def shutdown(self, **_kwargs):
        pass


def at_least_seconds(text):
    """The seconds of "at least 12.3 s" (or minutes) in a status or log line."""
    match = re.search(r"at least ([0-9.]+) (s|min)", text)
    return float(match.group(1)) * (60.0 if match.group(2) == "min" else 1.0)


# ---- SHV: the LS643 ---------------------------------------------------------------------------
class ShvLakeShoreTests(ete.EndToEnd):
    events = staticmethod(ete.RunLogBase.events)

    def lakeshore(self, h, rate="2", choose=True, **fake_options):
        controller, fake, _clock = connected_supply("643", owner="SHV", **fake_options)
        self.addCleanup(app._INSTRUMENT_LEASES.clear)
        fake.journal = h.log
        h.panel.rte_controllers["lakeshore"] = h.ls = controller
        h.fake = fake
        h.panel.rte_magnet_rate.set(rate)
        if choose:
            h.panel.rte_magnet_choice_var.set("Lake Shore 643")
            h.panel._rte_magnet_choice_changed()
            self.assertTrue(h.panel._rte_lakeshore_chosen(), h.showerror.call_args)
        # The LS475 reads, with where the supply's current was then.
        h.fields_at = []
        read = h.gauss.read_field

        def read_field():
            h.fields_at.append(fake.current)
            return read()
        h.gauss.read_field = read_field
        return controller, fake

    def field_scan(self, h, span=("-1", "1", "1")):
        with mock.patch.object(app.CombinedControlApp, "_draw_nonlocal_series"):
            h.panel.ppms_simple_mode.set("Field")
            h.panel._rte_mode_changed()
        for variable, value in zip(h.panel.ppms_simple_range, span):
            variable.set(value)
        h.panel.rte_round_trip.set(False)

    def test_an_angle_scan_holds_the_lakeshore_current_then_ramps_it_to_zero(self):
        h = self.make()
        controller, fake = self.lakeshore(h)
        self.start(h)
        panel = h.panel
        h.showerror.assert_not_called()
        question = h.askyesno.call_args.args[1]
        self.assertIn("the LS643 is set once to +1.5 A (ramped at 2 A/s)", question)
        self.assertIn("The LS643 ramps at 2 A/s", question)
        self.assertIn("then the LS643 are zeroed", question)
        # The KEPCO is never sent anything; the LS643 gets its rate, the held
        # current and, at the end, 0 A.
        self.assertEqual([entry for entry in h.log if entry[0] == "kepco"], [])
        self.assertEqual(fake.sent(), ["RATE 2.0000", "SETI 1.500", "SETI 0.000"])
        # The ramp is at its setpoint before the LS475 settle starts.
        self.assertAlmostEqual(h.fields_at[0], 1.5, places=6)
        # Every supply command on the rte-control worker, 50 ms apart.
        self.assertEqual({entry[-1] for entry in h.log}, {h.worker})
        self.assertTrue(fake.gaps_kept())
        rows = self.rows(h)
        self.assertEqual({(r["magnet_supply"], r["kepco_programmed_current_a"], r["kepco_voltage_limit_v"])
                          for r in rows}, {("LS643", "1.5", "")})
        for row in rows:
            self.assertAlmostEqual(float(row["magnet_measured_current_a"]), 1.5, places=3)
        self.assertEqual(list(rows[0])[-2:], ["magnet_supply", "magnet_measured_current_a"])
        self.assertEqual(panel.ppms_field_scan_status_var.get(), "Completed; LS643 and 6221 zeroed; stage at +0.000°")
        self.assertLessEqual(abs(fake.current), 0.01)
        self.assertFalse(controller.output_on)
        events = self.events(h)
        prepared = events[0]["magnet_supply"]
        self.assertEqual((prepared["name"], prepared["model"], prepared["rate_a_s"], prepared["program_max_a"],
                          prepared["identity"]), ("LS643", "LS643", 2.0, 40.0, "LSCI,MODEL643,1234567,1.0/1.0"))
        zeroed = next(e for e in events if e["event"] == "outputs_zeroed")
        self.assertEqual((zeroed["6221"], zeroed["kepco"], zeroed["magnet_supply"]), ("zeroed", "zeroed", "LS643"))
        held = next(e for e in events if e["event"] == "field_programmed")
        self.assertEqual((held["magnet_supply"], held["held"]), ("LS643", True))
        self.assertAlmostEqual(held["measured_current_a"], 1.5, places=6)

    def test_a_field_scan_steps_the_lakeshore_and_counts_the_ramps(self):
        h = self.make()
        controller, fake = self.lakeshore(h)
        self.field_scan(h)
        self.start(h)
        h.showerror.assert_not_called()
        self.assertEqual(fake.sent(), ["RATE 2.0000", "SETI -1.000", "SETI 0.000", "SETI 1.000", "SETI 0.000"])
        # Each step's LS475 settle starts at that step's current.
        settle = app.STFMR_FIELD_SETTLE_REQUIRED_SAMPLES
        self.assertEqual([round(h.fields_at[i], 6) for i in range(0, len(h.fields_at), settle + 2)][:3],
                         [-1.0, 0.0, 1.0])
        self.assertEqual({entry[-1] for entry in h.log}, {h.worker})
        rows = self.rows(h)
        self.assertEqual([float(r["kepco_programmed_current_a"]) for r in rows], [-1.0, 0.0, 1.0])
        self.assertEqual(h.panel.ppms_field_scan_status_var.get(), "Completed; LS643 and 6221 zeroed")
        # The estimate counts the ramps: from 0 A through -1, 0, +1 A and
        # back to 0 A at 2 A/s, 2 s, and LAKESHORE_SETPOINT_OVERHEAD_S of
        # read-backs per setpoint.
        line = next(text for text in h.logged if text.startswith("SHV Field scan;"))
        self.assertIn("LS643 ramps at 2 A/s", line)
        with_ramps = at_least_seconds(line)
        expected = app.lakeshore_scan_ramp_seconds([0.0, -1.0, 0.0, 1.0, 0.0], 2.0)
        self.assertAlmostEqual(expected, 2.0 + 4 * app.LAKESHORE_SETPOINT_OVERHEAD_S)
        other = self.make()
        self.field_scan(other)
        other.panel.rte_controllers["kepco"] = other.kepco
        self.start(other)
        without = at_least_seconds(next(text for text in other.logged if text.startswith("SHV Field scan;")))
        # The line shows whole seconds.
        self.assertAlmostEqual(with_ramps - without, expected, delta=1.0)

    def test_an_angle_field_scan_ramps_each_outer_field_before_its_sweep(self):
        h = ete.OuterScanTests.make_outer(self, "Angle+Field", ("0", "2", "1"))
        controller, fake = self.lakeshore(h)
        self.start(h)
        h.showerror.assert_not_called()
        question = h.askyesno.call_args.args[1]
        self.assertIn("3 LS643 steps from +0 A to +2 A (ramped at 2 A/s)", question)
        self.assertIn("it ramps there and the step waits until RDGI? is at it", question)
        self.assertIn("(the turning counted, the move to the first angle not). The LS643 ramps at 2 A/s", question)
        # The first outer field is 0 A, where the supply already is: nothing
        # sent for it (review, 2026-10-21).
        self.assertEqual(fake.sent(), ["RATE 2.0000", "SETI 1.000", "SETI 2.000", "SETI 0.000"])
        # Each outer field is reached before its settle and its angle sweep.
        settle = app.STFMR_FIELD_SETTLE_REQUIRED_SAMPLES
        per_step = settle + 2 * 2          # the settle, then two angles' before / after reads
        self.assertEqual([round(h.fields_at[i], 6) for i in range(0, len(h.fields_at), per_step)], [0.0, 1.0, 2.0])
        rows = self.rows(h)
        self.assertEqual([float(r["kepco_programmed_current_a"]) for r in rows], [0.0, 0.0, 1.0, 1.0, 2.0, 2.0])
        self.assertEqual({entry[-1] for entry in h.log}, {h.worker})
        self.assertEqual(h.panel.ppms_field_scan_status_var.get(), "Completed; LS643 and 6221 zeroed; stage at +10.000°")

    def test_a_repeated_setpoint_with_an_event_style_opst_is_not_waited_for(self):
        # OPST? reads and clears (review, 2026-10-21): the second -1 A of a
        # two-loop scan at -1 A is already there, so no SETI and no wait for a
        # ramp done that never comes.
        h = self.make()
        controller, fake = self.lakeshore(h, opst_event=True)
        self.field_scan(h, ("-1", "-1", "1"))
        h.panel.ppms_field_scan_loop_var.set("2")
        h.panel.rte_round_trip.set(True)
        self.start(h)
        h.showerror.assert_not_called()
        self.assertEqual(fake.sent(), ["RATE 2.0000", "SETI -1.000", "SETI 0.000"])
        self.assertEqual([float(r["kepco_programmed_current_a"]) for r in self.rows(h)], [-1.0, -1.0])
        self.assertEqual(h.panel.ppms_field_scan_status_var.get(), "Completed; LS643 and 6221 zeroed")

    def test_an_old_stop_request_does_not_stop_the_next_apply(self):
        h = self.make()
        controller, fake = self.lakeshore(h)
        controller.request_stop()           # left by a Zero / OFF nobody's zero cleared
        h.panel.rte_drive_target.set("2")
        h.panel._rte_apply_drive()
        self.pump(h)
        h.showerror.assert_not_called()
        self.assertAlmostEqual(fake.current, 2.0, places=6)

    def test_a_stop_during_a_ramp_sends_stop_and_the_end_zeroes(self):
        h = self.make()
        controller, fake = self.lakeshore(h, rate="1")
        self.field_scan(h, ("-3", "3", "3"))
        fake.on_advance = lambda f: h.panel.ppms_field_scan_stop_event.set() if f.current <= -1.0 else None
        self.start(h)
        sent = fake.sent()
        self.assertEqual(sent[:3], ["RATE 1.0000", "SETI -3.000", "STOP"])
        self.assertEqual(sent[3:], ["SETI 0.000"], "no setpoint after the STOP but the end's zero")
        self.assertEqual(self.rows(h), [])
        self.assertTrue(h.panel.ppms_field_scan_status_var.get().startswith("Stopped; LS643 and 6221 zeroed"))
        self.assertLessEqual(abs(fake.current), 0.01)
        self.assertEqual({entry[-1] for entry in h.log}, {h.worker})

    def test_an_error_during_a_ramp_fails_the_scan_and_the_end_still_zeroes(self):
        h = self.make()
        controller, fake = self.lakeshore(h)
        self.field_scan(h, ("-3", "3", "3"))
        fake.on_advance = lambda f: setattr(f, "hardware", 8) if f.current <= -1.0 else None
        self.start(h)
        sent = fake.sent()
        self.assertEqual(sent[:3], ["RATE 2.0000", "SETI -3.000", "STOP"])
        self.assertEqual(sent[3:], ["SETI 0.000"])
        status = h.panel.ppms_field_scan_status_var.get()
        self.assertTrue(status.startswith("Failed: LS643 ramp to -3 A ended: output over voltage"), status)
        self.assertLessEqual(abs(fake.current), 0.01, "the zero is never ended by an error bit")
        message = h.showerror.call_args.args[1]
        self.assertIn("At the end: 6221 zeroed; LS643 zeroed", message)

    def test_a_zero_that_does_not_arrive_says_check_it(self):
        h = self.make()
        controller, fake = self.lakeshore(h)
        self.field_scan(h, ("1", "1", "1"))
        fake.freeze_on_zero = True
        self.start(h)
        self.assertEqual(h.panel.ppms_field_scan_status_var.get(), "Completed; 6221 zeroed; LS643 zero FAILED: check it")
        self.assertIn("CHECK IT", h.showerror.call_args.args[1])
        self.assertTrue(controller.output_on)
        zeroed = next(e for e in self.events(h) if e["event"] == "outputs_zeroed")
        self.assertTrue(zeroed["kepco"].startswith("zero FAILED: LS643 zero not confirmed"), zeroed)
        # The choice stays locked to it while its output is not at zero.
        h.panel.rte_magnet_choice_var.set("KEPCO 4886")
        h.panel._rte_magnet_choice_changed()
        self.assertTrue(h.panel._rte_lakeshore_chosen())
        self.assertIn("zero it first (LS643 to 0 A)", h.showerror.call_args.args[1])

    def test_the_kepco_stays_the_default_and_the_lakeshore_gets_nothing(self):
        h = self.make()
        controller, fake = self.lakeshore(h, choose=False)
        self.assertFalse(h.panel._rte_lakeshore_chosen())
        self.start(h)
        h.showerror.assert_not_called()
        self.assertEqual(fake.sent(), [])
        self.assertEqual([e[2:5] for e in h.log if e[:2] == ("kepco", "set_output")], [("CURR", 1.5, 5.0)])
        self.assertEqual(self.names(h, "kepco")[-1], ("kepco", "force_off"))
        rows = self.rows(h)
        self.assertEqual({(r["magnet_supply"], r["magnet_measured_current_a"], r["kepco_voltage_limit_v"])
                          for r in rows}, {("KEPCO 4886", "", "5.0")})
        self.assertIn("the KEPCO is set once to +1.5 A (voltage limit 5 V)", h.askyesno.call_args.args[1])

    def test_the_choice_needs_the_supply_it_leaves_at_zero_and_an_idle_page(self):
        h = self.make()
        h.kepco.output_owned = True
        controller, fake = self.lakeshore(h, choose=False)
        h.panel.rte_magnet_choice_var.set("Lake Shore 643")
        h.panel._rte_magnet_choice_changed()
        self.assertFalse(h.panel._rte_lakeshore_chosen())
        self.assertEqual(h.panel.rte_magnet_choice_var.get(), "KEPCO 4886", "the choice put back")
        self.assertIn("The KEPCO output is on (or not confirmed at zero): zero it first (KEPCO Zero / OFF)",
                      h.showerror.call_args.args[1])
        h.kepco.output_owned = False
        h.panel.ppms_field_scan_running = True
        h.panel.rte_magnet_choice_var.set("Lake Shore 643")
        h.panel._rte_magnet_choice_changed()
        self.assertFalse(h.panel._rte_lakeshore_chosen())
        self.assertIn("SHV is busy", h.showerror.call_args.args[1])
        h.panel.ppms_field_scan_running = False
        h.panel.rte_magnet_choice_var.set("Lake Shore 643")
        h.panel._rte_magnet_choice_changed()
        self.assertTrue(h.panel._rte_lakeshore_chosen())
        # The settings and the scan box follow the choice.
        self.assertEqual(h.panel.rte_limit_label.options["text"], "Rate A/s")
        self.assertIs(h.panel.rte_limit_entry.options["textvariable"], h.panel.rte_magnet_rate)
        self.assertEqual(h.panel.rte_apply_button.options["text"], "Apply LS643")
        self.assertEqual(h.panel.rte_magnet_off_button.options["text"], "LS643 to 0 A")
        self.assertTrue(all(w.grid_removed for w in h.panel.rte_supply_widgets["kepco"]))
        self.assertFalse(any(w.grid_removed for w in h.panel.rte_supply_widgets["lakeshore"]))
        self.assertEqual(h.panel.rte_scan_note.get(), "Angle: Stage °; field: LS643 current A")

    def test_beyond_the_limits_nothing_is_sent(self):
        h = self.make()
        controller, fake = self.lakeshore(h, limit=(20.0, 5.0))
        h.panel.rte_drive_target.set("25")
        self.start(h)
        self.assertIn("above the LS643's LIMIT? maximum current of 20 A", h.showerror.call_args.args[1])
        h.panel.rte_drive_target.set("1")
        h.panel.rte_magnet_rate.set("6")
        self.start(h)
        self.assertIn("LIMIT? maximum rate of 5 A/s", h.showerror.call_args.args[1])
        h.panel.rte_magnet_rate.set("11")
        h.panel._rte_apply_drive()
        self.assertIn("Rate A/s must be from 0.0001 to 10 A/s", h.showerror.call_args.args[1])
        self.assertEqual(fake.sent(), [])
        h.askyesno.assert_not_called()

    def test_apply_ramps_on_the_worker_and_zero_stops_it_and_ramps_to_zero(self):
        h = self.make()
        controller, fake = self.lakeshore(h)
        h.panel.rte_drive_target.set("4")
        h.panel._rte_apply_drive()
        self.pump(h)
        h.showerror.assert_not_called()
        self.assertAlmostEqual(fake.current, 4.0, places=6)
        self.assertEqual(h.panel.rte_drive_live.get(), "+4.000 A (set +4.000) 2.00 V")
        # A Zero during the next ramp: the request is seen at the next poll,
        # STOP is sent, then the zero ramps it to 0 A.
        h.panel.rte_drive_target.set("8")
        fake.on_advance = lambda f: controller.request_stop() if f.current >= 5.0 else None
        h.panel._rte_apply_drive()
        self.pump(h)
        fake.on_advance = None
        self.assertEqual(fake.sent()[-1], "STOP")
        h.panel._rte_zero()
        self.pump(h)
        self.assertEqual(fake.sent()[-1], "SETI 0.000")
        self.assertLessEqual(abs(fake.current), 0.01)
        self.assertEqual(h.panel.rte_drive_live.get(), "LS643: at 0 A (zeroed)")
        self.assertEqual({entry[-1] for entry in h.log}, {h.worker})

    def test_connect_runs_on_the_worker_and_says_why_it_is_not_armed(self):
        from test_lakeshore_magnet_supply import Clock, FakeLS64x, Manager
        h = self.make()
        clock = Clock()
        fake = FakeLS64x(clock, "643", xpgm=1, current=2.0)
        controller = h.panel.rte_controllers["lakeshore"]
        controller.clock, controller.sleep = clock.monotonic, clock.sleep
        self.addCleanup(app._INSTRUMENT_LEASES.clear)
        warning = mock.Mock()
        with mock.patch.object(app, "pyvisa", object()), \
                mock.patch.object(app, "acquire_shared_visa_manager", return_value=Manager(fake)), \
                mock.patch.object(app.messagebox, "showwarning", warning):
            h.panel._rte_connect("lakeshore")
            self.pump(h)
        self.assertTrue(controller.connected)
        self.assertEqual(set(fake.threads), {h.worker}, "Connect's *IDN? and queries on the worker")
        self.assertEqual(fake.sent(), [])
        self.assertEqual(h.panel.rte_connections["lakeshore"].get(), "NOT ARMED")
        message = warning.call_args.args[1]
        self.assertIn("NOT armed", message)
        self.assertIn("programming mode is External (XPGM? 1), not Internal", message)
        self.assertIn("Its output is NOT at zero: RDGI? +2.000 A", message)
        # Chosen with its output on: Apply refuses (not armed), nothing sent.
        self.assertFalse(controller.output_on is False)
        h.panel.rte_magnet_choice = "Lake Shore 643"
        h.panel._rte_apply_drive()
        self.pump(h)
        self.assertIn("not armed", h.showerror.call_args.args[1])
        self.assertEqual(fake.sent(), [])

    def test_closing_the_program_ramps_it_to_zero_on_the_worker(self):
        h = self.make()
        controller, fake = self.lakeshore(h)
        h.panel.rte_drive_target.set("3")
        h.panel._rte_apply_drive()
        self.pump(h)
        with mock.patch.object(app, "release_shared_visa_manager"):
            h.panel.safe_close_ppms()
            h.executor.shutdown(wait=True)
        self.assertEqual(fake.sent()[-1], "SETI 0.000")
        self.assertLessEqual(abs(fake.current), 0.01)
        self.assertTrue(fake.closed)
        self.assertFalse(controller.connected)
        self.assertEqual({entry[-1] for entry in h.log}, {h.worker})


# ---- ST-FMR: the LS643 ------------------------------------------------------------------------
class StfmrLakeShoreTests(NoWindows):
    def sweep(self, rate="2", **fake_options):
        h = harness(self.tmp.name)
        h.stfmr_lockin_executor = WorkerExecutor(self, "stfmr-lockin")
        controller, fake = connected_supply("643", owner="STFMR", module=stfmr_app, **fake_options)[:2]
        self.addCleanup(stfmr_app._INSTRUMENT_LEASES.clear)
        h.stfmr_lockin_controllers["lakeshore"] = controller
        h.stfmr_lockin_magnet_choice = "LS643"
        h.stfmr_lockin_magnet_rate_var = Variable(rate)
        h.ls, h.fake = controller, fake
        return h

    def test_a_sweep_ramps_every_point_on_the_worker_and_zeroes_at_the_end(self):
        h = self.sweep()
        h.start_stfmr_lockin_scan()
        recorder = h.stfmr_lockin_recorder
        question = self.confirm.call_args.args[1]
        self.assertIn("s of LS643 ramps at 2 A/s", question)
        self.assertIn("the LS643 is zeroed and the SMB100A RF switched off", question)
        h.root.drain()
        self.error.assert_not_called()
        kepco = h.stfmr_lockin_controllers["kepco"]
        self.assertFalse([call for call in kepco.calls if call[0] in ("set_output", "set_scan_current", "zero",
                                                                      "force_off")])
        points = ["SETI -1.000", "SETI -0.500", "SETI 0.000", "SETI 0.500", "SETI 1.000"]
        self.assertEqual(h.fake.sent(), ["RATE 2.0000", *points, "SETI 0.000", *points, "SETI 0.000"])
        self.assertEqual(h.fake.page_threads(), {h.stfmr_lockin_executor.ident})
        rows = read_csv(recorder.csv_path)
        self.assertEqual({r["magnet_supply"] for r in rows}, {"LS643"})
        for row in rows:
            self.assertAlmostEqual(float(row["magnet_measured_current_a"]), float(row["current_a"]), places=3)
        history = stfmr_events(recorder)
        self.assertEqual(history[0]["settings"]["magnet_supply"]["name"], "LS643")
        self.assertEqual(history[0]["settings"]["magnet_supply"]["rate_a_s"], 2.0)
        zeroed = next(e for e in history if e["event"] == "outputs_zeroed")
        self.assertEqual((zeroed["LS643"], zeroed["SMB100A RF"]), ("zeroed", "zeroed"))
        self.assertTrue(history[-1]["status"].startswith("Completed; LS643 and RF zeroed; 6 points saved"),
                        history[-1]["status"])
        self.assertLessEqual(abs(h.fake.current), 0.01)

    def test_a_stop_during_a_ramp_ends_the_sweep_stopped_and_zeroed(self):
        h = self.sweep()
        h.fake.on_advance = lambda f: h.stfmr_lockin_stop_event.set() if f.current >= 0.2 else None
        h.start_stfmr_lockin_scan()
        h.root.drain()
        self.error.assert_not_called()
        sent = h.fake.sent()
        self.assertIn("STOP", sent)
        self.assertEqual(sent[sent.index("STOP") + 1:], ["SETI 0.000"], "only the end's zero after the STOP")
        self.assertTrue(h.stfmr_lockin_status_var.get().startswith("Stopped; LS643 and RF zeroed"),
                        h.stfmr_lockin_status_var.get())
        self.assertLessEqual(abs(h.fake.current), 0.01)

    def test_the_kepco_path_is_unchanged_by_default(self):
        h = self.sweep()
        h.stfmr_lockin_magnet_choice = stfmr_app.MAGNET_SUPPLY_KEPCO
        h.start_stfmr_lockin_scan()
        recorder = h.stfmr_lockin_recorder
        h.root.drain()
        self.assertEqual(h.fake.sent(), [])
        kepco = h.stfmr_lockin_controllers["kepco"]
        self.assertEqual({call[0] for call in kepco.calls}, {"output", "current", "zero"})
        rows = read_csv(recorder.csv_path)
        self.assertEqual({(r["magnet_supply"], r["magnet_measured_current_a"]) for r in rows}, {("KEPCO 4886", "")})


class StfmrNoWorkerTests(StfmrLakeShoreTests):
    def test_without_a_worker_the_end_never_zeroes_the_lakeshore_on_the_tk_thread(self):
        h = self.sweep()
        h.start_stfmr_lockin_scan()
        recorder = h.stfmr_lockin_recorder
        while not h.stfmr_lockin_rows:
            h.root.step()
        sent = list(h.fake.sent())
        h.stfmr_lockin_executor.fail = True
        h.stop_stfmr_lockin_scan()
        h.root.drain()
        self.assertEqual(h.fake.sent(), sent, "no SETI 0 from the Tk thread")
        self.assertEqual(h.fake.page_threads(), {h.stfmr_lockin_executor.ident})
        zeroed = next(e for e in stfmr_events(recorder) if e["event"] == "outputs_zeroed")
        self.assertEqual(zeroed["LS643"], stfmr_app.LAKESHORE_ZERO_NOT_RUN)
        self.assertIn("CHECK IT: LS643", self.error.call_args.args[1])


class StfmrPanelTests(StfmrLakeShoreTests):
    def test_apply_and_zero_off_ramp_on_the_worker(self):
        h = self.sweep()
        h.stfmr_lockin_manual_target_var = Variable("3")
        h.apply_stfmr_lockin_kepco_output()
        self.assertTrue(h._stfmr_lockin_magnet_jobs_pending(), "Start, a hand move and the choice wait for it")
        with self.assertRaisesRegex(RuntimeError, "in progress"):
            h._stfmr_lockin_require_4886_off()
        h.root.drain()
        self.error.assert_not_called()
        self.assertAlmostEqual(h.fake.current, 3.0, places=6)
        with self.assertRaisesRegex(RuntimeError, "The LS643 output is not at zero"):
            h._stfmr_lockin_require_4886_off()
        h.zero_stfmr_lockin_kepco_output()
        h.root.drain()
        self.assertLessEqual(abs(h.fake.current), 0.01)
        self.assertEqual(h.stfmr_lockin_status_var.get(), "LS643 at 0 A (zeroed)")
        h._stfmr_lockin_require_4886_off()
        self.assertEqual(h.fake.page_threads(), {h.stfmr_lockin_executor.ident})
        # Beyond the program's 40 A: refused before anything is sent.
        sent = list(h.fake.sent())
        h.stfmr_lockin_manual_target_var = Variable("41")
        h.apply_stfmr_lockin_kepco_output()
        self.assertIn("within ±40 A", self.error.call_args.args[1])
        self.assertEqual(h.fake.sent(), sent)


class StfmrStageInterlockTests(unittest.TestCase):
    """S1 / SF1 with the Lake Shores (user, 2026-10-21): the stage never
    turns while the chosen supply is not confirmed at zero, nor while any
    other page's supply (4886 or Lake Shore) is on."""

    def page(self, choice="LS643"):
        a = object.__new__(app.CombinedControlApp)
        kepco = mock.Mock(connected=True, output_owned=False)
        lakeshore = mock.Mock(connected=True, output_on=False, short_label="LS643")
        a.stfmr_lockin_controllers = {"kepco": kepco, "lakeshore": lakeshore}
        a.stfmr_lockin_magnet_choice = choice
        return a, kepco, lakeshore

    def test_the_chosen_lakeshore_must_be_connected_and_at_zero(self):
        a, _kepco, lakeshore = self.page()
        a._stfmr_lockin_require_4886_off()
        lakeshore.output_on = True
        with self.assertRaisesRegex(RuntimeError, "The LS643 output is not at zero"):
            a._stfmr_lockin_require_4886_off()
        lakeshore.output_on, lakeshore.connected = False, False
        with self.assertRaisesRegex(RuntimeError, "Connect the LS643 on this page"):
            a._stfmr_lockin_require_4886_off()
        lakeshore.connected = True
        a.stfmr_lockin_magnet_job_pending = 1
        with self.assertRaisesRegex(RuntimeError, "in progress"):
            a._stfmr_lockin_require_4886_off()
        a._stfmr_lockin_require_4886_off(pending=False)      # the worker's re-check reads flags only

    def test_another_pages_supply_on_refuses_the_move(self):
        a, kepco, _lakeshore = self.page()
        a.rte_panel = mock.Mock()
        a.rte_panel.__dict__["rte_controllers"] = {"lakeshore": mock.Mock(output_on=True), "kepco": None}
        with self.assertRaisesRegex(RuntimeError, "SHV page's LS643 output is not at zero"):
            a._stfmr_lockin_require_4886_off()
        # The FMR page's LS648 drives a different magnet (user, 2026-10-21,
        # after review): it never blocks this stage.
        a.rte_panel.__dict__["rte_controllers"] = {}
        a.stfmr_controllers = {"lakeshore": mock.Mock(output_on=True)}
        a._stfmr_lockin_require_4886_off()
        self.assertEqual(a._stfmr_lockin_other_lakeshore_on(), [])
        # With the LS643 chosen, this page's own 4886 counts as another supply.
        a.stfmr_controllers = {}
        kepco.output_owned = True
        with self.assertRaisesRegex(RuntimeError, "KEPCO 4886 output of the STFMR page is on"):
            a._stfmr_lockin_require_4886_off()

    def test_with_the_4886_chosen_its_own_lakeshore_on_refuses_the_move(self):
        a, _kepco, lakeshore = self.page(app.MAGNET_SUPPLY_KEPCO)
        a._stfmr_lockin_require_4886_off()
        lakeshore.output_on = True
        with self.assertRaisesRegex(RuntimeError, "this page's LS643 output is not at zero"):
            a._stfmr_lockin_require_4886_off()


# ---- FMR: the LS648 -----------------------------------------------------------------------------
class FmrBusyTests(fmr_run.FmrRun):
    """The review's B1 (2026-10-21): _set_stfmr_scan_busy had lost its def,
    and every FMR test stubbed it.  Here it is the page's own, on stand-in
    buttons: a KEPCO scan from Start to its end, and the LS648's Zero / OFF
    then Disconnect."""

    def page(self):
        a = self.make()
        del a._set_stfmr_scan_busy                      # the real method, not the harness's stub
        a.stfmr_mfli_controller = mock.Mock(mfli_connected=False)
        a.instrument_connection_indicators = {}
        a.stfmr_device_connect_buttons = {key: mock.Mock() for key in app.STFMR_DEFAULT_RESOURCES}
        a.stfmr_device_disconnect_buttons = {key: mock.Mock() for key in app.STFMR_DEFAULT_RESOURCES}
        a.stfmr_device_resource_widgets = {key: mock.Mock() for key in app.STFMR_DEFAULT_RESOURCES}
        for name in ("stfmr_apply_output_button", "stfmr_read_output_button", "stfmr_zero_output_button",
                     "stfmr_read_gauss_button", "stfmr_fmr_start_button", "stfmr_frequency_start_button"):
            setattr(a, name, mock.Mock())
        return a

    @staticmethod
    def last_state(button):
        return button.configure.call_args.kwargs["state"]

    def test_a_kepco_scan_runs_from_start_to_its_end_with_the_real_busy_state(self):
        a = self.page()
        self.drive(a, a.start_stfmr_fmr_scan)
        self.error.assert_not_called()
        self.assertEqual(a.stfmr_fmr_status_var.get(), "Completed; 4886 and RF zeroed; 6 points saved")
        self.assertEqual(self.last_state(a.stfmr_fmr_start_button), "normal", "idle again")
        self.assertEqual(self.last_state(a.stfmr_fmr_stop_button), "disabled")
        self.assertEqual(self.last_state(a.stfmr_apply_output_button), "normal")

    def test_the_lakeshore_zero_off_and_disconnect(self):
        a = self.page()
        errors = []

        def post_ui(callback):
            try:
                callback()
            except BaseException as error:      # a done callback's error is otherwise only logged
                errors.append(error)
                raise
        a.post_ui = post_ui
        self.addCleanup(lambda: self.assertEqual(errors, []))
        a.stfmr_executor = WorkerExecutor(self, "stfmr-control")
        controller, fake = connected_supply("648", owner="FMR", current=3.0)[:2]
        self.addCleanup(app._INSTRUMENT_LEASES.clear)
        a.stfmr_controllers["lakeshore"] = controller
        a.stfmr_magnet_choice, a.stfmr_closed = "LS648", False
        ls_row = (mock.Mock(), mock.Mock(), mock.Mock())
        a.stfmr_supply_rows = {"lakeshore": ls_row}
        a.stfmr_device_connection_vars = {"lakeshore": fmr_run.Var("Connected")}
        a.zero_stfmr_kepco_output()
        self.assertEqual(a.__dict__.get("stfmr_magnet_job_pending"), 0, "the job counted down")
        self.assertLessEqual(abs(fake.current), 0.05)
        self.assertEqual(self.last_state(a.stfmr_zero_output_button), "normal")
        self.assertEqual(self.last_state(ls_row[2]), "normal", "Disconnect usable again")
        with mock.patch.object(app, "release_shared_visa_manager"):
            a.disconnect_stfmr_device("lakeshore")
        self.assertFalse(controller.connected)
        self.assertEqual(a.stfmr_device_connection_vars["lakeshore"].get(), "Not connected")
        self.assertEqual(fake.page_threads(), {a.stfmr_executor.ident})



class FmrLakeShoreTests(fmr_run.FmrRun):
    def make_ls(self, rate=5.0, **fake_options):
        a = self.make()
        a.stfmr_executor = WorkerExecutor(self, "stfmr-control")
        controller, fake = connected_supply("648", owner="FMR", **fake_options)[:2]
        self.addCleanup(app._INSTRUMENT_LEASES.clear)
        a.stfmr_controllers["lakeshore"] = controller
        a.stfmr_magnet_choice = "LS648"
        a.stfmr_magnet_rate_a_s = rate
        a.stfmr_magnet_rate_var = fmr_run.Var(f"{rate:g}")
        a.stfmr_closed = False

        class Gauss:
            connected = True

            def read_field(self):
                return {"field": 300.0 * fake.current, "unit_code": 1, "unit": "G"}

        a.stfmr_controllers["gauss"] = Gauss()
        return a, controller, fake

    def test_a_field_scan_ramps_each_point_on_the_worker_and_zeroes_after_each_frequency(self):
        a, controller, fake = self.make_ls()
        self.drive(a, a.start_stfmr_fmr_scan, done=lambda: not a.stfmr_fmr_running
                   and not a.__dict__.get("stfmr_fmr_ending"))
        self.error.assert_not_called()
        self.assertIn("of LS648 ramps at 5 A/s", self.confirm.call_args.args[1])
        self.assertIn("the LS648 is ramped to 0 A and the PNA RF switched off", self.confirm.call_args.args[1])
        self.assertFalse([entry for entry in a.log if entry[0] == "4886"], "the 4886 is sent nothing")
        sweep = ["SETI -0.500", "SETI 0.000", "SETI 0.500"]
        self.assertEqual(fake.sent(), ["RATE 5.0000", *sweep, "SETI 0.000", *sweep, "SETI 0.000"])
        self.assertEqual(fake.page_threads(), {a.stfmr_executor.ident})
        rows = self.rows(self.folder)
        header = rows[0]
        self.assertEqual(header[-2:], ["magnet_supply", "magnet_measured_current_a"])
        self.assertEqual({row[-2] for row in rows[1:]}, {"LS648"})
        events = self.events(self.folder)
        self.assertEqual(events[0]["settings"]["outputs_zeroed_at_end"], ["LS648", "PNA RF"])
        self.assertEqual(events[0]["settings"]["magnet_supply"]["program_max_a"], 100.0)
        self.assertEqual({k: events[-2][k] for k in ("LS648", "PNA RF")}, {"LS648": "zeroed", "PNA RF": "zeroed"})
        self.assertEqual(a.stfmr_fmr_status_var.get(), "Completed; LS648 and RF zeroed; 6 points saved")
        self.assertLessEqual(abs(fake.current), 0.01)

    def test_zero_off_during_a_scan_stops_it_and_the_end_ramps_to_zero(self):
        a, controller, fake = self.make_ls()
        a.stfmr_controllers["vna"].on_read[2] = lambda: a.zero_stfmr_kepco_output()
        self.drive(a, a.start_stfmr_fmr_scan, done=lambda: not a.stfmr_fmr_running
                   and not a.__dict__.get("stfmr_fmr_ending"))
        self.assertTrue(a.stfmr_fmr_status_var.get().startswith("Stopped; LS648 and RF zeroed"),
                        a.stfmr_fmr_status_var.get())
        self.assertEqual(fake.sent()[-1], "SETI 0.000")
        self.assertLessEqual(abs(fake.current), 0.01)

    def test_apply_and_zero_off_ramp_on_the_worker(self):
        a, controller, fake = self.make_ls()
        a.stfmr_output_target_var = fmr_run.Var("-60")
        a.apply_stfmr_kepco_output()
        self.error.assert_not_called()
        self.assertAlmostEqual(fake.current, -60.0, places=6)
        self.assertEqual(a.stfmr_status_var.get(), "LS648 at -60 A; check the 475")
        self.assertEqual(a.stfmr_kepco_current_var.get(), "I: -60 A")
        a.zero_stfmr_kepco_output()
        self.assertLessEqual(abs(fake.current), 0.01)
        self.assertEqual(a.stfmr_kepco_output_var.get(), "At 0 A")
        self.assertEqual(fake.page_threads(), {a.stfmr_executor.ident})
        a.stfmr_output_target_var = fmr_run.Var("100.5")
        a.apply_stfmr_kepco_output()
        self.assertIn("within ±100 A", self.error.call_args.args[1])

    def test_without_a_worker_the_end_never_zeroes_the_lakeshore_on_the_tk_thread(self):
        a, controller, fake = self.make_ls()
        controller.ramp_to(2.0, 5.0)
        sent = list(fake.sent())
        a.stfmr_executor.fail = True
        results = []
        a._stfmr_zero_then(results.append)
        self.assertEqual(fake.sent(), sent)
        self.assertEqual(results[0]["LS648"], app.LAKESHORE_ZERO_NOT_RUN)
        self.assertEqual(results[0]["PNA RF"], "zeroed")

    def test_the_frequency_sweep_feeds_the_lakeshore_back_to_the_field(self):
        a, controller, fake = self.make_ls(rate=10.0)
        self.drive(a, a.start_stfmr_frequency_scan, done=lambda: not a.stfmr_frequency_running
                   and not a.__dict__.get("stfmr_fmr_ending"))
        self.error.assert_not_called()
        rows = self.rows(self.folder)
        currents = {round(float(row[rows[0].index("current_setpoint_a")]), 3) for row in rows[1:]}
        self.assertEqual(len(rows) - 1, 3)
        self.assertEqual(len(currents), 1)
        self.assertAlmostEqual(currents.pop(), 1.0, delta=0.02)          # 300 G at 300 G/A
        self.assertEqual(fake.page_threads(), {a.stfmr_executor.ident})
        self.assertEqual(fake.sent()[-1], "SETI 0.000")
        self.assertLessEqual(abs(fake.current), 0.01)
        self.assertEqual(a.stfmr_frequency_status_var.get(), "Completed; LS648 and RF zeroed; 3 points saved")


if __name__ == "__main__":
    unittest.main()
