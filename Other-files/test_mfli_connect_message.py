"""Offline checks for the MFLI connect failure diagnostics; no LabOne API."""
import unittest
from unittest.mock import Mock

from test_ppms_motor_debug import app

Controller = app.NonLocalACController
SERVERS = [("127.0.0.1", 8004)]


class ConnectFailureMessageTests(unittest.TestCase):
    def test_device_missing_on_server_leads_the_headline(self):
        text = Controller._mfli_connect_failure_message(
            "dev4414", SERVERS, ["Core 127.0.0.1:8004：cannot attach"],
            {("127.0.0.1", 8004): "visible=dev4435; connected=dev4435"})
        first = app.short_status_error(RuntimeError(text), 200)
        self.assertIn("dev4414 is not on that server", first)
        self.assertIn("dev4435", first)
        self.assertNotIn("handshake failed", first)
        self.assertIn("Server device list", text)
        self.assertNotIn("18.12", text.split("\n\n")[-1])

    def test_handshake_failure_names_last_step_and_suggests_18_12(self):
        text = Controller._mfli_connect_failure_message(
            "dev4414", SERVERS,
            ["LabOne C API 127.0.0.1:8004：ziAPIConnectEx failed", "Core 127.0.0.1:8004：version mismatch"],
            {})
        first = app.short_status_error(RuntimeError(text), 200)
        self.assertIn("handshake failed", first)
        self.assertIn("version mismatch", first)
        self.assertIn("INSTALL_MFLI_LABONE_18_12.bat", text)
        self.assertNotIn("DEV4435", text)

    def test_device_present_in_report_is_not_treated_as_missing(self):
        text = Controller._mfli_connect_failure_message(
            "dev4414", SERVERS, ["Core：read failed"],
            {("127.0.0.1", 8004): "visible=dev4414,dev4435"})
        self.assertIn("handshake failed", text.split("\n\n")[0])

    def test_server_device_report_is_best_effort(self):
        daq = Mock()
        daq.getString.side_effect = lambda node: "dev4435" if node.endswith("visible") else (_ for _ in ()).throw(RuntimeError("no node"))
        self.assertEqual(Controller._mfli_server_devices(daq), "visible=dev4435")
        daq.getString.side_effect = RuntimeError("no string ABI")
        self.assertEqual(Controller._mfli_server_devices(daq), "")


if __name__ == "__main__":
    unittest.main()
