"""Hardware-status overload monitoring, without connecting instruments."""
import ast
import csv
import sys
import tempfile
import types
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import Transport_all as app


class Daq:
    def __init__(self, flags):
        self.flags = flags
        self.reads = []

    def getSample(self, path):
        return dict(x=[1e-6], y=[2e-6], frequency=[317.3], flags=[3])

    def getInt(self, path):
        self.reads.append(path)
        if path.endswith('/status/flags/binary'):
            if self.flags is None:
                raise RuntimeError('node unavailable')
            return self.flags
        return 1

    def getDouble(self, path):
        return .03 if path.endswith('/range') else .01

    def setDouble(self, *args):
        pass


class OverloadTests(unittest.TestCase):
    def controller(self, flags, bridge=False):
        c = app.NonLocalACController(lambda *_: None)
        c.mfli_device = 'dev4435'
        c.mfli_config = {'harmonic': 1}
        if bridge:
            raw = dict(x=1e-6, y=2e-6, frequency=317.3, flags=3,
                       extref_locked=1, input_range_v=.03,
                       device_status_flags=flags)
            c.mfli_bridge = types.SimpleNamespace(request=lambda _: {'sample': raw})
        else:
            c.mfli_daq = Daq(flags)
        return c

    def test_direct_and_bridge_decode_input_bits_independently_of_lock(self):
        for bridge in (False, True):
            for flags, expected in ((0, False), (1, True), (2, True),
                                    (3, True), (128, False), (129, True),
                                    (32, False), (None, None)):
                with self.subTest(bridge=bridge, flags=flags):
                    sample = self.controller(flags, bridge).read_mfli(317.3)
                    self.assertIs(sample['input_overload'], expected)
                    self.assertTrue(sample['reference_valid'])
                    self.assertEqual(sample['device_status_flags'], flags)

    def test_no_guess_from_sample_flags_or_voltage(self):
        sample = self.controller(None, True).read_mfli(317.3)
        self.assertIsNone(sample['input_overload'])
        self.assertEqual(sample['lia_status'], 3)

    def test_bridge_read_branch_fetches_hardware_flags(self):
        # Execute the actual embedded Python 3.7 read branch with a fake DAQ.
        tree = ast.parse(app._MFLI_BRIDGE_SOURCE)
        branch = next(n for n in ast.walk(tree) if isinstance(n, ast.If)
                      and ast.unparse(n.test) == "command == 'read'")
        code = compile(ast.fix_missing_locations(ast.Module(body=branch.body,
                                                           type_ignores=[])),
                       '<bridge read>', 'exec')
        for flags in (0, 1, None):
            results = []
            scope = dict(daq=Daq(flags), device='dev4435', DEMOD=0, EXTREF=0,
                         scalar=lambda sample, key, **kw: sample[key][0],
                         input_state=lambda **kw: {'input_range_v': .03},
                         emit=results.append)
            exec(code, scope)
            self.assertEqual(results[0]['sample']['device_status_flags'], flags)

    def test_indicator_red_green_unknown(self):
        h = object.__new__(app.CombinedControlApp)
        values, colors = [], []
        h.stfmr_lockin_overload_var = types.SimpleNamespace(set=values.append)
        h.stfmr_lockin_overload_indicator = (
            types.SimpleNamespace(itemconfigure=lambda _, **kw: colors.append(kw['fill'])), 1)
        for state in (True, False, None):
            h._set_stfmr_lockin_overload_state(state)
        self.assertEqual(colors, ['#e53935', '#20b15a', '#9aa0a6'])
        self.assertEqual(values, ['MFLI Overload: overload', 'MFLI Overload: normal',
                                  'MFLI Overload: --'])

    def test_csv_preserves_unknown_as_blank_and_boolean_states(self):
        with tempfile.TemporaryDirectory() as tmp:
            rec = app.STFMRScanRecorder(Path(tmp), {})
            for flag in (1, 0, None):
                rec.append_point({'mfli_input_overload': None if flag is None else bool(flag),
                                  'mfli_device_status_flags': flag})
            with rec.csv_path.open(encoding='utf-8-sig', newline='') as stream:
                rows = list(csv.DictReader(stream))
            rec.close('test')
            self.assertEqual([r['mfli_input_overload'] for r in rows], ['True', 'False', ''])
            self.assertEqual([r['mfli_device_status_flags'] for r in rows], ['1', '0', ''])


if __name__ == '__main__':
    unittest.main()
