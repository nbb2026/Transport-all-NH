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



class Var:
    def __init__(self, value):
        self.value = value

    def get(self):
        return self.value


class ParseTests(unittest.TestCase):
    """User, 2026-10-13: Start scan raised TypeError (rounding a whole
    (loop, point, points, current) item, since 2026-10-09)."""

    def parse(self, **values):
        a = mock.Mock()
        fields = dict(start="5", end="-5", step="0.1", loop="1", settle=".1", collect=".1")
        fields.update(values)
        a.magnet_scan_start_current_var = Var(fields["start"])
        a.magnet_scan_end_current_var = Var(fields["end"])
        a.magnet_scan_step_current_var = Var(fields["step"])
        a.magnet_scan_loop_var = Var(fields["loop"])
        a.magnet_scan_settle_var = Var(fields["settle"])
        a.magnet_scan_collect_var = Var(fields["collect"])
        a._selected_magnet_switch_key.return_value = "OUT"
        return app.CombinedControlApp._parse_magnet_scan(a)

    def test_the_users_settings(self):
        sequence, settle_s, collect_s, key = self.parse()
        self.assertEqual((settle_s, collect_s, key), (0.1, 0.1, "OUT"))
        self.assertEqual(sequence[0], (1, 1, 201, 5.0))
        self.assertEqual(sequence[100][3], -5.0, "the end current, as start_magnet_scan reads it")
        self.assertEqual(sequence[-1][3], 5.0, "back to the start")
        self.assertEqual(len(sequence), 201)
        # What start_magnet_scan and the worker read from each item.
        loops = max(item[0] for item in sequence)
        points_per_loop = sequence[0][2]
        self.assertEqual((loops, points_per_loop, sequence[(points_per_loop - 1) // 2][3]), (1, 201, -5.0))
        for loop, point, points, current in sequence:
            self.assertEqual(current, round(current, 3), "1 mA resolution")

    def test_loops_repeat_the_round_trip(self):
        sequence, *_rest = self.parse(loop="2", start="1", end="-1", step="0.5")
        self.assertEqual([item[3] for item in sequence], [1.0, 0.5, 0.0, -0.5, -1.0, -0.5, 0.0, 0.5, 1.0] * 2)
        self.assertEqual([item[0] for item in sequence], [1] * 9 + [2] * 9)


if __name__ == "__main__":
    unittest.main()
