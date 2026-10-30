"""Layout checks for the cloud test machines, with screenshots of each page.

Run on a GitHub runner, where Tk draws on a virtual display (Xvfb on Linux) or
the runner's own desktop (Windows), so nothing appears on anyone's screen.  It
never talks to instruments.

    python Other-files/ci/gui_checks.py --shots DIR

For each emulated lab screen it builds the whole application at 96 DPI and
reports any widget that sticks out past the page and any label, button or
Zurich list entry whose text does not fit.  It also checks that the compact
pages end in the same layout whatever the resize history, and it saves a
screenshot of each page at the lab's 2000 px window.

Exit status is 1 if, on the lab PC's 2560x1440 screen, a compact page
(CIDWM, STFMR, FMR) clips or cuts anything, if STFMR or FMR do not sit
side by side at the 2000 px window, or if a layout depends on resize history.
Other screens and the other pages are reported only.
"""
import argparse
import importlib.util
import sys
import time
import tkinter as tk
import tkinter.font
import types
from pathlib import Path

PROGRAM = Path(__file__).resolve().parents[2] / "Transport_all.py"
COMPACT = ("CIDWM", "STFMR", "FMR")
REPORTED = ("MOKE light", "MOKE motor", "MOKE magnet", "MOKE-24", "Non-local", "PPMS", "SHV")
SCREENS = (  # (screen width, height, window width or 0 for the program's own)
    (2560, 1440, 2000),  # the lab PC, window widened by hand
    (2560, 1440, 0),     # the lab PC, the program's default window
    (1920, 1080, 0),
    (1600, 1586, 0),
)
SHOT_PAGES = ("MOKE light", "MOKE motor", "MOKE magnet", "CIDWM", "MOKE-24",
              "Non-local", "PPMS", "SHV", "FMR", "STFMR")
# The Non-local page's measurements are sub-tabs; each is checked and shot.
SUB_TABS = {"Non-local": ("nonlocal_measurements", ("resistance", "iv", "iv6221", "gate"))}


def load_program():
    sys.modules.setdefault("pco", types.SimpleNamespace(Camera=object))
    spec = importlib.util.spec_from_file_location("transport_ci", PROGRAM)
    module = importlib.util.module_from_spec(spec)
    sys.modules["transport_ci"] = module
    spec.loader.exec_module(module)
    return module


def build(module, screen_w, screen_h, window_w):
    original = module.calculate_window_layout

    class FakeScreen:
        def __init__(self, _root):
            pass

        def winfo_screenwidth(self):
            return screen_w

        def winfo_screenheight(self):
            return screen_h

    module.calculate_window_layout = lambda root: original(FakeScreen(root))
    try:
        root = tk.Tk()
        root.tk.call("tk", "scaling", 1.333)  # 96 DPI, as on the lab PC
        root.maxsize(4000, 3000)
        app = module.CombinedControlApp(root)
    finally:
        module.calculate_window_layout = original
    try:  # as main() does, so the MOKE motor page is drawn; it opens no port
        motor_class, _self_test = module.create_motor_subsystem()
        app.attach_motor(motor_class)
    except Exception as error:
        print(f"  note: MOKE motor page not attached: {error}")
    width = window_w or app.window_layout["control"][0]
    root.geometry(f"{width}x1300+0+0")
    root.update()
    return root, app, app.notebook


def show(root, notebook, name):
    for index in range(notebook.index("end")):
        if notebook.tab(index, "text") == name:
            notebook.select(index)
            for _ in range(4):
                root.update()
            return notebook.nametowidget(notebook.tabs()[index])
    raise KeyError(name)


