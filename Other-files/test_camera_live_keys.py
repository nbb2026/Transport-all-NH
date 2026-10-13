"""Camera Live's keys, with a stand-in camera (user, 2026-10-13: [C] did
nothing on the lab PC).  [C] clears the ROI and says so in the picture; a
click in the picture gives Camera Live the keyboard.  Needs a display.
"""
import queue
import tkinter as tk
import unittest
from unittest import mock

from lab_screen import LAB_WIDE, build_lab_app, lab_window
from test_ppms_motor_debug import app

ROI = {"cx": 50.0, "cy": 40.0, "w": 20.0, "h": 10.0, "angle": 0.0}


class StandInCamera:
    def __init__(self, *_args, **_kwargs):
        self.frames, self.errors = queue.Queue(), queue.Queue()
        self.background_mode = "off"

    def start(self):
        pass

    def stop(self):
        pass


class CameraLiveKeyTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        try:
            cls.root = tk.Tk()
        except tk.TclError as error:  # headless build server
            raise unittest.SkipTest(f"no display: {error}")
        cls.a = build_lab_app(app, cls.root)
        cls.root.geometry(f"{LAB_WIDE}x{lab_window(cls.a)[1]}+0+0")
        with mock.patch.object(app, "CAMERA_IMPORT_ERROR", None), \
                mock.patch.object(app, "CameraAcquisitionWorker", StandInCamera):
            cls.a.connect_camera()
        cls.camera = cls.a.camera_window
        cls.camera.show()
        cls.root.update()

    @classmethod
    def tearDownClass(cls):
        cls.a.closing = True
        cls.camera.close()
        cls.a.safe_close_hardware()
        cls.root.destroy()

    def select_roi(self):
        self.camera.roi = dict(ROI)
        self.a.set_2410_roi(self.camera.roi)
        self.root.update()

    def press(self, key):
        self.camera.window.focus_force()
        self.root.update()
        self.camera.canvas.event_generate(f"<KeyPress-{key}>")
        self.root.update()

    def test_c_clears_the_roi_and_says_so(self):
        for key in ("c", "C"):
            with self.subTest(key=key):
                self.select_roi()
                self.press(key)
                self.assertIsNone(self.camera.roi)
                self.assertIsNone(self.a.moke_roi)
                self.assertEqual(self.camera.key_feedback[0], "[C] ROI cleared")
                self.assertTrue(self.camera.canvas.find_withtag("key_feedback"), "shown in the picture")

    def test_during_a_scan_it_says_why_the_roi_stays(self):
        self.select_roi()
        self.a.magnet_scan_running = True
        try:
            self.press("c")
        finally:
            self.a.magnet_scan_running = False
        self.assertEqual(self.camera.roi, ROI)
        self.assertEqual(self.camera.key_feedback[0], "[C] ROI not cleared: a scan is running")

    def test_a_click_in_the_picture_gives_camera_live_the_keyboard(self):
        self.root.focus_force()  # the keyboard in the main window
        self.root.update()
        self.camera.canvas.event_generate("<ButtonPress-1>", x=5, y=5)
        self.camera.canvas.event_generate("<ButtonRelease-1>", x=5, y=5)
        self.root.update()
        focused = self.camera.window.focus_get()
        self.assertIsNotNone(focused)
        self.assertIs(focused.winfo_toplevel(), self.camera.window)


if __name__ == "__main__":
    unittest.main()
