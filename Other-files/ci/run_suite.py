"""Run the Other-files suite with each test's name and a hang watchdog.

If one test (its class set-up included) takes longer than HANG_S, every
thread's stack is written to stderr and the run stops, so a hang shows
where it is instead of running until the job is cancelled (2026-10-12)."""
import faulthandler
import sys
import unittest

HANG_S = 300


class WatchedResult(unittest.TextTestResult):
    def startTest(self, test):
        faulthandler.cancel_dump_traceback_later()
        faulthandler.dump_traceback_later(HANG_S, exit=True)
        super().startTest(test)


def main() -> int:
    # A crash (e.g. a segmentation fault inside Tk) prints the Python stack of
    # every thread, so the log shows which test and line it was (CI,
    # 2026-10-14: Linux ended with "Segmentation fault" and nothing else).
    faulthandler.enable()
    faulthandler.dump_traceback_later(HANG_S, exit=True)
    suite = unittest.defaultTestLoader.discover(".", pattern="test_*.py")
    result = unittest.TextTestRunner(verbosity=2, resultclass=WatchedResult).run(suite)
    faulthandler.cancel_dump_traceback_later()
    return 0 if result.wasSuccessful() else 1


if __name__ == "__main__":
    sys.exit(main())
