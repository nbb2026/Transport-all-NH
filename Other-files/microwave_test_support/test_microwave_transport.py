"""Offline tests only: no VISA/LabOne sessions opened."""
import csv, json, math, sys, tempfile, threading, unittest
from dataclasses import replace, asdict
from pathlib import Path
from unittest.mock import patch
# The program under test is Test_Microwave_NH_dev.py at the repository root.
import importlib.util
_spec=importlib.util.spec_from_file_location('Test_Microwave_NH_dev',Path(__file__).resolve().parents[2]/'Test_Microwave_NH_dev.py')
m=importlib.util.module_from_spec(_spec);sys.modules['Test_Microwave_NH_dev']=m;_spec.loader.exec_module(m)

class Fake(m.DemoDevice):
    def __init__(self,bank,key):super().__init__(bank,key);self.connected=True;self.calls=[];self.reads=0;self.fail=None
    def set_output(self,*args):self.calls.append(('set_output',args));super().set_output(*args)
    def set_scan_current(self,current):self.calls.append(('reapply',current));super().set_scan_current(current)
    def zero_output(self):self.calls.append(('zero',));super().zero_output()
    def rf_off(self):self.calls.append(('off',));super().rf_off()
    def write(self,c):self.calls.append(('write',c));super().write(c)
    def read_field(self):self.reads+=1;return {'field':999+self.reads*50,'unit':'G','unit_code':1}
    def read_sample(self,expected):
        self.reads+=1
        if self.fail=='read':raise RuntimeError('read failure')
        r=super().read_sample(expected)
        if self.fail=='lock':r['reference_valid']=False
        if self.fail=='overload':r['input_overload']=True
        if self.fail=='range':r['auto_range']='retry'
        if self.fail=='first_range' and self.reads==1:r['auto_range']='retry'
        return r

class EngineTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup)
        self.bank={'current':0.,'rf':False};self.devices={k:Fake(self.bank,k) for k in m.STFMR_LOCKIN_DEFAULT_RESOURCES}
        self.s=m.GridSettings((1.,2.),(17.3,317.3),fixed_current_a=-.15,samples_per_cell=2)
        self.stop=threading.Event();self.events=[]
    def setup_engine(self,settings=None,emit=None):
        rec=m.GridRecorder(self.temp.name,asdict(settings or self.s))
        engine=m.GridEngine(self.devices,settings or self.s,rec,self.stop,emit or (lambda *e:self.events.append(e)))
        engine.wait=lambda seconds:engine.check()
        return engine,rec
    def run_engine(self,engine):
        with patch.object(m,'export_grid'):return engine.run()
    def assert_off(self):
        self.assertEqual(self.bank['current'],0);self.assertFalse(self.bank['rf'])
        self.assertIn(('off',),self.devices['smb'].calls);self.assertIn(('zero',),self.devices['kepco'].calls)
    def test_fixed_current_grid_order_and_raw_samples(self):
        e,r=self.setup_engine();result=self.run_engine(e);self.assertEqual(result['outcome'],'completed');self.assert_off()
        calls=[c for c in self.devices['kepco'].calls if c[0]=='set_output'];self.assertEqual(calls,[('set_output',('CURR',-.15,10.))])
        self.assertEqual([c for c in self.devices['kepco'].calls if c[0]=='reapply'],[('reapply',-.15)])
        with r.csv_path.open(encoding='utf-8-sig') as f:rows=list(csv.DictReader(f))
        self.assertEqual(len(rows),8)
        self.assertEqual([(c['modulation_index'],c['microwave_index']) for c in e.cells.values()],[(0,0),(0,1),(1,0),(1,1)])
        self.assertTrue(all(float(row['current_a'])==-.15 for row in rows))
        self.assertGreater(float(rows[-1]['field_after_g']),float(rows[0]['field_g']))
        self.assertEqual(self.devices['mfli'].reads,16) # 2 discard + 2 save for EACH cell
    def test_wait_monitors_without_current_feedback(self):
        e,r=self.setup_engine();self.addCleanup(r.close,'test')  # closed even if a check fails (Windows locks open files)
        m.GridEngine.wait(e,.01)
        # At least one field reading: Windows' coarser clock can fit a second one into the wait.
        self.assertGreaterEqual(self.devices['gauss'].reads,1)
        self.assertEqual(self.devices['kepco'].calls,[])
        self.assertTrue(any(kind=='field' for kind,data in self.events))
    def test_reapply_failure_cleans_up(self):
        e,r=self.setup_engine()
        self.devices['kepco'].set_scan_current=lambda value:(_ for _ in ()).throw(RuntimeError('reapply failed'))
        result=self.run_engine(e);self.assertEqual(result['error'],'reapply failed');self.assert_off();self.assertEqual(r.rows_written,0)
    def test_stop_before_main_thread_reapply(self):
        e,r=self.setup_engine()
        def dispatch(fn):self.stop.set();return fn()
        e.main_thread_call=dispatch
        self.assertEqual(self.run_engine(e)['outcome'],'stopped');self.assert_off()
        self.assertFalse(any(c[0]=='reapply' for c in self.devices['kepco'].calls))
    def test_sr830_same_grid(self):
        e,r=self.setup_engine(replace(self.s,meter='SR830'));self.assertEqual(self.run_engine(e)['outcome'],'completed');self.assertEqual(r.rows_written,8);self.assert_off()
    def test_cancel_before_enable(self):
        e,r=self.setup_engine();self.stop.set();self.assertEqual(self.run_engine(e)['outcome'],'stopped')
        self.assertFalse(any(c[0]=='set_output' for c in self.devices['kepco'].calls));self.assertEqual(r.rows_written,0);self.assert_off()
    def test_cancel_after_first_complete_cell(self):
        def emit(kind,data):
            if kind=='cell':self.stop.set()
        e,r=self.setup_engine(emit=emit);self.assertEqual(self.run_engine(e)['outcome'],'stopped');self.assertEqual(len(e.cells),1);self.assertEqual(r.rows_written,2);self.assert_off()
    def test_stop_on_read_does_not_append_late_row(self):
        e,r=self.setup_engine();old=self.devices['mfli'].read_stfmr_mfli
        def read(*a,**k):
            v=old(*a,**k)
            if self.devices['mfli'].reads==3:self.stop.set()
            return v
        self.devices['mfli'].read_stfmr_mfli=read
        self.assertEqual(self.run_engine(e)['outcome'],'stopped');self.assertEqual(r.rows_written,0);self.assert_off()
    def test_reference_loss(self):
        self.devices['mfli'].fail='lock';e,r=self.setup_engine();self.assertEqual(self.run_engine(e)['outcome'],'error');self.assertEqual(r.rows_written,0);self.assert_off()
    def test_overload(self):
        self.devices['mfli'].fail='overload';e,r=self.setup_engine();self.assertEqual(self.run_engine(e)['outcome'],'error');self.assertEqual(r.rows_written,0);self.assert_off()
    def test_autorange_retry_bound(self):
        self.devices['mfli'].fail='range';e,r=self.setup_engine();self.assertEqual(self.run_engine(e)['outcome'],'error');self.assertEqual(r.rows_written,0);self.assert_off()
    def test_autorange_discards_old_read(self):
        self.devices['mfli'].fail='first_range';e,r=self.setup_engine();self.assertEqual(self.run_engine(e)['outcome'],'completed');self.assertEqual(self.devices['mfli'].reads,17);self.assertEqual(r.rows_written,8)
    def test_disk_failure_shuts_down_both(self):
        e,r=self.setup_engine();r.append_point=lambda row:(_ for _ in ()).throw(OSError('disk full'))
        self.assertEqual(self.run_engine(e)['outcome'],'error');self.assert_off()
    def test_rf_off_failure_still_zeroes_kepco(self):
        e,r=self.setup_engine();self.devices['smb'].rf_off=lambda:(_ for _ in ()).throw(RuntimeError('RF failed'))
        result=self.run_engine(e);self.assertEqual(result['outcome'],'error');self.assertEqual(self.bank['current'],0);self.assertEqual(len(result['shutdown_errors']),1)
    def test_configuration_failure_shuts_down(self):
        e,r=self.setup_engine();self.devices['smb'].configure=lambda *a:(_ for _ in ()).throw(RuntimeError('configure failed'))
        self.assertEqual(self.run_engine(e)['outcome'],'error');self.assertEqual(r.rows_written,0);self.assert_off()
    def test_wrapped_phase_average(self):
        e,r=self.setup_engine(replace(self.s,microwave_ghz=(1.,),modulation_hz=(17.3,)))
        def read(expected,**kw):
            self.devices['mfli'].reads+=1;angle=179 if self.devices['mfli'].reads%2 else -179
            return {'X':math.cos(math.radians(angle)),'Y':math.sin(math.radians(angle)),'R':1,'phase_deg':angle,'reference_valid':True,'reference_frequency_hz':expected,'input_overload':False}
        self.devices['mfli'].read_stfmr_mfli=read;self.run_engine(e)
        self.assertAlmostEqual(abs(e.cells[(0,0)]['phase_deg']),180)
    def test_partial_raw_cell_not_shown_as_complete(self):
        e,r=self.setup_engine();old=r.append_point
        def append(row):old(row);self.stop.set()
        r.append_point=append;self.assertEqual(self.run_engine(e)['outcome'],'stopped');self.assertEqual(r.rows_written,1);self.assertEqual(len(e.cells),0)
    def test_png_and_matrix_axes(self):
        s=replace(self.s,microwave_ghz=(1.,2.,3.));cells={(1,2):dict(microwave_index=2,modulation_index=1,microwave_frequency_ghz=3.,modulation_frequency_hz=317.3,x_v=4e-6,y_v=-3e-6,phase_deg=-36.87,field_g=123.,current_a=.1,samples=2)}
        m.export_grid(self.temp.name,s,cells)
        with open(Path(self.temp.name)/'X_V_matrix.csv',encoding='utf-8-sig') as f:rows=list(csv.reader(f))
        self.assertEqual(rows[0][1:],['1.0','2.0','3.0']);self.assertEqual(rows[1][1:],['','','']);self.assertEqual(float(rows[2][3]),4e-6)
        from PIL import Image
        for k in ('X','Y','Theta'):
            with Image.open(Path(self.temp.name)/f'{k}_heatmap.png') as im:self.assertEqual(im.size,(4200,2700));self.assertAlmostEqual(im.info['dpi'][0],900,places=1)

