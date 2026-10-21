"""RT-E and PPMS: every lock-in reading settles at least 1 x TC (user,
2026-10-14).

The user's rule: "every lock-in reading waits at least 7 x TC (99 % settled
at 12 dB/oct) after a change, on every page; Settle s still applies when
longer; the Start confirmation shows the wait".  Later that day, after
seeing 9 s a point, the user chose 1 x TC instead: "change all to 1*TC
please" (LOCKIN_SETTLE_TAUS = 1.0).  On RT-E (Field, Current, Angle) and
PPMS (Temperature, Field, Angle, Current and the combined scans, macro SCAN
lines included) the wait before each point's reading is max(Settle s,
1 x the slowest time constant among the lock-ins measured).  Where a test
checks that the time constant sets the wait, 1 x TC is longer than Settle s
(e.g. 1 s or 3 s against 0.5 s).

Checked here: the rule (SR830 only, Zurich only, both, Settle s longer);
the wait before every reading in each RT-E mode and each PPMS mode, the
first point of each harmonic pass included, while the PPMS temperature and
field stability waits keep their criteria; the Start confirmations, status
lines and duration estimates; the CSV columns, appended last.

The review of 2026-10-14 adds: RT-E reads each SR830's time constant at
the scan start, as PPMS does, and a time constant changed since Connect is
waited for; the status line shows a wait that differs from the announced
one while it runs; the wait is rounded to 1e-12 s (0.7 s, not
0.7000000000000001 s, in the CSV, under the 7 x TC rule); status lines show
at most five digits;
the Angle+Temperature and Current+Temperature modes, PPMS Angle harmonic
passes, and the Zurich TC box read at Start on both pages.

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


class LogVar:
    """A status Var whose every set goes into the scan's event log, so a
    test sees when, relative to the waits, the status changed."""

    def __init__(self, log, value=""):
        self.log, self.value = log, value

    def get(self):
        return self.value

    def set(self, value):
        self.value = value
        self.log.append(("status", value))


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
    def test_the_constant_is_1_time_constant(self):
        self.assertEqual(app.LOCKIN_SETTLE_TAUS, 1.0)

    def test_the_pure_rule(self):
        rule = app.ppms_lockin_settle_s
        self.assertEqual(rule(0.5, [1.0]), 1.0, "SR830 at 1 s")
        self.assertEqual(rule(0.2, [0.3]), 0.3)
        self.assertEqual(rule(0.5, [0.3]), 0.5, "1 x 0.3 s is shorter than Settle s")
        self.assertEqual(rule(0.5, [2.0]), 2.0, "Zurich at 2 s")
        self.assertEqual(rule(0.5, [1.0, 3.0]), 3.0, "both: the slowest counts")
        self.assertEqual(rule(30.0, [1.0, 3.0]), 30.0, "a longer Settle s is kept")
        self.assertEqual(rule(0.5, []), 0.5, "no lock-in: Settle s")
        self.assertAlmostEqual(rule(0.0, [float("nan"), 1e-3]), 1e-3, msg="a non-finite TC is ignored")

    def test_the_wait_has_no_float_noise(self):
        """7 x 0.1 s is 0.7000000000000001 in binary; the CSV writes str()
        of the wait, so it is rounded to 1e-12 s (user, 2026-10-14).  At the
        user's 1 x TC the product is exact; the rounding is kept and checked
        with the earlier 7 x TC."""
        rule = app.ppms_lockin_settle_s
        for taus, settle, expected in (([0.1], 0.05, "0.1"), ([0.3], 0.2, "0.3"), ([1e-5], 0., "1e-05"),
                                       ([0.03], 0.01, "0.03"), ([3e-5], 0., "3e-05"), ([30e3], 0.5, "30000.0")):
            with self.subTest(taus=taus):
                self.assertEqual(str(rule(settle, taus)), expected)
        self.assertEqual(str(rule(0.05, [app.SR830_TIME_CONSTANTS_S[8]])), "0.1", "the 100 ms 'SR830 TC'")
        with mock.patch.object(app, "LOCKIN_SETTLE_TAUS", 7.0):
            for taus, settle, expected in (([0.1], 0.5, "0.7"), ([0.3], 0.5, "2.1"), ([1e-5], 0., "7e-05"),
                                           ([0.03], 0.1, "0.21"), ([3e-5], 0., "0.00021")):
                with self.subTest(taus=taus, taus_rule=7.0):
                    self.assertEqual(str(rule(settle, taus)), expected)

    def test_the_status_lines_show_at_most_five_digits(self):
        """Whole seconds from 100 s, 3 significant digits below; with {:g}
        a Settle s of 12345.7 or a 0.0864199 s wait widened the RT-E status
        line past its label (user, 2026-10-14)."""
        text = app.ppms_settle_status_text
        for seconds, expected in ((7.0, "7"), (0.7, "0.7"), (2.1, "2.1"), (21.0, "21"), (70.0, "70"),
                                  (12.0, "12"), (12.34, "12.3"), (99.94, "99.9"), (123.4, "123"),
                                  (12345.7, "12346"), (86400.0, "86400"), (0.0864199, "0.0864"),
                                  (7e-05, "7e-05"), (0.0, "0")):
            with self.subTest(seconds=seconds):
                self.assertEqual(text(seconds), expected)

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
        self.assertEqual(panel._ppms_lockin_settle(0.5, ("830_a", "830_b"), False), (1.0, 1.0, "SR830-1"))
        panel = self.panel({"830_a": 12})
        self.assertEqual(panel._ppms_lockin_settle(0.5, ("830_a",), False), (10.0, 10.0, "SR830-1"))

    def test_zurich_only(self):
        panel = self.panel(zurich_tau=2.0)
        self.assertEqual(panel._ppms_lockin_settle(0.5, (), True), (2.0, 2.0, "the Zurich"))
        panel = self.panel(zurich_tau=0.01)
        self.assertEqual(panel._ppms_lockin_settle(0.5, (), True)[0], 0.5, "Settle s is longer than 10 ms")

    def test_both(self):
        panel = self.panel({"830_a": 10, "830_c": 11}, zurich_tau=2.0)
        self.assertEqual(panel._ppms_lockin_settle(0.5, ("830_a", "830_c"), True), (3.0, 3.0, "SR830-3"))
        panel = self.panel({"830_a": 10}, zurich_tau=5.0)
        self.assertEqual(panel._ppms_lockin_settle(0.5, ("830_a",), True), (5.0, 5.0, "the Zurich"))
        self.assertEqual(panel._ppms_lockin_settle(0.5, ("830_a",), False)[0], 1.0,
                         "a Zurich that is not measured does not count")

    def test_settle_s_longer(self):
        panel = self.panel({"830_a": 10}, zurich_tau=1.0)
        self.assertEqual(panel._ppms_lockin_settle(12.0, ("830_a",), True), (12.0, 1.0, "SR830-1"))

    def test_an_sr830_not_read_back_counts_with_what_connect_sets(self):
        """RT-E: OFLT 10 = 1 s; PPMS: the 'SR830 TC' choice."""
        rte = self.panel()
        self.assertEqual(rte._ppms_lockin_settle(0.5, ("830_b",), False), (1.0, 1.0, "SR830-2"))
        ppms = app.CombinedControlApp.__new__(app.CombinedControlApp)
        ppms.ppms_sr830_time_constant_var = Var("3 s")
        self.assertEqual(ppms._ppms_lockin_settle(0.5, ("830_a",), False), (3.0, 3.0, "SR830-1"))
        ppms.ppms_sr830_time_constant_codes = {"830_a": 9}   # read back: 300 ms
        self.assertAlmostEqual(ppms._ppms_lockin_settle(0.2, ("830_a",), False)[0], 0.3)

    def test_the_words(self):
        self.assertEqual(app.ppms_lockin_settle_text(0.5, 1.0, 1.0, "SR830-1"),
                         "1 s (1 x 1 s, the time constant of SR830-1, the slowest lock-in measured; "
                         "Settle s is 0.5 s)")
        self.assertEqual(app.ppms_lockin_settle_text(12.0, 12.0, 1.0, "the Zurich"),
                         "12 s (Settle s; 1 x the slowest lock-in time constant, 1 s on the Zurich, is 1 s)")


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

    def test_field_current_and_angle_wait_1_time_constant_before_every_reading(self):
        plans = {"Field": ((1, 1, -1.), (1, 2, 0.), (1, 3, 1.)),
                 "Current": ((1, 1, .1), (1, 2, .2), (1, 3, .3)),
                 "Angle": ((1, 1, 0.), (1, 2, 5.), (1, 3, 10.))}
        for mode, plan in plans.items():
            with self.subTest(mode=mode):
                panel = self.worker()
                rows = self.run_mode(panel, mode, plan)
                # 1 x the 1 s TC, longer than Settle s 0.5 s.
                self.assertEqual(settles_before_reads(self.log), [1.0, 1.0, 1.0], "first point included")
                self.assertEqual([row["effective_settle_s"] for row in rows], [1.0] * 3)
                self.assertEqual([row["slowest_lockin_tc_s"] for row in rows], [1.0] * 3)
                self.assertEqual([row["stable_s"] for row in rows], [0.5] * 3, "Settle s itself is kept")
                if mode == "Angle":
                    # The stage moves, then the wait, then the reading.
                    moves = [i for i, e in enumerate(self.log) if e[0] == "move"]
                    reads = [i for i, e in enumerate(self.log) if e[0] == "read"]
                    self.assertTrue(all(m < w < r for m, w, r in zip(
                        moves, [i for i, e in enumerate(self.log) if e == ("wait", 1.0)], reads)))

    def test_zurich_only_both_and_settle_s_longer(self):
        for codes, zurich_tau, active, stable, expected in (
                (None, 2.0, (), 0.5, 2.0),                 # the Zurich alone (RT-E's default meter)
                ({"830_a": 10}, 3.0, ("830_a",), 0.5, 3.0),   # both: the Zurich is slower
                ({"830_a": 10}, 0.1, ("830_a",), 0.5, 1.0),   # both: the SR830 is slower
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
        self.assertEqual(settles_before_reads(self.log), [3.0, 3.0, 1.0, 1.0])
        harmonic = self.log.index(("harmonic", "830_a", 2))
        self.assertEqual(self.log[harmonic + 1:].count(("wait", 1.0)), 2,
                         "the first point after the harmonic change waits too")
        rows = panel.ppms_field_scan_rows
        self.assertEqual([r["effective_settle_s"] for r in rows], [3.0, 3.0, 1.0, 1.0])
        # The LS475 columns since 2026-10-18 (user), then the magnet
        # supply's two last since 2026-10-21 (user).
        self.assertEqual(list(rows[0])[-6:], ["effective_settle_s", "slowest_lockin_tc_s",
                                             "field_before_g", "field_drift_g", "magnet_supply",
                                             "magnet_measured_current_a"])
        self.assertEqual(list(rows[2]), list(rows[0]), "the same columns in every pass")
        # The pass with fewer lock-ins is logged once, not at every point.
        logged = [c.args[0] for c in panel.thread_log.call_args_list if "each reading now waits" in c.args[0]]
        self.assertEqual(len(logged), 2)
        self.assertTrue(logged[-1].startswith("SHV scan: each reading now waits 1 s"), logged)

    def test_a_front_panel_time_constant_read_at_the_scan_start_is_waited_for_and_shown(self):
        """RT-E's scan start reads OFLT? (user, 2026-10-14): an SR830 set to
        10 s since Connect makes every reading wait 10 s, not the 1 s Start
        announced, and the status line says so during that first wait."""
        panel = self.worker()
        panel.ppms_scan_settle_logged = 1.0          # announced at Start with 1 s
        panel._configure_ppms_transport_scan = mock.Mock(
            side_effect=lambda config: panel.ppms_sr830_time_constant_codes.update({"830_a": 12}))
        panel.post_ui = lambda callback: callback()
        panel._rte_show_point = mock.Mock()
        panel.ppms_field_scan_status_var = LogVar(self.log)
        rows = self.run_mode(panel, "Current", ((1, 1, .1), (1, 2, .2)))
        self.assertEqual(settles_before_reads(self.log), [10.0, 10.0])
        self.assertEqual([(r["effective_settle_s"], r["slowest_lockin_tc_s"]) for r in rows], [(10.0, 10.0)] * 2)
        statuses = [i for i, e in enumerate(self.log) if e[0] == "status"]
        self.assertEqual([self.log[i][1] for i in statuses], ["Each reading now waits 10 s: 1 x 10 s TC of SR830-1"])
        self.assertEqual(self.log[statuses[0] + 1], ("wait", 10.0), "shown while it waits")
        logged = [c.args[0] for c in panel.thread_log.call_args_list if "each reading now waits" in c.args[0]]
        self.assertEqual(logged, ["SHV scan: each reading now waits 10 s (1 x 10 s, the time constant of "
                                  "SR830-1, the slowest lock-in measured; Settle s is 0.5 s)"])

    def test_rte_rows_end_with_the_new_columns(self):
        panel = self.worker(zurich_tau=2.0)
        rows = self.run_mode(panel, "Field", ((1, 1, -1.),))
        keys = list(rows[0])
        # The LS475 columns since 2026-10-18 (user), then the magnet
        # supply's two last since 2026-10-21 (user).
        self.assertEqual(keys[-7:], ["sr830_8_harmonic", "effective_settle_s", "slowest_lockin_tc_s",
                                     "field_before_g", "field_drift_g", "magnet_supply",
                                     "magnet_measured_current_a"])
        self.assertEqual((rows[0]["effective_settle_s"], rows[0]["slowest_lockin_tc_s"]), (2.0, 2.0))
        # The older columns keep their order.
        self.assertLess(keys.index("stable_s"), keys.index("first_point_windows"))


# ---- RT-E Start: the status line, the Angle Yes/No and the estimate -----------------
class RTEStartTests(NoWindows):
    def start(self, mode, stable="0.5", acquire="1", zurich_tau=None, codes=None, yes=True, zurich_box=None):
        panel = rte_tests.RTE.make_worker(self)
        panel._require_ppms_control_ready = mock.Mock()
        panel.zurich_scan_enabled = zurich_tau is not None
        panel.zurich_scan_tau = zurich_tau or 1.0
        if zurich_box is None:
            panel._rte_capture_zurich = mock.Mock()
        else:
            # The real capture reads the Zurich row's boxes at Start.
            panel.zurich_busy = False
            panel.ppms_lockin_selection = {"zurich": Var(True)}
            panel.zurich_harmonic, panel.zurich_reference = Var("1"), Var("External")
            panel.zurich_timeconstant = Var(zurich_box)
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
        # An Angle Start reads the stage (MST, EO) as a short job on the
        # worker before the scan (user, 2026-10-17): it runs at once here,
        # the scan itself is only recorded.
        panel.ppms_executor = rte_tests.StartExecutor()
        panel.ppms_environment_busy = panel.ppms_field_scan_running = False
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
                    # 5 x (1 + 1) + 1 s; 5 x (2 + 1) + 1 s with the Zurich at 2 s.
                    ({}, f"SHV {mode}: 5 points, settle 1 s; at least 11 s"),
                    (dict(zurich_tau=2.0), f"SHV {mode}: 5 points, settle 2 s; at least 16 s"),
                    (dict(zurich_tau=2.0, codes={}), f"SHV {mode}: 5 points, settle 2 s; at least 16 s"),
                    (dict(stable="20"), f"SHV {mode}: 5 points, settle 20 s; at least 1 min 46 s")):
                with self.subTest(mode=mode, **kwargs):
                    panel, ask = self.start(mode, **kwargs)
                    ask.assert_not_called()
                    self.assertEqual(panel.ppms_field_scan_status_var.get(), expected)
                    message = panel.thread_log.call_args_list[0].args[0]
                    self.assertIn("5 points x 1 harmonic pass(es)", message)
                    self.assertIn("each reading waits ", message)
                    self.assertEqual(panel.ppms_scan_settle_logged, float(expected.split("settle ")[1].split(" s")[0]))
                    panel.ppms_executor.submit.assert_called_once()
                    # Only an Angle Start reads the stage (review, 2026-10-17).
                    panel.rte_controllers["newmark"].check_ready.assert_not_called()

    def test_the_zurich_tc_box_read_at_start_sets_the_wait(self):
        """_rte_capture_zurich copies the "TC (s)" box into zurich_scan_tau
        before the wait and the estimate are worked out (a stale 1 s here)."""
        for codes, expected in (({}, "SHV Field: 5 points, settle 2 s; at least 16 s"),
                                (None, "SHV Field: 5 points, settle 2 s; at least 16 s")):
            with self.subTest(sr830=codes is None):
                panel, _ask = self.start("Field", codes=codes, zurich_box="2")
                self.assertEqual(panel.zurich_scan_tau, 2.0)
                self.assertEqual(panel.ppms_field_scan_status_var.get(), expected)
                self.assertIn("each reading waits 2 s (1 x 2 s, the time constant of the Zurich",
                              panel.thread_log.call_args_list[0].args[0])

    def test_the_status_line_rounds_the_wait(self):
        panel, _ask = self.start("Current", stable="12345.7")
        status = panel.ppms_field_scan_status_var.get()
        self.assertTrue(status.startswith("SHV Current: 5 points, settle 12346 s; at least "), status)
        self.assertIn("each reading waits 12345.7 s (Settle s;", panel.thread_log.call_args_list[0].args[0],
                      "the log keeps the exact value")

    def test_the_angle_yes_no_shows_the_wait(self):
        panel, ask = self.start("Angle", zurich_tau=3.0, yes=False)
        question = ask.call_args.args[1]
        self.assertIn("Each angle waits 3 s (1 x 3 s, the time constant of the Zurich, the slowest lock-in "
                      "measured; Settle s is 0.5 s) before it is read; the scan takes at least 21 s.",
                      question)
        panel.ppms_executor.submit.assert_not_called()
        panel, ask = self.start("Angle")
        # The stage check at Start, then the scan (user, 2026-10-17).
        panel.rte_controllers["newmark"].check_ready.assert_called_once_with()
        self.assertEqual(len(panel.ppms_executor.scans()), 1)
        # The Angle status line keeps its format (it has no room for the wait).
        self.assertEqual(panel.ppms_field_scan_status_var.get(), "SHV Angle 0° to 10°, KEPCO +1.5 A held; 5 points")
        self.assertIn("each reading waits 1 s", panel.thread_log.call_args_list[0].args[0])


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
        obj._stop_ppms_transport_source = lambda **_options: log.append(("off",))

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
                  "Field+Temperature": dict(angle=(26.,), temperature=(300., 301.)),
                  "Angle+Temperature": dict(angle=(26., 27.), temperature=(300., 301.)),
                  "Current+Temperature": dict(angle=(26.,), temperature=(300., 301.))}[mode]
        active = tuple(harmonics or ("830_a",))
        settings = app.validate_ppms_transport_scan_settings(1., 317.3, 50., 1, 2, active)
        settings.update(scan_mode=mode, nested=values, slowdown=14, temperature_rate=1.,
                        temperature_approach=1, acquisition_s=1.,
                        currents=(1., 2., 3.) if mode.startswith("Current") else (1.,))
        sequence = ([(1, 1, 3, 100.), (1, 2, 3, 200.), (1, 3, 3, 100.)] if "Field" in mode
                    else [(1, 1, 1, 0.)])
        self.stable = stable
        return obj, sequence, settings

    def run_scan(self, obj, sequence, settings):
        with mock.patch.object(app, "PPMSMotorDebug", self.motor), \
                mock.patch.object(app.time, "monotonic", self.clock):
            obj._ppms_scan_with_final_field(sequence, 100., self.stable, 1, settings)

    def test_every_mode_waits_1_time_constant_before_every_reading(self):
        for mode, points in (("Temperature", 2), ("Field", 3), ("Angle", 2), ("Current", 3),
                             ("Field+Temperature", 6), ("Angle+Temperature", 4),
                             ("Current+Temperature", 6)):
            with self.subTest(mode=mode):
                obj, sequence, settings = self.harness(mode)
                self.run_scan(obj, sequence, settings)
                # 1 x the 1 s TC, longer than Settle s 0.5 s.
                self.assertEqual(settles_before_reads(self.log), [1.0] * points, "first point included")
                rows = obj.ppms_field_scan_rows
                self.assertEqual([r["effective_settle_s"] for r in rows], [1.0] * points)
                self.assertEqual(list(rows[0])[-2:], ["effective_settle_s", "slowest_lockin_tc_s"])
                self.assertEqual([r["stable_s"] for r in rows], [0.5] * points, "Settle s itself is recorded")

    def test_temperature_and_field_stability_waits_keep_their_criteria(self):
        """The stability waits poll every 0.5 s and accept after max(1 s,
        Settle s) of good readings, as before; only the wait right before
        the reading is max(Settle s, 1 x TC)."""
        obj, sequence, settings = self.harness("Temperature")
        self.run_scan(obj, sequence, settings)
        waits = [e[1] for e in self.log if e[0] == "wait"]
        self.assertEqual(waits, [.5, .5, 1.0, .5, .5, 1.0])
        obj, sequence, settings = self.harness("Field")
        self.run_scan(obj, sequence, settings)
        waits = [e[1] for e in self.log if e[0] == "wait"]
        # The field's first setpoint (0.5 + 0.5 s), the temperature (0.5 +
        # 0.5 s), then 1 s before each of the three readings.
        self.assertEqual(waits, [.5, .5, .5, .5, 1.0, 1.0, 1.0])

    def test_a_longer_settle_s_is_kept_and_the_stability_criteria_follow_it_as_before(self):
        obj, sequence, settings = self.harness("Temperature", stable=8.0)
        self.run_scan(obj, sequence, settings)
        self.assertEqual(settles_before_reads(self.log), [8.0, 8.0])

    def test_with_the_zurich_the_slowest_lock_in_counts(self):
        obj, sequence, settings = self.harness("Current", zurich_tau=2.0)
        self.run_scan(obj, sequence, settings)
        self.assertEqual(settles_before_reads(self.log), [2.0] * 3, "the Zurich (2 s) is slower than SR830-1")
        rows = obj.ppms_field_scan_rows
        self.assertEqual({r["slowest_lockin_tc_s"] for r in rows}, {2.0})

    def test_each_harmonic_pass_settles_again(self):
        obj, sequence, settings = self.harness(
            "Field", codes={"830_a": 10, "830_c": 11}, harmonics={"830_a": "1+2", "830_c": "1"})
        self.run_scan(obj, sequence, settings)
        # Pass 1 measures SR830-3 (3 s) too; pass 2 SR830-1 (1 s) alone.
        self.assertEqual(settles_before_reads(self.log), [3.0] * 3 + [1.0] * 3)
        self.assertEqual([r["harmonic_pass"] for r in obj.ppms_field_scan_rows], [1] * 3 + [2] * 3)
        logged = [c.args[0] for c in obj.thread_log.call_args_list if "each reading now waits" in c.args[0]]
        self.assertEqual([text.split(" (")[0] for text in logged],
                         ["PPMS scan: each reading now waits 3 s", "PPMS scan: each reading now waits 1 s"])

    def test_angle_harmonic_passes_settle_again(self):
        obj, sequence, settings = self.harness(
            "Angle", codes={"830_a": 10, "830_c": 11}, harmonics={"830_a": "1+2", "830_c": "1"})
        self.run_scan(obj, sequence, settings)
        # Pass 1 measures SR830-3 (3 s) too; pass 2 SR830-1 (1 s) alone.
        self.assertEqual(settles_before_reads(self.log), [3.0] * 2 + [1.0] * 2)
        self.assertEqual([r["harmonic_pass"] for r in obj.ppms_field_scan_rows], [1] * 2 + [2] * 2)
        moves = [i for i, e in enumerate(self.log) if e[0] == "move"]
        reads = [i for i, e in enumerate(self.log) if e[0] == "read"]
        self.assertLess(moves[-1], reads[-1], "the last reading follows the last move")

    def test_a_100_ms_time_constant_is_written_without_float_noise(self):
        # Settle s 0.05 s, so the 100 ms TC sets the wait (1 x TC).
        obj, sequence, settings = self.harness("Current", codes={"830_a": 8}, stable=0.05)
        self.run_scan(obj, sequence, settings)
        rows = obj.ppms_field_scan_rows
        self.assertEqual([str(r["effective_settle_s"]) for r in rows], ["0.1"] * 3)
        self.assertEqual({r["slowest_lockin_tc_s"] for r in rows}, {0.1})

    def test_a_front_panel_time_constant_read_at_the_scan_start_is_used(self):
        """The scan start's OFLT? read (here 3 s instead of the 1 s set) is
        what the wait counts with, and it is logged."""
        obj, sequence, settings = self.harness("Current")
        obj.ppms_scan_settle_logged = 1.0      # announced at Start with 1 s

        def configure(config):
            obj.ppms_sr830_time_constant_codes["830_a"] = 11
            self.log.append(("configure",))
        obj._configure_ppms_transport_scan = configure
        self.run_scan(obj, sequence, settings)
        self.assertEqual(settles_before_reads(self.log), [3.0] * 3)
        logged = [c.args[0] for c in obj.thread_log.call_args_list if "each reading now waits" in c.args[0]]
        self.assertEqual(len(logged), 1)
        self.assertIn("3 s (1 x 3 s, the time constant of SR830-1", logged[0])

    def test_a_different_wait_is_shown_on_the_status_line_once(self):
        """The Start confirmation said 1 s; the scan start reads 10 s on the
        front panel.  The status line shows the 10 s for as long as the
        wait lasts (user, 2026-10-14), and only when the wait changes."""
        obj = app.CombinedControlApp.__new__(app.CombinedControlApp)
        obj.closing = False
        obj.ppms_sr830_time_constant_codes = {"830_a": 12}
        obj.ppms_scan_settle_logged = 1.0
        obj.thread_log = mock.Mock()
        obj.post_ui = lambda callback: callback()
        obj.ppms_field_scan_status_var = Var("")
        self.assertEqual(obj._ppms_point_settle(0.5, ("830_a",)), (10.0, 10.0))
        self.assertEqual(obj.ppms_field_scan_status_var.get(), "Each reading now waits 10 s: 1 x 10 s TC of SR830-1")
        obj.ppms_field_scan_status_var.set("CSV saved 1/5; remaining 4 min 44 s")
        obj._ppms_point_settle(0.5, ("830_a",))
        self.assertEqual(obj.ppms_field_scan_status_var.get(), "CSV saved 1/5; remaining 4 min 44 s",
                         "the same wait again: nothing new")
        self.assertEqual(obj.thread_log.call_count, 1)
        obj._ppms_point_settle(100.0, ("830_a",))
        self.assertEqual(obj.ppms_field_scan_status_var.get(), "Each reading now waits 100 s (Settle s)")
        obj.closing = True
        obj._ppms_point_settle(0.5, ("830_a",))
        self.assertEqual(obj.ppms_field_scan_status_var.get(), "Each reading now waits 100 s (Settle s)",
                         "not after the window closed")


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
                ({}, "1 s (1 x 1 s, the time constant of SR830-1, the slowest lock-in measured; "
                     "Settle s is 0.5 s)", "56 s"),            # 4 + 5 x (1 + 1) + 42 s
                (dict(zurich_tau=3.0), "3 s (1 x 3 s, the time constant of the Zurich, the slowest "
                                      "lock-in measured; Settle s is 0.5 s)", "1 min 06 s"),
                (dict(stable=30.0), "30 s (Settle s; 1 x the slowest lock-in time constant, 1 s on "
                                   "SR830-1, is 1 s)", "3 min 21 s"),
                (dict(label="100 ms", stable=0.05), "0.1 s (1 x 0.1 s, the time constant of SR830-1, the "
                                                   "slowest lock-in measured; Settle s is 0.05 s)", "52 s")):
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
        self.assertIn("Measurement points: 10\nWait before each reading: 3 s (1 x 3 s, the time constant "
                      "of SR830-2, the slowest lock-in measured; Settle s is 0.5 s); harmonic passes with "
                      "fewer lock-ins wait 1 s\n", message)
        # 4 s of ramp in each of the two passes, 5 x (3 + 1) s, 5 x (1 + 1)
        # s, 6 x 10 s and a 12 s set-up per pass: the ramps and set-ups come
        # again in the second pass (review, 2026-10-18; it said 1 min 46 s).
        self.assertIn("Estimated duration: about 2 min 02 s", message)
        # Both passes measure SR830-1 and the Zurich: one wait, no such note.
        obj = self.scan_object(zurich_tau=0.1, harmonic_a="1+2")
        obj.ppms_sr830_time_constant_codes = {"830_a": 11}
        message = self.start(obj, yes=False).call_args.args[1]
        self.assertIn("Measurement points: 10\nWait before each reading: 3 s (1 x 3 s", message)
        self.assertNotIn("harmonic passes with fewer lock-ins", message)

    def test_the_status_line_and_the_log_show_the_wait(self):
        obj = self.scan_object()
        self.start(obj, yes=True)
        self.assertEqual(obj.ppms_field_scan_status_var.get(),
                         "Field scan: 5 points, settle 1 s; estimated about 56 s")
        self.assertEqual(obj.ppms_field_scan_eta_var.get(), "Remaining: about 56 s")
        logs = [c.args[0] for c in obj.thread_log.call_args_list]
        self.assertIn("PPMS Field scan: each reading waits 1 s (1 x 1 s, the time constant of SR830-1, "
                      "the slowest lock-in measured; Settle s is 0.5 s)", logs)
        self.assertEqual(obj.ppms_scan_settle_logged, 1.0)
        obj.ppms_executor.submit.assert_called_once()
        self.assertEqual(obj.ppms_executor.submit.call_args.args[3], 0.5, "the worker gets Settle s itself")

    def test_the_status_line_rounds_the_wait(self):
        obj = self.scan_object(stable=12345.7)
        self.start(obj, yes=True)
        self.assertTrue(obj.ppms_field_scan_status_var.get().startswith("Field scan: 5 points, settle 12346 s; "))

    def test_the_ppms_page_reads_its_zurich_tc_box_at_start(self):
        """PPMSPanel.start_ppms_field_scan copies the Zurich row's "TC (s)"
        box into zurich_scan_tau before the shared Start works out the
        wait: 1 x 2 s = 2 s, slower than SR830-1 at 1 s."""
        import test_ppms_sr830_tc as tc_tests
        panel, _ttk = tc_tests.build_ppms_panel()
        panel._require_ppms_control_ready = mock.Mock()
        panel.root = None
        panel.ppms_simple_mode.set("Field")
        for var, value in zip(panel.ppms_simple_range, ("100", "300", "100")):
            var.set(value)
        panel.ppms_field_scan_stable_var.set("0.5")
        panel.ppms_acquisition_time_var.set("1")
        panel.ppms_last_readback = dict(temperature_k=300, field_oe=100)
        panel.ppms_environment_controller = types.SimpleNamespace(backend=app.PPMS_QDINSTRUMENT_BACKEND)
        panel.ppms_motor_confirm_var.set(True)     # every scan holds an angle
        panel.ppms_instrument_controllers["6221"] = types.SimpleNamespace(
            connected=True, resource_name="GPIB0::12::INSTR")
        panel.ppms_instrument_controllers["830_a"].connect("GPIB0::8::INSTR")
        for key in ("830_b", "830_c"):
            panel.ppms_lockin_selection[key].set(False)
        panel.ppms_lockin_selection["zurich"].set(True)
        panel.zurich_busy = False
        panel.zurich_controller.mfli_connected = True
        panel.zurich_harmonic.set("1")
        panel.zurich_timeconstant.set("2")
        panel.zurich_scan_tau = 1.0                  # stale until Start reads the box
        ask = self.start(panel, yes=False)
        self.assertEqual(panel.zurich_scan_tau, 2.0)
        self.assertIn("Wait before each reading: 2 s (1 x 2 s, the time constant of the Zurich, the slowest "
                      "lock-in measured; Settle s is 0.5 s)\n", ask.call_args.args[1])

    def test_a_macro_scan_line_goes_through_the_same_start(self):
        obj = self.scan_object(label="3 s")
        obj.ppms_macro_commands = iter([(2, "SCAN", ())])
        obj.ppms_macro_stop = threading.Event()
        obj.ppms_macro_status = Var("")
        obj.ppms_macro_editor = mock.Mock()
        ask = self.start(obj, yes=True, run=obj._ppms_macro_next)
        self.assertIn("Wait before each reading: 3 s (1 x 3 s", ask.call_args.args[1])
        self.assertTrue(obj.ppms_field_scan_running)
        obj.ppms_executor.submit.assert_called_once()
        self.assertEqual(obj.ppms_field_scan_status_var.get().split(";")[0], "Field scan: 5 points, settle 3 s")


if __name__ == "__main__":
    unittest.main()
