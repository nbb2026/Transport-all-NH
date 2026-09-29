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
    def test_kepco_uses_the_labview_trace_timeout(self):
        """Traces show 2-6 ms per write; 10 s was tried and only lengthened the hang."""
        import re
        from pathlib import Path
        text = Path(app.__file__).read_text(encoding="utf-8")
        block = text[text.index("class STFMRKepco4886Controller"):]
        block = block[:block.index("write_termination = None")]
        found = [int(v) for v in re.findall(r"instrument\.timeout = (\d+)", block)]
        self.assertEqual(found, [3000])


if __name__ == "__main__":
    unittest.main()


class FakeBit4886:
    """Records writes; can hold the handshake on chosen commands."""

    def __init__(self, stall=(), acts_on_stall=False):
        self.stall = set(stall)
        self.acts_on_stall = acts_on_stall
        self.writes = []
        self.clears = 0
        self.output_on = False

    def write_raw(self, payload):
        command = payload.decode("ascii")
        if command in self.stall:
            if self.acts_on_stall and command == "OUTP ON":
                self.output_on = True   # the card acted, then stalled the handshake
            raise FakeVisaTimeout()
        self.writes.append(command)
        if command == "OUTP ON":
            self.output_on = True
        if command == "OUTP OFF":
            self.output_on = False

    def clear(self):
        self.clears += 1


class KepcoWriteOnlyTests(unittest.TestCase):
    """The BIT 4886 is write-only and is never cleared, queried or resent to."""

    SEQUENCE = ["FUNC:MODE CURR", "CURR 0;VOLT 10", "VOLT:RANG:AUTO 1;CURR:RANG:AUTO 1",
                "OUTP ON", "CURR -3.000000"]

    def controller(self, fake):
        made = app.STFMRKepco4886Controller(lambda *_: None)
        made.instrument = fake
        return made

    def enable(self, made):
        return made.set_output("CURR", -3.0, 10.0)

    def test_normal_start_sends_the_proven_sequence_once(self):
        fake = FakeBit4886()
        self.enable(self.controller(fake))
        self.assertEqual(fake.writes, self.SEQUENCE)

    def test_a_stall_is_never_answered_with_a_device_clear_or_a_resend(self):
        for stalled in self.SEQUENCE:
            fake = FakeBit4886(stall={stalled})
            with self.assertRaises(BaseException):
                self.enable(self.controller(fake))
            self.assertEqual(fake.clears, 0, f"device clear sent after {stalled!r}")
            self.assertEqual(fake.writes.count("FUNC:MODE CURR"),
                             0 if stalled == "FUNC:MODE CURR" else 1,
                             f"sequence resent after {stalled!r}")

    def test_stall_before_output_on_leaves_nothing_to_switch_off(self):
        fake = FakeBit4886(stall={"CURR 0;VOLT 10"})
        made = self.controller(fake)
        with self.assertRaises(BaseException):
            self.enable(made)
        self.assertFalse(made.output_owned)
        self.assertNotIn("OUTP OFF", fake.writes)

    def test_stall_on_outp_on_still_switches_the_output_off(self):
        """The card may have acted on OUTP ON before stalling, so turn it off."""
        fake = FakeBit4886(stall={"OUTP ON"}, acts_on_stall=True)
        made = self.controller(fake)
        with self.assertRaises(BaseException):
            self.enable(made)
        self.assertEqual(fake.writes[-2:], ["CURR 0.000000", "OUTP OFF"])
        self.assertFalse(fake.output_on)
        self.assertFalse(made.output_owned)

    def test_stall_after_output_on_switches_the_output_off(self):
        fake = FakeBit4886(stall={"CURR -3.000000"})
        made = self.controller(fake)
        with self.assertRaises(BaseException):
            self.enable(made)
        self.assertEqual(fake.writes[-2:], ["CURR 0.000000", "OUTP OFF"])
        self.assertFalse(fake.output_on)

    def test_outp_off_is_sent_even_when_the_zero_setpoint_is_refused(self):
        fake = FakeBit4886()
        made = self.controller(fake)
        self.enable(made)
        fake.stall = {"CURR 0.000000"}
        with self.assertRaises(RuntimeError) as caught:
            made.safe_output_off()
        self.assertEqual(fake.writes[-1], "OUTP OFF")
        self.assertFalse(fake.output_on)
        self.assertIn("not confirmed", str(caught.exception))
        self.assertTrue(made.output_owned, "unconfirmed off must stay owned for a retry")

    def test_named_error_reaches_the_operator(self):
        fake = FakeBit4886(stall={"FUNC:MODE CURR"})
        made = self.controller(fake)
        with self.assertRaises(RuntimeError) as caught:
            with app.visa_operation("KEPCO 4886", "enabling the output at the first current"):
                self.enable(made)
        self.assertIn("KEPCO 4886", str(caught.exception))
        self.assertEqual(made.last_command, "FUNC:MODE CURR")


if __name__ == "__main__":
    unittest.main()
