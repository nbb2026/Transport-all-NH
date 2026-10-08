"""Every page that uses a Zurich MFLI picks one of the lab's two by name.

DEV4435 is reached over its USB link (192.168.69.78, or a PC-side LabOne
server); DEV4414, the Super Zurich, has its own Data Server on the lab network
at 192.168.68.250.  Each server sees only its own MFLI, so a page that probed
DEV4435's servers for DEV4414 could never connect: the STFMR and PPMS bug.
These tests run the fully built application with the network stubbed out.
"""
import time
import tkinter as tk
import tkinter.font
import unittest
from unittest import mock

from lab_screen import LAB_WIDE, build_lab_app, lab_window
from test_ppms_motor_debug import app

SUPER = [("192.168.68.250", 8004)]
USB = [("127.0.0.1", 8004), ("192.168.69.78", 8004)]
CHOICES = ("DEV4435 (Normal)", "DEV4414 (Super)")


class ZurichChoiceTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        try:
            cls.root = tk.Tk()
        except tk.TclError as error:  # headless build server
            raise unittest.SkipTest(f"no display: {error}")
        cls.app = build_lab_app(app, cls.root)  # as the lab PC shows it
        cls.root.geometry(f"{LAB_WIDE}x{lab_window(cls.app)[1]}+0+0")
        cls.root.update()

    @classmethod
    def tearDownClass(cls):
        cls.app.closing = True
        cls.app.safe_close_hardware()
        cls.root.destroy()

    def setUp(self):
        self.a = self.app
        self.errors, self.probed = [], []

        def refuse(address, timeout=None):
            self.probed.append(tuple(address))
            raise OSError("refused (test)")

        for patcher in (
            mock.patch.object(app.messagebox, "showerror", lambda *a, **k: self.errors.append(a[1])),
            mock.patch.object(app.messagebox, "showwarning", lambda *a, **k: self.errors.append(a[1])),
            mock.patch.object(app.socket, "create_connection", refuse),
        ):
            patcher.start()
            self.addCleanup(patcher.stop)

    def pump(self, done):
        for _ in range(500):
            self.root.update()
            if done():
                return
            time.sleep(0.01)
        self.fail("the connection attempt never finished")

    def show(self, tab_name):
        notebook = next(w for w in self.a.__dict__.values() if isinstance(w, tk.ttk.Notebook))
        for index in range(notebook.index("end")):
            if notebook.tab(index, "text") == tab_name:
                notebook.select(index)
        for _ in range(3):
            self.root.update()

    def choosers(self):
        """(page, list widget, its variable, default entry) for every Zurich user."""
        vi = next(w for w in self._descendants(self.root)
                  if w.winfo_class() == "TCombobox"
                  and str(w.cget("textvariable")) == str(self.a.nonlocal_vi_mfli_device_var))
        return (
            ("STFMR", self.a.stfmr_lockin_resource_widgets["mfli"],
             self.a.stfmr_lockin_resource_vars["mfli"], "DEV4435 (Normal)"),
            ("Non-local", vi, self.a.nonlocal_vi_mfli_device_var, "DEV4435 (Normal)"),
            ("PPMS", self.a.ppms_panel.zurich_entries[0], self.a.ppms_panel.zurich_resource,
             "DEV4414 (Super)"),
            ("RT-E", self.a.rte_panel.zurich_entries[0], self.a.rte_panel.zurich_resource,
             "DEV4414 (Super)"),
        )

    @classmethod
    def _descendants(cls, widget):
        for child in widget.winfo_children():
            yield child
            yield from cls._descendants(child)

    # ------------------------------------------------------------------ lists
    def test_every_zurich_page_offers_both_lockins_with_the_super_one_marked(self):
        for page, chooser, variable, default in self.choosers():
            with self.subTest(page=page):
                self.show(page)
                self.assertEqual(chooser.winfo_class(), "TCombobox")
                self.assertEqual(tuple(chooser.cget("values")), CHOICES)
                self.assertEqual(str(chooser.cget("state")), "readonly", "chosen, never typed")
                self.assertEqual(variable.get(), default, "each page keeps its lock-in")
                font = tkinter.font.Font(font=chooser.cget("font") or "TkTextFont")
                # The text area: ttk sizes it as width x the width of "0".
                room = chooser.winfo_width() - (
                    chooser.winfo_reqwidth() - int(chooser.cget("width")) * font.measure("0"))
                for label in CHOICES:
                    self.assertLessEqual(font.measure(label), room, f"{label} is cut")

    def test_gate_vi_list_is_as_wide_as_the_sr830_address_fields(self):
        self.show("Non-local")
        chooser = self.choosers()[1][1]
        sr830 = next(w for w in self._descendants(self.root)
                     if w.winfo_class() == "TEntry"
                     and str(w.cget("textvariable")) == str(self.a.nonlocal_vi_lockin_resource_vars["8"]))
        self.assertEqual((chooser.winfo_rootx(), chooser.winfo_width()),
                         (sr830.winfo_rootx(), sr830.winfo_width()))

    def test_rte_list_is_as_wide_as_the_rack_address_fields(self):
        self.show("RT-E")
        chooser = self.a.rte_panel.zurich_entries[0]
        rack = self.a.rte_panel.ppms_device_resource_widgets["6221"]
        self.assertEqual(chooser.winfo_width(), rack.winfo_width())

    # ---------------------------------------------------------------- servers
    def test_stfmr_and_gate_vi_try_the_server_that_can_reach_the_chosen_lockin(self):
        pages = (
            ("STFMR", self.a.stfmr_lockin_resource_vars["mfli"],
             lambda: self.a.connect_stfmr_lockin_device("mfli"), self.a.stfmr_lockin_mfli_controller),
            ("Non-local", self.a.nonlocal_vi_mfli_device_var,
             lambda: self.a.connect_nonlocal_vi_device("mfli"), self.a.nonlocal_vi_ac_controller),
        )
        for page, variable, connect, controller in pages:
            self.addCleanup(variable.set, "DEV4435 (Normal)")
            self.addCleanup(setattr, controller, "mfli_server_candidates", None)
            for label, servers in (("DEV4414 (Super)", SUPER), ("DEV4435 (Normal)", USB)):
                with self.subTest(page=page, choice=label):
                    self.probed.clear()
                    self.errors.clear()
                    variable.set(label)
                    connect()
                    self.pump(lambda: self.errors)
                    self.assertEqual(self.probed, servers)
                    self.assertFalse(controller.mfli_connected)

    def test_gate_vi_connect_all_hands_the_chosen_device_and_servers_on(self):
        variable = self.a.nonlocal_vi_mfli_device_var
        controller = self.a.nonlocal_vi_ac_controller
        self.addCleanup(variable.set, "DEV4435 (Normal)")
        self.addCleanup(setattr, controller, "mfli_server_candidates", None)
        self.addCleanup(setattr, self.a, "nonlocal_vi_busy", False)
        self.addCleanup(self.a.nonlocal_vi_use_mfli_var.set, self.a.nonlocal_vi_use_mfli_var.get())
        variable.set("DEV4414 (Super)")
        self.a.nonlocal_vi_use_mfli_var.set(True)
        with mock.patch.object(self.a.nonlocal_vi_executor, "submit") as submit:
            self.a.connect_nonlocal_vi()
        self.assertEqual(self.errors, [])
        self.assertEqual(submit.call_args.args[4], "dev4414")
        self.assertEqual(list(controller.mfli_server_candidates), SUPER)

    def test_ppms_and_rte_try_the_server_that_can_reach_the_chosen_lockin(self):
        """PPMS asked DEV4435's 192.168.69.78 server for DEV4414 (2026-09-18 log)."""
        for page, panel in (("PPMS", self.a.ppms_panel), ("RT-E", self.a.rte_panel)):
            self.addCleanup(panel.zurich_resource.set, "DEV4414 (Super)")
            for label, servers in (("DEV4414 (Super)", SUPER), ("DEV4435 (Normal)", USB)):
                with self.subTest(page=page, choice=label):
                    self.probed.clear()
                    self.errors.clear()
                    panel.zurich_resource.set(label)
                    panel.connect_ppms2_zurich()
                    self.pump(lambda: not panel.zurich_busy and self.probed)
                    self.assertEqual(self.probed, servers)
                    self.assertFalse(panel.zurich_controller.mfli_connected)
                    self.assertEqual(str(panel.zurich_entries[0].cget("state")), "readonly",
                                     "after a failure the list is choosable again, not typable")

    # --------------------------------------------------------------- messages
    def test_failure_points_at_the_other_lockin_without_misleading_addresses(self):
        for device, servers, present, absent in (
                ("dev4414", SUPER, ("Super Zurich", '"DEV4435 (Normal)"', "192.168.68.250"),
                 ("192.168.69.78", "192.168.69.77")),
                ("dev4435", None, ('"DEV4414 (Super)"', "192.168.68.250", "192.168.69.78"), ())):
            with self.subTest(device=device):
                controller = app.NonLocalACController(lambda _message: None)
                controller.mfli_server_candidates = servers
                with self.assertRaises(RuntimeError) as caught:
                    controller.connect_mfli(device)
                message = str(caught.exception)
                for text in present:
                    self.assertIn(text, message)
                for text in absent:
                    self.assertNotIn(text, message)


if __name__ == "__main__":
    unittest.main()
