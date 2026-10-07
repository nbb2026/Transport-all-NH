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

The Mode list runs it three ways, each a set number of times or until Stop:
a typed sequence (default D A B E), a cycle (default D-A-B-E-B-A, repeated
from its first state) or switching between any two states.

Stop, an error or the end of the run turns the 6221 off and returns the field
to 0 Oe (Driven), confirmed by readback.  The rotator stays where it is: it
has no software stop, so keep MultiVu's Motion Control at hand.

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
# 1500 Oe.  A and B can each be written two ways; the primed rows are the
# reversed field and current.
DEFAULT_STATES = (
    ("D", 0.0, 1500.0, 22.0),
    ("A", 270.0, -1500.0, 22.0),
    ("A'", 270.0, 1500.0, -22.0),
    ("B", 270.0, 1500.0, 22.0),
    ("B'", 270.0, -1500.0, -22.0),
    ("E", 0.0, -1500.0, 22.0),
)
DEFAULT_SEQUENCE = "D A B E"
DEFAULT_CYCLE = "D A B E B A"
DEFAULT_PAIR = ("A", "B")
APP_TITLE = "MS-fourState"
# The Mode list: its label, the sequence row's label, the default sequence,
# the count's label, and whether "Until Stop" is ticked on choosing it.
MODES = {
    "sequence": {"label": "Sequence", "row": "Sequence", "default": DEFAULT_SEQUENCE,
                 "count": "Repeats", "until_stop": False},
    "cycle": {"label": "Cycle", "row": "Cycle", "default": DEFAULT_CYCLE,
              "count": "Cycles", "until_stop": True},
    "two": {"label": "Two states", "row": "Switch between", "default": None,
            "count": "Pairs", "until_stop": True},
}
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
ANGLE_TOLERANCE_DEG = 0.06  # As PPMSMotorDebug.move_and_verify.
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
    """'D A B E', 'd,a,b,e' or "D A' B' E" -> ('D', 'A', 'B', 'E')."""
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


def build_settings(states, sequence_text, values, autorange=True, until_stop=False):
    """Check every value the run will send, before anything is sent.

    ``states``: {name: (angle text, field Oe text, pulse mA text)};
    ``values``: the DEFAULTS keys as entered.
    """
    width_s = _number(values["pulse_width_us"], "Pulse width (us)") / 1e6
    count = _whole(values["pulse_count"], "Pulses per write", 1, 1000)
    compliance = _number(values["compliance_v"], "Compliance (V)")
    rate = _number(values["field_rate"], "Field rate (Oe/s)")
    if values["approach"] not in APPROACHES:
        raise ValueError("Choose a field approach")
    decel = _whole(values["decel"], "Rotator decel", 0, 14)
    parsed = {}
    for name, (angle_text, field_text, pulse_text) in states.items():
        angle = _number(angle_text, f"{name} angle")
        field = _number(field_text, f"{name} field")
        pulse_ma = _number(pulse_text, f"{name} pulse current")
        core.PPMSMotorDebug.validate_move(angle, decel, angle)
        core.validate_ppms_field_command(field, rate)
        core.Keithley6221Controller._validate(pulse_ma / 1000.0, width_s, compliance, count)
        parsed[name] = (angle, field, pulse_ma)
    sequence = parse_sequence(sequence_text, tuple(parsed))
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
    repeats = None if until_stop else _whole(values["repeats"], "Repeats", 1, 100_000)
    return Settings(
        states=parsed, sequence=sequence, repeats=repeats,
        pulse_width_s=width_s, pulse_count=count, compliance_v=compliance,
        field_rate_oe_s=rate, approach_code=APPROACHES[values["approach"]],
        field_settle_s=settle["field_settle_s"], after_pulse_s=settle["after_pulse_s"],
        decel=decel, read_current_rms_a=read_rms_a, read_frequency_hz=frequency,
        read_time_s=read_time, read_settle_s=settle["read_settle_s"], autorange=bool(autorange),
    )


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

    def lockin_settle_s(self):
        return max(0.1, 5.0 * tau_seconds(int(sr830_query(self.lockin, "OFLT?", 1)[0])))

    # -- field and angle ------------------------------------------------
    def command_field(self, target):
        self.check()
        self.commanded_field = target  # Before sending: it may take effect even if the call fails.
        s = self.settings
        return self.ppms.set_field(target, s.field_rate_oe_s, s.approach_code, 1)

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
            data = self.ppms.read_environment()
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
            data = self.ppms.read_environment()
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
        try:
            self.source_off()
        except Exception as error:
            problems.append(f"6221 not confirmed off: {error}")
        try:
            s = self.settings
            data = self.ppms.read_environment()
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
                    data = self.ppms.read_environment()
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
        except Exception as error:
            problems.append(f"0 Oe not confirmed: {error}")
        self.shutdown_problems = problems
        if problems:
            self.emit("log", "Shutdown: " + "; ".join(problems) + ". Check MultiVu and the 6221.")


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


