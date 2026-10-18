"""MOKE magnet "Start scan" (user, 2026-10-13: "the start scan cannot be
used"): every refusal is a dialog naming its reason, not only the status
line.  No window: the method runs on a stand-in."""
import threading
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


class IntervalBoxTests(unittest.TestCase):
    """Review, 2026-10-13: text in the "Interval s" box ('1,5', '0.5s',
    empty) made Start raise TclError after Yes, with no dialog, Continuous
    left off and nothing submitted.  The interval is only log metadata."""

    def start(self, interval):
        a = stand_in(ready=True, armed=True, selected="OUT")
        a.magnet_scan_data_lock = threading.Lock()
        a.magnet_scan_eta_after_id = None
        a.moke_field_rows = {}
        a.magnet_port_vars = {k: Var(k) for k in ("OUT_BOP", "OUT_GAUSS", "IN_BOP", "IN_GAUSS")}
        a.magnet_auto_poll_var = Var(True)
        a.magnet_auto_poll_var.set = lambda value: setattr(a.magnet_auto_poll_var, "value", value)
        a.magnet_save_images_var = Var(False)
        a.magnet_scan_step_current_var = Var("0.5")
        a.magnet_safety_var = Var(True)
        a.keithley_scan_running = a.k6221_scan_running = a.magnet_poll_inflight = False
        a._parse_magnet_scan.return_value = (
            [(1, 1, 3, 1.0), (1, 2, 3, 0.0), (1, 3, 3, 1.0)], 0.1, 0.1, "OUT")
        a.camera_scan_parameter_snapshot.return_value = {}
        a.moke_hardware_parameter_snapshot.return_value = {}
        a._snapshot_variable = app.CombinedControlApp._snapshot_variable
        a.magnet_poll_interval_var = mock.Mock()
        if isinstance(interval, str):
            a.magnet_poll_interval_var.get.side_effect = app.tk.TclError(
                f'expected floating-point number but got "{interval}"')
        else:
            a.magnet_poll_interval_var.get.return_value = interval
        # The run folder step (user, 2026-10-18) answered with a stand-in.
        recorder = mock.Mock()
        with mock.patch.object(app, "begin_scan_run", return_value=recorder), \
                mock.patch.object(app.messagebox, "askyesno", return_value=True), \
                mock.patch.object(app.messagebox, "showwarning") as warned, \
                mock.patch.object(app.messagebox, "showerror") as failed:
            app.CombinedControlApp.start_magnet_scan(a)
        warned.assert_not_called()
        failed.assert_not_called()
        return a

    def test_text_in_the_box_still_starts_the_scan(self):
        for text in ("1,5", "0.5s", ""):
            with self.subTest(text=text):
                a = self.start(text)
                a.magnet_executor.submit.assert_called_once()
                self.assertIs(a.magnet_executor.submit.call_args.args[0], a._magnet_scan_worker)
                self.assertTrue(a.magnet_scan_running)
                self.assertEqual(a.magnet_scan_settings["magnet.poll_interval_s"], "unavailable")

    def test_a_number_is_recorded(self):
        a = self.start(0.5)
        a.magnet_executor.submit.assert_called_once()
        self.assertEqual(a.magnet_scan_settings["magnet.poll_interval_s"], 0.5)


if __name__ == "__main__":
    unittest.main()
