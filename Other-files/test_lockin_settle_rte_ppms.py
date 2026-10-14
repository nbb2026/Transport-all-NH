"""RT-E and PPMS: every lock-in reading settles at least 7 x TC (user,
2026-10-14).

The user's rule: "every lock-in reading waits at least 7 x TC (99 % settled
at 12 dB/oct) after a change, on every page; Settle s still applies when
longer; the Start confirmation shows the wait".  On RT-E (Field, Current,
Angle) and PPMS (Temperature, Field, Angle, Current and the combined scans,
macro SCAN lines included) the wait before each point's reading is
max(Settle s, 7 x the slowest time constant among the lock-ins measured).

Checked here: the rule (SR830 only, Zurich only, both, Settle s longer);
the wait before every reading in each RT-E mode and each PPMS mode, the
first point of each harmonic pass included, while the PPMS temperature and
field stability waits keep their criteria; the Start confirmations, status
lines and duration estimates; the CSV columns, appended last.

No Tk window and no instrument: the scan workers run on stand-ins with a
clock that moves only when the scan waits.  Every message box is mocked,
and creating a Tk root fails the test.
"""
import tempfile
import threading
import tkinter
import tkinter.messagebox
import types
import unittest
from unittest import mock

from test_ppms_motor_debug import app
from test_ppms_two_panels import Var
import test_rte_panel as rte_tests


class NoWindows(unittest.TestCase):
    def setUp(self):
        self.error = mock.Mock()
        unexpected = mock.Mock(side_effect=AssertionError("an unexpected dialog opened"))
        patches = [
            mock.patch("tkinter.Tk", side_effect=AssertionError("a test tried to open a Tk window")),
            mock.patch.object(tkinter.messagebox, "showerror", self.error),
        ]
        for name in ("showwarning", "showinfo", "askyesno", "askokcancel", "askyesnocancel",
                     "askquestion", "askretrycancel"):
            patches.append(mock.patch.object(tkinter.messagebox, name, unexpected))
        for patcher in patches:
            patcher.start()
            self.addCleanup(patcher.stop)


class Clock:
    """time.monotonic for a scan: it moves only when the scan waits."""

    def __init__(self):
        self.now = 1000.0

    def __call__(self):
        return self.now


class ClockEvent:
    """A scan's stop event that is never set: each wait is logged and moves
    the clock on."""

    def __init__(self, clock, log):
        self.clock, self.log = clock, log

    def is_set(self):
        return False

    def wait(self, seconds):
        self.log.append(("wait", seconds))
        self.clock.now += seconds
        return False


def settles_before_reads(log):
    """The wait just before each reading."""
    waits = []
    for index, entry in enumerate(log):
        if entry[0] == "read":
            before = [e for e in log[:index] if e[0] == "wait" and e[1]]
            waits.append(before[-1][1] if before else None)
    return waits


