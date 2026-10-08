"""MS-fourState: write the magnetic states D, A, B and E in the PPMS and read
each one at zero field with the SR830 first harmonic.

One step, with the state's angle, write field and pulse:

  1. 6221 off (read back); the field is brought to 0 Oe if it is not there.
  2. The PPMS rotator turns to the state's angle, only at zero field.
  3. The write field is set (Driven) and allowed to settle.
  4. The 6221 sends the write pulse(s): 0 -> I for the pulse width.
  5. The field returns to 0 Oe (Driven).
  6. Read: the 6221 sources a sine of I_read (RMS) whose phase marker is the
     SR830's external reference, as on the PPMS page; the SR830 first
     harmonic X, Y and theta are averaged over the read time.
  7. 6221 off; the point is plotted and written to the CSV.

The Mode list runs it two ways: Cycle repeats a cycle of states (default
D-A-B-E-B-A, so D-A-B-E-B-A-D-A-...) a set number of times or until Stop;
Sequence writes and reads the states once, in the order typed (for example
A B A B A B to switch between two states).

Stop, an error or the end of the run turns the 6221 off and returns the field
to 0 Oe (Driven), confirmed by readback.  The rotator stays where it is: it
has no software stop, so keep MultiVu's Motion Control at hand.

Manual settings, as on the main program's PPMS page, set one thing at a time
between runs: Move turns the rotator (only at zero field, 6221 off first);
Set H and Zero H go to a field and end Persistent; Set I starts the read sine
and leaves it on; I off turns the 6221 off; Pulse sends one write pulse train
with the Write settings; Read shows the angle, field, temperature and 6221
output.  Stop during one of them does what it does in a run (6221 off, 0 Oe
Driven); an error turns the 6221 off and leaves the field and angle.

The instrument code is that of Transport_all_NH_dev.py, which must be in the
same folder.  Close that program's PPMS, 6221 and SR830 connections first:
two programs must never drive the same instruments.

    python MS-fourState.py
"""
import csv
import importlib.util
import json
import math
import queue
import sys
import threading
import time
import tkinter as tk
import types
from dataclasses import asdict, dataclass
from datetime import datetime
from pathlib import Path
from tkinter import messagebox, ttk

CORE_PROGRAM = "Transport_all_NH_dev.py"