def example_data(app):
    """Example |R| and I-V data, so the screenshots show the plots drawn: an
    open 1 um Pt-Pt gap reading +/-20 nA of noise from -6 to 6 V, as in the
    user's figure.  Deterministic, and nothing is sent to an instrument."""
    module = sys.modules["transport_ci"]
    voltages = module.build_iv_sweep_voltages(-6, 6, 0.25)
    noise = [((index * 37) % 17 - 8) / 8 for index in range(len(voltages))]
    app.nonlocal_iv_settings = {"start_v": -6.0, "end_v": 6.0}
    app.nonlocal_iv_total_points = len(voltages)
    app.nonlocal_iv_rows = [
        (index + 1, voltage, voltage, 2.2e-8 * value, 0.62 * (index + 1), False)
        for index, (voltage, value) in enumerate(zip(voltages, noise))
    ]
    app.nonlocal_resistance_rows = [
        (0.2 * index, 369.7e6 * (1 + 0.002 * ((index * 7) % 5 - 2))) for index in range(100)
    ]
    # 6221-IV at RT-E's start-up settings: the Zurich alone, 1+2+3, added
    # point by point as a scan does, so the rows and status show too.  A
    # frequency scan (7, 11, 13 Hz): a curve per frequency, light to dark;
    # the pick-up in Y grows with the frequency.
    app.nonlocal_iv6221_rows = []
    app.nonlocal_iv6221_total_points = 60
    index = 0
    for frequency_index, frequency in enumerate((7.0, 11.0, 13.0), 1):
        for point in range(1, 21):
            index += 1
            current = 0.02 * point
            app._append_nonlocal_iv6221_point({
                "index": index, "pass": 1, "loop": 1, "point": point, "elapsed_s": 1.6 * index,
                "set_current_rms_ma": current, "current_rms_ma": current, "frequency_hz": frequency,
                "set_frequency_hz": frequency, "frequency_index": frequency_index,
                "frequency_count": 3, "discarded": 1 if index == 60 else 0,
                "series": {
                    ("mfli", 1): {"X": 2.0e-4 * current, "Y": 1.0e-7 * frequency * current,
                                  "R": 2.0e-4 * current, "theta": 0.03 * frequency},
                    ("mfli", 2): {"X": 3.0e-7 * current ** 2, "Y": -2.0e-9 * frequency * current,
                                  "R": 3.0e-7 * current ** 2, "theta": -88.0 + 0.1 * frequency},
                    ("mfli", 3): {"X": 4.0e-9 * current ** 3, "Y": 1.0e-10 * frequency * current,
                                  "R": 4.0e-9 * current ** 3, "theta": 12.0 + 0.2 * frequency},
                },
            })
    # Gate-VI with SR830 #8 at 1+2 and the Zurich at 1+2+3: a row of X, Y,
    # θ per order, then |R| and the X or Y vs pulse voltage per order.
    app.nonlocal_vi_lockin_harmonic_vars["8"].set("1+2")
    app.nonlocal_vi_mfli_harmonic_var.set("1+2+3")
    app.nonlocal_vi_measurement_enabled_vars["8"].set(True)
    app.nonlocal_vi_use_mfli_var.set(True)
    time_rows, summary_rows, pulse_rows, resistance_rows = [], [], [], []
    elapsed = 0.0
    resistance_rows.append((0.0, 3.6e8, 0.0, "initial_gate"))   # not plotted
    for point, voltage in enumerate((0, 4, 8, 12, 16, 20, 16, 12, 8, 4, 0), 1):
        pulse_rows.append((elapsed, float(voltage)))
        state = 1.0 + 0.04 * voltage / 20
        means = {("8", 1): 1.0e-3 * state, ("mfli", 1): 2.0e-4 * state,
                 ("mfli", 2): 3.0e-7 * state ** 2, ("mfli", 3): 4.0e-9 * state ** 3,
                 ("8", 2): 2.0e-6 * state ** 2}
        for passes in ((("8", 1), ("mfli", 1), ("mfli", 2), ("mfli", 3)), (("8", 2),)):
            for sample in range(8):
                elapsed += 1.0
                for key, order in passes:
                    r = means[(key, order)] * (1 + 0.003 * ((sample * 5 + point) % 7 - 3))
                    time_rows.append((elapsed, key, r, 10.0 * order, "formal", r, 0.05 * r, order))
        for (key, order), mean in means.items():
            summary_rows.append({"key": key, "order": order, "pulse_voltage_v": float(voltage),
                                 "mean": mean, "mean_x": mean, "mean_y": 0.05 * mean})
        resistance_rows.append((float(voltage), 3.7e8 * (1 + 0.01 * point), elapsed, "post_point"))
    with app.nonlocal_vi_data_lock:
        app.nonlocal_vi_time_rows = time_rows
        app.nonlocal_vi_summary_rows = summary_rows
        app.nonlocal_vi_pulse_rows = pulse_rows
        app.nonlocal_vi_resistance_rows = resistance_rows