# ---- the rule ---------------------------------------------------------------------
class RuleTests(NoWindows):
    def test_the_constant_is_7_time_constants(self):
        self.assertEqual(app.LOCKIN_SETTLE_TAUS, 7.0)

    def test_the_pure_rule(self):
        rule = app.ppms_lockin_settle_s
        self.assertEqual(rule(0.5, [1.0]), 7.0, "SR830 at 1 s")
        self.assertEqual(rule(0.5, [0.3]), max(0.5, 7 * 0.3))
        self.assertEqual(rule(0.5, [2.0]), 14.0, "Zurich at 2 s")
        self.assertEqual(rule(0.5, [1.0, 3.0]), 21.0, "both: the slowest counts")
        self.assertEqual(rule(30.0, [1.0, 3.0]), 30.0, "a longer Settle s is kept")
        self.assertEqual(rule(0.5, []), 0.5, "no lock-in: Settle s")
        self.assertAlmostEqual(rule(0.0, [float("nan"), 1e-3]), 7e-3, msg="a non-finite TC is ignored")

    def panel(self, codes=None, zurich_tau=None):
        panel = rte_tests.RTE.make_worker(self)
        panel.ppms_instrument_controllers = {key: mock.Mock(resource_name=f"GPIB0::{address}::INSTR")
                                             for key, address in (("830_a", 8), ("830_b", 9), ("830_c", 10))}
        if codes is not None:
            panel.ppms_sr830_time_constant_codes = dict(codes)
        if zurich_tau is not None:
            panel.zurich_scan_tau = zurich_tau
        return panel

    def test_sr830_only(self):
        panel = self.panel({"830_a": 10, "830_b": 8})
        self.assertEqual(panel._ppms_lockin_settle(0.5, ("830_a", "830_b"), False), (7.0, 1.0, "SR830-1"))
        panel = self.panel({"830_a": 12})
        self.assertEqual(panel._ppms_lockin_settle(0.5, ("830_a",), False), (70.0, 10.0, "SR830-1"))

    def test_zurich_only(self):
        panel = self.panel(zurich_tau=2.0)
        self.assertEqual(panel._ppms_lockin_settle(0.5, (), True), (14.0, 2.0, "the Zurich"))
        panel = self.panel(zurich_tau=0.01)
        self.assertEqual(panel._ppms_lockin_settle(0.5, (), True)[0], 0.5, "Settle s is longer than 70 ms")

    def test_both(self):
        panel = self.panel({"830_a": 10, "830_c": 11}, zurich_tau=2.0)
        self.assertEqual(panel._ppms_lockin_settle(0.5, ("830_a", "830_c"), True), (21.0, 3.0, "SR830-3"))
        panel = self.panel({"830_a": 10}, zurich_tau=5.0)
        self.assertEqual(panel._ppms_lockin_settle(0.5, ("830_a",), True), (35.0, 5.0, "the Zurich"))
        self.assertEqual(panel._ppms_lockin_settle(0.5, ("830_a",), False)[0], 7.0,
                         "a Zurich that is not measured does not count")

    def test_settle_s_longer(self):
        panel = self.panel({"830_a": 10}, zurich_tau=1.0)
        self.assertEqual(panel._ppms_lockin_settle(12.0, ("830_a",), True), (12.0, 1.0, "SR830-1"))

    def test_an_sr830_not_read_back_counts_with_what_connect_sets(self):
        """RT-E: OFLT 10 = 1 s; PPMS: the 'SR830 TC' choice."""
        rte = self.panel()
        self.assertEqual(rte._ppms_lockin_settle(0.5, ("830_b",), False), (7.0, 1.0, "SR830-2"))
        ppms = app.CombinedControlApp.__new__(app.CombinedControlApp)
        ppms.ppms_sr830_time_constant_var = Var("3 s")
        self.assertEqual(ppms._ppms_lockin_settle(0.5, ("830_a",), False), (21.0, 3.0, "SR830-1"))
        ppms.ppms_sr830_time_constant_codes = {"830_a": 9}   # read back: 300 ms
        self.assertAlmostEqual(ppms._ppms_lockin_settle(0.5, ("830_a",), False)[0], 2.1)

    def test_the_words(self):
        self.assertEqual(app.ppms_lockin_settle_text(0.5, 7.0, 1.0, "SR830-1"),
                         "7 s (7 x 1 s, the time constant of SR830-1, the slowest lock-in measured; "
                         "Settle s is 0.5 s)")
        self.assertEqual(app.ppms_lockin_settle_text(12.0, 12.0, 1.0, "the Zurich"),
                         "12 s (Settle s; 7 x the slowest lock-in time constant, 1 s on the Zurich, is 7 s)")


