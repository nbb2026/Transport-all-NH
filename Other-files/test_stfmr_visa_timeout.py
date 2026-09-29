"""A VISA timeout must name the instrument, and a dropped read must retry.

Three STFMR scans failed on 2026-09-29 with a bare VI_ERROR_TMO six seconds
in, before a single point was saved. The message named none of the four
instruments on the bus, so it could not be acted on.
"""
import unittest

from test_ppms_motor_debug import app


class FakeVisaTimeout(Exception):
    """Shaped like pyvisa's VisaIOError: a status code and the VI_ name."""

    def __init__(self):
        super().__init__("VI_ERROR_TMO (-1073807339): Timeout expired before operation completed.")
        self.error_code = app.VI_ERROR_TMO_CODE


class DetectionTests(unittest.TestCase):
    def test_timeout_recognised_by_code(self):
        self.assertTrue(app.is_visa_timeout(FakeVisaTimeout()))

    def test_timeout_recognised_by_text_alone(self):
        """Backends that raise a plain error still have to be caught."""
        self.assertTrue(app.is_visa_timeout(RuntimeError(
            "VI_ERROR_TMO (-1073807339): Timeout expired before operation completed.")))

    def test_other_errors_are_not_timeouts(self):
        for error in (RuntimeError("SR830 is not connected"),
                      ValueError("bad range"),
                      RuntimeError("VI_ERROR_NLISTENERS")):
            self.assertFalse(app.is_visa_timeout(error), error)


class NamingTests(unittest.TestCase):
    def test_timeout_is_reraised_naming_instrument_and_step(self):
        with self.assertRaises(RuntimeError) as caught:
            with app.visa_operation("KEPCO 4886", "enabling the output"):
                raise FakeVisaTimeout()
        message = str(caught.exception)
        self.assertIn("KEPCO 4886", message)
        self.assertIn("enabling the output", message)
        self.assertNotEqual(message, str(FakeVisaTimeout()))

    def test_original_error_is_kept_as_the_cause(self):
        with self.assertRaises(RuntimeError) as caught:
            with app.visa_operation("SMB100A"):
                raise FakeVisaTimeout()
        self.assertIsInstance(caught.exception.__cause__, FakeVisaTimeout)

    def test_non_timeout_errors_pass_through_untouched(self):
        with self.assertRaises(ValueError) as caught:
            with app.visa_operation("SR830", "setup"):
                raise ValueError("SR830 REF IN must be one of: Sine")
        self.assertEqual(str(caught.exception), "SR830 REF IN must be one of: Sine")

    def test_success_returns_normally(self):
        with app.visa_operation("SR830"):
            pass


class RetryTests(unittest.TestCase):
    def panel(self):
        made = app.CombinedControlApp.__new__(app.CombinedControlApp)
        made.logged = []
        made.thread_log = made.logged.append
        return made

    def test_read_recovers_after_one_dropped_transfer(self):
        panel = self.panel()
        calls = []

        def flaky():
            calls.append(1)
            if len(calls) < 2:
                raise FakeVisaTimeout()
            return {"field": 289.0}

        self.assertEqual(panel._stfmr_retry_read("Lake Shore 475", flaky), {"field": 289.0})
        self.assertEqual(len(calls), 2)
        self.assertTrue(any("timed out" in line for line in panel.logged))

    def test_persistent_timeout_fails_naming_the_instrument(self):
        panel = self.panel()
        calls = []

        def always():
            calls.append(1)
            raise FakeVisaTimeout()

        with self.assertRaises(RuntimeError) as caught:
            panel._stfmr_retry_read("SR830", always, attempts=3)
        self.assertEqual(len(calls), 3, "should stop after the attempt budget")
        self.assertIn("SR830", str(caught.exception))

    def test_a_real_fault_is_not_retried(self):
        """Only timeouts are transient; a wiring fault must surface at once."""
        panel = self.panel()
        calls = []

        def broken():
            calls.append(1)
            raise RuntimeError("SR830 is not connected")

        with self.assertRaises(RuntimeError):
            panel._stfmr_retry_read("SR830", broken)
        self.assertEqual(len(calls), 1)


class KepcoTimeoutTests(unittest.TestCase):
    def test_kepco_allows_time_for_relays_and_autorange(self):
        """OUTP ON with auto-ranging held the bus longer than 3 s."""
        import re
        from pathlib import Path
        text = Path(app.__file__).read_text(encoding="utf-8")
        block = text[text.index("class STFMRKepco4886Controller"):]
        block = block[:block.index("write_termination = None")]
        found = [int(v) for v in re.findall(r"instrument\.timeout = (\d+)", block)]
        self.assertEqual(found, [10000])


if __name__ == "__main__":
    unittest.main()
