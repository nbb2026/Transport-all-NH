"""Build the application as the lab PC shows it, on whatever machine runs the tests.

The lab PC has a 2560x1440 screen at 96 DPI.  The program sizes its window,
its scale and its fonts from the screen it finds, so tests that used the
machine's own screen only held on the Mac they were written on: GitHub's
Windows machine has a 1024x768 desktop, which caps the window at 1024 px and
shrinks the text.  Here the screen is faked and Tk is set to 96 DPI, as
Other-files/ci/gui_checks.py does.
"""
LAB_SCREEN = (2560, 1440)
LAB_WIDE = 2000  # the window as the user widens it at the lab


class _LabScreen:
    def winfo_screenwidth(self):
        return LAB_SCREEN[0]

    def winfo_screenheight(self):
        return LAB_SCREEN[1]


def build_lab_app(app_module, root):
    """CombinedControlApp on ``root`` as it opens on the lab PC."""
    root.tk.call("tk", "scaling", 1.333)  # 96 DPI
    original = app_module.calculate_window_layout
    app_module.calculate_window_layout = lambda _root: original(_LabScreen())
    try:
        return app_module.CombinedControlApp(root)
    finally:
        app_module.calculate_window_layout = original


def lab_window(built):
    """(default width, height) of the control window the program opens."""
    width, height = built.window_layout["control"][:2]
    return int(width), int(height)
