"""CIDWM 6221 (audit/review, 2026-10-13).

1. 6221 pulse sign scan: set_dc_pulse sends OUTP ON and SOUR:WAVE:INIT
   before its readback checks, so a failed check (or a failed off after the
   pulse) stopped the scan without a CSV row for a pulse that may have
   fired.  Now that pulse has its row, as the Avtech's has.
2. The scan's Start (6221 source) and 6221 Connect were refused because of
   the pulse output box's |I| or Pulse count, which neither uses.  Now the
   scan reads only the box's width and compliance and Connect only its
   compliance; Apply ±I still reads (and checks) the whole box.

No Tk and no instrument: the real 6221 controller, scan worker, parse and
connect methods, with a stand-in 6221 and every dialog mocked."""
import csv
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from test_cidwm_pulse_scan import SETTINGS
from test_cidwm_pulse_scan import page as scan_page
from test_k6221_failed_off_interlock import Stuck6221
from test_k6221_failed_off_interlock import page as k6221_page
from test_nonlocal_keithley import Var, bare_app
from test_ppms_motor_debug import app


class FailedPulseRowTests(unittest.TestCase):
    def scan(self, instrument):
        controller = app.Keithley6221Controller(lambda _message: None)
        controller.instrument = instrument
        a = scan_page(controller)
        folder = Path(self.enterContext(tempfile.TemporaryDirectory())) / "run"
        folder.mkdir()
        return a, folder

    @staticmethod
    def rows(folder):
        with (folder / "run.csv").open(encoding="utf-8-sig") as file:
            return list(csv.DictReader(file))

    def test_a_failed_check_after_init_has_its_row(self):
        for failure, message in (({"wrong": True}, "6221 frequency readback mismatch"),
                                 ({"fail_on": "SOUR:WAVE:INIT"}, "VI_ERROR_TMO on SOUR:WAVE:INIT")):
            with self.subTest(**failure):
                instrument = Stuck6221(fail_off=False, **failure)
                a, folder = self.scan(instrument)
                with self.assertRaisesRegex(BaseException, message):
                    a._cidwm_pulse_scan_worker(SETTINGS, folder)
                self.assertIn("OUTP ON", instrument.commands, "the pulse may have fired")
                rows = self.rows(folder)
                self.assertEqual(len(rows), 1, "the pulse has its row")
                row = rows[0]
                self.assertEqual(
                    (row["pulse"], row["sign"], row["set_current_mA"], row["current_mA"],
                     row["pulse_width_us"], row["compliance_V"], row["in_compliance_at_readback"],
                     row["raw_tiff"]),
                    ("1", "+", "2", "", "50", "100", "", ""),
                )
                self.assertTrue(row["elapsed_s"])
                self.assertTrue(
                    row["note"].startswith(f"the scan stopped (the pulse may have fired): {message}"),
                    row["note"],
                )
                self.assertEqual(instrument.commands[-1], "OUTP OFF", "the 6221 ends off")
                self.assertTrue(a.cidwm_scan_off_confirmed)

    def test_a_failed_off_after_the_pulse_has_its_row(self):
        instrument = Stuck6221(fail_off=True)
        a, folder = self.scan(instrument)
        with self.assertRaisesRegex(RuntimeError, "failed to turn output off"):
            a._cidwm_pulse_scan_worker(SETTINGS, folder)
        rows = self.rows(folder)
        self.assertEqual(len(rows), 1)
        row = rows[0]
        self.assertEqual((row["pulse"], row["current_mA"], row["in_compliance_at_readback"]),
                         ("1", "2", "False"), "the pulse's readback")
        self.assertTrue(
            row["note"].startswith("the scan stopped after the pulse: 6221 failed to turn output off"),
            row["note"],
        )
        self.assertFalse(a.cidwm_scan_off_confirmed, "the finish says to check the 6221")


