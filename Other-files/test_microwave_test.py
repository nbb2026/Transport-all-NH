"""Test_Microwave_NH_dev.py: English text and the lab's two Zurich lock-ins.

The program is built in simulation mode (no instruments) or with its real
controllers, which open nothing until Connect is pressed; the network is
stubbed out.
"""
import importlib.util
import re
import sys
import time
import tkinter as tk
import tkinter.font
import unittest
from pathlib import Path
from unittest import mock

PROGRAM = Path(__file__).resolve().parents[1] / "Test_Microwave_NH_dev.py"
spec = importlib.util.spec_from_file_location("microwave_test_program", PROGRAM)
mw = importlib.util.module_from_spec(spec)
sys.modules["microwave_test_program"] = mw
spec.loader.exec_module(mw)

CJK = re.compile(r"[　-〿㐀-鿿＀-￯]")
SUPER = [("192.168.68.250", 8004)]
USB = [("127.0.0.1", 8004), ("192.168.69.78", 8004)]


class EnglishTextTests(unittest.TestCase):
    def test_no_chinese_or_full_width_characters_remain(self):
        left = [f"{n}: {line.strip()[:80]}"
                for n, line in enumerate(PROGRAM.read_text(encoding="utf-8").splitlines(), 1)
                if CJK.search(line)]
        self.assertEqual(left, [])


class ZurichConnectionTests(unittest.TestCase):
    def test_each_lockin_tries_only_the_servers_that_can_reach_it(self):
        for device, servers, expected in (("dev4414", tuple(SUPER), SUPER), ("dev4435", None, USB)):
            with self.subTest(device=device):
                probed = []

                def refuse(address, timeout=None):
                    probed.append(tuple(address))
                    raise OSError("refused (test)")

                controller = mw.NonLocalACController(lambda _message: None)
                controller.mfli_server_candidates = servers
                with mock.patch.object(mw.socket, "create_connection", refuse):
                    with self.assertRaises(RuntimeError) as caught:
                        controller.connect_mfli(device)
                self.assertEqual(probed, expected)
                self.assertFalse(CJK.search(str(caught.exception)), "the message is in English")

    def test_list_entries_and_bare_names_map_to_devices(self):
        for text, expected in (("DEV4414 (Super)", ("dev4414", tuple(SUPER))),
                               ("MF-DEV4414", ("dev4414", tuple(SUPER))),
                               ("DEV4435 (Normal)", ("dev4435", None)),
                               ("dev4435", ("dev4435", None))):
            with self.subTest(text=text):
                self.assertEqual(mw.zurich_mfli_choice(text), expected)
        self.assertEqual(mw.zurich_mfli_label("dev4435"), "DEV4435 (Normal)")


class WindowTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        try:
            cls.root = tk.Tk()
        except tk.TclError as error:  # headless build server
            raise unittest.SkipTest(f"no display: {error}")
        cls.root.tk.call("tk", "scaling", 1.333)  # 96 DPI, as on the lab PC
        cls.app = mw.MicrowaveTransportApp(cls.root, demo=False)
        cls.root.geometry("1360x860+0+0")
        for _ in range(4):
            cls.root.update()

    @classmethod
    def tearDownClass(cls):
        cls.app.closing = True
        cls.app.executor.shutdown(wait=False)
        cls.root.destroy()

    def chooser(self):
        def walk(widget):
            for child in widget.winfo_children():
                yield child
                yield from walk(child)
        return next(w for w in walk(self.root) if getattr(w, "zurich_chooser", False) is True)

    def test_mfli_field_is_the_zurich_list_with_the_super_one_marked(self):
        chooser = self.chooser()
        self.assertEqual(str(chooser.cget("state")), "readonly")
        self.assertEqual(tuple(chooser.cget("values")), ("DEV4435 (Normal)", "DEV4414 (Super)"))
        self.assertEqual(chooser.get(), "DEV4435 (Normal)", "the default lock-in is unchanged")
        font = tkinter.font.Font(font=chooser.cget("font") or "TkTextFont")
        room = chooser.winfo_width() - (chooser.winfo_reqwidth() - int(chooser.cget("width")) * font.measure("0"))
        for label in chooser.cget("values"):
            self.assertLessEqual(font.measure(label), room, f"{label} is cut")

    def test_connecting_the_super_zurich_tries_its_own_server(self):
        probed, errors = [], []

        def refuse(address, timeout=None):
            probed.append(tuple(address))
            raise OSError("refused (test)")

        self.addCleanup(self.app.resources["mfli"].set, "DEV4435 (Normal)")
        self.app.resources["mfli"].set("DEV4414 (Super)")
        with mock.patch.object(mw.socket, "create_connection", refuse), \
                mock.patch.object(mw.messagebox, "showerror", lambda *a, **k: errors.append(a)):
            self.app.connection("mfli", "connect")
            for _ in range(500):
                self.root.update()
                if errors:
                    break
                time.sleep(0.01)
        self.assertEqual(probed, SUPER)
        self.assertEqual(len(errors), 1)
        self.assertFalse(self.app.connected("mfli"))
        self.assertEqual(str(self.chooser().cget("state")), "readonly", "choosable again, not typable")


if __name__ == "__main__":
    unittest.main()
