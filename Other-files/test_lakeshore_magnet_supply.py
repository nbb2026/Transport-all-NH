"""The Lake Shore 643 / 648 magnet supplies (user, 2026-10-21), offline.

FakeLS64x simulates the supply on a virtual clock: the output current moves
toward SETI at RATE in simulated time, OPST? has its ramp-done, compliance
and power-limit bits, ERST? its two error bytes, XPGM? and LIMIT? their
values, STOP halts the ramp.  It also records every command with its time,
so the manual's 50 ms rule can be checked.  No VISA session, no window."""
import math
import threading
import unittest
from unittest.mock import Mock, patch

from test_ppms_motor_debug import app


class Clock:
    """The controller's clock and sleep: time passes only when it sleeps."""

    def __init__(self):
        self.now = 1000.0

    def monotonic(self):
        return self.now

    def sleep(self, seconds):
        self.now += max(0.0, float(seconds))


class FakeLS64x:
    """A Lake Shore 643 / 648 as its GPIB session: query(), write(), close()."""

    def __init__(self, clock, model="643", *, current=0.0, setpoint=None, rate=50.0, limit=(135.1, 50.0),
                 xpgm=0, hardware=0, operational=0, identity=None, rseg=0, segments=(), opst_event=False):
        self.clock = clock
        self.model = model
        self.identity = identity or f"LSCI,MODEL{model},1234567,1.0/1.0"
        self.current = float(current)
        self.setpoint = float(current if setpoint is None else setpoint)
        self.rate = float(rate)
        self.limit = tuple(limit)
        self.xpgm = xpgm
        self.hardware = hardware
        self.operational = operational
        self.halted = False
        # Ramp segments: RSEG? (None: not answered) and RSEGS? n's
        # (current, rate) for n = 1..5 (missing ones read as 0, 0).
        self.rseg = rseg
        self.segments = list(segments)
        # OPST? as an event register (the manual's "reads and clears"): the
        # ramp done is reported once after the ramp ends, then cleared.
        self.opst_event = opst_event
        self.done_latched = False
        self.compliance = False
        self.power_limit = False
        # Called with (fake) after each step of simulated time: a test makes
        # an error or compliance appear mid-ramp with it.
        self.on_advance = None
        # A ramp that stalls: the current stops moving past this |I|.
        self.stall_at = None
        self.unanswered = set()
        self.commands = []
        self.times = []
        # The thread of every command, and an optional shared journal the
        # page tests order the supply's commands in: ("ls", command, thread).
        self.threads = []
        self.journal = None
        # SETI 0 halts the output where it is: a zero that never arrives.
        self.freeze_on_zero = False
        # STOP's write fails (a stalled bus): the ramp goes on.
        self.fail_stop = False
        self.closed = False
        self.t = clock.now
        self.timeout = None

    # ---- the simulation -----------------------------------------------------
    def _advance(self):
        now = self.clock.now
        dt, self.t = now - self.t, now
        if self.halted or dt <= 0 or self.xpgm != 0:
            return
        step = self.rate * dt
        gap = self.setpoint - self.current
        move = max(-step, min(step, gap))
        nxt = self.current + move
        if self.stall_at is not None and abs(nxt) > self.stall_at:
            nxt = math.copysign(self.stall_at, nxt)
        arrived = abs(nxt - self.setpoint) < 1e-9 and abs(self.current - self.setpoint) >= 1e-9
        self.current = nxt
        if arrived:
            self.done_latched = True
        if self.on_advance is not None:
            self.on_advance(self)

    def _opst(self):
        done = abs(self.current - self.setpoint) < 1e-9 and not self.halted
        if self.opst_event:
            done, self.done_latched = self.done_latched, False
        return (2 if done else 0) | (1 if self.compliance else 0) | (4 if self.power_limit else 0)

    def _io(self, text):
        self.times.append(self.clock.now)
        self.commands.append(text)
        self.threads.append(threading.get_ident())
        if self.journal is not None:
            self.journal.append(("ls", text, threading.get_ident()))
        self._advance()

    def query(self, command):
        self._io(command)
        if command in self.unanswered or command.split(" ")[0] in self.unanswered:
            raise TimeoutError(f"VI_ERROR_TMO {command}")
        replies = {
            "*IDN?": self.identity,
            "SETI?": f"{self.setpoint:+.3f}",
            "RDGI?": f"{self.current:+.4f}",
            "RDGV?": f"{self.current * 0.5:+.4f}",
            "RATE?": f"{self.rate:+.4f}",
            "LIMIT?": f"{self.limit[0]:+.3f},{self.limit[1]:+.4f}",
            "XPGM?": str(self.xpgm),
            "ERST?": f"{self.hardware:03d},{self.operational:03d}",
            "MAGWTR?": "2",
            "INTWTR?": "2",
        }
        if self.rseg is not None:
            replies["RSEG?"] = str(self.rseg)
        if command.startswith("RSEGS? "):
            index = int(command.split()[1])
            current, rate = self.segments[index - 1] if index <= len(self.segments) else (0.0, 0.0)
            replies[command] = f"{current:+.3f},{rate:+.4f}"
        if command == "OPST?":
            # Only when asked: as an event register, a read clears it.
            replies[command] = f"{self._opst():03d}"
        if command not in replies:
            raise TimeoutError(f"VI_ERROR_TMO {command}")
        return replies[command] + "\r"

    def write(self, command):
        self._io(command)
        name, _, value = command.partition(" ")
        if name == "RATE":
            self.rate = min(float(value), self.limit[1])
        elif name == "SETI":
            target = float(value)
            # "Setting value is limited by LIMIT" (manual).
            self.setpoint = math.copysign(min(abs(target), self.limit[0]), target)
            self.halted = self.freeze_on_zero and target == 0
            self.done_latched = False
        elif name == "STOP":
            if self.fail_stop:
                raise TimeoutError("VI_ERROR_TMO STOP")
            self.halted = True
        else:
            raise AssertionError(f"the program sent {command!r}")

    def close(self):
        self.closed = True

    # ---- checks --------------------------------------------------------------
    def sent(self):
        """The commands that are not queries."""
        return [command for command in self.commands if "?" not in command]

    def page_threads(self):
        """The threads of the commands after the connect."""
        return set(self.threads[getattr(self, "connect_commands", 0):])

    def gaps_kept(self):
        return all(b - a >= app.LAKESHORE_IO_GAP_S - 1e-9 for a, b in zip(self.times, self.times[1:]))


