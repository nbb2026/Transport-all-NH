"""Run the Other-files suite with each test's name and a hang watchdog,
each test module in a fresh Python process.

If one test (its class set-up included) takes longer than HANG_S, every
thread's stack is written to stderr and that module stops, so a hang shows
where it is instead of running until the job is cancelled (2026-10-12).

One process per module (CI, 2026-10-14): Tk keeps some state per display
for the whole process.  After one module had built and destroyed its own
copy of the program, a later module's message box crashed Tk on the Linux
CI (segmentation fault in focus_force).  The program itself has one window
for its whole life, so it never meets a window of a destroyed copy; the
tests no longer do either.  A crash also prints every thread's Python stack.
"""
import faulthandler
import os
import subprocess
import sys
import unittest
from pathlib import Path

HANG_S = 300
# A backstop for a whole module's process; the per-test watchdog normally
# stops a hang long before this.
MODULE_S = 1800


class WatchedResult(unittest.TextTestResult):
    def startTest(self, test):
        faulthandler.cancel_dump_traceback_later()
        faulthandler.dump_traceback_later(HANG_S, exit=True)
        super().startTest(test)


def run_module(name: str) -> int:
    """Run one test module in this process (the child)."""
    # Test modules import each other ("from test_ppms_motor_debug import
    # app"), as they could when the suite was discovered from this folder.
    sys.path.insert(0, os.getcwd())
    faulthandler.enable()
    faulthandler.dump_traceback_later(HANG_S, exit=True)
    suite = unittest.defaultTestLoader.loadTestsFromName(name)
    result = unittest.TextTestRunner(verbosity=2, resultclass=WatchedResult).run(suite)
    faulthandler.cancel_dump_traceback_later()
    return 0 if result.wasSuccessful() else 1


def main() -> int:
    if len(sys.argv) == 3 and sys.argv[1] == "--module":
        return run_module(sys.argv[2])
    # The same modules, in the same order, as discover(".", "test_*.py").
    modules = sorted(path.stem for path in Path(".").glob("test_*.py"))
    failed = []
    for name in modules:
        print(f"===== {name}", file=sys.stderr, flush=True)
        try:
            code = subprocess.run(
                [sys.executable, os.path.abspath(__file__), "--module", name],
                timeout=MODULE_S,
            ).returncode
        except subprocess.TimeoutExpired:
            code = f"no end after {MODULE_S} s"
        if code != 0:
            failed.append(f"{name} (exit {code})")
    print(f"\n{len(modules)} modules, {len(failed)} failed", file=sys.stderr)
    for entry in failed:
        print(f"FAILED: {entry}", file=sys.stderr)
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
