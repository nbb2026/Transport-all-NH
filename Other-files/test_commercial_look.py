"""The commercial-style look (user, 2026-10-18): palette, button roles,
lamps, cards and the connection overview, without a Tk window.

The ttk styles are checked on a recording stand-in for ttk.Style, the
widgets on stand-in trees; the real styles and sizes are checked on CI
(test_pair_layout_loop, test_panel_defaults, gui_checks.py)."""
import ast
import re
import threading
import tkinter as tk
import unittest
from pathlib import Path
from unittest.mock import patch

from test_ppms_motor_debug import app

PROGRAM = Path(__file__).resolve().parents[1] / "Transport_all_NH_dev.py"
HEX = re.compile(r"#[0-9a-f]{6}")


class FakeStyle:
    """ttk.Style's configure / map / layout / lookup, with its name lookup
    from the right ("Danger.Inline.TButton" -> "Inline.TButton" -> "TButton")."""

    def __init__(self, theme="clam", elements=("Button.border",), clone_fails=False):
        self.theme = theme
        self.options = {"TButton": {"padding": "9 6"}, "Compact.TButton": {"padding": (6, 5)},
                        "Inline.TButton": {"padding": "9 3"}}
        self.maps, self.layouts = {}, {"TButton": [("Button.button", {"sticky": "nswe", "children": [
            ("Button.padding", {"sticky": "nswe", "children": [("Button.label", {"sticky": "nswe"})]})]})]}
        self.elements = list(elements)
        self.clone_fails = clone_fails

    def theme_use(self):
        return self.theme

    def element_names(self):
        return tuple(self.elements)

    def element_create(self, name, kind, *args):
        if self.clone_fails:
            raise tk.TclError("no such element")
        self.elements.append(name)

    def configure(self, name, **options):
        self.options.setdefault(name, {}).update(options)

    def map(self, name, **options):
        self.maps.setdefault(name, {}).update(options)

    def _chain(self, name):
        while name:
            yield name
            name = name.partition(".")[2]

    def layout(self, name, spec=None):
        if spec is not None:
            self.layouts[name] = spec
            return None
        for candidate in self._chain(name):
            if candidate in self.layouts:
                return self.layouts[candidate]
        raise tk.TclError(f"Layout {name} not found")

    def lookup(self, name, option):
        for candidate in self._chain(name):
            if option in self.options.get(candidate, {}):
                return self.options[candidate][option]
        return ""


class FakeButton:
    """A probe whose requested size is its padding plus its outer element:
    the native button's 8 px a side, the plain border 2 px."""

    style = None

    def __init__(self, _root, text="", style="TButton"):
        padding = app.parse_ttk_padding(FakeButton.style.lookup(style, "padding"))
        outer = FakeButton.style.layout(style)[0][0]
        edge = 2 if outer == "Look.Button.border" else 8
        self.size = (60 + padding[0] + padding[2] + 2 * edge, 17 + padding[1] + padding[3] + 2 * edge)

    def winfo_reqwidth(self):
        return self.size[0]

    def winfo_reqheight(self):
        return self.size[1]

    def destroy(self):
        pass


class PaletteTests(unittest.TestCase):
    def test_colours_are_hex_and_lamps_keep_their_historical_fills(self):
        for name, value in app.THEME_COLORS.items():
            self.assertRegex(value, HEX, name)
        # The fills the program and its tests have always used.
        self.assertEqual({state: fill for state, (fill, _outline) in app.LAMP_COLORS.items()},
                         {"on": "#20b15a", "off": "#9aa0a6", "fault": "#e53935", "warning": "#f9a825"})
        self.assertEqual((app.LAMP_SIZE_PX, app.LAMP_DOT_PX), (16, 10))
        self.assertEqual(app.THEME_COLORS["plot_series"], app.THEME_COLORS["accent"])
        self.assertEqual(app.THEME_COLORS["plot_background"], "#ffffff")

    def test_one_set_of_red_button_colours(self):
        self.assertIs(app.RTE_RED_BUTTON_COLORS, app.RED_BUTTON_COLORS)
        normal = app.RED_BUTTON_COLORS["normal"]
        self.assertEqual((normal["bg"], normal["fg"], normal["activebackground"], normal["activeforeground"]),
                         ("#c62828", "white", "#8e0000", "white"))
        self.assertNotEqual(app.RED_BUTTON_COLORS["disabled"]["bg"], "#c62828", "a locked button looks locked")

    def test_no_red_or_plot_colour_outside_the_palette(self):
        source = PROGRAM.read_text(encoding="utf-8")
        for old in ("#d70018", "#a80012", "#b8c0c8", "#a51c19"):
            self.assertNotIn(old, source)