# ---- RT-E: the wait before each reading in every mode ------------------------------
class RTEWorkerTests(NoWindows):
    def worker(self, codes=None, zurich_tau=None, active=("830_a",)):
        panel = rte_tests.RTE.make_worker(self)
        panel.ppms_sr830_time_constant_codes = dict(codes or {"830_a": 10})
        panel.zurich_scan_enabled = zurich_tau is not None
        panel.zurich_scan_tau = zurich_tau if zurich_tau is not None else 1.0
        panel.thread_log = mock.Mock()
        log = self.log = []
        panel._rte_wait = lambda seconds: log.append(("wait", seconds))
        sample = dict(panel._average_ppms_transport_sample.return_value)

        def read(*_args):
            log.append(("read",))
            return dict(sample)
        panel._acquire_ppms_first_point = lambda *args: (read(), 2)
        panel._average_ppms_transport_sample = read
        panel.rte_controllers["newmark"] = mock.Mock()
        panel._rte_scan_move_stage = lambda target: (log.append(("move", target)) or dict(
            angle_deg=target, px=int(target * 10000), steps_per_degree=10000))
        self.settings = app.validate_ppms_transport_scan_settings(
            1., 317.3, 50., 1, 2, active, allow_no_lockins=not active)
        return panel

    def run_mode(self, panel, mode, plan, stable=0.5):
        panel._rte_scan_worker(mode, plan, dict(self.settings), 5., stable, 1.,
                               *((1.5,) if mode == "Angle" else ()))
        return panel.ppms_field_scan_rows

    def test_field_current_and_angle_wait_7_time_constants_before_every_reading(self):
        plans = {"Field": ((1, 1, -1.), (1, 2, 0.), (1, 3, 1.)),
                 "Current": ((1, 1, .1), (1, 2, .2), (1, 3, .3)),
                 "Angle": ((1, 1, 0.), (1, 2, 5.), (1, 3, 10.))}
        for mode, plan in plans.items():
            with self.subTest(mode=mode):
                panel = self.worker()
                rows = self.run_mode(panel, mode, plan)
                self.assertEqual(settles_before_reads(self.log), [7.0, 7.0, 7.0], "first point included")
                self.assertEqual([row["effective_settle_s"] for row in rows], [7.0] * 3)
                self.assertEqual([row["slowest_lockin_tc_s"] for row in rows], [1.0] * 3)
                self.assertEqual([row["stable_s"] for row in rows], [0.5] * 3, "Settle s itself is kept")
                if mode == "Angle":
                    # The stage moves, then the wait, then the reading.
                    moves = [i for i, e in enumerate(self.log) if e[0] == "move"]
                    reads = [i for i, e in enumerate(self.log) if e[0] == "read"]
                    self.assertTrue(all(m < w < r for m, w, r in zip(
                        moves, [i for i, e in enumerate(self.log) if e == ("wait", 7.0)], reads)))

    def test_zurich_only_both_and_settle_s_longer(self):
        for codes, zurich_tau, active, stable, expected in (
                (None, 2.0, (), 0.5, 14.0),                # the Zurich alone (RT-E's default meter)
                ({"830_a": 10}, 3.0, ("830_a",), 0.5, 21.0),  # both: the Zurich is slower
                ({"830_a": 10}, 0.1, ("830_a",), 0.5, 7.0),   # both: the SR830 is slower
                ({"830_a": 10}, 1.0, ("830_a",), 9.0, 9.0)):  # Settle s is longer
            with self.subTest(zurich_tau=zurich_tau, active=active, stable=stable):
                panel = self.worker(codes, zurich_tau, active)
                self.run_mode(panel, "Current", ((1, 1, .1), (1, 2, .2)), stable=stable)
                self.assertEqual(settles_before_reads(self.log), [expected, expected])

    def test_each_harmonic_pass_settles_again_with_its_own_lock_ins(self):
        panel = self.worker({"830_a": 10, "830_c": 11}, active=("830_a", "830_c"))
        panel.ppms_instrument_controllers["830_c"] = mock.Mock(resource_name="GPIB0::10::INSTR")
        setattr(panel, app.PPMS_SR830_HARMONIC_ATTRS["830_a"], Var("1+2"))
        setattr(panel, app.PPMS_SR830_HARMONIC_ATTRS["830_c"], Var("1"))
        panel._rte_set_harmonic = mock.Mock(side_effect=lambda *args: self.log.append(("harmonic",) + args))
        passes = panel._rte_harmonic_passes(self.settings)
        self.assertEqual([p["active_lockins"] for p in passes], [("830_a", "830_c"), ("830_a",)])
        panel._rte_harmonic_scan_worker("Current", ((1, 1, .1), (1, 2, .2)), passes, 5., 0.5, 1.)
        # Pass 1: SR830-3 at 3 s; pass 2 (SR830-1 alone, after HARM 2): 1 s.
        self.assertEqual(settles_before_reads(self.log), [21.0, 21.0, 7.0, 7.0])
        harmonic = self.log.index(("harmonic", "830_a", 2))
        self.assertEqual(self.log[harmonic + 1:].count(("wait", 7.0)), 2,
                         "the first point after the harmonic change waits too")
        rows = panel.ppms_field_scan_rows
        self.assertEqual([r["effective_settle_s"] for r in rows], [21.0, 21.0, 7.0, 7.0])
        self.assertEqual(list(rows[0])[-2:], ["effective_settle_s", "slowest_lockin_tc_s"])
        self.assertEqual(list(rows[2]), list(rows[0]), "the same columns in every pass")
        # The pass with fewer lock-ins is logged once, not at every point.
        logged = [c.args[0] for c in panel.thread_log.call_args_list if "each reading now waits" in c.args[0]]
        self.assertEqual(len(logged), 2)
        self.assertTrue(logged[-1].startswith("RT-E scan: each reading now waits 7 s"), logged)

    def test_rte_rows_end_with_the_new_columns(self):
        panel = self.worker(zurich_tau=2.0)
        rows = self.run_mode(panel, "Field", ((1, 1, -1.),))
        keys = list(rows[0])
        self.assertEqual(keys[-3:], ["sr830_8_harmonic", "effective_settle_s", "slowest_lockin_tc_s"])
        self.assertEqual((rows[0]["effective_settle_s"], rows[0]["slowest_lockin_tc_s"]), (14.0, 2.0))
        # The older columns keep their order.
        self.assertLess(keys.index("stable_s"), keys.index("first_point_windows"))