class Manager:
    def __init__(self, instrument):
        self.instrument = instrument
        self.opened = []

    def open_resource(self, resource):
        self.opened.append(resource)
        return self.instrument

    def list_resources(self):
        return ()


def connected_supply(model="643", owner="SHV", module=None, **fake_options):
    """A controller connected to a FakeLS64x, on a virtual clock: (controller,
    fake, clock).  ``module``: the program module the page under test was
    loaded as (some harnesses import it under its own name)."""
    module = module or app
    clock = Clock()
    fake = FakeLS64x(clock, model, **fake_options)
    controller = module.LakeShoreMagnetSupplyController(Mock(), model, owner_scope=owner)
    controller.clock, controller.sleep = clock.monotonic, clock.sleep
    with patch.object(module, "pyvisa", object()), \
            patch.object(module, "acquire_shared_visa_manager", return_value=Manager(fake)), \
            patch.object(module, "release_shared_visa_manager"):
        controller.connect(module.LAKESHORE_MAGNET_MODELS[model]["resource"])
    # Its commands from here on are the page's.
    fake.connect_commands = len(fake.commands)
    return controller, fake, clock


def release(controller):
    """Close a test controller's session and lease without a zero."""
    with patch.object(app, "release_shared_visa_manager"):
        controller._close_session()


