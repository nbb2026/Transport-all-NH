"""Avtech_pulse_test.py, the standalone Avtech test (user, 2026-10-13), on
its simulated generator and on stand-ins.  No GPIB, no window."""
import importlib.util
import io
import unittest
from contextlib import redirect_stdout
from pathlib import Path
from unittest import mock

SCRIPT = Path(__file__).resolve().parents[1] / "Avtech_pulse_test.py"
spec = importlib.util.spec_from_file_location("avtech_pulse_test", SCRIPT)
script = importlib.util.module_from_spec(spec)
spec.loader.exec_module(script)


class Recording(script.SimulatedAvtech):
    """The simulated -P generator, keeping every command it gets."""

    def __init__(self, identity=None, stuck_after_pulse=False):
        super().__init__()
        self.sent, self.identity, self.stuck_after_pulse = [], identity, stuck_after_pulse

    def write(self, command):
        self.sent.append(command)
        super().write(command)

    def query(self, command):
        self.sent.append(command)
        if command == "*IDN?" and self.identity:
            return self.identity
        if command == "output?" and self.stuck_after_pulse and self.pulses:
            return "1"
        return super().query(command)


def run(argv, generator):
    with mock.patch.object(script, "open_generator", return_value=(script.Avtech(generator, echo=lambda _t: None), None)), \
            redirect_stdout(io.StringIO()) as out:
        code = script.main(argv)
    return code, out.getvalue()


class ScriptTests(unittest.TestCase):
    def test_read_only_sends_queries_only(self):
        generator = Recording()
        code, _out = run([], generator)
        self.assertEqual(code, 0)
        self.assertTrue(generator.sent)
        self.assertTrue(all(command.endswith("?") for command in generator.sent), generator.sent)
        self.assertEqual(generator.pulses, [])

    def test_one_pulse_and_the_output_off(self):
        generator = Recording()
        code, out = run(["pulse", "--amplitude", "20", "--width", "10", "--yes"], generator)
        self.assertEqual(code, 0, out)
        self.assertEqual(generator.pulses, [(20.0, 1e-8)])
        self.assertEqual(generator.sent.count("trigger:source immediate"), 1)
        self.assertEqual((generator.output, generator.trigger), (0, "hold"))
        self.assertIn("Pulse fired: +20 V, 10 ns", out)

    def test_the_other_sign_on_a_p_unit_fires_nothing(self):
        generator = Recording()
        code, out = run(["pulse", "--amplitude", "-20", "--width", "10", "--yes"], generator)
        self.assertEqual(code, 1)
        self.assertIn("no pulse fired", out)
        self.assertEqual(generator.pulses, [])
        self.assertNotIn("output on", generator.sent)

    def test_without_yes_it_asks_and_no_means_nothing(self):
        generator = Recording()
        with mock.patch("builtins.input", return_value="no"):
            code, out = run(["pulse", "--amplitude", "20", "--width", "10"], generator)
        self.assertEqual((code, generator.pulses), (1, []))
        self.assertEqual(generator.sent, ["*IDN?"])

    def test_another_instrument_gets_only_the_identity_query(self):
        generator = Recording(identity="KEITHLEY INSTRUMENTS,MODEL 6221,1,A01")
        code, _out = run(["pulse", "--amplitude", "20", "--width", "10", "--yes"], generator)
        self.assertEqual((code, generator.sent), (1, ["*IDN?"]))

    def test_an_output_that_stays_on_is_exit_3(self):
        generator = Recording(stuck_after_pulse=True)
        code, out = run(["pulse", "--amplitude", "20", "--width", "10", "--yes"], generator)
        self.assertEqual(code, 3)
        self.assertIn("CHECK THE GENERATOR", out)

    def test_limits_are_checked_before_anything_is_opened(self):
        for argv in (["pulse", "--amplitude", "150", "--width", "10"], ["pulse", "--amplitude", "20", "--width", "0.5"],
                     ["pulse", "--amplitude", "20", "--width", "6000"], ["pulse", "--amplitude", "0", "--width", "10"]):
            with self.subTest(argv=argv), mock.patch.object(script, "open_generator") as opened, \
                    redirect_stdout(io.StringIO()), mock.patch("sys.stderr", io.StringIO()):
                with self.assertRaises(SystemExit) as caught:
                    script.main(argv)
                self.assertEqual(caught.exception.code, 2)
                opened.assert_not_called()


if __name__ == "__main__":
    unittest.main()
