"""Fixes from the whole-program review (2026-10-09), one check each.

No Tk and no instrument: stand-ins record what the code asks of them."""
import threading
import types
import unittest
from unittest import mock

import numpy as np

from test_nonlocal_keithley import Var, bare_app
from test_ppms_motor_debug import app


class Recorder:
    """An instrument that records writes and answers queries from a table."""

    def __init__(self, replies=None):
        self.log, self.replies = [], dict(replies or {})

    def write(self, command):
        self.log.append(command)

    def query(self, command):
        self.log.append(command)
        reply = self.replies[command]
        return reply.pop(0) if isinstance(reply, list) else reply


class SharedHelperTests(unittest.TestCase):
    def test_a_2_5_step_keeps_its_half_steps_in_the_labels(self):
        self.assertEqual(app.format_nice_tick(3002.5, 2.5), "3002.5")
        self.assertEqual(app.format_nice_tick(10005.0, 2.5), "10005.0")
        self.assertEqual(app.format_nice_tick(3000.0, 5.0), "3000")

    def test_a_scan_axis_does_not_repeat_its_end(self):
        self.assertEqual(app.ppms_scan_axis(0.1, 0.4, 0.1)[-2:], (0.30000000000000004, 0.4))
        self.assertEqual(len(app.ppms_scan_axis(0.1, 0.4, 0.1)), 4)

    def test_the_short_gpib_spelling_shares_the_lease(self):
        self.assertEqual(app.instrument_lease_key("GPIB0::30"), app.instrument_lease_key("GPIB0::30::INSTR"))


class MokeTests(unittest.TestCase):
    def test_magnet_disconnect_writes_0_a_first(self):
        a = bare_app()
        calls = []
        bop = mock.Mock(connected=True)
        bop.set_current.side_effect = lambda value: calls.append(("0 A", value))
        channel = mock.Mock(bop=bop)
        channel.close.side_effect = lambda: calls.append("close")
        a.magnet_channels = {"OUT": channel, "IN": mock.Mock(bop=None)}
        a.magnet_auto_poll_var = Var(True)
        a._submit_magnet_task = lambda label, worker, success: calls.append(("result", worker()))
        a.disconnect_magnets()
        self.assertEqual(calls[:2], [("0 A", 0.0), "close"])
        self.assertEqual(calls[-1], ("result", []), "no warnings")

    def test_a_frame_goes_into_the_scan_samples_once(self):
        window = app.TkCameraWindow.__new__(app.TkCameraWindow)
        window.roi = {"cx": 5.0, "cy": 5.0, "w": 4.0, "h": 4.0, "angle": 0.0}
        window.selecting_roi, window.roi_start, window.roi_end = False, None, None
        window.mapping = object()
        window.app = mock.Mock()
        image = np.ones((10, 10), dtype=float)
        frame = types.SimpleNamespace(raw=image, difference=image, display_difference=None)
        with mock.patch.object(app, "np", np):
            for _redraw in range(3):   # e.g. pan, zoom, a CLim edit
                window._ingest_roi_values(frame)
            self.assertEqual(window.app.ingest_2410_roi_sample.call_count, 1)
            window._ingest_roi_values(types.SimpleNamespace(raw=image, difference=image, display_difference=None))
        self.assertEqual(window.app.ingest_2410_roi_sample.call_count, 2, "a new frame counts")

    def test_moke_24_keithley_goes_off_before_its_range_changes(self):
        controller = app.Keithley2410Controller.__new__(app.Keithley2410Controller)
        controller.lock = threading.RLock()
        controller.instrument = object()
        controller.output_enabled = True       # a manual Write left it on
        controller.model = "2410"
        controller.compliance_a = 1e-3
        calls = []
        controller._write_checked = calls.append
        controller._configure_voltage_source_locked = lambda v: calls.append(f"configure {v:g}")
        controller.enable_output(100.0)
        self.assertEqual(calls, [":SOUR:VOLT 0", ":OUTP OFF", "configure 100", ":OUTP ON"])


class StfmrTests(unittest.TestCase):
    def test_the_overload_latched_by_a_range_change_is_dropped(self):
        controller = app.STFMRSR830Controller.__new__(app.STFMRSR830Controller)
        controller.lock = threading.RLock()
        controller.auto_range_enabled = True
        controller.sensitivity_index = 17
        controller.low_range_count = controller.range_cooldown = 0
        snap = "0.9e-3,0,0.95e-3,0,17.3"   # near full scale of SENS 17 (1 mV)
        controller.instrument = Recorder({
            "SNAP? 1,2,3,4,9": [snap, "1e-4,0,1e-4,0,17.3"],
            "LIAS?": ["0", "4", "0"],   # 4: latched by the range change
            "SENS?": "18",
        })
        controller.read_sample(17.3)                      # steps up to 18
        sample = controller.read_sample(17.3)
        self.assertEqual(controller.sensitivity_index, 18, "no further step")
        self.assertEqual(sample["status"], 0, "the latched bit was read and dropped")

    def test_zero_off_stops_a_running_fmr_scan_first(self):
        a = bare_app()
        kepco = mock.Mock(connected=True)
        a.stfmr_controllers = {"kepco": kepco}
        a.stfmr_fmr_running, a.stfmr_frequency_running = True, False
        order = []
        a.stop_stfmr_fmr_scan = lambda: order.append("stop")
        kepco.force_output_off.side_effect = lambda: order.append("off") or {}
        a._apply_stfmr_kepco_readback = lambda result: None
        a.stfmr_status_var = Var()
        a.zero_stfmr_kepco_output()
        self.assertEqual(order, ["stop", "off"])


class PpmsTests(unittest.TestCase):
    def test_rte_zero_off_acts_on_an_output_switched_on_elsewhere(self):
        panel = app.RTEPanel.__new__(app.RTEPanel)
        panel.ppms_field_scan_stop_event = threading.Event()
        panel.rte_closed = False
        kepco = mock.Mock()
        panel.rte_controllers = {"kepco": kepco}

        class Now:
            def submit(self, function):
                future = mock.Mock()
                future.result.return_value = function()
                future.add_done_callback.side_effect = lambda callback: callback(future)
                return future

        panel.ppms_executor = Now()
        panel.post_ui = lambda callback: callback()
        panel.rte_drive_live = Var()
        panel._rte_zero()
        kepco.force_output_off.assert_called_once()
        kepco.zero_output.assert_not_called()
        self.assertEqual(panel.rte_drive_live.get(), "KEPCO: zeroed and output OFF")

    def test_a_typed_temperature_rate_is_kept(self):
        a = bare_app()
        a.ppms_temperature_target_var, a.ppms_temperature_rate_var = Var("300"), Var("2")
        # Set T reads the rate as typed above 10 K, and lowers it below.
        with mock.patch.object(app.CombinedControlApp, "set_ppms_temperature",
                               app.CombinedControlApp.set_ppms_temperature):
            try:
                a.set_ppms_temperature()
            except Exception:
                pass   # nothing connected here; only the rate matters
        self.assertEqual(a.ppms_temperature_rate_var.get(), "2")
        a.ppms_temperature_target_var.set("5")
        try:
            a.set_ppms_temperature()
        except Exception:
            pass
        self.assertEqual(a.ppms_temperature_rate_var.get(), "1")


if __name__ == "__main__":
    unittest.main()