class ConnectTests(unittest.TestCase):
    def tearDown(self):
        app._INSTRUMENT_LEASES.clear()

    def test_connect_checks_the_model_and_reads_without_sending_anything(self):
        for model in ("643", "648"):
            with self.subTest(model=model):
                controller, fake, _clock = connected_supply(model, setpoint=0.0)
                self.assertTrue(controller.connected)
                self.assertEqual(fake.sent(), [], "connect sends no setpoint or setting")
                self.assertEqual(fake.commands[:9], ["*IDN?", "SETI?", "RDGI?", "RDGV?", "RATE?", "LIMIT?",
                                                     "XPGM?", "OPST?", "ERST?"])
                self.assertTrue({"MAGWTR?", "INTWTR?", "RSEG?"} <= set(fake.commands))
                self.assertEqual(controller.program_max_a, 40.0 if model == "643" else 100.0)
                self.assertEqual(controller.effective_max_a, controller.program_max_a)
                self.assertEqual(controller.effective_max_rate_a_s, 10.0)
                self.assertEqual(controller.arm_problem, "")
                self.assertFalse(controller.output_on)
                self.assertTrue(fake.gaps_kept())
                release(controller)

    def test_the_other_model_or_another_instrument_is_refused_and_closed(self):
        for model, identity in (("643", "LSCI,MODEL648,1,1.0"), ("648", "LSCI,MODEL643,1,1.0"),
                                ("643", "KEITHLEY INSTRUMENTS,MODEL 6430,1,1")):
            with self.subTest(model=model, identity=identity):
                clock = Clock()
                fake = FakeLS64x(clock, model, identity=identity)
                controller = app.LakeShoreMagnetSupplyController(Mock(), model)
                controller.clock, controller.sleep = clock.monotonic, clock.sleep
                with patch.object(app, "pyvisa", object()), \
                        patch.object(app, "acquire_shared_visa_manager", return_value=Manager(fake)), \
                        patch.object(app, "release_shared_visa_manager"):
                    with self.assertRaises(RuntimeError):
                        controller.connect("GPIB0::12::INSTR")
                self.assertFalse(controller.connected)
                self.assertTrue(fake.closed)
                self.assertEqual(fake.commands, ["*IDN?"], "nothing but *IDN? reaches a wrong instrument")
                self.assertEqual(app._INSTRUMENT_LEASES, {})

    def test_external_programming_or_a_latched_error_refuse_to_arm(self):
        for options, words in (({"xpgm": 1}, "External"), ({"xpgm": 2}, "Sum"),
                               ({"hardware": 4}, "output over current"),
                               ({"operational": 32}, "magnet flow switch fault")):
            with self.subTest(options=options):
                controller, fake, _clock = connected_supply(**options)
                self.assertIn(words, controller.arm_problem)
                with self.assertRaisesRegex(RuntimeError, "not armed"):
                    controller.ramp_to(1.0, 1.0)
                self.assertEqual(fake.sent(), [], "no RATE or SETI while not armed")
                release(controller)
        # High line voltage is only a warning (manual: operation can continue).
        controller, fake, _clock = connected_supply(operational=16)
        self.assertEqual(controller.arm_problem, "")
        release(controller)

    def test_limits_refuse_before_sending(self):
        controller, fake, _clock = connected_supply("643", limit=(25.0, 2.0))
        self.assertEqual(controller.effective_max_a, 25.0)
        self.assertEqual(controller.effective_max_rate_a_s, 2.0)
        for current, rate, words in ((40.5, 1.0, "program's limit"), (30.0, 1.0, "LIMIT\\? maximum current"),
                                     (1.0, 3.0, "LIMIT\\? maximum rate"), (1.0, 10.5, "Rate A/s"),
                                     (1.0, 0.00001, "Rate A/s"), (math.nan, 1.0, "finite")):
            with self.subTest(current=current, rate=rate), self.assertRaisesRegex(ValueError, words):
                controller.ramp_to(current, rate)
        self.assertEqual(fake.sent(), [])
        release(controller)
        controller, fake, _clock = connected_supply("648")
        with self.assertRaisesRegex(ValueError, "100 A"):
            controller.ramp_to(-100.5, 1.0)
        self.assertEqual(fake.sent(), [])
        release(controller)


