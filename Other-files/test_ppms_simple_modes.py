"""Four-mode launch validation without Tk or instrument writes."""
import threading
import tempfile
import csv
from pathlib import Path
import ast
import inspect
import textwrap
import types
import unittest
from unittest.mock import Mock, patch
from test_ppms_motor_debug import app


class ModeTests(unittest.TestCase):
    def test_stop_is_non_modal_in_all_four_modes(self):
        obj = app.CombinedControlApp.__new__(app.CombinedControlApp)
        obj.closing, obj.root = False, None
        obj.thread_log = Mock()
        obj.ppms_field_scan_status_var = Mock()
        obj.ppms_field_scan_eta_var = Mock()
        for mode in ("Temperature", "Field", "Angle", "Current"):
            for error in (None, app.PPMSScanCancelled("stopped as requested while waiting for lock-in reference")):
                with patch.object(app.messagebox, "showerror") as popup:
                    obj._report_ppms_scan_finished(mode, True, error)
                    popup.assert_not_called()
                self.assertIn(f"{mode} scan stopped", obj.ppms_field_scan_status_var.set.call_args.args[0])

    def test_real_failure_is_not_hidden_by_stop(self):
        obj = app.CombinedControlApp.__new__(app.CombinedControlApp)
        obj.closing, obj.root = False, None
        obj.ppms_field_scan_status_var = Mock()
        obj.ppms_field_scan_eta_var = Mock()
        with patch.object(app.messagebox, "showerror") as popup:
            obj._report_ppms_scan_finished("Current", True, OSError("CSV write failed"))
            popup.assert_called_once_with("PPMS Current scan", "CSV write failed", parent=None)

    def test_controls_are_created_inside_static_content(self):
        tree = ast.parse(textwrap.dedent(inspect.getsource(app.CombinedControlApp.build_ppms_ui)))
        parents = {}
        for node in ast.walk(tree):
            if isinstance(node, ast.Assign) and isinstance(node.value, ast.Call) and node.value.args:
                for target in node.targets:
                    if isinstance(target, ast.Name) and isinstance(node.value.args[0], ast.Name):
                        parents[target.id] = node.value.args[0].id
        self.assertEqual(parents["content"], "outer")
        self.assertNotIn("scroller", parents)
        for name in ("environment", "control", "instruments"):
            self.assertEqual(parents[name], "content")
        self.assertEqual(parents["transport_plot"], "outer")
        self.assertEqual(parents["scan"], "content")
        self.assertEqual(parents["metrics_outer"], "content")
        self.assertEqual(parents["metrics_view"], "metrics_outer")
        self.assertEqual(parents["metrics"], "metrics_view")
        self.assertNotIn("in_=", inspect.getsource(app.CombinedControlApp._compact_ppms_controls))

    def launch(self, mode, values, confirm=True, cancel_folder=False, filename="", existing=None, selected=None):
        obj = app.CombinedControlApp.__new__(app.CombinedControlApp)
        obj.ppms_field_scan_running = obj.ppms_environment_busy = False
        obj._require_ppms_control_ready = Mock()
        obj.ppms_simple_mode = Mock(get=lambda: mode)
        obj.ppms_simple_range = [Mock(get=lambda v=v: v) for v in values]
        obj.ppms_temperature_scan_range = [Mock(get=lambda v=v: v) for v in (300, 290, 5)]
        defaults = dict(ppms_acquisition_time_var=1, ppms_field_target_var=100, ppms_temperature_target_var=300,
            ppms_motor_target_var=26, ppms_field_scan_stable_var=0,
            ppms_field_rate_var=100, ppms_field_approach_var="No overshoot",
            ppms_transport_current_var=.001, ppms_transport_frequency_var=13.7,
            ppms_transport_compliance_var=50, ppms_transport_harmonic_a_var=1,
            ppms_transport_harmonic_b_var=2, ppms_motor_confirm_var=True,
            ppms_temperature_rate_var=1, ppms_temperature_approach_var="No overshoot",
            ppms_motor_speed_var=14)
        for name, value in defaults.items():
            setattr(obj, name, Mock(get=lambda v=value: v))
        obj.ppms_environment_controller = types.SimpleNamespace(backend=app.PPMS_QDINSTRUMENT_BACKEND)
        obj.ppms_instrument_controllers = {k: types.SimpleNamespace(connected=True, resource_name=f"GPIB0::{address}::INSTR")
            for k, address in (("6221", 30), ("830_a", 8), ("830_b", 9))}
        if selected is not None:
            obj.ppms_lockin_selection = {key: Mock(get=lambda key=key: key in selected)
                                        for key in ('830_a', '830_b', 'zurich')}
            obj.zurich_controller = types.SimpleNamespace(mfli_connected='zurich' in selected)
            for key in ('830_a', '830_b'):
                obj.ppms_instrument_controllers[key].connected = key in selected
        obj.ppms_last_readback = dict(temperature_k=300, field_oe=100)
        obj.ppms_field_scan_stop_event = threading.Event()
        for name in ("_redraw_ppms_transport_plot", "_update_ppms_control_ui", "ppms_executor",
                     "ppms_field_scan_progress", "ppms_field_scan_eta_var", "ppms_field_scan_status_var", "ppms_transport_status_var"):
            setattr(obj, name, Mock())
        obj.root = None
        obj.thread_log = Mock()
        order = []
        def answer(*args, **kwargs):
            order.append("confirm")
            return confirm
        with tempfile.TemporaryDirectory() as folder, patch.object(app.simpledialog, "askstring", return_value=filename), patch.object(app.messagebox, "askyesno", side_effect=answer), patch.object(app.filedialog, "askdirectory", side_effect=lambda **kw: (order.append("folder") or ("" if cancel_folder else folder))), patch.object(app.messagebox, "showerror") as error:
            if existing:
                (Path(folder) / existing).write_text("original", encoding="utf-8")
            obj.start_ppms_field_scan()
            self.assertFalse(error.called, error.call_args)
            self.assertEqual(order, ["confirm", "folder"] if confirm else ["confirm"])
            if not confirm or cancel_folder or filename is None:
                obj.ppms_executor.submit.assert_not_called()
                self.assertFalse(obj.ppms_field_scan_running)
                self.assertEqual(list(Path(folder).iterdir()), [])
                return
            if existing:
                self.assertEqual((Path(folder) / existing).read_text(encoding="utf-8"), "original")
        args = obj.ppms_executor.submit.call_args.args
        return obj, args[1], args[-1]

    def test_four_modes(self):
        for mode, values, axis in (("Temperature", (300, 290, 5), "temperature"),
                                   ("Field", (100, 300, 100), "field"),
                                   ("Angle", (26, 28, 1), "angle"),
                                   ("Current", (.001, .003, .001), "current")):
            obj, sequence, settings = self.launch(mode, values)
            self.assertEqual(obj.ppms_plot_axis, axis)
            self.assertIn("estimated about", obj.ppms_field_scan_status_var.set.call_args.args[0])
            self.assertNotIn("等待另计", obj.ppms_field_scan_status_var.set.call_args.args[0])
            self.assertEqual(obj.ppms_field_scan_total, 5 if mode == "Field" else 3)
            self.assertEqual(len(sequence), 5 if mode == "Field" else 1)
            self.assertEqual(len(settings["nested"]["temperature"]), 3 if mode == "Temperature" else 1)
            self.assertEqual(len(settings["nested"]["angle"]), 3 if mode == "Angle" else 1)
            self.assertEqual(len(settings["currents"]), 3 if mode == "Current" else 1)

    def test_optional_filename_and_no_overwrite(self):
        for entered, expected in (("sampleA", "sampleA.csv"), ("sampleA.csv", "sampleA.csv")):
            obj, _, _ = self.launch("Angle", (0, 10, 5), filename=entered)
            self.assertEqual(obj.ppms_live_csv_path.name, expected)
        obj, _, _ = self.launch("Angle", (0, 10, 5), filename="sampleA.csv", existing="sampleA.csv")
        self.assertEqual(obj.ppms_live_csv_path.name, "sampleA_01.csv")
        obj, _, _ = self.launch("Angle", (0, 10, 5), filename=" ")
        self.assertTrue(obj.ppms_live_csv_path.name.startswith("PPMS_Angle_"))
        self.launch("Angle", (0, 10, 5), filename=None)

    def test_temperature_field_launch(self):
        obj, sequence, settings = self.launch("Field+Temperature", (-100, 100, 100))
        self.assertEqual(obj.ppms_plot_axis, "field")
        self.assertEqual(obj.ppms_field_scan_total, 15)
        self.assertEqual([point[3] for point in sequence], [-100, 0, 100, 0, -100])
        self.assertEqual(settings["nested"]["temperature"], (300, 295, 290))
        self.assertEqual(settings["nested"]["angle"], (26,))
        self.assertEqual(settings["currents"], (.001,))

    def test_temperature_outer_angle_launch(self):
        obj, sequence, settings = self.launch("Angle+Temperature", (-5, 355, 5))
        self.assertEqual(obj.ppms_plot_axis, "angle")
        self.assertEqual(obj.ppms_field_scan_total, 73 * 3)
        self.assertEqual([p[3] for p in sequence], [100])
        self.assertEqual(settings["nested"]["temperature"], (300, 295, 290))
        self.assertEqual(settings["nested"]["angle"], tuple(range(-5, 356, 5)))

    def test_temperature_outer_current_launch(self):
        obj, sequence, settings = self.launch("Current+Temperature", (1, 3, 1))
        self.assertEqual(obj.ppms_plot_axis, "current")
        self.assertEqual(obj.ppms_field_scan_total, 9)
        self.assertEqual(settings["currents"], (1., 2., 3.))
        self.assertEqual(settings["current_rms_ma"], 1.)
        self.assertEqual(settings["nested"], {"angle": (26.,), "temperature": (300., 295., 290.)})
        self.assertEqual([p[3] for p in sequence], [100.])

    def test_launch_with_only_selected_lockins_connected(self):
        for selected in (('830_a',), ('830_b',), ('zurich',), ('830_a', 'zurich')):
            obj, sequence, settings = self.launch('Field', (0, 10, 5), selected=selected)
            self.assertEqual(settings['active_lockins'], tuple(k for k in selected if k != 'zurich'))
            self.assertEqual(settings['allow_no_lockins'], 'zurich' in selected)
            self.assertTrue(obj.ppms_field_scan_running)

    def test_decline_and_folder_cancel_do_not_start(self):
        self.launch("Field", (0, 100, 10), confirm=False)
        self.launch("Field", (0, 100, 10), cancel_folder=True)

    def test_field_roundtrip_descending_uneven_and_single_point(self):
        for limits, expected in (((100, -100, 75), [100, 25, -50, -100, -50, 25, 100]),
                                 ((0, 0, 100), [0])):
            obj, sequence, settings = self.launch("Field", limits)
            self.assertEqual([p[3] for p in sequence], expected)
            self.assertEqual(obj.ppms_field_scan_total, len(expected))
            self.assertEqual([p[1] for p in sequence], list(range(1, len(expected)+1)))

    def test_live_csv_appends_rows_with_one_header(self):
        obj = app.CombinedControlApp.__new__(app.CombinedControlApp)
        with tempfile.TemporaryDirectory() as folder:
            obj.ppms_live_csv_path = Path(folder) / "scan.csv"
            obj.ppms_live_csv_fields = None
            obj._append_ppms_live_csv(dict(field=1, x=2))
            obj._append_ppms_live_csv(dict(field=3, x=4))
            before = obj.ppms_live_csv_path.read_bytes()
            with self.assertRaisesRegex(RuntimeError, "channels changed"):
                obj._append_ppms_live_csv(dict(field=5, y=6))
            self.assertEqual(obj.ppms_live_csv_path.read_bytes(), before)
            with obj.ppms_live_csv_path.open(encoding="utf-8-sig") as handle:
                self.assertEqual(list(csv.DictReader(handle)), [dict(field="1", x="2"), dict(field="3", x="4")])


if __name__ == "__main__":
    unittest.main()