def box_page(current="150", count="1.5", width="50", compliance="100"):
    a = bare_app()
    a.k6221_controller = app.Keithley6221Controller(lambda _message: None)
    a.k6221_current_var, a.k6221_width_var = Var(current), Var(width)
    a.k6221_compliance_var, a.k6221_count_var = Var(compliance), Var(count)
    a.cidwm_scan_current_var = Var("2")
    a.cidwm_scan_pulses_var, a.cidwm_scan_signs_var, a.cidwm_scan_wait_var = Var("4"), Var("+-"), Var("1")
    return a


class ScanFieldTests(unittest.TestCase):
    def test_the_box_size_and_count_do_not_refuse_the_scan(self):
        for current, count in (("150", "1"), ("2", "1.5"), ("2", ""), ("150", "")):
            with self.subTest(current=current, count=count):
                settings = box_page(current, count)._parse_cidwm_pulse_scan()
                self.assertEqual(
                    (settings["source"], settings["current_ma"], settings["width_s"], settings["compliance_v"]),
                    ("6221", 2.0, 50e-6, 100.0),
                )

    def test_the_width_and_compliance_are_still_checked(self):
        for width, compliance, message in (("x", "100", "pulse width must be a number"),
                                           ("1", "100", "pulse width must be between"),
                                           ("50", "", "compliance must be a number"),
                                           ("50", "200", "compliance voltage must be between")):
            with self.subTest(width=width, compliance=compliance):
                with self.assertRaisesRegex(ValueError, message):
                    box_page(width=width, compliance=compliance)._parse_cidwm_pulse_scan()


def connect_page(compliance="100"):
    a = k6221_page(None)
    a.k6221_current_var, a.k6221_count_var = Var("150"), Var("1.5")
    a.k6221_compliance_var = Var(compliance)
    a.k6221_resource_var = Var("GPIB0::30::INSTR")
    a.nonlocal_controller = mock.Mock(shared_source_lockin_connected=False)
    a.nonlocal_vi_ac_controller = mock.Mock(any_connected=False)
    a.nonlocal_running = a.nonlocal_vi_running = a.nonlocal_vi_busy = False
    a._clear_k6221_readback_history = mock.Mock()
    return a


class ConnectAndApplyTests(unittest.TestCase):
    def test_connect_reads_only_the_compliance(self):
        a = connect_page()
        with mock.patch.object(a.k6221_controller, "connect", return_value="KEITHLEY INSTRUMENTS,MODEL 6221") \
                as connect, mock.patch.object(app.messagebox, "showwarning") as warning, \
                mock.patch.object(app.messagebox, "showerror"):
            a.connect_k6221()
        warning.assert_not_called()
        connect.assert_called_once_with("GPIB0::30::INSTR", 100.0)
        self.assertEqual(a.k6221_connection_var.get(), "Connected")

    def test_connect_still_checks_the_compliance(self):
        for compliance, message in (("200", "compliance voltage must be between"),
                                    ("x", "compliance must be a number")):
            with self.subTest(compliance=compliance):
                a = connect_page(compliance)
                with mock.patch.object(a.k6221_controller, "connect") as connect, \
                        mock.patch.object(app.messagebox, "showwarning") as warning, \
                        mock.patch.object(app.messagebox, "showerror"):
                    a.connect_k6221()
                connect.assert_not_called()
                self.assertIn(message, warning.call_args.args[1])

    def test_apply_still_checks_the_whole_box(self):
        instrument = Stuck6221(fail_off=False)
        a = k6221_page(instrument)
        a.k6221_count_var = Var("1.5")
        with mock.patch.object(app.messagebox, "showwarning") as warning, \
                mock.patch.object(app.messagebox, "showerror"):
            a.apply_k6221_output(1)
        self.assertIn("pulse count must be an integer", warning.call_args.args[1])
        self.assertEqual(instrument.commands, [], "nothing sent")
        self.assertFalse(a.k6221_busy)


if __name__ == "__main__":
    unittest.main()