# ---- RT-E Start: the status line, the Angle Yes/No and the estimate -----------------
class RTEStartTests(NoWindows):
    def start(self, mode, stable="0.5", acquire="1", zurich_tau=None, codes=None, yes=True):
        panel = rte_tests.RTE.make_worker(self)
        panel._require_ppms_control_ready = mock.Mock()
        panel._rte_capture_zurich = mock.Mock()
        panel.zurich_scan_enabled = zurich_tau is not None
        panel.zurich_scan_tau = zurich_tau or 1.0
        panel.ppms_sr830_time_constant_codes = dict(codes or {"830_a": 10})
        panel.ppms_simple_mode = Var(mode)
        span = ("0", "10", "5") if mode == "Angle" else ("0.01", "0.03", "0.01")
        panel.ppms_simple_range = [Var(v) for v in span]
        panel.ppms_field_scan_loop_var = Var("1")
        panel.rte_round_trip = Var(True)
        for key in ("gauss", "kepco"):
            panel.rte_controllers[key].connected = True
        panel.rte_controllers["newmark"] = mock.Mock(connected=mode == "Angle", steps_per_degree=10000,
                                                     motion_may_run=False, label="Newmark NSC-A1")
        panel.rte_stage_stop_event = threading.Event()
        panel.rte_drive_target, panel.rte_voltage_limit = Var("1.5"), Var("5")
        active = ("830_a",) if codes != {} else ()
        settings = app.validate_ppms_transport_scan_settings(.1, 17.3, 50., 1, 2, active,
                                                            allow_no_lockins=not active)
        panel._ppms_direct_transport_settings = mock.Mock(return_value=settings)
        panel._rte_harmonic_passes = mock.Mock(return_value=[settings])
        panel.ppms_field_scan_stable_var = Var(stable)
        panel.ppms_acquisition_time_var = Var(acquire)
        panel.ppms_field_scan_progress = mock.Mock()
        panel.ppms_field_scan_status_var = Var("")
        panel._update_ppms_control_ui = mock.Mock()
        panel._redraw_ppms_transport_plot = mock.Mock()
        panel.thread_log = mock.Mock()
        panel.ppms_executor = mock.Mock()
        panel.post_ui = lambda callback: callback()
        panel.root = None
        ask = mock.Mock(return_value=yes)
        with tempfile.TemporaryDirectory() as folder, \
                mock.patch.object(app.filedialog, "askdirectory", return_value=folder), \
                mock.patch.object(app.simpledialog, "askstring", return_value=""), \
                mock.patch.object(app.messagebox, "askyesno", ask):
            panel.start_ppms_field_scan()
        self.error.assert_not_called()
        return panel, ask

    def test_field_and_current_status_lines_show_the_wait_and_the_shortest_duration(self):
        for mode in ("Field", "Current"):
            for kwargs, expected in (
                    ({}, f"RT-E {mode}: 5 points, settle 7 s; at least 41 s"),
                    (dict(zurich_tau=2.0), f"RT-E {mode}: 5 points, settle 14 s; at least 1 min 16 s"),
                    (dict(zurich_tau=2.0, codes={}), f"RT-E {mode}: 5 points, settle 14 s; at least 1 min 16 s"),
                    (dict(stable="20"), f"RT-E {mode}: 5 points, settle 20 s; at least 1 min 46 s")):
                with self.subTest(mode=mode, **kwargs):
                    panel, ask = self.start(mode, **kwargs)
                    ask.assert_not_called()
                    self.assertEqual(panel.ppms_field_scan_status_var.get(), expected)
                    message = panel.thread_log.call_args_list[0].args[0]
                    self.assertIn("5 points x 1 harmonic pass(es)", message)
                    self.assertIn("each reading waits ", message)
                    self.assertEqual(panel.ppms_scan_settle_logged, float(expected.split("settle ")[1].split(" s")[0]))
                    panel.ppms_executor.submit.assert_called_once()

    def test_the_angle_yes_no_shows_the_wait(self):
        panel, ask = self.start("Angle", zurich_tau=3.0, yes=False)
        question = ask.call_args.args[1]
        self.assertIn("Each angle waits 21 s (7 x 3 s, the time constant of the Zurich, the slowest lock-in "
                      "measured; Settle s is 0.5 s) before it is read; the scan takes at least 1 min 51 s.",
                      question)
        panel.ppms_executor.submit.assert_not_called()
        panel, ask = self.start("Angle")
        # The Angle status line keeps its format (it has no room for the wait).
        self.assertEqual(panel.ppms_field_scan_status_var.get(), "RT-E Angle 0° to 10°, KEPCO +1.5 A held; 5 points")
        self.assertIn("each reading waits 7 s", panel.thread_log.call_args_list[0].args[0])


