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

Exit status is 1 if a compact page (MOKE-6221, STFMR, FMR) clips or cuts
anything, or depends on resize history.  The other pages are reported only:
they were never compacted and already clip in narrow windows.
"""
import argparse
import importlib.util
import sys
import time
import tkinter as tk
import tkinter.font
import types
from pathlib import Path

PROGRAM = Path(__file__).resolve().parents[2] / "Transport_all_NH_dev.py"
COMPACT = ("MOKE-6221", "STFMR", "FMR")
REPORTED = ("Gate-VI", "PPMS", "RT-E")
SCREENS = (  # (screen width, height, window width or 0 for the program's own)
    (2560, 1440, 2000),  # the lab PC, window widened by hand
    (2560, 1440, 0),     # the lab PC, the program's default window
    (1920, 1080, 0),
    (1600, 1586, 0),
)
SHOT_PAGES = ("STFMR", "FMR", "MOKE-6221", "Gate-VI", "PPMS", "RT-E")


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
    width = window_w or app.window_layout["control"][0]
    root.geometry(f"{width}x1300+0+0")
    root.update()
    notebook = next(v for v in app.__dict__.values() if isinstance(v, tk.ttk.Notebook))
    return root, app, notebook


def show(root, notebook, name):
    for index in range(notebook.index("end")):
        if notebook.tab(index, "text") == name:
            notebook.select(index)
            for _ in range(4):
                root.update()
            return notebook.nametowidget(notebook.tabs()[index])
    raise KeyError(name)


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
    return font.measure(text) <= widget.winfo_width() - slack


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
                    for value in child.cget("values"):
                        if font.measure(value) + 30 > child.winfo_width():
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
            for name in COMPACT + REPORTED:
                tab = show(root, notebook, name)
                clipped, cut, pairs = inspect(tab)
                problem = bool(clipped or cut)
                if name in COMPACT and problem:
                    failed = True
                marker = "FAIL" if name in COMPACT and problem else ("note" if problem else "ok  ")
                print(f"  {marker} {name:9s} layouts {pairs} | clipped {clipped or 'none'} | "
                      f"text cut {cut or 'none'}")
                if args.shots and screen_w == 2560 and name in SHOT_PAGES:
                    # The lab PC: the widened 2000 px window and the default one.
                    args.shots.mkdir(parents=True, exist_ok=True)
                    shot = args.shots / f"{name}_{root.winfo_width()}px.png"
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
