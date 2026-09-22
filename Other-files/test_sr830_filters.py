"""Guard all integrated SR830 setup paths without opening hardware."""
import ast
from pathlib import Path
import unittest


class OptionalFilters(unittest.TestCase):
    def test_every_line_filter_setup_disables_both_optional_filters(self):
        source = Path(__file__).resolve().parents[1] / 'Transport_all_NH_dev.py'
        tree = ast.parse(source.read_text(encoding='utf-8'))
        paths = 0
        for node in ast.walk(tree):
            if not isinstance(node, ast.FunctionDef):
                continue
            strings = {n.value for n in ast.walk(node)
                       if isinstance(n, ast.Constant) and isinstance(n.value, str)}
            if 'ILIN 0' in strings:
                paths += 1
                self.assertIn('SYNC 0', strings, node.name)
            self.assertFalse(strings.intersection({'ILIN 1', 'ILIN 2', 'ILIN 3', 'SYNC 1'}), node.name)
        self.assertEqual(paths, 3)  # STFMR, nonlocal, PPMS scan
        initializer = next(n for n in ast.walk(tree)
                           if isinstance(n, ast.FunctionDef) and n.name == '_initialize_ppms_sr830')
        pairs = [ast.literal_eval(n) for n in ast.walk(initializer)
                 if isinstance(n, ast.Tuple) and len(n.elts) == 2
                 and all(isinstance(v, ast.Constant) for v in n.elts)]
        for expected in [('ILIN', 0), ('SYNC', 0), ('OFLT', 10), ('OFSL', 1)]:
            self.assertIn(expected, pairs)


if __name__ == '__main__':
    unittest.main()
