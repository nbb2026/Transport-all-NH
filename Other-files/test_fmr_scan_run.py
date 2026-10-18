"""The FMR page's two scans on the shared save and end convention (user,
2026-10-18: every scan page saves through begin_scan_run and zeroes its
sources at every end; "every scan page" supersedes "no live CSV" for the
fixed-field frequency sweep).

The real scan methods run on a bare app with stand-ins: the real 4886
controller on a fake write-only card, a 475 that reads 300 G per ampere of
the card's current, a fake PNA, futures completed at once and a root.after()
queue on a fake clock.  Every dialog is answered here; no Tk window opens."""
import csv
import json
import tempfile
import threading
import time
import types
import unittest
from pathlib import Path
from unittest import mock

import test_fmr_stfmr_review as review
from test_nonlocal_keithley import Var, bare_app
from test_ppms_motor_debug import app
from test_stfmr_mod_prime_range import width_in_zeros


class Pna:
    """The PNA controller as the scans use it."""

    def __init__(self, log):
        self.log, self.connected = log, True
        self.fail_read_at = None        # the read (1, 2, ...) that raises
        self.fail_off = False
        self.on_read = {}               # read number -> hook run inside the read
        self.reads = 0

    def configure_cw(self, frequency_hz, selected, power_dbm):
        self.log.append(("pna", "configure", frequency_hz))
        return {1: power_dbm}

    def trigger_and_read(self, selected):
        self.reads += 1
        self.log.append(("pna", "read", self.reads))
        hook = self.on_read.get(self.reads)
        if hook is not None:
            hook()
        if self.reads == self.fail_read_at:
            raise RuntimeError("PNA did not answer")
        return {"S21": -3.0 - self.reads}

    def rf_off_read_back(self):
        self.log.append(("pna", "rf_off"))
        if self.fail_off:
            raise RuntimeError("the PNA RF did not read back OFF (OUTP? '1')")

    def disconnect(self):
        self.log.append(("pna", "disconnect"))


class Gauss:
    connected = True

    def __init__(self, card, log):
        self.card, self.log = card, log

    def read_field(self):
        return {"field": 300.0 * self.card.current, "unit_code": 1, "unit": "G"}

    def disconnect(self):
        pass


class Card(review.Card4886):
    def __init__(self, log, **options):
        super().__init__(**options)
        self.log = log

    def write_raw(self, payload):
        self.log.append(("4886", payload.decode("ascii")))
        super().write_raw(payload)


class Root(review.Root):
    """review's after() queue, with after_cancel."""

    def after(self, ms, callback):
        entry = [ms, callback]
        self.queue.append(entry)
        return id(entry)

    def after_cancel(self, token):
        self.queue[:] = [entry for entry in self.queue if id(entry) != token]

    def run(self, done, limit=2000):
        for _step in range(limit):
            if done() or not self.queue:
                return
            ms, callback = self.queue.pop(0)
            self.clock.now += ms / 1000.0
            callback()
        raise AssertionError("the loop did not end")


def sent(a):
    """The instrument calls, without the page's log lines."""
    return [entry for entry in a.log if entry[0] in ("4886", "pna")]