def pages(root, app, notebook, names):
    """(page, sub-tab key or "", the page shown) for every page and sub-tab."""
    for name in names:
        tab = show(root, notebook, name)
        attribute, keys = SUB_TABS.get(name, (None, ()))
        if attribute is None:
            yield name, "", tab
            continue
        inner = getattr(app, attribute)
        example_data(app)
        for index, key in enumerate(keys):
            inner.select(index)
            for _ in range(4):
                root.update()
            yield name, key, tab


def text_fits(widget):
    try:
        text = widget.cget("text")
    except tk.TclError:
        return True
    if not text or ("textvariable" in widget.keys() and str(widget.cget("textvariable"))):
        return True
    try:
        font = tkinter.font.nametofont(str(widget.cget("font")) or "TkDefaultFont")
    except (tk.TclError, KeyError):
        font = tkinter.font.nametofont("TkDefaultFont")
    slack = {"TButton": 10, "TCheckbutton": 22, "TLabel": 0}.get(widget.winfo_class(), 0)
    # Text with a wrap width shows in full on several lines, as long as the
    # wrap width fits the widget.
    try:
        wrap = widget.cget("wraplength") if "wraplength" in widget.keys() else ""
        if not str(wrap) and "style" in widget.keys():
            wrap = tk.ttk.Style(widget).lookup(str(widget.cget("style")) or widget.winfo_class(), "wraplength")
        wrap = int(float(str(wrap or 0)))
    except (tk.TclError, ValueError):
        wrap = 0
    if wrap > 0:
        longest = max(font.measure(line) for line in str(text).splitlines() or [""])
        return min(wrap, longest) <= widget.winfo_width() - slack + 2
    # Tk measures a string as one line; a wrapped label is as wide as its
    # longest line.
    longest = max(font.measure(line) for line in str(text).splitlines() or [""])
    return longest <= widget.winfo_width() - slack


def inspect(tab):
    right = tab.winfo_rootx() + tab.winfo_width()
    clipped, cut, pairs = [], [], []

    def walk(widget):
        for child in widget.winfo_children():
            if child.winfo_ismapped():
                over = child.winfo_rootx() + child.winfo_width() - right
                if over > 1:
                    label = child.cget("text") if "text" in child.keys() else ""
                    clipped.append(f"{child.winfo_class()}[{str(label)[:24]}] over by {over}px")
                if child.winfo_class() in ("TButton", "TCheckbutton", "TLabel") and not text_fits(child):
                    cut.append(str(child.cget("text"))[:28])
                if getattr(child, "zurich_chooser", False) is True:
                    font = tkinter.font.nametofont("TkTextFont")
                    # The text area: ttk sizes it as width x the width of "0";
                    # the rest of the requested width is arrow, border, padding.
                    room = child.winfo_width() - (
                        child.winfo_reqwidth() - int(child.cget("width")) * font.measure("0"))
                    for value in child.cget("values"):
                        if font.measure(value) > room:
                            cut.append(f"Zurich list: {value}")
            if hasattr(child, "_pair_side_by_side"):
                pairs.append("side" if child._pair_side_by_side else "stack")
            walk(child)

    walk(tab)
    return sorted(set(clipped)), sorted(set(cut)), pairs


