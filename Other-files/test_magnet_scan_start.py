"""MOKE magnet "Start scan" (user, 2026-10-13: "the start scan cannot be
used"): every refusal is a dialog naming its reason, not only the status
line.  No window: the method runs on a stand-in."""
import unittest
from unittest import mock

from test_ppms_motor_debug import app


def stand_in(**state):
    a = mock.Mock()
    a.__dict__.update(magnet_scan_running=False, magnet_busy=False)
    a.__dict__.update(state)
    a._magnet_devices_ready.return_value = state.get("ready", True)
    a._magnet_bops_armed.return_value = state.get("armed", False)
    a._selected_magnet_switch_key.return_value = state.get("selected")
    return a


class StartRefusalTests(unittest.TestCase):
    def reason(self, a):
        with mock.patch.object(app.messagebox, "showwarning") as shown:
            app.CombinedControlApp.start_magnet_scan(a)
        shown.assert_called_once()
        a.magnet_status_var.set.assert_called_with(shown.call_args.args[1])
        return shown.call_args.args[1]

    def test_the_selector_not_confirmed(self):
        self.assertIn("physical selector", self.reason(stand_in(selected=None)))

    def test_the_kepco_not_enabled(self):
        text = self.reason(stand_in(selected="IN"))
        self.assertIn("In-plane KEPCO is not enabled", text)
        self.assertIn("Enable/zero (before writing)", text)

    def test_a_magnet_command_still_running(self):
        self.assertIn("still running", self.reason(stand_in(magnet_busy=True)))

    def test_not_connected(self):
        self.assertIn("Connect the KEPCO", self.reason(stand_in(ready=False)))


if __name__ == "__main__":
    unittest.main()