# ---- PPMS: every mode, through the real nested and point workers --------------------
class PPMSWorkerTests(NoWindows):
    def harness(self, mode, codes=None, zurich_tau=None, stable=0.5, harmonics=None):
        obj = app.CombinedControlApp.__new__(app.CombinedControlApp)
        obj.closing = False
        self.clock, self.log = Clock(), []
        obj.ppms_field_scan_stop_event = ClockEvent(self.clock, self.log)
        obj.ppms_scan_context = {}
        obj.ppms_last_readback = None
        obj.ppms_field_scan_rows = []
        obj.ppms_field_scan_total = 100
        obj.ppms_field_scan_started_at = self.clock.now
        obj.ppms_sr830_time_constant_codes = dict(codes or {"830_a": 10})
        obj.zurich_scan_enabled = zurich_tau is not None
        obj.zurich_scan_tau = zurich_tau or 1.0
        obj.ppms_instrument_controllers = {
            key: types.SimpleNamespace(resource_name=f"GPIB0::{address}::INSTR")
            for key, address in (("830_a", 8), ("830_b", 9), ("830_c", 10))}
        for key, choice in (harmonics or {}).items():
            setattr(obj, app.PPMS_SR830_HARMONIC_ATTRS[key], Var(choice))
        state = self.state = dict(angle=26., field=0., temp=300.)
        log = self.log

        def read_live():
            return dict(field_oe=state["field"], field_status="StableDriven",
                        temperature_k=state["temp"], temperature_status="Stable")

        def set_field(target, *_args):
            state["field"] = target
            log.append(("field", target))
            return read_live()

        def set_temperature(target, *_args):
            state["temp"] = target
            log.append(("temperature", target))
        obj.ppms_environment_controller = types.SimpleNamespace(
            backend=app.PPMS_QDINSTRUMENT_BACKEND, active=state, set_field=set_field,
            set_temperature=set_temperature)
        obj._read_ppms_live = read_live
        obj._apply_ppms_environment_readback = lambda data: None
        obj._ppms_publish_motor_readback = lambda data: None
        obj.post_ui = lambda callback: None
        obj.thread_log = mock.Mock()
        obj._append_ppms_live_csv = lambda row: None
        obj._configure_ppms_transport_scan = lambda config: log.append(("configure",))
        obj._update_ppms_transport_current = lambda config: log.append(("amplitude", config["current_rms_ma"]))
        obj._stop_ppms_transport_source = lambda: log.append(("off",))

        def read(frequency, lockins, duration):
            log.append(("read",))
            sample = {}
            for key in lockins:
                address = {"830_a": 8, "830_b": 9, "830_c": 10}[key]
                sample.update({f"sr830_{address}_x_v": 1e-6, f"sr830_{address}_y_v": 0.,
                               f"sr830_{address}_r_v": 1e-6, f"sr830_{address}_frequency_hz": frequency,
                               f"sr830_{address}_reference_valid": True})
            return sample
        obj._acquire_ppms_first_point = lambda *args: (read(*args), 2)
        obj._average_ppms_transport_sample = read

        class Motor:
            def __init__(self, _active):
                self.on_read = None

            def read(self):
                return {"angle_deg": state["angle"], "status": "AtTarget"}

            def move_and_verify(self, target, slowdown, cancelled, index_target=None):
                state["angle"] = target
                log.append(("move", target))
        self.motor = Motor
        values = {"Temperature": dict(angle=(26.,), temperature=(300., 301.)),
                  "Field": dict(angle=(26.,), temperature=(300.,)),
                  "Angle": dict(angle=(26., 27.), temperature=(300.,)),
                  "Current": dict(angle=(26.,), temperature=(300.,)),
                  "Field+Temperature": dict(angle=(26.,), temperature=(300., 301.))}[mode]
        active = tuple(harmonics or ("830_a",))
        settings = app.validate_ppms_transport_scan_settings(1., 317.3, 50., 1, 2, active)
        settings.update(scan_mode=mode, nested=values, slowdown=14, temperature_rate=1.,
                        temperature_approach=1, acquisition_s=1.,
                        currents=(1., 2., 3.) if mode == "Current" else (1.,))
        sequence = ([(1, 1, 3, 100.), (1, 2, 3, 200.), (1, 3, 3, 100.)] if "Field" in mode
                    else [(1, 1, 1, 0.)])
        self.stable = stable
        return obj, sequence, settings

    def run_scan(self, obj, sequence, settings):
        with mock.patch.object(app, "PPMSMotorDebug", self.motor), \
                mock.patch.object(app.time, "monotonic", self.clock):
            obj._ppms_scan_with_final_field(sequence, 100., self.stable, 1, settings)

    def test_every_mode_waits_7_time_constants_before_every_reading(self):
        for mode, points in (("Temperature", 2), ("Field", 3), ("Angle", 2), ("Current", 3),
                             ("Field+Temperature", 6)):
            with self.subTest(mode=mode):
                obj, sequence, settings = self.harness(mode)
                self.run_scan(obj, sequence, settings)
                self.assertEqual(settles_before_reads(self.log), [7.0] * points, "first point included")
                rows = obj.ppms_field_scan_rows
                self.assertEqual([r["effective_settle_s"] for r in rows], [7.0] * points)
                self.assertEqual(list(rows[0])[-2:], ["effective_settle_s", "slowest_lockin_tc_s"])
                self.assertEqual([r["stable_s"] for r in rows], [0.5] * points, "Settle s itself is recorded")

    def test_temperature_and_field_stability_waits_keep_their_criteria(self):
        """The stability waits poll every 0.5 s and accept after max(1 s,
        Settle s) of good readings, as before; only the wait right before
        the reading grows to 7 x TC."""
        obj, sequence, settings = self.harness("Temperature")
        self.run_scan(obj, sequence, settings)
        waits = [e[1] for e in self.log if e[0] == "wait"]
        self.assertEqual(waits, [.5, .5, 7.0, .5, .5, 7.0])
        obj, sequence, settings = self.harness("Field")
        self.run_scan(obj, sequence, settings)
        waits = [e[1] for e in self.log if e[0] == "wait"]
        # The field's first setpoint (0.5 + 0.5 s), the temperature (0.5 +
        # 0.5 s), then 7 s before each of the three readings.
        self.assertEqual(waits, [.5, .5, .5, .5, 7.0, 7.0, 7.0])

    def test_a_longer_settle_s_is_kept_and_the_stability_criteria_follow_it_as_before(self):
        obj, sequence, settings = self.harness("Temperature", stable=8.0)
        self.run_scan(obj, sequence, settings)
        self.assertEqual(settles_before_reads(self.log), [8.0, 8.0])

    def test_with_the_zurich_the_slowest_lock_in_counts(self):
        obj, sequence, settings = self.harness("Current", zurich_tau=2.0)
        self.run_scan(obj, sequence, settings)
        self.assertEqual(settles_before_reads(self.log), [14.0] * 3, "the Zurich (2 s) is slower than SR830-1")
        rows = obj.ppms_field_scan_rows
        self.assertEqual({r["slowest_lockin_tc_s"] for r in rows}, {2.0})

    def test_each_harmonic_pass_settles_again(self):
        obj, sequence, settings = self.harness(
            "Field", codes={"830_a": 10, "830_c": 11}, harmonics={"830_a": "1+2", "830_c": "1"})
        self.run_scan(obj, sequence, settings)
        # Pass 1 measures SR830-3 (3 s) too; pass 2 SR830-1 (1 s) alone.
        self.assertEqual(settles_before_reads(self.log), [21.0] * 3 + [7.0] * 3)
        self.assertEqual([r["harmonic_pass"] for r in obj.ppms_field_scan_rows], [1] * 3 + [2] * 3)
        logged = [c.args[0] for c in obj.thread_log.call_args_list if "each reading now waits" in c.args[0]]
        self.assertEqual([text.split(" (")[0] for text in logged],
                         ["PPMS scan: each reading now waits 21 s", "PPMS scan: each reading now waits 7 s"])

    def test_a_front_panel_time_constant_read_at_the_scan_start_is_used(self):
        """The scan start's OFLT? read (here 3 s instead of the 1 s set) is
        what the wait counts with, and it is logged."""
        obj, sequence, settings = self.harness("Current")
        obj.ppms_scan_settle_logged = 7.0      # announced at Start with 1 s

        def configure(config):
            obj.ppms_sr830_time_constant_codes["830_a"] = 11
            self.log.append(("configure",))
        obj._configure_ppms_transport_scan = configure
        self.run_scan(obj, sequence, settings)
        self.assertEqual(settles_before_reads(self.log), [21.0] * 3)
        logged = [c.args[0] for c in obj.thread_log.call_args_list if "each reading now waits" in c.args[0]]
        self.assertEqual(len(logged), 1)
        self.assertIn("21 s (7 x 3 s, the time constant of SR830-1", logged[0])