class ButtonRoleTests(unittest.TestCase):
    EXPECTED = {
        "Start scan": "Accent", "Start multi-frequency sweep": "Accent",
        "Start STFMR multi-frequency sweep": "Accent", "Start round-trip scan": "Accent",
        "Choose folder and start scan": "Accent", "Choose folder and start 2400-IV sweep": "Accent",
        "Start continuous measurement": "Accent",
        "Stop": "Danger", "Stop scan": "Danger", "Stop / zero": "Danger", "Stop/zero": "Danger",
        "Stop stage": "Danger", "Stop sweep": "Danger", "Stop measurement": "Danger", "Stop Macro": "Danger",
        "Zero": "Danger", "Zero current": "Danger", "Zero field": "Danger", "I off": "Danger",
        "KEPCO Zero / OFF": "Danger", "6221 / 2400 OFF": "Danger", "2400 / 6221 OFF": "Danger",
        "6221 / 2400 output off now": "Danger", "Zero field + persist after scan": "Danger",
        # Neutral: their text toggles, or they are not a start or a stop.
        "Start recording": None, "Stop recording": None, "Enable/zero active path": None,
        "Set motor 0°": None, "Show sent values": None, "Apply output": None, "Connect": None,
        "Offset": None, "Show log": None, "Pause refresh": None, "": None,
    }

    def test_mapping_by_text(self):
        for text, role in self.EXPECTED.items():
            with self.subTest(text=text):
                self.assertEqual(app.button_role(text), role)

    def test_every_start_and_stop_button_of_the_program_gets_its_role(self):
        tree = ast.parse(PROGRAM.read_text(encoding="utf-8"))
        texts = set()
        for node in ast.walk(tree):
            if (isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute) and node.func.attr == "Button"
                    and ast.unparse(node.func.value) == "ttk"):
                for keyword in node.keywords:
                    if keyword.arg == "text" and isinstance(keyword.value, ast.Constant):
                        texts.add(keyword.value.value)
        for text in texts:
            words = re.findall(r"[a-z0-9]+", text.lower())
            if "recording" in words:
                continue
            with self.subTest(text=text):
                if words and words[0] == "start":
                    self.assertEqual(app.button_role(text), "Accent")
                if words and words[0] == "stop":
                    self.assertEqual(app.button_role(text), "Danger")

    def test_style_names(self):
        self.assertEqual(app.button_role_style("Accent"), "Accent.TButton")
        self.assertEqual(app.button_role_style("Danger", "Inline.TButton"), "Danger.Inline.TButton")
        self.assertEqual(app.button_role_style("Accent", "Compact.TButton"), "Accent.Compact.TButton")
        self.assertEqual(app.button_role_style("Danger", ""), "Danger.TButton")


class PaddingTests(unittest.TestCase):
    def test_parse(self):
        self.assertEqual(app.parse_ttk_padding("9 6"), (9, 6, 9, 6))
        self.assertEqual(app.parse_ttk_padding((6, 5)), (6, 5, 6, 5))
        self.assertEqual(app.parse_ttk_padding("4"), (4, 4, 4, 4))
        self.assertEqual(app.parse_ttk_padding("1 2 3"), (1, 2, 3, 2))
        self.assertEqual(app.parse_ttk_padding(("1 2", "3", "4")), (1, 2, 3, 4))
        self.assertEqual(app.parse_ttk_padding(""), (0, 0, 0, 0))

    def test_matched_padding_splits_the_difference(self):
        self.assertEqual(app.matched_padding((9, 6, 9, 6), (100, 40), (88, 28)), (15, 12, 15, 12))
        self.assertEqual(app.matched_padding((9, 6, 9, 6), (100, 40), (87, 27)), (15, 12, 16, 13))
        self.assertIsNone(app.matched_padding((1, 1, 1, 1), (40, 20), (50, 20)), "never a negative padding")