class RampTests(unittest.TestCase):
    def tearDown(self):
        app._INSTRUMENT_LEASES.clear()

    def test_set_current_ramps_at_the_rate_and_returns_at_the_setpoint(self):
        controller, fake, clock = connected_supply("643")
        began = clock.now
        reading = controller.ramp_to(5.0, 1.0)
        self.assertEqual(fake.sent(), ["RATE 1.0000", "SETI 5.000"])
        index = fake.commands.index("RATE 1.0000")
        # OPST? read and discarded just before SETI (it reads and clears, review 2026-10-21).
        self.assertEqual(fake.commands[index - 1:index + 5],
                         ["RATE?", "RATE 1.0000", "RATE?", "OPST?", "SETI 5.000", "SETI?"])
        self.assertAlmostEqual(reading["current_a"], 5.0, places=6)
        self.assertTrue(reading["at_setpoint"])
        self.assertGreaterEqual(clock.now - began, 5.0, "5 A at 1 A/s takes 5 s")
        self.assertLess(clock.now - began, 7.0)
        self.assertTrue(controller.output_on)
        self.assertTrue(fake.gaps_kept())
        # The rate unchanged: not written again.
        controller.ramp_to(4.0, 1.0)
        self.assertEqual(fake.sent()[-1], "SETI 4.000")
        self.assertEqual(fake.sent().count("RATE 1.0000"), 1)
        release(controller)

    def test_a_stop_mid_ramp_sends_stop_and_leaves_the_output_there(self):
        controller, fake, clock = connected_supply("643")
        stop = threading.Event()
        fake.on_advance = lambda f: stop.set() if abs(f.current) >= 2.0 else None
        with self.assertRaises(app.LakeShoreRampStopped) as caught:
            controller.ramp_to(8.0, 1.0, stop)
        self.assertEqual(fake.sent()[-1], "STOP")
        self.assertTrue(2.0 <= caught.exception.current_a < 3.5)
        self.assertTrue(controller.output_on)
        self.assertEqual(fake.sent().count("SETI 8.000"), 1, "no further setpoint after the stop")
        # request_stop from another thread does the same.
        fake.on_advance = lambda f: controller.request_stop() if abs(f.current) <= 1.0 else None
        with self.assertRaises(app.LakeShoreRampStopped):
            controller.ramp_to(0.5, 1.0)
        self.assertEqual(fake.sent()[-1], "STOP")
        release(controller)

    def test_an_error_or_compliance_mid_ramp_stops_and_reports(self):
        for name, cause in (("hardware", lambda f: setattr(f, "hardware", 8)),
                            ("operational", lambda f: setattr(f, "operational", 32)),
                            ("compliance", lambda f: setattr(f, "compliance", True)),
                            ("power limit", lambda f: setattr(f, "power_limit", True))):
            with self.subTest(name=name):
                controller, fake, _clock = connected_supply("648")
                fake.on_advance = lambda f, cause=cause: cause(f) if abs(f.current) >= 3.0 else None
                with self.assertRaises(app.LakeShoreRampError) as caught:
                    controller.ramp_to(10.0, 2.0)
                self.assertEqual(fake.sent()[-1], "STOP")
                self.assertIn("STOP sent", str(caught.exception))
                self.assertEqual(fake.sent().count("SETI 10.000"), 1)
                release(controller)

    def test_a_ramp_that_never_arrives_times_out_with_stop(self):
        controller, fake, clock = connected_supply("643")
        fake.stall_at = 1.5
        began = clock.now
        with self.assertRaisesRegex(app.LakeShoreRampError, "did not reach"):
            controller.ramp_to(3.0, 1.0)
        self.assertEqual(fake.sent()[-1], "STOP")
        self.assertAlmostEqual(clock.now - began, 3.0 * 1.5 + 15.0, delta=3.0)
        release(controller)

    def test_a_setpoint_that_reads_back_wrong_is_stopped(self):
        # The instrument's LIMIT? clamps SETI by itself; refused before that
        # here, so make LIMIT? report more than the clamp does.
        controller, fake, _clock = connected_supply("643")
        fake.limit = (2.0, 50.0)
        with self.assertRaisesRegex(app.LakeShoreRampError, "SETI\\? read back"):
            controller.ramp_to(5.0, 1.0)
        self.assertEqual(fake.sent()[-1], "STOP")
        release(controller)


