"""Scan run folders and the file-name step (user, 2026-10-18).

"like ST-FMR, pls also write a log of SHV and save results in a folder":
SHVScanRecorder makes <folder>/<name>/ with <name>.csv and <name>.log
(JSON lines).  "also add the name file step in STFMR save process": the
STFMR Start asks SHV's optional file name after its folder, and a typed
name is its run folder's and files' name.  The two pages share the name
rule and the unique folder (scan_file_stem, ask_scan_file_stem,
make_scan_run_folder).

Window-free: no Tk window, no instrument; every dialog is answered here.
SHV's Start, scans and their logs end to end are in
test_rte_angle_end_to_end (RunLogTests, ScanEndZeroTests)."""
import json
import shutil
import sys
import tempfile
import threading
import unittest
from pathlib import Path
from unittest import mock

# The shared STFMR harness lives in the test/ subfolder and imports the main
# program by module name from the folder above Other-files.
_HERE = Path(__file__).resolve().parent
for _path in (_HERE / "test", _HERE.parent):
    if str(_path) not in sys.path:
        sys.path.insert(0, str(_path))
from test_stfmr_realtime_recording import app, events, harness, read_csv


class Folder(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.folder = Path(self.tmp.name)
        guard = mock.patch("tkinter.Tk", side_effect=AssertionError("a test tried to open a Tk window"))
        guard.start()
        self.addCleanup(guard.stop)


class NameRuleTests(unittest.TestCase):
    def test_the_rule_is_shv_s(self):
        self.assertEqual(app.SCAN_FILE_NAME_PROMPT, "Optional file name (leave blank for a timestamped default):")
        for typed, stem in (("", ""), ("   ", ""), ("sample A", "sample A"), (" B1_300K.csv ", "B1_300K"),
                            ("run.CSV", "run"), ("a.b", "a.b"), ("x ", "x")):
            with self.subTest(typed=typed):
                self.assertEqual(app.scan_file_stem(typed), stem)
        for typed in ("a/b", "a\\b", "c:x", 'a"b', "a<b", "a>b", "a|b", "a?", "a*", "tab\there", ".csv", "x.",
                      "con", "CON.txt", "nul", "Com1", "LPT9.csv", "x. "):
            with self.subTest(typed=typed):
                with self.assertRaisesRegex(ValueError, "Use a valid Windows file name without a path."):
                    app.scan_file_stem(typed)

    def test_the_prompt_answers(self):
        with mock.patch.object(app.simpledialog, "askstring", return_value=None) as ask, \
                mock.patch.object(app.messagebox, "showerror") as error:
            self.assertIsNone(app.ask_scan_file_stem("root", "STFMR file name"))
        ask.assert_called_once_with("STFMR file name", app.SCAN_FILE_NAME_PROMPT, parent="root")
        error.assert_not_called()
        with mock.patch.object(app.simpledialog, "askstring", return_value="a/b"), \
                mock.patch.object(app.messagebox, "showerror") as error:
            self.assertIsNone(app.ask_scan_file_stem("root", "SHV file name"))
        error.assert_called_once_with("SHV file name", "Use a valid Windows file name without a path.", parent="root")
        with mock.patch.object(app.simpledialog, "askstring", return_value=" B1.csv"):
            self.assertEqual(app.ask_scan_file_stem("root", "SHV file name"), "B1")


class RunFolderTests(Folder):
    def test_a_name_in_use_gets_the_next_suffix_and_nothing_is_overwritten(self):
        first = app.make_scan_run_folder(self.folder, "B1")
        self.assertEqual(first, self.folder / "B1")
        (first / "B1.csv").write_text("kept", encoding="utf-8")
        (self.folder / "B1_01").write_text("a file of that name", encoding="utf-8")
        self.assertEqual(app.make_scan_run_folder(self.folder, "B1"), self.folder / "B1_02")
        self.assertEqual(app.make_scan_run_folder(self.folder, "B1"), self.folder / "B1_03")
        self.assertEqual((first / "B1.csv").read_text(encoding="utf-8"), "kept")
        self.assertEqual((self.folder / "B1_01").read_text(encoding="utf-8"), "a file of that name")
        self.assertEqual(sorted(p.name for p in first.iterdir()), ["B1.csv"])

    def test_a_folder_that_cannot_be_made_raises(self):
        with self.assertRaises(OSError):
            app.make_scan_run_folder(self.folder / "missing", "B1")
        self.assertFalse((self.folder / "missing").exists())


class SHVRecorderTests(Folder):
    def lines(self, recorder):
        with recorder.log_path.open(encoding="utf-8") as handle:
            return [json.loads(line) for line in handle]

    def test_the_folder_its_files_and_the_json_lines(self):
        recorder = app.SHVScanRecorder(self.folder, "angle")
        self.addCleanup(recorder.close, "test")
        self.assertEqual(recorder.directory, self.folder / "angle")
        self.assertEqual((recorder.csv_path, recorder.log_path),
                         (self.folder / "angle" / "angle.csv", self.folder / "angle" / "angle.log"))
        self.assertEqual(recorder.csv_path.read_bytes(), b"", "the live CSV is appended row by row, as before")
        self.assertTrue(recorder.event("run_prepared", mode="Angle", plan={"points": 2}))
        self.assertTrue(recorder.event("note", text="SHV Angle scan; ünïcode 5°"))
        self.assertEqual(recorder.close("completed", points_saved=2), [])
        lines = self.lines(recorder)
        self.assertEqual([line["event"] for line in lines], ["run_prepared", "note", "run_finished"])
        self.assertEqual(lines[0]["plan"], {"points": 2})
        self.assertEqual(lines[1]["text"], "SHV Angle scan; ünïcode 5°")
        self.assertEqual((lines[2]["outcome"], lines[2]["points_saved"]), ("completed", 2))
        self.assertTrue(all(set(line) >= {"timestamp", "event"} for line in lines))
        # Closed once: later writes and closes do nothing.
        self.assertTrue(recorder.closed)
        self.assertFalse(recorder.event("note", text="late"))
        self.assertEqual(recorder.close("again"), [])
        self.assertEqual(len(self.lines(recorder)), 3)

    def test_a_name_in_use_and_never_overwriting(self):
        old = app.SHVScanRecorder(self.folder, "angle")
        old.event("run_prepared")
        old.close("completed")
        kept = {p.name: p.read_bytes() for p in old.directory.iterdir()}
        recorder = app.SHVScanRecorder(self.folder, "angle")
        self.addCleanup(recorder.close, "test")
        self.assertEqual(recorder.directory, self.folder / "angle_01")
        self.assertEqual(sorted(p.name for p in recorder.directory.iterdir()), ["angle_01.csv", "angle_01.log"])
        self.assertEqual({p.name: p.read_bytes() for p in old.directory.iterdir()}, kept)

    def test_files_that_cannot_be_made_start_nothing(self):
        with self.assertRaises(OSError):
            app.SHVScanRecorder(self.folder / "missing", "angle")
        # A CSV already there (another program): opened "x", it is not
        # overwritten and the recorder is not made.
        directory = self.folder / "angle"
        real = app.make_scan_run_folder

        def taken(parent, stem):
            made = real(parent, stem)
            (made / f"{made.name}.csv").write_text("someone else's", encoding="utf-8")
            return made
        with mock.patch.object(app, "make_scan_run_folder", taken), self.assertRaises(FileExistsError):
            app.SHVScanRecorder(self.folder, "angle")
        self.assertEqual((directory / "angle.csv").read_text(encoding="utf-8"), "someone else's")
        self.assertFalse((directory / "angle.log").exists())

    def test_no_handle_is_held_between_records(self):
        # As the live CSV: each record opens, writes, fsyncs and closes the
        # log, so the folder can be copied or removed mid-scan on Windows.
        recorder = app.SHVScanRecorder(self.folder, "angle")
        self.assertTrue(recorder.event("run_prepared"))
        shutil.rmtree(recorder.directory)
        self.assertFalse(recorder.event("note", text="the folder is gone"))
        with self.assertRaisesRegex(RuntimeError, "The SHV log could not be written"):
            recorder.check()
        self.assertEqual(len(recorder.close("error")), 1)

    def test_a_failed_write_never_raises_and_is_kept_for_the_worker(self):
        recorder = app.SHVScanRecorder(self.folder, "angle")
        self.addCleanup(recorder.close, "test")

        def full(_text):
            raise OSError(28, "No space left on device")
        recorder._append = full
        recorder.check()        # nothing failed yet
        self.assertFalse(recorder.event("stage_move", px=1))
        self.assertFalse(recorder.event("point_saved", index=1))
        with self.assertRaisesRegex(RuntimeError, r"The SHV log could not be written \(\[Errno 28\] No space left "
                                                  r"on device\); the scan stopped"):
            recorder.check()
        errors = recorder.close("error")
        self.assertEqual(len(errors), 1)
        self.assertIn("Failed to write the closing log entry", errors[0])
        self.assertTrue(recorder.closed, "closed all the same")
        self.assertEqual(recorder.close("again"), [])

    def test_writes_from_two_threads_are_whole_lines(self):
        recorder = app.SHVScanRecorder(self.folder, "angle")
        self.addCleanup(recorder.close, "test")
        start = threading.Barrier(3)

        def write(name):
            start.wait()
            for index in range(300):
                recorder.event(name, index=index, text="x" * 200)
        threads = [threading.Thread(target=write, args=(name,)) for name in ("worker", "tk")]
        for thread in threads:
            thread.start()
        start.wait()
        for thread in threads:
            thread.join(timeout=60)
        recorder.close("completed")
        lines = self.lines(recorder)
        self.assertEqual(len(lines), 601)
        for name in ("worker", "tk"):
            self.assertEqual([line["index"] for line in lines if line["event"] == name], list(range(300)))
        self.assertIsNone(recorder.failure)


class STFMRNameStepTests(Folder):
    """The STFMR Start's file-name step, on the shared STFMR harness (stand-in
    instruments; the sweep runs to its end on the harness's Tk loop)."""

    def start(self, typed):
        order = []
        h = harness(self.tmp.name)

        def folder(*_args):
            order.append("folder")
            return self.folder
        h.select_csv_save_directory = folder

        def ask(title, prompt, parent=None):
            order.append(("name", title, prompt))
            return typed
        with mock.patch.object(app.simpledialog, "askstring", side_effect=ask), \
                mock.patch.object(app.messagebox, "askyesno", return_value=True) as confirm, \
                mock.patch.object(app.messagebox, "showerror") as error:
            h.start_stfmr_lockin_scan()
            recorder = h.stfmr_lockin_recorder
            if recorder is not None:
                h.root.drain()      # the sweep runs to its end and closes its files
        return h, recorder, order, confirm, error

    def test_a_typed_name_is_the_run_folder_and_its_files(self):
        h, recorder, order, confirm, error = self.start("sweep A.csv")
        error.assert_not_called()
        self.assertEqual(order, ["folder", ("name", "STFMR file name", app.SCAN_FILE_NAME_PROMPT)],
                         "the name after the folder, before the confirmation")
        confirm.assert_called_once()
        self.assertEqual(recorder.directory, self.folder / "sweep A")
        self.assertEqual((recorder.csv_path.name, recorder.log_path.name), ("sweep A.csv", "sweep A.log"))
        self.assertEqual(sorted(p.name for p in recorder.directory.iterdir()), ["sweep A.csv", "sweep A.log"])
        self.assertTrue(recorder.closed)
        history = events(recorder)
        self.assertEqual(history[0]["event"], "run_prepared")
        self.assertEqual(history[0]["settings"]["file_name"], "sweep A")
        self.assertEqual(history[-1]["event"], "run_finished")
        # The columns are the recorder's, as before.
        rows = read_csv(recorder.csv_path)
        self.assertTrue(rows)
        self.assertEqual(set(rows[0]), set(app.STFMRScanRecorder.FIELDNAMES))
        self.assertEqual(h.stfmr_lockin_save_path_var.get(), str(self.folder / "sweep A"))

    def test_a_blank_name_keeps_the_timestamped_default(self):
        _h, recorder, _order, _confirm, error = self.start("  ")
        error.assert_not_called()
        self.assertRegex(recorder.directory.name, r"^STFMR_field_scan_\d{8}_\d{6}_\d{6}$")
        self.assertEqual(recorder.csv_path.name, f"{recorder.directory.name}.csv")
        self.assertEqual(recorder.log_path.name, f"{recorder.directory.name}.log")
        self.assertEqual(events(recorder)[0]["settings"]["file_name"], "")

    def test_a_name_in_use_gets_a_suffix(self):
        (self.folder / "B1").mkdir()
        (self.folder / "B1" / "B1.csv").write_text("an earlier run", encoding="utf-8")
        _h, recorder, _order, _confirm, error = self.start("B1")
        error.assert_not_called()
        self.assertEqual(recorder.directory, self.folder / "B1_01")
        self.assertEqual(sorted(p.name for p in recorder.directory.iterdir()), ["B1_01.csv", "B1_01.log"])
        self.assertEqual((self.folder / "B1" / "B1.csv").read_text(encoding="utf-8"), "an earlier run")
        self.assertEqual(sorted(p.name for p in (self.folder / "B1").iterdir()), ["B1.csv"])

    def test_an_invalid_name_is_refused_and_starts_nothing(self):
        for typed in ("a/b", "CON", "x."):
            with self.subTest(typed=typed):
                h, recorder, _order, confirm, error = self.start(typed)
                self.assertIsNone(recorder)
                error.assert_called_once_with("STFMR file name", "Use a valid Windows file name without a path.",
                                              parent=h.root)
                confirm.assert_not_called()
                self.assertFalse(h.stfmr_lockin_running)
                self.assertTrue(all(not device.calls for device in h.stfmr_lockin_controllers.values()),
                                "no instrument was touched")
                self.assertEqual(list(self.folder.iterdir()), [], "nothing made")

    def test_cancel_starts_nothing(self):
        h, recorder, _order, confirm, error = self.start(None)
        self.assertIsNone(recorder)
        error.assert_not_called()
        confirm.assert_not_called()
        self.assertFalse(h.stfmr_lockin_running)
        self.assertTrue(all(not device.calls for device in h.stfmr_lockin_controllers.values()))
        self.assertEqual(list(self.folder.iterdir()), [])


if __name__ == "__main__":
    unittest.main()