def screenshot(root, tab, path):
    """Save the page as shown, even when it is larger than the machine's screen.

    GitHub's Windows machines have a 1024x768 desktop, so the page is shot in
    screen-sized tiles, moving the window so each tile is on screen, and the
    tiles are stitched together.
    """
    try:
        from PIL import Image, ImageGrab
    except ImportError:
        return "Pillow missing"
    root.lift()
    root.attributes("-topmost", True)
    for _ in range(4):
        root.update()
    width, height = tab.winfo_width(), tab.winfo_height()
    screen_w, screen_h = root.winfo_screenwidth(), root.winfo_screenheight()
    if sys.platform == "win32":
        screen_h -= 80  # stay clear of the taskbar, which stays on top
    page = Image.new("RGB", (width, height), "black")
    try:
        for top in range(0, height, screen_h):
            for left in range(0, width, screen_w):
                # Move the window until this tile's corner is at the screen's.
                for _ in range(3):
                    dx = tab.winfo_rootx() + left
                    dy = tab.winfo_rooty() + top
                    if (dx, dy) == (0, 0):
                        break
                    root.geometry(f"+{root.winfo_x() - dx}+{root.winfo_y() - dy}")
                    for _ in range(4):
                        root.update()
                time.sleep(0.2)
                root.update()
                tile_w = min(screen_w, width - left)
                tile_h = min(screen_h, height - top)
                page.paste(ImageGrab.grab(bbox=(0, 0, tile_w, tile_h)), (left, top))
    except Exception as error:  # no grabbable display
        return f"screenshot failed: {error}"
    finally:
        root.geometry("+0+0")
        root.update()
    page.save(path)
    return str(path)


def history_check(module):
    root, app, notebook = build(module, 2560, 1440, 2000)
    bad = []

    def pairs(tab):
        found = []

        def walk(widget):
            for child in widget.winfo_children():
                if hasattr(child, "_pair_side_by_side"):
                    found.append(child._pair_side_by_side)
                walk(child)

        walk(tab)
        return tuple(found)

    try:
        for name in COMPACT:
            def at(width):
                root.geometry(f"{width}x900+0+0")
                return pairs(show(root, notebook, name))

            reference = {width: at(width) for width in (900, 1300, 1700, 2000)}
            for history in ((900, 2000), (2000, 900), (1300, 900, 1700), (2000, 1300)):
                for width in history:
                    final = at(width)
                if final != reference[history[-1]]:
                    bad.append(f"{name}: {history} ends {final}, fresh gives {reference[history[-1]]}")
    finally:
        app.closing = True
        app.safe_close_hardware()
        root.destroy()
    return bad


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--shots", type=Path, help="folder for page screenshots")
    args = parser.parse_args()
    module = load_program()
    failed = False
    for screen_w, screen_h, window_w in SCREENS:
        root, app, notebook = build(module, screen_w, screen_h, window_w)
        try:
            print(f"screen {screen_w}x{screen_h}, window {root.winfo_width()} px, "
                  f"ui_scale {app.ui_scale:.2f}")
            for name, sub, tab in pages(root, app, notebook, COMPACT + REPORTED):
                clipped, cut, pairs = inspect(tab)
                problem = bool(clipped or cut)
                lab = screen_w == 2560
                if lab and window_w == 2000 and name in ("STFMR", "FMR") and (
                        not pairs or "stack" in pairs):
                    problem = True
                    cut = cut + ["not side by side at the widened lab window"]
                fatal = lab and name in COMPACT and problem
                failed = failed or fatal
                marker = "FAIL" if fatal else ("note" if problem else "ok  ")
                label = f"{name} {sub}" if sub else name
                print(f"  {marker} {label:9s} layouts {pairs} | clipped {clipped or 'none'} | "
                      f"text cut {cut or 'none'}")
                if args.shots and screen_w == 2560 and name in SHOT_PAGES:
                    # The lab PC: the widened 2000 px window and the default one.
                    args.shots.mkdir(parents=True, exist_ok=True)
                    page = f"{name}_{sub}" if sub else name
                    shot = args.shots / f"{page}_{root.winfo_width()}px.png"
                    print(f"       screenshot: {screenshot(root, tab, shot)}")
        finally:
            app.closing = True
            app.safe_close_hardware()
            root.destroy()
    bad = history_check(module)
    print("resize history: " + ("independent" if not bad else "; ".join(bad)))
    failed = failed or bool(bad)
    print("GUI CHECKS FAILED" if failed else "GUI CHECKS OK")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