class StatePlot:
    """H, I and SR830 X per step, as panel A of the figure."""

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
        steps = [row["step"] for row in self.rows]
        x_low, x_high = (0.5, max(steps) + 0.5) if steps else (0.5, 4.5)
        labelled = len(self.rows) <= self.LABELLED_POINTS
        marker = 4 if len(self.rows) <= 200 else 2

        def x_pixel(value):
            return left + (value - x_low) / (x_high - x_low) * (width - left - right)

        for index, (label, value_of) in enumerate(self.PANELS):
            y0 = top + index * (panel_height + gap)
            y1 = y0 + panel_height
            canvas.create_rectangle(left, y0, width - right, y1, outline="#555555")
            canvas.create_text(14, (y0 + y1) / 2, text=label, angle=90, anchor="center", font=("TkDefaultFont", 9))
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
                canvas.create_text(left - 6, y, text=f"{tick:g}", anchor="e", font=("TkDefaultFont", 8))
            points = [(x_pixel(row["step"]), y_pixel(v), base_state(row["state"]), row["state"])
                      for row, v in zip(self.rows, values)]
            if index == 2 and len(points) > 1:
                canvas.create_line(*[c for p in points for c in p[:2]], fill="#9e9e9e")
            for x, y, base, name in points:
                color = STATE_COLORS.get(base, "#6a1b9a")
                canvas.create_rectangle(x - marker, y - marker, x + marker, y + marker, fill=color, outline=color)
                if index == 2 and labelled:
                    canvas.create_text(x, y - 12, text=name, fill=color, font=("TkDefaultFont", 9, "bold"))
            if not points and index == 2:
                canvas.create_text((left + width - right) / 2, (y0 + y1) / 2, text="Waiting for data",
                                   fill="#757575")
        axis_y = height - bottom
        for tick in nice_ticks(x_low, x_high, min(10, max(1, len(steps)))):
            if float(tick).is_integer() and x_low <= tick <= x_high:
                x = x_pixel(tick)
                canvas.create_line(x, axis_y, x, axis_y + 4, fill="#555555")
                canvas.create_text(x, axis_y + 6, text=f"{int(tick)}", anchor="n", font=("TkDefaultFont", 8))
        canvas.create_text((left + width - right) / 2, height - 12, text="Step", font=("TkDefaultFont", 9))


