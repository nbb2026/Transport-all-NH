"""Dual lock-in offline regression; no physical device connections."""
import sys
from pathlib import Path
import unittest
from unittest.mock import Mock

# The shared STFMR harness lives in the test/ subfolder and imports the main
# program by module name from the folder above Other-files.
_HERE = Path(__file__).resolve().parent
for _path in (_HERE / "test", _HERE.parent):
    if str(_path) not in sys.path:
        sys.path.insert(0, str(_path))
from test_stfmr_realtime_recording import StreamingTests, app, read_csv, events


class DualTests(StreamingTests):
    def test_dual_stop_keeps_pairs_and_shuts_down(self):
        h,recorder=self.start(meter='MFLI + SR830',stop='5')
        self.wait_rows(h,2)
        h.stop_stfmr_lockin_scan()
        h.root.drain()
        self.assertEqual(len(read_csv(recorder.csv_path)),2)
        self.assertEqual(events(recorder)[-1]['outcome'],'stopped')
        self.assertIn(('zero',),h.stfmr_lockin_controllers['kepco'].calls)
        self.assertIn(('rf_off',),h.stfmr_lockin_controllers['smb'].calls)

    def draw_markers(self,handler,rows):
        """Open- and filled-circle marker calls for one frequency component."""
        canvas=Mock()
        canvas.winfo_width.return_value=800
        canvas.winfo_height.return_value=400
        handler._draw_plot_grid=Mock()
        handler._draw_stfmr_lockin_frequency_component(canvas,rows,'MFLI + SR830',
                                                      'mfli','x_v','X','wait')
        return ([c for c in canvas.create_oval.call_args_list if len(c.args)==4 and c.args[2]-c.args[0]==5],
                [c for c in canvas.create_oval.call_args_list if len(c.args)==4 and c.args[2]-c.args[0]==3])

    def test_gui_draws_thin_open_and_filled_circle_data_markers(self):
        h,_=self.start(meter='MFLI + SR830',stop='5')
        h.root.drain()
        # Distinct readings so every retained point gets its own marker; the
        # recorded rows all hold the same voltage, which the plot collapses to
        # one marker because the extra ovals would land on the same pixels.
        spread=[dict(row,x_v=3e-06*(index+1)) for index,row in enumerate(h.stfmr_lockin_rows)]
        open_circles,data_circles=self.draw_markers(h,spread)
        self.assertEqual(len(open_circles),3)
        self.assertTrue(all(c.kwargs['fill']=='white' and c.kwargs['outline'] and c.kwargs['width']==.75 for c in open_circles))
        self.assertEqual(len(data_circles),3)
        self.assertTrue(all(c.kwargs['fill']!='white' and not c.kwargs['outline'] for c in data_circles))

    def test_gui_draws_one_marker_for_coincident_points(self):
        """Stacked identical markers are redundant, so only the first is drawn."""
        h,_=self.start(meter='MFLI + SR830',stop='5')
        h.root.drain()
        open_circles,data_circles=self.draw_markers(h,h.stfmr_lockin_rows)
        self.assertEqual((len(open_circles),len(data_circles)),(1,1))

    def test_dual_completed_pairs_and_both_configurations(self):
        h, recorder = self.start(meter='MFLI + SR830')
        h.root.drain()
        rows = read_csv(recorder.csv_path)
        self.assertEqual(len(rows), 12)  # 2 carriers, 3 retained points, 2 meters
        for left,right in zip(rows[::2], rows[1::2]):
            self.assertEqual((left['meter'],right['meter']),('MFLI','SR830'))
            self.assertEqual(left['index'],right['index'])
            self.assertEqual(left['field_g'],right['field_g'])
            self.assertEqual(left['current_a'],right['current_a'])
            self.assertEqual(right['mfli_input_range_v'],'')
            self.assertEqual(right['sr830_ref_in'],'Sine')
        self.assertTrue(any(c[0]=='configure_mfli' for c in h.stfmr_lockin_mfli_controller.calls))
        self.assertTrue(any(c[0]=='configure' for c in h.stfmr_lockin_controllers['830'].calls))
        self.assertIn('SR830',h.stfmr_lockin_voltage_var.get())
        self.assertIn('MFLI',h.stfmr_lockin_reference_status_var.get())
        self.assertEqual(events(recorder)[-1]['outcome'],'completed')
        self.assertEqual(events(recorder)[-1]['saved_points'],12)
        self.assertIn(('rf_off',),h.stfmr_lockin_controllers['smb'].calls)

    def test_dual_sr830_retry_does_not_save_transition(self):
        h, recorder = self.start(meter='MFLI + SR830',stop='5')
        self.wait_rows(h,2)
        h.stfmr_lockin_controllers['830'].retry_next=True
        h.root.drain()
        self.assertEqual(len(read_csv(recorder.csv_path)),6)
        self.assertTrue(any(e['event']=='sr830_autorange_retry' for e in events(recorder)))

    def test_dual_sr830_read_failure_does_not_save_half_point(self):
        h, recorder = self.start(meter='MFLI + SR830',stop='5')
        self.wait_rows(h,2)
        h.stfmr_lockin_controllers['830'].read_sample=Mock(side_effect=RuntimeError('read failed'))
        h.root.drain()
        self.assertEqual(len(read_csv(recorder.csv_path)),2)
        self.assertEqual(events(recorder)[-1]['outcome'],'error')
        self.assertIn(('rf_off',),h.stfmr_lockin_controllers['smb'].calls)

    def test_dual_invalid_reference_rejects_both(self):
        h, recorder = self.start(meter='MFLI + SR830',stop='5')
        self.wait_rows(h,2)
        original=h.stfmr_lockin_mfli_controller.read_stfmr_mfli
        def bad(*a,**kw):
            s=original(*a,**kw);s['reference_valid']=False;return s
        h.stfmr_lockin_mfli_controller.read_stfmr_mfli=bad
        h.root.drain()
        self.assertEqual(len(read_csv(recorder.csv_path)),2)
        self.assertEqual(events(recorder)[-1]['outcome'],'error')

    def test_dual_requires_both_connections(self):
        h,recorder=self.start(meter='MFLI + SR830',stop='5')
        h.root.drain()
        h.stfmr_lockin_controllers['830'].connected=False
        with self.assertRaises(RuntimeError):h._stfmr_lockin_inputs()

    def test_dual_series_and_marker_styles(self):
        h,_=self.start(meter='MFLI + SR830',stop='5')
        h.root.drain()
        series=h._stfmr_lockin_field_component_series(h.stfmr_lockin_rows,'MFLI + SR830','mfli','x_v',group_frequency=True)
        self.assertEqual(set(series),{'mfli_freq_5_loop_1','8_freq_5_loop_1'})
        self.assertTrue(all(len(v)==3 for v in series.values()))
        h._draw_nonlocal_series=Mock()
        h._draw_stfmr_lockin_frequency_component(None,h.stfmr_lockin_rows,'MFLI + SR830','mfli','x_v','X','wait')
        kw=h._draw_nonlocal_series.call_args.kwargs
        self.assertEqual(kw['series_markers']['8_freq_5_loop_1'],'open_circle')
        self.assertEqual(kw['series_markers']['mfli_freq_5_loop_1'],'circle')
        self.assertIn('SR830',kw['series_labels']['8_freq_5_loop_1'])
        current=h._stfmr_lockin_current_field_series(h.stfmr_lockin_rows,'MFLI + SR830','mfli')
        self.assertEqual(sum(map(len,current.values())),3)


if __name__=='__main__':unittest.main()
