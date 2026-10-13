"""Camera settings rows (review, 2026-10-13): the averaging, binning and
mean-filter radios, the exposure and the 100x marker on the MOKE magnet and
CIDWM pages always show what the camera uses.

1. A click refused during a scan puts the radio back (it has already moved).
2. A camera connected again (after Disconnect or a camera error) gets the
   page's choices instead of starting at the defaults.

No Tk and no camera: the real CameraAcquisitionWorker (its thread never
started) behind a stand-in Camera Live window."""
import threading
import types
import unittest
from unittest import mock

from test_nonlocal_keithley import Var, bare_app
from test_ppms_motor_debug import app

DEFAULTS = (
    app.DIFFERENCE_TEMPORAL_AVERAGE_FRAMES,
    app.DIFFERENCE_SPATIAL_BINNING,
    app.DIFFERENCE_MEAN_FILTER_SIZE,
)


def camera():
    """A freshly connected camera: a new worker at its defaults and Camera
    Live with no frame yet, as TkCameraWindow builds them."""
    window = types.SimpleNamespace(
        worker=app.CameraAcquisitionWorker(lambda _message: None),
        latest_frame=None, rendering_paused=False, _draw=mock.Mock(),
        objective_100x_marker_visible=False, video_recorder=None, close=mock.Mock(),
    )
    window.set_100x_marker_visible = (
        lambda visible: app.TkCameraWindow.set_100x_marker_visible(window, visible)
    )
    return window


def no_dialogs(test):
    """No real message box can open (Tk-free), whatever the code does; a
    test patches the dialog it expects on top of this stand-in."""
    patcher = mock.patch.object(app, "messagebox", mock.Mock())
    patcher.start()
    test.addCleanup(patcher.stop)


def page():
    a = bare_app()
    a.root = None   # the dialogs are mocked
    a.camera_window = None
    a.magnet_scan_running = a.keithley_scan_running = a.k6221_scan_running = False
    a.magnet_scan_stop_event, a.keithley_stop_event, a.k6221_stop_event = (
        threading.Event(), threading.Event(), threading.Event())
    a.difference_temporal_average_var = Var(DEFAULTS[0])
    a.difference_spatial_binning_var = Var(DEFAULTS[1])
    a.difference_mean_filter_var = Var(DEFAULTS[2])
    a.camera_exposure_ms_var = Var(f"{app.EXPOSURE_S * 1000:g}")
    a.objective_100x_marker_visible = False
    a.objective_100x_marker_buttons = [mock.Mock()]
    for name in ("magnet_status_var", "camera_window_status_var", "difference_status_var",
                 "camera_exposure_status_var", "operation_var"):
        setattr(a, name, Var())
    a.camera_video_stopping = False
    for name in ("clear_difference_histogram", "_set_connection_indicator",
                 "_set_camera_dependent_controls", "_update_camera_launcher_controls",
                 "_update_moke_roi_controls", "clear_camera_roi", "append_log",
                 "reset_difference_live_time_window"):
        setattr(a, name, mock.Mock())
    return a


def radios(a):
    return (a.difference_temporal_average_var.get(), a.difference_spatial_binning_var.get(),
            a.difference_mean_filter_var.get())


def click(a, var_name, value):
    """A ttk radio: its variable takes the value, then its command runs."""
    getattr(a, var_name).set(value)
    a.apply_difference_display_average()