class FourStateApp:
    def __init__(self, root, mode="sequence"):
        if mode not in MODES:
            raise ValueError(f"Unknown mode {mode!r}")
        self.root = root
        self.initial_mode = mode
        self.title = APP_TITLE
        self.data_directory = HERE / f"{APP_TITLE} data"
        root.title(self.title)
        root.geometry("1360x900")
        root.minsize(1100, 760)
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
        self._build()
        root.protocol("WM_DELETE_WINDOW", self.close)
        self.drain_after = root.after(100, self._drain)

    # -- layout ---------------------------------------------------------
    def _build(self):
        root = self.root
        root.columnconfigure(0, weight=1)
        root.rowconfigure(0, weight=1)
        page = ttk.Frame(root, padding=8)
        page.grid(row=0, column=0, sticky="nsew")
        page.columnconfigure(0, weight=1)
        page.rowconfigure(3, weight=1)
        self.inputs = []

        connections = ttk.LabelFrame(page, text="Connections", padding=6)
        connections.grid(row=0, column=0, sticky="ew")
        connections.columnconfigure(7, weight=1)
        dll_default = next((str(p) for p in core.PPMSQDInstrumentReadbackController.candidate_dll_paths()
                            if p.is_file()), "")
        self.host_var = tk.StringVar(value=core.PPMS_DEFAULT_HOST)
        self.port_var = tk.StringVar(value=str(core.PPMS_QDINSTRUMENT_DEFAULT_PORT))
        self.dll_var = tk.StringVar(value=dll_default)
        self.source_resource_var = tk.StringVar(value=core.K6221_DEFAULT_RESOURCE)
        self.lockin_resource_var = tk.StringVar(value=core.PPMS_SR830_RESOURCE_OPTIONS[0])
        self.status_vars = {key: tk.StringVar(value="Not connected") for key in ("ppms", "6221", "sr830")}
        rows = (
            ("PPMS (QDInstrument)", "ppms"),
            ("Keithley 6221", "6221"),
            ("SR830", "sr830"),
        )
        for row, (label, key) in enumerate(rows):
            ttk.Label(connections, text=label).grid(row=row, column=0, sticky="w", padx=(0, 6), pady=1)
            if key == "ppms":
                host = ttk.Combobox(connections, textvariable=self.host_var, values=core.PPMS_HOST_CHOICES, width=16)
                host.grid(row=row, column=1, sticky="w")
                ttk.Label(connections, text="Port").grid(row=row, column=2, sticky="e", padx=(8, 2))
                port = ttk.Entry(connections, textvariable=self.port_var, width=7)
                port.grid(row=row, column=3, sticky="w")
                ttk.Label(connections, text="DLL").grid(row=row, column=4, sticky="e", padx=(8, 2))
                dll = ttk.Entry(connections, textvariable=self.dll_var, width=48)
                dll.grid(row=row, column=5, sticky="ew")
                self.inputs += [(host, "normal"), (port, "normal"), (dll, "normal")]
            elif key == "6221":
                widget = ttk.Entry(connections, textvariable=self.source_resource_var, width=18)
                widget.grid(row=row, column=1, sticky="w")
                self.inputs.append((widget, "normal"))
            else:
                widget = ttk.Combobox(connections, textvariable=self.lockin_resource_var,
                                      values=core.PPMS_SR830_RESOURCE_OPTIONS, width=16)
                widget.grid(row=row, column=1, sticky="w")
                self.inputs.append((widget, "normal"))
            ttk.Label(connections, textvariable=self.status_vars[key]).grid(
                row=row, column=7, sticky="w", padx=(12, 0))
        buttons = ttk.Frame(connections)
        buttons.grid(row=0, column=8, rowspan=3, sticky="e", padx=(8, 0))
        self.connect_button = ttk.Button(buttons, text="Connect all", command=self.connect_all, width=14)
        self.connect_button.grid(row=0, column=0, pady=1)
        self.disconnect_button = ttk.Button(buttons, text="Disconnect all", command=self.disconnect_all, width=14)
        self.disconnect_button.grid(row=1, column=0, pady=1)

        middle = ttk.Frame(page)
        middle.grid(row=1, column=0, sticky="ew", pady=(6, 0))
        for column in range(3):
            middle.columnconfigure(column, weight=1, uniform="setup")

        states = ttk.LabelFrame(middle, text="States (H in Oe: 1500 Oe = 0.15 T)", padding=6)
        states.grid(row=0, column=0, sticky="nsew", padx=(0, 4))
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

        self.values = {key: tk.StringVar(value=value) for key, value in DEFAULTS.items()}
        write = ttk.LabelFrame(middle, text="Write", padding=6)
        write.grid(row=0, column=1, sticky="nsew", padx=4)
        self._parameter_rows(write, (
            ("Pulse width (µs)", "pulse_width_us"),
            ("Pulses per write", "pulse_count"),
            ("6221 compliance (V)", "compliance_v"),
            ("Field rate (Oe/s)", "field_rate"),
            ("Field approach", "approach"),
            ("Field settle (s)", "field_settle_s"),
            ("Wait after pulse (s)", "after_pulse_s"),
            ("Rotator decel (0 fast–14 slow)", "decel"),
        ))
        read = ttk.LabelFrame(middle, text="Read (H = 0, SR830 1st harmonic)", padding=6)
        read.grid(row=0, column=2, sticky="nsew", padx=(4, 0))
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

        run = ttk.LabelFrame(page, text="Run", padding=6)
        run.grid(row=2, column=0, sticky="ew", pady=(6, 0))
        run.columnconfigure(12, weight=1)
        self.mode_var = tk.StringVar(value=MODES[self.initial_mode]["label"])
        ttk.Label(run, text="Mode").grid(row=0, column=0, sticky="w")
        mode = ttk.Combobox(run, textvariable=self.mode_var, values=tuple(m["label"] for m in MODES.values()),
                            state="readonly", width=10)
        mode.grid(row=0, column=1, sticky="w", padx=(4, 10))
        mode.bind("<<ComboboxSelected>>", lambda event: self._apply_mode())
        self.inputs.append((mode, "readonly"))
        self.sequence_label = ttk.Label(run)
        self.sequence_label.grid(row=0, column=2, sticky="w")
        self.sequence_var = tk.StringVar(value=MODES[self.initial_mode]["default"] or DEFAULT_SEQUENCE)
        self.sequence_entry = ttk.Entry(run, textvariable=self.sequence_var, width=20)
        self.sequence_entry.grid(row=0, column=3, sticky="w", padx=(4, 10))
        self.inputs.append((self.sequence_entry, "normal"))
        names = tuple(name for name, *_ in DEFAULT_STATES)
        self.pair_vars = (tk.StringVar(value=DEFAULT_PAIR[0]), tk.StringVar(value=DEFAULT_PAIR[1]))
        self.pair_frame = ttk.Frame(run)
        self.pair_frame.grid(row=0, column=3, sticky="w", padx=(4, 10))
        for column, variable in enumerate(self.pair_vars):
            if column:
                ttk.Label(self.pair_frame, text="and").grid(row=0, column=1, padx=4)
            choice = ttk.Combobox(self.pair_frame, textvariable=variable, values=names, state="readonly", width=5)
            choice.grid(row=0, column=2 * column)
            self.inputs.append((choice, "readonly"))
        self.count_label = ttk.Label(run)
        self.count_label.grid(row=0, column=4, sticky="w")
        repeats = ttk.Entry(run, textvariable=self.values["repeats"], width=6)
        repeats.grid(row=0, column=5, sticky="w", padx=(4, 6))
        self.until_stop_var = tk.BooleanVar(value=MODES[self.initial_mode]["until_stop"])
        until_stop = ttk.Checkbutton(run, text="Until Stop", variable=self.until_stop_var)
        until_stop.grid(row=0, column=6, sticky="w", padx=(0, 10))
        self.inputs += [(repeats, "normal"), (until_stop, "normal")]
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
        lower.grid(row=3, column=0, sticky="nsew", pady=(6, 0))
        lower.columnconfigure(0, weight=1)
        lower.rowconfigure(0, weight=1)
        self.canvas = tk.Canvas(lower, background="#ffffff", highlightthickness=0)
        self.canvas.grid(row=0, column=0, sticky="nsew")
        self.plot = StatePlot(self.canvas)
        side = ttk.Frame(lower)
        side.grid(row=0, column=1, sticky="ns", padx=(8, 0))
        side.rowconfigure(1, weight=1)
        self.last_var = tk.StringVar(value="Last read: --")
        ttk.Label(side, textvariable=self.last_var, justify="left", width=34).grid(row=0, column=0, sticky="nw")
        self.log = tk.Text(side, width=46, height=12, wrap="word", state="disabled")
        self.log.grid(row=1, column=0, sticky="nsew", pady=(6, 0))

    def _parameter_rows(self, frame, rows):
        for row, (label, key) in enumerate(rows):
            ttk.Label(frame, text=label).grid(row=row, column=0, sticky="w", pady=1)
            if key == "approach":
                widget = ttk.Combobox(frame, textvariable=self.values[key], values=tuple(APPROACHES),
                                      state="readonly", width=13)
                enabled = "readonly"
            else:
                widget = ttk.Entry(frame, textvariable=self.values[key], width=10)
                enabled = "normal"
            widget.grid(row=row, column=1, sticky="w", padx=(6, 0), pady=1)
            self.inputs.append((widget, enabled))

    @property
    def mode(self):
        label = self.mode_var.get()
        return next(key for key, info in MODES.items() if info["label"] == label)

    def _apply_mode(self, initial=False):
        """Show the row for the chosen mode; untouched defaults follow it."""
        info = MODES[self.mode]
        self.sequence_label.configure(text=info["row"])
        self.count_label.configure(text=info["count"])
        if info["default"] is None:
            self.sequence_entry.grid_remove()
            self.pair_frame.grid()
        else:
            self.pair_frame.grid_remove()
            self.sequence_entry.grid()
            if self.sequence_var.get().strip() in (DEFAULT_SEQUENCE, DEFAULT_CYCLE):
                self.sequence_var.set(info["default"])
        if not initial:
            self.until_stop_var.set(info["until_stop"])

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
        self.connect_button.configure(state=state)
        self.disconnect_button.configure(state=state)
        self.start_button.configure(state=state)
        self.stop_button.configure(state="normal" if running else "disabled")

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
    def connect_all(self):
        try:
            port = int(self.port_var.get())
            compliance = _number(self.values["compliance_v"].get(), "6221 compliance (V)")
            core.Keithley6221Controller._validate(0.0, 30e-6, compliance, 1)
        except ValueError as error:
            messagebox.showerror(self.title, str(error), parent=self.root)
            return
        host = self.host_var.get().strip()
        dll = self.dll_var.get().strip()
        source_resource = self.source_resource_var.get().strip()
        lockin_resource = self.lockin_resource_var.get().strip()

        def work():
            report = {}
            for key, connect in (
                    ("ppms", lambda: self.ppms.connect(core.PPMS_QDINSTRUMENT_BACKEND, host, port, dll)),
                    ("6221", lambda: self.source.connect(source_resource, compliance)),
                    ("sr830", lambda: connect_lockin(self.lockin, lockin_resource))):
                try:
                    result = connect()
                    if key == "ppms":
                        report[key] = (True, f"Connected: {result['temperature_k']:.4g} K, "
                                             f"{result['field_oe']:+.6g} Oe / {result['field_status']}")
                    elif key == "sr830":
                        report[key] = (True, f"Connected, PPMS page settings: {str(result)[:40]}")
                    else:
                        report[key] = (True, f"Connected: {str(result)[:60]}")
                except Exception as error:
                    report[key] = (False, f"Failed: {core.short_status_error(error, 110)}")
                    self.log_from_thread(f"{key} connection failed: {error}")
            return report

        def done(report, error):
            if error is not None:
                messagebox.showerror(self.title, str(error), parent=self.root)
                return
            for key, (_ok, text) in report.items():
                self.status_vars[key].set(text)
            failed = [key for key, (ok, _text) in report.items() if not ok]
            self.status_var.set("All connected" if not failed else
                                "Not connected: " + ", ".join(failed) + " (see the log)")

        self._in_thread("Connecting…", work, done)

    def disconnect_all(self):
        def work():
            problems = []
            for label, controller in (("6221", self.source), ("SR830", self.lockin), ("PPMS", self.ppms)):
                try:
                    controller.disconnect()
                except Exception as error:
                    problems.append(f"{label}: {error}")
            return problems

        def done(problems, error):
            for variable in self.status_vars.values():
                variable.set("Not connected")
            if problems or error:
                messagebox.showwarning(self.title, "; ".join(problems or []) or str(error), parent=self.root)
                self.status_var.set("Disconnected with problems; check the 6221 front panel")
            else:
                self.status_var.set("Disconnected (6221 output off; field and angle unchanged)")

        self._in_thread("Disconnecting…", work, done)

    # -- run ------------------------------------------------------------
    def sequence_text(self):
        if self.mode == "two":
            first, second = (variable.get() for variable in self.pair_vars)
            if first == second:
                raise ValueError("Choose two different states to switch between")
            return f"{first} {second}"
        return self.sequence_var.get()

    def current_settings(self):
        states = {name: tuple(variable.get() for variable in variables)
                  for name, variables in self.state_vars.items()}
        values = {key: variable.get() for key, variable in self.values.items()}
        return build_settings(states, self.sequence_text(), values, self.autorange_var.get(),
                              until_stop=self.until_stop_var.get())

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
        cycle_text = "until Stop" if s.repeats is None else f"{s.repeats} time(s)"
        lines = [f"Run this, {cycle_text}?", ""]
        for index, name in enumerate(s.sequence[:12], 1):
            angle, field, pulse = s.states[name]
            lines.append(f"{index:>2}. {name:<2}  φ {angle:g}°,  H {field:+g} Oe,  pulse {pulse:+g} mA")
        if len(s.sequence) > 12:
            lines.append(f"    … {len(s.sequence)} states per pass")
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
        try:
            self.root.after_cancel(self.drain_after)
        except (AttributeError, tk.TclError):
            pass
        self.root.destroy()


def main(mode="sequence"):
    root = tk.Tk()
    FourStateApp(root, mode)
    root.mainloop()


if __name__ == "__main__":
    main()