class ButtonStyleTests(unittest.TestCase):
    def test_colour_themes_keep_the_base_layout_and_padding(self):
        style = FakeStyle("clam")
        result = app.configure_button_roles(None, style)
        names = {app.button_role_style(role, base) for role in app.BUTTON_ROLES for base in app.BUTTON_BASE_STYLES}
        self.assertEqual(set(result), names)
        self.assertTrue(all(result.values()))
        for name in names:
            self.assertNotIn(name, style.layouts, "same layout as its base, so the same size")
            self.assertNotIn("padding", style.options[name])
            self.assertNotIn("font", style.options[name], "never another font")
        self.assertEqual(style.options["Accent.TButton"]["background"], app.THEME_COLORS["accent"])
        self.assertEqual(style.options["Danger.Inline.TButton"]["background"], app.THEME_COLORS["danger"])
        self.assertEqual(style.lookup("Danger.Inline.TButton", "padding"), "9 3")
        self.assertIn(("disabled", app.THEME_COLORS["disabled_fill"]), style.maps["Danger.TButton"]["background"])

    def test_native_theme_calibrates_the_plain_border_to_the_same_size(self):
        style = FakeStyle("vista", elements=())
        FakeButton.style = style
        with patch.object(app.ttk, "Button", FakeButton):
            result = app.configure_button_roles(object(), style)
            self.assertTrue(all(result.values()), result)
            for base in app.BUTTON_BASE_STYLES:
                for role in app.BUTTON_ROLES:
                    name = app.button_role_style(role, base)
                    self.assertEqual(style.layouts[name][0][0], "Look.Button.border")
                    self.assertEqual(FakeButton(None, style=name).size, FakeButton(None, style=base).size, name)

    def test_native_theme_without_the_clone_keeps_native_buttons_with_coloured_text(self):
        style = FakeStyle("vista", elements=(), clone_fails=True)
        result = app.configure_button_roles(object(), style)
        self.assertFalse(any(result.values()))
        self.assertEqual(style.options["Accent.TButton"]["foreground"], app.THEME_COLORS["accent"])
        self.assertEqual(style.options["Danger.Compact.TButton"]["foreground"], app.THEME_COLORS["danger"])
        self.assertEqual(style.layouts["Danger.TButton"], style.layouts["TButton"])
        self.assertEqual(app.parse_ttk_padding(style.options["Danger.Inline.TButton"]["padding"]), (9, 3, 9, 3))

    def test_a_size_that_cannot_match_falls_back(self):
        class Wide(FakeButton):
            def __init__(self, root, text="", style="TButton"):
                super().__init__(root, text, style)
                if style.startswith(app.BUTTON_ROLES):
                    self.size = (self.size[0] + 1000, self.size[1])

        style = FakeStyle("vista", elements=())
        FakeButton.style = style
        with patch.object(app.ttk, "Button", Wide):
            result = app.configure_button_roles(object(), style)
        self.assertFalse(any(result.values()))
        self.assertEqual(style.layouts["Accent.TButton"], style.layouts["TButton"])


class Canvas:
    def __init__(self, top=None):
        self.options, self.top = {}, top

    def itemconfigure(self, item, **options):
        self.item = item
        self.options.update(options)

    def winfo_toplevel(self):
        return self.top


class LampTests(unittest.TestCase):
    def test_states(self):
        self.assertEqual(app.lamp_state(True), "on")
        self.assertEqual(app.lamp_state(False), "off")
        self.assertEqual(app.lamp_state(True, fault=True), "fault")
        self.assertEqual(app.lamp_state(None), "off")

    def test_set_lamp_colours_and_tells_the_overview_once_per_change(self):
        calls = []
        top = type("Top", (), {})()
        top._look_overview_refresh = lambda: calls.append(1)
        canvas = Canvas(top)
        for state in ("on", "on", "fault", "off", "nonsense"):
            app.set_lamp((canvas, 3), state)
            fill, outline = app.LAMP_COLORS.get(state, app.LAMP_COLORS["off"])
            self.assertEqual((canvas.item, canvas.options["fill"], canvas.options["outline"]), (3, fill, outline))
        self.assertEqual(len(calls), 4, "on, fault, off, then nonsense as a new state; not the repeated on")
        app.set_lamp(None, "on")  # no lamp: nothing

    def test_a_lamp_set_off_the_tk_thread_does_not_call_tk(self):
        calls = []
        top = type("Top", (), {})()
        top._look_overview_refresh = lambda: calls.append(1)
        canvas = Canvas(top)
        worker = threading.Thread(target=app.set_lamp, args=((canvas, 1), "on"))
        worker.start()
        worker.join()
        self.assertEqual(canvas.options["fill"], "#20b15a")
        self.assertEqual(calls, [])

    def test_the_connection_indicators_use_the_lamp(self):
        harness = object.__new__(app.CombinedControlApp)
        canvas = Canvas()
        harness.instrument_connection_indicators = {"keithley6221": (canvas, 1)}
        harness._set_connection_indicator("keithley6221", True)
        self.assertEqual(canvas.options["outline"], "#10783a")
        harness._set_connection_indicator("keithley6221", True, fault=True)
        self.assertEqual(canvas.options, {"fill": "#e53935", "outline": "#9d1d1a"})
        harness.nonlocal_vi_connection_indicators = {"8": (canvas, 2)}
        harness._set_nonlocal_vi_indicator("8", False)
        self.assertEqual(canvas.options["fill"], "#9aa0a6")


