"""Layout checks and screenshots for MS-fourState.py on the cloud test machines.

    python Other-files/ci/ms_four_state_checks.py --shots DIR

Builds the program (no instruments are connected) at 96 DPI, as on the lab
PC, at its own 1720x1240 window (on a 2560x1440 screen), at 1360x960, at its 1100x760 minimum and widened to
2000 px, with a few example points in the plot, in its default Cycle mode;
and in Sequence mode at 1360 px.  Reports widgets that stick
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
WINDOWS = (("cycle", 1720, 1240, True), ("cycle", 1360, 960, True), ("cycle", 1100, 760, False),
           ("cycle", 2000, 1100, True), ("sequence", 1360, 960, True))
EXAMPLE = (("D", 1500, 22, 30e-6), ("A", 1500, -22, 11e-6), ("B", 1500, 22, -12.5e-6), ("E", 1500, -22, -27e-6))


EXAMPLE_CONSOLE = (
    "Step 2/4 (cycle 1), state A: write field +1500 Oe",
    "PPMS > SetField +1500 Oe at 100 Oe/s, No overshoot, Driven",
    "PPMS < T 300.002 K Stable | H +12.4 Oe Charging",
    "PPMS < T 300.002 K Stable | H +1500.0 Oe StableDriven",
    "PPMS < T 300.002 K Stable | H +1500.0 Oe StableDriven",
    "6221 > SOUR:WAVE:FUNC SQU", "6221 > SYST:ERR?", "6221 < 0,\"No error\"",
    "6221 > SOUR:WAVE:AMPL 1.1e-02", "6221 > SOUR:WAVE:INIT", "6221 > OUTP?", "6221 < 0",
    "SR830 > SNAP? 1,2,3,4,9", "SR830 < 1.1e-05,1.1e-06,1.1e-05,5.71,317.3",
    "SR830 > LIAS?", "SR830 < 0",
    "SR830 > SNAP? 1,2,3,4,9", "SR830 ! VI_ERROR_TMO (example)",
)


def example_timeline():
    """A run's timeline, as FourStateRun.trace sends it, for the four states:
    the field up to the write field and back, the pulse, then the read."""
    traces, t = [], 0.0
    for state, field, pulse, x in EXAMPLE:
        for step in range(30):  # 15 s up, 15 s down at 100 Oe/s, read every 0.5 s
            level = field * min(1.0, step / 15) if step < 15 else field * (29 - step) / 14
            traces.append((t, "H", level, state))
            if step == 15:
                traces.append((t, "pulse", pulse, state))
            t += 0.5
        traces.append((t, "I", 0.5, state))
        for sample in range(30):
            traces.append((t + 2.0 + 0.1 * sample, "X", x * (1 + 0.02 * ((sample % 5) - 2)), state))
        t += 5.0
        traces += [(t, "read", x, state), (t, "I", 0.0, None)]
        t += 2.0
    return traces


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
        app.plot.clear()
        for trace in example_timeline():
            app.plot.add(trace)
        app.plot.redraw()
        for line in EXAMPLE_CONSOLE:
            app._append_command(line) if " " in line and line.split()[1] in "<>!" else app._append_log(line)
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
