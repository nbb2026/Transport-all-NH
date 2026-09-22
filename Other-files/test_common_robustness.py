"""Offline shared infrastructure checks; no VISA or GUI connections."""
import unittest
from test_ppms_motor_debug import app


class LeaseTests(unittest.TestCase):
    def test_equivalent_gpib_spellings_share_lease(self):
        owner, other = object(), object()
        app.acquire_instrument_lease("GPIB::024::INSTR", owner, "test")
        try:
            for resource in ("GPIB0::24::INSTR", " gpib00::024::instr "):
                with self.assertRaises(RuntimeError):
                    app.acquire_instrument_lease(resource, other, "other")
            app.release_instrument_lease("GPIB0::24::INSTR", other)
            with self.assertRaises(RuntimeError):
                app.acquire_instrument_lease("GPIB0::24::INSTR", other, "other")
        finally:
            app.release_instrument_lease("GPIB0::24::INSTR", owner)
        app.acquire_instrument_lease("GPIB::24::INSTR", other, "other")
        app.release_instrument_lease("GPIB00::024::INSTR", other)

    def test_board_and_secondary_addresses_remain_distinct(self):
        keys = [app.instrument_lease_key(value) for value in
                ("GPIB0::8::INSTR", "GPIB1::8::INSTR", "GPIB0::8::1::INSTR", "GPIB0::8::2::INSTR")]
        self.assertEqual(len(set(keys)), 4)

    def test_other_resources_are_not_reinterpreted(self):
        for value in ("ASRL4::INSTR", "USB0::0X1234::1::ABC::INSTR", "MY_VISA_ALIAS"):
            self.assertEqual(app.instrument_lease_key(value), value)


if __name__ == "__main__":
    unittest.main()
