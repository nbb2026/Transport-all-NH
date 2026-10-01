"""Layout checks and screenshots for Test_Microwave_NH_dev.py on the cloud test machines.

    python Other-files/ci/microwave_checks.py --shots DIR

Builds the program in simulation mode (no instruments) at 96 DPI, as on the
lab PC, at its own 1360x860 window, at its 1000 px minimum and widened to
2000 px.  Reports widgets that stick out past the window and text that does
not fit, and saves a screenshot of each.  Exit status 1 if anything is clipped
or cut at 1360 or 2000 px.
"""
import argparse
import importlib.util
import sys
import tkinter as tk
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from gui_checks import inspect, screenshot  # noqa: E402

PROGRAM = Path(__file__).resolve().parents[2] / "Test_Microwave_NH_dev.py"
WIDTHS = ((1360, 860, True), (1000, 860, False), (2000, 1100, True))  # (width, height, must fit)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--shots", type=Path)
    args = parser.parse_args()
    spec = importlib.util.spec_from_file_location("microwave_ci", PROGRAM)
    module = importlib.util.module_from_spec(spec)
    sys.modules["microwave_ci"] = module
    spec.loader.exec_module(module)
    failed = False
    for width, height, must_fit in WIDTHS:
        root = tk.Tk()
        root.tk.call("tk", "scaling", 1.333)  # 96 DPI
        root.maxsize(4000, 3000)
        app = module.MicrowaveTransportApp(root, demo=True)
        root.geometry(f"{width}x{height}+0+0")
        for _ in range(6):
            root.update()
        page = root.winfo_children()[0]
        clipped, cut, _pairs = inspect(page)
        problem = bool(clipped or cut)
        failed = failed or (must_fit and problem)
        marker = "FAIL" if must_fit and problem else ("note" if problem else "ok  ")
        print(f"  {marker} window {root.winfo_width()}x{root.winfo_height()} | clipped {clipped or 'none'} | "
              f"text cut {cut or 'none'}")
        if args.shots:
            args.shots.mkdir(parents=True, exist_ok=True)
            print(f"       screenshot: {screenshot(root, page, args.shots / f'microwave_{width}px.png')}")
        app.closing = True
        app.executor.shutdown(wait=False)
        root.destroy()
    print("MICROWAVE CHECKS FAILED" if failed else "MICROWAVE CHECKS OK")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