class OverviewTests(unittest.TestCase):
    def test_text(self):
        pages = {"SHV": ["on", "off", "off"], "PPMS": ["off"], "FMR": []}
        self.assertEqual(app.connection_overview_text(pages), ("1/4", "1 of 4 instruments connected", "on"))
        self.assertEqual(app.connection_overview_text({"SHV": ["off"]})[2], "off")
        self.assertEqual(app.connection_overview_text({"SHV": ["on", "fault"]}),
                         ("2/2", "2 of 2 instruments connected", "fault"))
        self.assertEqual(app.connection_overview_text({}), ("0/0", "0 of 0 instruments connected", "off"))

    def test_tooltip_lines_skip_pages_without_instruments(self):
        pages = {"SHV": ["on", "off"], "FMR": [], "PPMS": ["fault"]}
        self.assertEqual(app.connection_overview_lines(pages),
                         ["SHV: 1 of 2 connected", "PPMS: 1 of 1 connected (fault)"])

    def test_no_status_bar(self):
        # The overview costs no height: placed in the tab row, never gridded
        # or packed into the window (user, 2026-10-18).
        source = PROGRAM.read_text(encoding="utf-8")
        body = source[source.index("class ConnectionOverview"):source.index("    def _show_tooltip")]
        self.assertNotIn("self.frame.grid(", body)
        self.assertNotIn("self.frame.pack(", body)
        self.assertIn("self.frame.place(in_=self.notebook", body)


class Widget(tk.Misc):
    """A stand-in widget tree: class, style, text and children (a tk.Misc,
    as the program walks only real widgets' parents)."""

    def __init__(self, kind, parent=None, style="", text="", background=""):
        self.kind, self.style, self.text, self.background = kind, style, text, background
        self.children, self.master = [], parent
        if parent is not None:
            parent.children.append(self)

    def winfo_children(self):
        return list(self.children)

    def winfo_class(self):
        return self.kind

    def cget(self, name):
        return {"style": self.style, "text": self.text, "background": self.background}[name]

    def configure(self, **options):
        if "style" in options:
            self.style = options["style"]
        if "background" in options:
            self.background = options["background"]


