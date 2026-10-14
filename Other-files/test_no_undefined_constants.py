"""Every ALL_CAPS name a program reads is bound somewhere in it (2026-10-14).

Two branches merged cleanly while one still read a constant the other had
removed (STFMR_LOCKIN_SR830_SETTLE_TAUS): each branch's tests passed, and the
merged program would have raised NameError at the PPMS scan start.  This
reads the source only; nothing is imported or run."""
import ast
import builtins
import re
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PROGRAMS = ("Transport_all_NH_dev.py", "Newmark_motor_test.py", "Avtech_pulse_test.py")


def undefined_constants(source):
    tree = ast.parse(source)
    bound, used = set(dir(builtins)), {}
    for node in ast.walk(tree):
        if isinstance(node, ast.Name):
            if isinstance(node.ctx, ast.Load):
                used.setdefault(node.id, node.lineno)
            else:
                bound.add(node.id)
        elif isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            bound.add(node.name)
        elif isinstance(node, (ast.Import, ast.ImportFrom)):
            for alias in node.names:
                bound.add((alias.asname or alias.name).split(".")[0])
        elif isinstance(node, ast.arg):
            bound.add(node.arg)
        elif isinstance(node, ast.ExceptHandler) and node.name:
            bound.add(node.name)
        elif isinstance(node, (ast.Global, ast.Nonlocal)):
            bound.update(node.names)
    return sorted((line, name) for name, line in used.items()
                  if re.fullmatch(r"[A-Z][A-Z0-9_]+", name) and name not in bound)


class UndefinedConstantTests(unittest.TestCase):
    def test_every_constant_read_is_defined(self):
        for name in PROGRAMS:
            with self.subTest(program=name):
                source = (ROOT / name).read_text(encoding="utf-8")
                self.assertEqual(undefined_constants(source), [])

    def test_the_check_finds_a_removed_constant(self):
        self.assertEqual(undefined_constants("A = 1\nprint(A, GONE)\n"), [(2, "GONE")])


if __name__ == "__main__":
    unittest.main()