class ZeroTests(unittest.TestCase):
    def tearDown(self):
        app._INSTRUMENT_LEASES.clear()

    def test_zero_ramps_to_0_at_the_rate_and_confirms_it(self):
        controller, fake, clock = connected_supply("648", current=6.0)
        self.assertTrue(controller.output_on, "connected at 6 A: not at zero")
        began = clock.now
        controller.zero(2.0)
        self.assertEqual(fake.sent(), ["RATE 2.0000", "SETI 0.000"])
        self.assertLessEqual(abs(fake.current), 0.01)
        self.assertGreaterEqual(clock.now - began, 3.0)
        self.assertFalse(controller.output_on)
        # Already at zero: nothing written.
        controller.zero(2.0)
        self.assertEqual(fake.sent(), ["RATE 2.0000", "SETI 0.000"])
        release(controller)

    def test_a_zero_is_not_stopped_by_a_stop_or_ended_by_an_error(self):
        controller, fake, _clock = connected_supply("643", current=3.0)
        controller.request_stop()
        fake.on_advance = lambda f: setattr(f, "compliance", True)
        controller.zero(1.0)
        self.assertNotIn("STOP", fake.sent())
        self.assertFalse(controller.output_on)
        self.assertTrue(controller.last_zero_notes)
        release(controller)

    def test_a_zero_that_does_not_arrive_raises_zero_error_and_stays_on(self):
        controller, fake, _clock = connected_supply("643", current=3.0)
        fake.xpgm = 1     # externally programmed: SETI 0 does not move it
        with self.assertRaisesRegex(app.LakeShoreZeroError, "zero not confirmed"):
            controller.zero(1.0)
        self.assertTrue(controller.output_on)
        release(controller)

    def test_disconnect_zeroes_then_closes_and_reports_a_failed_zero(self):
        controller, fake, _clock = connected_supply("643", current=2.0)
        with patch.object(app, "release_shared_visa_manager"):
            controller.disconnect()
        self.assertEqual(fake.sent(), ["RATE 1.0000", "SETI 0.000"])
        self.assertTrue(fake.closed)
        self.assertFalse(controller.connected)
        self.assertEqual(app._INSTRUMENT_LEASES, {})
        controller, fake, _clock = connected_supply("643", current=2.0)
        fake.xpgm = 1     # the current no longer follows SETI
        with patch.object(app, "release_shared_visa_manager"), \
                self.assertRaisesRegex(RuntimeError, "zero FAILED"):
            controller.disconnect()
        self.assertFalse(controller.connected)
        self.assertTrue(controller.output_on, "kept on for the interlocks")

    def test_two_pages_cannot_hold_the_same_supply(self):
        first, fake, _clock = connected_supply("643", owner="SHV")
        second = app.LakeShoreMagnetSupplyController(Mock(), "643", owner_scope="STFMR")
        with patch.object(app, "pyvisa", object()), \
                patch.object(app, "acquire_shared_visa_manager", return_value=Manager(fake)):
            with self.assertRaisesRegex(RuntimeError, "already connected by \"SHV / Lake Shore 643\""):
                second.connect("GPIB0::12")
        release(first)