def _load_core():
    path = Path(__file__).resolve().with_name(CORE_PROGRAM)
    if not path.is_file():
        raise SystemExit(f"{CORE_PROGRAM} must be in the same folder as {Path(__file__).name}")
    spec = importlib.util.spec_from_file_location("ms_four_state_core", path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


core = _load_core()

HERE = Path(__file__).resolve().parent
OE_PER_T = 10_000.0

# name, PPMS angle (deg), write field (Oe), pulse current (mA).  0.15 T is
# 1500 Oe.  D and E are at 360 deg rather than 0 (the same orientation): 90
# deg from A and B at 270 instead of 270 deg.  A or B written the other way
# (field and current both reversed) is entered in its row.
DEFAULT_STATES = (
    ("D", 360.0, 1500.0, 22.0),
    ("A", 270.0, -1500.0, 22.0),
    ("B", 270.0, 1500.0, 22.0),
    ("E", 360.0, -1500.0, 22.0),
)
# The manual settings' own fields; decel, field rate, frequency and the pulse
# width, count and compliance are shared with the Write and Read boxes.
MANUAL_DEFAULTS = {"angle": "360", "field": "0", "current_ma": "0.5", "pulse_ma": "22"}
DEFAULT_SEQUENCE = "D A B E"
DEFAULT_CYCLE = "D A B E B A"
APP_TITLE = "MS-fourState"
# The window opens at this size where the screen allows (the lab PC's
# 2560 x 1440 does), else smaller, so the plot gets the room; minimum 1100 x 760.
WINDOW_SIZE = (1720, 1240)
WINDOW_MINIMUM = (1100, 760)
MODES = {"cycle": "Cycle", "sequence": "Sequence"}  # The Mode list, first is the default.
STATE_COLORS = {"D": "#000000", "A": "#c62828", "B": "#1565c0", "E": "#2e7d32"}
DEFAULTS = {
    "pulse_width_us": "30",
    "pulse_count": "1",
    "compliance_v": "15",
    "field_rate": f"{core.PPMS_FIELD_RATE_DEFAULT_OE_S:g}",
    "approach": "No overshoot",
    "field_settle_s": "2",
    "after_pulse_s": "1",
    "decel": "14",
    "read_current_ma": "0.5",
    "read_frequency_hz": f"{core.PPMS_TRANSPORT_FREQUENCY_HZ:g}",
    "read_time_s": "3",
    "read_settle_s": "1",
    "repeats": "1",
}
APPROACHES = {"Linear": 0, "No overshoot": 1}
CONNECTIONS = {"ppms": "PPMS (QDInstrument)", "6221": "Keithley 6221", "sr830": "SR830"}
# The PPMS status box, as the PPMS page's live readings.  Read only.  Polled
# while idle; during a run or a manual action it shows that action's own
# readings instead, so nothing extra is asked of the instruments.
STATUS_POLL_S = 2.0
CRYOGEN_POLL_S = 30.0      # Chamber pressure and LEVEL? (helium), as the PPMS page.
STATUS_EMIT_S = 1.0        # At most one reading a second from a run to the box.
LAMP_COLORS = {"off": "#9aa0a6", "on": "#2e9d4f", "failed": "#d32f2f"}
ANGLE_TOLERANCE_DEG = 0.06  # As PPMSMotorDebug.move_and_verify.
ROTATOR_AT_REST = ("AtTarget", "AtIndexSwitch")
# Field statuses that end a manual Set H at once, rather than after its margin.
FIELD_FAULTS = ("magnetfailure", "currenterror")
MANUAL_READING_INTERVAL_S = 2.0  # The Now line while a manual Set H waits.
LARGE_ROTATION_DEG = 30.0   # As the PPMS page's manual Move.
FIELD_TIMEOUT_MARGIN_S = 600.0
SHUTDOWN_FIELD_MARGIN_S = 120.0
PULSE_DONE_MARGIN_S = 0.2
READ_SAMPLE_INTERVAL_S = 0.1
READ_OVERLOAD_RETRIES = 3
READ_CURRENT_MAX_RMS_A = 5e-3  # A typing slip such as 70 for 0.7 mA is refused.
LOCKIN_MAX_TAU_S = 10.0        # 5 time constants are waited before each read.
AUTORANGE_MAX_CHANGES = 24
AUTORANGE_LOW_FRACTION = 0.10
# The PPMS page's SR830 baseline (_initialize_ppms_sr830), first harmonic.
SR830_BASELINE = (
    ("FMOD", 0), ("RSLP", core.PPMS_SR830_REFERENCE_SLOPE), ("HARM", 1),
    ("ISRC", 1), ("IGND", 0), ("ICPL", 0), ("ILIN", 0), ("SYNC", 0),
    ("SENS", 9), ("OFLT", 10), ("OFSL", 1), ("RMOD", 1), ("PHAS", 0),
)
SR830_RECORDED = ("HARM", "FMOD", "RSLP", "ISRC", "IGND", "ICPL", "ILIN", "SYNC",
                  "SENS", "OFLT", "OFSL", "RMOD", "PHAS")
CSV_COLUMNS = (
    "step", "cycle", "state", "time_s", "timestamp",
    "angle_set_deg", "angle_read_deg",
    "write_field_set_oe", "write_field_read_oe", "write_field_status",
    "pulse_current_ma", "pulse_width_us", "pulse_count", "compliance_v", "pulse_in_compliance",
    "read_field_oe", "read_field_status", "temperature_k",
    "read_current_ma_rms", "read_frequency_hz",
    "x_v", "y_v", "r_v", "theta_deg", "x_std_v", "y_std_v", "samples",
    "sensitivity_code", "full_scale_v", "rxy_ohm",
)


def base_state(name):
    return str(name).rstrip("'")


@dataclass(frozen=True)
class Settings:
    states: dict
    sequence: tuple
    repeats: object  # int, or None for "until Stop"
    pulse_width_s: float
    pulse_count: int
    compliance_v: float
    field_rate_oe_s: float
    approach_code: int
    field_settle_s: float
    after_pulse_s: float
    decel: int
    read_current_rms_a: float
    read_frequency_hz: float
    read_time_s: float
    read_settle_s: float
    autorange: bool

    @property
    def total_steps(self):
        return None if self.repeats is None else self.repeats * len(self.sequence)

    def iter_steps(self):
        """(cycle, state) in order; endless when repeats is None."""
        cycle = 0
        while self.repeats is None or cycle < self.repeats:
            cycle += 1
            for name in self.sequence:
                yield cycle, name

    def large_rotations(self):
        """Turns over LARGE_ROTATION_DEG per cycle, and between cycles."""
        angles = [self.states[name][0] for name in self.sequence]
        within = sum(abs(b - a) > LARGE_ROTATION_DEG for a, b in zip(angles, angles[1:]))
        wrap = abs(angles[0] - angles[-1]) > LARGE_ROTATION_DEG
        return within, wrap


def parse_sequence(text, names):
    """'D A B E', 'd,a,b,e' or 'D-A-B-E' -> ('D', 'A', 'B', 'E')."""
    lookup = {name.upper(): name for name in names}
    sequence = []
    for token in str(text).replace(",", " ").replace(";", " ").replace("-", " ").split():
        name = lookup.get(token.upper())
        if name is None:
            raise ValueError(f"Unknown state {token!r} in the sequence; use {', '.join(names)}")
        sequence.append(name)
    if not sequence:
        raise ValueError("The sequence is empty; enter states such as D A B E")
    return tuple(sequence)


def _number(text, label):
    try:
        value = float(str(text).strip())
    except ValueError:
        raise ValueError(f"{label}: not a number ({text!r})") from None
    if not math.isfinite(value):
        raise ValueError(f"{label} must be finite")
    return value


def _whole(text, label, low, high):
    value = _number(text, label)
    if not value.is_integer() or not low <= value <= high:
        raise ValueError(f"{label} must be a whole number from {low} to {high}")
    return int(value)


def _shared_settings(values):
    """The Write and Read boxes' values, checked: Settings keyword arguments."""
    width_s = _number(values["pulse_width_us"], "Pulse width (us)") / 1e6
    count = _whole(values["pulse_count"], "Pulses per write", 1, 1000)
    compliance = _number(values["compliance_v"], "Compliance (V)")
    rate = _number(values["field_rate"], "Field rate (Oe/s)")
    if values["approach"] not in APPROACHES:
        raise ValueError("Choose a field approach")
    decel = _whole(values["decel"], "Rotator decel", 0, 14)
    # Checked here too, not only per state: a manual action has no states,
    # and Stop's return to 0 Oe needs a rate the PPMS accepts.
    core.validate_ppms_field_command(0.0, rate)
    core.Keithley6221Controller._validate(0.0, width_s, compliance, count)
    read_rms_a = _number(values["read_current_ma"], "Read current (mA RMS)") / 1000.0
    if not 0 < read_rms_a <= READ_CURRENT_MAX_RMS_A:
        raise ValueError(f"Read current must be above 0 and at most {READ_CURRENT_MAX_RMS_A * 1e3:g} mA RMS")
    frequency = _number(values["read_frequency_hz"], "Read frequency (Hz)")
    if not 1.0 <= frequency <= 100_000.0:
        raise ValueError("Read frequency must be 1-100000 Hz")
    settle = {}
    for key, label in (("field_settle_s", "Field settle (s)"), ("after_pulse_s", "Wait after pulse (s)"),
                       ("read_settle_s", "Extra read settle (s)")):
        settle[key] = _number(values[key], label)
        if not 0 <= settle[key] <= 3600:
            raise ValueError(f"{label} must be 0-3600 s")
    read_time = _number(values["read_time_s"], "Read time (s)")
    if not 0.5 <= read_time <= 3600:
        raise ValueError("Read time must be 0.5-3600 s")
    return dict(
        pulse_width_s=width_s, pulse_count=count, compliance_v=compliance,
        field_rate_oe_s=rate, approach_code=APPROACHES[values["approach"]],
        field_settle_s=settle["field_settle_s"], after_pulse_s=settle["after_pulse_s"],
        decel=decel, read_current_rms_a=read_rms_a, read_frequency_hz=frequency,
        read_time_s=read_time, read_settle_s=settle["read_settle_s"],
    )


def build_settings(states, sequence_text, values, autorange=True, until_stop=False):
    """Check every value the run will send, before anything is sent.

    ``states``: {name: (angle text, field Oe text, pulse mA text)};
    ``values``: the DEFAULTS keys as entered.
    """
    shared = _shared_settings(values)
    parsed = {}
    for name, (angle_text, field_text, pulse_text) in states.items():
        angle = _number(angle_text, f"{name} angle")
        field = _number(field_text, f"{name} field")
        pulse_ma = _number(pulse_text, f"{name} pulse current")
        core.PPMSMotorDebug.validate_move(angle, shared["decel"], angle)
        core.validate_ppms_field_command(field, shared["field_rate_oe_s"])
        core.Keithley6221Controller._validate(
            pulse_ma / 1000.0, shared["pulse_width_s"], shared["compliance_v"], shared["pulse_count"])
        parsed[name] = (angle, field, pulse_ma)
    sequence = parse_sequence(sequence_text, tuple(parsed))
    repeats = None if until_stop else _whole(values["repeats"], "Repeats", 1, 100_000)
    return Settings(states=parsed, sequence=sequence, repeats=repeats, autorange=bool(autorange), **shared)


def manual_settings(values, current_ma_text=None):
    """The shared settings for one manual action: no states, nothing to cycle.
    ``current_ma_text`` replaces the read current (the manual I mA RMS)."""
    if current_ma_text is not None:
        values = dict(values, read_current_ma=current_ma_text)
    return Settings(states={}, sequence=(), repeats=1, autorange=False, **_shared_settings(values))


class ManualDeclined(Exception):
    """A manual action the user declined at its confirmation; nothing sent."""


def sr830_query(lockin, command, count):
    """As the PPMS page: three attempts, clearing the bus after a bad reply."""
    last = ""
    error = None
    with lockin.lock:
        if lockin.instrument is None:
            raise RuntimeError("SR830 is not connected")
        for attempt in range(3):
            try:
                last = lockin.instrument.query(command).strip()
                values = tuple(float(part) for part in last.split(","))
                if len(values) != count or not all(math.isfinite(v) for v in values):
                    raise ValueError(f"expected {count} finite values")
                return values
            except Exception as caught:
                error = caught
                if attempt < 2:
                    try:
                        lockin.instrument.clear()
                    except Exception:
                        pass
                    time.sleep(0.08)
    raise RuntimeError(f"SR830 {command!r} kept returning bad replies: {last!r}") from error


def sr830_write(lockin, command):
    """Write, then check *ESR? for a rejected command, as the PPMS page."""
    with lockin.lock:
        if lockin.instrument is None:
            raise RuntimeError("SR830 is not connected")
        lockin.instrument.write(command)
        time.sleep(0.03)
        status = int(round(sr830_query(lockin, "*ESR?", 1)[0]))
        if status & 0x30:
            raise RuntimeError(f"SR830 rejected {command!r}; *ESR?={status}")


def initialize_lockin(lockin):
    """The PPMS page's SR830 baseline at connection, first harmonic."""
    with lockin.lock:
        lockin.instrument.write("*CLS")  # A disconnect can leave *ESR? latched.
        for command, value in SR830_BASELINE:
            sr830_write(lockin, f"{command} {value}")
        for command in ("DDEF 1,1,0", "DDEF 2,1,0"):
            sr830_write(lockin, command)
        for command, expected in SR830_BASELINE:
            actual = sr830_query(lockin, f"{command}?", 1)[0]
            if actual != expected:
                raise RuntimeError(f"SR830 setup readback mismatch: {command}={actual:g}, expected {expected}")


def connect_lockin(lockin, resource):
    """Connect, then apply the baseline; disconnect again if it fails."""
    identity = lockin.connect(resource)
    try:
        initialize_lockin(lockin)
    except BaseException:
        try:
            lockin.disconnect()
        except Exception:
            pass
        raise
    return identity


def source_query(source, command):
    with source.lock:
        if source.instrument is None:
            raise RuntimeError("Keithley 6221 is not connected")
        return float(source.instrument.query(command).strip())


def tau_seconds(code):
    if not 0 <= code <= 19:
        raise RuntimeError("SR830 time-constant readback invalid")
    return (1 if code % 2 == 0 else 3) * 10 ** (code // 2 - 5)


class FourStateRun:
    """The measurement itself, without Tk: runs in a worker thread."""

    def __init__(self, ppms, source, lockin, settings, *, emit, stop, motor=None,
                 clock=time.monotonic, sleep=time.sleep):
        self.ppms = ppms
        self.source = source
        self.lockin = lockin
        self.settings = settings
        self.emit = emit
        self.stop = stop
        self.motor = motor if motor is not None else core.PPMSMotorDebug(ppms.active)
        self.clock = clock
        self.sleep = sleep
        self.commanded_field = None  # Last field this run sent, sent or not confirmed.
        self.compliance_latch = True  # STAT:MEAS:EVEN? answered so far.
        self.shutdown_problems = []
        self.lockin_settings = {}
        self.environment_shown = None

    # -- helpers ---------------------------------------------------------
    def check(self):
        if self.stop.is_set():
            raise core.PPMSScanCancelled("Stopped")

    def wait(self, seconds):
        if self.stop.wait(max(0.0, float(seconds))):
            raise core.PPMSScanCancelled("Stopped")

    def source_off(self):
        """Output off, confirmed by OUTP? = 0."""
        self.source.output_off()
        if int(round(source_query(self.source, "OUTP?"))) != 0:
            raise RuntimeError("6221 output did not read back off")

    def environment(self):
        """The PPMS reading; at most one a second goes to the status box."""
        data = self.ppms.read_environment()
        if self.environment_shown is None or self.clock() - self.environment_shown >= STATUS_EMIT_S:
            self.emit("ppms-reading", dict(data))
            self.environment_shown = self.clock()
        return data

    def lockin_settle_s(self):
        return max(0.1, 5.0 * tau_seconds(int(sr830_query(self.lockin, "OFLT?", 1)[0])))

    # -- field and angle ------------------------------------------------
    def command_field(self, target, mode=1):
        """``mode`` 1 Driven (runs), 0 Persistent (the manual Set H)."""
        self.check()
        self.commanded_field = target  # Before sending: it may take effect even if the call fails.
        s = self.settings
        return self.ppms.set_field(target, s.field_rate_oe_s, s.approach_code, mode)

    def field_to(self, target):
        """Driven setpoint; returns the reading once settled for the settle time."""
        s = self.settings
        result = self.command_field(target)
        try:
            distance = abs(target - float(result["field_oe"]))
        except (KeyError, TypeError, ValueError):
            distance = abs(target)
        deadline = self.clock() + distance / s.field_rate_oe_s + FIELD_TIMEOUT_MARGIN_S
        settled_since = None
        while True:
            self.check()
            data = self.environment()
            if core.CombinedControlApp._ppms_field_is_ready(data, target, driven=True):
                settled_since = self.clock() if settled_since is None else settled_since
                if self.clock() - settled_since >= s.field_settle_s:
                    return data
            else:
                settled_since = None
            if self.clock() >= deadline:
                raise RuntimeError(
                    f"Field did not settle at {target:+g} Oe: {data.get('field_oe')} Oe / {data.get('field_status')}")
            self.wait(0.5)

    def ensure_zero_field(self):
        if self.commanded_field in (None, 0.0):
            data = self.environment()
            if core.CombinedControlApp._ppms_field_is_ready(data, 0.0):
                return data  # Already at zero (Driven or Persistent): nothing sent.
        return self.field_to(0.0)

    def rotate_to(self, angle):
        before = self.motor.read()
        if (abs(before["angle_deg"] - angle) <= ANGLE_TOLERANCE_DEG
                and before["status"] in ("AtTarget", "AtIndexSwitch")):
            return before["angle_deg"]
        large = abs(angle - before["angle_deg"]) > LARGE_ROTATION_DEG
        self.check()
        result = self.motor.move_and_verify(
            angle, self.settings.decel, cancelled=self.stop.is_set,
            allow_large=large, timeout=900.0 if large else 90.0)
        self.emit("ppms-reading", {"angle_deg": result["angle_deg"], "angle_status": result.get("status", "AtTarget")})
        return result["angle_deg"]

    # -- 6221 -----------------------------------------------------------
    def compliance_events(self):
        """Compliance latched since the last call (STAT:MEAS:EVEN? bit 3).

        'unknown' if the 6221 does not answer; then it is not asked again.
        """
        if not self.compliance_latch:
            return "unknown"
        try:
            return bool(int(round(source_query(self.source, "STAT:MEAS:EVEN?"))) & 8)
        except Exception as error:
            self.compliance_latch = False
            self.emit("log", f"6221 latched compliance not readable ({error}); pulses are not monitored")
            with self.source.lock:
                for _ in range(10):  # Empty the error queue, or the next checked write fails.
                    try:
                        reply = self.source.instrument.query("SYST:ERR?").strip()
                    except Exception:
                        break
                    if reply.split(",", 1)[0].strip() in ("0", "+0"):
                        break
            return "unknown"

    def pulse(self, pulse_ma):
        """The MOKE-6221 page's unipolar pulse (Keithley6221Controller.set_dc_pulse:
        a 50 % square of period 2 x width with offset I/2 and a finite cycle
        count; ARM, OUTP ON and INIT in that order, each checked), with every
        setting read back before ARM and the latched compliance bit read
        around the train.  What the 6221 outputs between ARM and INIT has
        not been measured: check the pulse with an oscilloscope first."""
        s = self.settings
        if pulse_ma == 0:
            self.source_off()
            return {"in_compliance": "no pulse"}
        current_a, width_s, compliance_v, count = core.Keithley6221Controller._validate(
            pulse_ma / 1000.0, s.pulse_width_s, s.compliance_v, s.pulse_count)
        frequency = 1.0 / (2.0 * width_s)
        amplitude = abs(current_a) / 2.0
        offset = current_a / 2.0
        source = self.source
        self.check()
        with source.lock:
            if source.instrument is None:
                raise RuntimeError("Keithley 6221 is not connected")
            for command in (
                "SOUR:WAVE:ABOR",
                f"CURR:COMP {compliance_v:.12g}",
                "SOUR:WAVE:FUNC SQU",
                "SOUR:WAVE:DCYC 50",
                f"SOUR:WAVE:AMPL {amplitude:.12e}",
                f"SOUR:WAVE:OFFS {offset:.12e}",
                f"SOUR:WAVE:FREQ {frequency:.12g}",
                "SOUR:WAVE:RANG BEST",
                "SOUR:WAVE:PMAR:STAT OFF",
                "SOUR:WAVE:EXTR OFF",
                f"SOUR:WAVE:DUR:CYCL {count}",
            ):
                source._write_checked(command)
            for label, command, expected, rel, absolute in (
                    ("amplitude", "SOUR:WAVE:AMPL?", amplitude, 5e-4, 1e-12),
                    ("offset", "SOUR:WAVE:OFFS?", offset, 5e-4, 1e-12),
                    ("frequency", "SOUR:WAVE:FREQ?", frequency, 5e-4, 1e-6),
                    ("compliance", "CURR:COMP?", compliance_v, 5e-4, 1e-6),
                    ("pulse count", "SOUR:WAVE:DUR:CYCL?", count, 0, 0.5)):
                actual = source_query(source, command)
                if not math.isclose(actual, expected, rel_tol=rel, abs_tol=absolute):
                    raise RuntimeError(f"6221 pulse {label} readback mismatch: requested {expected:g}, read {actual:g}")
            self.compliance_events()  # Clears the latch.
            self.check()
            for command in ("SOUR:WAVE:ARM", "OUTP ON", "SOUR:WAVE:INIT"):
                source._write_checked(command)
        self.wait(2.0 * width_s * count + PULSE_DONE_MARGIN_S)  # On Stop, shutdown aborts the train.
        still_on = int(round(source_query(source, "OUTP?"))) == 1
        in_compliance = self.compliance_events()
        self.source_off()
        if still_on:
            raise RuntimeError("6221 output was still on after the pulse train; it has been turned off")
        if in_compliance is True:
            self.emit("log", f"6221 reached compliance ({compliance_v:g} V) during the {pulse_ma:+g} mA pulse")
        return {"in_compliance": in_compliance}

    def start_read_current(self):
        """The PPMS page's sine: phase marker on, as the SR830 reference."""
        s = self.settings
        peak = math.sqrt(2.0) * s.read_current_rms_a
        source = self.source
        self.check()
        with source.lock:
            if source.instrument is None:
                raise RuntimeError("Keithley 6221 is not connected")
            for command in (
                "SOUR:WAVE:ABOR",
                f"CURR:COMP {s.compliance_v:.12g}",
                "SOUR:WAVE:FUNC SIN",
                f"SOUR:WAVE:FREQ {s.read_frequency_hz:.12g}",
                f"SOUR:WAVE:AMPL {peak:.12e}",
                "SOUR:WAVE:OFFS 0",
                "SOUR:WAVE:RANG BEST",
                "SOUR:WAVE:EXTR OFF",
                "SOUR:WAVE:DUR:CYCL INF",
                f"SOUR:WAVE:PMAR {core.NONLOCAL_MARKER_PHASE_DEG:g}",
                f"SOUR:WAVE:PMAR:OLIN {core.NONLOCAL_MARKER_LINE}",
                "SOUR:WAVE:PMAR:STAT ON",
                "OUTP OFF",
            ):
                source._write_checked(command)
            for label, command, expected, rel, absolute in (
                    ("compliance", "CURR:COMP?", s.compliance_v, 5e-4, 1e-6),
                    ("frequency", "SOUR:WAVE:FREQ?", s.read_frequency_hz, 5e-3, 0.1),
                    ("amplitude", "SOUR:WAVE:AMPL?", peak, 5e-4, 1e-12),
                    ("offset", "SOUR:WAVE:OFFS?", 0.0, 0, 1e-12)):
                actual = source_query(source, command)
                if not math.isclose(actual, expected, rel_tol=rel, abs_tol=absolute):
                    raise RuntimeError(f"6221 {label} readback mismatch: requested {expected:g}, read {actual:g}")
            for command in ("SOUR:WAVE:ABOR", "SOUR:WAVE:ARM", "SOUR:WAVE:INIT"):
                source._write_checked(command)
            if int(round(source_query(source, "OUTP?"))) != 1:
                raise RuntimeError("6221 sine output did not turn on")
            source.output_enabled = True
            source.output_mode = "AC"
            source.current_a = s.read_current_rms_a

    def check_read_current(self):
        if int(round(source_query(self.source, "OUTP?"))) != 1:
            raise RuntimeError("6221 output turned off during the read")
        if int(round(source_query(self.source, "STAT:MEAS:COND?"))) & 8:
            raise RuntimeError("6221 hit compliance during the read; raise the compliance or check the wiring")

    # -- SR830 ----------------------------------------------------------
    def configure_lockin(self):
        """First harmonic, external reference from the 6221 marker, filters
        off, as at connection; the rest is recorded as found."""
        lockin = self.lockin
        with lockin.lock:
            if lockin.instrument is None:
                raise RuntimeError("SR830 is not connected")
            lockin.instrument.write("*CLS")  # A disconnect can leave *ESR? latched.
        required = {"HARM": 1, "FMOD": 0, "RSLP": core.PPMS_SR830_REFERENCE_SLOPE, "ILIN": 0, "SYNC": 0}
        for command, value in required.items():
            sr830_write(lockin, f"{command} {value}")
        found = {command: sr830_query(lockin, f"{command}?", 1)[0] for command in SR830_RECORDED}
        wrong = {command: found[command] for command, value in required.items() if found[command] != value}
        if wrong:
            raise RuntimeError(f"SR830 setup readback mismatch: {wrong}")
        tau = tau_seconds(int(found["OFLT"]))
        if tau > LOCKIN_MAX_TAU_S:
            raise RuntimeError(f"SR830 time constant {tau:g} s is above {LOCKIN_MAX_TAU_S:g} s; set a shorter one")
        self.lockin_settings = {command: int(value) if float(value).is_integer() else value
                                for command, value in found.items()}
        self.lockin_settings["tau_s"] = tau
        for channel, name in ((1, "X"), (2, "Y")):
            offset, expand = sr830_query(lockin, f"OEXP? {channel}", 2)
            self.lockin_settings[f"offset_{name}_percent"] = offset
            self.lockin_settings[f"expand_{name}"] = int(expand)
        self.emit("lockin", dict(self.lockin_settings))

    def wait_reference_lock(self):
        expected = self.settings.read_frequency_hz
        deadline = self.clock() + core.PPMS_TRANSPORT_REFERENCE_TIMEOUT_S
        while True:
            frequency = sr830_query(self.lockin, "FREQ?", 1)[0]
            status = int(round(sr830_query(self.lockin, "LIAS?", 1)[0]))
            if not status & 8 and math.isclose(frequency, expected, rel_tol=5e-3, abs_tol=0.1):
                return frequency
            if self.clock() >= deadline:
                raise RuntimeError(
                    f"SR830 did not lock to the 6221 reference: {frequency:.6g} Hz, LIAS={status}; "
                    "check the 6221 trigger-link/marker cable to the SR830 reference input")
            self.wait(0.2)

    def clear_overload_latch(self):
        sr830_query(self.lockin, "LIAS?", 1)  # LIAS? reports and clears latched bits.

    def autorange(self):
        """One range up on overload, one down below 10 % of full scale; once
        it has gone up it never comes back down in the same call, so the two
        rules cannot alternate.  Returns the code in use."""
        scales = core.NONLOCAL_SR830_FULL_SCALE_V
        code = int(sr830_query(self.lockin, "SENS?", 1)[0])
        went_up = False
        for _ in range(AUTORANGE_MAX_CHANGES):
            if not 0 <= code < len(scales):
                raise RuntimeError(f"SR830 sensitivity readback invalid: {code}")
            magnitude = abs(sr830_query(self.lockin, "OUTP? 3", 1)[0])
            self.clear_overload_latch()
            status = int(round(sr830_query(self.lockin, "LIAS?", 1)[0]))
            if status & 7:
                if code >= len(scales) - 1:
                    raise RuntimeError("SR830 overload at its largest range; check the input")
                requested = code + 1
                went_up = True
            elif not went_up and code > 0 and magnitude < AUTORANGE_LOW_FRACTION * scales[code]:
                requested = code - 1
            else:
                return code
            sr830_write(self.lockin, f"SENS {requested}")
            code = int(sr830_query(self.lockin, "SENS?", 1)[0])
            if code != requested:
                raise RuntimeError(f"SR830 range readback mismatch: requested {requested}, read {code}")
            self.wait(self.lockin_settle_s())
            self.clear_overload_latch()
        return code

    def acquire(self, duration):
        expected = self.settings.read_frequency_hz
        samples = []
        end = self.clock() + duration
        while True:
            self.check()
            x, y, _r, _theta, frequency = sr830_query(self.lockin, "SNAP? 1,2,3,4,9", 5)
            status = int(round(sr830_query(self.lockin, "LIAS?", 1)[0]))
            self.check_read_current()
            if status & 8 or not math.isclose(frequency, expected, rel_tol=5e-3, abs_tol=0.1):
                raise RuntimeError(f"SR830 lost the 6221 reference during the read: {frequency:.6g} Hz, LIAS={status}")
            if status & 7:
                return samples, True
            samples.append((x, y, frequency))
            if self.clock() >= end and len(samples) >= 3:
                return samples, False
            self.wait(READ_SAMPLE_INTERVAL_S)

    def read(self):
        s = self.settings
        self.start_read_current()
        self.wait_reference_lock()
        self.wait(self.lockin_settle_s() + s.read_settle_s)
        for attempt in range(READ_OVERLOAD_RETRIES + 1):
            self.clear_overload_latch()
            code = self.autorange() if s.autorange else int(sr830_query(self.lockin, "SENS?", 1)[0])
            samples, overloaded = self.acquire(s.read_time_s)
            if not overloaded:
                break
            if not s.autorange or attempt == READ_OVERLOAD_RETRIES:
                raise RuntimeError("SR830 overload during the read")
        count = len(samples)
        mean_x = sum(v[0] for v in samples) / count
        mean_y = sum(v[1] for v in samples) / count

        def spread(values, mean):
            return math.sqrt(sum((v - mean) ** 2 for v in values) / (count - 1)) if count > 1 else 0.0

        scales = core.NONLOCAL_SR830_FULL_SCALE_V
        return {
            "x_v": mean_x, "y_v": mean_y, "r_v": math.hypot(mean_x, mean_y),
            # Theta of the mean vector, as the main program reports it.
            "theta_deg": math.degrees(math.atan2(mean_y, mean_x)),
            "x_std_v": spread([v[0] for v in samples], mean_x),
            "y_std_v": spread([v[1] for v in samples], mean_y),
            "samples": count,
            "read_frequency_hz": sum(v[2] for v in samples) / count,
            "sensitivity_code": code,
            "full_scale_v": scales[code] if 0 <= code < len(scales) else math.nan,
        }

    # -- the sequence ---------------------------------------------------
    def run(self):
        s = self.settings
        total = s.total_steps
        started = self.clock()
        done = 0
        try:
            self.source_off()
            self.emit("status", "Checking the SR830: 1st harmonic, external reference")
            self.configure_lockin()
            for index, (cycle, name) in enumerate(s.iter_steps(), 1):
                angle, field, pulse_ma = s.states[name]
                prefix = f"Step {index}{f'/{total}' if total else ''} (cycle {cycle}), state {name}"
                self.source_off()
                self.emit("status", f"{prefix}: field to 0 Oe before rotating")
                self.ensure_zero_field()
                self.emit("status", f"{prefix}: rotating to {angle:g}°")
                angle_read = self.rotate_to(angle)
                self.emit("status", f"{prefix}: write field {field:+g} Oe")
                write = self.field_to(field)
                self.emit("status", f"{prefix}: pulse {pulse_ma:+g} mA, {s.pulse_width_s * 1e6:g} µs × {s.pulse_count}")
                pulse = self.pulse(pulse_ma)
                self.wait(s.after_pulse_s)
                self.emit("status", f"{prefix}: field to 0 Oe for the read")
                read_field = self.field_to(0.0)
                self.emit("status", f"{prefix}: reading the SR830 at {s.read_current_rms_a * 1e3:g} mA RMS")
                sample = self.read()
                self.source_off()
                row = {
                    "step": index, "cycle": cycle, "state": name,
                    "time_s": round(self.clock() - started, 3),
                    "timestamp": datetime.now().isoformat(timespec="seconds"),
                    "angle_set_deg": angle, "angle_read_deg": angle_read,
                    "write_field_set_oe": field, "write_field_read_oe": write.get("field_oe"),
                    "write_field_status": write.get("field_status"),
                    "pulse_current_ma": pulse_ma, "pulse_width_us": s.pulse_width_s * 1e6,
                    "pulse_count": s.pulse_count, "compliance_v": s.compliance_v,
                    "pulse_in_compliance": pulse.get("in_compliance"),
                    "read_field_oe": read_field.get("field_oe"), "read_field_status": read_field.get("field_status"),
                    "temperature_k": read_field.get("temperature_k"),
                    "read_current_ma_rms": s.read_current_rms_a * 1e3,
                    **sample,
                    "rxy_ohm": sample["x_v"] / s.read_current_rms_a,
                }
                self.emit("point", row)
                done += 1
            self.emit("status", f"Finished: {done} states written and read")
            return done
        finally:
            self.shutdown()

    def shutdown(self):
        """6221 off and 0 Oe (Driven), both confirmed by readback.  Never moves
        the rotator.  Problems are kept in ``shutdown_problems``."""
        problems = []
        if self.source.connected:  # Not connected (a manual action): nothing to turn off.
            try:
                self.source_off()
            except Exception as error:
                problems.append(f"6221 not confirmed off: {error}")
        try:
            if not self.ppms.connected:
                raise LookupError  # Nothing to return to 0 Oe from here.
            s = self.settings
            data = self.environment()
            # A field this run commanded may not show in the reading yet.
            if self.commanded_field not in (None, 0.0) or not core.CombinedControlApp._ppms_field_is_ready(data, 0.0):
                self.emit("status", "Returning the field to 0 Oe…")
                self.commanded_field = 0.0
                result = self.ppms.set_field(0.0, s.field_rate_oe_s, s.approach_code, 1)
                start = max(abs(float(result.get("field_oe", 0.0))), abs(float(data.get("field_oe", 0.0))), 1500.0)
                deadline = self.clock() + start / s.field_rate_oe_s + SHUTDOWN_FIELD_MARGIN_S
                # Held for the settle time (at least 3 s): a reading from before
                # the command could otherwise pass for zero.
                hold = max(s.field_settle_s, 3.0)
                settled_since = None
                while True:
                    self.sleep(0.5)
                    data = self.environment()
                    if core.CombinedControlApp._ppms_field_is_ready(data, 0.0, driven=True):
                        settled_since = self.clock() if settled_since is None else settled_since
                        if self.clock() - settled_since >= hold:
                            break
                    else:
                        settled_since = None
                    if self.clock() >= deadline:
                        problems.append(f"field not confirmed at 0 Oe: {data.get('field_oe')} Oe / "
                                        f"{data.get('field_status')}")
                        break
        except LookupError:
            pass
        except Exception as error:
            problems.append(f"0 Oe not confirmed: {error}")
        self.shutdown_problems = problems
        if problems:
            self.emit("log", "Shutdown: " + "; ".join(problems) + ". Check MultiVu and the 6221.")

    # -- manual settings, as on the PPMS page -----------------------------
    def require_rotator_at_rest(self):
        """Refuse (nothing sent) unless the rotator reads AtTarget, as the PPMS
        page blocks its controls while a move may still be under way.  Its
        reading, or None when the PPMS is not connected here."""
        if not self.ppms.connected:
            return None
        reading = self.motor.read()
        if reading["status"] not in ROTATOR_AT_REST:
            raise RuntimeError(
                f"The rotator is {reading['status']} at {reading['angle_deg']:.2f}°: nothing sent. "
                "Read until it shows AtTarget, or check MultiVu")
        return reading

    def require_zero_field(self):
        data = self.environment()
        if not core.CombinedControlApp._ppms_field_is_ready(data, 0.0):
            raise RuntimeError(
                f"The rotator turns only at zero field; the field is {data.get('field_oe')} Oe / "
                f"{data.get('field_status')}. Zero H first")

    def manual_move(self, angle, confirm_large):
        """Move as a run step does: only at zero field and with the rotator at
        rest (refused, nothing sent, otherwise), then the 6221 off.  A turn
        over LARGE_ROTATION_DEG needs ``confirm_large(from_deg, to_deg)`` to
        return True; the field is read again after the answer."""
        self.require_zero_field()
        before = self.require_rotator_at_rest()
        if abs(angle - before["angle_deg"]) > LARGE_ROTATION_DEG:
            if not confirm_large(before["angle_deg"], angle):
                raise ManualDeclined("Move cancelled; MOVE not sent")
            self.check()
            self.require_zero_field()  # The question may have been open a while.
        if self.source.connected:
            self.source_off()
        return self.rotate_to(angle)

    def manual_field(self, target):
        """The PPMS page's Set H and Zero H: to ``target``, ending Persistent.
        Done by that page's rule (_follow_ppms_persistent_setpoint): Persistent
        at the target once the status has left Persistent, or Persistent at
        the target PPMS_PERSISTENT_CONFIRM_S after the command, so a reading
        from before it cannot pass.  Nothing is resent.  A magnet fault ends it
        at once; the reading goes to the Now line every few seconds."""
        s = self.settings
        self.require_rotator_at_rest()
        result = self.command_field(target, mode=0)
        sent = self.clock()
        try:
            distance = abs(target - float(result["field_oe"]))
        except (KeyError, TypeError, ValueError):
            distance = abs(target)
        deadline = sent + distance / s.field_rate_oe_s + core.PPMS_PERSISTENT_MARGIN_S
        tolerance = max(core.PPMS_FIELD_TOLERANCE_OE, abs(target) * core.PPMS_FIELD_RELATIVE_TOLERANCE)
        left_persistent = False
        shown = None
        while True:
            self.check()
            data = self.environment()
            if shown is None or self.clock() - shown >= MANUAL_READING_INTERVAL_S:
                self.emit("manual-reading", dict(data))
                shown = self.clock()
            status = str(data.get("field_status", "")).strip().lower().replace(" ", "")
            if any(fault in status for fault in FIELD_FAULTS):
                raise RuntimeError(f"PPMS reports {data.get('field_status')} at {data.get('field_oe')} Oe; "
                                   "check MultiVu (not resent)")
            try:
                measured = float(data["field_oe"])
            except (KeyError, TypeError, ValueError):
                measured = math.nan
            at_target = abs(measured - target) <= tolerance
            if status not in ("persistent", "stablepersistent"):
                left_persistent = True  # Ramping, or the switch warming or cooling.
            elif at_target and (left_persistent or self.clock() - sent >= core.PPMS_PERSISTENT_CONFIRM_S):
                return data
            elif left_persistent and not at_target:
                raise RuntimeError(f"Persistent at {measured:+.7g} Oe, not {target:+g} Oe; check MultiVu (not resent)")
            if self.clock() >= deadline:
                raise RuntimeError(f"Not Persistent at {target:+g} Oe in time: {data.get('field_oe')} Oe / "
                                   f"{data.get('field_status')}; check MultiVu (not resent)")
            self.wait(0.5)

    def manual_current(self):
        """The PPMS page's Set I: the read sine (settings.read_current_rms_a,
        phase marker on), left on.  The SR830's lock to it is reported, read
        only: nothing is written to the SR830."""
        self.require_rotator_at_rest()
        self.start_read_current()
        if not self.lockin.connected:
            return {"lock": "SR830 not connected"}
        expected = self.settings.read_frequency_hz
        deadline = self.clock() + core.PPMS_TRANSPORT_REFERENCE_TIMEOUT_S
        try:
            while True:
                frequency = sr830_query(self.lockin, "FREQ?", 1)[0]
                status = int(round(sr830_query(self.lockin, "LIAS?", 1)[0]))
                if not status & 8 and math.isclose(frequency, expected, rel_tol=5e-3, abs_tol=0.1):
                    return {"lock": f"SR830 locked at {frequency:.6g} Hz"}
                if self.clock() >= deadline:
                    return {"lock": f"SR830 not locked ({frequency:.6g} Hz, LIAS={status}); check its reference input"}
                self.wait(0.2)
        except core.PPMSScanCancelled:
            raise
        except Exception as error:
            return {"lock": f"SR830 lock not read: {core.short_status_error(error, 60)}"}

    def manual_pulse(self, pulse_ma):
        """One write pulse train as in a run (the Write settings), 6221 off first."""
        if pulse_ma == 0:
            raise ValueError("Pulse mA is 0: nothing to send")
        self.require_rotator_at_rest()
        self.source_off()
        return self.pulse(pulse_ma)

    def manual_read(self):
        """Angle, field, temperature and 6221 output, as far as connected."""
        reading = {}
        if self.ppms.connected:
            reading.update(self.ppms.read_environment())
            try:
                angle = self.motor.read()
                reading["angle_deg"], reading["angle_status"] = angle["angle_deg"], angle["status"]
            except Exception as error:
                reading["angle_error"] = core.short_status_error(error, 60)
        if self.source.connected:
            reading["source_on"] = int(round(source_query(self.source, "OUTP?"))) == 1
        return reading


def read_instrument_status(ppms, source, cryogen=False, motor=None):
    """What the PPMS status box shows: temperature, field, angle, and, with
    ``cryogen``, chamber pressure and helium level (the PPMS page's own read,
    which never starts a fill); the 6221 output.  Read only."""
    status = {"time": datetime.now().strftime("%H:%M:%S")}
    if ppms.connected:
        status.update(ppms.read_environment())
        try:
            angle = (motor or core.PPMSMotorDebug(ppms.active)).read()
            status["angle_deg"], status["angle_status"] = angle["angle_deg"], angle["status"]
        except Exception as error:
            status["angle_error"] = core.short_status_error(error, 40)
        if cryogen:
            status.update(core.CombinedControlApp._read_ppms_cryogen_display(
                types.SimpleNamespace(ppms_environment_controller=ppms)))
    if source.connected:
        try:
            status["source_on"] = int(round(source_query(source, "OUTP?"))) == 1
        except Exception as error:
            status["source_error"] = core.short_status_error(error, 40)
    return status


def run_manual(run, operation):
    """One manual action: (result, outcome, problems).  ``outcome`` is None,
    or ("declined" | "stopped" | "failed", error).  On Stop the run's shutdown
    (6221 off, 0 Oe Driven, read back); on an error the 6221 is turned off
    and the field and angle are left as they are."""
    try:
        return operation(run), None, []
    except ManualDeclined as error:
        return None, ("declined", error), []
    except core.PPMSScanCancelled as error:
        run.shutdown()
        return None, ("stopped", error), list(run.shutdown_problems)
    except BaseException as error:
        problems = []
        if run.source.connected:
            try:
                run.source_off()
            except Exception as off_error:
                problems.append(f"6221 not confirmed off: {off_error}")
        return None, ("failed", error), problems


def nice_ticks(low, high, count=5):
    if not (math.isfinite(low) and math.isfinite(high)):
        return []
    span = high - low
    # A span lost in rounding (equal values) would give a step that adds nothing.
    if span <= max(abs(low), abs(high), 1e-300) * 1e-9:
        middle = (low + high) / 2.0
        half = max(abs(middle) * 0.1, 1e-12)
        low, high = middle - half, middle + half
    raw = (high - low) / max(1, count)
    magnitude = 10 ** math.floor(math.log10(raw))
    step = next(m * magnitude for m in (1, 2, 2.5, 5, 10) if m * magnitude >= raw)
    first = math.ceil(low / step) * step
    ticks = []
    for index in range(int((high - first) / step + 1e-9) + 1):
        if index > 50:
            break
        value = first + index * step
        ticks.append(0.0 if abs(value) < step * 1e-9 else value)
    return ticks


# The plot's time axis is the clock: a state written at one time and read at
# later times shows how long it is kept (non-volatile storage).  Tick spacing
# in seconds, the first that gives at most the asked number of ticks.
TIME_STEPS_S = (1, 2, 5, 10, 15, 30, 60, 120, 300, 600, 900, 1800, 3600, 7200, 10800,
                21600, 43200, 86400, 172800, 604800)


def row_epoch(row):
    """A point's local clock time (its CSV timestamp), as epoch seconds."""
    try:
        return datetime.fromisoformat(str(row["timestamp"])).timestamp()
    except (KeyError, TypeError, ValueError):
        return float(row.get("time_s", row.get("step", 0.0)))


def time_ticks(low, high, count=8):
    """Ticks at round local clock times between epoch seconds ``low`` and
    ``high``: ([(epoch, label), ...], axis title).  Labels are HH:MM:SS for
    steps under a minute, HH:MM otherwise, with the month and day when the
    axis spans more than one date."""
    span = max(high - low, 1.0)
    step = next((s for s in TIME_STEPS_S if span / s <= count), TIME_STEPS_S[-1])
    start, end = datetime.fromtimestamp(low), datetime.fromtimestamp(high)
    midnight = start.replace(hour=0, minute=0, second=0, microsecond=0).timestamp()
    several_days = start.date() != end.date()
    fmt = "%m-%d %H:%M" if several_days else ("%H:%M:%S" if step < 60 else "%H:%M")
    ticks = []
    value = midnight + math.ceil((low - midnight) / step) * step
    while value <= high and len(ticks) < 60:
        ticks.append((value, datetime.fromtimestamp(value).strftime(fmt)))
        value += step
    dates = f"{start:%Y-%m-%d}" + (f" to {end:%Y-%m-%d}" if several_days else "")
    return ticks, f"Local time ({dates})"


class StatePlot:
    """H, I and SR830 X against the clock time of each point (its CSV
    timestamp), so retention can be read off the axis; panel A of the
    figure, with time for its steps."""

    PANELS = (
        ("H write (T)", lambda row: row["write_field_set_oe"] / OE_PER_T),
        ("I pulse (mA)", lambda row: row["pulse_current_ma"]),
        ("SR830 X (µV)", lambda row: row["x_v"] * 1e6),
    )
    LABELLED_POINTS = 60

    def __init__(self, canvas):
        self.canvas = canvas
        self.rows = []
        canvas.bind("<Configure>", lambda event: self.redraw())

    def set_rows(self, rows):
        self.rows = list(rows)
        self.redraw()

    def redraw(self):
        canvas = self.canvas
        canvas.delete("all")
        width = max(200, canvas.winfo_width())
        height = max(200, canvas.winfo_height())
        left, right, top, bottom = 90, 20, 14, 40
        gap = 12
        panel_height = (height - top - bottom - 2 * gap) / 3
        times = [row_epoch(row) for row in self.rows]
        if times:
            pad = (max(times) - min(times)) * 0.04 or 30.0
            x_low, x_high = min(times) - pad, max(times) + pad
        else:
            x_high = time.time()
            x_low = x_high - 300.0
        labelled = len(self.rows) <= self.LABELLED_POINTS
        marker = 5 if len(self.rows) <= 200 else 3

        def x_pixel(value):
            return left + (value - x_low) / (x_high - x_low) * (width - left - right)

        for index, (label, value_of) in enumerate(self.PANELS):
            y0 = top + index * (panel_height + gap)
            y1 = y0 + panel_height
            canvas.create_rectangle(left, y0, width - right, y1, outline="#555555")
            canvas.create_text(16, (y0 + y1) / 2, text=label, angle=90, anchor="center", font=("TkDefaultFont", 11))
            values = [value_of(row) for row in self.rows]
            if values:
                low, high = min(values), max(values)
                pad = (high - low) * 0.12 or max(abs(high), 1.0) * 0.12
                # Room above the highest X point for its state letter.
                low, high = low - pad, high + pad * (2.0 if index == 2 and labelled else 1.0)
            else:
                low, high = -1.0, 1.0

            def y_pixel(value, low=low, high=high, y0=y0, y1=y1):
                return y1 - (value - low) / (high - low) * (y1 - y0)

            for tick in nice_ticks(low, high, 4):
                y = y_pixel(tick)
                canvas.create_line(left - 4, y, left, y, fill="#555555")
                canvas.create_text(left - 6, y, text=f"{tick:g}", anchor="e", font=("TkDefaultFont", 10))
            points = [(x_pixel(at), y_pixel(v), base_state(row["state"]), row["state"])
                      for row, at, v in zip(self.rows, times, values)]
            if index == 2 and len(points) > 1:
                canvas.create_line(*[c for p in points for c in p[:2]], fill="#9e9e9e")
            for x, y, base, name in points:
                color = STATE_COLORS.get(base, "#6a1b9a")
                canvas.create_rectangle(x - marker, y - marker, x + marker, y + marker, fill=color, outline=color)
                if index == 2 and labelled:
                    canvas.create_text(x, y - 14, text=name, fill=color, font=("TkDefaultFont", 11, "bold"))
            if not points and index == 2:
                canvas.create_text((left + width - right) / 2, (y0 + y1) / 2, text="Waiting for data",
                                   fill="#757575")
        axis_y = height - bottom
        ticks, title = time_ticks(x_low, x_high, max(2, min(10, int((width - left - right) / 110))))
        for tick, label in ticks:
            x = x_pixel(tick)
            canvas.create_line(x, axis_y, x, axis_y + 4, fill="#555555")
            canvas.create_text(x, axis_y + 6, text=label, anchor="n", font=("TkDefaultFont", 10))
        canvas.create_text((left + width - right) / 2, height - 12, text=title, font=("TkDefaultFont", 11))


class FourStateApp:
    def __init__(self, root, mode="cycle"):
        if mode not in MODES:
            raise ValueError(f"Unknown mode {mode!r}")
        self.root = root
        self.initial_mode = mode
        self.title = APP_TITLE
        self.data_directory = HERE / f"{APP_TITLE} data"
        root.title(self.title)
        width = max(WINDOW_MINIMUM[0], min(WINDOW_SIZE[0], root.winfo_screenwidth() - 80))
        height = max(WINDOW_MINIMUM[1], min(WINDOW_SIZE[1], root.winfo_screenheight() - 120))
        root.geometry(f"{width}x{height}")
        root.minsize(*WINDOW_MINIMUM)
        self.events = queue.Queue()
        self.ppms = core.PPMSRemoteReadbackController(self.log_from_thread)
        self.source = core.Keithley6221Controller(self.log_from_thread)
        self.lockin = core.SafeVisaIdentityController(self.log_from_thread, "SR830", "SR830", owner_scope=self.title)
        self.stop_event = threading.Event()
        self.worker = None
        self.run = None
        self.busy = False
        self.rows = []
        self.csv_file = None
        self.csv_writer = None
        self.run_folder = None   # The running run's folder: logs go there.
        self.last_folder = None  # The latest run's folder: Save plot goes there.
        self.record = None
        # Set after a Move that was stopped or failed (the rotator may still
        # be turning; cleared by a reading at rest), and after a field
        # command that was not confirmed (Move refused until Zero H).
        self.motor_pending = False
        self.field_unconfirmed = None
        self.status_polling = False
        self.last_cryogen = None
        self._build()
        root.protocol("WM_DELETE_WINDOW", self.close)
        self.drain_after = root.after(100, self._drain)
        self.status_after = root.after(int(STATUS_POLL_S * 1000), self._poll_status)

    # -- layout ---------------------------------------------------------
    def _build(self):
        root = self.root
        root.columnconfigure(0, weight=1)
        root.rowconfigure(0, weight=1)
        page = ttk.Frame(root, padding=8)
        page.grid(row=0, column=0, sticky="nsew")
        page.columnconfigure(0, weight=1)
        page.rowconfigure(2, weight=1)
        self.inputs = []

        # The setup boxes side by side at their own widths, wrapping to a new
        # line only when the window is too narrow (see _flow): a box as wide
        # as the window held mostly blank.
        flow = ttk.Frame(page)
        flow.grid(row=0, column=0, sticky="ew")
        connections = ttk.LabelFrame(flow, text="Connections", padding=6)
        connections.columnconfigure(9, weight=1)
        dll_default = next((str(p) for p in core.PPMSQDInstrumentReadbackController.candidate_dll_paths()
                            if p.is_file()), "")
        self.host_var = tk.StringVar(value=core.PPMS_DEFAULT_HOST)
        self.port_var = tk.StringVar(value=str(core.PPMS_QDINSTRUMENT_DEFAULT_PORT))
        self.dll_var = tk.StringVar(value=dll_default)
        self.source_resource_var = tk.StringVar(value=core.K6221_DEFAULT_RESOURCE)
        self.lockin_resource_var = tk.StringVar(value=core.PPMS_SR830_RESOURCE_OPTIONS[0])
        self.status_vars = {key: tk.StringVar(value="Not connected") for key in ("ppms", "6221", "sr830")}
        # One row per instrument, each with its own Connect and Disconnect,
        # as on the main program's pages: name, address, buttons, lamp, state.
        self.connection_widgets = {}   # key: (address widgets, Connect, Disconnect, lamp)
        self.connection_failed = {key: False for key in CONNECTIONS}
        for row, (key, label) in enumerate(CONNECTIONS.items()):
            # Right-aligned; the column is as wide as the longest name.
            ttk.Label(connections, text=f"{label}:").grid(
                row=row, column=0, sticky="e", padx=(0, 4), pady=1)
            if key == "ppms":
                host = ttk.Combobox(connections, textvariable=self.host_var, values=core.PPMS_HOST_CHOICES, width=16)
                host.grid(row=row, column=1, sticky="w")
                ttk.Label(connections, text="Port").grid(row=row, column=2, sticky="e", padx=(8, 2))
                port = ttk.Entry(connections, textvariable=self.port_var, width=7)
                port.grid(row=row, column=3, sticky="w")
                ttk.Label(connections, text="DLL").grid(row=row, column=4, sticky="e", padx=(8, 2))
                dll = ttk.Entry(connections, textvariable=self.dll_var, width=26)
                dll.grid(row=row, column=5, sticky="ew")
                address = (host, port, dll)
            elif key == "6221":
                address = (ttk.Entry(connections, textvariable=self.source_resource_var, width=18),)
                address[0].grid(row=row, column=1, sticky="w")
            else:
                address = (ttk.Combobox(connections, textvariable=self.lockin_resource_var,
                                        values=core.PPMS_SR830_RESOURCE_OPTIONS, width=16),)
                address[0].grid(row=row, column=1, sticky="w")
            connect = ttk.Button(connections, text="Connect", width=8,
                                 command=lambda selected=key: self.connect_device(selected))
            connect.grid(row=row, column=6, padx=(10, 1), pady=1)
            disconnect = ttk.Button(connections, text="Disconnect", width=10,
                                    command=lambda selected=key: self.disconnect_device(selected))
            disconnect.grid(row=row, column=7, padx=1, pady=1)
            lamp = tk.Canvas(connections, width=16, height=16, highlightthickness=0,
                             background=root.cget("background"))
            lamp.create_oval(3, 3, 13, 13, fill=LAMP_COLORS["off"], outline="#6f757b", tags="lamp")
            lamp.grid(row=row, column=8, padx=(6, 2))
            state = ttk.Label(connections, textvariable=self.status_vars[key], anchor="w")
            state.grid(row=row, column=9, sticky="ew")
            # A long state (a failure) wraps rather than being cut.
            state.bind("<Configure>", lambda event: event.widget.configure(wraplength=max(120, event.width - 4)))
            self.connection_widgets[key] = (address, connect, disconnect, lamp)
        status = self._build_status(flow)

        self.values = {key: tk.StringVar(value=value) for key, value in DEFAULTS.items()}
        manual = self._build_manual(flow)

        states = ttk.LabelFrame(flow, text="States (1500 Oe = 0.15 T)", padding=6)
        for column, title in enumerate(("State", "φ PPMS (°)", "H write (Oe)", "I pulse (mA)")):
            ttk.Label(states, text=title).grid(row=0, column=column, padx=3, sticky="w")
        self.state_vars = {}
        for row, (name, angle, field, pulse) in enumerate(DEFAULT_STATES, 1):
            ttk.Label(states, text=name, foreground=STATE_COLORS.get(base_state(name), "#000000")).grid(
                row=row, column=0, padx=3, sticky="w")
            variables = tuple(tk.StringVar(value=f"{value:g}") for value in (angle, field, pulse))
            for column, variable in enumerate(variables, 1):
                entry = ttk.Entry(states, textvariable=variable, width=9)
                entry.grid(row=row, column=column, padx=3, pady=1, sticky="w")
                self.inputs.append((entry, "normal"))
            self.state_vars[name] = variables

        # Two columns of four, so the row is no taller than the States table:
        # the pulse on the left, the field and rotator on the right.
        write = ttk.LabelFrame(flow, text="Write", padding=6)
        self._parameter_rows(write, (
            ("Pulse width (µs)", "pulse_width_us"),
            ("Pulses per write", "pulse_count"),
            ("Compliance (V)", "compliance_v"),
            ("Wait after pulse (s)", "after_pulse_s"),
            ("Field rate (Oe/s)", "field_rate"),
            ("Field approach", "approach"),
            ("Field settle (s)", "field_settle_s"),
            ("Decel (0 fast–14 slow)", "decel"),
        ), columns=2)
        read = ttk.LabelFrame(flow, text="Read (H = 0, SR830 1st harmonic)", padding=6)
        self._parameter_rows(read, (
            ("Read current (mA RMS)", "read_current_ma"),
            ("Frequency (Hz)", "read_frequency_hz"),
            ("Read time (s)", "read_time_s"),
            ("Extra settle (s)", "read_settle_s"),
        ))
        self.autorange_var = tk.BooleanVar(value=True)
        autorange = ttk.Checkbutton(read, text="SR830 autorange before each read", variable=self.autorange_var)
        autorange.grid(row=4, column=0, columnspan=2, sticky="w", pady=(4, 0))
        self.inputs.append((autorange, "normal"))
        self._flow(flow, (connections, status, manual, states, write, read))

        run = ttk.LabelFrame(page, text="Run", padding=6)
        run.grid(row=1, column=0, sticky="ew", pady=(6, 0))
        run.columnconfigure(12, weight=1)
        self.mode_var = tk.StringVar(value=MODES[self.initial_mode])
        ttk.Label(run, text="Mode").grid(row=0, column=0, sticky="w")
        mode = ttk.Combobox(run, textvariable=self.mode_var, values=tuple(MODES.values()),
                            state="readonly", width=10)
        mode.grid(row=0, column=1, sticky="w", padx=(4, 10))
        mode.bind("<<ComboboxSelected>>", lambda event: self._apply_mode())
        self.inputs.append((mode, "readonly"))
        self.sequence_label = ttk.Label(run)
        self.sequence_label.grid(row=0, column=2, sticky="w")
        # Each mode keeps its own states, in the same place on the row.
        self.cycle_var = tk.StringVar(value=DEFAULT_CYCLE)
        self.sequence_var = tk.StringVar(value=DEFAULT_SEQUENCE)
        self.cycle_entry = ttk.Entry(run, textvariable=self.cycle_var, width=20)
        self.sequence_entry = ttk.Entry(run, textvariable=self.sequence_var, width=20)
        for entry in (self.cycle_entry, self.sequence_entry):
            entry.grid(row=0, column=3, sticky="w", padx=(4, 10))
            self.inputs.append((entry, "normal"))
        self.count_label = ttk.Label(run, text="Cycles")
        self.count_label.grid(row=0, column=4, sticky="w")
        self.count_entry = ttk.Entry(run, textvariable=self.values["repeats"], width=6)
        self.count_entry.grid(row=0, column=5, sticky="w", padx=(4, 6))
        self.until_stop_var = tk.BooleanVar(value=False)
        self.until_stop_box = ttk.Checkbutton(run, text="Until Stop", variable=self.until_stop_var)
        self.until_stop_box.grid(row=0, column=6, sticky="w", padx=(0, 10))
        self.inputs += [(self.count_entry, "normal"), (self.until_stop_box, "normal")]
        self.start_button = ttk.Button(run, text="Start", command=self.start, width=10)
        self.start_button.grid(row=0, column=7, padx=2)
        self.stop_button = ttk.Button(run, text="Stop", command=self.stop, width=8, state="disabled")
        self.stop_button.grid(row=0, column=8, padx=2)
        self.save_plot_button = ttk.Button(run, text="Save plot", command=self.save_plot, width=10)
        self.save_plot_button.grid(row=0, column=9, padx=2)
        self.progress = ttk.Progressbar(run, length=100)
        self.progress.grid(row=0, column=10, padx=(10, 6))
        self.status_var = tk.StringVar(value="Connect, check the settings, then Start")
        ttk.Label(run, textvariable=self.status_var).grid(row=0, column=11, columnspan=2, sticky="ew")
        self._apply_mode(initial=True)
        self.safety_var = tk.BooleanVar(value=False)
        ttk.Checkbutton(
            run, variable=self.safety_var,
            text=("Sample, wiring, pulse current, compliance, field and rotator clearance confirmed safe; "
                  "no other program is driving the PPMS, 6221 or SR830"),
        ).grid(row=1, column=0, columnspan=13, sticky="w", pady=(4, 0))

        lower = ttk.Frame(page)
        lower.grid(row=2, column=0, sticky="nsew", pady=(6, 0))
        lower.columnconfigure(0, weight=1)
        lower.rowconfigure(0, weight=1)
        self.canvas = tk.Canvas(lower, background="#ffffff", highlightthickness=0)
        self.canvas.grid(row=0, column=0, sticky="nsew")
        self.plot = StatePlot(self.canvas)
        side = ttk.Frame(lower)
        side.grid(row=0, column=1, sticky="ns", padx=(8, 0))
        side.rowconfigure(1, weight=1)
        self.last_var = tk.StringVar(value="Last read: --")
        ttk.Label(side, textvariable=self.last_var, justify="left", width=32).grid(row=0, column=0, sticky="nw")
        self.log = tk.Text(side, width=36, height=12, wrap="word", state="disabled")
        self.log.grid(row=1, column=0, sticky="nsew", pady=(6, 0))
        self._update_connection_controls()

    def _build_status(self, parent):
        """The PPMS status box, as the PPMS page's live readings: temperature,
        field and angle large, each with its status; pressure, helium level
        and the 6221 output below."""
        status = ttk.LabelFrame(parent, text="PPMS status (as on the PPMS page)", padding=6)
        self.status_box_vars = {key: tk.StringVar(value=value) for key, value in (
            ("temperature", "-- K"), ("temperature_status", "not read"),
            ("field", "-- Oe"), ("field_status", "not read"),
            ("angle", "-- °"), ("angle_status", "not read"),
            ("pressure", "--"), ("helium", "--"), ("source", "--"), ("time", "not read yet"))}
        for column, (title, value, state) in enumerate((("Temperature", "temperature", "temperature_status"),
                                                        ("Field", "field", "field_status"),
                                                        ("Angle", "angle", "angle_status"))):
            padx = (0 if column == 0 else 22, 0)
            ttk.Label(status, text=title).grid(row=0, column=column, sticky="w", padx=padx)
            ttk.Label(status, textvariable=self.status_box_vars[value], font=("TkDefaultFont", 15, "bold"),
                      width=11, anchor="w").grid(row=1, column=column, sticky="w", padx=padx)
            ttk.Label(status, textvariable=self.status_box_vars[state], foreground="#5f6670",
                      width=16, anchor="w").grid(row=2, column=column, sticky="w", padx=padx)
        line = ttk.Frame(status)
        line.grid(row=3, column=0, columnspan=3, sticky="w", pady=(4, 0))
        for title, key in (("Pressure", "pressure"), ("He level", "helium"), ("6221", "source"), ("Read", "time")):
            ttk.Label(line, text=f"{title}:", foreground="#5f6670").pack(side="left", padx=(0 if key == "pressure" else 12, 3))
            ttk.Label(line, textvariable=self.status_box_vars[key]).pack(side="left")
        return status

    @staticmethod
    def flow_lines(widths, available, gap):
        """Box indices per line: left to right, a new line when the next box
        does not fit (a box wider than the window gets a line of its own)."""
        lines, line, used = [], [], 0
        for index, width in enumerate(widths):
            need = width if not line else used + gap + width
            if line and need > available:
                lines.append(line)
                line, used = [index], width
            else:
                line.append(index)
                used = need
        if line:
            lines.append(line)
        return lines

    def _flow(self, container, boxes, gap=6):
        """Place ``boxes`` in lines (flow_lines), each line's boxes as tall as
        its tallest and its spare width shared out, so no line ends in a gap.
        Redone on a resize, after a connection changes and on the status
        timer, which catches a box asking for another size (a long state)."""
        def arrange(_event=None):
            try:
                available = container.winfo_width()
                widths = [box.winfo_reqwidth() for box in boxes]
                heights = [box.winfo_reqheight() for box in boxes]
            except tk.TclError:
                return
            if available <= 1:
                return
            layout = (available, tuple(widths), tuple(heights))
            if getattr(container, "_flow_layout", None) == layout:
                return
            container._flow_layout = layout
            y = 0
            for line in self.flow_lines(widths, available, gap):
                natural = sum(widths[i] for i in line)
                spare = max(0, available - natural - gap * (len(line) - 1))
                height = max(heights[i] for i in line)
                x = 0
                for position, index in enumerate(line):
                    width = widths[index] + spare * widths[index] // natural
                    if position == len(line) - 1:
                        width = max(widths[index], available - x)
                    boxes[index].place(x=x, y=y, width=width, height=height)
                    x += width + gap
                y += height + gap
            container.configure(height=max(1, y - gap))

        self._arrange_flow = arrange
        container.bind("<Configure>", arrange, add="+")
        container.after_idle(arrange)

    def _show_status(self, reading):
        """Fill the status box from what a reading holds; the rest stays."""
        vars_ = self.status_box_vars
        if "temperature_k" in reading:
            vars_["temperature"].set(f"{float(reading['temperature_k']):.3f} K")
            vars_["temperature_status"].set(str(reading.get("temperature_status", "")))
        if "field_oe" in reading:
            vars_["field"].set(f"{float(reading['field_oe']):+.1f} Oe")
            vars_["field_status"].set(str(reading.get("field_status", "")))
        if "angle_deg" in reading:
            vars_["angle"].set(f"{float(reading['angle_deg']):.2f}°")
            vars_["angle_status"].set(str(reading.get("angle_status", "")))
        elif "angle_error" in reading:
            vars_["angle_status"].set(f"not read: {reading['angle_error']}")
        def short(text, size=18):
            text = str(text)
            return text if len(text) <= size else text[:size - 1] + "…"

        if "pressure_display" in reading:
            vars_["pressure"].set(short(reading["pressure_display"]))
        if "helium_display" in reading:
            vars_["helium"].set(short(reading["helium_display"]))
        if "source_on" in reading:
            vars_["source"].set("ON" if reading["source_on"] else "OFF")
        elif "source_error" in reading:
            vars_["source"].set(f"not read: {reading['source_error']}")
        vars_["time"].set(reading.get("time") or datetime.now().strftime("%H:%M:%S"))

    def _poll_status(self):
        """Every STATUS_POLL_S while idle: read the status in a worker."""
        try:
            if not self.busy and not self.status_polling and (self.ppms.connected or self.source.connected):
                now = time.monotonic()
                cryogen = self.last_cryogen is None or now - self.last_cryogen >= CRYOGEN_POLL_S
                if cryogen and self.ppms.connected:
                    self.last_cryogen = now
                self.status_polling = True
                threading.Thread(target=self._status_worker, args=(cryogen,), daemon=True).start()
        finally:
            self.__dict__.get("_arrange_flow", lambda: None)()
            try:
                self.status_after = self.root.after(int(STATUS_POLL_S * 1000), self._poll_status)
            except tk.TclError:
                pass

    def _status_worker(self, cryogen):
        try:
            reading = read_instrument_status(self.ppms, self.source, cryogen)
        except Exception as error:
            reading = {"time": f"failed: {core.short_status_error(error, 60)}"}
        self.post("status-box", reading)

    def _build_manual(self, page):
        """The PPMS page's settings rows: angle, field and current, one action
        at a time.  Decel, field rate and frequency are the Write and Read
        boxes' own fields (the same values); the pulse uses the Write box's
        width, count and compliance."""
        manual = ttk.LabelFrame(page, text="Manual settings (as on the PPMS page)", padding=6)
        self.manual_vars = {key: tk.StringVar(value=value) for key, value in MANUAL_DEFAULTS.items()}
        self.manual_buttons = {}

        def field(row, column, label, variable):
            ttk.Label(manual, text=label).grid(row=row, column=column, sticky="e",
                                               padx=(0 if column == 0 else 10, 3), pady=1)
            entry = ttk.Entry(manual, textvariable=variable, width=8)
            entry.grid(row=row, column=column + 1, sticky="w", pady=1)
            self.inputs.append((entry, "normal"))

        def button(row, column, text, command):
            widget = ttk.Button(manual, text=text, command=command, width=8)
            widget.grid(row=row, column=column, padx=(8 if column == 4 else 2, 2), pady=1)
            self.inputs.append((widget, "normal"))
            self.manual_buttons[text] = widget

        field(0, 0, "Angle °", self.manual_vars["angle"])
        field(0, 2, "Decel 0–14", self.values["decel"])
        button(0, 4, "Move", self.manual_move)
        button(0, 5, "Read", self.manual_read)
        field(1, 0, "H Oe", self.manual_vars["field"])
        field(1, 2, "Oe/s", self.values["field_rate"])
        button(1, 4, "Set H", self.manual_set_field)
        button(1, 5, "Zero H", lambda: self.manual_set_field(zero=True))
        field(2, 0, "I mA RMS", self.manual_vars["current_ma"])
        field(2, 2, "f Hz", self.values["read_frequency_hz"])
        button(2, 4, "Set I", self.manual_set_current)
        button(2, 5, "I off", self.manual_current_off)
        field(3, 0, "Pulse mA", self.manual_vars["pulse_ma"])
        button(3, 4, "Pulse", self.manual_pulse)
        # Readings go to the PPMS status box.  The note wraps under the rows.
        note = ttk.Label(
            manual, foreground="#8a4b00", wraplength=420,
            text=("Set H and Zero H end Persistent; Move only at 0 Oe (6221 off first); "
                  "Pulse uses the Write box. Stop: 6221 off and 0 Oe."),
        )
        note.grid(row=4, column=0, columnspan=6, sticky="w", pady=(4, 0))
        return manual

    # -- manual settings ------------------------------------------------
    def _manual_check(self, *, ppms=False, source=False, safety=True, moves=False):
        """``moves``: the action drives the rotator, field or 6221 (all but
        I off and Read): refused while a stopped Move may still be turning."""
        if moves and self.motor_pending:
            raise ValueError("The rotator may still be turning after the last Move: "
                             "press Read until it shows AtTarget (or check MultiVu)")
        if ppms:
            if not self.ppms.connected:
                raise ValueError("Connect the PPMS first")
            if self.ppms.backend != core.PPMS_QDINSTRUMENT_BACKEND:
                raise ValueError("The rotator and field need the QDInstrument interface")
        if source and not self.source.connected:
            raise ValueError("Connect the 6221 first")
        if safety and not self.safety_var.get():
            raise ValueError("Tick the safety confirmation (under Run) first")

    def _manual_values(self):
        return {key: variable.get() for key, variable in self.values.items()}

    def manual_move(self):
        if self.busy:
            return
        try:
            self._manual_check(ppms=True, moves=True)
            if self.field_unconfirmed is not None:
                raise ValueError(f"The last field command ({self.field_unconfirmed:+g} Oe) was not confirmed: "
                                 "Zero H first, so the rotator never turns in a field")
            settings = manual_settings(self._manual_values())
            angle = _number(self.manual_vars["angle"].get(), "Angle")
            core.PPMSMotorDebug.validate_move(angle, settings.decel, angle)
        except ValueError as error:
            messagebox.showerror(self.title, str(error), parent=self.root)
            return

        def confirm_large(before, target):
            return self._ask_from_worker(
                f"Rotator {before:.4f}° → {target:g}°: a turn over {LARGE_ROTATION_DEG:g}°.\n"
                "Confirm the mechanical travel and wiring clearance; the rotator has no "
                "software stop (keep MultiVu Motion Control at hand). Continue?")

        self._run_manual(f"Moving the rotator to {angle:g}°", settings,
                         lambda run: run.manual_move(angle, confirm_large),
                         lambda result: f"Rotator at {result:.4f}°", kind="move")

    def manual_set_field(self, zero=False):
        if self.busy:
            return
        try:
            self._manual_check(ppms=True, moves=True)
            settings = manual_settings(self._manual_values())
            target = 0.0 if zero else _number(self.manual_vars["field"].get(), "H")
            core.validate_ppms_field_command(target, settings.field_rate_oe_s)
        except ValueError as error:
            messagebox.showerror(self.title, str(error), parent=self.root)
            return
        if zero:
            self.manual_vars["field"].set("0")
        self._run_manual(f"Field to {target:+g} Oe, then Persistent", settings,
                         lambda run: run.manual_field(target),
                         lambda result: f"Field {target:+g} Oe reached; Persistent confirmed",
                         kind="field", target=target)

    def manual_set_current(self):
        if self.busy:
            return
        try:
            self._manual_check(source=True, moves=True)
            settings = manual_settings(self._manual_values(), self.manual_vars["current_ma"].get())
        except ValueError as error:
            messagebox.showerror(self.title, str(error), parent=self.root)
            return
        rms_ma, frequency = settings.read_current_rms_a * 1e3, settings.read_frequency_hz
        self._run_manual(f"6221 sine {rms_ma:g} mA RMS at {frequency:g} Hz", settings,
                         lambda run: run.manual_current(),
                         lambda result: f"6221 ON, read back: {rms_ma:g} mA RMS at {frequency:g} Hz; "
                                        f"{result['lock']}")

    def manual_current_off(self):
        if self.busy:
            return
        try:
            self._manual_check(source=True, safety=False)
        except ValueError as error:
            messagebox.showerror(self.title, str(error), parent=self.root)
            return
        # The defaults, so a typing slip elsewhere never blocks turning off.
        self._run_manual("6221 off", manual_settings(DEFAULTS), lambda run: run.source_off(),
                         lambda result: "6221 output OFF, read back")

    def manual_pulse(self):
        if self.busy:
            return
        try:
            self._manual_check(source=True, moves=True)
            settings = manual_settings(self._manual_values())
            pulse_ma = _number(self.manual_vars["pulse_ma"].get(), "Pulse mA")
            if pulse_ma == 0:
                raise ValueError("Pulse mA is 0: nothing to send")
            core.Keithley6221Controller._validate(
                pulse_ma / 1000.0, settings.pulse_width_s, settings.compliance_v, settings.pulse_count)
        except ValueError as error:
            messagebox.showerror(self.title, str(error), parent=self.root)
            return
        width_us, count = settings.pulse_width_s * 1e6, settings.pulse_count
        if not messagebox.askyesno(
                self.title,
                f"Send {count} pulse{'s' if count > 1 else ''} of {pulse_ma:+g} mA, {width_us:g} µs, "
                f"compliance {settings.compliance_v:g} V, at the present field and angle?",
                parent=self.root, default="no"):
            return

        def describe(result):
            text = f"Pulse sent: {pulse_ma:+g} mA, {width_us:g} µs × {count}; 6221 off, read back"
            if result.get("in_compliance") is True:
                text += f"; the 6221 reached compliance ({settings.compliance_v:g} V)"
            return text

        self._run_manual(f"Pulse {pulse_ma:+g} mA", settings, lambda run: run.manual_pulse(pulse_ma), describe)

    def manual_read(self):
        if self.busy:
            return
        if not (self.ppms.connected or self.source.connected):
            messagebox.showerror(self.title, "Connect the PPMS or the 6221 first", parent=self.root)
            return
        self._run_manual("Reading", manual_settings(DEFAULTS), lambda run: None,
                         lambda result: "Read: see Now in Manual settings")

    def _ask_from_worker(self, question):
        """Ask on the Tk thread and wait for the answer; Stop cancels."""
        answered = threading.Event()
        answer = []

        def ask():
            try:
                if not self.stop_event.is_set():  # Stopped before it appeared: not asked.
                    answer.append(messagebox.askyesno(self.title, question, parent=self.root, default="no"))
            finally:
                answered.set()

        self.post("call", ask)
        while not answered.wait(0.1):
            if self.stop_event.is_set():
                raise core.PPMSScanCancelled("Stopped")
        if self.stop_event.is_set():
            raise core.PPMSScanCancelled("Stopped")
        return bool(answer and answer[0])

    def _run_manual(self, label, settings, operation, describe, kind="", target=None):
        """``operation(run)`` in a worker, one action at a time; Stop is on.
        ``kind``: "move" or "field" for the latches; ``target``: the field."""
        self.stop_event.clear()
        self._set_busy(True, running=True)
        self.status_var.set(f"{label}…")
        self._append_log(f"Manual: {label}")
        self._manual_log(f"Manual: {label}")
        run = FourStateRun(self.ppms, self.source, self.lockin, settings, emit=self.post, stop=self.stop_event)

        def read():
            try:
                return run.manual_read()
            except Exception as error:
                return {"error": core.short_status_error(error, 80)}

        def target_():
            result, outcome, problems = run_manual(run, operation)
            reading = read()
            if outcome is None and self.stop_event.is_set():
                # Stop came as the action ended: it still means 6221 off and
                # 0 Oe, as it would have a moment earlier.
                run.shutdown()
                outcome, problems = ("stopped", core.PPMSScanCancelled("Stopped")), list(run.shutdown_problems)
                reading = read()
            self.post("call", lambda: self._manual_finished(
                label, describe, result, outcome, problems, reading, kind, target))

        self.worker = threading.Thread(target=target_, daemon=True)
        self.worker.start()

    def _manual_finished(self, label, describe, result, outcome, problems, reading, kind="", target=None):
        self._set_busy(False)
        self.worker = None
        if "status_box_vars" in self.__dict__:
            self._show_status(reading)
        self._append_log(self.format_reading(reading).replace("\n", "   "))
        state = outcome[0] if outcome is not None else "done"
        # Latches: a Move not completed may leave the rotator turning; a field
        # command not confirmed may still act.  Stop's shutdown confirms 0 Oe.
        if kind == "move" and state in ("stopped", "failed"):
            self.motor_pending = True
        if reading.get("angle_status") in ROTATOR_AT_REST:
            self.motor_pending = False
        if kind == "field" and state == "failed":
            self.field_unconfirmed = target
        elif (kind == "field" and state == "done" and target == 0.0) or (state == "stopped" and not problems):
            self.field_unconfirmed = None
        if state == "done":
            text = describe(result)
            if self.stop_event.is_set():
                text += " (Stop came after it had ended: nothing undone)"
        elif state == "declined":
            text = f"{label}: {outcome[1]}"
        elif state == "stopped":
            text = "Stopped: " + ("6221 off and 0 Oe, read back" if not problems
                                  else "PROBLEM: " + "; ".join(problems))
            if kind == "move":
                text += ". If the rotator was moving, check MultiVu; Read until it shows AtTarget"
        else:
            text = f"{label} failed: {core.short_status_error(outcome[1], 100)}"
        self.status_var.set(text)
        self._append_log(text)
        self._manual_log(text)
        if state == "failed" or problems:
            detail = str(outcome[1]) if state == "failed" else text
            if state == "failed":
                notes = []
                if problems:
                    notes += problems
                elif self.source.connected:
                    notes.append("The 6221 has been turned off (read back).")
                if kind == "field":
                    notes.append(f"The PPMS may still be going to {target:+g} Oe; the command is not resent. "
                                 "Zero H, or MultiVu, to return to 0 Oe. Move is refused until then.")
                else:
                    notes.append("The field and angle are left as they are.")
                detail += "\n\n" + "\n".join(notes)
            messagebox.showerror(self.title, detail, parent=self.root)

    def _manual_log(self, text):
        """Manual actions change the sample outside any run: one line each in
        the data folder's manual_log.txt."""
        try:
            self.data_directory.mkdir(parents=True, exist_ok=True)
            with open(self.data_directory / "manual_log.txt", "a", encoding="utf-8") as handle:
                handle.write(f"{datetime.now().isoformat(timespec='seconds')} {text}\n")
        except OSError:
            pass

    @staticmethod
    def format_reading(reading):
        if "error" in reading:
            return f"Now: not read ({reading['error']})"
        first, second = [], []
        if "angle_deg" in reading:
            first.append(f"φ {float(reading['angle_deg']):.2f}° ({reading.get('angle_status')})")
        elif "angle_error" in reading:
            first.append(f"φ not read ({reading['angle_error']})")
        if "field_oe" in reading:
            first.append(f"H {float(reading['field_oe']):+.1f} Oe {reading.get('field_status', '')}")
        if "temperature_k" in reading:
            second.append(f"T {float(reading['temperature_k']):.3f} K")
        if "source_on" in reading:
            second.append(f"6221 {'ON' if reading['source_on'] else 'OFF'}")
        if not first and not second:
            return "Now: nothing connected to read"
        return "Now: " + "   ".join(first) + ("\n" + "   ".join(second) if second else "")

    def _parameter_rows(self, frame, rows, columns=1):
        per_column = math.ceil(len(rows) / columns)
        for index, (label, key) in enumerate(rows):
            group, row = divmod(index, per_column)
            column = 2 * group
            ttk.Label(frame, text=label).grid(row=row, column=column, sticky="w", pady=1,
                                              padx=(0 if group == 0 else 16, 0))
            if key == "approach":
                widget = ttk.Combobox(frame, textvariable=self.values[key], values=tuple(APPROACHES),
                                      state="readonly", width=13)
                enabled = "readonly"
            else:
                widget = ttk.Entry(frame, textvariable=self.values[key], width=10)
                enabled = "normal"
            widget.grid(row=row, column=column + 1, sticky="w", padx=(6, 0), pady=1)
            self.inputs.append((widget, enabled))

    @property
    def mode(self):
        label = self.mode_var.get()
        return next(key for key, text in MODES.items() if text == label)

    def _apply_mode(self, initial=False):
        """Cycle: its states, the number of cycles and Until Stop.
        Sequence: its states only, run once."""
        cycle = self.mode == "cycle"
        self.sequence_label.configure(text="Cycle" if cycle else "Sequence")
        for widget in (self.cycle_entry, self.count_label, self.count_entry, self.until_stop_box):
            if cycle:
                widget.grid()
            else:
                widget.grid_remove()
        if cycle:
            self.sequence_entry.grid_remove()
        else:
            self.sequence_entry.grid()

    # -- events from threads --------------------------------------------
    def post(self, kind, payload=None):
        self.events.put((kind, payload))

    def log_from_thread(self, message):
        message = str(message)
        quiet = message.startswith(("PPMS motor GetPPMSItem", "PPMS motor: angle="))
        self.post("file-log" if quiet else "log", message)

    def _drain(self):
        """Handle queued events; one failing handler never stops the rest."""
        try:
            while True:
                try:
                    kind, payload = self.events.get_nowait()
                except queue.Empty:
                    break
                try:
                    self._handle(kind, payload)
                except Exception as error:
                    try:
                        self._append_log(f"Display problem ({kind}): {error}")
                    except Exception:
                        pass
        finally:
            try:
                self.drain_after = self.root.after(100, self._drain)
            except tk.TclError:
                pass

    def _handle(self, kind, payload):
        if kind == "log":
            self._append_log(payload)
        elif kind == "file-log":
            self._append_log(payload, show=False)
        elif kind == "status":
            self.status_var.set(payload)
            self._append_log(payload)
        elif kind == "point":
            self._add_point(payload)
        elif kind == "lockin":
            self._record_lockin(payload)
        elif kind == "call":
            payload()
        elif kind in ("manual-reading", "ppms-reading"):
            self._show_status(payload)
        elif kind == "status-box":
            self.status_polling = False
            self._show_status(payload)

    def _append_log(self, text, show=True):
        stamp = datetime.now().strftime("%H:%M:%S")
        if show:
            self.log.configure(state="normal")
            self.log.insert("end", f"{stamp} {text}\n")
            lines = int(self.log.index("end-1c").split(".")[0])
            if lines > 800:
                self.log.delete("1.0", f"{lines - 800}.0")
            self.log.see("end")
            self.log.configure(state="disabled")
        if self.run_folder is not None:
            try:
                with open(self.run_folder / "log.txt", "a", encoding="utf-8") as handle:
                    handle.write(f"{stamp} {text}\n")
            except OSError:
                pass

    def _set_busy(self, busy, running=False):
        self.busy = busy
        state = "disabled" if busy else "normal"
        for widget, enabled in self.inputs:
            widget.configure(state="disabled" if busy else enabled)
        self._update_connection_controls()
        self.start_button.configure(state=state)
        self.stop_button.configure(state="normal" if running else "disabled")

    def _controller(self, key):
        return {"ppms": self.ppms, "6221": self.source, "sr830": self.lockin}[key]

    def _update_connection_controls(self):
        """Connect while not connected, Disconnect while connected, neither
        while busy; the address is fixed while connected."""
        for key, (address, connect, disconnect, lamp) in self.connection_widgets.items():
            connected = self._controller(key).connected
            connect.configure(state="normal" if not self.busy and not connected else "disabled")
            disconnect.configure(state="normal" if not self.busy and connected else "disabled")
            for widget in address:
                widget.configure(state="disabled" if self.busy or connected else "normal")
            color = "on" if connected else "failed" if self.connection_failed[key] else "off"
            lamp.itemconfigure("lamp", fill=LAMP_COLORS[color])
        if "_arrange_flow" in self.__dict__:
            self.root.after_idle(self._arrange_flow)  # A new state may need more room.

    def _in_thread(self, label, work, done):
        self._set_busy(True)
        self.status_var.set(label)

        def target():
            try:
                result = work()
                error = None
            except BaseException as caught:
                result, error = None, caught
            self.post("call", lambda: self._finished(done, result, error))

        threading.Thread(target=target, daemon=True).start()

    def _finished(self, done, result, error):
        self._set_busy(False)
        done(result, error)

    # -- connections ----------------------------------------------------
    def connect_device(self, key):
        """Connect one instrument; the others are left as they are."""
        if self.busy:
            return
        label = CONNECTIONS[key]
        try:
            if key == "ppms":
                port = int(self.port_var.get())
                host, dll = self.host_var.get().strip(), self.dll_var.get().strip()
                connect = lambda: self.ppms.connect(core.PPMS_QDINSTRUMENT_BACKEND, host, port, dll)
            elif key == "6221":
                compliance = _number(self.values["compliance_v"].get(), "6221 compliance (V)")
                core.Keithley6221Controller._validate(0.0, 30e-6, compliance, 1)
                resource = self.source_resource_var.get().strip()
                connect = lambda: self.source.connect(resource, compliance)
            else:
                resource = self.lockin_resource_var.get().strip()
                connect = lambda: connect_lockin(self.lockin, resource)
        except ValueError as error:
            messagebox.showerror(self.title, str(error), parent=self.root)
            return

        def work():
            result = connect()
            if key == "ppms":
                return (f"Connected: {result['temperature_k']:.4g} K, "
                        f"{result['field_oe']:+.6g} Oe / {result['field_status']}")
            if key == "sr830":
                return f"Connected, PPMS page settings: {str(result)[:40]}"
            return f"Connected: {str(result)[:60]}"

        def done(text, error):
            self.connection_failed[key] = error is not None
            if error is not None:
                text = f"Failed: {core.short_status_error(error, 110)}"
                self._append_log(f"{label} connection failed: {error}")
            self.status_vars[key].set(text)
            self.status_var.set(f"{label}: {text}")
            self._update_connection_controls()

        self.status_vars[key].set("Connecting…")
        self._in_thread(f"Connecting {label}…", work, done)

    def disconnect_device(self, key):
        """Disconnect one instrument (the 6221 turns its output off first);
        the field and angle are left as they are."""
        if self.busy:
            return
        label = CONNECTIONS[key]

        def done(_result, error):
            self.connection_failed[key] = False
            if error is not None:
                self.status_vars[key].set(f"Disconnect problem: {core.short_status_error(error, 90)}")
                messagebox.showwarning(self.title, f"{label}: {error}", parent=self.root)
            else:
                self.status_vars[key].set("Not connected")
            self.status_var.set(
                f"{label} disconnected" + (" (output off)" if key == "6221" else "")
                + (" (field and angle unchanged)" if key == "ppms" else "")
                + ("" if error is None else "; check it"))
            self._update_connection_controls()

        self.status_vars[key].set("Disconnecting…")
        self._in_thread(f"Disconnecting {label}…", self._controller(key).disconnect, done)

    # -- run ------------------------------------------------------------
    def current_settings(self):
        states = {name: tuple(variable.get() for variable in variables)
                  for name, variables in self.state_vars.items()}
        values = {key: variable.get() for key, variable in self.values.items()}
        if self.mode == "cycle":
            text, until_stop = self.cycle_var.get(), self.until_stop_var.get()
        else:
            text, until_stop = self.sequence_var.get(), False
            values["repeats"] = "1"  # A sequence runs once, as typed.
        return build_settings(states, text, values, self.autorange_var.get(), until_stop=until_stop)

    def start(self):
        if self.busy:
            return
        try:
            if not (self.ppms.connected and self.source.connected and self.lockin.connected):
                raise ValueError("Connect the PPMS, the 6221 and the SR830 first")
            if self.ppms.backend != core.PPMS_QDINSTRUMENT_BACKEND:
                raise ValueError("The rotator needs the QDInstrument interface")
            settings = self.current_settings()
            if not self.safety_var.get():
                raise ValueError("Tick the safety confirmation first")
            if self.motor_pending:
                raise ValueError("The rotator may still be turning after the last Move: "
                                 "press Read (Manual settings) until it shows AtTarget")
        except ValueError as error:
            messagebox.showerror(self.title, str(error), parent=self.root)
            return
        if not messagebox.askyesno(self.title, self.summary(settings), parent=self.root, default="no"):
            return
        try:
            folder = self.data_directory / f"{datetime.now().strftime('%Y%m%d_%H%M%S')}_{self.mode}"
            folder.mkdir(parents=True, exist_ok=False)
            record = asdict(settings)
            record["mode"] = self.mode
            record["until_stop"] = settings.repeats is None
            record["states"] = {name: dict(angle_deg=a, write_field_oe=h, pulse_ma=i)
                                for name, (a, h, i) in settings.states.items()}
            record["instruments"] = {"ppms_host": self.host_var.get(), "6221": self.source.identity,
                                     "sr830": self.lockin.identity}
            (folder / "settings.json").write_text(json.dumps(record, indent=2), encoding="utf-8")
            csv_file = open(folder / f"{self.title}.csv", "w", newline="", encoding="utf-8")
            try:
                writer = csv.DictWriter(csv_file, fieldnames=CSV_COLUMNS, extrasaction="ignore")
                writer.writeheader()
                csv_file.flush()
            except OSError:
                csv_file.close()
                raise
        except OSError as error:
            messagebox.showerror(self.title, f"Could not create the data files: {error}", parent=self.root)
            return
        self.run_folder = self.last_folder = folder
        self.record = record
        self.csv_file = csv_file
        self.csv_writer = writer
        self.csv_failed = False
        self.rows = []
        self.plot.set_rows([])
        self.steps_per_cycle = len(settings.sequence)
        self.progress.configure(maximum=settings.total_steps or self.steps_per_cycle, value=0)
        self.stop_event.clear()
        self._set_busy(True, running=True)
        self._append_log(f"Run folder: {folder}")
        self.run = FourStateRun(self.ppms, self.source, self.lockin, settings, emit=self.post, stop=self.stop_event)
        run = self.run

        def target():
            try:
                run.run()
                outcome = None
            except core.PPMSScanCancelled as error:
                outcome = ("stopped", error)
            except BaseException as error:
                outcome = ("failed", error)
            self.post("call", lambda: self._run_finished(outcome, list(run.shutdown_problems)))

        self.worker = threading.Thread(target=target, daemon=True)
        self.worker.start()

    def summary(self, s):
        if s.repeats is None:
            lines = ["Run this cycle until Stop?", ""]
        elif s.repeats == 1:
            lines = ["Run this once?", ""]
        else:
            lines = [f"Run this cycle {s.repeats} times?", ""]
        for index, name in enumerate(s.sequence[:12], 1):
            angle, field, pulse = s.states[name]
            lines.append(f"{index:>2}. {name:<2}  φ {angle:g}°,  H {field:+g} Oe,  pulse {pulse:+g} mA")
        if len(s.sequence) > 12:
            lines.append(f"    … {len(s.sequence)} states in all")
        within, wrap = s.large_rotations()
        repeated = s.repeats is None or s.repeats > 1
        turns = within + (1 if wrap and repeated else 0)
        lines += [
            "",
            f"Pulse: {s.pulse_width_s * 1e6:g} µs × {s.pulse_count}, compliance {s.compliance_v:g} V",
            f"Field: {s.field_rate_oe_s:g} Oe/s, Driven, settle {s.field_settle_s:g} s",
            f"Read at 0 Oe: {s.read_current_rms_a * 1e3:g} mA RMS at {s.read_frequency_hz:g} Hz, "
            f"{s.read_time_s:g} s, SR830 1st harmonic" + (", autorange" if s.autorange else ""),
            "",
            f"The rotator turns only at zero field: {turns} large rotation(s) per pass"
            " (plus one at the start if it is not at the first angle). It has no software stop:"
            " keep MultiVu Motion Control at hand.",
            "At the end, on Stop or on an error: 6221 off, field to 0 Oe (Driven), both read back;"
            " the rotator stays.",
        ]
        return "\n".join(lines)

    def _record_lockin(self, settings):
        if self.record is None or self.run_folder is None:
            return
        self.record["sr830_settings"] = settings
        (self.run_folder / "settings.json").write_text(json.dumps(self.record, indent=2), encoding="utf-8")

    def _add_point(self, row):
        self.rows.append(row)
        try:
            if self.csv_writer is not None:
                self.csv_writer.writerow(row)
                self.csv_file.flush()
        except OSError as error:
            self._append_log(f"CSV write failed at step {row['step']}: {error}")
            if not self.__dict__.get("csv_failed"):
                self.csv_failed = True
                messagebox.showwarning(self.title, f"Writing the CSV failed: {error}\n\nThe run continues and the "
                                       "plot keeps every point; settings.json and the log are in the run folder.",
                                       parent=self.root)
        finally:
            self.plot.set_rows(self.rows)
            done = len(self.rows)
            self.progress.configure(value=done if self.run is None or self.run.settings.total_steps
                                    else (done - 1) % self.steps_per_cycle + 1)
            self.last_var.set(
                f"Last read: step {row['step']}, state {row['state']}\n"
                f"X = {row['x_v'] * 1e6:+.4g} µV   Y = {row['y_v'] * 1e6:+.4g} µV\n"
                f"θ = {row['theta_deg']:+.2f}°   R'xy = {row['rxy_ohm'] * 1e3:+.4g} mΩ\n"
                f"H read {row['read_field_oe']:+.4g} Oe, T = {row['temperature_k']:.4g} K")

    def _run_finished(self, outcome, problems):
        try:
            if self.csv_file is not None:
                self.csv_file.close()
        except OSError as error:
            problems = problems + [f"CSV not closed cleanly: {error}"]
        finally:
            self.csv_file = None
            self.csv_writer = None
        try:
            if self.rows:
                self.save_plot(quiet=True)
        finally:
            self._set_busy(False)
            self.worker = None
        count = len(self.rows)
        if self.__dict__.get("csv_failed"):
            problems = problems + ["some CSV rows could not be written"]
        off = "6221 off and 0 Oe, read back" if not problems else "PROBLEM: " + "; ".join(problems)
        if not problems:
            self.field_unconfirmed = None  # The shutdown confirmed 0 Oe.
        if outcome is None:
            text = f"Finished: {count} points; {off}"
        elif outcome[0] == "stopped":
            text = f"Stopped after {count} points; {off}. If the rotator was moving, check MultiVu"
        else:
            text = f"Failed after {count} points: {core.short_status_error(outcome[1], 100)}; {off}"
        self.status_var.set(text)
        self._append_log(text)
        self.run_folder = None
        if outcome is not None and outcome[0] == "failed" or problems:
            detail = str(outcome[1]) if outcome is not None and outcome[0] == "failed" else "Run ended."
            if problems:
                detail += "\n\nShutdown problems:\n" + "\n".join(problems) + "\n\nCheck MultiVu and the 6221."
            messagebox.showerror(self.title, detail, parent=self.root)

    def stop(self):
        if self.worker is not None:
            self.stop_event.set()
            self.status_var.set("Stopping: 6221 off and field to 0 Oe after the current action…")

    def save_plot(self, quiet=False):
        folder = self.last_folder or self.data_directory
        try:
            folder.mkdir(parents=True, exist_ok=True)
            path = folder / f"{self.title}_plot.eps"
            self.canvas.update_idletasks()
            self.canvas.postscript(file=str(path), colormode="color")
        except (OSError, tk.TclError) as error:
            if not quiet:
                messagebox.showerror(self.title, f"Could not save the plot: {error}", parent=self.root)
            return
        if not quiet:
            self.status_var.set(f"Plot saved: {path}")

    def close(self):
        if self.worker is not None or self.busy:
            messagebox.showwarning(self.title, "Wait for the run or the connection to finish before closing.",
                                   parent=self.root)
            return
        for controller in (self.source, self.lockin, self.ppms):
            try:
                controller.disconnect()
            except Exception:
                pass
        for after in ("drain_after", "status_after"):
            try:
                self.root.after_cancel(getattr(self, after))
            except (AttributeError, tk.TclError):
                pass
        self.root.destroy()


def main(mode="cycle"):
    root = tk.Tk()
    FourStateApp(root, mode)
    root.mainloop()


if __name__ == "__main__":
    main()