class InputTests(unittest.TestCase):
    def test_theta_colorbar_quarter_ticks(self):
        settings=m.GridSettings((1.,2.),(5.,397.))
        for key,labels in (('phase_deg',['-180','-90','0','90','180']),
                           ('x_v',['-2','0','2']),('y_v',['-3','0','3'])):
            shapes,(_,y0,x1,y1)=m.heatmap_scene(700,450,settings,
                {(0,0):{'phase_deg':45.,'x_v':2e-6,'y_v':3e-6}},key)
            ticks=[s for s in shapes if s[0]=='text' and s[1]==x1+28]
            self.assertEqual([s[3] for s in ticks],labels)
            for i,tick in enumerate(ticks):
                self.assertAlmostEqual(tick[2],y1-i*(y1-y0)/(len(ticks)-1))

    def test_prime_modulation(self):
        values=m.prime_axis('5','400')
        self.assertEqual(len(values),76);self.assertEqual(values[0],5);self.assertEqual(values[-1],397)
        self.assertTrue(all(all(int(v)%d for d in range(2,math.isqrt(int(v))+1)) for v in values))
        self.assertEqual(m.prime_axis('5','5'),(5.,));self.assertEqual(m.prime_axis('1','3'),(2.,3.))
        for a,b in [('8','10'),('400','5'),('5.1','400'),('nan','400'),('5','inf'),('','400'),('0','5'),('5','1000001')]:
            with self.subTest(a=a,b=b),self.assertRaises(ValueError):m.prime_axis(a,b)
        self.assertEqual(m.GridSettings((1.,),(5.,)).power_dbm,18.)
    def test_lockin_indicators(self):
        self.assertEqual(m.lockin_indicators({'reference_valid':True,'input_overload':False}), (('green','Locked'),('green','Range OK')))
        self.assertEqual(m.lockin_indicators({'reference_valid':False,'input_overload':True}), (('red','Not locked'),('red','Overload')))
        self.assertEqual(m.lockin_indicators({})[1][0],'gray')
        self.assertEqual(m.lockin_indicators({'auto_range':'SENS 1->2'})[1][0],'amber')
        self.assertEqual(m.lockin_indicators({'status':8,'sensitivity_index':13})[1][0],'green')
        self.assertEqual(m.lockin_indicators({'status':1,'sensitivity_index':13})[1][0],'red')
    def test_estimated_scan_time(self):
        settings=m.GridSettings(tuple(float(i) for i in range(1,18)),m.prime_axis(5,400))
        self.assertAlmostEqual(m.estimated_scan_seconds(settings),3876.6)
        self.assertEqual(m.format_duration(m.estimated_scan_seconds(settings)),'1 h 4 min 37 s')
        self.assertAlmostEqual(m.estimated_scan_seconds(replace(settings,meter='SR830')),15504.6)
        short=m.GridSettings((1.,),(2.,),samples_per_cell=1,point_delay_s=.1,timeconstant_s=.01)
        self.assertAlmostEqual(m.estimated_scan_seconds(short),3.3)
    def test_ranges(self):
        self.assertEqual(m.parse_axis('1:1.3:0.1'),(1,1.1,1.2,1.3));self.assertEqual(m.parse_axis('3:1:-1'),(3,2,1));self.assertEqual(m.parse_axis('1:2:0.6'),(1,1.6,2));self.assertEqual(m.parse_axis('17.3，317.3'),(17.3,317.3))
    def test_invalid(self):
        for v in ('','nan','inf','0','-1','1,1','1:2:0','2:1:1','1:2:.000000001'):
            with self.subTest(v=v),self.assertRaises(ValueError):m.parse_axis(v)
    def test_limits(self):
        for changes in ({'fixed_current_a':10.01},{'fixed_current_a':float('nan')},{'voltage_limit_v':21},{'power_dbm':26},{'samples_per_cell':0},{'point_delay_s':0}):
            with self.subTest(changes=changes),self.assertRaises(ValueError):m.GridSettings((1.,),(17.3,),**changes).validate()

if __name__=='__main__':unittest.main(verbosity=2)
