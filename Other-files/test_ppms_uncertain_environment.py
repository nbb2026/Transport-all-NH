"""No hardware: uncertain acknowledgements remain single-send and observable."""
import time
import types
import unittest
from unittest.mock import Mock, patch
from test_ppms_motor_debug import app, Method, Type, Array


class UncertainEnvironment(unittest.TestCase):
    def test_never_tries_second_overload_after_invocation(self):
        for response in (1, 2, False):
            call = Mock(return_value=response)
            first = Method("SetField", [Type("System.Double")]*4, call)
            if response is False:
                first.ReturnType = Type("System.Boolean")
            second = Method("SetField", first.types, Mock(return_value=0))
            instrument = types.SimpleNamespace(GetType=lambda: types.SimpleNamespace(GetMethods=lambda: [first, second]))
            system = types.SimpleNamespace(Array=Array, Object=object,
                Convert=types.SimpleNamespace(ChangeType=lambda value, *args: value),
                Globalization=types.SimpleNamespace(CultureInfo=types.SimpleNamespace(InvariantCulture=None)))
            with patch.dict('sys.modules', System=system):
                with self.assertRaises(app.QDCommandUncertain if response == 1 else RuntimeError):
                    app.PPMSQDInstrumentReadbackController._invoke_typed_command(instrument, "SetField", (100, 10, 1, 1))
            call.assert_called_once()
            second.invoke.assert_not_called()

    def test_uncertain_temperature_and_field_follow_readback(self):
        for name, value_key, status_key, target in (("SetTemperature", "temperature_k", "temperature_status", 140),
                                                    ("SetField", "field_oe", "field_status", 100)):
            obj = app.PPMSQDInstrumentReadbackController.__new__(app.PPMSQDInstrumentReadbackController)
            obj.instrument = object()
            obj.logger = Mock()
            obj._invoke_typed_command = Mock(side_effect=app.QDCommandUncertain("return 1"))
            obj._send_environment_command(name, (target, 10, 1))
            data = {value_key: target+20, status_key: "Chasing"}
            obj._verify_environment_command(data)
            self.assertIn("confirming by readback", data["command_verification"])
            data = {value_key: target, status_key: "Stable"}
            obj._verify_environment_command(data)
            self.assertIn("readback now confirms", data["command_verification"])
            obj._invoke_typed_command.assert_called_once()
            self.assertIsNone(obj.pending_environment_command)

    def test_timeout_does_not_resend(self):
        obj = app.PPMSQDInstrumentReadbackController.__new__(app.PPMSQDInstrumentReadbackController)
        obj.pending_environment_command = ("SetTemperature", 140, time.monotonic()-1)
        data = dict(temperature_k=300, temperature_status="Stable")
        obj._verify_environment_command(data)
        self.assertIn("timed out", data["command_verification"])


if __name__ == '__main__':
    unittest.main()