class RefusedClickTests(unittest.TestCase):
    """Finding 1: during a scan the click is refused; the radio must not stay
    on a value the camera never got."""

    def setUp(self):
        no_dialogs(self)
        self.a = page()
        self.a.attach_camera(camera())

    def test_each_scan_and_each_row(self):
        for scan in ("magnet_scan_running", "keithley_scan_running", "k6221_scan_running"):
            for var_name, value in (("difference_temporal_average_var", 1),
                                    ("difference_spatial_binning_var", 8),
                                    ("difference_mean_filter_var", 1)):
                with self.subTest(scan=scan, radio=var_name):
                    a = self.a
                    setattr(a, scan, True)
                    shown_during_warning = []
                    with mock.patch.object(app.messagebox, "showwarning",
                                           side_effect=lambda *args, **kwargs:
                                           shown_during_warning.append(radios(a))) as warning:
                        click(a, var_name, value)
                    setattr(a, scan, False)
                    warning.assert_called_once()
                    self.assertEqual(radios(a), DEFAULTS)
                    self.assertEqual(a.camera_window.worker.difference_display_average_state(), DEFAULTS)
                    # Put back before the (modal) warning opens.
                    self.assertEqual(shown_during_warning, [DEFAULTS])

    def test_after_the_scan_the_next_click_applies_only_what_was_clicked(self):
        a = self.a
        a.magnet_scan_running = True
        with mock.patch.object(app.messagebox, "showwarning"):
            click(a, "difference_temporal_average_var", 1)
        a.magnet_scan_running = False
        # Clicking the binning already shown changes nothing (it applied the
        # refused 1 frame before).
        click(a, "difference_spatial_binning_var", DEFAULTS[1])
        self.assertEqual(a.camera_window.worker.difference_display_average_state(), DEFAULTS)
        click(a, "difference_temporal_average_var", 2)
        self.assertEqual(a.camera_window.worker.difference_display_average_state(),
                         (2, DEFAULTS[1], DEFAULTS[2]))
        self.assertEqual(radios(a), (2, DEFAULTS[1], DEFAULTS[2]))

    def test_an_invalid_value_is_put_back(self):
        a = self.a
        with mock.patch.object(app.messagebox, "showerror") as error:
            click(a, "difference_temporal_average_var", 3)
        error.assert_called_once()
        self.assertEqual(radios(a), DEFAULTS)
        self.assertEqual(a.camera_window.worker.difference_display_average_state(), DEFAULTS)

    def test_an_accepted_click_reaches_the_camera(self):
        a = self.a
        click(a, "difference_mean_filter_var", 4)
        self.assertEqual(a.camera_window.worker.difference_display_average_state(),
                         (DEFAULTS[0], DEFAULTS[1], 4))
        self.assertIn("4×4 Mean filter", a.magnet_status_var.get())


class ReconnectTests(unittest.TestCase):
    """Finding 2: a new camera starts at 4 frames, 4x4, 2x2, 100 ms and no
    marker; it must get what the page shows."""

    def setUp(self):
        no_dialogs(self)

    def choose_on_the_first_camera(self):
        a = self.a = page()
        first = camera()
        a.attach_camera(first)
        click(a, "difference_temporal_average_var", 1)
        click(a, "difference_spatial_binning_var", 1)
        click(a, "difference_mean_filter_var", 1)
        a.camera_exposure_ms_var.set("50")
        a.apply_camera_exposure()
        a.toggle_100x_marker()
        self.assertEqual(first.worker.difference_display_average_state(), (1, 1, 1))
        self.assertTrue(first.objective_100x_marker_visible)
        return first

    def assert_second_camera_follows_the_page(self):
        a = self.a
        second = camera()
        self.assertEqual(second.worker.difference_display_average_state(), DEFAULTS)
        a.attach_camera(second)
        self.assertEqual(radios(a), (1, 1, 1))
        self.assertEqual(second.worker.difference_display_average_state(), (1, 1, 1))
        self.assertTrue(second.objective_100x_marker_visible)
        second._draw.assert_not_called()       # no frame yet: only the flag
        self.assertTrue(second.worker.exposure_update_requested)
        self.assertAlmostEqual(second.worker.requested_exposure_s, 0.050)
        self.assertEqual(a.camera_exposure_ms_var.get(), "50")
        a.objective_100x_marker_buttons[0].configure.assert_called_with(text="Hide 100× marker")
        # One click on the marker button now hides it (it took two before).
        a.toggle_100x_marker()
        self.assertFalse(second.objective_100x_marker_visible)

    def test_disconnect_then_connect(self):
        first = self.choose_on_the_first_camera()
        self.a.disconnect_camera()
        first.close.assert_called_once()
        self.assertIsNone(self.a.camera_window)
        self.assert_second_camera_follows_the_page()

    def test_camera_error_then_connect(self):
        first = self.choose_on_the_first_camera()
        self.a.camera_start_failed(RuntimeError("camera lost"))
        first.close.assert_called_once()
        self.assertIsNone(self.a.camera_window)
        self.assert_second_camera_follows_the_page()

    def test_page_defaults_leave_the_camera_as_it_starts(self):
        a = page()
        new = camera()
        a.attach_camera(new)
        self.assertEqual(new.worker.difference_display_average_state(), DEFAULTS)
        # The same exposure is not requested: that would restart the recording.
        self.assertFalse(new.worker.exposure_update_requested)
        self.assertFalse(new.objective_100x_marker_visible)

    def test_an_invalid_exposure_entry_shows_the_cameras(self):
        a = page()
        a.camera_exposure_ms_var.set("fast")
        new = camera()
        a.attach_camera(new)
        self.assertFalse(new.worker.exposure_update_requested)
        self.assertEqual(a.camera_exposure_ms_var.get(), f"{app.EXPOSURE_S * 1000:g}")


if __name__ == "__main__":
    unittest.main()