# ---- PPMS Start: the confirmation, the estimate, the status line, macro SCAN ---------
class PPMSStartTests(NoWindows):
    def scan_object(self, label="1 s", stable=0.5, zurich_tau=None, harmonic_a=1, second=False):
        obj = app.CombinedControlApp.__new__(app.CombinedControlApp)
        obj.ppms_field_scan_running = obj.ppms_environment_busy = False
        obj.closing = False
        obj._require_ppms_control_ready = mock.Mock()
        obj.ppms_simple_mode = Var("Field")
        obj.ppms_simple_range = [Var(v) for v in (100, 300, 100)]
        values = dict(ppms_acquisition_time_var=1, ppms_field_target_var=100, ppms_temperature_target_var=300,
                      ppms_motor_target_var=26, ppms_field_scan_stable_var=stable, ppms_field_rate_var=100,
                      ppms_field_approach_var="No overshoot", ppms_transport_current_var=1,
                      ppms_transport_frequency_var=317.3, ppms_transport_compliance_var=10,
                      ppms_transport_harmonic_a_var=harmonic_a, ppms_temperature_rate_var=1,
                      ppms_temperature_approach_var="No overshoot", ppms_motor_speed_var=14,
                      ppms_motor_confirm_var=True, ppms_sr830_time_constant_var=label)
        for name, value in values.items():
            setattr(obj, name, Var(value))
        obj.ppms_environment_controller = types.SimpleNamespace(backend=app.PPMS_QDINSTRUMENT_BACKEND)
        obj.ppms_instrument_controllers = {
            "6221": types.SimpleNamespace(connected=True, resource_name="GPIB0::12::INSTR"),
            "830_a": types.SimpleNamespace(connected=True, resource_name="GPIB0::8::INSTR")}
        if second:
            obj.ppms_instrument_controllers["830_b"] = types.SimpleNamespace(
                connected=True, resource_name="GPIB0::9::INSTR")
        if zurich_tau is not None:
            obj.ppms_lockin_selection = {"830_a": Var(True), "zurich": Var(True)}
            obj.zurich_controller = types.SimpleNamespace(mfli_connected=True)
            obj.zurich_scan_enabled, obj.zurich_scan_tau = True, zurich_tau
        obj.ppms_last_readback = dict(temperature_k=300, field_oe=100)
        obj.ppms_field_scan_stop_event = threading.Event()
        for name in ("_redraw_ppms_transport_plot", "_update_ppms_control_ui", "ppms_executor",
                     "ppms_field_scan_progress", "ppms_transport_status_var"):
            setattr(obj, name, mock.Mock())
        obj.ppms_field_scan_status_var = Var("")
        obj.ppms_field_scan_eta_var = Var("")
        obj.thread_log = mock.Mock()
        obj.root = mock.Mock()
        return obj

    def start(self, obj, yes, run=None):
        ask = mock.Mock(return_value=yes)
        with tempfile.TemporaryDirectory() as folder, \
                mock.patch.object(app.messagebox, "askyesno", ask), \
                mock.patch.object(app.filedialog, "askdirectory", return_value=folder), \
                mock.patch.object(app.simpledialog, "askstring", return_value=""):
            (run or obj.start_ppms_field_scan)()
        self.error.assert_not_called()
        return ask

    def test_the_confirmation_shows_the_wait_and_the_estimate_counts_it(self):
        for kwargs, line, estimate in (
                ({}, "7 s (7 x 1 s, the time constant of SR830-1, the slowest lock-in measured; "
                     "Settle s is 0.5 s)", "1 min 26 s"),      # 4 + 5 x (7 + 1) + 42 s
                (dict(zurich_tau=3.0), "21 s (7 x 3 s, the time constant of the Zurich, the slowest "
                                      "lock-in measured; Settle s is 0.5 s)", "2 min 36 s"),
                (dict(stable=30.0), "30 s (Settle s; 7 x the slowest lock-in time constant, 1 s on "
                                   "SR830-1, is 7 s)", "3 min 21 s"),
                (dict(label="100 ms"), "0.7 s (7 x 0.1 s, the time constant of SR830-1, the slowest "
                                      "lock-in measured; Settle s is 0.5 s)", "55 s")):
            with self.subTest(**kwargs):
                obj = self.scan_object(**kwargs)
                ask = self.start(obj, yes=False)
                message = ask.call_args.args[1]
                self.assertIn(f"Measurement points: 5\nWait before each reading: {line}\n"
                              f"Estimated duration: about {estimate}\n", message)
                self.assertEqual(ask.call_args.kwargs["icon"], "question")
                obj.ppms_executor.submit.assert_not_called()

    def test_harmonic_passes_with_fewer_lock_ins_are_named_and_counted(self):
        # SR830-1 reads 1+2 at 1 s; SR830-2 reads 1 at 3 s (read back), so
        # the second pass measures SR830-1 alone.
        obj = self.scan_object(harmonic_a="1+2", second=True)
        obj.ppms_sr830_time_constant_codes = {"830_a": 10, "830_b": 11}
        message = self.start(obj, yes=False).call_args.args[1]
        self.assertIn("Measurement points: 10\nWait before each reading: 21 s (7 x 3 s, the time constant "
                      "of SR830-2, the slowest lock-in measured; Settle s is 0.5 s); harmonic passes with "
                      "fewer lock-ins wait 7 s\n", message)
        # 4 s of ramp, 5 x (21 + 1) s, 5 x (7 + 1) s, and 6 x 10 + 12 s.
        self.assertIn("Estimated duration: about 3 min 46 s", message)
        # Both passes measure SR830-1 and the Zurich: one wait, no such note.
        obj = self.scan_object(zurich_tau=0.1, harmonic_a="1+2")
        obj.ppms_sr830_time_constant_codes = {"830_a": 11}
        message = self.start(obj, yes=False).call_args.args[1]
        self.assertIn("Measurement points: 10\nWait before each reading: 21 s (7 x 3 s", message)
        self.assertNotIn("harmonic passes with fewer lock-ins", message)

    def test_the_status_line_and_the_log_show_the_wait(self):
        obj = self.scan_object()
        self.start(obj, yes=True)
        self.assertEqual(obj.ppms_field_scan_status_var.get(),
                         "Field scan: 5 points, settle 7 s; estimated about 1 min 26 s")
        self.assertEqual(obj.ppms_field_scan_eta_var.get(), "Remaining: about 1 min 26 s")
        logs = [c.args[0] for c in obj.thread_log.call_args_list]
        self.assertIn("PPMS Field scan: each reading waits 7 s (7 x 1 s, the time constant of SR830-1, "
                      "the slowest lock-in measured; Settle s is 0.5 s)", logs)
        self.assertEqual(obj.ppms_scan_settle_logged, 7.0)
        obj.ppms_executor.submit.assert_called_once()
        self.assertEqual(obj.ppms_executor.submit.call_args.args[3], 0.5, "the worker gets Settle s itself")

    def test_a_macro_scan_line_goes_through_the_same_start(self):
        obj = self.scan_object(label="3 s")
        obj.ppms_macro_commands = iter([(2, "SCAN", ())])
        obj.ppms_macro_stop = threading.Event()
        obj.ppms_macro_status = Var("")
        obj.ppms_macro_editor = mock.Mock()
        ask = self.start(obj, yes=True, run=obj._ppms_macro_next)
        self.assertIn("Wait before each reading: 21 s (7 x 3 s", ask.call_args.args[1])
        self.assertTrue(obj.ppms_field_scan_running)
        obj.ppms_executor.submit.assert_called_once()
        self.assertEqual(obj.ppms_field_scan_status_var.get().split(";")[0], "Field scan: 5 points, settle 21 s")


if __name__ == "__main__":
    unittest.main()
