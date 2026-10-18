"""The shared scan run base (user, 2026-10-18: "make the operation logic and
save logic and files in different module like the same as possible").

ScanRunRecorder (the run folder, the live CSV and the JSON-lines log of every
scan page), begin_scan_run (a Start's folder and file-name step),
zero_scan_outputs and scan_end_outputs_text (every scan's end), and SHV's and
STFMR's files and log lines unchanged on the shared class.

Window-free: no Tk window, no instrument; every dialog is answered here."""
import csv
import datetime
import json
import shutil
import sys
import tempfile
import threading
import types
import unittest
from pathlib import Path
from unittest import mock

_HERE = Path(__file__).resolve().parent
if str(_HERE.parent) not in sys.path:
    sys.path.insert(0, str(_HERE.parent))
import Transport_all_NH_dev as app

BOM = "﻿".encode("utf-8")


class Folder(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.folder = Path(self.tmp.name)
        guard = mock.patch("tkinter.Tk", side_effect=AssertionError("a test tried to open a Tk window"))
        guard.start()
        self.addCleanup(guard.stop)

    def recorder(self, name="B1", settings=None, page="PPMS", **kwargs):
        recorder = app.ScanRunRecorder(self.folder, page, name, {"mode": "Field"} if settings is None else settings,
                                       **kwargs)
        self.addCleanup(recorder.close, "test")
        return recorder

    @staticmethod
    def lines(recorder):
        with recorder.log_path.open(encoding="utf-8") as handle:
            return [json.loads(line) for line in handle]

    @staticmethod
    def rows(recorder):
        with recorder.csv_path.open(encoding="utf-8-sig", newline="") as handle:
            return list(csv.reader(handle))


class RecorderFileTests(Folder):
    def test_the_folder_its_files_and_run_prepared(self):
        recorder = self.recorder("B1", {"mode": "Field", "points": 3, "event": "a setting, not the event"})
        self.assertEqual(recorder.directory, self.folder / "B1")
        self.assertEqual((recorder.csv_path, recorder.log_path),
                         (self.folder / "B1" / "B1.csv", self.folder / "B1" / "B1.log"))
        self.assertEqual(sorted(p.name for p in recorder.directory.iterdir()), ["B1.csv", "B1.log"])
        self.assertEqual(recorder.csv_path.read_bytes(), b"", "the header comes with the first row")
        self.assertEqual(recorder.rows_written, 0)
        self.assertIsNone(recorder.fieldnames)
        (prepared,) = self.lines(recorder)
        self.assertEqual(prepared["event"], "run_prepared")
        datetime.datetime.fromisoformat(prepared["timestamp"])
        self.assertEqual((prepared["program"], prepared["app_version"]), ("Transport_all_NH_dev.py", app.APP_VERSION))
        self.assertTrue(prepared["python_version"])
        self.assertEqual((prepared["file_name"], prepared["run_folder"], prepared["csv_path"], prepared["log_path"]),
                         ("B1", str(recorder.directory), str(recorder.csv_path), str(recorder.log_path)))
        self.assertEqual(prepared["settings"], {"mode": "Field", "points": 3, "event": "a setting, not the event"})

    def test_a_blank_name_is_the_page_s_timestamped_default(self):
        recorder = self.recorder("")
        self.assertRegex(recorder.directory.name, r"^PPMS_\d{8}_\d{6}$")
        self.assertEqual(recorder.csv_path.name, f"{recorder.directory.name}.csv")
        self.assertEqual(self.lines(recorder)[0]["file_name"], "")

    def test_without_settings_the_page_writes_run_prepared(self):
        recorder = app.ScanRunRecorder(self.folder, "PPMS", "B1")
        self.addCleanup(recorder.close, "test")
        self.assertEqual(recorder.log_path.read_bytes(), b"")

    def test_a_name_in_use_gets_a_suffix_and_nothing_is_overwritten(self):
        old = self.recorder("B1")
        old.append_row({"a": 1})
        old.close("completed")
        kept = {p.name: p.read_bytes() for p in old.directory.iterdir()}
        (self.folder / "B1_01").write_text("a file of that name", encoding="utf-8")
        recorder = self.recorder("B1")
        self.assertEqual(recorder.directory, self.folder / "B1_02")
        self.assertEqual(sorted(p.name for p in recorder.directory.iterdir()), ["B1_02.csv", "B1_02.log"])
        self.assertEqual({p.name: p.read_bytes() for p in old.directory.iterdir()}, kept)
        self.assertEqual((self.folder / "B1_01").read_text(encoding="utf-8"), "a file of that name")

    def test_files_that_cannot_be_made_start_nothing(self):
        with self.assertRaises(OSError):
            app.ScanRunRecorder(self.folder / "missing", "PPMS", "B1", {})
        self.assertFalse((self.folder / "missing").exists())
        # A CSV already there (another program): opened "x", it is kept and
        # no log is made.
        real = app.make_scan_run_folder

        def taken(parent, stem):
            made = real(parent, stem)
            (made / f"{made.name}.csv").write_text("someone else's", encoding="utf-8")
            return made
        with mock.patch.object(app, "make_scan_run_folder", taken), self.assertRaises(FileExistsError):
            app.ScanRunRecorder(self.folder, "PPMS", "B1", {})
        self.assertEqual((self.folder / "B1" / "B1.csv").read_text(encoding="utf-8"), "someone else's")
        self.assertFalse((self.folder / "B1" / "B1.log").exists())

    def test_a_prefix_that_is_no_file_name_is_refused_before_anything_is_made(self):
        with self.assertRaisesRegex(ValueError, "Use a valid Windows file name"):
            app.ScanRunRecorder(self.folder, "|R| vs time", "", {})
        self.assertEqual(list(self.folder.iterdir()), [])

    def test_a_first_line_that_cannot_be_written_starts_nothing(self):
        # As SHV's Start: the run is closed not_started and the error raised.
        real = app.scan_log_line

        def line(name, details):
            if name == "run_prepared":
                raise OSError(28, "No space left on device")
            return real(name, details)
        with mock.patch.object(app, "scan_log_line", line), \
                self.assertRaisesRegex(OSError, "No space left on device"):
            app.ScanRunRecorder(self.folder, "PPMS", "B1", {})
        with (self.folder / "B1" / "B1.log").open(encoding="utf-8") as handle:
            (closing,) = [json.loads(text) for text in handle]
        self.assertEqual((closing["event"], closing["outcome"]), ("run_finished", "not_started"))
        self.assertIn("No space left on device", closing["error"])


class RecorderCSVTests(Folder):
    def test_columns_are_the_first_row_s_padded_and_never_shifted(self):
        recorder = self.recorder()
        recorder.append_row({"index": 1, "field_oe": 10.0, "x_v": 1e-6})
        self.assertEqual(recorder.fieldnames, ["index", "field_oe", "x_v"])
        # Missing cells are blank and the order is the header's.
        recorder.append_row({"x_v": 2e-6, "index": 2})
        recorder.append_row({"index": 3, "field_oe": None, "x_v": "ünï 5°"})
        # A key the header lacks: nothing written, the scan's failure path.
        before = recorder.csv_path.read_bytes()
        with self.assertRaisesRegex(ValueError, "has no column for y_v; the row was not written"):
            recorder.append_row({"index": 4, "y_v": 1.0})
        self.assertEqual(recorder.csv_path.read_bytes(), before)
        recorder.append_row({"index": 5, "field_oe": 50.0, "x_v": 5e-6})
        self.assertEqual(recorder.rows_written, 4)
        self.assertEqual(self.rows(recorder), [["index", "field_oe", "x_v"], ["1", "10.0", "1e-06"],
                                               ["2", "", "2e-06"], ["3", "", "ünï 5°"], ["5", "50.0", "5e-06"]])
        data = recorder.csv_path.read_bytes()
        self.assertTrue(data.startswith(BOM + b"index,field_oe,x_v\r\n"), "Excel's BOM, once")
        self.assertEqual(data.count(BOM), 1)

    def test_each_row_is_fsynced_once(self):
        recorder = self.recorder()
        with mock.patch.object(app.os, "fsync", wraps=app.os.fsync) as sync:
            recorder.append_row({"index": 1})
            self.assertEqual(sync.call_count, 1)
            recorder.append_row({"index": 2})
            self.assertEqual(sync.call_count, 2)

    def test_a_row_that_cannot_be_written_raises(self):
        recorder = self.recorder()
        recorder.append_row({"index": 1})
        shutil.rmtree(recorder.directory)
        with self.assertRaises(OSError):
            recorder.append_row({"index": 2})
        self.assertEqual(recorder.rows_written, 1)

    def test_a_fixed_header_is_written_with_the_files(self):
        class Fixed(app.ScanRunRecorder):
            FIELDNAMES = ("index", "x_v")
        recorder = Fixed(self.folder, "PPMS", "B1", {})
        self.addCleanup(recorder.close, "test")
        self.assertEqual(self.rows(recorder), [["index", "x_v"]])
        recorder.append_row({"x_v": 1.0})
        self.assertEqual(self.rows(recorder), [["index", "x_v"], ["", "1.0"]])
        with self.assertRaises(ValueError):
            recorder.append_row({"y_v": 1.0})


class RecorderLogTests(Folder):
    def test_events_notes_and_a_close_that_runs_once(self):
        recorder = self.recorder()
        self.assertTrue(recorder.event("scan_started", points=2))
        self.assertTrue(recorder.note("PPMS Field scan; ünïcode 5°"))
        # The event's name is positional only: a detail may be called "name".
        self.assertTrue(recorder.event("point_saved", index=1, name="SR830-1"))
        self.assertTrue(recorder.event("stop_requested", button="Stop"))
        self.assertTrue(recorder.event("outputs_zeroed", **{"6221": "zeroed", "KEPCO": "zeroed"}))
        self.assertEqual(recorder.close("stopped", points_saved=1, status="Stopped; KEPCO and 6221 zeroed"), [])
        lines = self.lines(recorder)
        self.assertEqual([line["event"] for line in lines], ["run_prepared", "scan_started", "note", "point_saved",
                                                            "stop_requested", "outputs_zeroed", "run_finished"])
        self.assertEqual(lines[2]["text"], "PPMS Field scan; ünïcode 5°")
        self.assertEqual(lines[3]["name"], "SR830-1")
        self.assertEqual((lines[5]["6221"], lines[5]["KEPCO"]), ("zeroed", "zeroed"))
        self.assertEqual((lines[-1]["outcome"], lines[-1]["points_saved"], lines[-1]["status"]),
                         ("stopped", 1, "Stopped; KEPCO and 6221 zeroed"))
        # Closed once: later writes do nothing, a row raises, a close is [].
        self.assertTrue(recorder.closed)
        self.assertFalse(recorder.event("note", text="late"))
        self.assertFalse(recorder.note("late"))
        with self.assertRaisesRegex(RuntimeError, "The PPMS live CSV is closed"):
            recorder.append_row({"index": 2})
        self.assertEqual(recorder.close("again"), [])
        self.assertEqual(len(self.lines(recorder)), 7)
        recorder.check()        # nothing failed

    def test_a_failed_write_never_raises_and_check_raises_the_first(self):
        recorder = self.recorder(label="Non-local 6221-IV", page="NL_6221_IV")
        failures = [OSError(28, "No space left on device"), OSError(5, "Input/output error")]

        def full(_text):
            raise failures.pop(0) if failures else OSError("later")
        recorder._append = full
        recorder.check()        # nothing failed yet
        self.assertFalse(recorder.event("point_saved", index=1))
        self.assertFalse(recorder.note("a line"))
        with self.assertRaisesRegex(RuntimeError, r"^The Non-local 6221-IV log could not be written \(\[Errno 28\] "
                                                  r"No space left on device\); the scan stopped$") as raised:
            recorder.check()
        self.assertEqual(raised.exception.__cause__.errno, 28, "the first failure is kept")
        errors = recorder.close("error")
        self.assertEqual(len(errors), 1)
        self.assertIn("Failed to write the closing log entry", errors[0])
        self.assertTrue(recorder.closed, "closed all the same")
        self.assertEqual(recorder.close("again"), [])

    def test_no_handle_is_held_between_writes(self):
        # Each write opens, fsyncs and closes its file, so the lab PC
        # (Windows) can copy or remove the folder mid-scan.
        opened = []
        real_open = Path.open

        def tracked(path, *args, **kwargs):
            handle = real_open(path, *args, **kwargs)
            opened.append(handle)
            return handle
        with mock.patch.object(Path, "open", tracked):
            recorder = self.recorder()
            self.assertTrue(opened)
            for write in (lambda: recorder.append_row({"index": 1}), lambda: recorder.event("point_saved", index=1),
                          lambda: recorder.note("a line"), lambda: recorder.close("completed")):
                write()
                self.assertTrue(all(handle.closed for handle in opened))
        self.assertEqual(len(opened), 7, "the two files made, run_prepared, then one open per write")
        recorder = self.recorder("B2")
        recorder.append_row({"index": 1})
        shutil.rmtree(recorder.directory)
        self.assertFalse(recorder.event("note", text="the folder is gone"))
        with self.assertRaisesRegex(RuntimeError, "The PPMS log could not be written"):
            recorder.check()

    def test_two_threads_write_whole_lines_and_rows(self):
        recorder = self.recorder()
        start = threading.Barrier(3)

        def write(name):
            start.wait()
            for index in range(200):
                recorder.event(name, index=index, text="x" * 200)
                recorder.append_row({"thread": name, "index": index, "text": "y" * 200})
        threads = [threading.Thread(target=write, args=(name,)) for name in ("worker", "tk")]
        for thread in threads:
            thread.start()
        start.wait()
        for thread in threads:
            thread.join(timeout=60)
        self.assertEqual(recorder.close("completed", points_saved=recorder.rows_written), [])
        lines = self.lines(recorder)
        self.assertEqual(len(lines), 402)
        rows = self.rows(recorder)
        self.assertEqual(rows[0], ["thread", "index", "text"])
        self.assertEqual(len(rows), 401)
        for name in ("worker", "tk"):
            self.assertEqual([line["index"] for line in lines if line["event"] == name], list(range(200)))
            self.assertEqual([int(row[1]) for row in rows[1:] if row[0] == name], list(range(200)))
        self.assertTrue(all(row[2] == "y" * 200 for row in rows[1:]))
        self.assertEqual((recorder.rows_written, lines[-1]["points_saved"]), (400, 400))
        self.assertIsNone(recorder.failure)


class BeginScanRunTests(Folder):
    def begin(self, folder="", name="", initial_dir=None, settings=None, **kwargs):
        calls = []

        def directory(**options):
            calls.append(("folder", options))
            return str(folder) if folder != "" else ""

        def ask(title, prompt, parent=None):
            calls.append(("name", title, prompt, parent))
            return name
        with mock.patch.object(app.filedialog, "askdirectory", side_effect=directory), \
                mock.patch.object(app.simpledialog, "askstring", side_effect=ask), \
                mock.patch.object(app.messagebox, "showerror") as error:
            recorder = app.begin_scan_run("root", kwargs.pop("label", "PPMS"), "PPMS live CSV folder", initial_dir,
                                          {"mode": "Field"} if settings is None else settings, **kwargs)
        if recorder is not None:
            self.addCleanup(recorder.close, "test")
        return recorder, calls, error

    def test_the_folder_then_the_name_then_the_run(self):
        recorder, calls, error = self.begin(self.folder, " B1.csv ", initial_dir=self.folder / "last")
        error.assert_not_called()
        self.assertEqual(calls, [("folder", {"parent": "root", "title": "PPMS live CSV folder",
                                             "initialdir": str(self.folder / "last")}),
                                 ("name", "PPMS file name", app.SCAN_FILE_NAME_PROMPT, "root")])
        self.assertIsInstance(recorder, app.ScanRunRecorder)
        self.assertEqual(recorder.directory, self.folder / "B1")
        self.assertEqual(sorted(p.name for p in recorder.directory.iterdir()), ["B1.csv", "B1.log"])
        (prepared,) = self.lines(recorder)
        self.assertEqual((prepared["event"], prepared["file_name"], prepared["settings"]),
                         ("run_prepared", "B1", {"mode": "Field"}))
        # The label names the page in the recorder's messages.
        recorder.failure = OSError("x")
        with self.assertRaisesRegex(RuntimeError, "^The PPMS log could not be written"):
            recorder.check()
        recorder.failure = None

    def test_no_initial_folder_is_passed_when_there_is_none(self):
        recorder, calls, _error = self.begin(self.folder, "")
        self.assertEqual(calls[0], ("folder", {"parent": "root", "title": "PPMS live CSV folder"}))
        self.assertRegex(recorder.directory.name, r"^PPMS_\d{8}_\d{6}$")

    def test_a_cancelled_folder_makes_nothing_and_asks_no_name(self):
        recorder, calls, error = self.begin("")
        self.assertIsNone(recorder)
        self.assertEqual([call[0] for call in calls], ["folder"])
        error.assert_not_called()
        self.assertEqual(list(self.folder.iterdir()), [])

    def test_a_cancelled_name_makes_nothing(self):
        recorder, calls, error = self.begin(self.folder, None)
        self.assertIsNone(recorder)
        self.assertEqual([call[0] for call in calls], ["folder", "name"])
        error.assert_not_called()
        self.assertEqual(list(self.folder.iterdir()), [])

    def test_a_refused_name_is_shown_and_makes_nothing(self):
        for typed in ("a/b", "CON", "x."):
            with self.subTest(typed=typed):
                recorder, _calls, error = self.begin(self.folder, typed)
                self.assertIsNone(recorder)
                error.assert_called_once_with("PPMS file name", "Use a valid Windows file name without a path.",
                                              parent="root")
                self.assertEqual(list(self.folder.iterdir()), [])

    def test_a_name_in_use_gets_a_suffix(self):
        (self.folder / "B1").mkdir()
        (self.folder / "B1" / "B1.csv").write_text("an earlier run", encoding="utf-8")
        recorder, _calls, error = self.begin(self.folder, "B1")
        error.assert_not_called()
        self.assertEqual(recorder.directory, self.folder / "B1_01")
        self.assertEqual(sorted(p.name for p in recorder.directory.iterdir()), ["B1_01.csv", "B1_01.log"])
        self.assertEqual(sorted(p.name for p in (self.folder / "B1").iterdir()), ["B1.csv"])
        self.assertEqual((self.folder / "B1" / "B1.csv").read_text(encoding="utf-8"), "an earlier run")

    def test_a_folder_that_cannot_be_made_is_shown_and_starts_nothing(self):
        missing = self.folder / "missing"
        recorder, _calls, error = self.begin(missing, "B1")
        self.assertIsNone(recorder)
        error.assert_called_once()
        self.assertEqual(error.call_args.args[0], "PPMS run folder")
        self.assertTrue(error.call_args.args[1].startswith(
            "Could not create the run folder and its files; the scan was not started: "))
        self.assertEqual(error.call_args.kwargs, {"parent": "root"})
        self.assertFalse(missing.exists())

    def test_a_first_line_that_cannot_be_written_is_shown_and_starts_nothing(self):
        real = app.scan_log_line

        def line(name, details):
            if name == "run_prepared":
                raise OSError(28, "No space left on device")
            return real(name, details)
        with mock.patch.object(app, "scan_log_line", line):
            recorder, _calls, error = self.begin(self.folder, "B1")
        self.assertIsNone(recorder)
        self.assertEqual(error.call_args.args[0], "PPMS run folder")
        self.assertIn("No space left on device", error.call_args.args[1])

    def test_a_label_that_is_no_file_name_needs_a_prefix(self):
        recorder, _calls, error = self.begin(self.folder, "", label="|R| vs time", prefix="R_vs_time")
        error.assert_not_called()
        self.assertRegex(recorder.directory.name, r"^R_vs_time_\d{8}_\d{6}$")
        self.assertEqual(recorder.label, "|R| vs time")
        shutil.rmtree(recorder.directory)
        # Without one, only a blank name fails, shown, with nothing made.
        recorder, _calls, error = self.begin(self.folder, "", label="|R| vs time")
        self.assertIsNone(recorder)
        self.assertEqual(error.call_args.args[0], "|R| vs time run folder")
        self.assertEqual(list(self.folder.iterdir()), [])
        recorder, _calls, error = self.begin(self.folder, "run A", label="|R| vs time")
        error.assert_not_called()
        self.assertEqual(recorder.directory, self.folder / "run A")


class ScanEndTests(unittest.TestCase):
    def test_shv_s_texts_are_unchanged(self):
        # The SHV page's texts, which CI measures (RTEStatusRowTests).
        shv = {"together": "KEPCO and 6221", "outputs": ("6221", "KEPCO")}
        text = app.scan_end_outputs_text
        for results, check, expected in (
                ({"6221": "zeroed", "KEPCO": "zeroed"}, True, "KEPCO and 6221 zeroed"),
                ({"6221": "zeroed", "KEPCO": "not connected"}, True, "6221 zeroed; KEPCO not connected"),
                ({"6221": "zeroed", "KEPCO": "zero FAILED: x"}, True, "6221 zeroed; KEPCO zero FAILED: check it"),
                ({"6221": "zeroed", "KEPCO": "zero FAILED: x"}, False, "6221 zeroed; KEPCO zero FAILED"),
                ({"6221": "zero FAILED: x", "KEPCO": "zeroed"}, True, "6221 zero FAILED; KEPCO zeroed: check it"),
                ({"6221": "zero FAILED: x", "KEPCO": "not connected"}, True,
                 "6221 zero FAILED; KEPCO not connected: check it"),
                ({"6221": "zero FAILED: x", "KEPCO": "zero FAILED: y"}, True, "zero FAILED: 6221 and KEPCO: check them"),
                ({"6221": "zero FAILED: x", "KEPCO": "zero FAILED: y"}, False, "zero FAILED: 6221 and KEPCO"),
                (None, True, "KEPCO and 6221 not confirmed zeroed: check them"),
                ({}, False, "KEPCO and 6221 not confirmed zeroed")):
            with self.subTest(results=results, check=check):
                self.assertEqual(text(results, check=check, **shv), expected)
                old = {"6221": results["6221"], "kepco": results["KEPCO"]} if results else results
                self.assertEqual(app.rte_outputs_text(old, check=check), expected)

    def test_any_page_s_outputs(self):
        text = app.scan_end_outputs_text
        self.assertEqual(text({"6221": "zeroed"}), "6221 zeroed")
        self.assertEqual(text({"6221": "zero FAILED: x"}), "6221 zero FAILED: check it")
        self.assertEqual(text({"6221": "not connected"}), "6221 not connected")
        self.assertEqual(text(None, ("2400",)), "2400 not confirmed zeroed: check it")
        self.assertEqual(text(None, ("2400",), check=False), "2400 not confirmed zeroed")
        self.assertEqual(text(None), "the outputs not confirmed zeroed: check them")
        three = ("6221", "2400", "KEPCO")
        self.assertEqual(text(dict.fromkeys(three, "zeroed")), "6221, 2400 and KEPCO zeroed")
        self.assertEqual(text({"6221": "zeroed", "2400": "zero FAILED: y", "KEPCO": "not connected"}),
                         "6221 zeroed; 2400 zero FAILED; KEPCO not connected: check it")
        self.assertEqual(text({"6221": "zero FAILED: x", "2400": "zero FAILED: y", "KEPCO": "zeroed"}),
                         "6221 zero FAILED; 2400 zero FAILED; KEPCO zeroed: check them")
        self.assertEqual(text(dict.fromkeys(three, "zero FAILED: z")), "zero FAILED: 6221, 2400 and KEPCO: check them")
        self.assertEqual(text(dict.fromkeys(three, "zero FAILED: z"), check=False), "zero FAILED: 6221, 2400 and KEPCO")
        # A label without a result counts as failed; the order is outputs'.
        self.assertEqual(text({"KEPCO": "zeroed"}, ("6221", "KEPCO")), "6221 zero FAILED; KEPCO zeroed: check it")
        self.assertEqual(text({"KEPCO": "zeroed", "6221": "zeroed"}, together="the magnet and 6221"),
                         "the magnet and 6221 zeroed")

    def test_each_output_is_tried_whatever_the_others_did(self):
        order = []

        def step(label, outcome=None, error=None):
            def zero():
                order.append(label)
                if error is not None:
                    raise error
                return outcome
            return label, zero
        results = app.zero_scan_outputs((step("6221", error=RuntimeError("the 6221 output did not read back OFF")),
                                         step("KEPCO", outcome="not connected"), step("2400", outcome=0.0),
                                         step("magnet", error=OSError("VISA timeout"))))
        self.assertEqual(order, ["6221", "KEPCO", "2400", "magnet"])
        self.assertEqual(results, {"6221": "zero FAILED: the 6221 output did not read back OFF",
                                   "KEPCO": "not connected", "2400": "zeroed", "magnet": "zero FAILED: VISA timeout"})
        self.assertEqual(list(results), ["6221", "KEPCO", "2400", "magnet"])
        self.assertEqual(app.scan_end_outputs_text(results),
                         "6221 zero FAILED; KEPCO not connected; 2400 zeroed; magnet zero FAILED: check them")


# SHV's and STFMR's recorders as they were at 5380ea8, before the shared class,
# kept here only to show that their files and log lines are unchanged.  A page
# that changes its recorder on purpose updates or drops its comparison.
class OldSTFMRScanRecorder:
    FIELDNAMES = app.STFMRScanRecorder.FIELDNAMES

    def __init__(self, parent, settings, name=""):
        if name:
            self.directory = app.make_scan_run_folder(Path(parent), name)
            stem = self.directory.name
        else:
            stamp = app.datetime_module.datetime.now().strftime("%Y%m%d_%H%M%S_%f")
            stem = f"STFMR_field_scan_{stamp}"
            self.directory = Path(parent) / stem
        self.csv_path = self.directory / f"{stem}.csv"
        self.log_path = self.directory / f"{stem}.log"
        self.csv_handle = self.log_handle = None
        self.rows_written = 0
        self.closed = False
        if not name:
            self.directory.mkdir()
        self.log_handle = self.log_path.open("x", encoding="utf-8")
        self.csv_handle = self.csv_path.open("x", newline="", encoding="utf-8-sig")
        self.writer = csv.DictWriter(self.csv_handle, fieldnames=self.FIELDNAMES)
        self.writer.writeheader()
        self._sync(self.csv_handle)
        self.event("run_prepared", csv_path=str(self.csv_path), settings=settings)

    @staticmethod
    def _sync(handle):
        handle.flush()
        app.os.fsync(handle.fileno())

    def event(self, name, **details):
        if self.closed:
            raise RuntimeError("STFMR log is closed")
        self.log_handle.write(app.scan_log_line(name, details))
        self._sync(self.log_handle)

    def append_point(self, row):
        if self.closed:
            raise RuntimeError("STFMR live CSV is closed")
        self.writer.writerow(row)
        self._sync(self.csv_handle)
        self.rows_written += 1

    def close(self, outcome, **details):
        if self.closed:
            return []
        errors = []
        try:
            self.event("run_finished", outcome=outcome, saved_points=self.rows_written, **details)
        except BaseException as error:
            errors.append(f"Failed to write the closing log entry: {error}")
        self.closed = True
        for name, handle in (("CSV", self.csv_handle), ("log", self.log_handle)):
            self._sync(handle)
            handle.close()
        return errors


class OldSHVScanRecorder:
    def __init__(self, parent, stem):
        self.directory = app.make_scan_run_folder(Path(parent), stem)
        name = self.directory.name
        self.csv_path = self.directory / f"{name}.csv"
        self.log_path = self.directory / f"{name}.log"
        self.lock = threading.Lock()
        self.failure = None
        self.closed = False
        for path, encoding in ((self.csv_path, "utf-8-sig"), (self.log_path, "utf-8")):
            with path.open("x", encoding=encoding, newline=""):
                pass

    def _append(self, text):
        with self.log_path.open("a", encoding="utf-8") as handle:
            handle.write(text)
            handle.flush()
            app.os.fsync(handle.fileno())

    def event(self, name, **details):
        with self.lock:
            if self.closed:
                return False
            try:
                self._append(app.scan_log_line(name, details))
                return True
            except Exception as error:
                if self.failure is None:
                    self.failure = error
                return False

    def check(self):
        error = self.failure
        if error is not None:
            raise RuntimeError(f"The SHV log could not be written ({error}); the scan stopped") from error

    def close(self, outcome, **details):
        with self.lock:
            if self.closed:
                return []
            self.closed = True
            try:
                self._append(app.scan_log_line("run_finished", dict(outcome=outcome, **details)))
            except Exception as error:
                return [f"Failed to write the closing log entry: {error}"]
            return []


class FixedClock(datetime.datetime):
    @classmethod
    def now(cls, tz=None):
        return datetime.datetime(2026, 10, 18, 12, 34, 56, 789012)


class UnchangedOutputTests(Folder):
    """The same calls on the old and the shared recorders, on one fixed
    clock, in two folders: the same files, byte for byte, and the same
    answers."""

    def setUp(self):
        super().setUp()
        clock = mock.patch.object(app, "datetime_module", types.SimpleNamespace(datetime=FixedClock))
        clock.start()
        self.addCleanup(clock.stop)

    def run_both(self, script):
        outputs = []
        for kind, make in (("old", script.old), ("new", script.new)):
            parent = self.folder / kind
            parent.mkdir()
            answers = script(make, parent)
            files = {}
            for path in sorted(parent.rglob("*")):
                if path.is_file():
                    # The folder's path, as it is written in the JSON lines.
                    escaped = json.dumps(str(parent), ensure_ascii=False)[1:-1].encode("utf-8")
                    files[path.relative_to(parent).as_posix()] = path.read_bytes().replace(escaped, b"<parent>")
            outputs.append((answers, files))
        (old_answers, old_files), (new_answers, new_files) = outputs
        self.assertTrue(old_files)
        self.assertEqual(sorted(new_files), sorted(old_files))
        for name in old_files:
            self.assertEqual(new_files[name], old_files[name], name)
        self.assertEqual(new_answers, old_answers)
        return old_files

    def test_stfmr(self):
        full = {name: index for index, name in enumerate(app.STFMRScanRecorder.FIELDNAMES)}
        full.update(meter="MFLI", reference_valid=True, timestamp_iso="2026-10-18T12:34:56", mfli_input_overload=None)

        def script(make, parent):
            answers = []
            for name in ("", "sweep A", "sweep A"):
                recorder = make(parent, {"meter": "MFLI", "file_name": name, "angles_deg": [0.0, 5.0], "unit": "5°"},
                                name=name)
                answers.append(recorder.directory.name)
                recorder.append_point(full)
                recorder.append_point({"index": 3, "field_g": 0.0, "mfli_input_overload": False})
                with self.assertRaises(ValueError):
                    recorder.append_point({"index": 4, "not_a_column": 1})
                recorder.event("startup_point_discarded", index=1, frequency_ghz=5.0)
                answers.append(recorder.rows_written)
                answers.append(recorder.close("completed", error=None, shutdown_errors=[], outputs_off=True))
                answers.append(recorder.close("again"))
                with self.assertRaisesRegex(RuntimeError, "STFMR log is closed"):
                    recorder.event("late")
            return answers
        script.old, script.new = OldSTFMRScanRecorder, app.STFMRScanRecorder
        files = self.run_both(script)
        self.assertEqual(sorted(files), ["STFMR_field_scan_20261018_123456_789012/STFMR_field_scan_20261018_123456_789012"
                                         + suffix for suffix in (".csv", ".log")]
                         + [f"sweep A{tail}/sweep A{tail}{suffix}" for tail in ("", "_01") for suffix in (".csv", ".log")])

    def test_shv(self):
        def script(make, parent):
            answers = []
            for stem in ("angle", "angle", "SHV_20261018_123456"):
                recorder = make(parent, stem)
                answers.append(recorder.directory.name)
                answers.append(recorder.event("run_prepared", mode="Angle", plan={"points": 2}, run_folder=str(
                    recorder.directory), csv_path=str(recorder.csv_path), log_path=str(recorder.log_path)))
                answers.append(recorder.event("note", text="SHV Angle scan; ünïcode 5°"))
                answers.append(recorder.event("outputs_zeroed", **{"6221": "zeroed", "kepco": "not connected"}))
                answers.append(recorder.close("completed", error="", points_saved=2, status="Completed; KEPCO and "
                                              "6221 zeroed", outputs={"6221": "zeroed", "kepco": "zeroed"}))
                answers.append(recorder.event("note", text="late"))
                answers.append(recorder.close("again"))
            # A failed write: kept, raised by check(), the close's error.
            recorder = make(parent, "failed")

            def full(_text):
                raise OSError(28, "No space left on device")
            recorder._append = full
            answers.append(recorder.event("point_saved", index=1))
            with self.assertRaises(RuntimeError) as raised:
                recorder.check()
            answers.append(str(raised.exception))
            answers.append(recorder.close("error"))
            return answers
        script.old, script.new = OldSHVScanRecorder, app.SHVScanRecorder
        files = self.run_both(script)
        self.assertEqual(files["angle/angle.csv"], b"", "the page appends the live CSV")

    def test_the_page_recorders_are_the_shared_class(self):
        self.assertTrue(issubclass(app.SHVScanRecorder, app.ScanRunRecorder))
        self.assertTrue(issubclass(app.STFMRScanRecorder, app.ScanRunRecorder))


if __name__ == "__main__":
    unittest.main()