class FmrRun(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.folder = Path(self.tmp.name)
        self.order = []
        self.confirm = mock.Mock(side_effect=lambda *a, **k: self.order.append("yes/no") or True)
        self.ask_folder = mock.Mock(side_effect=lambda **k: self.order.append("folder") or str(self.folder))
        self.ask_name = mock.Mock(side_effect=lambda *a, **k: self.order.append("name") or "run A")
        self.error = mock.Mock()
        unexpected = mock.Mock(side_effect=AssertionError("an unexpected dialog opened"))
        for patcher in (mock.patch("tkinter.Tk", side_effect=AssertionError("a test tried to open a Tk window")),
                        mock.patch.object(app.messagebox, "askyesno", self.confirm),
                        mock.patch.object(app.messagebox, "showerror", self.error),
                        mock.patch.object(app.messagebox, "showwarning", unexpected),
                        mock.patch.object(app.filedialog, "askdirectory", self.ask_folder),
                        mock.patch.object(app.simpledialog, "askstring", self.ask_name)):
            patcher.start()
            self.addCleanup(patcher.stop)

    def make(self, stall=()):
        a = bare_app()
        a.closing = False
        self.clock = review.Clock()
        a.root = Root(self.clock)
        a.stfmr_executor = review.Now()
        a.post_ui = lambda callback: callback()
        a.log = []
        a.thread_log = lambda text: a.log.append(("log", text))
        card = Card(a.log, stall=stall)
        a.card = card
        a.stfmr_controllers = {"kepco": review.connected_4886(card), "gauss": Gauss(card, a.log), "vna": Pna(a.log)}
        review.kepco_vars(a)
        a.stfmr_safety_var = Var(True)
        a.stfmr_resource_vars = {key: Var(f"GPIB0::{n}::INSTR") for n, key in enumerate(("kepco", "gauss", "vna"))}
        a.stfmr_fmr_step_current_var = Var("0.5")
        for kind in ("fmr", "frequency"):
            setattr(a, f"stfmr_{kind}_running", False)
            setattr(a, f"stfmr_{kind}_stop_event", threading.Event())
            setattr(a, f"stfmr_{kind}_after_id", None)
            setattr(a, f"stfmr_{kind}_rows", [])
            setattr(a, f"stfmr_{kind}_status_var", Var())
            setattr(a, f"stfmr_{kind}_save_button", mock.Mock())
            setattr(a, f"stfmr_{kind}_progress", None)
        a.stfmr_fmr_stop_button = mock.Mock()
        a.stfmr_gauss_field_var = Var()
        a.stfmr_fmr_actual_power_by_port = {}
        a.stfmr_fmr_field_settle_samples = []
        a.stfmr_frequency_feedback_points = []
        a._set_stfmr_scan_busy = lambda busy: None
        a._redraw_stfmr_fmr_plot = a._redraw_stfmr_frequency_plot = lambda: None
        a._stfmr_fmr_inputs = lambda: (("S21",), [5e9, 6e9], -10.0, [-0.5, 0.0, 0.5], 5.0, 0.2)
        a._stfmr_frequency_inputs = lambda: (("S21",), [5e9, 6e9, 7e9], -10.0, 300.0, 5.0, 5.0, 0.3)
        return a

    def drive(self, a, start, done=lambda: False):
        clock = types.SimpleNamespace(monotonic=self.clock.monotonic, strftime=time.strftime, time=time.time)
        with mock.patch.object(app, "time", clock):
            start()
            a.root.run(done)

    @staticmethod
    def events(folder):
        log = next(folder.glob("*/*.log"))
        return [json.loads(line) for line in log.read_text(encoding="utf-8").splitlines()]

    @staticmethod
    def rows(folder):
        with next(folder.glob("*/*.csv")).open(encoding="utf-8-sig", newline="") as handle:
            return list(csv.reader(handle))


class FieldScanTests(FmrRun):
    def test_completed_saves_live_and_zeroes_both_at_the_end(self):
        a = self.make()
        self.drive(a, a.start_stfmr_fmr_scan)
        self.error.assert_not_called()
        self.assertEqual(self.order, ["yes/no", "folder", "name"], "the shared order")
        self.assertIn("the 4886 is zeroed and turned off and the PNA RF switched off",
                      self.confirm.call_args.args[1])
        run = self.folder / "run A"
        self.assertEqual(sorted(p.name for p in run.iterdir()), ["run A.csv", "run A.log"])
        rows = self.rows(self.folder)
        self.assertEqual(tuple(rows[0]), a._stfmr_fmr_csv_fields(), "the Save CSV columns, in order")
        self.assertEqual([r[0] for r in rows[1:]], ["1", "2", "3", "4", "5", "6"])
        events = self.events(self.folder)
        self.assertEqual([e["event"] for e in events],
                         ["run_prepared", "scan_started", "pna_configured"] + ["point_saved"] * 3
                         + ["pna_configured"] + ["point_saved"] * 3 + ["outputs_zeroed", "run_finished"])
        prepared = events[0]
        self.assertEqual((prepared["file_name"], prepared["settings"]["scan"]), ("run A", "field sweep"))
        self.assertEqual(prepared["settings"]["outputs_zeroed_at_end"], ["4886", "PNA RF"])
        self.assertEqual(events[-2], {**events[-2], "4886": "zeroed", "PNA RF": "zeroed"})
        end = events[-1]
        self.assertEqual((end["outcome"], end["points_saved"], end["outputs"]),
                         ("completed", 6, {"4886": "zeroed", "PNA RF": "zeroed"}))
        self.assertEqual(end["status"], "Completed; 4886 and RF zeroed; 6 points saved")
        self.assertEqual(a.stfmr_fmr_status_var.get(), end["status"])
        # The 4886 zeroed as its Zero / OFF zeroes it, then the RF off: after
        # the last read, in that order.
        # (The first zero is the scan's own, after its last frequency.)
        calls = sent(a)
        last_read = max(i for i, e in enumerate(calls) if e[:2] == ("pna", "read"))
        self.assertEqual(calls[last_read + 1:], [("4886", "CURR 0.000000"), ("4886", "OUTP OFF"),
                                                 ("4886", "CURR 0.000000"), ("4886", "OUTP OFF"), ("pna", "rf_off")])
        self.assertEqual(review.readout(a)[3], "OUTP: OFF")
        self.assertIsNone(a.stfmr_fmr_recorder, "released at the end")

    def test_a_blank_name_takes_the_fmr_field_prefix(self):
        self.ask_name.side_effect = lambda *a, **k: ""
        a = self.make()
        self.drive(a, a.start_stfmr_fmr_scan)
        (run,) = self.folder.iterdir()
        self.assertRegex(run.name, r"^FMR_field_\d{8}_\d{6}$")

    def test_cancel_at_the_folder_makes_and_sends_nothing(self):
        self.ask_folder.side_effect = lambda **k: ""
        a = self.make()
        self.drive(a, a.start_stfmr_fmr_scan)
        self.assertEqual(list(self.folder.iterdir()), [])
        self.assertEqual(a.log, [])
        self.assertFalse(a.stfmr_fmr_running)
        self.ask_name.assert_not_called()

    def test_stop_ends_stopped_with_both_zeroed(self):
        a = self.make()
        self.drive(a, a.start_stfmr_fmr_scan, done=lambda: len(a.stfmr_fmr_rows) == 2)
        self.assertIsNotNone(a.stfmr_fmr_after_id)
        a.stop_stfmr_fmr_scan()
        a.root.run(lambda: False)
        events = self.events(self.folder)
        self.assertEqual([e["event"] for e in events[-4:]],
                         ["point_saved", "stop_requested", "outputs_zeroed", "run_finished"])
        self.assertEqual(events[-3]["button"], "Stop / zero")
        self.assertEqual((events[-1]["outcome"], events[-1]["stop_reason"]), ("stopped", "stopped by the user"))
        self.assertEqual(a.stfmr_fmr_status_var.get(), "Stopped; 4886 and RF zeroed; 2 points saved")
        self.assertEqual(sent(a)[-1], ("pna", "rf_off"))
        self.error.assert_not_called()

    def test_zero_off_during_a_read_saves_no_mixed_row(self):
        # review, 2026-10-18 (S5): the red Zero / OFF during the PNA read
        # zeroed the 4886 under it and the point was still saved.
        a = self.make()
        a.stfmr_controllers["vna"].on_read[3] = a.zero_stfmr_kepco_output
        self.drive(a, a.start_stfmr_fmr_scan)
        self.assertEqual(len(a.stfmr_fmr_rows), 2, "the read under the zero is not saved")
        self.assertEqual(len(self.rows(self.folder)), 3)
        events = self.events(self.folder)
        self.assertEqual([(e["event"], e.get("button")) for e in events if e["event"] == "stop_requested"],
                         [("stop_requested", "KEPCO Zero / OFF")])
        self.assertEqual(events[-1]["outcome"], "stopped")
        self.assertEqual(a.stfmr_fmr_status_var.get(), "Stopped; 4886 and RF zeroed; 2 points saved")
        self.error.assert_not_called()

    def test_a_failure_zeroes_both_and_its_dialog_says_how(self):
        a = self.make()
        a.stfmr_controllers["vna"].fail_read_at = 2
        with mock.patch.object(a.root, "after", wraps=a.root.after) as after:
            self.drive(a, a.start_stfmr_fmr_scan)
        self.assertEqual(a.stfmr_fmr_status_var.get(), "Failed: PNA did not answer")
        self.error.assert_called_once()
        self.assertEqual(self.error.call_args.args[0], "STFMR FMR")
        self.assertEqual(self.error.call_args.args[1],
                         "PNA did not answer\n\nAt the end: 4886 zeroed; PNA RF zeroed")
        self.assertTrue(any(call.args[0] == 0 for call in after.call_args_list), "from the Tk event loop")
        end = self.events(self.folder)[-1]
        self.assertEqual((end["outcome"], end["error"], end["points_saved"]), ("error", "PNA did not answer", 1))

    def test_a_4886_that_fails_its_zero_still_gets_the_rf_off_and_check_it(self):
        a = self.make()
        self.drive(a, a.start_stfmr_fmr_scan, done=lambda: len(a.stfmr_fmr_rows) == 2)
        a.card.stall = {"OUTP OFF"}
        a.stop_stfmr_fmr_scan()
        self.drive(a, lambda: None)
        self.assertEqual(sent(a)[-1], ("pna", "rf_off"), "tried whatever the 4886 did")
        self.assertEqual(review.readout(a)[3], "OUTP: NOT CONFIRMED OFF")
        status = a.stfmr_fmr_status_var.get()
        self.assertEqual(status, "Stopped; 4886 zero FAILED; RF zeroed: check it; 2 points saved")
        self.error.assert_called_once()
        self.assertEqual(self.error.call_args.args[0], "STFMR FMR outputs")
        self.assertIn("CHECK IT: 4886 not confirmed at zero.", self.error.call_args.args[1])
        end = self.events(self.folder)[-1]
        self.assertEqual(end["outcome"], "stopped")
        self.assertTrue(end["outputs"]["4886"].startswith("zero FAILED: KEPCO 4886 zero/OFF not confirmed"))

    def test_a_log_write_that_fails_ends_the_scan_safely(self):
        a = self.make()
        self.drive(a, a.start_stfmr_fmr_scan, done=lambda: len(a.stfmr_fmr_rows) == 1)

        def full(_text):
            raise OSError(28, "No space left on device")
        a.stfmr_fmr_recorder._append = full
        self.drive(a, lambda: None)
        self.assertTrue(a.stfmr_fmr_status_var.get().startswith("Failed: The FMR log could not be written"),
                        a.stfmr_fmr_status_var.get())
        self.assertEqual(len(a.stfmr_fmr_rows), 2, "ended at the next check, before another current")
        self.assertEqual(sent(a)[-1], ("pna", "rf_off"))
        self.error.assert_called_once()

    def test_save_csv_as_opens_in_the_run_folder(self):
        a = self.make()
        self.drive(a, a.start_stfmr_fmr_scan)
        with mock.patch.object(app.filedialog, "asksaveasfilename", return_value="") as save:
            a.save_stfmr_fmr_results()
        self.assertEqual(save.call_args.kwargs["initialdir"], str(self.folder / "run A"))

    def test_closing_mid_scan_zeroes_both_and_closes_the_log(self):
        a = self.make()
        self.drive(a, a.start_stfmr_fmr_scan, done=lambda: len(a.stfmr_fmr_rows) == 2)
        a.stfmr_closed = False
        a.stfmr_poll_after_id = None
        a.stfmr_mfli_controller = mock.Mock()
        a.stfmr_executor.shutdown = lambda **_k: None
        a.append_log = lambda _text: None
        a.closing = True
        a.safe_close_stfmr()
        events = self.events(self.folder)
        self.assertEqual([e["event"] for e in events[-3:]], ["stop_requested", "outputs_zeroed", "run_finished"])
        self.assertEqual(events[-3]["button"], "program closing")
        self.assertEqual((events[-1]["outcome"], events[-1]["points_saved"]), ("closed", 2))
        self.assertIn(("pna", "rf_off"), a.log)
        a.root.run(lambda: False)       # a late callback does nothing
        self.assertEqual(len(self.events(self.folder)), len(events))

    def test_the_status_lines_fit_the_48_character_label(self):
        for status in ("Completed; 4886 and RF zeroed; 100000 points saved",
                       "Stopped; 4886 and RF zeroed; 100000 points saved",
                       "Completed; 4886 zeroed; RF zero FAILED: check it",
                       "Stopping; the 4886 and PNA RF are zeroed next…",
                       "Failed: " + app.short_status_error(RuntimeError("x" * 80), 40)):
            with self.subTest(status=status):
                self.assertLessEqual(width_in_zeros(status), 48)


class FrequencySweepTests(FmrRun):
    def test_completed_saves_a_live_csv_and_zeroes_both(self):
        a = self.make()
        self.drive(a, a.start_stfmr_frequency_scan)
        self.error.assert_not_called()
        self.assertEqual(self.order, ["yes/no", "folder", "name"])
        rows = self.rows(self.folder)
        self.assertEqual(tuple(rows[0]), a._stfmr_frequency_csv_fields())
        self.assertEqual([float(r[5]) for r in rows[1:]], [5e9, 6e9, 7e9])
        events = self.events(self.folder)
        names = [e["event"] for e in events]
        self.assertEqual(names[:2], ["run_prepared", "scan_started"])
        self.assertEqual(events[0]["settings"]["scan"], "fixed-field frequency sweep")
        feedback = [e for e in events if e["event"] == "field_feedback"]
        self.assertTrue(feedback and feedback[-1]["reached"])
        self.assertEqual(names[-5:], ["point_saved"] * 3 + ["outputs_zeroed", "run_finished"])
        self.assertEqual(events[-1]["status"], "Completed; 4886 and RF zeroed; 3 points saved")
        self.assertEqual(sent(a)[-3:], [("4886", "CURR 0.000000"), ("4886", "OUTP OFF"), ("pna", "rf_off")])
        self.assertEqual(a.log[-1], ("log", "FMR scan end: 4886 zeroed; PNA RF zeroed"))

    def test_a_blank_name_takes_the_fmr_frequency_prefix(self):
        self.ask_name.side_effect = lambda *a, **k: ""
        a = self.make()
        self.drive(a, a.start_stfmr_frequency_scan)
        (run,) = self.folder.iterdir()
        self.assertRegex(run.name, r"^FMR_frequency_\d{8}_\d{6}$")

    def test_zero_off_during_a_point_is_a_stop_not_a_drift(self):
        # review, 2026-10-18 (S5): the zeroed field read as a "drift" error.
        a = self.make()
        a.stfmr_controllers["vna"].on_read[2] = a.zero_stfmr_kepco_output
        self.drive(a, a.start_stfmr_frequency_scan)
        self.error.assert_not_called()
        self.assertEqual(a.stfmr_frequency_status_var.get(), "Stopped; 4886 and RF zeroed; 1 point saved")
        self.assertEqual(self.events(self.folder)[-1]["outcome"], "stopped")

    def test_save_csv_as_opens_in_the_run_folder(self):
        a = self.make()
        self.drive(a, a.start_stfmr_frequency_scan)
        with mock.patch.object(app.filedialog, "asksaveasfilename", return_value="") as save:
            a.save_stfmr_frequency_results()
        self.assertEqual(save.call_args.kwargs["initialdir"], str(self.folder / "run A"))


class ReadBackTests(unittest.TestCase):
    def test_the_pna_rf_off_is_read_back(self):
        pna = app.STFMRPNAFMRController(lambda _text: None)
        replies = {"OUTP?": "0"}
        pna.instrument = types.SimpleNamespace(writes=[], write=lambda c: pna.instrument.writes.append(c),
                                               query=lambda c: replies[c])
        pna.rf_off_read_back()
        self.assertEqual(pna.instrument.writes, ["OUTP OFF"])
        replies["OUTP?"] = "1"
        with self.assertRaisesRegex(RuntimeError, r"did not read back OFF \(OUTP\? '1'\)"):
            pna.rf_off_read_back()

    def test_the_rf_off_tick_is_gone(self):
        with open(app.__file__, encoding="utf-8") as source:
            text = source.read()
        self.assertNotIn('text="PNA RF off at end"', text)
        self.assertNotIn("stfmr_fmr_rf_off_var", text)


if __name__ == "__main__":
    unittest.main()
