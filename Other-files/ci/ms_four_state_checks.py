"""Layout checks and screenshots for MS-fourState.py on the cloud test machines.

    python Other-files/ci/ms_four_state_checks.py --shots DIR

Builds the program (no instruments are connected) at 96 DPI, as on the lab
PC, at its own 1360x900 window, at its 1100x760 minimum and widened to
2000 px, with a few example points in the plot; and its cycle and
two-state windows (MS-cycleState.py, MS-twoState.py) at 1360 px.  Reports widgets that stick
out past the window and text that does not fit, and saves a screenshot of
each.  Exit status 1 if anything is clipped or cut at 1360 or 2000 px on
Windows, the lab PC's system; on Linux the results are reported only.
"""
import argparse
import importlib.util
import sys
import tkinter as tk
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from gui_checks import inspect, screenshot  # noqa: E402

PROGRAM = Path(__file__).resolve().parents[2] / "MS-fourState.py"
# (mode, width, height, must fit)
WINDOWS = (("sequence", 1360, 900, True), ("sequence", 1100, 760, False), ("sequence", 2000, 1100, True),
           ("cycle", 1360, 900, True), ("two", 1360, 900, True))
EXAMPLE = (("D", 1500, 22, 30e-6), ("A", -1500, 22, 11e-6), ("B", 1500, 22, -12.5e-6), ("E", -1500, 22, -27e-6))


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--shots", type=Path)
    args = parser.parse_args()
    spec = importlib.util.spec_from_file_location("ms_four_state_ci", PROGRAM)
    module = importlib.util.module_from_spec(spec)
    sys.modules["ms_four_state_ci"] = module
    spec.loader.exec_module(module)
    failed = False
    for mode, width, height, must_fit in WINDOWS:
        root = tk.Tk()
        root.tk.call("tk", "scaling", 1.333)  # 96 DPI
        root.maxsize(4000, 3000)
        app = module.FourStateApp(root, mode)
        root.geometry(f"{width}x{height}+0+0")
        for _ in range(6):
            root.update()
        app.plot.set_rows([dict(step=i, state=s, write_field_set_oe=h, pulse_current_ma=c, x_v=x)
                           for i, (s, h, c, x) in enumerate(EXAMPLE, 1)])
        root.update()
        page = root.winfo_children()[0]
        clipped, cut, _pairs = inspect(page)
        problem = bool(clipped or cut)
        fatal = must_fit and problem and sys.platform == "win32"
        failed = failed or fatal
        marker = "FAIL" if fatal else ("note" if problem else "ok  ")
        print(f"  {marker} {mode} window {root.winfo_width()}x{root.winfo_height()} | clipped {clipped or 'none'} | "
              f"text cut {cut or 'none'}")
        if args.shots:
            args.shots.mkdir(parents=True, exist_ok=True)
            print(f"       screenshot: {screenshot(root, page, args.shots / f'ms_{mode}_{width}px.png')}")
        app.close()
    print("MS-FOURSTATE CHECKS FAILED" if failed else "MS-FOURSTATE CHECKS OK")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