class TreeTests(unittest.TestCase):
    def setUp(self):
        self.style = FakeStyle("clam")
        for name in ("TFrame", "TLabel", "TCheckbutton", "Muted.TLabel"):
            self.style.configure(name, background=app.THEME_COLORS["surface"])
        self.style.configure("Status.TLabel", background=app.THEME_COLORS["status"])

    def test_cards_white_page_grey_and_button_roles(self):
        root = Widget("Tk")
        page = Widget("TFrame", root)
        title = Widget("TLabel", page, text="Safety")
        muted = Widget("TLabel", page, style="Muted.TLabel")
        status = Widget("TLabel", page, style="Status.TLabel")
        own = Widget("TLabel", page, background="#fff1a8")
        stop = Widget("TButton", page, text="Stop")
        card = Widget("TLabelframe", page)
        inner = Widget("TFrame", card)
        label = Widget("TLabel", inner)
        check = Widget("TCheckbutton", inner)
        start = Widget("TButton", inner, text="Start scan")
        inline = Widget("TButton", inner, style="Inline.TButton", text="Stop stage")
        strong = Widget("TButton", inner, style="Strong.TButton", text="Start scan")
        connect = Widget("TButton", inner, text="Connect")
        lamp = Widget("Canvas", inner)
        lamp._lamp_kind = "connection"
        page_lamp = Widget("Canvas", page)
        page_lamp._lamp_kind = "connection"
        dialog = Widget("Toplevel", root)
        dialog_label = Widget("TLabel", dialog)
        app.apply_look_to_tree(root, self.style)
        self.assertEqual((page.style, title.style, muted.style), ("Page.TFrame", "Page.TLabel", "Page.Muted.TLabel"))
        self.assertEqual(self.style.options["Page.Muted.TLabel"]["background"], app.THEME_COLORS["page"])
        self.assertEqual((status.style, own.style), ("Status.TLabel", ""), "own backgrounds are kept")
        self.assertEqual((inner.style, label.style, check.style), ("", "", ""), "white inside the card")
        self.assertEqual((stop.style, start.style, inline.style), ("Danger.TButton", "Accent.TButton",
                                                                   "Danger.Inline.TButton"))
        self.assertEqual((strong.style, connect.style), ("Strong.TButton", ""))
        self.assertEqual((lamp.background, page_lamp.background), (app.THEME_COLORS["surface"], app.THEME_COLORS["page"]))
        self.assertEqual(dialog_label.style, "", "other windows keep their look")
        app.apply_look_to_tree(root, self.style)  # again, as on a tab change: no change
        self.assertEqual((page.style, stop.style), ("Page.TFrame", "Danger.TButton"))

    def test_surface_of(self):
        root = Widget("Tk")
        page = Widget("TFrame", root)
        card = Widget("TLabelframe", page)
        inner = Widget("TFrame", card)
        self.assertEqual(app.theme_surface_of(inner), app.THEME_COLORS["surface"])
        self.assertEqual(app.theme_surface_of(page), app.THEME_COLORS["page"])
        self.assertEqual(app.theme_surface_of(object()), app.THEME_COLORS["surface"])

    def test_stand_in_widgets_never_loop(self):
        # A Mock's master is another Mock, forever: the walk must not follow
        # it (it once ran the offline suite out of memory).
        from unittest.mock import Mock
        self.assertEqual(app.theme_surface_of(Mock()), app.THEME_COLORS["surface"])
        app.apply_look_to_tree(Mock(), FakeStyle())
        app.register_lamp(Mock())


class RedButtonTests(unittest.TestCase):
    def test_red_button_fills_a_frame_of_the_ttk_buttons_size_where_it_was(self):
        made = []

        class Frame:
            def __init__(self, parent, **options):
                self.parent, self.options, self.calls = parent, options, []
                made.append(self)

            def grid(self, **grid):
                self.grid_options = grid

            def grid_propagate(self, flag):
                self.propagate = flag

            def columnconfigure(self, *args, **kw):
                pass

            rowconfigure = columnconfigure

        class Button(Frame):
            pass

        class TtkButton:
            master = "params"
            destroyed = False

            def winfo_reqwidth(self):
                return 131

            def winfo_reqheight(self):
                return 34

            def destroy(self):
                TtkButton.destroyed = True

        with patch.object(app.ttk, "Frame", Frame), patch.object(app.tk, "Button", Button):
            button = app.red_button_in_place_of(TtkButton(), "6221 / 2400 OFF", print,
                                                row=3, column=4, columnspan=3, sticky="e")
        holder = made[0]
        self.assertTrue(TtkButton.destroyed)
        self.assertEqual((holder.parent, holder.options), ("params", {"width": 131, "height": 34}))
        self.assertEqual(holder.grid_options, {"row": 3, "column": 4, "columnspan": 3, "sticky": "e"})
        self.assertFalse(holder.propagate)
        self.assertIs(button.parent, holder)
        self.assertEqual({k: button.options[k] for k in ("text", "bg", "fg", "padx", "pady")},
                         {"text": "6221 / 2400 OFF", "bg": "#c62828", "fg": "white", "padx": 0, "pady": 0})

    def test_paint(self):
        class Button:
            def __init__(self):
                self.options = {"state": "normal"}

            def cget(self, name):
                return self.options[name]

            def configure(self, **options):
                self.options.update(options)

        button = Button()
        app.paint_red_button(button)
        self.assertEqual(button.options["bg"], "#c62828")
        button.configure(state="disabled")
        app.paint_red_button(button)
        self.assertEqual(button.options["bg"], app.THEME_COLORS["disabled_fill"])


if __name__ == "__main__":
    unittest.main()