class ReviewTests(unittest.TestCase):
    """The review of 2026-10-21: ramp segments, OPST? that reads and clears,
    a STOP that cannot be sent, the zero's rate and the close's bound."""

    def tearDown(self):
        app._INSTRUMENT_LEASES.clear()

    def test_ramp_segments_on_or_unanswered_with_an_active_segment_refuse_to_arm(self):
        words = "disable Ramp Segments on the front panel (the program uses one rate)"
        for options in ({"rseg": 1, "segments": [(20.0, 0.5)]}, {"rseg": None, "segments": [(0.0, 1.0), (5.0, 2.0)]}):
            with self.subTest(options=options):
                controller, fake, _clock = connected_supply(**options)
                self.assertIn(words, controller.arm_problem)
                with self.assertRaisesRegex(RuntimeError, "not armed.*Ramp Segments"):
                    controller.ramp_to(1.0, 1.0)
                self.assertEqual(fake.sent(), [], "no RATE, SETI or RSEG write")
                # A zero is still allowed (the way out).
                fake.current = fake.setpoint = 1.0
                controller.zero(1.0)
                self.assertEqual(fake.sent(), ["RATE 1.0000", "SETI 0.000"])
                release(controller)
        for options in ({"rseg": 1, "segments": [(0.0, 3.0)]}, {"rseg": 0, "segments": [(20.0, 0.5)]}):
            with self.subTest(options=options):
                controller, fake, _clock = connected_supply(**options)
                self.assertEqual(controller.arm_problem, "")
                controller.ramp_to(1.0, 1.0)
                release(controller)
        # RSEG? and RSEGS? both unanswered (no such feature): armed.
        clock = Clock()
        fake = FakeLS64x(clock, "643", rseg=None)
        fake.unanswered = {"RSEGS?"}
        controller = app.LakeShoreMagnetSupplyController(Mock(), "643")
        controller.clock, controller.sleep = clock.monotonic, clock.sleep
        with patch.object(app, "pyvisa", object()), \
                patch.object(app, "acquire_shared_visa_manager", return_value=Manager(fake)), \
                patch.object(app, "release_shared_visa_manager"):
            controller.connect("GPIB0::12::INSTR")
        self.assertEqual(controller.arm_problem, "")
        release(controller)

    def test_ramp_segments_switched_on_after_connect_refuse_the_next_setpoint(self):
        controller, fake, _clock = connected_supply(rseg=0, segments=[(10.0, 0.2)])
        controller.ramp_to(1.0, 1.0)
        fake.rseg = 1
        with self.assertRaisesRegex(RuntimeError, "Ramp Segments"):
            controller.ramp_to(2.0, 1.0)
        self.assertEqual(fake.sent()[-1], "SETI 1.000")
        release(controller)

    def test_an_event_style_opst_and_a_repeated_setpoint(self):
        controller, fake, clock = connected_supply(opst_event=True)
        controller.ramp_to(-1.0, 1.0)
        began = clock.now
        controller.ramp_to(-1.0, 1.0)
        self.assertEqual(fake.sent(), ["RATE 1.0000", "SETI -1.000"], "already there: nothing sent")
        self.assertLess(clock.now - began, 1.0, "no wait for a ramp done that never comes")
        # A ramp done left from before cannot count: read away before SETI.
        fake.done_latched = True
        controller.ramp_to(3.0, 1.0)
        index = fake.commands.index("SETI 3.000")
        self.assertEqual(fake.commands[index - 1], "OPST?")
        self.assertAlmostEqual(fake.current, 3.0, places=6)
        release(controller)

    def test_a_stop_that_cannot_be_sent_is_said(self):
        controller, fake, _clock = connected_supply()
        fake.fail_stop = True
        fake.on_advance = lambda f: controller.request_stop() if f.current >= 2.0 else None
        with self.assertRaisesRegex(app.LakeShoreRampStopped,
                                    "STOP could not be sent: .*the supply may still be ramping to \\+8 A"):
            controller.ramp_to(8.0, 1.0)
        # The request stays until cleared: the pages clear it before each
        # Apply or Start (review, 2026-10-21).
        with self.assertRaises(app.LakeShoreRampStopped):
            controller.ramp_to(9.0, 1.0)
        controller.clear_stop_request()
        fake.fail_stop = False
        fake.on_advance = lambda f: setattr(f, "hardware", 8) if f.current >= 8.5 else None
        with self.assertRaisesRegex(app.LakeShoreRampError, "STOP sent, output at"):
            controller.ramp_to(9.0, 1.0)
        release(controller)

    def test_the_zero_never_ramps_faster_than_rate_at_connect(self):
        controller, fake, clock = connected_supply(rate=0.5, current=2.0)
        began = clock.now
        controller.zero(2.0)
        self.assertEqual(fake.sent(), ["SETI 0.000"], "RATE? reads 0.5 already: not written")
        self.assertGreaterEqual(clock.now - began, 4.0, "2 A at 0.5 A/s")
        release(controller)

    def test_the_closing_zero_is_never_slower_than_10_ma_per_s_and_its_wait_is_bounded(self):
        controller, fake, clock = connected_supply(rate=0.001, current=0.2)
        logger = controller.logger
        with patch.object(app, "release_shared_visa_manager"):
            controller.disconnect(closing=True)
        self.assertEqual(fake.sent(), ["RATE 0.0100", "SETI 0.000"])
        logged = " ".join(str(call.args[0]) for call in logger.call_args_list)
        self.assertIn("ramping to 0 A at 0.01 A/s from +0.200 A, about 20 s (waited for at most 45 s)", logged)
        # A zero that does not arrive: reported after the bound, then closed.
        controller, fake, clock = connected_supply(rate=0.001, current=0.2)
        fake.freeze_on_zero = True
        began = clock.now
        with patch.object(app, "release_shared_visa_manager"), \
                self.assertRaisesRegex(RuntimeError, "zero FAILED"):
            controller.disconnect(closing=True)
        self.assertLess(clock.now - began, 0.2 / 0.01 * 1.5 + 15.0 + 2.0)
        self.assertFalse(controller.connected)


class HelperTests(unittest.TestCase):
    def test_tolerance_rate_box_and_ramp_time(self):
        # max(50 mA, 0.2 %): the 648 manual's ±20 mA ±0.05 % setting and
        # reading accuracy each (review, 2026-10-21; it was 10 mA).
        for model in ("643", "648"):
            self.assertEqual(app.lakeshore_current_tolerance_a(0.0, model), 0.05)
            self.assertAlmostEqual(app.lakeshore_current_tolerance_a(40.0, model), 0.08)
            self.assertAlmostEqual(app.lakeshore_current_tolerance_a(-100.0, model), 0.2)
        self.assertEqual(app.parse_lakeshore_rate("1"), 1.0)
        for text in ("0", "10.01", "abc", "nan", "0.00009"):
            with self.subTest(text=text), self.assertRaises(ValueError):
                app.parse_lakeshore_rate(text)
        self.assertEqual(app.lakeshore_ramp_seconds([0, 2, -2, 0], 2.0), 4.0)

    def test_live_line_fits_its_box(self):
        for current, setpoint in ((1.0, 1.0), (-9.999, -10.0), (99.99, 100.0), (-100.0, -100.0)):
            text = app.lakeshore_live_text(dict(current_a=current, setpoint_a=setpoint, voltage_v=-75.0))
            self.assertLessEqual(len(text), 32, text)
        for reading in ({}, dict(errors_blocking=["x"]), dict(compliance=True), dict(at_setpoint=True),
                        dict(ramping=True)):
            self.assertLessEqual(len(app.lakeshore_state_word(reading, "")), 13)

    def test_magnet_supply_keeps_the_kepco_calls(self):
        kepco = Mock(output_owned=True, cached_current_a=2.0)
        supply = app.MagnetSupply(kepco)
        supply.start(2.0, 5.0)
        supply.step(3.0)
        supply.zero()
        self.assertEqual(kepco.mock_calls[:3], [unittest.mock.call.set_output("CURR", 2.0, 5.0),
                                               unittest.mock.call.set_scan_current(3.0),
                                               unittest.mock.call.force_output_off()])
        self.assertEqual(supply.name, "KEPCO 4886")
        self.assertEqual(supply.programmed_current_a(), 2.0)
        self.assertEqual(supply.measured_current_a(), "")
        # The Lake Shore: ramps, and a Stop is the page's own exception.
        lakeshore = Mock(short_label="LS643", setpoint_a=1.0, current_a=0.99, effective_max_a=40.0)
        lakeshore.ramp_to.side_effect = app.LakeShoreRampStopped("stopped", 0.5)
        supply = app.MagnetSupply(kepco, lakeshore, use_lakeshore=True, rate_a_s=2.0,
                                  stopped=lambda error: app.PPMSScanCancelled(str(error)))
        with self.assertRaises(app.PPMSScanCancelled):
            supply.step(1.0)
        lakeshore.ramp_to.assert_called_once_with(1.0, 2.0, None, None)
        supply.zero()
        lakeshore.zero.assert_called_once_with(2.0)
        self.assertEqual((supply.name, supply.max_current_a(), supply.programmed_current_a(),
                          supply.measured_current_a()), ("LS643", 40.0, 1.0, 0.99))


if __name__ == "__main__":
    unittest.main()
